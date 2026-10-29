"""Settings controller of Hoopster: the hub, Recently deleted with Restore, Export data, Retention
period, About and the user manual (REQ-006, REQ-043, REQ-071; task 2026-10-23_settings_hub).

The restored item comes from the clicked button's pattern index, checked against the entity
whitelist and against the live h_data.v_recently_deleted (Context Adaptation Table); the
exported table only from data_settings.EXPORT_TABLES. Every write goes through
app_controller.run_write (run_write_with_reason when the database's own refusal is shown).
Every callback is a user-facing boundary (COD-4-04).
"""

import csv
import io
import logging
import re
import zipfile
from datetime import date, datetime

from dash import ALL, Dash, Input, Output, State, ctx, dcc, no_update
from dash.development.base_component import Component
from dash.exceptions import PreventUpdate
from flask_login import current_user

from config.app_config import (
    PROJECT_ROOT,
    RECENTLY_DELETED_LIMIT,
    RETENTION_DAYS_MAX,
    RETENTION_DAYS_MIN,
    TEST_DATA_MARKER,
    USER_MANUAL_PATH,
)
from src import data_handler
from src.app_controller import (
    DeviceClock,
    log_failure,
    new_reference_code,
    normalise_clock,
    run_write,
    run_write_with_reason,
)
from src.data_settings import (
    DELETED_KIND_ALL,
    DELETED_KIND_ENTITIES,
    EXPORT_TABLE_SET,
    EXPORT_TABLES,
    RESTORABLE_ENTITIES,
    export_table_rows,
    get_database_status,
    get_listed_deleted,
    get_retention_days,
    list_recently_deleted,
    restore_item,
    set_retention_days,
)
from src.layouts_settings import (
    about_layout,
    deleted_count_text,
    deleted_items,
    deleted_layout,
    export_layout,
    manual_layout,
    retention_layout,
    settings_hub_layout,
)

logger = logging.getLogger(__name__)

MESSAGE_DELETED_LOAD_FAILED = "Could not load Recently deleted. Reference: {ref}"
MESSAGE_NOT_LISTED = "This item is no longer in Recently deleted."
MESSAGE_RESTORED = "Restored: {label}."
MESSAGE_RESTORE_REFUSED = "Could not restore {label}: {reason}"
MESSAGE_RESTORE_FAILED = "Could not restore {label}. Reference: {ref}"
MESSAGE_EXPORT_FAILED = "Could not export the data. Reference: {ref}"
MESSAGE_CHOOSE_TABLE = "Choose a table."
MESSAGE_RETENTION_RANGE = (
    f"Enter a whole number of days from {RETENTION_DAYS_MIN} to {RETENTION_DAYS_MAX}."
)
MESSAGE_RETENTION_FAILED = "Could not save the retention period. Reference: {ref}"
MESSAGE_SAVED = "Saved."

# "entity:id" of a Restore button: an entity name, then 1 to 18 digits (fits bigint).
ENTITY_KEY_PATTERN = re.compile(r"([a-z_]{1,40}):([0-9]{1,18})")
# Longest search text used; longer input is cut (labels are at most a few hundred characters).
SEARCH_MAX_LENGTH = 100
EXPORT_ZIP_NAME = "hoopster-export-{day}.zip"
EXPORT_CSV_NAME = "{table}-{day}.csv"
EXPORT_DAY_UNKNOWN = "today"
CSV_TRUE = "true"
CSV_FALSE = "false"
CSV_ENCODING = "utf-8-sig"


# --- Page builders ---

def build_settings_page(
    player_id: int | None, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds the Settings hub.

    Parameters:
        player_id: the selected player (unused; Settings needs no player).
        clock: the device clock (unused).
        params: the route parameters (none).

    Return Values:
        Component: the page.

    Notes:
        None.
    """
    return settings_hub_layout()


def build_deleted_page(
    player_id: int | None, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds Recently deleted with the default filters: all kinds, no search, test data
        hidden.

    Parameters:
        player_id: the selected player (unused; works with no live player).
        clock: the device clock (its zone formats the times).
        params: the route parameters (none).

    Return Values:
        Component: the page.

    Notes:
        Exceptions propagate to the router, which shows the error page with a reference.
    """
    session = data_handler.get_session()
    items, total = list_recently_deleted(
        session,
        DELETED_KIND_ENTITIES[DELETED_KIND_ALL],
        None,
        TEST_DATA_MARKER,
        RECENTLY_DELETED_LIMIT,
    )
    return deleted_layout(items, total, get_retention_days(session), clock["zone"])


def build_export_page(
    player_id: int | None, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds Export data.

    Parameters:
        player_id: the selected player (unused).
        clock: the device clock (unused).
        params: the route parameters (none).

    Return Values:
        Component: the page.

    Notes:
        None.
    """
    return export_layout(list(EXPORT_TABLES))


def build_retention_page(
    player_id: int | None, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds Retention period with the current value.

    Parameters:
        player_id: the selected player (unused).
        clock: the device clock (unused).
        params: the route parameters (none).

    Return Values:
        Component: the page.

    Notes:
        Exceptions propagate to the router.
    """
    return retention_layout(get_retention_days(data_handler.get_session()))


def build_about_page(
    player_id: int | None, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds About: version, database status and the signed-in user.

    Parameters:
        player_id: the selected player (unused).
        clock: the device clock (unused).
        params: the route parameters (none).

    Return Values:
        Component: the page; a failed database read still shows the page, with "not
        reachable" and a reference.

    Notes:
        A boundary for the status read only (COD-4-04).
    """
    info: dict[str, object] = {"user_name": current_user.display_name, "error_ref": None}
    try:
        info.update(get_database_status(data_handler.get_session()))
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Database status read failed (ref %s)", ref)
        info["error_ref"] = ref
    return about_layout(info)


def read_user_manual() -> str | None:
    """
    Description:
        Reads the user manual file (USER_MANUAL_PATH under the project root).

    Parameters:
        None.

    Return Values:
        str | None: the Markdown text, or None when the file is missing or unreadable
        (e.g. a server without the .agent folder).

    Notes:
        The path is a constant, not external input. A missing manual is logged as a
        warning: the page says it is not available and nothing else is affected.
    """
    path = PROJECT_ROOT / USER_MANUAL_PATH
    try:
        with open(path, encoding="utf-8") as manual_file:
            return manual_file.read()
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning(
            "User manual not readable at %s: %s", USER_MANUAL_PATH, type(exc).__name__
        )
        return None


def build_manual_page(
    player_id: int | None, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds the User manual page.

    Parameters:
        player_id: the selected player (unused).
        clock: the device clock (unused).
        params: the route parameters (none).

    Return Values:
        Component: the page.

    Notes:
        None.
    """
    return manual_layout(read_user_manual())


# --- Pure helpers ---

def parse_entity_key(value: object) -> tuple[str, int] | None:
    """
    Description:
        Reads a Restore button's index "entity:id".

    Parameters:
        value: the pattern index, e.g. "player:12".

    Return Values:
        tuple[str, int] | None: (entity, id), or None when the shape is wrong or the entity
        is not in RESTORABLE_ENTITIES.

    Notes:
        Pure.
    """
    if not isinstance(value, str):
        return None
    match = ENTITY_KEY_PATTERN.fullmatch(value)
    if match is None or match.group(1) not in RESTORABLE_ENTITIES:
        return None
    return match.group(1), int(match.group(2))


def parse_retention_days(value: object) -> tuple[int | None, str | None]:
    """
    Description:
        Checks a retention period: a whole number from RETENTION_DAYS_MIN to
        RETENTION_DAYS_MAX.

    Parameters:
        value: retention-days.value (int, float, text or None).

    Return Values:
        tuple[int | None, str | None]: (days, None), or (None, "Enter a whole number of
        days from 7 to 365.").

    Notes:
        Pure. A float with a fraction (7.5), a bool, blank or other text is refused.
    """
    days: int | None = None
    if isinstance(value, bool):
        days = None
    elif isinstance(value, int):
        days = value
    elif isinstance(value, float) and value.is_integer():
        days = int(value)
    elif isinstance(value, str) and value.strip().isdigit() and value.strip().isascii():
        days = int(value.strip())
    if days is None or not RETENTION_DAYS_MIN <= days <= RETENTION_DAYS_MAX:
        return None, MESSAGE_RETENTION_RANGE
    return days, None


def _csv_value(value: object) -> str:
    """
    Description:
        One CSV cell: None as empty, dates and times as ISO 8601, booleans as true / false,
        anything else as its text.

    Parameters:
        value: the database value.

    Return Values:
        str: the cell text.

    Notes:
        Pure. bool is checked before anything else because it is an int subclass.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return CSV_TRUE if value else CSV_FALSE
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def csv_bytes(columns: list[str], rows: list[tuple[object, ...]]) -> bytes:
    """
    Description:
        One table as CSV: a header row, then one line per row.

    Parameters:
        columns: the column names.
        rows: the rows.

    Return Values:
        bytes: UTF-8 with a byte-order mark (so spreadsheet programs read accents), CRLF
        line ends.

    Notes:
        Pure. Values starting with "=" are kept as they are: the export is a backup
        (plan §9).
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    writer.writerows([_csv_value(value) for value in row] for row in rows)
    return buffer.getvalue().encode(CSV_ENCODING)


def export_zip(tables: list[tuple[str, list[str], list[tuple[object, ...]]]]) -> bytes:
    """
    Description:
        Packs tables into one ZIP, one "<schema>.<table>.csv" per table.

    Parameters:
        tables: (schema-qualified name, columns, rows) per table.

    Return Values:
        bytes: the ZIP file (deflated).

    Notes:
        Pure; built in memory, nothing is written to disk (COD-20-04).
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, columns, rows in tables:
            archive.writestr(f"{name}.csv", csv_bytes(columns, rows))
    return buffer.getvalue()


def _export_day(clock: object) -> str:
    """
    Description:
        The device's date for an export file name.

    Parameters:
        clock: device-clock.data.

    Return Values:
        str: "YYYY-MM-DD", or "today" when the device date is unknown.

    Notes:
        Pure. The device's date, never the server's (REQ-055).
    """
    return normalise_clock(clock)["today"] or EXPORT_DAY_UNKNOWN


def _search_text(value: object) -> str | None:
    """
    Description:
        The search box value to filter by.

    Parameters:
        value: deleted-search.value.

    Return Values:
        str | None: the stripped text (at most SEARCH_MAX_LENGTH characters), or None when
        empty.

    Notes:
        Pure.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:SEARCH_MAX_LENGTH]


# --- Recently deleted callbacks (REQ-006, D10, D11) ---

def _deleted_view(
    kind: object, search: object, hide_test: object, clock: object
) -> tuple[list[Component], str]:
    """
    Description:
        Reads Recently deleted for the filters and builds the list and the count line.

    Parameters:
        kind: deleted-kind.value (an unknown kind means "all").
        search: deleted-search.value.
        hide_test: deleted-hide-test.checked.
        clock: device-clock.data (zone for the times).

    Return Values:
        tuple[list[Component], str]: deleted-list children and deleted-count text.

    Notes:
        Raises on a failed read; the callers are the boundaries.
    """
    entities = DELETED_KIND_ENTITIES.get(
        kind if isinstance(kind, str) else DELETED_KIND_ALL,
        DELETED_KIND_ENTITIES[DELETED_KIND_ALL],
    )
    items, total = list_recently_deleted(
        data_handler.get_session(),
        entities,
        _search_text(search),
        TEST_DATA_MARKER if hide_test else None,
        RECENTLY_DELETED_LIMIT,
    )
    rows = deleted_items(items, normalise_clock(clock)["zone"])
    return rows, deleted_count_text(len(items), total)


def refresh_deleted(
    kind: object, search: object, hide_test: object, clock: object
) -> tuple[object, object, str]:
    """
    Description:
        Rebuilds Recently deleted when a filter changes.

    Parameters:
        kind: deleted-kind.value.
        search: deleted-search.value.
        hide_test: deleted-hide-test.checked.
        clock: device-clock.data.

    Return Values:
        tuple[object, object, str]: deleted-list children, deleted-count text and
        deleted-feedback text.

    Notes:
        A boundary (COD-4-04): a failed read keeps the list and shows a reference.
    """
    try:
        rows, count_text = _deleted_view(kind, search, hide_test, clock)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Recently deleted read failed (ref %s)", ref)
        return no_update, no_update, MESSAGE_DELETED_LOAD_FAILED.format(ref=ref)
    return rows, count_text, ""


def _clicked_restore_key() -> tuple[str, int] | None:
    """
    Description:
        The item of the Restore button that was really clicked.

    Parameters:
        None.

    Return Values:
        tuple[str, int] | None: (entity, id) of a clicked Restore button.

    Notes:
        Raises PreventUpdate for a run without a click: a rebuilt list fires ALL callbacks
        with n_clicks None (router insertion trap, notes_for_agents §8).
    """
    triggered = ctx.triggered_id
    if not isinstance(triggered, dict) or triggered.get("type") != "deleted-restore":
        raise PreventUpdate
    if not ctx.triggered or not ctx.triggered[0].get("value"):
        raise PreventUpdate
    return parse_entity_key(triggered.get("index"))


def restore_deleted(
    n_clicks: list[int | None],
    kind: object,
    search: object,
    hide_test: object,
    clock: object,
) -> tuple[object, object, str, str]:
    """
    Description:
        Restore on a Recently deleted row: checks the item is still listed, restores it
        through h_data.fn_restore and rebuilds the list. No confirmation (D10).

    Parameters:
        n_clicks: the n_clicks of every Restore button (ALL).
        kind: deleted-kind.value.
        search: deleted-search.value.
        hide_test: deleted-hide-test.checked.
        clock: device-clock.data.

    Return Values:
        tuple[object, object, str, str]: deleted-list children, deleted-count text,
        deleted-feedback text and deleted-status text.

    Notes:
        A boundary (COD-4-04). The item comes from the clicked button's index (whitelisted
        entity, then get_listed_deleted); the acting user is current_user.user_id. A refusal
        by fn_restore (SQLSTATE P0001) is shown in the database's own words.
    """
    key = _clicked_restore_key()
    if key is None:
        ref = new_reference_code()
        logger.error("Restore refused (ref %s): the button index is not an item key", ref)
        return no_update, no_update, MESSAGE_DELETED_LOAD_FAILED.format(ref=ref), ""
    entity, entity_id = key
    try:
        label = get_listed_deleted(data_handler.get_session(), entity, entity_id)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Restore check failed (ref %s)", ref)
        return no_update, no_update, MESSAGE_DELETED_LOAD_FAILED.format(ref=ref), ""
    if label is None:
        feedback, status = MESSAGE_NOT_LISTED, ""
    else:
        result, reason = run_write_with_reason(
            current_user.user_id,
            lambda session: restore_item(session, entity, entity_id),
            "restore item",
        )
        if result.ok:
            logger.info("Restored %s %s (%s rows)", entity, entity_id, result.value)
            feedback, status = "", MESSAGE_RESTORED.format(label=label)
        elif reason is not None:
            feedback, status = MESSAGE_RESTORE_REFUSED.format(label=label, reason=reason), ""
        else:
            feedback, status = MESSAGE_RESTORE_FAILED.format(label=label, ref=result.ref), ""
    try:
        rows, count_text = _deleted_view(kind, search, hide_test, clock)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Recently deleted reload failed (ref %s)", ref)
        return no_update, no_update, MESSAGE_DELETED_LOAD_FAILED.format(ref=ref), status
    return rows, count_text, feedback, status


# --- Export callbacks (REQ-043, D13) ---

def export_all(n_clicks: int | None, clock: object) -> tuple[object, str]:
    """
    Description:
        Export all: every EXPORT_TABLES table as one CSV each, in one ZIP download.

    Parameters:
        n_clicks: export-all.n_clicks.
        clock: device-clock.data (the date in the file name).

    Return Values:
        tuple[object, str]: export-download data (dcc.send_bytes) and export-feedback text.

    Notes:
        A boundary (COD-4-04). Logs the table and row counts only, never the data. The
        tables are read one after another (plan §9: a write meanwhile can make two CSVs
        disagree by that write).
    """
    if not n_clicks:
        raise PreventUpdate
    tables: list[tuple[str, list[str], list[tuple[object, ...]]]] = []
    try:
        session = data_handler.get_session()
        # One SELECT per table is the batch here: each table has its own columns.
        for table in EXPORT_TABLES:
            columns, rows = export_table_rows(session, table)
            tables.append((table, columns, rows))
        content = export_zip(tables)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Export all failed (ref %s)", ref)
        return no_update, MESSAGE_EXPORT_FAILED.format(ref=ref)
    logger.info(
        "Exported %s tables, %s rows, for user %s",
        len(tables),
        sum(len(rows) for _, _, rows in tables),
        current_user.user_id,
    )
    return dcc.send_bytes(content, EXPORT_ZIP_NAME.format(day=_export_day(clock))), ""


def export_one(n_clicks: int | None, table: object, clock: object) -> tuple[object, str]:
    """
    Description:
        Download CSV: one table.

    Parameters:
        n_clicks: export-table-download.n_clicks.
        table: export-table.value, accepted only when it is in EXPORT_TABLES.
        clock: device-clock.data (the date in the file name).

    Return Values:
        tuple[object, str]: export-download data and export-feedback text ("Choose a
        table." without a valid choice).

    Notes:
        A boundary (COD-4-04).
    """
    if not n_clicks:
        raise PreventUpdate
    if not isinstance(table, str) or table not in EXPORT_TABLE_SET:
        return no_update, MESSAGE_CHOOSE_TABLE
    try:
        columns, rows = export_table_rows(data_handler.get_session(), table)
        content = csv_bytes(columns, rows)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Export of %s failed (ref %s)", table, ref)
        return no_update, MESSAGE_EXPORT_FAILED.format(ref=ref)
    logger.info("Exported %s (%s rows) for user %s", table, len(rows), current_user.user_id)
    file_name = EXPORT_CSV_NAME.format(table=table, day=_export_day(clock))
    return dcc.send_bytes(content, file_name), ""


# --- Retention callback (REQ-006, D12) ---

def save_retention(n_clicks: int | None, value: object) -> tuple[str, str]:
    """
    Description:
        Save on Retention period.

    Parameters:
        n_clicks: retention-save.n_clicks.
        value: retention-days.value.

    Return Values:
        tuple[str, str]: retention-saved text ("Saved." or "") and retention-feedback text.

    Notes:
        A boundary (COD-4-04). The acting user is current_user.user_id.
    """
    if not n_clicks:
        raise PreventUpdate
    days, problem = parse_retention_days(value)
    if days is None:
        return "", problem or MESSAGE_RETENTION_RANGE
    checked_days: int = days
    result = run_write(
        current_user.user_id,
        lambda session: set_retention_days(session, checked_days),
        "save retention period",
    )
    if not result.ok:
        return "", MESSAGE_RETENTION_FAILED.format(ref=result.ref)
    logger.info("Retention period set to %s days by user %s", checked_days, current_user.user_id)
    return MESSAGE_SAVED, ""


# --- Registration ---

def register_settings_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the Settings data-page callbacks: the Recently deleted filters and
        Restore, the two exports and the retention Save.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        Every Output written by two callbacks is first written by the filter or export
        callback; the second writer uses allow_duplicate. Button callbacks ignore runs
        without a click (router insertion trap, notes_for_agents §8).
    """
    app.callback(
        Output("deleted-list", "children"),
        Output("deleted-count", "children"),
        Output("deleted-feedback", "children"),
        Input("deleted-kind", "value"),
        Input("deleted-search", "value"),
        Input("deleted-hide-test", "checked"),
        State("device-clock", "data"),
        prevent_initial_call=True,
    )(refresh_deleted)

    app.callback(
        Output("deleted-list", "children", allow_duplicate=True),
        Output("deleted-count", "children", allow_duplicate=True),
        Output("deleted-feedback", "children", allow_duplicate=True),
        Output("deleted-status", "children"),
        Input({"type": "deleted-restore", "index": ALL}, "n_clicks"),
        State("deleted-kind", "value"),
        State("deleted-search", "value"),
        State("deleted-hide-test", "checked"),
        State("device-clock", "data"),
        prevent_initial_call=True,
    )(restore_deleted)

    app.callback(
        Output("export-download", "data"),
        Output("export-feedback", "children"),
        Input("export-all", "n_clicks"),
        State("device-clock", "data"),
        prevent_initial_call=True,
    )(export_all)

    app.callback(
        Output("export-download", "data", allow_duplicate=True),
        Output("export-feedback", "children", allow_duplicate=True),
        Input("export-table-download", "n_clicks"),
        State("export-table", "value"),
        State("device-clock", "data"),
        prevent_initial_call=True,
    )(export_one)

    app.callback(
        Output("retention-saved", "children"),
        Output("retention-feedback", "children"),
        Input("retention-save", "n_clicks"),
        State("retention-days", "value"),
        prevent_initial_call=True,
    )(save_retention)
