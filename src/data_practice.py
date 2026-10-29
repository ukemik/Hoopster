"""Data access for practice types, practices and their participants, and for the drills of a
practice: starting from a template, the overview, placing sets and ad-hoc drills, removing
and swapping them (practice_drills 2b).

REQ-025, REQ-051, REQ-053, REQ-072, REQ-074, REQ-075, REQ-081, REQ-082. Court spots, shot
types and the shot and free-throw entries of a practice live in src/data_entry.py, shared
with games; drill results live in src/data_drill_results.py. Every function
takes the request session first and runs its SQL through data_handler. Write functions never
commit: they run inside app_controller.run_write, after the acting user has been set
(COD-6-01, COD-7-01).

A practice has no player of its own; its players are its participant rows in
h_data.practice_players. "Participant row" below means the live practice_players row for a
practice and a player. Every practice record carries that row's practice_player_id, which the
server always resolves in SQL from (practice, player) and never accepts from the client
(Amendment A3, COD-8-01). Rows of a removed participant are never read again, even after the
same player is added back (COD-22-02, COD-22-03).
"""

import logging
from datetime import date
from typing import cast

from sqlalchemy.orm import Session

from src.data_handler import fetch_all, fetch_one

logger = logging.getLogger(__name__)


# --- Practice types and practices (REQ-025) ---

LIST_PRACTICE_TYPES_SQL = """
SELECT practice_type_code, label
FROM h_ref.practice_types
WHERE deleted_at IS NULL
ORDER BY sort_order
"""

INSERT_PRACTICE_SESSION_SQL = """
INSERT INTO h_data.practice_sessions
    (session_date, practice_type_code, duration_minutes, location, notes)
VALUES (:session_date, :practice_type_code,
        CAST(:duration_minutes AS smallint), CAST(:location AS text), CAST(:notes AS text))
RETURNING practice_session_id
"""

GET_PRACTICE_SQL = """
SELECT ps.practice_session_id,
       ps.session_date,
       ps.practice_type_code,
       pt.label AS practice_type_label
FROM h_data.practice_sessions ps
JOIN h_ref.practice_types pt ON pt.practice_type_code = ps.practice_type_code
WHERE ps.practice_session_id = :practice_session_id
  AND ps.deleted_at IS NULL
"""


def list_practice_types(session: Session) -> list[dict[str, object]]:
    """
    Description:
        Lists the live practice types in display order, for the New practice form and its
        validation.

    Parameters:
        session: the request session.

    Return Values:
        list[dict[str, object]]: {"practice_type_code", "label"} per type.

    Notes:
        None.
    """
    return fetch_all(session, LIST_PRACTICE_TYPES_SQL)


def insert_practice_session(
    session: Session,
    session_date: date,
    practice_type_code: str,
    *,
    duration_minutes: int | None = None,
    location: str | None = None,
    notes: str | None = None,
) -> int:
    """
    Description:
        Creates a practice with its optional details (REQ-025). Its players are added
        separately as participant rows (insert_practice_players), in the same transaction.

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        session_date: the calendar date chosen on the device, stored as entered (REQ-055).
        practice_type_code: an h_ref.practice_types code, validated by the caller.
        duration_minutes: 1-600 minutes, or None (validated by the caller).
        location: up to 100 characters, or None.
        notes: up to 4000 characters, or None.

    Return Values:
        int: the new h_data.practice_sessions.practice_session_id.

    Notes:
        A practice has no player and no season of its own: each participant row gets its
        player's season from trg_practice_players_season (REQ-018). entry_status defaults
        to in_progress and is not used (D18). The table CHECKs back the detail limits.
    """
    row = fetch_one(
        session,
        INSERT_PRACTICE_SESSION_SQL,
        {
            "session_date": session_date,
            "practice_type_code": practice_type_code,
            "duration_minutes": duration_minutes,
            "location": location,
            "notes": notes,
        },
    )
    if row is None:
        logger.error("Practice insert returned no id")
        raise RuntimeError("Practice insert returned no id")
    return cast(int, row["practice_session_id"])


def get_practice(session: Session, practice_session_id: int) -> dict[str, object] | None:
    """
    Description:
        Loads a live practice with its type label.

    Parameters:
        session: the request session.
        practice_session_id: the id from the court page URL.

    Return Values:
        dict[str, object] | None: {"practice_session_id", "session_date",
        "practice_type_code", "practice_type_label"}, or None when the practice does not
        exist or is deleted.

    Notes:
        No player filter: a practice is shared by its participants. Every write re-checks in
        SQL that the command's player is a live participant of the practice.
    """
    return fetch_one(session, GET_PRACTICE_SQL, {"practice_session_id": practice_session_id})


# --- Practice participants (REQ-074, Amendments A2 and A3) ---

# Only live profiles are inserted; array_position keeps the order the players were chosen in,
# so the participant rows (and the court picker) follow it.
INSERT_PRACTICE_PLAYERS_SQL = """
INSERT INTO h_data.practice_players (practice_session_id, player_id)
SELECT CAST(:practice_session_id AS bigint), p.player_id
FROM h_data.players p
WHERE p.player_id = ANY (CAST(:player_ids AS bigint[]))
  AND p.deleted_at IS NULL
ORDER BY array_position(CAST(:player_ids AS bigint[]), p.player_id)
RETURNING player_id
"""

LIST_PRACTICE_PARTICIPANTS_SQL = """
SELECT pp.player_id, p.display_name
FROM h_data.practice_players pp
JOIN h_data.players p ON p.player_id = pp.player_id
JOIN h_data.practice_sessions ps ON ps.practice_session_id = pp.practice_session_id
WHERE pp.practice_session_id = :practice_session_id
  AND pp.deleted_at IS NULL
  AND p.deleted_at IS NULL
  AND ps.deleted_at IS NULL
ORDER BY pp.practice_player_id
"""

LIST_ADDABLE_PLAYERS_SQL = """
SELECT p.player_id, p.display_name
FROM h_data.players p
WHERE p.deleted_at IS NULL
  AND NOT EXISTS (
      SELECT 1
      FROM h_data.practice_players pp
      WHERE pp.practice_session_id = :practice_session_id
        AND pp.player_id = p.player_id
        AND pp.deleted_at IS NULL)
ORDER BY lower(p.display_name), p.player_id
"""

COUNT_PRACTICE_PLAYER_RECORDS_SQL = """
SELECT h_data.fn_practice_player_record_count(
    CAST(:practice_session_id AS bigint),
    CAST(:player_id AS bigint)) AS record_count
"""

ADD_PRACTICE_PLAYER_SQL = """
SELECT h_data.fn_add_practice_player(
    CAST(:practice_session_id AS bigint),
    CAST(:player_id AS bigint)) AS practice_player_id
"""

REMOVE_PRACTICE_PLAYER_SQL = """
SELECT h_data.fn_remove_practice_player(
    CAST(:practice_session_id AS bigint),
    CAST(:player_id AS bigint)) AS rows_marked
"""


def insert_practice_players(
    session: Session, practice_session_id: int, player_ids: list[int]
) -> int:
    """
    Description:
        Adds the chosen players to a new practice, one participant row each, in the order
        they were chosen.

    Parameters:
        session: the write session (inside run_write, the same transaction as
            insert_practice_session; acting user = current_user.user_id).
        practice_session_id: the practice just created by insert_practice_session.
        player_ids: the participants: practice-new-players.value parsed by
            parse_player_values, distinct h_data.players.player_id values. Never user ids
            (COD-8-01).

    Return Values:
        int: the number of participant rows inserted (= len(player_ids)).

    Notes:
        Raises ValueError, so the caller's transaction rolls back and no practice is
        created, when player_ids is empty or when fewer rows were inserted than ids were
        given (an id that is not a live player, or a repeated id). Seasons are assigned by
        trg_practice_players_season.
    """
    if not player_ids:
        raise ValueError("a practice needs at least one player")
    rows = fetch_all(
        session,
        INSERT_PRACTICE_PLAYERS_SQL,
        {"practice_session_id": practice_session_id, "player_ids": player_ids},
    )
    if len(rows) < len(player_ids):
        raise ValueError("not every chosen player is a live player")
    return len(rows)


def list_practice_participants(
    session: Session, practice_session_id: int
) -> list[dict[str, object]]:
    """
    Description:
        Lists the players taking part in a practice: its live participant rows whose
        profile is live, in the order they were added.

    Parameters:
        session: the request session.
        practice_session_id: a live practice (from the URL, checked by get_practice).

    Return Values:
        list[dict[str, object]]: {"player_id", "display_name"} per participant, ordered by
        practice_player_id. Empty when the practice has no live participant (e.g. every
        participant's profile was removed) or is deleted.

    Notes:
        Removed participant rows never appear, also when the same player has a newer live
        row (Amendment A3).
    """
    return fetch_all(
        session, LIST_PRACTICE_PARTICIPANTS_SQL, {"practice_session_id": practice_session_id}
    )


def list_addable_players(
    session: Session, practice_session_id: int
) -> list[dict[str, object]]:
    """
    Description:
        Lists the live players who can be added to a practice: those with no live
        participant row in it. A player removed from the practice earlier is addable again.

    Parameters:
        session: the request session.
        practice_session_id: the entry-parent practice id.

    Return Values:
        list[dict[str, object]]: {"player_id", "display_name"} per player, ordered by
        lower(display_name), then player_id.

    Notes:
        Read-only. h_data.fn_add_practice_player re-checks under a lock.
    """
    return fetch_all(
        session, LIST_ADDABLE_PLAYERS_SQL, {"practice_session_id": practice_session_id}
    )


def count_practice_player_records(
    session: Session, practice_session_id: int, player_id: int
) -> int:
    """
    Description:
        Counts what a player has recorded in a practice: the live shots, free throws and
        drill attempts of the player's live participant row
        (h_data.fn_practice_player_record_count).

    Parameters:
        session: the request session.
        practice_session_id: the entry-parent practice id.
        player_id: the player to remove (court-player.value). Never a user id (COD-8-01).

    Return Values:
        int: the count; 0 when nothing is recorded or the player has no live participant
        row.

    Notes:
        For the "cannot be removed" message only; h_data.fn_remove_practice_player re-checks
        under a lock.
    """
    row = fetch_one(
        session,
        COUNT_PRACTICE_PLAYER_RECORDS_SQL,
        {"practice_session_id": practice_session_id, "player_id": player_id},
    )
    if row is None:
        logger.error("Practice player record count returned no row")
        raise RuntimeError("Practice player record count returned no row")
    return cast(int, row["record_count"])


def add_practice_player(session: Session, practice_session_id: int, player_id: int) -> int:
    """
    Description:
        Adds a player to an existing practice through h_data.fn_add_practice_player, which
        always inserts a new participant row (never restores a removed one), so the player
        starts with nothing recorded (Amendments A2 and A3).

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        practice_session_id: the entry-parent practice id.
        player_id: the player to add (court-add-player-select.value as int). Never a user
            id (COD-8-01).

    Return Values:
        int: the new h_data.practice_players.practice_player_id.

    Notes:
        The function raises, rolling back the caller's transaction, when the practice is
        not live, the profile is removed, or the player already has a live participant row.
    """
    row = fetch_one(
        session,
        ADD_PRACTICE_PLAYER_SQL,
        {"practice_session_id": practice_session_id, "player_id": player_id},
    )
    if row is None:
        logger.error("Add practice player returned no row")
        raise RuntimeError("Add practice player returned no row")
    return cast(int, row["practice_player_id"])


def remove_practice_player(session: Session, practice_session_id: int, player_id: int) -> int:
    """
    Description:
        Removes a player who has nothing recorded from a practice through
        h_data.fn_remove_practice_player, which soft-deletes the live participant row and
        everything carrying its practice_player_id with one deleted_at.

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        practice_session_id: the entry-parent practice id.
        player_id: the player to remove (court-player.value as int). Never a user id
            (COD-8-01).

    Return Values:
        int: the number of rows marked.

    Notes:
        The function raises, rolling back the caller's transaction, when the player has no
        live participant row, has records, or is the last live player. A removed
        participant row is never restored (Amendment A3).
    """
    row = fetch_one(
        session,
        REMOVE_PRACTICE_PLAYER_SQL,
        {"practice_session_id": practice_session_id, "player_id": player_id},
    )
    if row is None:
        logger.error("Remove practice player returned no row")
        raise RuntimeError("Remove practice player returned no row")
    return cast(int, row["rows_marked"])


# --- Drills of a practice: template start and overview (practice_drills 2b; REQ-025, REQ-072) ---

# Practice drill sources that may be swapped (h_data.fn_swap_practice_drill).
SWAPPABLE_SOURCES: frozenset[str] = frozenset({"drawn", "swapped"})
# h_data.fn_soft_delete entity names used here (its whitelist; never built from input).
ENTITY_PRACTICE_DRILL = "practice_drill"
ENTITY_PRACTICE_DRILL_SET = "practice_drill_set"

START_FROM_TEMPLATE_SQL = """
SELECT template_position, drill_set_id, set_name, practice_drill_set_id,
       line_no, requested, drawn, notice
FROM h_data.fn_add_template_to_practice(
    CAST(:practice_session_id AS bigint), CAST(:practice_template_id AS bigint))
"""

GET_PRACTICE_OVERVIEW_SQL = """
SELECT ps.practice_session_id,
       ps.session_date,
       ps.practice_type_code,
       pt.label AS practice_type_label,
       ps.duration_minutes,
       ps.location,
       ps.notes,
       ps.practice_template_version_id,
       tv.version_no AS template_version_no,
       t.template_name
FROM h_data.practice_sessions ps
JOIN h_ref.practice_types pt ON pt.practice_type_code = ps.practice_type_code
LEFT JOIN h_data.practice_template_versions tv
       ON tv.practice_template_version_id = ps.practice_template_version_id
LEFT JOIN h_data.practice_templates t ON t.practice_template_id = tv.practice_template_id
WHERE ps.practice_session_id = :practice_session_id
  AND ps.deleted_at IS NULL
"""

UPDATE_PRACTICE_DETAILS_SQL = """
UPDATE h_data.practice_sessions
SET duration_minutes = CAST(:duration_minutes AS smallint),
    location = CAST(:location AS text),
    notes = CAST(:notes AS text)
WHERE practice_session_id = :practice_session_id
  AND deleted_at IS NULL
RETURNING practice_session_id
"""

# Live placements of a live practice; the set name comes from the lineage in any state, so a
# retired set still shows in the practices that used it (D10).
LIST_PLACED_SETS_SQL = """
SELECT pds.practice_drill_set_id,
       pds.practice_position,
       pds.draw_notice,
       sv.drill_set_id,
       sv.version_no,
       sv.set_kind,
       s.set_name
FROM h_data.practice_drill_sets pds
JOIN h_data.practice_sessions ps ON ps.practice_session_id = pds.practice_session_id
JOIN h_data.drill_set_versions sv ON sv.drill_set_version_id = pds.drill_set_version_id
JOIN h_data.drill_sets s ON s.drill_set_id = sv.drill_set_id
WHERE pds.practice_session_id = :practice_session_id
  AND pds.deleted_at IS NULL
  AND ps.deleted_at IS NULL
ORDER BY pds.practice_position, pds.practice_drill_set_id
"""

# Run order (plan §3.6): the placement's position (or the ad-hoc drill's own), then the
# position in the set, then the id. A drill of a removed placement is hidden with it
# (COD-22-03). players_done counts live results with at least one live attempt.
LIST_PRACTICE_DRILLS_SQL = """
SELECT pd.practice_drill_id,
       pd.practice_drill_set_id,
       pd.source,
       pd.set_position,
       COALESCE(pds.practice_position, pd.practice_position) AS run_position,
       pd.drill_version_id,
       dv.drill_id,
       dv.version_no,
       dv.result_type_code,
       d.drill_name,
       rt.label AS result_type_label,
       rt.capture_kind,
       (SELECT count(*)::integer
        FROM h_analytics.v_drill_results vr
        WHERE vr.practice_drill_id = pd.practice_drill_id
          AND vr.attempt_count > 0) AS players_done
FROM h_data.practice_drills pd
JOIN h_data.practice_sessions ps ON ps.practice_session_id = pd.practice_session_id
LEFT JOIN h_data.practice_drill_sets pds ON pds.practice_drill_set_id = pd.practice_drill_set_id
JOIN h_data.drill_versions dv ON dv.drill_version_id = pd.drill_version_id
JOIN h_data.drills d ON d.drill_id = dv.drill_id
JOIN h_ref.result_types rt ON rt.result_type_code = dv.result_type_code
WHERE pd.practice_session_id = :practice_session_id
  AND pd.deleted_at IS NULL
  AND ps.deleted_at IS NULL
  AND (pd.practice_drill_set_id IS NULL OR pds.deleted_at IS NULL)
ORDER BY COALESCE(pds.practice_position, pd.practice_position),
         pd.set_position NULLS FIRST,
         pd.practice_drill_id
"""

# Current sets not already placed live in this practice (D14).
LIST_SELECTABLE_SETS_SQL = """
SELECT cs.drill_set_id, cs.set_name, cs.set_kind, cs.version_no
FROM h_data.v_current_drill_sets cs
WHERE NOT EXISTS (
    SELECT 1
    FROM h_data.practice_drill_sets pds
    JOIN h_data.drill_set_versions sv ON sv.drill_set_version_id = pds.drill_set_version_id
    WHERE pds.practice_session_id = :practice_session_id
      AND pds.deleted_at IS NULL
      AND sv.drill_set_id = cs.drill_set_id)
ORDER BY lower(cs.set_name), cs.drill_set_id
"""

LIST_SELECTABLE_DRILLS_SQL = """
SELECT cd.drill_id, cd.drill_name, cd.version_no, rt.label AS result_type_label
FROM h_data.v_current_drills cd
JOIN h_ref.result_types rt ON rt.result_type_code = cd.result_type_code
ORDER BY lower(cd.drill_name), cd.drill_id
"""

LIST_SELECTABLE_TEMPLATES_SQL = """
SELECT practice_template_id, template_name, version_no, set_count
FROM h_data.v_current_practice_templates
ORDER BY lower(template_name), practice_template_id
"""

# The practice row lock taken before every placement, removal and swap, so they run one after
# the other and after any entry write (which locks the same row first).
LOCK_LIVE_PRACTICE_SQL = """
SELECT practice_session_id
FROM h_data.practice_sessions
WHERE practice_session_id = :practice_session_id
  AND deleted_at IS NULL
FOR UPDATE
"""

SET_PLACED_SQL = """
SELECT EXISTS (
    SELECT 1
    FROM h_data.practice_drill_sets pds
    JOIN h_data.drill_set_versions sv ON sv.drill_set_version_id = pds.drill_set_version_id
    WHERE pds.practice_session_id = :practice_session_id
      AND pds.deleted_at IS NULL
      AND sv.drill_set_id = :drill_set_id) AS placed
"""

ADD_SET_TO_PRACTICE_SQL = """
SELECT practice_drill_set_id, line_no, requested, drawn, notice
FROM h_data.fn_add_drill_set_to_practice(
    CAST(:practice_session_id AS bigint), CAST(:drill_set_id AS bigint))
"""

# practice_position = the highest live position of sets and ad-hoc drills + 1, the rule of
# h_data.fn_add_drill_set_to_practice; the version is the drill's current one.
ADD_ADHOC_DRILL_SQL = """
INSERT INTO h_data.practice_drills
    (practice_session_id, practice_position, drill_version_id, source)
SELECT CAST(:practice_session_id AS bigint),
       (COALESCE(GREATEST(
            (SELECT max(pds.practice_position) FROM h_data.practice_drill_sets pds
             WHERE pds.practice_session_id = CAST(:practice_session_id AS bigint)
               AND pds.deleted_at IS NULL),
            (SELECT max(pd.practice_position) FROM h_data.practice_drills pd
             WHERE pd.practice_session_id = CAST(:practice_session_id AS bigint)
               AND pd.deleted_at IS NULL)), 0) + 1)::smallint,
       cd.drill_version_id,
       'ad_hoc'
FROM h_data.v_current_drills cd
WHERE cd.drill_id = :drill_id
RETURNING practice_drill_id
"""

COUNT_LIVE_RESULTS_SQL = """
SELECT count(*)::integer AS result_count
FROM h_analytics.v_drill_results vr
JOIN h_data.practice_drills pd ON pd.practice_drill_id = vr.practice_drill_id
WHERE vr.practice_session_id = :practice_session_id
  AND (CAST(:practice_drill_id AS bigint) IS NULL
       OR pd.practice_drill_id = CAST(:practice_drill_id AS bigint))
  AND (CAST(:practice_drill_set_id AS bigint) IS NULL
       OR pd.practice_drill_set_id = CAST(:practice_drill_set_id AS bigint))
"""

LOCK_PRACTICE_DRILL_SQL = """
SELECT pd.practice_drill_id, pd.source
FROM h_data.practice_drills pd
LEFT JOIN h_data.practice_drill_sets pds ON pds.practice_drill_set_id = pd.practice_drill_set_id
WHERE pd.practice_drill_id = :practice_drill_id
  AND pd.practice_session_id = :practice_session_id
  AND pd.deleted_at IS NULL
  AND (pd.practice_drill_set_id IS NULL OR pds.deleted_at IS NULL)
FOR UPDATE OF pd
"""

LOCK_PLACED_SET_SQL = """
SELECT practice_drill_set_id
FROM h_data.practice_drill_sets
WHERE practice_drill_set_id = :practice_drill_set_id
  AND practice_session_id = :practice_session_id
  AND deleted_at IS NULL
FOR UPDATE
"""

SOFT_DELETE_PRACTICE_ITEM_SQL = """
SELECT h_data.fn_soft_delete(CAST(:entity AS text), CAST(:item_id AS bigint)) AS rows_marked
"""

SWAP_PRACTICE_DRILL_SQL = """
SELECT practice_drill_id, old_drill_version_id, new_drill_version_id, results_removed, notice
FROM h_data.fn_swap_practice_drill(CAST(:practice_drill_id AS bigint))
"""


def start_practice_from_template(
    session: Session, practice_session_id: int, practice_template_id: int
) -> list[dict[str, object]]:
    """
    Description:
        Starts a practice from a template (REQ-072): records the template's latest version on
        the practice and places its current sets in order (h_data.fn_add_template_to_practice).

    Parameters:
        session: the write session (inside run_write_with_reason, the same transaction that
            created the practice).
        practice_session_id: the practice just created.
        practice_template_id: the template LINEAGE id (practice-new-template.value). Never a
            version id (COD-8-01).

    Return Values:
        list[dict[str, object]]: one row per placed line or skipped set: {"template_position",
        "drill_set_id", "set_name", "practice_drill_set_id", "line_no", "requested",
        "drawn", "notice"}.

    Notes:
        The function raises (P0001, shown to the user) when the template is no longer
        current, e.g. retired after the form opened. Notices are also kept on the placements
        (draw_notice) and shown on the overview.
    """
    return fetch_all(
        session,
        START_FROM_TEMPLATE_SQL,
        {
            "practice_session_id": practice_session_id,
            "practice_template_id": practice_template_id,
        },
    )


def get_practice_overview(
    session: Session, practice_session_id: int
) -> dict[str, object] | None:
    """
    Description:
        Loads a live practice for its overview: type, date, details and the template version
        it was started from.

    Parameters:
        session: the request session.
        practice_session_id: the practice id from the URL.

    Return Values:
        dict[str, object] | None: {"practice_session_id", "session_date",
        "practice_type_code", "practice_type_label", "duration_minutes", "location",
        "notes", "practice_template_version_id", "template_version_no", "template_name"}
        (the template columns None when started without one); None when the practice does
        not exist or is deleted.

    Notes:
        The template name is read in any state, so a retired template still shows (D10).
    """
    return fetch_one(
        session, GET_PRACTICE_OVERVIEW_SQL, {"practice_session_id": practice_session_id}
    )


def update_practice_details(
    session: Session,
    practice_session_id: int,
    duration_minutes: int | None,
    location: str | None,
    notes: str | None,
) -> bool:
    """
    Description:
        Saves the details of a live practice from its overview (REQ-025).

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        practice_session_id: the practice from the ov-context store.
        duration_minutes: 1-600, or None.
        location: up to 100 characters, or None.
        notes: up to 4000 characters, or None.

    Return Values:
        bool: True when saved; False when the practice is no longer live.

    Notes:
        The controller validates the limits; the table CHECKs back them.
    """
    row = fetch_one(
        session,
        UPDATE_PRACTICE_DETAILS_SQL,
        {
            "practice_session_id": practice_session_id,
            "duration_minutes": duration_minutes,
            "location": location,
            "notes": notes,
        },
    )
    return row is not None


def list_placed_sets(session: Session, practice_session_id: int) -> list[dict[str, object]]:
    """
    Description:
        Lists the live drill sets placed in a live practice, in practice order.

    Parameters:
        session: the request session.
        practice_session_id: the practice id (URL or ov-context).

    Return Values:
        list[dict[str, object]]: {"practice_drill_set_id", "practice_position",
        "draw_notice", "drill_set_id", "version_no", "set_kind", "set_name"} per placement.

    Notes:
        A placement without drills (a short draw) is listed too; its notice says why.
    """
    return fetch_all(
        session, LIST_PLACED_SETS_SQL, {"practice_session_id": practice_session_id}
    )


def list_practice_drills(session: Session, practice_session_id: int) -> list[dict[str, object]]:
    """
    Description:
        Lists the live drills of a live practice in run order, with their set, result type
        and how many players have a result.

    Parameters:
        session: the request session.
        practice_session_id: the practice id (URL or ov-context).

    Return Values:
        list[dict[str, object]]: {"practice_drill_id", "practice_drill_set_id" (None for an
        ad-hoc drill), "source", "set_position", "run_position", "drill_version_id",
        "drill_id", "version_no", "result_type_code", "drill_name", "result_type_label",
        "capture_kind", "players_done"} per drill.

    Notes:
        Drills of a removed placement are hidden with it (COD-22-03). Retired drills stay
        listed (D10).
    """
    return fetch_all(
        session, LIST_PRACTICE_DRILLS_SQL, {"practice_session_id": practice_session_id}
    )


def list_selectable_sets(session: Session, practice_session_id: int) -> list[dict[str, object]]:
    """
    Description:
        Lists the current drill sets that are not already placed live in the practice, for
        the overview's Add drill set (D14).

    Parameters:
        session: the request session.
        practice_session_id: the practice id.

    Return Values:
        list[dict[str, object]]: {"drill_set_id", "set_name", "set_kind", "version_no"} per
        set, by name.

    Notes:
        add_set_to_practice re-checks under the practice lock.
    """
    return fetch_all(
        session, LIST_SELECTABLE_SETS_SQL, {"practice_session_id": practice_session_id}
    )


def list_selectable_drills(session: Session) -> list[dict[str, object]]:
    """
    Description:
        Lists the current drills (live, not retired) for the overview's Add drill.

    Parameters:
        session: the request session.

    Return Values:
        list[dict[str, object]]: {"drill_id", "drill_name", "version_no",
        "result_type_label"} per drill, by name.

    Notes:
        None.
    """
    return fetch_all(session, LIST_SELECTABLE_DRILLS_SQL)


def list_selectable_templates(session: Session) -> list[dict[str, object]]:
    """
    Description:
        Lists the current practice templates for the New practice form.

    Parameters:
        session: the request session.

    Return Values:
        list[dict[str, object]]: {"practice_template_id", "template_name", "version_no",
        "set_count"} per template, by name.

    Notes:
        None.
    """
    return fetch_all(session, LIST_SELECTABLE_TEMPLATES_SQL)


def _lock_live_practice(session: Session, practice_session_id: int) -> None:
    """
    Description:
        Locks a live practice row FOR UPDATE, the first statement of every placement,
        removal and swap.

    Parameters:
        session: the write session.
        practice_session_id: the practice id.

    Return Values:
        None. Raises LookupError when the practice is not live.

    Notes:
        Entry writes lock the same row first (data_entry.lock_entry_owner), so the two never
        interleave.
    """
    if fetch_one(
        session, LOCK_LIVE_PRACTICE_SQL, {"practice_session_id": practice_session_id}
    ) is None:
        raise LookupError(f"practice {practice_session_id} is not live")


def add_set_to_practice(
    session: Session, practice_session_id: int, drill_set_id: int
) -> list[dict[str, object]] | None:
    """
    Description:
        Places the current version of a drill set at the end of a practice (REQ-051,
        REQ-053), unless that set is already placed live in it (D14).

    Parameters:
        session: the write session (inside run_write_with_reason).
        practice_session_id: the practice from the ov-context store.
        drill_set_id: the set LINEAGE id (ov-add-set.value). Never a version or placement
            id (COD-8-01).

    Return Values:
        list[dict[str, object]] | None: the rows of h_data.fn_add_drill_set_to_practice
        ({"practice_drill_set_id", "line_no", "requested", "drawn", "notice"}), or None when
        the set is already placed (nothing written).

    Notes:
        The practice row is locked before the check, so two devices adding at once are
        serialised (Risk R4). The function raises (P0001) for a set that is no longer
        current or has no drills or lines.
    """
    _lock_live_practice(session, practice_session_id)
    placed = fetch_one(
        session,
        SET_PLACED_SQL,
        {"practice_session_id": practice_session_id, "drill_set_id": drill_set_id},
    )
    if placed is not None and placed["placed"]:
        return None
    return fetch_all(
        session,
        ADD_SET_TO_PRACTICE_SQL,
        {"practice_session_id": practice_session_id, "drill_set_id": drill_set_id},
    )


def add_adhoc_drill(session: Session, practice_session_id: int, drill_id: int) -> int:
    """
    Description:
        Adds a single (ad-hoc) drill at the end of a practice with the drill's current
        version (REQ-051).

    Parameters:
        session: the write session (inside run_write_with_reason).
        practice_session_id: the practice from the ov-context store.
        drill_id: the drill LINEAGE id (ov-add-drill.value). Never a practice_drill_id
            (COD-8-01).

    Return Values:
        int: the new h_data.practice_drills.practice_drill_id.

    Notes:
        Locks the practice first. Raises LookupError when the practice is not live or the
        drill is no longer current (nothing inserted).
    """
    _lock_live_practice(session, practice_session_id)
    row = fetch_one(
        session,
        ADD_ADHOC_DRILL_SQL,
        {"practice_session_id": practice_session_id, "drill_id": drill_id},
    )
    if row is None:
        raise LookupError(f"drill {drill_id} is not current")
    return cast(int, row["practice_drill_id"])


def count_live_results(
    session: Session,
    practice_session_id: int,
    *,
    practice_drill_id: int | None = None,
    practice_drill_set_id: int | None = None,
) -> int:
    """
    Description:
        Counts the live results of a practice drill, or of every drill of a placed set, for
        the removal and swap warnings (REQ-005, REQ-082).

    Parameters:
        session: the request session.
        practice_session_id: the practice id.
        practice_drill_id: the practice drill, or None.
        practice_drill_set_id: the placed set, or None.

    Return Values:
        int: the number of live results (h_analytics.v_drill_results).

    Notes:
        Give one of the two keyword ids; with neither, every result of the practice counts.
    """
    row = fetch_one(
        session,
        COUNT_LIVE_RESULTS_SQL,
        {
            "practice_session_id": practice_session_id,
            "practice_drill_id": practice_drill_id,
            "practice_drill_set_id": practice_drill_set_id,
        },
    )
    return int(cast(int, row["result_count"])) if row is not None else 0


def _soft_delete_practice_item(session: Session, entity: str, item_id: int) -> int:
    """
    Description:
        Calls h_data.fn_soft_delete for a practice drill or a placed set.

    Parameters:
        session: the write session.
        entity: ENTITY_PRACTICE_DRILL or ENTITY_PRACTICE_DRILL_SET (constants only).
        item_id: the row id, already checked to be live in the practice.

    Return Values:
        int: the number of rows marked.

    Notes:
        The function cascades to results, attempts, taps and linked shots with one
        deleted_at, so they can be restored together (REQ-081, COD-22-03).
    """
    row = fetch_one(
        session, SOFT_DELETE_PRACTICE_ITEM_SQL, {"entity": entity, "item_id": item_id}
    )
    if row is None:
        logger.error("Practice item soft delete returned no row")
        raise RuntimeError("Practice item soft delete returned no row")
    return cast(int, row["rows_marked"])


def remove_practice_drill(session: Session, practice_session_id: int, practice_drill_id: int) -> int:
    """
    Description:
        Removes a drill from a practice with its results (soft delete, restorable for 30
        days; REQ-081, REQ-082).

    Parameters:
        session: the write session (inside run_write_with_reason).
        practice_session_id: the practice from the ov-context store.
        practice_drill_id: the practice drill from the ov-remove-target store (an overview
            pattern id).

    Return Values:
        int: the number of rows marked.

    Notes:
        Locks the practice, then checks the drill is live in this practice (its placed set,
        if any, live too); otherwise LookupError and nothing changes.
    """
    _lock_live_practice(session, practice_session_id)
    if fetch_one(
        session,
        LOCK_PRACTICE_DRILL_SQL,
        {"practice_session_id": practice_session_id, "practice_drill_id": practice_drill_id},
    ) is None:
        raise LookupError(f"practice drill {practice_drill_id} is not live in the practice")
    return _soft_delete_practice_item(session, ENTITY_PRACTICE_DRILL, practice_drill_id)


def remove_placed_set(
    session: Session, practice_session_id: int, practice_drill_set_id: int
) -> int:
    """
    Description:
        Removes a placed drill set from a practice with its drills and their results (soft
        delete, restorable for 30 days; REQ-081, REQ-082).

    Parameters:
        session: the write session (inside run_write_with_reason).
        practice_session_id: the practice from the ov-context store.
        practice_drill_set_id: the placement from the ov-remove-target store.

    Return Values:
        int: the number of rows marked.

    Notes:
        Locks the practice, then the placement as live in this practice; otherwise
        LookupError and nothing changes.
    """
    _lock_live_practice(session, practice_session_id)
    if fetch_one(
        session,
        LOCK_PLACED_SET_SQL,
        {
            "practice_session_id": practice_session_id,
            "practice_drill_set_id": practice_drill_set_id,
        },
    ) is None:
        raise LookupError(f"placed set {practice_drill_set_id} is not live in the practice")
    return _soft_delete_practice_item(session, ENTITY_PRACTICE_DRILL_SET, practice_drill_set_id)


def swap_practice_drill(
    session: Session, practice_session_id: int, practice_drill_id: int
) -> dict[str, object]:
    """
    Description:
        Swaps a drawn drill for another current drill matching its draw line
        (h_data.fn_swap_practice_drill); the slot's results are soft-deleted first
        (REQ-053, REQ-082).

    Parameters:
        session: the write session (inside run_write_with_reason).
        practice_session_id: the practice from the drill-context store.
        practice_drill_id: the practice drill from the run page URL (drill-context).

    Return Values:
        dict[str, object]: {"practice_drill_id", "old_drill_version_id",
        "new_drill_version_id" (None when nothing matched), "results_removed", "notice"}.

    Notes:
        Locks the practice, then the drill as live in this practice; raises LookupError
        otherwise, and ValueError for a source other than drawn or swapped (only those can
        be swapped). When nothing matches, nothing changes and notice says so.
    """
    _lock_live_practice(session, practice_session_id)
    drill = fetch_one(
        session,
        LOCK_PRACTICE_DRILL_SQL,
        {"practice_session_id": practice_session_id, "practice_drill_id": practice_drill_id},
    )
    if drill is None:
        raise LookupError(f"practice drill {practice_drill_id} is not live in the practice")
    if drill["source"] not in SWAPPABLE_SOURCES:
        raise ValueError("only a drawn drill can be swapped")
    row = fetch_one(session, SWAP_PRACTICE_DRILL_SQL, {"practice_drill_id": practice_drill_id})
    if row is None:
        logger.error("Practice drill swap returned no row")
        raise RuntimeError("Practice drill swap returned no row")
    return row
