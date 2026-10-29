"""Data access for player profiles: list, look up, add, edit and remove (REQ-073).

Every function takes the request session first and runs its SQL through data_handler. Write
functions never commit: they run inside app_controller.run_write, after the acting user has
been set (COD-6-01, COD-7-01). A player is a profile whose statistics the app records, never a
login account: a player id is never used in place of a user id, or the reverse (COD-8-01).
"""

import logging
from datetime import date
from typing import cast

from sqlalchemy.orm import Session

from src.data_handler import fetch_all, fetch_one

logger = logging.getLogger(__name__)


# --- Players (REQ-073, COD-8-01) ---

LIST_LIVE_PLAYERS_SQL = """
SELECT player_id, display_name, birth_date, position, dominant_hand
FROM h_data.players
WHERE deleted_at IS NULL
ORDER BY lower(display_name), player_id
"""

GET_LIVE_PLAYER_SQL = """
SELECT player_id, display_name, birth_date, position, dominant_hand
FROM h_data.players
WHERE player_id = :player_id
  AND deleted_at IS NULL
"""

GET_DEFAULT_PLAYER_ID_SQL = """
SELECT player_id
FROM h_data.players
WHERE deleted_at IS NULL
ORDER BY player_id
LIMIT 1
"""

INSERT_PLAYER_SQL = """
INSERT INTO h_data.players (display_name, birth_date, position, dominant_hand)
VALUES (:display_name, :birth_date, :position, :dominant_hand)
RETURNING player_id
"""

UPDATE_PLAYER_SQL = """
UPDATE h_data.players
SET display_name = :display_name,
    birth_date = :birth_date,
    position = :position,
    dominant_hand = :dominant_hand
WHERE player_id = :player_id
  AND deleted_at IS NULL
RETURNING player_id
"""

# Compares exactly as ux_players_name does: lower(display_name) among live profiles.
PLAYER_NAME_TAKEN_SQL = """
SELECT EXISTS (
    SELECT 1
    FROM h_data.players
    WHERE lower(display_name) = lower(CAST(:display_name AS text))
      AND deleted_at IS NULL
      AND (CAST(:exclude_player_id AS bigint) IS NULL
           OR player_id <> CAST(:exclude_player_id AS bigint))
) AS is_taken
"""

SOFT_DELETE_PLAYER_SQL = """
SELECT h_data.fn_soft_delete('player', CAST(:player_id AS bigint)) AS rows_marked
"""

# FOR SHARE: a concurrent profile removal (an UPDATE of this row) waits until the caller's
# write transaction ends, so its cascade also covers the rows added in it (goals_measurements
# D14).
LOCK_LIVE_PLAYER_SQL = """
SELECT player_id
FROM h_data.players
WHERE player_id = :player_id
  AND deleted_at IS NULL
FOR SHARE
"""


def list_live_players(session: Session) -> list[dict[str, object]]:
    """
    Description:
        Lists the live player profiles by name (case-insensitive), for the Players screen
        and every player picker.

    Parameters:
        session: the request session.

    Return Values:
        list[dict[str, object]]: {"player_id", "display_name", "birth_date", "position",
        "dominant_hand"} per live player, ordered by lower(display_name), then player_id.
        birth_date, position and dominant_hand may be None.

    Notes:
        Removed profiles are never listed (COD-22-03).
    """
    return fetch_all(session, LIST_LIVE_PLAYERS_SQL)


def get_live_player(session: Session, player_id: int) -> dict[str, object] | None:
    """
    Description:
        Loads one live player profile.

    Parameters:
        session: the request session.
        player_id: h_data.players.player_id: the selected player (current_player_id()) or
            the player being edited (URL /players/<id>). Never a user id (COD-8-01).

    Return Values:
        dict[str, object] | None: {"player_id", "display_name", "birth_date", "position",
        "dominant_hand"}, or None when the player does not exist or was removed.

    Notes:
        Used to re-validate a cached or client-sent player id before it is used.
    """
    return fetch_one(session, GET_LIVE_PLAYER_SQL, {"player_id": player_id})


def get_default_player_id(session: Session) -> int | None:
    """
    Description:
        Returns the player the app records for: the live player with the lowest id.

    Parameters:
        session: the request session.

    Return Values:
        int | None: h_data.players.player_id, or None if no player exists.

    Notes:
        The fallback when no player is selected yet, or the selected one was removed. The
        result is the "selected player" identifier of the Context Adaptation Table, never
        current_user.user_id.
    """
    row = fetch_one(session, GET_DEFAULT_PLAYER_ID_SQL)
    return None if row is None else cast(int, row["player_id"])


def insert_player(
    session: Session,
    display_name: str,
    birth_date: date | None = None,
    position: str | None = None,
    dominant_hand: str | None = None,
) -> int:
    """
    Description:
        Creates a player profile.

    Parameters:
        session: the write session (inside run_write; acting user = current_user.user_id,
            or SYSTEM_USER_ID from the setup script).
        display_name: the player's name, stripped, 1-100 characters; must not match a live
            profile's name case-insensitively (ux_players_name).
        birth_date: the calendar date of birth, or None.
        position: free text up to 50 characters, or None.
        dominant_hand: 'left', 'right' or None.

    Return Values:
        int: the new h_data.players.player_id.

    Notes:
        A player is not a login account; never use a user id in its place (COD-8-01).
        The caller validates the values; the table's checks and ux_players_name are the
        backstop and raise a database error.
    """
    row = fetch_one(
        session,
        INSERT_PLAYER_SQL,
        {
            "display_name": display_name,
            "birth_date": birth_date,
            "position": position,
            "dominant_hand": dominant_hand,
        },
    )
    if row is None:
        logger.error("Player insert returned no id")
        raise RuntimeError("Player insert returned no id")
    return cast(int, row["player_id"])


def update_player(
    session: Session,
    player_id: int,
    display_name: str,
    birth_date: date | None,
    position: str | None,
    dominant_hand: str | None,
) -> bool:
    """
    Description:
        Saves the edit form of a live player profile (all four fields at once).

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        player_id: the edited player, from the player-edit-id store (set after
            get_live_player). Never a user id (COD-8-01).
        display_name: the new name, stripped, 1-100 characters, unique among live profiles
            case-insensitively.
        birth_date: the date of birth, or None to clear it.
        position: up to 50 characters, or None to clear it.
        dominant_hand: 'left', 'right' or None to clear it.

    Return Values:
        bool: True when the profile was updated, False when it does not exist or was
        removed meanwhile.

    Notes:
        Players stay editable after creation (REQ-082).
    """
    row = fetch_one(
        session,
        UPDATE_PLAYER_SQL,
        {
            "player_id": player_id,
            "display_name": display_name,
            "birth_date": birth_date,
            "position": position,
            "dominant_hand": dominant_hand,
        },
    )
    return row is not None


def player_name_taken(
    session: Session, display_name: str, exclude_player_id: int | None
) -> bool:
    """
    Description:
        Whether a live profile other than the excluded one already has this name,
        compared case-insensitively as ux_players_name does.

    Parameters:
        session: the request session.
        display_name: the name to check, already stripped by the caller.
        exclude_player_id: the player being edited (its own name never counts), or None
            when adding a new player.

    Return Values:
        bool: True when the name is taken.

    Notes:
        A friendly pre-check for the form message; the unique index stays the backstop
        against a concurrent insert.
    """
    row = fetch_one(
        session,
        PLAYER_NAME_TAKEN_SQL,
        {"display_name": display_name, "exclude_player_id": exclude_player_id},
    )
    return row is not None and bool(row["is_taken"])


def soft_delete_player(session: Session, player_id: int) -> int:
    """
    Description:
        Removes a player profile through h_data.fn_soft_delete('player', ...), which
        cascades the same deleted_at to everything of that player: participant rows and
        their records (also in shared practices), games and their entries, goals, physical
        measurements and seasons (Amendment A4, OQ 14 option A).

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        player_id: the edited player, from the player-edit-id store. Never a user id
            (COD-8-01).

    Return Values:
        int: the number of rows marked (the profile plus its cascaded rows).

    Notes:
        The function raises when the player does not exist or is already removed, so the
        caller's transaction rolls back. The app confirms first (REQ-005); restoring within
        30 days brings everything back (h_data.fn_restore).
    """
    row = fetch_one(session, SOFT_DELETE_PLAYER_SQL, {"player_id": player_id})
    if row is None:
        logger.error("Player soft delete returned no row")
        raise RuntimeError("Player soft delete returned no row")
    return cast(int, row["rows_marked"])


def lock_live_player(session: Session, player_id: int) -> bool:
    """
    Description:
        Locks a live player profile row FOR SHARE for the rest of the write transaction
        (task 2026-10-23_goals_measurements, D14).

    Parameters:
        session: the write session (inside run_write).
        player_id: the goal's or measurement's player. Never a user id (COD-8-01).

    Return Values:
        bool: True when the profile is live (and now locked); False when it does not exist
        or was removed.

    Notes:
        Call first in every goal or measurement insert, in the same transaction. A
        concurrent removal of the profile then waits for this transaction, and its cascade
        also marks the new row, so no goal or measurement is ever added to a removed profile.
    """
    return fetch_one(session, LOCK_LIVE_PLAYER_SQL, {"player_id": player_id}) is not None
