"""Data access for games: the game record, the live +1 counters, duplicate and autocomplete
reads, and the page reads (box score, averages, highlights, the game's shot chart, stat
definitions).

REQ-018 to REQ-024, REQ-061 to REQ-064 (task 2026-10-23_games). Every function takes the request
session first and runs its SQL through data_handler. Write functions never commit: they run
inside app_controller.run_write, after the acting user has been set (COD-6-01, COD-7-01).

The game's shot and free-throw entries are written by the shared entry layer
(src/data_entry.py, EntryParent("game", game_id)), which already locks the live game row of the
game's player (lock_entry_owner) and totals its entries (get_entry_totals); this module does not
repeat them (plan §3.2 A2, items 1.9 to 1.12). Every read hides deleted games and games of
removed profiles (COD-22-03); the analytics views apply that rule themselves.
"""

import logging
from datetime import date
from typing import cast

from sqlalchemy.orm import Session

from config.app_config import AUTOCOMPLETE_LIMIT, GAME_COUNTER_CODES, GAME_COUNTER_MAX
from src.data_handler import fetch_all, fetch_one

logger = logging.getLogger(__name__)

ENTRY_STATUS_IN_PROGRESS = "in_progress"
ENTRY_STATUS_COMPLETED = "completed"
OP_ADD = "add"
OP_UNDO = "undo"


# --- Reference lists (REQ-018) ---

LIST_GAME_CATEGORIES_SQL = """
SELECT game_category_code AS code, label
FROM h_ref.game_categories
WHERE deleted_at IS NULL
ORDER BY sort_order
"""

LIST_GAME_FORMATS_SQL = """
SELECT game_format_code AS code, label
FROM h_ref.game_formats
WHERE deleted_at IS NULL
ORDER BY sort_order
"""

LIST_STAT_DEFINITIONS_SQL = """
SELECT stat_code, label, abbreviation, unit, formula_text, explanation
FROM h_ref.stat_definitions
WHERE deleted_at IS NULL
ORDER BY sort_order
"""


def list_game_categories(session: Session) -> list[dict[str, object]]:
    """
    Description:
        Lists the live game categories in display order (REQ-018).

    Parameters:
        session: the request session.

    Return Values:
        list[dict[str, object]]: {"code", "label"} per category, by sort_order.

    Notes:
        The insert and update re-check in SQL that the chosen code is live.
    """
    return fetch_all(session, LIST_GAME_CATEGORIES_SQL)


def list_game_formats(session: Session) -> list[dict[str, object]]:
    """
    Description:
        Lists the live game formats (5v5, 3v3, 2v2, 1v1) in display order (REQ-018).

    Parameters:
        session: the request session.

    Return Values:
        list[dict[str, object]]: {"code", "label"} per format, by sort_order.

    Notes:
        The insert and update re-check in SQL that the chosen code is live.
    """
    return fetch_all(session, LIST_GAME_FORMATS_SQL)


def list_stat_definitions(session: Session) -> dict[str, dict[str, object]]:
    """
    Description:
        Reads the live stat definitions (label, abbreviation, formula and plain-language
        explanation) behind every stat tile (REQ-064).

    Parameters:
        session: the request session.

    Return Values:
        dict[str, dict[str, object]]: stat_code -> {"stat_code", "label", "abbreviation",
        "unit", "formula_text", "explanation"}, in sort_order (dict order).

    Notes:
        Read only.
    """
    rows = fetch_all(session, LIST_STAT_DEFINITIONS_SQL)
    return {str(row["stat_code"]): row for row in rows}


# --- The game record (REQ-018, REQ-020, REQ-022, REQ-023; plan D2, D7, D8, D14) ---

# The live game of a live profile with labels, its live season (else NULL) and who created and
# last edited it (REQ-004). Counters are cast to int for the JSON stores.
GET_GAME_SQL = """
SELECT g.game_id,
       g.player_id,
       p.display_name AS player_name,
       g.season_id,
       g.season_is_manual,
       s.season_name,
       g.game_date,
       g.game_category_code,
       gc.label AS category_label,
       g.game_format_code,
       gf.label AS format_label,
       g.team_name,
       g.opponent_name,
       g.location,
       g.teammate_names,
       g.home_away,
       g.team_score::int AS team_score,
       g.opponent_score::int AS opponent_score,
       g.result,
       g.target_score::int AS target_score,
       g.minutes_played,
       g.off_rebounds::int AS off_rebounds,
       g.def_rebounds::int AS def_rebounds,
       g.assists::int AS assists,
       g.steals::int AS steals,
       g.blocks::int AS blocks,
       g.turnovers::int AS turnovers,
       g.fouls::int AS fouls,
       g.entry_status,
       g.notes,
       g.created_at,
       cu.display_name AS created_by_name,
       g.updated_at,
       uu.display_name AS updated_by_name
FROM h_data.games g
JOIN h_data.players p ON p.player_id = g.player_id
JOIN h_ref.game_categories gc ON gc.game_category_code = g.game_category_code
JOIN h_ref.game_formats gf ON gf.game_format_code = g.game_format_code
JOIN h_system.users cu ON cu.user_id = g.created_by
JOIN h_system.users uu ON uu.user_id = g.updated_by
LEFT JOIN h_data.seasons s ON s.season_id = g.season_id AND s.deleted_at IS NULL
WHERE g.game_id = :game_id
  AND g.deleted_at IS NULL
  AND p.deleted_at IS NULL
"""

# The same live game locked for a read-modify-write in one transaction (Finish game, the +1
# counters). Only the games row is locked.
LOCK_GAME_SQL = """
SELECT g.game_id,
       g.player_id,
       g.season_id,
       g.season_is_manual,
       g.game_date,
       g.game_category_code,
       g.game_format_code,
       g.team_name,
       g.opponent_name,
       g.location,
       g.teammate_names,
       g.home_away,
       g.team_score::int AS team_score,
       g.opponent_score::int AS opponent_score,
       g.target_score::int AS target_score,
       g.minutes_played,
       g.off_rebounds::int AS off_rebounds,
       g.def_rebounds::int AS def_rebounds,
       g.assists::int AS assists,
       g.steals::int AS steals,
       g.blocks::int AS blocks,
       g.turnovers::int AS turnovers,
       g.fouls::int AS fouls,
       g.entry_status,
       g.notes
FROM h_data.games g
JOIN h_data.players p ON p.player_id = g.player_id
WHERE g.game_id = :game_id
  AND g.deleted_at IS NULL
  AND p.deleted_at IS NULL
FOR UPDATE OF g
"""

GET_GAME_COUNTERS_SQL = """
SELECT g.off_rebounds::int AS off_rebounds,
       g.def_rebounds::int AS def_rebounds,
       g.assists::int AS assists,
       g.steals::int AS steals,
       g.blocks::int AS blocks,
       g.turnovers::int AS turnovers,
       g.fouls::int AS fouls
FROM h_data.games g
JOIN h_data.players p ON p.player_id = g.player_id
WHERE g.game_id = :game_id
  AND g.deleted_at IS NULL
  AND p.deleted_at IS NULL
"""

# entry_status is derived on every write: completed exactly when both scores are present (D2).
# The category and format must be live, the profile live, and a manual season a live season of
# the game's player; otherwise the SELECT returns no row and nothing is inserted. The season
# trigger (trg_games_season) assigns the covering season unless season_is_manual.
INSERT_GAME_SQL = """
INSERT INTO h_data.games
    (player_id, season_id, season_is_manual, game_date, game_category_code, game_format_code,
     team_name, opponent_name, location, teammate_names, home_away, team_score, opponent_score,
     target_score, minutes_played, entry_status, notes)
SELECT CAST(:player_id AS bigint),
       CAST(:season_id AS bigint),
       CAST(:season_is_manual AS boolean),
       CAST(:game_date AS date),
       CAST(:game_category_code AS text),
       CAST(:game_format_code AS text),
       CAST(:team_name AS text),
       CAST(:opponent_name AS text),
       CAST(:location AS text),
       CAST(:teammate_names AS text),
       CAST(:home_away AS text),
       CAST(:team_score AS smallint),
       CAST(:opponent_score AS smallint),
       CAST(:target_score AS smallint),
       CAST(:minutes_played AS numeric),
       CASE WHEN CAST(:team_score AS smallint) IS NOT NULL
                 AND CAST(:opponent_score AS smallint) IS NOT NULL
            THEN 'completed' ELSE 'in_progress' END,
       CAST(:notes AS text)
WHERE EXISTS (SELECT 1 FROM h_data.players p
              WHERE p.player_id = CAST(:player_id AS bigint) AND p.deleted_at IS NULL)
  AND EXISTS (SELECT 1 FROM h_ref.game_categories gc
              WHERE gc.game_category_code = CAST(:game_category_code AS text)
                AND gc.deleted_at IS NULL)
  AND EXISTS (SELECT 1 FROM h_ref.game_formats gf
              WHERE gf.game_format_code = CAST(:game_format_code AS text)
                AND gf.deleted_at IS NULL)
  AND (NOT CAST(:season_is_manual AS boolean)
       OR CAST(:season_id AS bigint) IS NULL
       OR EXISTS (SELECT 1 FROM h_data.seasons s
                  WHERE s.season_id = CAST(:season_id AS bigint)
                    AND s.player_id = CAST(:player_id AS bigint)
                    AND s.deleted_at IS NULL))
RETURNING game_id
"""

# The player never changes (ux_games_player, plan D14). Same live checks as the insert.
UPDATE_GAME_SQL = """
UPDATE h_data.games g
SET season_id = CAST(:season_id AS bigint),
    season_is_manual = CAST(:season_is_manual AS boolean),
    game_date = CAST(:game_date AS date),
    game_category_code = CAST(:game_category_code AS text),
    game_format_code = CAST(:game_format_code AS text),
    team_name = CAST(:team_name AS text),
    opponent_name = CAST(:opponent_name AS text),
    location = CAST(:location AS text),
    teammate_names = CAST(:teammate_names AS text),
    home_away = CAST(:home_away AS text),
    team_score = CAST(:team_score AS smallint),
    opponent_score = CAST(:opponent_score AS smallint),
    target_score = CAST(:target_score AS smallint),
    minutes_played = CAST(:minutes_played AS numeric),
    off_rebounds = CAST(:off_rebounds AS smallint),
    def_rebounds = CAST(:def_rebounds AS smallint),
    assists = CAST(:assists AS smallint),
    steals = CAST(:steals AS smallint),
    blocks = CAST(:blocks AS smallint),
    turnovers = CAST(:turnovers AS smallint),
    fouls = CAST(:fouls AS smallint),
    entry_status = CASE WHEN CAST(:team_score AS smallint) IS NOT NULL
                             AND CAST(:opponent_score AS smallint) IS NOT NULL
                        THEN 'completed' ELSE 'in_progress' END,
    notes = CAST(:notes AS text)
WHERE g.game_id = :game_id
  AND g.deleted_at IS NULL
  AND EXISTS (SELECT 1 FROM h_data.players p
              WHERE p.player_id = g.player_id AND p.deleted_at IS NULL)
  AND EXISTS (SELECT 1 FROM h_ref.game_categories gc
              WHERE gc.game_category_code = CAST(:game_category_code AS text)
                AND gc.deleted_at IS NULL)
  AND EXISTS (SELECT 1 FROM h_ref.game_formats gf
              WHERE gf.game_format_code = CAST(:game_format_code AS text)
                AND gf.deleted_at IS NULL)
  AND (NOT CAST(:season_is_manual AS boolean)
       OR CAST(:season_id AS bigint) IS NULL
       OR EXISTS (SELECT 1 FROM h_data.seasons s
                  WHERE s.season_id = CAST(:season_id AS bigint)
                    AND s.player_id = g.player_id
                    AND s.deleted_at IS NULL))
RETURNING g.game_id
"""

SOFT_DELETE_GAME_SQL = """
SELECT h_data.fn_soft_delete('game', CAST(:game_id AS bigint)) AS rows_marked
"""

# The record keys update_game and insert_game bind, besides the counters.
RECORD_KEYS: tuple[str, ...] = (
    "season_id",
    "season_is_manual",
    "game_date",
    "game_category_code",
    "game_format_code",
    "team_name",
    "opponent_name",
    "location",
    "teammate_names",
    "home_away",
    "team_score",
    "opponent_score",
    "target_score",
    "minutes_played",
    "notes",
)


def get_game(session: Session, game_id: int) -> dict[str, object] | None:
    """
    Description:
        Loads one live game of a live profile with its labels, live season name, player name
        and the names of who added and last edited it (REQ-004).

    Parameters:
        session: the request session.
        game_id: h_data.games.game_id from the URL /games/<id>. Never a player or user id
            (COD-8-01).

    Return Values:
        dict[str, object] | None: every games column plus "player_name", "season_name"
        (None when the season is not live), "category_label", "format_label",
        "created_by_name" and "updated_by_name"; None when the game is not live or its
        profile was removed.

    Notes:
        Read only. minutes_played is a Decimal or None.
    """
    return fetch_one(session, GET_GAME_SQL, {"game_id": game_id})


def lock_game(session: Session, game_id: int) -> dict[str, object] | None:
    """
    Description:
        Locks the live game row of a live profile FOR UPDATE and returns its record, for a
        read-modify-write in one transaction (Finish game keeps the live counters).

    Parameters:
        session: the write session (inside run_write).
        game_id: h_data.games.game_id (the game-id store). Never a player or user id.

    Return Values:
        dict[str, object] | None: the game's columns (as LOCK_GAME_SQL), or None when the
        game is not live or its profile was removed.

    Notes:
        Only the games row is locked, the same row the entry layer and the counters lock
        first, so the lock order stays: game row, then entries.
    """
    return fetch_one(session, LOCK_GAME_SQL, {"game_id": game_id})


def get_game_counters(session: Session, game_id: int) -> dict[str, int] | None:
    """
    Description:
        Reads the seven non-shooting counters of a live game (REQ-020).

    Parameters:
        session: the request session.
        game_id: h_data.games.game_id.

    Return Values:
        dict[str, int] | None: {code: value} for GAME_COUNTER_CODES, or None when the game is
        not live.

    Notes:
        Read only; used to put the current values into a failed +1 ack.
    """
    row = fetch_one(session, GET_GAME_COUNTERS_SQL, {"game_id": game_id})
    if row is None:
        return None
    return {code: int(cast(int, row[code])) for code in GAME_COUNTER_CODES}


def _record_params(fields: dict[str, object]) -> dict[str, object]:
    """
    Description:
        The bound parameters of the game record shared by insert_game and update_game.

    Parameters:
        fields: the validated record (see insert_game).

    Return Values:
        dict[str, object]: one entry per RECORD_KEYS key; a missing key is None, except
        season_is_manual, which defaults to False.

    Notes:
        Pure. minutes_played stays a Decimal (one decimal) or None.
    """
    params: dict[str, object] = {key: fields.get(key) for key in RECORD_KEYS}
    params["season_is_manual"] = bool(fields.get("season_is_manual", False))
    return params


def insert_game(session: Session, player_id: int, fields: dict[str, object]) -> int:
    """
    Description:
        Creates a game of a player (REQ-018). entry_status is completed exactly when both
        scores are given (D2), else in_progress; the counters keep their defaults (0).

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        player_id: the game's player, from game-new-player.value after a live check. Never
            current_player_id() blindly and never a user id (Context Adaptation Table).
        fields: the validated record from controller_games.parse_game_form:
            "game_category_code", "game_format_code", "game_date" (date), "team_name",
            "opponent_name", "location", "teammate_names", "home_away" (None or
            home/away/neutral), "target_score", "team_score", "opponent_score",
            "minutes_played" (Decimal or None), "notes", "season_is_manual" (bool) and
            "season_id" (None when automatic).

    Return Values:
        int: the new h_data.games.game_id.

    Notes:
        Raises ValueError when the profile is removed, the category or format is not live,
        or a manual season is not a live season of that player (nothing inserted). The
        season trigger assigns the covering season when the season is automatic and
        re-checks a manual one.
    """
    params = {**_record_params(fields), "player_id": player_id}
    inserted = fetch_one(session, INSERT_GAME_SQL, params)
    if inserted is None:
        raise ValueError("game player, category, format or season is not live")
    return int(cast(int, inserted["game_id"]))


def update_game(session: Session, game_id: int, fields: dict[str, object]) -> bool:
    """
    Description:
        Saves the whole game record and its seven counters (REQ-022, REQ-023); entry_status
        is derived again from the scores (D2). The player never changes.

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        game_id: h_data.games.game_id from the game-id store. Never a player or user id.
        fields: the record as for insert_game plus the seven counters keyed by
            GAME_COUNTER_CODES (whole numbers 0 to GAME_COUNTER_MAX).

    Return Values:
        bool: True when the live game was updated; False when nothing matched (the game was
        deleted, the profile removed, the category or format is not live, or a manual season
        is not a live season of the game's player).

    Notes:
        Raises KeyError when a counter is missing from fields (the caller always passes all
        seven, Finish game from the locked row).
    """
    params = {**_record_params(fields), "game_id": game_id}
    for code in GAME_COUNTER_CODES:
        params[code] = fields[code]
    updated = fetch_one(session, UPDATE_GAME_SQL, params)
    return updated is not None


def soft_delete_game(session: Session, game_id: int) -> int:
    """
    Description:
        Deletes a game through h_data.fn_soft_delete('game', id), which hides it and
        cascades the same deleted_at to its shot and free-throw entries (REQ-005, REQ-006,
        D14).

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        game_id: h_data.games.game_id from the game-id store.

    Return Values:
        int: the number of rows marked (the game plus its entries).

    Notes:
        fn_soft_delete raises when the game does not exist or is already deleted. The
        confirmation dialog is the caller's (REQ-005).
    """
    marked = fetch_one(session, SOFT_DELETE_GAME_SQL, {"game_id": game_id})
    if marked is None:
        logger.error("Game soft delete returned no row")
        raise RuntimeError("Game soft delete returned no row")
    return int(cast(int, marked["rows_marked"]))


# --- Live +1 counters (REQ-010, REQ-021; plan §3.3) ---

# One compare-and-set statement per counter, keyed by its code. Composed from the module
# constant GAME_COUNTER_CODES only (the codes are column names of h_data.games), never from
# external values (COD-14-03); the command's stat only selects a key of this dict.
COUNTER_COMPARE_AND_SET_SQL: dict[str, str] = {
    code: (
        "UPDATE h_data.games g\n"
        f"SET {code} = CAST(:target AS smallint)\n"
        "WHERE g.game_id = :game_id\n"
        "  AND g.deleted_at IS NULL\n"
        f"  AND g.{code} = CAST(:expected AS smallint)\n"
        "RETURNING g.game_id\n"
    )
    for code in GAME_COUNTER_CODES
}


def _counters_of(row: dict[str, object]) -> dict[str, int]:
    """
    Description:
        The seven counters of a game row.

    Parameters:
        row: a row with one column per GAME_COUNTER_CODES code.

    Return Values:
        dict[str, int]: {code: value}.

    Notes:
        Pure.
    """
    return {code: int(cast(int, row[code])) for code in GAME_COUNTER_CODES}


def apply_game_stat(
    session: Session, game_id: int, stat: str, expected: int, op: str
) -> dict[str, object]:
    """
    Description:
        Applies one live +1 or its undo by compare-and-set (plan §3.3): the counter becomes
        expected + 1 (add) or expected - 1 (undo) only while it still equals expected.

    Parameters:
        session: the write session (inside run_write, acting user = current_user.user_id).
        game_id: h_data.games.game_id from the game-id store. Never a player or user id.
        stat: one of GAME_COUNTER_CODES.
        expected: the counter's last server-confirmed value when the command was sent
            (game-stat-base[stat]).
        op: "add" or "undo".

    Return Values:
        dict[str, object]: {"applied": bool, "values": {code: int}} with the counters after
        the statement. applied is True when the row changed, and also when the counter
        already equals the target (a retry of a command already applied, so nothing changes
        twice); False when another device changed the counter meanwhile.

    Notes:
        The game row is locked FOR UPDATE first (the entry layer's lock order: game row
        first). Raises ValueError for an unknown stat or op, an undo from 0 (or a negative
        expected), a target above GAME_COUNTER_MAX, or a game that is not live (deleted or
        its profile removed).
    """
    if stat not in COUNTER_COMPARE_AND_SET_SQL:
        raise ValueError("unknown game stat")
    if op not in (OP_ADD, OP_UNDO):
        raise ValueError("unknown game stat op")
    if expected < 0 or (op == OP_UNDO and expected == 0):
        raise ValueError("nothing to undo")
    target = expected + 1 if op == OP_ADD else expected - 1
    if target > GAME_COUNTER_MAX:
        raise ValueError("game stat above its maximum")

    locked = lock_game(session, game_id)
    if locked is None:
        raise ValueError("game not live")
    values = _counters_of(locked)
    changed = fetch_one(
        session,
        COUNTER_COMPARE_AND_SET_SQL[stat],
        {"game_id": game_id, "expected": expected, "target": target},
    )
    if changed is not None:
        values[stat] = target
        return {"applied": True, "values": values}
    if values[stat] == target:
        logger.warning("Game %s %s already at %s; retry not applied again", game_id, stat, target)
        return {"applied": True, "values": values}
    return {"applied": False, "values": values}


# --- Duplicate warning and autocomplete (REQ-062, REQ-063; plan D5, D6) ---

FIND_DUPLICATE_GAMES_SQL = """
SELECT g.game_id, g.game_date, g.opponent_name
FROM h_data.games g
WHERE g.player_id = :player_id
  AND g.deleted_at IS NULL
  AND g.game_date = CAST(:game_date AS date)
  AND lower(btrim(g.opponent_name)) = lower(btrim(CAST(:opponent_name AS text)))
  AND (CAST(:exclude_game_id AS bigint) IS NULL
       OR g.game_id <> CAST(:exclude_game_id AS bigint))
ORDER BY g.game_id
"""

# Distinct trimmed values of live games of live profiles (every player), most recently used
# first (by game date, then game id), at most :limit per kind.
LIST_SUGGESTIONS_SQL = """
WITH live AS (
    SELECT g.game_id,
           g.game_date,
           btrim(g.team_name) AS team,
           btrim(g.opponent_name) AS opponent,
           btrim(g.location) AS place
    FROM h_data.games g
    JOIN h_data.players p ON p.player_id = g.player_id
    WHERE g.deleted_at IS NULL
      AND p.deleted_at IS NULL
), used AS (
    SELECT 'teams'::text AS kind, team AS value, max(game_date) AS last_date,
           max(game_id) AS last_id
    FROM live WHERE team <> '' GROUP BY team
    UNION ALL
    SELECT 'opponents'::text, opponent, max(game_date), max(game_id)
    FROM live WHERE opponent <> '' GROUP BY opponent
    UNION ALL
    SELECT 'locations'::text, place, max(game_date), max(game_id)
    FROM live WHERE place <> '' GROUP BY place
), ranked AS (
    SELECT kind, value,
           row_number() OVER (PARTITION BY kind ORDER BY last_date DESC, last_id DESC) AS rn
    FROM used
)
SELECT kind, value
FROM ranked
WHERE rn <= :limit
ORDER BY kind, rn
"""

LAST_TEAM_AND_LOCATION_SQL = """
SELECT (SELECT btrim(g.team_name)
        FROM h_data.games g
        WHERE g.player_id = :player_id
          AND g.deleted_at IS NULL
          AND btrim(g.team_name) <> ''
        ORDER BY g.game_date DESC, g.game_id DESC
        LIMIT 1) AS last_team,
       (SELECT btrim(g.location)
        FROM h_data.games g
        WHERE g.player_id = :player_id
          AND g.deleted_at IS NULL
          AND btrim(g.location) <> ''
        ORDER BY g.game_date DESC, g.game_id DESC
        LIMIT 1) AS last_location
"""

SUGGESTION_KINDS: tuple[str, ...] = ("teams", "opponents", "locations")


def find_duplicate_games(
    session: Session,
    player_id: int,
    game_date: date,
    opponent_name: str,
    exclude_game_id: int | None,
) -> list[dict[str, object]]:
    """
    Description:
        Lists the player's live games on a date against the same opponent, compared trimmed
        and case-insensitively (REQ-063, D5).

    Parameters:
        session: the request session.
        player_id: the game's player. Never a user id (COD-8-01).
        game_date: the game's date.
        opponent_name: the opponent as typed; never empty (the caller skips the check
            without an opponent).
        exclude_game_id: the edited game's id, or None on create.

    Return Values:
        list[dict[str, object]]: {"game_id", "game_date", "opponent_name"} per duplicate,
        by game_id.

    Notes:
        Read only. The warning is advisory: the user may save anyway.
    """
    return fetch_all(
        session,
        FIND_DUPLICATE_GAMES_SQL,
        {
            "player_id": player_id,
            "game_date": game_date,
            "opponent_name": opponent_name,
            "exclude_game_id": exclude_game_id,
        },
    )


def get_entry_suggestions(session: Session, player_id: int) -> dict[str, object]:
    """
    Description:
        The autocomplete suggestions and remembered defaults of the game form (REQ-062,
        D6).

    Parameters:
        session: the request session.
        player_id: the player whose last team and location are prefilled (the game's or the
            picked player). Never a user id (COD-8-01).

    Return Values:
        dict[str, object]: {"teams", "opponents", "locations"}: lists of distinct trimmed
        values from every live game of every live profile, most recently used first, at
        most AUTOCOMPLETE_LIMIT each; {"last_team", "last_location"}: from that player's
        newest live game (by date, then id) that has the value, else None.

    Notes:
        Read only; two statements.
    """
    suggestions: dict[str, object] = {kind: [] for kind in SUGGESTION_KINDS}
    rows = fetch_all(session, LIST_SUGGESTIONS_SQL, {"limit": AUTOCOMPLETE_LIMIT})
    for row in rows:
        values = suggestions.get(str(row["kind"]))
        if isinstance(values, list):
            values.append(str(row["value"]))
    last = fetch_one(session, LAST_TEAM_AND_LOCATION_SQL, {"player_id": player_id}) or {}
    suggestions["last_team"] = last.get("last_team")
    suggestions["last_location"] = last.get("last_location")
    return suggestions


# --- Page reads: list, box score, averages, highlights, shot chart (REQ-024, REQ-061) ---

LIST_GAMES_SQL = """
SELECT b.game_id,
       b.game_date,
       b.opponent_name,
       b.team_score::int AS team_score,
       b.opponent_score::int AS opponent_score,
       b.result,
       b.points::int AS points,
       gc.label AS category_label,
       b.entry_status
FROM h_analytics.v_game_box_scores b
JOIN h_ref.game_categories gc ON gc.game_category_code = b.game_category_code
WHERE b.player_id = :player_id
ORDER BY b.game_date DESC, b.game_id DESC
"""

GET_GAME_BOX_SCORE_SQL = """
SELECT b.*
FROM h_analytics.v_game_box_scores b
WHERE b.game_id = :game_id
"""

GET_GAME_AVERAGES_SQL = """
SELECT a.*
FROM h_analytics.fn_game_averages(CAST(:player_id AS bigint)) a
"""

GET_GAME_HIGHLIGHTS_SQL = """
SELECT h.stat_code, h.value, h.previous_high, h.is_new_high
FROM h_analytics.fn_game_highlights(CAST(:game_id AS bigint)) h
"""

# The zone label is joined for the court figure's spot metadata.
GET_GAME_HEAT_MAP_SQL = """
SELECT h.court_spot_id,
       h.spot_number,
       h.spot_name,
       h.zone_code,
       z.label AS zone_label,
       h.x_ft::float8 AS x_ft,
       h.y_ft::float8 AS y_ft,
       h.makes::int AS makes,
       h.attempts::int AS attempts,
       h.make_pct,
       h.band_rank,
       h.band_label
FROM h_analytics.fn_game_heat_map_spots(CAST(:game_id AS bigint)) h
LEFT JOIN h_ref.zones z ON z.zone_code = h.zone_code
ORDER BY h.spot_number
"""


def list_games(session: Session, player_id: int) -> list[dict[str, object]]:
    """
    Description:
        Lists a player's live games for the games grid, newest first (REQ-048).

    Parameters:
        session: the request session.
        player_id: the selected player (current_player_id()). Never a user id (COD-8-01).

    Return Values:
        list[dict[str, object]]: {"game_id", "game_date", "opponent_name", "team_score",
        "opponent_score", "result", "points", "category_label", "entry_status"} per game.

    Notes:
        Read through h_analytics.v_game_box_scores (live games of live profiles).
    """
    return fetch_all(session, LIST_GAMES_SQL, {"player_id": player_id})


def get_game_box_score(session: Session, game_id: int) -> dict[str, object] | None:
    """
    Description:
        The game's box score row (REQ-019, REQ-020, REQ-024).

    Parameters:
        session: the request session.
        game_id: h_data.games.game_id.

    Return Values:
        dict[str, object] | None: the h_analytics.v_game_box_scores row (every game column,
        fgm, fga, three_pm, three_pa, ftm, fta, points, total_rebounds, fg_pct, three_pct,
        ft_pct, efg_pct, ts_pct, ast_to; percentages unrounded, NULL without attempts), or
        None when the game is not live.

    Notes:
        Read only.
    """
    return fetch_one(session, GET_GAME_BOX_SCORE_SQL, {"game_id": game_id})


def get_game_averages(session: Session, player_id: int) -> dict[str, object] | None:
    """
    Description:
        The player's per-game averages over all live games (REQ-024, D12).

    Parameters:
        session: the request session.
        player_id: the selected player. Never a user id (COD-8-01).

    Return Values:
        dict[str, object] | None: the h_analytics.fn_game_averages row (games_played, the
        total_ and avg_ columns, fg_pct, three_pct, ft_pct, efg_pct, ts_pct, ast_to).

    Notes:
        Called with the player only, so every other filter is unset.
    """
    return fetch_one(session, GET_GAME_AVERAGES_SQL, {"player_id": player_id})


def get_game_highlights(session: Session, game_id: int) -> list[dict[str, object]]:
    """
    Description:
        This game's key stats against the previous career highs (REQ-061).

    Parameters:
        session: the request session.
        game_id: h_data.games.game_id.

    Return Values:
        list[dict[str, object]]: {"stat_code", "value", "previous_high", "is_new_high"} per
        stat (points, total_rebounds, assists, steals, blocks).

    Notes:
        Read through h_analytics.fn_game_highlights.
    """
    return fetch_all(session, GET_GAME_HIGHLIGHTS_SQL, {"game_id": game_id})


def get_game_heat_map(session: Session, game_id: int) -> list[dict[str, object]]:
    """
    Description:
        The game's shot chart: makes, attempts, make % and colour band per court spot, from
        that game's spot shots only (REQ-061, D1).

    Parameters:
        session: the request session.
        game_id: h_data.games.game_id.

    Return Values:
        list[dict[str, object]]: per spot {"court_spot_id", "spot_number", "spot_name",
        "zone_code", "zone_label", "x_ft", "y_ft" (floats), "makes", "attempts",
        "make_pct", "band_rank", "band_label"}, by spot_number.

    Notes:
        Read through h_analytics.fn_game_heat_map_spots; manual entries are not on the
        chart.
    """
    return fetch_all(session, GET_GAME_HEAT_MAP_SQL, {"game_id": game_id})
