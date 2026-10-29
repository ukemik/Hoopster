"""Goals controller of Hoopster: the Goals, New goal and goal page builders, the pure form
rules (direction, measures, form check, status after save), and the list, form, Save and
Delete callbacks (REQ-032, REQ-033, REQ-005; task 2026-10-23_goals_measurements).

The Goals list and New goal use the selected player (current_player_id(), changed by
goals-player through set_current_player_id after a live check); a new goal's player is the
goal-new-player-id store, locked again in the write (D14); an edited goal's player comes from
its own row (Context Adaptation Table, COD-8-01). Progress always comes from
fn_goal_progress with the device's date (D3). Statuses are refreshed for the written player
inside every save, and as SYSTEM_USER_ID for all players before a goals page is built (D4).
Every write goes through app_controller.run_write.
"""

import logging
import re
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from dash import ClientsideFunction, Dash, Input, Output, State, no_update
from dash.development.base_component import Component
from dash.exceptions import PreventUpdate
from flask_login import current_user
from sqlalchemy.orm import Session

from config.app_config import (
    GOAL_NAME_MAX_LENGTH,
    GOAL_NOTES_MAX_LENGTH,
    GOAL_PERCENT_MAX,
    GOAL_REFRESH_MAX_DATE_SKEW_DAYS,
    GOAL_TARGET_DECIMALS,
    GOAL_TARGET_MAX,
    GOAL_WINDOW_N_MAX,
    HABIT_DAYS_MAX,
    MEASUREMENT_VALUE_MAX,
    SYSTEM_USER_ID,
)
from src import data_handler
from src.app_controller import (
    DeviceClock,
    log_failure,
    new_reference_code,
    normalise_clock,
    parse_player_value,
    run_write,
    set_current_player_id,
)
from src.data_goals import (
    GoalStatus,
    GoalValues,
    delete_goal,
    get_drill_goal_measures,
    get_goal,
    insert_goal,
    list_goal_drills,
    list_goal_seasons,
    list_goal_stats,
    list_goals_with_progress,
    lock_goal,
    refresh_goal_statuses,
    season_of_player,
    update_goal,
)
from src.data_measurements import get_physical_test, list_physical_tests
from src.data_players import get_live_player, list_live_players, lock_live_player
from src.data_settings import get_retention_days
from src.layouts import not_found_page
from src.layouts_goals import (
    FILTER_ALL,
    GOAL_SECTION_SUFFIXES,
    GOAL_TYPE_DRILL,
    GOAL_TYPE_HABIT,
    GOAL_TYPE_PHYSICAL,
    GOAL_TYPE_STAT,
    GOALS_PATH,
    METRIC_CLEAN_PCT,
    METRIC_MAKE_PCT,
    METRIC_MAKES,
    METRIC_VALUE_1,
    METRIC_VALUE_2,
    STATUS_ACHIEVED,
    STATUS_ACTIVE,
    STATUS_AUTO,
    STATUS_LABELS,
    WINDOW_DATE_RANGE,
    WINDOW_LAST_N,
    WINDOW_SEASON,
    drill_options,
    goal_list_items,
    goal_new_layout,
    goal_page_layout,
    goal_progress_block,
    goal_section_styles,
    goal_status_text,
    goals_layout,
    season_options,
    stat_options,
)
from src.layouts_measurements import test_options, unit_text

logger = logging.getLogger(__name__)

DIRECTION_AT_LEAST = "at_least"
DIRECTION_AT_MOST = "at_most"
GOAL_TYPES: frozenset[str] = frozenset(
    {GOAL_TYPE_STAT, GOAL_TYPE_DRILL, GOAL_TYPE_HABIT, GOAL_TYPE_PHYSICAL}
)
SCOPE_GAME = "game"
SCOPE_PRACTICE = "practice"
SCOPE_BOTH = "both"
SCOPES: frozenset[str] = frozenset({SCOPE_GAME, SCOPE_PRACTICE, SCOPE_BOTH})
WINDOWS: frozenset[str] = frozenset({WINDOW_LAST_N, WINDOW_SEASON, WINDOW_DATE_RANGE})
BASES: frozenset[str] = frozenset({"best", "latest"})
STATUS_CHOICE_VALUES: frozenset[str] = frozenset({STATUS_AUTO, *STATUS_LABELS})
FILTER_VALUES: frozenset[str] = frozenset({FILTER_ALL, *STATUS_LABELS})
DEFAULT_FILTER = STATUS_ACTIVE
STAT_UNIT_PERCENT = "percent"
STAT_APPLIES_GAME = "game"
CAPTURE_SHOTS = "shots"
CAPTURE_OUTCOMES = "outcomes"
CAPTURE_VALUES = "values"
RESULT_TYPE_PHYSICAL = "physical"
UNIT_SECONDS = "s"
MAX_GROUP_NO = 3
# A picker value holding an id: 1 to 18 ASCII digits (fits bigint).
ID_VALUE_PATTERN = re.compile(r"[0-9]{1,18}")
TARGET_QUANTUM = Decimal(1).scaleb(-GOAL_TARGET_DECIMALS)

MESSAGE_TYPE = "Choose a goal type."
MESSAGE_NAME = f"Enter a goal name of 1 to {GOAL_NAME_MAX_LENGTH} characters."
MESSAGE_TARGET = f"Enter a target from 0 to {GOAL_TARGET_MAX}."
MESSAGE_TARGET_PERCENT = f"A percentage target is at most {GOAL_PERCENT_MAX}."
MESSAGE_TARGET_HABIT = f"A habit target is a whole number of days from 1 to {HABIT_DAYS_MAX}."
MESSAGE_TARGET_PHYSICAL = f"A physical target must be above 0 and at most {MEASUREMENT_VALUE_MAX}."
MESSAGE_START = "Choose a start date."
MESSAGE_DEADLINE = "The deadline cannot be before the start date."
MESSAGE_NOTES = f"Notes are limited to {GOAL_NOTES_MAX_LENGTH} characters."
MESSAGE_STAT = "Choose a stat."
MESSAGE_SCOPE = "Choose Games, Practices or Both."
MESSAGE_GAME_ONLY = "{abbr} counts games only; choose Games or Both."
MESSAGE_WINDOW = "Choose a window: Last N, Season or Dates."
MESSAGE_WINDOW_N = f"Enter how many games or practices count, from 1 to {GOAL_WINDOW_N_MAX}."
MESSAGE_SEASON = "Choose a season."
MESSAGE_RANGE = "Choose both window dates."
MESSAGE_RANGE_ORDER = "The window's end cannot be before its start."
MESSAGE_DRILL = "Choose a drill."
MESSAGE_MEASURE = "Choose what to measure."
MESSAGE_BASIS = "Choose Best result or Latest result."
MESSAGE_TEST = "Choose a physical test."
MESSAGE_STATUS = "Choose a status."
MESSAGE_NO_DATE = "The device date is not known yet. Reload the page and try again."
MESSAGE_SAVE_FAILED = "Could not save the goal. Reference: {ref}"
MESSAGE_LIST_FAILED = "Could not load the goals. Reference: {ref}"
MESSAGE_DELETE_FAILED = "Could not delete the goal. Reference: {ref}"
MESSAGE_SAVED = "Saved."
MESSAGE_DELETE_QUESTION = (
    "Delete the goal {name}? You can restore it from Settings > Recently deleted for {days} "
    "days."
)

HINT_DIRECTION: dict[str, str] = {DIRECTION_AT_LEAST: "At least", DIRECTION_AT_MOST: "At most"}
HINT_HABIT = f"Days per week, 1–{HABIT_DAYS_MAX}"
HINT_STAT_UNITS: dict[str, str] = {
    "percent": "percent (0–100)",
    "ratio": "ratio",
    "count": "average per game or practice",
}
HINT_SEPARATOR = " · "

# The goal form's fields: (key of the raw form dict, element id suffix), in the order of the
# form States of create_goal and save_goal.
FORM_FIELDS: tuple[tuple[str, str], ...] = (
    ("type", "type"),
    ("name", "name"),
    ("target", "target"),
    ("start", "start"),
    ("deadline", "deadline"),
    ("notes", "notes"),
    ("stat", "stat"),
    ("scope", "scope"),
    ("window", "window"),
    ("window_n", "window-n"),
    ("season", "season"),
    ("from", "from"),
    ("to", "to"),
    ("drill", "drill"),
    ("measure", "measure"),
    ("basis", "basis"),
    ("test", "test"),
)
FORM_PREFIXES: tuple[str, ...] = ("goal-new", "goal-edit")
# GoalValues fields compared by progress_fields_changed (everything but name and notes).
PROGRESS_FIELDS: tuple[str, ...] = (
    "target_value",
    "target_direction",
    "start_date",
    "deadline",
    "stat_code",
    "window_kind",
    "window_n",
    "window_season_id",
    "window_from",
    "window_to",
    "window_scope",
    "drill_id",
    "drill_metric",
    "drill_group_no",
    "drill_basis",
    "physical_test_id",
)


# --- Small readers (pure) ---

def _picker_id(value: object) -> int | None:
    """
    Description:
        Reads a picker value holding an id (dmc values are strings).

    Parameters:
        value: e.g. "12".

    Return Values:
        int | None: the id, or None when missing or not 1 to 18 ASCII digits.

    Notes:
        Pure.
    """
    if not isinstance(value, str) or ID_VALUE_PATTERN.fullmatch(value) is None:
        return None
    return int(value)


def _stored_id(value: object) -> int | None:
    """
    Description:
        Reads an id store (an int; a bool is not an id).

    Parameters:
        value: the store's data.

    Return Values:
        int | None: the id, or None.

    Notes:
        Pure.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _parse_date(value: object) -> date | None:
    """
    Description:
        Reads a date picker's value.

    Parameters:
        value: "YYYY-MM-DD" (a date-time string is cut to its date), or None.

    Return Values:
        date | None: the date, or None when missing or unreadable.

    Notes:
        Pure.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_number(value: object) -> Decimal | None:
    """
    Description:
        Reads a NumberInput value as a Decimal.

    Parameters:
        value: an int, float or numeric text; "" or None when empty.

    Return Values:
        Decimal | None: the finite number, or None when empty or not a number.

    Notes:
        Pure. A bool is not a number.
    """
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _clock_date(clock: object) -> date | None:
    """
    Description:
        The device's today from the device-clock store (REQ-055).

    Parameters:
        clock: device-clock.data (or the router's DeviceClock).

    Return Values:
        date | None: the device date, or None when unknown.

    Notes:
        Pure. Never the server's date.
    """
    today = normalise_clock(clock)["today"]
    return date.fromisoformat(today) if today else None


# --- Form rules (pure, COD-17-01; REQ-033) ---

def goal_direction(
    goal_type: str,
    stat: dict | None,
    measures: dict | None,
    metric: str | None,
    group: int | None,
    test: dict | None,
) -> str:
    """
    Description:
        The direction of a goal, derived and never asked (D5): a stat goal follows the
        stat's higher_is_better; a drill goal on makes, make % or clean-rep % is at least,
        and on a number follows that number's own flag on a physical drill, otherwise the
        result type's; a habit goal is at least; a physical goal is at most when the test's
        lower_is_better is set.

    Parameters:
        goal_type: "stat", "drill", "habit" or "physical".
        stat: the stat's list_goal_stats row (stat goals), or None.
        measures: get_drill_goal_measures of the drill (drill goals), or None.
        metric: the drill metric (drill goals), or None.
        group: the outcome group of a clean_pct goal, or None (clean-rep % is always at
            least, so the group does not change the direction).
        test: the test's list_physical_tests row (physical goals), or None.

    Return Values:
        str: "at_least" or "at_most".

    Notes:
        Pure. A missing flag gives "at_least".
    """
    higher: object = True
    if goal_type == GOAL_TYPE_STAT and stat is not None:
        higher = stat.get("higher_is_better")
    elif goal_type == GOAL_TYPE_PHYSICAL and test is not None:
        higher = not test.get("lower_is_better")
    elif goal_type == GOAL_TYPE_DRILL and measures is not None and metric in (
        METRIC_VALUE_1,
        METRIC_VALUE_2,
    ):
        own_flag = measures.get(f"{metric}_higher_is_better")
        if measures.get("result_type_code") == RESULT_TYPE_PHYSICAL and own_flag is not None:
            higher = own_flag
        else:
            higher = measures.get("higher_is_better")
    return DIRECTION_AT_MOST if higher is False else DIRECTION_AT_LEAST


def parse_measure(value: object) -> tuple[str, int | None] | None:
    """
    Description:
        Reads the measure picker's value.

    Parameters:
        value: "makes", "make_pct", "value_1", "value_2" or "clean_pct:<1-3>".

    Return Values:
        tuple[str, int | None] | None: (metric, outcome group or None), or None when the
        value is not one of these.

    Notes:
        Pure.
    """
    if value in (METRIC_MAKES, METRIC_MAKE_PCT, METRIC_VALUE_1, METRIC_VALUE_2):
        return str(value), None
    if isinstance(value, str) and value.startswith(f"{METRIC_CLEAN_PCT}:"):
        group_text = value[len(METRIC_CLEAN_PCT) + 1:]
        if group_text.isdigit() and len(group_text) == 1 and 1 <= int(group_text) <= MAX_GROUP_NO:
            return METRIC_CLEAN_PCT, int(group_text)
    return None


def _value_option(measures: dict, metric: str) -> dict[str, str] | None:
    """
    Description:
        The measure option of a drill's first or second number.

    Parameters:
        measures: get_drill_goal_measures of the drill.
        metric: "value_1" or "value_2".

    Return Values:
        dict[str, str] | None: {"value": metric, "label": "{label} ({unit})"}, or None for
        a second number without a label.

    Notes:
        Pure. Without a label (a non-physical type), the first number is "Seconds" or
        "Count" by the result type's unit.
    """
    label = measures.get(f"{metric}_label")
    unit = measures.get(f"{metric}_unit") or measures.get("value_unit")
    if not label:
        if metric == METRIC_VALUE_2:
            return None
        label = "Seconds" if unit == UNIT_SECONDS else "Count"
    return {"value": metric, "label": f"{label} ({unit})"}


def measure_options(measures: dict | None) -> list[dict[str, str]]:
    """
    Description:
        What a drill goal may measure on a drill, by result type: a shooting drill Makes
        and Make %; an outcome-button drill one "Clean-rep % · {group}" per live group; a
        values drill its first number, and its second when it has a label.

    Parameters:
        measures: get_drill_goal_measures of the drill, or None.

    Return Values:
        list[dict[str, str]]: the measure picker's options (values as parse_measure reads
        them); empty without a drill.

    Notes:
        Pure.
    """
    if not measures:
        return []
    capture = measures.get("capture_kind")
    if capture == CAPTURE_SHOTS:
        return [
            {"value": METRIC_MAKES, "label": "Makes"},
            {"value": METRIC_MAKE_PCT, "label": "Make %"},
        ]
    if capture == CAPTURE_OUTCOMES:
        return [
            {
                "value": f"{METRIC_CLEAN_PCT}:{int(group['group_no'])}",
                "label": f"Clean-rep %{HINT_SEPARATOR}{group['group_name']}",
            }
            for group in measures.get("groups") or []
        ]
    options = [_value_option(measures, METRIC_VALUE_1), _value_option(measures, METRIC_VALUE_2)]
    return [option for option in options if option is not None]


def _target_problem(
    target: Decimal, goal_type: str, stat: dict | None, metric: str | None
) -> str | None:
    """
    Description:
        The target rules of a goal type: 0 to GOAL_TARGET_MAX; at most 100 for a percentage;
        a habit's whole number of days from 1 to 7; a physical value above 0 and at most
        99999.99.

    Parameters:
        target: the target, already rounded to 3 decimals.
        goal_type: the goal's type.
        stat: the chosen stat's row (stat goals), or None.
        metric: the chosen drill metric (drill goals), or None.

    Return Values:
        str | None: the message, or None when the target is fine.

    Notes:
        Pure.
    """
    if target < 0 or target > GOAL_TARGET_MAX:
        return MESSAGE_TARGET
    if goal_type == GOAL_TYPE_HABIT:
        if target != target.to_integral_value() or not 1 <= target <= HABIT_DAYS_MAX:
            return MESSAGE_TARGET_HABIT
    if goal_type == GOAL_TYPE_PHYSICAL and not 0 < target <= MEASUREMENT_VALUE_MAX:
        return MESSAGE_TARGET_PHYSICAL
    is_percent = (
        goal_type == GOAL_TYPE_STAT and stat is not None and stat.get("unit") == STAT_UNIT_PERCENT
    ) or (goal_type == GOAL_TYPE_DRILL and metric in (METRIC_MAKE_PCT, METRIC_CLEAN_PCT))
    if is_percent and target > GOAL_PERCENT_MAX:
        return MESSAGE_TARGET_PERCENT
    return None


def _parse_stat_fields(raw: dict, stat: dict | None) -> tuple[dict | None, str | None]:
    """
    Description:
        The stat goal fields in screen order: the stat, the scope (a game-only stat refuses
        Practices, D17) and the window with its own fields.

    Parameters:
        raw: the raw form dict.
        stat: the chosen stat's row, or None when none is chosen or it is not eligible.

    Return Values:
        tuple[dict | None, str | None]: (GoalValues keyword fields, None) or (None, the
        message).

    Notes:
        Pure. Whether the season is the player's is checked by the callers (database).
    """
    if stat is None:
        return None, MESSAGE_STAT
    scope = raw.get("scope")
    if scope not in SCOPES:
        return None, MESSAGE_SCOPE
    if scope == SCOPE_PRACTICE and stat.get("applies_to") == STAT_APPLIES_GAME:
        return None, MESSAGE_GAME_ONLY.format(abbr=stat.get("abbreviation"))
    window = raw.get("window")
    fields: dict = {"stat_code": str(stat["stat_code"]), "window_kind": window, "window_scope": scope}
    if window == WINDOW_LAST_N:
        count = _parse_number(raw.get("window_n"))
        if count is None or count != count.to_integral_value() or not 1 <= count <= GOAL_WINDOW_N_MAX:
            return None, MESSAGE_WINDOW_N
        return {**fields, "window_n": int(count)}, None
    if window == WINDOW_SEASON:
        season_id = _picker_id(raw.get("season"))
        if season_id is None:
            return None, MESSAGE_SEASON
        return {**fields, "window_season_id": season_id}, None
    if window == WINDOW_DATE_RANGE:
        window_from = _parse_date(raw.get("from"))
        window_to = _parse_date(raw.get("to"))
        if window_from is None or window_to is None:
            return None, MESSAGE_RANGE
        if window_to < window_from:
            return None, MESSAGE_RANGE_ORDER
        return {**fields, "window_from": window_from, "window_to": window_to}, None
    return None, MESSAGE_WINDOW


def _parse_drill_fields(raw: dict, measures: dict | None) -> tuple[dict | None, str | None]:
    """
    Description:
        The drill goal fields in screen order: the drill, the measure (one of
        measure_options) and the basis.

    Parameters:
        raw: the raw form dict.
        measures: get_drill_goal_measures of the chosen drill, or None.

    Return Values:
        tuple[dict | None, str | None]: (GoalValues keyword fields, None) or (None, the
        message).

    Notes:
        Pure.
    """
    drill_id = _picker_id(raw.get("drill"))
    if drill_id is None or measures is None or int(measures["drill_id"]) != drill_id:
        return None, MESSAGE_DRILL
    allowed = {option["value"] for option in measure_options(measures)}
    parsed = parse_measure(raw.get("measure"))
    if parsed is None or raw.get("measure") not in allowed:
        return None, MESSAGE_MEASURE
    if raw.get("basis") not in BASES:
        return None, MESSAGE_BASIS
    metric, group = parsed
    return {
        "drill_id": drill_id,
        "drill_metric": metric,
        "drill_group_no": group,
        "drill_basis": str(raw.get("basis")),
    }, None


def parse_goal_form(
    raw: dict,
    *,
    stats: list[dict],
    measures: dict | None,
    tests: list[dict],
    today: date | None,
) -> tuple[GoalValues | None, str | None]:
    """
    Description:
        Checks a goal form in screen order and returns the first problem: the type, the
        name (1-100 characters after trimming), the target (0 to GOAL_TARGET_MAX, rounded
        half-up to 3 decimals; at most 100 for percentages; a habit's whole 1-7 days; a
        physical value above 0 and at most 99999.99), the dates (a start; a deadline not
        before it), the notes, then the type's own fields (stat, scope and window; drill,
        measure and basis; physical test). Sets target_direction with goal_direction.

    Parameters:
        raw: the form values keyed as FORM_FIELDS (type, name, target, start, deadline,
            notes, stat, scope, window, window_n, season, from, to, drill, measure, basis,
            test).
        stats: list_goal_stats rows (the eligible stats).
        measures: get_drill_goal_measures of the chosen drill, or None.
        tests: list_physical_tests rows (the live tests).
        today: the device's date; no rule of the form depends on it today (kept for the
            plan's contract).

    Return Values:
        tuple[GoalValues | None, str | None]: (the values, None), or (None, the message).

    Notes:
        Pure. The database checks (the season is the player's, the references are live)
        are re-done by the callers and the write statements (D18).
    """
    goal_type = raw.get("type")
    if goal_type not in GOAL_TYPES:
        return None, MESSAGE_TYPE
    raw_name = raw.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) else ""
    if not name or len(name) > GOAL_NAME_MAX_LENGTH:
        return None, MESSAGE_NAME
    stat = next((s for s in stats if s.get("stat_code") == raw.get("stat")), None)
    parsed_measure = parse_measure(raw.get("measure"))
    metric = parsed_measure[0] if parsed_measure else None
    number = _parse_number(raw.get("target"))
    if number is None:
        return None, MESSAGE_TARGET
    target = number.quantize(TARGET_QUANTUM, rounding=ROUND_HALF_UP)
    problem = _target_problem(target, str(goal_type), stat, metric)
    if problem:
        return None, problem
    start_date = _parse_date(raw.get("start"))
    if start_date is None:
        return None, MESSAGE_START
    deadline = _parse_date(raw.get("deadline"))
    if deadline is not None and deadline < start_date:
        return None, MESSAGE_DEADLINE
    raw_notes = raw.get("notes")
    notes_text = raw_notes.strip() if isinstance(raw_notes, str) else ""
    if len(notes_text) > GOAL_NOTES_MAX_LENGTH:
        return None, MESSAGE_NOTES

    test: dict | None = None
    found: dict | None
    if goal_type == GOAL_TYPE_STAT:
        found, problem = _parse_stat_fields(raw, stat)
    elif goal_type == GOAL_TYPE_DRILL:
        found, problem = _parse_drill_fields(raw, measures)
    elif goal_type == GOAL_TYPE_PHYSICAL:
        test_id = _picker_id(raw.get("test"))
        test = next((t for t in tests if int(t["physical_test_id"]) == test_id), None)
        found, problem = ({"physical_test_id": test_id}, None) if test else (None, MESSAGE_TEST)
    else:
        found, problem = {}, None
    if found is None:
        return None, problem
    fields: dict = found
    direction = goal_direction(
        str(goal_type), stat, measures, fields.get("drill_metric"), fields.get("drill_group_no"), test
    )
    return (
        GoalValues(
            goal_name=name,
            goal_type=str(goal_type),
            target_value=target,
            target_direction=direction,
            start_date=start_date,
            deadline=deadline,
            notes=notes_text or None,
            **fields,
        ),
        None,
    )


def _same_value(old: object, new: object) -> bool:
    """
    Description:
        Whether a stored column and a form value are equal; numbers compare by value.

    Parameters:
        old: the stored value.
        new: the form value.

    Return Values:
        bool: True when equal.

    Notes:
        Pure. 10 (smallint) equals Decimal("10.000").
    """
    if isinstance(old, (int, Decimal)) and isinstance(new, (int, Decimal)):
        if isinstance(old, bool) or isinstance(new, bool):
            return old == new
        return Decimal(old) == Decimal(new)
    return old == new


def progress_fields_changed(old_row: dict, values: GoalValues) -> bool:
    """
    Description:
        Whether an edit changes anything progress depends on: every field but the name and
        the notes (D9).

    Parameters:
        old_row: the goal's stored row (lock_goal).
        values: the checked form values.

    Return Values:
        bool: True when any progress field differs.

    Notes:
        Pure.
    """
    return any(
        not _same_value(old_row.get(field), getattr(values, field)) for field in PROGRESS_FIELDS
    )


def status_after_save(
    choice: str, old_row: dict | None, changed: bool, today: date
) -> GoalStatus:
    """
    Description:
        The status a goal is saved with (D9). "auto": not manual; a new goal, a goal that
        was manual, or one whose progress fields changed becomes active with no achieved
        date (the refresh in the same save then judges it); otherwise its status and date
        are kept. A manual "achieved" keeps an earlier achieved date of an achieved goal,
        else records today; a manual "active", "missed" or "archived" clears the date.

    Parameters:
        choice: "auto", "active", "achieved", "missed" or "archived".
        old_row: the stored row (lock_goal), or None for a new goal.
        changed: progress_fields_changed of the edit (ignored for a new goal).
        today: the device's date (REQ-055).

    Return Values:
        GoalStatus: the status, manual flag and achieved date.

    Notes:
        Pure. Raises ValueError for an unknown choice.
    """
    if choice == STATUS_AUTO:
        if old_row is None or old_row.get("status_is_manual") or changed:
            return GoalStatus(STATUS_ACTIVE, False, None)
        achieved_on = old_row.get("achieved_on")
        return GoalStatus(
            str(old_row.get("status")),
            False,
            achieved_on if isinstance(achieved_on, date) else None,
        )
    if choice == STATUS_ACHIEVED:
        earlier = old_row.get("achieved_on") if old_row is not None else None
        if old_row is not None and old_row.get("status") == STATUS_ACHIEVED and isinstance(earlier, date):
            return GoalStatus(STATUS_ACHIEVED, True, earlier)
        return GoalStatus(STATUS_ACHIEVED, True, today)
    if choice in STATUS_LABELS:
        return GoalStatus(choice, True, None)
    raise ValueError(f"unknown goal status choice {choice!r}")


def target_hint_text(
    goal_type: object,
    stat: dict | None,
    measures: dict | None,
    measure_value: object,
    test: dict | None,
) -> str:
    """
    Description:
        The hint under the target: the derived direction and what the number means, e.g.
        "At least · percent (0–100)", "At most · seconds", "Days per week, 1–7".

    Parameters:
        goal_type: the type picker's value.
        stat: the chosen stat's row, or None.
        measures: get_drill_goal_measures of the chosen drill, or None.
        measure_value: the measure picker's value.
        test: the chosen test's row, or None.

    Return Values:
        str: the hint; "" until the type's choice is made.

    Notes:
        Pure.
    """
    if goal_type == GOAL_TYPE_HABIT:
        return HINT_HABIT
    if goal_type == GOAL_TYPE_STAT and stat is not None:
        direction = goal_direction(GOAL_TYPE_STAT, stat, None, None, None, None)
        unit = HINT_STAT_UNITS.get(str(stat.get("unit")), str(stat.get("unit")))
        return f"{HINT_DIRECTION[direction]}{HINT_SEPARATOR}{unit}"
    if goal_type == GOAL_TYPE_PHYSICAL and test is not None:
        direction = goal_direction(GOAL_TYPE_PHYSICAL, None, None, None, None, test)
        return f"{HINT_DIRECTION[direction]}{HINT_SEPARATOR}{unit_text(test.get('unit'))}"
    parsed = parse_measure(measure_value)
    if goal_type != GOAL_TYPE_DRILL or measures is None or parsed is None:
        return ""
    metric, group = parsed
    direction = goal_direction(GOAL_TYPE_DRILL, None, measures, metric, group, None)
    if metric == METRIC_MAKES:
        unit = "makes"
    elif metric in (METRIC_MAKE_PCT, METRIC_CLEAN_PCT):
        unit = HINT_STAT_UNITS[STAT_UNIT_PERCENT]
    else:
        raw_unit = measures.get(f"{metric}_unit") or measures.get("value_unit")
        unit = "seconds" if raw_unit == UNIT_SECONDS else str(raw_unit)
    return f"{HINT_DIRECTION[direction]}{HINT_SEPARATOR}{unit}"


def _raw_form(form_values: tuple) -> dict:
    """
    Description:
        The goal form's State values as a dict keyed as FORM_FIELDS.

    Parameters:
        form_values: the 17 form States, in FORM_FIELDS order.

    Return Values:
        dict: key -> value.

    Notes:
        Pure.
    """
    return {key: value for (key, _suffix), value in zip(FORM_FIELDS, form_values, strict=True)}


# --- Page builders (REQ-032, REQ-033, REQ-041) ---

def _refresh_all(clock: DeviceClock) -> None:
    """
    Description:
        Refreshes every live player's goal statuses as SYSTEM_USER_ID before a goals page is
        built (D4), so missed goals are marked whenever the app is opened (REQ-032,
        COD-20-03).

    Parameters:
        clock: the device clock; nothing runs while its date is unknown.

    Return Values:
        None.

    Notes:
        Runs only when the device date is within GOAL_REFRESH_MAX_DATE_SKEW_DAYS of the
        server's UTC date (D4 amendment, 2026-10-26): a device with a wrong date must not
        mark every player's goals Missed or Achieved. Otherwise it is skipped with a warning
        giving the two dates only. The device date stays the date used (REQ-055).
        run_write logs a failure with its reference; it is also logged here as a warning and
        never blocks the page.
    """
    today = _clock_date(clock)
    if today is None:
        return
    server_today = datetime.now(UTC).date()
    if abs((today - server_today).days) > GOAL_REFRESH_MAX_DATE_SKEW_DAYS:
        logger.warning(
            "Goal status refresh skipped: device date %s is far from server UTC date %s",
            today.isoformat(),
            server_today.isoformat(),
        )
        return
    result = run_write(
        SYSTEM_USER_ID,
        lambda session: refresh_goal_statuses(session, today, None),
        "refresh goal statuses",
    )
    if not result.ok:
        logger.warning("Goal status refresh failed (ref %s); page shown without it", result.ref)


def _form_options(
    session: Session, player_id: int, measures: dict | None
) -> dict[str, list[dict[str, str]]]:
    """
    Description:
        The goal form's picker options: eligible stats, current drills (plus the goal's own
        drill when it is retired), the drill's measures, the player's seasons, the tests.

    Parameters:
        session: the request session.
        player_id: the goal's player (seasons are per player).
        measures: get_drill_goal_measures of the goal's drill, or None.

    Return Values:
        dict[str, list[dict[str, str]]]: {"stats", "drills", "measures", "seasons",
        "tests"}.

    Notes:
        Read only; five statements, none in a loop.
    """
    drills = list_goal_drills(session)
    if measures is not None and all(int(d["drill_id"]) != int(measures["drill_id"]) for d in drills):
        drills = [
            *drills,
            {"drill_id": measures["drill_id"], "drill_name": f"{measures['drill_name']} (retired)"},
        ]
    return {
        "stats": stat_options(list_goal_stats(session)),
        "drills": drill_options(drills),
        "measures": measure_options(measures),
        "seasons": season_options(list_goal_seasons(session, player_id)),
        "tests": test_options(list_physical_tests(session)),
    }


def build_goals_page(player_id: int, clock: DeviceClock, params: dict[str, str]) -> Component:
    """
    Description:
        Builds the Goals screen for the selected player: refreshes the statuses, then lists
        the active goals with progress.

    Parameters:
        player_id: the selected player from current_player_id() (never None here). Never a
            user id (COD-8-01).
        clock: the device clock (today for progress, REQ-055).
        params: the route parameters (none).

    Return Values:
        Component: the page.

    Notes:
        Exceptions propagate to the router, which shows the error page with a reference.
        Without a device date the progress cannot be computed, so the router's error page
        is shown (the clock store is normally set before the router runs).
    """
    _refresh_all(clock)
    today = _clock_date(clock)
    if today is None:
        raise ValueError("device date unknown")
    session = data_handler.get_session()
    goals = list_goals_with_progress(session, player_id, today, (DEFAULT_FILTER,), None)
    return goals_layout(list_live_players(session), player_id, goals, DEFAULT_FILTER)


def build_goal_new_page(
    player_id: int, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds the New goal page for the selected player (D6).

    Parameters:
        player_id: the selected player (never None here). Never a user id.
        clock: the device clock (today is the default start date).
        params: the route parameters (none).

    Return Values:
        Component: the page, or the not-found page when the player is no longer live.

    Notes:
        goal-new-player-id is written by the layout only after this get_live_player check.
    """
    session = data_handler.get_session()
    player = get_live_player(session, player_id)
    if player is None:
        return not_found_page()
    return goal_new_layout(player, _form_options(session, player_id, None), clock["today"])


def build_goal_page(player_id: int, clock: DeviceClock, params: dict[str, str]) -> Component:
    """
    Description:
        Builds the goal page for the goal in the URL: refreshes the statuses, then reads the
        goal with its progress and its drill's measures.

    Parameters:
        player_id: the selected player (unused: the goal's player comes from its row).
        clock: the device clock (today for progress, zone for the audit lines).
        params: {"goal_id": "<digits>"} from /goals/<id>.

    Return Values:
        Component: the page, or the not-found page when the goal or its profile is not live.

    Notes:
        goal-edit-id is written by the layout only after this get_goal check.
    """
    _refresh_all(clock)
    today = _clock_date(clock)
    if today is None:
        raise ValueError("device date unknown")
    session = data_handler.get_session()
    goal = get_goal(session, int(params["goal_id"]), today)
    if goal is None:
        return not_found_page()
    measures = None
    if goal.get("goal_type") == GOAL_TYPE_DRILL and goal.get("drill_id") is not None:
        measures = get_drill_goal_measures(session, int(goal["drill_id"]))
    options = _form_options(session, int(goal["player_id"]), measures)
    return goal_page_layout(goal, options, measures, clock["zone"])


# --- Callbacks (REQ-032, REQ-033, REQ-005) ---

def show_goals(player_value: str | None, status_filter: str | None, clock: object) -> object:
    """
    Description:
        The Goals player picker and status filter: makes the picked player the selected
        player and lists that player's goals of the chosen status.

    Parameters:
        player_value: goals-player.value, the player id as a string.
        status_filter: goals-filter.value ("active", "achieved", "missed", "archived" or
            "all").
        clock: device-clock.data (State; today for progress).

    Return Values:
        object: the new children of goals-list, or a message with a reference.

    Notes:
        A boundary (COD-4-04). Raises PreventUpdate for a value that is not a player id or
        a filter, and when the player is no longer live (logged as a warning).
    """
    player_id = parse_player_value(player_value)
    if player_id is None or status_filter not in FILTER_VALUES:
        raise PreventUpdate
    today = _clock_date(clock)
    if today is None:
        return MESSAGE_NO_DATE
    statuses = None if status_filter == FILTER_ALL else (str(status_filter),)
    try:
        selected = set_current_player_id(player_id)
        goals = (
            list_goals_with_progress(data_handler.get_session(), player_id, today, statuses, None)
            if selected
            else None
        )
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Goals list failed (ref %s)", ref)
        return MESSAGE_LIST_FAILED.format(ref=ref)
    if goals is None:
        logger.warning("Goals picker chose player %s, which is not a live player", player_id)
        raise PreventUpdate
    return goal_list_items(goals, "goals")


def goal_sections(goal_type: str | None, window_kind: str | None) -> tuple[dict, ...]:
    """
    Description:
        Shows the form section of the chosen type and, for a stat goal, of its window.

    Parameters:
        goal_type: {prefix}-type.value.
        window_kind: {prefix}-window.value.

    Return Values:
        tuple[dict, ...]: the styles of {prefix}-sec-stat, -sec-drill, -sec-habit,
        -sec-physical, -sec-last-n, -sec-season and -sec-range.

    Notes:
        Pure apart from Dash; registered once per form prefix.
    """
    styles = goal_section_styles(goal_type, window_kind)
    return tuple(styles[suffix] for suffix in GOAL_SECTION_SUFFIXES)


def goal_drill_changed(drill_value: str | None) -> tuple[object, object]:
    """
    Description:
        A new drill in the form: offers its measures and picks the first.

    Parameters:
        drill_value: {prefix}-drill.value (the lineage id as a string).

    Return Values:
        tuple[object, object]: {prefix}-measure.data and .value ([] and None without a
        drill).

    Notes:
        A boundary (COD-4-04): a failed read leaves the measure picker unchanged.
    """
    drill_id = _picker_id(drill_value)
    if drill_id is None:
        return [], None
    try:
        options = measure_options(get_drill_goal_measures(data_handler.get_session(), drill_id))
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Drill measures read failed (ref %s)", ref)
        return no_update, no_update
    return options, options[0]["value"] if options else None


def goal_target_hint(
    goal_type: str | None,
    stat: str | None,
    drill: str | None,
    measure: str | None,
    test: str | None,
) -> str:
    """
    Description:
        The hint under the target (direction and meaning), refreshed as the form changes.

    Parameters:
        goal_type: {prefix}-type.value.
        stat: {prefix}-stat.value.
        drill: {prefix}-drill.value.
        measure: {prefix}-measure.value.
        test: {prefix}-test.value.

    Return Values:
        str: target_hint_text, or "" when a read fails (logged with a reference).

    Notes:
        A boundary (COD-4-04). Runs when the page is inserted too, so the hint is there from
        the start; it writes only inside the page.
    """
    try:
        session = data_handler.get_session()
        stat_row = None
        measures = None
        test_row = None
        if goal_type == GOAL_TYPE_STAT and stat:
            stat_row = next((s for s in list_goal_stats(session) if s["stat_code"] == stat), None)
        drill_id = _picker_id(drill)
        if goal_type == GOAL_TYPE_DRILL and drill_id is not None:
            measures = get_drill_goal_measures(session, drill_id)
        test_id = _picker_id(test)
        if goal_type == GOAL_TYPE_PHYSICAL and test_id is not None:
            test_row = get_physical_test(session, test_id)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Goal hint read failed (ref %s)", ref)
        return ""
    return target_hint_text(goal_type, stat_row, measures, measure, test_row)


def _check_form(
    session: Session, raw: dict, player_id: int, today: date
) -> tuple[GoalValues | None, str | None]:
    """
    Description:
        Reads what parse_goal_form needs, parses the form, and checks that a season window
        is a live season of the goal's player.

    Parameters:
        session: the request session.
        raw: the raw form dict (its "type" is the goal's type).
        player_id: the goal's player.
        today: the device's date.

    Return Values:
        tuple[GoalValues | None, str | None]: (the values, None) or (None, the message).

    Notes:
        Read only; the write statements re-check every reference (D18).
    """
    measures = None
    drill_id = _picker_id(raw.get("drill"))
    if raw.get("type") == GOAL_TYPE_DRILL and drill_id is not None:
        measures = get_drill_goal_measures(session, drill_id)
    values, problem = parse_goal_form(
        raw,
        stats=list_goal_stats(session),
        measures=measures,
        tests=list_physical_tests(session),
        today=today,
    )
    if values is None:
        return None, problem
    if values.window_season_id is not None and not season_of_player(
        session, values.window_season_id, player_id
    ):
        return None, MESSAGE_SEASON
    return values, None


def create_goal(
    n_clicks: int | None, player_store: object, clock: object, *form_values: object
) -> tuple[object, str]:
    """
    Description:
        Save goal on New goal: checks the form, then in one transaction locks the live
        profile, adds the goal (status automatic) and refreshes that player's statuses;
        opens the new goal's page.

    Parameters:
        n_clicks: goal-new-submit.n_clicks.
        player_store: goal-new-player-id.data (written after get_live_player).
        clock: device-clock.data (today, REQ-055).
        *form_values: the 17 goal-new form values in FORM_FIELDS order.

    Return Values:
        tuple[object, str]: url.pathname ("/goals/<id>" on success, else no_update) and
        goal-new-feedback text.

    Notes:
        A boundary (COD-4-04). Raises PreventUpdate without a click (router insertion trap).
        The acting user is current_user.user_id; the goal's player is the store, never
        current_player_id() at callback time (D6).
    """
    if not n_clicks:
        raise PreventUpdate
    player_id = _stored_id(player_store)
    today = _clock_date(clock)
    if today is None:
        return no_update, MESSAGE_NO_DATE
    if player_id is None:
        ref = new_reference_code()
        logger.error("Create goal refused (ref %s): goal-new-player-id is not an id", ref)
        return no_update, MESSAGE_SAVE_FAILED.format(ref=ref)
    try:
        values, problem = _check_form(data_handler.get_session(), _raw_form(form_values), player_id, today)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Goal form check failed (ref %s)", ref)
        return no_update, MESSAGE_SAVE_FAILED.format(ref=ref)
    if values is None:
        return no_update, problem or ""
    checked: GoalValues = values
    goal_player_id: int = player_id

    def work(session: Session) -> int:
        """
        Description:
            Locks the live profile, adds the goal and refreshes the player's statuses.

        Parameters:
            session: the write session.

        Return Values:
            int: the new goal_id.

        Notes:
            A removed profile raises LookupError, so nothing is written (D14).
        """
        if not lock_live_player(session, goal_player_id):
            raise LookupError(f"player {goal_player_id} is not live")
        goal_id = insert_goal(
            session, goal_player_id, checked, status_after_save(STATUS_AUTO, None, True, today)
        )
        refresh_goal_statuses(session, today, goal_player_id)
        return goal_id

    result = run_write(current_user.user_id, work, "add goal")
    if not result.ok or result.value is None:
        return no_update, MESSAGE_SAVE_FAILED.format(ref=result.ref)
    logger.info("Added goal %s for player %s", result.value, goal_player_id)
    return f"{GOALS_PATH}/{result.value}", ""


def save_goal(
    n_clicks: int | None,
    goal_id_store: object,
    status_choice: str | None,
    clock: object,
    *form_values: object,
) -> tuple[str, str, object, object]:
    """
    Description:
        Save on the goal page: checks the form against the goal's own player and type (the
        type cannot change, D7), then in one transaction locks the goal, saves it with
        status_after_save and refreshes its player's statuses; shows the new progress.

    Parameters:
        n_clicks: goal-edit-save.n_clicks.
        goal_id_store: goal-edit-id.data (written after get_goal).
        status_choice: goal-edit-status.value ("auto" or a status).
        clock: device-clock.data (today, REQ-055).
        *form_values: the 17 goal-edit form values in FORM_FIELDS order.

    Return Values:
        tuple[str, str, object, object]: goal-edit-saved ("Saved." or ""),
        goal-edit-feedback, goal-progress.children and goal-edit-status-text (no_update
        unless saved).

    Notes:
        A boundary (COD-4-04). The goal's player comes from its row, never the selected
        player.
    """
    if not n_clicks:
        raise PreventUpdate
    goal_id = _stored_id(goal_id_store)
    today = _clock_date(clock)
    if today is None:
        return "", MESSAGE_NO_DATE, no_update, no_update
    if status_choice not in STATUS_CHOICE_VALUES:
        return "", MESSAGE_STATUS, no_update, no_update
    if goal_id is None:
        ref = new_reference_code()
        logger.error("Save goal refused (ref %s): goal-edit-id is not an id", ref)
        return "", MESSAGE_SAVE_FAILED.format(ref=ref), no_update, no_update
    try:
        session = data_handler.get_session()
        goal = get_goal(session, goal_id, today)
        if goal is None:
            raise LookupError(f"goal {goal_id} is not live")
        raw = {**_raw_form(form_values), "type": goal["goal_type"]}
        values, problem = _check_form(session, raw, int(goal["player_id"]), today)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Goal form check failed (ref %s)", ref)
        return "", MESSAGE_SAVE_FAILED.format(ref=ref), no_update, no_update
    if values is None:
        return "", problem or "", no_update, no_update
    checked: GoalValues = values
    edited_id: int = goal_id
    choice = str(status_choice)

    def work(write_session: Session) -> None:
        """
        Description:
            Locks the goal, saves it with its new status and refreshes its player.

        Parameters:
            write_session: the write session.

        Return Values:
            None.

        Notes:
            A goal deleted meanwhile raises LookupError (rolled back, reference shown).
        """
        old_row = lock_goal(write_session, edited_id)
        if old_row is None:
            raise LookupError(f"goal {edited_id} is not live")
        status = status_after_save(choice, old_row, progress_fields_changed(old_row, checked), today)
        if not update_goal(write_session, edited_id, checked, status):
            raise LookupError(f"goal {edited_id} could not be updated")
        refresh_goal_statuses(write_session, today, int(old_row["player_id"]))

    result = run_write(current_user.user_id, work, "save goal")
    if not result.ok:
        return "", MESSAGE_SAVE_FAILED.format(ref=result.ref), no_update, no_update
    logger.info("Saved goal %s", edited_id)
    try:
        saved = get_goal(data_handler.get_session(), edited_id, today)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Goal reload failed (ref %s)", ref)
        saved = None
    if saved is None:
        return MESSAGE_SAVED, "", no_update, no_update
    return MESSAGE_SAVED, "", goal_progress_block(saved, "goal"), goal_status_text(saved)


def open_delete_goal(
    n_clicks: int | None, goal_id_store: object, clock: object
) -> tuple[bool, str, str]:
    """
    Description:
        Delete goal: opens the confirmation (REQ-005) with the goal's name and the
        retention period.

    Parameters:
        n_clicks: goal-delete-open.n_clicks.
        goal_id_store: goal-edit-id.data.
        clock: device-clock.data (State).

    Return Values:
        tuple[bool, str, str]: goal-delete-modal.opened (True), goal-delete-text and
        goal-delete-feedback ("" or a message with a reference).

    Notes:
        A boundary (COD-4-04): a failed read still opens the dialog with a reference, and
        Delete then fails safely.
    """
    if not n_clicks:
        raise PreventUpdate
    try:
        goal_id = _stored_id(goal_id_store)
        today = _clock_date(clock)
        session = data_handler.get_session()
        goal = get_goal(session, goal_id, today) if goal_id is not None and today else None
        if goal is None:
            raise LookupError("goal is not live")
        days = get_retention_days(session)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Goal delete dialog failed (ref %s)", ref)
        return True, "", MESSAGE_DELETE_FAILED.format(ref=ref)
    return True, MESSAGE_DELETE_QUESTION.format(name=goal["goal_name"], days=days), ""


def confirm_delete_goal(n_clicks: int | None, goal_id_store: object) -> tuple[object, str]:
    """
    Description:
        The dialog's Delete: deletes the goal through fn_soft_delete('goal') and returns to
        the Goals list.

    Parameters:
        n_clicks: goal-delete-confirm.n_clicks.
        goal_id_store: goal-edit-id.data.

    Return Values:
        tuple[object, str]: url.pathname ("/goals" on success, else no_update) and
        goal-delete-feedback text.

    Notes:
        A boundary (COD-4-04). Raises PreventUpdate without a click (router insertion trap).
    """
    if not n_clicks:
        raise PreventUpdate
    goal_id = _stored_id(goal_id_store)
    if goal_id is None:
        ref = new_reference_code()
        logger.error("Delete goal refused (ref %s): goal-edit-id is not an id", ref)
        return no_update, MESSAGE_DELETE_FAILED.format(ref=ref)
    deleted_id: int = goal_id
    result = run_write(
        current_user.user_id,
        lambda session: delete_goal(session, deleted_id),
        "delete goal",
    )
    if not result.ok:
        return no_update, MESSAGE_DELETE_FAILED.format(ref=result.ref)
    logger.info("Deleted goal %s", deleted_id)
    return GOALS_PATH, ""


# --- Registration ---

def _form_states(prefix: str) -> list[State]:
    """
    Description:
        The States of a goal form's 17 fields, in FORM_FIELDS order.

    Parameters:
        prefix: "goal-new" or "goal-edit".

    Return Values:
        list[State]: one State per field (property "value").

    Notes:
        None.
    """
    return [State(f"{prefix}-{suffix}", "value") for _key, suffix in FORM_FIELDS]


def _register_form_callbacks(app: Dash, prefix: str) -> None:
    """
    Description:
        Registers the callbacks shared by both goal forms: the sections, the drill's
        measures and the target hint.

    Parameters:
        app: the Dash application.
        prefix: "goal-new" or "goal-edit".

    Return Values:
        None.

    Notes:
        The sections and measures run only on a change (the layout sets the initial ones;
        on the goal page the stored measure must stay); the hint also runs when the page is
        inserted.
    """
    app.callback(
        *[Output(f"{prefix}-{suffix}", "style") for suffix in GOAL_SECTION_SUFFIXES],
        Input(f"{prefix}-type", "value"),
        Input(f"{prefix}-window", "value"),
        prevent_initial_call=True,
    )(goal_sections)

    app.callback(
        Output(f"{prefix}-measure", "data"),
        Output(f"{prefix}-measure", "value"),
        Input(f"{prefix}-drill", "value"),
        prevent_initial_call=True,
    )(goal_drill_changed)

    app.callback(
        Output(f"{prefix}-target-hint", "children"),
        Input(f"{prefix}-type", "value"),
        Input(f"{prefix}-stat", "value"),
        Input(f"{prefix}-drill", "value"),
        Input(f"{prefix}-measure", "value"),
        Input(f"{prefix}-test", "value"),
    )(goal_target_hint)


def register_goals_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the Goals callbacks: the list's picker and filter, the form callbacks of
        both forms, Save goal, Save, and the delete dialog's open, Cancel (clientside) and
        Delete.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        url.pathname is written by several callbacks, so create_goal and
        confirm_delete_goal use allow_duplicate and ignore runs without a click (router
        insertion trap, notes_for_agents §8).
    """
    app.callback(
        Output("goals-list", "children"),
        Input("goals-player", "value"),
        Input("goals-filter", "value"),
        State("device-clock", "data"),
        prevent_initial_call=True,
    )(show_goals)

    for prefix in FORM_PREFIXES:
        _register_form_callbacks(app, prefix)

    app.callback(
        Output("url", "pathname", allow_duplicate=True),
        Output("goal-new-feedback", "children"),
        Input("goal-new-submit", "n_clicks"),
        State("goal-new-player-id", "data"),
        State("device-clock", "data"),
        *_form_states("goal-new"),
        prevent_initial_call=True,
    )(create_goal)

    app.callback(
        Output("goal-edit-saved", "children"),
        Output("goal-edit-feedback", "children"),
        Output("goal-progress", "children"),
        Output("goal-edit-status-text", "children"),
        Input("goal-edit-save", "n_clicks"),
        State("goal-edit-id", "data"),
        State("goal-edit-status", "value"),
        State("device-clock", "data"),
        *_form_states("goal-edit"),
        prevent_initial_call=True,
    )(save_goal)

    app.callback(
        Output("goal-delete-modal", "opened"),
        Output("goal-delete-text", "children"),
        Output("goal-delete-feedback", "children"),
        Input("goal-delete-open", "n_clicks"),
        State("goal-edit-id", "data"),
        State("device-clock", "data"),
        prevent_initial_call=True,
    )(open_delete_goal)

    app.clientside_callback(
        ClientsideFunction("hoopster", "close_modal"),
        Output("goal-delete-modal", "opened", allow_duplicate=True),
        Input("goal-delete-cancel", "n_clicks"),
        prevent_initial_call=True,
    )

    app.callback(
        Output("url", "pathname", allow_duplicate=True),
        Output("goal-delete-feedback", "children", allow_duplicate=True),
        Input("goal-delete-confirm", "n_clicks"),
        State("goal-edit-id", "data"),
        prevent_initial_call=True,
    )(confirm_delete_goal)
