"""Games controller of Hoopster: the five game page builders (list, New game, game page, live
mode, summary), the pure form check and formatters, and the game callbacks: player pickers,
grid navigation, create, save, delete, the live +1 stat executor and Finish game.

REQ-018 to REQ-024, REQ-004 to REQ-006, REQ-010, REQ-057, REQ-061 to REQ-064 (task
2026-10-23_games). The live page's court entry is the shared entry panel; its callbacks are
registered once by controller_entry.register_entry_callbacks (plan §3.2 A4) and its data
functions take EntryParent("game", game_id). The +1 stats use their own serialised channel
(plan §3.3): the clientside hoopster_games.game_stat_dispatch is the only writer of
game-stat-queue, game-stat-confirmed and game-stat-outbox; execute_game_stat_command has no
Output and answers through dash.set_props into game-stat-base and game-stat-ack. Every write
goes through app_controller.run_write (COD-6-01).
"""

import logging
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from dash import (
    ALL,
    ClientsideFunction,
    Dash,
    Input,
    Output,
    State,
    ctx,
    no_update,
    set_props,
)
from dash.development.base_component import Component
from dash.exceptions import PreventUpdate
from flask_login import current_user
from sqlalchemy.orm import Session

from config.app_config import (
    GAME_COUNTER_CODES,
    GAME_COUNTER_MAX,
    GAME_MINUTES_MAX,
    GAME_NOTES_MAX_LENGTH,
    GAME_SCORE_MAX,
    GAME_TARGET_MAX,
    GAME_TEAMMATES_MAX_LENGTH,
    GAME_TEXT_MAX_LENGTH,
)
from src import data_handler
from src.app_controller import (
    DeviceClock,
    log_failure,
    new_reference_code,
    normalise_clock,
    parse_player_value,
    run_write,
    set_current_player_id,
)
from src.controller_entry import format_device_time
from src.data_entry import (
    PARENT_KIND_GAME,
    EntryParent,
    get_entry_totals,
    list_active_court_spots,
    list_shot_types,
)
from src.data_games import (
    ENTRY_STATUS_IN_PROGRESS,
    OP_ADD,
    OP_UNDO,
    apply_game_stat,
    find_duplicate_games,
    get_entry_suggestions,
    get_game,
    get_game_averages,
    get_game_box_score,
    get_game_counters,
    get_game_heat_map,
    get_game_highlights,
    insert_game,
    list_game_categories,
    list_game_formats,
    list_games,
    list_stat_definitions,
    lock_game,
    soft_delete_game,
    update_game,
)
from src.data_players import list_live_players
from src.data_seasons import list_seasons
from src.layouts import not_found_page
from src.layouts_entry import format_entry_datetime
from src.layouts_games import (
    GAMES_PATH,
    HOME_AWAY_NOT_SET,
    PRACTICE_GAME_CODE,
    SEASON_AUTO,
    format_game_date,
    game_live_layout,
    game_new_layout,
    game_page_layout,
    game_summary_layout,
    games_content,
    games_layout,
    highlight_lines,
    season_options,
)

logger = logging.getLogger(__name__)

DASH_TEXT = "–"
STORED_HOME_AWAY: frozenset[str] = frozenset({"home", "away", "neutral"})

MESSAGE_CHOOSE_PLAYER = "Choose a player."
MESSAGE_CATEGORY = "Choose a category."
MESSAGE_FORMAT = "Choose a format."
MESSAGE_DATE = "Choose a date."
MESSAGE_TEXT_TOO_LONG = "{field} can be up to " + str(GAME_TEXT_MAX_LENGTH) + " characters."
MESSAGE_TEAMMATES = (
    f"Opponent and teammate names can be up to {GAME_TEAMMATES_MAX_LENGTH} characters."
)
MESSAGE_HOME_AWAY = "Choose home, away, neutral or not set."
MESSAGE_TARGET = f"Enter a target score from 1 to {GAME_TARGET_MAX}."
MESSAGE_BOTH_SCORES = "Enter both scores, or neither."
MESSAGE_SCORES = f"Scores are whole numbers from 0 to {GAME_SCORE_MAX}."
MESSAGE_MINUTES = f"Minutes are 0 to {GAME_MINUTES_MAX}."
MESSAGE_COUNTERS = f"Stats are whole numbers from 0 to {GAME_COUNTER_MAX}."
MESSAGE_NOTES = f"Notes can be up to {GAME_NOTES_MAX_LENGTH} characters."
MESSAGE_SEASON = "Choose a season."
MESSAGE_DUPLICATE = (
    "A game against {opponent} on {date} already exists. Tap again to save it anyway."
)
MESSAGE_SAVE_FAILED = "Could not save the game. Reference: {ref}"
MESSAGE_DELETE_FAILED = "Could not delete the game. Reference: {ref}"
MESSAGE_LOAD_FAILED = "Could not load the games. Reference: {ref}"
MESSAGE_SAVED = "Saved."
MESSAGE_WAIT_UNSAVED = "Wait until every entry is saved."
MESSAGE_FINISH_SCORES = "Enter both scores."
MESSAGE_DELETE_QUESTION = (
    "Delete this game? Its shots and free throws are hidden with it and can be restored "
    "within 30 days."
)

TENTH = Decimal("0.1")
HUNDREDTH = Decimal("0.01")


# --- Pure helpers: numbers and formatters (plan item 2.2) ---

def _is_int(value: object) -> bool:
    """
    Description:
        Whether a JSON value is an integer (a bool is not).

    Parameters:
        value: the value.

    Return Values:
        bool: True for an int that is not a bool.

    Notes:
        Pure.
    """
    return isinstance(value, int) and not isinstance(value, bool)


def _to_decimal(value: object) -> Decimal | None:
    """
    Description:
        A database or input number as a Decimal.

    Parameters:
        value: an int, float, Decimal or numeric text.

    Return Values:
        Decimal | None: the value, or None for None, a bool, non-numeric text or a
        non-finite number.

    Notes:
        Pure. A float goes through its text so 0.1 stays 0.1.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def format_pct(value: object) -> str:
    """
    Description:
        A percentage with one decimal, rounded half up, e.g. "48.5%"; "–" when NULL (no
        attempts).

    Parameters:
        value: the unrounded percentage from the database (0 to 100) or None.

    Return Values:
        str: the text.

    Notes:
        Pure.
    """
    number = _to_decimal(value)
    if number is None:
        return DASH_TEXT
    return f"{number.quantize(TENTH, rounding=ROUND_HALF_UP)}%"


def format_avg(value: object) -> str:
    """
    Description:
        An average (or minutes) with one decimal, rounded half up, e.g. "12.3"; "–" when
        NULL.

    Parameters:
        value: the number or None.

    Return Values:
        str: the text.

    Notes:
        Pure.
    """
    number = _to_decimal(value)
    if number is None:
        return DASH_TEXT
    return str(number.quantize(TENTH, rounding=ROUND_HALF_UP))


def format_ratio(value: object) -> str:
    """
    Description:
        A ratio (AST/TO) with two decimals, rounded half up, e.g. "1.50"; "–" when NULL (no
        turnovers).

    Parameters:
        value: the ratio or None.

    Return Values:
        str: the text.

    Notes:
        Pure.
    """
    number = _to_decimal(value)
    if number is None:
        return DASH_TEXT
    return str(number.quantize(HUNDREDTH, rounding=ROUND_HALF_UP))


def made_of_text(makes: object, attempts: object, pct: object) -> str:
    """
    Description:
        A shooting line, "{makes}/{attempts} · {pct}", e.g. "5/10 · 50.0%" or "0/0 · –".

    Parameters:
        makes: made shots.
        attempts: attempted shots.
        pct: the unrounded percentage or None.

    Return Values:
        str: the text.

    Notes:
        Pure.
    """
    return f"{int(makes or 0)}/{int(attempts or 0)} · {format_pct(pct)}"


def _count_text(value: object) -> str:
    """
    Description:
        A whole count as text, 0 when NULL.

    Parameters:
        value: the count.

    Return Values:
        str: the text.

    Notes:
        Pure.
    """
    return str(int(value or 0))


def average_tiles(averages: dict | None) -> list[tuple[str, str]]:
    """
    Description:
        The per-game averages tiles of /games (REQ-024, D12): PTS, REB, AST, STL, BLK, TOV
        per game, then FG%, 3P%, FT%, eFG%, TS%, AST/TO.

    Parameters:
        averages: data_games.get_game_averages row, or None.

    Return Values:
        list[tuple[str, str]]: (stat_code, formatted value) pairs.

    Notes:
        Pure.
    """
    row = averages or {}
    return [
        ("points", format_avg(row.get("avg_points"))),
        ("total_rebounds", format_avg(row.get("avg_rebounds"))),
        ("assists", format_avg(row.get("avg_assists"))),
        ("steals", format_avg(row.get("avg_steals"))),
        ("blocks", format_avg(row.get("avg_blocks"))),
        ("turnovers", format_avg(row.get("avg_turnovers"))),
        ("fg_pct", format_pct(row.get("fg_pct"))),
        ("three_pct", format_pct(row.get("three_pct"))),
        ("ft_pct", format_pct(row.get("ft_pct"))),
        ("efg_pct", format_pct(row.get("efg_pct"))),
        ("ts_pct", format_pct(row.get("ts_pct"))),
        ("ast_to", format_ratio(row.get("ast_to"))),
    ]


def shooting_tiles(box: dict | None) -> list[tuple[str, str]]:
    """
    Description:
        The game page's read-only shooting tiles: FG, 3P, FT (made/attempted · %), PTS, REB,
        eFG%, TS%.

    Parameters:
        box: data_games.get_game_box_score row, or None.

    Return Values:
        list[tuple[str, str]]: (stat_code, formatted value) pairs.

    Notes:
        Pure. REB = OREB + DREB from the view (REQ-020).
    """
    row = box or {}
    return [
        ("fg_pct", made_of_text(row.get("fgm"), row.get("fga"), row.get("fg_pct"))),
        ("three_pct", made_of_text(row.get("three_pm"), row.get("three_pa"), row.get("three_pct"))),
        ("ft_pct", made_of_text(row.get("ftm"), row.get("fta"), row.get("ft_pct"))),
        ("points", _count_text(row.get("points"))),
        ("total_rebounds", _count_text(row.get("total_rebounds"))),
        ("efg_pct", format_pct(row.get("efg_pct"))),
        ("ts_pct", format_pct(row.get("ts_pct"))),
    ]


def summary_tiles(box: dict | None) -> list[tuple[str, str]]:
    """
    Description:
        The post-game box score (REQ-061): PTS, FG, 3P, FT, eFG%, TS%, OREB, DREB, REB, AST,
        STL, BLK, TOV, PF, AST/TO, MIN.

    Parameters:
        box: data_games.get_game_box_score row, or None.

    Return Values:
        list[tuple[str, str]]: (stat_code, formatted value) pairs.

    Notes:
        Pure. Minutes show one decimal, "–" when not recorded.
    """
    row = box or {}
    return [
        ("points", _count_text(row.get("points"))),
        ("fg_pct", made_of_text(row.get("fgm"), row.get("fga"), row.get("fg_pct"))),
        ("three_pct", made_of_text(row.get("three_pm"), row.get("three_pa"), row.get("three_pct"))),
        ("ft_pct", made_of_text(row.get("ftm"), row.get("fta"), row.get("ft_pct"))),
        ("efg_pct", format_pct(row.get("efg_pct"))),
        ("ts_pct", format_pct(row.get("ts_pct"))),
        ("off_rebounds", _count_text(row.get("off_rebounds"))),
        ("def_rebounds", _count_text(row.get("def_rebounds"))),
        ("total_rebounds", _count_text(row.get("total_rebounds"))),
        ("assists", _count_text(row.get("assists"))),
        ("steals", _count_text(row.get("steals"))),
        ("blocks", _count_text(row.get("blocks"))),
        ("turnovers", _count_text(row.get("turnovers"))),
        ("fouls", _count_text(row.get("fouls"))),
        ("ast_to", format_ratio(row.get("ast_to"))),
        ("minutes", format_avg(row.get("minutes_played"))),
    ]


# --- Pure helpers: the game form (plan item 2.2, D5, D8, D13) ---

def _clean_text(value: object) -> str | None:
    """
    Description:
        A text field's value, trimmed; empty becomes None (stored as NULL).

    Parameters:
        value: the field's value.

    Return Values:
        str | None: the trimmed text, or None.

    Notes:
        Pure.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _is_empty(value: object) -> bool:
    """
    Description:
        Whether a number field is empty.

    Parameters:
        value: the NumberInput value.

    Return Values:
        bool: True for None and "" (also blank text).

    Notes:
        Pure.
    """
    return value is None or (isinstance(value, str) and not value.strip())


def parse_whole(value: object) -> tuple[bool, int | None]:
    """
    Description:
        Reads an optional whole-number field.

    Parameters:
        value: a NumberInput value: an int, a whole float, digits as text, "" or None.

    Return Values:
        tuple[bool, int | None]: (True, None) when empty; (True, n) for a whole number
        n >= 0; (False, None) for anything else (decimals, negatives, text).

    Notes:
        Pure.
    """
    if _is_empty(value):
        return True, None
    if isinstance(value, int) and not isinstance(value, bool):
        return (value >= 0, value if value >= 0 else None)
    if isinstance(value, float):
        if value.is_integer() and value >= 0:
            return True, int(value)
        return False, None
    if isinstance(value, str):
        text = value.strip()
        if text.isascii() and text.isdigit():
            return True, int(text)
    return False, None


def parse_minutes(value: object) -> tuple[bool, Decimal | None]:
    """
    Description:
        Reads the minutes field, rounded half up to one decimal (numeric(4,1)).

    Parameters:
        value: a NumberInput value (number, numeric text, "" or None).

    Return Values:
        tuple[bool, Decimal | None]: (True, None) when empty; (True, minutes) when 0 to
        GAME_MINUTES_MAX after rounding; (False, None) otherwise.

    Notes:
        Pure.
    """
    if _is_empty(value):
        return True, None
    number = _to_decimal(value)
    if number is None:
        return False, None
    rounded = number.quantize(TENTH, rounding=ROUND_HALF_UP)
    if rounded < 0 or rounded > GAME_MINUTES_MAX:
        return False, None
    return True, rounded


def _parse_date(value: object) -> date | None:
    """
    Description:
        Reads a date picker's value.

    Parameters:
        value: "YYYY-MM-DD" (a date-time string is cut to its date), or None.

    Return Values:
        date | None: the date, or None.

    Notes:
        Pure. Stored as entered (REQ-055).
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_scores(team: object, opponent: object) -> tuple[tuple[int | None, int | None], str | None]:
    """
    Description:
        Reads the two final scores: both or neither, whole numbers 0 to GAME_SCORE_MAX.

    Parameters:
        team: the team score field.
        opponent: the opponent score field.

    Return Values:
        tuple: ((team_score, opponent_score), None) when valid (both None when empty), or
        ((None, None), the message).

    Notes:
        Pure. "Enter both scores, or neither." is checked before the range.
    """
    if _is_empty(team) != _is_empty(opponent):
        return (None, None), MESSAGE_BOTH_SCORES
    team_ok, team_score = parse_whole(team)
    opponent_ok, opponent_score = parse_whole(opponent)
    if not team_ok or not opponent_ok:
        return (None, None), MESSAGE_SCORES
    for score in (team_score, opponent_score):
        if score is not None and score > GAME_SCORE_MAX:
            return (None, None), MESSAGE_SCORES
    return (team_score, opponent_score), None


def _parse_counters(raw: dict) -> tuple[dict[str, int] | None, str | None]:
    """
    Description:
        Reads the seven after-game totals (REQ-022): required whole numbers 0 to
        GAME_COUNTER_MAX.

    Parameters:
        raw: {code: NumberInput value} for GAME_COUNTER_CODES.

    Return Values:
        tuple[dict[str, int] | None, str | None]: ({code: n}, None), or (None, the
        message).

    Notes:
        Pure.
    """
    counters: dict[str, int] = {}
    for code in GAME_COUNTER_CODES:
        ok, number = parse_whole(raw.get(code))
        if not ok or number is None or number > GAME_COUNTER_MAX:
            return None, MESSAGE_COUNTERS
        counters[code] = number
    return counters, None


def _parse_season(value: object, season_ids: set[int]) -> tuple[bool, int | None] | None:
    """
    Description:
        Reads the season picker: "auto" or the id of a live season of the game's player.

    Parameters:
        value: the season picker's value.
        season_ids: the live season ids of the game's player.

    Return Values:
        tuple[bool, int | None] | None: (season_is_manual, season_id) — (False, None) for
        Automatic — or None when the value is not allowed.

    Notes:
        Pure.
    """
    if value in (None, "", SEASON_AUTO):
        return False, None
    if isinstance(value, str) and value.isascii() and value.isdigit():
        season_id = int(value)
        if season_id in season_ids:
            return True, season_id
    return None


def _parse_texts(values: dict) -> tuple[dict[str, str | None] | None, str | None]:
    """
    Description:
        Reads team, opponent and location (each at most GAME_TEXT_MAX_LENGTH characters)
        and the opponent and teammate names (at most GAME_TEAMMATES_MAX_LENGTH).

    Parameters:
        values: the form values ("team", "opponent", "location", "teammates").

    Return Values:
        tuple: ({"team_name", "opponent_name", "location", "teammate_names"}, None), or
        (None, the message).

    Notes:
        Pure. Trimmed; empty is None.
    """
    texts: dict[str, str | None] = {}
    for key, column, label in (
        ("team", "team_name", "Team"),
        ("opponent", "opponent_name", "Opponent"),
        ("location", "location", "Location"),
    ):
        text = _clean_text(values.get(key))
        if text is not None and len(text) > GAME_TEXT_MAX_LENGTH:
            return None, MESSAGE_TEXT_TOO_LONG.format(field=label)
        texts[column] = text
    teammates = _clean_text(values.get("teammates"))
    if teammates is not None and len(teammates) > GAME_TEAMMATES_MAX_LENGTH:
        return None, MESSAGE_TEAMMATES
    texts["teammate_names"] = teammates
    return texts, None


def parse_game_form(
    values: dict, codes: dict[str, set[str]], season_ids: set[int]
) -> tuple[dict[str, object] | None, str | None]:
    """
    Description:
        Checks the game form in screen order (plan item 2.2): category, format, date, team /
        opponent / location, opponent and teammate names, home/away, target score (practice
        games), scores (both or neither, 0 to 999), minutes (0 to 99, one decimal), the
        seven counters (game page only), notes, season.

    Parameters:
        values: {"category", "format", "date", "team", "opponent", "location", "teammates",
            "home_away", "target", "team_score", "opponent_score", "minutes", "notes",
            "season"} and, on the game page only, "counters" ({code: value}).
        codes: {"categories": live category codes, "formats": live format codes}.
        season_ids: the live season ids of the game's player.

    Return Values:
        tuple[dict[str, object] | None, str | None]: (fields, None) with the keys of
        data_games.insert_game (plus the seven counters when "counters" was given), or
        (None, the first message).

    Notes:
        Pure. Teammate names and the target score are kept only for the Practice game
        category, else stored NULL (D8).
    """
    category = values.get("category")
    if not isinstance(category, str) or category not in codes["categories"]:
        return None, MESSAGE_CATEGORY
    game_format = values.get("format")
    if not isinstance(game_format, str) or game_format not in codes["formats"]:
        return None, MESSAGE_FORMAT
    game_date = _parse_date(values.get("date"))
    if game_date is None:
        return None, MESSAGE_DATE
    texts, message = _parse_texts(values)
    if texts is None:
        return None, message
    home_away = values.get("home_away")
    if home_away in (None, "", HOME_AWAY_NOT_SET):
        home_away = None
    elif not isinstance(home_away, str) or home_away not in STORED_HOME_AWAY:
        return None, MESSAGE_HOME_AWAY

    is_practice_game = category == PRACTICE_GAME_CODE
    target: int | None = None
    if is_practice_game:
        target_ok, target = parse_whole(values.get("target"))
        if not target_ok or (target is not None and not 1 <= target <= GAME_TARGET_MAX):
            return None, MESSAGE_TARGET
    (team_score, opponent_score), message = _parse_scores(
        values.get("team_score"), values.get("opponent_score")
    )
    if message is not None:
        return None, message
    minutes_ok, minutes = parse_minutes(values.get("minutes"))
    if not minutes_ok:
        return None, MESSAGE_MINUTES

    fields: dict[str, object] = {}
    raw_counters = values.get("counters")
    if isinstance(raw_counters, dict):
        counters, message = _parse_counters(raw_counters)
        if counters is None:
            return None, message
        fields.update(counters)
    notes = _clean_text(values.get("notes"))
    if notes is not None and len(notes) > GAME_NOTES_MAX_LENGTH:
        return None, MESSAGE_NOTES
    season = _parse_season(values.get("season"), season_ids)
    if season is None:
        return None, MESSAGE_SEASON

    fields.update(
        {
            "game_category_code": category,
            "game_format_code": game_format,
            "game_date": game_date,
            "team_name": texts["team_name"],
            "opponent_name": texts["opponent_name"],
            "location": texts["location"],
            "teammate_names": texts["teammate_names"] if is_practice_game else None,
            "home_away": home_away,
            "target_score": target if is_practice_game else None,
            "team_score": team_score,
            "opponent_score": opponent_score,
            "minutes_played": minutes,
            "notes": notes,
            "season_is_manual": season[0],
            "season_id": season[1],
        }
    )
    return fields, None


def duplicate_signature(player_id: int, game_date: date, opponent: str) -> str:
    """
    Description:
        The signature of a duplicate warning (REQ-063, D5): the game's player id, "|", the
        ISO date, "|", the lower-case trimmed opponent. A second tap with the same signature
        saves anyway.

    Parameters:
        player_id: the game's player (a player id, never a user id; COD-8-01).
        game_date: the game's date.
        opponent: the opponent as typed.

    Return Values:
        str: e.g. "12|2026-10-18|eagles".

    Notes:
        Pure. The player is part of the signature so a warning seen for one player is not
        taken as the acknowledgement of another player's duplicate (games review_1, Minor).
    """
    return f"{player_id}|{game_date.isoformat()}|{opponent.strip().lower()}"


def fields_from_game_row(row: dict) -> dict[str, object]:
    """
    Description:
        The update_game fields of a stored (locked) game, so Finish game rewrites the record
        unchanged apart from the scores and minutes.

    Parameters:
        row: data_games.lock_game row.

    Return Values:
        dict[str, object]: the record keys of data_games.update_game plus the seven
        counters, as stored.

    Notes:
        Pure.
    """
    fields: dict[str, object] = {
        key: row.get(key)
        for key in (
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
    }
    for code in GAME_COUNTER_CODES:
        fields[code] = row.get(code)
    return fields


def _stored_id(value: object) -> int | None:
    """
    Description:
        Reads an id store (game-id).

    Parameters:
        value: the store's data.

    Return Values:
        int | None: the id, or None when it is not an int (a bool is not an id).

    Notes:
        Pure.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _has_pending(
    stat_queue: object, stat_outbox: object, court_queue: object, court_outbox: object
) -> bool:
    """
    Description:
        Whether the browser still holds +1 stats or shots that are not saved (plan §3.2 A3).

    Parameters:
        stat_queue: game-stat-queue.data.
        stat_outbox: game-stat-outbox.data.
        court_queue: court-queue.data.
        court_outbox: court-outbox.data.

    Return Values:
        bool: True when a queue is not empty or a command is in flight.

    Notes:
        Pure.
    """
    return (
        bool(stat_queue)
        or stat_outbox is not None
        or bool(court_queue)
        or court_outbox is not None
    )


# --- Page builders (REQ-018 to REQ-024, REQ-061, REQ-064) ---

def _games_content_for(player_id: int) -> list[Component]:
    """
    Description:
        Reads and builds games-content (averages and grid) for a player.

    Parameters:
        player_id: the selected player. Never a user id (COD-8-01).

    Return Values:
        list[Component]: games_content children.

    Notes:
        Exceptions propagate to the caller (router or picker callback).
    """
    session = data_handler.get_session()
    averages = get_game_averages(session, player_id)
    rows = list_games(session, player_id)
    definitions = list_stat_definitions(session)
    game_count = int((averages or {}).get("games_played") or 0)
    return games_content(average_tiles(averages), game_count, rows, definitions)


def build_games_page(player_id: int, clock: DeviceClock, params: dict[str, str]) -> Component:
    """
    Description:
        Builds /games: the player picker, New game, the per-game averages and the games grid
        of the selected player (REQ-024, REQ-048).

    Parameters:
        player_id: the selected player from current_player_id() (never None here). Never a
            user id (COD-8-01).
        clock: the device clock (unused).
        params: the route parameters (none).

    Return Values:
        Component: the page.

    Notes:
        Exceptions propagate to the router, which shows the error page with a reference.
    """
    players = list_live_players(data_handler.get_session())
    return games_layout(players, player_id, _games_content_for(player_id))


def build_game_new_page(player_id: int, clock: DeviceClock, params: dict[str, str]) -> Component:
    """
    Description:
        Builds the New game form, the selected player preselected, the date the device's
        today, team and location the player's last values (REQ-018, REQ-062).

    Parameters:
        player_id: the selected player; only the picker's default (Context Adaptation
            Table). Never a user id.
        clock: the device clock; clock["today"] is the default date (never the server's).
        params: the route parameters (none).

    Return Values:
        Component: the page.

    Notes:
        None.
    """
    session = data_handler.get_session()
    return game_new_layout(
        list_live_players(session),
        player_id,
        list_game_categories(session),
        list_game_formats(session),
        list_seasons(session, player_id),
        get_entry_suggestions(session, player_id),
        clock["today"],
    )


def _game_from_params(session: Session, params: dict[str, str]) -> dict | None:
    """
    Description:
        Loads the live game of the URL.

    Parameters:
        session: the request session.
        params: {"game_id": "<digits>"}.

    Return Values:
        dict | None: data_games.get_game row, or None when not live.

    Notes:
        None.
    """
    return get_game(session, int(params["game_id"]))


def build_game_page(player_id: int, clock: DeviceClock, params: dict[str, str]) -> Component:
    """
    Description:
        Builds the game page: record, final score, after-game totals, shooting tiles,
        audit lines and Delete (REQ-004 to REQ-006, REQ-020, REQ-022, REQ-023).

    Parameters:
        player_id: the selected player (unused: the game's player comes from its row).
        clock: the device clock; its zone formats the audit times.
        params: {"game_id": "<digits>"} from /games/<id>.

    Return Values:
        Component: the page, or the not-found page when the game is not live.

    Notes:
        The game-id store is written by the layout only after this get_game check.
    """
    session = data_handler.get_session()
    game = _game_from_params(session, params)
    if game is None:
        return not_found_page()
    game_id = int(game["game_id"])
    game_player_id = int(game["player_id"])
    zone = clock["zone"]
    audit_texts = (
        format_entry_datetime(game.get("created_at"), zone),
        format_entry_datetime(game.get("updated_at"), zone),
    )
    return game_page_layout(
        game,
        list_game_categories(session),
        list_game_formats(session),
        list_seasons(session, game_player_id),
        get_entry_suggestions(session, game_player_id),
        shooting_tiles(get_game_box_score(session, game_id)),
        list_stat_definitions(session),
        audit_texts,
    )


def build_game_live_page(
    player_id: int, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds live mode (REQ-021): the shared entry panel for the game and the +1 stats.

    Parameters:
        player_id: the selected player (unused: the entry player is the game's player).
        clock: the device clock (unused while building).
        params: {"game_id": "<digits>"} from /games/<id>/live.

    Return Values:
        Component: the page, or the not-found page when the game is not live.

    Notes:
        entry-parent {"kind": "game", "id"} and the fixed court-player (the game's
        player_id) are written only after get_game (Context Adaptation Table); every entry
        write re-checks both in SQL (lock_entry_owner). The base tallies are
        get_entry_totals of the game (plan item 1.12, shipped by the entry layer).
    """
    session = data_handler.get_session()
    game = _game_from_params(session, params)
    if game is None:
        return not_found_page()
    game_id = int(game["game_id"])
    counters = {code: int(game.get(code) or 0) for code in GAME_COUNTER_CODES}
    return game_live_layout(
        game,
        counters,
        get_entry_totals(session, EntryParent(PARENT_KIND_GAME, game_id)),
        list_active_court_spots(session),
        list_shot_types(session),
        list_stat_definitions(session),
    )


def build_game_summary_page(
    player_id: int, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds the post-game summary (REQ-061): result, highlights, box score and the
        game's shot chart.

    Parameters:
        player_id: the selected player (unused).
        clock: the device clock (unused).
        params: {"game_id": "<digits>"} from /games/<id>/summary.

    Return Values:
        Component: the page, or the not-found page when the game is not live.

    Notes:
        The summary is available for any live game; before the final score the result line
        says "No final score yet".
    """
    session = data_handler.get_session()
    game = _game_from_params(session, params)
    if game is None:
        return not_found_page()
    game_id = int(game["game_id"])
    return game_summary_layout(
        game,
        highlight_lines(get_game_highlights(session, game_id)),
        summary_tiles(get_game_box_score(session, game_id)),
        list_stat_definitions(session),
        get_game_heat_map(session, game_id),
    )


# --- Callbacks: games list (REQ-024, REQ-048) ---

def select_games_player(value: str | None) -> list[Component] | str:
    """
    Description:
        The games player picker: makes the picked player the selected player and rebuilds
        the averages and the grid for that player.

    Parameters:
        value: games-player.value, the player id as a string.

    Return Values:
        list[Component] | str: the new children of games-content, or a message with a
        reference when the games could not be read.

    Notes:
        A boundary (COD-4-04). As select_home_player: a value that is not a player id, or a
        player no longer live (warning), changes nothing (PreventUpdate).
    """
    player_id = parse_player_value(value)
    if player_id is None:
        raise PreventUpdate
    try:
        if not set_current_player_id(player_id):
            logger.warning("Games picker chose player %s, which is not a live player", player_id)
            raise PreventUpdate
        return _games_content_for(player_id)
    except PreventUpdate:
        raise
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Games player change failed (ref %s)", ref)
        return MESSAGE_LOAD_FAILED.format(ref=ref)


def open_game_from_grid(cell: dict | None) -> str:
    """
    Description:
        A click on a row of the games grid opens that game's page.

    Parameters:
        cell: games-grid.cellClicked ({"rowId", "colId", ...}).

    Return Values:
        str: url.pathname "/games/<game_id>".

    Notes:
        Raises PreventUpdate when there is no row id of digits (D9: the grid is read-only).
    """
    if not isinstance(cell, dict):
        raise PreventUpdate
    row_id = cell.get("rowId")
    if not isinstance(row_id, str) or not row_id.isascii() or not row_id.isdigit():
        raise PreventUpdate
    return f"{GAMES_PATH}/{int(row_id)}"


# --- Callbacks: New game (REQ-018, REQ-062, REQ-063) ---

def game_new_player_changed(value: str | None) -> tuple[object, object, object, object]:
    """
    Description:
        A new player in the New game form: that player's seasons in the season picker
        (value Automatic) and that player's last team and location (REQ-062).

    Parameters:
        value: game-new-player.value.

    Return Values:
        tuple: game-new-season.data, game-new-season.value ("auto"), game-new-team.value and
        game-new-location.value ("" when the player has none); no_update for each when the
        value is not a live player or the read fails (logged).

    Notes:
        A boundary (COD-4-04); the form's own check refuses a player that is not live.
    """
    player_id = parse_player_value(value)
    if player_id is None:
        raise PreventUpdate
    try:
        session = data_handler.get_session()
        live_ids = {int(row["player_id"]) for row in list_live_players(session)}
        if player_id not in live_ids:
            logger.warning("New game picker chose player %s, which is not live", player_id)
            raise PreventUpdate
        seasons = list_seasons(session, player_id)
        suggestions = get_entry_suggestions(session, player_id)
    except PreventUpdate:
        raise
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "New game player change failed (ref %s)", ref)
        return no_update, no_update, no_update, no_update
    return (
        season_options(seasons),
        SEASON_AUTO,
        suggestions.get("last_team") or "",
        suggestions.get("last_location") or "",
    )


def _form_codes(session: Session) -> dict[str, set[str]]:
    """
    Description:
        The live category and format codes the form accepts.

    Parameters:
        session: the request session.

    Return Values:
        dict[str, set[str]]: {"categories", "formats"}.

    Notes:
        None.
    """
    return {
        "categories": {str(row["code"]) for row in list_game_categories(session)},
        "formats": {str(row["code"]) for row in list_game_formats(session)},
    }


def _duplicate_warning(
    session: Session,
    player_id: int,
    fields: dict[str, object],
    exclude_game_id: int | None,
    dup_ack: object,
) -> tuple[str, str] | None:
    """
    Description:
        The duplicate-game warning (REQ-063, D5), unless the user already saw it for this
        player, date and opponent.

    Parameters:
        session: the request session.
        player_id: the game's player.
        fields: the parsed form (game_date, opponent_name).
        exclude_game_id: the edited game, or None on create.
        dup_ack: the *-dup-ack store: the signature of the warning already shown.

    Return Values:
        tuple[str, str] | None: (message, signature) to show and store, or None to save.

    Notes:
        Skipped without an opponent.
    """
    opponent = fields.get("opponent_name")
    game_date = fields.get("game_date")
    if not isinstance(opponent, str) or not isinstance(game_date, date):
        return None
    signature = duplicate_signature(player_id, game_date, opponent)
    if dup_ack == signature:
        return None
    if not find_duplicate_games(session, player_id, game_date, opponent, exclude_game_id):
        return None
    message = MESSAGE_DUPLICATE.format(opponent=opponent, date=format_game_date(game_date))
    return message, signature


def _form_values(
    category: object,
    game_format: object,
    game_date: object,
    team: object,
    opponent: object,
    teammates: object,
    location: object,
    home_away: object,
    target: object,
    season: object,
    notes: object,
) -> dict[str, object]:
    """
    Description:
        The record fields' values as parse_game_form reads them.

    Parameters:
        category, game_format, game_date, team, opponent, teammates, location, home_away,
            target, season, notes: the *-category ... *-notes values.

    Return Values:
        dict[str, object]: the values dict (no scores, minutes or counters).

    Notes:
        Pure.
    """
    return {
        "category": category,
        "format": game_format,
        "date": game_date,
        "team": team,
        "opponent": opponent,
        "teammates": teammates,
        "location": location,
        "home_away": home_away,
        "target": target,
        "season": season,
        "notes": notes,
    }


def create_game(
    live_clicks: int | None,
    save_clicks: int | None,
    player_value: str | None,
    category: str | None,
    game_format: str | None,
    game_date: str | None,
    team: str | None,
    opponent: str | None,
    teammates: str | None,
    location: str | None,
    home_away: str | None,
    target: object,
    season: str | None,
    notes: str | None,
    dup_ack: object,
) -> tuple[str, object, object]:
    """
    Description:
        Start live game / Save game: checks the form, warns once about a duplicate, creates
        the game and opens its live page or its game page (REQ-018, REQ-063).

    Parameters:
        live_clicks: game-new-live.n_clicks.
        save_clicks: game-new-save.n_clicks.
        player_value: game-new-player.value, the game's player (Context Adaptation Table).
        category ... notes: the game-new-* field values.
        dup_ack: game-new-dup-ack.data.

    Return Values:
        tuple[str, object, object]: game-new-feedback text, url.pathname
        ("/games/<id>/live" or "/games/<id>" on success, else no_update) and
        game-new-dup-ack.data (the signature when warning, else no_update).

    Notes:
        A boundary (COD-4-04). Raises PreventUpdate without a click: url lives in the shell,
        so Dash also runs this when the router inserts the page. The player is checked live
        here and by the insert (and the season trigger).
    """
    trigger = ctx.triggered_id
    clicks = {"game-new-live": live_clicks, "game-new-save": save_clicks}
    if trigger not in clicks or not clicks[trigger]:
        raise PreventUpdate
    player_id = parse_player_value(player_value)
    try:
        session = data_handler.get_session()
        live_ids = {int(row["player_id"]) for row in list_live_players(session)}
        if player_id is None or player_id not in live_ids:
            return MESSAGE_CHOOSE_PLAYER, no_update, no_update
        season_ids = {int(row["season_id"]) for row in list_seasons(session, player_id)}
        fields, message = parse_game_form(
            _form_values(
                category, game_format, game_date, team, opponent, teammates, location,
                home_away, target, season, notes,
            ),
            _form_codes(session),
            season_ids,
        )
        if fields is None:
            return message or "", no_update, no_update
        warning = _duplicate_warning(session, player_id, fields, None, dup_ack)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Game form check failed (ref %s)", ref)
        return MESSAGE_SAVE_FAILED.format(ref=ref), no_update, no_update
    if warning is not None:
        return warning[0], no_update, warning[1]
    game_player_id: int = player_id
    checked: dict[str, object] = fields

    result = run_write(
        current_user.user_id,
        lambda write_session: insert_game(write_session, game_player_id, checked),
        "create game",
    )
    if not result.ok:
        return MESSAGE_SAVE_FAILED.format(ref=result.ref), no_update, no_update
    logger.info("Created game %s for player %s", result.value, game_player_id)
    if trigger == "game-new-live":
        return "", f"{GAMES_PATH}/{result.value}/live", no_update
    return "", f"{GAMES_PATH}/{result.value}", no_update


# --- Callbacks: game page (REQ-020, REQ-022, REQ-023, REQ-005, REQ-006) ---

def _changed_identity(stored: dict, fields: dict[str, object]) -> bool:
    """
    Description:
        Whether the date or the opponent (trimmed, case-insensitive) differs from the stored
        game, which is when an edit re-runs the duplicate check (D5).

    Parameters:
        stored: data_games.get_game row.
        fields: the parsed form.

    Return Values:
        bool: True when either changed.

    Notes:
        Pure.
    """
    stored_opponent = str(stored.get("opponent_name") or "").strip().lower()
    new_opponent = str(fields.get("opponent_name") or "").strip().lower()
    return stored.get("game_date") != fields.get("game_date") or stored_opponent != new_opponent


def save_game(
    n_clicks: int | None,
    game_id: object,
    category: str | None,
    game_format: str | None,
    game_date: str | None,
    team: str | None,
    opponent: str | None,
    teammates: str | None,
    location: str | None,
    home_away: str | None,
    target: object,
    season: str | None,
    notes: str | None,
    team_score: object,
    opponent_score: object,
    minutes: object,
    off_rebounds: object,
    def_rebounds: object,
    assists: object,
    steals: object,
    blocks: object,
    turnovers: object,
    fouls: object,
    dup_ack: object,
) -> tuple[str, str, object, object]:
    """
    Description:
        Save on the game page: checks the form, warns once about a duplicate when the date
        or opponent changed, saves the record and the after-game totals (REQ-022, REQ-023).
        A game that this save completes (both scores now present, D2) opens its summary.

    Parameters:
        n_clicks: game-edit-save.n_clicks.
        game_id: game-id.data (written after get_game). Never a player or user id.
        category ... notes: the game-edit-* record values.
        team_score, opponent_score, minutes: the final score and minutes.
        off_rebounds ... fouls: the seven game-edit-<code> totals.
        dup_ack: game-edit-dup-ack.data.

    Return Values:
        tuple[str, str, object, object]: game-edit-saved text ("Saved." or ""),
        game-edit-feedback text, url.pathname ("/games/<id>/summary" or no_update) and
        game-edit-dup-ack.data.

    Notes:
        A boundary (COD-4-04). The absolute totals overwrite live taps made elsewhere
        meanwhile (plan R2). A game deleted meanwhile fails with a reference.
    """
    if not n_clicks:
        raise PreventUpdate
    stored_id = _stored_id(game_id)
    if stored_id is None:
        ref = new_reference_code()
        logger.error("Save game refused (ref %s): game-id is not an id", ref)
        return "", MESSAGE_SAVE_FAILED.format(ref=ref), no_update, no_update
    counter_values = (off_rebounds, def_rebounds, assists, steals, blocks, turnovers, fouls)
    values = _form_values(
        category, game_format, game_date, team, opponent, teammates, location, home_away,
        target, season, notes,
    )
    values.update(
        {
            "team_score": team_score,
            "opponent_score": opponent_score,
            "minutes": minutes,
            "counters": dict(zip(GAME_COUNTER_CODES, counter_values, strict=True)),
        }
    )
    try:
        session = data_handler.get_session()
        stored = get_game(session, stored_id)
        if stored is None:
            raise LookupError(f"game {stored_id} is not live")
        game_player_id = int(stored["player_id"])
        season_ids = {int(row["season_id"]) for row in list_seasons(session, game_player_id)}
        fields, message = parse_game_form(values, _form_codes(session), season_ids)
        if fields is None:
            return "", message or "", no_update, no_update
        warning = (
            _duplicate_warning(session, game_player_id, fields, stored_id, dup_ack)
            if _changed_identity(stored, fields)
            else None
        )
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Game save check failed (ref %s)", ref)
        return "", MESSAGE_SAVE_FAILED.format(ref=ref), no_update, no_update
    if warning is not None:
        return "", warning[0], no_update, warning[1]
    edited_id: int = stored_id
    checked: dict[str, object] = fields

    def work(write_session: Session) -> None:
        """
        Description:
            Updates the live game, failing when it is no longer live.

        Parameters:
            write_session: the write session.

        Return Values:
            None.

        Notes:
            The LookupError rolls back and gives the user a reference.
        """
        if not update_game(write_session, edited_id, checked):
            raise LookupError(f"game {edited_id} is not live")

    result = run_write(current_user.user_id, work, "save game")
    if not result.ok:
        return "", MESSAGE_SAVE_FAILED.format(ref=result.ref), no_update, no_update
    logger.info("Saved game %s", edited_id)
    completed_now = (
        stored.get("entry_status") == ENTRY_STATUS_IN_PROGRESS
        and checked.get("team_score") is not None
        and checked.get("opponent_score") is not None
    )
    if completed_now:
        return "", "", f"{GAMES_PATH}/{edited_id}/summary", no_update
    return MESSAGE_SAVED, "", no_update, no_update


def open_delete_game(n_clicks: int | None) -> tuple[bool, str, str]:
    """
    Description:
        Delete game: opens the confirmation (REQ-005).

    Parameters:
        n_clicks: game-delete-open.n_clicks.

    Return Values:
        tuple[bool, str, str]: game-delete-modal.opened (True), game-delete-text (the
        question) and game-delete-feedback ("").

    Notes:
        No database access.
    """
    if not n_clicks:
        raise PreventUpdate
    return True, MESSAGE_DELETE_QUESTION, ""


def confirm_delete_game(n_clicks: int | None, game_id: object) -> tuple[object, str]:
    """
    Description:
        The Delete button of the confirmation: hides the game and its entries through
        h_data.fn_soft_delete('game') and opens the games list (REQ-006, D14).

    Parameters:
        n_clicks: game-delete-confirm.n_clicks.
        game_id: game-id.data.

    Return Values:
        tuple[object, str]: url.pathname ("/games" on success, else no_update) and
        game-delete-feedback text.

    Notes:
        A boundary (COD-4-04). Raises PreventUpdate without a click (router insertion
        trap).
    """
    if not n_clicks:
        raise PreventUpdate
    stored_id = _stored_id(game_id)
    if stored_id is None:
        ref = new_reference_code()
        logger.error("Delete game refused (ref %s): game-id is not an id", ref)
        return no_update, MESSAGE_DELETE_FAILED.format(ref=ref)
    deleted_id: int = stored_id
    result = run_write(
        current_user.user_id,
        lambda write_session: soft_delete_game(write_session, deleted_id),
        "delete game",
    )
    if not result.ok:
        return no_update, MESSAGE_DELETE_FAILED.format(ref=result.ref)
    logger.info("Deleted game %s (%s rows marked)", deleted_id, result.value)
    return GAMES_PATH, ""


# --- Callbacks: live +1 stats (REQ-010, REQ-021; plan §3.3, D3) ---

STAT_KEY_MAX_LENGTH = 64
GAME_STAT_ACK_STORE_ID = "game-stat-ack"
GAME_STAT_BASE_STORE_ID = "game-stat-base"
STORE_PROPERTY = "data"


def validate_game_stat_command(outbox: object, game_id: object) -> str | None:
    """
    Description:
        Checks a +1 command strictly before anything is written.

    Parameters:
        outbox: game-stat-outbox.data: {"op": "add" | "undo", "key", "attempt", "sent_at",
            "stat", "expected"} (an undo also carries "target_key", ignored here).
        game_id: game-id.data.

    Return Values:
        str | None: the reason the command is refused, or None when it is valid.

    Notes:
        Pure. Whether the game is live is checked in SQL (apply_game_stat).
    """
    if not isinstance(outbox, dict):
        return "command is not an object"
    if not _is_int(game_id):
        return "game id is not an integer"
    if outbox.get("op") not in (OP_ADD, OP_UNDO):
        return "op is not add or undo"
    key = outbox.get("key")
    if not isinstance(key, str) or not key or len(key) > STAT_KEY_MAX_LENGTH:
        return "command key is invalid"
    if not _is_int(outbox.get("attempt")):
        return "attempt is not an integer"
    stat = outbox.get("stat")
    if not isinstance(stat, str) or stat not in GAME_COUNTER_CODES:
        return "stat is not a game counter"
    expected = outbox.get("expected")
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
        return "expected is not a whole number"
    return None


def game_stat_ack(
    outbox: object,
    ok: bool,
    values: dict[str, int] | None,
    saved_at: str | None,
    ref: str | None,
) -> dict[str, object]:
    """
    Description:
        Builds the ack for game-stat-ack, copying key, attempt and op from the command.

    Parameters:
        outbox: the command as received (copied from even when invalid).
        ok: whether the command was carried out.
        values: the seven counters after the command (or the current ones after a
            failure), or None when they could not be read.
        saved_at: "HH:MM:SS" on the device's clock, else None.
        ref: the failure's reference code, else None.

    Return Values:
        dict[str, object]: {"key", "attempt", "op", "ok", "values", "saved_at", "ref"}.

    Notes:
        Pure. The dispatcher ignores an ack whose key, attempt and op do not match its
        outbox.
    """
    source = outbox if isinstance(outbox, dict) else {}
    return {
        "key": source.get("key"),
        "attempt": source.get("attempt"),
        "op": source.get("op"),
        "ok": ok,
        "values": values,
        "saved_at": saved_at,
        "ref": ref,
    }


def _current_counters(game_id: int) -> dict[str, int] | None:
    """
    Description:
        Reads the game's counters for a failed ack, so a Retry sends a fresh expected value.

    Parameters:
        game_id: the game.

    Return Values:
        dict[str, int] | None: the counters, or None when the game is not live or the read
        fails (logged).

    Notes:
        A failure here is logged as a warning (class only, never the error text, COD-4-05)
        and not raised: the ack still reports the original failure, without values.
    """
    try:
        return get_game_counters(data_handler.get_session(), game_id)
    except Exception as exc:
        logger.warning(
            "Could not read the game counters for a failed +1 ack (%s)", type(exc).__name__
        )
        return None


def _deliver(values: dict[str, int] | None, ack: dict[str, object]) -> None:
    """
    Description:
        Delivers the counters into game-stat-base (when known), then the ack into
        game-stat-ack, with dash.set_props. The only writer of game-stat-ack.

    Parameters:
        values: the counters, or None.
        ack: the ack.

    Return Values:
        None.

    Notes:
        A set_props value starts a fresh callback chain, so the dispatcher's game-stat-ack
        Input fires without the loop Dash 4.4.1 prunes (plan §3.3, as court-ack). Call at
        most once per run.
    """
    if values is not None:
        set_props(GAME_STAT_BASE_STORE_ID, {STORE_PROPERTY: values})
    set_props(GAME_STAT_ACK_STORE_ID, {STORE_PROPERTY: ack})


def _carry_out_stat(
    command: dict, game_id: int, zone_name: str, now_utc: datetime
) -> tuple[dict[str, object], dict[str, int] | None]:
    """
    Description:
        Applies one validated +1 command in one transaction and builds its ack.

    Parameters:
        command: the validated command.
        game_id: the game (game-id store); re-checked live in SQL.
        zone_name: the device's IANA zone for the save time.
        now_utc: the server's current time, aware UTC.

    Return Values:
        tuple[dict[str, object], dict[str, int] | None]: the ack and the counters to put in
        game-stat-base (None when unknown).

    Notes:
        A command whose counter was changed elsewhere (compare-and-set refused) is an
        ok: false ack with a reference and the current values (logged as an error, since
        the user sees "Save failed").
    """
    stat = str(command["stat"])
    expected = int(command["expected"])
    op = str(command["op"])
    result = run_write(
        current_user.user_id,
        lambda write_session: apply_game_stat(write_session, game_id, stat, expected, op),
        "game stat",
    )
    if not result.ok or result.value is None:
        values = _current_counters(game_id)
        return game_stat_ack(command, False, values, None, result.ref), values
    outcome = result.value
    raw_values = outcome.get("values")
    values = raw_values if isinstance(raw_values, dict) else None
    if outcome.get("applied"):
        saved_at = format_device_time(now_utc, zone_name)
        return game_stat_ack(command, True, values, saved_at, None), values
    ref = new_reference_code()
    logger.error(
        "Game %s %s %s refused (ref %s): changed elsewhere, expected %s",
        game_id, stat, op, ref, expected,
    )
    return game_stat_ack(command, False, values, None, ref), values


def execute_game_stat_command(
    outbox: dict | None, game_id: object, clock: dict | None
) -> None:
    """
    Description:
        The +1 channel's server callback: carries out the single command in flight and
        answers through set_props (game-stat-base, then game-stat-ack).

    Parameters:
        outbox: game-stat-outbox.data, the command in flight, or None.
        game_id: game-id.data.
        clock: device-clock.data, for the save time's zone.

    Return Values:
        None (no Output; Dash rejects any other value from a no-output callback).

    Notes:
        Raises PreventUpdate for an empty outbox. A boundary: every failure, a refused
        command included, is an ok: false ack with a reference, never an exception
        (COD-4-04).
    """
    if outbox is None:
        raise PreventUpdate
    reason = validate_game_stat_command(outbox, game_id)
    stored_game_id = _stored_id(game_id)
    if reason is not None or stored_game_id is None:
        ref = new_reference_code()
        logger.error("Game stat command refused (ref %s): %s", ref, reason)
        _deliver(None, game_stat_ack(outbox, False, None, None, ref))
        return None
    if not current_user.is_authenticated:
        ref = new_reference_code()
        logger.error("Game stat command refused (ref %s): not signed in", ref)
        _deliver(None, game_stat_ack(outbox, False, None, None, ref))
        return None
    try:
        ack, values = _carry_out_stat(
            outbox,
            stored_game_id,
            normalise_clock(clock)["zone"],
            datetime.now(timezone.utc),
        )
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Game stat command failed (ref %s)", ref)
        _deliver(None, game_stat_ack(outbox, False, None, None, ref))
        return None
    _deliver(values, ack)
    return None


# --- Callbacks: Finish game (REQ-061; plan §3.4, D2) ---

def open_finish_game(
    n_clicks: int | None,
    game_id: object,
    stat_queue: list | None,
    stat_outbox: dict | None,
    court_queue: list | None,
    court_outbox: dict | None,
) -> tuple[object, str, object, object, object]:
    """
    Description:
        Finish game: refuses while +1 stats or shots are unsaved; otherwise opens the dialog
        prefilled with the stored scores and minutes.

    Parameters:
        n_clicks: game-finish-open.n_clicks.
        game_id: game-id.data.
        stat_queue: game-stat-queue.data.
        stat_outbox: game-stat-outbox.data.
        court_queue: court-queue.data (plan §3.2 A3).
        court_outbox: court-outbox.data.

    Return Values:
        tuple: game-finish-modal.opened, game-finish-feedback text, and the values of
        game-finish-team-score, game-finish-opponent-score, game-finish-minutes.

    Notes:
        A boundary (COD-4-04).
    """
    if not n_clicks:
        raise PreventUpdate
    if _has_pending(stat_queue, stat_outbox, court_queue, court_outbox):
        return no_update, MESSAGE_WAIT_UNSAVED, no_update, no_update, no_update
    try:
        stored_id = _stored_id(game_id)
        game = get_game(data_handler.get_session(), stored_id) if stored_id is not None else None
        if game is None:
            raise LookupError("game is not live")
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Finish game dialog failed (ref %s)", ref)
        return no_update, MESSAGE_SAVE_FAILED.format(ref=ref), no_update, no_update, no_update
    minutes = game.get("minutes_played")
    return (
        True,
        "",
        game.get("team_score") if game.get("team_score") is not None else "",
        game.get("opponent_score") if game.get("opponent_score") is not None else "",
        float(str(minutes)) if minutes is not None else "",
    )


def _parse_finish(
    team_score: object, opponent_score: object, minutes: object
) -> tuple[tuple[int, int, Decimal | None] | None, str | None]:
    """
    Description:
        Checks the Finish game dialog: both scores required, whole 0 to 999; minutes
        optional, 0 to 99 with one decimal.

    Parameters:
        team_score: game-finish-team-score.value.
        opponent_score: game-finish-opponent-score.value.
        minutes: game-finish-minutes.value.

    Return Values:
        tuple: ((team, opponent, minutes), None), or (None, the message).

    Notes:
        Pure.
    """
    if _is_empty(team_score) or _is_empty(opponent_score):
        return None, MESSAGE_FINISH_SCORES
    (team, opponent), message = _parse_scores(team_score, opponent_score)
    if message is not None or team is None or opponent is None:
        return None, message or MESSAGE_SCORES
    minutes_ok, parsed_minutes = parse_minutes(minutes)
    if not minutes_ok:
        return None, MESSAGE_MINUTES
    return (team, opponent, parsed_minutes), None


def confirm_finish_game(
    n_clicks: int | None,
    game_id: object,
    team_score: object,
    opponent_score: object,
    minutes: object,
    stat_queue: list | None,
    stat_outbox: dict | None,
    court_queue: list | None,
    court_outbox: dict | None,
) -> tuple[object, str]:
    """
    Description:
        Save and finish: stores the final score and minutes, which completes the game (D2),
        and opens the post-game summary (REQ-061).

    Parameters:
        n_clicks: game-finish-confirm.n_clicks.
        game_id: game-id.data.
        team_score, opponent_score, minutes: the dialog's values.
        stat_queue, stat_outbox, court_queue, court_outbox: the pending stores (checked
            again, as on opening).

    Return Values:
        tuple[object, str]: url.pathname ("/games/<id>/summary" on success, else no_update)
        and game-finish-modal-feedback text.

    Notes:
        A boundary (COD-4-04). The stored record and counters are read under the row lock
        in the same transaction (lock_game), so live taps saved meanwhile are kept. Raises
        PreventUpdate without a click (router insertion trap).
    """
    if not n_clicks:
        raise PreventUpdate
    if _has_pending(stat_queue, stat_outbox, court_queue, court_outbox):
        return no_update, MESSAGE_WAIT_UNSAVED
    stored_id = _stored_id(game_id)
    if stored_id is None:
        ref = new_reference_code()
        logger.error("Finish game refused (ref %s): game-id is not an id", ref)
        return no_update, MESSAGE_SAVE_FAILED.format(ref=ref)
    parsed, message = _parse_finish(team_score, opponent_score, minutes)
    if parsed is None:
        return no_update, message or ""
    finished_id: int = stored_id
    final_team, final_opponent, final_minutes = parsed

    def work(write_session: Session) -> None:
        """
        Description:
            Locks the game, then rewrites its stored record with the final score and
            minutes.

        Parameters:
            write_session: the write session.

        Return Values:
            None.

        Notes:
            LookupError (game not live) rolls back and gives the user a reference.
        """
        row = lock_game(write_session, finished_id)
        if row is None:
            raise LookupError(f"game {finished_id} is not live")
        fields = fields_from_game_row(row)
        fields.update(
            {
                "team_score": final_team,
                "opponent_score": final_opponent,
                "minutes_played": final_minutes,
            }
        )
        if not update_game(write_session, finished_id, fields):
            raise LookupError(f"game {finished_id} could not be updated")

    result = run_write(current_user.user_id, work, "finish game")
    if not result.ok:
        return no_update, MESSAGE_SAVE_FAILED.format(ref=result.ref)
    logger.info("Finished game %s", finished_id)
    return f"{GAMES_PATH}/{finished_id}/summary", ""


# --- Registration ---

def _register_list_and_form_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the games list, New game and game page callbacks.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        Every url.pathname writer uses allow_duplicate and ignores runs without a click.
    """
    app.callback(
        Output("games-content", "children"),
        Input("games-player", "value"),
        prevent_initial_call=True,
    )(select_games_player)

    app.callback(
        Output("url", "pathname", allow_duplicate=True),
        Input("games-grid", "cellClicked"),
        prevent_initial_call=True,
    )(open_game_from_grid)

    app.callback(
        Output("game-new-season", "data"),
        Output("game-new-season", "value"),
        Output("game-new-team", "value"),
        Output("game-new-location", "value"),
        Input("game-new-player", "value"),
        prevent_initial_call=True,
    )(game_new_player_changed)

    for prefix in ("game-new", "game-edit"):
        app.clientside_callback(
            ClientsideFunction("hoopster_games", "practice_game_fields"),
            Output(f"{prefix}-teammates-wrap", "style"),
            Output(f"{prefix}-target-wrap", "style"),
            Input(f"{prefix}-category", "value"),
            prevent_initial_call=True,
        )

    app.callback(
        Output("game-new-feedback", "children"),
        Output("url", "pathname", allow_duplicate=True),
        Output("game-new-dup-ack", "data"),
        Input("game-new-live", "n_clicks"),
        Input("game-new-save", "n_clicks"),
        State("game-new-player", "value"),
        State("game-new-category", "value"),
        State("game-new-format", "value"),
        State("game-new-date", "value"),
        State("game-new-team", "value"),
        State("game-new-opponent", "value"),
        State("game-new-teammates", "value"),
        State("game-new-location", "value"),
        State("game-new-home-away", "value"),
        State("game-new-target", "value"),
        State("game-new-season", "value"),
        State("game-new-notes", "value"),
        State("game-new-dup-ack", "data"),
        prevent_initial_call=True,
    )(create_game)

    app.callback(
        Output("game-edit-saved", "children"),
        Output("game-edit-feedback", "children"),
        Output("url", "pathname", allow_duplicate=True),
        Output("game-edit-dup-ack", "data"),
        Input("game-edit-save", "n_clicks"),
        State("game-id", "data"),
        State("game-edit-category", "value"),
        State("game-edit-format", "value"),
        State("game-edit-date", "value"),
        State("game-edit-team", "value"),
        State("game-edit-opponent", "value"),
        State("game-edit-teammates", "value"),
        State("game-edit-location", "value"),
        State("game-edit-home-away", "value"),
        State("game-edit-target", "value"),
        State("game-edit-season", "value"),
        State("game-edit-notes", "value"),
        State("game-edit-team-score", "value"),
        State("game-edit-opponent-score", "value"),
        State("game-edit-minutes", "value"),
        *[State(f"game-edit-{code}", "value") for code in GAME_COUNTER_CODES],
        State("game-edit-dup-ack", "data"),
        prevent_initial_call=True,
    )(save_game)

    app.callback(
        Output("game-delete-modal", "opened"),
        Output("game-delete-text", "children"),
        Output("game-delete-feedback", "children"),
        Input("game-delete-open", "n_clicks"),
        prevent_initial_call=True,
    )(open_delete_game)

    app.clientside_callback(
        ClientsideFunction("hoopster", "close_modal"),
        Output("game-delete-modal", "opened", allow_duplicate=True),
        Input("game-delete-cancel", "n_clicks"),
        prevent_initial_call=True,
    )

    app.callback(
        Output("url", "pathname", allow_duplicate=True),
        Output("game-delete-feedback", "children", allow_duplicate=True),
        Input("game-delete-confirm", "n_clicks"),
        State("game-id", "data"),
        prevent_initial_call=True,
    )(confirm_delete_game)


def _register_live_callbacks(app: Dash) -> None:
    """
    Description:
        Registers live mode's +1 channel (dispatcher, display, executor) and Finish game.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        Single-writer rules (plan §3.3): game_stat_dispatch is the only writer of
        game-stat-queue, game-stat-confirmed and game-stat-outbox; game-stat-ack and
        game-stat-base are written only by execute_game_stat_command through set_props, and
        no callback has an Output on them. The display runs on page insertion (outputs in
        the page) so the labels and badge start right.
    """
    app.clientside_callback(
        ClientsideFunction("hoopster_games", "game_stat_dispatch"),
        Output("game-stat-queue", "data"),
        Output("game-stat-confirmed", "data"),
        Output("game-stat-outbox", "data"),
        Input({"type": "game-stat-add", "stat": ALL}, "n_clicks"),
        Input("game-stat-undo", "n_clicks"),
        Input("game-stat-retry", "n_clicks"),
        Input("game-stat-ack", "data"),
        Input("game-stat-watchdog", "n_intervals"),
        State("game-stat-queue", "data"),
        State("game-stat-confirmed", "data"),
        State("game-stat-outbox", "data"),
        State("game-stat-base", "data"),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        ClientsideFunction("hoopster_games", "game_stat_render"),
        Output({"type": "game-stat-add", "stat": ALL}, "children"),
        Output("game-stat-status", "children"),
        Output("game-stat-status", "color"),
        Output("game-stat-retry", "style"),
        Output("game-stat-undo", "disabled"),
        Output("game-stat-watchdog", "disabled"),
        Input("game-stat-base", "data"),
        Input("game-stat-queue", "data"),
        Input("game-stat-confirmed", "data"),
        Input("game-stat-outbox", "data"),
        Input("game-stat-ack", "data"),
        State("game-stat-labels", "data"),
    )

    app.callback(
        Input("game-stat-outbox", "data"),
        State("game-id", "data"),
        State("device-clock", "data"),
        prevent_initial_call=True,
    )(execute_game_stat_command)

    app.callback(
        Output("game-finish-modal", "opened"),
        Output("game-finish-feedback", "children"),
        Output("game-finish-team-score", "value"),
        Output("game-finish-opponent-score", "value"),
        Output("game-finish-minutes", "value"),
        Input("game-finish-open", "n_clicks"),
        State("game-id", "data"),
        State("game-stat-queue", "data"),
        State("game-stat-outbox", "data"),
        State("court-queue", "data"),
        State("court-outbox", "data"),
        prevent_initial_call=True,
    )(open_finish_game)

    app.clientside_callback(
        ClientsideFunction("hoopster", "close_modal"),
        Output("game-finish-modal", "opened", allow_duplicate=True),
        Input("game-finish-cancel", "n_clicks"),
        prevent_initial_call=True,
    )

    app.callback(
        Output("url", "pathname", allow_duplicate=True),
        Output("game-finish-modal-feedback", "children"),
        Input("game-finish-confirm", "n_clicks"),
        State("game-id", "data"),
        State("game-finish-team-score", "value"),
        State("game-finish-opponent-score", "value"),
        State("game-finish-minutes", "value"),
        State("game-stat-queue", "data"),
        State("game-stat-outbox", "data"),
        State("court-queue", "data"),
        State("court-outbox", "data"),
        prevent_initial_call=True,
    )(confirm_finish_game)


def register_games_callbacks(app: Dash) -> None:
    """
    Description:
        Registers every games callback: list, New game, game page, delete, the live +1
        channel and Finish game. The live page's court entry callbacks are registered by
        controller_entry.register_entry_callbacks (plan §3.2 A4).

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        The components exist only on their own pages, so the app runs with
        suppress_callback_exceptions.
    """
    _register_list_and_form_callbacks(app)
    _register_live_callbacks(app)
