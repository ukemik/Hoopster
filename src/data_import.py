"""Data access for the Import page: the item-type registry and the h_staging batch reads and
writes (REQ-027, REQ-050; task 2026-10-23_drill_import).

Every import lands in h_staging (COD-1-06): the app creates a batch, stages raw text cells in
the item type's staging table, runs the type's check function and, after a confirmation, its
publish function. No import writes to h_data any other way. Write functions never commit: they
run inside app_controller.run_write / run_write_with_reason, after the acting user has been set
(COD-6-01, COD-7-01). The item type selects fixed SQL from IMPORT_ITEM_TYPES; it is never
interpolated into a statement (COD-14-03).
"""

import logging
from dataclasses import dataclass
from typing import TypedDict, cast

from sqlalchemy.orm import Session

from src.data_drills import SET_CONSTRAINTS_IMMEDIATE_SQL
from src.data_handler import execute, fetch_all, fetch_one

logger = logging.getLogger(__name__)

# h_staging.import_batches.item_type / source_kind / batch_status values
# (ck_import_batches_item_type, _source_kind, _status).
ITEM_TYPE_DRILL = "drill"
SOURCE_CSV = "csv"
SOURCE_XLSX = "xlsx"
SOURCE_PASTE = "paste"
SOURCE_KINDS: frozenset[str] = frozenset({SOURCE_CSV, SOURCE_XLSX, SOURCE_PASTE})
STATUS_LOADED = "loaded"
STATUS_HAS_ERRORS = "has_errors"
STATUS_READY = "ready"
STATUS_PUBLISHED = "published"
STATUS_ABANDONED = "abandoned"
# A batch in one of these statuses can still be checked, published or abandoned.
OPEN_STATUSES: tuple[str, ...] = (STATUS_LOADED, STATUS_HAS_ERRORS, STATUS_READY)
# Result types fn_check_drill_batch refuses (Gate 1 Q8); the paste form does not offer them.
NOT_IMPORTABLE_RESULT_TYPES: frozenset[str] = frozenset({"outcome_buttons", "physical"})
# ck_import_batches_source_name.
SOURCE_NAME_MAX_LENGTH = 200


# --- Registry (REQ-050: a new item type is one entry plus its own staging objects) ---

@dataclass(frozen=True)
class ImportItemType:
    """
    Description:
        One importable item type: its names on screen, its staging columns and the SQL of
        its staging table and functions.

    Parameters:
        code: the h_staging.import_batches.item_type value, e.g. "drill".
        label: the item-type select's label, e.g. "Drills".
        singular: the item word for one, e.g. "drill".
        plural: the item word for several, e.g. "drills".
        columns: the staging table's data columns in file order; also the template header
            and the bind names of stage_sql (one text array per column).
        required_column: the column a file must have (its name column).
        stage_sql: one INSERT ... SELECT over unnest of :row_nos and one array per column.
        check_sql: runs the type's check function for :import_batch_id.
        publish_sql: runs the type's publish function for :import_batch_id, returning
            published_count.
        rows_sql: the batch's live rows for the preview, ordered by row_no.

    Return Values:
        Not applicable (frozen data class).

    Notes:
        New in this task; no existing __init__ changes.
    """

    code: str
    label: str
    singular: str
    plural: str
    columns: tuple[str, ...]
    required_column: str
    stage_sql: str
    check_sql: str
    publish_sql: str
    rows_sql: str


class StagedRow(TypedDict):
    """
    Description:
        One row to stage: its row number and its raw text cells.

    Parameters:
        row_no: the spreadsheet row (header = 1) or the pasted line number, at least 1.
        cells: staging column -> raw text, or None for an empty or absent cell.

    Return Values:
        Not applicable (typed dict).

    Notes:
        Built only by the controller's pure parsers; content is judged by the check
        function, never in Python (plan D3).
    """

    row_no: int
    cells: dict[str, str | None]


DRILL_IMPORT_COLUMNS: tuple[str, ...] = (
    "drill_name",
    "description",
    "primary_type",
    "secondary_types",
    "difficulty",
    "result_type",
    "symmetry_type",
    "default_target",
    "fixed_seconds",
    "spots",
)

# All rows in one statement: parallel bound arrays, unnested together (COD-16-01).
STAGE_DRILL_ROWS_SQL = """
INSERT INTO h_staging.drill_import_rows
    (import_batch_id, row_no, drill_name, description, primary_type, secondary_types,
     difficulty, result_type, symmetry_type, default_target, fixed_seconds, spots)
SELECT CAST(:import_batch_id AS bigint), u.row_no, u.drill_name, u.description,
       u.primary_type, u.secondary_types, u.difficulty, u.result_type, u.symmetry_type,
       u.default_target, u.fixed_seconds, u.spots
FROM unnest(CAST(:row_nos AS integer[]),
            CAST(:drill_name AS text[]),
            CAST(:description AS text[]),
            CAST(:primary_type AS text[]),
            CAST(:secondary_types AS text[]),
            CAST(:difficulty AS text[]),
            CAST(:result_type AS text[]),
            CAST(:symmetry_type AS text[]),
            CAST(:default_target AS text[]),
            CAST(:fixed_seconds AS text[]),
            CAST(:spots AS text[]))
     AS u(row_no, drill_name, description, primary_type, secondary_types, difficulty,
          result_type, symmetry_type, default_target, fixed_seconds, spots)
"""

CHECK_DRILL_BATCH_SQL = """
SELECT row_no
FROM h_staging.fn_check_drill_batch(CAST(:import_batch_id AS bigint))
"""

PUBLISH_DRILL_BATCH_SQL = """
SELECT h_staging.fn_publish_drill_batch(CAST(:import_batch_id AS bigint)) AS published_count
"""

# Rows of a live batch only (COD-22-03).
LIST_DRILL_BATCH_ROWS_SQL = """
SELECT r.row_no, r.drill_name, r.primary_type, r.difficulty, r.result_type,
       r.row_status, r.error_message
FROM h_staging.drill_import_rows r
JOIN h_staging.import_batches b
  ON b.import_batch_id = r.import_batch_id
 AND b.deleted_at IS NULL
WHERE r.import_batch_id = CAST(:import_batch_id AS bigint)
  AND r.deleted_at IS NULL
ORDER BY r.row_no
"""

IMPORT_ITEM_TYPES: dict[str, ImportItemType] = {
    ITEM_TYPE_DRILL: ImportItemType(
        code=ITEM_TYPE_DRILL,
        label="Drills",
        singular="drill",
        plural="drills",
        columns=DRILL_IMPORT_COLUMNS,
        required_column="drill_name",
        stage_sql=STAGE_DRILL_ROWS_SQL,
        check_sql=CHECK_DRILL_BATCH_SQL,
        publish_sql=PUBLISH_DRILL_BATCH_SQL,
        rows_sql=LIST_DRILL_BATCH_ROWS_SQL,
    ),
}


def get_item_type(item_type: str) -> ImportItemType:
    """
    Description:
        The registry entry of an item type.

    Parameters:
        item_type: an item type code, e.g. "drill".

    Return Values:
        ImportItemType: the entry.

    Notes:
        Raises ValueError for a code that is not a key of IMPORT_ITEM_TYPES.
    """
    item = IMPORT_ITEM_TYPES.get(item_type)
    if item is None:
        raise ValueError(f"unknown import item type {item_type!r}")
    return item


# --- Batch writes ---

INSERT_BATCH_SQL = """
INSERT INTO h_staging.import_batches (item_type, source_kind, source_name)
VALUES (:item_type, :source_kind, :source_name)
RETURNING import_batch_id
"""

ABANDON_BATCH_SQL = """
UPDATE h_staging.import_batches
SET batch_status = 'abandoned'
WHERE import_batch_id = CAST(:import_batch_id AS bigint)
  AND deleted_at IS NULL
  AND batch_status = ANY (CAST(:open_statuses AS text[]))
RETURNING import_batch_id
"""


def create_batch(
    session: Session, item_type: str, source_kind: str, source_name: str | None
) -> int:
    """
    Description:
        Creates an import batch (status "loaded").

    Parameters:
        session: the write session (inside run_write_with_reason, acting user =
            current_user.user_id, stamped as created_by by the audit trigger).
        item_type: a key of IMPORT_ITEM_TYPES.
        source_kind: "csv", "xlsx" or "paste".
        source_name: the uploaded file's name, or None for a paste; cut to 200 characters,
            and an empty name is stored as None.

    Return Values:
        int: the new h_staging.import_batches.import_batch_id.

    Notes:
        Raises ValueError for an unknown item type or source kind, before any SQL.
    """
    get_item_type(item_type)
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"unknown import source kind {source_kind!r}")
    name = source_name[:SOURCE_NAME_MAX_LENGTH] if source_name else None
    row = fetch_one(
        session,
        INSERT_BATCH_SQL,
        {"item_type": item_type, "source_kind": source_kind, "source_name": name},
    )
    if row is None:
        logger.error("Import batch insert returned no id")
        raise RuntimeError("Import batch insert returned no id")
    return cast(int, row["import_batch_id"])


def stage_rows(
    session: Session, item_type: str, import_batch_id: int, rows: list[StagedRow]
) -> int:
    """
    Description:
        Stages every row of a batch in one INSERT ... SELECT over unnest of parallel bound
        arrays: the row numbers and one text array per staging column.

    Parameters:
        session: the write session (inside run_write_with_reason).
        item_type: a key of IMPORT_ITEM_TYPES; selects the staging statement.
        import_batch_id: the BATCH id create_batch returned in the same transaction.
        rows: the rows to stage; a column missing from a row's cells is staged as NULL.

    Return Values:
        int: the number of rows inserted.

    Notes:
        Raises ValueError for an unknown item type or when rows is empty. One statement,
        never one per row (COD-16-01).
    """
    item = get_item_type(item_type)
    if not rows:
        raise ValueError("no rows to stage")
    params: dict[str, object] = {
        "import_batch_id": import_batch_id,
        "row_nos": [row["row_no"] for row in rows],
    }
    for column in item.columns:
        params[column] = [row["cells"].get(column) for row in rows]
    return execute(session, item.stage_sql, params)


def check_batch(session: Session, item_type: str, import_batch_id: int) -> None:
    """
    Description:
        Runs the item type's check function on a batch: it cleans and validates every row and
        sets the row and batch statuses and counts.

    Parameters:
        session: the write session (the check updates staging rows; commit through
            run_write / run_write_with_reason).
        item_type: a key of IMPORT_ITEM_TYPES.
        import_batch_id: the BATCH id.

    Return Values:
        None. The function's result rows are discarded; the preview reads the table.

    Notes:
        The function raises (P0001) for a published or abandoned batch.
    """
    item = get_item_type(item_type)
    fetch_all(session, item.check_sql, {"import_batch_id": import_batch_id})


def abandon_batch(session: Session, import_batch_id: int) -> bool:
    """
    Description:
        Marks a batch abandoned when it is live and still open (loaded, has_errors or ready).

    Parameters:
        session: the write session (inside run_write or run_write_with_reason).
        import_batch_id: the BATCH id from the import-batch store.

    Return Values:
        bool: True when the batch changed; False when it is gone, published or already
        abandoned.

    Notes:
        A status change in h_staging only (allowed by ck_import_batches_status); never
        touches h_data (COD-1-06). Not item-type specific: the batch log is shared.
    """
    row = fetch_one(
        session,
        ABANDON_BATCH_SQL,
        {"import_batch_id": import_batch_id, "open_statuses": list(OPEN_STATUSES)},
    )
    return row is not None


def publish_batch(session: Session, item_type: str, import_batch_id: int) -> int:
    """
    Description:
        Publishes a ready batch through the item type's publish function, then makes the
        deferred constraints run at once.

    Parameters:
        session: the write session (inside run_write_with_reason, acting user =
            current_user.user_id).
        item_type: a key of IMPORT_ITEM_TYPES.
        import_batch_id: the BATCH id from the import-batch store.

    Return Values:
        int: the number of items published.

    Notes:
        All or nothing in the caller's transaction. The function raises (P0001) unless the
        batch is ready, and "Batch <id> has <n> rows with errors; nothing was published"
        when its re-check finds errors. SET CONSTRAINTS ALL IMMEDIATE makes a deferred
        version-completeness failure surface here with its message (plan D12).
    """
    item = get_item_type(item_type)
    row = fetch_one(session, item.publish_sql, {"import_batch_id": import_batch_id})
    execute(session, SET_CONSTRAINTS_IMMEDIATE_SQL)
    if row is None:
        logger.error("Import publish returned no row")
        raise RuntimeError("Import publish returned no row")
    return int(cast(int, row["published_count"] or 0))


# --- Batch reads ---

GET_BATCH_SQL = """
SELECT import_batch_id, item_type, source_kind, source_name, batch_status,
       row_count, error_row_count, published_row_count
FROM h_staging.import_batches
WHERE import_batch_id = CAST(:import_batch_id AS bigint)
  AND deleted_at IS NULL
"""


def get_batch(session: Session, import_batch_id: int) -> dict[str, object] | None:
    """
    Description:
        Reads a live batch.

    Parameters:
        session: the request session.
        import_batch_id: the BATCH id.

    Return Values:
        dict[str, object] | None: {"import_batch_id", "item_type", "source_kind",
        "source_name", "batch_status", "row_count", "error_row_count",
        "published_row_count"}, or None when the batch does not exist or is deleted.

    Notes:
        None.
    """
    return fetch_one(session, GET_BATCH_SQL, {"import_batch_id": import_batch_id})


def list_batch_rows(
    session: Session, item_type: str, import_batch_id: int
) -> list[dict[str, object]]:
    """
    Description:
        Reads the live rows of a live batch for the preview.

    Parameters:
        session: the request session.
        item_type: a key of IMPORT_ITEM_TYPES; selects the staging table.
        import_batch_id: the BATCH id.

    Return Values:
        list[dict[str, object]]: for drills {"row_no", "drill_name", "primary_type",
        "difficulty", "result_type", "row_status", "error_message"} per row, ordered by
        row_no.

    Notes:
        Raises ValueError for an unknown item type.
    """
    item = get_item_type(item_type)
    return fetch_all(session, item.rows_sql, {"import_batch_id": import_batch_id})
