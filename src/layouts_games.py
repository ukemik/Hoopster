"""Games UI for Hoopster: the games list with per-game averages and the AG Grid, the New game
form, the game page (record, after-game totals, delete), the live page (shared entry panel plus
the +1 stat buttons and Finish game) and the post-game summary with the game's shot chart.

REQ-018 to REQ-024, REQ-045, REQ-048, REQ-061 to REQ-064 (task 2026-10-23_games). Built only
through the wrappers of src/layouts.py (COD-5-01); the court entry is the shared entry panel of
src/layouts_entry.py, given the entry parent {"kind": "game", "id"} and the game's player as a
fixed court-player (plan §3.2 A1). Values arrive already formatted where the plan says so
(controller_games.format_pct / format_avg / format_ratio); dates are formatted here.
"""

import logging
from datetime import date, datetime

import dash_ag_grid as dag
import dash_mantine_components as dmc
import plotly.graph_objects as go
from dash import dcc, html
from dash.development.base_component import Component

from config.app_config import (
    GAME_COUNTER_CODES,
    GAME_COUNTER_MAX,
    GAME_MINUTES_MAX,
    GAME_SCORE_MAX,
    GAME_STAT_WATCHDOG_MS,
    GAME_TARGET_MAX,
    HEAT_MAP_BAND_COLORS,
    HEAT_MAP_NO_ATTEMPTS_COLOR,
    HEAT_MAP_OUTLINE_COLOR,
)
from src.layouts import (
    CONTROL_SIZE,
    audit_fields_block,
    confirm_modal,
    entry_button,
    form_message,
    nav_link,
    page_skeleton,
    player_options,
    primary_button,
    save_status_badge,
    secondary_button,
    stat_tile,
)
from src.layouts_entry import (
    COURT_CLASS,
    COURT_VIEW_HALF,
    build_court_figure,
    entry_panel,
    fixed_entry_player,
)

logger = logging.getLogger(__name__)

# The h_ref.game_categories code whose games carry teammate names and a target score (D8).
# Duplicated in assets/hoopster_games.js (practice_game_fields).
PRACTICE_GAME_CODE = "practice_game"
# The "not set" segment of the home/away control (stored as NULL).
HOME_AWAY_NOT_SET = "none"
HOME_AWAY_OPTIONS: list[dict[str, str]] = [
    {"value": HOME_AWAY_NOT_SET, "label": "Not set"},
    {"value": "home", "label": "Home"},
    {"value": "away", "label": "Away"},
    {"value": "neutral", "label": "Neutral"},
]
# The season picker's "Automatic" option: the database assigns the covering season (D7).
SEASON_AUTO = "auto"
SEASON_AUTO_LABEL = "Automatic"

HIDDEN_STYLE: dict[str, str] = {"display": "none"}
SHOWN_STYLE: dict[str, str] = {}
SEGMENTED_CLASS = "hoopster-segmented"
DATE_PICKER_FORMAT = "ddd D MMM YYYY"

GAMES_PATH = "/games"


# --- Dates and small texts ---

def format_game_date(value: object) -> str:
    """
    Description:
        Formats a game's calendar date as stored, e.g. "Sat 18 Oct 2026" (REQ-055).

    Parameters:
        value: the date (a date; anything else gives its text).

    Return Values:
        str: the formatted date, the day without a leading zero.

    Notes:
        Pure. No time zone applies to a calendar date.
    """
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return f"{value.strftime('%a')} {value.day} {value.strftime('%b %Y')}"
    return str(value)


def score_text(team_score: object, opponent_score: object) -> str:
    """
    Description:
        The score as "54–40" (team first, en dash).

    Parameters:
        team_score: the player's team's score.
        opponent_score: the opponent's score.

    Return Values:
        str: the text.

    Notes:
        Pure.
    """
    return f"{team_score}–{opponent_score}"


RESULT_LETTERS: dict[str, str] = {"win": "W", "loss": "L", "tie": "T"}
RESULT_WORDS: dict[str, str] = {"win": "Win", "loss": "Loss", "tie": "Tie"}
IN_PROGRESS_TEXT = "In progress"
NO_FINAL_SCORE_TEXT = "No final score yet"


def stat_definition(definitions: dict[str, dict], code: str) -> dict:
    """
    Description:
        A stat's definition row, or a stand-in when it is missing (a deleted definition).

    Parameters:
        definitions: data_games.list_stat_definitions.
        code: the stat_code.

    Return Values:
        dict: the row, or {"stat_code": code, "label": code, "abbreviation": code,
        "formula_text": None, "explanation": ""}.

    Notes:
        Pure.
    """
    found = definitions.get(code)
    if found is not None:
        return found
    return {
        "stat_code": code,
        "label": code,
        "abbreviation": code,
        "formula_text": None,
        "explanation": "",
    }


def stat_tiles_grid(
    tiles: list[tuple[str, str]], definitions: dict[str, dict], scope: str, element_id: str
) -> Component:
    """
    Description:
        A grid of stat tiles (REQ-064), three per row on a phone.

    Parameters:
        tiles: (stat_code, formatted value) pairs in display order.
        definitions: data_games.list_stat_definitions.
        scope: the tiles' scope (see layouts.stat_tile).
        element_id: the grid's id.

    Return Values:
        Component: a dmc.SimpleGrid of stat_tile components.

    Notes:
        None.
    """
    return dmc.SimpleGrid(
        [stat_tile(stat_definition(definitions, code), text, scope) for code, text in tiles],
        cols=3,
        spacing="xs",
        verticalSpacing="xs",
        id=element_id,
    )


def _number_input(
    element_id: str,
    label: str,
    value: object,
    maximum: int,
    decimal_scale: int,
) -> dmc.NumberInput:
    """
    Description:
        A touch-sized number input that keeps what is typed, so the form's own check speaks.

    Parameters:
        element_id: the stable element id.
        label: the field label.
        value: the initial value (an int, a Decimal, or "" for empty).
        maximum: the largest value offered.
        decimal_scale: 0 for whole numbers, 1 for minutes.

    Return Values:
        dmc.NumberInput: the input (minimum 0, no negatives, numeric or decimal keyboard).

    Notes:
        clampBehavior "none", as the entry panel's count inputs. decimalScale is passed only
        for a decimal field.
    """
    options: dict[str, object] = {}
    if decimal_scale > 0:
        options["decimalScale"] = decimal_scale
    return dmc.NumberInput(
        id=element_id,
        label=label,
        value=value,
        min=0,
        max=maximum,
        allowDecimal=decimal_scale > 0,
        allowNegative=False,
        clampBehavior="none",
        size=CONTROL_SIZE,
        hideControls=True,
        inputProps={"inputMode": "decimal" if decimal_scale > 0 else "numeric"},
        **options,
    )


def _number_value(value: object) -> object:
    """
    Description:
        A stored number as a NumberInput value: the number, or "" when NULL.

    Parameters:
        value: an int, a Decimal or None.

    Return Values:
        object: an int, a float (one decimal) or "".

    Notes:
        Pure. A Decimal becomes a float so the value is JSON-serialisable.
    """
    if value is None:
        return ""
    if isinstance(value, int):
        return value
    return float(str(value))


# --- Shared game record fields (REQ-018, REQ-062; plan D6 to D8) ---

def season_options(seasons: list[dict]) -> list[dict[str, str]]:
    """
    Description:
        The season picker's options: "Automatic", then the player's live seasons.

    Parameters:
        seasons: rows of data_seasons.list_seasons.

    Return Values:
        list[dict[str, str]]: {"value": "auto", "label": "Automatic"}, then {"value":
        str(season_id), "label": season_name} per season.

    Notes:
        Pure.
    """
    return [{"value": SEASON_AUTO, "label": SEASON_AUTO_LABEL}] + [
        {"value": str(int(row["season_id"])), "label": str(row["season_name"])}
        for row in seasons
    ]


def _code_options(rows: list[dict]) -> list[dict[str, str]]:
    """
    Description:
        Turns {"code", "label"} rows into dmc options.

    Parameters:
        rows: data_games.list_game_categories or list_game_formats.

    Return Values:
        list[dict[str, str]]: {"value", "label"} per row, in order.

    Notes:
        Pure.
    """
    return [{"value": str(row["code"]), "label": str(row["label"])} for row in rows]


def practice_game_style(category: object) -> dict[str, str]:
    """
    Description:
        The style of the practice-game-only wrappers (teammate names, target score): shown
        only for the Practice game category (D8).

    Parameters:
        category: the category picker's value.

    Return Values:
        dict[str, str]: {} or {"display": "none"}.

    Notes:
        Pure. Mirrors assets/hoopster_games.js practice_game_fields.
    """
    return SHOWN_STYLE if category == PRACTICE_GAME_CODE else HIDDEN_STYLE


def game_record_fields(
    prefix: str,
    categories: list[dict],
    formats: list[dict],
    seasons: list[dict],
    suggestions: dict,
    values: dict,
) -> list[Component]:
    """
    Description:
        The game record fields shared by New game ("game-new") and the game page
        ("game-edit"): category, format, date, team, opponent, location, opponent and
        teammate names (practice games), home/away, target score (practice games), season
        and notes.

    Parameters:
        prefix: "game-new" or "game-edit"; ids are "{prefix}-category", "-format", "-date",
            "-team", "-opponent", "-location", "-teammates" (inside "-teammates-wrap"),
            "-home-away", "-target" (inside "-target-wrap"), "-season", "-notes".
        categories: data_games.list_game_categories.
        formats: data_games.list_game_formats.
        seasons: data_seasons.list_seasons of the game's player.
        suggestions: data_games.get_entry_suggestions ("teams", "opponents", "locations").
        values: the initial values: {"category", "format", "date" ("YYYY-MM-DD" or None),
            "team", "opponent", "location", "teammates", "home_away", "target", "season",
            "notes"}; a missing key is empty.

    Return Values:
        list[Component]: the fields in screen order.

    Notes:
        Team, opponent and location are Autocompletes over every live game's values (D6).
        The two practice-game wrappers start hidden unless the category is Practice game;
        practice_game_fields (clientside) toggles them.
    """
    practice_style = practice_game_style(values.get("category"))
    target = values.get("target")
    return [
        dmc.Select(
            id=f"{prefix}-category",
            label="Category",
            data=_code_options(categories),
            value=values.get("category"),
            allowDeselect=False,
            size=CONTROL_SIZE,
        ),
        dmc.Stack(
            [
                dmc.Text("Format", size="sm", fw=500),
                dmc.SegmentedControl(
                    id=f"{prefix}-format",
                    data=_code_options(formats),
                    value=values.get("format"),
                    fullWidth=True,
                    size=CONTROL_SIZE,
                    className=SEGMENTED_CLASS,
                ),
            ],
            gap=4,
        ),
        dmc.DatePickerInput(
            id=f"{prefix}-date",
            label="Date",
            value=values.get("date"),
            valueFormat=DATE_PICKER_FORMAT,
            size=CONTROL_SIZE,
        ),
        dmc.Autocomplete(
            id=f"{prefix}-team",
            label="Team",
            data=list(suggestions.get("teams") or []),
            value=values.get("team") or "",
            size=CONTROL_SIZE,
        ),
        dmc.Autocomplete(
            id=f"{prefix}-opponent",
            label="Opponent",
            data=list(suggestions.get("opponents") or []),
            value=values.get("opponent") or "",
            size=CONTROL_SIZE,
        ),
        dmc.Autocomplete(
            id=f"{prefix}-location",
            label="Location",
            data=list(suggestions.get("locations") or []),
            value=values.get("location") or "",
            size=CONTROL_SIZE,
        ),
        html.Div(
            dmc.Textarea(
                id=f"{prefix}-teammates",
                label="Opponent and teammate names",
                value=values.get("teammates") or "",
                autosize=True,
                minRows=2,
                size=CONTROL_SIZE,
            ),
            id=f"{prefix}-teammates-wrap",
            style=practice_style,
        ),
        dmc.Stack(
            [
                dmc.Text("Home or away", size="sm", fw=500),
                dmc.SegmentedControl(
                    id=f"{prefix}-home-away",
                    data=HOME_AWAY_OPTIONS,
                    value=values.get("home_away") or HOME_AWAY_NOT_SET,
                    fullWidth=True,
                    size=CONTROL_SIZE,
                    className=SEGMENTED_CLASS,
                ),
            ],
            gap=4,
        ),
        html.Div(
            _number_input(
                f"{prefix}-target",
                "Target score",
                target if target is not None else "",
                GAME_TARGET_MAX,
                0,
            ),
            id=f"{prefix}-target-wrap",
            style=practice_style,
        ),
        dmc.Select(
            id=f"{prefix}-season",
            label="Season",
            data=season_options(seasons),
            value=values.get("season") or SEASON_AUTO,
            allowDeselect=False,
            size=CONTROL_SIZE,
        ),
        dmc.Textarea(
            id=f"{prefix}-notes",
            label="Notes",
            value=values.get("notes") or "",
            autosize=True,
            minRows=2,
            size=CONTROL_SIZE,
        ),
    ]


# --- Games list (REQ-024, REQ-048; plan D9, D12) ---

GAMES_GRID_HEIGHT_PX = 460


def games_grid_rows(rows: list[dict]) -> list[dict[str, object]]:
    """
    Description:
        The games grid's rowData.

    Parameters:
        rows: data_games.list_games, newest first.

    Return Values:
        list[dict[str, object]]: {"row_id" (str(game_id)), "date_iso" ("YYYY-MM-DD"),
        "opponent", "result" ("W 54–40", "L …", "T …" or "In progress"), "points"} per game.

    Notes:
        Pure. row_id is the grid's getRowId, so cellClicked carries the game id. The date
        column shows date_iso as "Sat 18 Oct" through d3 and sorts by the ISO text.
    """
    grid_rows: list[dict[str, object]] = []
    for row in rows:
        result = row.get("result")
        if row.get("entry_status") != "completed" or result not in RESULT_LETTERS:
            result_text = IN_PROGRESS_TEXT
        else:
            result_text = (
                f"{RESULT_LETTERS[str(result)]} "
                f"{score_text(row.get('team_score'), row.get('opponent_score'))}"
            )
        game_date = row.get("game_date")
        grid_rows.append(
            {
                "row_id": str(int(row["game_id"])),
                "date_iso": game_date.isoformat() if isinstance(game_date, date) else "",
                "opponent": str(row.get("opponent_name") or ""),
                "result": result_text,
                "points": int(row.get("points") or 0),
            }
        )
    return grid_rows


def _games_grid(rows: list[dict]) -> Component:
    """
    Description:
        The read-only games grid (D9): Date, Opponent, Result, PTS; a click on a row opens the
        game page.

    Parameters:
        rows: data_games.list_games.

    Return Values:
        Component: dag.AgGrid id "games-grid".

    Notes:
        Sortable columns, Opponent filterable, responsiveSizeToFit; editing happens on the
        game page. dash-ag-grid's built-in theme, like the drill library grid.
    """
    column_defs = [
        {
            "field": "date_iso",
            "headerName": "Date",
            "sortable": True,
            "valueFormatter": {
                "function": "params.value ? d3.timeFormat('%a %-d %b')"
                "(d3.timeParse('%Y-%m-%d')(params.value)) : ''"
            },
        },
        {"field": "opponent", "headerName": "Opponent", "sortable": True, "filter": True},
        {"field": "result", "headerName": "Result", "sortable": True},
        {"field": "points", "headerName": "PTS", "sortable": True, "maxWidth": 90},
    ]
    return html.Div(
        dag.AgGrid(
            id="games-grid",
            rowData=games_grid_rows(rows),
            columnDefs=column_defs,
            getRowId="params.data.row_id",
            defaultColDef={"resizable": True},
            columnSize="responsiveSizeToFit",
            style={"height": GAMES_GRID_HEIGHT_PX, "width": "100%"},
        ),
        className="hoopster-games-grid",
    )


def games_content(
    average_tiles: list[tuple[str, str]],
    game_count: int,
    rows: list[dict],
    definitions: dict[str, dict],
) -> list[Component]:
    """
    Description:
        The contents of games-content: the per-game averages (heading "Per game ({n}
        games)", or "No games yet.") and the games grid.

    Parameters:
        average_tiles: (stat_code, formatted average) pairs from the controller.
        game_count: the number of live games (fn_game_averages.games_played).
        rows: data_games.list_games.
        definitions: data_games.list_stat_definitions.

    Return Values:
        list[Component]: the children of games-content.

    Notes:
        Rebuilt by select_games_player when another player is picked.
    """
    if game_count <= 0:
        averages: list[Component] = [dmc.Text("No games yet.", id="games-empty", c="dimmed")]
    else:
        noun = "game" if game_count == 1 else "games"
        averages = [
            dmc.Title(f"Per game ({game_count} {noun})", order=4, id="games-avg-heading"),
            stat_tiles_grid(average_tiles, definitions, "games-avg", "games-avg-tiles"),
        ]
    return [*averages, _games_grid(rows)]


def games_layout(
    players: list[dict], selected_player_id: int, content: list[Component]
) -> Component:
    """
    Description:
        The games list screen (REQ-024, REQ-048): player picker, New game, then the averages
        and the grid.

    Parameters:
        players: data_players.list_live_players (the picker's options).
        selected_player_id: current_player_id(), the picker's value. Never a user id.
        content: games_content for that player.

    Return Values:
        Component: the page.

    Notes:
        None.
    """
    return page_skeleton(
        "Games",
        [
            dmc.Select(
                id="games-player",
                label="Player",
                data=player_options(players),
                value=str(selected_player_id),
                allowDeselect=False,
                size=CONTROL_SIZE,
            ),
            html.Div(
                nav_link("games-new", "New game", "/games/new"),
                className="hoopster-link-button hoopster-link-primary",
            ),
            html.Div(content, id="games-content"),
        ],
    )


# --- New game (REQ-018, REQ-062, REQ-063) ---

def game_new_layout(
    players: list[dict],
    selected_player_id: int,
    categories: list[dict],
    formats: list[dict],
    seasons: list[dict],
    suggestions: dict,
    today: str | None,
) -> Component:
    """
    Description:
        The New game form: player, the record fields, Start live game and Save game.

    Parameters:
        players: data_players.list_live_players.
        selected_player_id: current_player_id(), the player picker's default only.
        categories: data_games.list_game_categories.
        formats: data_games.list_game_formats.
        seasons: data_seasons.list_seasons of the selected player.
        suggestions: data_games.get_entry_suggestions for the selected player.
        today: the device's date "YYYY-MM-DD" (device-clock), or None.

    Return Values:
        Component: the page.

    Notes:
        Team and location are prefilled with the player's last values (REQ-062); the
        category starts empty ("Choose a category."). The format starts on the first live
        format, because a Mantine SegmentedControl without a value shows its first segment
        as chosen while sending no value. game-new-dup-ack holds the signature of the
        duplicate the user was warned about (REQ-063).
    """
    values = {
        "format": str(formats[0]["code"]) if formats else None,
        "date": today,
        "team": suggestions.get("last_team"),
        "location": suggestions.get("last_location"),
        "season": SEASON_AUTO,
        "home_away": HOME_AWAY_NOT_SET,
    }
    return page_skeleton(
        "New game",
        [
            dmc.Select(
                id="game-new-player",
                label="Player",
                data=player_options(players),
                value=str(selected_player_id),
                allowDeselect=False,
                size=CONTROL_SIZE,
            ),
            *game_record_fields("game-new", categories, formats, seasons, suggestions, values),
            dcc.Store(id="game-new-dup-ack", storage_type="memory"),
            primary_button("game-new-live", "Start live game"),
            secondary_button("game-new-save", "Save game"),
            form_message("game-new-feedback"),
        ],
    )


# --- Game page (REQ-004 to REQ-006, REQ-020, REQ-022, REQ-023) ---

DELETE_GAME_TITLE = "Delete game?"


def _record_values(game: dict) -> dict[str, object]:
    """
    Description:
        The record fields' initial values from a stored game.

    Parameters:
        game: data_games.get_game row.

    Return Values:
        dict[str, object]: see game_record_fields' values.

    Notes:
        Pure. A manual season is shown by its id; an automatic one as "auto".
    """
    game_date = game.get("game_date")
    season_id = game.get("season_id")
    season = (
        str(int(season_id)) if game.get("season_is_manual") and season_id is not None
        else SEASON_AUTO
    )
    return {
        "category": game.get("game_category_code"),
        "format": game.get("game_format_code"),
        "date": game_date.isoformat() if isinstance(game_date, date) else None,
        "team": game.get("team_name"),
        "opponent": game.get("opponent_name"),
        "location": game.get("location"),
        "teammates": game.get("teammate_names"),
        "home_away": game.get("home_away"),
        "target": game.get("target_score"),
        "season": season,
        "notes": game.get("notes"),
    }


def game_header_text(game: dict) -> str:
    """
    Description:
        "{player} · vs {opponent} · {date}" (or "{player} · {date}" without an opponent).

    Parameters:
        game: data_games.get_game row.

    Return Values:
        str: the header.

    Notes:
        Pure.
    """
    parts = [str(game.get("player_name"))]
    if game.get("opponent_name"):
        parts.append(f"vs {game['opponent_name']}")
    parts.append(format_game_date(game.get("game_date")))
    return " · ".join(parts)


def _counter_inputs(game: dict, definitions: dict[str, dict]) -> Component:
    """
    Description:
        The seven after-game totals (REQ-022) in a two-column grid.

    Parameters:
        game: data_games.get_game row.
        definitions: data_games.list_stat_definitions (labels).

    Return Values:
        Component: a dmc.SimpleGrid of NumberInputs "game-edit-<code>".

    Notes:
        Whole numbers 0 to GAME_COUNTER_MAX.
    """
    return dmc.SimpleGrid(
        [
            _number_input(
                f"game-edit-{code}",
                str(stat_definition(definitions, code).get("label", code)),
                game.get(code, 0),
                GAME_COUNTER_MAX,
                0,
            )
            for code in GAME_COUNTER_CODES
        ],
        cols=2,
        spacing="xs",
    )


def game_page_layout(
    game: dict,
    categories: list[dict],
    formats: list[dict],
    seasons: list[dict],
    suggestions: dict,
    shooting_tiles: list[tuple[str, str]],
    definitions: dict[str, dict],
    audit_texts: tuple[str, str],
) -> Component:
    """
    Description:
        The game page: the record, the final score and minutes, the after-game totals, the
        read-only shooting tiles, Save, links to live mode and the summary, who added and
        last edited the game, and Delete with its confirmation (REQ-005).

    Parameters:
        game: data_games.get_game row, already confirmed live.
        categories: data_games.list_game_categories.
        formats: data_games.list_game_formats.
        seasons: data_seasons.list_seasons of the game's player.
        suggestions: data_games.get_entry_suggestions.
        shooting_tiles: (stat_code, formatted value) pairs (FG, 3P, FT, PTS, REB, eFG%,
            TS%) from the controller.
        definitions: data_games.list_stat_definitions.
        audit_texts: (created_at, updated_at) formatted on the device's clock.

    Return Values:
        Component: the page.

    Notes:
        The player is shown as text (game-edit-player): it never changes (D14). The game-id
        store is written only after the builder's get_game check.
    """
    game_id = int(game["game_id"])
    created_text, updated_text = audit_texts
    return page_skeleton(
        "Game",
        [
            dmc.Text(game_header_text(game), id="game-edit-header", fw=500),
            dcc.Store(id="game-id", storage_type="memory", data=game_id),
            dmc.Text(f"Player: {game.get('player_name')}", id="game-edit-player"),
            *game_record_fields(
                "game-edit", categories, formats, seasons, suggestions, _record_values(game)
            ),
            dmc.Title("Final score", order=4),
            dmc.SimpleGrid(
                [
                    _number_input(
                        "game-edit-team-score",
                        "Team score",
                        _number_value(game.get("team_score")),
                        GAME_SCORE_MAX,
                        0,
                    ),
                    _number_input(
                        "game-edit-opponent-score",
                        "Opponent score",
                        _number_value(game.get("opponent_score")),
                        GAME_SCORE_MAX,
                        0,
                    ),
                ],
                cols=2,
                spacing="xs",
            ),
            _number_input(
                "game-edit-minutes",
                "Minutes played",
                _number_value(game.get("minutes_played")),
                GAME_MINUTES_MAX,
                1,
            ),
            dmc.Title("Stats", order=4),
            _counter_inputs(game, definitions),
            dmc.Title("Shooting", order=4),
            dmc.Text(
                "From the shots and free throws entered in live mode.", size="sm", c="dimmed"
            ),
            stat_tiles_grid(shooting_tiles, definitions, "game-page", "game-edit-tiles"),
            dcc.Store(id="game-edit-dup-ack", storage_type="memory"),
            primary_button("game-edit-save", "Save"),
            dmc.Text("", id="game-edit-saved", c="green"),
            form_message("game-edit-feedback"),
            html.Div(
                nav_link("game-edit-live", "Live mode and shots", f"{GAMES_PATH}/{game_id}/live"),
                className="hoopster-link-button",
            ),
            html.Div(
                nav_link("game-edit-summary", "Summary", f"{GAMES_PATH}/{game_id}/summary"),
                className="hoopster-link-button",
            ),
            html.Div(
                nav_link("games-back", "All games", GAMES_PATH),
                className="hoopster-link-button",
            ),
            audit_fields_block(
                "game-audit",
                game.get("created_by_name"),
                created_text,
                game.get("updated_by_name"),
                updated_text,
            ),
            secondary_button("game-delete-open", "Delete game", color="red"),
            confirm_modal("game-delete", DELETE_GAME_TITLE, "Delete"),
        ],
    )


# --- Live mode (REQ-010, REQ-021; plan §3.3, D3) ---

def stat_button_label(abbreviation: str, value: int) -> str:
    """
    Description:
        A +1 button's label, "+1 {ABBR} ({n})".

    Parameters:
        abbreviation: the stat's abbreviation (e.g. "OREB").
        value: the counter shown.

    Return Values:
        str: the label.

    Notes:
        Pure. Mirrors assets/hoopster_games.js game_stat_render.
    """
    return f"+1 {abbreviation} ({value})"


def _stat_buttons(counters: dict[str, int], definitions: dict[str, dict]) -> Component:
    """
    Description:
        The seven large +1 buttons, two per row, in GAME_COUNTER_CODES order.

    Parameters:
        counters: the game's counters at build ({code: value}).
        definitions: data_games.list_stat_definitions (abbreviations).

    Return Values:
        Component: game-stat-grid, a dmc.SimpleGrid of entry_buttons with ids {"type":
        "game-stat-add", "stat": code}.

    Notes:
        entry_button starts disabled for the court; these need no spot, so they are enabled.
        game_stat_render relies on this order (it matches the game-stat-labels store).
    """
    buttons = []
    for code in GAME_COUNTER_CODES:
        abbreviation = str(stat_definition(definitions, code).get("abbreviation", code))
        button = entry_button(
            {"type": "game-stat-add", "stat": code},
            stat_button_label(abbreviation, int(counters.get(code, 0))),
            "orange",
        )
        button.disabled = False
        buttons.append(button)
    return dmc.SimpleGrid(
        buttons, cols=2, spacing="xs", id="game-stat-grid", className="hoopster-stat-grid"
    )


def _finish_modal() -> Component:
    """
    Description:
        The Finish game dialog: the two scores and optional minutes, Save and finish, Cancel.

    Parameters:
        None.

    Return Values:
        Component: game-finish-modal (closed) with game-finish-team-score,
        game-finish-opponent-score, game-finish-minutes, game-finish-confirm,
        game-finish-cancel and game-finish-modal-feedback.

    Notes:
        open_finish_game prefills the stored values.
    """
    return dmc.Modal(
        dmc.Stack(
            [
                _number_input("game-finish-team-score", "Team score", "", GAME_SCORE_MAX, 0),
                _number_input(
                    "game-finish-opponent-score", "Opponent score", "", GAME_SCORE_MAX, 0
                ),
                _number_input(
                    "game-finish-minutes", "Minutes played (optional)", "", GAME_MINUTES_MAX, 1
                ),
                primary_button("game-finish-confirm", "Save and finish"),
                secondary_button("game-finish-cancel", "Cancel"),
                form_message("game-finish-modal-feedback"),
            ]
        ),
        id="game-finish-modal",
        title="Finish game",
        centered=True,
        opened=False,
    )


def _stat_stores(counters: dict[str, int], definitions: dict[str, dict]) -> list[Component]:
    """
    Description:
        The +1 channel's stores and watchdog (plan §3.3).

    Parameters:
        counters: the game's counters at build.
        definitions: data_games.list_stat_definitions.

    Return Values:
        list[Component]: game-stat-base (the server-confirmed counters), game-stat-queue [],
        game-stat-confirmed [] (the undo stack), game-stat-outbox (null), game-stat-ack
        (null; written only by the server through set_props), game-stat-labels ([{"stat",
        "abbr"}] in button order) and the disabled game-stat-watchdog interval.

    Notes:
        All in memory, so the undo stack covers exactly what was tapped since the screen
        opened (REQ-010).
    """
    labels = [
        {"stat": code, "abbr": str(stat_definition(definitions, code).get("abbreviation", code))}
        for code in GAME_COUNTER_CODES
    ]
    return [
        dcc.Store(id="game-stat-base", storage_type="memory", data=dict(counters)),
        dcc.Store(id="game-stat-queue", storage_type="memory", data=[]),
        dcc.Store(id="game-stat-confirmed", storage_type="memory", data=[]),
        dcc.Store(id="game-stat-outbox", storage_type="memory"),
        dcc.Store(id="game-stat-ack", storage_type="memory"),
        dcc.Store(id="game-stat-labels", storage_type="memory", data=labels),
        dcc.Interval(id="game-stat-watchdog", interval=GAME_STAT_WATCHDOG_MS, disabled=True),
    ]


def game_live_layout(
    game: dict,
    counters: dict[str, int],
    totals: dict[str, dict[str, int]],
    spots: list[dict],
    shot_types: list[dict],
    definitions: dict[str, dict],
) -> Component:
    """
    Description:
        Live mode (REQ-021): the header, the shared entry panel for the game (court, free
        throws, its own Undo, save badge, Entries), the +1 stat buttons with "Undo stat",
        their save badge and Retry, Finish game and a link to the game page.

    Parameters:
        game: data_games.get_game row, already confirmed live.
        counters: the game's seven counters ({code: value}).
        totals: data_entry.get_entry_totals of EntryParent("game", game_id).
        spots: data_entry.list_active_court_spots.
        shot_types: data_entry.list_shot_types.
        definitions: data_games.list_stat_definitions.

    Return Values:
        Component: the page.

    Notes:
        The entry parent is passed as a plain dict {"kind": "game", "id"} and the entry
        player is the game's player through fixed_entry_player (plan §3.2 A1); the entry
        callbacks are registered once by controller_entry (A4). Two undo controls (D3).
    """
    game_id = int(game["game_id"])
    player_id = int(game["player_id"])
    parent = {"kind": "game", "id": game_id}
    retry_button = secondary_button("game-stat-retry", "Retry")
    # Hidden until a +1 save fails; game_stat_render toggles it.
    retry_button.style = dict(HIDDEN_STYLE)
    return page_skeleton(
        "Live game",
        [
            dmc.Text(game_header_text(game), id="game-live-header", fw=500),
            dcc.Store(id="game-id", storage_type="memory", data=game_id),
            fixed_entry_player(player_id),
            *entry_panel(parent, totals, player_id, spots, shot_types),
            dmc.Title("Stats", order=3),
            _stat_buttons(counters, definitions),
            secondary_button("game-stat-undo", "Undo stat", disabled=True),
            save_status_badge("game-stat-status"),
            retry_button,
            *_stat_stores(counters, definitions),
            primary_button("game-finish-open", "Finish game"),
            form_message("game-finish-feedback"),
            _finish_modal(),
            html.Div(
                nav_link("game-live-details", "Game details", f"{GAMES_PATH}/{game_id}"),
                className="hoopster-link-button",
            ),
        ],
    )


# --- Post-game summary (REQ-061, REQ-064) ---

HIGHLIGHT_WORDS: dict[str, tuple[str, str]] = {
    "points": ("point", "points"),
    "total_rebounds": ("rebound", "rebounds"),
    "assists": ("assist", "assists"),
    "steals": ("steal", "steals"),
    "blocks": ("block", "blocks"),
}
NO_NEW_HIGHS_TEXT = "No new career highs this game."
CHART_CAPTION = "Manual entries are not on the chart."


def result_line(game: dict) -> str:
    """
    Description:
        The summary's result line: "Win 54–40 vs Eagles", "Loss …", "Tie …" (without " vs
        …" when there is no opponent), or "No final score yet".

    Parameters:
        game: data_games.get_game row.

    Return Values:
        str: the line.

    Notes:
        Pure.
    """
    result = game.get("result")
    if result not in RESULT_WORDS:
        return NO_FINAL_SCORE_TEXT
    score = score_text(game.get("team_score"), game.get("opponent_score"))
    line = f"{RESULT_WORDS[str(result)]} {score}"
    if game.get("opponent_name"):
        line += f" vs {game['opponent_name']}"
    return line


def highlight_lines(rows: list[dict]) -> list[str]:
    """
    Description:
        One line per new career high of this game (REQ-061): "New career high: 18 points
        (previous best 15)", or "First game: 18 points" when there is no earlier game; "No
        new career highs this game." when there are none.

    Parameters:
        rows: data_games.get_game_highlights.

    Return Values:
        list[str]: the lines, in the rows' order.

    Notes:
        Pure. A value of 1 uses the singular ("1 assist").
    """
    lines: list[str] = []
    for row in rows:
        if not row.get("is_new_high"):
            continue
        code = str(row.get("stat_code"))
        value = int(row.get("value") or 0)
        singular, plural = HIGHLIGHT_WORDS.get(code, (code, code))
        amount = f"{value} {singular if value == 1 else plural}"
        previous = row.get("previous_high")
        if previous is None:
            lines.append(f"First game: {amount}")
        else:
            lines.append(f"New career high: {amount} (previous best {previous})")
    return lines or [NO_NEW_HIGHS_TEXT]


HEAT_MARKER_SIZE_PX = 36
HEAT_TEXT_SIZE_PX = 11
HEAT_DARK_TEXT = "#212529"
HEAT_LIGHT_TEXT = "#ffffff"
# Bands whose fill is dark enough for white text.
HEAT_LIGHT_TEXT_RANKS: frozenset[int] = frozenset({1, 4, 5})
# A spot with attempts but no matching band (no band defined) is drawn neutral.
HEAT_NEUTRAL_RANK = 3


def build_game_heat_figure(rows: list[dict]) -> go.Figure:
    """
    Description:
        The game's shot chart (REQ-061): the shared half-court figure with one marker per
        spot, coloured by its band (1 Cold blue ... 5 Hot red) and labelled "m/a" where the
        game has attempts; spots without attempts are white with a grey outline and no
        text.

    Parameters:
        rows: data_games.get_game_heat_map (every live active spot plus any spot with
            attempts in this game), by spot_number.

    Return Values:
        go.Figure: the figure; static (no hover, no click).

    Notes:
        Built on layouts_entry.build_court_figure so the court is drawn once in the code
        base. Colours come from config HEAT_MAP_BAND_COLORS.
    """
    figure = build_court_figure(rows, COURT_VIEW_HALF)
    colors: list[str] = []
    texts: list[str] = []
    text_colors: list[str] = []
    for row in rows:
        attempts = int(row.get("attempts") or 0)
        if attempts <= 0:
            colors.append(HEAT_MAP_NO_ATTEMPTS_COLOR)
            texts.append("")
            text_colors.append(HEAT_DARK_TEXT)
            continue
        rank = row.get("band_rank")
        band = (
            int(rank)
            if isinstance(rank, int) and rank in HEAT_MAP_BAND_COLORS
            else HEAT_NEUTRAL_RANK
        )
        colors.append(HEAT_MAP_BAND_COLORS[band])
        texts.append(f"{int(row.get('makes') or 0)}/{attempts}")
        text_colors.append(HEAT_LIGHT_TEXT if band in HEAT_LIGHT_TEXT_RANKS else HEAT_DARK_TEXT)
    figure.update_traces(
        text=texts,
        marker={
            "size": HEAT_MARKER_SIZE_PX,
            "color": colors,
            "line": {"color": HEAT_MAP_OUTLINE_COLOR, "width": 2},
        },
        textfont={"color": text_colors, "size": HEAT_TEXT_SIZE_PX},
        hoverinfo="skip",
    )
    figure.update_layout(hovermode=False, clickmode="none", uirevision="game-chart")
    return figure


def game_summary_layout(
    game: dict,
    lines: list[str],
    box_tiles: list[tuple[str, str]],
    definitions: dict[str, dict],
    heat_rows: list[dict],
) -> Component:
    """
    Description:
        The post-game summary (REQ-061): the result line, date · category · format, the
        career-high lines, the box score as stat tiles (REQ-064), the game's shot chart and
        links to the game page and the games list.

    Parameters:
        game: data_games.get_game row, already confirmed live.
        lines: highlight_lines of the game.
        box_tiles: (stat_code, formatted value) pairs (PTS, FG, 3P, FT, eFG%, TS%, OREB, DREB,
            REB, AST, STL, BLK, TOV, PF, AST/TO, MIN) from the controller.
        definitions: data_games.list_stat_definitions.
        heat_rows: data_games.get_game_heat_map.

    Return Values:
        Component: the page.

    Notes:
        The chart is a static dcc.Graph without a mode bar.
    """
    game_id = int(game["game_id"])
    detail = " · ".join(
        [
            format_game_date(game.get("game_date")),
            str(game.get("category_label")),
            str(game.get("format_label")),
        ]
    )
    return page_skeleton(
        "Game summary",
        [
            dmc.Title(result_line(game), order=3, id="game-summary-result"),
            dmc.Text(detail, id="game-summary-detail", c="dimmed"),
            html.Div(
                [dmc.Text(line) for line in lines], id="game-summary-highlights"
            ),
            dmc.Title("Box score", order=4),
            stat_tiles_grid(box_tiles, definitions, "summary", "game-summary-box"),
            dmc.Title("Shot chart", order=4),
            dcc.Graph(
                id="game-summary-chart",
                figure=build_game_heat_figure(heat_rows),
                config={"displayModeBar": False, "staticPlot": True, "responsive": True},
                className=COURT_CLASS,
            ),
            dmc.Text(CHART_CAPTION, id="game-summary-chart-caption", size="sm", c="dimmed"),
            html.Div(
                nav_link("game-summary-edit", "Game details", f"{GAMES_PATH}/{game_id}"),
                className="hoopster-link-button",
            ),
            html.Div(
                nav_link("games-back", "All games", GAMES_PATH),
                className="hoopster-link-button",
            ),
        ],
    )
