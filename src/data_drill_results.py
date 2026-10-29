"""Data access for practice run mode (practice_drills 2b): the drill being run, its spot plan and
outcome structure, value attempts, outcome reps, result summaries and personal bests.

REQ-030, REQ-056, REQ-060, REQ-076 to REQ-079, REQ-082. Every function takes the request
session first and runs its SQL through data_handler. Write functions never commit: they run
inside app_controller.run_write / run_write_with_reason, after the acting user has been set
(COD-6-01, COD-7-01).

Every write starts with data_entry.lock_drill_owner (the drill-mode lock_entry_owner): it locks
the practice row of the live practice drill and resolves the entry player's live participant
row; the practice_player_id is never taken from the client (Context Adaptation Table). Results
are found or created with data_entry.get_or_create_result, the helper shared with drill-mode
shot entry. Shooting results are entered through data_entry (entry parent kind "drill").
"""

import logging
from datetime import datetime
from typing import cast

from sqlalchemy.orm import Session

from src.data_entry import (
    delete_empty_attempt,
    drill_side,
    get_or_create_result,
    insert_drill_attempt,
    lock_drill_owner,
)
from src.data_handler import execute, fetch_all, fetch_one

logger = logging.getLogger(__name__)

CAPTURE_VALUES = "values"
CAPTURE_OUTCOMES = "outcomes"
CAPTURE_SHOTS = "shots"

SOFT_DELETE_ATTEMPT_SQL = """
SELECT h_data.fn_soft_delete('drill_attempt', CAST(:drill_attempt_id AS bigint)) AS rows_marked
"""


# --- The drill being run (REQ-056, REQ-076, REQ-079, REQ-082) ---

GET_RUN_DRILL_SQL = """
SELECT pd.practice_drill_id,
       pd.practice_session_id,
       pd.practice_drill_set_id,
       pd.source,
       pd.set_position,
       pd.drill_version_id,
       dv.drill_id,
       dv.version_no,
       dv.result_type_code,
       dv.symmetry_type_code,
       dv.default_target,
       dv.fixed_seconds,
       dv.value_1_label,
       dv.value_1_unit,
       dv.value_1_higher_is_better,
       dv.value_2_label,
       dv.value_2_unit,
       dv.value_2_higher_is_better,
       d.drill_name,
       d.description,
       dl.label AS difficulty_label,
       dt.type_name,
       rt.label AS result_type_label,
       rt.capture_kind,
       rt.value_unit,
       rt.higher_is_better,
       rt.needs_fixed_seconds,
       sy.label AS symmetry_label,
       sy.left_label,
       sy.right_label,
       s.set_name,
       sv.version_no AS set_version_no,
       sv.set_kind,
       pds.draw_notice,
       EXISTS (SELECT 1
               FROM h_data.drill_spots sp
               WHERE sp.drill_version_id = dv.drill_version_id
                 AND sp.is_free_throw
                 AND sp.deleted_at IS NULL) AS has_ft_spot
FROM h_data.practice_drills pd
JOIN h_data.practice_sessions ps ON ps.practice_session_id = pd.practice_session_id
LEFT JOIN h_data.practice_drill_sets pds ON pds.practice_drill_set_id = pd.practice_drill_set_id
LEFT JOIN h_data.drill_set_versions sv ON sv.drill_set_version_id = pds.drill_set_version_id
LEFT JOIN h_data.drill_sets s ON s.drill_set_id = sv.drill_set_id
JOIN h_data.drill_versions dv ON dv.drill_version_id = pd.drill_version_id
JOIN h_data.drills d ON d.drill_id = dv.drill_id
JOIN h_ref.difficulty_levels dl ON dl.difficulty_code = d.difficulty_code
JOIN h_data.drill_types dt ON dt.drill_type_id = d.primary_drill_type_id
JOIN h_ref.result_types rt ON rt.result_type_code = dv.result_type_code
LEFT JOIN h_ref.symmetry_types sy ON sy.symmetry_type_code = dv.symmetry_type_code
WHERE pd.practice_drill_id = :practice_drill_id
  AND pd.practice_session_id = :practice_session_id
  AND pd.deleted_at IS NULL
  AND ps.deleted_at IS NULL
  AND (pd.practice_drill_set_id IS NULL OR pds.deleted_at IS NULL)
"""

LIST_OUTCOME_STRUCTURE_SQL = """
SELECT g.outcome_group_id, g.group_no, g.group_name,
       o.drill_outcome_id, o.outcome_no, o.outcome_label, o.is_success
FROM h_data.drill_outcome_groups g
JOIN h_data.drill_outcomes o
  ON o.outcome_group_id = g.outcome_group_id AND o.deleted_at IS NULL
WHERE g.drill_version_id = :drill_version_id
  AND g.deleted_at IS NULL
ORDER BY g.group_no, o.outcome_no
"""

LIST_DRILL_SPOT_PLAN_SQL = """
SELECT t.spot_order, t.court_spot_id, t.spot_number, t.is_free_throw, t.attempts_target,
       cs.spot_name
FROM h_data.fn_drill_spot_targets(CAST(:drill_version_id AS bigint)) t
LEFT JOIN h_ref.court_spots cs ON cs.court_spot_id = t.court_spot_id
ORDER BY t.spot_order
"""

SHOOTING_HISTORY_SQL = """
SELECT a.player_id, a.attempt_no, a.side, a.makes, a.attempts
FROM h_analytics.v_drill_attempts a
WHERE a.practice_session_id = :practice_session_id
  AND a.practice_drill_id = :practice_drill_id
ORDER BY a.player_id, a.attempt_no
"""


def get_run_drill(
    session: Session, practice_session_id: int, practice_drill_id: int
) -> dict[str, object] | None:
    """
    Description:
        Loads a live practice drill of a live practice for the run page: its version,
        result type (with capture_kind), symmetry labels, fixed seconds, value labels and
        units, set info and whether the version has a free-throw-line spot.

    Parameters:
        session: the request session.
        practice_session_id: the practice from the URL.
        practice_drill_id: the practice drill from the URL (/run/<pd>), or the first drill
            in run order. Never a drill_id (COD-8-01).

    Return Values:
        dict[str, object] | None: the GET_RUN_DRILL_SQL columns; None when the drill is not
        live in this practice (or its placed set is removed).

    Notes:
        Names are read from the lineage in any state, so a retired drill or set still shows
        in the practices that used it (D10).
    """
    return fetch_one(
        session,
        GET_RUN_DRILL_SQL,
        {"practice_session_id": practice_session_id, "practice_drill_id": practice_drill_id},
    )


def list_outcome_structure(session: Session, drill_version_id: int) -> list[dict[str, object]]:
    """
    Description:
        The outcome groups and buttons of an outcome-button drill version (REQ-077).

    Parameters:
        session: the request session.
        drill_version_id: the practice drill's version (from get_run_drill), never a drill
            lineage id (COD-8-01).

    Return Values:
        list[dict[str, object]]: {"outcome_group_id", "group_no", "group_name", "outcomes":
        [{"drill_outcome_id", "outcome_no", "outcome_label", "is_success"}]} per group, in
        group order.

    Notes:
        One read; the rows are folded in Python.
    """
    rows = fetch_all(session, LIST_OUTCOME_STRUCTURE_SQL, {"drill_version_id": drill_version_id})
    groups: list[dict[str, object]] = []
    by_id: dict[int, list[dict[str, object]]] = {}
    for row in rows:
        group_id = cast(int, row["outcome_group_id"])
        if group_id not in by_id:
            by_id[group_id] = []
            groups.append(
                {
                    "outcome_group_id": group_id,
                    "group_no": row["group_no"],
                    "group_name": row["group_name"],
                    "outcomes": by_id[group_id],
                }
            )
        by_id[group_id].append(
            {
                "drill_outcome_id": row["drill_outcome_id"],
                "outcome_no": row["outcome_no"],
                "outcome_label": row["outcome_label"],
                "is_success": row["is_success"],
            }
        )
    return groups


def list_drill_spot_plan(session: Session, drill_version_id: int) -> list[dict[str, object]]:
    """
    Description:
        The spots of a shooting drill version in order with their attempts targets
        (h_data.fn_drill_spot_targets), for the court plan and auto-advance (REQ-031).

    Parameters:
        session: the request session.
        drill_version_id: the practice drill's version (from get_run_drill).

    Return Values:
        list[dict[str, object]]: {"spot_order", "court_spot_id" (None for the free-throw
        line), "spot_number", "is_free_throw", "attempts_target" (None = no target),
        "spot_name"} per spot.

    Notes:
        None.
    """
    return fetch_all(session, LIST_DRILL_SPOT_PLAN_SQL, {"drill_version_id": drill_version_id})


def get_shooting_history(
    session: Session, practice_session_id: int, practice_drill_id: int
) -> dict[int, list[dict[str, object]]]:
    """
    Description:
        The live attempts of every participant for a shooting practice drill, for the
        "Earlier attempts" line beside the personal best (REQ-078).

    Parameters:
        session: the request session.
        practice_session_id: the practice.
        practice_drill_id: the practice drill.

    Return Values:
        dict[int, list[dict[str, object]]]: player_id -> [{"player_id", "attempt_no",
        "side", "makes", "attempts"}] in attempt order.

    Notes:
        Read on page open only (D16). Formatting is layouts_run.format_shooting_history.
    """
    rows = fetch_all(
        session,
        SHOOTING_HISTORY_SQL,
        {"practice_session_id": practice_session_id, "practice_drill_id": practice_drill_id},
    )
    history: dict[int, list[dict[str, object]]] = {}
    for row in rows:
        history.setdefault(int(cast(int, row["player_id"])), []).append(row)
    return history


def _lock_run_owner(
    session: Session,
    practice_session_id: int,
    practice_drill_id: int,
    player_id: int,
    capture_kind: str,
) -> dict[str, object]:
    """
    Description:
        lock_drill_owner, plus the checks that the practice drill belongs to this practice
        and records this capture kind.

    Parameters:
        session: the write session.
        practice_session_id: the practice from the drill-context store.
        practice_drill_id: the practice drill from the drill-context store.
        player_id: the entry player (drill-player.value or outcome-submit.player_id).
        capture_kind: "values" or "outcomes".

    Return Values:
        dict[str, object]: the lock_drill_owner row.

    Notes:
        Raises ValueError when the owner is not found, the drill is in another practice, or
        its result type captures something else.
    """
    owner = lock_drill_owner(session, practice_drill_id, player_id)
    if owner["practice_session_id"] != practice_session_id:
        raise ValueError("practice drill is not in this practice")
    if owner["capture_kind"] != capture_kind:
        raise ValueError(f"drill does not capture {capture_kind}")
    return owner


# --- Value attempts (REQ-030, REQ-076, REQ-078, REQ-079; D9) ---

LIST_VALUE_ATTEMPTS_SQL = """
SELECT a.drill_attempt_id, a.attempt_no, a.side, a.value_1, a.value_2, a.rate_per_minute
FROM h_analytics.v_drill_attempts a
WHERE a.practice_session_id = :practice_session_id
  AND a.practice_drill_id = :practice_drill_id
  AND a.player_id = :player_id
ORDER BY a.attempt_no
"""

# A live attempt of the participant's live result for this practice drill, locked.
LOCK_OWN_ATTEMPT_SQL = """
SELECT a.drill_attempt_id
FROM h_data.drill_attempts a
JOIN h_data.drill_results r ON r.drill_result_id = a.drill_result_id
WHERE a.drill_attempt_id = :drill_attempt_id
  AND a.practice_player_id = :practice_player_id
  AND a.deleted_at IS NULL
  AND r.practice_drill_id = :practice_drill_id
  AND r.practice_player_id = :practice_player_id
  AND r.deleted_at IS NULL
FOR UPDATE OF a
"""


def list_value_attempts(
    session: Session, player_id: int, practice_session_id: int, practice_drill_id: int
) -> list[dict[str, object]]:
    """
    Description:
        The entry player's live attempts of a values drill in this practice, in order.

    Parameters:
        session: the request session.
        player_id: the entry player (drill-player.value). Never a user id (COD-8-01).
        practice_session_id: the practice.
        practice_drill_id: the practice drill.

    Return Values:
        list[dict[str, object]]: {"drill_attempt_id", "attempt_no", "side", "value_1",
        "value_2", "rate_per_minute"} per attempt.

    Notes:
        Read through h_analytics.v_drill_attempts, which hides deleted rows and rows of
        deleted parents (COD-22-03).
    """
    return fetch_all(
        session,
        LIST_VALUE_ATTEMPTS_SQL,
        {
            "practice_session_id": practice_session_id,
            "practice_drill_id": practice_drill_id,
            "player_id": player_id,
        },
    )


def add_value_attempt(
    session: Session,
    player_id: int,
    practice_session_id: int,
    practice_drill_id: int,
    side: str | None,
    value_1: float,
    value_2: float | None,
) -> int:
    """
    Description:
        Saves one attempt of a values drill (time, count, streak, reaction or physical
        measurement) for the entry player (REQ-030, REQ-076, REQ-078).

    Parameters:
        session: the write session (inside run_write_with_reason).
        player_id: the entry player (drill-player.value).
        practice_session_id: the practice from the drill-context store.
        practice_drill_id: the practice drill from the drill-context store.
        side: "left" or "right" for a drill with a symmetry type, else ignored (REQ-079).
        value_1: the first value, at least 0.
        value_2: the physical second value, or None.

    Return Values:
        int: the new drill_attempt_id.

    Notes:
        Locks the owner first and gets or creates the live result. The attempt trigger
        refuses a second value the version does not name (its message reaches the user).
    """
    owner = _lock_run_owner(
        session, practice_session_id, practice_drill_id, player_id, CAPTURE_VALUES
    )
    result_id = get_or_create_result(
        session,
        practice_session_id,
        practice_drill_id,
        cast(int, owner["practice_player_id"]),
        player_id,
    )
    created = insert_drill_attempt(
        session,
        result_id,
        owner,
        player_id,
        {
            "side": drill_side(owner, side),
            "value_1": value_1,
            "value_2": value_2,
            "attempted_at": None,
        },
    )
    return cast(int, created["drill_attempt_id"])


def delete_value_attempt(
    session: Session,
    player_id: int,
    practice_session_id: int,
    practice_drill_id: int,
    drill_attempt_id: int,
) -> bool:
    """
    Description:
        Deletes one value attempt of the entry player through h_data.fn_soft_delete, so it
        can be restored for 30 days (REQ-005, REQ-006, D9).

    Parameters:
        session: the write session (inside run_write_with_reason).
        player_id: the entry player (drill-player.value).
        practice_session_id: the practice from the drill-context store.
        practice_drill_id: the practice drill from the drill-context store.
        drill_attempt_id: the attempt from the drill-delete-target store (a pattern id).

    Return Values:
        bool: True when deleted; False when the attempt is not a live attempt of this
        player's result for this practice drill (nothing changed).

    Notes:
        Locks the owner, then the attempt, before fn_soft_delete (which checks no owner).
    """
    owner = _lock_run_owner(
        session, practice_session_id, practice_drill_id, player_id, CAPTURE_VALUES
    )
    locked = fetch_one(
        session,
        LOCK_OWN_ATTEMPT_SQL,
        {
            "drill_attempt_id": drill_attempt_id,
            "practice_player_id": owner["practice_player_id"],
            "practice_drill_id": practice_drill_id,
        },
    )
    if locked is None:
        return False
    fetch_one(session, SOFT_DELETE_ATTEMPT_SQL, {"drill_attempt_id": drill_attempt_id})
    return True


# --- Outcome reps (REQ-077, REQ-010) ---

LIST_GROUP_OUTCOME_IDS_SQL = """
SELECT g.outcome_group_id, o.drill_outcome_id
FROM h_data.drill_outcome_groups g
JOIN h_data.drill_outcomes o
  ON o.outcome_group_id = g.outcome_group_id AND o.deleted_at IS NULL
WHERE g.drill_version_id = :drill_version_id
  AND g.deleted_at IS NULL
"""

# The idempotency check of a rep: a live attempt of this participant's live result with the
# same tap instant.
FIND_REP_SQL = """
SELECT a.drill_attempt_id
FROM h_data.drill_attempts a
JOIN h_data.drill_results r ON r.drill_result_id = a.drill_result_id
WHERE r.practice_drill_id = :practice_drill_id
  AND r.practice_player_id = :practice_player_id
  AND r.deleted_at IS NULL
  AND a.deleted_at IS NULL
  AND a.attempted_at = CAST(:attempted_at AS timestamptz)
"""

INSERT_TAPS_SQL = """
INSERT INTO h_data.drill_attempt_outcomes (drill_attempt_id, outcome_group_id, drill_outcome_id)
SELECT CAST(:drill_attempt_id AS bigint), u.group_id, u.outcome_id
FROM unnest(CAST(:group_ids AS bigint[]), CAST(:outcome_ids AS bigint[])) AS u(group_id, outcome_id)
"""

# Undo is a hard delete of this visit's rep (D9): its taps first, then the attempt.
DELETE_TAPS_SQL = """
DELETE FROM h_data.drill_attempt_outcomes
WHERE drill_attempt_id = :drill_attempt_id
"""


def _check_picks(
    session: Session, drill_version_id: int, picks: dict[int, int]
) -> None:
    """
    Description:
        Checks a rep has exactly one pick per outcome group of the version, each a button of
        its group (REQ-077).

    Parameters:
        session: the write session.
        drill_version_id: the practice drill's version (from lock_drill_owner).
        picks: outcome_group_id -> drill_outcome_id.

    Return Values:
        None. Raises ValueError when a group is missing, extra or picked with a button of
        another group.

    Notes:
        The composite foreign key and the tap trigger are the backstop.
    """
    rows = fetch_all(session, LIST_GROUP_OUTCOME_IDS_SQL, {"drill_version_id": drill_version_id})
    allowed: dict[int, set[int]] = {}
    for row in rows:
        allowed.setdefault(int(cast(int, row["outcome_group_id"])), set()).add(
            int(cast(int, row["drill_outcome_id"]))
        )
    if set(picks) != set(allowed):
        raise ValueError("a rep needs one pick per outcome group")
    for group_id, outcome_id in picks.items():
        if outcome_id not in allowed[group_id]:
            raise ValueError("an outcome does not belong to its group")


def add_outcome_rep(
    session: Session,
    player_id: int,
    practice_session_id: int,
    practice_drill_id: int,
    picks: dict[int, int],
    tapped_at: datetime,
    side: str | None = None,
) -> int:
    """
    Description:
        Saves one rep of an outcome-button drill: an attempt with one tap per outcome group
        (REQ-077), idempotently on the tap instant.

    Parameters:
        session: the write session (inside run_write_with_reason).
        player_id: the entry player (outcome-submit.player_id).
        practice_session_id: the practice from the drill-context store.
        practice_drill_id: the practice drill from the drill-context store.
        picks: outcome_group_id -> drill_outcome_id, exactly one per group of the version.
        tapped_at: the device's tap instant (aware), stored as attempted_at.
        side: "left" or "right" for a drill with a symmetry type, else ignored (REQ-079).

    Return Values:
        int: the drill_attempt_id (the existing one for a retried rep).

    Notes:
        Locks the owner first. A retry with the same tapped_at returns the saved attempt
        instead of a second one.
    """
    owner = _lock_run_owner(
        session, practice_session_id, practice_drill_id, player_id, CAPTURE_OUTCOMES
    )
    _check_picks(session, cast(int, owner["drill_version_id"]), picks)
    existing = fetch_one(
        session,
        FIND_REP_SQL,
        {
            "practice_drill_id": practice_drill_id,
            "practice_player_id": owner["practice_player_id"],
            "attempted_at": tapped_at,
        },
    )
    if existing is not None:
        logger.warning("Outcome rep already saved as attempt %s", existing["drill_attempt_id"])
        return cast(int, existing["drill_attempt_id"])
    result_id = get_or_create_result(
        session,
        practice_session_id,
        practice_drill_id,
        cast(int, owner["practice_player_id"]),
        player_id,
    )
    created = insert_drill_attempt(
        session,
        result_id,
        owner,
        player_id,
        {
            "side": drill_side(owner, side),
            "value_1": None,
            "value_2": None,
            "attempted_at": tapped_at,
        },
    )
    attempt_id = cast(int, created["drill_attempt_id"])
    group_ids = list(picks)
    execute(
        session,
        INSERT_TAPS_SQL,
        {
            "drill_attempt_id": attempt_id,
            "group_ids": group_ids,
            "outcome_ids": [picks[group_id] for group_id in group_ids],
        },
    )
    return attempt_id


def undo_outcome_rep(
    session: Session,
    player_id: int,
    practice_session_id: int,
    practice_drill_id: int,
    drill_attempt_id: int,
) -> bool:
    """
    Description:
        Undoes one rep saved during this visit with a hard DELETE of its taps and its
        attempt, and of the result when it is left empty (REQ-010, D9).

    Parameters:
        session: the write session (inside run_write).
        player_id: the entry player (drill-player.value).
        practice_session_id: the practice from the drill-context store.
        practice_drill_id: the practice drill from the drill-context store.
        drill_attempt_id: the last id of this player in the outcome-visit store.

    Return Values:
        bool: True when the rep was removed; False when it is not a live attempt of this
        player's result for this practice drill (already gone).

    Notes:
        Locks the owner, then the attempt.
    """
    owner = _lock_run_owner(
        session, practice_session_id, practice_drill_id, player_id, CAPTURE_OUTCOMES
    )
    locked = fetch_one(
        session,
        LOCK_OWN_ATTEMPT_SQL,
        {
            "drill_attempt_id": drill_attempt_id,
            "practice_player_id": owner["practice_player_id"],
            "practice_drill_id": practice_drill_id,
        },
    )
    if locked is None:
        return False
    execute(session, DELETE_TAPS_SQL, {"drill_attempt_id": drill_attempt_id})
    delete_empty_attempt(session, drill_attempt_id)
    return True


# --- Summaries and personal bests (REQ-060, REQ-078; D7, D16) ---

GET_RESULT_SUMMARY_SQL = """
SELECT vr.drill_result_id,
       vr.attempt_count,
       vr.total_makes,
       vr.total_attempts,
       vr.total_make_pct,
       vr.best_value_1,
       vr.avg_value_1,
       vr.best_value_2,
       vr.avg_value_2,
       vr.reps_1, vr.clean_pct_1,
       vr.reps_2, vr.clean_pct_2,
       vr.reps_3, vr.clean_pct_3,
       (SELECT max(a.rate_per_minute)
        FROM h_analytics.v_drill_attempts a
        WHERE a.drill_result_id = vr.drill_result_id) AS best_rate_per_minute
FROM h_analytics.v_drill_results vr
WHERE vr.practice_session_id = :practice_session_id
  AND vr.practice_drill_id = :practice_drill_id
  AND vr.player_id = :player_id
"""

PERSONAL_BESTS_SQL = """
SELECT pl.player_id, b.result_type_code, b.metric, b.metric_label, b.unit,
       b.higher_is_better, b.best_value, b.best_makes, b.best_attempts, b.achieved_on
FROM unnest(CAST(:player_ids AS bigint[])) AS pl(player_id)
CROSS JOIN LATERAL h_analytics.fn_drill_personal_bests(pl.player_id, CAST(:drill_id AS bigint)) b
ORDER BY pl.player_id, b.metric
"""


def get_result_summary(
    session: Session, practice_session_id: int, practice_drill_id: int, player_id: int
) -> dict[str, object] | None:
    """
    Description:
        The entry player's result for a practice drill, aggregated over its live attempts
        (h_analytics.v_drill_results), for the summary line (D7).

    Parameters:
        session: the request session (or the write session after a save).
        practice_session_id: the practice.
        practice_drill_id: the practice drill.
        player_id: the entry player. Never a user id (COD-8-01).

    Return Values:
        dict[str, object] | None: {"drill_result_id", "attempt_count", "total_makes",
        "total_attempts", "total_make_pct", "best_value_1", "avg_value_1", "best_value_2",
        "avg_value_2", "reps_1", "clean_pct_1", "reps_2", "clean_pct_2", "reps_3",
        "clean_pct_3", "best_rate_per_minute"}; None when the player has no live result.

    Notes:
        Best follows the result type's direction, or the drill's for a physical value.
    """
    return fetch_one(
        session,
        GET_RESULT_SUMMARY_SQL,
        {
            "practice_session_id": practice_session_id,
            "practice_drill_id": practice_drill_id,
            "player_id": player_id,
        },
    )


def get_personal_bests_for_players(
    session: Session, player_ids: list[int], drill_id: int
) -> dict[int, list[dict[str, object]]]:
    """
    Description:
        The personal bests of several players for one drill lineage, in one statement
        (h_analytics.fn_drill_personal_bests; REQ-060).

    Parameters:
        session: the request session.
        player_ids: the participants (h_data.players ids). Never user ids.
        drill_id: the drill LINEAGE id (drill_versions.drill_id read on the server). Never a
            practice_drill_id (COD-8-01).

    Return Values:
        dict[int, list[dict[str, object]]]: player_id -> [{"player_id", "result_type_code",
        "metric", "metric_label", "unit", "higher_is_better", "best_value", "best_makes",
        "best_attempts", "achieved_on"}] by metric; a player without bests is absent.

    Notes:
        Read on page open and on a player change, never live (D16).
    """
    if not player_ids:
        return {}
    rows = fetch_all(
        session, PERSONAL_BESTS_SQL, {"player_ids": list(player_ids), "drill_id": drill_id}
    )
    bests: dict[int, list[dict[str, object]]] = {}
    for row in rows:
        bests.setdefault(int(cast(int, row["player_id"])), []).append(row)
    return bests


def get_personal_bests(
    session: Session, player_id: int, drill_id: int
) -> list[dict[str, object]]:
    """
    Description:
        One player's personal bests for one drill lineage (REQ-060).

    Parameters:
        session: the request session.
        player_id: the entry player (drill-player.value). Never a user id.
        drill_id: the drill LINEAGE id (from get_run_drill). Never a practice_drill_id.

    Return Values:
        list[dict[str, object]]: as get_personal_bests_for_players, for this player.

    Notes:
        None.
    """
    return get_personal_bests_for_players(session, [player_id], drill_id).get(player_id, [])
