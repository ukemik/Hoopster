"""Drill set and practice template UI for Hoopster: the two lists, the drill set form (manual,
quick and dynamic) and the practice template form, with reorderable lists and the removal
dialogs (REQ-051 to REQ-054, REQ-072, REQ-080, REQ-081).

Layout modules import nothing from controllers; the callbacks live in src/controller_sets.py.
A form's working copy lives in a store (set-working, tpl-working) that only the controller's
edit callback writes; the lists are rebuilt from it.
"""

import logging

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
ITEM_CLASS = "hoopster-recent-item hoopster-recent-link"

# h_data.drill_set_versions.set_kind values and their labels (D6: fixed after creation).
KIND_MANUAL = "manual_fixed"
KIND_QUICK = "quick_fixed"
KIND_DYNAMIC = "dynamic"
SET_KIND_LABELS: dict[str, str] = {
    KIND_MANUAL: "Manual",
    KIND_QUICK: "Quick",
    KIND_DYNAMIC: "Dynamic",
}

ACTION_UP = "up"
ACTION_DOWN = "down"
ACTION_REMOVE = "remove"
ACTION_LABELS: dict[str, str] = {ACTION_UP: "Up", ACTION_DOWN: "Down", ACTION_REMOVE: "Remove"}

LINE_COUNT_MIN = 1
LINE_COUNT_MAX = 50


# --- Reorderable lists (COD-13-05: two functions instead of a flag) ---

def _ordered_items(prefix: str, rows: list[str], actions: tuple[str, ...]) -> list[Component]:
    """
    Description:
        Builds a numbered list whose rows carry the given action buttons.

    Parameters:
        prefix: the id prefix, e.g. "set-drill".
        rows: the row labels, in order.
        actions: the actions per row, from ACTION_UP, ACTION_DOWN, ACTION_REMOVE.

    Return Values:
        list[Component]: one dmc.Paper per row; "Nothing added yet." when rows is empty.

    Notes:
        Button ids {"type": f"{prefix}-{action}", "index": i}, i = the row's 0-based
        position. Up is disabled on the first row and Down on the last.
    """
    if not rows:
        return [dmc.Text("Nothing added yet.", c="dimmed")]
    last = len(rows) - 1
    items: list[Component] = []
    for position, label in enumerate(rows):
        buttons = [
            secondary_button(
                {"type": f"{prefix}-{action}", "index": position},
                ACTION_LABELS[action],
                disabled=(action == ACTION_UP and position == 0)
                or (action == ACTION_DOWN and position == last),
                color="red" if action == ACTION_REMOVE else None,
            )
            for action in actions
        ]
        items.append(
            dmc.Paper(
                dmc.Stack(
                    [dmc.Text(f"{position + 1}. {label}"), dmc.Group(buttons, grow=True)],
                    gap="xs",
                ),
                withBorder=True,
                p="xs",
            )
        )
    return items


def ordered_rows(prefix: str, rows: list[str]) -> list[Component]:
    """
    Description:
        A reorderable list with Up, Down and Remove per row.

    Parameters:
        prefix: the id prefix ("set-drill", "set-line" or "tpl-set").
        rows: the row labels, in order.

    Return Values:
        list[Component]: the rows (see _ordered_items).

    Notes:
        Ids {"type": f"{prefix}-up|down|remove", "index": i}.
    """
    return _ordered_items(prefix, rows, (ACTION_UP, ACTION_DOWN, ACTION_REMOVE))


def ordered_rows_fixed(prefix: str, rows: list[str]) -> list[Component]:
    """
    Description:
        A reorderable list with Up and Down only: the drawn drills of a quick set, whose
        membership is fixed by the draw (REQ-054, D13).

    Parameters:
        prefix: the id prefix ("set-drill").
        rows: the row labels, in order.

    Return Values:
        list[Component]: the rows (see _ordered_items).

    Notes:
        Ids {"type": f"{prefix}-up|down", "index": i}.
    """
    return _ordered_items(prefix, rows, (ACTION_UP, ACTION_DOWN))


# --- Labels ---

def version_header_text(version_no: object, practice_count: int) -> str:
    """
    Description:
        The header line of an edited drill set or template: "Version n · used in N
        practices".

    Parameters:
        version_no: the latest live version number.
        practice_count: data_drills.get_library_usage's practice_count.

    Return Values:
        str: the header.

    Notes:
        Pure. The same text as layouts_drills.version_header_text (layouts_* modules import
        only layouts).
    """
    return f"Version {version_no}{DETAIL_SEPARATOR}used in {practice_count} practices"


def line_label(line: dict, labels: dict) -> str:
    """
    Description:
        The text of a draw line, e.g. "4 Beginner drills, any type" or "3 Intermediate
        drills, type Shooting".

    Parameters:
        line: {"drill_count", "difficulty_code", "drill_type_id"}.
        labels: the set-labels store data ({"difficulties": {code: label}, "types":
            {str(id): name}}).

    Return Values:
        str: the label.

    Notes:
        Pure.
    """
    count = int(line["drill_count"])
    difficulty = labels["difficulties"].get(str(line["difficulty_code"]), line["difficulty_code"])
    noun = "drill" if count == 1 else "drills"
    type_id = line.get("drill_type_id")
    type_text = (
        "any type"
        if type_id is None
        else f"type {labels['types'].get(str(type_id), 'unknown')}"
    )
    return f"{count} {difficulty} {noun}, {type_text}"


def drill_label(drill_id: int, labels: dict) -> str:
    """
    Description:
        The text of a drill in a set's drill list.

    Parameters:
        drill_id: the drill LINEAGE id.
        labels: the set-labels store data ({"drills": {str(id): name}}).

    Return Values:
        str: the drill's name, or "Drill <id>" when unknown.

    Notes:
        Pure.
    """
    return str(labels["drills"].get(str(drill_id), f"Drill {drill_id}"))


def set_section_styles(kind: str, has_draw: bool) -> list[dict[str, str]]:
    """
    Description:
        Which parts of the set form a kind shows: the drill list, the Add drill controls and
        the lines.

    Parameters:
        kind: the set kind.
        has_draw: True for a saved quick set, whose drawn drills can be reordered.

    Return Values:
        list[dict[str, str]]: styles of set-section-drills, set-section-add-drill and
        set-section-lines ({} shown, {"display": "none"} hidden).

    Notes:
        Pure. Manual: drills and Add drill. Dynamic: lines. Quick: lines, plus the drawn
        drills once the set is saved.
    """
    shown = {
        KIND_MANUAL: (True, True, False),
        KIND_DYNAMIC: (False, False, True),
        KIND_QUICK: (has_draw, False, True),
    }.get(kind, (False, False, False))
    return [SHOWN_STYLE if is_shown else HIDDEN_STYLE for is_shown in shown]


def set_drill_rows(kind: str, drill_ids: list[int], labels: dict) -> list[Component]:
    """
    Description:
        The set's drill list for its kind: Up / Down / Remove for a manual set, Up / Down
        only for the drawn drills of a quick set.

    Parameters:
        kind: the set kind.
        drill_ids: drill LINEAGE ids in working order.
        labels: the set-labels store data.

    Return Values:
        list[Component]: the rows, ids prefixed "set-drill".

    Notes:
        Pure apart from building components.
    """
    names = [drill_label(drill_id, labels) for drill_id in drill_ids]
    if kind == KIND_QUICK:
        return ordered_rows_fixed("set-drill", names)
    return ordered_rows("set-drill", names)


def set_line_rows(lines: list[dict], labels: dict) -> list[Component]:
    """
    Description:
        The set's draw lines with Up / Down / Remove.

    Parameters:
        lines: the working lines.
        labels: the set-labels store data.

    Return Values:
        list[Component]: the rows, ids prefixed "set-line".

    Notes:
        None.
    """
    return ordered_rows("set-line", [line_label(line, labels) for line in lines])


def quick_draw_notice(editor: dict | None) -> list[str]:
    """
    Description:
        The lasting notice of a saved quick set whose draw came up short: the version
        holds fewer drills than its lines asked for (REQ-053, REQ-054).

    Parameters:
        editor: data_sets.get_set_editor, or None.

    Return Values:
        list[str]: one sentence when short, else empty.

    Notes:
        Pure. The per-line notices of h_data.fn_draw_quick_fixed_set are shown right after
        a save; this sentence keeps the shortfall visible when the form is reopened.
    """
    if not editor or editor["set_kind"] != KIND_QUICK:
        return []
    requested = sum(int(line["drill_count"]) for line in editor["lines"])
    drawn = len(editor["items"])
    if drawn >= requested:
        return []
    return [
        f"The draw found {drawn} of the {requested} drills the lines asked for: the "
        "library had too few matching drills."
    ]


def notice_children(notices: list[str]) -> list[Component]:
    """
    Description:
        The lines of a set-notice alert.

    Parameters:
        notices: the notice sentences.

    Return Values:
        list[Component]: one dmc.Text per notice.

    Notes:
        Pure apart from building components.
    """
    return [dmc.Text(notice, size="sm") for notice in notices]


# --- Drill sets list (REQ-051) ---

def _set_detail(row: dict) -> str:
    """
    Description:
        The second line of a drill set card: "Kind · N drills|N lines · v<n>".

    Parameters:
        row: one row of data_sets.list_library_sets.

    Return Values:
        str: the detail text.

    Notes:
        Pure. Dynamic sets count lines; manual and quick sets count drills.
    """
    kind = str(row["set_kind"])
    if kind == KIND_DYNAMIC:
        count = int(row["line_count"])
        size = "1 line" if count == 1 else f"{count} lines"
    else:
        count = int(row["item_count"])
        size = "1 drill" if count == 1 else f"{count} drills"
    return DETAIL_SEPARATOR.join([SET_KIND_LABELS.get(kind, kind), size, f"v{row['version_no']}"])


def sets_layout(rows: list[dict]) -> Component:
    """
    Description:
        The Drill sets screen: New drill set and one card-link per selectable set.

    Parameters:
        rows: rows of data_sets.list_library_sets.

    Return Values:
        Component: the page.

    Notes:
        Card ids {"type": "sets-card", "index": drill_set_id}, href "/drill-sets/<id>".
    """
    cards: list[Component] = [
        dcc.Link(
            [
                dmc.Text(str(row["set_name"])),
                dmc.Text(_set_detail(row), size="sm", c="dimmed"),
            ],
            id={"type": "sets-card", "index": int(row["drill_set_id"])},
            href=f"/drill-sets/{int(row['drill_set_id'])}",
            className=ITEM_CLASS,
        )
        for row in rows
    ] or [dmc.Text("No drill sets yet.", id="sets-empty", c="dimmed")]
    return page_skeleton(
        "Drill sets",
        [
            html.Div(
                nav_link("sets-new", "New drill set", "/drill-sets/new"),
                className="hoopster-link-button hoopster-link-primary",
            ),
            dmc.Stack(cards, id="sets-list", gap=0),
        ],
    )


# --- Drill set form (REQ-051 to REQ-054, REQ-080) ---

def set_labels(editor: dict | None, options: dict[str, list[dict]]) -> dict[str, dict[str, str]]:
    """
    Description:
        The set-labels store: names for the drills, difficulties and types the form shows.

    Parameters:
        editor: data_sets.get_set_editor, or None.
        options: data_drills.list_reference_options plus "drills"
            (data_drills.list_library_drills).

    Return Values:
        dict[str, dict[str, str]]: {"drills": {str(drill_id): name}, "difficulties":
        {code: label}, "types": {str(drill_type_id): name}}.

    Notes:
        Pure. Includes the editor's own drills, even ones no longer current.
    """
    drills = {str(row["drill_id"]): str(row["drill_name"]) for row in options["drills"]}
    if editor:
        for item in editor["items"]:
            drills.setdefault(str(item["drill_id"]), str(item["drill_name"]))
    return {
        "drills": drills,
        "difficulties": {
            str(row["difficulty_code"]): str(row["label"]) for row in options["difficulties"]
        },
        "types": {str(row["drill_type_id"]): str(row["type_name"]) for row in options["drill_types"]},
    }


def set_working(editor: dict | None) -> dict[str, list]:
    """
    Description:
        The set-working store's first value: the latest version's drills and lines.

    Parameters:
        editor: data_sets.get_set_editor, or None for a new set.

    Return Values:
        dict[str, list]: {"drill_ids": [int], "lines": [{"drill_count", "difficulty_code",
        "drill_type_id"}]}.

    Notes:
        Pure.
    """
    if not editor:
        return {"drill_ids": [], "lines": []}
    return {
        "drill_ids": [int(item["drill_id"]) for item in editor["items"]],
        "lines": [
            {
                "drill_count": int(line["drill_count"]),
                "difficulty_code": str(line["difficulty_code"]),
                "drill_type_id": (
                    None if line["drill_type_id"] is None else int(line["drill_type_id"])
                ),
            }
            for line in editor["lines"]
        ],
    }


def _set_line_controls(options: dict[str, list[dict]]) -> list[Component]:
    """
    Description:
        The Add line controls: count, difficulty, optional type, Add line.

    Parameters:
        options: as for set_form_layout.

    Return Values:
        list[Component]: the controls.

    Notes:
        None.
    """
    return [
        dmc.NumberInput(
            id="set-line-count",
            label="Number of drills",
            value=1,
            min=LINE_COUNT_MIN,
            max=LINE_COUNT_MAX,
            allowDecimal=False,
            size=CONTROL_SIZE,
            hideControls=True,
        ),
        dmc.Select(
            id="set-line-difficulty",
            label="Difficulty",
            data=[
                {"value": str(row["difficulty_code"]), "label": str(row["label"])}
                for row in options["difficulties"]
            ],
            size=CONTROL_SIZE,
        ),
        dmc.Select(
            id="set-line-type",
            label="Type (optional)",
            placeholder="Any type",
            data=[
                {"value": str(row["drill_type_id"]), "label": str(row["type_name"])}
                for row in options["drill_types"]
            ],
            clearable=True,
            size=CONTROL_SIZE,
        ),
        secondary_button("set-add-line-go", "Add line"),
    ]


def set_form_layout(
    editor: dict | None, options: dict[str, list[dict]], usage: dict[str, int] | None
) -> Component:
    """
    Description:
        The drill set form for a new set (/drill-sets/new) or an existing one
        (/drill-sets/<id>): name, description, kind, the drill list and/or lines by kind,
        Save, and for an existing set Remove with its confirmation (REQ-005, REQ-081).

    Parameters:
        editor: data_sets.get_set_editor, or None for a new set.
        options: data_drills.list_reference_options plus "drills"
            (data_drills.list_library_drills, the Add drill choices).
        usage: data_drills.get_library_usage of the set, or None for a new set.

    Return Values:
        Component: the page.

    Notes:
        Stores: set-working (the working copy), set-labels (names), set-form-context
        ({"drill_set_id", "kind"}; the LINEAGE id is written only after the builder's live
        check). The kind is read-only for an existing set (D6). set-notice shows draw
        notices.
    """
    kind = str(editor["set_kind"]) if editor else KIND_MANUAL
    labels = set_labels(editor, options)
    working = set_working(editor)
    drills_style, add_style, lines_style = set_section_styles(kind, editor is not None)
    notices = quick_draw_notice(editor)
    header = (
        version_header_text(editor["version_no"], (usage or {}).get("practice_count", 0))
        if editor
        else "A new drill set starts at version 1."
    )
    children: list[Component] = [
        dmc.Text(header, id="set-form-header", c="dimmed"),
        dmc.TextInput(
            id="set-name",
            label="Name",
            value=str(editor["set_name"]) if editor else "",
            size=CONTROL_SIZE,
        ),
        dmc.Textarea(
            id="set-description",
            label="Description",
            value=str(editor["description"] or "") if editor else "",
            autosize=True,
            minRows=2,
            size=CONTROL_SIZE,
        ),
        dmc.Stack(
            [
                dmc.Text("Kind", size="sm", fw=500),
                dmc.SegmentedControl(
                    id="set-kind",
                    data=[{"value": value, "label": label} for value, label in SET_KIND_LABELS.items()],
                    value=kind,
                    disabled=editor is not None,
                    fullWidth=True,
                    size=CONTROL_SIZE,
                    className="hoopster-segmented",
                ),
            ],
            gap=4,
        ),
        dmc.Alert(
            notice_children(notices),
            id="set-notice",
            color="yellow",
            style=SHOWN_STYLE if notices else HIDDEN_STYLE,
        ),
        html.Div(
            dmc.Stack(
                [
                    dmc.Text("Drills", fw=500),
                    dmc.Stack(
                        set_drill_rows(kind, working["drill_ids"], labels),
                        id="set-drill-list",
                        gap="xs",
                    ),
                ],
                gap="xs",
            ),
            id="set-section-drills",
            style=drills_style,
        ),
        html.Div(
            dmc.Stack(
                [
                    dmc.Select(
                        id="set-add-drill",
                        label="Add a drill",
                        placeholder="Search drills",
                        data=[
                            {"value": str(row["drill_id"]), "label": str(row["drill_name"])}
                            for row in options["drills"]
                        ],
                        searchable=True,
                        size=CONTROL_SIZE,
                    ),
                    secondary_button("set-add-drill-go", "Add drill"),
                ],
                gap="xs",
            ),
            id="set-section-add-drill",
            style=add_style,
        ),
        html.Div(
            dmc.Stack(
                [
                    dmc.Text("Lines (drawn in this order)", fw=500),
                    dmc.Stack(
                        set_line_rows(working["lines"], labels), id="set-line-list", gap="xs"
                    ),
                    *_set_line_controls(options),
                ],
                gap="xs",
            ),
            id="set-section-lines",
            style=lines_style,
        ),
        form_message("set-edit-feedback"),
        primary_button("set-save", "Save drill set"),
        dmc.Text("", id="set-saved", c="green"),
        form_message("set-feedback"),
    ]
    if editor:
        children += [
            secondary_button("set-remove", "Remove drill set", color="red"),
            confirm_modal("set-remove", "Remove drill set?", "Remove"),
        ]
    children += [
        html.Div(
            nav_link("sets-back", "All drill sets", "/drill-sets"),
            className="hoopster-link-button",
        ),
        dcc.Store(id="set-working", storage_type="memory", data=working),
        dcc.Store(id="set-labels", storage_type="memory", data=labels),
        dcc.Store(
            id="set-form-context",
            storage_type="memory",
            data={
                "drill_set_id": int(editor["drill_set_id"]) if editor else None,
                "kind": kind if editor else None,
            },
        ),
    ]
    return page_skeleton("Edit drill set" if editor else "New drill set", children)


# --- Practice templates (REQ-072, REQ-080) ---

def templates_layout(rows: list[dict]) -> Component:
    """
    Description:
        The Practice templates screen: New template and one card-link per selectable
        template.

    Parameters:
        rows: rows of data_sets.list_library_templates.

    Return Values:
        Component: the page.

    Notes:
        Card ids {"type": "templates-card", "index": practice_template_id}, href
        "/templates/<id>", detail "N drill sets · v<n>".
    """
    cards: list[Component] = []
    for row in rows:
        count = int(row["set_count"])
        size = "1 drill set" if count == 1 else f"{count} drill sets"
        template_id = int(row["practice_template_id"])
        cards.append(
            dcc.Link(
                [
                    dmc.Text(str(row["template_name"])),
                    dmc.Text(
                        f"{size}{DETAIL_SEPARATOR}v{row['version_no']}", size="sm", c="dimmed"
                    ),
                ],
                id={"type": "templates-card", "index": template_id},
                href=f"/templates/{template_id}",
                className=ITEM_CLASS,
            )
        )
    if not cards:
        cards = [dmc.Text("No practice templates yet.", id="templates-empty", c="dimmed")]
    return page_skeleton(
        "Practice templates",
        [
            html.Div(
                nav_link("templates-new", "New template", "/templates/new"),
                className="hoopster-link-button hoopster-link-primary",
            ),
            dmc.Stack(cards, id="templates-list", gap=0),
        ],
    )


def template_set_rows(drill_set_ids: list[int], labels: dict[str, str]) -> list[Component]:
    """
    Description:
        The template's drill sets with Up / Down / Remove.

    Parameters:
        drill_set_ids: drill set LINEAGE ids in working order.
        labels: the tpl-labels store data ({str(drill_set_id): name}).

    Return Values:
        list[Component]: the rows, ids prefixed "tpl-set".

    Notes:
        None.
    """
    return ordered_rows(
        "tpl-set",
        [labels.get(str(set_id), f"Drill set {set_id}") for set_id in drill_set_ids],
    )


def template_set_options(
    drill_set_ids: list[int], labels: dict[str, str], current_ids: list[int]
) -> list[dict[str, str]]:
    """
    Description:
        The Add drill set choices: current sets not already in the list (a set appears at
        most once in a template, REQ-080).

    Parameters:
        drill_set_ids: the working list.
        labels: the tpl-labels store data.
        current_ids: the selectable sets' ids, in display order.

    Return Values:
        list[dict[str, str]]: {"value": str(id), "label": name} per addable set.

    Notes:
        Pure.
    """
    listed = set(drill_set_ids)
    return [
        {"value": str(set_id), "label": labels.get(str(set_id), f"Drill set {set_id}")}
        for set_id in current_ids
        if set_id not in listed
    ]


def template_form_layout(
    editor: dict | None, sets: list[dict], usage: dict[str, int] | None
) -> Component:
    """
    Description:
        The practice template form for a new template (/templates/new) or an existing one
        (/templates/<id>): name, description, the ordered drill sets with Add drill set,
        Save, and for an existing template Remove with its confirmation (REQ-005,
        REQ-072, REQ-081).

    Parameters:
        editor: data_sets.get_template_editor, or None for a new template.
        sets: data_sets.list_library_sets (the selectable sets).
        usage: data_drills.get_library_usage of the template, or None.

    Return Values:
        Component: the page.

    Notes:
        Stores: tpl-working ({"drill_set_ids"}), tpl-labels ({str(id): name}, including the
        editor's own sets), tpl-current ([ids] of the selectable sets, in order) and
        tpl-form-context ({"practice_template_id"}, the LINEAGE id written only after the
        builder's live check).
    """
    labels = {str(row["drill_set_id"]): str(row["set_name"]) for row in sets}
    working_ids: list[int] = []
    if editor:
        for row in editor["sets"]:
            labels.setdefault(str(row["drill_set_id"]), str(row["set_name"]))
            working_ids.append(int(row["drill_set_id"]))
    current_ids = [int(row["drill_set_id"]) for row in sets]
    header = (
        version_header_text(editor["version_no"], (usage or {}).get("practice_count", 0))
        if editor
        else "A new practice template starts at version 1."
    )
    children: list[Component] = [
        dmc.Text(header, id="tpl-form-header", c="dimmed"),
        dmc.TextInput(
            id="tpl-name",
            label="Name",
            value=str(editor["template_name"]) if editor else "",
            size=CONTROL_SIZE,
        ),
        dmc.Textarea(
            id="tpl-description",
            label="Description",
            value=str(editor["description"] or "") if editor else "",
            autosize=True,
            minRows=2,
            size=CONTROL_SIZE,
        ),
        dmc.Text("Drill sets (in practice order)", fw=500),
        dmc.Stack(template_set_rows(working_ids, labels), id="tpl-set-list", gap="xs"),
        dmc.Select(
            id="tpl-add-set",
            label="Add a drill set",
            placeholder="Choose a drill set",
            data=template_set_options(working_ids, labels, current_ids),
            searchable=True,
            size=CONTROL_SIZE,
        ),
        secondary_button("tpl-add-set-go", "Add drill set"),
        form_message("tpl-edit-feedback"),
        primary_button("tpl-save", "Save template"),
        dmc.Text("", id="tpl-saved", c="green"),
        form_message("tpl-feedback"),
    ]
    if editor:
        children += [
            secondary_button("tpl-remove", "Remove template", color="red"),
            confirm_modal("tpl-remove", "Remove practice template?", "Remove"),
        ]
    children += [
        html.Div(
            nav_link("templates-back", "All templates", "/templates"),
            className="hoopster-link-button",
        ),
        dcc.Store(id="tpl-working", storage_type="memory", data={"drill_set_ids": working_ids}),
        dcc.Store(id="tpl-labels", storage_type="memory", data=labels),
        dcc.Store(id="tpl-current", storage_type="memory", data=current_ids),
        dcc.Store(
            id="tpl-form-context",
            storage_type="memory",
            data={
                "practice_template_id": int(editor["practice_template_id"]) if editor else None
            },
        ),
    ]
    return page_skeleton("Edit template" if editor else "New template", children)
