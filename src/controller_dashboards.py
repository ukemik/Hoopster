"""Dashboards controller of Hoopster: the page builder, the pure filter, formatting and table
helpers, and the dashboard callbacks (player picker, filter bar, the four views and the
comparisons).

REQ-036 to REQ-040, REQ-064, REQ-065 (task 2026-10-23_dashboards). The dashboards only read:
there is no run_write call and nothing is committed. The filter controls feed one callback,
update_filters, which writes the validated filter store dsh-filters; the views read only that
store and re-validate it (filters_from_store), because a client can alter a store. Each view has
one server callback that draws its panel while its tab is active and raises PreventUpdate
otherwise (plan §3, R1). The number formatters are imported from controller_games, a documented
controller-to-controller import without a cycle (D12).
"""

import dataclasses
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from dash import Dash, Input, Output, no_update
from dash.development.base_component import Component
from dash.exceptions import PreventUpdate
from sqlalchemy.orm import Session

from config.app_config import (
    DASH_COMPARE_N_MAX,
    DASH_POSITION_BUCKET_SIZE,
    DASH_STREAK_LIMIT,
    HEAT_MAP_BAND_COLORS,
)
from src import data_handler
from src.app_controller import (
    DeviceClock,
    log_failure,
    new_reference_code,
    parse_player_value,
    set_current_player_id,
)
from src.controller_games import format_avg, format_pct, format_ratio, made_of_text
from src.data_dashboards import (
    FilterSet,
    get_box_scores_for_games,
    get_career_highs,
    get_drill_personal_bests,
    get_game_averages,
    get_heat_map_spots,
    get_heat_map_zones,
    get_longest_streaks,
    get_season_summaries,
    get_shooting_totals,
    get_shooting_trend,
    get_shot_position_trend,
    list_achieved_goals,
    list_heat_map_bands,
    list_zones,
)
from src.data_entry import list_shot_types
from src.data_games import (
    list_game_categories,
    list_game_formats,
    list_games,
    list_stat_definitions,
)
from src.data_players import list_live_players
from src.data_seasons import list_seasons
from src.layouts_dashboards import (
    ALL_VALUE,
    CHART_MODE_ZONES,
    COMPARE_GAMES,
    COMPARE_LAST,
    COMPARE_MODES,
    COMPARE_PERIODS,
    CONTEXT_GAME,
    CONTEXT_PRACTICE,
    GAMES_ROW,
    MEASURE_AVG,
    MEASURE_TOTAL,
    TREND_PERIOD_MONTH,
    TREND_PERIOD_WEEK,
    TREND_STAT_COLUMNS,
    TREND_STAT_DEFAULT,
    VIEW_BESTS,
    VIEW_CHART,
    VIEW_GAMES,
    VIEW_TRENDS,
    bests_content,
    build_position_figure,
    build_trend_figure,
    build_zone_heat_figure,
    chart_tiles_block,
    dashboards_layout,
    dimmed_text,
    game_option_label,
    game_options,
    games_average_block,
    season_options,
    season_table_block,
    stats_table,
    zone_list,
)
from src.layouts_games import (
    HEAT_NEUTRAL_RANK,
    HIDDEN_STYLE,
    SHOWN_STYLE,
    build_game_heat_figure,
    format_game_date,
)

logger = logging.getLogger(__name__)

DASH_TEXT = "–"
MEASURE_GAME = "game"

# The h_ref code CHECK (^[a-z0-9_]+$, at most 50 characters): codes are shape-checked only
# (D4). Ids are 1 to 18 digits, so they fit bigint.
CODE_PATTERN = re.compile(r"[a-z0-9_]{1,50}")
ID_PATTERN = re.compile(r"[0-9]{1,18}")
ISO_DATE_LENGTH = 10

# The 22 h_ref.stat_definitions codes in their sort order (plan §3.3).
BOX_SCORE_STATS: tuple[str, ...] = (
    "fg_pct",
    "three_pct",
    "ft_pct",
    "efg_pct",
    "ts_pct",
    "ast_to",
    "points",
    "fgm",
    "fga",
    "three_pm",
    "three_pa",
    "ftm",
    "fta",
    "off_rebounds",
    "def_rebounds",
    "total_rebounds",
    "assists",
    "steals",
    "blocks",
    "turnovers",
    "fouls",
    "minutes",
)
# Stats whose column is the same in both measures (percentages and AST/TO).
SAME_COLUMN_STATS: frozenset[str] = frozenset(
    {"fg_pct", "three_pct", "ft_pct", "efg_pct", "ts_pct", "ast_to"}
)
# Column-map exceptions of fn_game_averages / fn_season_summaries / v_game_box_scores.
AVG_COLUMN_EXCEPTIONS: dict[str, str] = {
    "total_rebounds": "avg_rebounds",
    "minutes": "avg_minutes",
}
TOTAL_COLUMN_EXCEPTIONS: dict[str, str] = {
    "total_rebounds": "total_rebounds",
    "minutes": "total_minutes",
}
GAME_COLUMN_EXCEPTIONS: dict[str, str] = {"minutes": "minutes_played"}
UNIT_PERCENT = "percent"
UNIT_RATIO = "ratio"
MINUTES_STAT = "minutes"

MESSAGE_SEASON = "Choose a valid season."
MESSAGE_DATES = "Dates must be real dates."
MESSAGE_DATE_ORDER = "From must be on or before To."
MESSAGE_FILTER = "Choose a valid filter."
MESSAGE_RELOAD = "Reload the page."
MESSAGE_LOAD_FAILED = "Could not load the dashboard. Reference: {ref}"
MESSAGE_GAMES_ONLY = "Game stats count games only. Choose All or Games."
MESSAGE_COMPARE_N = f"Enter a number of games from 1 to {DASH_COMPARE_N_MAX}."
MESSAGE_PERIOD_DATES = "Choose dates for both periods."
MESSAGE_TWO_GAMES = "Choose two different games."
MESSAGE_GAME_GONE = "That game is no longer available."

CMP_RESULT_ID = "dsh-cmp-prompt"
CMP_TABLE_ID = "dsh-cmp-stats"
CMP_SCOPE = "dsh-cmp"


# --- Pure helpers: filters (plan items 2.1 to 2.3) ---

def _is_positive_int(value: object) -> bool:
    """
    Description:
        Whether a JSON value is a positive integer (a bool is not).

    Parameters:
        value: the value.

    Return Values:
        bool: True for an int above 0 that is not a bool.

    Notes:
        Pure.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_all(value: object) -> bool:
    """
    Description:
        Whether a filter control is unset: None, "" or "all".

    Parameters:
        value: the control's value.

    Return Values:
        bool: True when the filter is off.

    Notes:
        Pure.
    """
    return value is None or value == "" or value == ALL_VALUE


def _parse_date_value(value: object) -> tuple[bool, date | None]:
    """
    Description:
        Reads a date picker's value.

    Parameters:
        value: "YYYY-MM-DD" (a longer date-time string is cut to its first 10 characters),
            "" or None.

    Return Values:
        tuple[bool, date | None]: (True, None) when empty, (True, date) when valid, (False,
        None) otherwise.

    Notes:
        Pure.
    """
    if value is None or value == "":
        return True, None
    if not isinstance(value, str):
        return False, None
    try:
        return True, date.fromisoformat(value[:ISO_DATE_LENGTH])
    except ValueError:
        return False, None


def _parse_code(value: object) -> tuple[bool, str | None]:
    """
    Description:
        Reads a code filter: unset, or a code of the h_ref shape.

    Parameters:
        value: the picker's value.

    Return Values:
        tuple[bool, str | None]: (True, None) when unset, (True, code) when the shape fits,
        (False, None) otherwise.

    Notes:
        Pure. Shape only (D4): an unknown code matches nothing.
    """
    if _is_all(value):
        return True, None
    if isinstance(value, str) and CODE_PATTERN.fullmatch(value) is not None:
        return True, value
    return False, None


def _parse_context(value: object) -> tuple[bool, str | None]:
    """
    Description:
        Reads the game vs practice control.

    Parameters:
        value: "all", "game" or "practice" (None and "" mean all).

    Return Values:
        tuple[bool, str | None]: (True, None) for all, (True, value) for game or practice,
        (False, None) otherwise.

    Notes:
        Pure.
    """
    if _is_all(value):
        return True, None
    if value in (CONTEXT_GAME, CONTEXT_PRACTICE):
        return True, str(value)
    return False, None


def _parse_dates(
    date_from: object, date_to: object
) -> tuple[tuple[date | None, date | None], str | None]:
    """
    Description:
        Reads a From / To pair; either end may be open.

    Parameters:
        date_from: the From picker's value.
        date_to: the To picker's value.

    Return Values:
        tuple: ((from, to), None) when valid, or ((None, None), the message).

    Notes:
        Pure. "Dates must be real dates." comes before "From must be on or before To.".
    """
    from_ok, first = _parse_date_value(date_from)
    to_ok, last = _parse_date_value(date_to)
    if not from_ok or not to_ok:
        return (None, None), MESSAGE_DATES
    if first is not None and last is not None and first > last:
        return (None, None), MESSAGE_DATE_ORDER
    return (first, last), None


def parse_filters(
    player_id: int,
    season: object,
    date_from: object,
    date_to: object,
    context: object,
    category: object,
    game_format: object,
    zone: object,
    shot_type: object,
) -> tuple[FilterSet | None, str | None]:
    """
    Description:
        Checks the filter bar (REQ-036) and builds filter set F. "all", None and "" mean no
        filter.

    Parameters:
        player_id: the selected player from dsh-player-store (server-written). Never a user
            id (COD-8-01).
        season: dsh-season.value ("all" or 1 to 18 digits).
        date_from: dsh-date-from.value ("YYYY-MM-DD" or empty).
        date_to: dsh-date-to.value.
        context: dsh-context.value ("all", "game", "practice").
        category: dsh-category.value (code or "all").
        game_format: dsh-format.value.
        zone: dsh-zone.value.
        shot_type: dsh-shot-type.value.

    Return Values:
        tuple[FilterSet | None, str | None]: (filters, None) when valid, else (None, the
        first message): "Choose a valid season.", "Dates must be real dates.", "From must be
        on or before To.", "Choose a valid filter.".

    Notes:
        Pure. A player id that is not a positive int gives "Choose a valid filter.".
    """
    season_id: int | None = None
    if not _is_all(season):
        if not isinstance(season, str) or ID_PATTERN.fullmatch(season) is None:
            return None, MESSAGE_SEASON
        season_id = int(season)
    (first, last), message = _parse_dates(date_from, date_to)
    if message is not None:
        return None, message
    context_ok, context_value = _parse_context(context)
    codes = [_parse_code(value) for value in (category, game_format, zone, shot_type)]
    if not _is_positive_int(player_id) or not context_ok or not all(ok for ok, _ in codes):
        return None, MESSAGE_FILTER
    return (
        FilterSet(
            player_id=player_id,
            season_id=season_id,
            date_from=first,
            date_to=last,
            context=context_value,
            game_category_code=codes[0][1],
            game_format_code=codes[1][1],
            zone_code=codes[2][1],
            shot_type_code=codes[3][1],
        ),
        None,
    )


def filters_to_store(filters: FilterSet) -> dict[str, object]:
    """
    Description:
        The dsh-filters store data of a filter set (plan §3.2).

    Parameters:
        filters: the validated filter set.

    Return Values:
        dict[str, object]: {"player_id", "season_id", "date_from", "date_to" (ISO text or
        None), "context", "game_category_code", "game_format_code", "zone_code",
        "shot_type_code"}.

    Notes:
        Pure. Written only by the page builder and update_filters.
    """
    return {
        "player_id": filters.player_id,
        "season_id": filters.season_id,
        "date_from": filters.date_from.isoformat() if filters.date_from else None,
        "date_to": filters.date_to.isoformat() if filters.date_to else None,
        "context": filters.context,
        "game_category_code": filters.game_category_code,
        "game_format_code": filters.game_format_code,
        "zone_code": filters.zone_code,
        "shot_type_code": filters.shot_type_code,
    }


def filters_from_store(data: object) -> FilterSet | None:
    """
    Description:
        Re-validates the dsh-filters store (the client can alter a store, R3).

    Parameters:
        data: dsh-filters.data.

    Return Values:
        FilterSet | None: the filter set, or None when the data is not a dict, player_id is
        not a positive int (a bool is refused), season_id is neither None nor a positive
        int, or any other field fails the parse_filters rules.

    Notes:
        Pure.
    """
    if not isinstance(data, dict):
        return None
    player_id = data.get("player_id")
    season_id = data.get("season_id")
    if not _is_positive_int(player_id):
        return None
    if season_id is not None and not _is_positive_int(season_id):
        return None
    filters, message = parse_filters(
        int(player_id),
        ALL_VALUE if season_id is None else str(season_id),
        data.get("date_from"),
        data.get("date_to"),
        data.get("context"),
        data.get("game_category_code"),
        data.get("game_format_code"),
        data.get("zone_code"),
        data.get("shot_type_code"),
    )
    if message is not None:
        return None
    return filters


def active_filter_count(filters: FilterSet) -> int:
    """
    Description:
        How many of the eight filters are on; a date range counts once.

    Parameters:
        filters: the filter set.

    Return Values:
        int: 0 to 7.

    Notes:
        Pure.
    """
    values = (
        filters.season_id,
        filters.date_from or filters.date_to,
        filters.context,
        filters.game_category_code,
        filters.game_format_code,
        filters.zone_code,
        filters.shot_type_code,
    )
    return sum(1 for value in values if value is not None)


def filter_summary_text(count: int) -> str:
    """
    Description:
        The filter accordion's control text.

    Parameters:
        count: active_filter_count.

    Return Values:
        str: "Filters" when 0, else "Filters · {n} on".

    Notes:
        Pure.
    """
    return "Filters" if count <= 0 else f"Filters · {count} on"


# --- Pure helpers: numbers, tiles, tables (plan items 2.4 to 2.9a; §3.3) ---

def stat_column(stat_code: str, measure: str) -> str:
    """
    Description:
        The column holding a stat (plan §3.3): fn_game_averages / fn_season_summaries for
        "avg" and "total", v_game_box_scores for "game".

    Parameters:
        stat_code: a BOX_SCORE_STATS code.
        measure: "avg", "total" or "game".

    Return Values:
        str: the column, e.g. avg_points, total_rebounds, avg_minutes, minutes_played,
        fg_pct.

    Notes:
        Pure. The percentages and AST/TO use the same column in every measure.
    """
    if measure == MEASURE_GAME:
        return GAME_COLUMN_EXCEPTIONS.get(stat_code, stat_code)
    if stat_code in SAME_COLUMN_STATS:
        return stat_code
    if measure == MEASURE_AVG:
        return AVG_COLUMN_EXCEPTIONS.get(stat_code, f"avg_{stat_code}")
    return TOTAL_COLUMN_EXCEPTIONS.get(stat_code, f"total_{stat_code}")


def stat_text(stat_code: str, unit: object, value: object, measure: str) -> str:
    """
    Description:
        Formats one stat value by its h_ref.stat_definitions unit (plan §3.3, REQ-024).

    Parameters:
        stat_code: the stat's code (minutes always has one decimal).
        unit: "percent", "ratio" or "count" (anything else is a count).
        value: the database value, or None.
        measure: "avg" (per-game average), "total" or "game" (one game).

    Return Values:
        str: "48.5%", "1.50", "12.3", "12" or "–" for NULL.

    Notes:
        Pure. A count total or single-game value is a whole number.
    """
    if value is None:
        return DASH_TEXT
    if stat_code == MINUTES_STAT:
        return format_avg(value)
    if unit == UNIT_PERCENT:
        return format_pct(value)
    if unit == UNIT_RATIO:
        return format_ratio(value)
    if measure == MEASURE_AVG:
        return format_avg(value)
    try:
        return str(int(Decimal(str(value))))
    except (InvalidOperation, ValueError, OverflowError):
        return DASH_TEXT


def _unit_of(definitions: dict[str, dict], stat_code: str) -> object:
    """
    Description:
        A stat's unit from its definition.

    Parameters:
        definitions: data_games.list_stat_definitions.
        stat_code: the code.

    Return Values:
        object: the unit, or None when the definition is missing.

    Notes:
        Pure.
    """
    return (definitions.get(stat_code) or {}).get("unit")


def games_tiles(
    averages: dict, measure: str, definitions: dict[str, dict]
) -> list[tuple[str, str]]:
    """
    Description:
        The Games view's tiles (REQ-039): every BOX_SCORE_STATS stat per game or in total.

    Parameters:
        averages: data_dashboards.get_game_averages row.
        measure: "avg" or "total".
        definitions: data_games.list_stat_definitions (units).

    Return Values:
        list[tuple[str, str]]: (stat_code, formatted value) in BOX_SCORE_STATS order.

    Notes:
        Pure.
    """
    tiles: list[tuple[str, str]] = []
    for code in BOX_SCORE_STATS:
        value = averages.get(stat_column(code, measure))
        tiles.append((code, stat_text(code, _unit_of(definitions, code), value, measure)))
    return tiles


def chart_tiles(totals: dict) -> list[tuple[str, str]]:
    """
    Description:
        The Shot chart view's tiles after the filters (REQ-036, REQ-064): FG, 3P, FT
        (made/attempted · %), eFG%, TS%, PTS.

    Parameters:
        totals: data_dashboards.get_shooting_totals row.

    Return Values:
        list[tuple[str, str]]: (stat_code, formatted value) pairs.

    Notes:
        Pure.
    """
    return [
        ("fg_pct", made_of_text(totals.get("fgm"), totals.get("fga"), totals.get("fg_pct"))),
        (
            "three_pct",
            made_of_text(totals.get("three_pm"), totals.get("three_pa"), totals.get("three_pct")),
        ),
        ("ft_pct", made_of_text(totals.get("ftm"), totals.get("fta"), totals.get("ft_pct"))),
        ("efg_pct", format_pct(totals.get("efg_pct"))),
        ("ts_pct", format_pct(totals.get("ts_pct"))),
        ("points", str(int(totals.get("points") or 0))),
    ]


def season_table(
    summaries: list[dict], measure: str, definitions: dict[str, dict]
) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """
    Description:
        Season vs season (REQ-039, D7, D10) as stats × seasons.

    Parameters:
        summaries: data_dashboards.get_season_summaries rows, in function order ("No
            season" last).
        measure: "avg" or "total".
        definitions: data_games.list_stat_definitions (units).

    Return Values:
        tuple: (headers = the season names, rows = ("games", games played per season), then
        (stat_code, cells) per BOX_SCORE_STATS stat).

    Notes:
        Pure.
    """
    headers = [str(row.get("season_name")) for row in summaries]
    rows: list[tuple[str, list[str]]] = [
        (GAMES_ROW, [str(int(row.get("games_played") or 0)) for row in summaries])
    ]
    for code in BOX_SCORE_STATS:
        column = stat_column(code, measure)
        unit = _unit_of(definitions, code)
        cells = [stat_text(code, unit, row.get(column), measure) for row in summaries]
        rows.append((code, cells))
    return headers, rows


def compare_from_averages(
    first: dict, second: dict, definitions: dict[str, dict]
) -> list[tuple[str, str, str]]:
    """
    Description:
        Two fn_game_averages rows side by side (REQ-065): games played, then every stat per
        game.

    Parameters:
        first: the left column's row (last N, or period A).
        second: the right column's row (previous N, or period B).
        definitions: data_games.list_stat_definitions (units).

    Return Values:
        list[tuple[str, str, str]]: ("games", n_a, n_b), then (stat_code, a, b).

    Notes:
        Pure.
    """
    rows = [
        (
            GAMES_ROW,
            str(int(first.get("games_played") or 0)),
            str(int(second.get("games_played") or 0)),
        )
    ]
    for code in BOX_SCORE_STATS:
        column = stat_column(code, MEASURE_AVG)
        unit = _unit_of(definitions, code)
        rows.append(
            (
                code,
                stat_text(code, unit, first.get(column), MEASURE_AVG),
                stat_text(code, unit, second.get(column), MEASURE_AVG),
            )
        )
    return rows


def compare_from_games(
    first: dict, second: dict, definitions: dict[str, dict]
) -> list[tuple[str, str, str]]:
    """
    Description:
        Two single games side by side (REQ-065): every stat of their v_game_box_scores rows.

    Parameters:
        first: game A's box score.
        second: game B's box score.
        definitions: data_games.list_stat_definitions (units).

    Return Values:
        list[tuple[str, str, str]]: (stat_code, a, b) per BOX_SCORE_STATS stat (no games
        row).

    Notes:
        Pure.
    """
    rows: list[tuple[str, str, str]] = []
    for code in BOX_SCORE_STATS:
        column = stat_column(code, MEASURE_GAME)
        unit = _unit_of(definitions, code)
        rows.append(
            (
                code,
                stat_text(code, unit, first.get(column), MEASURE_GAME),
                stat_text(code, unit, second.get(column), MEASURE_GAME),
            )
        )
    return rows


def parse_compare_n(value: object) -> int | None:
    """
    Description:
        Reads the "Last N" field.

    Parameters:
        value: dsh-cmp-n.value (an int, a whole float, digits as text, "" or None).

    Return Values:
        int | None: the whole number 1 to DASH_COMPARE_N_MAX, else None (a bool is None).

    Notes:
        Pure.
    """
    number: int | None = None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        number = int(value.strip())
    if number is None or not 1 <= number <= DASH_COMPARE_N_MAX:
        return None
    return number


def _range_text(first: date | None, last: date | None) -> str:
    """
    Description:
        A period's header: "{from}–{to}", "From {from}" or "Until {to}".

    Parameters:
        first: the period's start, or None.
        last: the period's end, or None.

    Return Values:
        str: the text ("" when both are None).

    Notes:
        Pure.
    """
    if first is not None and last is not None:
        return f"{format_game_date(first)}–{format_game_date(last)}"
    if first is not None:
        return f"From {format_game_date(first)}"
    if last is not None:
        return f"Until {format_game_date(last)}"
    return ""


def period_headers(
    mode: str,
    n: int | None,
    ranges: tuple[tuple[date | None, date | None], tuple[date | None, date | None]] | None,
) -> tuple[str, str]:
    """
    Description:
        The two column headers of a comparison (REQ-065).

    Parameters:
        mode: "last" or "periods".
        n: N of "Last N" (mode "last").
        ranges: ((a_from, a_to), (b_from, b_to)) (mode "periods").

    Return Values:
        tuple[str, str]: ("Last {n}", "Previous {n}"), or the two period texts.

    Notes:
        Pure.
    """
    if mode == COMPARE_LAST or ranges is None:
        return f"Last {n}", f"Previous {n}"
    (a_from, a_to), (b_from, b_to) = ranges
    return _range_text(a_from, a_to), _range_text(b_from, b_to)


def zone_lines(zone_rows: list[dict]) -> list[tuple[str, str, int | None]]:
    """
    Description:
        The zone totals list (REQ-038): (zone label, "12/20 · 60.0% · Warm", band rank).

    Parameters:
        zone_rows: data_dashboards.get_heat_map_zones.

    Return Values:
        list[tuple[str, str, int | None]]: one line per zone. The rank is None when the zone
        has no attempts; a zone with attempts but no matching band gets the neutral rank
        (HEAT_NEUTRAL_RANK) and no band label.

    Notes:
        Pure. The rank rule matches layouts_dashboards.build_zone_heat_figure, so the list
        swatch and the zone map show the same colour (REQ-038).
    """
    lines: list[tuple[str, str, int | None]] = []
    for row in zone_rows:
        text = made_of_text(row.get("makes"), row.get("attempts"), row.get("make_pct"))
        if int(row.get("attempts") or 0) <= 0:
            lines.append((str(row.get("zone_label")), text, None))
            continue
        rank = row.get("band_rank")
        has_band = isinstance(rank, int) and rank in HEAT_MAP_BAND_COLORS
        if has_band and row.get("band_label"):
            text = f"{text} · {row.get('band_label')}"
        band_rank = int(rank) if has_band else HEAT_NEUTRAL_RANK
        lines.append((str(row.get("zone_label")), text, band_rank))
    return lines


def group_drill_bests(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    """
    Description:
        Groups drill personal bests by drill lineage, in row order (REQ-040).

    Parameters:
        rows: data_dashboards.get_drill_personal_bests.

    Return Values:
        list[tuple[str, list[dict]]]: (drill name, its rows) per drill_id.

    Notes:
        Pure.
    """
    groups: dict[int, tuple[str, list[dict]]] = {}
    for row in rows:
        drill_id = int(row["drill_id"])
        if drill_id not in groups:
            groups[drill_id] = (str(row.get("drill_name")), [])
        groups[drill_id][1].append(row)
    return list(groups.values())


def _selected_id(value: object) -> int | None:
    """
    Description:
        Reads a game picker's value (a game id as digits).

    Parameters:
        value: the picker's value.

    Return Values:
        int | None: the id, or None.

    Notes:
        Pure.
    """
    if isinstance(value, str) and ID_PATTERN.fullmatch(value) is not None:
        return int(value)
    return None


# --- Page builder (plan item 2.10) ---

def build_dashboards_page(player_id: int, clock: DeviceClock, params: dict[str, str]) -> Component:
    """
    Description:
        Builds /dashboards (REQ-036 to REQ-040, REQ-065): the player picker, the filter bar
        and the four views, for the selected player with no filters.

    Parameters:
        player_id: the selected player from current_player_id() (the router passes a live
            one). Never a user id (COD-8-01).
        clock: the device clock (unused).
        params: the route parameters (none).

    Return Values:
        Component: the page.

    Notes:
        Exceptions propagate to the router, which shows the error page with a reference.
        dsh-filters starts as the player with no filters; dsh-player-store as {"player_id"}.
    """
    session = data_handler.get_session()
    return dashboards_layout(
        list_live_players(session),
        player_id,
        list_seasons(session, player_id),
        list_game_categories(session),
        list_game_formats(session),
        list_zones(session),
        list_shot_types(session),
        list_heat_map_bands(session),
        list_games(session, player_id),
        list_stat_definitions(session),
        filters_to_store(FilterSet(player_id=player_id)),
    )


# --- Callbacks: player and filter bar (plan items 2.11 to 2.13) ---

def select_dashboard_player(value: str | None) -> tuple[object, ...]:
    """
    Description:
        The dashboards player picker: makes the picked player the selected player for the
        whole app (D2), resets the season filter and the compare game pickers to that
        player's, and writes dsh-player-store (which re-runs update_filters).

    Parameters:
        value: dsh-player.value, the player id as a string.

    Return Values:
        tuple: dsh-season.data, dsh-season.value ("all"), dsh-cmp-game-a.data, .value
        (None), dsh-cmp-game-b.data, .value (None), dsh-player-store.data
        ({"player_id"}), dsh-filter-message.children (""); on a read failure no_update for
        all but the message, which carries the reference.

    Notes:
        A boundary (COD-4-04). A value that is not a player id, or a player no longer live
        (warning), changes nothing (PreventUpdate). The store is written only after
        set_current_player_id accepted the id (Context Adaptation Table).
    """
    player_id = parse_player_value(value)
    if player_id is None:
        raise PreventUpdate
    try:
        if not set_current_player_id(player_id):
            logger.warning("Dashboards picker chose player %s, which is not live", player_id)
            raise PreventUpdate
        session = data_handler.get_session()
        seasons = list_seasons(session, player_id)
        games = list_games(session, player_id)
    except PreventUpdate:
        raise
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Dashboards player change failed (ref %s)", ref)
        return (*([no_update] * 7), MESSAGE_LOAD_FAILED.format(ref=ref))
    options = game_options(games)
    return (
        season_options(seasons),
        ALL_VALUE,
        options,
        None,
        options,
        None,
        {"player_id": player_id},
        "",
    )


def clear_filters(n_clicks: int | None) -> tuple[object, ...]:
    """
    Description:
        Clear filters: every filter control back to "no filter".

    Parameters:
        n_clicks: dsh-filter-clear.n_clicks.

    Return Values:
        tuple: dsh-season "all", dsh-date-from None, dsh-date-to None, dsh-context "all",
        dsh-category / dsh-format / dsh-zone / dsh-shot-type "all".

    Notes:
        No database access; update_filters then writes the store.
    """
    if not n_clicks:
        raise PreventUpdate
    return ALL_VALUE, None, None, ALL_VALUE, ALL_VALUE, ALL_VALUE, ALL_VALUE, ALL_VALUE


def update_filters(
    player_store: object,
    season: object,
    date_from: object,
    date_to: object,
    context: object,
    category: object,
    game_format: object,
    zone: object,
    shot_type: object,
) -> tuple[object, object, str]:
    """
    Description:
        The single writer of dsh-filters after the page build: validates the filter bar and
        stores filter set F (REQ-036).

    Parameters:
        player_store: dsh-player-store.data ({"player_id": int}, server-written).
        season ... shot_type: the eight filter controls' values.

    Return Values:
        tuple[object, object, str]: dsh-filters.data, dsh-filter-summary.children and
        dsh-filter-message.children. Invalid input: no_update, no_update, the message (the
        views keep their last valid filters).

    Notes:
        Pure apart from logging. A player store that is not {"player_id": positive int} is
        refused with "Reload the page." (logged as a warning).
    """
    player_id = player_store.get("player_id") if isinstance(player_store, dict) else None
    if not _is_positive_int(player_id):
        logger.warning("Dashboards player store is not a player id; filters not updated")
        return no_update, no_update, MESSAGE_RELOAD
    filters, message = parse_filters(
        int(player_id), season, date_from, date_to, context, category, game_format, zone,
        shot_type,
    )
    if filters is None:
        return no_update, no_update, message or MESSAGE_FILTER
    return filters_to_store(filters), filter_summary_text(active_filter_count(filters)), ""


# --- Callbacks: the four views (plan items 2.14 to 2.18) ---

def _store_filters(filters_data: object) -> FilterSet | None:
    """
    Description:
        Re-validates dsh-filters for a view, logging a refused store.

    Parameters:
        filters_data: dsh-filters.data.

    Return Values:
        FilterSet | None: the filters, or None (the view shows "Reload the page.").

    Notes:
        A refused store is logged as a warning: the user is told to reload.
    """
    filters = filters_from_store(filters_data)
    if filters is None:
        logger.warning("Dashboards filter store refused")
    return filters


def render_chart_view(view: object, filters_data: object, mode: object) -> tuple[object, ...]:
    """
    Description:
        The Shot chart view (REQ-038, REQ-036): the filtered shooting tiles and the heat map
        by spot, or by zone with the zone totals list.

    Parameters:
        view: dsh-view.value.
        filters_data: dsh-filters.data.
        mode: dsh-chart-mode.value ("spots" or "zones"; anything else is spots).

    Return Values:
        tuple: dsh-chart-tiles.children, dsh-heat-chart.figure, dsh-zone-list.children ([]
        in Spots mode), dsh-chart-message.children.

    Notes:
        A boundary (COD-4-04). PreventUpdate while another tab is active.
    """
    if view != VIEW_CHART:
        raise PreventUpdate
    filters = _store_filters(filters_data)
    if filters is None:
        return no_update, no_update, no_update, MESSAGE_RELOAD
    try:
        session = data_handler.get_session()
        definitions = list_stat_definitions(session)
        totals = get_shooting_totals(session, filters)
        spots = get_heat_map_spots(session, filters)
        if mode == CHART_MODE_ZONES:
            zones = get_heat_map_zones(session, filters)
            figure = build_zone_heat_figure(spots, zones)
            zone_children = zone_list(zone_lines(zones))
        else:
            figure = build_game_heat_figure(spots)
            zone_children = []
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Dashboard shot chart failed (ref %s)", ref)
        return no_update, no_update, no_update, MESSAGE_LOAD_FAILED.format(ref=ref)
    return chart_tiles_block(chart_tiles(totals), definitions), figure, zone_children, ""


def render_trends_view(
    view: object, filters_data: object, period: object, stat: object
) -> tuple[object, object, str]:
    """
    Description:
        The Trends view (REQ-037): FG%, 3P% or FT% per week or month (games and practice as
        separate lines) and the warm-up vs fatigue chart.

    Parameters:
        view: dsh-view.value.
        filters_data: dsh-filters.data.
        period: dsh-trend-period.value (unknown -> "month").
        stat: dsh-trend-stat.value (unknown -> "fg").

    Return Values:
        tuple: dsh-trend-chart.figure, dsh-fatigue-chart.figure,
        dsh-trends-message.children.

    Notes:
        A boundary (COD-4-04). Buckets of DASH_POSITION_BUCKET_SIZE single field goals.
    """
    if view != VIEW_TRENDS:
        raise PreventUpdate
    period_value = (
        period if period in (TREND_PERIOD_WEEK, TREND_PERIOD_MONTH) else TREND_PERIOD_MONTH
    )
    stat_value = (
        stat if isinstance(stat, str) and stat in TREND_STAT_COLUMNS else TREND_STAT_DEFAULT
    )
    filters = _store_filters(filters_data)
    if filters is None:
        return no_update, no_update, MESSAGE_RELOAD
    try:
        session = data_handler.get_session()
        rows = get_shooting_trend(session, str(period_value), filters)
        buckets = get_shot_position_trend(session, DASH_POSITION_BUCKET_SIZE, filters)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Dashboard trends failed (ref %s)", ref)
        return no_update, no_update, MESSAGE_LOAD_FAILED.format(ref=ref)
    return build_trend_figure(rows, stat_value), build_position_figure(buckets), ""


def render_games_view(
    view: object, filters_data: object, measure: object
) -> tuple[object, object, str]:
    """
    Description:
        The Games view (REQ-039, D10): per-game averages or totals of every box-score stat
        after the filters, and the unfiltered season vs season table.

    Parameters:
        view: dsh-view.value.
        filters_data: dsh-filters.data.
        measure: dsh-games-measure.value ("avg" or "total"; anything else is "avg").

    Return Values:
        tuple: dsh-games-avg.children, dsh-season-table.children,
        dsh-games-message.children.

    Notes:
        A boundary (COD-4-04). Context Practice shows the games-only text instead of the
        tiles (the season table is unfiltered, D7).
    """
    if view != VIEW_GAMES:
        raise PreventUpdate
    measure_value = measure if measure in (MEASURE_AVG, MEASURE_TOTAL) else MEASURE_AVG
    filters = _store_filters(filters_data)
    if filters is None:
        return no_update, no_update, MESSAGE_RELOAD
    try:
        session = data_handler.get_session()
        definitions = list_stat_definitions(session)
        summaries = get_season_summaries(session, filters.player_id)
        if filters.context == CONTEXT_PRACTICE:
            averages_children: object = [dimmed_text("dsh-games-only", MESSAGE_GAMES_ONLY)]
        else:
            averages = get_game_averages(session, filters)
            averages_children = games_average_block(
                int(averages.get("games_played") or 0),
                str(measure_value),
                games_tiles(averages, str(measure_value), definitions),
                definitions,
            )
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Dashboard games view failed (ref %s)", ref)
        return no_update, no_update, MESSAGE_LOAD_FAILED.format(ref=ref)
    headers, rows = season_table(summaries, str(measure_value), definitions)
    return averages_children, season_table_block(headers, rows, definitions), ""


def _compare_table(
    headers: tuple[str, str], rows: list[tuple[str, str, str]], definitions: dict[str, dict]
) -> Component:
    """
    Description:
        The comparison result table (scope "dsh-cmp").

    Parameters:
        headers: the two column headers.
        rows: (code, a, b) rows.
        definitions: data_games.list_stat_definitions.

    Return Values:
        Component: stats_table "dsh-cmp-stats".

    Notes:
        Pure.
    """
    return stats_table(
        CMP_TABLE_ID,
        list(headers),
        [(code, [first, second]) for code, first, second in rows],
        definitions,
        CMP_SCOPE,
    )


def _compare_last(
    session: Session, filters: FilterSet, n_value: object, definitions: dict[str, dict]
) -> Component:
    """
    Description:
        Last N vs the previous N games under the filters (REQ-065).

    Parameters:
        session: the request session.
        filters: the validated filters.
        n_value: dsh-cmp-n.value.
        definitions: data_games.list_stat_definitions.

    Return Values:
        Component: the table, or the dimmed prompt for an invalid N.

    Notes:
        Two fn_game_averages calls: (N, skip 0) and (N, skip N).
    """
    count = parse_compare_n(n_value)
    if count is None:
        return dimmed_text(CMP_RESULT_ID, MESSAGE_COMPARE_N)
    latest = get_game_averages(session, filters, count, 0)
    previous = get_game_averages(session, filters, count, count)
    headers = period_headers(COMPARE_LAST, count, None)
    rows = compare_from_averages(latest, previous, definitions)
    return _compare_table(headers, rows, definitions)


def _compare_periods(
    session: Session,
    filters: FilterSet,
    dates: tuple[object, object, object, object],
    definitions: dict[str, dict],
) -> Component:
    """
    Description:
        Two date periods side by side under the other filters (REQ-065): each period's dates
        replace the filter dates.

    Parameters:
        session: the request session.
        filters: the validated filters.
        dates: (a_from, a_to, b_from, b_to) picker values.
        definitions: data_games.list_stat_definitions.

    Return Values:
        Component: the table, or a dimmed prompt ("Dates must be real dates.", "Choose
        dates for both periods.", "From must be on or before To.").

    Notes:
        Two fn_game_averages calls.
    """
    (a_from, a_to), message_a = _parse_dates(dates[0], dates[1])
    (b_from, b_to), message_b = _parse_dates(dates[2], dates[3])
    message = message_a or message_b
    if message is not None:
        return dimmed_text(CMP_RESULT_ID, message)
    if (a_from is None and a_to is None) or (b_from is None and b_to is None):
        return dimmed_text(CMP_RESULT_ID, MESSAGE_PERIOD_DATES)
    period_a = dataclasses.replace(filters, date_from=a_from, date_to=a_to)
    period_b = dataclasses.replace(filters, date_from=b_from, date_to=b_to)
    first = get_game_averages(session, period_a)
    second = get_game_averages(session, period_b)
    headers = period_headers(COMPARE_PERIODS, None, ((a_from, a_to), (b_from, b_to)))
    rows = compare_from_averages(first, second, definitions)
    return _compare_table(headers, rows, definitions)


def _compare_games(
    session: Session,
    filters: FilterSet,
    picks: tuple[object, object],
    definitions: dict[str, dict],
) -> Component:
    """
    Description:
        Two single games side by side (REQ-065); the filters do not apply (D5).

    Parameters:
        session: the request session.
        filters: the validated filters (only player_id is used).
        picks: (dsh-cmp-game-a.value, dsh-cmp-game-b.value).
        definitions: data_games.list_stat_definitions.

    Return Values:
        Component: the table, or a dimmed prompt ("Choose two different games.", "That game
        is no longer available.").

    Notes:
        One get_box_scores_for_games call; SQL also requires the filter player
        (Context Adaptation Table).
    """
    game_a = _selected_id(picks[0])
    game_b = _selected_id(picks[1])
    if game_a is None or game_b is None or game_a == game_b:
        return dimmed_text(CMP_RESULT_ID, MESSAGE_TWO_GAMES)
    boxes = get_box_scores_for_games(session, filters.player_id, [game_a, game_b])
    first = boxes.get(game_a)
    second = boxes.get(game_b)
    if first is None or second is None:
        return dimmed_text(CMP_RESULT_ID, MESSAGE_GAME_GONE)
    headers = (game_option_label(first), game_option_label(second))
    rows = compare_from_games(first, second, definitions)
    return _compare_table(headers, rows, definitions)


def render_compare(
    view: object,
    filters_data: object,
    mode: object,
    n_value: object,
    a_from: object,
    a_to: object,
    b_from: object,
    b_to: object,
    game_a: object,
    game_b: object,
) -> tuple[object, ...]:
    """
    Description:
        The comparisons of the Games view (REQ-065, D9): last N vs previous N, two date
        periods, or two games, side by side in one table.

    Parameters:
        view: dsh-view.value.
        filters_data: dsh-filters.data.
        mode: dsh-cmp-mode.value ("last", "periods", "games"; anything else is "last").
        n_value: dsh-cmp-n.value.
        a_from, a_to, b_from, b_to: the four period pickers.
        game_a, game_b: the two game pickers.

    Return Values:
        tuple: dsh-cmp-last-wrap.style, dsh-cmp-periods-wrap.style,
        dsh-cmp-games-wrap.style (only the mode's group shown) and dsh-cmp-result.children
        (the table, or a dimmed prompt or error "dsh-cmp-prompt").

    Notes:
        A boundary (COD-4-04); errors go to dsh-cmp-result, so dsh-games-message has one
        writer. Context Practice gives the games-only text for Last N and Periods.
    """
    if view != VIEW_GAMES:
        raise PreventUpdate
    mode_value = mode if mode in COMPARE_MODES else COMPARE_LAST
    styles = tuple(SHOWN_STYLE if mode_value == item else HIDDEN_STYLE for item in COMPARE_MODES)
    filters = _store_filters(filters_data)
    if filters is None:
        return (*styles, dimmed_text(CMP_RESULT_ID, MESSAGE_RELOAD))
    if mode_value != COMPARE_GAMES and filters.context == CONTEXT_PRACTICE:
        return (*styles, dimmed_text(CMP_RESULT_ID, MESSAGE_GAMES_ONLY))
    try:
        session = data_handler.get_session()
        definitions = list_stat_definitions(session)
        if mode_value == COMPARE_GAMES:
            result = _compare_games(session, filters, (game_a, game_b), definitions)
        elif mode_value == COMPARE_PERIODS:
            dates = (a_from, a_to, b_from, b_to)
            result = _compare_periods(session, filters, dates, definitions)
        else:
            result = _compare_last(session, filters, n_value, definitions)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Dashboard compare failed (ref %s)", ref)
        result = dimmed_text(CMP_RESULT_ID, MESSAGE_LOAD_FAILED.format(ref=ref))
    return (*styles, result)


def render_bests_view(view: object, filters_data: object) -> tuple[object, str]:
    """
    Description:
        The Bests view (REQ-040, D13): career highs, the longest made-shot streaks (the only
        part that follows the filters), drill personal bests and goals achieved.

    Parameters:
        view: dsh-view.value.
        filters_data: dsh-filters.data.

    Return Values:
        tuple[object, str]: dsh-bests-content.children, dsh-bests-message.children.

    Notes:
        A boundary (COD-4-04). Links use only ids from the function rows.
    """
    if view != VIEW_BESTS:
        raise PreventUpdate
    filters = _store_filters(filters_data)
    if filters is None:
        return no_update, MESSAGE_RELOAD
    try:
        session = data_handler.get_session()
        definitions = list_stat_definitions(session)
        highs = get_career_highs(session, filters.player_id)
        streaks = get_longest_streaks(session, filters, DASH_STREAK_LIMIT)
        drill_bests = get_drill_personal_bests(session, filters.player_id)
        goals = list_achieved_goals(session, filters.player_id)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Dashboard bests failed (ref %s)", ref)
        return no_update, MESSAGE_LOAD_FAILED.format(ref=ref)
    content = bests_content(highs, streaks, group_drill_bests(drill_bests), goals, definitions)
    return content, ""


# --- Registration (plan item 2.19) ---

def register_dashboards_callbacks(app: Dash) -> None:
    """
    Description:
        Registers every dashboard callback: the player picker, Clear filters, the filter
        store, the four views and the comparisons.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        Single writers: dsh-filters is written only by the page builder and update_filters;
        dsh-player-store only by the page builder and select_dashboard_player. dsh-season
        .value and dsh-filter-message have a second writer each (clear_filters,
        select_dashboard_player) with allow_duplicate. The view callbacks are not
        prevent_initial_call: they draw the active view when the page is inserted and raise
        PreventUpdate for the others. The components exist only on /dashboards, so the app
        runs with suppress_callback_exceptions.
    """
    app.callback(
        Output("dsh-season", "data"),
        Output("dsh-season", "value"),
        Output("dsh-cmp-game-a", "data"),
        Output("dsh-cmp-game-a", "value"),
        Output("dsh-cmp-game-b", "data"),
        Output("dsh-cmp-game-b", "value"),
        Output("dsh-player-store", "data"),
        Output("dsh-filter-message", "children", allow_duplicate=True),
        Input("dsh-player", "value"),
        prevent_initial_call=True,
    )(select_dashboard_player)

    app.callback(
        Output("dsh-season", "value", allow_duplicate=True),
        Output("dsh-date-from", "value"),
        Output("dsh-date-to", "value"),
        Output("dsh-context", "value"),
        Output("dsh-category", "value"),
        Output("dsh-format", "value"),
        Output("dsh-zone", "value"),
        Output("dsh-shot-type", "value"),
        Input("dsh-filter-clear", "n_clicks"),
        prevent_initial_call=True,
    )(clear_filters)

    app.callback(
        Output("dsh-filters", "data"),
        Output("dsh-filter-summary", "children"),
        Output("dsh-filter-message", "children"),
        Input("dsh-player-store", "data"),
        Input("dsh-season", "value"),
        Input("dsh-date-from", "value"),
        Input("dsh-date-to", "value"),
        Input("dsh-context", "value"),
        Input("dsh-category", "value"),
        Input("dsh-format", "value"),
        Input("dsh-zone", "value"),
        Input("dsh-shot-type", "value"),
        prevent_initial_call=True,
    )(update_filters)

    app.callback(
        Output("dsh-chart-tiles", "children"),
        Output("dsh-heat-chart", "figure"),
        Output("dsh-zone-list", "children"),
        Output("dsh-chart-message", "children"),
        Input("dsh-view", "value"),
        Input("dsh-filters", "data"),
        Input("dsh-chart-mode", "value"),
    )(render_chart_view)

    app.callback(
        Output("dsh-trend-chart", "figure"),
        Output("dsh-fatigue-chart", "figure"),
        Output("dsh-trends-message", "children"),
        Input("dsh-view", "value"),
        Input("dsh-filters", "data"),
        Input("dsh-trend-period", "value"),
        Input("dsh-trend-stat", "value"),
    )(render_trends_view)

    app.callback(
        Output("dsh-games-avg", "children"),
        Output("dsh-season-table", "children"),
        Output("dsh-games-message", "children"),
        Input("dsh-view", "value"),
        Input("dsh-filters", "data"),
        Input("dsh-games-measure", "value"),
    )(render_games_view)

    app.callback(
        Output("dsh-cmp-last-wrap", "style"),
        Output("dsh-cmp-periods-wrap", "style"),
        Output("dsh-cmp-games-wrap", "style"),
        Output("dsh-cmp-result", "children"),
        Input("dsh-view", "value"),
        Input("dsh-filters", "data"),
        Input("dsh-cmp-mode", "value"),
        Input("dsh-cmp-n", "value"),
        Input("dsh-cmp-a-from", "value"),
        Input("dsh-cmp-a-to", "value"),
        Input("dsh-cmp-b-from", "value"),
        Input("dsh-cmp-b-to", "value"),
        Input("dsh-cmp-game-a", "value"),
        Input("dsh-cmp-game-b", "value"),
    )(render_compare)

    app.callback(
        Output("dsh-bests-content", "children"),
        Output("dsh-bests-message", "children"),
        Input("dsh-view", "value"),
        Input("dsh-filters", "data"),
    )(render_bests_view)
