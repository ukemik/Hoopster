"""Drill library UI for Hoopster: the Drill types list, the drill library (AG Grid on wide
screens, cards on phones) and the drill form with every result type (REQ-026, REQ-028,
REQ-029, REQ-048, REQ-076, REQ-077, REQ-079, REQ-080, REQ-081).

Layout modules import nothing from controllers; the callbacks live in
src/controller_drills.py. Both library views are always in the page; CSS shows the cards
below 700 px and the grid from 700 px (assets/hoopster.css). The drill form's sections are
shown by result type at build time and then by the clientside
hoopster_library.drill_form_sections (assets/hoopster_library.js), which mirrors
drill_section_styles.
"""

import logging

import dash_ag_grid as dag
import dash_mantine_components as dmc
from dash import dcc, html
from dash.development.base_component import Component

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
TEXT_DEBOUNCE_MS = 300

NARROW_ONLY_CLASS = "hoopster-narrow-only"
WIDE_ONLY_CLASS = "hoopster-wide-only"
ITEM_CLASS = "hoopster-recent-item hoopster-recent-link"

# h_ref.result_types.capture_kind values.
CAPTURE_SHOTS = "shots"
CAPTURE_OUTCOMES = "outcomes"
CAPTURE_VALUES = "values"
RESULT_TYPE_PHYSICAL = "physical"

# The units ck_drill_versions_units allows for a physical drill's numbers: (value, label).
PHYSICAL_UNIT_OPTIONS: tuple[tuple[str, str], ...] = (
    ("in", "inches"),
    ("cm", "centimetres"),
    ("ft", "feet"),
    ("m", "metres"),
    ("lb", "pounds"),
    ("kg", "kilograms"),
    ("s", "seconds"),
    ("ms", "milliseconds"),
    ("count", "count"),
    ("deg", "degrees"),
)
DIRECTION_HIGHER = "higher"
DIRECTION_LOWER = "lower"
DIRECTION_OPTIONS: list[dict[str, str]] = [
    {"value": DIRECTION_HIGHER, "label": "Higher is better"},
    {"value": DIRECTION_LOWER, "label": "Lower is better"},
]

# The free-throw line's value in the drill-spots picker; court spots are "s<court_spot_id>".
SPOT_VALUE_FREE_THROW = "ft"
SPOT_VALUE_PREFIX = "s"
FREE_THROW_LABEL = "Free-throw line"

# REQ-077: at most 3 outcome groups of at most 6 buttons; the form shows every slot.
OUTCOME_GROUP_SLOTS = 3
OUTCOME_BUTTON_SLOTS = 6

# Target labels per capture kind; a values type shows its unit (plan §3.4).
TARGET_LABEL_SHOTS = "Shots per attempt"
TARGET_LABEL_REPS = "Reps per attempt"
TARGET_LABEL_DEFAULT = "Target"
UNIT_WORDS: dict[str, str] = {"s": "seconds", "count": "count"}

# The ids of the drill form's sections, in the order drill_form_sections returns their styles.
DRILL_SECTION_IDS: tuple[str, ...] = (
    "drill-section-target",
    "drill-section-fixed",
    "drill-section-physical",
    "drill-section-shooting",
    "drill-section-outcomes",
)


# --- Drill types (REQ-029) ---

def _used_by_text(count: int) -> str:
    """
    Description:
        The "used by N drills" line of a drill type.

    Parameters:
        count: live drills using the type as primary or secondary type.

    Return Values:
        str: "Used by 1 drill" or "Used by N drills".

    Notes:
        Pure.
    """
    return f"Used by {count} drill" if count == 1 else f"Used by {count} drills"


def drill_type_rows(types: list[dict]) -> list[Component]:
    """
    Description:
        The rows of the Drill types list: per type a name field, its use count, Save and
        Delete, and a feedback line.

    Parameters:
        types: rows of data_drills.list_drill_types_with_use.

    Return Values:
        list[Component]: one dmc.Paper per type; with no types, one dmc.Text "No drill types
        yet." with id "dtype-empty".

    Notes:
        Pattern ids per row, index = drill_type_id: {"type": "dtype-name"} (TextInput),
        {"type": "dtype-save"}, {"type": "dtype-delete"} and {"type": "dtype-row-feedback"}.
        The children of dtype-rows, so the add and delete callbacks can rebuild them.
    """
    if not types:
        return [dmc.Text("No drill types yet.", id="dtype-empty", c="dimmed")]
    rows: list[Component] = []
    for drill_type in types:
        type_id = int(drill_type["drill_type_id"])
        rows.append(
            dmc.Paper(
                dmc.Stack(
                    [
                        dmc.TextInput(
                            id={"type": "dtype-name", "index": type_id},
                            label="Name",
                            value=str(drill_type["type_name"]),
                            size=CONTROL_SIZE,
                        ),
                        dmc.Text(
                            _used_by_text(int(drill_type["drill_count"])),
                            size="sm",
                            c="dimmed",
                        ),
                        dmc.Group(
                            [
                                secondary_button(
                                    {"type": "dtype-save", "index": type_id}, "Save"
                                ),
                                secondary_button(
                                    {"type": "dtype-delete", "index": type_id},
                                    "Delete",
                                    color="red",
                                ),
                            ],
                            grow=True,
                        ),
                        dmc.Text("", id={"type": "dtype-row-feedback", "index": type_id}),
                    ],
                    gap="xs",
                ),
                withBorder=True,
                p="sm",
            )
        )
    return rows


def drill_types_layout(types: list[dict]) -> Component:
    """
    Description:
        The Drill types screen (REQ-029): the editable list and the Add drill type form, with
        the delete confirmation (REQ-005).

    Parameters:
        types: rows of data_drills.list_drill_types_with_use.

    Return Values:
        Component: the page.

    Notes:
        The store dtype-del-target holds the drill_type_id the open dialog would delete; it
        is written only by the open callback.
    """
    return page_skeleton(
        "Drill types",
        [
            dmc.Stack(drill_type_rows(types), id="dtype-rows", gap="sm"),
            dmc.TextInput(id="dtype-new-name", label="New drill type", size=CONTROL_SIZE),
            primary_button("dtype-add", "Add drill type"),
            form_message("dtype-add-feedback"),
            dcc.Store(id="dtype-del-target", storage_type="memory"),
            confirm_modal("dtype-del", "Delete drill type?", "Delete"),
        ],
    )


# --- Library list (REQ-026, REQ-048) ---

def _drill_detail(row: dict) -> str:
    """
    Description:
        The second line of a drill card: "Difficulty · Type · Result type · v<n>".

    Parameters:
        row: one row of data_drills.list_library_drills.

    Return Values:
        str: the detail text.

    Notes:
        Pure.
    """
    return DETAIL_SEPARATOR.join(
        [
            str(row["difficulty_label"]),
            str(row["type_name"]),
            str(row["result_type_label"]),
            f"v{row['version_no']}",
        ]
    )


def drill_cards(rows: list[dict]) -> list[Component]:
    """
    Description:
        The phone view of the library: one card-link per drill to its form.

    Parameters:
        rows: rows of data_drills.list_library_drills, already filtered.

    Return Values:
        list[Component]: per drill a dcc.Link with id {"type": "drills-card", "index":
        drill_id} and href "/drills/<id>", showing the name above the detail line; with no
        rows, one dmc.Text "No drills match." with id "drills-empty".

    Notes:
        The children of drills-cards, rebuilt by the filter callback.
    """
    if not rows:
        return [dmc.Text("No drills match.", id="drills-empty", c="dimmed")]
    return [
        dcc.Link(
            [
                dmc.Text(str(row["drill_name"])),
                dmc.Text(_drill_detail(row), size="sm", c="dimmed"),
            ],
            id={"type": "drills-card", "index": int(row["drill_id"])},
            href=f"/drills/{int(row['drill_id'])}",
            className=ITEM_CLASS,
        )
        for row in rows
    ]


def drill_grid_rows(rows: list[dict]) -> list[dict[str, object]]:
    """
    Description:
        The grid's rowData: one plain dict per drill.

    Parameters:
        rows: rows of data_drills.list_library_drills, already filtered.

    Return Values:
        list[dict[str, object]]: {"row_id", "drill_id", "drill_name", "type_name",
        "difficulty_label", "result_type_label", "default_target", "version_no",
        "open": "Open"} per drill.

    Notes:
        Pure. row_id (str(drill_id)) is the grid's getRowId, so cellValueChanged and
        cellClicked carry the drill LINEAGE id.
    """
    return [
        {
            "row_id": str(row["row_id"]),
            "drill_id": int(row["drill_id"]),
            "drill_name": str(row["drill_name"]),
            "type_name": str(row["type_name"]),
            "difficulty_label": str(row["difficulty_label"]),
            "result_type_label": str(row["result_type_label"]),
            "default_target": row["default_target"],
            "version_no": row["version_no"],
            "open": "Open",
        }
        for row in rows
    ]


def drill_count_text(count: int) -> str:
    """
    Description:
        The drills-count line.

    Parameters:
        count: the number of drills shown.

    Return Values:
        str: "1 drill" or "N drills".

    Notes:
        Pure.
    """
    return "1 drill" if count == 1 else f"{count} drills"


def _grid_column_defs(difficulty_labels: list[str]) -> list[dict[str, object]]:
    """
    Description:
        The library grid's columns: Name and Difficulty editable in the cell (D11), Type,
        Result type, Target, Version and Open.

    Parameters:
        difficulty_labels: the four difficulty labels in order, for the select editor.

    Return Values:
        list[dict[str, object]]: the AG Grid columnDefs.

    Notes:
        Pure. Open's colId is "open"; a click on it opens the drill form.
    """
    return [
        {"field": "drill_name", "headerName": "Name", "editable": True, "minWidth": 160},
        {"field": "type_name", "headerName": "Type"},
        {
            "field": "difficulty_label",
            "headerName": "Difficulty",
            "editable": True,
            "cellEditor": "agSelectCellEditor",
            "cellEditorParams": {"values": difficulty_labels},
        },
        {"field": "result_type_label", "headerName": "Result type"},
        {"field": "default_target", "headerName": "Target"},
        {"field": "version_no", "headerName": "Version"},
        {
            "field": "open",
            "colId": "open",
            "headerName": "",
            "sortable": False,
            "filter": False,
            "cellStyle": {"cursor": "pointer", "fontWeight": 600},
        },
    ]


def _option_list(rows: list[dict], value_key: str, label_key: str) -> list[dict[str, str]]:
    """
    Description:
        Turns option rows into dmc Select data (values as strings).

    Parameters:
        rows: the option rows.
        value_key: the key of the value column.
        label_key: the key of the label column.

    Return Values:
        list[dict[str, str]]: {"value", "label"} per row, in order.

    Notes:
        Pure.
    """
    return [{"value": str(row[value_key]), "label": str(row[label_key])} for row in rows]


def drills_layout(rows: list[dict], options: dict[str, list[dict]]) -> Component:
    """
    Description:
        The drill library screen (REQ-026, REQ-048): New drill, search and filters, the
        count, the cards (phones) and the AG Grid (wide screens).

    Parameters:
        rows: rows of data_drills.list_library_drills.
        options: data_drills.list_reference_options.

    Return Values:
        Component: the page.

    Notes:
        The store drills-difficulty-codes maps each difficulty label to its code for the
        grid's in-cell edit. The grid uses dash-ag-grid's built-in theme (no CDN, no legacy
        theme class; plan Risk R5) and autoHeight, so its style height is unset.
    """
    difficulties = options["difficulties"]
    difficulty_labels = [str(row["label"]) for row in difficulties]
    grid = dag.AgGrid(
        id="drills-grid",
        rowData=drill_grid_rows(rows),
        columnDefs=_grid_column_defs(difficulty_labels),
        getRowId="params.data.row_id",
        defaultColDef={"sortable": True, "filter": True, "resizable": True},
        dashGridOptions={
            "domLayout": "autoHeight",
            "singleClickEdit": True,
            "stopEditingWhenCellsLoseFocus": True,
        },
        columnSize="responsiveSizeToFit",
        style={"height": None, "width": "100%"},
    )
    return page_skeleton(
        "Drills",
        [
            html.Div(
                nav_link("drills-new", "New drill", "/drills/new"),
                className="hoopster-link-button hoopster-link-primary",
            ),
            # The general Import page (task 2026-10-23_drill_import, REQ-027, REQ-050).
            html.Div(
                nav_link("drills-import", "Import drills", "/import"),
                className="hoopster-link-button",
            ),
            dmc.TextInput(
                id="drills-search",
                label="Search",
                placeholder="Name or description",
                debounce=TEXT_DEBOUNCE_MS,
                size=CONTROL_SIZE,
            ),
            dmc.Select(
                id="drills-filter-type",
                label="Type",
                placeholder="All types",
                data=_option_list(options["drill_types"], "drill_type_id", "type_name"),
                clearable=True,
                size=CONTROL_SIZE,
            ),
            dmc.Select(
                id="drills-filter-difficulty",
                label="Difficulty",
                placeholder="All difficulties",
                data=_option_list(difficulties, "difficulty_code", "label"),
                clearable=True,
                size=CONTROL_SIZE,
            ),
            dmc.Select(
                id="drills-filter-result",
                label="Result type",
                placeholder="All result types",
                data=_option_list(options["result_types"], "result_type_code", "label"),
                clearable=True,
                size=CONTROL_SIZE,
            ),
            dmc.Text(drill_count_text(len(rows)), id="drills-count", c="dimmed"),
            form_message("drills-grid-feedback"),
            html.Div(drill_cards(rows), id="drills-cards", className=NARROW_ONLY_CLASS),
            html.Div(grid, className=WIDE_ONLY_CLASS),
            dcc.Store(
                id="drills-difficulty-codes",
                storage_type="memory",
                data={str(row["label"]): str(row["difficulty_code"]) for row in difficulties},
            ),
        ],
    )


# --- Drill form (REQ-028, REQ-076, REQ-077, REQ-079, REQ-080) ---

def result_type_meta(result_types: list[dict]) -> dict[str, dict[str, object]]:
    """
    Description:
        The per-result-type facts the form needs to show its sections.

    Parameters:
        result_types: options["result_types"] of data_drills.list_reference_options.

    Return Values:
        dict[str, dict[str, object]]: result_type_code -> {"capture_kind", "value_unit",
        "needs_fixed_seconds"}.

    Notes:
        Pure. Also the data of the drill-result-meta store, read by the clientside
        drill_form_sections and by the controller's form check.
    """
    return {
        str(row["result_type_code"]): {
            "capture_kind": row["capture_kind"],
            "value_unit": row["value_unit"],
            "needs_fixed_seconds": bool(row["needs_fixed_seconds"]),
        }
        for row in result_types
    }


def drill_target_label(result_type_code: str | None, meta: dict[str, dict[str, object]]) -> str:
    """
    Description:
        The label of the target field for a result type (plan §3.4).

    Parameters:
        result_type_code: the chosen result type, or None.
        meta: result_type_meta.

    Return Values:
        str: "Shots per attempt" (shooting), "Reps per attempt" (outcome buttons),
        "Target (<unit>)" for a values type with a unit, else "Target".

    Notes:
        Pure. Mirrored by hoopster_library.drill_form_sections; keep both in step.
    """
    facts = meta.get(result_type_code or "")
    if not facts:
        return TARGET_LABEL_DEFAULT
    if facts["capture_kind"] == CAPTURE_SHOTS:
        return TARGET_LABEL_SHOTS
    if facts["capture_kind"] == CAPTURE_OUTCOMES:
        return TARGET_LABEL_REPS
    unit = facts.get("value_unit")
    if isinstance(unit, str) and unit:
        return f"Target ({UNIT_WORDS.get(unit, unit)})"
    return TARGET_LABEL_DEFAULT


def drill_section_styles(
    result_type_code: str | None, meta: dict[str, dict[str, object]]
) -> list[dict[str, str]]:
    """
    Description:
        Which drill-form sections a result type shows: target (any chosen type except
        physical), fixed time (types that need it), physical numbers, spots (shooting) and
        outcome groups (outcome buttons).

    Parameters:
        result_type_code: the chosen result type, or None (every section hidden).
        meta: result_type_meta.

    Return Values:
        list[dict[str, str]]: one style per DRILL_SECTION_IDS entry, {} shown or
        {"display": "none"} hidden.

    Notes:
        Pure. Mirrored by hoopster_library.drill_form_sections; keep both in step. Hidden
        sections are ignored on save.
    """
    facts = meta.get(result_type_code or "")
    if not facts:
        return [HIDDEN_STYLE] * len(DRILL_SECTION_IDS)
    shown = [
        result_type_code != RESULT_TYPE_PHYSICAL,
        bool(facts["needs_fixed_seconds"]),
        result_type_code == RESULT_TYPE_PHYSICAL,
        facts["capture_kind"] == CAPTURE_SHOTS,
        facts["capture_kind"] == CAPTURE_OUTCOMES,
    ]
    return [SHOWN_STYLE if is_shown else HIDDEN_STYLE for is_shown in shown]


def _spot_options(court_spots: list[dict]) -> list[dict[str, str]]:
    """
    Description:
        The drill-spots picker options: "#n Name" per active court spot, then the
        free-throw line.

    Parameters:
        court_spots: options["court_spots"].

    Return Values:
        list[dict[str, str]]: {"value": "s<court_spot_id>" or "ft", "label"}.

    Notes:
        Pure. Values never use spot_number (COD-8-01).
    """
    spot_options = [
        {
            "value": f"{SPOT_VALUE_PREFIX}{int(spot['court_spot_id'])}",
            "label": f"#{spot['spot_number']} {spot['spot_name']}",
        }
        for spot in court_spots
    ]
    spot_options.append({"value": SPOT_VALUE_FREE_THROW, "label": FREE_THROW_LABEL})
    return spot_options


def spot_values(spot_ids: list[int]) -> list[str]:
    """
    Description:
        Turns a version's spot ids into drill-spots picker values, keeping the order.

    Parameters:
        spot_ids: court spot ids, 0 for the free-throw line (data_drills.FREE_THROW_SPOT_ID).

    Return Values:
        list[str]: "s<id>" per court spot, "ft" for the free-throw line.

    Notes:
        Pure.
    """
    return [
        SPOT_VALUE_FREE_THROW if spot_id == 0 else f"{SPOT_VALUE_PREFIX}{spot_id}"
        for spot_id in spot_ids
    ]


def _outcome_group_slot(group_no: int, group: dict | None) -> Component:
    """
    Description:
        One outcome-group slot of the form: the group name and six button slots, each a
        label and a "Success" checkbox.

    Parameters:
        group_no: 1-3.
        group: the editor's group (data_drills._group_outcome_rows) to prefill, or None.

    Return Values:
        Component: the slot.

    Notes:
        Ids: {"type": "og-name", "index": group_no}, {"type": "og-label", "group": group_no,
        "index": n} and {"type": "og-success", "group": group_no, "index": n}, n = 1-6.
    """
    outcomes = list(group["outcomes"]) if group else []
    button_rows: list[Component] = []
    for button_no in range(1, OUTCOME_BUTTON_SLOTS + 1):
        outcome = outcomes[button_no - 1] if button_no <= len(outcomes) else None
        button_rows.append(
            dmc.Group(
                [
                    dmc.TextInput(
                        id={"type": "og-label", "group": group_no, "index": button_no},
                        placeholder=f"Button {button_no}",
                        value=str(outcome["outcome_label"]) if outcome else "",
                        size=CONTROL_SIZE,
                        style={"flex": 1},
                    ),
                    dmc.Checkbox(
                        id={"type": "og-success", "group": group_no, "index": button_no},
                        label="Success",
                        checked=bool(outcome["is_success"]) if outcome else False,
                        size=CONTROL_SIZE,
                        className="hoopster-check",
                    ),
                ],
                wrap="nowrap",
            )
        )
    return dmc.Paper(
        dmc.Stack(
            [
                dmc.TextInput(
                    id={"type": "og-name", "index": group_no},
                    label=f"Group {group_no} name",
                    value=str(group["group_name"]) if group else "",
                    size=CONTROL_SIZE,
                ),
                *button_rows,
            ],
            gap="xs",
        ),
        withBorder=True,
        p="sm",
    )


def _physical_section(editor: dict | None, unit_options: list[dict[str, str]]) -> Component:
    """
    Description:
        The physical-measurement numbers of the form: the first (required) and the second
        (optional) named number, each with a unit and a better direction (REQ-076).

    Parameters:
        editor: data_drills.get_drill_editor, or None for a new drill.
        unit_options: the unit picker data.

    Return Values:
        Component: the section's content.

    Notes:
        A second number counts only when its name is filled.
    """
    def value_of(key: str) -> object:
        """
        Description:
            Reads a field of the editor, None for a new drill.

        Parameters:
            key: the editor key.

        Return Values:
            object: the value, or None.

        Notes:
            None.
        """
        return editor.get(key) if editor else None

    def direction(key: str) -> str:
        """
        Description:
            The direction control's value for a stored higher_is_better flag.

        Parameters:
            key: "value_1_higher_is_better" or "value_2_higher_is_better".

        Return Values:
            str: "lower" when the flag is False, else "higher".

        Notes:
            None.
        """
        return DIRECTION_LOWER if value_of(key) is False else DIRECTION_HIGHER

    return dmc.Stack(
        [
            dmc.Text("First number", fw=500),
            dmc.TextInput(
                id="drill-v1-label",
                label="Name",
                value=str(value_of("value_1_label") or ""),
                size=CONTROL_SIZE,
            ),
            dmc.Select(
                id="drill-v1-unit",
                label="Unit",
                data=unit_options,
                value=value_of("value_1_unit"),
                size=CONTROL_SIZE,
            ),
            dmc.SegmentedControl(
                id="drill-v1-higher",
                data=DIRECTION_OPTIONS,
                value=direction("value_1_higher_is_better"),
                fullWidth=True,
                size=CONTROL_SIZE,
                className="hoopster-segmented",
            ),
            dmc.Text("Second number (optional)", fw=500),
            dmc.TextInput(
                id="drill-v2-label",
                label="Name",
                value=str(value_of("value_2_label") or ""),
                size=CONTROL_SIZE,
            ),
            dmc.Select(
                id="drill-v2-unit",
                label="Unit",
                data=unit_options,
                value=value_of("value_2_unit"),
                clearable=True,
                size=CONTROL_SIZE,
            ),
            dmc.SegmentedControl(
                id="drill-v2-higher",
                data=DIRECTION_OPTIONS,
                value=direction("value_2_higher_is_better"),
                fullWidth=True,
                size=CONTROL_SIZE,
                className="hoopster-segmented",
            ),
        ],
        gap="xs",
    )


def removal_text(usage: dict[str, int], with_goals: bool) -> str:
    """
    Description:
        The question of a library item's removal dialog (REQ-081).

    Parameters:
        usage: data_drills.get_library_usage: {"practice_count", "goal_count"}.
        with_goals: True for a drill, whose goals count too; False for sets and templates.

    Return Values:
        str: "Used in N practices (including any in Recently deleted)[ and M goals]. It will
        be retired: hidden from lists, kept in those practices." when used, else "Not used
        by any practice. It will be deleted and can be restored for 30 days."

    Notes:
        Pure. Matches h_data.fn_remove_library_item's own choice between retire and delete.
    """
    practices = usage.get("practice_count", 0)
    goals = usage.get("goal_count", 0) if with_goals else 0
    if practices == 0 and goals == 0:
        return "Not used by any practice. It will be deleted and can be restored for 30 days."
    used = f"Used in {practices} practices (including any in Recently deleted)"
    if with_goals:
        used += f" and {goals} goals"
    return used + ". It will be retired: hidden from lists, kept in those practices."


def version_header_text(version_no: object, practice_count: int) -> str:
    """
    Description:
        The header line of an edited library item: "Version n · used in N practices".

    Parameters:
        version_no: the latest live version number.
        practice_count: data_drills.get_library_usage's practice_count.

    Return Values:
        str: the header.

    Notes:
        Pure.
    """
    return f"Version {version_no}{DETAIL_SEPARATOR}used in {practice_count} practices"


def _lineage_fields(editor: dict | None, options: dict[str, list[dict]]) -> list[Component]:
    """
    Description:
        The drill form's lineage fields, edited in place (REQ-080): name, description,
        primary type, secondary types and difficulty.

    Parameters:
        editor: data_drills.get_drill_editor, or None for a new drill.
        options: data_drills.list_reference_options.

    Return Values:
        list[Component]: the fields, top to bottom.

    Notes:
        Values are strings (dmc), ids as str(drill_type_id).
    """
    type_options = _option_list(options["drill_types"], "drill_type_id", "type_name")
    primary = str(editor["primary_drill_type_id"]) if editor else None
    secondary = [str(type_id) for type_id in editor["secondary_type_ids"]] if editor else []
    return [
        dmc.TextInput(
            id="drill-name",
            label="Name",
            value=str(editor["drill_name"]) if editor else "",
            size=CONTROL_SIZE,
        ),
        dmc.Textarea(
            id="drill-description",
            label="Description",
            value=str(editor["description"] or "") if editor else "",
            autosize=True,
            minRows=2,
            size=CONTROL_SIZE,
        ),
        dmc.Select(
            id="drill-primary-type",
            label="Primary type",
            data=type_options,
            value=primary,
            allowDeselect=False,
            size=CONTROL_SIZE,
        ),
        dmc.MultiSelect(
            id="drill-secondary-types",
            label="Secondary types (optional)",
            data=type_options,
            value=secondary,
            hidePickedOptions=True,
            size=CONTROL_SIZE,
        ),
        dmc.Select(
            id="drill-difficulty",
            label="Difficulty",
            data=_option_list(options["difficulties"], "difficulty_code", "label"),
            value=str(editor["difficulty_code"]) if editor else None,
            allowDeselect=False,
            size=CONTROL_SIZE,
        ),
    ]


def _recording_fields(
    editor: dict | None,
    options: dict[str, list[dict]],
    meta: dict[str, dict[str, object]],
) -> list[Component]:
    """
    Description:
        The drill form's versioned fields (REQ-076, REQ-077, REQ-079, REQ-080): result type,
        symmetry and the sections that depend on the result type.

    Parameters:
        editor: data_drills.get_drill_editor, or None for a new drill.
        options: data_drills.list_reference_options.
        meta: result_type_meta.

    Return Values:
        list[Component]: the fields and sections, top to bottom.

    Notes:
        The sections' styles come from drill_section_styles at build time.
    """
    result_type = str(editor["result_type_code"]) if editor else None
    styles = dict(zip(DRILL_SECTION_IDS, drill_section_styles(result_type, meta), strict=True))
    unit_options = [{"value": value, "label": label} for value, label in PHYSICAL_UNIT_OPTIONS]
    groups = list(editor["groups"]) if editor else []
    group_slots = [
        _outcome_group_slot(group_no, groups[group_no - 1] if group_no <= len(groups) else None)
        for group_no in range(1, OUTCOME_GROUP_SLOTS + 1)
    ]
    return [
        dmc.Select(
            id="drill-result-type",
            label="Result type",
            placeholder="Choose a result type",
            data=_option_list(options["result_types"], "result_type_code", "label"),
            value=result_type,
            allowDeselect=False,
            size=CONTROL_SIZE,
        ),
        dmc.Select(
            id="drill-symmetry",
            label="Symmetry (optional)",
            placeholder="None",
            data=_option_list(options["symmetry_types"], "symmetry_type_code", "label"),
            value=editor["symmetry_type_code"] if editor else None,
            clearable=True,
            size=CONTROL_SIZE,
        ),
        html.Div(
            dmc.NumberInput(
                id="drill-target",
                label=drill_target_label(result_type, meta),
                value=editor["default_target"] if editor else None,
                min=1,
                allowDecimal=False,
                size=CONTROL_SIZE,
                hideControls=True,
            ),
            id="drill-section-target",
            style=styles["drill-section-target"],
        ),
        html.Div(
            dmc.NumberInput(
                id="drill-fixed-seconds",
                label="Fixed time (seconds)",
                value=editor["fixed_seconds"] if editor else None,
                min=1,
                allowDecimal=False,
                size=CONTROL_SIZE,
                hideControls=True,
            ),
            id="drill-section-fixed",
            style=styles["drill-section-fixed"],
        ),
        html.Div(
            _physical_section(editor, unit_options),
            id="drill-section-physical",
            style=styles["drill-section-physical"],
        ),
        html.Div(
            dmc.MultiSelect(
                id="drill-spots",
                label="Spots, in shooting order",
                data=_spot_options(options["court_spots"]),
                value=spot_values(list(editor["spot_ids"])) if editor else [],
                hidePickedOptions=True,
                searchable=True,
                size=CONTROL_SIZE,
            ),
            id="drill-section-shooting",
            style=styles["drill-section-shooting"],
        ),
        html.Div(
            dmc.Stack(
                [
                    dmc.Text(
                        "1 to 3 groups, each with 2 to 6 buttons. A group counts when it has "
                        "a name; a button counts when it has a label.",
                        size="sm",
                        c="dimmed",
                    ),
                    *group_slots,
                ],
                gap="sm",
            ),
            id="drill-section-outcomes",
            style=styles["drill-section-outcomes"],
        ),
    ]


def drill_form_layout(
    editor: dict | None, options: dict[str, list[dict]], usage: dict[str, int] | None
) -> Component:
    """
    Description:
        The drill form for a new drill (/drills/new) or an existing one (/drills/<id>):
        lineage fields, versioned fields, Save, and for an existing drill Remove with its
        confirmation (REQ-005, REQ-081).

    Parameters:
        editor: data_drills.get_drill_editor, or None for a new drill.
        options: data_drills.list_reference_options.
        usage: data_drills.get_library_usage of the drill, or None for a new drill.

    Return Values:
        Component: the page.

    Notes:
        The drill-form-id store holds the drill LINEAGE id (None for a new drill), written
        only after the builder's live check. The drill-result-meta store feeds the clientside
        section switch. The removal group (drill-remove and its dialog) is present only for
        an existing drill.
    """
    meta = result_type_meta(options["result_types"])
    header = (
        version_header_text(editor["version_no"], (usage or {}).get("practice_count", 0))
        if editor
        else "A new drill starts at version 1."
    )
    children: list[Component] = [
        dmc.Text(header, id="drill-form-header", c="dimmed"),
        *_lineage_fields(editor, options),
        *_recording_fields(editor, options, meta),
        primary_button("drill-save", "Save drill"),
        dmc.Text("", id="drill-saved", c="green"),
        form_message("drill-feedback"),
    ]
    if editor:
        children += [
            secondary_button("drill-remove", "Remove drill", color="red"),
            confirm_modal("drill-remove", "Remove drill?", "Remove"),
        ]
    children += [
        html.Div(
            nav_link("drills-back", "All drills", "/drills"),
            className="hoopster-link-button",
        ),
        dcc.Store(
            id="drill-form-id",
            storage_type="memory",
            data=int(editor["drill_id"]) if editor else None,
        ),
        dcc.Store(id="drill-result-meta", storage_type="memory", data=meta),
    ]
    return page_skeleton("Edit drill" if editor else "New drill", children)
