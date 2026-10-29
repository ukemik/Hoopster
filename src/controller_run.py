"""Run-mode controller of Hoopster (practice_drills 2b): the run page builder, the pure run
navigation, and the callbacks of the values panel, the outcome panel and Swap.

REQ-030, REQ-053, REQ-056, REQ-060, REQ-076 to REQ-079, REQ-082. Shooting drills are entered
through the shared entry panel in drill mode (entry parent kind "drill"; its callbacks are
registered by controller_entry.register_entry_callbacks). The practice and practice drill come
from the URL and reach the callbacks through the drill-context store, written only after the
builder's live check; every write re-checks both in SQL (Context Adaptation Table). The entry
player is drill-player.value (or outcome-submit.player_id), never the selected player.
"""

import logging
from datetime import datetime, timedelta, timezone

from dash import ALL, ClientsideFunction, Dash, Input, Output, State, ctx, no_update
from dash.development.base_component import Component
from dash.exceptions import PreventUpdate
from flask_login import current_user
from sqlalchemy.orm import Session

from config.app_config import TAPPED_AT_MAX_SKEW_HOURS
from src import data_handler
from src.app_controller import (
    DeviceClock,
    log_failure,
    new_reference_code,
    parse_player_value,
    run_write,
    run_write_with_reason,
)
from src.data_drill_results import (
    CAPTURE_OUTCOMES,
    CAPTURE_SHOTS,
    add_outcome_rep,
    add_value_attempt,
    delete_value_attempt,
    get_personal_bests,
    get_personal_bests_for_players,
    get_result_summary,
    get_run_drill,
    get_shooting_history,
    list_drill_spot_plan,
    list_outcome_structure,
    list_value_attempts,
    undo_outcome_rep,
)
from src.data_entry import (
    PARENT_KIND_DRILL,
    SYMMETRY_COURT_SIDE,
    EntryParent,
    get_entry_totals,
    list_active_court_spots,
    list_shot_types,
)
from src.data_practice import (
    count_live_results,
    get_practice,
    list_practice_drills,
    list_practice_participants,
    swap_practice_drill,
)
from src.layouts import not_found_page
from src.layouts_entry import entry_panel
from src.layouts_practice import court_no_players_layout, court_player_picker
from src.layouts_run import (
    RESULT_PHYSICAL,
    attempt_rows,
    drill_plan,
    first_open_spot,
    format_attempt,
    format_personal_best,
    format_shooting_history,
    format_summary,
    no_drills_layout,
    outcome_panel,
    run_page_layout,
    step_text,
    values_panel,
)

logger = logging.getLogger(__name__)

MESSAGE_PICK_PLAYER = "Pick a player first."
MESSAGE_VALUE = "Enter a value of 0 or more."
MESSAGE_WHOLE = "Enter a whole number."
MESSAGE_VALUE_TOO_LARGE = "That value is too large."
MESSAGE_SAVE_FAILED = "Could not save the attempt. Reference: {ref}"
MESSAGE_LOAD_FAILED = "Could not load the results. Reference: {ref}"
MESSAGE_DRILL_GONE = "This drill is no longer in the practice."
MESSAGE_DELETE_QUESTION = "Delete attempt {label}? It can be restored for 30 days."
MESSAGE_ATTEMPT_GONE = "This attempt no longer exists."
MESSAGE_DELETE_FAILED = "Could not delete the attempt. Reference: {ref}"
MESSAGE_REP_FAILED = "Rep not saved - tap again ({detail})"
MESSAGE_NOTHING_TO_UNDO = "Nothing to undo"
MESSAGE_UNDONE = "Undone"
MESSAGE_SAVED = "Saved"
MESSAGE_SWAP_QUESTION = "Swap {name} for another drill of the same kind?"
MESSAGE_SWAP_RESULTS = " {count} result(s) will be removed (restorable for 30 days)."
MESSAGE_SWAP_FAILED = "Could not swap the drill. Reference: {ref}"
STATUS_SAVED_COLOR = "green"
STATUS_FAILED_COLOR = "red"
STATUS_NEUTRAL_COLOR = "gray"
# h_data.drill_attempts.value_1/value_2 are numeric(9,3): below one million.
VALUE_MAX = 999999.999
SIDE_VALUES: frozenset[str] = frozenset({"left", "right"})


# --- Context helpers ---

def _drill_context(context: object) -> tuple[int, int] | None:
    """
    Description:
        Reads the drill-context store.

    Parameters:
        context: drill-context.data, {"practice_session_id": int, "practice_drill_id": int}.

    Return Values:
        tuple[int, int] | None: (practice_session_id, practice_drill_id), or None when the
        store has another shape.

    Notes:
        Pure. Every write re-checks both in SQL.
    """
    if not isinstance(context, dict):
        return None
    practice = context.get("practice_session_id")
    drill = context.get("practice_drill_id")
    if not isinstance(practice, int) or isinstance(practice, bool):
        return None
    if not isinstance(drill, int) or isinstance(drill, bool):
        return None
    return practice, drill


def choose_run_player(participants: list[dict], preferred_player_id: int | None) -> int:
    """
    Description:
        The participant a run-page picker shows first: the selected player when a live
        participant, else the first participant (D4).

    Parameters:
        participants: rows of data_practice.list_practice_participants, at least one.
        preferred_player_id: the selected player (current_player_id()), or None.

    Return Values:
        int: a participant's player_id.

    Notes:
        Pure. Never changes the selected player (REQ-075).
    """
    participant_ids = [int(participant["player_id"]) for participant in participants]
    if preferred_player_id in participant_ids:
        return int(preferred_player_id)
    return participant_ids[0]


# --- Navigation (REQ-056, REQ-082) ---

def run_navigation(drills: list[dict], practice_drill_id: int) -> dict[str, int | None]:
    """
    Description:
        The run page's neighbours of a drill in run order: previous and next drill, and the
        first drill of the previous and next set (each ad-hoc drill is its own group).

    Parameters:
        drills: data_practice.list_practice_drills rows, in run order.
        practice_drill_id: the drill shown.

    Return Values:
        dict[str, int | None]: {"prev", "next", "prev_set", "next_set"} practice_drill_ids,
        None where there is none (or when the drill is not in the list).

    Notes:
        Pure.
    """
    ids = [int(row["practice_drill_id"]) for row in drills]
    empty: dict[str, int | None] = {"prev": None, "next": None, "prev_set": None, "next_set": None}
    if practice_drill_id not in ids:
        return empty
    groups: list[list[int]] = []
    last_key: object = object()
    for row, drill_id in zip(drills, ids, strict=True):
        set_id = row["practice_drill_set_id"]
        key = set_id if set_id is not None else ("ad_hoc", drill_id)
        if not groups or key != last_key:
            groups.append([])
            last_key = key
        groups[-1].append(drill_id)
    index = ids.index(practice_drill_id)
    group_index = next(i for i, group in enumerate(groups) if practice_drill_id in group)
    return {
        "prev": ids[index - 1] if index > 0 else None,
        "next": ids[index + 1] if index + 1 < len(ids) else None,
        "prev_set": groups[group_index - 1][0] if group_index > 0 else None,
        "next_set": groups[group_index + 1][0] if group_index + 1 < len(groups) else None,
    }


def run_step(drills: list[dict], drill: dict) -> str:
    """
    Description:
        The run-step line of a drill: its set's number among the placed sets with drills,
        and its position in the set (plan §3.6).

    Parameters:
        drills: data_practice.list_practice_drills rows, in run order.
        drill: the data_drill_results.get_run_drill row shown.

    Return Values:
        str: "Set 2 of 3 · Name (v2) · Drill 3 of 5", or "Single drill".

    Notes:
        Pure.
    """
    set_id = drill.get("practice_drill_set_id")
    if set_id is None:
        return step_text(drill, None, 0, 0, 0)
    set_order: list[object] = []
    for row in drills:
        row_set = row["practice_drill_set_id"]
        if row_set is not None and row_set not in set_order:
            set_order.append(row_set)
    in_set = [
        int(row["practice_drill_id"]) for row in drills if row["practice_drill_set_id"] == set_id
    ]
    drill_id = int(drill["practice_drill_id"])
    position = in_set.index(drill_id) + 1 if drill_id in in_set else 1
    set_number = set_order.index(set_id) + 1 if set_id in set_order else 1
    return step_text(drill, set_number, len(set_order), position, len(in_set))


# --- Run page builder (plan §3.6) ---

def _shooting_panel(
    session: Session, drill: dict, participants: list[dict], default_player_id: int
) -> list[Component]:
    """
    Description:
        The shooting panel: the court-player picker and the shared entry panel in drill mode
        with the spot plan, the free-throw rule, the side control and each participant's
        personal best and earlier attempts (plan §3.7).

    Parameters:
        session: the request session.
        drill: the get_run_drill row (capture_kind "shots").
        participants: the live participants.
        default_player_id: the picker's first player (D4).

    Return Values:
        list[Component]: the panel.

    Notes:
        A shooting drill with the court_side symmetry has no side control (the side comes
        from each shot's spot). Five reads plus two batch reads, none in a loop.
    """
    practice_session_id = int(drill["practice_session_id"])
    practice_drill_id = int(drill["practice_drill_id"])
    totals = get_entry_totals(session, EntryParent(PARENT_KIND_DRILL, practice_drill_id))
    plan = drill_plan(list_drill_spot_plan(session, int(drill["drill_version_id"])))
    player_ids = [int(row["player_id"]) for row in participants]
    bests = get_personal_bests_for_players(session, player_ids, int(drill["drill_id"]))
    history = get_shooting_history(session, practice_session_id, practice_drill_id)
    pb_by_player = {
        str(player_id): "\n".join(
            part
            for part in (
                format_personal_best(bests.get(player_id, [])),
                format_shooting_history(history.get(player_id, [])),
            )
            if part
        )
        for player_id in player_ids
    }
    symmetry = drill.get("symmetry_type_code")
    side_labels = (
        {"left_label": drill.get("left_label"), "right_label": drill.get("right_label")}
        if symmetry is not None and symmetry != SYMMETRY_COURT_SIDE
        else None
    )
    default_base = totals.get(str(default_player_id), {})
    base_spots = default_base.get("spots") if isinstance(default_base, dict) else None
    drill_mode = {
        "plan": plan,
        "has_ft_spot": bool(drill.get("has_ft_spot")),
        "symmetry": side_labels,
        "pb_by_player": pb_by_player,
        "selected_spot": first_open_spot(plan, base_spots if isinstance(base_spots, dict) else {}),
    }
    return [
        court_player_picker(participants, default_player_id),
        *entry_panel(
            {"kind": PARENT_KIND_DRILL, "id": practice_drill_id},
            totals,
            default_player_id,
            list_active_court_spots(session),
            list_shot_types(session),
            drill_mode,
        ),
    ]


def build_run_page(player_id: int, clock: DeviceClock, params: dict[str, str]) -> Component:
    """
    Description:
        Builds /practice/<id>/run[/<pd>] (plan §3.6): the given drill, or the first drill in
        run order, with its header, navigation, Swap and the result panel for its capture
        kind (shots, values or outcomes).

    Parameters:
        player_id: the selected player (current_player_id()); only the picker's default
            (D4). Never a user id (COD-8-01).
        clock: the device clock (unused).
        params: {"practice_session_id": "<digits>"} and optionally {"practice_drill_id":
            "<digits>"} from the URL.

    Return Values:
        Component: the page; the not-found page when the practice is not live or <pd> is
        not live in it; "no drills yet" when the practice has none; the no-players page
        when it has no live participant.

    Notes:
        drill-context and entry-parent are written from the URL only after the live checks
        (Context Adaptation Table).
    """
    session = data_handler.get_session()
    practice_session_id = int(params["practice_session_id"])
    practice = get_practice(session, practice_session_id)
    if practice is None:
        return not_found_page()
    drills = list_practice_drills(session, practice_session_id)
    if not drills:
        return no_drills_layout(practice)
    drill_param = params.get("practice_drill_id")
    practice_drill_id = int(drill_param) if drill_param else int(drills[0]["practice_drill_id"])
    drill = get_run_drill(session, practice_session_id, practice_drill_id)
    if drill is None:
        return not_found_page()
    participants = list_practice_participants(session, practice_session_id)
    if not participants:
        return court_no_players_layout(practice)
    default_player_id = choose_run_player(participants, player_id)
    capture = drill.get("capture_kind")
    if capture == CAPTURE_SHOTS:
        panel = _shooting_panel(session, drill, participants, default_player_id)
    else:
        summary = get_result_summary(
            session, practice_session_id, practice_drill_id, default_player_id
        )
        pb_text = format_personal_best(
            get_personal_bests(session, default_player_id, int(drill["drill_id"]))
        )
        if capture == CAPTURE_OUTCOMES:
            groups = list_outcome_structure(session, int(drill["drill_version_id"]))
            panel = outcome_panel(
                drill, groups, participants, default_player_id, summary, pb_text
            )
        else:
            attempts = list_value_attempts(
                session, default_player_id, practice_session_id, practice_drill_id
            )
            panel = values_panel(
                drill, participants, default_player_id, attempts, summary, pb_text
            )
    context = {"drill": drill, "step": run_step(drills, drill)}
    return run_page_layout(context, run_navigation(drills, practice_drill_id), panel)


# --- Player change (D16) ---

def _player_results(
    practice_session_id: int, practice_drill_id: int, player_id: int
) -> tuple[list[Component], str, str] | None:
    """
    Description:
        Reads one player's attempts, summary and personal best for the values or outcome
        panel.

    Parameters:
        practice_session_id: the practice.
        practice_drill_id: the practice drill.
        player_id: the entry player.

    Return Values:
        tuple[list[Component], str, str] | None: (drill-attempts children, drill-summary
        text, drill-pb text), or None when the drill is no longer live in the practice.

    Notes:
        Reads only; exceptions propagate to the calling boundary.
    """
    session = data_handler.get_session()
    drill = get_run_drill(session, practice_session_id, practice_drill_id)
    if drill is None:
        return None
    summary = get_result_summary(session, practice_session_id, practice_drill_id, player_id)
    pb_text = format_personal_best(get_personal_bests(session, player_id, int(drill["drill_id"])))
    if drill.get("capture_kind") == CAPTURE_OUTCOMES:
        groups = list_outcome_structure(session, int(drill["drill_version_id"]))
        return [], format_summary(drill, summary, groups), pb_text
    attempts = list_value_attempts(session, player_id, practice_session_id, practice_drill_id)
    return attempt_rows(drill, attempts), format_summary(drill, summary), pb_text


def render_drill_player(
    player_value: str | None, context: dict | None
) -> tuple[object, str, str]:
    """
    Description:
        A change of drill-player: shows that player's attempts, summary and personal best
        (D16: the PB is read on page open and on a player change, never live).

    Parameters:
        player_value: drill-player.value (Context Adaptation Table: the entry player).
        context: drill-context.data.

    Return Values:
        tuple[object, str, str]: drill-attempts children, drill-summary text and drill-pb
        text.

    Notes:
        A boundary (COD-4-04): a failed read shows a reference in the summary.
    """
    player_id = parse_player_value(player_value)
    target = _drill_context(context)
    if player_id is None or target is None:
        raise PreventUpdate
    try:
        results = _player_results(target[0], target[1], player_id)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Drill results read failed (ref %s)", ref)
        return no_update, MESSAGE_LOAD_FAILED.format(ref=ref), no_update
    if results is None:
        return [], MESSAGE_DRILL_GONE, ""
    return results


# --- Values panel (REQ-030, REQ-076, REQ-078, REQ-079; D9) ---

def parse_value(value: object, whole: bool) -> tuple[float | None, str | None]:
    """
    Description:
        Reads a value input: a number of at least 0 (a whole number for counts) below the
        column's limit.

    Parameters:
        value: the NumberInput value (int, float, numeric text or "").
        whole: True for count types.

    Return Values:
        tuple[float | None, str | None]: (the value, None), or (None, the message).

    Notes:
        Pure. Empty is refused for value 1; callers treat an empty value 2 as None first.
    """
    number: float | None = None
    if isinstance(value, bool):
        number = None
    elif isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            number = float(value.strip())
        except ValueError:
            number = None
    if number is None or number != number or number < 0:
        return None, MESSAGE_VALUE
    if number > VALUE_MAX:
        return None, MESSAGE_VALUE_TOO_LARGE
    if whole and not number.is_integer():
        return None, MESSAGE_WHOLE
    return number, None


def _parse_attempt_values(
    drill: dict, value_1: object, value_2: object
) -> tuple[tuple[float, float | None] | None, str | None]:
    """
    Description:
        Reads the values panel's inputs for the drill's type: value 1 required; value 2
        only for a physical drill with a second number, and optional there.

    Parameters:
        drill: the get_run_drill row.
        value_1: drill-value-1.value.
        value_2: drill-value-2.value.

    Return Values:
        tuple[tuple[float, float | None] | None, str | None]: ((value 1, value 2 or None),
        None) when valid, else (None, the message).

    Notes:
        Pure. Counts are whole numbers; seconds and physical values may have decimals.
    """
    whole = drill.get("result_type_code") != RESULT_PHYSICAL and drill.get("value_unit") == "count"
    first, message = parse_value(value_1, whole)
    if first is None:
        return None, message
    has_second = drill.get("result_type_code") == RESULT_PHYSICAL and bool(drill.get("value_2_label"))
    if not has_second or value_2 is None or (isinstance(value_2, str) and not value_2.strip()):
        return (first, None), None
    second, message = parse_value(value_2, False)
    if second is None:
        return None, message
    return (first, second), None


def save_value_attempt(
    n_clicks: int | None,
    context: dict | None,
    player_value: str | None,
    side: str | None,
    value_1: object,
    value_2: object,
) -> tuple[str, object, object, object, object]:
    """
    Description:
        "Save attempt" on the values panel: saves one attempt for the picked player and
        refreshes the attempts and summary (REQ-030, REQ-076, REQ-078).

    Parameters:
        n_clicks: drill-add-attempt.n_clicks.
        context: drill-context.data (practice and practice drill).
        player_value: drill-player.value, the entry player.
        side: drill-side.value ("left" or "right"; stored only when the drill records a
            side, REQ-079).
        value_1: drill-value-1.value.
        value_2: drill-value-2.value.

    Return Values:
        tuple[str, object, object, object, object]: drill-feedback text, drill-attempts
        children, drill-summary text, drill-value-1 value and drill-value-2 value (both
        cleared on success); no_update where nothing changes.

    Notes:
        A boundary (COD-4-04). The database's own refusal is shown as its message.
    """
    if not n_clicks:
        raise PreventUpdate
    player_id = parse_player_value(player_value)
    target = _drill_context(context)
    if player_id is None:
        return MESSAGE_PICK_PLAYER, no_update, no_update, no_update, no_update
    if target is None:
        ref = new_reference_code()
        logger.error("Value attempt refused (ref %s): drill-context is not an id", ref)
        return MESSAGE_SAVE_FAILED.format(ref=ref), no_update, no_update, no_update, no_update
    practice_session_id, practice_drill_id = target
    try:
        drill = get_run_drill(data_handler.get_session(), practice_session_id, practice_drill_id)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Value attempt check failed (ref %s)", ref)
        return MESSAGE_SAVE_FAILED.format(ref=ref), no_update, no_update, no_update, no_update
    if drill is None:
        return MESSAGE_DRILL_GONE, no_update, no_update, no_update, no_update
    values, message = _parse_attempt_values(drill, value_1, value_2)
    if values is None:
        return message or MESSAGE_VALUE, no_update, no_update, no_update, no_update
    chosen_side = side if side in SIDE_VALUES else None
    entry_player: int = player_id
    first, second = values
    result, reason = run_write_with_reason(
        current_user.user_id,
        lambda session: add_value_attempt(
            session, entry_player, practice_session_id, practice_drill_id,
            chosen_side, first, second,
        ),
        "save drill attempt",
    )
    if not result.ok:
        failure = reason or MESSAGE_SAVE_FAILED.format(ref=result.ref)
        return failure, no_update, no_update, no_update, no_update
    logger.info(
        "Saved attempt %s of player %s for practice drill %s",
        result.value,
        entry_player,
        practice_drill_id,
    )
    try:
        refreshed = _player_results(practice_session_id, practice_drill_id, entry_player)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Drill results read failed (ref %s)", ref)
        return MESSAGE_LOAD_FAILED.format(ref=ref), no_update, no_update, "", ""
    if refreshed is None:
        return MESSAGE_DRILL_GONE, no_update, no_update, "", ""
    attempts, summary_text, _pb = refreshed
    return "", attempts, summary_text, "", ""


def open_delete_attempt(
    n_clicks_list: list[int | None], context: dict | None, player_value: str | None
) -> tuple[bool, str, object, str]:
    """
    Description:
        A Delete tap on a value attempt: opens the confirmation (REQ-005, D9).

    Parameters:
        n_clicks_list: {"type": "drill-attempt-delete", "index": ALL}.n_clicks.
        context: drill-context.data.
        player_value: drill-player.value, whose attempts are listed.

    Return Values:
        tuple[bool, str, object, str]: attempt-del-modal.opened (True), attempt-del-text,
        drill-delete-target.data ({"drill_attempt_id"} or None) and attempt-del-feedback.

    Notes:
        Raises PreventUpdate unless a button was really tapped (a re-rendered list fires
        this callback, plan §3.8.2). A boundary (COD-4-04).
    """
    if not ctx.triggered or not ctx.triggered[0].get("value"):
        raise PreventUpdate
    triggered = ctx.triggered_id
    attempt_id = triggered.get("index") if isinstance(triggered, dict) else None
    player_id = parse_player_value(player_value)
    target = _drill_context(context)
    try:
        if not isinstance(attempt_id, int) or player_id is None or target is None:
            raise ValueError("attempt, player or drill-context is not an id")
        session = data_handler.get_session()
        drill = get_run_drill(session, target[0], target[1])
        attempts = list_value_attempts(session, player_id, target[0], target[1])
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Delete attempt dialog failed (ref %s)", ref)
        return True, "", None, MESSAGE_DELETE_FAILED.format(ref=ref)
    matching = [row for row in attempts if row["drill_attempt_id"] == attempt_id]
    if drill is None or not matching:
        return True, "", None, MESSAGE_ATTEMPT_GONE
    question = MESSAGE_DELETE_QUESTION.format(label=format_attempt(drill, matching[0]))
    return True, question, {"drill_attempt_id": attempt_id}, ""


def confirm_delete_attempt(
    n_clicks: int | None,
    context: dict | None,
    player_value: str | None,
    target_attempt: dict | None,
) -> tuple[object, str, object, object]:
    """
    Description:
        "Delete" in the attempt confirmation: soft-deletes the attempt (restorable for 30
        days) and refreshes the attempts and summary (REQ-005, REQ-006, D9).

    Parameters:
        n_clicks: attempt-del-confirm.n_clicks.
        context: drill-context.data.
        player_value: drill-player.value, the entry player.
        target_attempt: drill-delete-target.data, {"drill_attempt_id"}.

    Return Values:
        tuple[object, str, object, object]: attempt-del-modal.opened (False on success),
        attempt-del-feedback text, drill-attempts children and drill-summary text.

    Notes:
        A boundary (COD-4-04). The data layer re-checks the attempt is a live attempt of
        this player's result for this practice drill.
    """
    if not n_clicks:
        raise PreventUpdate
    player_id = parse_player_value(player_value)
    target = _drill_context(context)
    attempt_id = (
        target_attempt.get("drill_attempt_id") if isinstance(target_attempt, dict) else None
    )
    if (
        player_id is None
        or target is None
        or not isinstance(attempt_id, int)
        or isinstance(attempt_id, bool)
    ):
        ref = new_reference_code()
        logger.error("Attempt delete refused (ref %s): player, context or attempt not an id", ref)
        return no_update, MESSAGE_DELETE_FAILED.format(ref=ref), no_update, no_update
    entry_player: int = player_id
    chosen_attempt: int = attempt_id
    result, reason = run_write_with_reason(
        current_user.user_id,
        lambda session: delete_value_attempt(
            session, entry_player, target[0], target[1], chosen_attempt
        ),
        "delete drill attempt",
    )
    if not result.ok:
        failure = reason or MESSAGE_DELETE_FAILED.format(ref=result.ref)
        return no_update, failure, no_update, no_update
    if not result.value:
        logger.error("Attempt %s matched no live attempt of player %s", chosen_attempt, entry_player)
        return no_update, MESSAGE_ATTEMPT_GONE, no_update, no_update
    logger.info("Soft-deleted attempt %s of player %s", chosen_attempt, entry_player)
    try:
        refreshed = _player_results(target[0], target[1], entry_player)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Drill results read failed (ref %s)", ref)
        return False, "", no_update, MESSAGE_LOAD_FAILED.format(ref=ref)
    if refreshed is None:
        return False, "", [], MESSAGE_DRILL_GONE
    attempts, summary_text, _pb = refreshed
    return False, "", attempts, summary_text


# --- Outcome panel (REQ-077, REQ-010, REQ-058) ---

def parse_rep(submit: object) -> tuple[int, dict[int, int], str | None, datetime] | None:
    """
    Description:
        Reads the outcome-submit store written by the clientside outcome_pick.

    Parameters:
        submit: outcome-submit.data, {"key", "picks": {group_id: outcome_id}, "player_id",
            "side", "tapped_at"}.

    Return Values:
        tuple[int, dict[int, int], str | None, datetime] | None: (player id, picks, side,
        tap instant in UTC), or None when the value is not of that shape.

    Notes:
        Pure apart from the clock: a missing, unreadable or skewed tapped_at falls back to
        the server time (idempotency is then lost for that rep, as for court entries).
    """
    if not isinstance(submit, dict):
        return None
    player_id = submit.get("player_id")
    picks_raw = submit.get("picks")
    if not isinstance(player_id, int) or isinstance(player_id, bool):
        return None
    if not isinstance(picks_raw, dict) or not picks_raw:
        return None
    picks: dict[int, int] = {}
    for group, outcome in picks_raw.items():
        group_id = parse_player_value(group) if isinstance(group, str) else None
        if group_id is None or not isinstance(outcome, int) or isinstance(outcome, bool):
            return None
        picks[group_id] = outcome
    side = submit.get("side")
    now_utc = datetime.now(timezone.utc)
    tapped_at = now_utc
    raw = submit.get("tapped_at")
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is not None:
            parsed_utc = parsed.astimezone(timezone.utc)
            if abs(parsed_utc - now_utc) <= timedelta(hours=TAPPED_AT_MAX_SKEW_HOURS):
                tapped_at = parsed_utc
    if tapped_at is now_utc:
        logger.warning("Rep tap time missing, unreadable or too far from server time")
    return player_id, picks, (side if side in SIDE_VALUES else None), tapped_at


def _summary_text(practice_session_id: int, practice_drill_id: int, player_id: int) -> object:
    """
    Description:
        The drill-summary text after an outcome write, or no_update when it cannot be read.

    Parameters:
        practice_session_id: the practice.
        practice_drill_id: the practice drill.
        player_id: the entry player.

    Return Values:
        object: the summary text, or no_update (logged with a reference).

    Notes:
        A boundary helper: never raises.
    """
    try:
        refreshed = _player_results(practice_session_id, practice_drill_id, player_id)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Drill results read failed (ref %s)", ref)
        return no_update
    return refreshed[1] if refreshed is not None else MESSAGE_DRILL_GONE


def save_outcome_rep_cb(
    submit: dict | None, context: dict | None, visit: dict | None
) -> tuple[object, str, str, object]:
    """
    Description:
        Saves a complete outcome rep written by outcome_pick: one attempt with one tap per
        group (REQ-077), idempotent on its tap instant.

    Parameters:
        submit: outcome-submit.data.
        context: drill-context.data.
        visit: outcome-visit.data, {str(player_id): [attempt ids of this visit]}.

    Return Values:
        tuple[object, str, str, object]: drill-summary text, outcome-status text and colour
        (REQ-058), and outcome-visit.data with the new attempt id appended.

    Notes:
        A boundary (COD-4-04). The entry player is the rep's own player_id (the picker's
        value when it was tapped), never the selected player.
    """
    rep = parse_rep(submit)
    target = _drill_context(context)
    if rep is None or target is None:
        raise PreventUpdate
    player_id, picks, side, tapped_at = rep
    result, reason = run_write_with_reason(
        current_user.user_id,
        lambda session: add_outcome_rep(
            session, player_id, target[0], target[1], picks, tapped_at, side
        ),
        "save outcome rep",
    )
    if not result.ok:
        detail = reason or f"Reference: {result.ref}"
        return no_update, MESSAGE_REP_FAILED.format(detail=detail), STATUS_FAILED_COLOR, no_update
    attempt_id = int(result.value) if result.value is not None else None
    visit_ids = dict(visit) if isinstance(visit, dict) else {}
    player_ids = list(visit_ids.get(str(player_id)) or [])
    if attempt_id is not None and attempt_id not in player_ids:
        player_ids.append(attempt_id)
    visit_ids[str(player_id)] = player_ids
    summary = _summary_text(target[0], target[1], player_id)
    return summary, MESSAGE_SAVED, STATUS_SAVED_COLOR, visit_ids


def undo_outcome_rep_cb(
    n_clicks: int | None, context: dict | None, player_value: str | None, visit: dict | None
) -> tuple[object, str, str, object]:
    """
    Description:
        Undo on the outcome panel: removes the picked player's last rep of this visit with
        a hard delete; repeated taps go further back (REQ-010, D9).

    Parameters:
        n_clicks: outcome-undo.n_clicks.
        context: drill-context.data.
        player_value: drill-player.value, the entry player.
        visit: outcome-visit.data.

    Return Values:
        tuple[object, str, str, object]: drill-summary text, outcome-status text and colour,
        and outcome-visit.data without the undone id.

    Notes:
        A boundary (COD-4-04). A rep already gone is dropped from the visit and reported as
        undone.
    """
    if not n_clicks:
        raise PreventUpdate
    player_id = parse_player_value(player_value)
    target = _drill_context(context)
    visit_ids = dict(visit) if isinstance(visit, dict) else {}
    if player_id is None or target is None:
        return no_update, MESSAGE_NOTHING_TO_UNDO, STATUS_NEUTRAL_COLOR, no_update
    player_ids = [
        value for value in (visit_ids.get(str(player_id)) or [])
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    if not player_ids:
        return no_update, MESSAGE_NOTHING_TO_UNDO, STATUS_NEUTRAL_COLOR, no_update
    entry_player: int = player_id
    last_id = player_ids[-1]
    result = run_write(
        current_user.user_id,
        lambda session: undo_outcome_rep(session, entry_player, target[0], target[1], last_id),
        "undo outcome rep",
    )
    if not result.ok:
        detail = f"Reference: {result.ref}"
        return no_update, f"Undo failed ({detail})", STATUS_FAILED_COLOR, no_update
    if not result.value:
        logger.warning("Rep %s of player %s was already gone; undo reported as done", last_id, entry_player)
    visit_ids[str(entry_player)] = player_ids[:-1]
    summary = _summary_text(target[0], target[1], entry_player)
    return summary, MESSAGE_UNDONE, STATUS_NEUTRAL_COLOR, visit_ids


# --- Swap (REQ-053, REQ-082) ---

def open_swap(n_clicks: int | None, context: dict | None) -> tuple[bool, str, str]:
    """
    Description:
        "Swap drill": opens the confirmation, warning how many results the swap removes.

    Parameters:
        n_clicks: run-swap.n_clicks.
        context: drill-context.data.

    Return Values:
        tuple[bool, str, str]: run-swap-modal.opened (True), run-swap-text and
        run-swap-feedback.

    Notes:
        A boundary (COD-4-04).
    """
    if not n_clicks:
        raise PreventUpdate
    target = _drill_context(context)
    try:
        if target is None:
            raise ValueError("drill-context is not an id")
        session = data_handler.get_session()
        drill = get_run_drill(session, target[0], target[1])
        count = count_live_results(session, target[0], practice_drill_id=target[1])
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Swap dialog failed (ref %s)", ref)
        return True, "", MESSAGE_SWAP_FAILED.format(ref=ref)
    if drill is None:
        return True, "", MESSAGE_DRILL_GONE
    question = MESSAGE_SWAP_QUESTION.format(name=drill["drill_name"])
    if count > 0:
        question += MESSAGE_SWAP_RESULTS.format(count=count)
    return True, question, ""


def confirm_swap(n_clicks: int | None, context: dict | None) -> tuple[object, str]:
    """
    Description:
        "Swap" in the confirmation: swaps the drawn drill for another matching one (its
        results are soft-deleted first) and reopens the same run path (REQ-053, REQ-082).

    Parameters:
        n_clicks: run-swap-confirm.n_clicks.
        context: drill-context.data.

    Return Values:
        tuple[object, str]: url.pathname ("/practice/<id>/run/<pd>" after a swap, else
        no_update) and run-swap-feedback ("No other matching drill is available." when
        nothing matched, or the failure).

    Notes:
        A boundary (COD-4-04). Navigating callback: raises PreventUpdate unless the button
        was clicked (plan §3.8.1).
    """
    if not n_clicks:
        raise PreventUpdate
    target = _drill_context(context)
    if target is None:
        ref = new_reference_code()
        logger.error("Swap refused (ref %s): drill-context is not an id", ref)
        return no_update, MESSAGE_SWAP_FAILED.format(ref=ref)
    result, reason = run_write_with_reason(
        current_user.user_id,
        lambda session: swap_practice_drill(session, target[0], target[1]),
        "swap practice drill",
    )
    if not result.ok or result.value is None:
        return no_update, reason or MESSAGE_SWAP_FAILED.format(ref=result.ref)
    if result.value.get("new_drill_version_id") is None:
        return no_update, str(result.value.get("notice") or "No other matching drill is available.")
    logger.info(
        "Swapped practice drill %s (%s results removed)",
        target[1],
        result.value.get("results_removed"),
    )
    return f"/practice/{target[0]}/run/{target[1]}", ""


# --- Registration ---

def _register_values_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the values panel's callbacks: the timer, Save attempt and the delete
        confirmation.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        timer_step is the first (non-duplicate) writer of drill-value-1.value; Save attempt
        clears it with allow_duplicate. drill-attempts and drill-summary are first written
        by render_drill_player.
    """
    app.clientside_callback(
        ClientsideFunction("hoopster", "timer_step"),
        Output("drill-timer", "data"),
        Output("drill-timer-display", "children"),
        Output("drill-timer-tick", "disabled"),
        Output("drill-value-1", "value"),
        Input("drill-timer-start", "n_clicks"),
        Input("drill-timer-stop", "n_clicks"),
        Input("drill-timer-reset", "n_clicks"),
        Input("drill-timer-tick", "n_intervals"),
        State("drill-timer", "data"),
        prevent_initial_call=True,
    )

    app.callback(
        Output("drill-feedback", "children"),
        Output("drill-attempts", "children", allow_duplicate=True),
        Output("drill-summary", "children", allow_duplicate=True),
        Output("drill-value-1", "value", allow_duplicate=True),
        Output("drill-value-2", "value"),
        Input("drill-add-attempt", "n_clicks"),
        State("drill-context", "data"),
        State("drill-player", "value"),
        State("drill-side", "value"),
        State("drill-value-1", "value"),
        State("drill-value-2", "value"),
        prevent_initial_call=True,
    )(save_value_attempt)

    app.callback(
        Output("attempt-del-modal", "opened"),
        Output("attempt-del-text", "children"),
        Output("drill-delete-target", "data"),
        Output("attempt-del-feedback", "children"),
        Input({"type": "drill-attempt-delete", "index": ALL}, "n_clicks"),
        State("drill-context", "data"),
        State("drill-player", "value"),
        prevent_initial_call=True,
    )(open_delete_attempt)

    app.clientside_callback(
        ClientsideFunction("hoopster", "close_modal"),
        Output("attempt-del-modal", "opened", allow_duplicate=True),
        Input("attempt-del-cancel", "n_clicks"),
        prevent_initial_call=True,
    )

    app.callback(
        Output("attempt-del-modal", "opened", allow_duplicate=True),
        Output("attempt-del-feedback", "children", allow_duplicate=True),
        Output("drill-attempts", "children", allow_duplicate=True),
        Output("drill-summary", "children", allow_duplicate=True),
        Input("attempt-del-confirm", "n_clicks"),
        State("drill-context", "data"),
        State("drill-player", "value"),
        State("drill-delete-target", "data"),
        prevent_initial_call=True,
    )(confirm_delete_attempt)


def _register_outcome_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the outcome panel's callbacks: outcome_pick (clientside), saving a rep
        and Undo.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        No loop (plan §3.8.3): outcome_pick -> outcome-submit -> save_outcome_rep_cb, which
        writes nothing outcome_pick reads as Input. save_outcome_rep_cb is the first writer
        of outcome-status and outcome-visit.
    """
    app.clientside_callback(
        ClientsideFunction("hoopster", "outcome_pick"),
        Output("outcome-pending", "data"),
        Output("outcome-pending-text", "children"),
        Output("outcome-submit", "data"),
        Input({"type": "outcome-btn", "group": ALL, "index": ALL}, "n_clicks"),
        State("outcome-pending", "data"),
        State("outcome-groups", "data"),
        State("drill-player", "value"),
        State("drill-side", "value"),
        prevent_initial_call=True,
    )

    app.callback(
        Output("drill-summary", "children", allow_duplicate=True),
        Output("outcome-status", "children"),
        Output("outcome-status", "color"),
        Output("outcome-visit", "data"),
        Input("outcome-submit", "data"),
        State("drill-context", "data"),
        State("outcome-visit", "data"),
        prevent_initial_call=True,
    )(save_outcome_rep_cb)

    app.callback(
        Output("drill-summary", "children", allow_duplicate=True),
        Output("outcome-status", "children", allow_duplicate=True),
        Output("outcome-status", "color", allow_duplicate=True),
        Output("outcome-visit", "data", allow_duplicate=True),
        Input("outcome-undo", "n_clicks"),
        State("drill-context", "data"),
        State("drill-player", "value"),
        State("outcome-visit", "data"),
        prevent_initial_call=True,
    )(undo_outcome_rep_cb)


def register_run_callbacks(app: Dash) -> None:
    """
    Description:
        Registers every run-page callback: the player change, the values panel, the outcome
        panel and Swap. Shooting drills use the entry callbacks
        (controller_entry.register_entry_callbacks).

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        The components exist only on their own panels, so the app runs with
        suppress_callback_exceptions; each panel is a whole component group (plan §3.8.6).
        render_drill_player is the first writer of drill-attempts, drill-summary and
        drill-pb. The swap dialog's opened and feedback are first written by open_swap.
    """
    app.callback(
        Output("drill-attempts", "children"),
        Output("drill-summary", "children"),
        Output("drill-pb", "children"),
        Input("drill-player", "value"),
        State("drill-context", "data"),
        prevent_initial_call=True,
    )(render_drill_player)

    _register_values_callbacks(app)
    _register_outcome_callbacks(app)

    app.callback(
        Output("run-swap-modal", "opened"),
        Output("run-swap-text", "children"),
        Output("run-swap-feedback", "children"),
        Input("run-swap", "n_clicks"),
        State("drill-context", "data"),
        prevent_initial_call=True,
    )(open_swap)

    app.clientside_callback(
        ClientsideFunction("hoopster", "close_modal"),
        Output("run-swap-modal", "opened", allow_duplicate=True),
        Input("run-swap-cancel", "n_clicks"),
        prevent_initial_call=True,
    )

    app.callback(
        Output("url", "pathname", allow_duplicate=True),
        Output("run-swap-feedback", "children", allow_duplicate=True),
        Input("run-swap-confirm", "n_clicks"),
        State("drill-context", "data"),
        prevent_initial_call=True,
    )(confirm_swap)
