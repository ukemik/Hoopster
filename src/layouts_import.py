"""Import page UI for Hoopster: choose the item type, upload a CSV or Excel file or paste a list,
then read the preview and Import or Discard (REQ-027, REQ-050; task 2026-10-23_drill_import).

Built only through the wrappers of src/layouts.py (COD-5-01). Layout modules import nothing
from controllers or the data layer: the item types, options and help rows arrive as plain
values. The callbacks live in src/controller_import.py.
"""

import logging

import dash_mantine_components as dmc
from dash import dcc, html
from dash.development.base_component import Component

from config.app_config import IMPORT_MAX_BYTES
from src.layouts import (
    CONTROL_SIZE,
    confirm_modal,
    form_message,
    nav_link,
    page_skeleton,
    primary_button,
    secondary_button,
)

logger = logging.getLogger(__name__)

HIDDEN_STYLE: dict[str, str] = {"display": "none"}
SHOWN_STYLE: dict[str, str] = {}
DETAIL_SEPARATOR = " · "

SOURCE_TAB_FILE = "file"
SOURCE_TAB_PASTE = "paste"
DEFAULT_ITEM_TYPE = "drill"

# h_ref.result_types.capture_kind of shooting drills.
CAPTURE_SHOTS = "shots"
# h_staging row and batch statuses the preview distinguishes.
ROW_STATUS_ERROR = "error"
BATCH_STATUS_READY = "ready"
BATCH_STATUS_HAS_ERRORS = "has_errors"

UPLOAD_TEXT = "Choose a CSV or Excel file"
UPLOAD_NOTE = "CSV or Excel (.xlsx), up to 1 MB and 500 rows. Row 1 holds the column names."
PASTE_PLACEHOLDER = "Name\nName | description"
PASTE_MIN_ROWS = 6
NO_NAME_TEXT = "(no name)"


# --- Pure helpers ---

def _plural(count: int, singular: str, plural: str) -> str:
    """
    Description:
        "1 drill" / "3 drills".

    Parameters:
        count: the number.
        singular: the word for one.
        plural: the word for several (and for zero).

    Return Values:
        str: the count and the right word.

    Notes:
        Pure.
    """
    return f"{count} {singular if count == 1 else plural}"


def paste_field_styles(
    result_type: str | None, result_meta: dict
) -> tuple[dict[str, str], dict[str, str]]:
    """
    Description:
        Which extra paste fields a result type needs: spots for a shooting type, the fixed
        time for a type that needs fixed seconds.

    Parameters:
        result_type: import-paste-result.value (a result type code), or None.
        result_meta: result type code -> {"capture_kind", "needs_fixed_seconds", ...}
            (the import-result-meta store).

    Return Values:
        tuple[dict[str, str], dict[str, str]]: the styles of import-paste-spots-section and
        import-paste-seconds-section, {} shown or {"display": "none"} hidden.

    Notes:
        Pure. Both hidden for no or an unknown result type.
    """
    facts = result_meta.get(result_type or "") if isinstance(result_meta, dict) else None
    if not isinstance(facts, dict):
        return HIDDEN_STYLE, HIDDEN_STYLE
    spots_style = SHOWN_STYLE if facts.get("capture_kind") == CAPTURE_SHOTS else HIDDEN_STYLE
    seconds_style = SHOWN_STYLE if facts.get("needs_fixed_seconds") else HIDDEN_STYLE
    return spots_style, seconds_style


def summary_text(batch: dict, item: dict) -> str:
    """
    Description:
        The summary line above the preview.

    Parameters:
        batch: data_import.get_batch: uses "batch_status", "row_count", "error_row_count".
        item: {"singular", "plural"} of the item type, e.g. "drill" / "drills".

    Return Values:
        str: ready -> "{n} drills checked: all valid. Press Import to add them.";
        has_errors -> "{n} rows checked: {e} with errors. Fix them and load again; nothing
        is imported until every row is valid."; any other status -> "".

    Notes:
        Pure. "1 drill" / "1 row" in the singular.
    """
    status = batch.get("batch_status")
    row_count = int(batch.get("row_count") or 0)
    if status == BATCH_STATUS_READY:
        counted = _plural(row_count, str(item.get("singular")), str(item.get("plural")))
        return f"{counted} checked: all valid. Press Import to add them."
    if status == BATCH_STATUS_HAS_ERRORS:
        errors = int(batch.get("error_row_count") or 0)
        return (
            f"{_plural(row_count, 'row', 'rows')} checked: {errors} with errors. Fix them and "
            "load again; nothing is imported until every row is valid."
        )
    return ""


def _row_detail(row: dict, labels: dict) -> str:
    """
    Description:
        The dimmed line of a preview row: "Type · Difficulty · Result type".

    Parameters:
        row: a data_import.list_batch_rows row.
        labels: {"difficulties": {code: label}, "result_types": {code: label}}.

    Return Values:
        str: the filled parts joined with " · " (a code shows its label, an unknown value
        shows as typed); "" when all three are empty.

    Notes:
        Pure.
    """
    difficulty_labels = labels.get("difficulties", {})
    result_labels = labels.get("result_types", {})
    parts: list[str] = []
    if row.get("primary_type"):
        parts.append(str(row["primary_type"]))
    if row.get("difficulty"):
        parts.append(str(difficulty_labels.get(row["difficulty"], row["difficulty"])))
    if row.get("result_type"):
        parts.append(str(result_labels.get(row["result_type"], row["result_type"])))
    return DETAIL_SEPARATOR.join(parts)


def preview_items(rows: list[dict], batch_status: object, labels: dict) -> list[Component]:
    """
    Description:
        The preview (REQ-050, D7): every row when the batch is valid; only the problem rows,
        with their errors, when it has errors.

    Parameters:
        rows: data_import.list_batch_rows rows, ordered by row_no.
        batch_status: the batch's status ("ready", "has_errors", ...).
        labels: {"difficulties": {code: label}, "result_types": {code: label}}.

    Return Values:
        list[Component]: one bordered dmc.Paper per row shown, id {"type": "import-row",
        "index": row_no}: "Row {row_no} · {name or '(no name)'}", the detail line, and for an
        error row its error message in red. [] for any other status.

    Notes:
        Pure.
    """
    if batch_status == BATCH_STATUS_READY:
        shown = rows
    elif batch_status == BATCH_STATUS_HAS_ERRORS:
        shown = [row for row in rows if row.get("row_status") == ROW_STATUS_ERROR]
    else:
        return []
    items: list[Component] = []
    for row in shown:
        row_no = int(row["row_no"])
        name = row.get("drill_name") or NO_NAME_TEXT
        children: list[Component] = [
            dmc.Text(f"Row {row_no}{DETAIL_SEPARATOR}{name}", fw=500),
        ]
        detail = _row_detail(row, labels)
        if detail:
            children.append(dmc.Text(detail, size="sm", c="dimmed"))
        if row.get("row_status") == ROW_STATUS_ERROR and row.get("error_message"):
            children.append(dmc.Text(str(row["error_message"]), size="sm", c="red"))
        items.append(
            dmc.Paper(
                dmc.Stack(children, gap=2),
                id={"type": "import-row", "index": row_no},
                withBorder=True,
                p="sm",
                style={"overflowWrap": "anywhere"},
            )
        )
    return items


def publish_question(count: int, item: dict) -> str:
    """
    Description:
        The question of the Import confirmation (REQ-005).

    Parameters:
        count: the number of rows to import.
        item: {"singular", "plural"} of the item type.

    Return Values:
        str: "Add {n} drills to the library? Imported drills can be edited or removed like
        any other drill." ("1 drill" in the singular).

    Notes:
        Pure.
    """
    singular = str(item.get("singular"))
    plural = str(item.get("plural"))
    return (
        f"Add {_plural(count, singular, plural)} to the library? Imported {plural} can be "
        f"edited or removed like any other {singular}."
    )


# --- Page ---

def _file_panel(help_rows: list[tuple[str, str]]) -> Component:
    """
    Description:
        The "Upload a file" tab: the upload box, its note, the two template downloads and
        the column help.

    Parameters:
        help_rows: (column, rule) per staging column.

    Return Values:
        Component: the panel's content.

    Notes:
        dcc.Upload refuses a file over IMPORT_MAX_BYTES in the browser; the server checks
        the size again. There is deliberately no accept filter: with one, the browser drops
        a wrong file type silently, so every file reaches the server check, which answers
        "Choose a .csv or .xlsx file." (drill_import plan amendment, test 070).
    """
    help_items = [
        dmc.Stack(
            [
                dmc.Text(column, fw=600, style={"fontFamily": "monospace"}),
                dmc.Text(rule, size="sm"),
            ],
            gap=0,
        )
        for column, rule in help_rows
    ]
    return dmc.Stack(
        [
            dcc.Upload(
                html.Div(UPLOAD_TEXT),
                id="import-upload",
                multiple=False,
                max_size=IMPORT_MAX_BYTES,
                className="hoopster-upload",
            ),
            dmc.Text(UPLOAD_NOTE, id="import-upload-note", size="sm", c="dimmed"),
            secondary_button("import-template-csv", "Download CSV template"),
            secondary_button("import-template-xlsx", "Download Excel template"),
            dcc.Download(id="import-download"),
            dmc.Accordion(
                dmc.AccordionItem(
                    [
                        dmc.AccordionControl("Columns and allowed values"),
                        dmc.AccordionPanel(dmc.Stack(help_items, gap="xs")),
                    ],
                    value="help",
                ),
                id="import-help",
            ),
        ],
        gap="sm",
        pt="sm",
    )


def _paste_panel(paste_options: dict) -> Component:
    """
    Description:
        The "Paste a list" tab: drill type, difficulty and result type chosen once, the
        spots or fixed time when the result type needs them, the list and Check list.

    Parameters:
        paste_options: {"drill_types", "difficulties", "result_types", "spots"}, each a list
            of {"value", "label"}: type names, difficulty codes, importable result type
            codes, spot NUMBERS as text and "FT" (never court_spot_id; plan §3 table).

    Return Values:
        Component: the panel's content.

    Notes:
        The two optional sections start hidden; show_paste_fields switches them.
    """
    return dmc.Stack(
        [
            dmc.Select(
                id="import-paste-type",
                label="Drill type",
                data=paste_options.get("drill_types", []),
                searchable=True,
                size=CONTROL_SIZE,
            ),
            dmc.Select(
                id="import-paste-difficulty",
                label="Difficulty",
                data=paste_options.get("difficulties", []),
                size=CONTROL_SIZE,
            ),
            dmc.Select(
                id="import-paste-result",
                label="Result type",
                data=paste_options.get("result_types", []),
                size=CONTROL_SIZE,
            ),
            html.Div(
                dmc.MultiSelect(
                    id="import-paste-spots",
                    label="Spots",
                    data=paste_options.get("spots", []),
                    value=[],
                    hidePickedOptions=True,
                    searchable=True,
                    size=CONTROL_SIZE,
                ),
                id="import-paste-spots-section",
                style=HIDDEN_STYLE,
            ),
            html.Div(
                dmc.NumberInput(
                    id="import-paste-seconds",
                    label="Fixed time (seconds)",
                    min=1,
                    allowDecimal=False,
                    hideControls=True,
                    size=CONTROL_SIZE,
                ),
                id="import-paste-seconds-section",
                style=HIDDEN_STYLE,
            ),
            dmc.Textarea(
                id="import-paste-text",
                label="Drills, one per line",
                placeholder=PASTE_PLACEHOLDER,
                autosize=True,
                minRows=PASTE_MIN_ROWS,
                size=CONTROL_SIZE,
            ),
            primary_button("import-paste-check", "Check list"),
        ],
        gap="sm",
        pt="sm",
    )


def _result_area(open_library_label: str) -> list[Component]:
    """
    Description:
        The shared result area under both tabs: feedback, summary, preview, Import and
        Discard, the done message, the batch store and the Import confirmation.

    Parameters:
        open_library_label: the done message's link text, e.g. "Open the drill library".

    Return Values:
        list[Component]: the components, top to bottom.

    Notes:
        import-batch holds {"import_batch_id", "item_type", "row_count"} of the previewed
        batch, written only by the load callbacks.
    """
    return [
        form_message("import-feedback"),
        dmc.Text("", id="import-summary"),
        dmc.Stack([], id="import-preview", gap="xs"),
        primary_button("import-confirm", "Import", disabled=True),
        secondary_button("import-discard", "Discard", disabled=True),
        dmc.Alert(
            dmc.Stack(
                [
                    dmc.Text("", id="import-done-text"),
                    html.Div(
                        nav_link("import-open-library", open_library_label, "/drills"),
                        className="hoopster-link-button",
                    ),
                ],
                gap="xs",
            ),
            id="import-done",
            color="green",
            style=HIDDEN_STYLE,
        ),
        dcc.Store(id="import-batch", storage_type="memory"),
        confirm_modal("import-publish", "Import?", "Import", color="orange"),
    ]


def import_layout(
    item_types: list[dict],
    paste_options: dict,
    help_rows: list[tuple[str, str]],
) -> Component:
    """
    Description:
        The Import page (REQ-050, REQ-027): the library link, the item-type select, the
        "Upload a file" / "Paste a list" tabs and the shared result area.

    Parameters:
        item_types: {"value": item type code, "label"} per registry entry
            (data_import.IMPORT_ITEM_TYPES).
        paste_options: see _paste_panel, plus "result_meta" (result type code ->
            {"capture_kind", "needs_fixed_seconds"}) for the import-result-meta store.
        help_rows: (column, rule) per staging column of the item type.

    Return Values:
        Component: the page.

    Notes:
        Drills are the only item type in version 1, so the select starts at "drill" and the
        paste tab asks drill fields.
    """
    return page_skeleton(
        "Import",
        [
            html.Div(
                nav_link("import-library-link", "‹ Drill library", "/drills"),
                className="hoopster-link-button",
            ),
            dmc.Select(
                id="import-item-type",
                label="What to import",
                data=item_types,
                value=DEFAULT_ITEM_TYPE,
                allowDeselect=False,
                clearable=False,
                size=CONTROL_SIZE,
            ),
            dmc.Tabs(
                [
                    dmc.TabsList(
                        [
                            dmc.TabsTab(
                                "Upload a file", value=SOURCE_TAB_FILE, id="import-tab-file"
                            ),
                            dmc.TabsTab(
                                "Paste a list", value=SOURCE_TAB_PASTE, id="import-tab-paste"
                            ),
                        ],
                        grow=True,
                    ),
                    dmc.TabsPanel(_file_panel(help_rows), value=SOURCE_TAB_FILE),
                    dmc.TabsPanel(_paste_panel(paste_options), value=SOURCE_TAB_PASTE),
                ],
                id="import-source",
                value=SOURCE_TAB_FILE,
                className="hoopster-tabs",
            ),
            *_result_area("Open the drill library"),
            dcc.Store(
                id="import-result-meta",
                storage_type="memory",
                data=paste_options.get("result_meta", {}),
            ),
        ],
    )
