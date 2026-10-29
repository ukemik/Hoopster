"""Data access for the drill library: drill types, drills with their versions, and the removal
of library items (REQ-026, REQ-028, REQ-029, REQ-076, REQ-077, REQ-079, REQ-080, REQ-081).

Every function takes the request session first and runs its SQL through data_handler. Write
functions never commit: they run inside app_controller.run_write / run_write_with_reason, after
the acting user has been set (COD-6-01, COD-7-01). Library ids are lineage ids (drill_id,
drill_set_id, practice_template_id), never version ids (COD-8-01, plan Context Adaptation
Table). Every function that writes a version ends with SET CONSTRAINTS ALL IMMEDIATE, so the
deferred completeness check (h_data.tf_version_complete) fails inside the work, where its
message can reach the screen (plan §3.2).
"""

import logging
from typing import TypedDict, cast

from sqlalchemy.orm import Session

from src.data_handler import execute, fetch_all, fetch_one

logger = logging.getLogger(__name__)

# The entities h_data.fn_practice_usage and h_data.fn_remove_library_item accept.
ENTITY_DRILL = "drill"
ENTITY_DRILL_SET = "drill_set"
ENTITY_PRACTICE_TEMPLATE = "practice_template"
LIBRARY_ENTITIES: frozenset[str] = frozenset(
    {ENTITY_DRILL, ENTITY_DRILL_SET, ENTITY_PRACTICE_TEMPLATE}
)

# In a drill form's spot list, 0 stands for the free-throw line (court spot ids start at 1).
FREE_THROW_SPOT_ID = 0

# The lineage fields the library grid may edit in place (D11), each with its own statement.
GRID_FIELD_NAME = "drill_name"
GRID_FIELD_DIFFICULTY = "difficulty_code"

SET_CONSTRAINTS_IMMEDIATE_SQL = "SET CONSTRAINTS ALL IMMEDIATE"


# --- Form shapes ---

class OutcomeForm(TypedDict):
    """
    Description:
        One button of an outcome group, as checked by the drill form.

    Parameters:
        label: the button text, stripped, 1-30 characters.
        is_success: True when the button counts as a clean rep.

    Return Values:
        Not applicable (typed dict).

    Notes:
        None.
    """

    label: str
    is_success: bool


class OutcomeGroupForm(TypedDict):
    """
    Description:
        One outcome group of the drill form (REQ-077).

    Parameters:
        group_name: the group name, stripped, 1-50 characters.
        outcomes: 2-6 buttons in screen order.

    Return Values:
        Not applicable (typed dict).

    Notes:
        None.
    """

    group_name: str
    outcomes: list[OutcomeForm]


class DrillForm(TypedDict):
    """
    Description:
        The checked values of the drill form, ready to store (built only by
        controller_drills.parse_drill_form).

    Parameters:
        drill_name: stripped, 1-100 characters.
        description: up to 1000 characters, or None.
        primary_drill_type_id: a live h_data.drill_types id.
        secondary_type_ids: live drill type ids, never the primary one.
        difficulty_code: an h_ref.difficulty_levels code.
        result_type_code: an h_ref.result_types code.
        symmetry_type_code: an h_ref.symmetry_types code, or None.
        default_target: a whole number of at least 1, or None (always None for physical).
        fixed_seconds: whole seconds for "Fixed time for reps", else None.
        value_1_label, value_1_unit, value_1_higher_is_better: the first named number of a
            physical drill, all None for any other type.
        value_2_label, value_2_unit, value_2_higher_is_better: the optional second number.
        spot_ids: shooting only: court spot ids in spot order, FREE_THROW_SPOT_ID (0) for the
            free-throw line; empty for other types.
        groups: outcome buttons only: 1-3 groups; empty for other types.

    Return Values:
        Not applicable (typed dict).

    Notes:
        The recording fields (everything from result_type_code down) are versioned; the
        lineage fields are edited in place (REQ-080).
    """

    drill_name: str
    description: str | None
    primary_drill_type_id: int
    secondary_type_ids: list[int]
    difficulty_code: str
    result_type_code: str
    symmetry_type_code: str | None
    default_target: int | None
    fixed_seconds: int | None
    value_1_label: str | None
    value_1_unit: str | None
    value_1_higher_is_better: bool | None
    value_2_label: str | None
    value_2_unit: str | None
    value_2_higher_is_better: bool | None
    spot_ids: list[int]
    groups: list[OutcomeGroupForm]


# --- Drill types (REQ-029) ---

LIST_DRILL_TYPES_WITH_USE_SQL = """
SELECT dt.drill_type_id,
       dt.type_name,
       dt.sort_order,
       (SELECT count(*)::integer
        FROM h_data.drills d
        WHERE d.deleted_at IS NULL
          AND (d.primary_drill_type_id = dt.drill_type_id
               OR EXISTS (SELECT 1
                          FROM h_data.drill_secondary_types st
                          WHERE st.drill_id = d.drill_id
                            AND st.drill_type_id = dt.drill_type_id
                            AND st.deleted_at IS NULL))) AS drill_count
FROM h_data.drill_types dt
WHERE dt.deleted_at IS NULL
ORDER BY dt.sort_order, lower(dt.type_name), dt.drill_type_id
"""

# sort_order = the highest live sort order + 1, so a new type is listed last.
INSERT_DRILL_TYPE_SQL = """
INSERT INTO h_data.drill_types (type_name, sort_order)
SELECT CAST(:type_name AS text), COALESCE(max(t.sort_order), 0) + 1
FROM h_data.drill_types t
WHERE t.deleted_at IS NULL
RETURNING drill_type_id
"""

RENAME_DRILL_TYPE_SQL = """
UPDATE h_data.drill_types
SET type_name = :type_name
WHERE drill_type_id = :drill_type_id
  AND deleted_at IS NULL
RETURNING drill_type_id
"""

DELETE_DRILL_TYPE_SQL = """
SELECT h_data.fn_soft_delete('drill_type', CAST(:drill_type_id AS bigint)) AS rows_marked
"""


def list_drill_types_with_use(session: Session) -> list[dict[str, object]]:
    """
    Description:
        Lists the live drill types with the number of live drills that use each one as their
        primary or a secondary type, for the Drill types screen.

    Parameters:
        session: the request session.

    Return Values:
        list[dict[str, object]]: {"drill_type_id", "type_name", "sort_order", "drill_count"}
        per live type, ordered by sort_order, then name.

    Notes:
        Retired drills are live rows and count: they still block the type's deletion
        (h_data.fn_soft_delete('drill_type')).
    """
    return fetch_all(session, LIST_DRILL_TYPES_WITH_USE_SQL)


def insert_drill_type(session: Session, name: str) -> int:
    """
    Description:
        Adds a drill type at the end of the list.

    Parameters:
        session: the write session (inside run_write_with_reason, acting user =
            current_user.user_id).
        name: the stripped name, 1-100 characters, unique among live types
            case-insensitively (ux_drill_types_name).

    Return Values:
        int: the new h_data.drill_types.drill_type_id.

    Notes:
        A duplicate name raises the unique violation that the controller maps to its message.
    """
    row = fetch_one(session, INSERT_DRILL_TYPE_SQL, {"type_name": name})
    if row is None:
        logger.error("Drill type insert returned no id")
        raise RuntimeError("Drill type insert returned no id")
    return cast(int, row["drill_type_id"])


def rename_drill_type(session: Session, drill_type_id: int, name: str) -> bool:
    """
    Description:
        Renames a live drill type in place.

    Parameters:
        session: the write session (inside run_write_with_reason).
        drill_type_id: the h_data.drill_types id from the row's pattern id.
        name: the stripped new name, 1-100 characters.

    Return Values:
        bool: True when the type was renamed, False when it is no longer live.

    Notes:
        None.
    """
    row = fetch_one(
        session, RENAME_DRILL_TYPE_SQL, {"drill_type_id": drill_type_id, "type_name": name}
    )
    return row is not None


def delete_drill_type(session: Session, drill_type_id: int) -> int:
    """
    Description:
        Soft-deletes a drill type through h_data.fn_soft_delete('drill_type', ...), which
        refuses a type still used by a live drill, secondary link or drill-set line.

    Parameters:
        session: the write session (inside run_write_with_reason).
        drill_type_id: the h_data.drill_types id held in the dtype-del-target store.

    Return Values:
        int: the number of rows marked (1).

    Notes:
        The refusal is the database's own RAISE (SQLSTATE P0001); its text reaches the user
        through run_write_with_reason (plan §3.2).
    """
    row = fetch_one(session, DELETE_DRILL_TYPE_SQL, {"drill_type_id": drill_type_id})
    if row is None:
        logger.error("Drill type soft delete returned no row")
        raise RuntimeError("Drill type soft delete returned no row")
    return cast(int, row["rows_marked"])


# --- Form options (REQ-028, REQ-076, REQ-079) ---

LIST_DIFFICULTIES_SQL = """
SELECT difficulty_code, label
FROM h_ref.difficulty_levels
WHERE deleted_at IS NULL
ORDER BY sort_order
"""

LIST_RESULT_TYPES_SQL = """
SELECT result_type_code, label, capture_kind, value_unit, needs_fixed_seconds
FROM h_ref.result_types
WHERE deleted_at IS NULL
ORDER BY sort_order
"""

LIST_SYMMETRY_TYPES_SQL = """
SELECT symmetry_type_code, label
FROM h_ref.symmetry_types
WHERE deleted_at IS NULL
ORDER BY sort_order
"""

LIST_LIVE_DRILL_TYPES_SQL = """
SELECT drill_type_id, type_name
FROM h_data.drill_types
WHERE deleted_at IS NULL
ORDER BY sort_order, lower(type_name), drill_type_id
"""

LIST_ACTIVE_SPOTS_SQL = """
SELECT court_spot_id, spot_number, spot_name
FROM h_ref.court_spots
WHERE is_active
  AND deleted_at IS NULL
ORDER BY spot_number
"""


def list_reference_options(session: Session) -> dict[str, list[dict[str, object]]]:
    """
    Description:
        Reads every option list the library screens offer.

    Parameters:
        session: the request session.

    Return Values:
        dict[str, list[dict[str, object]]]: "difficulties" ({"difficulty_code", "label"}),
        "result_types" ({"result_type_code", "label", "capture_kind", "value_unit",
        "needs_fixed_seconds"}), "symmetry_types" ({"symmetry_type_code", "label"}),
        "drill_types" ({"drill_type_id", "type_name"}, live only) and "court_spots"
        ({"court_spot_id", "spot_number", "spot_name"}, active only), each in display order.

    Notes:
        Five small reads, none in a loop.
    """
    return {
        "difficulties": fetch_all(session, LIST_DIFFICULTIES_SQL),
        "result_types": fetch_all(session, LIST_RESULT_TYPES_SQL),
        "symmetry_types": fetch_all(session, LIST_SYMMETRY_TYPES_SQL),
        "drill_types": fetch_all(session, LIST_LIVE_DRILL_TYPES_SQL),
        "court_spots": fetch_all(session, LIST_ACTIVE_SPOTS_SQL),
    }


# --- Library list (REQ-026, REQ-048) ---

LIST_LIBRARY_DRILLS_SQL = """
SELECT cd.drill_id,
       cd.drill_id::text AS row_id,
       cd.drill_name,
       cd.description,
       cd.primary_drill_type_id,
       dt.type_name,
       cd.difficulty_code,
       dl.label AS difficulty_label,
       cd.result_type_code,
       rt.label AS result_type_label,
       cd.default_target,
       cd.version_no,
       COALESCE((SELECT string_agg(sdt.type_name, ', ' ORDER BY sdt.sort_order, lower(sdt.type_name))
                 FROM h_data.drill_secondary_types st
                 JOIN h_data.drill_types sdt
                   ON sdt.drill_type_id = st.drill_type_id AND sdt.deleted_at IS NULL
                 WHERE st.drill_id = cd.drill_id
                   AND st.deleted_at IS NULL), '') AS secondary_type_names
FROM h_data.v_current_drills cd
JOIN h_data.drill_types dt ON dt.drill_type_id = cd.primary_drill_type_id
JOIN h_ref.difficulty_levels dl ON dl.difficulty_code = cd.difficulty_code
JOIN h_ref.result_types rt ON rt.result_type_code = cd.result_type_code
ORDER BY lower(cd.drill_name), cd.drill_id
"""


def list_library_drills(session: Session) -> list[dict[str, object]]:
    """
    Description:
        Lists the selectable drills (live, not retired, latest live version) with their
        labels, for the library grid, its cards and every drill picker.

    Parameters:
        session: the request session.

    Return Values:
        list[dict[str, object]]: {"drill_id", "row_id" (str(drill_id), the grid's row id),
        "drill_name", "description", "primary_drill_type_id", "type_name",
        "difficulty_code", "difficulty_label", "result_type_code", "result_type_label",
        "default_target", "version_no", "secondary_type_names" (", "-joined, "" when none)}
        per drill, ordered by name.

    Notes:
        Reads h_data.v_current_drills, which applies the live and not-retired filters once
        (COD-22-03, D10).
    """
    return fetch_all(session, LIST_LIBRARY_DRILLS_SQL)


# --- Drill editor read (REQ-028, REQ-076, REQ-077, REQ-079) ---

GET_CURRENT_DRILL_SQL = """
SELECT drill_id, drill_name, description, primary_drill_type_id, difficulty_code,
       drill_version_id, version_no, result_type_code, symmetry_type_code,
       default_target, fixed_seconds,
       value_1_label, value_1_unit, value_1_higher_is_better,
       value_2_label, value_2_unit, value_2_higher_is_better
FROM h_data.v_current_drills
WHERE drill_id = :drill_id
"""

LIST_SECONDARY_TYPE_IDS_SQL = """
SELECT drill_type_id
FROM h_data.drill_secondary_types
WHERE drill_id = :drill_id
  AND deleted_at IS NULL
ORDER BY drill_secondary_type_id
"""

LIST_VERSION_SPOTS_SQL = """
SELECT spot_order, court_spot_id, is_free_throw
FROM h_data.drill_spots
WHERE drill_version_id = :drill_version_id
  AND deleted_at IS NULL
ORDER BY spot_order
"""

LIST_VERSION_OUTCOMES_SQL = """
SELECT g.outcome_group_id, g.group_no, g.group_name,
       o.drill_outcome_id, o.outcome_no, o.outcome_label, o.is_success
FROM h_data.drill_outcome_groups g
LEFT JOIN h_data.drill_outcomes o
  ON o.outcome_group_id = g.outcome_group_id AND o.deleted_at IS NULL
WHERE g.drill_version_id = :drill_version_id
  AND g.deleted_at IS NULL
ORDER BY g.group_no, o.outcome_no
"""


def _group_outcome_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """
    Description:
        Folds the flat group-and-outcome rows of a version into one dict per group.

    Parameters:
        rows: LIST_VERSION_OUTCOMES_SQL rows, ordered by group_no, outcome_no.

    Return Values:
        list[dict[str, object]]: {"outcome_group_id", "group_no", "group_name", "outcomes":
        [{"drill_outcome_id", "outcome_no", "outcome_label", "is_success"}]} per group.

    Notes:
        Pure. A group without live outcomes (impossible after a complete commit) gets an
        empty list.
    """
    groups: list[dict[str, object]] = []
    by_id: dict[int, list[dict[str, object]]] = {}
    for row in rows:
        group_id = cast(int, row["outcome_group_id"])
        if group_id not in by_id:
            outcomes: list[dict[str, object]] = []
            by_id[group_id] = outcomes
            groups.append(
                {
                    "outcome_group_id": group_id,
                    "group_no": row["group_no"],
                    "group_name": row["group_name"],
                    "outcomes": outcomes,
                }
            )
        if row["drill_outcome_id"] is not None:
            by_id[group_id].append(
                {
                    "drill_outcome_id": row["drill_outcome_id"],
                    "outcome_no": row["outcome_no"],
                    "outcome_label": row["outcome_label"],
                    "is_success": row["is_success"],
                }
            )
    return groups


def get_drill_editor(session: Session, drill_id: int) -> dict[str, object] | None:
    """
    Description:
        Loads a live, non-retired drill for the drill form: the lineage, its live secondary
        types, and the latest live version with its spots and outcome groups.

    Parameters:
        session: the request session.
        drill_id: the drill LINEAGE id from /drills/<id> (or the id being saved). Never a
            drill_version_id or practice_drill_id (COD-8-01).

    Return Values:
        dict[str, object] | None: the GET_CURRENT_DRILL_SQL columns plus
        "secondary_type_ids" (list[int]), "spot_ids" (list[int], FREE_THROW_SPOT_ID for the
        free-throw line, in spot order) and "groups" (see _group_outcome_rows); None when the
        drill does not exist, is deleted or is retired (D10).

    Notes:
        Inside a save, call it after the lineage row is locked.
    """
    drill = fetch_one(session, GET_CURRENT_DRILL_SQL, {"drill_id": drill_id})
    if drill is None:
        return None
    version_id = drill["drill_version_id"]
    secondary = fetch_all(session, LIST_SECONDARY_TYPE_IDS_SQL, {"drill_id": drill_id})
    spots = fetch_all(session, LIST_VERSION_SPOTS_SQL, {"drill_version_id": version_id})
    outcome_rows = fetch_all(
        session, LIST_VERSION_OUTCOMES_SQL, {"drill_version_id": version_id}
    )
    editor = dict(drill)
    editor["secondary_type_ids"] = [cast(int, row["drill_type_id"]) for row in secondary]
    editor["spot_ids"] = [
        FREE_THROW_SPOT_ID if row["is_free_throw"] else cast(int, row["court_spot_id"])
        for row in spots
    ]
    editor["groups"] = _group_outcome_rows(outcome_rows)
    return editor


# --- Drill save (REQ-028, REQ-080, D11, D12) ---

INSERT_DRILL_SQL = """
INSERT INTO h_data.drills (drill_name, description, primary_drill_type_id, difficulty_code)
VALUES (:drill_name, :description, :primary_drill_type_id, :difficulty_code)
RETURNING drill_id
"""

LOCK_LIVE_DRILL_SQL = """
SELECT drill_id
FROM h_data.drills
WHERE drill_id = :drill_id
  AND deleted_at IS NULL
  AND retired_at IS NULL
FOR UPDATE
"""

UPDATE_DRILL_LINEAGE_SQL = """
UPDATE h_data.drills
SET drill_name = :drill_name,
    description = :description,
    primary_drill_type_id = :primary_drill_type_id,
    difficulty_code = :difficulty_code
WHERE drill_id = :drill_id
"""

# Removed links are soft-deleted; links kept stay as they are (COD-22-02: never revived).
SOFT_DELETE_SECONDARY_TYPES_SQL = """
UPDATE h_data.drill_secondary_types
SET deleted_at = now()
WHERE drill_id = :drill_id
  AND deleted_at IS NULL
  AND NOT (drill_type_id = ANY (CAST(:keep_type_ids AS bigint[])))
"""

# Added links are new rows; a type that already has a live link is skipped.
INSERT_SECONDARY_TYPES_SQL = """
INSERT INTO h_data.drill_secondary_types (drill_id, drill_type_id)
SELECT CAST(:drill_id AS bigint), u.type_id
FROM unnest(CAST(:type_ids AS bigint[])) AS u(type_id)
WHERE NOT EXISTS (SELECT 1
                  FROM h_data.drill_secondary_types st
                  WHERE st.drill_id = CAST(:drill_id AS bigint)
                    AND st.drill_type_id = u.type_id
                    AND st.deleted_at IS NULL)
"""

# version_no is assigned by h_data.tf_version_guard.
INSERT_DRILL_VERSION_SQL = """
INSERT INTO h_data.drill_versions
    (drill_id, result_type_code, symmetry_type_code, default_target, fixed_seconds,
     value_1_label, value_1_unit, value_1_higher_is_better,
     value_2_label, value_2_unit, value_2_higher_is_better)
VALUES
    (:drill_id, :result_type_code, :symmetry_type_code, :default_target, :fixed_seconds,
     :value_1_label, :value_1_unit, :value_1_higher_is_better,
     :value_2_label, :value_2_unit, :value_2_higher_is_better)
RETURNING drill_version_id, version_no
"""

# One row per spot in the given order; 0 (FREE_THROW_SPOT_ID) is the free-throw line.
INSERT_DRILL_SPOTS_SQL = """
INSERT INTO h_data.drill_spots (drill_version_id, spot_order, court_spot_id, is_free_throw)
SELECT CAST(:drill_version_id AS bigint), u.ord::smallint, NULLIF(u.spot_id, 0), u.spot_id = 0
FROM unnest(CAST(:spot_ids AS bigint[])) WITH ORDINALITY AS u(spot_id, ord)
"""

INSERT_OUTCOME_GROUPS_SQL = """
INSERT INTO h_data.drill_outcome_groups (drill_version_id, group_no, group_name)
SELECT CAST(:drill_version_id AS bigint), u.ord::smallint, u.group_name
FROM unnest(CAST(:group_names AS text[])) WITH ORDINALITY AS u(group_name, ord)
"""

INSERT_OUTCOMES_SQL = """
INSERT INTO h_data.drill_outcomes (outcome_group_id, outcome_no, outcome_label, is_success)
SELECT g.outcome_group_id, u.outcome_no, u.outcome_label, u.is_success
FROM unnest(CAST(:group_nos AS smallint[]), CAST(:outcome_nos AS smallint[]),
            CAST(:labels AS text[]), CAST(:successes AS boolean[]))
     AS u(group_no, outcome_no, outcome_label, is_success)
JOIN h_data.drill_outcome_groups g
  ON g.drill_version_id = CAST(:drill_version_id AS bigint)
 AND g.group_no = u.group_no
 AND g.deleted_at IS NULL
"""

UPDATE_VALUE_LABELS_SQL = """
UPDATE h_data.drill_versions
SET value_1_label = CAST(:value_1_label AS text),
    value_2_label = CAST(:value_2_label AS text)
WHERE drill_version_id = :drill_version_id
  AND (value_1_label IS DISTINCT FROM CAST(:value_1_label AS text)
       OR value_2_label IS DISTINCT FROM CAST(:value_2_label AS text))
"""

UPDATE_GROUP_NAMES_SQL = """
UPDATE h_data.drill_outcome_groups g
SET group_name = u.group_name
FROM unnest(CAST(:group_ids AS bigint[]), CAST(:group_names AS text[])) AS u(group_id, group_name)
WHERE g.outcome_group_id = u.group_id
  AND g.group_name IS DISTINCT FROM u.group_name
"""

UPDATE_OUTCOME_LABELS_SQL = """
UPDATE h_data.drill_outcomes o
SET outcome_label = u.outcome_label
FROM unnest(CAST(:outcome_ids AS bigint[]), CAST(:labels AS text[])) AS u(outcome_id, outcome_label)
WHERE o.drill_outcome_id = u.outcome_id
  AND o.outcome_label IS DISTINCT FROM u.outcome_label
"""


def recording_signature(fields: DrillForm | dict[str, object]) -> tuple[object, ...]:
    """
    Description:
        The recording signature of a drill version (plan §3.4 step 4): everything that
        changes how results are recorded, without the labels.

    Parameters:
        fields: a DrillForm, or a get_drill_editor dict (the same keys).

    Return Values:
        tuple[object, ...]: (result type, symmetry, target, fixed seconds, whether value 1
        exists, its unit and direction, whether value 2 exists, its unit and direction, the
        ordered spot ids, and per group the success flags of its buttons in order).

    Notes:
        Pure. Two versions with equal signatures differ at most in value labels, group names
        and button labels, which are updated in place (REQ-080, D12).
    """
    groups = cast(list[dict[str, object]], fields["groups"])
    group_shape = tuple(
        tuple(
            bool(outcome["is_success"])
            for outcome in cast(list[dict[str, object]], group["outcomes"])
        )
        for group in groups
    )
    return (
        fields["result_type_code"],
        fields["symmetry_type_code"],
        fields["default_target"],
        fields["fixed_seconds"],
        fields["value_1_label"] is not None,
        fields["value_1_unit"],
        fields["value_1_higher_is_better"],
        fields["value_2_label"] is not None,
        fields["value_2_unit"],
        fields["value_2_higher_is_better"],
        tuple(cast(list[int], fields["spot_ids"])),
        group_shape,
    )


def _insert_version(session: Session, drill_id: int, form: DrillForm) -> int:
    """
    Description:
        Inserts a new drill version with its spots or outcome groups and buttons.

    Parameters:
        session: the write session.
        drill_id: the drill lineage id.
        form: the checked form.

    Return Values:
        int: the new version_no (assigned by h_data.tf_version_guard).

    Notes:
        Contents can only be added in the transaction that creates their version
        (tf_version_guard), so this runs inside the save's single transaction. Three batch
        inserts at most, none in a loop (COD-16-01).
    """
    row = fetch_one(
        session,
        INSERT_DRILL_VERSION_SQL,
        {
            "drill_id": drill_id,
            "result_type_code": form["result_type_code"],
            "symmetry_type_code": form["symmetry_type_code"],
            "default_target": form["default_target"],
            "fixed_seconds": form["fixed_seconds"],
            "value_1_label": form["value_1_label"],
            "value_1_unit": form["value_1_unit"],
            "value_1_higher_is_better": form["value_1_higher_is_better"],
            "value_2_label": form["value_2_label"],
            "value_2_unit": form["value_2_unit"],
            "value_2_higher_is_better": form["value_2_higher_is_better"],
        },
    )
    if row is None:
        logger.error("Drill version insert returned no id")
        raise RuntimeError("Drill version insert returned no id")
    version_id = cast(int, row["drill_version_id"])
    if form["spot_ids"]:
        execute(
            session,
            INSERT_DRILL_SPOTS_SQL,
            {"drill_version_id": version_id, "spot_ids": list(form["spot_ids"])},
        )
    if form["groups"]:
        execute(
            session,
            INSERT_OUTCOME_GROUPS_SQL,
            {
                "drill_version_id": version_id,
                "group_names": [group["group_name"] for group in form["groups"]],
            },
        )
        group_nos: list[int] = []
        outcome_nos: list[int] = []
        labels: list[str] = []
        successes: list[bool] = []
        for group_no, group in enumerate(form["groups"], start=1):
            for outcome_no, outcome in enumerate(group["outcomes"], start=1):
                group_nos.append(group_no)
                outcome_nos.append(outcome_no)
                labels.append(outcome["label"])
                successes.append(outcome["is_success"])
        execute(
            session,
            INSERT_OUTCOMES_SQL,
            {
                "drill_version_id": version_id,
                "group_nos": group_nos,
                "outcome_nos": outcome_nos,
                "labels": labels,
                "successes": successes,
            },
        )
    return cast(int, row["version_no"])


def _update_labels_in_place(
    session: Session, current: dict[str, object], form: DrillForm
) -> None:
    """
    Description:
        Writes the form's labels onto the latest version when only labels changed: the two
        value labels, the group names and the button labels (REQ-080, D12).

    Parameters:
        session: the write session.
        current: get_drill_editor of the drill, read after the lineage lock.
        form: the checked form, whose recording signature equals the current one.

    Return Values:
        None.

    Notes:
        Equal signatures mean the same group and button counts, so groups and buttons pair
        up by position. Each statement updates only rows whose label differs; three batch
        statements, none in a loop (COD-16-01). h_data.tf_version_guard allows label
        changes in place.
    """
    execute(
        session,
        UPDATE_VALUE_LABELS_SQL,
        {
            "drill_version_id": current["drill_version_id"],
            "value_1_label": form["value_1_label"],
            "value_2_label": form["value_2_label"],
        },
    )
    current_groups = cast(list[dict[str, object]], current["groups"])
    if not current_groups:
        return
    group_ids: list[int] = []
    group_names: list[str] = []
    outcome_ids: list[int] = []
    outcome_labels: list[str] = []
    for current_group, form_group in zip(current_groups, form["groups"], strict=True):
        group_ids.append(cast(int, current_group["outcome_group_id"]))
        group_names.append(form_group["group_name"])
        current_outcomes = cast(list[dict[str, object]], current_group["outcomes"])
        for current_outcome, form_outcome in zip(
            current_outcomes, form_group["outcomes"], strict=True
        ):
            outcome_ids.append(cast(int, current_outcome["drill_outcome_id"]))
            outcome_labels.append(form_outcome["label"])
    execute(
        session, UPDATE_GROUP_NAMES_SQL, {"group_ids": group_ids, "group_names": group_names}
    )
    execute(
        session,
        UPDATE_OUTCOME_LABELS_SQL,
        {"outcome_ids": outcome_ids, "labels": outcome_labels},
    )


def _save_secondary_types(session: Session, drill_id: int, type_ids: list[int]) -> None:
    """
    Description:
        Makes the drill's live secondary types equal to type_ids: removed links are
        soft-deleted, added ones inserted as new rows (COD-22-02).

    Parameters:
        session: the write session, with the drill lineage locked (or just inserted).
        drill_id: the drill lineage id.
        type_ids: the wanted secondary drill type ids (never the primary type).

    Return Values:
        None.

    Notes:
        The only direct deleted_at update in the library: the links have no children
        (plan §3.9, Deletes).
    """
    execute(
        session,
        SOFT_DELETE_SECONDARY_TYPES_SQL,
        {"drill_id": drill_id, "keep_type_ids": list(type_ids)},
    )
    if type_ids:
        execute(
            session,
            INSERT_SECONDARY_TYPES_SQL,
            {"drill_id": drill_id, "type_ids": list(type_ids)},
        )


def _lineage_params(form: DrillForm) -> dict[str, object]:
    """
    Description:
        The bound parameters of the lineage insert and update.

    Parameters:
        form: the checked form.

    Return Values:
        dict[str, object]: drill_name, description, primary_drill_type_id, difficulty_code.

    Notes:
        Pure.
    """
    return {
        "drill_name": form["drill_name"],
        "description": form["description"],
        "primary_drill_type_id": form["primary_drill_type_id"],
        "difficulty_code": form["difficulty_code"],
    }


def save_drill(session: Session, drill_id: int | None, form: DrillForm) -> dict[str, object]:
    """
    Description:
        Saves the drill form in one transaction (plan §3.4). A new drill gets its lineage,
        secondary types and version 1. An existing drill has its lineage locked and
        updated in place and its secondary types synchronised; then a changed recording
        signature inserts a new version, while a change of labels only updates the latest
        version in place (REQ-080, D12).

    Parameters:
        session: the write session (inside run_write_with_reason, acting user =
            current_user.user_id).
        drill_id: the drill LINEAGE id from the drill-form-id store, or None for a new drill.
            Never a version id (COD-8-01).
        form: the checked form (controller_drills.parse_drill_form).

    Return Values:
        dict[str, object]: {"drill_id": int, "version_no": int, "new_version": bool}.

    Notes:
        Raises LookupError when the drill is no longer live or was retired meanwhile. Ends
        with SET CONSTRAINTS ALL IMMEDIATE so an incomplete version fails here, where its
        message can be shown (plan §3.2). A duplicate name raises the ux_drills_name unique
        violation (REQ-028).
    """
    if drill_id is None:
        row = fetch_one(session, INSERT_DRILL_SQL, _lineage_params(form))
        if row is None:
            logger.error("Drill insert returned no id")
            raise RuntimeError("Drill insert returned no id")
        new_drill_id = cast(int, row["drill_id"])
        _save_secondary_types(session, new_drill_id, form["secondary_type_ids"])
        version_no = _insert_version(session, new_drill_id, form)
        execute(session, SET_CONSTRAINTS_IMMEDIATE_SQL)
        return {"drill_id": new_drill_id, "version_no": version_no, "new_version": True}

    if fetch_one(session, LOCK_LIVE_DRILL_SQL, {"drill_id": drill_id}) is None:
        raise LookupError(f"drill {drill_id} is not live or is retired")
    execute(session, UPDATE_DRILL_LINEAGE_SQL, {**_lineage_params(form), "drill_id": drill_id})
    _save_secondary_types(session, drill_id, form["secondary_type_ids"])
    current = get_drill_editor(session, drill_id)
    if current is None:
        raise LookupError(f"drill {drill_id} has no live version")
    if recording_signature(current) != recording_signature(form):
        version_no = _insert_version(session, drill_id, form)
        new_version = True
    else:
        _update_labels_in_place(session, current, form)
        version_no = cast(int, current["version_no"])
        new_version = False
    execute(session, SET_CONSTRAINTS_IMMEDIATE_SQL)
    return {"drill_id": drill_id, "version_no": version_no, "new_version": new_version}


# --- Grid edit (D11) ---

UPDATE_DRILL_NAME_SQL = """
UPDATE h_data.drills
SET drill_name = :value
WHERE drill_id = :drill_id
  AND deleted_at IS NULL
  AND retired_at IS NULL
RETURNING drill_id
"""

UPDATE_DRILL_DIFFICULTY_SQL = """
UPDATE h_data.drills
SET difficulty_code = :value
WHERE drill_id = :drill_id
  AND deleted_at IS NULL
  AND retired_at IS NULL
RETURNING drill_id
"""

# The whitelist of grid-editable fields, each with its own fixed statement (COD-14-03).
GRID_FIELD_STATEMENTS: dict[str, str] = {
    GRID_FIELD_NAME: UPDATE_DRILL_NAME_SQL,
    GRID_FIELD_DIFFICULTY: UPDATE_DRILL_DIFFICULTY_SQL,
}


def update_drill_lineage_field(
    session: Session, drill_id: int, field: str, value: str
) -> bool:
    """
    Description:
        Saves one in-cell edit of the library grid on the drill lineage: the name or the
        difficulty (D11). Recording fields are only changed through the form.

    Parameters:
        session: the write session (inside run_write_with_reason).
        drill_id: the drill LINEAGE id from the grid row id.
        field: "drill_name" or "difficulty_code".
        value: the checked new value (a stripped name, or a difficulty code).

    Return Values:
        bool: True when the drill was updated; False when it is no longer live or retired.

    Notes:
        Raises ValueError for any other field. A duplicate name raises the ux_drills_name
        unique violation (REQ-028).
    """
    statement = GRID_FIELD_STATEMENTS.get(field)
    if statement is None:
        raise ValueError(f"field {field!r} cannot be edited in the grid")
    row = fetch_one(session, statement, {"drill_id": drill_id, "value": value})
    return row is not None


# --- Usage and removal (REQ-081) ---

GET_LIBRARY_USAGE_SQL = """
SELECT h_data.fn_practice_usage(CAST(:entity AS text), CAST(:lineage_id AS bigint)) AS practice_count,
       CASE WHEN CAST(:entity AS text) = 'drill'
            THEN (SELECT count(*)::integer FROM h_data.goals g WHERE g.drill_id = CAST(:lineage_id AS bigint))
            ELSE 0
       END AS goal_count
"""

REMOVE_LIBRARY_ITEM_SQL = """
SELECT action, practice_count, goal_count
FROM h_data.fn_remove_library_item(CAST(:entity AS text), CAST(:lineage_id AS bigint))
"""


def _check_entity(entity: str) -> None:
    """
    Description:
        Refuses an entity name the library functions do not know.

    Parameters:
        entity: "drill", "drill_set" or "practice_template".

    Return Values:
        None. Raises ValueError for any other name.

    Notes:
        None.
    """
    if entity not in LIBRARY_ENTITIES:
        raise ValueError(f"unknown library entity {entity!r}")


def get_library_usage(session: Session, entity: str, lineage_id: int) -> dict[str, int]:
    """
    Description:
        How many practices (live or in Recently deleted) use any version of a library item,
        and, for a drill, how many goals target it in any state (REQ-081).

    Parameters:
        session: the request session.
        entity: "drill", "drill_set" or "practice_template".
        lineage_id: the LINEAGE id (drill_id, drill_set_id or practice_template_id).

    Return Values:
        dict[str, int]: {"practice_count", "goal_count"} (goal_count is 0 for sets and
        templates).

    Notes:
        The same counts h_data.fn_remove_library_item uses to choose between delete and
        retire, so the warning matches what happens.
    """
    _check_entity(entity)
    row = fetch_one(
        session, GET_LIBRARY_USAGE_SQL, {"entity": entity, "lineage_id": lineage_id}
    )
    if row is None:
        return {"practice_count": 0, "goal_count": 0}
    return {
        "practice_count": int(cast(int, row["practice_count"] or 0)),
        "goal_count": int(cast(int, row["goal_count"] or 0)),
    }


def remove_library_item(session: Session, entity: str, lineage_id: int) -> dict[str, object]:
    """
    Description:
        Removes a drill, drill set or practice template through
        h_data.fn_remove_library_item: deleted (restorable for 30 days) when unused, retired
        when used (REQ-081).

    Parameters:
        session: the write session (inside run_write_with_reason).
        entity: "drill", "drill_set" or "practice_template".
        lineage_id: the LINEAGE id from the form's id store.

    Return Values:
        dict[str, object]: {"action": "deleted" | "retired", "practice_count",
        "goal_count"}.

    Notes:
        The function raises (P0001) when the item is gone or already retired and still in
        use; that message reaches the user through run_write_with_reason.
    """
    _check_entity(entity)
    row = fetch_one(
        session, REMOVE_LIBRARY_ITEM_SQL, {"entity": entity, "lineage_id": lineage_id}
    )
    if row is None:
        logger.error("Library removal returned no row")
        raise RuntimeError("Library removal returned no row")
    return dict(row)
