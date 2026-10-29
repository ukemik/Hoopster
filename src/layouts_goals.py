"""Goals UI for Hoopster: the Goals list with its player picker and status filter, the shared
goal form (New goal and the goal page), the progress blocks, and the Home goals section
(REQ-032, REQ-033, REQ-041; task 2026-10-23_goals_measurements).

Routes /goals, /goals/new and /goals/<id>; the More drawer and Home link here. Built only
through the wrappers of src/layouts.py (COD-5-01). Layout modules import nothing from
controllers; the callbacks live in src/controller_goals.py.
"""

import logging
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import dash_mantine_components as dmc
from dash import dcc, html
from dash.development.base_component import Component

from config.app_config import (
    GOAL_NAME_MAX_LENGTH,
    GOAL_NOTES_MAX_LENGTH,
    GOAL_TARGET_DECIMALS,
    GOAL_TARGET_MAX,
    GOAL_WINDOW_N_MAX,
)
from src.layouts import (
    CONTROL_SIZE,
    audit_fields_block,
    confirm_modal,
    form_message,
    nav_link,
    page_skeleton,
    player_options,
    primary_button,
    secondary_button,
)
from src.layouts_entry import format_entry_datetime

logger = logging.getLogger(__name__)

GOALS_PATH = "/goals"
GOAL_NEW_PATH = "/goals/new"
DATE_PICKER_FORMAT = "ddd D MMM YYYY"
SEGMENTED_CLASS = "hoopster-segmented"
ITEM_CLASS = "hoopster-recent-item hoopster-recent-link"
HIDDEN_STYLE: dict[str, str] = {"display": "none"}
SHOWN_STYLE: dict[str, str] = {}
NO_VALUE_TEXT = "–"
DETAIL_SEPARATOR = " · "

# Goal types (h_data.goals.goal_type).
GOAL_TYPE_STAT = "stat"
GOAL_TYPE_DRILL = "drill"
GOAL_TYPE_HABIT = "habit"
GOAL_TYPE_PHYSICAL = "physical"
GOAL_TYPE_OPTIONS: list[dict[str, str]] = [
    {"value": GOAL_TYPE_STAT, "label": "Stat"},
    {"value": GOAL_TYPE_DRILL, "label": "Drill"},
    {"value": GOAL_TYPE_HABIT, "label": "Habit"},
    {"value": GOAL_TYPE_PHYSICAL, "label": "Physical"},
]
# Stat goal scope (window_scope) and window (window_kind).
SCOPE_OPTIONS: list[dict[str, str]] = [
    {"value": "game", "label": "Games"},
    {"value": "practice", "label": "Practices"},
    {"value": "both", "label": "Both"},
]
WINDOW_LAST_N = "last_n"
WINDOW_SEASON = "season"
WINDOW_DATE_RANGE = "date_range"
WINDOW_OPTIONS: list[dict[str, str]] = [
    {"value": WINDOW_LAST_N, "label": "Last N"},
    {"value": WINDOW_SEASON, "label": "Season"},
    {"value": WINDOW_DATE_RANGE, "label": "Dates"},
]
# Drill goal basis (drill_basis).
BASIS_OPTIONS: list[dict[str, str]] = [
    {"value": "best", "label": "Best result"},
    {"value": "latest", "label": "Latest result"},
]
# Statuses (h_data.goals.status) and the Status choice of the goal page (D8).
STATUS_ACTIVE = "active"
STATUS_ACHIEVED = "achieved"
STATUS_MISSED = "missed"
STATUS_ARCHIVED = "archived"
STATUS_AUTO = "auto"
STATUS_LABELS: dict[str, str] = {
    STATUS_ACTIVE: "Active",
    STATUS_ACHIEVED: "Achieved",
    STATUS_MISSED: "Missed",
    STATUS_ARCHIVED: "Archived",
}
STATUS_CHOICES: list[dict[str, str]] = [
    {"value": STATUS_AUTO, "label": "Automatic"},
    *({"value": value, "label": label} for value, label in STATUS_LABELS.items()),
]
FILTER_ALL = "all"
FILTER_OPTIONS: list[dict[str, str]] = [
    *({"value": value, "label": label} for value, label in STATUS_LABELS.items()),
    {"value": FILTER_ALL, "label": "All"},
]
STATUS_COLORS: dict[str, str] = {
    STATUS_ACTIVE: "blue",
    STATUS_ACHIEVED: "green",
    STATUS_MISSED: "red",
    STATUS_ARCHIVED: "gray",
}

# The form's sections, in the order goal_section_styles and the sections callback use;
# each element id is "{prefix}-{suffix}".
GOAL_SECTION_SUFFIXES: tuple[str, ...] = (
    "sec-stat",
    "sec-drill",
    "sec-habit",
    "sec-physical",
    "sec-last-n",
    "sec-season",
    "sec-range",
)
# The window sub-section each window kind shows.
WINDOW_SECTIONS: dict[str, str] = {
    WINDOW_LAST_N: "sec-last-n",
    WINDOW_SEASON: "sec-season",
    WINDOW_DATE_RANGE: "sec-range",
}

# Value kinds of format_goal_value.
KIND_PERCENT = "percent"
KIND_RATIO = "ratio"
KIND_COUNT = "count"
KIND_MAKES = "makes"
KIND_DAYS = "days"
KIND_UNIT = "unit"
UNIT_SECONDS = "s"
UNIT_COUNT = "count"
# Drill metrics (h_data.goals.drill_metric).
METRIC_MAKES = "makes"
METRIC_MAKE_PCT = "make_pct"
METRIC_CLEAN_PCT = "clean_pct"
METRIC_VALUE_1 = "value_1"
METRIC_VALUE_2 = "value_2"

DEFAULT_SCOPE = "both"
DEFAULT_WINDOW_N = 10
DEFAULT_BASIS = "best"
PERCENT_SCALE = 100

HABIT_SECTION_TEXT = (
    "Counts days with a game or practice, Monday to Sunday. Add a deadline for an "
    "automatic result."
)
NO_GOALS_TEXT = "No goals here yet."
NO_ACTIVE_GOALS_TEXT = "No active goals."
RETIRED_DRILL_TEXT = "This drill is retired; the goal keeps counting its results."
MANUAL_SUFFIX = " (set by hand)"


# --- Pure helpers (REQ-032, REQ-033) ---

def goal_section_styles(goal_type: object, window_kind: object) -> dict[str, dict[str, str]]:
    """
    Description:
        Which parts of the goal form are shown: the section of the goal's type and, for a
        stat goal, the sub-section of its window.

    Parameters:
        goal_type: the type picker's value ("stat", "drill", "habit", "physical").
        window_kind: the window picker's value ("last_n", "season", "date_range").

    Return Values:
        dict[str, dict[str, str]]: suffix (GOAL_SECTION_SUFFIXES) -> style; {} means shown,
        {"display": "none"} hidden.

    Notes:
        Pure. Used for the initial styles and by the sections callback.
    """
    shown = {f"sec-{goal_type}"}
    if goal_type == GOAL_TYPE_STAT and window_kind in WINDOW_SECTIONS:
        shown.add(WINDOW_SECTIONS[str(window_kind)])
    return {
        suffix: dict(SHOWN_STYLE) if suffix in shown else dict(HIDDEN_STYLE)
        for suffix in GOAL_SECTION_SUFFIXES
    }


def _decimal(value: object) -> Decimal | None:
    """
    Description:
        A number as a Decimal.

    Parameters:
        value: an int, float, Decimal or numeric text.

    Return Values:
        Decimal | None: the value, or None when missing or not a finite number.

    Notes:
        Pure. A bool is not a number.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _fixed(number: Decimal, decimals: int) -> str:
    """
    Description:
        A number rounded half-up to a fixed count of decimals.

    Parameters:
        number: the value.
        decimals: how many decimals to show.

    Return Values:
        str: e.g. "72.5" for 72.46 with 1 decimal.

    Notes:
        Pure.
    """
    return format(number.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP), "f")


def _trimmed(number: Decimal, decimals: int) -> str:
    """
    Description:
        A number rounded half-up to at most a count of decimals, without trailing zeros.

    Parameters:
        number: the value.
        decimals: the most decimals to show.

    Return Values:
        str: e.g. "61.5" for 61.50, "12" for 12.00.

    Notes:
        Pure.
    """
    text = _fixed(number, decimals)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def format_goal_value(value: object, kind: str, unit: str | None) -> str:
    """
    Description:
        Formats a goal's progress or target value for the screen.

    Parameters:
        value: the number (Decimal, int or float), or None.
        kind: KIND_PERCENT ("72.5%"), KIND_RATIO (2 decimals), KIND_COUNT (1 decimal),
            KIND_MAKES or KIND_DAYS (whole numbers), or KIND_UNIT (a measured value with its
            unit).
        unit: for KIND_UNIT: "s" gives "4.25 s"; "in", "lb" and the other drill units are
            shown after the number ("61.5 in"); "count" or None shows the number only.

    Return Values:
        str: the text; "–" when value is None or not a number.

    Notes:
        Pure. Percent values are 0 to 100, as the database returns them.
    """
    number = _decimal(value)
    if number is None:
        return NO_VALUE_TEXT
    if kind == KIND_PERCENT:
        return f"{_fixed(number, 1)}%"
    if kind == KIND_RATIO:
        return _fixed(number, 2)
    if kind == KIND_COUNT:
        return _fixed(number, 1)
    if kind in (KIND_MAKES, KIND_DAYS):
        return _fixed(number, 0)
    if unit == UNIT_SECONDS:
        return f"{_fixed(number, 2)} s"
    text = _trimmed(number, 2)
    return f"{text} {unit}" if unit and unit != UNIT_COUNT else text


def goal_value_kind(goal: dict) -> tuple[str, str | None]:
    """
    Description:
        How a goal's values are formatted: its value kind and unit.

    Parameters:
        goal: a data_goals.list_goals_with_progress or get_goal row.

    Return Values:
        tuple[str, str | None]: (kind, unit) for format_goal_value. Stat goals use the
        stat's unit; drill goals makes, percent, or the measured number's unit; habit goals
        days; physical goals the test's unit.

    Notes:
        Pure.
    """
    goal_type = goal.get("goal_type")
    if goal_type == GOAL_TYPE_STAT:
        return str(goal.get("stat_unit") or KIND_COUNT), None
    if goal_type == GOAL_TYPE_HABIT:
        return KIND_DAYS, None
    if goal_type == GOAL_TYPE_PHYSICAL:
        return KIND_UNIT, _text_or_none(goal.get("test_unit"))
    metric = goal.get("drill_metric")
    if metric == METRIC_MAKES:
        return KIND_MAKES, None
    if metric in (METRIC_MAKE_PCT, METRIC_CLEAN_PCT):
        return KIND_PERCENT, None
    own_unit = goal.get("value_2_unit") if metric == METRIC_VALUE_2 else goal.get("value_1_unit")
    return KIND_UNIT, _text_or_none(own_unit or goal.get("drill_value_unit"))


def _text_or_none(value: object) -> str | None:
    """
    Description:
        A value as text, or None when it is missing or empty.

    Parameters:
        value: the value.

    Return Values:
        str | None: the text, or None.

    Notes:
        Pure.
    """
    return str(value) if value not in (None, "") else None


def goal_progress_text(goal: dict) -> str:
    """
    Description:
        A goal's progress line: "{value} / {target}", or for a habit goal "{n} of {target}
        days this week".

    Parameters:
        goal: a data_goals.list_goals_with_progress or get_goal row.

    Return Values:
        str: the text, e.g. "41.2% / 45.0%" or "2 of 3 days this week".

    Notes:
        Pure. The values come from fn_goal_progress; nothing is recomputed (D3).
    """
    if goal.get("goal_type") == GOAL_TYPE_HABIT:
        days = _decimal(goal.get("progress_value")) or Decimal(0)
        target = _decimal(goal.get("target_value")) or Decimal(0)
        return f"{_fixed(days, 0)} of {_fixed(target, 0)} days this week"
    kind, unit = goal_value_kind(goal)
    value_text = format_goal_value(goal.get("progress_value"), kind, unit)
    target_text = format_goal_value(goal.get("target_value"), kind, unit)
    return f"{value_text} / {target_text}"


def format_goal_date(value: object) -> str:
    """
    Description:
        A goal's calendar date as stored, e.g. "Thu 23 Oct 2026" (REQ-055).

    Parameters:
        value: the date.

    Return Values:
        str: the text, or the value's text when it is not a date.

    Notes:
        Pure.
    """
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return f"{value.strftime('%a')} {value.day} {value.strftime('%b %Y')}"
    return str(value)


def goal_status_text(goal: dict) -> str:
    """
    Description:
        A goal's status in words: "Active", "Achieved on {date}", "Missed" or "Archived",
        followed by " (set by hand)" when the status is manual.

    Parameters:
        goal: a goal row with "status", "status_is_manual" and "achieved_on".

    Return Values:
        str: the text.

    Notes:
        Pure.
    """
    status = str(goal.get("status") or STATUS_ACTIVE)
    text = STATUS_LABELS.get(status, status)
    if status == STATUS_ACHIEVED and goal.get("achieved_on") is not None:
        text = f"Achieved on {format_goal_date(goal.get('achieved_on'))}"
    if goal.get("status_is_manual"):
        text += MANUAL_SUFFIX
    return text


def measure_label(goal: dict) -> str:
    """
    Description:
        What a drill goal measures, in words: "Makes", "Make %", "Clean-rep % · {group}"
        or the measured number's label.

    Parameters:
        goal: a goal row with the drill columns of data_goals.list_goals_with_progress.

    Return Values:
        str: the text.

    Notes:
        Pure. The labels come from the drill's latest live version.
    """
    metric = goal.get("drill_metric")
    if metric == METRIC_MAKES:
        return "Makes"
    if metric == METRIC_MAKE_PCT:
        return "Make %"
    if metric == METRIC_CLEAN_PCT:
        group = goal.get("group_name") or f"group {goal.get('drill_group_no')}"
        return f"Clean-rep %{DETAIL_SEPARATOR}{group}"
    label = goal.get("value_2_label") if metric == METRIC_VALUE_2 else goal.get("value_1_label")
    return str(label) if label else "Result"


def goal_subject_text(goal: dict) -> str:
    """
    Description:
        What a goal is about, in one line: the stat and its window, the drill and its
        measure, the habit, or the physical test.

    Parameters:
        goal: a data_goals.list_goals_with_progress or get_goal row.

    Return Values:
        str: e.g. "FG% · Last 10 · Both", "Form shooting · Make %", "Days with a game or
        practice", "Vertical jump".

    Notes:
        Pure.
    """
    goal_type = goal.get("goal_type")
    if goal_type == GOAL_TYPE_STAT:
        window_kind = goal.get("window_kind")
        if window_kind == WINDOW_LAST_N:
            window = f"Last {goal.get('window_n')}"
        elif window_kind == WINDOW_SEASON:
            window = str(goal.get("season_name") or "Season")
        else:
            window = (
                f"{format_goal_date(goal.get('window_from'))} to "
                f"{format_goal_date(goal.get('window_to'))}"
            )
        scope = next(
            (o["label"] for o in SCOPE_OPTIONS if o["value"] == goal.get("window_scope")), ""
        )
        return DETAIL_SEPARATOR.join(
            [str(goal.get("stat_abbreviation") or goal.get("stat_code")), window, scope]
        )
    if goal_type == GOAL_TYPE_DRILL:
        return f"{goal.get('drill_name')}{DETAIL_SEPARATOR}{measure_label(goal)}"
    if goal_type == GOAL_TYPE_HABIT:
        return "Days with a game or practice"
    return str(goal.get("test_name") or "Physical test")


def stat_options(stats: list[dict]) -> list[dict[str, str]]:
    """
    Description:
        The stat picker's options: "{label} ({abbreviation})".

    Parameters:
        stats: data_goals.list_goal_stats rows.

    Return Values:
        list[dict[str, str]]: {"value": stat_code, "label": ...} per stat.

    Notes:
        Pure.
    """
    return [
        {"value": str(stat["stat_code"]), "label": f"{stat['label']} ({stat['abbreviation']})"}
        for stat in stats
    ]


def drill_options(drills: list[dict]) -> list[dict[str, str]]:
    """
    Description:
        The drill picker's options; the value is the lineage id as a string.

    Parameters:
        drills: data_goals.list_goal_drills rows (plus, on a goal page, the goal's own
            retired drill).

    Return Values:
        list[dict[str, str]]: {"value": str(drill_id), "label": drill_name} per drill.

    Notes:
        Pure.
    """
    return [
        {"value": str(int(drill["drill_id"])), "label": str(drill["drill_name"])}
        for drill in drills
    ]


def season_options(seasons: list[dict]) -> list[dict[str, str]]:
    """
    Description:
        The season picker's options.

    Parameters:
        seasons: data_goals.list_goal_seasons rows of the goal's player.

    Return Values:
        list[dict[str, str]]: {"value": str(season_id), "label": season_name} per season.

    Notes:
        Pure.
    """
    return [
        {"value": str(int(season["season_id"])), "label": str(season["season_name"])}
        for season in seasons
    ]


def _number_value(value: object) -> object:
    """
    Description:
        A stored number as a NumberInput value.

    Parameters:
        value: an int, a Decimal or None.

    Return Values:
        object: a float or int, or "" when missing.

    Notes:
        Pure. A Decimal becomes a float so the value is JSON-serialisable.
    """
    number = _decimal(value)
    if number is None:
        return ""
    return int(number) if number == number.to_integral_value() else float(number)


def _iso(value: object) -> str | None:
    """
    Description:
        A date as "YYYY-MM-DD" for a date picker.

    Parameters:
        value: a date or None.

    Return Values:
        str | None: the text, or None.

    Notes:
        Pure.
    """
    return value.isoformat() if isinstance(value, date) else None


def goal_form_values(goal: dict) -> dict[str, object]:
    """
    Description:
        The goal form's initial values from a stored goal.

    Parameters:
        goal: a data_goals.get_goal row.

    Return Values:
        dict[str, object]: the values goal_form_fields reads (type, name, target, start,
        deadline, notes, stat, scope, window, window_n, season, from, to, drill, measure,
        basis, test).

    Notes:
        Pure. A clean_pct measure is "clean_pct:<group>".
    """
    metric = goal.get("drill_metric")
    measure = (
        f"{METRIC_CLEAN_PCT}:{goal.get('drill_group_no')}"
        if metric == METRIC_CLEAN_PCT
        else metric
    )

    def id_text(key: str) -> str | None:
        """
        Description:
            An id column as a picker value.

        Parameters:
            key: the column name.

        Return Values:
            str | None: the id as a string, or None.

        Notes:
            Pure.
        """
        raw = goal.get(key)
        return str(int(raw)) if isinstance(raw, int) and not isinstance(raw, bool) else None

    return {
        "type": goal.get("goal_type"),
        "name": goal.get("goal_name") or "",
        "target": _number_value(goal.get("target_value")),
        "start": _iso(goal.get("start_date")),
        "deadline": _iso(goal.get("deadline")),
        "notes": goal.get("notes") or "",
        "stat": goal.get("stat_code"),
        "scope": goal.get("window_scope") or DEFAULT_SCOPE,
        "window": goal.get("window_kind") or WINDOW_LAST_N,
        "window_n": _number_value(goal.get("window_n")) if goal.get("window_n") else DEFAULT_WINDOW_N,
        "season": id_text("window_season_id"),
        "from": _iso(goal.get("window_from")),
        "to": _iso(goal.get("window_to")),
        "drill": id_text("drill_id"),
        "measure": measure,
        "basis": goal.get("drill_basis") or DEFAULT_BASIS,
        "test": id_text("physical_test_id"),
    }


def new_goal_values(today: str | None) -> dict[str, object]:
    """
    Description:
        The New goal form's initial values: a stat goal starting on the device's today.

    Parameters:
        today: the device's date "YYYY-MM-DD", or None when unknown.

    Return Values:
        dict[str, object]: the values goal_form_fields reads.

    Notes:
        Pure.
    """
    return {
        "type": GOAL_TYPE_STAT,
        "name": "",
        "target": "",
        "start": today,
        "deadline": None,
        "notes": "",
        "stat": None,
        "scope": DEFAULT_SCOPE,
        "window": WINDOW_LAST_N,
        "window_n": DEFAULT_WINDOW_N,
        "season": None,
        "from": None,
        "to": None,
        "drill": None,
        "measure": None,
        "basis": DEFAULT_BASIS,
        "test": None,
    }


# --- Components ---

def goal_progress_block(goal: dict, scope: str) -> Component:
    """
    Description:
        One goal's progress: its name and status badge, what it is about, a progress bar,
        the progress text and the database's detail line.

    Parameters:
        goal: a data_goals.list_goals_with_progress or get_goal row.
        scope: "goals" (list), "home-goal" (Home) or "goal" (the goal page).

    Return Values:
        Component: a stack with ids {"type": "{scope}-progress", "index": goal_id} (the
        dmc.Progress, value = progress_ratio x 100), {"type": "{scope}-status", ...} (the
        badge) and {"type": "{scope}-progress-text", ...}.

    Notes:
        Pure. The ratio is fn_goal_progress's (0..1, capped).
    """
    goal_id = int(goal["goal_id"])
    status = str(goal.get("status") or STATUS_ACTIVE)
    ratio = _decimal(goal.get("progress_ratio")) or Decimal(0)
    children: list[Component] = [
        dmc.Group(
            [
                dmc.Text(str(goal.get("goal_name")), fw=600, style={"overflowWrap": "anywhere"}),
                dmc.Badge(
                    STATUS_LABELS.get(status, status),
                    id={"type": f"{scope}-status", "index": goal_id},
                    color=STATUS_COLORS.get(status, "gray"),
                ),
            ],
            justify="space-between",
            wrap="nowrap",
        ),
        dmc.Text(goal_subject_text(goal), size="sm", c="dimmed"),
        dmc.Progress(
            id={"type": f"{scope}-progress", "index": goal_id},
            value=float(ratio * PERCENT_SCALE),
            size="lg",
            color=STATUS_COLORS.get(status, "blue"),
        ),
        dmc.Text(goal_progress_text(goal), id={"type": f"{scope}-progress-text", "index": goal_id}),
    ]
    if goal.get("detail"):
        children.append(dmc.Text(str(goal["detail"]), size="sm", c="dimmed"))
    return dmc.Stack(children, gap=4)


def goal_list_items(goals: list[dict], scope: str) -> list[Component]:
    """
    Description:
        A list of goals: one link per goal to its page, holding its progress block.

    Parameters:
        goals: data_goals.list_goals_with_progress rows.
        scope: "goals" on the Goals list, "home-goal" on Home.

    Return Values:
        list[Component]: dcc.Link items with id {"type": "{scope}-item", "index": goal_id}
        (class hoopster-recent-item hoopster-recent-link, 44 px and more), or one Text
        "No goals here yet." with id "{scope}-empty".

    Notes:
        Pure.
    """
    if not goals:
        return [dmc.Text(NO_GOALS_TEXT, id=f"{scope}-empty", c="dimmed")]
    return [
        dcc.Link(
            goal_progress_block(goal, scope),
            id={"type": f"{scope}-item", "index": int(goal["goal_id"])},
            href=f"{GOALS_PATH}/{int(goal['goal_id'])}",
            className=ITEM_CLASS,
        )
        for goal in goals
    ]


def goals_layout(
    players: list[dict], selected_id: int, goals: list[dict], filter_value: str
) -> Component:
    """
    Description:
        The Goals screen: the player picker, the status filter, Add goal and the goals.

    Parameters:
        players: data_players.list_live_players (the picker's options).
        selected_id: current_player_id(), the picker's value. Never a user id.
        goals: data_goals.list_goals_with_progress of that player for the filter.
        filter_value: the filter's value ("active", "achieved", "missed", "archived" or
            "all").

    Return Values:
        Component: the page with goals-player, goals-filter, goals-add and goals-list.

    Notes:
        show_goals rebuilds goals-list when the player or the filter changes.
    """
    return page_skeleton(
        "Goals",
        [
            dmc.Select(
                id="goals-player",
                label="Player",
                data=player_options(players),
                value=str(selected_id),
                allowDeselect=False,
                size=CONTROL_SIZE,
            ),
            dmc.SegmentedControl(
                id="goals-filter",
                data=FILTER_OPTIONS,
                value=filter_value,
                fullWidth=True,
                size=CONTROL_SIZE,
                className=SEGMENTED_CLASS,
            ),
            html.Div(
                nav_link("goals-add", "Add goal", GOAL_NEW_PATH),
                className="hoopster-link-button hoopster-link-primary",
            ),
            dmc.Stack(goal_list_items(goals, "goals"), id="goals-list", gap="xs"),
        ],
    )


def _date_input(element_id: str, label: str, value: object, *, clearable: bool) -> Component:
    """
    Description:
        A touch-sized date picker of the goal form.

    Parameters:
        element_id: the stable element id.
        label: the field label.
        value: "YYYY-MM-DD" or None.
        clearable: True adds the clear button (optional dates).

    Return Values:
        Component: the dmc.DatePickerInput.

    Notes:
        None.
    """
    return dmc.DatePickerInput(
        id=element_id,
        label=label,
        value=value,
        valueFormat=DATE_PICKER_FORMAT,
        clearable=clearable,
        size=CONTROL_SIZE,
    )


def _segmented(element_id: str, data: list[dict[str, str]], value: object) -> Component:
    """
    Description:
        A full-width, touch-sized segmented control (class hoopster-segmented).

    Parameters:
        element_id: the stable element id.
        data: the {"value", "label"} options.
        value: the selected value.

    Return Values:
        Component: the dmc.SegmentedControl.

    Notes:
        None.
    """
    return dmc.SegmentedControl(
        id=element_id,
        data=data,
        value=value,
        fullWidth=True,
        size=CONTROL_SIZE,
        className=SEGMENTED_CLASS,
    )


def _stat_section(prefix: str, options: dict, values: dict, styles: dict) -> Component:
    """
    Description:
        The stat goal section: stat, scope, window and the window's own fields.

    Parameters:
        prefix: "goal-new" or "goal-edit".
        options: the picker options (see goal_form_fields).
        values: the initial values.
        styles: goal_section_styles of the initial type and window.

    Return Values:
        Component: the div "{prefix}-sec-stat".

    Notes:
        None.
    """
    return html.Div(
        dmc.Stack(
            [
                dmc.Select(
                    id=f"{prefix}-stat",
                    label="Stat",
                    data=options.get("stats", []),
                    value=values.get("stat"),
                    searchable=True,
                    size=CONTROL_SIZE,
                ),
                dmc.Text("Counts", size="sm"),
                _segmented(f"{prefix}-scope", SCOPE_OPTIONS, values.get("scope")),
                dmc.Text("Window", size="sm"),
                _segmented(f"{prefix}-window", WINDOW_OPTIONS, values.get("window")),
                html.Div(
                    dmc.NumberInput(
                        id=f"{prefix}-window-n",
                        label="Number of games or practices",
                        value=values.get("window_n"),
                        min=1,
                        max=GOAL_WINDOW_N_MAX,
                        allowDecimal=False,
                        allowNegative=False,
                        clampBehavior="none",
                        hideControls=True,
                        inputProps={"inputMode": "numeric"},
                        size=CONTROL_SIZE,
                    ),
                    id=f"{prefix}-sec-last-n",
                    style=styles["sec-last-n"],
                ),
                html.Div(
                    dmc.Select(
                        id=f"{prefix}-season",
                        label="Season",
                        data=options.get("seasons", []),
                        value=values.get("season"),
                        size=CONTROL_SIZE,
                    ),
                    id=f"{prefix}-sec-season",
                    style=styles["sec-season"],
                ),
                html.Div(
                    dmc.Stack(
                        [
                            _date_input(f"{prefix}-from", "From", values.get("from"), clearable=False),
                            _date_input(f"{prefix}-to", "To", values.get("to"), clearable=False),
                        ]
                    ),
                    id=f"{prefix}-sec-range",
                    style=styles["sec-range"],
                ),
            ]
        ),
        id=f"{prefix}-sec-stat",
        style=styles["sec-stat"],
    )


def _drill_section(prefix: str, options: dict, values: dict, styles: dict) -> Component:
    """
    Description:
        The drill goal section: drill, measure and basis.

    Parameters:
        prefix: "goal-new" or "goal-edit".
        options: the picker options (see goal_form_fields).
        values: the initial values.
        styles: goal_section_styles of the initial type and window.

    Return Values:
        Component: the div "{prefix}-sec-drill".

    Notes:
        goal_drill_changed refills the measure options when the drill changes.
    """
    return html.Div(
        dmc.Stack(
            [
                dmc.Select(
                    id=f"{prefix}-drill",
                    label="Drill",
                    data=options.get("drills", []),
                    value=values.get("drill"),
                    searchable=True,
                    size=CONTROL_SIZE,
                ),
                dmc.Select(
                    id=f"{prefix}-measure",
                    label="Measure",
                    data=options.get("measures", []),
                    value=values.get("measure"),
                    size=CONTROL_SIZE,
                ),
                dmc.Text("Counts", size="sm"),
                _segmented(f"{prefix}-basis", BASIS_OPTIONS, values.get("basis")),
            ]
        ),
        id=f"{prefix}-sec-drill",
        style=styles["sec-drill"],
    )


def goal_form_fields(
    prefix: str, options: dict, values: dict, *, type_editable: bool
) -> list[Component]:
    """
    Description:
        The shared goal form (New goal and the goal page): type, name, target with its
        hint, start, deadline, the four type sections, and notes, in the order
        parse_goal_form checks them.

    Parameters:
        prefix: "goal-new" or "goal-edit"; every id is "{prefix}-<field>".
        options: picker options: {"stats", "drills", "measures", "seasons", "tests"}, each a
            list of {"value", "label"} (stat_options, drill_options, the controller's
            measure_options, season_options, layouts_measurements.test_options).
        values: the initial values (new_goal_values or goal_form_values).
        type_editable: False disables the type picker (a goal's type is fixed, D7).

    Return Values:
        list[Component]: the fields, top to bottom.

    Notes:
        The initial section styles come from goal_section_styles; the sections callback
        changes them with the type and window.
    """
    styles = goal_section_styles(values.get("type"), values.get("window"))
    return [
        dmc.Select(
            id=f"{prefix}-type",
            label="Goal type",
            data=GOAL_TYPE_OPTIONS,
            value=values.get("type"),
            allowDeselect=False,
            disabled=not type_editable,
            size=CONTROL_SIZE,
        ),
        dmc.TextInput(
            id=f"{prefix}-name",
            label="Name",
            value=str(values.get("name") or ""),
            inputProps={"maxLength": GOAL_NAME_MAX_LENGTH},
            size=CONTROL_SIZE,
        ),
        dmc.NumberInput(
            id=f"{prefix}-target",
            label="Target",
            value=values.get("target"),
            min=0,
            max=GOAL_TARGET_MAX,
            decimalScale=GOAL_TARGET_DECIMALS,
            allowNegative=False,
            clampBehavior="none",
            hideControls=True,
            inputProps={"inputMode": "decimal"},
            size=CONTROL_SIZE,
        ),
        dmc.Text("", id=f"{prefix}-target-hint", size="sm", c="dimmed"),
        _date_input(f"{prefix}-start", "Start date", values.get("start"), clearable=False),
        _date_input(f"{prefix}-deadline", "Deadline (optional)", values.get("deadline"), clearable=True),
        _stat_section(prefix, options, values, styles),
        _drill_section(prefix, options, values, styles),
        html.Div(
            dmc.Text(HABIT_SECTION_TEXT, size="sm"),
            id=f"{prefix}-sec-habit",
            style=styles["sec-habit"],
        ),
        html.Div(
            dmc.Select(
                id=f"{prefix}-test",
                label="Physical test",
                data=options.get("tests", []),
                value=values.get("test"),
                size=CONTROL_SIZE,
            ),
            id=f"{prefix}-sec-physical",
            style=styles["sec-physical"],
        ),
        dmc.Textarea(
            id=f"{prefix}-notes",
            label="Notes (optional)",
            value=str(values.get("notes") or ""),
            inputProps={"maxLength": GOAL_NOTES_MAX_LENGTH},
            autosize=True,
            minRows=2,
            size=CONTROL_SIZE,
        ),
    ]


def goal_new_layout(player: dict, options: dict, today: str | None) -> Component:
    """
    Description:
        The New goal page for the selected player (D6).

    Parameters:
        player: data_players.get_live_player row of the selected player.
        options: the picker options (see goal_form_fields).
        today: the device's date "YYYY-MM-DD" (the default start date), or None.

    Return Values:
        Component: the page: goal-new-back, goal-new-for "For: {name}", the store
        goal-new-player-id, the form, goal-new-submit "Save goal" and goal-new-feedback.

    Notes:
        goal-new-player-id is written here only after the builder's live check (Context
        Adaptation Table).
    """
    return page_skeleton(
        "New goal",
        [
            html.Div(
                nav_link("goal-new-back", "‹ Goals", GOALS_PATH),
                className="hoopster-link-button",
            ),
            dmc.Text(f"For: {player.get('display_name')}", id="goal-new-for", fw=600),
            dcc.Store(
                id="goal-new-player-id", storage_type="memory", data=int(player["player_id"])
            ),
            *goal_form_fields("goal-new", options, new_goal_values(today), type_editable=True),
            primary_button("goal-new-submit", "Save goal"),
            form_message("goal-new-feedback"),
        ],
    )


def goal_page_layout(
    goal: dict, options: dict, measures: dict | None, zone: str | None
) -> Component:
    """
    Description:
        The goal page: its progress and status on top, the form (type fixed), the Status
        choice, Save, Delete with its confirmation, and the audit lines.

    Parameters:
        goal: data_goals.get_goal row, already confirmed live.
        options: the picker options (see goal_form_fields).
        measures: data_goals.get_drill_goal_measures of the goal's drill, or None.
        zone: the device's IANA zone (audit times, REQ-055).

    Return Values:
        Component: the page with goal-edit-back, the store goal-edit-id, goal-progress,
        goal-edit-status-text, the form, goal-edit-status, goal-edit-save, goal-edit-saved,
        goal-edit-feedback, goal-delete-open and the goal-delete dialog.

    Notes:
        goal-edit-id is written here only after the builder's get_goal check. The Status
        choice shows Automatic unless the status is manual (D8).
    """
    status_choice = str(goal.get("status")) if goal.get("status_is_manual") else STATUS_AUTO
    retired_note: list[Component] = []
    if goal.get("goal_type") == GOAL_TYPE_DRILL and measures and measures.get("retired_at"):
        retired_note = [dmc.Text(RETIRED_DRILL_TEXT, id="goal-edit-retired", size="sm", c="dimmed")]
    return page_skeleton(
        "Goal",
        [
            html.Div(
                nav_link("goal-edit-back", "‹ Goals", GOALS_PATH),
                className="hoopster-link-button",
            ),
            dcc.Store(id="goal-edit-id", storage_type="memory", data=int(goal["goal_id"])),
            dmc.Text(f"Player: {goal.get('player_name')}", id="goal-edit-player", size="sm"),
            html.Div(goal_progress_block(goal, "goal"), id="goal-progress"),
            dmc.Text(goal_status_text(goal), id="goal-edit-status-text"),
            *retired_note,
            *goal_form_fields("goal-edit", options, goal_form_values(goal), type_editable=False),
            dmc.Select(
                id="goal-edit-status",
                label="Status",
                data=STATUS_CHOICES,
                value=status_choice,
                allowDeselect=False,
                size=CONTROL_SIZE,
            ),
            primary_button("goal-edit-save", "Save"),
            dmc.Text("", id="goal-edit-saved", c="green"),
            form_message("goal-edit-feedback"),
            secondary_button("goal-delete-open", "Delete goal", color="red"),
            confirm_modal("goal-delete", "Delete goal?", "Delete"),
            audit_fields_block(
                "goal-edit-audit",
                goal.get("created_by_name"),
                format_entry_datetime(goal.get("created_at"), zone),
                goal.get("updated_by_name"),
                format_entry_datetime(goal.get("updated_at"), zone),
            ),
        ],
    )


# --- Home goals section (REQ-041, D15) ---

def home_goals_section(goals: list[dict]) -> list[Component]:
    """
    Description:
        The children of Home's goals panel: the title, the active goals with progress bars
        (or "No active goals.") and the "All goals" link.

    Parameters:
        goals: data_goals.list_goals_with_progress rows (active, at most HOME_GOALS_LIMIT).

    Return Values:
        list[Component]: [title, goals stack (home-goal-item links or home-goals-empty),
        home-goals-link].

    Notes:
        Pure. Also the second output of the Home player picker.
    """
    if goals:
        items = goal_list_items(goals, "home-goal")
    else:
        items = [dmc.Text(NO_ACTIVE_GOALS_TEXT, id="home-goals-empty", c="dimmed")]
    return [
        dmc.Title("Goals", order=3),
        dmc.Stack(items, gap="xs"),
        html.Div(
            nav_link("home-goals-link", "All goals", GOALS_PATH),
            className="hoopster-link-button",
        ),
    ]
