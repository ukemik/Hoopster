"""Drill set and practice template controller of Hoopster: the list and form page builders, the
pure list editing, and the edit, save and removal callbacks (REQ-051 to REQ-054, REQ-072,
REQ-080, REQ-081; REQ-005 confirmations).

Set and template ids are LINEAGE ids from the URL, written into the form-context stores only
after the builder's live check; drills are referenced by drill LINEAGE id (COD-8-01). The
edit callbacks are the only writers of the working stores and their lists. Every write goes
through app_controller.run_write_with_reason (plan §3.2). A new set or template opens its
edit page after saving; an edit is saved in place.
"""

import logging

from dash import ALL, Dash, Input, Output, State, ClientsideFunction, ctx, no_update
from dash.development.base_component import Component
from dash.exceptions import PreventUpdate
from flask_login import current_user

from src import data_handler
from src.app_controller import (
    DeviceClock,
    log_failure,
    new_reference_code,
    run_write_with_reason,
)
from src.data_drills import (
    ENTITY_DRILL_SET,
    ENTITY_PRACTICE_TEMPLATE,
    get_library_usage,
    list_library_drills,
    list_reference_options,
    remove_library_item,
)
from src.data_sets import (
    KIND_DYNAMIC,
    KIND_MANUAL,
    KIND_QUICK,
    SET_KINDS,
    SetForm,
    SetLine,
    TemplateForm,
    get_set_editor,
    get_template_editor,
    list_library_sets,
    list_library_templates,
    save_drill_set,
    save_template,
)
from src.layouts import not_found_page
from src.layouts_drills import removal_text
from src.layouts_sets import (
    ACTION_DOWN,
    ACTION_REMOVE,
    ACTION_UP,
    LINE_COUNT_MAX,
    LINE_COUNT_MIN,
    notice_children,
    set_drill_rows,
    set_form_layout,
    set_line_rows,
    set_section_styles,
    set_working,
    sets_layout,
    template_form_layout,
    template_set_options,
    template_set_rows,
    templates_layout,
    version_header_text,
)

logger = logging.getLogger(__name__)

SETS_PATH = "/drill-sets"
TEMPLATES_PATH = "/templates"
NAME_MAX_LENGTH = 100
DESCRIPTION_MAX_LENGTH = 4000
LIST_ACTIONS: frozenset[str] = frozenset({ACTION_UP, ACTION_DOWN, ACTION_REMOVE})

SET_CONSTRAINT_MESSAGES: dict[str, str] = {
    "ux_drill_sets_name": "A drill set with that name already exists.",
}
TEMPLATE_CONSTRAINT_MESSAGES: dict[str, str] = {
    "ux_practice_templates_name": "A practice template with that name already exists.",
    "ux_practice_template_sets_set": "A drill set can appear only once in a template.",
}

MESSAGE_SET_NAME = "Enter a drill set name of 1 to 100 characters."
MESSAGE_TEMPLATE_NAME = "Enter a template name of 1 to 100 characters."
MESSAGE_DESCRIPTION = "The description is limited to 4000 characters."
MESSAGE_KIND = "Choose Manual, Quick or Dynamic."
MESSAGE_NEEDS_DRILL = "Add at least one drill."
MESSAGE_NEEDS_LINE = "Add at least one line."
MESSAGE_NEEDS_SET = "Add at least one drill set."
MESSAGE_SET_TWICE = "A drill set can appear only once in a template."
MESSAGE_CHOOSE_DRILL = "Choose a drill to add."
MESSAGE_CHOOSE_SET = "Choose a drill set to add."
MESSAGE_LINE_COUNT = f"The number of drills must be {LINE_COUNT_MIN} to {LINE_COUNT_MAX}."
MESSAGE_LINE_DIFFICULTY = "Choose a difficulty for the line."
MESSAGE_LINE_TYPE = "Choose a type from the list, or any type."
MESSAGE_WORKING = "The form's list could not be read. Reload the page."
MESSAGE_SAVE_FAILED = "Could not save. Reference: {ref}"
MESSAGE_RELOAD_FAILED = "Saved, but the list could not be reloaded. Reference: {ref}"
MESSAGE_REMOVE_FAILED = "Could not remove. Reference: {ref}"
MESSAGE_LOAD_FAILED = "Could not load. Reference: {ref}"
MESSAGE_SAVED = "Saved."
MESSAGE_SAVED_VERSION = "Saved (version {version})."

HIDDEN_STYLE: dict[str, str] = {"display": "none"}
SHOWN_STYLE: dict[str, str] = {}


# --- Shared helpers ---

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


def _as_id(value: object) -> int | None:
    """
    Description:
        Reads an id: an int (not a bool), or a string of digits as dmc sends it.

    Parameters:
        value: a store value or a Select value.

    Return Values:
        int | None: the id, or None.

    Notes:
        Pure.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _trigger() -> tuple[object, object]:
    """
    Description:
        The running callback's trigger: its id and the value that triggered it.

    Parameters:
        None.

    Return Values:
        tuple[object, object]: (ctx.triggered_id, the triggering value or None).

    Notes:
        A re-rendered list fires ALL callbacks with an empty value; callers refuse those
        (plan §3.8.2).
    """
    value = ctx.triggered[0].get("value") if ctx.triggered else None
    return ctx.triggered_id, value


def _pattern_action(triggered: object, prefix: str) -> tuple[str, int] | None:
    """
    Description:
        Reads a list button's action and row from its pattern id.

    Parameters:
        triggered: ctx.triggered_id.
        prefix: the list's id prefix, e.g. "set-drill".

    Return Values:
        tuple[str, int] | None: (action, row index) when the trigger is one of the prefix's
        buttons, else None.

    Notes:
        Pure.
    """
    if not isinstance(triggered, dict):
        return None
    button_type = triggered.get("type")
    index = triggered.get("index")
    if not isinstance(button_type, str) or not button_type.startswith(prefix + "-"):
        return None
    action = button_type[len(prefix) + 1:]
    if action not in LIST_ACTIONS or not isinstance(index, int) or isinstance(index, bool):
        return None
    return action, index


def _clean_text(value: object, max_length: int) -> tuple[bool, str | None]:
    """
    Description:
        Strips a text field: empty means None; too long is refused.

    Parameters:
        value: the field's value.
        max_length: the longest allowed text.

    Return Values:
        tuple[bool, str | None]: (True, text or None) when acceptable, (False, None) when
        too long.

    Notes:
        Pure.
    """
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return True, None
    if len(text) > max_length:
        return False, None
    return True, text


def apply_list_action(items: list, action: str, index: int) -> list:
    """
    Description:
        Applies one list button to a working list: move the row up or down, or remove it.

    Parameters:
        items: the working list (drill ids, lines or set ids).
        action: "up", "down" or "remove".
        index: the row's 0-based position.

    Return Values:
        list: a new list; an unchanged copy when the move is impossible or the index is out
        of range.

    Notes:
        Pure.
    """
    result = list(items)
    if not 0 <= index < len(result):
        return result
    if action == ACTION_REMOVE:
        del result[index]
    elif action == ACTION_UP and index > 0:
        result[index - 1], result[index] = result[index], result[index - 1]
    elif action == ACTION_DOWN and index < len(result) - 1:
        result[index + 1], result[index] = result[index], result[index + 1]
    return result


# --- Page builders (REQ-051 to REQ-054, REQ-072) ---

def build_sets_page(
    player_id: int | None, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds the Drill sets screen.

    Parameters:
        player_id: the selected player, or None (unused: the library is not player-scoped,
            D5).
        clock: the device clock (unused).
        params: the route parameters (none).

    Return Values:
        Component: the page.

    Notes:
        Exceptions propagate to the router, which shows the error page with a reference.
    """
    return sets_layout(list_library_sets(data_handler.get_session()))


def _set_form_options() -> dict[str, list[dict]]:
    """
    Description:
        The option lists of the drill set form: the reference options plus the selectable
        drills.

    Parameters:
        None.

    Return Values:
        dict[str, list[dict]]: data_drills.list_reference_options plus "drills".

    Notes:
        Reads the request session.
    """
    session = data_handler.get_session()
    options = dict(list_reference_options(session))
    options["drills"] = list_library_drills(session)
    return options


def build_set_form_page(
    player_id: int | None, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds the drill set form: empty for /drill-sets/new, filled for /drill-sets/<id>.

    Parameters:
        player_id: the selected player, or None (unused, D5).
        clock: the device clock (unused).
        params: {} or {"drill_set_id": "<digits>"}.

    Return Values:
        Component: the page, or the not-found page when the set does not exist, is deleted
        or is retired (D10).

    Notes:
        The set-form-context store is written by the layout only after get_set_editor's
        live check.
    """
    options = _set_form_options()
    if "drill_set_id" not in params:
        return set_form_layout(None, options, None)
    session = data_handler.get_session()
    drill_set_id = int(params["drill_set_id"])
    editor = get_set_editor(session, drill_set_id)
    if editor is None:
        return not_found_page()
    return set_form_layout(
        editor, options, get_library_usage(session, ENTITY_DRILL_SET, drill_set_id)
    )


def build_templates_page(
    player_id: int | None, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds the Practice templates screen.

    Parameters:
        player_id: the selected player, or None (unused, D5).
        clock: the device clock (unused).
        params: the route parameters (none).

    Return Values:
        Component: the page.

    Notes:
        None.
    """
    return templates_layout(list_library_templates(data_handler.get_session()))


def build_template_form_page(
    player_id: int | None, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds the practice template form: empty for /templates/new, filled for
        /templates/<id>.

    Parameters:
        player_id: the selected player, or None (unused, D5).
        clock: the device clock (unused).
        params: {} or {"practice_template_id": "<digits>"}.

    Return Values:
        Component: the page, or the not-found page when the template does not exist, is
        deleted or is retired (D10).

    Notes:
        The tpl-form-context store is written by the layout only after
        get_template_editor's live check.
    """
    session = data_handler.get_session()
    sets = list_library_sets(session)
    if "practice_template_id" not in params:
        return template_form_layout(None, sets, None)
    template_id = int(params["practice_template_id"])
    editor = get_template_editor(session, template_id)
    if editor is None:
        return not_found_page()
    return template_form_layout(
        editor, sets, get_library_usage(session, ENTITY_PRACTICE_TEMPLATE, template_id)
    )


# --- Drill set: working copy (REQ-052 to REQ-054) ---

def _read_working(working: object) -> tuple[list[int], list[SetLine]] | None:
    """
    Description:
        Reads the set-working store.

    Parameters:
        working: set-working.data ({"drill_ids": [...], "lines": [...]}).

    Return Values:
        tuple[list[int], list[SetLine]] | None: (drill ids, lines), or None when the shape
        is wrong.

    Notes:
        Pure. The store is written only by this module, but the browser can alter it, so it
        is checked again here and by the database.
    """
    if not isinstance(working, dict):
        return None
    raw_ids = working.get("drill_ids")
    raw_lines = working.get("lines")
    if not isinstance(raw_ids, list) or not isinstance(raw_lines, list):
        return None
    drill_ids = [_as_id(value) for value in raw_ids]
    if any(drill_id is None for drill_id in drill_ids):
        return None
    lines: list[SetLine] = []
    for raw in raw_lines:
        if not isinstance(raw, dict):
            return None
        count = raw.get("drill_count")
        difficulty = raw.get("difficulty_code")
        type_id = raw.get("drill_type_id")
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or not LINE_COUNT_MIN <= count <= LINE_COUNT_MAX
            or not isinstance(difficulty, str)
            or (type_id is not None and _as_id(type_id) is None)
        ):
            return None
        lines.append(
            {
                "drill_count": count,
                "difficulty_code": difficulty,
                "drill_type_id": None if type_id is None else _as_id(type_id),
            }
        )
    return [drill_id for drill_id in drill_ids if drill_id is not None], lines


def _new_line(
    count: object, difficulty: object, drill_type: object, labels: dict
) -> tuple[SetLine | None, str | None]:
    """
    Description:
        Checks the Add line controls.

    Parameters:
        count: set-line-count.value.
        difficulty: set-line-difficulty.value.
        drill_type: set-line-type.value (None for any type).
        labels: the set-labels store data.

    Return Values:
        tuple[SetLine | None, str | None]: (the line, None) or (None, message).

    Notes:
        Pure.
    """
    if isinstance(count, float) and count.is_integer():
        count = int(count)
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or not LINE_COUNT_MIN <= count <= LINE_COUNT_MAX
    ):
        return None, MESSAGE_LINE_COUNT
    if not isinstance(difficulty, str) or difficulty not in labels.get("difficulties", {}):
        return None, MESSAGE_LINE_DIFFICULTY
    type_id = None
    if drill_type not in (None, ""):
        type_id = _as_id(drill_type)
        if type_id is None or str(type_id) not in labels.get("types", {}):
            return None, MESSAGE_LINE_TYPE
    return {"drill_count": count, "difficulty_code": difficulty, "drill_type_id": type_id}, None


def _apply_set_edit(
    triggered: object,
    drill_ids: list[int],
    lines: list[SetLine],
    controls: dict[str, object],
    labels: dict,
) -> tuple[list[int], list[SetLine], str]:
    """
    Description:
        Applies one edit of the set form to the working copy: a list button, Add drill or
        Add line.

    Parameters:
        triggered: ctx.triggered_id (the trigger's value was already checked).
        drill_ids: the working drill ids.
        lines: the working lines.
        controls: {"add_drill", "line_count", "line_difficulty", "line_type"} values.
        labels: the set-labels store data.

    Return Values:
        tuple[list[int], list[SetLine], str]: the new drill ids, the new lines and a
        feedback message ("" when the edit was applied).

    Notes:
        Pure. Raises PreventUpdate for a trigger it does not know.
    """
    drill_action = _pattern_action(triggered, "set-drill")
    if drill_action is not None:
        return apply_list_action(drill_ids, *drill_action), lines, ""
    line_action = _pattern_action(triggered, "set-line")
    if line_action is not None:
        return drill_ids, apply_list_action(lines, *line_action), ""
    if triggered == "set-add-drill-go":
        drill_id = _as_id(controls.get("add_drill"))
        if drill_id is None or str(drill_id) not in labels.get("drills", {}):
            return drill_ids, lines, MESSAGE_CHOOSE_DRILL
        return [*drill_ids, drill_id], lines, ""
    if triggered == "set-add-line-go":
        line, problem = _new_line(
            controls.get("line_count"),
            controls.get("line_difficulty"),
            controls.get("line_type"),
            labels,
        )
        if line is None:
            return drill_ids, lines, problem or ""
        return drill_ids, [*lines, line], ""
    raise PreventUpdate


def _set_kind_for(context: object, kind_value: object) -> str:
    """
    Description:
        The kind the set form shows: the saved set's own kind (D6), else the chosen one.

    Parameters:
        context: set-form-context.data ({"drill_set_id", "kind"}).
        kind_value: set-kind.value.

    Return Values:
        str: a set kind (manual when nothing valid is known).

    Notes:
        Pure.
    """
    if isinstance(context, dict) and context.get("kind") in SET_KINDS:
        return str(context["kind"])
    if kind_value in SET_KINDS:
        return str(kind_value)
    return KIND_MANUAL


def edit_set_working(
    drill_up: list,
    drill_down: list,
    drill_remove: list,
    line_up: list,
    line_down: list,
    line_remove: list,
    add_drill_clicks: int | None,
    add_line_clicks: int | None,
    kind_value: str | None,
    context: dict | None,
    working: dict | None,
    labels: dict | None,
    add_drill: str | None,
    line_count: object,
    line_difficulty: str | None,
    line_type: str | None,
) -> tuple[object, ...]:
    """
    Description:
        The only writer of the set's working copy and its lists: Up / Down / Remove on the
        drills and lines, Add drill, Add line, a kind change on a new set, and the reload
        after a save that wrote a new version (REQ-052 to REQ-054).

    Parameters:
        drill_up, drill_down, drill_remove, line_up, line_down, line_remove: the ALL list
            buttons' n_clicks (only the trigger matters).
        add_drill_clicks: set-add-drill-go.n_clicks.
        add_line_clicks: set-add-line-go.n_clicks.
        kind_value: set-kind.value.
        context: set-form-context.data; {"reload": True} asks for a reload from the
            database.
        working: set-working.data.
        labels: set-labels.data.
        add_drill: set-add-drill.value.
        line_count, line_difficulty, line_type: the Add line controls.

    Return Values:
        tuple[object, ...]: set-working.data, set-drill-list.children,
        set-line-list.children, the styles of set-section-drills, set-section-add-drill
        and set-section-lines, set-edit-feedback text, and set-add-drill.value (cleared
        after an add).

    Notes:
        Raises PreventUpdate unless a button was really clicked (plan §3.8.2). The reload is
        a boundary: a failed read shows a reference.
    """
    triggered, value = _trigger()
    if not value:
        raise PreventUpdate
    label_data = labels if isinstance(labels, dict) else {"drills": {}, "difficulties": {}, "types": {}}
    kind = _set_kind_for(context, kind_value)
    has_draw = isinstance(context, dict) and _as_id(context.get("drill_set_id")) is not None
    if triggered == "set-form-context":
        if not isinstance(context, dict) or not context.get("reload"):
            raise PreventUpdate
        drill_set_id = _as_id(context.get("drill_set_id"))
        try:
            editor = (
                get_set_editor(data_handler.get_session(), drill_set_id)
                if drill_set_id is not None
                else None
            )
        except Exception as exc:
            ref = new_reference_code()
            log_failure(logger, exc, "Drill set reload failed (ref %s)", ref)
            return (no_update,) * 6 + (MESSAGE_RELOAD_FAILED.format(ref=ref), no_update)
        if editor is None:
            raise PreventUpdate
        reloaded = set_working(editor)
        drill_ids, lines = reloaded["drill_ids"], reloaded["lines"]
        feedback = ""
    elif triggered == "set-kind":
        read = _read_working(working)
        if read is None:
            return (no_update,) * 6 + (MESSAGE_WORKING, no_update)
        drill_ids, lines = read
        feedback = ""
    else:
        read = _read_working(working)
        if read is None:
            return (no_update,) * 6 + (MESSAGE_WORKING, no_update)
        controls = {
            "add_drill": add_drill,
            "line_count": line_count,
            "line_difficulty": line_difficulty,
            "line_type": line_type,
        }
        drill_ids, lines, feedback = _apply_set_edit(triggered, *read, controls, label_data)
    styles = set_section_styles(kind, has_draw)
    return (
        {"drill_ids": drill_ids, "lines": lines},
        set_drill_rows(kind, drill_ids, label_data),
        set_line_rows(list(lines), label_data),
        *styles,
        feedback,
        None if triggered == "set-add-drill-go" and not feedback else no_update,
    )


# --- Drill set: save (REQ-051 to REQ-054, REQ-080) ---

def parse_set_form(
    name: object, description: object, kind_value: object, working: object, context: object
) -> tuple[SetForm | None, str | None]:
    """
    Description:
        Checks the drill set form before anything is written: name, description, kind and
        the contents its kind needs.

    Parameters:
        name: set-name.value.
        description: set-description.value.
        kind_value: set-kind.value (a saved set keeps its own kind, D6).
        working: set-working.data.
        context: set-form-context.data.

    Return Values:
        tuple[SetForm | None, str | None]: (the values, None) or (None, the message).

    Notes:
        Pure. tf_version_complete stays the backstop (plan §3.2).
    """
    name_ok, set_name = _clean_text(name, NAME_MAX_LENGTH)
    if not name_ok or set_name is None:
        return None, MESSAGE_SET_NAME
    description_ok, description_text = _clean_text(description, DESCRIPTION_MAX_LENGTH)
    if not description_ok:
        return None, MESSAGE_DESCRIPTION
    kind = _set_kind_for(context, kind_value)
    if kind_value not in SET_KINDS and not (isinstance(context, dict) and context.get("kind")):
        return None, MESSAGE_KIND
    read = _read_working(working)
    if read is None:
        return None, MESSAGE_WORKING
    drill_ids, lines = read
    if kind == KIND_MANUAL and not drill_ids:
        return None, MESSAGE_NEEDS_DRILL
    if kind in (KIND_QUICK, KIND_DYNAMIC) and not lines:
        return None, MESSAGE_NEEDS_LINE
    return {
        "set_name": set_name,
        "description": description_text,
        "set_kind": kind,
        "drill_ids": drill_ids if kind != KIND_DYNAMIC else [],
        "lines": lines if kind != KIND_MANUAL else [],
    }, None


def save_set_form(
    n_clicks: int | None,
    context: dict | None,
    name: str | None,
    description: str | None,
    kind_value: str | None,
    working: dict | None,
) -> tuple[object, ...]:
    """
    Description:
        The drill set form's Save: checks the form, saves the lineage and, when the
        contents changed, a new version (a quick set with new lines is drawn again; REQ-054,
        D13).

    Parameters:
        n_clicks: set-save.n_clicks.
        context: set-form-context.data ({"drill_set_id": LINEAGE id or None, "kind"}).
        name: set-name.value.
        description: set-description.value.
        kind_value: set-kind.value.
        working: set-working.data.

    Return Values:
        tuple[object, ...]: set-saved text, set-feedback text, set-notice.children,
        set-notice.style, set-form-header text, set-form-context.data ({..., "reload":
        True} after a new version, so edit_set_working reloads the lists) and url.pathname
        ("/drill-sets/<id>" after creating a set).

    Notes:
        A boundary (COD-4-04). A duplicate name shows its message; the database's refusals
        show their text (plan §3.2).
    """
    if not n_clicks:
        raise PreventUpdate
    drill_set_id = _as_id(context.get("drill_set_id")) if isinstance(context, dict) else None
    form, problem = parse_set_form(name, description, kind_value, working, context)
    if form is None:
        return ("", problem or "") + (no_update,) * 5
    checked_form: SetForm = form
    result, reason = run_write_with_reason(
        current_user.user_id,
        lambda session: save_drill_set(session, drill_set_id, checked_form),
        "save drill set",
        SET_CONSTRAINT_MESSAGES,
    )
    if not result.ok or result.value is None:
        failure = _failure_message(reason, result.ref, MESSAGE_SAVE_FAILED)
        return ("", failure) + (no_update,) * 5
    saved = result.value
    logger.info(
        "Saved drill set %s (version %s, new version %s)",
        saved["drill_set_id"], saved["version_no"], saved["new_version"],
    )
    notices = list(saved["notices"])
    notice_style = SHOWN_STYLE if notices else HIDDEN_STYLE
    if drill_set_id is None:
        return (
            "", "", notice_children(notices), notice_style, no_update, no_update,
            f"{SETS_PATH}/{saved['drill_set_id']}",
        )
    message = (
        MESSAGE_SAVED_VERSION.format(version=saved["version_no"])
        if saved["new_version"]
        else MESSAGE_SAVED
    )
    new_context = (
        {"drill_set_id": drill_set_id, "kind": checked_form["set_kind"], "reload": True}
        if saved["new_version"]
        else no_update
    )
    return (
        message,
        "",
        notice_children(notices),
        notice_style,
        _header_after_save(ENTITY_DRILL_SET, drill_set_id, saved["version_no"]),
        new_context,
        no_update,
    )


def _header_after_save(entity: str, lineage_id: int, version_no: object) -> object:
    """
    Description:
        A form's header after a save: "Version n · used in N practices".

    Parameters:
        entity: "drill_set" or "practice_template".
        lineage_id: the LINEAGE id.
        version_no: the saved latest version.

    Return Values:
        object: the header text, or no_update when the usage read fails (logged).

    Notes:
        A boundary helper.
    """
    try:
        usage = get_library_usage(data_handler.get_session(), entity, lineage_id)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Library usage read failed (ref %s)", ref)
        return no_update
    return version_header_text(version_no, usage["practice_count"])


# --- Practice template: working copy and save (REQ-072, REQ-080) ---

def _read_set_ids(working: object) -> list[int] | None:
    """
    Description:
        Reads the tpl-working store.

    Parameters:
        working: tpl-working.data ({"drill_set_ids": [...]}).

    Return Values:
        list[int] | None: the drill set ids, or None when the shape is wrong.

    Notes:
        Pure.
    """
    if not isinstance(working, dict) or not isinstance(working.get("drill_set_ids"), list):
        return None
    set_ids = [_as_id(value) for value in working["drill_set_ids"]]
    if any(set_id is None for set_id in set_ids):
        return None
    return [set_id for set_id in set_ids if set_id is not None]


def edit_template_working(
    set_up: list,
    set_down: list,
    set_remove: list,
    add_clicks: int | None,
    working: dict | None,
    labels: dict | None,
    current: list | None,
    add_set: str | None,
) -> tuple[object, ...]:
    """
    Description:
        The only writer of the template's working list: Up / Down / Remove and Add drill
        set, which offers only current sets not already listed (REQ-072, REQ-080).

    Parameters:
        set_up, set_down, set_remove: the ALL list buttons' n_clicks.
        add_clicks: tpl-add-set-go.n_clicks.
        working: tpl-working.data.
        labels: tpl-labels.data ({str(id): name}).
        current: tpl-current.data (the selectable set ids, in order).
        add_set: tpl-add-set.value.

    Return Values:
        tuple[object, ...]: tpl-working.data, tpl-set-list.children, tpl-add-set.data,
        tpl-add-set.value (cleared after an add) and tpl-edit-feedback text.

    Notes:
        Raises PreventUpdate unless a button was really clicked (plan §3.8.2).
    """
    triggered, value = _trigger()
    if not value:
        raise PreventUpdate
    set_ids = _read_set_ids(working)
    if set_ids is None:
        return (no_update,) * 4 + (MESSAGE_WORKING,)
    label_data = labels if isinstance(labels, dict) else {}
    current_ids = [set_id for set_id in (_as_id(v) for v in (current or [])) if set_id is not None]
    feedback = ""
    added = False
    action = _pattern_action(triggered, "tpl-set")
    if action is not None:
        set_ids = apply_list_action(set_ids, *action)
    elif triggered == "tpl-add-set-go":
        set_id = _as_id(add_set)
        if set_id is None or set_id not in current_ids:
            feedback = MESSAGE_CHOOSE_SET
        elif set_id in set_ids:
            feedback = MESSAGE_SET_TWICE
        else:
            set_ids = [*set_ids, set_id]
            added = True
    else:
        raise PreventUpdate
    return (
        {"drill_set_ids": set_ids},
        template_set_rows(set_ids, label_data),
        template_set_options(set_ids, label_data, current_ids),
        None if added else no_update,
        feedback,
    )


def parse_template_form(
    name: object, description: object, working: object
) -> tuple[TemplateForm | None, str | None]:
    """
    Description:
        Checks the practice template form: name, description and at least one drill set,
        each at most once.

    Parameters:
        name: tpl-name.value.
        description: tpl-description.value.
        working: tpl-working.data.

    Return Values:
        tuple[TemplateForm | None, str | None]: (the values, None) or (None, the message).

    Notes:
        Pure. ux_practice_template_sets_set and tf_version_complete stay the backstop.
    """
    name_ok, template_name = _clean_text(name, NAME_MAX_LENGTH)
    if not name_ok or template_name is None:
        return None, MESSAGE_TEMPLATE_NAME
    description_ok, description_text = _clean_text(description, DESCRIPTION_MAX_LENGTH)
    if not description_ok:
        return None, MESSAGE_DESCRIPTION
    set_ids = _read_set_ids(working)
    if set_ids is None:
        return None, MESSAGE_WORKING
    if not set_ids:
        return None, MESSAGE_NEEDS_SET
    if len(set(set_ids)) != len(set_ids):
        return None, MESSAGE_SET_TWICE
    return {
        "template_name": template_name,
        "description": description_text,
        "drill_set_ids": set_ids,
    }, None


def save_template_form(
    n_clicks: int | None,
    context: dict | None,
    name: str | None,
    description: str | None,
    working: dict | None,
) -> tuple[object, ...]:
    """
    Description:
        The template form's Save: checks the form, saves the lineage and, when the list of
        sets changed, a new version (REQ-072, REQ-080).

    Parameters:
        n_clicks: tpl-save.n_clicks.
        context: tpl-form-context.data ({"practice_template_id": LINEAGE id or None}).
        name: tpl-name.value.
        description: tpl-description.value.
        working: tpl-working.data.

    Return Values:
        tuple[object, ...]: tpl-saved text, tpl-feedback text, tpl-form-header text and
        url.pathname ("/templates/<id>" after creating a template).

    Notes:
        A boundary (COD-4-04).
    """
    if not n_clicks:
        raise PreventUpdate
    template_id = (
        _as_id(context.get("practice_template_id")) if isinstance(context, dict) else None
    )
    form, problem = parse_template_form(name, description, working)
    if form is None:
        return "", problem or "", no_update, no_update
    checked_form: TemplateForm = form
    result, reason = run_write_with_reason(
        current_user.user_id,
        lambda session: save_template(session, template_id, checked_form),
        "save practice template",
        TEMPLATE_CONSTRAINT_MESSAGES,
    )
    if not result.ok or result.value is None:
        return "", _failure_message(reason, result.ref, MESSAGE_SAVE_FAILED), no_update, no_update
    saved = result.value
    logger.info(
        "Saved practice template %s (version %s, new version %s)",
        saved["practice_template_id"], saved["version_no"], saved["new_version"],
    )
    if template_id is None:
        return "", "", no_update, f"{TEMPLATES_PATH}/{saved['practice_template_id']}"
    message = (
        MESSAGE_SAVED_VERSION.format(version=saved["version_no"])
        if saved["new_version"]
        else MESSAGE_SAVED
    )
    return (
        message,
        "",
        _header_after_save(ENTITY_PRACTICE_TEMPLATE, template_id, saved["version_no"]),
        no_update,
    )


# --- Removal (REQ-081, REQ-005) ---

def _open_removal(n_clicks: int | None, entity: str, lineage_id: object) -> tuple[bool, str, str]:
    """
    Description:
        Opens a set's or template's removal dialog with the retire-or-delete text.

    Parameters:
        n_clicks: the Remove button's n_clicks.
        entity: "drill_set" or "practice_template".
        lineage_id: the form-context LINEAGE id.

    Return Values:
        tuple[bool, str, str]: the dialog's opened, text and feedback ("").

    Notes:
        A boundary: a failed usage read opens the dialog with a reference.
    """
    if not n_clicks:
        raise PreventUpdate
    item_id = _as_id(lineage_id)
    if item_id is None:
        raise PreventUpdate
    try:
        usage = get_library_usage(data_handler.get_session(), entity, item_id)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Library usage read failed (ref %s)", ref)
        return True, MESSAGE_LOAD_FAILED.format(ref=ref), ""
    return True, removal_text(usage, with_goals=False), ""


def _confirm_removal(
    n_clicks: int | None, entity: str, lineage_id: object, list_path: str
) -> tuple[object, str]:
    """
    Description:
        Removes a set or template through fn_remove_library_item (deleted when unused,
        retired when used) and returns to its list.

    Parameters:
        n_clicks: the dialog confirm button's n_clicks.
        entity: "drill_set" or "practice_template".
        lineage_id: the form-context LINEAGE id.
        list_path: "/drill-sets" or "/templates".

    Return Values:
        tuple[object, str]: url.pathname (list_path on success, else no_update) and the
        dialog's feedback text.

    Notes:
        A boundary (COD-4-04).
    """
    if not n_clicks:
        raise PreventUpdate
    item_id = _as_id(lineage_id)
    if item_id is None:
        raise PreventUpdate
    result, reason = run_write_with_reason(
        current_user.user_id,
        lambda session: remove_library_item(session, entity, item_id),
        f"remove {entity}",
    )
    if not result.ok or result.value is None:
        return no_update, _failure_message(reason, result.ref, MESSAGE_REMOVE_FAILED)
    logger.info("Removed %s %s: %s", entity, item_id, result.value["action"])
    return list_path, ""


def open_remove_set(n_clicks: int | None, context: dict | None) -> tuple[bool, str, str]:
    """
    Description:
        Remove drill set: opens the confirmation (REQ-081).

    Parameters:
        n_clicks: set-remove.n_clicks.
        context: set-form-context.data.

    Return Values:
        tuple[bool, str, str]: set-remove-modal.opened, set-remove-text, set-remove-feedback.

    Notes:
        None.
    """
    lineage_id = context.get("drill_set_id") if isinstance(context, dict) else None
    return _open_removal(n_clicks, ENTITY_DRILL_SET, lineage_id)


def confirm_remove_set(n_clicks: int | None, context: dict | None) -> tuple[object, str]:
    """
    Description:
        The set dialog's Remove: deletes or retires the set, then opens the list.

    Parameters:
        n_clicks: set-remove-confirm.n_clicks.
        context: set-form-context.data.

    Return Values:
        tuple[object, str]: url.pathname and set-remove-feedback.

    Notes:
        None.
    """
    lineage_id = context.get("drill_set_id") if isinstance(context, dict) else None
    return _confirm_removal(n_clicks, ENTITY_DRILL_SET, lineage_id, SETS_PATH)


def open_remove_template(n_clicks: int | None, context: dict | None) -> tuple[bool, str, str]:
    """
    Description:
        Remove template: opens the confirmation (REQ-081).

    Parameters:
        n_clicks: tpl-remove.n_clicks.
        context: tpl-form-context.data.

    Return Values:
        tuple[bool, str, str]: tpl-remove-modal.opened, tpl-remove-text, tpl-remove-feedback.

    Notes:
        None.
    """
    lineage_id = context.get("practice_template_id") if isinstance(context, dict) else None
    return _open_removal(n_clicks, ENTITY_PRACTICE_TEMPLATE, lineage_id)


def confirm_remove_template(n_clicks: int | None, context: dict | None) -> tuple[object, str]:
    """
    Description:
        The template dialog's Remove: deletes or retires the template, then opens the list.

    Parameters:
        n_clicks: tpl-remove-confirm.n_clicks.
        context: tpl-form-context.data.

    Return Values:
        tuple[object, str]: url.pathname and tpl-remove-feedback.

    Notes:
        None.
    """
    lineage_id = context.get("practice_template_id") if isinstance(context, dict) else None
    return _confirm_removal(n_clicks, ENTITY_PRACTICE_TEMPLATE, lineage_id, TEMPLATES_PATH)


# --- Registration ---

def register_sets_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the drill set and template form callbacks: editing, saving and removal.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        url.pathname has earlier writers, so every navigating Output uses allow_duplicate;
        so do the dialogs' Cancel and the later writers of the feedback lines (plan
        §3.8.4).
    """
    _register_set_callbacks(app)
    _register_template_callbacks(app)


def _register_set_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the drill set form's edit, save and removal callbacks.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        None.
    """
    app.callback(
        Output("set-working", "data"),
        Output("set-drill-list", "children"),
        Output("set-line-list", "children"),
        Output("set-section-drills", "style"),
        Output("set-section-add-drill", "style"),
        Output("set-section-lines", "style"),
        Output("set-edit-feedback", "children"),
        Output("set-add-drill", "value"),
        Input({"type": "set-drill-up", "index": ALL}, "n_clicks"),
        Input({"type": "set-drill-down", "index": ALL}, "n_clicks"),
        Input({"type": "set-drill-remove", "index": ALL}, "n_clicks"),
        Input({"type": "set-line-up", "index": ALL}, "n_clicks"),
        Input({"type": "set-line-down", "index": ALL}, "n_clicks"),
        Input({"type": "set-line-remove", "index": ALL}, "n_clicks"),
        Input("set-add-drill-go", "n_clicks"),
        Input("set-add-line-go", "n_clicks"),
        Input("set-kind", "value"),
        Input("set-form-context", "data"),
        State("set-working", "data"),
        State("set-labels", "data"),
        State("set-add-drill", "value"),
        State("set-line-count", "value"),
        State("set-line-difficulty", "value"),
        State("set-line-type", "value"),
        prevent_initial_call=True,
    )(edit_set_working)
    app.callback(
        Output("set-saved", "children"),
        Output("set-feedback", "children"),
        Output("set-notice", "children"),
        Output("set-notice", "style"),
        Output("set-form-header", "children"),
        Output("set-form-context", "data"),
        Output("url", "pathname", allow_duplicate=True),
        Input("set-save", "n_clicks"),
        State("set-form-context", "data"),
        State("set-name", "value"),
        State("set-description", "value"),
        State("set-kind", "value"),
        State("set-working", "data"),
        prevent_initial_call=True,
    )(save_set_form)
    app.callback(
        Output("set-remove-modal", "opened"),
        Output("set-remove-text", "children"),
        Output("set-remove-feedback", "children"),
        Input("set-remove", "n_clicks"),
        State("set-form-context", "data"),
        prevent_initial_call=True,
    )(open_remove_set)
    app.clientside_callback(
        ClientsideFunction("hoopster", "close_modal"),
        Output("set-remove-modal", "opened", allow_duplicate=True),
        Input("set-remove-cancel", "n_clicks"),
        prevent_initial_call=True,
    )
    app.callback(
        Output("url", "pathname", allow_duplicate=True),
        Output("set-remove-feedback", "children", allow_duplicate=True),
        Input("set-remove-confirm", "n_clicks"),
        State("set-form-context", "data"),
        prevent_initial_call=True,
    )(confirm_remove_set)


def _register_template_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the template form's edit, save and removal callbacks.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        None.
    """
    app.callback(
        Output("tpl-working", "data"),
        Output("tpl-set-list", "children"),
        Output("tpl-add-set", "data"),
        Output("tpl-add-set", "value"),
        Output("tpl-edit-feedback", "children"),
        Input({"type": "tpl-set-up", "index": ALL}, "n_clicks"),
        Input({"type": "tpl-set-down", "index": ALL}, "n_clicks"),
        Input({"type": "tpl-set-remove", "index": ALL}, "n_clicks"),
        Input("tpl-add-set-go", "n_clicks"),
        State("tpl-working", "data"),
        State("tpl-labels", "data"),
        State("tpl-current", "data"),
        State("tpl-add-set", "value"),
        prevent_initial_call=True,
    )(edit_template_working)
    app.callback(
        Output("tpl-saved", "children"),
        Output("tpl-feedback", "children"),
        Output("tpl-form-header", "children"),
        Output("url", "pathname", allow_duplicate=True),
        Input("tpl-save", "n_clicks"),
        State("tpl-form-context", "data"),
        State("tpl-name", "value"),
        State("tpl-description", "value"),
        State("tpl-working", "data"),
        prevent_initial_call=True,
    )(save_template_form)
    app.callback(
        Output("tpl-remove-modal", "opened"),
        Output("tpl-remove-text", "children"),
        Output("tpl-remove-feedback", "children"),
        Input("tpl-remove", "n_clicks"),
        State("tpl-form-context", "data"),
        prevent_initial_call=True,
    )(open_remove_template)
    app.clientside_callback(
        ClientsideFunction("hoopster", "close_modal"),
        Output("tpl-remove-modal", "opened", allow_duplicate=True),
        Input("tpl-remove-cancel", "n_clicks"),
        prevent_initial_call=True,
    )
    app.callback(
        Output("url", "pathname", allow_duplicate=True),
        Output("tpl-remove-feedback", "children", allow_duplicate=True),
        Input("tpl-remove-confirm", "n_clicks"),
        State("tpl-form-context", "data"),
        prevent_initial_call=True,
    )(confirm_remove_template)
