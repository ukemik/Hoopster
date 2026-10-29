"""Entry controller of Hoopster: the court command executor and the Entries, entry detail,
edit and delete callbacks shared by the practice and game screens, and the registration of
every entry-panel callback.

REQ-004 to REQ-014, REQ-044, REQ-058, REQ-075. The court screen's writes follow the command
protocol of plan §3.6: the clientside dispatcher sends one command at a time through
court-outbox, execute_court_command carries it out, refreshes court-base with the database
totals and delivers its ack into court-ack with dash.set_props (the executor has no Output,
so no callback cycle). The ack copies the command's key, attempt and op so the dispatcher can
match it. Every command carries its entry player (court-player.value) and table; the data
layer resolves and locks the owner itself (lock_entry_owner, Context Adaptation Table).

Drill mode (task 2026-10-20_practice_drills, plan §3.7): the entry parent kind "drill" (a
practice drill id) is accepted; its inserts carry side and new_attempt_after and never the
"No spot" mode. Both fields are refused for the practice and game kinds, whose commands are
otherwise validated and carried out exactly as before.
"""

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dash import ALL, ClientsideFunction, Dash, Input, Output, State, ctx, no_update, set_props
from dash.development.base_component import Component
from dash.exceptions import PreventUpdate
from flask_login import current_user
from sqlalchemy.orm import Session

from config.app_config import ENTRY_ATTEMPTS_MAX, ENTRY_LIST_LIMIT, TAPPED_AT_MAX_SKEW_HOURS
from src import data_handler
from src.app_controller import (
    log_failure,
    new_reference_code,
    normalise_clock,
    parse_player_value,
    run_write,
)
from src.data_entry import (
    ENTRY_TABLES,
    PARENT_KIND_DRILL,
    PARENT_KIND_GAME,
    PARENT_KIND_PRACTICE,
    SIDES,
    TABLE_SHOT,
    EntryParent,
    delete_entry_hard,
    get_entry,
    get_entry_totals,
    insert_entry,
    list_entries,
    list_shot_types,
    soft_delete_entry,
    update_entry,
)
from src.layouts_entry import entry_detail_body, entry_list_items

logger = logging.getLogger(__name__)


# --- Court commands (REQ-010 to REQ-014, REQ-044, REQ-058; plan §3.6) ---

OP_INSERT = "insert"
OP_DELETE = "delete"
PARENT_KINDS: frozenset[str] = frozenset(
    {PARENT_KIND_PRACTICE, PARENT_KIND_GAME, PARENT_KIND_DRILL}
)
COMMAND_KEY_MAX_LENGTH = 64
SAVED_TIME_FORMAT = "%H:%M:%S"
COURT_ACK_STORE_ID = "court-ack"
COURT_ACK_PROPERTY = "data"
COURT_BASE_STORE_ID = "court-base"
COURT_BASE_PROPERTY = "data"

MODE_SINGLE = "single"
MODE_CUMULATIVE = "cumulative"
MODE_MANUAL = "manual"
SHOT_MODES: frozenset[str] = frozenset({MODE_SINGLE, MODE_CUMULATIVE, MODE_MANUAL})
FREE_THROW_MODES: frozenset[str] = frozenset({MODE_SINGLE, MODE_CUMULATIVE})
SINGLE_ATTEMPTS = 1
MANUAL_POINT_VALUES: frozenset[int] = frozenset({2, 3})
# The CHECK values of h_data.shot_entries (ck_shot_entries_hand, _creation, _defense) and the
# longest h_ref.shot_types code (ck_shot_types_code).
DETAIL_VALUES: dict[str, frozenset[str]] = {
    "hand": frozenset({"left", "right"}),
    "creation": frozenset({"catch_and_shoot", "off_dribble"}),
    "defense": frozenset({"open", "contested"}),
}
SHOT_TYPE_CODE_MAX_LENGTH = 50
DETAIL_KEYS: tuple[str, ...] = ("shot_type_code", "hand", "creation", "defense")
# The "unset" segment of the detail controls (layouts_entry.DETAIL_NONE).
DETAIL_NONE = "none"


def _is_int(value: object) -> bool:
    """
    Description:
        Whether a JSON value is an integer (a bool is not).

    Parameters:
        value: the value.

    Return Values:
        bool: True for an int that is not a bool.

    Notes:
        Pure.
    """
    return isinstance(value, int) and not isinstance(value, bool)


def _is_one_of(value: object, allowed: frozenset[str]) -> bool:
    """
    Description:
        Whether a JSON value is one of a set of strings.

    Parameters:
        value: the value (any JSON type, a list included).
        allowed: the allowed strings.

    Return Values:
        bool: True for a str in allowed.

    Notes:
        Pure. Checks the type first, so an unhashable value (a list or object sent by an
        altered client) is refused instead of raising TypeError.
    """
    return isinstance(value, str) and value in allowed


def parse_entry_parent(value: object) -> EntryParent | None:
    """
    Description:
        Reads the entry-parent store.

    Parameters:
        value: entry-parent.data, expected {"kind": "practice" | "game" | "drill", "id":
            <int>}; for "drill" the id is a practice_drill_id (plan §3.7).

    Return Values:
        EntryParent | None: the parent, or None when the value is not of that shape.

    Notes:
        Pure. Shape only: every write re-checks the parent in SQL (lock_entry_owner).
    """
    if not isinstance(value, dict):
        return None
    kind = value.get("kind")
    parent_id = value.get("id")
    if not _is_one_of(kind, PARENT_KINDS) or not _is_int(parent_id):
        return None
    return EntryParent(str(kind), int(parent_id))


def _counts_refusal(makes: object, attempts: object) -> str | None:
    """
    Description:
        Checks a pair of counts: whole numbers, 1 <= attempts <= ENTRY_ATTEMPTS_MAX and
        0 <= makes <= attempts (plan D7, ck_*_entries_counts).

    Parameters:
        makes: the makes.
        attempts: the attempts.

    Return Values:
        str | None: the refusal reason, or None when valid.

    Notes:
        Pure.
    """
    if not _is_int(makes) or not _is_int(attempts):
        return "makes and attempts must be integers"
    if not 1 <= attempts <= ENTRY_ATTEMPTS_MAX:
        return "attempts out of range"
    if not 0 <= makes <= attempts:
        return "makes out of range"
    return None


def _details_refusal(details: object) -> str | None:
    """
    Description:
        Checks a command's shot details: null, or an object whose shot_type_code is null or
        a string of at most 50 characters and whose hand, creation and defense are null or
        one of their CHECK values.

    Parameters:
        details: the command's "details".

    Return Values:
        str | None: the refusal reason, or None when valid.

    Notes:
        Pure. Whether the shot type is live is checked in SQL.
    """
    if details is None:
        return None
    if not isinstance(details, dict):
        return "details is not an object"
    shot_type = details.get("shot_type_code")
    if shot_type is not None and (
        not isinstance(shot_type, str)
        or not shot_type
        or len(shot_type) > SHOT_TYPE_CODE_MAX_LENGTH
    ):
        return "shot_type_code is invalid"
    for key, allowed in DETAIL_VALUES.items():
        value = details.get(key)
        if value is not None and not _is_one_of(value, allowed):
            return f"{key} is invalid"
    return None


def _shot_insert_refusal(command: dict) -> str | None:
    """
    Description:
        Checks the mode-specific fields of a field-goal insert.

    Parameters:
        command: the insert command (table "shot").

    Return Values:
        str | None: the refusal reason, or None when valid.

    Notes:
        Pure. single: integer spot, no point value, 1 attempt, makes 0 or 1. cumulative:
        integer spot, no point value. manual: no spot, point value 2 or 3, no details
        (plan D5). Counts of every mode are checked by _counts_refusal.
    """
    mode = command.get("entry_mode")
    spot = command.get("court_spot_id")
    point_value = command.get("point_value")
    if not _is_one_of(mode, SHOT_MODES):
        return "entry_mode is invalid"
    if mode == MODE_MANUAL:
        if spot is not None:
            return "a manual entry has no spot"
        if not _is_int(point_value) or point_value not in MANUAL_POINT_VALUES:
            return "point_value must be 2 or 3"
        if command.get("details") is not None:
            return "a manual entry has no details"
    else:
        if not _is_int(spot):
            return "court_spot_id is not an integer"
        if point_value is not None:
            return "only a manual entry has a point value"
    if mode == MODE_SINGLE and command.get("attempts") != SINGLE_ATTEMPTS:
        return "a single entry has one attempt"
    return _counts_refusal(command.get("makes"), command.get("attempts")) or _details_refusal(
        command.get("details")
    )


def _free_throw_insert_refusal(command: dict) -> str | None:
    """
    Description:
        Checks the fields of a free-throw insert: single or cumulative, no spot, no point
        value, no details (REQ-014, plan D5).

    Parameters:
        command: the insert command (table "free_throw").

    Return Values:
        str | None: the refusal reason, or None when valid.

    Notes:
        Pure.
    """
    mode = command.get("entry_mode")
    if not _is_one_of(mode, FREE_THROW_MODES):
        return "entry_mode is invalid"
    if command.get("court_spot_id") is not None or command.get("point_value") is not None:
        return "a free throw has no spot or point value"
    if command.get("details") is not None:
        return "a free throw has no details"
    if mode == MODE_SINGLE and command.get("attempts") != SINGLE_ATTEMPTS:
        return "a single entry has one attempt"
    return _counts_refusal(command.get("makes"), command.get("attempts"))


def _drill_fields_refusal(command: dict, kind: str) -> str | None:
    """
    Description:
        Checks the drill-mode fields of a command (plan §3.7): side (null, "left" or
        "right") and new_attempt_after (null or a whole number >= 0) for a drill parent, and
        no "No spot" entry there; for any other parent both fields must be absent or null.

    Parameters:
        command: the court command.
        kind: the entry parent's kind.

    Return Values:
        str | None: the refusal reason, or None when valid.

    Notes:
        Pure. Whether the side is recorded is decided in SQL from the drill's symmetry
        (data_entry.drill_side).
    """
    side = command.get("side")
    after = command.get("new_attempt_after")
    if kind != PARENT_KIND_DRILL:
        if side is not None or after is not None:
            return "side and new_attempt_after are for drill entries only"
        return None
    if side is not None and not _is_one_of(side, SIDES):
        return "side is invalid"
    if after is not None and (not _is_int(after) or after < 0):
        return "new_attempt_after is invalid"
    if command.get("op") == OP_INSERT and command.get("entry_mode") == MODE_MANUAL:
        return "a drill entry needs a spot"
    return None


def validate_court_command(outbox: object, parent: object) -> str | None:
    """
    Description:
        Checks a court command and its entry parent strictly before anything is written.

    Parameters:
        outbox: court-outbox.data: {"op", "key", "attempt", "sent_at", "player_id",
            "table"} plus, for an insert, {"entry_mode", "court_spot_id", "point_value",
            "makes", "attempts", "details", "tapped_at"} (and, for a drill parent,
            optional "side" and "new_attempt_after"), or, for a delete, {"entry_id"}.
            player_id is the entry player (court-player.value).
        parent: entry-parent.data: {"kind": "practice" | "game" | "drill", "id": <int>}.

    Return Values:
        str | None: the reason the command is refused, or None when it is valid.

    Notes:
        Pure. Extra fields (sent_at, tapped_at) are accepted. Whether the player owns
        entries in the parent, the spot is active and the shot type live is checked in SQL.
    """
    if not isinstance(outbox, dict):
        return "command is not an object"
    entry_parent = parse_entry_parent(parent)
    if entry_parent is None:
        return "entry parent is not a practice, game or drill id"
    drill_refusal = _drill_fields_refusal(outbox, entry_parent.kind)
    if drill_refusal is not None:
        return drill_refusal
    key = outbox.get("key")
    if not isinstance(key, str) or not key or len(key) > COMMAND_KEY_MAX_LENGTH:
        return "command key is invalid"
    if not _is_int(outbox.get("attempt")):
        return "attempt is not an integer"
    if not _is_int(outbox.get("player_id")):
        return "player_id is not an integer"
    table = outbox.get("table")
    if not _is_one_of(table, ENTRY_TABLES):
        return "table is not shot or free_throw"
    op = outbox.get("op")
    if op == OP_INSERT:
        if table == TABLE_SHOT:
            return _shot_insert_refusal(outbox)
        return _free_throw_insert_refusal(outbox)
    if op == OP_DELETE:
        if not _is_int(outbox.get("entry_id")):
            return "entry_id is not an integer"
        return None
    return "op is not insert or delete"


def court_ack(
    outbox: object,
    ok: bool,
    entry_id: int | None,
    saved_at: str | None,
    ref: str | None,
) -> dict[str, object]:
    """
    Description:
        Builds the ack for court-ack, copying key, attempt, op and table exactly from the
        command.

    Parameters:
        outbox: the command as received (copied from even when invalid).
        ok: whether the command was carried out.
        entry_id: the saved or deleted entry's id (in the command's table), else None.
        saved_at: "HH:MM:SS" in the device's zone, else None.
        ref: the failure's reference code, else None.

    Return Values:
        dict[str, object]: {"key", "attempt", "op", "table", "ok", "entry_id", "saved_at",
        "ref"}.

    Notes:
        The dispatcher ignores an ack whose key, attempt and op do not match its outbox,
        so they are copied unchanged, including on a validation failure.
    """
    source = outbox if isinstance(outbox, dict) else {}
    return {
        "key": source.get("key"),
        "attempt": source.get("attempt"),
        "op": source.get("op"),
        "table": source.get("table"),
        "ok": ok,
        "entry_id": entry_id,
        "saved_at": saved_at,
        "ref": ref,
    }


def parse_tapped_at(value: object, now_utc: datetime) -> datetime:
    """
    Description:
        Reads the device's tap instant, used as the entry's shot_at and its identity for an
        idempotent retry.

    Parameters:
        value: the command's tapped_at, an ISO 8601 string with a zone (e.g. ending "Z").
        now_utc: the server's current time, aware UTC.

    Return Values:
        datetime: the tap instant in UTC, or now_utc when the value is missing, unreadable,
        has no zone, or is more than TAPPED_AT_MAX_SKEW_HOURS from now_utc.

    Notes:
        Pure apart from the warning logged on fallback (Risk R3: a fallback loses retry
        idempotency for that entry).
    """
    if isinstance(value, str):
        try:
            parsed: datetime | None = datetime.fromisoformat(value)
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is not None:
            parsed_utc = parsed.astimezone(timezone.utc)
            if abs(parsed_utc - now_utc) <= timedelta(hours=TAPPED_AT_MAX_SKEW_HOURS):
                return parsed_utc
    logger.warning("Tap time missing, unreadable or too far from server time; using server time")
    return now_utc


def format_device_time(instant: datetime, zone_name: str | None) -> str:
    """
    Description:
        Formats an instant as "HH:MM:SS" in the device's time zone (REQ-055).

    Parameters:
        instant: the instant (aware; a naive value is taken as UTC).
        zone_name: the device's IANA zone from device-clock, or None.

    Return Values:
        str: the wall-clock time on the device.

    Notes:
        Falls back to UTC with a warning for an unknown or malformed zone. OSError is also
        caught because an odd zone name can reach the file system lookup.
    """
    zone: ZoneInfo | timezone = timezone.utc
    if zone_name:
        try:
            zone = ZoneInfo(zone_name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            logger.warning("Unknown device time zone %r; showing UTC", zone_name)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(zone).strftime(SAVED_TIME_FORMAT)


def _entry_fields(command: dict, shot_at: datetime) -> dict[str, object]:
    """
    Description:
        The insert_entry fields of a validated insert command.

    Parameters:
        command: the validated insert command.
        shot_at: the tap instant from parse_tapped_at.

    Return Values:
        dict[str, object]: {"entry_mode", "court_spot_id", "point_value", "makes",
        "attempts", "shot_at", "shot_type_code", "hand", "creation", "defense"}; the details
        are None when the command has none.

    Notes:
        Pure.
    """
    details = command.get("details")
    detail_source = details if isinstance(details, dict) else {}
    fields: dict[str, object] = {
        "entry_mode": command["entry_mode"],
        "court_spot_id": command.get("court_spot_id"),
        "point_value": command.get("point_value"),
        "makes": command["makes"],
        "attempts": command["attempts"],
        "shot_at": shot_at,
    }
    for key in DETAIL_KEYS:
        fields[key] = detail_source.get(key)
    return fields


def _carry_out_command(
    command: dict, parent: EntryParent, zone_name: str, now_utc: datetime
) -> tuple[dict[str, object], dict[str, dict[str, int]] | None]:
    """
    Description:
        Saves or undoes one entry of a validated command and reads the parent's totals, in
        one transaction, for the command's entry player.

    Parameters:
        command: the validated court command; command["player_id"] is the entry player
            (court-player.value when the entry was made). Never current_player_id() and
            never a user id (Context Adaptation Table).
        parent: the entry parent; re-checked in SQL by lock_entry_owner.
        zone_name: the device's IANA zone for the save time.
        now_utc: the server's current time, aware UTC.

    Return Values:
        tuple[dict[str, object], dict[str, dict[str, int]] | None]: the ack, and the
        parent's totals for court-base after a success (None after a failure).

    Notes:
        A retried insert of a saved entry returns the existing row; an undo of a row already
        gone is reported as success (plan §3.6). The data layer locks the owner first; a
        player who is no longer an owner makes the command fail with a reference
        (Amendment A4). For a drill parent the insert also passes the command's side and
        new_attempt_after (practice_drills plan §3.7).
    """
    acting_user_id = current_user.user_id
    player_id = command["player_id"]
    table = command["table"]
    if command["op"] == OP_INSERT:
        entry = _entry_fields(command, parse_tapped_at(command.get("tapped_at"), now_utc))
        if parent.kind == PARENT_KIND_DRILL:
            # Drill mode (plan §3.7): the attempt rule's two inputs.
            entry["side"] = command.get("side")
            entry["new_attempt_after"] = command.get("new_attempt_after")

        def insert_work(session: Session) -> tuple[dict[str, object], dict[str, dict[str, int]]]:
            """
            Description:
                Inserts the entry and reads the totals in the same transaction.

            Parameters:
                session: the write session.

            Return Values:
                tuple: (insert_entry result, get_entry_totals result).

            Notes:
                None.
            """
            inserted = insert_entry(session, parent, player_id, table, entry)
            return inserted, get_entry_totals(session, parent)

        result = run_write(acting_user_id, insert_work, "save entry")
        if not result.ok or result.value is None:
            return court_ack(command, False, None, None, result.ref), None
        inserted, totals = result.value
        created_at = inserted["created_at"]
        saved_instant = created_at if isinstance(created_at, datetime) else now_utc
        ack = court_ack(
            command,
            True,
            int(inserted["entry_id"]),
            format_device_time(saved_instant, zone_name),
            None,
        )
        return ack, totals

    entry_id = command["entry_id"]

    def undo_work(session: Session) -> tuple[bool, dict[str, dict[str, int]]]:
        """
        Description:
            Hard-deletes the entry and reads the totals in the same transaction.

        Parameters:
            session: the write session.

        Return Values:
            tuple: (whether a row was deleted, get_entry_totals result).

        Notes:
            None.
        """
        deleted = delete_entry_hard(session, parent, player_id, table, entry_id)
        return deleted, get_entry_totals(session, parent)

    result_undo = run_write(acting_user_id, undo_work, "undo entry")
    if not result_undo.ok or result_undo.value is None:
        return court_ack(command, False, None, None, result_undo.ref), None
    deleted, totals = result_undo.value
    if not deleted:
        logger.warning(
            "%s entry %s was already gone; undo reported as done", table, entry_id
        )
    ack = court_ack(command, True, entry_id, format_device_time(now_utc, zone_name), None)
    return ack, totals


def _deliver_ack(ack: dict[str, object]) -> None:
    """
    Description:
        Delivers a court command's ack into the court-ack store with dash.set_props. This is
        the only writer of court-ack.

    Parameters:
        ack: the ack built by court_ack.

    Return Values:
        None.

    Notes:
        execute_court_command has no Output (plan §3.6). A set_props value reaches the
        browser as a side update, which starts a fresh callback chain, so the dispatcher's
        court-ack Input fires without forming the court_dispatch -> execute_court_command ->
        court_dispatch loop that Dash 4.4.1 prunes. Call it at most once per run.
    """
    set_props(COURT_ACK_STORE_ID, {COURT_ACK_PROPERTY: ack})


def execute_court_command(
    outbox: dict | None, parent: dict | None, clock: dict | None
) -> None:
    """
    Description:
        The entry screen's server callback: carries out the single command in flight,
        replaces court-base with the parent's database totals after a success, and delivers
        the ack into court-ack.

    Parameters:
        outbox: court-outbox.data, the command in flight, or None.
        parent: entry-parent.data, {"kind": "practice" | "game", "id"}.
        clock: device-clock.data, for the save time's zone.

    Return Values:
        None (court-base and the ack are delivered with set_props).

    Notes:
        Raises PreventUpdate when the outbox is empty (the dispatcher cleared it). A
        boundary: every failure, including a refused command, is an ok: false ack with a new
        reference and no totals, never an exception (COD-4-04). court-base is set before
        court-ack (plan D8). Has no Output, so it must return None: Dash rejects any other
        value from a no-output callback.
    """
    if outbox is None:
        raise PreventUpdate
    reason = validate_court_command(outbox, parent)
    entry_parent = parse_entry_parent(parent)
    if reason is not None or entry_parent is None:
        ref = new_reference_code()
        logger.error("Court command refused (ref %s): %s", ref, reason)
        _deliver_ack(court_ack(outbox, False, None, None, ref))
        return None
    if not current_user.is_authenticated:
        ref = new_reference_code()
        logger.error("Court command refused (ref %s): not signed in", ref)
        _deliver_ack(court_ack(outbox, False, None, None, ref))
        return None

    try:
        ack, totals = _carry_out_command(
            outbox,
            entry_parent,
            normalise_clock(clock)["zone"],
            datetime.now(timezone.utc),
        )
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Court command failed (ref %s)", ref)
        _deliver_ack(court_ack(outbox, False, None, None, ref))
        return None
    if totals is not None:
        set_props(COURT_BASE_STORE_ID, {COURT_BASE_PROPERTY: totals})
    _deliver_ack(ack)
    return None


# --- Entries list, detail, edit and delete (REQ-004 to REQ-006; plan D10 to D12) ---

MESSAGE_ENTRIES_FAILED = "Could not load the entries. Reference: {ref}"
MESSAGE_ENTRY_GONE = "This entry no longer exists."
MESSAGE_ENTRY_LOAD_FAILED = "Could not open the entry. Reference: {ref}"
MESSAGE_WAIT_UNSAVED = "Wait until every entry is saved."
MESSAGE_ENTRY_CHANGED = "This entry was changed or deleted elsewhere. Reopen Entries."
MESSAGE_SAVE_FAILED = "Could not save the entry. Reference: {ref}"
MESSAGE_DELETE_FAILED = "Could not delete the entry. Reference: {ref}"
MESSAGE_EDIT_TARGET = "This entry cannot be edited. Reopen Entries."
MESSAGE_EDIT_COUNTS = (
    f"Enter attempts from 1 to {ENTRY_ATTEMPTS_MAX} and makes from 0 to attempts."
)
MESSAGE_EDIT_MADE = "Choose Make or Miss."
MESSAGE_EDIT_POINTS = "Choose 2-pointers or 3-pointers."
MESSAGE_EDIT_DETAILS = "Choose valid shot details."
ENTRY_ITEM_TYPE = "entry-item"
MADE_VALUES: dict[str, int] = {"1": 1, "0": 0}
POINT_VALUES: dict[str, int] = {"2": 2, "3": 3}


def _picked_owner(parent: object, player_value: object) -> tuple[EntryParent, int] | None:
    """
    Description:
        Reads the entry parent and the picked player of an Entries, edit or delete callback.

    Parameters:
        parent: entry-parent.data.
        player_value: court-player.value (Context Adaptation Table).

    Return Values:
        tuple[EntryParent, int] | None: (parent, player id), or None when either is not an
        id.

    Notes:
        Pure. Every read and write re-checks both in SQL.
    """
    entry_parent = parse_entry_parent(parent)
    player_id = parse_player_value(player_value)
    if entry_parent is None or player_id is None:
        return None
    return entry_parent, player_id


def parse_entry_item_index(index: object) -> tuple[str, int] | None:
    """
    Description:
        Reads an Entries list item's index "<table>-<entry_id>".

    Parameters:
        index: the pattern-matching id's "index", e.g. "shot-12" or "free_throw-7".

    Return Values:
        tuple[str, int] | None: (table, entry id), or None when the text is not of that
        shape or the table is unknown.

    Notes:
        Pure.
    """
    if not isinstance(index, str):
        return None
    table, separator, id_text = index.rpartition("-")
    if not separator or table not in ENTRY_TABLES:
        return None
    if not id_text.isascii() or not id_text.isdigit():
        return None
    return table, int(id_text)


def open_entries(
    n_clicks: int | None, parent: dict | None, player_value: str | None, clock: dict | None
) -> tuple[bool, list[Component], str]:
    """
    Description:
        "Entries": opens the dialog listing the picked player's newest entries in this
        parent (plan D12).

    Parameters:
        n_clicks: court-entries-open.n_clicks.
        parent: entry-parent.data.
        player_value: court-player.value, the picked player (Context Adaptation Table).
        clock: device-clock.data, for the times' zone.

    Return Values:
        tuple[bool, list[Component], str]: entries-modal.opened (True), entries-list
        children and entries-feedback text.

    Notes:
        A boundary (COD-4-04): a failed read opens the dialog with a reference.
    """
    if not n_clicks:
        raise PreventUpdate
    try:
        owner = _picked_owner(parent, player_value)
        if owner is None:
            raise ValueError("entry parent or court player is not an id")
        entry_parent, player_id = owner
        rows = list_entries(
            data_handler.get_session(), entry_parent, player_id, ENTRY_LIST_LIMIT
        )
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Entries read failed (ref %s)", ref)
        return True, [], MESSAGE_ENTRIES_FAILED.format(ref=ref)
    return True, entry_list_items(rows, normalise_clock(clock)["zone"]), ""


def open_entry_detail(
    n_clicks_list: list[int | None],
    parent: dict | None,
    player_value: str | None,
    clock: dict | None,
) -> tuple[object, object, object, str]:
    """
    Description:
        A tap on an Entries list item: opens the entry detail dialog with the entry's
        summary, who created and last edited it and when, and its edit controls (REQ-004).

    Parameters:
        n_clicks_list: {"type": "entry-item", "index": ALL}.n_clicks.
        parent: entry-parent.data.
        player_value: court-player.value, the picked player.
        clock: device-clock.data, for the times' zone.

    Return Values:
        tuple[object, object, object, str]: entry-detail-modal.opened (True),
        entry-detail-body children, entry-detail-target.data ({"table", "entry_id",
        "entry_mode"} or None) and entry-detail-feedback text.

    Notes:
        Raises PreventUpdate for a run without a real tap (the list was re-rendered, so
        every item reports n_clicks None). A boundary (COD-4-04). The target is written only
        here, from the server-built list, and every write re-checks it in SQL.
    """
    if not any(trigger.get("value") for trigger in ctx.triggered):
        raise PreventUpdate
    triggered = ctx.triggered_id
    item = parse_entry_item_index(
        triggered.get("index") if isinstance(triggered, dict) else None
    )
    try:
        owner = _picked_owner(parent, player_value)
        if owner is None or item is None:
            raise ValueError("entry parent, court player or entry item is not an id")
        entry_parent, player_id = owner
        table, entry_id = item
        session = data_handler.get_session()
        row = get_entry(session, entry_parent, player_id, table, entry_id)
        if row is None:
            return True, [], None, MESSAGE_ENTRY_GONE
        shot_types = list_shot_types(session)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Entry detail read failed (ref %s)", ref)
        return True, [], None, MESSAGE_ENTRY_LOAD_FAILED.format(ref=ref)
    target = {"table": table, "entry_id": entry_id, "entry_mode": row["entry_mode"]}
    body = entry_detail_body(row, normalise_clock(clock)["zone"], shot_types)
    return True, body, target, ""


def _parse_count(value: object) -> int | None:
    """
    Description:
        Reads a number input's value as a whole number.

    Parameters:
        value: the dmc NumberInput value: an int, a whole float, digits as text, or "".

    Return Values:
        int | None: the number, or None when it is not a non-negative whole number.

    Notes:
        Pure.
    """
    if _is_int(value):
        return int(value) if int(value) >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    if isinstance(value, str) and value.strip().isdigit() and value.strip().isascii():
        return int(value.strip())
    return None


def _parse_edit_details(
    shot_type: object, hand: object, creation: object, defense: object
) -> dict[str, object] | None:
    """
    Description:
        Reads the four detail controls of the edit form; "none" and empty are unset.

    Parameters:
        shot_type: entry-edit-shot-type.value (a code or None).
        hand: entry-edit-hand.value.
        creation: entry-edit-creation.value.
        defense: entry-edit-defense.value.

    Return Values:
        dict[str, object] | None: {"shot_type_code", "hand", "creation", "defense"} with
        None for unset, or None when a value is not allowed.

    Notes:
        Pure. Whether the shot type is live is checked in SQL.
    """
    def unset(value: object) -> bool:
        """
        Description:
            Whether a detail value means "not set".

        Parameters:
            value: the control's value.

        Return Values:
            bool: True for None, "" and "none".

        Notes:
            None.
        """
        return value is None or value == "" or value == DETAIL_NONE

    details: dict[str, object] = {key: None for key in DETAIL_KEYS}
    details["shot_type_code"] = None if unset(shot_type) else shot_type
    for key, value in (("hand", hand), ("creation", creation), ("defense", defense)):
        details[key] = None if unset(value) else value
    if _details_refusal(details) is not None:
        return None
    return details


def parse_entry_edit(
    target: object,
    made: object,
    makes: object,
    attempts: object,
    points: object,
    shot_type: object,
    hand: object,
    creation: object,
    defense: object,
) -> tuple[dict[str, object] | None, str | None]:
    """
    Description:
        Builds update_entry's changes from the entry detail form, by the entry's table and
        mode (plan D10), with the rules of validate_court_command.

    Parameters:
        target: entry-detail-target.data, {"table", "entry_id", "entry_mode"}.
        made: entry-edit-made.value, "1" or "0" (single entries).
        makes: entry-edit-makes.value (X of Y, No spot, free-throw totals).
        attempts: entry-edit-attempts.value (same).
        points: entry-edit-points.value, "2" or "3" (No spot).
        shot_type: entry-edit-shot-type.value (single and X of Y field goals).
        hand: entry-edit-hand.value (same).
        creation: entry-edit-creation.value (same).
        defense: entry-edit-defense.value (same).

    Return Values:
        tuple[dict[str, object] | None, str | None]: (changes, None) when valid, with
        changes {"entry_mode", "makes", "attempts", "point_value", "shot_type_code", "hand",
        "creation", "defense"}; or (None, the message to show).

    Notes:
        Pure. Controls that do not apply to the entry are ignored (they are hidden, not
        absent). No-spot entries and free throws never carry details (plan D5).
    """
    if not isinstance(target, dict):
        return None, MESSAGE_EDIT_TARGET
    table = target.get("table")
    mode = target.get("entry_mode")
    allowed_modes = SHOT_MODES if table == TABLE_SHOT else FREE_THROW_MODES
    if (
        not _is_one_of(table, ENTRY_TABLES)
        or not _is_one_of(mode, allowed_modes)
        or not _is_int(target.get("entry_id"))
    ):
        return None, MESSAGE_EDIT_TARGET

    changes: dict[str, object] = {"entry_mode": mode, "point_value": None}
    changes.update({key: None for key in DETAIL_KEYS})
    if mode == MODE_SINGLE:
        if not isinstance(made, str) or made not in MADE_VALUES:
            return None, MESSAGE_EDIT_MADE
        changes["makes"] = MADE_VALUES[made]
        changes["attempts"] = SINGLE_ATTEMPTS
    else:
        parsed_makes = _parse_count(makes)
        parsed_attempts = _parse_count(attempts)
        if _counts_refusal(parsed_makes, parsed_attempts) is not None:
            return None, MESSAGE_EDIT_COUNTS
        changes["makes"] = parsed_makes
        changes["attempts"] = parsed_attempts

    if table == TABLE_SHOT and mode == MODE_MANUAL:
        if not isinstance(points, str) or points not in POINT_VALUES:
            return None, MESSAGE_EDIT_POINTS
        changes["point_value"] = POINT_VALUES[points]
    elif table == TABLE_SHOT:
        details = _parse_edit_details(shot_type, hand, creation, defense)
        if details is None:
            return None, MESSAGE_EDIT_DETAILS
        changes.update(details)
    return changes, None


def _has_unsaved(queue: object, outbox: object) -> bool:
    """
    Description:
        Whether the browser still holds commands that are not saved.

    Parameters:
        queue: court-queue.data.
        outbox: court-outbox.data.

    Return Values:
        bool: True when the queue is not empty or a command is in flight.

    Notes:
        Pure. Edit and delete are refused meanwhile (plan D11), so the server totals they
        return never race the dispatcher's queue.
    """
    return bool(queue) or outbox is not None


def save_entry_detail(
    n_clicks: int | None,
    target: dict | None,
    parent: dict | None,
    player_value: str | None,
    made: str | None,
    makes: object,
    attempts: object,
    points: str | None,
    shot_type: str | None,
    hand: str | None,
    creation: str | None,
    defense: str | None,
    queue: list | None,
    outbox: dict | None,
) -> tuple[str, object, object, object]:
    """
    Description:
        "Save changes" in the entry detail dialog: updates the entry and refreshes the
        tally base, in one transaction (REQ-004, plan D10).

    Parameters:
        n_clicks: entry-detail-save.n_clicks.
        target: entry-detail-target.data.
        parent: entry-parent.data.
        player_value: court-player.value, the picked player.
        made, makes, attempts, points, shot_type, hand, creation, defense: the eight
            entry-edit-* values (see parse_entry_edit).
        queue: court-queue.data.
        outbox: court-outbox.data.

    Return Values:
        tuple[str, object, object, object]: entry-detail-feedback text,
        entry-detail-modal.opened, entries-modal.opened (both False on success) and
        court-base.data (the new totals on success); no_update where nothing changes.

    Notes:
        A boundary (COD-4-04). Refused while any entry is unsaved (plan D11). An entry that
        matched nothing (changed, deleted or no longer the owner's, or a newly chosen shot
        type retired elsewhere meanwhile) writes nothing and shows "changed or deleted
        elsewhere", logged as a warning. Keeping the entry's own since-retired shot type
        saves normally (update_entry, review F2).
    """
    if not n_clicks:
        raise PreventUpdate
    if _has_unsaved(queue, outbox):
        return MESSAGE_WAIT_UNSAVED, no_update, no_update, no_update
    owner = _picked_owner(parent, player_value)
    if owner is None:
        ref = new_reference_code()
        logger.error("Entry save refused (ref %s): parent or court player is not an id", ref)
        return MESSAGE_SAVE_FAILED.format(ref=ref), no_update, no_update, no_update
    changes, message = parse_entry_edit(
        target, made, makes, attempts, points, shot_type, hand, creation, defense
    )
    if changes is None or not isinstance(target, dict):
        return message or MESSAGE_EDIT_TARGET, no_update, no_update, no_update
    entry_parent, player_id = owner
    table = str(target["table"])
    entry_id = int(target["entry_id"])
    valid_changes: dict[str, object] = changes

    def work(session: Session) -> dict[str, dict[str, int]] | None:
        """
        Description:
            Updates the entry and reads the totals in the same transaction.

        Parameters:
            session: the write session.

        Return Values:
            dict[str, dict[str, int]] | None: the totals, or None when no row matched.

        Notes:
            Nothing is written when no row matched.
        """
        if not update_entry(session, entry_parent, player_id, table, entry_id, valid_changes):
            return None
        return get_entry_totals(session, entry_parent)

    result = run_write(current_user.user_id, work, "edit entry")
    if not result.ok:
        return MESSAGE_SAVE_FAILED.format(ref=result.ref), no_update, no_update, no_update
    if result.value is None:
        logger.error(
            "%s entry %s matched no live row of player %s; edit not saved",
            table,
            entry_id,
            player_id,
        )
        return MESSAGE_ENTRY_CHANGED, no_update, no_update, no_update
    logger.info("Edited %s entry %s of player %s", table, entry_id, player_id)
    return "", False, False, result.value


def confirm_entry_delete(
    n_clicks: int | None,
    target: dict | None,
    parent: dict | None,
    player_value: str | None,
    queue: list | None,
    outbox: dict | None,
) -> tuple[object, str, object, object, object]:
    """
    Description:
        "Delete" in the delete confirmation: soft-deletes the entry through
        h_data.fn_soft_delete after checking its owner, and refreshes the tally base, in one
        transaction (REQ-005, REQ-006, plan D11).

    Parameters:
        n_clicks: entry-delete-confirm.n_clicks.
        target: entry-detail-target.data, {"table", "entry_id", "entry_mode"}.
        parent: entry-parent.data.
        player_value: court-player.value, the picked player.
        queue: court-queue.data.
        outbox: court-outbox.data.

    Return Values:
        tuple[object, str, object, object, object]: entry-delete-modal.opened,
        entry-delete-feedback text, entry-detail-modal.opened, entries-modal.opened (all
        three dialogs False on success) and court-base.data (the new totals on success);
        no_update where nothing changes.

    Notes:
        A boundary (COD-4-04). Refused while any entry is unsaved (plan D11).
        soft_delete_entry locks the entry as a live free-form row of that owner and parent
        before calling fn_soft_delete, which itself checks no owner.
    """
    if not n_clicks:
        raise PreventUpdate
    if _has_unsaved(queue, outbox):
        return no_update, MESSAGE_WAIT_UNSAVED, no_update, no_update, no_update
    owner = _picked_owner(parent, player_value)
    valid_target = (
        isinstance(target, dict)
        and _is_one_of(target.get("table"), ENTRY_TABLES)
        and _is_int(target.get("entry_id"))
    )
    if owner is None or not valid_target or not isinstance(target, dict):
        ref = new_reference_code()
        logger.error("Entry delete refused (ref %s): parent, player or entry is not an id", ref)
        return no_update, MESSAGE_DELETE_FAILED.format(ref=ref), no_update, no_update, no_update
    entry_parent, player_id = owner
    table = str(target["table"])
    entry_id = int(target["entry_id"])

    def work(session: Session) -> dict[str, dict[str, int]]:
        """
        Description:
            Soft-deletes the entry and reads the totals in the same transaction.

        Parameters:
            session: the write session.

        Return Values:
            dict[str, dict[str, int]]: the totals.

        Notes:
            soft_delete_entry raises ValueError when the entry is not the owner's live
            free-form row, which rolls everything back.
        """
        soft_delete_entry(session, entry_parent, player_id, table, entry_id)
        return get_entry_totals(session, entry_parent)

    result = run_write(current_user.user_id, work, "delete entry")
    if not result.ok:
        return (
            no_update,
            MESSAGE_DELETE_FAILED.format(ref=result.ref),
            no_update,
            no_update,
            no_update,
        )
    logger.info("Soft-deleted %s entry %s of player %s", table, entry_id, player_id)
    return False, "", False, False, result.value


# --- Registration ---

def _register_court_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the court part of the entry panel: spot selection and view, the command
        dispatcher, the display, the executor, the mode switch, the forms and the shot
        details.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        See register_entry_callbacks for the single-writer rules. Drill mode (practice_drills
        plan §3.7): select_spot also listens to court-queue for the plan's auto-advance
        (court-base, court-plan, court-player, court-confirmed and court-attempt-request are
        States); court_dispatch reads court-attempt-request, court-drill-side and court-base;
        court_render listens to court-attempt-request. entry_mode_view runs on the initial
        call too, so a mode remembered on the device (drill mode's persisted court-mode)
        shows its panel; on other screens it returns the classes the page was built with.
    """
    app.clientside_callback(
        ClientsideFunction("hoopster", "select_spot"),
        Output("court-graph", "figure"),
        Output("court-selected-spot", "data"),
        Output("court-selected-label", "children"),
        Output("court-make", "disabled"),
        Output("court-miss", "disabled"),
        Output("court-graph-wrap", "className"),
        Input("court-graph", "clickData"),
        Input("court-view", "value"),
        Input("court-queue", "data"),
        State("court-graph", "figure"),
        State("court-figures", "data"),
        State("court-selected-spot", "data"),
        State("court-base", "data"),
        State("court-plan", "data"),
        State("court-player", "value"),
        State("court-confirmed", "data"),
        State("court-attempt-request", "data"),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        ClientsideFunction("hoopster", "court_dispatch"),
        Output("court-queue", "data"),
        Output("court-confirmed", "data"),
        Output("court-outbox", "data"),
        Output("court-failure", "data"),
        Input("court-make", "n_clicks"),
        Input("court-miss", "n_clicks"),
        Input("court-ft-make", "n_clicks"),
        Input("court-ft-miss", "n_clicks"),
        Input("court-undo", "n_clicks"),
        Input("court-retry", "n_clicks"),
        Input("court-request", "data"),
        Input("court-ack", "data"),
        Input("court-watchdog", "n_intervals"),
        State("court-selected-spot", "data"),
        State("court-queue", "data"),
        State("court-confirmed", "data"),
        State("court-outbox", "data"),
        State("court-failure", "data"),
        State("entry-parent", "data"),
        State("court-player", "value"),
        State("court-detail-shot-type", "value"),
        State("court-detail-hand", "value"),
        State("court-detail-creation", "value"),
        State("court-detail-defense", "value"),
        State("court-attempt-request", "data"),
        State("court-drill-side", "value"),
        State("court-base", "data"),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        ClientsideFunction("hoopster", "court_render"),
        Output("court-tally", "children"),
        Output("court-ft-tally", "children"),
        Output("court-status", "children"),
        Output("court-status", "color"),
        Output("court-retry", "style"),
        Output("court-undo", "disabled"),
        Output("court-watchdog", "disabled"),
        Input("court-base", "data"),
        Input("court-queue", "data"),
        Input("court-confirmed", "data"),
        Input("court-outbox", "data"),
        Input("court-ack", "data"),
        Input("court-failure", "data"),
        Input("court-player", "value"),
        Input("court-attempt-request", "data"),
    )

    app.callback(
        Input("court-outbox", "data"),
        State("entry-parent", "data"),
        State("device-clock", "data"),
        prevent_initial_call=True,
    )(execute_court_command)

    app.clientside_callback(
        ClientsideFunction("hoopster", "entry_mode_view"),
        Output("court-single-panel", "className"),
        Output("court-cumulative-panel", "className"),
        Output("court-manual-panel", "className"),
        Output("court-details-area", "className"),
        Output("court-ft-single-panel", "className"),
        Output("court-ft-cumulative-panel", "className"),
        Input("court-mode", "value"),
    )

    app.clientside_callback(
        ClientsideFunction("hoopster", "entry_form_submit"),
        Output("court-request", "data"),
        Output("court-form-feedback", "children"),
        Output("court-cum-makes", "value"),
        Output("court-cum-attempts", "value"),
        Output("court-manual-makes", "value"),
        Output("court-manual-attempts", "value"),
        Output("court-ft-makes", "value"),
        Output("court-ft-attempts", "value"),
        Input("court-cum-save", "n_clicks"),
        Input("court-manual-save", "n_clicks"),
        Input("court-ft-save", "n_clicks"),
        State("court-selected-spot", "data"),
        State("court-cum-makes", "value"),
        State("court-cum-attempts", "value"),
        State("court-manual-points", "value"),
        State("court-manual-makes", "value"),
        State("court-manual-attempts", "value"),
        State("court-ft-makes", "value"),
        State("court-ft-attempts", "value"),
        State("court-detail-shot-type", "value"),
        State("court-detail-hand", "value"),
        State("court-detail-creation", "value"),
        State("court-detail-defense", "value"),
        State("court-player", "value"),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        ClientsideFunction("hoopster", "toggle_open"),
        Output("court-details", "opened"),
        Input("court-details-toggle", "n_clicks"),
        State("court-details", "opened"),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        ClientsideFunction("hoopster", "details_remember"),
        Output("court-details-memory", "data"),
        Output("court-details-summary", "children"),
        Input("court-detail-shot-type", "value"),
        Input("court-detail-hand", "value"),
        Input("court-detail-creation", "value"),
        Input("court-detail-defense", "value"),
        State("court-details-memory", "data"),
        State("court-detail-shot-type", "data"),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        ClientsideFunction("hoopster", "details_restore"),
        Output("court-detail-shot-type", "value"),
        Output("court-detail-hand", "value"),
        Output("court-detail-creation", "value"),
        Output("court-detail-defense", "value"),
        Input("court-details-memory", "modified_timestamp"),
        State("court-details-memory", "data"),
        State("court-detail-shot-type", "value"),
        State("court-detail-hand", "value"),
        State("court-detail-creation", "value"),
        State("court-detail-defense", "value"),
    )

    app.clientside_callback(
        ClientsideFunction("hoopster", "clear_details"),
        Output("court-detail-shot-type", "value", allow_duplicate=True),
        Output("court-detail-hand", "value", allow_duplicate=True),
        Output("court-detail-creation", "value", allow_duplicate=True),
        Output("court-detail-defense", "value", allow_duplicate=True),
        Input("court-details-clear", "n_clicks"),
        prevent_initial_call=True,
    )


def _register_drill_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the drill sub-panel's clientside callbacks (practice_drills plan §3.7):
        new_attempt_request, drill_progress and drill_pb_text.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        new_attempt_request is the single writer of court-attempt-request. drill_progress
        runs on the initial call (it returns "" without a plan) and also listens to
        court-selected-spot to mark the current spot; it writes only court-plan-progress,
        so no chain returns to select_spot. The components exist on every entry page
        (hidden outside drill mode).
    """
    app.clientside_callback(
        ClientsideFunction("hoopster", "new_attempt_request"),
        Output("court-attempt-request", "data"),
        Input("court-new-attempt", "n_clicks"),
        Input("court-drill-side", "value"),
        State("court-attempt-request", "data"),
        State("court-base", "data"),
        State("court-player", "value"),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        ClientsideFunction("hoopster", "drill_progress"),
        Output("court-plan-progress", "children"),
        Input("court-plan", "data"),
        Input("court-base", "data"),
        Input("court-queue", "data"),
        Input("court-player", "value"),
        Input("court-attempt-request", "data"),
        Input("court-selected-spot", "data"),
        State("court-confirmed", "data"),
    )

    app.clientside_callback(
        ClientsideFunction("hoopster", "drill_pb_text"),
        Output("court-drill-pb", "children"),
        Input("court-player", "value"),
        State("court-drill-pb-data", "data"),
        prevent_initial_call=True,
    )


def _register_entries_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the Entries list, entry detail, edit and delete callbacks.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        Each dialog's opened (and the detail feedback) is first written by its open
        callback; the other writers use allow_duplicate. court-base's first writer is
        controller_practice.confirm_add_player, so these use allow_duplicate too.
    """
    app.callback(
        Output("entries-modal", "opened"),
        Output("entries-list", "children"),
        Output("entries-feedback", "children"),
        Input("court-entries-open", "n_clicks"),
        State("entry-parent", "data"),
        State("court-player", "value"),
        State("device-clock", "data"),
        prevent_initial_call=True,
    )(open_entries)

    app.callback(
        Output("entry-detail-modal", "opened"),
        Output("entry-detail-body", "children"),
        Output("entry-detail-target", "data"),
        Output("entry-detail-feedback", "children"),
        Input({"type": ENTRY_ITEM_TYPE, "index": ALL}, "n_clicks"),
        State("entry-parent", "data"),
        State("court-player", "value"),
        State("device-clock", "data"),
        prevent_initial_call=True,
    )(open_entry_detail)

    app.callback(
        Output("entry-detail-feedback", "children", allow_duplicate=True),
        Output("entry-detail-modal", "opened", allow_duplicate=True),
        Output("entries-modal", "opened", allow_duplicate=True),
        Output("court-base", "data", allow_duplicate=True),
        Input("entry-detail-save", "n_clicks"),
        State("entry-detail-target", "data"),
        State("entry-parent", "data"),
        State("court-player", "value"),
        State("entry-edit-made", "value"),
        State("entry-edit-makes", "value"),
        State("entry-edit-attempts", "value"),
        State("entry-edit-points", "value"),
        State("entry-edit-shot-type", "value"),
        State("entry-edit-hand", "value"),
        State("entry-edit-creation", "value"),
        State("entry-edit-defense", "value"),
        State("court-queue", "data"),
        State("court-outbox", "data"),
        prevent_initial_call=True,
    )(save_entry_detail)

    app.clientside_callback(
        ClientsideFunction("hoopster", "open_drawer"),
        Output("entry-delete-modal", "opened"),
        Input("entry-detail-delete", "n_clicks"),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        ClientsideFunction("hoopster", "close_modal"),
        Output("entry-delete-modal", "opened", allow_duplicate=True),
        Input("entry-delete-cancel", "n_clicks"),
        prevent_initial_call=True,
    )

    app.callback(
        Output("entry-delete-modal", "opened", allow_duplicate=True),
        Output("entry-delete-feedback", "children"),
        Output("entry-detail-modal", "opened", allow_duplicate=True),
        Output("entries-modal", "opened", allow_duplicate=True),
        Output("court-base", "data", allow_duplicate=True),
        Input("entry-delete-confirm", "n_clicks"),
        State("entry-detail-target", "data"),
        State("entry-parent", "data"),
        State("court-player", "value"),
        State("court-queue", "data"),
        State("court-outbox", "data"),
        prevent_initial_call=True,
    )(confirm_entry_delete)


def register_entry_callbacks(app: Dash) -> None:
    """
    Description:
        Registers every entry-panel callback, shared by the practice and game screens:
        spot selection and view, the command dispatcher, display and executor, the mode
        switch, the X of Y / No spot / free-throw forms, shot details, Entries, entry detail,
        edit and delete.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        Single-writer rules: court_dispatch is the only writer of court-queue,
        court-confirmed, court-outbox and court-failure; entry_form_submit is the only
        writer of court-request; court-ack is written only by execute_court_command through
        set_props, and no callback has an Output on court-ack (an Output would recreate the
        loop that Dash prunes, plan §3.6). court-base is set by the executor (set_props) and
        by the edit and delete callbacks, and patched by the practice Add and Remove player
        callbacks. The components exist only on their own pages, so the app runs with
        suppress_callback_exceptions. details_remember has prevent_initial_call so the
        page-insertion run cannot overwrite the remembered details; details_restore runs on
        the initial call so they come back when the page opens (REQ-013). The drill
        sub-panel's callbacks (practice_drills plan §3.7) are registered here too, because
        the sub-panel is part of every entry panel.
    """
    _register_court_callbacks(app)
    _register_drill_callbacks(app)
    _register_entries_callbacks(app)
