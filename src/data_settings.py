"""Data access for the Settings data pages: Recently deleted with Restore, the retention period,
the CSV export and the database status (REQ-006, REQ-043, REQ-071; task
2026-10-23_settings_hub).

Every function takes the request session first and runs its SQL through data_handler. Write
functions never commit: they run inside app_controller.run_write (COD-6-01, COD-7-01).
Recently deleted reads h_data.v_recently_deleted and restores through h_data.fn_restore, so the
rules of what is listed and what can be restored live in the database only (COD-22-03).
"""

import logging
from typing import cast

from sqlalchemy.orm import Session

from src.data_handler import execute, fetch_all, fetch_one, fetch_table

logger = logging.getLogger(__name__)


# --- Recently deleted (REQ-006) ---

# The "Show" filter of Recently deleted: key -> entity names of h_data.v_recently_deleted.
DELETED_KIND_PLAYERS = ("player",)
DELETED_KIND_PRACTICES = (
    "practice_session",
    "practice_drill_set",
    "practice_drill",
    "drill_result",
    "drill_attempt",
)
DELETED_KIND_GAMES = ("game",)
DELETED_KIND_SHOTS = ("shot_entry", "free_throw_entry")
DELETED_KIND_SEASONS = ("season",)
DELETED_KIND_GOALS = ("goal", "physical_measurement")
DELETED_KIND_LIBRARY = ("drill", "drill_set", "practice_template", "drill_type", "physical_test")

# Every entity name of v_recently_deleted, which are exactly fn_restore's (17).
RESTORABLE_ENTITIES: frozenset[str] = frozenset(
    DELETED_KIND_PLAYERS
    + DELETED_KIND_PRACTICES
    + DELETED_KIND_GAMES
    + DELETED_KIND_SHOTS
    + DELETED_KIND_SEASONS
    + DELETED_KIND_GOALS
    + DELETED_KIND_LIBRARY
)

DELETED_KIND_ALL = "all"
DELETED_KIND_ENTITIES: dict[str, tuple[str, ...]] = {
    DELETED_KIND_ALL: tuple(sorted(RESTORABLE_ENTITIES)),
    "players": DELETED_KIND_PLAYERS,
    "practices": DELETED_KIND_PRACTICES,
    "games": DELETED_KIND_GAMES,
    "shots": DELETED_KIND_SHOTS,
    "seasons": DELETED_KIND_SEASONS,
    "goals": DELETED_KIND_GOALS,
    "library": DELETED_KIND_LIBRARY,
}

# LIKE escape character for user-typed search text (so % and _ match literally).
LIKE_ESCAPE = "!"

LIST_RECENTLY_DELETED_SQL = """
SELECT entity, entity_id, label, deleted_at, deleted_by_name, purge_after,
       count(*) OVER () AS total_count
FROM h_data.v_recently_deleted
WHERE entity = ANY(CAST(:entities AS text[]))
  AND (CAST(:search_pattern AS text) IS NULL
       OR label ILIKE CAST(:search_pattern AS text) ESCAPE '!')
  AND (CAST(:exclude_pattern AS text) IS NULL
       OR label NOT ILIKE CAST(:exclude_pattern AS text) ESCAPE '!')
ORDER BY deleted_at DESC, entity, entity_id DESC
LIMIT :row_limit
"""

GET_LISTED_DELETED_SQL = """
SELECT label
FROM h_data.v_recently_deleted
WHERE entity = :entity
  AND entity_id = :entity_id
"""

RESTORE_ITEM_SQL = """
SELECT h_data.fn_restore(CAST(:entity AS text), CAST(:entity_id AS bigint)) AS restored
"""


def contains_pattern(text: str) -> str:
    """
    Description:
        Turns typed text into a "contains" pattern for ILIKE, with %, _ and the escape
        character itself escaped so they match literally.

    Parameters:
        text: the text to look for.

    Return Values:
        str: "%" + escaped text + "%", for a query that declares ESCAPE '!'.

    Notes:
        Pure. The pattern is still a bound parameter (COD-14-03).
    """
    escaped = (
        text.replace(LIKE_ESCAPE, LIKE_ESCAPE + LIKE_ESCAPE)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"


def list_recently_deleted(
    session: Session,
    entities: tuple[str, ...],
    search: str | None,
    exclude_text: str | None,
    limit: int,
) -> tuple[list[dict[str, object]], int]:
    """
    Description:
        Lists independently deleted items from h_data.v_recently_deleted, newest first.

    Parameters:
        session: the request session.
        entities: the entity names to show (a value of DELETED_KIND_ENTITIES).
        search: text the label must contain (case-insensitive), or None.
        exclude_text: text the label must not contain (the test-data marker), or None.
        limit: the most rows to return (RECENTLY_DELETED_LIMIT).

    Return Values:
        tuple[list[dict[str, object]], int]: rows {"entity", "entity_id", "label",
        "deleted_at", "deleted_by_name", "purge_after"} and the number of matching rows
        before the limit.

    Notes:
        Read-only. Order: deleted_at newest first, then entity, then id newest first.
    """
    rows = fetch_all(
        session,
        LIST_RECENTLY_DELETED_SQL,
        {
            "entities": list(entities),
            "search_pattern": contains_pattern(search) if search else None,
            "exclude_pattern": contains_pattern(exclude_text) if exclude_text else None,
            "row_limit": limit,
        },
    )
    total = cast(int, rows[0]["total_count"]) if rows else 0
    items = [{key: value for key, value in row.items() if key != "total_count"} for row in rows]
    return items, total


def get_listed_deleted(session: Session, entity: str, entity_id: int) -> str | None:
    """
    Description:
        Whether an item is still listed in Recently deleted, and its label.

    Parameters:
        session: the request session.
        entity: an entity name of RESTORABLE_ENTITIES.
        entity_id: the item's id.

    Return Values:
        str | None: the label, or None when the item is not listed (restored meanwhile, or
        hidden with a removed profile).

    Notes:
        Read-only.
    """
    row = fetch_one(session, GET_LISTED_DELETED_SQL, {"entity": entity, "entity_id": entity_id})
    return None if row is None else str(row["label"])


def restore_item(session: Session, entity: str, entity_id: int) -> int:
    """
    Description:
        Restores one deleted item, and everything deleted with it, through h_data.fn_restore.

    Parameters:
        session: the write session (inside run_write, acting user = the signed-in user).
        entity: an entity name of RESTORABLE_ENTITIES.
        entity_id: the item's id.

    Return Values:
        int: the number of rows restored.

    Notes:
        Raises ValueError for an entity outside RESTORABLE_ENTITIES, before any SQL.
        fn_restore refuses with RAISE EXCEPTION (SQLSTATE P0001) and a user-readable
        message, e.g. "... was removed; restore the player first"; run_write_with_reason
        carries that message to the screen (D10).
    """
    if entity not in RESTORABLE_ENTITIES:
        raise ValueError(f"Unknown entity {entity!r} for restore")
    row = fetch_one(session, RESTORE_ITEM_SQL, {"entity": entity, "entity_id": entity_id})
    if row is None:
        logger.error("Restore returned no row for %s %s", entity, entity_id)
        raise RuntimeError("Restore returned no row")
    return cast(int, row["restored"])


# --- Retention period (REQ-006, D12) ---

RETENTION_SETTING_KEY = "soft_delete_retention_days"

GET_SETTING_SQL = """
SELECT setting_value
FROM h_system.app_settings
WHERE setting_key = :setting_key
  AND deleted_at IS NULL
"""

SET_SETTING_SQL = """
UPDATE h_system.app_settings
SET setting_value = :setting_value
WHERE setting_key = :setting_key
  AND deleted_at IS NULL
"""


def get_retention_days(session: Session) -> int:
    """
    Description:
        Reads the soft-delete retention period in days.

    Parameters:
        session: the request session.

    Return Values:
        int: h_system.app_settings soft_delete_retention_days.

    Notes:
        Raises RuntimeError if the seeded setting is missing (a database build problem).
        The value is an integer by ck_app_settings_value.
    """
    row = fetch_one(session, GET_SETTING_SQL, {"setting_key": RETENTION_SETTING_KEY})
    if row is None:
        logger.error("Setting %s is missing", RETENTION_SETTING_KEY)
        raise RuntimeError("Retention setting is missing")
    return int(str(row["setting_value"]))


def set_retention_days(session: Session, days: int) -> None:
    """
    Description:
        Changes the soft-delete retention period.

    Parameters:
        session: the write session (inside run_write, acting user = the signed-in user).
        days: the checked number of days (RETENTION_DAYS_MIN to RETENTION_DAYS_MAX).

    Return Values:
        None.

    Notes:
        Raises RuntimeError unless exactly one row changes. Only this function uses the
        app role's UPDATE on h_system.app_settings. Nothing purges yet (D12).
    """
    changed = execute(
        session,
        SET_SETTING_SQL,
        {"setting_key": RETENTION_SETTING_KEY, "setting_value": str(days)},
    )
    if changed != 1:
        logger.error("Retention update changed %s rows, expected 1", changed)
        raise RuntimeError("Retention update did not change exactly one row")


# --- Export (REQ-043, D13) ---

# Every base table of h_data (28) and h_ref (12), copied from sql/h_data.sql and sql/h_ref.sql,
# alphabetical. h_system (password, key and code hashes; device and sign-in logs) and h_staging
# are never exported.
EXPORT_TABLES: tuple[str, ...] = (
    "h_data.drill_attempt_outcomes",
    "h_data.drill_attempts",
    "h_data.drill_outcome_groups",
    "h_data.drill_outcomes",
    "h_data.drill_results",
    "h_data.drill_secondary_types",
    "h_data.drill_set_items",
    "h_data.drill_set_lines",
    "h_data.drill_set_versions",
    "h_data.drill_sets",
    "h_data.drill_spots",
    "h_data.drill_types",
    "h_data.drill_versions",
    "h_data.drills",
    "h_data.free_throw_entries",
    "h_data.games",
    "h_data.goals",
    "h_data.physical_measurements",
    "h_data.players",
    "h_data.practice_drill_sets",
    "h_data.practice_drills",
    "h_data.practice_players",
    "h_data.practice_sessions",
    "h_data.practice_template_sets",
    "h_data.practice_template_versions",
    "h_data.practice_templates",
    "h_data.seasons",
    "h_data.shot_entries",
    "h_ref.court_spots",
    "h_ref.difficulty_levels",
    "h_ref.game_categories",
    "h_ref.game_formats",
    "h_ref.heat_map_bands",
    "h_ref.physical_tests",
    "h_ref.practice_types",
    "h_ref.result_types",
    "h_ref.shot_types",
    "h_ref.stat_definitions",
    "h_ref.symmetry_types",
    "h_ref.zones",
)
EXPORT_TABLE_SET: frozenset[str] = frozenset(EXPORT_TABLES)


def export_table_rows(
    session: Session, table: str
) -> tuple[list[str], list[tuple[object, ...]]]:
    """
    Description:
        Reads every live row of one exportable table, all columns, ordered by the first
        column.

    Parameters:
        session: the request session.
        table: a schema-qualified name of EXPORT_TABLES.

    Return Values:
        tuple[list[str], list[tuple[object, ...]]]: (column names, rows).

    Notes:
        Raises ValueError for a name outside EXPORT_TABLES, before any SQL. Soft-deleted
        rows are left out (REQ-043). Read-only.
    """
    if table not in EXPORT_TABLE_SET:
        raise ValueError(f"Table {table!r} is not exportable")
    # The identifier cannot be a bound parameter. It is not external input: the check above
    # admits only a literal of EXPORT_TABLES, so this is not string building from external
    # values (COD-14-03).
    sql = f"SELECT * FROM {table} WHERE deleted_at IS NULL ORDER BY 1"
    return fetch_table(session, sql)


# --- About (D16) ---

SERVER_VERSION_SQL = "SHOW server_version"


def get_database_status(session: Session) -> dict[str, object]:
    """
    Description:
        The database facts Settings > About shows.

    Parameters:
        session: the request session.

    Return Values:
        dict[str, object]: {"server_version": the PostgreSQL version text}.

    Notes:
        Read-only. A failure propagates; the caller shows "not reachable".
    """
    row = fetch_one(session, SERVER_VERSION_SQL)
    return {"server_version": None if row is None else str(row["server_version"])}
