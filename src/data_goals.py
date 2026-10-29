"""Data access for goals (REQ-032, REQ-033, REQ-041; task 2026-10-23_goals_measurements).

A goal belongs to one player profile and is one of four types (stat, drill, habit, physical);
ck_goals_type_fields requires exactly the fields of its type. Progress always comes from
h_analytics.fn_goal_progress with the device's date (D3), and automatic statuses from
h_system.fn_refresh_goal_statuses (D4). Every function takes the request session first and
runs its SQL through data_handler; writes never commit (they run inside
app_controller.run_write). Bound parameters only (COD-14-03).
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import cast

from sqlalchemy.orm import Session

from src.data_handler import fetch_all, fetch_one

logger = logging.getLogger(__name__)


# --- Values of a goal ---

@dataclass(frozen=True)
class GoalValues:
    """
    Description:
        The checked, type-specific values of a goal form (built by
        controller_goals.parse_goal_form).

    Parameters:
        goal_name: the trimmed name, 1 to 100 characters.
        goal_type: "stat", "drill", "habit" or "physical".
        target_value: the target, rounded to 3 decimals.
        target_direction: "at_least" or "at_most" (derived, D5).
        start_date: the first day progress counts.
        deadline: the last day, or None.
        notes: the notes, or None.
        stat_code: stat goals: h_ref.stat_definitions.stat_code; else None.
        window_kind: stat goals: "last_n", "season" or "date_range"; else None.
        window_n: stat goals with last_n: 1 to 200; else None.
        window_season_id: stat goals with season: h_data.seasons.season_id; else None.
        window_from: stat goals with date_range: the first day; else None.
        window_to: stat goals with date_range: the last day; else None.
        window_scope: stat goals: "game", "practice" or "both"; else None.
        drill_id: drill goals: the drill lineage h_data.drills.drill_id; else None.
        drill_metric: drill goals: "makes", "make_pct", "value_1", "value_2" or "clean_pct".
        drill_group_no: drill goals with clean_pct: the outcome group 1 to 3; else None.
        drill_basis: drill goals: "best" or "latest"; else None.
        physical_test_id: physical goals: h_ref.physical_tests id; else None.

    Return Values:
        Not applicable (data class).

    Notes:
        Fields of another goal type are None, as ck_goals_type_fields requires.
    """

    goal_name: str
    goal_type: str
    target_value: Decimal
    target_direction: str
    start_date: date
    deadline: date | None
    notes: str | None
    stat_code: str | None = None
    window_kind: str | None = None
    window_n: int | None = None
    window_season_id: int | None = None
    window_from: date | None = None
    window_to: date | None = None
    window_scope: str | None = None
    drill_id: int | None = None
    drill_metric: str | None = None
    drill_group_no: int | None = None
    drill_basis: str | None = None
    physical_test_id: int | None = None


@dataclass(frozen=True)
class GoalStatus:
    """
    Description:
        The status a goal is saved with (built by controller_goals.status_after_save, D9).

    Parameters:
        status: "active", "achieved", "missed" or "archived".
        status_is_manual: True when the user set the status by hand.
        achieved_on: the date the goal was reached, or None (required for achieved,
            ck_goals_achieved).

    Return Values:
        Not applicable (data class).

    Notes:
        None.
    """

    status: str
    status_is_manual: bool
    achieved_on: date | None


# --- Reference reads (REQ-033) ---

LIST_GOAL_STATS_SQL = """
SELECT sd.stat_code, sd.label, sd.abbreviation, sd.unit, sd.higher_is_better, sd.applies_to
FROM h_ref.stat_definitions sd
WHERE sd.deleted_at IS NULL
  AND sd.is_goal_eligible
ORDER BY sd.sort_order, sd.stat_code
"""

LIST_GOAL_DRILLS_SQL = """
SELECT cd.drill_id, cd.drill_name, cd.result_type_code
FROM h_data.v_current_drills cd
ORDER BY lower(cd.drill_name), cd.drill_id
"""

# The latest live version of a live drill lineage; a retired lineage is allowed (D11).
GET_DRILL_MEASURES_SQL = """
SELECT d.drill_id, d.drill_name, d.retired_at, dv.drill_version_id, dv.result_type_code,
       rt.capture_kind, rt.value_unit, rt.higher_is_better,
       dv.value_1_label, dv.value_1_unit, dv.value_1_higher_is_better,
       dv.value_2_label, dv.value_2_unit, dv.value_2_higher_is_better
FROM h_data.drills d
JOIN LATERAL (
    SELECT v.drill_version_id, v.result_type_code,
           v.value_1_label, v.value_1_unit, v.value_1_higher_is_better,
           v.value_2_label, v.value_2_unit, v.value_2_higher_is_better
    FROM h_data.drill_versions v
    WHERE v.drill_id = d.drill_id
      AND v.deleted_at IS NULL
    ORDER BY v.version_no DESC
    LIMIT 1
) dv ON true
JOIN h_ref.result_types rt ON rt.result_type_code = dv.result_type_code
WHERE d.drill_id = :drill_id
  AND d.deleted_at IS NULL
"""

LIST_DRILL_GROUPS_SQL = """
SELECT og.group_no, og.group_name
FROM h_data.drill_outcome_groups og
WHERE og.drill_version_id = :drill_version_id
  AND og.deleted_at IS NULL
ORDER BY og.group_no
"""

LIST_GOAL_SEASONS_SQL = """
SELECT s.season_id, s.season_name, s.start_date, s.end_date
FROM h_data.seasons s
JOIN h_data.players p ON p.player_id = s.player_id
WHERE s.player_id = :player_id
  AND s.deleted_at IS NULL
  AND p.deleted_at IS NULL
ORDER BY s.start_date DESC, s.season_id DESC
"""

SEASON_OF_PLAYER_SQL = """
SELECT EXISTS (
    SELECT 1
    FROM h_data.seasons s
    WHERE s.season_id = :season_id
      AND s.player_id = :player_id
      AND s.deleted_at IS NULL
) AS is_theirs
"""


def list_goal_stats(session: Session) -> list[dict[str, object]]:
    """
    Description:
        Lists the stats a goal may target (is_goal_eligible), in display order.

    Parameters:
        session: the request session.

    Return Values:
        list[dict[str, object]]: {"stat_code", "label", "abbreviation", "unit" ("percent",
        "ratio" or "count"), "higher_is_better", "applies_to" ("game" or "both")} per stat.

    Notes:
        Read only.
    """
    return fetch_all(session, LIST_GOAL_STATS_SQL)


def list_goal_drills(session: Session) -> list[dict[str, object]]:
    """
    Description:
        Lists the drills a new goal may target: the current drills (live, not retired),
        by name (D11).

    Parameters:
        session: the request session.

    Return Values:
        list[dict[str, object]]: {"drill_id" (the lineage id, never a version id),
        "drill_name", "result_type_code"} per drill.

    Notes:
        Read only. From h_data.v_current_drills.
    """
    return fetch_all(session, LIST_GOAL_DRILLS_SQL)


def get_drill_goal_measures(session: Session, drill_id: int) -> dict[str, object] | None:
    """
    Description:
        What a drill goal can measure on a drill: its latest live version's result type and
        numbers, and the live outcome groups of that version.

    Parameters:
        session: the request session.
        drill_id: h_data.drills.drill_id (the lineage). A retired lineage is allowed, so an
            existing goal keeps its drill (D11).

    Return Values:
        dict[str, object] | None: {"drill_id", "drill_name", "retired_at",
        "drill_version_id", "result_type_code", "capture_kind", "value_unit",
        "higher_is_better", "value_1_label", "value_1_unit", "value_1_higher_is_better",
        "value_2_label", "value_2_unit", "value_2_higher_is_better", "groups":
        [{"group_no", "group_name"}]}; None when the drill is deleted or has no live
        version.

    Notes:
        Read only; two statements (the groups only when the drill exists).
    """
    row = fetch_one(session, GET_DRILL_MEASURES_SQL, {"drill_id": drill_id})
    if row is None:
        return None
    groups = fetch_all(
        session, LIST_DRILL_GROUPS_SQL, {"drill_version_id": row["drill_version_id"]}
    )
    return {**row, "groups": groups}


def list_goal_seasons(session: Session, player_id: int) -> list[dict[str, object]]:
    """
    Description:
        Lists a player's live seasons, newest first, for the season window of a stat goal.

    Parameters:
        session: the request session.
        player_id: the goal's player (never a user id, COD-8-01).

    Return Values:
        list[dict[str, object]]: {"season_id", "season_name", "start_date", "end_date"};
        empty when the profile is removed.

    Notes:
        Read only.
    """
    return fetch_all(session, LIST_GOAL_SEASONS_SQL, {"player_id": player_id})


def season_of_player(session: Session, season_id: int, player_id: int) -> bool:
    """
    Description:
        Whether a season is a live season of the given player.

    Parameters:
        session: the request session.
        season_id: h_data.seasons.season_id.
        player_id: the goal's player.

    Return Values:
        bool: True when the season is live and belongs to the player.

    Notes:
        Read only; the goal write statements re-check it (D14).
    """
    row = fetch_one(
        session, SEASON_OF_PLAYER_SQL, {"season_id": season_id, "player_id": player_id}
    )
    return bool(row and row["is_theirs"])


# --- Status refresh (REQ-032, D4) ---

REFRESH_GOAL_STATUSES_SQL = """
SELECT count(*) AS changed
FROM h_system.fn_refresh_goal_statuses(CAST(:today AS date), CAST(:player_id AS bigint)) rg
"""


def refresh_goal_statuses(session: Session, today: date, player_id: int | None) -> int:
    """
    Description:
        Applies the automatic goal statuses (REQ-032): active, non-manual goals that are met
        become achieved (achieved_on = today); those past their deadline become missed.

    Parameters:
        session: the write session (inside run_write; the acting user is the user for a
            save, SYSTEM_USER_ID for the refresh before a page is built).
        today: the device's date (REQ-055), never the server's.
        player_id: the written row's player after a save; None for every live player.

    Return Values:
        int: how many goals changed status.

    Notes:
        Runs h_system.fn_refresh_goal_statuses. Achieved goals never revert.
    """
    row = fetch_one(
        session, REFRESH_GOAL_STATUSES_SQL, {"today": today, "player_id": player_id}
    )
    return int(cast(int, row["changed"])) if row is not None else 0


# --- Goal reads (REQ-032, REQ-033, REQ-041) ---

# The display columns of a goal, shared by the list and the single-goal read: the raw row,
# the stat's label, the drill's name with its latest live version's numbers and the
# outcome group's name, the test's name and unit, the season's name, and the progress
# columns of fn_goal_progress (gp).
GOAL_COLUMNS_SQL = """
SELECT g.goal_id, g.player_id, g.goal_name, g.goal_type, g.target_value, g.target_direction,
       g.start_date, g.deadline, g.status, g.status_is_manual, g.achieved_on,
       g.stat_code, g.window_kind, g.window_n, g.window_season_id, g.window_from,
       g.window_to, g.window_scope, g.drill_id, g.drill_metric, g.drill_group_no,
       g.drill_basis, g.physical_test_id, g.notes,
       sd.label AS stat_label, sd.abbreviation AS stat_abbreviation, sd.unit AS stat_unit,
       d.drill_name, dv.result_type_code AS drill_result_type, dv.value_unit AS drill_value_unit,
       dv.value_1_label, dv.value_1_unit, dv.value_2_label, dv.value_2_unit,
       og.group_name,
       pt.test_name, pt.unit AS test_unit,
       s.season_name,
       gp.progress_value, gp.is_met, gp.progress_ratio, gp.sample_events, gp.detail
"""

GOAL_JOINS_SQL = """
JOIN h_data.players p ON p.player_id = g.player_id
LEFT JOIN h_ref.stat_definitions sd ON sd.stat_code = g.stat_code
LEFT JOIN h_data.drills d ON d.drill_id = g.drill_id
LEFT JOIN LATERAL (
    SELECT v.drill_version_id, v.result_type_code, rt.value_unit,
           v.value_1_label, v.value_1_unit, v.value_2_label, v.value_2_unit
    FROM h_data.drill_versions v
    JOIN h_ref.result_types rt ON rt.result_type_code = v.result_type_code
    WHERE v.drill_id = g.drill_id
      AND v.deleted_at IS NULL
    ORDER BY v.version_no DESC
    LIMIT 1
) dv ON true
LEFT JOIN h_data.drill_outcome_groups og
       ON og.drill_version_id = dv.drill_version_id
      AND og.group_no = g.drill_group_no
      AND og.deleted_at IS NULL
LEFT JOIN h_ref.physical_tests pt ON pt.physical_test_id = g.physical_test_id
LEFT JOIN h_data.seasons s ON s.season_id = g.window_season_id
"""

# Order: active, achieved, missed, archived; then deadline (none last), name, id.
LIST_GOALS_SQL = (
    GOAL_COLUMNS_SQL
    + """
FROM h_data.goals g
LEFT JOIN h_analytics.fn_goal_progress(CAST(:today AS date), CAST(:player_id AS bigint)) gp
       ON gp.goal_id = g.goal_id
"""
    + GOAL_JOINS_SQL
    + """
WHERE g.player_id = :player_id
  AND g.deleted_at IS NULL
  AND p.deleted_at IS NULL
  AND (CAST(:statuses AS text[]) IS NULL OR g.status = ANY (CAST(:statuses AS text[])))
ORDER BY CASE g.status WHEN 'active' THEN 1 WHEN 'achieved' THEN 2 WHEN 'missed' THEN 3 ELSE 4 END,
         g.deadline NULLS LAST, lower(g.goal_name), g.goal_id
LIMIT CAST(:limit AS integer)
"""
)

GET_GOAL_SQL = (
    GOAL_COLUMNS_SQL
    + """,
       p.display_name AS player_name,
       g.created_at, cu.display_name AS created_by_name,
       g.updated_at, uu.display_name AS updated_by_name
FROM h_data.goals g
LEFT JOIN LATERAL h_analytics.fn_goal_progress(CAST(:today AS date), g.player_id, g.goal_id) gp
       ON true
"""
    + GOAL_JOINS_SQL
    + """
JOIN h_system.users cu ON cu.user_id = g.created_by
JOIN h_system.users uu ON uu.user_id = g.updated_by
WHERE g.goal_id = :goal_id
  AND g.deleted_at IS NULL
  AND p.deleted_at IS NULL
"""
)

LOCK_GOAL_SQL = """
SELECT g.goal_id, g.player_id, g.goal_name, g.goal_type, g.target_value, g.target_direction,
       g.start_date, g.deadline, g.status, g.status_is_manual, g.achieved_on,
       g.stat_code, g.window_kind, g.window_n, g.window_season_id, g.window_from,
       g.window_to, g.window_scope, g.drill_id, g.drill_metric, g.drill_group_no,
       g.drill_basis, g.physical_test_id, g.notes
FROM h_data.goals g
JOIN h_data.players p ON p.player_id = g.player_id
WHERE g.goal_id = :goal_id
  AND g.deleted_at IS NULL
  AND p.deleted_at IS NULL
FOR UPDATE OF g
"""


def list_goals_with_progress(
    session: Session,
    player_id: int,
    today: date,
    statuses: tuple[str, ...] | None,
    limit: int | None,
) -> list[dict[str, object]]:
    """
    Description:
        Lists a player's live goals with their progress and display labels, in one query
        (no N+1): fn_goal_progress for the player, joined on goal_id.

    Parameters:
        session: the request session.
        player_id: the selected player (current_player_id()). Never a user id (COD-8-01).
        today: the device's date (REQ-055).
        statuses: the statuses to list, e.g. ("active",); None lists every status.
        limit: the most goals to return, or None for all.

    Return Values:
        list[dict[str, object]]: per goal the raw goal columns, "stat_label",
        "stat_abbreviation", "stat_unit", "drill_name", "drill_result_type",
        "drill_value_unit", "value_1_label", "value_1_unit", "value_2_label",
        "value_2_unit", "group_name", "test_name", "test_unit", "season_name" (None when
        not of the goal's type), and "progress_value", "is_met", "progress_ratio" (0..1),
        "sample_events", "detail". Ordered active, achieved, missed, archived; then
        deadline (none last), name, id. Empty when the profile is removed.

    Notes:
        Read only. The drill columns come from the drill's latest live version (LATERAL).
    """
    return fetch_all(
        session,
        LIST_GOALS_SQL,
        {
            "player_id": player_id,
            "today": today,
            "statuses": list(statuses) if statuses is not None else None,
            "limit": limit,
        },
    )


def get_goal(session: Session, goal_id: int, today: date) -> dict[str, object] | None:
    """
    Description:
        Loads one live goal of a live profile with its progress, display labels, player name
        and audit lines (REQ-004).

    Parameters:
        session: the request session.
        goal_id: h_data.goals.goal_id from the URL /goals/<id> or the goal-edit-id store.
        today: the device's date (REQ-055).

    Return Values:
        dict[str, object] | None: the list_goals_with_progress columns plus "player_name",
        "created_at", "created_by_name", "updated_at", "updated_by_name"; None when the goal
        or its profile is not live.

    Notes:
        The goal's player comes from this row, never from the selected player (Context
        Adaptation Table). Progress is fn_goal_progress(today, player, goal_id).
    """
    return fetch_one(session, GET_GOAL_SQL, {"goal_id": goal_id, "today": today})


def lock_goal(session: Session, goal_id: int) -> dict[str, object] | None:
    """
    Description:
        Reads and locks (FOR UPDATE) the raw row of a live goal of a live profile.

    Parameters:
        session: the write session (inside run_write).
        goal_id: h_data.goals.goal_id from the goal-edit-id store.

    Return Values:
        dict[str, object] | None: the goal's columns (no labels, no audit fields), or None
        when the goal or its profile is not live.

    Notes:
        Used by Save, so the form is checked against the row as it is in this transaction.
    """
    return fetch_one(session, LOCK_GOAL_SQL, {"goal_id": goal_id})


# --- Goal writes (REQ-032, REQ-033, D14) ---

# The reference check of a goal write, by type: an eligible live stat (and, for a season
# window, a live season of the goal's player); a current drill, or the goal's own drill kept
# unchanged (which may be retired), and for clean_pct a live outcome group of its latest
# version; a live physical test. {player} and {kept_drill} are filled below with fixed SQL
# text only, never with external values (COD-14-03).
REFERENCE_CHECK_TEMPLATE = """
  AND EXISTS (SELECT 1 FROM h_data.players lp
              WHERE lp.player_id = {player} AND lp.deleted_at IS NULL)
  AND CASE CAST(:goal_type AS text)
          WHEN 'stat' THEN
              EXISTS (SELECT 1 FROM h_ref.stat_definitions sd
                      WHERE sd.stat_code = CAST(:stat_code AS text)
                        AND sd.deleted_at IS NULL
                        AND sd.is_goal_eligible)
              AND (CAST(:window_season_id AS bigint) IS NULL
                   OR EXISTS (SELECT 1 FROM h_data.seasons ws
                              WHERE ws.season_id = CAST(:window_season_id AS bigint)
                                AND ws.player_id = {player}
                                AND ws.deleted_at IS NULL))
          WHEN 'drill' THEN
              (EXISTS (SELECT 1 FROM h_data.v_current_drills cd
                       WHERE cd.drill_id = CAST(:drill_id AS bigint))
               OR {kept_drill})
              AND (CAST(:drill_metric AS text) <> 'clean_pct'
                   OR EXISTS (SELECT 1 FROM h_data.drill_outcome_groups og
                              WHERE og.deleted_at IS NULL
                                AND og.group_no = CAST(:drill_group_no AS smallint)
                                AND og.drill_version_id = (
                                    SELECT v.drill_version_id
                                    FROM h_data.drill_versions v
                                    WHERE v.drill_id = CAST(:drill_id AS bigint)
                                      AND v.deleted_at IS NULL
                                    ORDER BY v.version_no DESC
                                    LIMIT 1)))
          WHEN 'physical' THEN
              EXISTS (SELECT 1 FROM h_ref.physical_tests pt
                      WHERE pt.physical_test_id = CAST(:physical_test_id AS bigint)
                        AND pt.deleted_at IS NULL)
          WHEN 'habit' THEN true
          ELSE false
      END
"""

# A new goal has no drill of its own yet.
INSERT_REFERENCE_CHECK = REFERENCE_CHECK_TEMPLATE.format(
    player="CAST(:player_id AS bigint)", kept_drill="false"
)
# An edited goal: its own player, and its own drill if unchanged (still a live lineage).
UPDATE_REFERENCE_CHECK = REFERENCE_CHECK_TEMPLATE.format(
    player="g.player_id",
    kept_drill=(
        "(g.drill_id = CAST(:drill_id AS bigint) AND EXISTS (SELECT 1 FROM h_data.drills kd"
        " WHERE kd.drill_id = g.drill_id AND kd.deleted_at IS NULL))"
    ),
)

INSERT_GOAL_SQL = (
    """
INSERT INTO h_data.goals (
    player_id, goal_name, goal_type, target_value, target_direction, start_date, deadline,
    status, status_is_manual, achieved_on, stat_code, window_kind, window_n,
    window_season_id, window_from, window_to, window_scope, drill_id, drill_metric,
    drill_group_no, drill_basis, physical_test_id, notes)
SELECT CAST(:player_id AS bigint), CAST(:goal_name AS text), CAST(:goal_type AS text),
       CAST(:target_value AS numeric), CAST(:target_direction AS text),
       CAST(:start_date AS date), CAST(:deadline AS date),
       CAST(:status AS text), CAST(:status_is_manual AS boolean), CAST(:achieved_on AS date),
       CAST(:stat_code AS text), CAST(:window_kind AS text), CAST(:window_n AS smallint),
       CAST(:window_season_id AS bigint), CAST(:window_from AS date), CAST(:window_to AS date),
       CAST(:window_scope AS text), CAST(:drill_id AS bigint), CAST(:drill_metric AS text),
       CAST(:drill_group_no AS smallint), CAST(:drill_basis AS text),
       CAST(:physical_test_id AS bigint), CAST(:notes AS text)
WHERE true
"""
    + INSERT_REFERENCE_CHECK
    + """
RETURNING goal_id
"""
)

# The type never changes (D7), so the WHERE also requires the stored type.
UPDATE_GOAL_SQL = (
    """
UPDATE h_data.goals g
SET goal_name = CAST(:goal_name AS text),
    target_value = CAST(:target_value AS numeric),
    target_direction = CAST(:target_direction AS text),
    start_date = CAST(:start_date AS date),
    deadline = CAST(:deadline AS date),
    status = CAST(:status AS text),
    status_is_manual = CAST(:status_is_manual AS boolean),
    achieved_on = CAST(:achieved_on AS date),
    stat_code = CAST(:stat_code AS text),
    window_kind = CAST(:window_kind AS text),
    window_n = CAST(:window_n AS smallint),
    window_season_id = CAST(:window_season_id AS bigint),
    window_from = CAST(:window_from AS date),
    window_to = CAST(:window_to AS date),
    window_scope = CAST(:window_scope AS text),
    drill_id = CAST(:drill_id AS bigint),
    drill_metric = CAST(:drill_metric AS text),
    drill_group_no = CAST(:drill_group_no AS smallint),
    drill_basis = CAST(:drill_basis AS text),
    physical_test_id = CAST(:physical_test_id AS bigint),
    notes = CAST(:notes AS text)
WHERE g.goal_id = :goal_id
  AND g.deleted_at IS NULL
  AND g.goal_type = CAST(:goal_type AS text)
"""
    + UPDATE_REFERENCE_CHECK
    + """
RETURNING g.goal_id
"""
)

SOFT_DELETE_GOAL_SQL = """
SELECT h_data.fn_soft_delete('goal', CAST(:goal_id AS bigint)) AS rows_marked
"""


def _goal_params(values: GoalValues, status: GoalStatus) -> dict[str, object]:
    """
    Description:
        The bound parameters of a goal write.

    Parameters:
        values: the checked goal values.
        status: the status to save.

    Return Values:
        dict[str, object]: one entry per column parameter of INSERT_GOAL_SQL and
        UPDATE_GOAL_SQL (without player_id and goal_id).

    Notes:
        Pure.
    """
    return {
        "goal_name": values.goal_name,
        "goal_type": values.goal_type,
        "target_value": values.target_value,
        "target_direction": values.target_direction,
        "start_date": values.start_date,
        "deadline": values.deadline,
        "status": status.status,
        "status_is_manual": status.status_is_manual,
        "achieved_on": status.achieved_on,
        "stat_code": values.stat_code,
        "window_kind": values.window_kind,
        "window_n": values.window_n,
        "window_season_id": values.window_season_id,
        "window_from": values.window_from,
        "window_to": values.window_to,
        "window_scope": values.window_scope,
        "drill_id": values.drill_id,
        "drill_metric": values.drill_metric,
        "drill_group_no": values.drill_group_no,
        "drill_basis": values.drill_basis,
        "physical_test_id": values.physical_test_id,
        "notes": values.notes,
    }


def insert_goal(
    session: Session, player_id: int, values: GoalValues, status: GoalStatus
) -> int:
    """
    Description:
        Adds a goal to a player (REQ-032, REQ-033). The statement inserts nothing unless the
        profile is live and the type's reference is valid: an eligible stat (and a live
        season of this player for a season window), a current drill (with a live outcome
        group for clean_pct), or a live physical test.

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        player_id: the goal player from the goal-new-player-id store, locked first with
            data_players.lock_live_player. Never a user id (COD-8-01).
        values: the checked goal values (parse_goal_form).
        status: the status to save (status_after_save).

    Return Values:
        int: the new h_data.goals.goal_id.

    Notes:
        Raises LookupError when nothing was inserted (a reference is not valid), so the
        transaction rolls back. The table's CHECKs are the backstop for the field rules.
    """
    row = fetch_one(
        session, INSERT_GOAL_SQL, {"player_id": player_id, **_goal_params(values, status)}
    )
    if row is None:
        raise LookupError("goal reference is not live or not the player's")
    return int(cast(int, row["goal_id"]))


def update_goal(
    session: Session, goal_id: int, values: GoalValues, status: GoalStatus
) -> bool:
    """
    Description:
        Saves the edit form of a live goal (every field except its type and player, D7),
        with the same reference checks as insert_goal, except that a drill left unchanged
        may be retired (D11).

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        goal_id: h_data.goals.goal_id from the goal-edit-id store.
        values: the checked goal values; values.goal_type must be the stored type.
        status: the status to save (status_after_save).

    Return Values:
        bool: True when updated; False when the goal or its profile is not live, the type
        differs, or a reference is not valid.

    Notes:
        The season check uses the goal's own player (g.player_id).
    """
    row = fetch_one(
        session, UPDATE_GOAL_SQL, {"goal_id": goal_id, **_goal_params(values, status)}
    )
    return row is not None


def delete_goal(session: Session, goal_id: int) -> None:
    """
    Description:
        Deletes a goal through h_data.fn_soft_delete('goal', id); it can be restored from
        Settings > Recently deleted (REQ-006).

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        goal_id: h_data.goals.goal_id from the goal-edit-id store.

    Return Values:
        None.

    Notes:
        fn_soft_delete raises when the goal does not exist or is already deleted. The
        confirmation dialog is the caller's (REQ-005).
    """
    row = fetch_one(session, SOFT_DELETE_GOAL_SQL, {"goal_id": goal_id})
    if row is None:
        logger.error("Goal soft delete returned no row")
        raise RuntimeError("Goal soft delete returned no row")
