"""Drill library controller of Hoopster: the Drill types, library and drill form page builders,
the pure filter and form check, and their callbacks (REQ-026, REQ-028, REQ-029, REQ-048,
REQ-076, REQ-077, REQ-079, REQ-080, REQ-081; REQ-005 confirmations).

Library ids are LINEAGE ids from the URL, a grid row id or a pattern id, never version ids
(COD-8-01). Every write goes through app_controller.run_write_with_reason, so the database's
own refusals and name clashes reach the screen (plan §3.2). Callback traps of plan §3.8:
prevent_initial_call everywhere, pattern buttons answer only a real click, later writers of a
property use allow_duplicate.
"""

import logging
from collections.abc import Mapping

from dash import ALL, MATCH, ClientsideFunction, Dash, Input, Output, State, ctx, no_update
from dash.development.base_component import Component
from dash.exceptions import PreventUpdate
from flask_login import current_user
from sqlalchemy.orm import Session

from src import data_handler
from src.app_controller import (
    DeviceClock,
    log_failure,
    new_reference_code,
    run_write_with_reason,
)
from src.data_drills import (
    ENTITY_DRILL,
    FREE_THROW_SPOT_ID,
    GRID_FIELD_DIFFICULTY,
    GRID_FIELD_NAME,
    DrillForm,
    OutcomeForm,
    OutcomeGroupForm,
    delete_drill_type,
    get_drill_editor,
    get_library_usage,
    insert_drill_type,
    list_drill_types_with_use,
    list_library_drills,
    list_reference_options,
    remove_library_item,
    rename_drill_type,
    save_drill,
    update_drill_lineage_field,
)
from src.layouts import not_found_page
from src.layouts_drills import (
    DIRECTION_LOWER,
    OUTCOME_BUTTON_SLOTS,
    OUTCOME_GROUP_SLOTS,
    PHYSICAL_UNIT_OPTIONS,
    RESULT_TYPE_PHYSICAL,
    SPOT_VALUE_FREE_THROW,
    SPOT_VALUE_PREFIX,
    CAPTURE_OUTCOMES,
    CAPTURE_SHOTS,
    drill_cards,
    drill_count_text,
    drill_form_layout,
    drill_grid_rows,
    drill_type_rows,
    drill_types_layout,
    drills_layout,
    removal_text,
    version_header_text,
)

logger = logging.getLogger(__name__)

DRILLS_PATH = "/drills"
GRID_COLUMN_NAME = "drill_name"
GRID_COLUMN_DIFFICULTY = "difficulty_label"
GRID_COLUMN_OPEN = "open"

NAME_MAX_LENGTH = 100
DESCRIPTION_MAX_LENGTH = 1000
VALUE_LABEL_MAX_LENGTH = 50
GROUP_NAME_MAX_LENGTH = 50
BUTTON_LABEL_MAX_LENGTH = 30
MIN_BUTTONS_PER_GROUP = 2
PHYSICAL_UNITS: frozenset[str] = frozenset(value for value, _ in PHYSICAL_UNIT_OPTIONS)

# Unique index -> message (plan §3.2).
DRILL_CONSTRAINT_MESSAGES: dict[str, str] = {
    "ux_drills_name": "That name belongs to an existing drill. Choose a different name.",
}
DRILL_TYPE_CONSTRAINT_MESSAGES: dict[str, str] = {
    "ux_drill_types_name": "A drill type with that name already exists.",
}

MESSAGE_TYPE_NAME = "Enter a drill type name of 1 to 100 characters."
MESSAGE_TYPE_GONE = "This drill type was deleted meanwhile. Reload the page."
MESSAGE_DRILL_NAME = "Enter a drill name of 1 to 100 characters."
MESSAGE_DESCRIPTION = "The description is limited to 1000 characters."
MESSAGE_PRIMARY_TYPE = "Choose a primary type."
MESSAGE_SECONDARY_TYPES = "Choose secondary types from the list."
MESSAGE_SECONDARY_IS_PRIMARY = "A secondary type cannot also be the primary type."
MESSAGE_DIFFICULTY = "Choose a difficulty."
MESSAGE_RESULT_TYPE = "Choose a result type."
MESSAGE_SYMMETRY = "Choose a symmetry type from the list, or none."
MESSAGE_TARGET = "The target must be a whole number of at least 1, or empty."
MESSAGE_FIXED_SECONDS = "Enter the fixed time in whole seconds (at least 1)."
MESSAGE_VALUE_1 = "Name the first number (1 to 50 characters) and choose its unit."
MESSAGE_VALUE_2 = "Choose a unit for the second number, or clear its name (1 to 50 characters)."
MESSAGE_SPOTS = "Choose at least one spot."
MESSAGE_GROUP_UNNAMED = "Name every outcome group that has buttons."
MESSAGE_GROUP_NAME = "Group names are limited to 50 characters."
MESSAGE_BUTTON_LABEL = "Button labels are limited to 30 characters."
MESSAGE_GROUPS = "An outcome drill needs 1 to 3 groups, each with 2 to 6 buttons."
MESSAGE_SAVE_FAILED = "Could not save. Reference: {ref}"
MESSAGE_LOAD_FAILED = "Could not load the drill library. Reference: {ref}"
MESSAGE_REMOVE_FAILED = "Could not remove. Reference: {ref}"
MESSAGE_SAVED = "Saved."
MESSAGE_SAVED_VERSION = "Saved (version {version})."
MESSAGE_GRID_SAVED = "Saved."
MESSAGE_GRID_DIFFICULTY = "Choose one of the four difficulties."
DELETE_TYPE_TEXT = 'Delete the drill type "{name}"? It can be restored for 30 days.'

FEEDBACK_OK_COLOR = "green"
FEEDBACK_ERROR_COLOR = "red"


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


def _clicked_index(button_type: str) -> int:
    """
    Description:
        The index of the pattern button that was really clicked (plan §3.8.2).

    Parameters:
        button_type: the pattern id's "type", e.g. "dtype-delete".

    Return Values:
        int: the clicked button's "index".

    Notes:
        Raises PreventUpdate when the trigger is not that button type, or its n_clicks is
        empty (a re-rendered list fires ALL callbacks with no click).
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


def _clean_name(value: object) -> str | None:
    """
    Description:
        Strips a name and checks it is 1-100 characters.

    Parameters:
        value: the field's value.

    Return Values:
        str | None: the stripped name, or None when empty or too long.

    Notes:
        Pure.
    """
    name = value.strip() if isinstance(value, str) else ""
    if not name or len(name) > NAME_MAX_LENGTH:
        return None
    return name


# --- Page builders (REQ-026, REQ-028, REQ-029) ---

def build_drill_types_page(
    player_id: int | None, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds the Drill types screen (REQ-029).

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
    return drill_types_layout(list_drill_types_with_use(data_handler.get_session()))


def build_drills_page(
    player_id: int | None, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds the drill library screen (REQ-026, REQ-048).

    Parameters:
        player_id: the selected player, or None (unused, D5).
        clock: the device clock (unused).
        params: the route parameters (none).

    Return Values:
        Component: the page.

    Notes:
        None.
    """
    session = data_handler.get_session()
    return drills_layout(list_library_drills(session), list_reference_options(session))


def build_drill_form_page(
    player_id: int | None, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds the drill form: empty for /drills/new, filled for /drills/<id>.

    Parameters:
        player_id: the selected player, or None (unused, D5).
        clock: the device clock (unused).
        params: {} for a new drill, {"drill_id": "<digits>"} for an existing one.

    Return Values:
        Component: the page, or the not-found page when the drill does not exist, is
        deleted or is retired (D10).

    Notes:
        The drill-form-id store is written by the layout only after get_drill_editor's live
        check (Context Adaptation Table).
    """
    session = data_handler.get_session()
    options = list_reference_options(session)
    if "drill_id" not in params:
        return drill_form_layout(None, options, None)
    drill_id = int(params["drill_id"])
    editor = get_drill_editor(session, drill_id)
    if editor is None:
        return not_found_page()
    usage = get_library_usage(session, ENTITY_DRILL, drill_id)
    return drill_form_layout(editor, options, usage)


# --- Pure helpers: library filter and drill form check ---

def filter_drills(
    rows: list[dict],
    search: object,
    drill_type_id: object,
    difficulty_code: object,
    result_type_code: object,
) -> list[dict]:
    """
    Description:
        Filters the library rows by the search text (in the name or description,
        case-insensitive), the primary type, the difficulty and the result type.

    Parameters:
        rows: rows of data_drills.list_library_drills.
        search: drills-search.value ("" or None for no search).
        drill_type_id: drills-filter-type.value, str(drill_type_id) or None for all.
        difficulty_code: drills-filter-difficulty.value or None for all.
        result_type_code: drills-filter-result.value or None for all.

    Return Values:
        list[dict]: the matching rows, in their order.

    Notes:
        Pure. The type filter matches the primary type, as the draws do (REQ-053).
    """
    needle = search.strip().casefold() if isinstance(search, str) else ""
    matches: list[dict] = []
    for row in rows:
        if drill_type_id and str(row["primary_drill_type_id"]) != str(drill_type_id):
            continue
        if difficulty_code and row["difficulty_code"] != difficulty_code:
            continue
        if result_type_code and row["result_type_code"] != result_type_code:
            continue
        if needle:
            haystack = f"{row['drill_name']} {row.get('description') or ''}".casefold()
            if needle not in haystack:
                continue
        matches.append(row)
    return matches


def _positive_int(value: object) -> tuple[bool, int | None]:
    """
    Description:
        Reads an optional whole number of at least 1 from a NumberInput.

    Parameters:
        value: the field's value (int, float, str or None; "" when cleared).

    Return Values:
        tuple[bool, int | None]: (True, None) when empty, (True, n) for a valid number,
        (False, None) otherwise.

    Notes:
        Pure. A bool is not a number.
    """
    if value is None or value == "":
        return True, None
    if isinstance(value, bool):
        return False, None
    number: float
    if isinstance(value, (int, float)):
        number = value
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        return False, None
    if number != int(number) or number < 1:
        return False, None
    return True, int(number)


def _label(value: object, max_length: int) -> tuple[bool, str | None]:
    """
    Description:
        Reads an optional label: stripped, empty means none.

    Parameters:
        value: the field's value.
        max_length: the longest allowed label.

    Return Values:
        tuple[bool, str | None]: (True, None) when empty, (True, label) when valid,
        (False, None) when too long.

    Notes:
        Pure.
    """
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return True, None
    if len(text) > max_length:
        return False, None
    return True, text


def _parse_spots(values: object, court_spot_ids: set[int]) -> list[int] | None:
    """
    Description:
        Reads the drill-spots picker: "s<court_spot_id>" or "ft", in pick order.

    Parameters:
        values: drill-spots.value.
        court_spot_ids: the active court spot ids offered.

    Return Values:
        list[int] | None: court spot ids in order, FREE_THROW_SPOT_ID for the free-throw
        line; None when empty, repeated or unknown.

    Notes:
        Pure.
    """
    if not isinstance(values, list) or not values:
        return None
    spot_ids: list[int] = []
    for value in values:
        if value == SPOT_VALUE_FREE_THROW:
            spot_id = FREE_THROW_SPOT_ID
        elif (
            isinstance(value, str)
            and value.startswith(SPOT_VALUE_PREFIX)
            and value[len(SPOT_VALUE_PREFIX):].isdigit()
        ):
            spot_id = int(value[len(SPOT_VALUE_PREFIX):])
            if spot_id not in court_spot_ids:
                return None
        else:
            return None
        if spot_id in spot_ids:
            return None
        spot_ids.append(spot_id)
    return spot_ids


def _parse_groups(
    group_names: Mapping[int, object],
    button_labels: Mapping[tuple[int, int], object],
    button_success: Mapping[tuple[int, int], object],
) -> tuple[list[OutcomeGroupForm] | None, str | None]:
    """
    Description:
        Reads the outcome-group slots: a group counts when its name is filled, a button when
        its label is filled; 1-3 groups of 2-6 buttons are needed (REQ-077).

    Parameters:
        group_names: group slot (1-3) -> og-name value.
        button_labels: (group slot, button slot) -> og-label value.
        button_success: (group slot, button slot) -> og-success checked.

    Return Values:
        tuple[list[OutcomeGroupForm] | None, str | None]: (groups, None) or (None, message).

    Notes:
        Pure. Empty slots between filled ones are skipped, so groups and buttons are
        numbered in screen order.
    """
    groups: list[OutcomeGroupForm] = []
    for group_no in range(1, OUTCOME_GROUP_SLOTS + 1):
        name_ok, name = _label(group_names.get(group_no), GROUP_NAME_MAX_LENGTH)
        if not name_ok:
            return None, MESSAGE_GROUP_NAME
        outcomes: list[OutcomeForm] = []
        for button_no in range(1, OUTCOME_BUTTON_SLOTS + 1):
            label_ok, label = _label(
                button_labels.get((group_no, button_no)), BUTTON_LABEL_MAX_LENGTH
            )
            if not label_ok:
                return None, MESSAGE_BUTTON_LABEL
            if label is not None:
                outcomes.append(
                    {
                        "label": label,
                        "is_success": button_success.get((group_no, button_no)) is True,
                    }
                )
        if name is None:
            if outcomes:
                return None, MESSAGE_GROUP_UNNAMED
            continue
        if not MIN_BUTTONS_PER_GROUP <= len(outcomes) <= OUTCOME_BUTTON_SLOTS:
            return None, MESSAGE_GROUPS
        groups.append({"group_name": name, "outcomes": outcomes})
    if not groups:
        return None, MESSAGE_GROUPS
    return groups, None


def _parse_physical(fields: Mapping[str, object]) -> tuple[dict[str, object] | None, str | None]:
    """
    Description:
        Reads a physical drill's numbers: the first is required, the second optional
        (REQ-076).

    Parameters:
        fields: the raw form values (keys v1_label, v1_unit, v1_higher, v2_label, v2_unit,
            v2_higher).

    Return Values:
        tuple[dict[str, object] | None, str | None]: (the six value_* fields, None) or
        (None, message).

    Notes:
        Pure. A direction other than "lower" means higher is better.
    """
    label_1_ok, label_1 = _label(fields.get("v1_label"), VALUE_LABEL_MAX_LENGTH)
    unit_1 = fields.get("v1_unit")
    if not label_1_ok or label_1 is None or unit_1 not in PHYSICAL_UNITS:
        return None, MESSAGE_VALUE_1
    label_2_ok, label_2 = _label(fields.get("v2_label"), VALUE_LABEL_MAX_LENGTH)
    unit_2 = fields.get("v2_unit")
    if not label_2_ok or (label_2 is not None and unit_2 not in PHYSICAL_UNITS):
        return None, MESSAGE_VALUE_2
    return {
        "value_1_label": label_1,
        "value_1_unit": unit_1,
        "value_1_higher_is_better": fields.get("v1_higher") != DIRECTION_LOWER,
        "value_2_label": label_2,
        "value_2_unit": unit_2 if label_2 is not None else None,
        "value_2_higher_is_better": (
            fields.get("v2_higher") != DIRECTION_LOWER if label_2 is not None else None
        ),
    }, None


def _parse_lineage(
    fields: Mapping[str, object], options: Mapping[str, list[dict]]
) -> tuple[dict[str, object] | None, str | None]:
    """
    Description:
        Reads the lineage fields of the drill form: name, description, primary and
        secondary types, difficulty (REQ-028).

    Parameters:
        fields: the raw form values (keys name, description, primary_type,
            secondary_types, difficulty).
        options: data_drills.list_reference_options.

    Return Values:
        tuple[dict[str, object] | None, str | None]: (the lineage fields of DrillForm,
        None) or (None, message).

    Notes:
        Pure.
    """
    name = _clean_name(fields.get("name"))
    if name is None:
        return None, MESSAGE_DRILL_NAME
    description_ok, description = _label(fields.get("description"), DESCRIPTION_MAX_LENGTH)
    if not description_ok:
        return None, MESSAGE_DESCRIPTION
    type_ids = {str(row["drill_type_id"]): int(row["drill_type_id"]) for row in options["drill_types"]}
    primary = type_ids.get(str(fields.get("primary_type")))
    if primary is None:
        return None, MESSAGE_PRIMARY_TYPE
    raw_secondary = fields.get("secondary_types") or []
    if not isinstance(raw_secondary, list) or any(str(v) not in type_ids for v in raw_secondary):
        return None, MESSAGE_SECONDARY_TYPES
    secondary = list(dict.fromkeys(type_ids[str(value)] for value in raw_secondary))
    if primary in secondary:
        return None, MESSAGE_SECONDARY_IS_PRIMARY
    difficulty = fields.get("difficulty")
    if difficulty not in {row["difficulty_code"] for row in options["difficulties"]}:
        return None, MESSAGE_DIFFICULTY
    return {
        "drill_name": name,
        "description": description,
        "primary_drill_type_id": primary,
        "secondary_type_ids": secondary,
        "difficulty_code": str(difficulty),
    }, None


def parse_drill_form(
    fields: Mapping[str, object], options: Mapping[str, list[dict]]
) -> tuple[DrillForm | None, str | None]:
    """
    Description:
        Checks the drill form for shape and completeness before anything is written
        (plan §3.4), in screen order. Sections the result type hides are ignored.

    Parameters:
        fields: the raw form values: name, description, primary_type, secondary_types,
            difficulty, result_type, symmetry, target, fixed_seconds, v1_label, v1_unit,
            v1_higher, v2_label, v2_unit, v2_higher, spots, group_names ({slot: value}),
            button_labels ({(group, button): value}), button_success ({(group, button):
            checked}).
        options: data_drills.list_reference_options (the allowed codes, ids and spots).

    Return Values:
        tuple[DrillForm | None, str | None]: (the values, None) when acceptable, else
        (None, the message to show).

    Notes:
        Pure. The database's checks (tf_version_guard, tf_version_complete, the unique
        name) stay the backstop.
    """
    lineage, problem = _parse_lineage(fields, options)
    if lineage is None:
        return None, problem
    result_meta = {str(row["result_type_code"]): row for row in options["result_types"]}
    result_type = fields.get("result_type")
    if not isinstance(result_type, str) or result_type not in result_meta:
        return None, MESSAGE_RESULT_TYPE
    facts = result_meta[result_type]
    symmetry = fields.get("symmetry") or None
    if symmetry is not None and symmetry not in {
        row["symmetry_type_code"] for row in options["symmetry_types"]
    }:
        return None, MESSAGE_SYMMETRY

    form: DrillForm = {
        **lineage,  # type: ignore[typeddict-item]
        "result_type_code": result_type,
        "symmetry_type_code": None if symmetry is None else str(symmetry),
        "default_target": None,
        "fixed_seconds": None,
        "value_1_label": None,
        "value_1_unit": None,
        "value_1_higher_is_better": None,
        "value_2_label": None,
        "value_2_unit": None,
        "value_2_higher_is_better": None,
        "spot_ids": [],
        "groups": [],
    }
    if result_type == RESULT_TYPE_PHYSICAL:
        values, problem = _parse_physical(fields)
        if values is None:
            return None, problem
        form.update(values)  # type: ignore[typeddict-item]
    else:
        target_ok, target = _positive_int(fields.get("target"))
        if not target_ok:
            return None, MESSAGE_TARGET
        form["default_target"] = target
    if facts["needs_fixed_seconds"]:
        seconds_ok, seconds = _positive_int(fields.get("fixed_seconds"))
        if not seconds_ok or seconds is None:
            return None, MESSAGE_FIXED_SECONDS
        form["fixed_seconds"] = seconds
    if facts["capture_kind"] == CAPTURE_SHOTS:
        spot_ids = _parse_spots(
            fields.get("spots"), {int(row["court_spot_id"]) for row in options["court_spots"]}
        )
        if spot_ids is None:
            return None, MESSAGE_SPOTS
        form["spot_ids"] = spot_ids
    if facts["capture_kind"] == CAPTURE_OUTCOMES:
        groups, problem = _parse_groups(
            fields.get("group_names") or {},  # type: ignore[arg-type]
            fields.get("button_labels") or {},  # type: ignore[arg-type]
            fields.get("button_success") or {},  # type: ignore[arg-type]
        )
        if groups is None:
            return None, problem
        form["groups"] = groups
    return form, None


def _pattern_state(type_name: str) -> list[dict]:
    """
    Description:
        The wildcard State entries of one pattern type in the running callback: each
        {"id": {...}, "property": ..., "value": ...}.

    Parameters:
        type_name: the pattern id's "type", e.g. "og-label".

    Return Values:
        list[dict]: the entries, or [] when the page holds none.

    Notes:
        Reads dash.ctx.states_list, which keeps every component's id, so values are paired
        with their slot rather than with their position on the page.
    """
    for state in ctx.states_list:
        if isinstance(state, list) and state:
            first_id = state[0].get("id")
            if isinstance(first_id, dict) and first_id.get("type") == type_name:
                return state
    return []


def _outcome_fields() -> dict[str, object]:
    """
    Description:
        Collects the outcome-group slots of the running save callback by slot number.

    Parameters:
        None.

    Return Values:
        dict[str, object]: {"group_names": {group: value}, "button_labels": {(group,
        button): value}, "button_success": {(group, button): checked}}.

    Notes:
        Call only inside save_drill_form.
    """
    group_names = {
        entry["id"]["index"]: entry.get("value") for entry in _pattern_state("og-name")
    }
    button_labels = {
        (entry["id"]["group"], entry["id"]["index"]): entry.get("value")
        for entry in _pattern_state("og-label")
    }
    button_success = {
        (entry["id"]["group"], entry["id"]["index"]): entry.get("value")
        for entry in _pattern_state("og-success")
    }
    return {
        "group_names": group_names,
        "button_labels": button_labels,
        "button_success": button_success,
    }


# --- Drill type callbacks (REQ-029, REQ-005) ---

def add_drill_type(n_clicks: int | None, name: str | None) -> tuple[object, str, object]:
    """
    Description:
        The Add drill type callback: checks the name, adds the type and rebuilds the list.

    Parameters:
        n_clicks: dtype-add.n_clicks.
        name: dtype-new-name.value.

    Return Values:
        tuple[object, str, object]: dtype-rows.children (no_update unless added),
        dtype-add-feedback text, dtype-new-name.value ("" after an add).

    Notes:
        A boundary (COD-4-04). A duplicate name shows the ux_drill_types_name message.
    """
    if not n_clicks:
        raise PreventUpdate
    type_name = _clean_name(name)
    if type_name is None:
        return no_update, MESSAGE_TYPE_NAME, no_update
    result, reason = run_write_with_reason(
        current_user.user_id,
        lambda session: insert_drill_type(session, type_name),
        "add drill type",
        DRILL_TYPE_CONSTRAINT_MESSAGES,
    )
    if not result.ok:
        return no_update, _failure_message(reason, result.ref, MESSAGE_SAVE_FAILED), no_update
    logger.info("Added drill type %s", result.value)
    return _reload_type_rows(), "", ""


def _reload_type_rows() -> object:
    """
    Description:
        Re-reads the drill types for the list after a write.

    Parameters:
        None.

    Return Values:
        object: the dtype-rows children, or no_update when the read fails (logged with a
        reference).

    Notes:
        A boundary helper: the write already succeeded, so a failed reload only leaves the
        old list on screen.
    """
    try:
        return drill_type_rows(list_drill_types_with_use(data_handler.get_session()))
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Drill types reload failed (ref %s)", ref)
        return no_update


def save_drill_type(n_clicks: int | None, name: str | None) -> tuple[str, str]:
    """
    Description:
        The Save button of one drill type row: renames the type in place.

    Parameters:
        n_clicks: {"type": "dtype-save", "index": MATCH}.n_clicks.
        name: the same row's dtype-name value.

    Return Values:
        tuple[str, str]: the row's dtype-row-feedback text and colour.

    Notes:
        A boundary (COD-4-04). The type id is the clicked button's index.
    """
    if not n_clicks:
        raise PreventUpdate
    triggered = ctx.triggered_id
    if not isinstance(triggered, dict) or not isinstance(triggered.get("index"), int):
        raise PreventUpdate
    drill_type_id: int = triggered["index"]
    type_name = _clean_name(name)
    if type_name is None:
        return MESSAGE_TYPE_NAME, FEEDBACK_ERROR_COLOR

    def work(session: Session) -> None:
        """
        Description:
            Renames the type, failing when it is no longer live.

        Parameters:
            session: the write session.

        Return Values:
            None.

        Notes:
            The LookupError rolls back and gives the user a reference.
        """
        if not rename_drill_type(session, drill_type_id, type_name):
            raise LookupError(f"drill type {drill_type_id} is not live")

    result, reason = run_write_with_reason(
        current_user.user_id, work, "rename drill type", DRILL_TYPE_CONSTRAINT_MESSAGES
    )
    if not result.ok:
        return _failure_message(reason, result.ref, MESSAGE_SAVE_FAILED), FEEDBACK_ERROR_COLOR
    logger.info("Renamed drill type %s", drill_type_id)
    return MESSAGE_SAVED, FEEDBACK_OK_COLOR


def open_delete_type(clicks: list) -> tuple[object, object, object, str]:
    """
    Description:
        A row's Delete button: opens the confirmation with the type's name (REQ-005).

    Parameters:
        clicks: every {"type": "dtype-delete"} n_clicks (only the trigger matters).

    Return Values:
        tuple[object, object, object, str]: dtype-del-modal.opened, dtype-del-text,
        dtype-del-target.data (the drill_type_id) and dtype-del-feedback ("").

    Notes:
        Raises PreventUpdate unless a Delete button was really clicked (plan §3.8.2). A type
        gone meanwhile shows a message instead of the dialog.
    """
    drill_type_id = _clicked_index("dtype-delete")
    try:
        types = list_drill_types_with_use(data_handler.get_session())
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Drill type lookup failed (ref %s)", ref)
        return True, MESSAGE_LOAD_FAILED.format(ref=ref), None, ""
    match = next((row for row in types if int(row["drill_type_id"]) == drill_type_id), None)
    if match is None:
        return True, MESSAGE_TYPE_GONE, None, ""
    return True, DELETE_TYPE_TEXT.format(name=match["type_name"]), drill_type_id, ""


def confirm_delete_type(n_clicks: int | None, target: object) -> tuple[object, object, str]:
    """
    Description:
        The dialog's Delete button: soft-deletes the type through fn_soft_delete, which
        refuses a type still in use with its own message (plan §3.2).

    Parameters:
        n_clicks: dtype-del-confirm.n_clicks.
        target: dtype-del-target.data, the drill_type_id written by open_delete_type.

    Return Values:
        tuple[object, object, str]: dtype-rows.children (reloaded on success),
        dtype-del-modal.opened (False on success) and dtype-del-feedback text.

    Notes:
        A boundary (COD-4-04).
    """
    if not n_clicks:
        raise PreventUpdate
    drill_type_id = _stored_id(target)
    if drill_type_id is None:
        return no_update, no_update, MESSAGE_TYPE_GONE
    result, reason = run_write_with_reason(
        current_user.user_id,
        lambda session: delete_drill_type(session, drill_type_id),
        "delete drill type",
    )
    if not result.ok:
        return no_update, no_update, _failure_message(reason, result.ref, MESSAGE_REMOVE_FAILED)
    logger.info("Deleted drill type %s", drill_type_id)
    return _reload_type_rows(), False, ""


# --- Library callbacks (REQ-026, REQ-048, D11) ---

def filter_library(
    search: str | None,
    drill_type_id: str | None,
    difficulty_code: str | None,
    result_type_code: str | None,
) -> tuple[object, object, object, str]:
    """
    Description:
        Re-reads the library and fills both views with the drills that match the filters.

    Parameters:
        search: drills-search.value.
        drill_type_id: drills-filter-type.value.
        difficulty_code: drills-filter-difficulty.value.
        result_type_code: drills-filter-result.value.

    Return Values:
        tuple[object, object, object, str]: drills-cards.children, drills-grid.rowData,
        drills-count text and drills-grid-feedback text.

    Notes:
        A boundary: a failed read keeps the views and shows a reference.
    """
    try:
        rows = list_library_drills(data_handler.get_session())
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Drill library read failed (ref %s)", ref)
        return no_update, no_update, no_update, MESSAGE_LOAD_FAILED.format(ref=ref)
    matches = filter_drills(rows, search, drill_type_id, difficulty_code, result_type_code)
    return (
        drill_cards(matches),
        drill_grid_rows(matches),
        drill_count_text(len(matches)),
        "",
    )


def _grid_change(changes: object) -> dict | None:
    """
    Description:
        The latest in-cell edit of the grid's cellValueChanged list.

    Parameters:
        changes: drills-grid.cellValueChanged (a list of change dicts).

    Return Values:
        dict | None: the last change, or None when there is none.

    Notes:
        Pure.
    """
    if isinstance(changes, list) and changes and isinstance(changes[-1], dict):
        return changes[-1]
    if isinstance(changes, dict):
        return changes
    return None


def _grid_new_value(change: dict) -> object:
    """
    Description:
        The edited cell's new value from one cellValueChanged entry.

    Parameters:
        change: one change dict from drills-grid.cellValueChanged.

    Return Values:
        object: the new value, or None when the entry carries none.

    Notes:
        Pure. dash-ag-grid 35.3.0 sends the new value under "value" (async-community.js
        builds {rowIndex, rowId, data, oldValue, value, colId, timestamp}); its Python
        docstring says "newValue", so that key is kept only as a fallback.
    """
    if "value" in change:
        return change.get("value")
    return change.get("newValue")


def save_drill_cell(
    changes: object,
    difficulty_codes: dict | None,
    search: str | None,
    drill_type_id: str | None,
    difficulty_code: str | None,
    result_type_code: str | None,
) -> tuple[str, object]:
    """
    Description:
        Saves an in-cell edit of the library grid on the drill lineage: the name or the
        difficulty (D11). Recording fields are versioned through the form only.

    Parameters:
        changes: drills-grid.cellValueChanged.
        difficulty_codes: drills-difficulty-codes.data ({label: code}).
        search, drill_type_id, difficulty_code, result_type_code: the current filters, to
            reload the grid after a failure.

    Return Values:
        tuple[str, object]: drills-grid-feedback text and drills-grid.rowData (reloaded on
        failure so the cell shows the stored value, else no_update).

    Notes:
        A boundary (COD-4-04). The drill is the grid row id (str(drill_id), the LINEAGE id).
        A duplicate name shows the REQ-028 message.
    """
    change = _grid_change(changes)
    if change is None:
        raise PreventUpdate
    column = change.get("colId")
    if column not in (GRID_COLUMN_NAME, GRID_COLUMN_DIFFICULTY):
        raise PreventUpdate
    row_id = change.get("rowId")
    if not isinstance(row_id, str) or not row_id.isdigit():
        raise PreventUpdate
    drill_id = int(row_id)
    filters = (search, drill_type_id, difficulty_code, result_type_code)
    if column == GRID_COLUMN_NAME:
        field, value = GRID_FIELD_NAME, _clean_name(_grid_new_value(change))
        problem = MESSAGE_DRILL_NAME
    else:
        codes = difficulty_codes if isinstance(difficulty_codes, dict) else {}
        field, value = GRID_FIELD_DIFFICULTY, codes.get(str(_grid_new_value(change)))
        problem = MESSAGE_GRID_DIFFICULTY
    if value is None:
        return problem, _reload_grid(*filters)

    checked_value: str = value

    def work(session: Session) -> None:
        """
        Description:
            Saves the one field, failing when the drill is no longer live or is retired.

        Parameters:
            session: the write session.

        Return Values:
            None.

        Notes:
            None.
        """
        if not update_drill_lineage_field(session, drill_id, field, checked_value):
            raise LookupError(f"drill {drill_id} is not live or is retired")

    result, reason = run_write_with_reason(
        current_user.user_id, work, "grid edit drill", DRILL_CONSTRAINT_MESSAGES
    )
    if not result.ok:
        return _failure_message(reason, result.ref, MESSAGE_SAVE_FAILED), _reload_grid(*filters)
    logger.info("Grid edit of drill %s (%s)", drill_id, field)
    return MESSAGE_GRID_SAVED, no_update


def _reload_grid(
    search: str | None,
    drill_type_id: str | None,
    difficulty_code: str | None,
    result_type_code: str | None,
) -> object:
    """
    Description:
        Re-reads the library rows for the grid with the current filters.

    Parameters:
        search, drill_type_id, difficulty_code, result_type_code: the filters.

    Return Values:
        object: the rowData, or no_update when the read fails (logged with a reference).

    Notes:
        A boundary helper.
    """
    try:
        rows = list_library_drills(data_handler.get_session())
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Drill grid reload failed (ref %s)", ref)
        return no_update
    return drill_grid_rows(
        filter_drills(rows, search, drill_type_id, difficulty_code, result_type_code)
    )


def open_drill_from_grid(cell: dict | None) -> str:
    """
    Description:
        A click on a row's Open cell opens that drill's form.

    Parameters:
        cell: drills-grid.cellClicked ({"colId", "rowId", ...}).

    Return Values:
        str: url.pathname "/drills/<drill_id>".

    Notes:
        Raises PreventUpdate for any other cell (plan §3.8.1).
    """
    if not isinstance(cell, dict) or cell.get("colId") != GRID_COLUMN_OPEN:
        raise PreventUpdate
    row_id = cell.get("rowId")
    if not isinstance(row_id, str) or not row_id.isdigit():
        raise PreventUpdate
    return f"{DRILLS_PATH}/{int(row_id)}"


# --- Drill form callbacks (REQ-028, REQ-076, REQ-077, REQ-079, REQ-080) ---

def save_drill_form(
    n_clicks: int | None,
    drill_id_store: object,
    name: object,
    description: object,
    primary_type: object,
    secondary_types: object,
    difficulty: object,
    result_type: object,
    symmetry: object,
    target: object,
    fixed_seconds: object,
    v1_label: object,
    v1_unit: object,
    v1_higher: object,
    v2_label: object,
    v2_unit: object,
    v2_higher: object,
    spots: object,
    group_names: object,
    button_labels: object,
    button_success: object,
) -> tuple[str, str, object, object]:
    """
    Description:
        The drill form's Save: checks the form, then saves it in one transaction (new
        drill, lineage edit, new version or label-only update; REQ-080, D12).

    Parameters:
        n_clicks: drill-save.n_clicks.
        drill_id_store: drill-form-id.data, the drill LINEAGE id or None for a new drill.
        name ... spots: the form fields (drill-name ... drill-spots values).
        group_names, button_labels, button_success: the ALL og-name, og-label and
            og-success values (read with their ids through dash.ctx.states_list).

    Return Values:
        tuple[str, str, object, object]: drill-saved text, drill-feedback text,
        url.pathname ("/drills/<id>" after creating a drill, else no_update) and
        drill-form-header text (refreshed after an edit).

    Notes:
        A boundary (COD-4-04). A name clash shows the REQ-028 message; the database's own
        refusals show their text (plan §3.2).
    """
    if not n_clicks:
        raise PreventUpdate
    drill_id = _stored_id(drill_id_store)
    try:
        options = list_reference_options(data_handler.get_session())
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Drill form options read failed (ref %s)", ref)
        return "", MESSAGE_SAVE_FAILED.format(ref=ref), no_update, no_update
    fields: dict[str, object] = {
        "name": name,
        "description": description,
        "primary_type": primary_type,
        "secondary_types": secondary_types,
        "difficulty": difficulty,
        "result_type": result_type,
        "symmetry": symmetry,
        "target": target,
        "fixed_seconds": fixed_seconds,
        "v1_label": v1_label,
        "v1_unit": v1_unit,
        "v1_higher": v1_higher,
        "v2_label": v2_label,
        "v2_unit": v2_unit,
        "v2_higher": v2_higher,
        "spots": spots,
        **_outcome_fields(),
    }
    form, problem = parse_drill_form(fields, options)
    if form is None:
        return "", problem or "", no_update, no_update
    checked_form: DrillForm = form
    result, reason = run_write_with_reason(
        current_user.user_id,
        lambda session: save_drill(session, drill_id, checked_form),
        "save drill",
        DRILL_CONSTRAINT_MESSAGES,
    )
    if not result.ok or result.value is None:
        return "", _failure_message(reason, result.ref, MESSAGE_SAVE_FAILED), no_update, no_update
    saved = result.value
    logger.info(
        "Saved drill %s (version %s, new version %s)",
        saved["drill_id"], saved["version_no"], saved["new_version"],
    )
    if drill_id is None:
        return "", "", f"{DRILLS_PATH}/{saved['drill_id']}", no_update
    return (
        MESSAGE_SAVED_VERSION.format(version=saved["version_no"]),
        "",
        no_update,
        _header_after_save(drill_id, saved["version_no"]),
    )


def _header_after_save(drill_id: int, version_no: object) -> object:
    """
    Description:
        The drill form's header after a save: "Version n · used in N practices".

    Parameters:
        drill_id: the drill LINEAGE id.
        version_no: the saved latest version.

    Return Values:
        object: the header text, or no_update when the usage read fails (logged).

    Notes:
        A boundary helper.
    """
    try:
        usage = get_library_usage(data_handler.get_session(), ENTITY_DRILL, drill_id)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Drill usage read failed (ref %s)", ref)
        return no_update
    return version_header_text(version_no, usage["practice_count"])


# --- Removal callbacks (REQ-081, REQ-005) ---

def open_remove_drill(n_clicks: int | None, drill_id_store: object) -> tuple[bool, str, str]:
    """
    Description:
        Remove drill: opens the confirmation saying whether the drill will be retired
        ("Used in N practices ... and M goals") or deleted (REQ-081).

    Parameters:
        n_clicks: drill-remove.n_clicks.
        drill_id_store: drill-form-id.data, the drill LINEAGE id.

    Return Values:
        tuple[bool, str, str]: drill-remove-modal.opened, drill-remove-text and
        drill-remove-feedback ("").

    Notes:
        A boundary: a failed usage read opens the dialog with a reference instead.
    """
    if not n_clicks:
        raise PreventUpdate
    drill_id = _stored_id(drill_id_store)
    if drill_id is None:
        raise PreventUpdate
    try:
        usage = get_library_usage(data_handler.get_session(), ENTITY_DRILL, drill_id)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Drill usage read failed (ref %s)", ref)
        return True, MESSAGE_LOAD_FAILED.format(ref=ref), ""
    return True, removal_text(usage, with_goals=True), ""


def confirm_remove_drill(n_clicks: int | None, drill_id_store: object) -> tuple[object, str]:
    """
    Description:
        The dialog's Remove: deletes an unused drill or retires a used one through
        fn_remove_library_item, then returns to the library.

    Parameters:
        n_clicks: drill-remove-confirm.n_clicks.
        drill_id_store: drill-form-id.data, the drill LINEAGE id.

    Return Values:
        tuple[object, str]: url.pathname ("/drills" on success, else no_update) and
        drill-remove-feedback text.

    Notes:
        A boundary (COD-4-04). url lives in the shell, so the click guard matters
        (plan §3.8.1).
    """
    if not n_clicks:
        raise PreventUpdate
    drill_id = _stored_id(drill_id_store)
    if drill_id is None:
        raise PreventUpdate
    result, reason = run_write_with_reason(
        current_user.user_id,
        lambda session: remove_library_item(session, ENTITY_DRILL, drill_id),
        "remove drill",
    )
    if not result.ok or result.value is None:
        return no_update, _failure_message(reason, result.ref, MESSAGE_REMOVE_FAILED)
    logger.info("Removed drill %s: %s", drill_id, result.value["action"])
    return DRILLS_PATH, ""


# --- Registration ---

def register_drills_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the Drill types, library and drill form callbacks.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        url.pathname has earlier writers, so every navigating Output uses allow_duplicate.
        Later writers of dtype-rows, drills-grid.rowData, drills-grid-feedback and the
        dialogs' opened use allow_duplicate (plan §3.8.4). Nothing is added to
        PUBLIC_CALLBACK_OUTPUT_IDS.
    """
    _register_drill_type_callbacks(app)
    _register_library_callbacks(app)
    _register_form_callbacks(app)


def _register_drill_type_callbacks(app: Dash) -> None:
    """
    Description:
        Registers Add, Save (per row), Delete (open, cancel, confirm) of the Drill types
        screen.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        None.
    """
    app.callback(
        Output("dtype-rows", "children"),
        Output("dtype-add-feedback", "children"),
        Output("dtype-new-name", "value"),
        Input("dtype-add", "n_clicks"),
        State("dtype-new-name", "value"),
        prevent_initial_call=True,
    )(add_drill_type)
    app.callback(
        Output({"type": "dtype-row-feedback", "index": MATCH}, "children"),
        Output({"type": "dtype-row-feedback", "index": MATCH}, "c"),
        Input({"type": "dtype-save", "index": MATCH}, "n_clicks"),
        State({"type": "dtype-name", "index": MATCH}, "value"),
        prevent_initial_call=True,
    )(save_drill_type)
    app.callback(
        Output("dtype-del-modal", "opened"),
        Output("dtype-del-text", "children"),
        Output("dtype-del-target", "data"),
        Output("dtype-del-feedback", "children"),
        Input({"type": "dtype-delete", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )(open_delete_type)
    app.clientside_callback(
        ClientsideFunction("hoopster", "close_modal"),
        Output("dtype-del-modal", "opened", allow_duplicate=True),
        Input("dtype-del-cancel", "n_clicks"),
        prevent_initial_call=True,
    )
    app.callback(
        Output("dtype-rows", "children", allow_duplicate=True),
        Output("dtype-del-modal", "opened", allow_duplicate=True),
        Output("dtype-del-feedback", "children", allow_duplicate=True),
        Input("dtype-del-confirm", "n_clicks"),
        State("dtype-del-target", "data"),
        prevent_initial_call=True,
    )(confirm_delete_type)


def _register_library_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the library filter, the grid's in-cell save and its Open click.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        None.
    """
    filters = [
        State("drills-search", "value"),
        State("drills-filter-type", "value"),
        State("drills-filter-difficulty", "value"),
        State("drills-filter-result", "value"),
    ]
    app.callback(
        Output("drills-cards", "children"),
        Output("drills-grid", "rowData"),
        Output("drills-count", "children"),
        Output("drills-grid-feedback", "children"),
        Input("drills-search", "value"),
        Input("drills-filter-type", "value"),
        Input("drills-filter-difficulty", "value"),
        Input("drills-filter-result", "value"),
        prevent_initial_call=True,
    )(filter_library)
    app.callback(
        Output("drills-grid-feedback", "children", allow_duplicate=True),
        Output("drills-grid", "rowData", allow_duplicate=True),
        Input("drills-grid", "cellValueChanged"),
        State("drills-difficulty-codes", "data"),
        *filters,
        prevent_initial_call=True,
    )(save_drill_cell)
    app.callback(
        Output("url", "pathname", allow_duplicate=True),
        Input("drills-grid", "cellClicked"),
        prevent_initial_call=True,
    )(open_drill_from_grid)


def _register_form_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the drill form's section switch (clientside), Save, and Remove (open,
        cancel, confirm).

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        The section switch is hoopster_library.drill_form_sections
        (assets/hoopster_library.js).
    """
    app.clientside_callback(
        ClientsideFunction("hoopster_library", "drill_form_sections"),
        Output("drill-section-target", "style"),
        Output("drill-section-fixed", "style"),
        Output("drill-section-physical", "style"),
        Output("drill-section-shooting", "style"),
        Output("drill-section-outcomes", "style"),
        Output("drill-target", "label"),
        Input("drill-result-type", "value"),
        State("drill-result-meta", "data"),
        prevent_initial_call=True,
    )
    app.callback(
        Output("drill-saved", "children"),
        Output("drill-feedback", "children"),
        Output("url", "pathname", allow_duplicate=True),
        Output("drill-form-header", "children"),
        Input("drill-save", "n_clicks"),
        State("drill-form-id", "data"),
        State("drill-name", "value"),
        State("drill-description", "value"),
        State("drill-primary-type", "value"),
        State("drill-secondary-types", "value"),
        State("drill-difficulty", "value"),
        State("drill-result-type", "value"),
        State("drill-symmetry", "value"),
        State("drill-target", "value"),
        State("drill-fixed-seconds", "value"),
        State("drill-v1-label", "value"),
        State("drill-v1-unit", "value"),
        State("drill-v1-higher", "value"),
        State("drill-v2-label", "value"),
        State("drill-v2-unit", "value"),
        State("drill-v2-higher", "value"),
        State("drill-spots", "value"),
        State({"type": "og-name", "index": ALL}, "value"),
        State({"type": "og-label", "group": ALL, "index": ALL}, "value"),
        State({"type": "og-success", "group": ALL, "index": ALL}, "checked"),
        prevent_initial_call=True,
    )(save_drill_form)
    app.callback(
        Output("drill-remove-modal", "opened"),
        Output("drill-remove-text", "children"),
        Output("drill-remove-feedback", "children"),
        Input("drill-remove", "n_clicks"),
        State("drill-form-id", "data"),
        prevent_initial_call=True,
    )(open_remove_drill)
    app.clientside_callback(
        ClientsideFunction("hoopster", "close_modal"),
        Output("drill-remove-modal", "opened", allow_duplicate=True),
        Input("drill-remove-cancel", "n_clicks"),
        prevent_initial_call=True,
    )
    app.callback(
        Output("url", "pathname", allow_duplicate=True),
        Output("drill-remove-feedback", "children", allow_duplicate=True),
        Input("drill-remove-confirm", "n_clicks"),
        State("drill-form-id", "data"),
        prevent_initial_call=True,
    )(confirm_remove_drill)
