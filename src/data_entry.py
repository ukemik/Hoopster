"""Data access for shot and free-throw entries of a practice or a game (the "entry" area).

REQ-004, REQ-006, REQ-007, REQ-008, REQ-010 to REQ-017, REQ-058, REQ-075. Every function takes
the request session first and runs its SQL through data_handler. Write functions never commit:
they run inside app_controller.run_write, after the acting user has been set (COD-6-01,
COD-7-01).

An entry belongs to an entry parent (EntryParent): a practice or a game. Its owner is the
entry player within that parent: in a practice, the player's live participant row
(practice_player_id, always resolved here in SQL and never accepted from the client,
Amendment A3, COD-8-01); in a game, the game's own player. Every write starts with
lock_entry_owner, which locks the parent row first (one lock order: parent row, then entry
rows) and refuses a parent or owner that is not live. Every read and write here covers
free-form entries only (drill_attempt_id IS NULL, plan D13) and hides deleted entries,
deleted parents, removed participant rows and removed profiles (COD-22-03).

Drill mode (practice_drills 2b, plan §3.7): a third parent kind, "drill", whose parent id is a
practice drill (practice_drill_id). Its entries are linked to a drill attempt of the entry
player's result for that practice drill; the practice is derived in SQL. Every drill branch is
keyed on kind == "drill" and uses its own SQL constants, so the practice and game paths are
unchanged. The drill helpers lock_drill_owner, get_or_create_result, insert_drill_attempt and
delete_empty_attempt are shared with src/data_drill_results.py.
"""

import logging
from typing import NamedTuple, cast

from sqlalchemy.orm import Session

from src.data_handler import fetch_all, fetch_one

logger = logging.getLogger(__name__)

PARENT_KIND_PRACTICE = "practice"
PARENT_KIND_GAME = "game"
# Drill mode: the parent id is a practice drill (plan §3.7, item 13 "KIND_DRILL").
PARENT_KIND_DRILL = "drill"

TABLE_SHOT = "shot"
TABLE_FREE_THROW = "free_throw"
ENTRY_TABLES: frozenset[str] = frozenset({TABLE_SHOT, TABLE_FREE_THROW})

ENTRY_MODE_SINGLE = "single"
# A single entry is always one attempt (REQ-017, ck_*_entries_counts).
SINGLE_ENTRY_ATTEMPTS = 1

# h_data.fn_soft_delete entity names, by table (its whitelist; never built from input).
SOFT_DELETE_ENTITY: dict[str, str] = {
    TABLE_SHOT: "shot_entry",
    TABLE_FREE_THROW: "free_throw_entry",
}


class EntryParent(NamedTuple):
    """
    Description:
        The parent an entry belongs to: a practice or a game.

    Parameters:
        kind: "practice" or "game".
        parent_id: h_data.practice_sessions.practice_session_id or h_data.games.game_id.

    Return Values:
        Not applicable (named tuple).

    Notes:
        Built by the controller from the entry-parent store (parse_entry_parent) or by a
        page builder after its live check. Every write re-checks the parent in SQL.
    """

    kind: str
    parent_id: int


def _parent_params(parent: EntryParent) -> dict[str, object]:
    """
    Description:
        The parent's two bound parameters: the practice id or the game id, the other None.

    Parameters:
        parent: the entry parent.

    Return Values:
        dict[str, object]: {"practice_session_id": int | None, "game_id": int | None}.

    Notes:
        Pure. Raises ValueError for an unknown kind.
    """
    if parent.kind == PARENT_KIND_PRACTICE:
        return {"practice_session_id": parent.parent_id, "game_id": None}
    if parent.kind == PARENT_KIND_GAME:
        return {"practice_session_id": None, "game_id": parent.parent_id}
    raise ValueError("unknown entry parent kind")


def _check_table(table: str) -> None:
    """
    Description:
        Refuses a table name other than "shot" or "free_throw".

    Parameters:
        table: the entry table key.

    Return Values:
        None. Raises ValueError for an unknown table.

    Notes:
        Pure. The table only selects a SQL constant; it is never put into SQL text.
    """
    if table not in ENTRY_TABLES:
        raise ValueError("unknown entry table")


# --- Court spots and shot types (REQ-007, REQ-008, REQ-013, REQ-016) ---

LIST_ACTIVE_COURT_SPOTS_SQL = """
SELECT cs.court_spot_id,
       cs.spot_number,
       cs.spot_name,
       cs.zone_code,
       z.label AS zone_label,
       cs.x_ft::float8 AS x_ft,
       cs.y_ft::float8 AS y_ft
FROM h_ref.court_spots cs
JOIN h_ref.zones z ON z.zone_code = cs.zone_code
WHERE cs.is_active
  AND cs.deleted_at IS NULL
ORDER BY cs.spot_number
"""

LIST_SHOT_TYPES_SQL = """
SELECT shot_type_code, label
FROM h_ref.shot_types
WHERE deleted_at IS NULL
ORDER BY sort_order
"""


def list_active_court_spots(session: Session) -> list[dict[str, object]]:
    """
    Description:
        Lists the active court spots with their zone label, read on every court-page load so
        nothing about the court is hard-coded (REQ-008).

    Parameters:
        session: the request session.

    Return Values:
        list[dict[str, object]]: {"court_spot_id", "spot_number", "spot_name", "zone_code",
        "zone_label", "x_ft", "y_ft"} per spot, ordered by spot_number; x_ft and y_ft are
        floats in feet.

    Notes:
        Shots reference spots by court_spot_id, never spot_number.
    """
    return fetch_all(session, LIST_ACTIVE_COURT_SPOTS_SQL)


def list_shot_types(session: Session) -> list[dict[str, object]]:
    """
    Description:
        Lists the live shot types (REQ-013) in display order, for the shot-details picker.

    Parameters:
        session: the request session.

    Return Values:
        list[dict[str, object]]: {"shot_type_code", "label"} per type, by sort_order.

    Notes:
        The insert and update re-check in SQL that a chosen code is live.
    """
    return fetch_all(session, LIST_SHOT_TYPES_SQL)


# --- Entry owner lock (Amendment A3, COD-8-01) ---

# Locks only the practice row (FOR UPDATE OF ps): that is what serialises two sends of one
# entry, and it keeps the lock order of h_data.fn_remove_practice_player (practice first). The
# insert trigger then takes its own share lock on the participant row.
LOCK_PRACTICE_OWNER_SQL = """
SELECT pp.practice_player_id
FROM h_data.practice_sessions ps
JOIN h_data.practice_players pp ON pp.practice_session_id = ps.practice_session_id
JOIN h_data.players p ON p.player_id = pp.player_id
WHERE ps.practice_session_id = :parent_id
  AND ps.deleted_at IS NULL
  AND pp.player_id = :player_id
  AND pp.deleted_at IS NULL
  AND p.deleted_at IS NULL
FOR UPDATE OF ps
"""

# Locks the live game row of a live profile; the entry player must be the game's player (the
# two-column foreign key on ux_games_player backs this in the database).
LOCK_GAME_OWNER_SQL = """
SELECT g.game_id
FROM h_data.games g
JOIN h_data.players p ON p.player_id = g.player_id
WHERE g.game_id = :parent_id
  AND g.deleted_at IS NULL
  AND g.player_id = :player_id
  AND p.deleted_at IS NULL
FOR UPDATE OF g
"""

LOCK_OWNER_SQL: dict[str, str] = {
    PARENT_KIND_PRACTICE: LOCK_PRACTICE_OWNER_SQL,
    PARENT_KIND_GAME: LOCK_GAME_OWNER_SQL,
}


def lock_entry_owner(session: Session, parent: EntryParent, player_id: int) -> int | None:
    """
    Description:
        Locks the entry parent's row and checks the entry player owns entries in it: in a
        practice, a live participant row of a live profile; in a game, the game's player
        with a live profile.

    Parameters:
        session: the write session (inside run_write).
        parent: the entry parent (practice, game or drill), from the entry-parent store.
        player_id: the entry player (the command's player_id or court-player.value). Never
            current_player_id() and never a user id (COD-8-01).

    Return Values:
        int | None: the practice's live practice_player_id for that player (a practice or a
        drill parent); None for a game.

    Notes:
        Always the first statement of every entry write, so the lock order is parent row,
        then entry rows. Raises ValueError ("entry owner not found") when the parent is not
        live or the player is not its owner, and for an unknown parent kind. A drill parent
        locks the practice of the live practice drill (lock_drill_owner).
    """
    if parent.kind == PARENT_KIND_DRILL:
        owner = lock_drill_owner(session, parent.parent_id, player_id)
        return cast(int, owner["practice_player_id"])
    if parent.kind not in LOCK_OWNER_SQL:
        raise ValueError("unknown entry parent kind")
    locked = fetch_one(
        session,
        LOCK_OWNER_SQL[parent.kind],
        {"parent_id": parent.parent_id, "player_id": player_id},
    )
    if locked is None:
        raise ValueError("entry owner not found")
    if parent.kind == PARENT_KIND_PRACTICE:
        return cast(int, locked["practice_player_id"])
    return None


def _owner_params(
    parent: EntryParent, player_id: int, practice_player_id: int | None
) -> dict[str, object]:
    """
    Description:
        The bound parameters of the owner condition shared by every entry write: parent ids,
        participant row and player.

    Parameters:
        parent: the entry parent.
        player_id: the entry player.
        practice_player_id: the participant row from lock_entry_owner (None for a game).

    Return Values:
        dict[str, object]: {"practice_session_id", "game_id", "practice_player_id",
        "player_id"}.

    Notes:
        Pure. Pairs with the OWNER condition text of each write statement.
    """
    return {
        **_parent_params(parent),
        "practice_player_id": practice_player_id,
        "player_id": player_id,
    }


# --- Drill mode: owner, result and attempts (practice_drills plan §3.7, item 14) ---

RESULT_TYPE_SHOOTING = "shooting"
SYMMETRY_COURT_SIDE = "court_side"
SIDES: frozenset[str] = frozenset({"left", "right"})

# Step 1 of the drill owner lock: the practice row of the practice drill, FOR UPDATE. A separate
# statement, so the owner read that follows sees every change committed before the lock was
# granted (READ COMMITTED takes a new snapshot per statement).
LOCK_DRILL_PRACTICE_SQL = """
SELECT ps.practice_session_id
FROM h_data.practice_sessions ps
WHERE ps.practice_session_id = (SELECT pd.practice_session_id
                                FROM h_data.practice_drills pd
                                WHERE pd.practice_drill_id = :practice_drill_id)
  AND ps.deleted_at IS NULL
FOR UPDATE
"""

# Step 2: the live practice drill (and its live placed set, if any), its version, and the
# entry player's live participant row of a live profile.
GET_DRILL_OWNER_SQL = """
SELECT pp.practice_player_id,
       pd.practice_session_id,
       pd.drill_version_id,
       dv.drill_id,
       dv.result_type_code,
       dv.symmetry_type_code,
       rt.capture_kind
FROM h_data.practice_drills pd
LEFT JOIN h_data.practice_drill_sets pds ON pds.practice_drill_set_id = pd.practice_drill_set_id
JOIN h_data.drill_versions dv ON dv.drill_version_id = pd.drill_version_id
JOIN h_ref.result_types rt ON rt.result_type_code = dv.result_type_code
JOIN h_data.practice_players pp ON pp.practice_session_id = pd.practice_session_id
JOIN h_data.players p ON p.player_id = pp.player_id
WHERE pd.practice_drill_id = :practice_drill_id
  AND pd.deleted_at IS NULL
  AND (pd.practice_drill_set_id IS NULL OR pds.deleted_at IS NULL)
  AND pp.player_id = :player_id
  AND pp.deleted_at IS NULL
  AND p.deleted_at IS NULL
"""

FIND_LIVE_RESULT_SQL = """
SELECT drill_result_id
FROM h_data.drill_results
WHERE practice_drill_id = :practice_drill_id
  AND practice_player_id = :practice_player_id
  AND deleted_at IS NULL
"""

# drill_version_id is copied from the practice drill (the trigger sets the same value).
INSERT_RESULT_SQL = """
INSERT INTO h_data.drill_results
    (practice_session_id, practice_player_id, practice_drill_id, player_id, drill_version_id)
SELECT pd.practice_session_id,
       CAST(:practice_player_id AS bigint),
       pd.practice_drill_id,
       CAST(:player_id AS bigint),
       pd.drill_version_id
FROM h_data.practice_drills pd
WHERE pd.practice_drill_id = :practice_drill_id
  AND pd.practice_session_id = :practice_session_id
RETURNING drill_result_id
"""

LATEST_LIVE_ATTEMPT_SQL = """
SELECT drill_attempt_id, attempt_no, side
FROM h_data.drill_attempts
WHERE drill_result_id = :drill_result_id
  AND deleted_at IS NULL
ORDER BY attempt_no DESC
LIMIT 1
"""

# attempt_no is set by h_data.tf_drill_attempts_before_write; attempted_at defaults to now().
INSERT_DRILL_ATTEMPT_SQL = """
INSERT INTO h_data.drill_attempts
    (drill_result_id, practice_session_id, practice_player_id, player_id,
     side, value_1, value_2, attempted_at)
VALUES (CAST(:drill_result_id AS bigint),
        CAST(:practice_session_id AS bigint),
        CAST(:practice_player_id AS bigint),
        CAST(:player_id AS bigint),
        CAST(:side AS text),
        CAST(:value_1 AS numeric),
        CAST(:value_2 AS numeric),
        COALESCE(CAST(:attempted_at AS timestamptz), now()))
RETURNING drill_attempt_id, attempt_no
"""

# An attempt is hard-deleted (undo, REQ-010) only while nothing references it in any state,
# so no foreign key is broken and no soft-deleted row loses its parent.
DELETE_EMPTY_ATTEMPT_SQL = """
DELETE FROM h_data.drill_attempts a
WHERE a.drill_attempt_id = :drill_attempt_id
  AND a.deleted_at IS NULL
  AND NOT EXISTS (SELECT 1 FROM h_data.shot_entries se
                  WHERE se.drill_attempt_id = a.drill_attempt_id)
  AND NOT EXISTS (SELECT 1 FROM h_data.free_throw_entries fe
                  WHERE fe.drill_attempt_id = a.drill_attempt_id)
  AND NOT EXISTS (SELECT 1 FROM h_data.drill_attempt_outcomes o
                  WHERE o.drill_attempt_id = a.drill_attempt_id)
RETURNING a.drill_result_id
"""

# A result without attempts (in any state) and without notes is removed with its last undo,
# so undoing everything leaves the drill "not started" (plan §3.7).
DELETE_EMPTY_RESULT_SQL = """
DELETE FROM h_data.drill_results r
WHERE r.drill_result_id = :drill_result_id
  AND r.deleted_at IS NULL
  AND r.notes IS NULL
  AND NOT EXISTS (SELECT 1 FROM h_data.drill_attempts a
                  WHERE a.drill_result_id = r.drill_result_id)
RETURNING r.drill_result_id
"""


def lock_drill_owner(
    session: Session, practice_drill_id: int, player_id: int
) -> dict[str, object]:
    """
    Description:
        The drill-mode owner lock: locks the practice row of a live practice drill FOR
        UPDATE, then resolves the entry player's live participant row and the drill's
        current version.

    Parameters:
        session: the write session (inside run_write / run_write_with_reason).
        practice_drill_id: the practice drill (entry-parent id of kind "drill", or the
            drill-context store). Never a drill_id (COD-8-01).
        player_id: the entry player (the command's player_id, drill-player.value or
            outcome-submit.player_id). Never a user id.

    Return Values:
        dict[str, object]: {"practice_player_id", "practice_session_id", "drill_version_id",
        "drill_id", "result_type_code", "symmetry_type_code", "capture_kind"}.

    Notes:
        Raises ValueError ("entry owner not found") when the practice, the practice drill or
        its placed set is not live, or the player has no live participant row of a live
        profile. The practice_player_id is always resolved here, never taken from the client
        (Context Adaptation Table). Placements, removals and swaps lock the same practice
        row first, so they never interleave with a drill entry.
    """
    if fetch_one(
        session, LOCK_DRILL_PRACTICE_SQL, {"practice_drill_id": practice_drill_id}
    ) is None:
        raise ValueError("entry owner not found")
    owner = fetch_one(
        session,
        GET_DRILL_OWNER_SQL,
        {"practice_drill_id": practice_drill_id, "player_id": player_id},
    )
    if owner is None:
        raise ValueError("entry owner not found")
    return owner


def get_or_create_result(
    session: Session,
    practice_session_id: int,
    practice_drill_id: int,
    practice_player_id: int,
    player_id: int,
) -> int:
    """
    Description:
        The entry player's live result for a practice drill, created when there is none
        (one live result per practice drill and participant row, REQ-078).

    Parameters:
        session: the write session, after lock_drill_owner.
        practice_session_id: the practice of the practice drill (from lock_drill_owner).
        practice_drill_id: the practice drill.
        practice_player_id: the participant row resolved by lock_drill_owner.
        player_id: the entry player.

    Return Values:
        int: the h_data.drill_results.drill_result_id.

    Notes:
        A new result never revives a deleted one (COD-22-02); its version is the practice
        drill's current one (trigger). The practice lock makes the find-or-insert safe;
        ux_drill_results_slot_participant is the backstop.
    """
    found = fetch_one(
        session,
        FIND_LIVE_RESULT_SQL,
        {"practice_drill_id": practice_drill_id, "practice_player_id": practice_player_id},
    )
    if found is not None:
        return cast(int, found["drill_result_id"])
    inserted = fetch_one(
        session,
        INSERT_RESULT_SQL,
        {
            "practice_session_id": practice_session_id,
            "practice_drill_id": practice_drill_id,
            "practice_player_id": practice_player_id,
            "player_id": player_id,
        },
    )
    if inserted is None:
        raise ValueError("practice drill not found")
    return cast(int, inserted["drill_result_id"])


def insert_drill_attempt(
    session: Session,
    drill_result_id: int,
    owner: dict[str, object],
    player_id: int,
    attempt: dict[str, object],
) -> dict[str, object]:
    """
    Description:
        Inserts one attempt of a drill result (REQ-078).

    Parameters:
        session: the write session, after lock_drill_owner.
        drill_result_id: the result (get_or_create_result).
        owner: the lock_drill_owner row (practice and participant row).
        player_id: the entry player.
        attempt: {"side", "value_1", "value_2", "attempted_at"}; each may be None
            (attempted_at None = now()).

    Return Values:
        dict[str, object]: {"drill_attempt_id", "attempt_no"}.

    Notes:
        The trigger numbers the attempt and refuses a side or values the version does not
        record (REQ-076, REQ-079).
    """
    row = fetch_one(
        session,
        INSERT_DRILL_ATTEMPT_SQL,
        {
            "drill_result_id": drill_result_id,
            "practice_session_id": owner["practice_session_id"],
            "practice_player_id": owner["practice_player_id"],
            "player_id": player_id,
            "side": attempt.get("side"),
            "value_1": attempt.get("value_1"),
            "value_2": attempt.get("value_2"),
            "attempted_at": attempt.get("attempted_at"),
        },
    )
    if row is None:
        logger.error("Drill attempt insert returned no id")
        raise RuntimeError("Drill attempt insert returned no id")
    return row


def delete_empty_attempt(session: Session, drill_attempt_id: int) -> None:
    """
    Description:
        After an undo, hard-deletes the attempt when nothing references it any more, and
        then its result when that has no attempts and no notes (plan §3.7, D9).

    Parameters:
        session: the write session, after lock_drill_owner.
        drill_attempt_id: the attempt whose last entry or taps were just removed.

    Return Values:
        None.

    Notes:
        Only live rows with no referencing rows in any state are deleted, so a soft-deleted
        row never loses its parent (COD-22-02).
    """
    deleted = fetch_one(session, DELETE_EMPTY_ATTEMPT_SQL, {"drill_attempt_id": drill_attempt_id})
    if deleted is None:
        return
    fetch_one(session, DELETE_EMPTY_RESULT_SQL, {"drill_result_id": deleted["drill_result_id"]})


def drill_side(owner: dict[str, object], side: object) -> str | None:
    """
    Description:
        The side an attempt of this drill stores: the given side when the version records
        one, else None (REQ-079).

    Parameters:
        owner: the lock_drill_owner row (result type and symmetry).
        side: "left", "right" or None, from the client.

    Return Values:
        str | None: the side to store.

    Notes:
        Pure. A shooting drill with the court_side symmetry takes its side from each shot's
        spot, so it stores none (h_data.tf_drill_attempts_before_write).
    """
    symmetry = owner.get("symmetry_type_code")
    if symmetry is None:
        return None
    if owner.get("result_type_code") == RESULT_TYPE_SHOOTING and symmetry == SYMMETRY_COURT_SIDE:
        return None
    return side if isinstance(side, str) and side in SIDES else None


# --- Totals: the court-base store (REQ-010, REQ-058; plan D8, D13) ---

# Every live participant row (live profile, live practice), 0 when it has no entries. Entries
# are matched by practice_player_id, so a removed earlier row of the same player never counts.
GET_PRACTICE_TOTALS_SQL = """
WITH fg AS (
    SELECT se.practice_player_id,
           sum(se.makes)::int AS makes,
           sum(se.attempts)::int AS attempts
    FROM h_data.shot_entries se
    WHERE se.practice_session_id = :parent_id
      AND se.deleted_at IS NULL
      AND se.drill_attempt_id IS NULL
    GROUP BY se.practice_player_id
), ft AS (
    SELECT fe.practice_player_id,
           sum(fe.makes)::int AS makes,
           sum(fe.attempts)::int AS attempts
    FROM h_data.free_throw_entries fe
    WHERE fe.practice_session_id = :parent_id
      AND fe.deleted_at IS NULL
      AND fe.drill_attempt_id IS NULL
    GROUP BY fe.practice_player_id
)
SELECT pp.player_id,
       COALESCE(fg.makes, 0) AS makes,
       COALESCE(fg.attempts, 0) AS attempts,
       COALESCE(ft.makes, 0) AS ft_makes,
       COALESCE(ft.attempts, 0) AS ft_attempts
FROM h_data.practice_players pp
JOIN h_data.players p ON p.player_id = pp.player_id
JOIN h_data.practice_sessions ps ON ps.practice_session_id = pp.practice_session_id
LEFT JOIN fg ON fg.practice_player_id = pp.practice_player_id
LEFT JOIN ft ON ft.practice_player_id = pp.practice_player_id
WHERE pp.practice_session_id = :parent_id
  AND pp.deleted_at IS NULL
  AND p.deleted_at IS NULL
  AND ps.deleted_at IS NULL
"""

# The live game's player (live profile) over that game's live entries; one row, or none when
# the game or profile is not live.
GET_GAME_TOTALS_SQL = """
WITH fg AS (
    SELECT sum(se.makes)::int AS makes, sum(se.attempts)::int AS attempts
    FROM h_data.shot_entries se
    WHERE se.game_id = :parent_id
      AND se.deleted_at IS NULL
      AND se.drill_attempt_id IS NULL
), ft AS (
    SELECT sum(fe.makes)::int AS makes, sum(fe.attempts)::int AS attempts
    FROM h_data.free_throw_entries fe
    WHERE fe.game_id = :parent_id
      AND fe.deleted_at IS NULL
      AND fe.drill_attempt_id IS NULL
)
SELECT g.player_id,
       COALESCE(fg.makes, 0) AS makes,
       COALESCE(fg.attempts, 0) AS attempts,
       COALESCE(ft.makes, 0) AS ft_makes,
       COALESCE(ft.attempts, 0) AS ft_attempts
FROM h_data.games g
JOIN h_data.players p ON p.player_id = g.player_id
CROSS JOIN fg
CROSS JOIN ft
WHERE g.game_id = :parent_id
  AND g.deleted_at IS NULL
  AND p.deleted_at IS NULL
"""

GET_TOTALS_SQL: dict[str, str] = {
    PARENT_KIND_PRACTICE: GET_PRACTICE_TOTALS_SQL,
    PARENT_KIND_GAME: GET_GAME_TOTALS_SQL,
}

# Drill mode (plan §3.7): per live participant of the live practice drill, the latest live
# attempt of its live result with that attempt's field goals, free throws and attempts per spot
# key ("s<court_spot_id>" or "ft"), plus the whole result's makes and attempts (field goals and
# free throws, as h_analytics.v_drill_results counts them).
GET_DRILL_TOTALS_SQL = """
WITH slot AS (
    SELECT pd.practice_drill_id, pd.practice_session_id
    FROM h_data.practice_drills pd
    JOIN h_data.practice_sessions ps ON ps.practice_session_id = pd.practice_session_id
    LEFT JOIN h_data.practice_drill_sets pds
           ON pds.practice_drill_set_id = pd.practice_drill_set_id
    WHERE pd.practice_drill_id = :parent_id
      AND pd.deleted_at IS NULL
      AND ps.deleted_at IS NULL
      AND (pd.practice_drill_set_id IS NULL OR pds.deleted_at IS NULL)
), owners AS (
    SELECT pp.practice_player_id, pp.player_id
    FROM slot
    JOIN h_data.practice_players pp ON pp.practice_session_id = slot.practice_session_id
    JOIN h_data.players p ON p.player_id = pp.player_id
    WHERE pp.deleted_at IS NULL
      AND p.deleted_at IS NULL
), live_attempts AS (
    SELECT a.drill_attempt_id, a.attempt_no, a.side, r.practice_player_id
    FROM slot
    JOIN h_data.drill_results r ON r.practice_drill_id = slot.practice_drill_id
    JOIN h_data.drill_attempts a ON a.drill_result_id = r.drill_result_id
    WHERE r.deleted_at IS NULL
      AND a.deleted_at IS NULL
), latest AS (
    SELECT DISTINCT ON (la.practice_player_id)
           la.practice_player_id, la.drill_attempt_id, la.attempt_no, la.side
    FROM live_attempts la
    ORDER BY la.practice_player_id, la.attempt_no DESC
), linked AS (
    SELECT la.practice_player_id, la.drill_attempt_id,
           's' || se.court_spot_id::text AS spot_key, false AS is_ft,
           se.makes::int AS makes, se.attempts::int AS attempts
    FROM live_attempts la
    JOIN h_data.shot_entries se ON se.drill_attempt_id = la.drill_attempt_id
    WHERE se.deleted_at IS NULL
    UNION ALL
    SELECT la.practice_player_id, la.drill_attempt_id,
           'ft', true, fe.makes::int, fe.attempts::int
    FROM live_attempts la
    JOIN h_data.free_throw_entries fe ON fe.drill_attempt_id = la.drill_attempt_id
    WHERE fe.deleted_at IS NULL
)
SELECT o.player_id,
       COALESCE(l.attempt_no, 0)::int AS attempt_no,
       l.side,
       COALESCE((SELECT sum(k.makes) FROM linked k
                 WHERE k.drill_attempt_id = l.drill_attempt_id AND NOT k.is_ft), 0)::int AS makes,
       COALESCE((SELECT sum(k.attempts) FROM linked k
                 WHERE k.drill_attempt_id = l.drill_attempt_id AND NOT k.is_ft), 0)::int AS attempts,
       COALESCE((SELECT sum(k.makes) FROM linked k
                 WHERE k.drill_attempt_id = l.drill_attempt_id AND k.is_ft), 0)::int AS ft_makes,
       COALESCE((SELECT sum(k.attempts) FROM linked k
                 WHERE k.drill_attempt_id = l.drill_attempt_id AND k.is_ft), 0)::int AS ft_attempts,
       COALESCE((SELECT sum(k.makes) FROM linked k
                 WHERE k.practice_player_id = o.practice_player_id), 0)::int AS result_makes,
       COALESCE((SELECT sum(k.attempts) FROM linked k
                 WHERE k.practice_player_id = o.practice_player_id), 0)::int AS result_attempts,
       COALESCE((SELECT jsonb_object_agg(g.spot_key, g.spot_attempts)
                 FROM (SELECT k.spot_key, sum(k.attempts)::int AS spot_attempts
                       FROM linked k
                       WHERE k.drill_attempt_id = l.drill_attempt_id
                       GROUP BY k.spot_key) g), '{}'::jsonb) AS spots
FROM owners o
LEFT JOIN latest l ON l.practice_player_id = o.practice_player_id
"""


def _drill_totals(rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    """
    Description:
        Shapes the GET_DRILL_TOTALS_SQL rows as the court-base store of drill mode.

    Parameters:
        rows: the query rows, one per live participant.

    Return Values:
        dict[str, dict[str, object]]: {str(player_id): {"makes", "attempts", "ft_makes",
        "ft_attempts", "attempt_no", "side", "spots", "result_makes", "result_attempts"}}.

    Notes:
        Pure. makes/attempts and ft_* are the latest attempt's, so the shared tallies show
        the attempt in progress; spots maps "s<id>" / "ft" to that attempt's attempts.
    """
    totals: dict[str, dict[str, object]] = {}
    for row in rows:
        spots = row["spots"] if isinstance(row["spots"], dict) else {}
        totals[str(int(cast(int, row["player_id"])))] = {
            "makes": int(cast(int, row["makes"])),
            "attempts": int(cast(int, row["attempts"])),
            "ft_makes": int(cast(int, row["ft_makes"])),
            "ft_attempts": int(cast(int, row["ft_attempts"])),
            "attempt_no": int(cast(int, row["attempt_no"])),
            "side": row["side"],
            "spots": {str(key): int(value) for key, value in spots.items()},
            "result_makes": int(cast(int, row["result_makes"])),
            "result_attempts": int(cast(int, row["result_attempts"])),
        }
    return totals


def get_entry_totals(session: Session, parent: EntryParent) -> dict[str, dict[str, object]]:
    """
    Description:
        Totals the live free-form field goals and free throws of every owner of an entry
        parent: the court-base store, refreshed after every save, undo, edit and delete
        (plan D8). For a drill parent: each participant's latest attempt (see _drill_totals).

    Parameters:
        session: the request session (or the write session, to read in the same
            transaction as a write).
        parent: the entry parent (practice, game or drill).

    Return Values:
        dict[str, dict[str, object]]: {str(player_id): {"makes", "attempts", "ft_makes",
        "ft_attempts"}} (all int): every live participant of a practice (0s when nothing is
        entered), or the game's player; a drill parent adds "attempt_no", "side", "spots",
        "result_makes" and "result_attempts". Empty when the parent or every owner is not
        live.

    Notes:
        One statement (COD-16-01). Drill-linked entries are not counted for a practice or
        game (plan D13). Raises ValueError for an unknown parent kind.
    """
    if parent.kind == PARENT_KIND_DRILL:
        return _drill_totals(
            fetch_all(session, GET_DRILL_TOTALS_SQL, {"parent_id": parent.parent_id})
        )
    if parent.kind not in GET_TOTALS_SQL:
        raise ValueError("unknown entry parent kind")
    rows = fetch_all(session, GET_TOTALS_SQL[parent.kind], {"parent_id": parent.parent_id})
    return {
        str(int(cast(int, row["player_id"]))): {
            "makes": int(cast(int, row["makes"])),
            "attempts": int(cast(int, row["attempts"])),
            "ft_makes": int(cast(int, row["ft_makes"])),
            "ft_attempts": int(cast(int, row["ft_attempts"])),
        }
        for row in rows
    }


# --- Insert and undo (REQ-010 to REQ-014, REQ-017, REQ-058) ---

# The owner condition of every entry write: the parent's ids (one of them NULL), the
# participant row (NULL in a game) and the player.
FIND_DUPLICATE_SHOT_SQL = """
SELECT shot_entry_id AS entry_id, created_at
FROM h_data.shot_entries
WHERE practice_session_id IS NOT DISTINCT FROM CAST(:practice_session_id AS bigint)
  AND game_id IS NOT DISTINCT FROM CAST(:game_id AS bigint)
  AND practice_player_id IS NOT DISTINCT FROM CAST(:practice_player_id AS bigint)
  AND player_id = CAST(:player_id AS bigint)
  AND deleted_at IS NULL
  AND drill_attempt_id IS NULL
  AND entry_mode = CAST(:entry_mode AS text)
  AND court_spot_id IS NOT DISTINCT FROM CAST(:court_spot_id AS bigint)
  AND manual_point_value IS NOT DISTINCT FROM CAST(:point_value AS smallint)
  AND makes = CAST(:makes AS smallint)
  AND attempts = CAST(:attempts AS smallint)
  AND shot_at = CAST(:shot_at AS timestamptz)
"""

FIND_DUPLICATE_FREE_THROW_SQL = """
SELECT free_throw_entry_id AS entry_id, created_at
FROM h_data.free_throw_entries
WHERE practice_session_id IS NOT DISTINCT FROM CAST(:practice_session_id AS bigint)
  AND game_id IS NOT DISTINCT FROM CAST(:game_id AS bigint)
  AND practice_player_id IS NOT DISTINCT FROM CAST(:practice_player_id AS bigint)
  AND player_id = CAST(:player_id AS bigint)
  AND deleted_at IS NULL
  AND drill_attempt_id IS NULL
  AND entry_mode = CAST(:entry_mode AS text)
  AND makes = CAST(:makes AS smallint)
  AND attempts = CAST(:attempts AS smallint)
  AND shot_at = CAST(:shot_at AS timestamptz)
"""

# The spot (when given) must be active and live and the shot type (when given) live; a refused
# value makes the SELECT return no row, so nothing is inserted.
INSERT_SHOT_SQL = """
INSERT INTO h_data.shot_entries
    (game_id, practice_session_id, practice_player_id, player_id,
     entry_mode, court_spot_id, manual_point_value, makes, attempts, shot_at,
     shot_type_code, hand, creation, defense)
SELECT CAST(:game_id AS bigint),
       CAST(:practice_session_id AS bigint),
       CAST(:practice_player_id AS bigint),
       CAST(:player_id AS bigint),
       CAST(:entry_mode AS text),
       CAST(:court_spot_id AS bigint),
       CAST(:point_value AS smallint),
       CAST(:makes AS smallint),
       CAST(:attempts AS smallint),
       CAST(:shot_at AS timestamptz),
       CAST(:shot_type_code AS text),
       CAST(:hand AS text),
       CAST(:creation AS text),
       CAST(:defense AS text)
WHERE (CAST(:court_spot_id AS bigint) IS NULL
       OR EXISTS (SELECT 1
                  FROM h_ref.court_spots cs
                  WHERE cs.court_spot_id = CAST(:court_spot_id AS bigint)
                    AND cs.is_active
                    AND cs.deleted_at IS NULL))
  AND (CAST(:shot_type_code AS text) IS NULL
       OR EXISTS (SELECT 1
                  FROM h_ref.shot_types st
                  WHERE st.shot_type_code = CAST(:shot_type_code AS text)
                    AND st.deleted_at IS NULL))
RETURNING shot_entry_id AS entry_id, created_at
"""

INSERT_FREE_THROW_SQL = """
INSERT INTO h_data.free_throw_entries
    (game_id, practice_session_id, practice_player_id, player_id,
     entry_mode, makes, attempts, shot_at)
VALUES (CAST(:game_id AS bigint),
        CAST(:practice_session_id AS bigint),
        CAST(:practice_player_id AS bigint),
        CAST(:player_id AS bigint),
        CAST(:entry_mode AS text),
        CAST(:makes AS smallint),
        CAST(:attempts AS smallint),
        CAST(:shot_at AS timestamptz))
RETURNING free_throw_entry_id AS entry_id, created_at
"""

DELETE_SHOT_SQL = """
DELETE FROM h_data.shot_entries
WHERE shot_entry_id = :entry_id
  AND practice_session_id IS NOT DISTINCT FROM CAST(:practice_session_id AS bigint)
  AND game_id IS NOT DISTINCT FROM CAST(:game_id AS bigint)
  AND practice_player_id IS NOT DISTINCT FROM CAST(:practice_player_id AS bigint)
  AND player_id = CAST(:player_id AS bigint)
  AND deleted_at IS NULL
  AND drill_attempt_id IS NULL
RETURNING shot_entry_id AS entry_id
"""

DELETE_FREE_THROW_SQL = """
DELETE FROM h_data.free_throw_entries
WHERE free_throw_entry_id = :entry_id
  AND practice_session_id IS NOT DISTINCT FROM CAST(:practice_session_id AS bigint)
  AND game_id IS NOT DISTINCT FROM CAST(:game_id AS bigint)
  AND practice_player_id IS NOT DISTINCT FROM CAST(:practice_player_id AS bigint)
  AND player_id = CAST(:player_id AS bigint)
  AND deleted_at IS NULL
  AND drill_attempt_id IS NULL
RETURNING free_throw_entry_id AS entry_id
"""

FIND_DUPLICATE_SQL: dict[str, str] = {
    TABLE_SHOT: FIND_DUPLICATE_SHOT_SQL,
    TABLE_FREE_THROW: FIND_DUPLICATE_FREE_THROW_SQL,
}
INSERT_SQL: dict[str, str] = {
    TABLE_SHOT: INSERT_SHOT_SQL,
    TABLE_FREE_THROW: INSERT_FREE_THROW_SQL,
}
DELETE_HARD_SQL: dict[str, str] = {
    TABLE_SHOT: DELETE_SHOT_SQL,
    TABLE_FREE_THROW: DELETE_FREE_THROW_SQL,
}

# --- Drill mode: insert and undo (plan §3.7) ---

# The duplicate check of drill mode: the participant's live entries linked to any attempt of
# this practice drill's results, same fields and shot_at.
FIND_DUPLICATE_DRILL_SHOT_SQL = """
SELECT se.shot_entry_id AS entry_id, se.created_at
FROM h_data.shot_entries se
JOIN h_data.drill_attempts a ON a.drill_attempt_id = se.drill_attempt_id
JOIN h_data.drill_results r ON r.drill_result_id = a.drill_result_id
WHERE r.practice_drill_id = :practice_drill_id
  AND se.practice_player_id = :practice_player_id
  AND se.deleted_at IS NULL
  AND se.entry_mode = CAST(:entry_mode AS text)
  AND se.court_spot_id IS NOT DISTINCT FROM CAST(:court_spot_id AS bigint)
  AND se.makes = CAST(:makes AS smallint)
  AND se.attempts = CAST(:attempts AS smallint)
  AND se.shot_at = CAST(:shot_at AS timestamptz)
"""

FIND_DUPLICATE_DRILL_FREE_THROW_SQL = """
SELECT fe.free_throw_entry_id AS entry_id, fe.created_at
FROM h_data.free_throw_entries fe
JOIN h_data.drill_attempts a ON a.drill_attempt_id = fe.drill_attempt_id
JOIN h_data.drill_results r ON r.drill_result_id = a.drill_result_id
WHERE r.practice_drill_id = :practice_drill_id
  AND fe.practice_player_id = :practice_player_id
  AND fe.deleted_at IS NULL
  AND fe.entry_mode = CAST(:entry_mode AS text)
  AND fe.makes = CAST(:makes AS smallint)
  AND fe.attempts = CAST(:attempts AS smallint)
  AND fe.shot_at = CAST(:shot_at AS timestamptz)
"""

# As INSERT_SHOT_SQL, with the drill attempt link and always a spot (no manual mode in drills).
INSERT_DRILL_SHOT_SQL = """
INSERT INTO h_data.shot_entries
    (practice_session_id, practice_player_id, player_id,
     entry_mode, court_spot_id, makes, attempts, shot_at,
     shot_type_code, hand, creation, defense, drill_attempt_id)
SELECT CAST(:practice_session_id AS bigint),
       CAST(:practice_player_id AS bigint),
       CAST(:player_id AS bigint),
       CAST(:entry_mode AS text),
       CAST(:court_spot_id AS bigint),
       CAST(:makes AS smallint),
       CAST(:attempts AS smallint),
       CAST(:shot_at AS timestamptz),
       CAST(:shot_type_code AS text),
       CAST(:hand AS text),
       CAST(:creation AS text),
       CAST(:defense AS text),
       CAST(:drill_attempt_id AS bigint)
WHERE EXISTS (SELECT 1
              FROM h_ref.court_spots cs
              WHERE cs.court_spot_id = CAST(:court_spot_id AS bigint)
                AND cs.is_active
                AND cs.deleted_at IS NULL)
  AND (CAST(:shot_type_code AS text) IS NULL
       OR EXISTS (SELECT 1
                  FROM h_ref.shot_types st
                  WHERE st.shot_type_code = CAST(:shot_type_code AS text)
                    AND st.deleted_at IS NULL))
RETURNING shot_entry_id AS entry_id, created_at
"""

# The free-throw trigger refuses a link to a version without a free-throw-line spot.
INSERT_DRILL_FREE_THROW_SQL = """
INSERT INTO h_data.free_throw_entries
    (practice_session_id, practice_player_id, player_id,
     entry_mode, makes, attempts, shot_at, drill_attempt_id)
VALUES (CAST(:practice_session_id AS bigint),
        CAST(:practice_player_id AS bigint),
        CAST(:player_id AS bigint),
        CAST(:entry_mode AS text),
        CAST(:makes AS smallint),
        CAST(:attempts AS smallint),
        CAST(:shot_at AS timestamptz),
        CAST(:drill_attempt_id AS bigint))
RETURNING free_throw_entry_id AS entry_id, created_at
"""

# Undo of a drill entry: only a live row of the participant linked to a live attempt of its
# live result for this practice drill.
DELETE_DRILL_SHOT_SQL = """
DELETE FROM h_data.shot_entries se
USING h_data.drill_attempts a, h_data.drill_results r
WHERE se.shot_entry_id = :entry_id
  AND se.practice_player_id = :practice_player_id
  AND se.deleted_at IS NULL
  AND a.drill_attempt_id = se.drill_attempt_id
  AND a.deleted_at IS NULL
  AND r.drill_result_id = a.drill_result_id
  AND r.practice_drill_id = :practice_drill_id
  AND r.practice_player_id = :practice_player_id
  AND r.deleted_at IS NULL
RETURNING se.drill_attempt_id
"""

DELETE_DRILL_FREE_THROW_SQL = """
DELETE FROM h_data.free_throw_entries fe
USING h_data.drill_attempts a, h_data.drill_results r
WHERE fe.free_throw_entry_id = :entry_id
  AND fe.practice_player_id = :practice_player_id
  AND fe.deleted_at IS NULL
  AND a.drill_attempt_id = fe.drill_attempt_id
  AND a.deleted_at IS NULL
  AND r.drill_result_id = a.drill_result_id
  AND r.practice_drill_id = :practice_drill_id
  AND r.practice_player_id = :practice_player_id
  AND r.deleted_at IS NULL
RETURNING fe.drill_attempt_id
"""

FIND_DUPLICATE_DRILL_SQL: dict[str, str] = {
    TABLE_SHOT: FIND_DUPLICATE_DRILL_SHOT_SQL,
    TABLE_FREE_THROW: FIND_DUPLICATE_DRILL_FREE_THROW_SQL,
}
INSERT_DRILL_SQL: dict[str, str] = {
    TABLE_SHOT: INSERT_DRILL_SHOT_SQL,
    TABLE_FREE_THROW: INSERT_DRILL_FREE_THROW_SQL,
}
DELETE_DRILL_HARD_SQL: dict[str, str] = {
    TABLE_SHOT: DELETE_DRILL_SHOT_SQL,
    TABLE_FREE_THROW: DELETE_DRILL_FREE_THROW_SQL,
}


def _resolve_shooting_attempt(
    session: Session,
    drill_result_id: int,
    owner: dict[str, object],
    player_id: int,
    entry: dict[str, object],
) -> int:
    """
    Description:
        Chooses the attempt a drill shot belongs to (REQ-078, plan §3.7 "Attempts"): a new
        attempt when the command asks for one after the latest attempt's number, or when its
        side differs from the latest attempt's; otherwise the latest attempt (attempt 1 when
        there is none).

    Parameters:
        session: the write session, after lock_drill_owner.
        drill_result_id: the entry player's result for the practice drill.
        owner: the lock_drill_owner row.
        player_id: the entry player.
        entry: the entry fields with "side" and "new_attempt_after" (each may be None).

    Return Values:
        int: the drill_attempt_id to link the entry to.

    Notes:
        Race-free under the practice lock: queued shots sent before the base refresh carry
        the same new_attempt_after, so once the first creates attempt N+1 the rest attach to
        it.
    """
    side = drill_side(owner, entry.get("side"))
    new_after = entry.get("new_attempt_after")
    latest = fetch_one(session, LATEST_LIVE_ATTEMPT_SQL, {"drill_result_id": drill_result_id})
    if latest is not None:
        asked_new = new_after is not None and new_after == latest["attempt_no"]
        if not asked_new and latest["side"] == side:
            return cast(int, latest["drill_attempt_id"])
    created = insert_drill_attempt(
        session,
        drill_result_id,
        owner,
        player_id,
        {"side": side, "value_1": None, "value_2": None, "attempted_at": None},
    )
    return cast(int, created["drill_attempt_id"])


def _insert_drill_entry(
    session: Session,
    practice_drill_id: int,
    player_id: int,
    table: str,
    entry: dict[str, object],
) -> dict[str, object]:
    """
    Description:
        insert_entry for a drill parent (plan §3.7): saves one field goal or free throw of
        the entry player, linked to a drill attempt, idempotently.

    Parameters:
        session: the write session (inside run_write).
        practice_drill_id: the practice drill (entry-parent id).
        player_id: the entry player (the command's player_id).
        table: "shot" or "free_throw".
        entry: as insert_entry, plus "side" and "new_attempt_after".

    Return Values:
        dict[str, object]: {"entry_id", "created_at", "was_duplicate"}.

    Notes:
        One transaction: (1) lock the owner; (2) refuse a version that is not shooting;
        (3) return the existing row of a retried entry; (4) get or create the live result;
        (5) resolve the attempt; (6) insert with practice_session_id, practice_player_id and
        drill_attempt_id. Raises ValueError when the owner is not found, the drill is not a
        shooting drill, or the spot is not active or the shot type not live.
    """
    owner = lock_drill_owner(session, practice_drill_id, player_id)
    if owner["result_type_code"] != RESULT_TYPE_SHOOTING:
        raise ValueError("drill is not a shooting drill")
    params: dict[str, object] = {
        "practice_session_id": owner["practice_session_id"],
        "practice_player_id": owner["practice_player_id"],
        "practice_drill_id": practice_drill_id,
        "player_id": player_id,
        "entry_mode": entry["entry_mode"],
        "makes": entry["makes"],
        "attempts": entry["attempts"],
        "shot_at": entry["shot_at"],
    }
    if table == TABLE_SHOT:
        params.update(
            {
                "court_spot_id": entry.get("court_spot_id"),
                "shot_type_code": entry.get("shot_type_code"),
                "hand": entry.get("hand"),
                "creation": entry.get("creation"),
                "defense": entry.get("defense"),
            }
        )
    duplicate = fetch_one(session, FIND_DUPLICATE_DRILL_SQL[table], params)
    if duplicate is not None:
        logger.warning(
            "Drill entry already saved as %s entry %s; retry not inserted",
            table,
            duplicate["entry_id"],
        )
        return {
            "entry_id": duplicate["entry_id"],
            "created_at": duplicate["created_at"],
            "was_duplicate": True,
        }
    result_id = get_or_create_result(
        session,
        cast(int, owner["practice_session_id"]),
        practice_drill_id,
        cast(int, owner["practice_player_id"]),
        player_id,
    )
    params["drill_attempt_id"] = _resolve_shooting_attempt(
        session, result_id, owner, player_id, entry
    )
    inserted = fetch_one(session, INSERT_DRILL_SQL[table], params)
    if inserted is None:
        raise ValueError("court spot not active or shot type not live")
    return {
        "entry_id": inserted["entry_id"],
        "created_at": inserted["created_at"],
        "was_duplicate": False,
    }


def _delete_drill_entry_hard(
    session: Session, practice_drill_id: int, player_id: int, table: str, entry_id: int
) -> bool:
    """
    Description:
        delete_entry_hard for a drill parent (REQ-010, plan §3.7): hard-deletes one drill
        entry of this visit, then its attempt when that is left empty, then the result when
        that has no attempts and no notes, so undoing everything leaves the drill "not
        started".

    Parameters:
        session: the write session (inside run_write).
        practice_drill_id: the practice drill (entry-parent id).
        player_id: the entry player (the command's player_id).
        table: "shot" or "free_throw".
        entry_id: an id the server returned in an ack during this visit.

    Return Values:
        bool: True when the entry was deleted; False when nothing matched (already gone, or
        not this participant's entry of this practice drill).

    Notes:
        lock_drill_owner first. Only live rows are hard-deleted.
    """
    owner = lock_drill_owner(session, practice_drill_id, player_id)
    deleted = fetch_one(
        session,
        DELETE_DRILL_HARD_SQL[table],
        {
            "entry_id": entry_id,
            "practice_drill_id": practice_drill_id,
            "practice_player_id": owner["practice_player_id"],
        },
    )
    if deleted is None:
        return False
    delete_empty_attempt(session, cast(int, deleted["drill_attempt_id"]))
    return True


def _insert_params(
    owner: dict[str, object], table: str, entry: dict[str, object]
) -> dict[str, object]:
    """
    Description:
        The bound parameters of an entry insert and its duplicate check.

    Parameters:
        owner: _owner_params of the entry.
        table: "shot" or "free_throw".
        entry: the entry fields (see insert_entry).

    Return Values:
        dict[str, object]: owner plus entry_mode, makes, attempts, shot_at; for the shot
        table also court_spot_id, point_value and the four details (None when absent).

    Notes:
        Pure. Free throws carry no spot, point value or details.
    """
    params: dict[str, object] = {
        **owner,
        "entry_mode": entry["entry_mode"],
        "makes": entry["makes"],
        "attempts": entry["attempts"],
        "shot_at": entry["shot_at"],
    }
    if table == TABLE_SHOT:
        params.update(
            {
                "court_spot_id": entry.get("court_spot_id"),
                "point_value": entry.get("point_value"),
                "shot_type_code": entry.get("shot_type_code"),
                "hand": entry.get("hand"),
                "creation": entry.get("creation"),
                "defense": entry.get("defense"),
            }
        )
    return params


def insert_entry(
    session: Session,
    parent: EntryParent,
    player_id: int,
    table: str,
    entry: dict[str, object],
) -> dict[str, object]:
    """
    Description:
        Saves one field-goal or free-throw entry of the entry player, idempotently: a retry
        of an entry already saved returns the existing row instead of a duplicate.

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        parent: the entry parent (practice or game).
        player_id: the entry player: the court command's player_id. Never
            current_player_id() and never a user id (COD-8-01).
        table: "shot" (h_data.shot_entries) or "free_throw" (h_data.free_throw_entries).
        entry: {"entry_mode", "makes", "attempts", "shot_at" (aware datetime, the device's
            tap instant)} plus, for a shot, "court_spot_id" (never spot_number; None for a
            manual entry), "point_value" (2 or 3 for a manual entry, else None) and the
            details "shot_type_code", "hand", "creation", "defense" (each None when unset).

    Return Values:
        dict[str, object]: {"entry_id": int, "created_at": datetime, "was_duplicate": bool}.

    Notes:
        Steps, in the caller's single transaction: lock_entry_owner; look for the same live
        free-form entry of that owner (same table, mode, spot, point value, counts and
        shot_at); else insert with practice_session_id, practice_player_id and player_id
        (practice) or game_id and player_id (game). The parent lock makes two concurrent
        sends of one entry run one after the other, so only one inserts. seq_in_context is
        set by the entry trigger. Mode, count and detail shapes are validated by the
        controller and backed by the table CHECKs. Raises ValueError when the owner is not
        found, or the spot is not active or the shot type not live (nothing inserted). A
        drill parent (entry also carrying "side" and "new_attempt_after") links the entry to
        a drill attempt (_insert_drill_entry, plan §3.7).
    """
    _check_table(table)
    if parent.kind == PARENT_KIND_DRILL:
        return _insert_drill_entry(session, parent.parent_id, player_id, table, entry)
    practice_player_id = lock_entry_owner(session, parent, player_id)
    params = _insert_params(_owner_params(parent, player_id, practice_player_id), table, entry)

    duplicate = fetch_one(session, FIND_DUPLICATE_SQL[table], params)
    if duplicate is not None:
        logger.warning(
            "Entry already saved as %s entry %s; retry not inserted",
            table,
            duplicate["entry_id"],
        )
        return {
            "entry_id": duplicate["entry_id"],
            "created_at": duplicate["created_at"],
            "was_duplicate": True,
        }

    inserted = fetch_one(session, INSERT_SQL[table], params)
    if inserted is None:
        raise ValueError("court spot not active or shot type not live")
    return {
        "entry_id": inserted["entry_id"],
        "created_at": inserted["created_at"],
        "was_duplicate": False,
    }


def delete_entry_hard(
    session: Session, parent: EntryParent, player_id: int, table: str, entry_id: int
) -> bool:
    """
    Description:
        Undoes one entry made during this visit with a hard DELETE (REQ-010, Gate 1 Q7).

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        parent: the entry parent (practice or game).
        player_id: the entry player: the court command's player_id. Never
            current_player_id() and never a user id (COD-8-01).
        table: "shot" or "free_throw".
        entry_id: an id the server returned in an ack during this visit.

    Return Values:
        bool: True if the row was deleted, False if nothing matched: already gone,
        soft-deleted meanwhile, drill-linked, or not that owner's (reported to the user as
        success, so a retried undo is harmless).

    Notes:
        lock_entry_owner first, so a player who is no longer an owner makes the undo fail.
        Only a live row is ever hard-deleted; a soft-deleted one stays recoverable
        (COD-22-02). A drill parent also removes the attempt and result the undo leaves
        empty (_delete_drill_entry_hard, plan §3.7).
    """
    _check_table(table)
    if parent.kind == PARENT_KIND_DRILL:
        return _delete_drill_entry_hard(session, parent.parent_id, player_id, table, entry_id)
    practice_player_id = lock_entry_owner(session, parent, player_id)
    deleted = fetch_one(
        session,
        DELETE_HARD_SQL[table],
        {**_owner_params(parent, player_id, practice_player_id), "entry_id": entry_id},
    )
    return deleted is not None


# --- Entries list and detail (REQ-004) ---

# Both tables in one row shape. The parent filter takes the practice id or the game id (the
# other NULL); removed participant rows, deleted parents and removed profiles are hidden
# (COD-22-03). Names come from h_system.users.display_name.
ENTRY_ROWS_SQL = """
SELECT 'shot' AS "table",
       se.shot_entry_id AS entry_id,
       se.entry_mode,
       cs.spot_number::int AS spot_number,
       cs.spot_name,
       z.label AS zone_label,
       se.manual_point_value::int AS manual_point_value,
       se.makes::int AS makes,
       se.attempts::int AS attempts,
       se.shot_type_code,
       st.label AS shot_type_label,
       se.hand,
       se.creation,
       se.defense,
       se.seq_in_context,
       se.created_at,
       cu.display_name AS created_by_name,
       se.updated_at,
       uu.display_name AS updated_by_name
FROM h_data.shot_entries se
JOIN h_data.players p ON p.player_id = se.player_id
JOIN h_system.users cu ON cu.user_id = se.created_by
JOIN h_system.users uu ON uu.user_id = se.updated_by
LEFT JOIN h_data.practice_sessions ps ON ps.practice_session_id = se.practice_session_id
LEFT JOIN h_data.practice_players pp ON pp.practice_player_id = se.practice_player_id
LEFT JOIN h_data.games g ON g.game_id = se.game_id
LEFT JOIN h_ref.court_spots cs ON cs.court_spot_id = se.court_spot_id
LEFT JOIN h_ref.zones z ON z.zone_code = cs.zone_code
LEFT JOIN h_ref.shot_types st ON st.shot_type_code = se.shot_type_code
WHERE se.player_id = CAST(:player_id AS bigint)
  AND se.practice_session_id IS NOT DISTINCT FROM CAST(:practice_session_id AS bigint)
  AND se.game_id IS NOT DISTINCT FROM CAST(:game_id AS bigint)
  AND se.deleted_at IS NULL
  AND se.drill_attempt_id IS NULL
  AND p.deleted_at IS NULL
  AND (se.practice_session_id IS NULL OR (ps.deleted_at IS NULL AND pp.deleted_at IS NULL))
  AND (se.game_id IS NULL OR g.deleted_at IS NULL)
UNION ALL
SELECT 'free_throw' AS "table",
       fe.free_throw_entry_id AS entry_id,
       fe.entry_mode,
       NULL::int AS spot_number,
       NULL::text AS spot_name,
       NULL::text AS zone_label,
       NULL::int AS manual_point_value,
       fe.makes::int AS makes,
       fe.attempts::int AS attempts,
       NULL::text AS shot_type_code,
       NULL::text AS shot_type_label,
       NULL::text AS hand,
       NULL::text AS creation,
       NULL::text AS defense,
       fe.seq_in_context,
       fe.created_at,
       cu.display_name AS created_by_name,
       fe.updated_at,
       uu.display_name AS updated_by_name
FROM h_data.free_throw_entries fe
JOIN h_data.players p ON p.player_id = fe.player_id
JOIN h_system.users cu ON cu.user_id = fe.created_by
JOIN h_system.users uu ON uu.user_id = fe.updated_by
LEFT JOIN h_data.practice_sessions ps ON ps.practice_session_id = fe.practice_session_id
LEFT JOIN h_data.practice_players pp ON pp.practice_player_id = fe.practice_player_id
LEFT JOIN h_data.games g ON g.game_id = fe.game_id
WHERE fe.player_id = CAST(:player_id AS bigint)
  AND fe.practice_session_id IS NOT DISTINCT FROM CAST(:practice_session_id AS bigint)
  AND fe.game_id IS NOT DISTINCT FROM CAST(:game_id AS bigint)
  AND fe.deleted_at IS NULL
  AND fe.drill_attempt_id IS NULL
  AND p.deleted_at IS NULL
  AND (fe.practice_session_id IS NULL OR (ps.deleted_at IS NULL AND pp.deleted_at IS NULL))
  AND (fe.game_id IS NULL OR g.deleted_at IS NULL)
"""

# Composed from module constants only, never from external values (COD-14-03).
LIST_ENTRIES_SQL = (
    "SELECT e.* FROM ("
    + ENTRY_ROWS_SQL
    + ") e ORDER BY e.created_at DESC, e.entry_id DESC LIMIT :limit"
)

GET_ENTRY_SQL = (
    "SELECT e.* FROM ("
    + ENTRY_ROWS_SQL
    + ') e WHERE e."table" = :table AND e.entry_id = :entry_id'
)


def list_entries(
    session: Session, parent: EntryParent, player_id: int, limit: int
) -> list[dict[str, object]]:
    """
    Description:
        Lists the entry player's live free-form field goals and free throws in an entry
        parent, newest first, for the Entries dialog (plan D12).

    Parameters:
        session: the request session.
        parent: the entry parent (practice or game).
        player_id: the picked player (court-player.value). Never a user id (COD-8-01).
        limit: the most rows to return (ENTRY_LIST_LIMIT).

    Return Values:
        list[dict[str, object]]: per entry {"table", "entry_id", "entry_mode",
        "spot_number", "spot_name", "zone_label", "manual_point_value", "makes",
        "attempts", "shot_type_code", "shot_type_label", "hand", "creation", "defense",
        "seq_in_context", "created_at", "created_by_name", "updated_at",
        "updated_by_name"}, by created_at descending, then entry_id descending.

    Notes:
        Read only, no lock. Empty when the parent, the profile or the participant row is not
        live (COD-22-03).
    """
    return fetch_all(
        session,
        LIST_ENTRIES_SQL,
        {**_parent_params(parent), "player_id": player_id, "limit": limit},
    )


def get_entry(
    session: Session, parent: EntryParent, player_id: int, table: str, entry_id: int
) -> dict[str, object] | None:
    """
    Description:
        Loads one live free-form entry of the entry player in an entry parent, for the entry
        detail dialog (REQ-004).

    Parameters:
        session: the request session.
        parent: the entry parent (practice or game).
        player_id: the picked player (court-player.value). Never a user id (COD-8-01).
        table: "shot" or "free_throw".
        entry_id: the entry's id from the Entries list item.

    Return Values:
        dict[str, object] | None: the row (columns as list_entries), or None when it does
        not exist, is deleted, drill-linked, or not that owner's.

    Notes:
        Read only, same filters as list_entries.
    """
    _check_table(table)
    return fetch_one(
        session,
        GET_ENTRY_SQL,
        {
            **_parent_params(parent),
            "player_id": player_id,
            "table": table,
            "entry_id": entry_id,
        },
    )


# --- Edit and soft delete (REQ-004 to REQ-006; plan D10, D11) ---

# Mode, spot and parent never change; the trigger also refuses a change of parent, sequence,
# player or participant row. A shot type that is neither live nor the row's current value
# makes the UPDATE match no row; keeping a since-retired current type is allowed, so such an
# entry stays editable (review F2). The WHERE clause sees the row's value before the SET.
UPDATE_SHOT_SQL = """
UPDATE h_data.shot_entries
SET makes = CAST(:makes AS smallint),
    attempts = CAST(:attempts AS smallint),
    manual_point_value = CAST(:point_value AS smallint),
    shot_type_code = CAST(:shot_type_code AS text),
    hand = CAST(:hand AS text),
    creation = CAST(:creation AS text),
    defense = CAST(:defense AS text)
WHERE shot_entry_id = :entry_id
  AND practice_session_id IS NOT DISTINCT FROM CAST(:practice_session_id AS bigint)
  AND game_id IS NOT DISTINCT FROM CAST(:game_id AS bigint)
  AND practice_player_id IS NOT DISTINCT FROM CAST(:practice_player_id AS bigint)
  AND player_id = CAST(:player_id AS bigint)
  AND deleted_at IS NULL
  AND drill_attempt_id IS NULL
  AND entry_mode = CAST(:entry_mode AS text)
  AND (CAST(:shot_type_code AS text) IS NULL
       OR shot_entries.shot_type_code = CAST(:shot_type_code AS text)
       OR EXISTS (SELECT 1
                  FROM h_ref.shot_types st
                  WHERE st.shot_type_code = CAST(:shot_type_code AS text)
                    AND st.deleted_at IS NULL))
RETURNING shot_entry_id AS entry_id
"""

UPDATE_FREE_THROW_SQL = """
UPDATE h_data.free_throw_entries
SET makes = CAST(:makes AS smallint),
    attempts = CAST(:attempts AS smallint)
WHERE free_throw_entry_id = :entry_id
  AND practice_session_id IS NOT DISTINCT FROM CAST(:practice_session_id AS bigint)
  AND game_id IS NOT DISTINCT FROM CAST(:game_id AS bigint)
  AND practice_player_id IS NOT DISTINCT FROM CAST(:practice_player_id AS bigint)
  AND player_id = CAST(:player_id AS bigint)
  AND deleted_at IS NULL
  AND drill_attempt_id IS NULL
  AND entry_mode = CAST(:entry_mode AS text)
RETURNING free_throw_entry_id AS entry_id
"""

LOCK_SHOT_SQL = """
SELECT shot_entry_id AS entry_id
FROM h_data.shot_entries
WHERE shot_entry_id = :entry_id
  AND practice_session_id IS NOT DISTINCT FROM CAST(:practice_session_id AS bigint)
  AND game_id IS NOT DISTINCT FROM CAST(:game_id AS bigint)
  AND practice_player_id IS NOT DISTINCT FROM CAST(:practice_player_id AS bigint)
  AND player_id = CAST(:player_id AS bigint)
  AND deleted_at IS NULL
  AND drill_attempt_id IS NULL
FOR UPDATE
"""

LOCK_FREE_THROW_SQL = """
SELECT free_throw_entry_id AS entry_id
FROM h_data.free_throw_entries
WHERE free_throw_entry_id = :entry_id
  AND practice_session_id IS NOT DISTINCT FROM CAST(:practice_session_id AS bigint)
  AND game_id IS NOT DISTINCT FROM CAST(:game_id AS bigint)
  AND practice_player_id IS NOT DISTINCT FROM CAST(:practice_player_id AS bigint)
  AND player_id = CAST(:player_id AS bigint)
  AND deleted_at IS NULL
  AND drill_attempt_id IS NULL
FOR UPDATE
"""

SOFT_DELETE_ENTRY_SQL = """
SELECT h_data.fn_soft_delete(CAST(:entity AS text), CAST(:entry_id AS bigint)) AS rows_marked
"""

UPDATE_SQL: dict[str, str] = {
    TABLE_SHOT: UPDATE_SHOT_SQL,
    TABLE_FREE_THROW: UPDATE_FREE_THROW_SQL,
}
LOCK_ENTRY_SQL: dict[str, str] = {
    TABLE_SHOT: LOCK_SHOT_SQL,
    TABLE_FREE_THROW: LOCK_FREE_THROW_SQL,
}


def update_entry(
    session: Session,
    parent: EntryParent,
    player_id: int,
    table: str,
    entry_id: int,
    changes: dict[str, object],
) -> bool:
    """
    Description:
        Edits one live free-form entry of the entry player from the entry detail dialog
        (REQ-004, plan D10): counts, Make/Miss, point value and shot details.

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        parent: the entry parent (practice or game).
        player_id: the picked player (court-player.value). Never a user id (COD-8-01).
        table: "shot" or "free_throw".
        entry_id: the entry's id (entry-detail-target).
        changes: the complete new values, built by the controller's parse_entry_edit:
            "entry_mode" (the expected, unchanged mode), "makes", "attempts" (ignored and 1
            for a single entry), and for a shot "point_value" (manual only, else None) and
            "shot_type_code", "hand", "creation", "defense" (None clears).

    Return Values:
        bool: True when the row was updated; False when no row matched (gone, deleted,
        another mode, not the owner's, or a shot type that is neither live nor the entry's
        current shot type).

    Notes:
        lock_entry_owner first. Mode, spot and parent never change (plan D10). The audit
        trigger stamps updated_by and updated_at. A shot type retired after the entry was
        saved may be kept unchanged, so the entry stays editable (review F2); it cannot be
        newly chosen.
    """
    _check_table(table)
    practice_player_id = lock_entry_owner(session, parent, player_id)
    entry_mode = changes["entry_mode"]
    attempts = (
        SINGLE_ENTRY_ATTEMPTS if entry_mode == ENTRY_MODE_SINGLE else changes.get("attempts")
    )
    params: dict[str, object] = {
        **_owner_params(parent, player_id, practice_player_id),
        "entry_id": entry_id,
        "entry_mode": entry_mode,
        "makes": changes.get("makes"),
        "attempts": attempts,
    }
    if table == TABLE_SHOT:
        params.update(
            {
                "point_value": changes.get("point_value"),
                "shot_type_code": changes.get("shot_type_code"),
                "hand": changes.get("hand"),
                "creation": changes.get("creation"),
                "defense": changes.get("defense"),
            }
        )
    updated = fetch_one(session, UPDATE_SQL[table], params)
    return updated is not None


def soft_delete_entry(
    session: Session, parent: EntryParent, player_id: int, table: str, entry_id: int
) -> int:
    """
    Description:
        Deletes one live free-form entry of the entry player through h_data.fn_soft_delete,
        so it is hidden and recoverable for 30 days (REQ-006, plan D11).

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        parent: the entry parent (practice or game).
        player_id: the picked player (court-player.value). Never a user id (COD-8-01).
        table: "shot" or "free_throw".
        entry_id: the entry's id (entry-detail-target).

    Return Values:
        int: the number of rows marked (1).

    Notes:
        fn_soft_delete checks no owner, so the entry is first locked FOR UPDATE as a live
        free-form row of that owner and parent; none raises ValueError ("entry not found").
        Lock order: parent row (lock_entry_owner), then the entry row. The confirmation
        (REQ-005) is the caller's.
    """
    _check_table(table)
    practice_player_id = lock_entry_owner(session, parent, player_id)
    locked = fetch_one(
        session,
        LOCK_ENTRY_SQL[table],
        {**_owner_params(parent, player_id, practice_player_id), "entry_id": entry_id},
    )
    if locked is None:
        raise ValueError("entry not found")
    marked = fetch_one(
        session,
        SOFT_DELETE_ENTRY_SQL,
        {"entity": SOFT_DELETE_ENTITY[table], "entry_id": entry_id},
    )
    if marked is None:
        logger.error("Entry soft delete returned no row")
        raise RuntimeError("Entry soft delete returned no row")
    return cast(int, marked["rows_marked"])
