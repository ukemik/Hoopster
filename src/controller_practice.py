"""Practice controller of Hoopster: the New practice, court and practice overview page
builders, practice creation (with a template and details), adding or removing a player on the
court, and the overview's details, placement and removal callbacks.

REQ-025, REQ-051, REQ-053, REQ-055, REQ-072, REQ-074, REQ-075, REQ-081, REQ-082. The court
screen's entry callbacks (spot selection, the command dispatcher and executor, Entries, edit
and delete) are shared with games and live in src/controller_entry.py
(register_entry_callbacks); run mode lives in src/controller_run.py.
"""

import logging
from datetime import date
from typing import cast

from dash import ALL, ClientsideFunction, Dash, Input, Output, Patch, State, ctx, no_update
from dash.development.base_component import Component
from dash.exceptions import PreventUpdate
from flask_login import current_user
from sqlalchemy.orm import Session

from src import data_handler
from src.app_controller import (
    DeviceClock,
    current_player_id,
    log_failure,
    new_reference_code,
    parse_player_value,
    run_write,
    run_write_with_reason,
)
from src.data_entry import (
    EntryParent,
    get_entry_totals,
    list_active_court_spots,
    list_shot_types,
)
from src.data_players import list_live_players
from src.data_practice import (
    add_adhoc_drill,
    add_practice_player,
    add_set_to_practice,
    count_live_results,
    count_practice_player_records,
    get_practice,
    get_practice_overview,
    insert_practice_players,
    insert_practice_session,
    list_addable_players,
    list_placed_sets,
    list_practice_drills,
    list_practice_participants,
    list_practice_types,
    list_selectable_drills,
    list_selectable_sets,
    list_selectable_templates,
    remove_placed_set,
    remove_practice_drill,
    remove_practice_player,
    start_practice_from_template,
    update_practice_details,
)
from src.layouts import not_found_page, player_options
from src.layouts_practice import (
    DURATION_MAX,
    DURATION_MIN,
    LOCATION_MAX_LENGTH,
    NOTES_MAX_LENGTH,
    court_layout,
    court_no_players_layout,
    court_player_picker,
    overview_body,
    practice_new_layout,
    practice_overview_layout,
    run_area_class,
    set_options,
)

logger = logging.getLogger(__name__)


# --- Page builders (REQ-007, REQ-008, REQ-025, REQ-055, REQ-074) ---

def build_practice_new_page(
    player_id: int, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds the New practice form, with the date defaulting to the device's date and the
        selected player preselected in the players picker.

    Parameters:
        player_id: the selected player from app_controller.current_player_id(); only the
            picker's default (Context Adaptation Table). Never a user id (COD-8-01).
        clock: the device clock; clock["today"] is the default date, or None.
        params: the route parameters (none).

    Return Values:
        Component: the page.

    Notes:
        Never the server's date (REQ-055). The practice's players are the picker's value at
        Start practice (create_practice). The template picker lists the current templates
        (practice_drills plan §3.5).
    """
    session = data_handler.get_session()
    practice_types = list_practice_types(session)
    players = list_live_players(session)
    templates = list_selectable_templates(session)
    return practice_new_layout(practice_types, clock["today"], players, player_id, templates)


def choose_court_player(participants: list[dict], preferred_player_id: int | None) -> int:
    """
    Description:
        The participant the court picker shows selected: the preferred player when it is a
        live participant, else the first participant.

    Parameters:
        participants: rows of data_practice.list_practice_participants, at least one.
        preferred_player_id: the selected player (current_player_id()) or a player just
            added, or None.

    Return Values:
        int: an h_data.players.player_id of one of the participants.

    Notes:
        Pure.
    """
    participant_ids = [int(participant["player_id"]) for participant in participants]
    if preferred_player_id in participant_ids:
        return int(preferred_player_id)
    return participant_ids[0]


def build_court_page(player_id: int, clock: DeviceClock, params: dict[str, str]) -> Component:
    """
    Description:
        Builds the court entry screen of a practice: its live participants in the player
        picker, the spots and shot types read from the database, and each participant's
        current field-goal and free-throw totals as the base.

    Parameters:
        player_id: the selected player from app_controller.current_player_id(); only the
            picker's default. Never a user id (COD-8-01).
        clock: the device clock (unused while building; the save time uses it later).
        params: {"practice_session_id": "<digits>"} from the URL.

    Return Values:
        Component: the court page; the no-players page when the practice has no live
        participant (e.g. every participant's profile was removed, Amendment A4); or the
        in-app not-found page when the practice does not exist or is deleted.

    Notes:
        The entry-parent store is written from the URL only after the live-practice check
        (Context Adaptation Table); every write re-checks it in SQL. Totals count live
        participant rows and free-form entries only (Amendment A3, plan D13).
    """
    session = data_handler.get_session()
    practice = get_practice(session, int(params["practice_session_id"]))
    if practice is None:
        return not_found_page()
    practice_session_id = int(practice["practice_session_id"])
    participants = list_practice_participants(session, practice_session_id)
    if not participants:
        return court_no_players_layout(practice)
    default_player_id = choose_court_player(participants, player_id)
    spots = list_active_court_spots(session)
    shot_types = list_shot_types(session)
    totals = get_entry_totals(session, EntryParent(PARENT_KIND_PRACTICE, practice_session_id))
    return court_layout(practice, participants, default_player_id, totals, spots, shot_types)


# --- Practice creation (REQ-025, REQ-055) ---

MESSAGE_CHOOSE_TYPE = "Choose a practice type."
MESSAGE_CHOOSE_DATE = "Choose a date."
MESSAGE_CHOOSE_PLAYERS = "Choose at least one player."
MESSAGE_CREATE_FAILED = "Could not start the practice. Reference: {ref}"
MESSAGE_CHOOSE_TEMPLATE = "Choose a template from the list, or none."
MESSAGE_DURATION = f"Duration must be {DURATION_MIN} to {DURATION_MAX} minutes."
MESSAGE_LOCATION = f"Location is limited to {LOCATION_MAX_LENGTH} characters."
MESSAGE_NOTES = f"Notes are limited to {NOTES_MAX_LENGTH} characters."


def parse_id(value: object) -> int | None:
    """
    Description:
        Reads a Select or store id: an int (not a bool), or a string of 1 to 18 ASCII digits
        as dmc sends it.

    Parameters:
        value: the value.

    Return Values:
        int | None: the id, or None for anything else (also None and "").

    Notes:
        Pure. Shape only: every write re-checks the id in SQL.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value > 0 else None
    return parse_player_value(value)


def _parse_duration(value: object) -> tuple[int | None, bool]:
    """
    Description:
        Reads the duration field: empty, or a whole number of minutes.

    Parameters:
        value: the NumberInput value: "", None, an int, a whole float or digits as text.

    Return Values:
        tuple[int | None, bool]: (minutes or None for empty, whether the value is valid).

    Notes:
        Pure. The range is checked by parse_practice_details.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, True
    if isinstance(value, bool):
        return None, False
    if isinstance(value, int):
        return value, True
    if isinstance(value, float) and value.is_integer():
        return int(value), True
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        return int(value.strip()), True
    return None, False


def _optional_text(value: object) -> str | None:
    """
    Description:
        A text field's value, stripped; blank is None (stored as NULL, plan §3.5).

    Parameters:
        value: the field's value.

    Return Values:
        str | None: the stripped text, or None when blank or not text.

    Notes:
        Pure.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def parse_practice_details(
    duration: object, location: object, notes: object
) -> tuple[dict[str, object] | None, str | None]:
    """
    Description:
        Checks the practice detail fields (REQ-025, plan §3.5): duration 1-600 minutes,
        location up to 100 characters, notes up to 4000; blanks become None.

    Parameters:
        duration: the duration NumberInput value.
        location: the location TextInput value.
        notes: the notes Textarea value.

    Return Values:
        tuple[dict[str, object] | None, str | None]: ({"duration_minutes", "location",
        "notes"}, None) when valid, else (None, the message to show).

    Notes:
        Pure. The table CHECKs back the same limits.
    """
    minutes, readable = _parse_duration(duration)
    if not readable or (minutes is not None and not DURATION_MIN <= minutes <= DURATION_MAX):
        return None, MESSAGE_DURATION
    place = _optional_text(location)
    if place is not None and len(place) > LOCATION_MAX_LENGTH:
        return None, MESSAGE_LOCATION
    text = _optional_text(notes)
    if text is not None and len(text) > NOTES_MAX_LENGTH:
        return None, MESSAGE_NOTES
    return {"duration_minutes": minutes, "location": place, "notes": text}, None


def parse_session_date(value: object) -> date | None:
    """
    Description:
        Reads the date picker's value.

    Parameters:
        value: practice-new-date.value, expected "YYYY-MM-DD".

    Return Values:
        date | None: the calendar date, or None when missing or unreadable.

    Notes:
        Pure. The date is stored as entered (REQ-055).
    """
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_player_values(values: object) -> list[int] | None:
    """
    Description:
        Reads the New practice players picker's value.

    Parameters:
        values: practice-new-players.value, a list of player ids as strings, in the order
            they were picked.

    Return Values:
        list[int] | None: the distinct player ids in pick order, or None when the value is
        not a list, is empty, or holds anything that is not a player id.

    Notes:
        Pure. Shape only: create_practice checks the ids against the live players, and
        insert_practice_players refuses any that is not live in SQL.
    """
    if not isinstance(values, list) or not values:
        return None
    player_ids: list[int] = []
    for value in values:
        player_id = parse_player_value(value)
        if player_id is None:
            return None
        if player_id not in player_ids:
            player_ids.append(player_id)
    return player_ids


def create_practice(
    n_clicks: int | None,
    practice_type: str | None,
    session_date: str | None,
    player_values: list[str] | None,
    template_value: str | None = None,
    duration: object = None,
    location: str | None = None,
    notes: str | None = None,
) -> tuple[str, object]:
    """
    Description:
        The Start practice callback: validates the form, creates the practice with its
        details and one participant row per chosen player, starts it from the chosen
        template, and navigates to its first drill (with a template) or its court screen.

    Parameters:
        n_clicks: practice-new-submit.n_clicks.
        practice_type: practice-new-type.value, an h_ref.practice_types code.
        session_date: practice-new-date.value, "YYYY-MM-DD".
        player_values: practice-new-players.value, player ids as strings in pick order.
        template_value: practice-new-template.value, a template LINEAGE id as a string, or
            None for no template.
        duration: practice-new-duration.value (minutes, optional).
        location: practice-new-location.value (optional).
        notes: practice-new-notes.value (optional).

    Return Values:
        tuple[str, object]: practice-new-feedback text and url.pathname
        ("/practice/<id>/run" with a template, "/practice/<id>/court" without, D2; else
        no_update).

    Notes:
        A boundary (COD-4-04): failures show the database's own message when there is one
        (e.g. a template retired since the form opened), else a reference. The acting user
        is current_user.user_id; the participants are the picker's value, checked against
        the live players here and in SQL (Context Adaptation Table). The practice, its
        participant rows and the template's sets are written in one run_write_with_reason,
        so a refusal leaves no practice behind. Raises PreventUpdate unless Start practice
        was actually clicked: Dash also runs this callback when the router inserts the New
        practice page, because its Output url lives in the shell, and prevent_initial_call
        does not stop that run (plan §3.5).
    """
    # The page-insertion run has no click; url is outside the page, so Dash queues it anyway.
    if not n_clicks:
        raise PreventUpdate
    try:
        session = data_handler.get_session()
        type_codes = {str(row["practice_type_code"]) for row in list_practice_types(session)}
        live_player_ids = {int(row["player_id"]) for row in list_live_players(session)}
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Practice form check failed (ref %s)", ref)
        return MESSAGE_CREATE_FAILED.format(ref=ref), no_update

    if not isinstance(practice_type, str) or practice_type not in type_codes:
        return MESSAGE_CHOOSE_TYPE, no_update
    chosen_date = parse_session_date(session_date)
    if chosen_date is None:
        return MESSAGE_CHOOSE_DATE, no_update
    player_ids = parse_player_values(player_values)
    if player_ids is None or not set(player_ids) <= live_player_ids:
        return MESSAGE_CHOOSE_PLAYERS, no_update
    template_id: int | None = None
    if template_value not in (None, ""):
        template_id = parse_id(template_value)
        if template_id is None:
            return MESSAGE_CHOOSE_TEMPLATE, no_update
    details, message = parse_practice_details(duration, location, notes)
    if details is None:
        return message or MESSAGE_DURATION, no_update
    # Narrowed names for the closure below (a closure does not keep the checks' narrowing).
    chosen_type: str = practice_type
    chosen_day: date = chosen_date
    chosen_player_ids: list[int] = player_ids
    chosen_details: dict[str, object] = details
    chosen_template_id: int | None = template_id

    def work(write_session: Session) -> int:
        """
        Description:
            Creates the practice with its details and participant rows, and starts it from
            the template when one was chosen.

        Parameters:
            write_session: the write session.

        Return Values:
            int: the new practice_session_id.

        Notes:
            insert_practice_players raises ValueError when a player is no longer live, and
            h_data.fn_add_template_to_practice raises when the template is no longer
            current; either rolls back the practice too.
        """
        practice_session_id = insert_practice_session(
            write_session,
            chosen_day,
            chosen_type,
            duration_minutes=cast(int | None, chosen_details["duration_minutes"]),
            location=cast(str | None, chosen_details["location"]),
            notes=cast(str | None, chosen_details["notes"]),
        )
        insert_practice_players(write_session, practice_session_id, chosen_player_ids)
        if chosen_template_id is not None:
            start_practice_from_template(write_session, practice_session_id, chosen_template_id)
        return practice_session_id

    result, reason = run_write_with_reason(current_user.user_id, work, "create practice")
    if not result.ok:
        return reason or MESSAGE_CREATE_FAILED.format(ref=result.ref), no_update
    logger.info(
        "Created practice %s with players %s, template %s",
        result.value,
        chosen_player_ids,
        chosen_template_id,
    )
    if chosen_template_id is not None:
        return "", f"/practice/{result.value}/run"
    return "", f"/practice/{result.value}/court"


# --- Entry parent of the practice court ---

PARENT_KIND_PRACTICE = "practice"


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


# --- Court: add or remove a player (REQ-074; Amendments A2, A3) ---

MESSAGE_ALL_PLAYERS_IN = "Every player profile is already in this practice."
MESSAGE_CHOOSE_PLAYER = "Choose a player."
MESSAGE_ADD_PLAYER_FAILED = "Could not add the player. Reference: {ref}"
MESSAGE_REMOVE_PLAYER_QUESTION = (
    "Remove {name} from this practice? A player added again starts fresh in this practice."
)
MESSAGE_WAIT_FOR_SHOTS = "Wait until {name}'s shots are saved, or undo them first."
MESSAGE_LAST_PLAYER = "A practice needs at least one player."
MESSAGE_HAS_RECORDS = "{name} has shots or results in this practice and cannot be removed."
MESSAGE_REMOVE_PLAYER_FAILED = "Could not remove the player. Reference: {ref}"
MESSAGE_PICKER_RELOAD_FAILED = (
    "Done, but the player list could not be reloaded. Reopen the practice. Reference: {ref}"
)
# The court-base entry of a player added during the visit: a new participant row starts with
# nothing recorded (Amendment A3); field goals and free throws (data_entry.get_entry_totals).
EMPTY_TALLY: dict[str, int] = {"makes": 0, "attempts": 0, "ft_makes": 0, "ft_attempts": 0}


def _parent_practice_id(parent: object) -> int | None:
    """
    Description:
        Reads the practice id from the entry-parent store.

    Parameters:
        parent: entry-parent.data, {"kind": "practice", "id": <int>}.

    Return Values:
        int | None: the practice id, or None when the store is not a practice parent.

    Notes:
        Pure. Every write re-checks the practice in SQL.
    """
    if not isinstance(parent, dict) or parent.get("kind") != PARENT_KIND_PRACTICE:
        return None
    practice_session_id = parent.get("id")
    return practice_session_id if _is_int(practice_session_id) else None


def _participant_name(participants: list[dict], player_id: int) -> str | None:
    """
    Description:
        Finds a participant's display name.

    Parameters:
        participants: rows of data_practice.list_practice_participants.
        player_id: the player to look for.

    Return Values:
        str | None: the display name, or None when the player is not a live participant.

    Notes:
        Pure.
    """
    for participant in participants:
        if int(participant["player_id"]) == player_id:
            return str(participant["display_name"])
    return None


def has_visit_shots(player_id: int, queue: object, outbox: object, confirmed: object) -> bool:
    """
    Description:
        Whether the browser holds shots of a player from this visit: queued commands, the
        command in flight, or shots confirmed during the visit.

    Parameters:
        player_id: the player to remove.
        queue: court-queue.data, a list of commands carrying "player_id".
        outbox: court-outbox.data, the command in flight or None.
        confirmed: court-confirmed.data, a list of entries carrying "player_id".

    Return Values:
        bool: True when any of them belongs to the player.

    Notes:
        Pure. A shot confirmed during the visit is also counted by the database; checking
        it here first gives the "wait or undo" message before the "has shots" one.
    """
    entries: list[object] = []
    for store in (queue, confirmed):
        if isinstance(store, list):
            entries.extend(store)
    if isinstance(outbox, dict):
        entries.append(outbox)
    return any(
        isinstance(entry, dict) and entry.get("player_id") == player_id for entry in entries
    )


def _rebuilt_picker(practice_session_id: int, preferred_player_id: int | None) -> Component:
    """
    Description:
        Rebuilds the court player picker from the practice's live participants.

    Parameters:
        practice_session_id: the entry-parent practice id.
        preferred_player_id: the participant to show selected when present (else the first).

    Return Values:
        Component: the new children of court-player-area.

    Notes:
        Raises LookupError when the practice has no live participant left; the callers are
        boundaries and show a reference.
    """
    participants = list_practice_participants(data_handler.get_session(), practice_session_id)
    if not participants:
        raise LookupError(f"practice {practice_session_id} has no live participant")
    return court_player_picker(
        participants, choose_court_player(participants, preferred_player_id)
    )


def open_add_player(n_clicks: int | None, parent: dict | None) -> tuple[bool, list, None, str]:
    """
    Description:
        "+ Add player": opens the dialog with the players who can be added (live profiles
        without a live participant row in this practice).

    Parameters:
        n_clicks: court-add-player-open.n_clicks.
        parent: entry-parent.data.

    Return Values:
        tuple[bool, list, None, str]: court-add-player-modal.opened (True),
        court-add-player-select.data, court-add-player-select.value (None) and
        court-add-player-feedback text.

    Notes:
        A boundary (COD-4-04): a failed read opens the dialog with a reference. With nobody
        to add, the dialog says so.
    """
    if not n_clicks:
        raise PreventUpdate
    practice_session_id = _parent_practice_id(parent)
    if practice_session_id is None:
        ref = new_reference_code()
        logger.error("Add player refused (ref %s): entry parent is not a practice id", ref)
        return True, [], None, MESSAGE_ADD_PLAYER_FAILED.format(ref=ref)
    try:
        addable = list_addable_players(data_handler.get_session(), practice_session_id)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Addable players read failed (ref %s)", ref)
        return True, [], None, MESSAGE_ADD_PLAYER_FAILED.format(ref=ref)
    if not addable:
        return True, [], None, MESSAGE_ALL_PLAYERS_IN
    return True, player_options(addable), None, ""


def confirm_add_player(
    n_clicks: int | None, parent: dict | None, value: str | None
) -> tuple[object, object, object, str]:
    """
    Description:
        The Add button of the dialog: adds the chosen player to the practice as a new
        participant row, rebuilds the picker with that player selected, and gives the player
        an empty tally in court-base.

    Parameters:
        n_clicks: court-add-player-confirm.n_clicks.
        parent: entry-parent.data, the practice (Context Adaptation Table).
        value: court-add-player-select.value, the player to add as a string.

    Return Values:
        tuple[object, object, object, str]: court-player-area.children, court-base.data (a
        dash.Patch adding str(player_id) = 0/0), court-add-player-modal.opened (False on
        success) and court-add-player-feedback text; no_update where nothing changes.

    Notes:
        A boundary (COD-4-04). h_data.fn_add_practice_player always inserts a new
        participant row, so a player removed earlier starts again at 0 / 0 (Amendment A3);
        it refuses a player who already has a live row.
    """
    if not n_clicks:
        raise PreventUpdate
    player_id = parse_player_value(value)
    if player_id is None:
        return no_update, no_update, no_update, MESSAGE_CHOOSE_PLAYER
    practice_session_id = _parent_practice_id(parent)
    if practice_session_id is None:
        ref = new_reference_code()
        logger.error("Add player refused (ref %s): entry parent is not a practice id", ref)
        return no_update, no_update, no_update, MESSAGE_ADD_PLAYER_FAILED.format(ref=ref)
    added_player_id: int = player_id
    target_practice_id: int = practice_session_id
    result = run_write(
        current_user.user_id,
        lambda session: add_practice_player(session, target_practice_id, added_player_id),
        "add practice player",
    )
    if not result.ok:
        return no_update, no_update, no_update, MESSAGE_ADD_PLAYER_FAILED.format(ref=result.ref)
    logger.info(
        "Added player %s to practice %s (participant row %s)",
        added_player_id,
        target_practice_id,
        result.value,
    )
    try:
        picker = _rebuilt_picker(target_practice_id, added_player_id)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Court picker reload failed (ref %s)", ref)
        return no_update, no_update, no_update, MESSAGE_PICKER_RELOAD_FAILED.format(ref=ref)
    base_patch = Patch()
    base_patch[str(added_player_id)] = dict(EMPTY_TALLY)
    return picker, base_patch, False, ""


def open_remove_player(
    n_clicks: int | None, parent: dict | None, player_value: str | None
) -> tuple[bool, str, str]:
    """
    Description:
        "Remove player": opens the dialog asking to remove the player picked on the court.

    Parameters:
        n_clicks: court-remove-player-open.n_clicks.
        parent: entry-parent.data.
        player_value: court-player.value, the player to remove as a string.

    Return Values:
        tuple[bool, str, str]: court-remove-player-modal.opened (True),
        court-remove-player-text children and court-remove-player-feedback text.

    Notes:
        A boundary (COD-4-04). Nothing is checked or written here; confirm_remove_player
        does both.
    """
    if not n_clicks:
        raise PreventUpdate
    player_id = parse_player_value(player_value)
    practice_session_id = _parent_practice_id(parent)
    try:
        if player_id is None or practice_session_id is None:
            raise ValueError("court player or entry parent is not an id")
        participants = list_practice_participants(
            data_handler.get_session(), practice_session_id
        )
        name = _participant_name(participants, player_id)
        if name is None:
            raise LookupError(f"player {player_id} is not a live participant")
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Remove player dialog failed (ref %s)", ref)
        return True, "", MESSAGE_REMOVE_PLAYER_FAILED.format(ref=ref)
    return True, MESSAGE_REMOVE_PLAYER_QUESTION.format(name=name), ""


def _removal_refusal(
    practice_session_id: int,
    player_id: int,
    queue: object,
    outbox: object,
    confirmed: object,
) -> str | None:
    """
    Description:
        Checks, in order, why a player cannot be removed from the practice: shots of this
        visit, being the last player, or anything recorded.

    Parameters:
        practice_session_id: the entry-parent practice id.
        player_id: the player to remove (court-player.value).
        queue: court-queue.data.
        outbox: court-outbox.data.
        confirmed: court-confirmed.data.

    Return Values:
        str | None: the message to show, or None when the removal may go ahead.

    Notes:
        Reads the database; exceptions propagate to the calling boundary. Raises
        LookupError when the player is not a live participant. h_data.fn_remove_practice_player
        re-checks the last two rules under a lock.
    """
    session = data_handler.get_session()
    participants = list_practice_participants(session, practice_session_id)
    name = _participant_name(participants, player_id)
    if name is None:
        raise LookupError(f"player {player_id} is not a live participant")
    if has_visit_shots(player_id, queue, outbox, confirmed):
        return MESSAGE_WAIT_FOR_SHOTS.format(name=name)
    if len(participants) <= 1:
        return MESSAGE_LAST_PLAYER
    if count_practice_player_records(session, practice_session_id, player_id) > 0:
        return MESSAGE_HAS_RECORDS.format(name=name)
    return None


def confirm_remove_player(
    n_clicks: int | None,
    parent: dict | None,
    player_value: str | None,
    queue: list | None,
    outbox: dict | None,
    confirmed: list | None,
) -> tuple[object, object, object, str]:
    """
    Description:
        The Remove button of the dialog: removes the picked player from the practice when
        nothing is recorded for them, rebuilds the picker and drops the player from
        court-base.

    Parameters:
        n_clicks: court-remove-player-confirm.n_clicks.
        parent: entry-parent.data, the practice (Context Adaptation Table).
        player_value: court-player.value, the player to remove (Context Adaptation Table).
        queue: court-queue.data.
        outbox: court-outbox.data.
        confirmed: court-confirmed.data.

    Return Values:
        tuple[object, object, object, str]: court-player-area.children, court-base.data (a
        dash.Patch deleting str(player_id)), court-remove-player-modal.opened (False on
        success) and court-remove-player-feedback text; no_update where nothing changes.

    Notes:
        A boundary (COD-4-04). Refused, with nothing written, when the browser holds shots
        of the player from this visit, when it is the last player, or when the player has
        records. The removed participant row is never restored; adding the player again
        creates a new one (Amendment A3). The picker then shows the selected player if
        still a participant, else the first.
    """
    if not n_clicks:
        raise PreventUpdate
    player_id = parse_player_value(player_value)
    practice_session_id = _parent_practice_id(parent)
    if player_id is None or practice_session_id is None:
        ref = new_reference_code()
        logger.error("Remove player refused (ref %s): court player or parent is not an id", ref)
        return no_update, no_update, no_update, MESSAGE_REMOVE_PLAYER_FAILED.format(ref=ref)
    removed_player_id: int = player_id
    checked_practice_id: int = practice_session_id
    try:
        refusal = _removal_refusal(
            checked_practice_id, removed_player_id, queue, outbox, confirmed
        )
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Remove player check failed (ref %s)", ref)
        return no_update, no_update, no_update, MESSAGE_REMOVE_PLAYER_FAILED.format(ref=ref)
    if refusal is not None:
        logger.error(
            "Removal of player %s from practice %s refused: %s",
            removed_player_id,
            checked_practice_id,
            refusal,
        )
        return no_update, no_update, no_update, refusal

    result = run_write(
        current_user.user_id,
        lambda session: remove_practice_player(session, checked_practice_id, removed_player_id),
        "remove practice player",
    )
    if not result.ok:
        return (
            no_update,
            no_update,
            no_update,
            MESSAGE_REMOVE_PLAYER_FAILED.format(ref=result.ref),
        )
    logger.info(
        "Removed player %s from practice %s (%s rows marked)",
        removed_player_id,
        checked_practice_id,
        result.value,
    )
    try:
        picker = _rebuilt_picker(checked_practice_id, current_player_id())
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Court picker reload failed (ref %s)", ref)
        return no_update, no_update, no_update, MESSAGE_PICKER_RELOAD_FAILED.format(ref=ref)
    base_patch = Patch()
    del base_patch[str(removed_player_id)]
    return picker, base_patch, False, ""


# --- Practice overview (practice_drills 2b; REQ-025, REQ-051, REQ-053, REQ-081, REQ-082) ---

MESSAGE_DETAILS_SAVED = "Saved."
MESSAGE_DETAILS_FAILED = "Could not save the details. Reference: {ref}"
MESSAGE_PRACTICE_GONE = "This practice no longer exists."
MESSAGE_SET_PLACED = "That drill set is already in this practice."
MESSAGE_CHOOSE_SET = "Choose a drill set to add."
MESSAGE_CHOOSE_DRILL = "Choose a drill to add."
MESSAGE_ADD_FAILED = "Could not add it. Reference: {ref}"
MESSAGE_REFRESH_FAILED = "Done, but the page could not be refreshed. Reopen it. Reference: {ref}"
MESSAGE_REMOVE_QUESTION = "Remove {name} from this practice?"
MESSAGE_REMOVE_RESULTS = " It has {count} result(s); they can be restored for 30 days."
MESSAGE_REMOVE_GONE = "This is no longer in the practice."
MESSAGE_REMOVE_FAILED = "Could not remove it. Reference: {ref}"
OV_REMOVE_DRILL_TYPE = "ov-remove-drill"
OV_REMOVE_SET_TYPE = "ov-remove-set"
REMOVE_KIND_DRILL = "drill"
REMOVE_KIND_SET = "set"


def build_practice_overview_page(
    player_id: int, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds the practice overview /practice/<id> (plan §3.5): header, details, Run
        practice and Shoot on court, the sets and drills in run order, Add drill set and Add
        drill.

    Parameters:
        player_id: the selected player (current_player_id()); unused here (the overview is
            shared by the participants). Never a user id (COD-8-01).
        clock: the device clock (unused).
        params: {"practice_session_id": "<digits>"} from the URL.

    Return Values:
        Component: the page, or the not-found page when the practice is not live.

    Notes:
        ov-context is written from the URL only after the live check (Context Adaptation
        Table); every write re-checks it in SQL. A handful of reads, none in a loop.
    """
    session = data_handler.get_session()
    practice_session_id = int(params["practice_session_id"])
    practice = get_practice_overview(session, practice_session_id)
    if practice is None:
        return not_found_page()
    page_practice = dict(practice)
    page_practice["participants"] = list_practice_participants(session, practice_session_id)
    sets = list_placed_sets(session, practice_session_id)
    drills = list_practice_drills(session, practice_session_id)
    options = {
        "sets": list_selectable_sets(session, practice_session_id),
        "drills": list_selectable_drills(session),
    }
    return practice_overview_layout(page_practice, sets, drills, options)


def _context_practice_id(context: object) -> int | None:
    """
    Description:
        Reads the practice id from the ov-context store.

    Parameters:
        context: ov-context.data, {"practice_session_id": <int>}.

    Return Values:
        int | None: the practice id, or None when the store has another shape.

    Notes:
        Pure. Every write re-checks the practice in SQL.
    """
    if not isinstance(context, dict):
        return None
    value = context.get("practice_session_id")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _overview_refresh(practice_session_id: int) -> tuple[list[Component], list[dict], str]:
    """
    Description:
        Re-reads what an overview write changes: the body, the Add drill set options and the
        Run practice link's visibility.

    Parameters:
        practice_session_id: the practice from ov-context.

    Return Values:
        tuple[list[Component], list[dict], str]: ov-body children, ov-add-set data and
        ov-run-area className.

    Notes:
        Reads only; exceptions propagate to the calling boundary.
    """
    session = data_handler.get_session()
    sets = list_placed_sets(session, practice_session_id)
    drills = list_practice_drills(session, practice_session_id)
    participants = list_practice_participants(session, practice_session_id)
    body = overview_body(practice_session_id, sets, drills, len(participants))
    selectable = set_options(list_selectable_sets(session, practice_session_id))
    return body, selectable, run_area_class(bool(drills))


def save_practice_details(
    n_clicks: int | None,
    context: dict | None,
    duration: object,
    location: str | None,
    notes: str | None,
) -> tuple[str, str]:
    """
    Description:
        "Save details" on the overview: saves duration, location and notes (REQ-025).

    Parameters:
        n_clicks: ov-save.n_clicks.
        context: ov-context.data, the practice (Context Adaptation Table).
        duration: ov-duration.value.
        location: ov-location.value.
        notes: ov-notes.value.

    Return Values:
        tuple[str, str]: ov-details-saved text ("Saved." on success) and
        ov-details-feedback text (the problem, else "").

    Notes:
        A boundary (COD-4-04).
    """
    if not n_clicks:
        raise PreventUpdate
    practice_session_id = _context_practice_id(context)
    details, message = parse_practice_details(duration, location, notes)
    if details is None:
        return "", message or MESSAGE_DURATION
    if practice_session_id is None:
        ref = new_reference_code()
        logger.error("Practice details refused (ref %s): ov-context is not a practice id", ref)
        return "", MESSAGE_DETAILS_FAILED.format(ref=ref)
    target_id: int = practice_session_id
    saved_details: dict[str, object] = details
    result = run_write(
        current_user.user_id,
        lambda session: update_practice_details(
            session,
            target_id,
            cast(int | None, saved_details["duration_minutes"]),
            cast(str | None, saved_details["location"]),
            cast(str | None, saved_details["notes"]),
        ),
        "save practice details",
    )
    if not result.ok:
        return "", MESSAGE_DETAILS_FAILED.format(ref=result.ref)
    if not result.value:
        logger.warning("Practice %s is no longer live; details not saved", target_id)
        return "", MESSAGE_PRACTICE_GONE
    return MESSAGE_DETAILS_SAVED, ""


def _refreshed_outputs(practice_session_id: int, feedback: str) -> tuple[object, ...]:
    """
    Description:
        The outputs shared by Add drill set, Add drill and Remove after a successful write:
        the refreshed body, set options and Run link, plus the feedback.

    Parameters:
        practice_session_id: the practice.
        feedback: the feedback text to show ("" on plain success).

    Return Values:
        tuple[object, ...]: (ov-body children, ov-add-set data, ov-run-area className,
        feedback); when the re-read fails, no_update for the first three and a "reopen"
        message with a reference.

    Notes:
        A boundary helper: a failed read is logged with a reference, never raised.
    """
    try:
        body, selectable, run_class = _overview_refresh(practice_session_id)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Overview refresh failed (ref %s)", ref)
        return no_update, no_update, no_update, MESSAGE_REFRESH_FAILED.format(ref=ref)
    return body, selectable, run_class, feedback


def add_set(
    n_clicks: int | None, context: dict | None, set_value: str | None
) -> tuple[object, object, object, str, object]:
    """
    Description:
        "Add drill set" on the overview: places the chosen set's current version at the end
        of the practice (REQ-051, REQ-053), unless it is already placed (D14).

    Parameters:
        n_clicks: ov-add-set-go.n_clicks.
        context: ov-context.data, the practice.
        set_value: ov-add-set.value, a set LINEAGE id as a string.

    Return Values:
        tuple[object, object, object, str, object]: ov-body children, ov-add-set data,
        ov-add-set value (cleared on success), ov-add-feedback text and ov-run-area
        className; no_update where nothing changes.

    Notes:
        A boundary (COD-4-04). The database's own refusal (a set no longer current, or one
        with no drills) is shown as its message (plan §3.2).
    """
    if not n_clicks:
        raise PreventUpdate
    practice_session_id = _context_practice_id(context)
    drill_set_id = parse_id(set_value)
    if drill_set_id is None:
        return no_update, no_update, no_update, MESSAGE_CHOOSE_SET, no_update
    if practice_session_id is None:
        ref = new_reference_code()
        logger.error("Add drill set refused (ref %s): ov-context is not a practice id", ref)
        return no_update, no_update, no_update, MESSAGE_ADD_FAILED.format(ref=ref), no_update
    target_id: int = practice_session_id
    chosen_set_id: int = drill_set_id
    result, reason = run_write_with_reason(
        current_user.user_id,
        lambda session: add_set_to_practice(session, target_id, chosen_set_id),
        "add drill set to practice",
    )
    if not result.ok:
        message = reason or MESSAGE_ADD_FAILED.format(ref=result.ref)
        return no_update, no_update, no_update, message, no_update
    if result.value is None:
        logger.error("Drill set %s is already in practice %s", chosen_set_id, target_id)
        return no_update, no_update, no_update, MESSAGE_SET_PLACED, no_update
    logger.info("Placed drill set %s in practice %s", chosen_set_id, target_id)
    body, selectable, run_class, feedback = _refreshed_outputs(target_id, "")
    return body, selectable, None, str(feedback), run_class


def add_drill(
    n_clicks: int | None, context: dict | None, drill_value: str | None
) -> tuple[object, object, str, object]:
    """
    Description:
        "Add drill" on the overview: adds a single (ad-hoc) drill with its current version
        at the end of the practice (REQ-051).

    Parameters:
        n_clicks: ov-add-drill-go.n_clicks.
        context: ov-context.data, the practice.
        drill_value: ov-add-drill.value, a drill LINEAGE id as a string.

    Return Values:
        tuple[object, object, str, object]: ov-body children, ov-add-drill value (cleared on
        success), ov-add-feedback text and ov-run-area className.

    Notes:
        A boundary (COD-4-04).
    """
    if not n_clicks:
        raise PreventUpdate
    practice_session_id = _context_practice_id(context)
    drill_id = parse_id(drill_value)
    if drill_id is None:
        return no_update, no_update, MESSAGE_CHOOSE_DRILL, no_update
    if practice_session_id is None:
        ref = new_reference_code()
        logger.error("Add drill refused (ref %s): ov-context is not a practice id", ref)
        return no_update, no_update, MESSAGE_ADD_FAILED.format(ref=ref), no_update
    target_id: int = practice_session_id
    chosen_drill_id: int = drill_id
    result, reason = run_write_with_reason(
        current_user.user_id,
        lambda session: add_adhoc_drill(session, target_id, chosen_drill_id),
        "add drill to practice",
    )
    if not result.ok:
        return no_update, no_update, reason or MESSAGE_ADD_FAILED.format(ref=result.ref), no_update
    logger.info(
        "Added drill %s to practice %s as practice drill %s",
        chosen_drill_id,
        target_id,
        result.value,
    )
    body, _selectable, run_class, feedback = _refreshed_outputs(target_id, "")
    return body, None, str(feedback), run_class


def _removal_target(triggered: object) -> dict[str, object] | None:
    """
    Description:
        Reads an overview Remove button's pattern id as an ov-remove-target value.

    Parameters:
        triggered: ctx.triggered_id.

    Return Values:
        dict[str, object] | None: {"kind": "drill" | "set", "id": int}, or None.

    Notes:
        Pure.
    """
    if not isinstance(triggered, dict):
        return None
    index = triggered.get("index")
    if not isinstance(index, int) or isinstance(index, bool):
        return None
    kinds = {OV_REMOVE_DRILL_TYPE: REMOVE_KIND_DRILL, OV_REMOVE_SET_TYPE: REMOVE_KIND_SET}
    kind = kinds.get(str(triggered.get("type")))
    return {"kind": kind, "id": index} if kind else None


def _removal_question(practice_session_id: int, target: dict[str, object]) -> str | None:
    """
    Description:
        The question of the overview's remove dialog: "Remove <name> from this practice?",
        plus the result count and the 30-day restore when results exist (REQ-005, REQ-081).

    Parameters:
        practice_session_id: the practice.
        target: {"kind": "drill" | "set", "id"}.

    Return Values:
        str | None: the question, or None when the item is no longer live in the practice.

    Notes:
        Reads only; exceptions propagate to the calling boundary.
    """
    session = data_handler.get_session()
    item_id = cast(int, target["id"])
    if target["kind"] == REMOVE_KIND_DRILL:
        rows = list_practice_drills(session, practice_session_id)
        names = [str(row["drill_name"]) for row in rows if row["practice_drill_id"] == item_id]
        count = count_live_results(session, practice_session_id, practice_drill_id=item_id)
    else:
        rows = list_placed_sets(session, practice_session_id)
        names = [str(row["set_name"]) for row in rows if row["practice_drill_set_id"] == item_id]
        count = count_live_results(session, practice_session_id, practice_drill_set_id=item_id)
    if not names:
        return None
    question = MESSAGE_REMOVE_QUESTION.format(name=names[0])
    if count > 0:
        question += MESSAGE_REMOVE_RESULTS.format(count=count)
    return question


def open_remove(
    drill_clicks: list[int | None], set_clicks: list[int | None], context: dict | None
) -> tuple[bool, str, object, str]:
    """
    Description:
        A Remove drill or Remove set tap on the overview: opens the confirmation with the
        item's name and its results (REQ-005, REQ-082).

    Parameters:
        drill_clicks: {"type": "ov-remove-drill", "index": ALL}.n_clicks.
        set_clicks: {"type": "ov-remove-set", "index": ALL}.n_clicks.
        context: ov-context.data, the practice.

    Return Values:
        tuple[bool, str, object, str]: ov-remove-modal.opened (True), ov-remove-text,
        ov-remove-target.data ({"kind", "id"}, or None when gone) and ov-remove-feedback.

    Notes:
        Raises PreventUpdate unless a button was really tapped: a re-rendered body fires
        this callback with empty clicks (plan §3.8.2). A boundary (COD-4-04).
    """
    if not ctx.triggered or not ctx.triggered[0].get("value"):
        raise PreventUpdate
    target = _removal_target(ctx.triggered_id)
    practice_session_id = _context_practice_id(context)
    try:
        if target is None or practice_session_id is None:
            raise ValueError("remove target or ov-context is not an id")
        question = _removal_question(practice_session_id, target)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Remove dialog failed (ref %s)", ref)
        return True, "", None, MESSAGE_REMOVE_FAILED.format(ref=ref)
    if question is None:
        return True, "", None, MESSAGE_REMOVE_GONE
    return True, question, target, ""


def confirm_remove(
    n_clicks: int | None, context: dict | None, target: dict | None
) -> tuple[object, str, object, object, object]:
    """
    Description:
        "Remove" in the overview's confirmation: soft-deletes the drill or placed set with
        its results (restorable for 30 days) and refreshes the overview (REQ-081, REQ-082).

    Parameters:
        n_clicks: ov-remove-confirm.n_clicks.
        context: ov-context.data, the practice (Context Adaptation Table).
        target: ov-remove-target.data, {"kind": "drill" | "set", "id"}.

    Return Values:
        tuple[object, str, object, object, object]: ov-remove-modal.opened (False on
        success), ov-remove-feedback text, ov-body children, ov-add-set data and
        ov-run-area className; no_update where nothing changes.

    Notes:
        A boundary (COD-4-04). The data layer locks the practice and re-checks that the item
        is live in it before h_data.fn_soft_delete.
    """
    if not n_clicks:
        raise PreventUpdate
    practice_session_id = _context_practice_id(context)
    kind = target.get("kind") if isinstance(target, dict) else None
    item_id = target.get("id") if isinstance(target, dict) else None
    if (
        practice_session_id is None
        or kind not in (REMOVE_KIND_DRILL, REMOVE_KIND_SET)
        or not isinstance(item_id, int)
        or isinstance(item_id, bool)
    ):
        ref = new_reference_code()
        logger.error("Overview removal refused (ref %s): context or target is not an id", ref)
        return no_update, MESSAGE_REMOVE_FAILED.format(ref=ref), no_update, no_update, no_update
    target_practice: int = practice_session_id
    target_item: int = item_id
    remove = remove_practice_drill if kind == REMOVE_KIND_DRILL else remove_placed_set
    result, reason = run_write_with_reason(
        current_user.user_id,
        lambda session: remove(session, target_practice, target_item),
        f"remove practice {kind}",
    )
    if not result.ok:
        message = reason or MESSAGE_REMOVE_FAILED.format(ref=result.ref)
        return no_update, message, no_update, no_update, no_update
    logger.info(
        "Removed %s %s from practice %s (%s rows marked)",
        kind,
        target_item,
        target_practice,
        result.value,
    )
    body, selectable, run_class, feedback = _refreshed_outputs(target_practice, "")
    # A failed refresh keeps the dialog open so its "reopen" message is seen.
    return (no_update if feedback else False), str(feedback), body, selectable, run_class


# --- Registration ---

def register_practice_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the New practice callback and the court's Add player and Remove player
        callbacks. The court's entry callbacks are registered by
        controller_entry.register_entry_callbacks.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        The components exist only on their own pages, so the app runs with
        suppress_callback_exceptions. court-player is read by the entry callbacks: the
        picker is rebuilt by the add and remove callbacks, and the new picker re-runs
        court_render. Each dialog's opened and feedback are first written by its open
        callback, so the other writers use allow_duplicate (Dash traps, plan §3.3).
        confirm_add_player is the first writer of court-base.data; every other writer uses
        allow_duplicate. The overview callbacks are registered by
        _register_overview_callbacks.
    """
    app.callback(
        Output("practice-new-feedback", "children"),
        Output("url", "pathname"),
        Input("practice-new-submit", "n_clicks"),
        State("practice-new-type", "value"),
        State("practice-new-date", "value"),
        State("practice-new-players", "value"),
        State("practice-new-template", "value"),
        State("practice-new-duration", "value"),
        State("practice-new-location", "value"),
        State("practice-new-notes", "value"),
        prevent_initial_call=True,
    )(create_practice)
    _register_overview_callbacks(app)

    app.callback(
        Output("court-add-player-modal", "opened"),
        Output("court-add-player-select", "data"),
        Output("court-add-player-select", "value"),
        Output("court-add-player-feedback", "children"),
        Input("court-add-player-open", "n_clicks"),
        State("entry-parent", "data"),
        prevent_initial_call=True,
    )(open_add_player)

    app.callback(
        Output("court-player-area", "children"),
        Output("court-base", "data"),
        Output("court-add-player-modal", "opened", allow_duplicate=True),
        Output("court-add-player-feedback", "children", allow_duplicate=True),
        Input("court-add-player-confirm", "n_clicks"),
        State("entry-parent", "data"),
        State("court-add-player-select", "value"),
        prevent_initial_call=True,
    )(confirm_add_player)

    app.clientside_callback(
        ClientsideFunction("hoopster", "close_modal"),
        Output("court-add-player-modal", "opened", allow_duplicate=True),
        Input("court-add-player-cancel", "n_clicks"),
        prevent_initial_call=True,
    )

    app.callback(
        Output("court-remove-player-modal", "opened"),
        Output("court-remove-player-text", "children"),
        Output("court-remove-player-feedback", "children"),
        Input("court-remove-player-open", "n_clicks"),
        State("entry-parent", "data"),
        State("court-player", "value"),
        prevent_initial_call=True,
    )(open_remove_player)

    app.callback(
        Output("court-player-area", "children", allow_duplicate=True),
        Output("court-base", "data", allow_duplicate=True),
        Output("court-remove-player-modal", "opened", allow_duplicate=True),
        Output("court-remove-player-feedback", "children", allow_duplicate=True),
        Input("court-remove-player-confirm", "n_clicks"),
        State("entry-parent", "data"),
        State("court-player", "value"),
        State("court-queue", "data"),
        State("court-outbox", "data"),
        State("court-confirmed", "data"),
        prevent_initial_call=True,
    )(confirm_remove_player)

    app.clientside_callback(
        ClientsideFunction("hoopster", "close_modal"),
        Output("court-remove-player-modal", "opened", allow_duplicate=True),
        Input("court-remove-player-cancel", "n_clicks"),
        prevent_initial_call=True,
    )


def _register_overview_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the practice overview's callbacks (practice_drills plan §3.5): Save
        details, Add drill set, Add drill, and the remove dialog (open, cancel, confirm).

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        add_set is the first writer of ov-body, ov-add-set data, ov-add-feedback and
        ov-run-area className; add_drill and confirm_remove use allow_duplicate.
        open_remove is the first writer of the remove dialog's opened and feedback (plan
        §3.8.4) and the only writer of ov-remove-target.
    """
    app.callback(
        Output("ov-details-saved", "children"),
        Output("ov-details-feedback", "children"),
        Input("ov-save", "n_clicks"),
        State("ov-context", "data"),
        State("ov-duration", "value"),
        State("ov-location", "value"),
        State("ov-notes", "value"),
        prevent_initial_call=True,
    )(save_practice_details)

    app.callback(
        Output("ov-body", "children"),
        Output("ov-add-set", "data"),
        Output("ov-add-set", "value"),
        Output("ov-add-feedback", "children"),
        Output("ov-run-area", "className"),
        Input("ov-add-set-go", "n_clicks"),
        State("ov-context", "data"),
        State("ov-add-set", "value"),
        prevent_initial_call=True,
    )(add_set)

    app.callback(
        Output("ov-body", "children", allow_duplicate=True),
        Output("ov-add-drill", "value"),
        Output("ov-add-feedback", "children", allow_duplicate=True),
        Output("ov-run-area", "className", allow_duplicate=True),
        Input("ov-add-drill-go", "n_clicks"),
        State("ov-context", "data"),
        State("ov-add-drill", "value"),
        prevent_initial_call=True,
    )(add_drill)

    app.callback(
        Output("ov-remove-modal", "opened"),
        Output("ov-remove-text", "children"),
        Output("ov-remove-target", "data"),
        Output("ov-remove-feedback", "children"),
        Input({"type": OV_REMOVE_DRILL_TYPE, "index": ALL}, "n_clicks"),
        Input({"type": OV_REMOVE_SET_TYPE, "index": ALL}, "n_clicks"),
        State("ov-context", "data"),
        prevent_initial_call=True,
    )(open_remove)

    app.clientside_callback(
        ClientsideFunction("hoopster", "close_modal"),
        Output("ov-remove-modal", "opened", allow_duplicate=True),
        Input("ov-remove-cancel", "n_clicks"),
        prevent_initial_call=True,
    )

    app.callback(
        Output("ov-remove-modal", "opened", allow_duplicate=True),
        Output("ov-remove-feedback", "children", allow_duplicate=True),
        Output("ov-body", "children", allow_duplicate=True),
        Output("ov-add-set", "data", allow_duplicate=True),
        Output("ov-run-area", "className", allow_duplicate=True),
        Input("ov-remove-confirm", "n_clicks"),
        State("ov-context", "data"),
        State("ov-remove-target", "data"),
        prevent_initial_call=True,
    )(confirm_remove)
