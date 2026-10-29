"""Measurements controller of Hoopster: the Measurements, measurement and Physical tests page
builders, the pure form checks, and the picker, chart, Add, Save, Delete and test-list
callbacks (REQ-034, REQ-035, REQ-005; task 2026-10-23_goals_measurements).

The Measurements screen shows the selected player (current_player_id(), changed by
meas-player through set_current_player_id after a live check); a new measurement's player is
meas-player's value at the time of Add, locked again in the write (D6, D14); an edited
measurement's player comes from its own row (Context Adaptation Table, COD-8-01). Every
measurement write refreshes that player's goal statuses in the same transaction (D4). The
physical-test list is shared by all players (D2). Every write goes through
app_controller.run_write.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import plotly.graph_objects as go
from dash import ALL, MATCH, ClientsideFunction, Dash, Input, Output, State, ctx, no_update
from dash.development.base_component import Component
from dash.exceptions import PreventUpdate
from flask_login import current_user
from sqlalchemy.orm import Session

from config.app_config import (
    EARLIEST_ENTRY_DATE,
    MEASUREMENT_DECIMALS,
    MEASUREMENT_HISTORY_LIMIT,
    MEASUREMENT_NOTES_MAX_LENGTH,
    MEASUREMENT_VALUE_MAX,
    PHYSICAL_TEST_NAME_MAX_LENGTH,
    PHYSICAL_TEST_UNITS,
)
from src import data_handler
from src.app_controller import (
    DeviceClock,
    log_failure,
    new_reference_code,
    normalise_clock,
    parse_player_value,
    run_write,
    run_write_with_reason,
    set_current_player_id,
)
from src.data_goals import refresh_goal_statuses
from src.data_measurements import (
    delete_measurement,
    delete_physical_test,
    get_measurement,
    insert_measurement,
    insert_physical_test,
    list_latest_measurements,
    list_measurement_series,
    list_measurements,
    list_physical_tests,
    lock_physical_test,
    update_measurement,
    update_physical_test,
)
from src.data_players import list_live_players, lock_live_player
from src.data_settings import get_retention_days
from src.layouts import not_found_page
from src.layouts_measurements import (
    MEASUREMENTS_PATH,
    build_measurement_figure,
    format_measurement_date,
    format_measurement_value,
    history_items,
    latest_items,
    measurement_page_layout,
    measurements_layout,
    physical_test_rows,
    physical_tests_layout,
)

logger = logging.getLogger(__name__)

ID_VALUE_PATTERN = re.compile(r"[0-9]{1,18}")
VALUE_QUANTUM = Decimal(1).scaleb(-MEASUREMENT_DECIMALS)
FEEDBACK_OK_COLOR = "green"
FEEDBACK_ERROR_COLOR = "red"
PHYSICAL_TEST_CONSTRAINT_MESSAGES: dict[str, str] = {
    "ux_physical_tests_name": "A physical test with this name already exists."
}

MESSAGE_TEST = "Choose a test."
MESSAGE_DATE = "Choose a date."
MESSAGE_DATE_EARLY = f"Choose a date from {EARLIEST_ENTRY_DATE.year} on."
MESSAGE_FUTURE = "A measurement cannot be in the future."
MESSAGE_VALUE = f"Enter a value above 0 and at most {MEASUREMENT_VALUE_MAX}."
MESSAGE_NOTES = f"Notes are limited to {MEASUREMENT_NOTES_MAX_LENGTH} characters."
MESSAGE_NO_DATE = "The device date is not known yet. Reload the page and try again."
MESSAGE_SAVED_MEASUREMENT = "Saved {test} {value}."
MESSAGE_SAVED = "Saved."
MESSAGE_SAVE_FAILED = "Could not save the measurement. Reference: {ref}"
MESSAGE_LOAD_FAILED = "Could not load the measurements. Reference: {ref}"
MESSAGE_DELETE_FAILED = "Could not delete. Reference: {ref}"
MESSAGE_DELETE_QUESTION = (
    "Delete the measurement {test} {value} of {date}? You can restore it from Settings > "
    "Recently deleted for {days} days."
)
MESSAGE_TEST_NAME = f"Enter a test name of 1 to {PHYSICAL_TEST_NAME_MAX_LENGTH} characters."
MESSAGE_TEST_UNIT = "Choose inches, pounds or seconds."
MESSAGE_TEST_USED = (
    "Unit and direction can change only while no measurement or goal uses this test."
)
MESSAGE_TEST_SAVE_FAILED = "Could not save the test. Reference: {ref}"
MESSAGE_TEST_GONE = "This test no longer exists."
MESSAGE_TEST_DELETE_QUESTION = "Delete the test {name}?"
MESSAGE_TEST_IN_USE = (
    " It is used by {count} measurements or goals and cannot be deleted until they are."
)


# --- Small readers (pure) ---

def _picker_id(value: object) -> int | None:
    """
    Description:
        Reads a picker value holding an id (dmc values are strings).

    Parameters:
        value: e.g. "3".

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


def _clock_date(clock: object) -> date | None:
    """
    Description:
        The device's today from the device-clock store (REQ-055).

    Parameters:
        clock: device-clock.data.

    Return Values:
        date | None: the device date, or None when unknown.

    Notes:
        Pure. Never the server's date.
    """
    today = normalise_clock(clock)["today"]
    return date.fromisoformat(today) if today else None


def _clicked_index(button_type: str) -> int:
    """
    Description:
        The index of the pattern button that was really clicked.

    Parameters:
        button_type: the pattern id's "type", e.g. "ptest-delete".

    Return Values:
        int: the clicked button's "index".

    Notes:
        Raises PreventUpdate when the trigger is not that button type or its n_clicks is
        empty (a re-rendered list fires ALL callbacks without a click).
    """
    triggered = ctx.triggered_id
    if not isinstance(triggered, dict) or triggered.get("type") != button_type:
        raise PreventUpdate
    if not ctx.triggered or not ctx.triggered[0].get("value"):
        raise PreventUpdate
    index = triggered.get("index")
    if not isinstance(index, int) or isinstance(index, bool):
        raise PreventUpdate
    return index


# --- Form checks (pure, COD-17-01; REQ-034, D13) ---

@dataclass(frozen=True)
class MeasurementForm:
    """
    Description:
        The checked values of a measurement form.

    Parameters:
        physical_test_id: a live test's id.
        measured_on: the date, from 1900 to the device's today.
        measured_value: above 0, at most 99999.99, rounded half-up to 2 decimals.
        notes: up to 4000 characters, or None.

    Return Values:
        Not applicable (data class).

    Notes:
        Built only by parse_measurement_form.
    """

    physical_test_id: int
    measured_on: date
    measured_value: Decimal
    notes: str | None


@dataclass(frozen=True)
class TestForm:
    """
    Description:
        The checked values of a physical-test form.

    Parameters:
        test_name: the trimmed name, 1 to 100 characters.
        unit: "in", "lb" or "s".
        lower_is_better: True when a lower value is better.

    Return Values:
        Not applicable (data class).

    Notes:
        Built only by parse_test_form.
    """

    test_name: str
    unit: str
    lower_is_better: bool


def parse_measurement_form(
    test: object,
    measured_on: object,
    value: object,
    notes: object,
    *,
    tests: list[dict],
    today: date,
) -> tuple[MeasurementForm | None, str | None]:
    """
    Description:
        Checks a measurement form in screen order: a live test; a date from 1900 to the
        device's today ("A measurement cannot be in the future."); a value above 0 and at
        most 99999.99, rounded half-up to 2 decimals; notes of at most 4000 characters
        (blank notes become None).

    Parameters:
        test: the test picker's value (an id as a string).
        measured_on: the date picker's value.
        value: the value input's value.
        notes: the notes field's value.
        tests: list_physical_tests rows (the live tests).
        today: the device's date (REQ-055).

    Return Values:
        tuple[MeasurementForm | None, str | None]: (the values, None) or (None, the
        message).

    Notes:
        Pure. The write statements re-check that the test is live (D18).
    """
    test_id = _picker_id(test)
    if test_id is None or all(int(t["physical_test_id"]) != test_id for t in tests):
        return None, MESSAGE_TEST
    day = _parse_date(measured_on)
    if day is None:
        return None, MESSAGE_DATE
    if day < EARLIEST_ENTRY_DATE:
        return None, MESSAGE_DATE_EARLY
    if day > today:
        return None, MESSAGE_FUTURE
    if value is None or isinstance(value, bool) or value == "":
        return None, MESSAGE_VALUE
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None, MESSAGE_VALUE
    if not number.is_finite():
        return None, MESSAGE_VALUE
    rounded = number.quantize(VALUE_QUANTUM, rounding=ROUND_HALF_UP)
    if rounded <= 0 or rounded > MEASUREMENT_VALUE_MAX:
        return None, MESSAGE_VALUE
    notes_text = notes.strip() if isinstance(notes, str) else ""
    if len(notes_text) > MEASUREMENT_NOTES_MAX_LENGTH:
        return None, MESSAGE_NOTES
    return MeasurementForm(test_id, day, rounded, notes_text or None), None


def parse_test_form(
    name: object, unit: object, lower: object
) -> tuple[TestForm | None, str | None]:
    """
    Description:
        Checks a physical-test form: a name of 1 to 100 characters after trimming and a unit
        of PHYSICAL_TEST_UNITS.

    Parameters:
        name: the name field's value.
        unit: the unit picker's value.
        lower: the "Lower is better" checkbox's checked value.

    Return Values:
        tuple[TestForm | None, str | None]: (the values, None) or (None, the message).

    Notes:
        Pure. Whether the name is free is checked by ux_physical_tests_name at write time.
    """
    test_name = name.strip() if isinstance(name, str) else ""
    if not test_name or len(test_name) > PHYSICAL_TEST_NAME_MAX_LENGTH:
        return None, MESSAGE_TEST_NAME
    if unit not in PHYSICAL_TEST_UNITS:
        return None, MESSAGE_TEST_UNIT
    return TestForm(test_name, str(unit), bool(lower)), None


# --- Page builders (REQ-034, REQ-035) ---

def _chart_view(
    session: Session, player_id: int, tests: list[dict], test_id: int | None
) -> tuple[go.Figure, list[Component]]:
    """
    Description:
        The chart and history of one test for a player.

    Parameters:
        session: the request session.
        player_id: the shown player.
        tests: list_physical_tests rows.
        test_id: the test to show, or None without tests.

    Return Values:
        tuple[go.Figure, list[Component]]: the figure and the history items.

    Notes:
        Read only; two statements.
    """
    test = next((t for t in tests if int(t["physical_test_id"]) == test_id), None)
    if test is None:
        return build_measurement_figure([], None), history_items([], "")
    series = list_measurement_series(session, player_id, int(test["physical_test_id"]))
    history = list_measurements(
        session, player_id, int(test["physical_test_id"]), MEASUREMENT_HISTORY_LIMIT
    )
    return build_measurement_figure(series, test), history_items(history, test["unit"])


def _first_chart_test(tests: list[dict], latest: list[dict]) -> int | None:
    """
    Description:
        The test the chart starts on: the first test that has data, else the first test.

    Parameters:
        tests: list_physical_tests rows, in order.
        latest: list_latest_measurements rows of the player.

    Return Values:
        int | None: the physical_test_id, or None without tests.

    Notes:
        Pure.
    """
    with_data = {int(row["physical_test_id"]) for row in latest}
    for test in tests:
        if int(test["physical_test_id"]) in with_data:
            return int(test["physical_test_id"])
    return int(tests[0]["physical_test_id"]) if tests else None


def build_measurements_page(
    player_id: int, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds the Measurements screen for the selected player.

    Parameters:
        player_id: the selected player from current_player_id() (never None here). Never a
            user id (COD-8-01).
        clock: the device clock (today is the default date).
        params: the route parameters (none).

    Return Values:
        Component: the page.

    Notes:
        Exceptions propagate to the router, which shows the error page with a reference.
    """
    session = data_handler.get_session()
    tests = list_physical_tests(session)
    latest = list_latest_measurements(session, player_id)
    chart_test_id = _first_chart_test(tests, latest)
    series: list[dict] = []
    history: list[dict] = []
    if chart_test_id is not None:
        series = list_measurement_series(session, player_id, chart_test_id)
        history = list_measurements(session, player_id, chart_test_id, MEASUREMENT_HISTORY_LIMIT)
    return measurements_layout(
        list_live_players(session),
        player_id,
        tests,
        latest,
        chart_test_id,
        series,
        history,
        clock["today"],
    )


def build_measurement_page(
    player_id: int, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds the page of the measurement in the URL.

    Parameters:
        player_id: the selected player (unused: the measurement's player comes from its
            row).
        clock: the device clock (zone for the audit lines).
        params: {"physical_measurement_id": "<digits>"} from /measurements/<id>.

    Return Values:
        Component: the page, or the not-found page when the measurement or its profile is
        not live.

    Notes:
        meas-edit-id is written by the layout only after this get_measurement check.
    """
    session = data_handler.get_session()
    row = get_measurement(session, int(params["physical_measurement_id"]))
    if row is None:
        return not_found_page()
    return measurement_page_layout(row, list_physical_tests(session), clock["zone"])


def build_physical_tests_page(
    player_id: int | None, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds the Physical tests screen (D2), shared by all players.

    Parameters:
        player_id: the selected player, or None (unused: the list is not player-scoped).
        clock: the device clock (unused).
        params: the route parameters (none).

    Return Values:
        Component: the page.

    Notes:
        In ROUTES_WITHOUT_PLAYER.
    """
    return physical_tests_layout(list_physical_tests(data_handler.get_session()))


# --- Measurement callbacks (REQ-034, REQ-035, REQ-005) ---

def _player_view(
    session: Session, player_id: int, chart_test_id: int | None
) -> tuple[list[Component], object, go.Figure, list[Component]]:
    """
    Description:
        Everything the Measurements screen shows for a player: the latest values, the chart
        test, the chart and the history.

    Parameters:
        session: the request session.
        player_id: the shown player.
        chart_test_id: the test to chart, or None for the first test that has data.

    Return Values:
        tuple[list[Component], object, go.Figure, list[Component]]: meas-latest children,
        meas-chart-test value (a string or None), meas-chart figure, meas-history children.

    Notes:
        Read only.
    """
    tests = list_physical_tests(session)
    latest = list_latest_measurements(session, player_id)
    test_id = chart_test_id if chart_test_id is not None else _first_chart_test(tests, latest)
    figure, history = _chart_view(session, player_id, tests, test_id)
    return latest_items(latest), str(test_id) if test_id is not None else None, figure, history


def select_meas_player(value: str | None) -> tuple[object, object, object, object]:
    """
    Description:
        The Measurements player picker: makes the picked player the selected player and
        shows that player's latest values, chart and history.

    Parameters:
        value: meas-player.value, the player id as a string.

    Return Values:
        tuple[object, object, object, object]: meas-latest children, meas-chart-test value,
        meas-chart figure and meas-history children.

    Notes:
        A boundary (COD-4-04). Raises PreventUpdate for a value that is not a player id and
        when the player is no longer live (logged as a warning).
    """
    player_id = parse_player_value(value)
    if player_id is None:
        raise PreventUpdate
    try:
        if not set_current_player_id(player_id):
            logger.warning("Measurements picker chose player %s, which is not live", player_id)
            raise PreventUpdate
        return _player_view(data_handler.get_session(), player_id, None)
    except PreventUpdate:
        raise
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Measurements player change failed (ref %s)", ref)
        return MESSAGE_LOAD_FAILED.format(ref=ref), no_update, no_update, no_update


def show_meas_test(test: str | None, player: str | None) -> tuple[object, object]:
    """
    Description:
        The chart's test picker: shows that test's chart and history for the picked player.

    Parameters:
        test: meas-chart-test.value.
        player: meas-player.value (State).

    Return Values:
        tuple[object, object]: meas-chart figure and meas-history children.

    Notes:
        A boundary (COD-4-04). Raises PreventUpdate for values that are not ids.
    """
    test_id = _picker_id(test)
    player_id = parse_player_value(player)
    if test_id is None or player_id is None:
        raise PreventUpdate
    try:
        session = data_handler.get_session()
        return _chart_view(session, player_id, list_physical_tests(session), test_id)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Measurement chart failed (ref %s)", ref)
        return no_update, MESSAGE_LOAD_FAILED.format(ref=ref)


def add_measurement(
    n_clicks: int | None,
    player: str | None,
    test: str | None,
    measured_on: str | None,
    value: object,
    notes: str | None,
    clock: object,
) -> tuple[object, ...]:
    """
    Description:
        Add measurement: checks the form, then in one transaction locks the live profile,
        adds the measurement and refreshes that player's goal statuses; shows the new
        values with the added test in the chart.

    Parameters:
        n_clicks: meas-add-submit.n_clicks.
        player: meas-player.value (the measurement player, D6).
        test: meas-add-test.value.
        measured_on: meas-add-date.value.
        value: meas-add-value.value.
        notes: meas-add-notes.value.
        clock: device-clock.data (today, REQ-055).

    Return Values:
        tuple[object, ...]: meas-latest children, meas-chart-test value, meas-chart figure,
        meas-history children, meas-add-feedback text and colour, meas-add-value value and
        meas-add-notes value (cleared after an add).

    Notes:
        A boundary (COD-4-04). Raises PreventUpdate without a click. The player is the
        picker's value, never current_player_id() at callback time.
    """
    if not n_clicks:
        raise PreventUpdate
    unchanged = (no_update, no_update, no_update, no_update)
    today = _clock_date(clock)
    player_id = parse_player_value(player)
    if today is None:
        return (*unchanged, MESSAGE_NO_DATE, FEEDBACK_ERROR_COLOR, no_update, no_update)
    if player_id is None:
        ref = new_reference_code()
        logger.error("Add measurement refused (ref %s): meas-player is not a player id", ref)
        failure = MESSAGE_SAVE_FAILED.format(ref=ref)
        return (*unchanged, failure, FEEDBACK_ERROR_COLOR, no_update, no_update)
    try:
        session = data_handler.get_session()
        tests = list_physical_tests(session)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Measurement form check failed (ref %s)", ref)
        failure = MESSAGE_SAVE_FAILED.format(ref=ref)
        return (*unchanged, failure, FEEDBACK_ERROR_COLOR, no_update, no_update)
    form, problem = parse_measurement_form(test, measured_on, value, notes, tests=tests, today=today)
    if form is None:
        return (*unchanged, problem or "", FEEDBACK_ERROR_COLOR, no_update, no_update)
    checked: MeasurementForm = form
    measurement_player_id: int = player_id

    def work(write_session: Session) -> int:
        """
        Description:
            Locks the live profile, adds the measurement and refreshes the goal statuses.

        Parameters:
            write_session: the write session.

        Return Values:
            int: the new physical_measurement_id.

        Notes:
            A removed profile raises LookupError, so nothing is written (D14).
        """
        if not lock_live_player(write_session, measurement_player_id):
            raise LookupError(f"player {measurement_player_id} is not live")
        new_id = insert_measurement(
            write_session,
            measurement_player_id,
            checked.physical_test_id,
            checked.measured_on,
            checked.measured_value,
            checked.notes,
        )
        refresh_goal_statuses(write_session, today, measurement_player_id)
        return new_id

    result = run_write(current_user.user_id, work, "add measurement")
    if not result.ok:
        failure = MESSAGE_SAVE_FAILED.format(ref=result.ref)
        return (*unchanged, failure, FEEDBACK_ERROR_COLOR, no_update, no_update)
    logger.info("Added measurement %s for player %s", result.value, measurement_player_id)
    test_row = next(t for t in tests if int(t["physical_test_id"]) == checked.physical_test_id)
    saved = MESSAGE_SAVED_MEASUREMENT.format(
        test=test_row["test_name"],
        value=format_measurement_value(checked.measured_value, test_row["unit"]),
    )
    try:
        view = _player_view(data_handler.get_session(), measurement_player_id, checked.physical_test_id)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Measurements reload failed (ref %s)", ref)
        return (*unchanged, saved, FEEDBACK_OK_COLOR, "", "")
    return (*view, saved, FEEDBACK_OK_COLOR, "", "")


def save_measurement(
    n_clicks: int | None,
    measurement_store: object,
    test: str | None,
    measured_on: str | None,
    value: object,
    notes: str | None,
    clock: object,
) -> tuple[str, str]:
    """
    Description:
        Save on the measurement page: checks the form and saves it, then refreshes the
        measurement's player's goal statuses in the same transaction.

    Parameters:
        n_clicks: meas-edit-save.n_clicks.
        measurement_store: meas-edit-id.data (written after get_measurement).
        test: meas-edit-test.value.
        measured_on: meas-edit-date.value.
        value: meas-edit-value.value.
        notes: meas-edit-notes.value.
        clock: device-clock.data (today, REQ-055).

    Return Values:
        tuple[str, str]: meas-edit-saved ("Saved." or "") and meas-edit-feedback.

    Notes:
        A boundary (COD-4-04). The player comes from the row (update_measurement returns
        it).
    """
    if not n_clicks:
        raise PreventUpdate
    measurement_id = _stored_id(measurement_store)
    today = _clock_date(clock)
    if today is None:
        return "", MESSAGE_NO_DATE
    if measurement_id is None:
        ref = new_reference_code()
        logger.error("Save measurement refused (ref %s): meas-edit-id is not an id", ref)
        return "", MESSAGE_SAVE_FAILED.format(ref=ref)
    try:
        tests = list_physical_tests(data_handler.get_session())
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Measurement form check failed (ref %s)", ref)
        return "", MESSAGE_SAVE_FAILED.format(ref=ref)
    form, problem = parse_measurement_form(test, measured_on, value, notes, tests=tests, today=today)
    if form is None:
        return "", problem or ""
    checked: MeasurementForm = form
    edited_id: int = measurement_id

    def work(write_session: Session) -> None:
        """
        Description:
            Saves the measurement and refreshes its player's goal statuses.

        Parameters:
            write_session: the write session.

        Return Values:
            None.

        Notes:
            A measurement, profile or test that is no longer live raises LookupError.
        """
        player_id = update_measurement(
            write_session,
            edited_id,
            checked.physical_test_id,
            checked.measured_on,
            checked.measured_value,
            checked.notes,
        )
        if player_id is None:
            raise LookupError(f"measurement {edited_id} is not live")
        refresh_goal_statuses(write_session, today, player_id)

    result = run_write(current_user.user_id, work, "save measurement")
    if not result.ok:
        return "", MESSAGE_SAVE_FAILED.format(ref=result.ref)
    logger.info("Saved measurement %s", edited_id)
    return MESSAGE_SAVED, ""


def open_delete_measurement(
    n_clicks: int | None, measurement_store: object
) -> tuple[bool, str, str]:
    """
    Description:
        Delete measurement: opens the confirmation (REQ-005) naming the measurement.

    Parameters:
        n_clicks: meas-delete-open.n_clicks.
        measurement_store: meas-edit-id.data.

    Return Values:
        tuple[bool, str, str]: meas-delete-modal.opened (True), meas-delete-text and
        meas-delete-feedback.

    Notes:
        A boundary (COD-4-04): a failed read still opens the dialog with a reference.
    """
    if not n_clicks:
        raise PreventUpdate
    try:
        measurement_id = _stored_id(measurement_store)
        session = data_handler.get_session()
        row = get_measurement(session, measurement_id) if measurement_id is not None else None
        if row is None:
            raise LookupError("measurement is not live")
        days = get_retention_days(session)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Measurement delete dialog failed (ref %s)", ref)
        return True, "", MESSAGE_DELETE_FAILED.format(ref=ref)
    question = MESSAGE_DELETE_QUESTION.format(
        test=row["test_name"],
        value=format_measurement_value(row["measured_value"], row["unit"]),
        date=format_measurement_date(row["measured_on"]),
        days=days,
    )
    return True, question, ""


def confirm_delete_measurement(
    n_clicks: int | None, measurement_store: object, clock: object
) -> tuple[object, str]:
    """
    Description:
        The dialog's Delete: deletes the measurement, refreshes its player's goal statuses
        and returns to Measurements.

    Parameters:
        n_clicks: meas-delete-confirm.n_clicks.
        measurement_store: meas-edit-id.data.
        clock: device-clock.data (today; the refresh is skipped while it is unknown).

    Return Values:
        tuple[object, str]: url.pathname ("/measurements" on success, else no_update) and
        meas-delete-feedback text.

    Notes:
        A boundary (COD-4-04). Raises PreventUpdate without a click (router insertion trap).
    """
    if not n_clicks:
        raise PreventUpdate
    measurement_id = _stored_id(measurement_store)
    if measurement_id is None:
        ref = new_reference_code()
        logger.error("Delete measurement refused (ref %s): meas-edit-id is not an id", ref)
        return no_update, MESSAGE_DELETE_FAILED.format(ref=ref)
    deleted_id: int = measurement_id
    today = _clock_date(clock)

    def work(write_session: Session) -> None:
        """
        Description:
            Deletes the measurement and refreshes its player's goal statuses.

        Parameters:
            write_session: the write session.

        Return Values:
            None.

        Notes:
            None.
        """
        player_id = delete_measurement(write_session, deleted_id)
        if today is not None:
            refresh_goal_statuses(write_session, today, player_id)

    result = run_write(current_user.user_id, work, "delete measurement")
    if not result.ok:
        return no_update, MESSAGE_DELETE_FAILED.format(ref=result.ref)
    logger.info("Deleted measurement %s", deleted_id)
    return MEASUREMENTS_PATH, ""


# --- Physical test callbacks (REQ-034, D12, REQ-005) ---

def _reload_test_rows() -> object:
    """
    Description:
        Re-reads the physical tests for the list after a write.

    Parameters:
        None.

    Return Values:
        object: the ptest-rows children, or no_update when the read fails (logged with a
        reference).

    Notes:
        A boundary helper: the write already succeeded.
    """
    try:
        return physical_test_rows(list_physical_tests(data_handler.get_session()))
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Physical tests reload failed (ref %s)", ref)
        return no_update


def _failure_message(reason: str | None, ref: str | None, template: str) -> str:
    """
    Description:
        The text a failed write shows: the database's reason when there is one, else the
        template with the reference.

    Parameters:
        reason: the message of run_write_with_reason, or None.
        ref: the reference code.
        template: a message with a "{ref}" placeholder.

    Return Values:
        str: the message.

    Notes:
        Pure.
    """
    return reason if reason else template.format(ref=ref)


def add_test(
    n_clicks: int | None, name: str | None, unit: str | None, lower: bool | None
) -> tuple[object, str, object]:
    """
    Description:
        Add test: checks the form, adds the test at the end of the list and rebuilds it.

    Parameters:
        n_clicks: ptest-add.n_clicks.
        name: ptest-new-name.value.
        unit: ptest-new-unit.value.
        lower: ptest-new-lower.checked.

    Return Values:
        tuple[object, str, object]: ptest-rows children (no_update unless added),
        ptest-add-feedback text and ptest-new-name value ("" after an add).

    Notes:
        A boundary (COD-4-04). A duplicate name shows "A physical test with this name
        already exists." (ux_physical_tests_name).
    """
    if not n_clicks:
        raise PreventUpdate
    form, problem = parse_test_form(name, unit, lower)
    if form is None:
        return no_update, problem or "", no_update
    checked: TestForm = form
    result, reason = run_write_with_reason(
        current_user.user_id,
        lambda session: insert_physical_test(
            session, checked.test_name, checked.unit, checked.lower_is_better
        ),
        "add physical test",
        PHYSICAL_TEST_CONSTRAINT_MESSAGES,
    )
    if not result.ok:
        return no_update, _failure_message(reason, result.ref, MESSAGE_TEST_SAVE_FAILED), no_update
    logger.info("Added physical test %s", result.value)
    return _reload_test_rows(), "", ""


def save_test(
    n_clicks: int | None, name: str | None, unit: str | None, lower: bool | None
) -> tuple[str, str]:
    """
    Description:
        A test row's Save: renames the test, and changes its unit or direction only while
        no live measurement or goal uses it (D12).

    Parameters:
        n_clicks: {"type": "ptest-save", "index": MATCH}.n_clicks.
        name: the row's ptest-name value.
        unit: the row's ptest-unit value.
        lower: the row's ptest-lower checked value.

    Return Values:
        tuple[str, str]: the row's ptest-row-feedback text and colour.

    Notes:
        A boundary (COD-4-04). The use count is read under lock_physical_test in the write
        transaction; the update statement re-checks it (D18).
    """
    if not n_clicks:
        raise PreventUpdate
    triggered = ctx.triggered_id
    if not isinstance(triggered, dict) or not isinstance(triggered.get("index"), int):
        raise PreventUpdate
    test_id: int = triggered["index"]
    form, problem = parse_test_form(name, unit, lower)
    if form is None:
        return problem or "", FEEDBACK_ERROR_COLOR
    checked: TestForm = form

    def work(session: Session) -> bool:
        """
        Description:
            Locks the test and saves it unless a unit or direction change meets a used
            test.

        Parameters:
            session: the write session.

        Return Values:
            bool: True when saved; False when refused by D12 (nothing written).

        Notes:
            A test gone meanwhile raises LookupError.
        """
        locked = lock_physical_test(session, test_id)
        if locked is None:
            raise LookupError(f"physical test {test_id} is not live")
        kind_changed = (
            locked["unit"] != checked.unit
            or bool(locked["lower_is_better"]) != checked.lower_is_better
        )
        if kind_changed and int(locked["use_count"]) > 0:
            return False
        if not update_physical_test(
            session, test_id, checked.test_name, checked.unit, checked.lower_is_better
        ):
            raise LookupError(f"physical test {test_id} could not be updated")
        return True

    result, reason = run_write_with_reason(
        current_user.user_id, work, "save physical test", PHYSICAL_TEST_CONSTRAINT_MESSAGES
    )
    if not result.ok:
        return _failure_message(reason, result.ref, MESSAGE_TEST_SAVE_FAILED), FEEDBACK_ERROR_COLOR
    if not result.value:
        return MESSAGE_TEST_USED, FEEDBACK_ERROR_COLOR
    logger.info("Saved physical test %s", test_id)
    return MESSAGE_SAVED, FEEDBACK_OK_COLOR


def open_delete_test(clicks: list) -> tuple[object, object, object, str]:
    """
    Description:
        A test row's Delete: opens the confirmation (REQ-005) with the test's name and, for
        a used test, how often it is used.

    Parameters:
        clicks: every {"type": "ptest-delete"} n_clicks (only the trigger matters).

    Return Values:
        tuple[object, object, object, str]: ptest-del-modal.opened, ptest-del-text,
        ptest-del-target.data (the physical_test_id) and ptest-del-feedback ("").

    Notes:
        A boundary (COD-4-04). Raises PreventUpdate unless a Delete button was really
        clicked.
    """
    test_id = _clicked_index("ptest-delete")
    try:
        tests = list_physical_tests(data_handler.get_session())
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Physical test lookup failed (ref %s)", ref)
        return True, MESSAGE_LOAD_FAILED.format(ref=ref), None, ""
    match = next((t for t in tests if int(t["physical_test_id"]) == test_id), None)
    if match is None:
        return True, MESSAGE_TEST_GONE, None, ""
    question = MESSAGE_TEST_DELETE_QUESTION.format(name=match["test_name"])
    if int(match["use_count"]) > 0:
        question += MESSAGE_TEST_IN_USE.format(count=int(match["use_count"]))
    return True, question, test_id, ""


def confirm_delete_test(n_clicks: int | None, target: object) -> tuple[object, object, str]:
    """
    Description:
        The dialog's Delete: deletes the test through fn_soft_delete('physical_test'),
        which refuses a used test with its own message.

    Parameters:
        n_clicks: ptest-del-confirm.n_clicks.
        target: ptest-del-target.data, written by open_delete_test.

    Return Values:
        tuple[object, object, str]: ptest-rows children (rebuilt on success),
        ptest-del-modal.opened (False on success) and ptest-del-feedback text (the
        database's refusal or a reference).

    Notes:
        A boundary (COD-4-04).
    """
    if not n_clicks:
        raise PreventUpdate
    test_id = _stored_id(target)
    if test_id is None:
        return no_update, no_update, MESSAGE_TEST_GONE
    deleted_id: int = test_id
    result, reason = run_write_with_reason(
        current_user.user_id,
        lambda session: delete_physical_test(session, deleted_id),
        "delete physical test",
    )
    if not result.ok:
        return no_update, no_update, _failure_message(reason, result.ref, MESSAGE_DELETE_FAILED)
    logger.info("Deleted physical test %s", deleted_id)
    return _reload_test_rows(), False, ""


# --- Registration ---

def _register_measurement_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the Measurements screen and measurement page callbacks.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        select_meas_player is the first writer of meas-latest, meas-chart-test.value,
        meas-chart.figure and meas-history; later writers use allow_duplicate.
    """
    app.callback(
        Output("meas-latest", "children"),
        Output("meas-chart-test", "value"),
        Output("meas-chart", "figure"),
        Output("meas-history", "children"),
        Input("meas-player", "value"),
        prevent_initial_call=True,
    )(select_meas_player)

    app.callback(
        Output("meas-chart", "figure", allow_duplicate=True),
        Output("meas-history", "children", allow_duplicate=True),
        Input("meas-chart-test", "value"),
        State("meas-player", "value"),
        prevent_initial_call=True,
    )(show_meas_test)

    app.callback(
        Output("meas-latest", "children", allow_duplicate=True),
        Output("meas-chart-test", "value", allow_duplicate=True),
        Output("meas-chart", "figure", allow_duplicate=True),
        Output("meas-history", "children", allow_duplicate=True),
        Output("meas-add-feedback", "children"),
        Output("meas-add-feedback", "c"),
        Output("meas-add-value", "value"),
        Output("meas-add-notes", "value"),
        Input("meas-add-submit", "n_clicks"),
        State("meas-player", "value"),
        State("meas-add-test", "value"),
        State("meas-add-date", "value"),
        State("meas-add-value", "value"),
        State("meas-add-notes", "value"),
        State("device-clock", "data"),
        prevent_initial_call=True,
    )(add_measurement)

    app.callback(
        Output("meas-edit-saved", "children"),
        Output("meas-edit-feedback", "children"),
        Input("meas-edit-save", "n_clicks"),
        State("meas-edit-id", "data"),
        State("meas-edit-test", "value"),
        State("meas-edit-date", "value"),
        State("meas-edit-value", "value"),
        State("meas-edit-notes", "value"),
        State("device-clock", "data"),
        prevent_initial_call=True,
    )(save_measurement)

    app.callback(
        Output("meas-delete-modal", "opened"),
        Output("meas-delete-text", "children"),
        Output("meas-delete-feedback", "children"),
        Input("meas-delete-open", "n_clicks"),
        State("meas-edit-id", "data"),
        prevent_initial_call=True,
    )(open_delete_measurement)

    app.clientside_callback(
        ClientsideFunction("hoopster", "close_modal"),
        Output("meas-delete-modal", "opened", allow_duplicate=True),
        Input("meas-delete-cancel", "n_clicks"),
        prevent_initial_call=True,
    )

    app.callback(
        Output("url", "pathname", allow_duplicate=True),
        Output("meas-delete-feedback", "children", allow_duplicate=True),
        Input("meas-delete-confirm", "n_clicks"),
        State("meas-edit-id", "data"),
        State("device-clock", "data"),
        prevent_initial_call=True,
    )(confirm_delete_measurement)


def _register_test_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the Physical tests callbacks: Add, the row Save, and the delete dialog's
        open, Cancel (clientside) and Delete.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        ptest-rows and the dialog's opened and feedback are first written by add_test and
        open_delete_test; later writers use allow_duplicate.
    """
    app.callback(
        Output("ptest-rows", "children"),
        Output("ptest-add-feedback", "children"),
        Output("ptest-new-name", "value"),
        Input("ptest-add", "n_clicks"),
        State("ptest-new-name", "value"),
        State("ptest-new-unit", "value"),
        State("ptest-new-lower", "checked"),
        prevent_initial_call=True,
    )(add_test)

    app.callback(
        Output({"type": "ptest-row-feedback", "index": MATCH}, "children"),
        Output({"type": "ptest-row-feedback", "index": MATCH}, "c"),
        Input({"type": "ptest-save", "index": MATCH}, "n_clicks"),
        State({"type": "ptest-name", "index": MATCH}, "value"),
        State({"type": "ptest-unit", "index": MATCH}, "value"),
        State({"type": "ptest-lower", "index": MATCH}, "checked"),
        prevent_initial_call=True,
    )(save_test)

    app.callback(
        Output("ptest-del-modal", "opened"),
        Output("ptest-del-text", "children"),
        Output("ptest-del-target", "data"),
        Output("ptest-del-feedback", "children"),
        Input({"type": "ptest-delete", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )(open_delete_test)

    app.clientside_callback(
        ClientsideFunction("hoopster", "close_modal"),
        Output("ptest-del-modal", "opened", allow_duplicate=True),
        Input("ptest-del-cancel", "n_clicks"),
        prevent_initial_call=True,
    )

    app.callback(
        Output("ptest-rows", "children", allow_duplicate=True),
        Output("ptest-del-modal", "opened", allow_duplicate=True),
        Output("ptest-del-feedback", "children", allow_duplicate=True),
        Input("ptest-del-confirm", "n_clicks"),
        State("ptest-del-target", "data"),
        prevent_initial_call=True,
    )(confirm_delete_test)


def register_measurements_callbacks(app: Dash) -> None:
    """
    Description:
        Registers every Measurements and Physical tests callback.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        url.pathname is written by several callbacks, so confirm_delete_measurement uses
        allow_duplicate and ignores runs without a click (router insertion trap,
        notes_for_agents §8).
    """
    _register_measurement_callbacks(app)
    _register_test_callbacks(app)
