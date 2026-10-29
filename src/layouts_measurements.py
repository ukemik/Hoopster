"""Physical measurements UI for Hoopster: the Measurements screen (latest values, Add
measurement, the chart and history of one test), the measurement page, and the editable
Physical tests list (REQ-034, REQ-035; task 2026-10-23_goals_measurements).

Routes /measurements, /measurements/<id> and /physical-tests; the More drawer and Settings >
Training link here. Built only through the wrappers of src/layouts.py (COD-5-01). Layout
modules import nothing from controllers; the callbacks live in
src/controller_measurements.py.
"""

import logging
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import dash_mantine_components as dmc
import plotly.graph_objects as go
from dash import dcc, html
from dash.development.base_component import Component

from config.app_config import (
    MEASUREMENT_DECIMALS,
    MEASUREMENT_NOTES_MAX_LENGTH,
    MEASUREMENT_VALUE_MAX,
    PHYSICAL_TEST_NAME_MAX_LENGTH,
    PHYSICAL_TEST_UNITS,
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

MEASUREMENTS_PATH = "/measurements"
PHYSICAL_TESTS_PATH = "/physical-tests"
DATE_PICKER_FORMAT = "ddd D MMM YYYY"
ITEM_CLASS = "hoopster-recent-item"
LINK_ITEM_CLASS = "hoopster-recent-item hoopster-recent-link"
DETAIL_SEPARATOR = " · "

# The chart (D13): a line with markers, no zoom and no mode bar, sized for a 380 px phone.
CHART_HEIGHT_PX = 300
CHART_MARGIN: dict[str, int] = {"l": 50, "r": 10, "t": 30, "b": 40}
CHART_LINE_COLOR = "#fd7e14"
GRAPH_CONFIG: dict[str, bool] = {
    "displayModeBar": False,
    "scrollZoom": False,
    "responsive": True,
}
# A responsive dcc.Graph without a style height defaults to 100% of its auto-height parent,
# which can lay it out 0 px tall (AP-4); this gives the container the chart's own height.
GRAPH_STYLE: dict[str, str] = {"height": f"{CHART_HEIGHT_PX}px", "width": "100%"}
LOWER_IS_BETTER_TEXT = "Lower is better"
NO_MEASUREMENTS_TEXT = "No measurements yet."
NO_HISTORY_TEXT = "No measurements of this test yet."
NO_TESTS_TEXT = "No physical tests yet."
UNIT_COLUMN_OPTIONS: list[dict[str, str]] = [
    {"value": code, "label": word} for code, word in PHYSICAL_TEST_UNITS.items()
]
DEFAULT_UNIT = next(iter(PHYSICAL_TEST_UNITS))


# --- Helpers ---

def unit_text(unit: object) -> str:
    """
    Description:
        A test unit in words, e.g. "inches" for "in".

    Parameters:
        unit: the unit code ("in", "lb", "s").

    Return Values:
        str: the word, or the code itself when it is not known.

    Notes:
        Pure.
    """
    return PHYSICAL_TEST_UNITS.get(str(unit), str(unit))


def test_options(tests: list[dict]) -> list[dict[str, str]]:
    """
    Description:
        The physical-test picker's options, e.g. "Height (in)".

    Parameters:
        tests: data_measurements.list_physical_tests rows.

    Return Values:
        list[dict[str, str]]: {"value": str(physical_test_id), "label": "{name} ({unit})"}
        per test, in the rows' order.

    Notes:
        Pure. Also used for the physical goal's test picker.
    """
    return [
        {
            "value": str(int(test["physical_test_id"])),
            "label": f"{test['test_name']} ({test['unit']})",
        }
        for test in tests
    ]


def format_measurement_value(value: object, unit: object) -> str:
    """
    Description:
        A measured value with its unit, at most two decimals: "61.5 in", "4.25 s".

    Parameters:
        value: the value (a Decimal).
        unit: the test's unit code.

    Return Values:
        str: the text, or "–" when value is not a number.

    Notes:
        Pure.
    """
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return "–"
    if not number.is_finite():
        return "–"
    text = format(
        number.quantize(Decimal(1).scaleb(-MEASUREMENT_DECIMALS), rounding=ROUND_HALF_UP), "f"
    )
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text} {unit}"


def format_measurement_date(value: object) -> str:
    """
    Description:
        A measurement's calendar date as stored, e.g. "Thu 23 Oct 2026" (REQ-055).

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


def _number_value(value: object) -> object:
    """
    Description:
        A stored value as a NumberInput value.

    Parameters:
        value: a Decimal or None.

    Return Values:
        object: a float, or "" when missing.

    Notes:
        Pure. A Decimal becomes a float so the value is JSON-serialisable.
    """
    try:
        return float(Decimal(str(value))) if value is not None else ""
    except (InvalidOperation, ValueError):
        return ""


# --- Chart (REQ-035, D13) ---

def build_measurement_figure(series: list[dict], test: dict | None) -> go.Figure:
    """
    Description:
        The chart of one test over time: a line with markers, dates across, values up.

    Parameters:
        series: data_measurements.list_measurement_series rows, oldest first.
        test: the test's row ({"test_name", "unit", "lower_is_better"}), or None when there
            is no test.

    Return Values:
        go.Figure: the figure; an empty series shows "No measurements yet.". A
        lower-is-better test carries a "Lower is better" note.

    Notes:
        Pure. Both axes are fixed (no zoom, D13); 300 px tall with small margins.
    """
    figure = go.Figure()
    unit = test.get("unit") if test else None
    if series:
        figure.add_trace(
            go.Scatter(
                x=[str(row["measured_on"]) for row in series],
                y=[float(Decimal(str(row["measured_value"]))) for row in series],
                mode="lines+markers",
                line={"color": CHART_LINE_COLOR},
                name=str(test.get("test_name")) if test else "",
                hovertemplate="%{x}: %{y}<extra></extra>",
            )
        )
    else:
        figure.add_annotation(
            text=NO_MEASUREMENTS_TEXT,
            showarrow=False,
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
        )
    if test and test.get("lower_is_better"):
        figure.add_annotation(
            text=LOWER_IS_BETTER_TEXT,
            showarrow=False,
            xref="paper",
            yref="paper",
            x=1,
            y=1.08,
            xanchor="right",
        )
    figure.update_layout(
        height=CHART_HEIGHT_PX,
        margin=CHART_MARGIN,
        showlegend=False,
        xaxis={"fixedrange": True, "type": "date"},
        yaxis={"fixedrange": True, "title": {"text": unit_text(unit) if unit else ""}},
        plot_bgcolor="#ffffff",
    )
    return figure


# --- Lists ---

def latest_items(latest: list[dict]) -> list[Component]:
    """
    Description:
        The latest value of each test: "{test}: {value}" with its date.

    Parameters:
        latest: data_measurements.list_latest_measurements rows.

    Return Values:
        list[Component]: one box per test with id {"type": "meas-latest-item", "index":
        physical_test_id}, or one Text "No measurements yet." with id "meas-latest-empty".

    Notes:
        Pure.
    """
    if not latest:
        return [dmc.Text(NO_MEASUREMENTS_TEXT, id="meas-latest-empty", c="dimmed")]
    return [
        html.Div(
            dmc.Group(
                [
                    dmc.Text(
                        f"{row['test_name']}: "
                        f"{format_measurement_value(row['measured_value'], row['unit'])}",
                        fw=600,
                    ),
                    dmc.Text(format_measurement_date(row["measured_on"]), size="sm", c="dimmed"),
                ],
                justify="space-between",
            ),
            id={"type": "meas-latest-item", "index": int(row["physical_test_id"])},
            className=ITEM_CLASS,
        )
        for row in latest
    ]


def history_items(rows: list[dict], unit: object) -> list[Component]:
    """
    Description:
        The history of one test, newest first: one link per measurement to its page.

    Parameters:
        rows: data_measurements.list_measurements rows.
        unit: the test's unit code.

    Return Values:
        list[Component]: dcc.Link items with id {"type": "meas-item", "index":
        physical_measurement_id} showing "{date} · {value} {unit}" and the notes, or one
        Text with id "meas-history-empty".

    Notes:
        Pure.
    """
    if not rows:
        return [dmc.Text(NO_HISTORY_TEXT, id="meas-history-empty", c="dimmed")]
    items: list[Component] = []
    for row in rows:
        measurement_id = int(row["physical_measurement_id"])
        content: list[Component] = [
            dmc.Text(
                f"{format_measurement_date(row['measured_on'])}{DETAIL_SEPARATOR}"
                f"{format_measurement_value(row['measured_value'], unit)}"
            )
        ]
        if row.get("notes"):
            content.append(
                dmc.Text(str(row["notes"]), size="sm", c="dimmed", style={"overflowWrap": "anywhere"})
            )
        items.append(
            dcc.Link(
                content,
                id={"type": "meas-item", "index": measurement_id},
                href=f"{MEASUREMENTS_PATH}/{measurement_id}",
                className=LINK_ITEM_CLASS,
            )
        )
    return items


def _value_input(element_id: str, value: object) -> Component:
    """
    Description:
        The measured value's number input: 2 decimals, no negatives, no spin controls.

    Parameters:
        element_id: the stable element id.
        value: the initial value ("" for empty).

    Return Values:
        Component: the dmc.NumberInput.

    Notes:
        clampBehavior "none" keeps what is typed, so parse_measurement_form's message
        speaks.
    """
    return dmc.NumberInput(
        id=element_id,
        label="Value",
        value=value,
        min=0,
        max=float(MEASUREMENT_VALUE_MAX),
        decimalScale=MEASUREMENT_DECIMALS,
        allowNegative=False,
        clampBehavior="none",
        hideControls=True,
        inputProps={"inputMode": "decimal"},
        size=CONTROL_SIZE,
    )


def _notes_input(element_id: str, value: object) -> Component:
    """
    Description:
        The measurement notes field (at most 4000 characters).

    Parameters:
        element_id: the stable element id.
        value: the initial text.

    Return Values:
        Component: the dmc.Textarea.

    Notes:
        None.
    """
    return dmc.Textarea(
        id=element_id,
        label="Notes (optional)",
        value=str(value or ""),
        inputProps={"maxLength": MEASUREMENT_NOTES_MAX_LENGTH},
        autosize=True,
        minRows=2,
        size=CONTROL_SIZE,
    )


# --- Measurements screen (REQ-034, REQ-035) ---

def measurements_layout(
    players: list[dict],
    selected_id: int,
    tests: list[dict],
    latest: list[dict],
    chart_test_id: int | None,
    series: list[dict],
    history: list[dict],
    today: str | None,
) -> Component:
    """
    Description:
        The Measurements screen: the player picker, the latest values, Add measurement, the
        chart and history of one test, and the link to the test list.

    Parameters:
        players: data_players.list_live_players (the picker's options).
        selected_id: current_player_id(), the picker's value. Never a user id.
        tests: data_measurements.list_physical_tests rows.
        latest: data_measurements.list_latest_measurements of the player.
        chart_test_id: the test shown in the chart and history, or None without tests.
        series: list_measurement_series of that test.
        history: list_measurements of that test.
        today: the device's date "YYYY-MM-DD" (the default date), or None.

    Return Values:
        Component: the page with meas-player, meas-latest, meas-add-test, meas-add-date,
        meas-add-value, meas-add-notes, meas-add-submit, meas-add-feedback,
        meas-chart-test, meas-chart, meas-history and meas-tests-link.

    Notes:
        The new measurement's player is the picker's value at the time of Add (D6).
        meas-chart carries GRAPH_STYLE, a fixed CHART_HEIGHT_PX container height (AP-4).
    """
    options = test_options(tests)
    chart_test = next(
        (t for t in tests if int(t["physical_test_id"]) == chart_test_id), None
    )
    chart_value = str(chart_test_id) if chart_test_id is not None else None
    first_test = options[0]["value"] if options else None
    return page_skeleton(
        "Measurements",
        [
            dmc.Select(
                id="meas-player",
                label="Player",
                data=player_options(players),
                value=str(selected_id),
                allowDeselect=False,
                size=CONTROL_SIZE,
            ),
            dmc.Title("Latest", order=3),
            dmc.Stack(latest_items(latest), id="meas-latest", gap="xs"),
            dmc.Title("Add a measurement", order=3),
            dmc.Select(
                id="meas-add-test",
                label="Test",
                data=options,
                value=first_test,
                allowDeselect=False,
                size=CONTROL_SIZE,
            ),
            dmc.DatePickerInput(
                id="meas-add-date",
                label="Date",
                value=today,
                valueFormat=DATE_PICKER_FORMAT,
                size=CONTROL_SIZE,
            ),
            _value_input("meas-add-value", ""),
            _notes_input("meas-add-notes", ""),
            primary_button("meas-add-submit", "Add measurement"),
            form_message("meas-add-feedback"),
            dmc.Title("Over time", order=3),
            dmc.Select(
                id="meas-chart-test",
                label="Test",
                data=options,
                value=chart_value,
                allowDeselect=False,
                size=CONTROL_SIZE,
            ),
            dcc.Graph(
                id="meas-chart",
                figure=build_measurement_figure(series, chart_test),
                config=GRAPH_CONFIG,
                style=GRAPH_STYLE,
            ),
            dmc.Stack(
                history_items(history, chart_test.get("unit") if chart_test else ""),
                id="meas-history",
                gap="xs",
            ),
            html.Div(
                nav_link("meas-tests-link", "Edit test list", PHYSICAL_TESTS_PATH),
                className="hoopster-link-button",
            ),
        ],
    )


# --- Measurement page ---

def measurement_page_layout(row: dict, tests: list[dict], zone: str | None) -> Component:
    """
    Description:
        The page of one measurement: test, date, value and notes with Save, Delete with its
        confirmation, and the audit lines (REQ-004, REQ-005).

    Parameters:
        row: data_measurements.get_measurement row, already confirmed live.
        tests: data_measurements.list_physical_tests rows.
        zone: the device's IANA zone (audit times, REQ-055).

    Return Values:
        Component: the page with meas-edit-back, the store meas-edit-id, meas-edit-for,
        meas-edit-test, meas-edit-date, meas-edit-value, meas-edit-notes, meas-edit-save,
        meas-edit-saved, meas-edit-feedback, meas-delete-open, the meas-delete dialog and
        the meas-edit-audit lines.

    Notes:
        meas-edit-id is written here only after the builder's get_measurement check.
    """
    measured_on = row.get("measured_on")
    return page_skeleton(
        "Measurement",
        [
            html.Div(
                nav_link("meas-edit-back", "‹ Measurements", MEASUREMENTS_PATH),
                className="hoopster-link-button",
            ),
            dcc.Store(
                id="meas-edit-id", storage_type="memory", data=int(row["physical_measurement_id"])
            ),
            dmc.Text(f"Player: {row.get('player_name')}", id="meas-edit-for", fw=600),
            dmc.Select(
                id="meas-edit-test",
                label="Test",
                data=test_options(tests),
                value=str(int(row["physical_test_id"])),
                allowDeselect=False,
                size=CONTROL_SIZE,
            ),
            dmc.DatePickerInput(
                id="meas-edit-date",
                label="Date",
                value=measured_on.isoformat() if isinstance(measured_on, date) else None,
                valueFormat=DATE_PICKER_FORMAT,
                size=CONTROL_SIZE,
            ),
            _value_input("meas-edit-value", _number_value(row.get("measured_value"))),
            _notes_input("meas-edit-notes", row.get("notes")),
            primary_button("meas-edit-save", "Save"),
            dmc.Text("", id="meas-edit-saved", c="green"),
            form_message("meas-edit-feedback"),
            secondary_button("meas-delete-open", "Delete measurement", color="red"),
            confirm_modal("meas-delete", "Delete measurement?", "Delete"),
            audit_fields_block(
                "meas-edit-audit",
                row.get("created_by_name"),
                format_entry_datetime(row.get("created_at"), zone),
                row.get("updated_by_name"),
                format_entry_datetime(row.get("updated_at"), zone),
            ),
        ],
    )


# --- Physical tests list (REQ-034, D2, D12) ---

def _used_by_text(count: int) -> str:
    """
    Description:
        The "Used by N" line of a physical test.

    Parameters:
        count: live measurements plus live goals of the test.

    Return Values:
        str: "Used by {n}" with "measurement or goal" in the right number.

    Notes:
        Pure.
    """
    noun = "measurement or goal" if count == 1 else "measurements or goals"
    return f"Used by {count} {noun}"


def physical_test_rows(tests: list[dict]) -> list[Component]:
    """
    Description:
        The rows of the Physical tests list: per test its name, unit, the "lower is better"
        box, its use count, Save and Delete, and a feedback line.

    Parameters:
        tests: data_measurements.list_physical_tests rows.

    Return Values:
        list[Component]: one dmc.Paper per test; with none, one Text with id
        "ptest-empty".

    Notes:
        Pattern ids per row, index = physical_test_id: {"type": "ptest-name"},
        {"type": "ptest-unit"}, {"type": "ptest-lower"} (Checkbox, class hoopster-check),
        {"type": "ptest-save"}, {"type": "ptest-delete"} and {"type": "ptest-row-feedback"}.
    """
    if not tests:
        return [dmc.Text(NO_TESTS_TEXT, id="ptest-empty", c="dimmed")]
    rows: list[Component] = []
    for test in tests:
        test_id = int(test["physical_test_id"])
        rows.append(
            dmc.Paper(
                dmc.Stack(
                    [
                        dmc.TextInput(
                            id={"type": "ptest-name", "index": test_id},
                            label="Name",
                            value=str(test["test_name"]),
                            inputProps={"maxLength": PHYSICAL_TEST_NAME_MAX_LENGTH},
                            size=CONTROL_SIZE,
                        ),
                        dmc.Select(
                            id={"type": "ptest-unit", "index": test_id},
                            label="Unit",
                            data=UNIT_COLUMN_OPTIONS,
                            value=str(test["unit"]),
                            allowDeselect=False,
                            size=CONTROL_SIZE,
                        ),
                        dmc.Checkbox(
                            id={"type": "ptest-lower", "index": test_id},
                            label="Lower is better",
                            checked=bool(test["lower_is_better"]),
                            size=CONTROL_SIZE,
                            className="hoopster-check",
                        ),
                        dmc.Text(_used_by_text(int(test["use_count"])), size="sm", c="dimmed"),
                        dmc.Group(
                            [
                                secondary_button({"type": "ptest-save", "index": test_id}, "Save"),
                                secondary_button(
                                    {"type": "ptest-delete", "index": test_id},
                                    "Delete",
                                    color="red",
                                ),
                            ],
                            grow=True,
                        ),
                        dmc.Text("", id={"type": "ptest-row-feedback", "index": test_id}),
                    ],
                    gap="xs",
                ),
                withBorder=True,
                p="sm",
            )
        )
    return rows


def physical_tests_layout(tests: list[dict]) -> Component:
    """
    Description:
        The Physical tests screen (D2): the editable list shared by all players, the Add
        form and the delete confirmation (REQ-005).

    Parameters:
        tests: data_measurements.list_physical_tests rows.

    Return Values:
        Component: the page with ptest-measurements-link, ptest-settings-link, ptest-rows,
        ptest-new-name, ptest-new-unit, ptest-new-lower, ptest-add, ptest-add-feedback, the
        store ptest-del-target and the ptest-del dialog.

    Notes:
        ptest-del-target holds the test the open dialog would delete; only open_delete_test
        writes it.
    """
    return page_skeleton(
        "Physical tests",
        [
            dmc.SimpleGrid(
                [
                    html.Div(
                        nav_link("ptest-measurements-link", "‹ Measurements", MEASUREMENTS_PATH),
                        className="hoopster-link-button",
                    ),
                    html.Div(
                        nav_link("ptest-settings-link", "‹ Settings", "/settings"),
                        className="hoopster-link-button",
                    ),
                ],
                cols=2,
                spacing="xs",
            ),
            dmc.Stack(physical_test_rows(tests), id="ptest-rows", gap="sm"),
            dmc.Title("Add a test", order=3),
            dmc.TextInput(
                id="ptest-new-name",
                label="Name",
                inputProps={"maxLength": PHYSICAL_TEST_NAME_MAX_LENGTH},
                size=CONTROL_SIZE,
            ),
            dmc.Select(
                id="ptest-new-unit",
                label="Unit",
                data=UNIT_COLUMN_OPTIONS,
                value=DEFAULT_UNIT,
                allowDeselect=False,
                size=CONTROL_SIZE,
            ),
            dmc.Checkbox(
                id="ptest-new-lower",
                label="Lower is better",
                checked=False,
                size=CONTROL_SIZE,
                className="hoopster-check",
            ),
            primary_button("ptest-add", "Add test"),
            form_message("ptest-add-feedback"),
            dcc.Store(id="ptest-del-target", storage_type="memory"),
            confirm_modal("ptest-del", "Delete physical test?", "Delete"),
        ],
    )
