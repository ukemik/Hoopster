"""Data access for the dashboards: the shared filter set, the reference lists of the filter bar
and the reads behind the four views (shot chart, trends, games, bests).

REQ-036 to REQ-040, REQ-065 (task 2026-10-23_dashboards). Read only: this module never writes
and never commits (COD-6-01). Every number comes from an existing h_analytics function or view
(sql/h_analytics.sql), which already hides deleted rows and rows of removed profiles
(COD-22-03); the three plain reads here (zones, heat-map bands, achieved goals) test deleted_at
themselves. Every statement is a module constant with bound, explicitly cast parameters
(COD-14-03).
"""

import logging
from dataclasses import dataclass
from datetime import date
from typing import cast

from sqlalchemy.orm import Session

from src.data_handler import fetch_all, fetch_one

logger = logging.getLogger(__name__)

TREND_PERIODS: frozenset[str] = frozenset({"week", "month"})


# --- Filter set F (REQ-036) ---

@dataclass(frozen=True)
class FilterSet:
    """
    Description:
        The dashboard filter set F of sql/h_analytics.sql: the player plus eight optional
        filters (None = not filtered).

    Parameters:
        player_id: h_data.players.player_id of the selected player. Never a user id
            (COD-8-01).
        season_id: h_data.seasons.season_id, or None for every season.
        date_from: first event date counted, or None (open start).
        date_to: last event date counted, or None (open end).
        context: "game", "practice" or None for both.
        game_category_code: h_ref.game_categories code, or None (games only when set).
        game_format_code: h_ref.game_formats code, or None (games only when set).
        zone_code: h_ref.zones code, or None (field goals only; free throws unaffected).
        shot_type_code: h_ref.shot_types code, or None (field goals only).

    Return Values:
        Not applicable (frozen data class).

    Notes:
        Built only by controller_dashboards.parse_filters / filters_from_store, which check
        every field's shape. An unknown code is a bound value that matches nothing (D4).
    """

    player_id: int
    season_id: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    context: str | None = None
    game_category_code: str | None = None
    game_format_code: str | None = None
    zone_code: str | None = None
    shot_type_code: str | None = None


# The nine filter arguments of every F function, in signature order. Constant text only: the
# values are always bound (COD-14-03).
FILTER_ARGS_SQL = """CAST(:player_id AS bigint),
       CAST(:season_id AS bigint),
       CAST(:date_from AS date),
       CAST(:date_to AS date),
       CAST(:context AS text),
       CAST(:game_category_code AS text),
       CAST(:game_format_code AS text),
       CAST(:zone_code AS text),
       CAST(:shot_type_code AS text)"""


def filter_params(filters: FilterSet) -> dict[str, object]:
    """
    Description:
        The nine bound parameters of filter set F, named as the function parameters without
        their "p_" prefix.

    Parameters:
        filters: the validated filter set.

    Return Values:
        dict[str, object]: {"player_id", "season_id", "date_from", "date_to", "context",
        "game_category_code", "game_format_code", "zone_code", "shot_type_code"}.

    Notes:
        Pure.
    """
    return {
        "player_id": filters.player_id,
        "season_id": filters.season_id,
        "date_from": filters.date_from,
        "date_to": filters.date_to,
        "context": filters.context,
        "game_category_code": filters.game_category_code,
        "game_format_code": filters.game_format_code,
        "zone_code": filters.zone_code,
        "shot_type_code": filters.shot_type_code,
    }


# --- Reference lists of the filter bar and the legend (REQ-036, REQ-038) ---

LIST_ZONES_SQL = """
SELECT z.zone_code AS code, z.label
FROM h_ref.zones z
WHERE z.deleted_at IS NULL
ORDER BY z.sort_order
"""

LIST_HEAT_MAP_BANDS_SQL = """
SELECT hb.zone_code,
       z.label AS zone_label,
       hb.band_rank::int AS band_rank,
       hb.band_label,
       hb.min_pct,
       hb.max_pct
FROM h_ref.heat_map_bands hb
JOIN h_ref.zones z ON z.zone_code = hb.zone_code
WHERE hb.deleted_at IS NULL
  AND z.deleted_at IS NULL
ORDER BY z.sort_order, hb.band_rank
"""


def list_zones(session: Session) -> list[dict[str, object]]:
    """
    Description:
        Lists the live court zones for the zone filter (REQ-036).

    Parameters:
        session: the request session.

    Return Values:
        list[dict[str, object]]: {"code", "label"} per zone, by sort_order.

    Notes:
        Read only.
    """
    return fetch_all(session, LIST_ZONES_SQL)


def list_heat_map_bands(session: Session) -> list[dict[str, object]]:
    """
    Description:
        Lists the fixed per-zone colour bands of the heat map for its legend (REQ-038, D6).

    Parameters:
        session: the request session.

    Return Values:
        list[dict[str, object]]: {"zone_code", "zone_label", "band_rank" (int),
        "band_label", "min_pct" (Decimal), "max_pct" (Decimal or None = open-ended)} per
        live band of a live zone, by zone sort order, then rank.

    Notes:
        Read only. The thresholds are never hard-coded in the app (D6).
    """
    return fetch_all(session, LIST_HEAT_MAP_BANDS_SQL)


# --- Shot chart (REQ-038) and shooting totals (REQ-036) ---

GET_SHOOTING_TOTALS_SQL = f"""
SELECT t.*
FROM h_analytics.fn_shooting_totals({FILTER_ARGS_SQL}) t
"""

# The zone label is joined for the court figure's spot metadata (the same shape as
# data_games.get_game_heat_map, so layouts_games.build_game_heat_figure draws it).
GET_HEAT_MAP_SPOTS_SQL = f"""
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
FROM h_analytics.fn_heat_map_spots({FILTER_ARGS_SQL}) h
LEFT JOIN h_ref.zones z ON z.zone_code = h.zone_code
ORDER BY h.spot_number
"""

GET_HEAT_MAP_ZONES_SQL = f"""
SELECT h.zone_code,
       h.zone_label,
       h.makes::int AS makes,
       h.attempts::int AS attempts,
       h.make_pct,
       h.band_rank::int AS band_rank,
       h.band_label
FROM h_analytics.fn_heat_map_zones({FILTER_ARGS_SQL}) h
JOIN h_ref.zones z ON z.zone_code = h.zone_code
ORDER BY z.sort_order
"""


def get_shooting_totals(session: Session, filters: FilterSet) -> dict[str, object]:
    """
    Description:
        The shooting totals and percentages after filter set F (REQ-036): the one row of
        h_analytics.fn_shooting_totals.

    Parameters:
        session: the request session.
        filters: the validated filter set; filters.player_id is the selected player, never
            a user id (COD-8-01).

    Return Values:
        dict[str, object]: {"fgm", "fga", "fg_pct", "three_pm", "three_pa", "three_pct",
        "ftm", "fta", "ft_pct", "efg_pct", "ts_pct", "points"}; percentages unrounded, None
        without attempts. {} if the function returned no row (it always returns one).

    Notes:
        Read only. Zone and shot type do not narrow free throws (function rule, D5).
    """
    return fetch_one(session, GET_SHOOTING_TOTALS_SQL, filter_params(filters)) or {}


def get_heat_map_spots(session: Session, filters: FilterSet) -> list[dict[str, object]]:
    """
    Description:
        Makes, attempts, make % and colour band per court spot after filter set F; spot
        shots only, manual entries excluded (REQ-038, REQ-012).

    Parameters:
        session: the request session.
        filters: the validated filter set (player_id = the selected player, COD-8-01).

    Return Values:
        list[dict[str, object]]: per spot {"court_spot_id", "spot_number", "spot_name",
        "zone_code", "zone_label", "x_ft", "y_ft" (floats), "makes", "attempts" (ints),
        "make_pct", "band_rank", "band_label"}, by spot_number.

    Notes:
        Read through h_analytics.fn_heat_map_spots: every live active spot plus any spot
        with data.
    """
    return fetch_all(session, GET_HEAT_MAP_SPOTS_SQL, filter_params(filters))


def get_heat_map_zones(session: Session, filters: FilterSet) -> list[dict[str, object]]:
    """
    Description:
        The heat map grouped by zone (the zone toggle of REQ-038), after filter set F.

    Parameters:
        session: the request session.
        filters: the validated filter set (player_id = the selected player, COD-8-01).

    Return Values:
        list[dict[str, object]]: per live zone {"zone_code", "zone_label", "makes",
        "attempts", "band_rank" (ints; band_rank None without a band), "make_pct",
        "band_label"}, by zone sort order.

    Notes:
        Read through h_analytics.fn_heat_map_zones; manual entries excluded.
    """
    return fetch_all(session, GET_HEAT_MAP_ZONES_SQL, filter_params(filters))


# --- Trends (REQ-037) ---

GET_SHOOTING_TREND_SQL = f"""
SELECT t.period_start,
       t.context,
       t.fgm::int AS fgm,
       t.fga::int AS fga,
       t.fg_pct,
       t.three_pm::int AS three_pm,
       t.three_pa::int AS three_pa,
       t.three_pct,
       t.ftm::int AS ftm,
       t.fta::int AS fta,
       t.ft_pct
FROM h_analytics.fn_shooting_trend(CAST(:period AS text),
       {FILTER_ARGS_SQL}) t
ORDER BY t.period_start, t.context
"""

GET_SHOT_POSITION_TREND_SQL = f"""
SELECT p.bucket_no,
       p.bucket_label,
       p.makes::int AS makes,
       p.attempts::int AS attempts,
       p.make_pct,
       p.events
FROM h_analytics.fn_shot_position_trend(CAST(:bucket_size AS integer),
       {FILTER_ARGS_SQL}) p
ORDER BY p.bucket_no
"""


def get_shooting_trend(
    session: Session, period: str, filters: FilterSet
) -> list[dict[str, object]]:
    """
    Description:
        FG%, 3P% and FT% per week or month, game and practice as separate rows, after filter
        set F (REQ-037).

    Parameters:
        session: the request session.
        period: "week" (ISO weeks from Monday) or "month".
        filters: the validated filter set (player_id = the selected player, COD-8-01).

    Return Values:
        list[dict[str, object]]: per (period_start, context) {"period_start" (date),
        "context", "fgm", "fga", "fg_pct", "three_pm", "three_pa", "three_pct", "ftm",
        "fta", "ft_pct"}, by period, then context.

    Notes:
        Raises ValueError for any other period (the controller never sends one).
    """
    if period not in TREND_PERIODS:
        raise ValueError("trend period must be week or month")
    params = {**filter_params(filters), "period": period}
    return fetch_all(session, GET_SHOOTING_TREND_SQL, params)


def get_shot_position_trend(
    session: Session, bucket_size: int, filters: FilterSet
) -> list[dict[str, object]]:
    """
    Description:
        The warm-up vs fatigue view (REQ-037): make % by shot position within a game or
        practice, single field-goal shots only, in buckets of bucket_size.

    Parameters:
        session: the request session.
        bucket_size: shots per bucket (config DASH_POSITION_BUCKET_SIZE).
        filters: the validated filter set (player_id = the selected player, COD-8-01).

    Return Values:
        list[dict[str, object]]: per bucket {"bucket_no", "bucket_label" ("1–10"),
        "makes", "attempts", "make_pct", "events"}, by bucket_no.

    Notes:
        Read through h_analytics.fn_shot_position_trend; positions are numbered before the
        filters apply.
    """
    params = {**filter_params(filters), "bucket_size": bucket_size}
    return fetch_all(session, GET_SHOT_POSITION_TREND_SQL, params)


# --- Games: averages, seasons, two games (REQ-039, REQ-065) ---

GET_GAME_AVERAGES_SQL = f"""
SELECT a.*
FROM h_analytics.fn_game_averages({FILTER_ARGS_SQL},
       CAST(:last_n AS integer),
       CAST(:skip_last AS integer)) a
"""

GET_SEASON_SUMMARIES_SQL = """
SELECT s.*
FROM h_analytics.fn_season_summaries(CAST(:player_id AS bigint)) s
ORDER BY (s.season_id IS NULL), s.start_date, s.season_id
"""

GET_BOX_SCORES_FOR_GAMES_SQL = """
SELECT b.*
FROM h_analytics.v_game_box_scores b
WHERE b.player_id = CAST(:player_id AS bigint)
  AND b.game_id = ANY(CAST(:game_ids AS bigint[]))
"""


def get_game_averages(
    session: Session, filters: FilterSet, last_n: int | None = None, skip_last: int = 0
) -> dict[str, object]:
    """
    Description:
        Per-game averages and totals of every box-score stat over the games selected by
        filter set F, optionally only the most recent last_n after skipping skip_last
        (REQ-039, REQ-065).

    Parameters:
        session: the request session.
        filters: the validated filter set (player_id = the selected player, COD-8-01).
        last_n: number of most recent games, or None for all.
        skip_last: most recent games skipped first (0 = none).

    Return Values:
        dict[str, object]: the one h_analytics.fn_game_averages row: "games_played", the
        total_ / avg_ columns, "fg_pct", "three_pct", "ft_pct", "efg_pct", "ts_pct",
        "ast_to"; {} if no row came back (the function always returns one).

    Notes:
        Read only. Context "practice" selects no games (function rule). "Last N vs previous
        N" is two calls: (N, 0) and (N, N).
    """
    params = {**filter_params(filters), "last_n": last_n, "skip_last": skip_last}
    return fetch_one(session, GET_GAME_AVERAGES_SQL, params) or {}


def get_season_summaries(session: Session, player_id: int) -> list[dict[str, object]]:
    """
    Description:
        Season vs season (REQ-039): per live season of the player (plus "No season" when it
        has games) the games played, totals, averages and percentages.

    Parameters:
        session: the request session.
        player_id: the selected player (FilterSet.player_id), never a user id (COD-8-01).

    Return Values:
        list[dict[str, object]]: h_analytics.fn_season_summaries rows ("season_id" None for
        "No season", "season_name", "start_date", "end_date", then the fn_game_averages
        columns), by start date with "No season" last.

    Notes:
        Unfiltered by design (D7).
    """
    return fetch_all(session, GET_SEASON_SUMMARIES_SQL, {"player_id": player_id})


def get_box_scores_for_games(
    session: Session, player_id: int, game_ids: list[int]
) -> dict[int, dict[str, object]]:
    """
    Description:
        The box scores of chosen games of one player, in one statement (REQ-065, two
        games).

    Parameters:
        session: the request session.
        player_id: the filter player (FilterSet.player_id), never a user id (COD-8-01); a
            game of another player is not returned.
        game_ids: h_data.games.game_id values from the compare pickers.

    Return Values:
        dict[int, dict[str, object]]: game_id -> the h_analytics.v_game_box_scores row; a
        game that is not this player's or not live is absent.

    Notes:
        Read only. No query in a loop (COD-16-01).
    """
    rows = fetch_all(
        session,
        GET_BOX_SCORES_FOR_GAMES_SQL,
        {"player_id": player_id, "game_ids": list(game_ids)},
    )
    return {int(cast(int, row["game_id"])): row for row in rows}


# --- Bests (REQ-040) ---

# WITH ORDINALITY keeps the function's stat order (points, rebounds, assists, steals, blocks).
GET_CAREER_HIGHS_SQL = """
SELECT c.stat_code, c.value, c.game_id, c.game_date
FROM h_analytics.fn_career_highs(CAST(:player_id AS bigint))
     WITH ORDINALITY AS c(stat_code, value, game_id, game_date, ord)
ORDER BY c.ord
"""

GET_LONGEST_STREAKS_SQL = f"""
SELECT s.streak_length,
       s.context,
       s.game_id,
       s.practice_session_id,
       s.event_date,
       s.first_shot_entry_id,
       s.last_shot_entry_id
FROM h_analytics.fn_longest_make_streak({FILTER_ARGS_SQL},
       CAST(:limit AS integer)) s
ORDER BY s.streak_length DESC, s.event_date, s.first_shot_entry_id
"""

GET_DRILL_PERSONAL_BESTS_SQL = """
SELECT b.drill_id,
       b.drill_name,
       b.result_type_code,
       b.metric,
       b.metric_label,
       b.unit,
       b.higher_is_better,
       b.best_value,
       b.best_makes,
       b.best_attempts,
       b.achieved_on
FROM h_analytics.fn_drill_personal_bests(CAST(:player_id AS bigint), NULL) b
ORDER BY b.drill_name, b.drill_id, b.metric
"""

LIST_ACHIEVED_GOALS_SQL = """
SELECT g.goal_id, g.goal_name, g.goal_type, g.achieved_on
FROM h_data.goals g
JOIN h_data.players p ON p.player_id = g.player_id
WHERE g.player_id = CAST(:player_id AS bigint)
  AND g.deleted_at IS NULL
  AND p.deleted_at IS NULL
  AND g.status = 'achieved'
ORDER BY g.achieved_on DESC, g.goal_id DESC
"""


def get_career_highs(session: Session, player_id: int) -> list[dict[str, object]]:
    """
    Description:
        Single-game career highs in points, rebounds, assists, steals and blocks, with the
        earliest game reaching each (REQ-040, D13).

    Parameters:
        session: the request session.
        player_id: the selected player (FilterSet.player_id), never a user id (COD-8-01).

    Return Values:
        list[dict[str, object]]: {"stat_code", "value", "game_id", "game_date"} per stat, in
        the function's order; empty without live games.

    Notes:
        Career values: the filters do not apply (D5).
    """
    return fetch_all(session, GET_CAREER_HIGHS_SQL, {"player_id": player_id})


def get_longest_streaks(
    session: Session, filters: FilterSet, limit: int
) -> list[dict[str, object]]:
    """
    Description:
        The longest runs of consecutive made single field goals within one game or practice,
        after filter set F (REQ-040).

    Parameters:
        session: the request session.
        filters: the validated filter set (player_id = the selected player, COD-8-01).
        limit: how many streaks (config DASH_STREAK_LIMIT).

    Return Values:
        list[dict[str, object]]: {"streak_length", "context", "game_id",
        "practice_session_id", "event_date", "first_shot_entry_id", "last_shot_entry_id"},
        longest first.

    Notes:
        Read through h_analytics.fn_longest_make_streak; any miss ends a streak.
    """
    params = {**filter_params(filters), "limit": limit}
    return fetch_all(session, GET_LONGEST_STREAKS_SQL, params)


def get_drill_personal_bests(session: Session, player_id: int) -> list[dict[str, object]]:
    """
    Description:
        The player's personal bests of every drill lineage (REQ-040, REQ-060).

    Parameters:
        session: the request session.
        player_id: the selected player (FilterSet.player_id), never a user id (COD-8-01).

    Return Values:
        list[dict[str, object]]: {"drill_id", "drill_name", "result_type_code", "metric",
        "metric_label", "unit", "higher_is_better", "best_value", "best_makes",
        "best_attempts", "achieved_on"} per drill and metric, by drill name, then metric.

    Notes:
        h_analytics.fn_drill_personal_bests(player, NULL); drill_id is the lineage id.
    """
    return fetch_all(session, GET_DRILL_PERSONAL_BESTS_SQL, {"player_id": player_id})


def list_achieved_goals(session: Session, player_id: int) -> list[dict[str, object]]:
    """
    Description:
        The player's achieved goals with their dates (REQ-040).

    Parameters:
        session: the request session.
        player_id: the selected player (FilterSet.player_id), never a user id (COD-8-01).

    Return Values:
        list[dict[str, object]]: {"goal_id", "goal_name", "goal_type", "achieved_on"} per
        live achieved goal of a live profile, newest achievement first.

    Notes:
        Read only. Empty until goal statuses are maintained (plan §8 gap 1).
    """
    return fetch_all(session, LIST_ACHIEVED_GOALS_SQL, {"player_id": player_id})
