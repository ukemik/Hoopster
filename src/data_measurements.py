"""Data access for physical measurements and the physical-test list (REQ-034, REQ-035; task
2026-10-23_goals_measurements).

A measurement belongs to one player profile and one live physical test; the history is kept
(any number per test and day, D13). The test list is shared by all players and editable
(D2, D12); fn_soft_delete refuses a test still used by a live measurement or goal. Every
function takes the request session first and runs its SQL through data_handler; writes never
commit (they run inside app_controller.run_write). Bound parameters only (COD-14-03).
"""

import logging
from datetime import date
from decimal import Decimal
from typing import cast

from sqlalchemy.orm import Session

from src.data_handler import fetch_all, fetch_one

logger = logging.getLogger(__name__)


# --- Physical tests (REQ-034, D2, D12) ---

# use_count is the same count fn_soft_delete('physical_test') refuses on: live measurements
# plus live goals of the test.
PHYSICAL_TEST_COLUMNS_SQL = """
SELECT pt.physical_test_id, pt.test_name, pt.unit, pt.lower_is_better, pt.sort_order,
       ((SELECT count(*) FROM h_data.physical_measurements pm
         WHERE pm.physical_test_id = pt.physical_test_id AND pm.deleted_at IS NULL)
        + (SELECT count(*) FROM h_data.goals g
           WHERE g.physical_test_id = pt.physical_test_id AND g.deleted_at IS NULL))::integer
           AS use_count
FROM h_ref.physical_tests pt
"""

LIST_PHYSICAL_TESTS_SQL = (
    PHYSICAL_TEST_COLUMNS_SQL
    + """
WHERE pt.deleted_at IS NULL
ORDER BY pt.sort_order, lower(pt.test_name), pt.physical_test_id
"""
)

GET_PHYSICAL_TEST_SQL = (
    PHYSICAL_TEST_COLUMNS_SQL
    + """
WHERE pt.physical_test_id = :test_id
  AND pt.deleted_at IS NULL
"""
)

LOCK_PHYSICAL_TEST_SQL = (
    PHYSICAL_TEST_COLUMNS_SQL
    + """
WHERE pt.physical_test_id = :test_id
  AND pt.deleted_at IS NULL
FOR UPDATE OF pt
"""
)

# sort_order = the largest so far + 1, so a new test is listed last.
INSERT_PHYSICAL_TEST_SQL = """
INSERT INTO h_ref.physical_tests (test_name, unit, lower_is_better, sort_order)
SELECT CAST(:test_name AS text), CAST(:unit AS text), CAST(:lower_is_better AS boolean),
       CAST(COALESCE(max(t.sort_order), 0) + 1 AS smallint)
FROM h_ref.physical_tests t
RETURNING physical_test_id
"""

# The unit and direction may change only while the test is unused (D12); the controller
# checks first under lock_physical_test, and this statement re-checks (D18).
UPDATE_PHYSICAL_TEST_SQL = """
UPDATE h_ref.physical_tests pt
SET test_name = CAST(:test_name AS text),
    unit = CAST(:unit AS text),
    lower_is_better = CAST(:lower_is_better AS boolean)
WHERE pt.physical_test_id = :test_id
  AND pt.deleted_at IS NULL
  AND ((pt.unit = CAST(:unit AS text) AND pt.lower_is_better = CAST(:lower_is_better AS boolean))
       OR (NOT EXISTS (SELECT 1 FROM h_data.physical_measurements pm
                       WHERE pm.physical_test_id = pt.physical_test_id
                         AND pm.deleted_at IS NULL)
           AND NOT EXISTS (SELECT 1 FROM h_data.goals g
                           WHERE g.physical_test_id = pt.physical_test_id
                             AND g.deleted_at IS NULL)))
RETURNING pt.physical_test_id
"""

SOFT_DELETE_PHYSICAL_TEST_SQL = """
SELECT h_data.fn_soft_delete('physical_test', CAST(:test_id AS bigint)) AS rows_marked
"""


def list_physical_tests(session: Session) -> list[dict[str, object]]:
    """
    Description:
        Lists the live physical tests in their order, with how often each is used.

    Parameters:
        session: the request session.

    Return Values:
        list[dict[str, object]]: {"physical_test_id", "test_name", "unit" ("in", "lb" or
        "s"), "lower_is_better", "sort_order", "use_count"} per test, by sort_order, then
        name.

    Notes:
        Read only. use_count counts live measurements plus live goals, as the delete does.
    """
    return fetch_all(session, LIST_PHYSICAL_TESTS_SQL)


def get_physical_test(session: Session, test_id: int) -> dict[str, object] | None:
    """
    Description:
        Loads one live physical test with its use count.

    Parameters:
        session: the request session.
        test_id: h_ref.physical_tests.physical_test_id.

    Return Values:
        dict[str, object] | None: the list_physical_tests columns, or None when the test is
        not live.

    Notes:
        Read only.
    """
    return fetch_one(session, GET_PHYSICAL_TEST_SQL, {"test_id": test_id})


def lock_physical_test(session: Session, test_id: int) -> dict[str, object] | None:
    """
    Description:
        Loads and locks (FOR UPDATE) one live physical test with its use count.

    Parameters:
        session: the write session (inside run_write).
        test_id: h_ref.physical_tests.physical_test_id (the clicked row's pattern index).

    Return Values:
        dict[str, object] | None: the list_physical_tests columns, or None when the test is
        not live.

    Notes:
        Used by Save, so the unused-test rule (D12) is judged in the write transaction.
    """
    return fetch_one(session, LOCK_PHYSICAL_TEST_SQL, {"test_id": test_id})


def insert_physical_test(
    session: Session, test_name: str, unit: str, lower_is_better: bool
) -> int:
    """
    Description:
        Adds a physical test at the end of the list (REQ-034).

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        test_name: the trimmed name, 1 to 100 characters.
        unit: "in", "lb" or "s".
        lower_is_better: True when a lower value is better (e.g. a sprint time).

    Return Values:
        int: the new h_ref.physical_tests.physical_test_id.

    Notes:
        A name already used by a live test (case-insensitive) raises the unique violation
        23505 of ux_physical_tests_name; the caller shows its message.
    """
    row = fetch_one(
        session,
        INSERT_PHYSICAL_TEST_SQL,
        {"test_name": test_name, "unit": unit, "lower_is_better": lower_is_better},
    )
    if row is None:
        logger.error("Physical test insert returned no id")
        raise RuntimeError("Physical test insert returned no id")
    return int(cast(int, row["physical_test_id"]))


def update_physical_test(
    session: Session, test_id: int, test_name: str, unit: str, lower_is_better: bool
) -> bool:
    """
    Description:
        Renames a live physical test, and changes its unit or direction while it is unused
        (D12).

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        test_id: h_ref.physical_tests.physical_test_id.
        test_name: the trimmed name, 1 to 100 characters.
        unit: "in", "lb" or "s".
        lower_is_better: True when a lower value is better.

    Return Values:
        bool: True when updated; False when the test is not live, or the unit or direction
        changed while a live measurement or goal uses it.

    Notes:
        A duplicate name raises 23505 (ux_physical_tests_name).
    """
    row = fetch_one(
        session,
        UPDATE_PHYSICAL_TEST_SQL,
        {
            "test_id": test_id,
            "test_name": test_name,
            "unit": unit,
            "lower_is_better": lower_is_better,
        },
    )
    return row is not None


def delete_physical_test(session: Session, test_id: int) -> None:
    """
    Description:
        Deletes a physical test through h_data.fn_soft_delete('physical_test', id), which
        refuses (RAISE, SQLSTATE P0001) while a live measurement or goal uses it.

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        test_id: h_ref.physical_tests.physical_test_id from the ptest-del-target store.

    Return Values:
        None.

    Notes:
        The caller shows the database's refusal (run_write_with_reason). Restorable from
        Recently deleted.
    """
    row = fetch_one(session, SOFT_DELETE_PHYSICAL_TEST_SQL, {"test_id": test_id})
    if row is None:
        logger.error("Physical test soft delete returned no row")
        raise RuntimeError("Physical test soft delete returned no row")


# --- Measurement reads (REQ-034, REQ-035) ---

# The newest live measurement per test, listed in test order.
LIST_LATEST_MEASUREMENTS_SQL = """
SELECT latest.physical_measurement_id, latest.physical_test_id, latest.test_name,
       latest.unit, latest.measured_on, latest.measured_value
FROM (
    SELECT DISTINCT ON (pm.physical_test_id)
           pm.physical_measurement_id, pm.physical_test_id, pt.test_name, pt.unit,
           pt.sort_order, pm.measured_on, pm.measured_value
    FROM h_data.physical_measurements pm
    JOIN h_ref.physical_tests pt ON pt.physical_test_id = pm.physical_test_id
    JOIN h_data.players p ON p.player_id = pm.player_id
    WHERE pm.player_id = :player_id
      AND pm.deleted_at IS NULL
      AND pt.deleted_at IS NULL
      AND p.deleted_at IS NULL
    ORDER BY pm.physical_test_id, pm.measured_on DESC, pm.physical_measurement_id DESC
) latest
ORDER BY latest.sort_order, lower(latest.test_name), latest.physical_test_id
"""

LIST_MEASUREMENT_SERIES_SQL = """
SELECT pm.physical_measurement_id, pm.measured_on, pm.measured_value
FROM h_data.physical_measurements pm
JOIN h_data.players p ON p.player_id = pm.player_id
WHERE pm.player_id = :player_id
  AND pm.physical_test_id = :test_id
  AND pm.deleted_at IS NULL
  AND p.deleted_at IS NULL
ORDER BY pm.measured_on, pm.physical_measurement_id
"""

LIST_MEASUREMENTS_SQL = """
SELECT pm.physical_measurement_id, pm.measured_on, pm.measured_value, pm.notes
FROM h_data.physical_measurements pm
JOIN h_data.players p ON p.player_id = pm.player_id
WHERE pm.player_id = :player_id
  AND pm.physical_test_id = :test_id
  AND pm.deleted_at IS NULL
  AND p.deleted_at IS NULL
ORDER BY pm.measured_on DESC, pm.physical_measurement_id DESC
LIMIT :limit
"""

GET_MEASUREMENT_SQL = """
SELECT pm.physical_measurement_id, pm.player_id, p.display_name AS player_name,
       pm.physical_test_id, pt.test_name, pt.unit, pm.measured_on, pm.measured_value,
       pm.notes, pm.created_at, cu.display_name AS created_by_name,
       pm.updated_at, uu.display_name AS updated_by_name
FROM h_data.physical_measurements pm
JOIN h_data.players p ON p.player_id = pm.player_id
JOIN h_ref.physical_tests pt ON pt.physical_test_id = pm.physical_test_id
JOIN h_system.users cu ON cu.user_id = pm.created_by
JOIN h_system.users uu ON uu.user_id = pm.updated_by
WHERE pm.physical_measurement_id = :measurement_id
  AND pm.deleted_at IS NULL
  AND p.deleted_at IS NULL
"""


def list_latest_measurements(session: Session, player_id: int) -> list[dict[str, object]]:
    """
    Description:
        The newest live measurement of each test for a player (REQ-034).

    Parameters:
        session: the request session.
        player_id: the selected player (meas-player). Never a user id (COD-8-01).

    Return Values:
        list[dict[str, object]]: {"physical_measurement_id", "physical_test_id",
        "test_name", "unit", "measured_on", "measured_value"} per test that has one, in test
        order; empty when the profile is removed.

    Notes:
        Read only.
    """
    return fetch_all(session, LIST_LATEST_MEASUREMENTS_SQL, {"player_id": player_id})


def list_measurement_series(
    session: Session, player_id: int, test_id: int
) -> list[dict[str, object]]:
    """
    Description:
        Every live measurement of one test for a player, oldest first, for the chart
        (REQ-035).

    Parameters:
        session: the request session.
        player_id: the selected player.
        test_id: h_ref.physical_tests.physical_test_id.

    Return Values:
        list[dict[str, object]]: {"physical_measurement_id", "measured_on",
        "measured_value"} per measurement.

    Notes:
        Read only.
    """
    return fetch_all(
        session, LIST_MEASUREMENT_SERIES_SQL, {"player_id": player_id, "test_id": test_id}
    )


def list_measurements(
    session: Session, player_id: int, test_id: int, limit: int
) -> list[dict[str, object]]:
    """
    Description:
        The history of one test for a player, newest first (REQ-034).

    Parameters:
        session: the request session.
        player_id: the selected player.
        test_id: h_ref.physical_tests.physical_test_id.
        limit: the most rows to return (MEASUREMENT_HISTORY_LIMIT).

    Return Values:
        list[dict[str, object]]: {"physical_measurement_id", "measured_on",
        "measured_value", "notes"} per measurement.

    Notes:
        Read only.
    """
    return fetch_all(
        session,
        LIST_MEASUREMENTS_SQL,
        {"player_id": player_id, "test_id": test_id, "limit": limit},
    )


def get_measurement(session: Session, measurement_id: int) -> dict[str, object] | None:
    """
    Description:
        Loads one live measurement of a live profile with its test, player name and audit
        lines (REQ-004).

    Parameters:
        session: the request session.
        measurement_id: h_data.physical_measurements.physical_measurement_id from the URL
            /measurements/<id> or the meas-edit-id store.

    Return Values:
        dict[str, object] | None: {"physical_measurement_id", "player_id", "player_name",
        "physical_test_id", "test_name", "unit", "measured_on", "measured_value", "notes",
        "created_at", "created_by_name", "updated_at", "updated_by_name"}, or None when the
        measurement or its profile is not live.

    Notes:
        The measurement's player comes from this row (Context Adaptation Table).
    """
    return fetch_one(session, GET_MEASUREMENT_SQL, {"measurement_id": measurement_id})


# --- Measurement writes (REQ-034, D13, D14) ---

# Inserts only for a live profile and a live test (the caller has locked the profile).
INSERT_MEASUREMENT_SQL = """
INSERT INTO h_data.physical_measurements
    (player_id, physical_test_id, measured_on, measured_value, notes)
SELECT CAST(:player_id AS bigint), CAST(:test_id AS bigint), CAST(:measured_on AS date),
       CAST(:measured_value AS numeric), CAST(:notes AS text)
WHERE EXISTS (SELECT 1 FROM h_ref.physical_tests pt
              WHERE pt.physical_test_id = CAST(:test_id AS bigint) AND pt.deleted_at IS NULL)
  AND EXISTS (SELECT 1 FROM h_data.players p
              WHERE p.player_id = CAST(:player_id AS bigint) AND p.deleted_at IS NULL)
RETURNING physical_measurement_id
"""

UPDATE_MEASUREMENT_SQL = """
UPDATE h_data.physical_measurements pm
SET physical_test_id = CAST(:test_id AS bigint),
    measured_on = CAST(:measured_on AS date),
    measured_value = CAST(:measured_value AS numeric),
    notes = CAST(:notes AS text)
WHERE pm.physical_measurement_id = :measurement_id
  AND pm.deleted_at IS NULL
  AND EXISTS (SELECT 1 FROM h_data.players p
              WHERE p.player_id = pm.player_id AND p.deleted_at IS NULL)
  AND EXISTS (SELECT 1 FROM h_ref.physical_tests pt
              WHERE pt.physical_test_id = CAST(:test_id AS bigint) AND pt.deleted_at IS NULL)
RETURNING pm.player_id
"""

MEASUREMENT_PLAYER_SQL = """
SELECT pm.player_id
FROM h_data.physical_measurements pm
JOIN h_data.players p ON p.player_id = pm.player_id
WHERE pm.physical_measurement_id = :measurement_id
  AND pm.deleted_at IS NULL
  AND p.deleted_at IS NULL
"""

SOFT_DELETE_MEASUREMENT_SQL = """
SELECT h_data.fn_soft_delete('physical_measurement', CAST(:measurement_id AS bigint))
       AS rows_marked
"""


def insert_measurement(
    session: Session,
    player_id: int,
    test_id: int,
    measured_on: date,
    value: Decimal,
    notes: str | None,
) -> int:
    """
    Description:
        Records a physical measurement (REQ-034); earlier ones are kept.

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        player_id: the measurement player from meas-player, locked first with
            data_players.lock_live_player. Never a user id (COD-8-01).
        test_id: h_ref.physical_tests.physical_test_id.
        measured_on: the measurement's date (not after the device's today).
        value: above 0, at most 99999.99, 2 decimals.
        notes: up to 4000 characters, or None.

    Return Values:
        int: the new physical_measurement_id.

    Notes:
        Raises LookupError when the test (or the profile) is not live: nothing inserted.
    """
    row = fetch_one(
        session,
        INSERT_MEASUREMENT_SQL,
        {
            "player_id": player_id,
            "test_id": test_id,
            "measured_on": measured_on,
            "measured_value": value,
            "notes": notes,
        },
    )
    if row is None:
        raise LookupError("physical test or player is not live")
    return int(cast(int, row["physical_measurement_id"]))


def update_measurement(
    session: Session,
    measurement_id: int,
    test_id: int,
    measured_on: date,
    value: Decimal,
    notes: str | None,
) -> int | None:
    """
    Description:
        Saves the edit form of a live measurement.

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        measurement_id: physical_measurement_id from the meas-edit-id store.
        test_id: h_ref.physical_tests.physical_test_id.
        measured_on: the date (not after the device's today).
        value: above 0, at most 99999.99, 2 decimals.
        notes: up to 4000 characters, or None.

    Return Values:
        int | None: the measurement's player_id (for the goal status refresh); None when
        the measurement, its profile or the test is not live.

    Notes:
        The player never changes here.
    """
    row = fetch_one(
        session,
        UPDATE_MEASUREMENT_SQL,
        {
            "measurement_id": measurement_id,
            "test_id": test_id,
            "measured_on": measured_on,
            "measured_value": value,
            "notes": notes,
        },
    )
    return None if row is None else int(cast(int, row["player_id"]))


def delete_measurement(session: Session, measurement_id: int) -> int:
    """
    Description:
        Deletes a measurement through h_data.fn_soft_delete('physical_measurement', id);
        restorable from Recently deleted.

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        measurement_id: physical_measurement_id from the meas-edit-id store.

    Return Values:
        int: the measurement's player_id (for the goal status refresh).

    Notes:
        Raises LookupError when the measurement or its profile is not live.
    """
    row = fetch_one(session, MEASUREMENT_PLAYER_SQL, {"measurement_id": measurement_id})
    if row is None:
        raise LookupError(f"measurement {measurement_id} is not live")
    player_id = int(cast(int, row["player_id"]))
    marked = fetch_one(session, SOFT_DELETE_MEASUREMENT_SQL, {"measurement_id": measurement_id})
    if marked is None:
        logger.error("Measurement soft delete returned no row")
        raise RuntimeError("Measurement soft delete returned no row")
    return player_id
