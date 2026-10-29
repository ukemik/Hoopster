"""Data access for drill sets and practice templates: lists, editors and saves with versions
(REQ-051 to REQ-054, REQ-072, REQ-080).

Every function takes the request session first and runs its SQL through data_handler. Write
functions never commit: they run inside app_controller.run_write_with_reason (COD-6-01,
COD-7-01). Set and template ids are LINEAGE ids (drill_set_id, practice_template_id); items
reference drills by drill LINEAGE id (COD-8-01). A changed list of drills, lines or sets is a
new version; a name or description is edited in place (REQ-080). Every function that writes a
version ends with SET CONSTRAINTS ALL IMMEDIATE (plan §3.2).
"""

import logging
from typing import TypedDict, cast

from sqlalchemy.orm import Session

from src.data_drills import SET_CONSTRAINTS_IMMEDIATE_SQL
from src.data_handler import execute, fetch_all, fetch_one

logger = logging.getLogger(__name__)

# h_data.drill_set_versions.set_kind values (ck_drill_set_versions_kind).
KIND_MANUAL = "manual_fixed"
KIND_QUICK = "quick_fixed"
KIND_DYNAMIC = "dynamic"
SET_KINDS: frozenset[str] = frozenset({KIND_MANUAL, KIND_QUICK, KIND_DYNAMIC})


# --- Form shapes ---

class SetLine(TypedDict):
    """
    Description:
        One draw line of a quick fixed or dynamic set (REQ-053, REQ-054).

    Parameters:
        drill_count: 1-50 drills.
        difficulty_code: an h_ref.difficulty_levels code.
        drill_type_id: a live drill type id matched against the primary type, or None for
            any type.

    Return Values:
        Not applicable (typed dict).

    Notes:
        The same keys as the set-working store's lines.
    """

    drill_count: int
    difficulty_code: str
    drill_type_id: int | None


class SetForm(TypedDict):
    """
    Description:
        The checked values of the drill set form (built by controller_sets).

    Parameters:
        set_name: stripped, 1-100 characters.
        description: up to 4000 characters, or None.
        set_kind: "manual_fixed", "quick_fixed" or "dynamic"; for an existing set it must be
            the set's own kind (D6).
        drill_ids: drill LINEAGE ids in working order (manual: the drills; quick: the drawn
            drills, reordered); unused for dynamic.
        lines: the draw lines in working order (quick and dynamic); unused for manual.

    Return Values:
        Not applicable (typed dict).

    Notes:
        None.
    """

    set_name: str
    description: str | None
    set_kind: str
    drill_ids: list[int]
    lines: list[SetLine]


class TemplateForm(TypedDict):
    """
    Description:
        The checked values of the practice template form (built by controller_sets).

    Parameters:
        template_name: stripped, 1-100 characters.
        description: up to 4000 characters, or None.
        drill_set_ids: drill set LINEAGE ids in order, at least one, each at most once
            (REQ-080).

    Return Values:
        Not applicable (typed dict).

    Notes:
        None.
    """

    template_name: str
    description: str | None
    drill_set_ids: list[int]


# --- Drill set lists and editor (REQ-051 to REQ-054) ---

LIST_LIBRARY_SETS_SQL = """
SELECT cs.drill_set_id,
       cs.set_name,
       cs.description,
       cs.drill_set_version_id,
       cs.version_no,
       cs.set_kind,
       (SELECT count(*)::integer FROM h_data.drill_set_items i
        WHERE i.drill_set_version_id = cs.drill_set_version_id AND i.deleted_at IS NULL) AS item_count,
       (SELECT count(*)::integer FROM h_data.drill_set_lines l
        WHERE l.drill_set_version_id = cs.drill_set_version_id AND l.deleted_at IS NULL) AS line_count
FROM h_data.v_current_drill_sets cs
ORDER BY lower(cs.set_name), cs.drill_set_id
"""

GET_CURRENT_SET_SQL = """
SELECT drill_set_id, set_name, description, drill_set_version_id, version_no, set_kind
FROM h_data.v_current_drill_sets
WHERE drill_set_id = :drill_set_id
"""

# is_current: the drill is still selectable (live, not retired); a fixed set keeps listing a
# drill that was retired since, and a practice built from it leaves that drill out with a
# notice (h_data.fn_add_drill_set_to_practice).
LIST_SET_ITEMS_SQL = """
SELECT i.drill_id,
       d.drill_name,
       EXISTS (SELECT 1 FROM h_data.v_current_drills cd WHERE cd.drill_id = i.drill_id) AS is_current
FROM h_data.drill_set_items i
JOIN h_data.drills d ON d.drill_id = i.drill_id
WHERE i.drill_set_version_id = :drill_set_version_id
  AND i.deleted_at IS NULL
ORDER BY i.item_position, i.drill_set_item_id
"""

LIST_SET_LINES_SQL = """
SELECT l.line_no, l.drill_count, l.difficulty_code, l.drill_type_id
FROM h_data.drill_set_lines l
WHERE l.drill_set_version_id = :drill_set_version_id
  AND l.deleted_at IS NULL
ORDER BY l.line_no
"""


def list_library_sets(session: Session) -> list[dict[str, object]]:
    """
    Description:
        Lists the selectable drill sets (live, not retired) with their latest live version
        and its size, for the Drill sets screen and the template's set picker.

    Parameters:
        session: the request session.

    Return Values:
        list[dict[str, object]]: {"drill_set_id", "set_name", "description",
        "drill_set_version_id", "version_no", "set_kind", "item_count", "line_count"} per
        set, ordered by name.

    Notes:
        Reads h_data.v_current_drill_sets (COD-22-03, D10).
    """
    return fetch_all(session, LIST_LIBRARY_SETS_SQL)


def get_set_editor(session: Session, drill_set_id: int) -> dict[str, object] | None:
    """
    Description:
        Loads a live, non-retired drill set for its form: the lineage and the latest live
        version with its drills and lines.

    Parameters:
        session: the request session.
        drill_set_id: the set LINEAGE id from /drill-sets/<id>. Never a version id or a
            practice_drill_set_id (COD-8-01).

    Return Values:
        dict[str, object] | None: the GET_CURRENT_SET_SQL columns plus "items"
        ([{"drill_id", "drill_name", "is_current"}] in item order) and "lines"
        ([{"line_no", "drill_count", "difficulty_code", "drill_type_id"}] in line order);
        None when the set does not exist, is deleted or is retired (D10).

    Notes:
        Inside a save, call it after the lineage row is locked.
    """
    current = fetch_one(session, GET_CURRENT_SET_SQL, {"drill_set_id": drill_set_id})
    if current is None:
        return None
    params = {"drill_set_version_id": current["drill_set_version_id"]}
    editor = dict(current)
    editor["items"] = fetch_all(session, LIST_SET_ITEMS_SQL, params)
    editor["lines"] = fetch_all(session, LIST_SET_LINES_SQL, params)
    return editor


# --- Drill set save (REQ-051 to REQ-054, REQ-080, D6, D13) ---

INSERT_SET_SQL = """
INSERT INTO h_data.drill_sets (set_name, description)
VALUES (:set_name, :description)
RETURNING drill_set_id
"""

LOCK_LIVE_SET_SQL = """
SELECT drill_set_id
FROM h_data.drill_sets
WHERE drill_set_id = :drill_set_id
  AND deleted_at IS NULL
  AND retired_at IS NULL
FOR UPDATE
"""

UPDATE_SET_LINEAGE_SQL = """
UPDATE h_data.drill_sets
SET set_name = :set_name,
    description = :description
WHERE drill_set_id = :drill_set_id
"""

# version_no is assigned by h_data.tf_version_guard.
INSERT_SET_VERSION_SQL = """
INSERT INTO h_data.drill_set_versions (drill_set_id, set_kind)
VALUES (:drill_set_id, :set_kind)
RETURNING drill_set_version_id, version_no
"""

INSERT_SET_ITEMS_SQL = """
INSERT INTO h_data.drill_set_items (drill_set_version_id, item_position, drill_id)
SELECT CAST(:drill_set_version_id AS bigint), u.ord::smallint, u.drill_id
FROM unnest(CAST(:drill_ids AS bigint[])) WITH ORDINALITY AS u(drill_id, ord)
"""

# drill_type_id 0 stands for "any type" in the array and is stored as NULL.
INSERT_SET_LINES_SQL = """
INSERT INTO h_data.drill_set_lines (drill_set_version_id, line_no, drill_count, difficulty_code, drill_type_id)
SELECT CAST(:drill_set_version_id AS bigint), u.ord::smallint, u.drill_count, u.difficulty_code,
       NULLIF(u.drill_type_id, 0)
FROM unnest(CAST(:drill_counts AS smallint[]), CAST(:difficulty_codes AS text[]),
            CAST(:drill_type_ids AS bigint[])) WITH ORDINALITY
     AS u(drill_count, difficulty_code, drill_type_id, ord)
"""

DRAW_QUICK_SET_SQL = """
SELECT line_no, requested, drawn, notice
FROM h_data.fn_draw_quick_fixed_set(CAST(:drill_set_version_id AS bigint))
"""

# In the line array, 0 means "any drill type" (drill type ids start at 1).
ANY_DRILL_TYPE_ID = 0


def _line_key(line: SetLine | dict[str, object]) -> tuple[int, str, int | None]:
    """
    Description:
        The comparable content of a draw line.

    Parameters:
        line: a SetLine or a LIST_SET_LINES_SQL row.

    Return Values:
        tuple[int, str, int | None]: (drill_count, difficulty_code, drill_type_id).

    Notes:
        Pure.
    """
    type_id = line["drill_type_id"]
    return (
        int(cast(int, line["drill_count"])),
        str(line["difficulty_code"]),
        None if type_id is None else int(cast(int, type_id)),
    )


def _insert_set_version(session: Session, drill_set_id: int, set_kind: str) -> tuple[int, int]:
    """
    Description:
        Inserts a new version of a drill set.

    Parameters:
        session: the write session, with the set lineage locked (or just inserted).
        drill_set_id: the set lineage id.
        set_kind: the version's kind.

    Return Values:
        tuple[int, int]: (drill_set_version_id, version_no).

    Notes:
        Its contents must be inserted in the same transaction (tf_version_guard).
    """
    row = fetch_one(
        session, INSERT_SET_VERSION_SQL, {"drill_set_id": drill_set_id, "set_kind": set_kind}
    )
    if row is None:
        logger.error("Drill set version insert returned no id")
        raise RuntimeError("Drill set version insert returned no id")
    return cast(int, row["drill_set_version_id"]), cast(int, row["version_no"])


def _insert_items(session: Session, version_id: int, drill_ids: list[int]) -> None:
    """
    Description:
        Inserts a fixed set version's drills in the given order (positions 1..n).

    Parameters:
        session: the write session.
        version_id: the new drill_set_version_id.
        drill_ids: drill LINEAGE ids in order.

    Return Values:
        None.

    Notes:
        One batch insert (COD-16-01).
    """
    execute(
        session,
        INSERT_SET_ITEMS_SQL,
        {"drill_set_version_id": version_id, "drill_ids": list(drill_ids)},
    )


def _insert_lines(session: Session, version_id: int, lines: list[SetLine]) -> None:
    """
    Description:
        Inserts a set version's draw lines in the given order (line numbers 1..n).

    Parameters:
        session: the write session.
        version_id: the new drill_set_version_id.
        lines: the lines in order.

    Return Values:
        None.

    Notes:
        One batch insert (COD-16-01).
    """
    execute(
        session,
        INSERT_SET_LINES_SQL,
        {
            "drill_set_version_id": version_id,
            "drill_counts": [line["drill_count"] for line in lines],
            "difficulty_codes": [line["difficulty_code"] for line in lines],
            "drill_type_ids": [
                ANY_DRILL_TYPE_ID if line["drill_type_id"] is None else line["drill_type_id"]
                for line in lines
            ],
        },
    )


def _draw_quick_set(session: Session, version_id: int) -> list[str]:
    """
    Description:
        Draws a new quick fixed set version once (h_data.fn_draw_quick_fixed_set) and
        collects its short-draw notices (REQ-054).

    Parameters:
        session: the write session that created the version.
        version_id: the new quick_fixed drill_set_version_id. Never a set lineage id.

    Return Values:
        list[str]: the notices of the lines that came up short, in line order (may be
        empty).

    Notes:
        Must run in the transaction that created the version (tf_version_guard).
    """
    rows = fetch_all(session, DRAW_QUICK_SET_SQL, {"drill_set_version_id": version_id})
    return [str(row["notice"]) for row in rows if row["notice"]]


def _write_new_set_version(
    session: Session, drill_set_id: int, form: SetForm, redraw: bool
) -> tuple[int, list[str]]:
    """
    Description:
        Writes a new version of a drill set from the form, by kind: manual = the drills;
        dynamic = the lines; quick with redraw = the lines and a fresh draw; quick without
        redraw = the lines copied and the drawn drills in working order (D13).

    Parameters:
        session: the write session, with the set lineage locked (or just inserted).
        drill_set_id: the set lineage id.
        form: the checked form.
        redraw: for a quick set, True draws the lines fresh (new set or changed lines);
            ignored for the other kinds.

    Return Values:
        tuple[int, list[str]]: (the new version_no, the draw notices).

    Notes:
        None.
    """
    version_id, version_no = _insert_set_version(session, drill_set_id, form["set_kind"])
    notices: list[str] = []
    if form["set_kind"] == KIND_MANUAL:
        _insert_items(session, version_id, form["drill_ids"])
    elif form["set_kind"] == KIND_DYNAMIC:
        _insert_lines(session, version_id, form["lines"])
    else:
        _insert_lines(session, version_id, form["lines"])
        if redraw:
            notices = _draw_quick_set(session, version_id)
        else:
            _insert_items(session, version_id, form["drill_ids"])
    return version_no, notices


def _set_change(current: dict[str, object], form: SetForm) -> tuple[bool, bool]:
    """
    Description:
        Decides whether a set save needs a new version and, for a quick set, a fresh draw
        (REQ-080, D13).

    Parameters:
        current: get_set_editor of the set, read after the lineage lock.
        form: the checked form, of the set's own kind.

    Return Values:
        tuple[bool, bool]: (new version needed, redraw needed).

    Notes:
        Pure. Raises ValueError when the kind differs (D6) or when a quick set's working
        drills are not a permutation of its drawn drills.
    """
    if form["set_kind"] != current["set_kind"]:
        raise ValueError("the kind of a drill set cannot change")
    current_drill_ids = [
        int(cast(int, item["drill_id"])) for item in cast(list[dict], current["items"])
    ]
    current_lines = [_line_key(line) for line in cast(list[dict], current["lines"])]
    form_lines = [_line_key(line) for line in form["lines"]]
    if form["set_kind"] == KIND_MANUAL:
        return current_drill_ids != list(form["drill_ids"]), False
    if form["set_kind"] == KIND_DYNAMIC:
        return current_lines != form_lines, False
    if current_lines != form_lines:
        return True, True
    if sorted(current_drill_ids) != sorted(form["drill_ids"]):
        raise ValueError("a quick set's drill order must keep the same drawn drills")
    return current_drill_ids != list(form["drill_ids"]), False


def save_drill_set(
    session: Session, drill_set_id: int | None, form: SetForm
) -> dict[str, object]:
    """
    Description:
        Saves the drill set form in one transaction: the lineage is inserted or locked and
        updated in place, and a new version is written only when the contents changed
        (REQ-051 to REQ-054, REQ-080, D13).

    Parameters:
        session: the write session (inside run_write_with_reason, acting user =
            current_user.user_id).
        drill_set_id: the set LINEAGE id from the set-form-context store, or None for a new
            set. Never a version id (COD-8-01).
        form: the checked form.

    Return Values:
        dict[str, object]: {"drill_set_id": int, "version_no": int, "new_version": bool,
        "notices": list[str]} (notices only from a quick draw).

    Notes:
        Raises LookupError when the set is no longer live or was retired, and ValueError
        for a changed kind or a quick reorder that is not a permutation (D13). Ends with SET
        CONSTRAINTS ALL IMMEDIATE (plan §3.2). A duplicate name raises the
        ux_drill_sets_name unique violation.
    """
    lineage = {"set_name": form["set_name"], "description": form["description"]}
    if drill_set_id is None:
        row = fetch_one(session, INSERT_SET_SQL, lineage)
        if row is None:
            logger.error("Drill set insert returned no id")
            raise RuntimeError("Drill set insert returned no id")
        new_set_id = cast(int, row["drill_set_id"])
        version_no, notices = _write_new_set_version(session, new_set_id, form, True)
        execute(session, SET_CONSTRAINTS_IMMEDIATE_SQL)
        return {
            "drill_set_id": new_set_id,
            "version_no": version_no,
            "new_version": True,
            "notices": notices,
        }

    if fetch_one(session, LOCK_LIVE_SET_SQL, {"drill_set_id": drill_set_id}) is None:
        raise LookupError(f"drill set {drill_set_id} is not live or is retired")
    execute(session, UPDATE_SET_LINEAGE_SQL, {**lineage, "drill_set_id": drill_set_id})
    current = get_set_editor(session, drill_set_id)
    if current is None:
        raise LookupError(f"drill set {drill_set_id} has no live version")
    needs_version, redraw = _set_change(current, form)
    notices: list[str] = []
    if needs_version:
        version_no, notices = _write_new_set_version(session, drill_set_id, form, redraw)
    else:
        version_no = cast(int, current["version_no"])
    execute(session, SET_CONSTRAINTS_IMMEDIATE_SQL)
    return {
        "drill_set_id": drill_set_id,
        "version_no": version_no,
        "new_version": needs_version,
        "notices": notices,
    }


# --- Practice templates (REQ-072, REQ-080) ---

LIST_LIBRARY_TEMPLATES_SQL = """
SELECT practice_template_id, template_name, description, practice_template_version_id,
       version_no, set_count
FROM h_data.v_current_practice_templates
ORDER BY lower(template_name), practice_template_id
"""

GET_CURRENT_TEMPLATE_SQL = """
SELECT practice_template_id, template_name, description, practice_template_version_id,
       version_no, set_count
FROM h_data.v_current_practice_templates
WHERE practice_template_id = :practice_template_id
"""

# is_current: the set is still selectable; a template keeps listing a set retired since.
LIST_TEMPLATE_SETS_SQL = """
SELECT ts.drill_set_id,
       s.set_name,
       EXISTS (SELECT 1 FROM h_data.v_current_drill_sets cs WHERE cs.drill_set_id = ts.drill_set_id) AS is_current
FROM h_data.practice_template_sets ts
JOIN h_data.drill_sets s ON s.drill_set_id = ts.drill_set_id
WHERE ts.practice_template_version_id = :practice_template_version_id
  AND ts.deleted_at IS NULL
ORDER BY ts.template_position, ts.practice_template_set_id
"""

INSERT_TEMPLATE_SQL = """
INSERT INTO h_data.practice_templates (template_name, description)
VALUES (:template_name, :description)
RETURNING practice_template_id
"""

LOCK_LIVE_TEMPLATE_SQL = """
SELECT practice_template_id
FROM h_data.practice_templates
WHERE practice_template_id = :practice_template_id
  AND deleted_at IS NULL
  AND retired_at IS NULL
FOR UPDATE
"""

UPDATE_TEMPLATE_LINEAGE_SQL = """
UPDATE h_data.practice_templates
SET template_name = :template_name,
    description = :description
WHERE practice_template_id = :practice_template_id
"""

# version_no is assigned by h_data.tf_version_guard.
INSERT_TEMPLATE_VERSION_SQL = """
INSERT INTO h_data.practice_template_versions (practice_template_id)
VALUES (:practice_template_id)
RETURNING practice_template_version_id, version_no
"""

INSERT_TEMPLATE_SETS_SQL = """
INSERT INTO h_data.practice_template_sets (practice_template_version_id, template_position, drill_set_id)
SELECT CAST(:practice_template_version_id AS bigint), u.ord::smallint, u.drill_set_id
FROM unnest(CAST(:drill_set_ids AS bigint[])) WITH ORDINALITY AS u(drill_set_id, ord)
"""


def list_library_templates(session: Session) -> list[dict[str, object]]:
    """
    Description:
        Lists the selectable practice templates (live, not retired) with their latest live
        version and its set count.

    Parameters:
        session: the request session.

    Return Values:
        list[dict[str, object]]: {"practice_template_id", "template_name", "description",
        "practice_template_version_id", "version_no", "set_count"} per template, ordered by
        name.

    Notes:
        Reads h_data.v_current_practice_templates (COD-22-03, D10).
    """
    return fetch_all(session, LIST_LIBRARY_TEMPLATES_SQL)


def get_template_editor(session: Session, template_id: int) -> dict[str, object] | None:
    """
    Description:
        Loads a live, non-retired practice template for its form: the lineage and the
        latest live version's sets in order.

    Parameters:
        session: the request session.
        template_id: the template LINEAGE id from /templates/<id>. Never a version id
            (COD-8-01).

    Return Values:
        dict[str, object] | None: the GET_CURRENT_TEMPLATE_SQL columns plus "sets"
        ([{"drill_set_id", "set_name", "is_current"}] in order); None when the template does
        not exist, is deleted or is retired (D10).

    Notes:
        Inside a save, call it after the lineage row is locked.
    """
    current = fetch_one(
        session, GET_CURRENT_TEMPLATE_SQL, {"practice_template_id": template_id}
    )
    if current is None:
        return None
    editor = dict(current)
    editor["sets"] = fetch_all(
        session,
        LIST_TEMPLATE_SETS_SQL,
        {"practice_template_version_id": current["practice_template_version_id"]},
    )
    return editor


def _insert_template_version(
    session: Session, template_id: int, drill_set_ids: list[int]
) -> int:
    """
    Description:
        Inserts a new template version with its sets in order.

    Parameters:
        session: the write session, with the template lineage locked (or just inserted).
        template_id: the template lineage id.
        drill_set_ids: drill set LINEAGE ids in order, each at most once.

    Return Values:
        int: the new version_no.

    Notes:
        ux_practice_template_sets_set refuses a set listed twice (REQ-080).
    """
    row = fetch_one(
        session, INSERT_TEMPLATE_VERSION_SQL, {"practice_template_id": template_id}
    )
    if row is None:
        logger.error("Template version insert returned no id")
        raise RuntimeError("Template version insert returned no id")
    execute(
        session,
        INSERT_TEMPLATE_SETS_SQL,
        {
            "practice_template_version_id": row["practice_template_version_id"],
            "drill_set_ids": list(drill_set_ids),
        },
    )
    return cast(int, row["version_no"])


def save_template(
    session: Session, template_id: int | None, form: TemplateForm
) -> dict[str, object]:
    """
    Description:
        Saves the practice template form in one transaction: the lineage is inserted or
        locked and updated in place, and a new version is written when the list of sets
        differs from the latest version's (REQ-072, REQ-080).

    Parameters:
        session: the write session (inside run_write_with_reason, acting user =
            current_user.user_id).
        template_id: the template LINEAGE id from the tpl-form-context store, or None for a
            new template. Never a version id (COD-8-01).
        form: the checked form.

    Return Values:
        dict[str, object]: {"practice_template_id": int, "version_no": int,
        "new_version": bool}.

    Notes:
        Raises LookupError when the template is no longer live or was retired. Ends with SET
        CONSTRAINTS ALL IMMEDIATE (plan §3.2). A duplicate name raises the
        ux_practice_templates_name unique violation.
    """
    lineage = {"template_name": form["template_name"], "description": form["description"]}
    if template_id is None:
        row = fetch_one(session, INSERT_TEMPLATE_SQL, lineage)
        if row is None:
            logger.error("Template insert returned no id")
            raise RuntimeError("Template insert returned no id")
        new_template_id = cast(int, row["practice_template_id"])
        version_no = _insert_template_version(session, new_template_id, form["drill_set_ids"])
        execute(session, SET_CONSTRAINTS_IMMEDIATE_SQL)
        return {
            "practice_template_id": new_template_id,
            "version_no": version_no,
            "new_version": True,
        }

    if fetch_one(session, LOCK_LIVE_TEMPLATE_SQL, {"practice_template_id": template_id}) is None:
        raise LookupError(f"practice template {template_id} is not live or is retired")
    execute(
        session, UPDATE_TEMPLATE_LINEAGE_SQL, {**lineage, "practice_template_id": template_id}
    )
    current = get_template_editor(session, template_id)
    if current is None:
        raise LookupError(f"practice template {template_id} has no live version")
    current_ids = [
        int(cast(int, row["drill_set_id"])) for row in cast(list[dict], current["sets"])
    ]
    needs_version = current_ids != list(form["drill_set_ids"])
    if needs_version:
        version_no = _insert_template_version(session, template_id, form["drill_set_ids"])
    else:
        version_no = cast(int, current["version_no"])
    execute(session, SET_CONSTRAINTS_IMMEDIATE_SQL)
    return {
        "practice_template_id": template_id,
        "version_no": version_no,
        "new_version": needs_version,
    }
