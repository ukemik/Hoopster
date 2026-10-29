"""Data access for seasons (REQ-018; task 2026-10-23_games, plan D7).

A season belongs to one player profile and never overlaps another live season of that player
(trigger trg_seasons_no_overlap); names are unique per player case-insensitively among live
seasons (ux_seasons_player_name). Games and practices are assigned to the covering season by
the database triggers, also after a season changes or is deleted (trg_seasons_reassign). Every
function takes the request session first and runs its SQL through data_handler; writes never
commit (they run inside app_controller.run_write).
"""

import logging
from datetime import date
from typing import cast

from sqlalchemy.orm import Session

from src.data_handler import fetch_all, fetch_one

logger = logging.getLogger(__name__)


# --- Seasons (REQ-018) ---

LIST_SEASONS_SQL = """
SELECT s.season_id, s.player_id, s.season_name, s.start_date, s.end_date
FROM h_data.seasons s
JOIN h_data.players p ON p.player_id = s.player_id
WHERE s.player_id = :player_id
  AND s.deleted_at IS NULL
  AND p.deleted_at IS NULL
ORDER BY s.start_date, s.season_id
"""

GET_SEASON_SQL = """
SELECT s.season_id, s.player_id, p.display_name AS player_name, s.season_name, s.start_date,
       s.end_date
FROM h_data.seasons s
JOIN h_data.players p ON p.player_id = s.player_id
WHERE s.season_id = :season_id
  AND s.deleted_at IS NULL
  AND p.deleted_at IS NULL
"""

SEASON_OVERLAP_SQL = """
SELECT s.season_name
FROM h_data.seasons s
WHERE s.player_id = :player_id
  AND s.deleted_at IS NULL
  AND s.start_date <= CAST(:end_date AS date)
  AND s.end_date >= CAST(:start_date AS date)
  AND (CAST(:exclude_id AS bigint) IS NULL OR s.season_id <> CAST(:exclude_id AS bigint))
ORDER BY s.start_date, s.season_id
LIMIT 1
"""

SEASON_NAME_TAKEN_SQL = """
SELECT EXISTS (
    SELECT 1
    FROM h_data.seasons s
    WHERE s.player_id = :player_id
      AND s.deleted_at IS NULL
      AND lower(s.season_name) = lower(CAST(:season_name AS text))
      AND (CAST(:exclude_id AS bigint) IS NULL OR s.season_id <> CAST(:exclude_id AS bigint))
) AS taken
"""

# The profile must be live; the no-overlap trigger also refuses a removed profile.
INSERT_SEASON_SQL = """
INSERT INTO h_data.seasons (player_id, season_name, start_date, end_date)
SELECT CAST(:player_id AS bigint),
       CAST(:season_name AS text),
       CAST(:start_date AS date),
       CAST(:end_date AS date)
WHERE EXISTS (SELECT 1 FROM h_data.players p
              WHERE p.player_id = CAST(:player_id AS bigint) AND p.deleted_at IS NULL)
RETURNING season_id
"""

UPDATE_SEASON_SQL = """
UPDATE h_data.seasons s
SET season_name = CAST(:season_name AS text),
    start_date = CAST(:start_date AS date),
    end_date = CAST(:end_date AS date)
WHERE s.season_id = :season_id
  AND s.deleted_at IS NULL
  AND EXISTS (SELECT 1 FROM h_data.players p
              WHERE p.player_id = s.player_id AND p.deleted_at IS NULL)
RETURNING s.season_id
"""

SOFT_DELETE_SEASON_SQL = """
SELECT h_data.fn_soft_delete('season', CAST(:season_id AS bigint)) AS rows_marked
"""


def list_seasons(session: Session, player_id: int) -> list[dict[str, object]]:
    """
    Description:
        Lists a player's live seasons by start date (REQ-018).

    Parameters:
        session: the request session.
        player_id: the selected player (Seasons screen) or the game's player (season
            picker). Never a user id (COD-8-01).

    Return Values:
        list[dict[str, object]]: {"season_id", "player_id", "season_name", "start_date",
        "end_date"} per season; empty when the profile is removed.

    Notes:
        Read only.
    """
    return fetch_all(session, LIST_SEASONS_SQL, {"player_id": player_id})


def get_season(session: Session, season_id: int) -> dict[str, object] | None:
    """
    Description:
        Loads one live season of a live profile, with the player's name.

    Parameters:
        session: the request session.
        season_id: h_data.seasons.season_id from the URL /seasons/<id>.

    Return Values:
        dict[str, object] | None: {"season_id", "player_id", "player_name", "season_name",
        "start_date", "end_date"}, or None when the season or its profile is not live.

    Notes:
        The season's player comes from this row, never from the selected player (Context
        Adaptation Table).
    """
    return fetch_one(session, GET_SEASON_SQL, {"season_id": season_id})


def season_overlap(
    session: Session,
    player_id: int,
    start_date: date,
    end_date: date,
    exclude_id: int | None,
) -> str | None:
    """
    Description:
        Finds a live season of the player whose dates overlap the given ones (both ends
        inclusive), as trg_seasons_no_overlap would refuse.

    Parameters:
        session: the request session.
        player_id: the season's player.
        start_date: the first day.
        end_date: the last day (not before start_date).
        exclude_id: the edited season's id, or None when adding.

    Return Values:
        str | None: the name of the earliest overlapping season, or None.

    Notes:
        Read only; the trigger re-checks under a lock at write time.
    """
    row = fetch_one(
        session,
        SEASON_OVERLAP_SQL,
        {
            "player_id": player_id,
            "start_date": start_date,
            "end_date": end_date,
            "exclude_id": exclude_id,
        },
    )
    return None if row is None else str(row["season_name"])


def season_name_taken(
    session: Session, player_id: int, season_name: str, exclude_id: int | None
) -> bool:
    """
    Description:
        Whether another live season of the player already has this name, compared
        case-insensitively as ux_seasons_player_name does.

    Parameters:
        session: the request session.
        player_id: the season's player.
        season_name: the trimmed name.
        exclude_id: the edited season's id, or None when adding.

    Return Values:
        bool: True when the name is taken.

    Notes:
        Read only.
    """
    row = fetch_one(
        session,
        SEASON_NAME_TAKEN_SQL,
        {"player_id": player_id, "season_name": season_name, "exclude_id": exclude_id},
    )
    return bool(row and row["taken"])


def insert_season(
    session: Session, player_id: int, season_name: str, start_date: date, end_date: date
) -> int:
    """
    Description:
        Adds a season to a player (REQ-018). The database re-assigns the player's automatic
        games and practices that fall in it.

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        player_id: the selected player (current_player_id()). Never a user id (COD-8-01).
        season_name: the trimmed name, 1 to 100 characters.
        start_date: the first day.
        end_date: the last day, not before start_date.

    Return Values:
        int: the new h_data.seasons.season_id.

    Notes:
        Raises ValueError when the profile is not live (nothing inserted); the triggers
        raise on an overlap and the unique index on a taken name.
    """
    inserted = fetch_one(
        session,
        INSERT_SEASON_SQL,
        {
            "player_id": player_id,
            "season_name": season_name,
            "start_date": start_date,
            "end_date": end_date,
        },
    )
    if inserted is None:
        raise ValueError("season player is not live")
    return int(cast(int, inserted["season_id"]))


def update_season(
    session: Session, season_id: int, season_name: str, start_date: date, end_date: date
) -> bool:
    """
    Description:
        Renames a live season or changes its dates (REQ-018).

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        season_id: h_data.seasons.season_id from the season-edit-id store.
        season_name: the trimmed name, 1 to 100 characters.
        start_date: the first day.
        end_date: the last day, not before start_date.

    Return Values:
        bool: True when the live season was updated; False when it or its profile is no
        longer live.

    Notes:
        The season's player never changes here.
    """
    updated = fetch_one(
        session,
        UPDATE_SEASON_SQL,
        {
            "season_id": season_id,
            "season_name": season_name,
            "start_date": start_date,
            "end_date": end_date,
        },
    )
    return updated is not None


def soft_delete_season(session: Session, season_id: int) -> int:
    """
    Description:
        Deletes a season through h_data.fn_soft_delete('season', id). Its games and
        practices are kept; automatic ones move to whichever live season covers their date,
        if any (trg_seasons_reassign).

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        season_id: h_data.seasons.season_id from the season-edit-id store.

    Return Values:
        int: the number of rows marked (1).

    Notes:
        fn_soft_delete raises when the season does not exist or is already deleted. The
        confirmation dialog is the caller's (REQ-005).
    """
    marked = fetch_one(session, SOFT_DELETE_SEASON_SQL, {"season_id": season_id})
    if marked is None:
        logger.error("Season soft delete returned no row")
        raise RuntimeError("Season soft delete returned no row")
    return int(cast(int, marked["rows_marked"]))
