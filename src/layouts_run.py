"""Run-mode UI for Hoopster (practice_drills 2b): the run page frame with its step header and
navigation, the values panel with its stopwatch or countdown, the outcome-button panel, the
personal-best and summary texts, and the shooting plan of drill-mode entry.

REQ-030, REQ-056, REQ-060, REQ-076 to REQ-079, REQ-082. Shooting drills use the shared entry
panel of src/layouts_entry.py in drill mode (plan §3.7). Built only through the wrappers of
src/layouts.py (COD-5-01); imports no data_* module.
"""

import logging
import math
from datetime import date
from typing import cast

import dash_mantine_components as dmc
from dash import dcc, html
from dash.development.base_component import Component

from config.app_config import RUN_TIMER_INTERVAL_MS
from src.layouts import (
    CONTROL_SIZE,
    confirm_modal,
    form_message,
    nav_link,
    page_skeleton,
    primary_button,
    save_status_badge,
    secondary_button,
)
from src.layouts_practice import court_player_picker

logger = logging.getLogger(__name__)

DETAIL_SEPARATOR = " · "
SWAPPABLE_SOURCES: frozenset[str] = frozenset({"drawn", "swapped"})
HIDDEN_CLASS = "hoopster-hidden"
SEGMENTED_CLASS = "hoopster-segmented"
PRE_LINE_CLASS = "hoopster-pre-line"
TIMER_CLASS = "hoopster-timer"
OUTCOME_GRID_CLASS = "hoopster-outcome-grid"
OUTCOME_GRID_COLUMNS = 2
NAV_GRID_COLUMNS = 2
NO_PERSONAL_BEST = "No personal best yet"
NO_ATTEMPTS = "No attempts yet"
DEFAULT_SIDE = "left"

# Result types (h_ref.result_types) that drive the values panel (plan §3.6, D17).
RESULT_FIXED_TASK_TIME = "fixed_task_time"
RESULT_FIXED_TIME_REPS = "fixed_time_reps"
RESULT_FAILURE_TIME = "failure_time"
RESULT_PHYSICAL = "physical"
STOPWATCH_TYPES: frozenset[str] = frozenset({RESULT_FIXED_TASK_TIME, RESULT_FAILURE_TIME})
COUNTDOWN_TYPES: frozenset[str] = frozenset({RESULT_FIXED_TIME_REPS})
TIMER_MODE_STOPWATCH = "stopwatch"
TIMER_MODE_COUNTDOWN = "countdown"
UNIT_SECONDS = "s"
UNIT_COUNT = "count"
# Decimals per value kind (plan §3.6): counts whole, seconds 2, physical 3.
DECIMALS_SECONDS = 2
DECIMALS_PHYSICAL = 3
CLEAN_METRIC_PREFIX = "clean_pct_"


# --- Number and date formatting ---

def format_number(value: object, decimals: int) -> str:
    """
    Description:
        Formats a stored number with at most `decimals` decimals and no trailing zeros, e.g.
        12.340 -> "12.34", 46.00 -> "46".

    Parameters:
        value: a number (int, float or Decimal) or None.
        decimals: the most decimals to show.

    Return Values:
        str: the text, or "" for None.

    Notes:
        Pure.
    """
    if value is None:
        return ""
    text = f"{float(cast(float, value)):.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def round_percent(value: object) -> int:
    """
    Description:
        Rounds a percentage half up, like layouts_entry.format_tally and Math.round in
        assets/hoopster.js.

    Parameters:
        value: a percentage 0-100 (numeric), never None.

    Return Values:
        int: the rounded percentage.

    Notes:
        Pure.
    """
    return math.floor(float(cast(float, value)) + 0.5)


def format_pb_date(value: object) -> str:
    """
    Description:
        A personal best's date as "12 Oct 2026" (calendar date as stored, REQ-055).

    Parameters:
        value: a date, or anything else.

    Return Values:
        str: the text; "" when value is not a date.

    Notes:
        Pure.
    """
    if not isinstance(value, date):
        return ""
    return f"{value.day} {value.strftime('%b %Y')}"


def _value_decimals(drill: dict) -> int:
    """
    Description:
        How many decimals the values of a drill take: physical 3, seconds 2, counts 0.

    Parameters:
        drill: a data_drill_results.get_run_drill row.

    Return Values:
        int: the decimals.

    Notes:
        Pure.
    """
    if drill.get("result_type_code") == RESULT_PHYSICAL:
        return DECIMALS_PHYSICAL
    return DECIMALS_SECONDS if drill.get("value_unit") == UNIT_SECONDS else 0


def _unit_suffix(unit: object) -> str:
    """
    Description:
        The text after a value: " s" for seconds, nothing for a count, " <unit>" otherwise.

    Parameters:
        unit: the value unit ("s", "count", "cm", ...), or None.

    Return Values:
        str: the suffix.

    Notes:
        Pure.
    """
    if not unit or unit == UNIT_COUNT:
        return ""
    return f" {unit}"


# --- Personal best, summary and history texts (REQ-060, REQ-078; D7, D16) ---

def _clean_group_name(metric_label: str) -> str:
    """
    Description:
        The outcome group name inside a clean-rep metric label "Clean reps % (Accuracy)".

    Parameters:
        metric_label: the metric_label of h_analytics.fn_drill_personal_bests.

    Return Values:
        str: "Accuracy", or the whole label when it has no parentheses.

    Notes:
        Pure.
    """
    start = metric_label.find("(")
    end = metric_label.rfind(")")
    if start < 0 or end <= start:
        return metric_label
    return metric_label[start + 1:end]


def _personal_best_line(row: dict) -> str:
    """
    Description:
        One personal-best line (plan §3.6 "PB text").

    Parameters:
        row: a data_drill_results.get_personal_bests row.

    Return Values:
        str: e.g. "PB 41/50 (82%) · 12 Oct 2026", "PB 46 s · ...", "PB 22 · ...",
        "PB Vertical jump 61.5 cm · ...", "PB clean reps Accuracy 90% (20) · ...".

    Notes:
        Pure.
    """
    metric = str(row.get("metric") or "")
    when = format_pb_date(row.get("achieved_on"))
    if metric == "makes":
        makes = int(row.get("best_makes") or 0)
        attempts = int(row.get("best_attempts") or 0)
        percent = math.floor(makes * 100 / attempts + 0.5) if attempts > 0 else 0
        text = f"PB {makes}/{attempts} ({percent}%)"
    elif metric.startswith(CLEAN_METRIC_PREFIX):
        group = _clean_group_name(str(row.get("metric_label") or ""))
        reps = int(row.get("best_attempts") or 0)
        text = f"PB clean reps {group} {round_percent(row.get('best_value') or 0)}% ({reps})"
    elif row.get("result_type_code") == RESULT_PHYSICAL:
        value = format_number(row.get("best_value"), DECIMALS_PHYSICAL)
        text = f"PB {row.get('metric_label')} {value}{_unit_suffix(row.get('unit'))}"
    else:
        decimals = DECIMALS_SECONDS if row.get("unit") == UNIT_SECONDS else 0
        value = format_number(row.get("best_value"), decimals)
        text = f"PB {value}{_unit_suffix(row.get('unit'))}"
    return f"{text}{DETAIL_SEPARATOR}{when}" if when else text


def format_personal_best(rows: list[dict]) -> str:
    """
    Description:
        The personal-best text of a drill for one player, one line per metric (REQ-060).

    Parameters:
        rows: data_drill_results.get_personal_bests rows of one player and drill.

    Return Values:
        str: the lines joined with newlines, or "No personal best yet".

    Notes:
        Pure. Percentages round half up, like format_tally.
    """
    if not rows:
        return NO_PERSONAL_BEST
    return "\n".join(_personal_best_line(row) for row in rows)


def format_shooting_history(attempts: list[dict]) -> str:
    """
    Description:
        The "Earlier attempts" line of a shooting drill: "Earlier attempts: 1) 41/50 ·
        2) 38/50".

    Parameters:
        attempts: data_drill_results.get_shooting_history rows of one player.

    Return Values:
        str: the line, or "" when the player has no attempts yet.

    Notes:
        Pure. Read on page open only (D16).
    """
    if not attempts:
        return ""
    parts = [
        f"{int(row['attempt_no'])}) {int(row.get('makes') or 0)}/{int(row.get('attempts') or 0)}"
        for row in attempts
    ]
    return "Earlier attempts: " + DETAIL_SEPARATOR.join(parts)


def _value_summary(drill: dict, summary: dict) -> str:
    """
    Description:
        The summary of a values drill (D7): best and average of each value and the count;
        fixed time for reps also its best rate per minute.

    Parameters:
        drill: a get_run_drill row.
        summary: a get_result_summary row.

    Return Values:
        str: the text.

    Notes:
        Pure. Best follows the type's or drill's direction (h_analytics.v_drill_results).
    """
    decimals = _value_decimals(drill)
    is_physical = drill.get("result_type_code") == RESULT_PHYSICAL
    unit_1 = drill.get("value_1_unit") if is_physical else drill.get("value_unit")
    label_1 = f"{drill.get('value_1_label')} " if is_physical else ""
    parts = [
        f"Best {label_1}{format_number(summary.get('best_value_1'), decimals)}"
        f"{_unit_suffix(unit_1)}",
        f"Average {format_number(summary.get('avg_value_1'), decimals)}{_unit_suffix(unit_1)}",
    ]
    if is_physical and drill.get("value_2_label") and summary.get("best_value_2") is not None:
        parts.append(
            f"Best {drill.get('value_2_label')} "
            f"{format_number(summary.get('best_value_2'), decimals)}"
            f"{_unit_suffix(drill.get('value_2_unit'))}"
        )
    count = int(summary.get("attempt_count") or 0)
    parts.append(f"{count} attempt{'' if count == 1 else 's'}")
    if drill.get("result_type_code") == RESULT_FIXED_TIME_REPS and summary.get(
        "best_rate_per_minute"
    ) is not None:
        parts.append(f"Best rate {format_number(summary.get('best_rate_per_minute'), 1)} per minute")
    return DETAIL_SEPARATOR.join(parts)


def _outcome_summary(groups: list[dict], summary: dict) -> str:
    """
    Description:
        The summary of an outcome-button drill (D7): reps and clean-rep % per group.

    Parameters:
        groups: data_drill_results.list_outcome_structure of the version.
        summary: a get_result_summary row.

    Return Values:
        str: e.g. "Accuracy: 12 reps, 75% clean · Form: 12 reps, 50% clean".

    Notes:
        Pure. Groups are matched by group number (reps_n, clean_pct_n).
    """
    parts = []
    for group in groups:
        number = int(group["group_no"])
        reps = int(summary.get(f"reps_{number}") or 0)
        clean = summary.get(f"clean_pct_{number}")
        clean_text = f"{round_percent(clean)}% clean" if clean is not None else "no reps"
        parts.append(f"{group['group_name']}: {reps} reps, {clean_text}")
    return DETAIL_SEPARATOR.join(parts)


def format_summary(drill: dict, summary: dict | None, groups: list[dict] | None = None) -> str:
    """
    Description:
        The drill-summary text of the player's result (D7).

    Parameters:
        drill: a get_run_drill row.
        summary: a get_result_summary row, or None when the player has no result.
        groups: the outcome structure (outcome drills only), else None.

    Return Values:
        str: "No attempts yet", the values summary or the outcome summary.

    Notes:
        Pure. Shooting summaries are written clientside (drill_progress).
    """
    if not summary or not int(summary.get("attempt_count") or 0):
        return NO_ATTEMPTS
    if drill.get("capture_kind") == "outcomes":
        return _outcome_summary(groups or [], summary)
    return _value_summary(drill, summary)


# --- Shooting plan (REQ-031, plan §3.7) ---

def drill_plan(spot_rows: list[dict]) -> dict[str, list[dict[str, object]]]:
    """
    Description:
        The court-plan store of a shooting drill: its spots in order with their targets.

    Parameters:
        spot_rows: data_drill_results.list_drill_spot_plan rows.

    Return Values:
        dict[str, list[dict[str, object]]]: {"spots": [{"key": "s<court_spot_id>" | "ft",
        "court_spot_id": int | None, "label": "#6 Left elbow" | "Free-throw line",
        "target": int | None}]}.

    Notes:
        Pure.
    """
    spots: list[dict[str, object]] = []
    for row in spot_rows:
        target = row.get("attempts_target")
        if row.get("is_free_throw"):
            spots.append(
                {"key": "ft", "court_spot_id": None, "label": "Free-throw line",
                 "target": int(target) if target is not None else None}
            )
            continue
        spot_id = int(row["court_spot_id"])
        spots.append(
            {
                "key": f"s{spot_id}",
                "court_spot_id": spot_id,
                "label": f"#{row.get('spot_number')} {row.get('spot_name')}",
                "target": int(target) if target is not None else None,
            }
        )
    return {"spots": spots}


def first_open_spot(plan: dict, base_spots: dict) -> int | None:
    """
    Description:
        The court spot to pre-select: the first plan court spot below its target, else the
        first plan court spot.

    Parameters:
        plan: the drill_plan value.
        base_spots: the default player's court-base "spots" ({"s<id>" | "ft": attempts}).

    Return Values:
        int | None: a court_spot_id, or None when the plan has no court spot.

    Notes:
        Pure. The free-throw line is never selected on the court.
    """
    court_spots = [spot for spot in plan.get("spots", []) if spot.get("court_spot_id")]
    for spot in court_spots:
        target = spot.get("target")
        done = int(base_spots.get(str(spot["key"]), 0))
        if target is None or done < int(target):
            return int(spot["court_spot_id"])
    return int(court_spots[0]["court_spot_id"]) if court_spots else None


# --- Run page frame (REQ-056, REQ-082) ---

def step_text(drill: dict, set_number: int | None, set_count: int, drill_number: int,
              drill_count: int) -> str:
    """
    Description:
        The run-step line: "Set 2 of 3 · Shooting Block (v2) · Drill 3 of 5", or "Single
        drill" for an ad-hoc drill.

    Parameters:
        drill: a get_run_drill row.
        set_number: the 1-based number of the drill's set among the placed sets with drills,
            or None for an ad-hoc drill.
        set_count: the number of placed sets with drills.
        drill_number: the drill's 1-based position in its set.
        drill_count: the number of drills in its set.

    Return Values:
        str: the text.

    Notes:
        Pure.
    """
    if set_number is None:
        return "Single drill"
    return DETAIL_SEPARATOR.join(
        [
            f"Set {set_number} of {set_count}",
            f"{drill.get('set_name')} (v{drill.get('set_version_no')})",
            f"Drill {drill_number} of {drill_count}",
        ]
    )


def _meta_text(drill: dict) -> str:
    """
    Description:
        The drill's meta line: "Difficulty · Type · Result type · target".

    Parameters:
        drill: a get_run_drill row.

    Return Values:
        str: the text; the target part names its meaning ("Target 50").

    Notes:
        Pure.
    """
    parts = [
        str(drill.get("difficulty_label")),
        str(drill.get("type_name")),
        str(drill.get("result_type_label")),
    ]
    if drill.get("default_target") is not None:
        parts.append(f"Target {drill.get('default_target')}")
    if drill.get("fixed_seconds") is not None:
        parts.append(f"{drill.get('fixed_seconds')} s")
    return DETAIL_SEPARATOR.join(parts)


def _nav_links(practice_session_id: int, navigation: dict) -> Component:
    """
    Description:
        The run page's navigation links, each only when it exists: run-prev, run-next,
        run-prev-set, run-next-set, and run-overview.

    Parameters:
        practice_session_id: the practice.
        navigation: controller_run.run_navigation: {"prev", "next", "prev_set", "next_set"}
            practice_drill_ids or None.

    Return Values:
        Component: a two-column grid of link buttons.

    Notes:
        Server-built nav_links (no callback).
    """
    base = f"/practice/{practice_session_id}/run/"
    links: list[Component] = []
    for key, element_id, label in (
        ("prev", "run-prev", "Previous drill"),
        ("next", "run-next", "Next drill"),
        ("prev_set", "run-prev-set", "Previous set"),
        ("next_set", "run-next-set", "Next set"),
    ):
        target = navigation.get(key)
        if target is not None:
            links.append(
                html.Div(nav_link(element_id, label, f"{base}{target}"),
                         className="hoopster-link-button")
            )
    links.append(
        html.Div(
            nav_link("run-overview", "Practice overview", f"/practice/{practice_session_id}"),
            className="hoopster-link-button",
        )
    )
    return dmc.SimpleGrid(links, cols=NAV_GRID_COLUMNS, spacing="xs")


def run_page_layout(context: dict, navigation: dict, panel: list[Component]) -> Component:
    """
    Description:
        The run page /practice/<id>/run[/<pd>] (plan §3.6): step line, drill name, meta
        line, description, the set's notice, navigation, Swap (drawn drills), then the
        result panel.

    Parameters:
        context: {"drill": get_run_drill row, "step": step_text}.
        navigation: controller_run.run_navigation.
        panel: the result panel's components (entry panel in drill mode, values panel or
            outcome panel), including its player picker and personal best.

    Return Values:
        Component: the page.

    Notes:
        drill-context {"practice_session_id", "practice_drill_id"} is written here after the
        builder's live check; every write re-checks it in SQL. run-swap and its dialog exist
        only for a drawn or swapped drill (REQ-053).
    """
    drill = context["drill"]
    practice_session_id = int(drill["practice_session_id"])
    practice_drill_id = int(drill["practice_drill_id"])
    header: list[Component] = [
        dmc.Text(str(context.get("step") or ""), id="run-step", size="sm", c="dimmed"),
        dmc.Title(str(drill["drill_name"]), order=3, id="run-drill-name"),
        dmc.Text(_meta_text(drill), id="run-drill-meta", size="sm"),
    ]
    if drill.get("description"):
        header.append(dmc.Text(str(drill["description"]), id="run-drill-description"))
    if drill.get("draw_notice"):
        header.append(dmc.Alert(str(drill["draw_notice"]), color="yellow", id="run-notice"))
    header.append(_nav_links(practice_session_id, navigation))
    if drill.get("source") in SWAPPABLE_SOURCES:
        header.append(secondary_button("run-swap", "Swap drill"))
        header.append(confirm_modal("run-swap", "Swap drill?", "Swap", color="orange"))
    return page_skeleton(
        "Run practice",
        [
            *header,
            *panel,
            dcc.Store(
                id="drill-context",
                storage_type="memory",
                data={
                    "practice_session_id": practice_session_id,
                    "practice_drill_id": practice_drill_id,
                },
            ),
        ],
    )


def no_drills_layout(practice: dict) -> Component:
    """
    Description:
        The run page of a practice without drills: "This practice has no drills yet." and a
        link to its overview.

    Parameters:
        practice: a data_practice.get_practice / get_practice_overview row.

    Return Values:
        Component: the page.

    Notes:
        None.
    """
    practice_session_id = int(practice["practice_session_id"])
    return page_skeleton(
        "Run practice",
        [
            dmc.Text("This practice has no drills yet.", id="run-no-drills"),
            html.Div(
                nav_link("run-overview", "Practice overview", f"/practice/{practice_session_id}"),
                className="hoopster-link-button hoopster-link-primary",
            ),
        ],
    )


# --- Values panel (REQ-030, REQ-056, REQ-076, REQ-078, REQ-079; D8, D9, D17) ---

def _side_control(drill: dict) -> Component:
    """
    Description:
        The drill-side control (Left/Right with the symmetry's labels, default left, D8),
        shown only when the drill records a side; always present so saves can read it.

    Parameters:
        drill: a get_run_drill row.

    Return Values:
        Component: drill-side-area holding drill-side.

    Notes:
        Values and outcome drills record a side whenever they have a symmetry type.
    """
    has_side = drill.get("symmetry_type_code") is not None
    return html.Div(
        dmc.Stack(
            [
                dmc.Text(str(drill.get("symmetry_label") or "Side"), size="sm"),
                dmc.SegmentedControl(
                    id="drill-side",
                    data=[
                        {"value": "left", "label": str(drill.get("left_label") or "Left")},
                        {"value": "right", "label": str(drill.get("right_label") or "Right")},
                    ],
                    value=DEFAULT_SIDE,
                    fullWidth=True,
                    size=CONTROL_SIZE,
                    className=SEGMENTED_CLASS,
                ),
            ],
            gap="xs",
        ),
        id="drill-side-area",
        className="" if has_side else HIDDEN_CLASS,
    )


def _value_label(drill: dict, number: int) -> str:
    """
    Description:
        The label of a value input: the result type's quantity with its unit, or the
        drill's own label and unit for a physical measurement.

    Parameters:
        drill: a get_run_drill row.
        number: 1 or 2.

    Return Values:
        str: e.g. "Seconds", "Reps in 60 s", "Count", "Vertical jump (cm)".

    Notes:
        Pure.
    """
    if drill.get("result_type_code") == RESULT_PHYSICAL:
        label = drill.get(f"value_{number}_label") or f"Value {number}"
        unit = drill.get(f"value_{number}_unit")
        return f"{label} ({unit})" if unit else str(label)
    if drill.get("result_type_code") == RESULT_FIXED_TIME_REPS:
        return f"Reps in {drill.get('fixed_seconds')} s"
    return "Seconds" if drill.get("value_unit") == UNIT_SECONDS else "Count"


def _value_input(element_id: str, label: str, decimals: int) -> dmc.NumberInput:
    """
    Description:
        A value input of the values panel: at least 0. Every input accepts a decimal point,
        counts included, so a typed "7.5" reaches the server's whole-number check
        (controller_run.parse_value) and shows "Enter a whole number." with nothing saved.

    Parameters:
        element_id: "drill-value-1" or "drill-value-2".
        label: the field label.
        decimals: 0 (counts), 2 or 3; limits the decimals typed for non-count inputs.

    Return Values:
        dmc.NumberInput: the input, empty.

    Notes:
        clampBehavior "none" keeps a typed value so the save shows the message. Counts leave
        decimalScale out entirely (not None: Dash sends None as null, which dmc treats as 0
        decimals and strips "7.5" to 7), so the browser never strips or rounds a typed
        decimal (plan amendment AP-3, 2026-10-26; DEF-057).
    """
    options: dict[str, object] = {}
    if decimals > 0:
        options["decimalScale"] = decimals
    return dmc.NumberInput(
        id=element_id,
        label=label,
        value="",
        min=0,
        allowNegative=False,
        allowDecimal=True,
        clampBehavior="none",
        size=CONTROL_SIZE,
        hideControls=True,
        inputProps={"inputMode": "decimal"},
        **options,
    )


def _timer_controls(drill: dict) -> list[Component]:
    """
    Description:
        The stopwatch (Fixed task for time, To failure: time) or countdown (Fixed time for
        reps) of a values drill (REQ-056, D17); none for other types.

    Parameters:
        drill: a get_run_drill row.

    Return Values:
        list[Component]: [timer block] with drill-timer-display, drill-timer-start, -stop,
        -reset, the drill-timer store and the drill-timer-tick interval; [] without a timer.

    Notes:
        The clientside timer_step is the single writer of drill-timer, the display, the
        interval's disabled and (stopwatch Stop) drill-value-1.
    """
    code = drill.get("result_type_code")
    if code in STOPWATCH_TYPES:
        mode, limit, display = TIMER_MODE_STOPWATCH, None, "0.00 s"
    elif code in COUNTDOWN_TYPES and drill.get("fixed_seconds"):
        limit = int(drill["fixed_seconds"])
        mode, display = TIMER_MODE_COUNTDOWN, f"{limit // 60}:{limit % 60:02d}"
    else:
        return []
    return [
        html.Div(
            dmc.Stack(
                [
                    dmc.Text(display, id="drill-timer-display", className=TIMER_CLASS),
                    dmc.SimpleGrid(
                        [
                            primary_button("drill-timer-start", "Start"),
                            secondary_button("drill-timer-stop", "Stop"),
                            secondary_button("drill-timer-reset", "Reset"),
                        ],
                        cols=3,
                        spacing="xs",
                    ),
                    dcc.Store(
                        id="drill-timer",
                        storage_type="memory",
                        data={
                            "mode": mode,
                            "running": False,
                            "started_at": None,
                            "elapsed_ms": 0,
                            "limit_s": limit,
                        },
                    ),
                    dcc.Interval(
                        id="drill-timer-tick", interval=RUN_TIMER_INTERVAL_MS, disabled=True
                    ),
                ],
                gap="xs",
            ),
            id="drill-timer-area",
        )
    ]


def format_attempt(drill: dict, attempt: dict) -> str:
    """
    Description:
        One value attempt as "#n · value(s) · side".

    Parameters:
        drill: a get_run_drill row.
        attempt: a data_drill_results.list_value_attempts row.

    Return Values:
        str: e.g. "#2 · 12.34 s · Left hand".

    Notes:
        Pure.
    """
    decimals = _value_decimals(drill)
    is_physical = drill.get("result_type_code") == RESULT_PHYSICAL
    unit_1 = drill.get("value_1_unit") if is_physical else drill.get("value_unit")
    parts = [
        f"#{attempt['attempt_no']}",
        f"{format_number(attempt.get('value_1'), decimals)}{_unit_suffix(unit_1)}",
    ]
    if attempt.get("value_2") is not None:
        parts.append(
            f"{format_number(attempt.get('value_2'), decimals)}"
            f"{_unit_suffix(drill.get('value_2_unit'))}"
        )
    side = attempt.get("side")
    if side:
        parts.append(str(drill.get(f"{side}_label") or side))
    return DETAIL_SEPARATOR.join(parts)


def attempt_rows(drill: dict, attempts: list[dict]) -> list[Component]:
    """
    Description:
        The player's value attempts, each with Delete (REQ-005, D9).

    Parameters:
        drill: a get_run_drill row (units and side labels).
        attempts: data_drill_results.list_value_attempts rows.

    Return Values:
        list[Component]: one row per attempt with the button {"type":
        "drill-attempt-delete", "index": drill_attempt_id}; "No attempts yet" when none.

    Notes:
        Pure.
    """
    if not attempts:
        return [dmc.Text(NO_ATTEMPTS, c="dimmed", id="drill-attempts-empty")]
    rows: list[Component] = []
    for attempt in attempts:
        rows.append(
            dmc.SimpleGrid(
                [
                    dmc.Text(format_attempt(drill, attempt)),
                    secondary_button(
                        {"type": "drill-attempt-delete", "index": int(attempt["drill_attempt_id"])},
                        "Delete",
                        color="red",
                    ),
                ],
                cols=2,
                spacing="xs",
            )
        )
    return rows


def _result_block(summary_text: str, pb_text: str) -> list[Component]:
    """
    Description:
        The shared result lines of the values and outcome panels: the summary and the
        personal best.

    Parameters:
        summary_text: format_summary of the default player.
        pb_text: format_personal_best of the default player.

    Return Values:
        list[Component]: [drill-summary, drill-pb].

    Notes:
        render_drill_player rewrites both on a player change (D16).
    """
    return [
        dmc.Text(summary_text, id="drill-summary", fw=600),
        dmc.Text(pb_text, id="drill-pb", size="sm", className=PRE_LINE_CLASS),
    ]


def values_panel(
    drill: dict,
    participants: list[dict],
    default_player_id: int,
    attempts: list[dict],
    summary: dict | None,
    pb_text: str,
) -> list[Component]:
    """
    Description:
        The values panel (plan §3.6): player picker drill-player, side, timer, value inputs,
        Save attempt, feedback, summary, personal best, the attempts with Delete and the
        delete confirmation.

    Parameters:
        drill: a get_run_drill row (capture_kind "values").
        participants: rows of data_practice.list_practice_participants.
        default_player_id: the selected player if a live participant, else the first (D4).
        attempts: list_value_attempts of the default player.
        summary: get_result_summary of the default player, or None.
        pb_text: format_personal_best of the default player.

    Return Values:
        list[Component]: the panel, a whole component group (plan §3.8.6).

    Notes:
        drill-value-2 is always present (Save reads it) and shown only for a physical drill
        with a second number. drill-delete-target holds the attempt id to delete.
    """
    decimals = _value_decimals(drill)
    has_second = drill.get("result_type_code") == RESULT_PHYSICAL and bool(drill.get("value_2_label"))
    return [
        court_player_picker(participants, default_player_id, "drill-player"),
        _side_control(drill),
        *_timer_controls(drill),
        _value_input("drill-value-1", _value_label(drill, 1), decimals),
        html.Div(
            _value_input("drill-value-2", _value_label(drill, 2), decimals),
            className="" if has_second else HIDDEN_CLASS,
        ),
        primary_button("drill-add-attempt", "Save attempt"),
        form_message("drill-feedback"),
        *_result_block(format_summary(drill, summary), pb_text),
        dmc.Title("Attempts", order=4),
        html.Div(dmc.Stack(attempt_rows(drill, attempts), gap="xs"), id="drill-attempts"),
        confirm_modal("attempt-del", "Delete attempt?", "Delete"),
        dcc.Store(id="drill-delete-target", storage_type="memory"),
    ]


# --- Outcome panel (REQ-077, REQ-010, REQ-058) ---

def outcome_groups_store(groups: list[dict]) -> list[dict[str, object]]:
    """
    Description:
        The outcome-groups store read by the clientside outcome_pick: each group's id, name
        and button labels.

    Parameters:
        groups: data_drill_results.list_outcome_structure.

    Return Values:
        list[dict[str, object]]: [{"id": outcome_group_id, "name": group_name, "labels":
        {str(drill_outcome_id): outcome_label}}] in group order.

    Notes:
        Pure.
    """
    return [
        {
            "id": int(group["outcome_group_id"]),
            "name": str(group["group_name"]),
            "labels": {
                str(int(outcome["drill_outcome_id"])): str(outcome["outcome_label"])
                for outcome in group["outcomes"]
            },
        }
        for group in groups
    ]


def outcome_panel(
    drill: dict,
    groups: list[dict],
    participants: list[dict],
    default_player_id: int,
    summary: dict | None,
    pb_text: str,
) -> list[Component]:
    """
    Description:
        The outcome-button panel (plan §3.6): player picker drill-player, side, one block
        of buttons per outcome group (two columns at 380 px), the pending picks, Undo with
        the save status, summary and personal best.

    Parameters:
        drill: a get_run_drill row (capture_kind "outcomes").
        groups: data_drill_results.list_outcome_structure of the version.
        participants: rows of data_practice.list_practice_participants.
        default_player_id: the selected player if a live participant, else the first (D4).
        summary: get_result_summary of the default player, or None.
        pb_text: format_personal_best of the default player.

    Return Values:
        list[Component]: the panel, a whole component group (plan §3.8.6).

    Notes:
        Buttons have the id {"type": "outcome-btn", "group": outcome_group_id, "index":
        drill_outcome_id}. Stores: outcome-pending (picks so far), outcome-groups,
        outcome-submit (a complete rep, written by outcome_pick) and outcome-visit
        ({player_id: [attempt ids]}, this visit's reps for Undo). A hidden drill-attempts
        div keeps render_drill_player's outputs present.
    """
    blocks: list[Component] = []
    for group in groups:
        group_id = int(group["outcome_group_id"])
        buttons = [
            secondary_button(
                {"type": "outcome-btn", "group": group_id, "index": int(outcome["drill_outcome_id"])},
                str(outcome["outcome_label"]),
            )
            for outcome in group["outcomes"]
        ]
        blocks.append(dmc.Text(str(group["group_name"]), fw=600))
        blocks.append(
            dmc.SimpleGrid(
                buttons, cols=OUTCOME_GRID_COLUMNS, spacing="xs", className=OUTCOME_GRID_CLASS
            )
        )
    return [
        court_player_picker(participants, default_player_id, "drill-player"),
        _side_control(drill),
        *blocks,
        dmc.Text("", id="outcome-pending-text", size="sm"),
        dmc.Group(
            [
                secondary_button("outcome-undo", "Undo"),
                save_status_badge("outcome-status"),
            ]
        ),
        *_result_block(format_summary(drill, summary, groups), pb_text),
        html.Div(id="drill-attempts", className=HIDDEN_CLASS),
        dcc.Store(id="outcome-pending", storage_type="memory"),
        dcc.Store(id="outcome-groups", storage_type="memory", data=outcome_groups_store(groups)),
        dcc.Store(id="outcome-submit", storage_type="memory"),
        dcc.Store(id="outcome-visit", storage_type="memory", data={}),
    ]
