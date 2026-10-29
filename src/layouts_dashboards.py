"""Dashboards UI for Hoopster: the page (player picker, filter bar, four view tabs), the trend,
warm-up and zone heat-map figures, the stats tables, the zone list, the heat-map legend and the
bests lists.

REQ-036 to REQ-040, REQ-045, REQ-064, REQ-065 (task 2026-10-23_dashboards). Built only through
the wrappers of src/layouts.py (COD-5-01); the spot heat map is
layouts_games.build_game_heat_figure and the court is layouts_entry.build_court_figure, never
copied (D6). Pure builders: no data_* or controller imports. Values arrive already formatted by
controller_dashboards; dates are formatted here with layouts_games.format_game_date.
"""

import logging
from decimal import Decimal, InvalidOperation

import dash_mantine_components as dmc
import plotly.graph_objects as go
from dash import dcc, html
from dash.development.base_component import Component

from config.app_config import (
    DASH_CHART_HEIGHT_PX,
    DASH_COMPARE_N_DEFAULT,
    DASH_COMPARE_N_MAX,
    DASH_GAME_LINE_COLOR,
    DASH_PRACTICE_LINE_COLOR,
    HEAT_MAP_BAND_COLORS,
    HEAT_MAP_NO_ATTEMPTS_COLOR,
    HEAT_MAP_OUTLINE_COLOR,
)
from src.layouts import (
    CONTROL_SIZE,
    form_message,
    nav_link,
    page_skeleton,
    player_options,
    secondary_button,
    stat_label,
)
from src.layouts_entry import COURT_CLASS, COURT_VIEW_HALF, build_court_figure
from src.layouts_games import (
    CHART_CAPTION,
    DATE_PICKER_FORMAT,
    GAMES_PATH,
    HEAT_MARKER_SIZE_PX,
    HEAT_NEUTRAL_RANK,
    HIDDEN_STYLE,
    IN_PROGRESS_TEXT,
    RESULT_LETTERS,
    SEGMENTED_CLASS,
    SHOWN_STYLE,
    build_game_heat_figure,
    format_game_date,
    score_text,
    stat_definition,
    stat_tiles_grid,
)
from src.layouts_run import format_personal_best

logger = logging.getLogger(__name__)

# The "no filter" value of every filter picker (stored as NULL).
ALL_VALUE = "all"

# The four views (dsh-view tab values, D1).
VIEW_CHART = "chart"
VIEW_TRENDS = "trends"
VIEW_GAMES = "games"
VIEW_BESTS = "bests"

# Filter bar: game vs practice (h_analytics context values).
CONTEXT_GAME = "game"
CONTEXT_PRACTICE = "practice"

# Shot chart: spots or zone totals (REQ-038).
CHART_MODE_SPOTS = "spots"
CHART_MODE_ZONES = "zones"

# Trends (REQ-037, D8): period and stat toggles. Each stat code picks (stat_definitions code,
# pct column, makes column, attempts column) of fn_shooting_trend.
TREND_PERIOD_WEEK = "week"
TREND_PERIOD_MONTH = "month"
TREND_STAT_COLUMNS: dict[str, tuple[str, str, str, str]] = {
    "fg": ("fg_pct", "fg_pct", "fgm", "fga"),
    "three": ("three_pct", "three_pct", "three_pm", "three_pa"),
    "ft": ("ft_pct", "ft_pct", "ftm", "fta"),
}
TREND_STAT_DEFAULT = "fg"
# Trace name and colour per context, in legend order.
TREND_LINES: tuple[tuple[str, str, str], ...] = (
    (CONTEXT_GAME, "Games", DASH_GAME_LINE_COLOR),
    (CONTEXT_PRACTICE, "Practice", DASH_PRACTICE_LINE_COLOR),
)

# Games view (REQ-039, D10): per game or totals.
MEASURE_AVG = "avg"
MEASURE_TOTAL = "total"

# Compare modes (REQ-065, D9).
COMPARE_LAST = "last"
COMPARE_PERIODS = "periods"
COMPARE_GAMES = "games"
COMPARE_MODES: tuple[str, ...] = (COMPARE_LAST, COMPARE_PERIODS, COMPARE_GAMES)

# Row key of the games-played row in the stats tables (plain text, not a stat).
GAMES_ROW = "games"
GAMES_ROW_LABEL = "Games"

PRACTICE_PATH = "/practice"
DASH_TABLE_CLASS = "hoopster-dash-table"
DASH_GRAPH_CLASS = "hoopster-dash-graph"
SWATCH_CLASS = "hoopster-swatch"
CHART_MARGIN_PX: dict[str, int] = {"l": 40, "r": 10, "t": 10, "b": 30}
PERCENT_AXIS_RANGE: list[int] = [0, 100]
LEGEND_BELOW_Y = -0.25
EMPTY_TEXT_SIZE_PX = 14

MESSAGE_GAMES_EMPTY = "No games for these filters."
MESSAGE_NO_SEASONS = "No seasons or games yet."
MESSAGE_NO_HIGHS = "No games yet."
MESSAGE_NO_STREAKS = "No single-shot streaks for these filters."
MESSAGE_NO_DRILL_BESTS = "No drill personal bests yet."
MESSAGE_NO_GOALS = "No goals achieved yet."
LEGEND_NOTE = "Colours use fixed ranges per zone, so a colour means the same over time."
FILTER_HINT = "Category and format keep games only."
SEASON_TABLE_CAPTION = "Every game of each season; the filters do not apply here."
FATIGUE_CAPTION = "Single shots only, counted within each game or practice."
BESTS_CAPTION = "Only the streaks follow the filters; the rest are career bests."

GRAPH_CONFIG: dict[str, bool] = {"displayModeBar": False, "responsive": True}
# A responsive graph sizes itself to its container (AP-4): without a container height the
# Trends charts, first drawn inside a hidden tab panel, were laid out 0 px tall.
DASH_GRAPH_STYLE: dict[str, str] = {"height": f"{DASH_CHART_HEIGHT_PX}px", "width": "100%"}
COURT_GRAPH_CONFIG: dict[str, bool] = {
    "displayModeBar": False,
    "staticPlot": True,
    "responsive": True,
}


# --- Picker options and small texts ---

def season_options(seasons: list[dict]) -> list[dict[str, str]]:
    """
    Description:
        The season filter's options: "All seasons", then the player's live seasons.

    Parameters:
        seasons: rows of data_seasons.list_seasons.

    Return Values:
        list[dict[str, str]]: {"value": "all", "label": "All seasons"}, then {"value":
        str(season_id), "label": season_name} per season.

    Notes:
        Pure.
    """
    return [{"value": ALL_VALUE, "label": "All seasons"}] + [
        {"value": str(int(row["season_id"])), "label": str(row["season_name"])}
        for row in seasons
    ]


def code_options(rows: list[dict], code_key: str) -> list[dict[str, str]]:
    """
    Description:
        A code filter's options: "All", then one option per reference row.

    Parameters:
        rows: reference rows with a code column and "label" (categories, formats and zones
            use "code"; shot types use "shot_type_code").
        code_key: the name of the code column.

    Return Values:
        list[dict[str, str]]: {"value": "all", "label": "All"}, then {"value": code,
        "label": label} per row, in order.

    Notes:
        Pure.
    """
    return [{"value": ALL_VALUE, "label": "All"}] + [
        {"value": str(row[code_key]), "label": str(row["label"])} for row in rows
    ]


def game_option_label(row: dict) -> str:
    """
    Description:
        A game's text in the compare pickers and table header: "Sun 18 Oct 2026 · vs Eagles
        · W 54–40"; the opponent part is omitted when absent; "In progress" when the game
        has no final result.

    Parameters:
        row: a data_games.list_games or v_game_box_scores row ("game_date",
            "opponent_name", "team_score", "opponent_score", "result", "entry_status").

    Return Values:
        str: the label.

    Notes:
        Pure. The result rule matches layouts_games.games_grid_rows.
    """
    parts: list[str] = []
    if row.get("game_date") is not None:
        parts.append(format_game_date(row.get("game_date")))
    if row.get("opponent_name"):
        parts.append(f"vs {row['opponent_name']}")
    result = row.get("result")
    if row.get("entry_status") != "completed" or result not in RESULT_LETTERS:
        parts.append(IN_PROGRESS_TEXT)
    else:
        score = score_text(row.get("team_score"), row.get("opponent_score"))
        parts.append(f"{RESULT_LETTERS[str(result)]} {score}")
    return " · ".join(parts)


def game_options(rows: list[dict]) -> list[dict[str, str]]:
    """
    Description:
        The compare game pickers' options.

    Parameters:
        rows: data_games.list_games of the player, newest first.

    Return Values:
        list[dict[str, str]]: {"value": str(game_id), "label": game_option_label(row)} per
        game, in the rows' order.

    Notes:
        Pure.
    """
    return [
        {"value": str(int(row["game_id"])), "label": game_option_label(row)} for row in rows
    ]


def _band_number(value: object) -> str:
    """
    Description:
        A band threshold without trailing zeros: 20.00 -> "20", 28.50 -> "28.5".

    Parameters:
        value: the numeric(5,2) threshold (Decimal, int or float).

    Return Values:
        str: the text, or the value's own text when it is not a number.

    Notes:
        Pure.
    """
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    if not number.is_finite():
        return str(value)
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _band_range_text(band: dict) -> str:
    """
    Description:
        One band of the legend: "Cold < 20", "Cool 20–28" or "Hot ≥ 40".

    Parameters:
        band: a data_dashboards.list_heat_map_bands row.

    Return Values:
        str: the text.

    Notes:
        Pure. The lowest band (min 0) shows "< max"; an open-ended band "≥ min".
    """
    label = str(band.get("band_label"))
    min_pct = band.get("min_pct")
    max_pct = band.get("max_pct")
    if max_pct is None:
        return f"{label} ≥ {_band_number(min_pct)}"
    if Decimal(str(min_pct)) <= 0:
        return f"{label} < {_band_number(max_pct)}"
    return f"{label} {_band_number(min_pct)}–{_band_number(max_pct)}"


def legend_lines(bands: list[dict]) -> list[tuple[str, str]]:
    """
    Description:
        The legend's per-zone lines (REQ-038, D6): "Three-point: Cold < 20 · Cool 20–28 ·
        Average 28–34 · Warm 34–40 · Hot ≥ 40".

    Parameters:
        bands: data_dashboards.list_heat_map_bands, by zone order, then rank.

    Return Values:
        list[tuple[str, str]]: (zone_code, line) per zone, in the bands' zone order.

    Notes:
        Pure. Thresholds come only from h_ref.heat_map_bands (never hard-coded).
    """
    zones: dict[str, tuple[str, list[str]]] = {}
    for band in bands:
        code = str(band.get("zone_code"))
        if code not in zones:
            zones[code] = (str(band.get("zone_label")), [])
        zones[code][1].append(_band_range_text(band))
    return [(code, f"{label}: {' · '.join(parts)}") for code, (label, parts) in zones.items()]


def band_swatches(bands: list[dict]) -> list[tuple[int, str]]:
    """
    Description:
        The legend's colour keys: one (rank, band label) per band rank, by rank.

    Parameters:
        bands: data_dashboards.list_heat_map_bands.

    Return Values:
        list[tuple[int, str]]: (band_rank, band_label), the first label seen for each rank.

    Notes:
        Pure.
    """
    labels: dict[int, str] = {}
    for band in bands:
        rank = int(band.get("band_rank") or 0)
        labels.setdefault(rank, str(band.get("band_label")))
    return sorted(labels.items())


# --- Figures (REQ-037, REQ-038) ---

def _base_layout(figure: go.Figure) -> None:
    """
    Description:
        The shared sizing of the trend and warm-up charts: fixed height for a phone, small
        margins, legend below, no zoom or drag.

    Parameters:
        figure: the figure to update in place.

    Return Values:
        None.

    Notes:
        Both axes are fixedrange so a swipe scrolls the page (REQ-045).
    """
    figure.update_layout(
        height=DASH_CHART_HEIGHT_PX,
        margin=CHART_MARGIN_PX,
        dragmode=False,
        legend={"orientation": "h", "y": LEGEND_BELOW_Y},
        xaxis={"fixedrange": True},
        yaxis={"fixedrange": True, "range": PERCENT_AXIS_RANGE, "ticksuffix": "%"},
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
    )


def empty_figure(message: str) -> go.Figure:
    """
    Description:
        A chart with no data: hidden axes and the message centred.

    Parameters:
        message: the text, e.g. "No shots for these filters.".

    Return Values:
        go.Figure: the figure.

    Notes:
        Pure.
    """
    figure = go.Figure()
    figure.update_layout(
        height=DASH_CHART_HEIGHT_PX,
        margin=CHART_MARGIN_PX,
        dragmode=False,
        xaxis={"visible": False, "fixedrange": True},
        yaxis={"visible": False, "fixedrange": True},
        annotations=[
            {
                "text": message,
                "xref": "paper",
                "yref": "paper",
                "x": 0.5,
                "y": 0.5,
                "showarrow": False,
                "font": {"size": EMPTY_TEXT_SIZE_PX},
            }
        ],
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
    )
    return figure


def _pct_value(value: object) -> float | None:
    """
    Description:
        A percentage for a chart axis.

    Parameters:
        value: the unrounded percentage (Decimal) or None.

    Return Values:
        float | None: the value, or None (no attempts: a gap in the line).

    Notes:
        Pure.
    """
    if value is None:
        return None
    try:
        return float(str(value))
    except ValueError:
        return None


def build_trend_figure(rows: list[dict], stat: str) -> go.Figure:
    """
    Description:
        Shooting trend (REQ-037): one line with markers per context present (Games orange,
        Practice blue), x = period start, y = the chosen percentage.

    Parameters:
        rows: data_dashboards.get_shooting_trend.
        stat: "fg", "three" or "ft" (any other value is "fg").

    Return Values:
        go.Figure: the figure, y axis 0–100 %; empty_figure("No shots for these filters.")
        when there are no rows.

    Notes:
        A period without attempts of that stat is None, bridged by connectgaps. Hover
        "{trace}: {pct} ({makes}/{attempts})".
    """
    if not rows:
        return empty_figure("No shots for these filters.")
    _, pct_key, makes_key, attempts_key = TREND_STAT_COLUMNS.get(
        stat, TREND_STAT_COLUMNS[TREND_STAT_DEFAULT]
    )
    figure = go.Figure()
    for context, name, color in TREND_LINES:
        context_rows = [row for row in rows if row.get("context") == context]
        if not context_rows:
            continue
        figure.add_trace(
            go.Scatter(
                x=[row.get("period_start") for row in context_rows],
                y=[_pct_value(row.get(pct_key)) for row in context_rows],
                customdata=[
                    [int(row.get(makes_key) or 0), int(row.get(attempts_key) or 0)]
                    for row in context_rows
                ],
                mode="lines+markers",
                name=name,
                line={"color": color},
                marker={"color": color},
                connectgaps=True,
                hovertemplate=(
                    f"{name}: %{{y:.1f}}% "
                    "(%{customdata[0]}/%{customdata[1]})<extra></extra>"
                ),
            )
        )
    _base_layout(figure)
    return figure


def build_position_figure(rows: list[dict]) -> go.Figure:
    """
    Description:
        Warm-up vs fatigue (REQ-037): make % per bucket of shot positions within a game or
        practice, as bars labelled "makes/attempts".

    Parameters:
        rows: data_dashboards.get_shot_position_trend.

    Return Values:
        go.Figure: the figure; empty_figure("No single shots for these filters.") when there
        are no rows.

    Notes:
        Pure.
    """
    if not rows:
        return empty_figure("No single shots for these filters.")
    figure = go.Figure(
        go.Bar(
            x=[str(row.get("bucket_label")) for row in rows],
            y=[_pct_value(row.get("make_pct")) for row in rows],
            text=[
                f"{int(row.get('makes') or 0)}/{int(row.get('attempts') or 0)}" for row in rows
            ],
            textposition="auto",
            marker={"color": DASH_GAME_LINE_COLOR},
            hovertemplate="%{x}: %{y:.1f}% (%{text})<extra></extra>",
            name="Make %",
        )
    )
    _base_layout(figure)
    figure.update_layout(showlegend=False, xaxis={"type": "category"})
    return figure


def build_zone_heat_figure(spot_rows: list[dict], zone_rows: list[dict]) -> go.Figure:
    """
    Description:
        The zone view of the shot chart (REQ-038): every spot coloured by its zone's band
        (HEAT_MAP_BAND_COLORS), white when the zone has no attempts; no marker text.

    Parameters:
        spot_rows: data_dashboards.get_heat_map_spots (court positions).
        zone_rows: data_dashboards.get_heat_map_zones (the zone totals and bands).

    Return Values:
        go.Figure: the figure on layouts_entry.build_court_figure; static (no hover, no
        click).

    Notes:
        A zone with attempts but no matching band is drawn in the neutral band colour, as
        the spot view does.
    """
    figure = build_court_figure(spot_rows, COURT_VIEW_HALF)
    zones = {str(row.get("zone_code")): row for row in zone_rows}
    colors: list[str] = []
    for spot in spot_rows:
        zone = zones.get(str(spot.get("zone_code")))
        if zone is None or int(zone.get("attempts") or 0) <= 0:
            colors.append(HEAT_MAP_NO_ATTEMPTS_COLOR)
            continue
        rank = zone.get("band_rank")
        band = (
            int(rank)
            if isinstance(rank, int) and rank in HEAT_MAP_BAND_COLORS
            else HEAT_NEUTRAL_RANK
        )
        colors.append(HEAT_MAP_BAND_COLORS[band])
    figure.update_traces(
        text=[""] * len(spot_rows),
        marker={
            "size": HEAT_MARKER_SIZE_PX,
            "color": colors,
            "line": {"color": HEAT_MAP_OUTLINE_COLOR, "width": 2},
        },
        hoverinfo="skip",
    )
    figure.update_layout(hovermode=False, clickmode="none", uirevision="dsh-zone-chart")
    return figure


# --- Lists, legend, tables (REQ-038, REQ-039, REQ-064, REQ-065) ---

def _swatch(color: str) -> Component:
    """
    Description:
        A small colour square (16 px, class hoopster-swatch).

    Parameters:
        color: the CSS colour.

    Return Values:
        Component: the span.

    Notes:
        Plain markup: the component library has no bare colour square (COD-5-02).
    """
    return html.Span(className=SWATCH_CLASS, style={"backgroundColor": color})


def zone_list(lines: list[tuple[str, str, int | None]]) -> list[Component]:
    """
    Description:
        The zone totals under the zone chart: per zone a swatch in its band colour and
        "{zone label} {made/attempted · % · band}".

    Parameters:
        lines: controller_dashboards.zone_lines: (zone label, text, band rank or None).

    Return Values:
        list[Component]: one dmc.Group per zone, ids "dsh-zone-line-{index}" (from 0).

    Notes:
        A zone without a band (no attempts) has a white swatch.
    """
    items: list[Component] = []
    for index, (label, text, rank) in enumerate(lines):
        color = HEAT_MAP_NO_ATTEMPTS_COLOR
        if rank is not None:
            color = HEAT_MAP_BAND_COLORS.get(rank, HEAT_MAP_NO_ATTEMPTS_COLOR)
        items.append(
            dmc.Group(
                [_swatch(color), dmc.Text(f"{label} {text}", size="sm")],
                gap="xs",
                wrap="nowrap",
                id=f"dsh-zone-line-{index}",
            )
        )
    return items


def heat_legend(bands: list[dict]) -> Component:
    """
    Description:
        The heat-map legend (REQ-038, D6): the band colours with their labels, one line of
        fixed ranges per zone, and the note that colours mean the same over time.

    Parameters:
        bands: data_dashboards.list_heat_map_bands.

    Return Values:
        Component: div "dsh-heat-legend" with "dsh-legend-bands" and one
        "dsh-legend-zone-{zone_code}" line per zone.

    Notes:
        Built once with the page; the bands are reference data.
    """
    keys = [
        dmc.Group(
            [
                _swatch(HEAT_MAP_BAND_COLORS.get(rank, HEAT_MAP_NO_ATTEMPTS_COLOR)),
                dmc.Text(label, size="sm"),
            ],
            gap=4,
            wrap="nowrap",
        )
        for rank, label in band_swatches(bands)
    ]
    lines = [
        dmc.Text(line, size="sm", id=f"dsh-legend-zone-{code}")
        for code, line in legend_lines(bands)
    ]
    return html.Div(
        dmc.Stack(
            [
                dmc.Group(keys, gap="sm", id="dsh-legend-bands"),
                *lines,
                dmc.Text(LEGEND_NOTE, size="sm", c="dimmed"),
            ],
            gap=4,
        ),
        id="dsh-heat-legend",
    )


def stats_table(
    element_id: str,
    headers: list[str],
    rows: list[tuple[str, list[str]]],
    definitions: dict[str, dict],
    scope: str,
) -> Component:
    """
    Description:
        A stats × columns table (season vs season, compare): the first column is the stat's
        tappable abbreviation (stat_label, REQ-064), "Games" for the games-played row.

    Parameters:
        element_id: the dmc.Table's id ("dsh-season-stats", "dsh-cmp-stats").
        headers: the column headers after the stat column (season names, "Last 5", ...).
        rows: (stat code or "games", formatted cells in header order).
        definitions: data_games.list_stat_definitions.
        scope: the stat_label scope ("dsh-season", "dsh-cmp").

    Return Values:
        Component: a striped dmc.Table inside a div of class hoopster-dash-table (the table
        scrolls inside itself at 380 px, never the page).

    Notes:
        Pure.
    """
    head = dmc.TableThead(
        dmc.TableTr([dmc.TableTh("")] + [dmc.TableTh(header) for header in headers])
    )
    body_rows = []
    for code, cells in rows:
        first = (
            dmc.Text(GAMES_ROW_LABEL, fw=600, size="sm")
            if code == GAMES_ROW
            else stat_label(stat_definition(definitions, code), scope)
        )
        body_rows.append(
            dmc.TableTr([dmc.TableTd(first)] + [dmc.TableTd(cell) for cell in cells])
        )
    return html.Div(
        dmc.Table([head, dmc.TableTbody(body_rows)], striped=True, id=element_id),
        className=DASH_TABLE_CLASS,
    )


def season_table_block(
    headers: list[str], rows: list[tuple[str, list[str]]], definitions: dict[str, dict]
) -> Component:
    """
    Description:
        The season vs season table (REQ-039), or "No seasons or games yet." when the player
        has neither.

    Parameters:
        headers: the season names (controller_dashboards.season_table).
        rows: the table rows.
        definitions: data_games.list_stat_definitions.

    Return Values:
        Component: stats_table "dsh-season-stats" (scope "dsh-season"), or the dimmed text
        "dsh-season-empty".

    Notes:
        Pure.
    """
    if not headers:
        return dimmed_text("dsh-season-empty", MESSAGE_NO_SEASONS)
    return stats_table("dsh-season-stats", headers, rows, definitions, "dsh-season")


def dimmed_text(element_id: str, text: str) -> Component:
    """
    Description:
        A dimmed line for prompts and empty states inside a view.

    Parameters:
        element_id: the stable element id.
        text: the text.

    Return Values:
        Component: the dmc.Text.

    Notes:
        Pure.
    """
    return dmc.Text(text, id=element_id, c="dimmed", size="sm")


def chart_tiles_block(tiles: list[tuple[str, str]], definitions: dict[str, dict]) -> Component:
    """
    Description:
        The Shot chart view's filtered shooting tiles (FG, 3P, FT, eFG%, TS%, PTS; REQ-064).

    Parameters:
        tiles: (stat_code, formatted value) pairs from controller_dashboards.chart_tiles.
        definitions: data_games.list_stat_definitions.

    Return Values:
        Component: layouts_games.stat_tiles_grid "dsh-chart-grid", scope "dsh-chart".

    Notes:
        Pure.
    """
    return stat_tiles_grid(tiles, definitions, "dsh-chart", "dsh-chart-grid")


def games_average_block(
    game_count: int, measure: str, tiles: list[tuple[str, str]], definitions: dict[str, dict]
) -> list[Component]:
    """
    Description:
        The Games view's averages or totals (REQ-039): heading "Per game ({n} games)" or
        "Totals ({n} games)" over the tiles; "No games for these filters." with no games.

    Parameters:
        game_count: fn_game_averages.games_played.
        measure: "avg" or "total".
        tiles: (stat_code, formatted value) pairs from controller_dashboards.games_tiles.
        definitions: data_games.list_stat_definitions.

    Return Values:
        list[Component]: the children of dsh-games-avg: "dsh-games-heading" and the tiles
        grid "dsh-games-grid" (scope "dsh-games"), or the dimmed "dsh-games-empty".

    Notes:
        Pure. One game reads "(1 game)".
    """
    if game_count <= 0:
        return [dimmed_text("dsh-games-empty", MESSAGE_GAMES_EMPTY)]
    noun = "game" if game_count == 1 else "games"
    title = "Per game" if measure == MEASURE_AVG else "Totals"
    return [
        dmc.Title(f"{title} ({game_count} {noun})", order=4, id="dsh-games-heading"),
        stat_tiles_grid(tiles, definitions, "dsh-games", "dsh-games-grid"),
    ]


# --- Bests (REQ-040, D13) ---

def _link_line(element_id: str, text: str, href: str) -> Component:
    """
    Description:
        A touch-sized (44 px) link line.

    Parameters:
        element_id: the link's id.
        text: the link text.
        href: the in-app path.

    Return Values:
        Component: nav_link inside a hoopster-link-button div.

    Notes:
        Pure.
    """
    return html.Div(nav_link(element_id, text, href), className="hoopster-link-button")


def _highs_section(highs: list[dict], definitions: dict[str, dict]) -> list[Component]:
    """
    Description:
        Career highs: "{label} {value} · {date}" linking to the game's summary.

    Parameters:
        highs: data_dashboards.get_career_highs.
        definitions: data_games.list_stat_definitions (labels).

    Return Values:
        list[Component]: one link "dsh-high-{stat_code}" per stat, or "No games yet.".

    Notes:
        Pure.
    """
    if not highs:
        return [dimmed_text("dsh-highs-empty", MESSAGE_NO_HIGHS)]
    items: list[Component] = []
    for row in highs:
        code = str(row.get("stat_code"))
        label = str(stat_definition(definitions, code).get("label", code))
        text = f"{label} {row.get('value')} · {format_game_date(row.get('game_date'))}"
        items.append(
            _link_line(f"dsh-high-{code}", text, f"{GAMES_PATH}/{int(row['game_id'])}/summary")
        )
    return items


def _streaks_section(streaks: list[dict]) -> list[Component]:
    """
    Description:
        Longest made-shot streaks: "{n} in a row · Game|Practice · {date}" linking to the
        game summary or the practice.

    Parameters:
        streaks: data_dashboards.get_longest_streaks, longest first.

    Return Values:
        list[Component]: links "dsh-streak-{i}" (i from 1), or the empty text.

    Notes:
        Pure.
    """
    if not streaks:
        return [dimmed_text("dsh-streaks-empty", MESSAGE_NO_STREAKS)]
    items: list[Component] = []
    for index, row in enumerate(streaks, start=1):
        is_game = row.get("context") == CONTEXT_GAME and row.get("game_id") is not None
        where = "Game" if is_game else "Practice"
        href = (
            f"{GAMES_PATH}/{int(row['game_id'])}/summary"
            if is_game
            else f"{PRACTICE_PATH}/{int(row['practice_session_id'])}"
        )
        text = (
            f"{int(row.get('streak_length') or 0)} in a row · {where} · "
            f"{format_game_date(row.get('event_date'))}"
        )
        items.append(_link_line(f"dsh-streak-{index}", text, href))
    return items


def _drills_section(drill_groups: list[tuple[str, list[dict]]]) -> list[Component]:
    """
    Description:
        Drill personal bests: per drill its name, then the run-mode PB lines (REQ-060).

    Parameters:
        drill_groups: (drill name, its fn_drill_personal_bests rows) per drill lineage.

    Return Values:
        list[Component]: the drills, or "No drill personal bests yet.".

    Notes:
        Pure. Same text as run mode (layouts_run.format_personal_best).
    """
    if not drill_groups:
        return [dimmed_text("dsh-drills-empty", MESSAGE_NO_DRILL_BESTS)]
    items: list[Component] = []
    for name, rows in drill_groups:
        items.append(
            dmc.Stack(
                [
                    dmc.Text(name, fw=600),
                    dmc.Text(
                        format_personal_best(rows), size="sm", style={"whiteSpace": "pre-line"}
                    ),
                ],
                gap=0,
            )
        )
    return items


def _goals_section(goals: list[dict]) -> list[Component]:
    """
    Description:
        Goals achieved: "{goal_name} · achieved {date}".

    Parameters:
        goals: data_dashboards.list_achieved_goals.

    Return Values:
        list[Component]: one line per goal, or "No goals achieved yet.".

    Notes:
        Pure.
    """
    if not goals:
        return [dimmed_text("dsh-goals-empty", MESSAGE_NO_GOALS)]
    return [
        dmc.Text(
            f"{row.get('goal_name')} · achieved {format_game_date(row.get('achieved_on'))}"
        )
        for row in goals
    ]


def bests_content(
    highs: list[dict],
    streaks: list[dict],
    drill_groups: list[tuple[str, list[dict]]],
    goals: list[dict],
    definitions: dict[str, dict],
) -> list[Component]:
    """
    Description:
        The Bests view (REQ-040): Career highs, Longest made-shot streaks, Drill personal
        bests and Goals achieved, each titled, with dates.

    Parameters:
        highs: data_dashboards.get_career_highs.
        streaks: data_dashboards.get_longest_streaks.
        drill_groups: controller_dashboards.group_drill_bests.
        goals: data_dashboards.list_achieved_goals.
        definitions: data_games.list_stat_definitions.

    Return Values:
        list[Component]: the children of dsh-bests-content (sections "dsh-bests-highs",
        "dsh-bests-streaks", "dsh-bests-drills", "dsh-bests-goals").

    Notes:
        Pure. Links come from the function rows only, never from the client.
    """
    sections = (
        ("dsh-bests-highs", "Career highs", _highs_section(highs, definitions)),
        ("dsh-bests-streaks", "Longest made-shot streaks", _streaks_section(streaks)),
        ("dsh-bests-drills", "Drill personal bests", _drills_section(drill_groups)),
        ("dsh-bests-goals", "Goals achieved", _goals_section(goals)),
    )
    return [
        html.Div(dmc.Stack([dmc.Title(title, order=4), *items], gap="xs"), id=element_id)
        for element_id, title, items in sections
    ]


# --- Page (REQ-036, REQ-045, REQ-066; plan §3.1) ---

def _segmented(element_id: str, label: str, data: list[dict[str, str]], value: str) -> Component:
    """
    Description:
        A labelled, full-width, touch-sized segmented control.

    Parameters:
        element_id: the control's id.
        label: the label above it ("" for none).
        data: the segments ({"value", "label"}).
        value: the selected value.

    Return Values:
        Component: a dmc.Stack of the label and the control (class hoopster-segmented).

    Notes:
        Pure.
    """
    control = dmc.SegmentedControl(
        id=element_id,
        data=data,
        value=value,
        fullWidth=True,
        size=CONTROL_SIZE,
        className=SEGMENTED_CLASS,
    )
    if not label:
        return control
    return dmc.Stack([dmc.Text(label, size="sm", fw=500), control], gap=4)


def _date_input(element_id: str, label: str) -> Component:
    """
    Description:
        A clearable, touch-sized date picker, empty at first.

    Parameters:
        element_id: the picker's id.
        label: its label.

    Return Values:
        Component: the dmc.DatePickerInput.

    Notes:
        Pure.
    """
    return dmc.DatePickerInput(
        id=element_id,
        label=label,
        value=None,
        clearable=True,
        valueFormat=DATE_PICKER_FORMAT,
        size=CONTROL_SIZE,
    )


def _select(element_id: str, label: str, data: list[dict[str, str]]) -> Component:
    """
    Description:
        A filter picker starting on "All".

    Parameters:
        element_id: the picker's id.
        label: its label.
        data: its options (the first is "All").

    Return Values:
        Component: the dmc.Select.

    Notes:
        Pure.
    """
    return dmc.Select(
        id=element_id,
        label=label,
        data=data,
        value=ALL_VALUE,
        allowDeselect=False,
        size=CONTROL_SIZE,
    )


def _filter_bar(
    seasons: list[dict],
    categories: list[dict],
    formats: list[dict],
    zones: list[dict],
    shot_types: list[dict],
) -> Component:
    """
    Description:
        The shared filter bar (REQ-036, D3) in an accordion, closed at first; its control
        shows "Filters" or "Filters · n on".

    Parameters:
        seasons: data_seasons.list_seasons of the player.
        categories: data_games.list_game_categories.
        formats: data_games.list_game_formats.
        zones: data_dashboards.list_zones.
        shot_types: data_entry.list_shot_types.

    Return Values:
        Component: dmc.Accordion "dsh-filter-accordion" (item "filters").

    Notes:
        Ids per plan §3.1 item 4.
    """
    context_data = [
        {"value": ALL_VALUE, "label": "All"},
        {"value": CONTEXT_GAME, "label": "Games"},
        {"value": CONTEXT_PRACTICE, "label": "Practice"},
    ]
    panel = dmc.Stack(
        [
            _select("dsh-season", "Season", season_options(seasons)),
            _date_input("dsh-date-from", "From"),
            _date_input("dsh-date-to", "To"),
            _segmented("dsh-context", "Games or practice", context_data, ALL_VALUE),
            _select("dsh-category", "Game category", code_options(categories, "code")),
            _select("dsh-format", "Game format", code_options(formats, "code")),
            _select("dsh-zone", "Zone", code_options(zones, "code")),
            _select("dsh-shot-type", "Shot type", code_options(shot_types, "shot_type_code")),
            dmc.Text(FILTER_HINT, size="sm", c="dimmed"),
            secondary_button("dsh-filter-clear", "Clear filters"),
            form_message("dsh-filter-message"),
        ],
        gap="sm",
    )
    return dmc.Accordion(
        dmc.AccordionItem(
            [
                dmc.AccordionControl(dmc.Text("Filters", id="dsh-filter-summary", fw=600)),
                dmc.AccordionPanel(panel),
            ],
            value="filters",
        ),
        id="dsh-filter-accordion",
    )


def _chart_panel(bands: list[dict]) -> Component:
    """
    Description:
        The Shot chart view (REQ-038): Spots / Zones, the filtered shooting tiles, the court
        heat map, the zone list, the legend and the manual-entries caption.

    Parameters:
        bands: data_dashboards.list_heat_map_bands (legend).

    Return Values:
        Component: the panel's stack.

    Notes:
        The figure is drawn by render_chart_view; the empty court is shown until then.
    """
    modes = [
        {"value": CHART_MODE_SPOTS, "label": "Spots"},
        {"value": CHART_MODE_ZONES, "label": "Zones"},
    ]
    return dmc.Stack(
        [
            _segmented("dsh-chart-mode", "", modes, CHART_MODE_SPOTS),
            html.Div(id="dsh-chart-tiles"),
            dcc.Graph(
                id="dsh-heat-chart",
                figure=build_game_heat_figure([]),
                config=COURT_GRAPH_CONFIG,
                className=COURT_CLASS,
            ),
            html.Div(id="dsh-zone-list"),
            heat_legend(bands),
            dmc.Text(CHART_CAPTION, id="dsh-chart-caption", size="sm", c="dimmed"),
            form_message("dsh-chart-message"),
        ],
        gap="sm",
        pt="sm",
    )


def _trends_panel(definitions: dict[str, dict]) -> Component:
    """
    Description:
        The Trends view (REQ-037, D8): Week / Month, FG% / 3P% / FT%, the trend chart and the
        warm-up vs fatigue chart.

    Parameters:
        definitions: data_games.list_stat_definitions (the stat toggle shows their
            abbreviations).

    Return Values:
        Component: the panel's stack.

    Notes:
        Both figures are drawn by render_trends_view. Both graphs carry DASH_GRAPH_STYLE, a
        fixed DASH_CHART_HEIGHT_PX container height, so they are visible at 380 px and on a
        desktop even when first drawn inside the hidden Trends panel (AP-4).
    """
    periods = [
        {"value": TREND_PERIOD_WEEK, "label": "Week"},
        {"value": TREND_PERIOD_MONTH, "label": "Month"},
    ]
    stats = [
        {
            "value": key,
            "label": str(stat_definition(definitions, columns[0]).get("abbreviation", key)),
        }
        for key, columns in TREND_STAT_COLUMNS.items()
    ]
    return dmc.Stack(
        [
            _segmented("dsh-trend-period", "", periods, TREND_PERIOD_MONTH),
            _segmented("dsh-trend-stat", "", stats, TREND_STAT_DEFAULT),
            dcc.Graph(
                id="dsh-trend-chart",
                figure=empty_figure(""),
                config=GRAPH_CONFIG,
                className=DASH_GRAPH_CLASS,
                style=DASH_GRAPH_STYLE,
            ),
            dmc.Title("Warm-up vs fatigue", order=4),
            dcc.Graph(
                id="dsh-fatigue-chart",
                figure=empty_figure(""),
                config=GRAPH_CONFIG,
                className=DASH_GRAPH_CLASS,
                style=DASH_GRAPH_STYLE,
            ),
            dmc.Text(FATIGUE_CAPTION, id="dsh-fatigue-caption", size="sm", c="dimmed"),
            form_message("dsh-trends-message"),
        ],
        gap="sm",
        pt="sm",
    )


def _compare_controls(games: list[dict]) -> list[Component]:
    """
    Description:
        The compare controls (REQ-065, D9): the mode, then one group per mode (only the
        Last N group shown at first) and the result area.

    Parameters:
        games: data_games.list_games of the player (the two game pickers).

    Return Values:
        list[Component]: the controls in screen order.

    Notes:
        render_compare shows the chosen mode's group.
    """
    modes = [
        {"value": COMPARE_LAST, "label": "Last N"},
        {"value": COMPARE_PERIODS, "label": "Periods"},
        {"value": COMPARE_GAMES, "label": "Two games"},
    ]
    options = game_options(games)
    pickers = (("dsh-cmp-game-a", "Game A"), ("dsh-cmp-game-b", "Game B"))
    return [
        _segmented("dsh-cmp-mode", "", modes, COMPARE_LAST),
        html.Div(
            dmc.NumberInput(
                id="dsh-cmp-n",
                label="Games",
                value=DASH_COMPARE_N_DEFAULT,
                min=1,
                max=DASH_COMPARE_N_MAX,
                allowDecimal=False,
                allowNegative=False,
                clampBehavior="none",
                hideControls=True,
                size=CONTROL_SIZE,
                inputProps={"inputMode": "numeric"},
            ),
            id="dsh-cmp-last-wrap",
            style=SHOWN_STYLE,
        ),
        html.Div(
            dmc.Stack(
                [
                    _date_input("dsh-cmp-a-from", "Period A from"),
                    _date_input("dsh-cmp-a-to", "Period A to"),
                    _date_input("dsh-cmp-b-from", "Period B from"),
                    _date_input("dsh-cmp-b-to", "Period B to"),
                ],
                gap="sm",
            ),
            id="dsh-cmp-periods-wrap",
            style=HIDDEN_STYLE,
        ),
        html.Div(
            dmc.Stack(
                [
                    dmc.Select(
                        id=picker_id,
                        label=label,
                        data=options,
                        value=None,
                        searchable=True,
                        clearable=True,
                        size=CONTROL_SIZE,
                    )
                    for picker_id, label in pickers
                ],
                gap="sm",
            ),
            id="dsh-cmp-games-wrap",
            style=HIDDEN_STYLE,
        ),
        html.Div(id="dsh-cmp-result"),
    ]


def _games_panel(games: list[dict]) -> Component:
    """
    Description:
        The Games view (REQ-039, REQ-065, D10): Per game / Totals, the averages tiles, season
        vs season and the comparisons.

    Parameters:
        games: data_games.list_games of the player.

    Return Values:
        Component: the panel's stack.

    Notes:
        Filled by render_games_view and render_compare.
    """
    measures = [
        {"value": MEASURE_AVG, "label": "Per game"},
        {"value": MEASURE_TOTAL, "label": "Totals"},
    ]
    return dmc.Stack(
        [
            _segmented("dsh-games-measure", "", measures, MEASURE_AVG),
            html.Div(id="dsh-games-avg"),
            dmc.Title("Season vs season", order=4),
            html.Div(id="dsh-season-table"),
            dmc.Text(SEASON_TABLE_CAPTION, id="dsh-season-caption", size="sm", c="dimmed"),
            dmc.Title("Compare", order=4),
            *_compare_controls(games),
            form_message("dsh-games-message"),
        ],
        gap="sm",
        pt="sm",
    )


def _bests_panel() -> Component:
    """
    Description:
        The Bests view (REQ-040): its caption, the content filled by render_bests_view and
        the message line.

    Parameters:
        None.

    Return Values:
        Component: the panel's stack.

    Notes:
        Pure.
    """
    return dmc.Stack(
        [
            dmc.Text(BESTS_CAPTION, id="dsh-bests-caption", size="sm", c="dimmed"),
            html.Div(id="dsh-bests-content"),
            form_message("dsh-bests-message"),
        ],
        gap="sm",
        pt="sm",
    )


def dashboards_layout(
    players: list[dict],
    player_id: int,
    seasons: list[dict],
    categories: list[dict],
    formats: list[dict],
    zones: list[dict],
    shot_types: list[dict],
    bands: list[dict],
    games: list[dict],
    definitions: dict[str, dict],
    filters_store: dict,
) -> Component:
    """
    Description:
        The Dashboards page (REQ-036 to REQ-040, REQ-065): player picker, the two stores,
        the filter bar and the four view tabs (Shot chart, Trends, Games, Bests).

    Parameters:
        players: data_players.list_live_players (picker options).
        player_id: the selected player (current_player_id() via the router). Never a user
            id (COD-8-01).
        seasons: data_seasons.list_seasons of that player.
        categories: data_games.list_game_categories.
        formats: data_games.list_game_formats.
        zones: data_dashboards.list_zones.
        shot_types: data_entry.list_shot_types.
        bands: data_dashboards.list_heat_map_bands.
        games: data_games.list_games of that player.
        definitions: data_games.list_stat_definitions.
        filters_store: the initial dsh-filters data (controller_dashboards.filters_to_store
            of the player with no filters).

    Return Values:
        Component: the page.

    Notes:
        Every panel is in the layout; each view callback draws its panel only while its tab
        is active (plan §3, R1). The tab is not persisted (D1).
    """
    tabs = (
        (VIEW_CHART, "Shot chart", _chart_panel(bands)),
        (VIEW_TRENDS, "Trends", _trends_panel(definitions)),
        (VIEW_GAMES, "Games", _games_panel(games)),
        (VIEW_BESTS, "Bests", _bests_panel()),
    )
    return page_skeleton(
        "Dashboards",
        [
            dmc.Select(
                id="dsh-player",
                label="Player",
                data=player_options(players),
                value=str(player_id),
                allowDeselect=False,
                size=CONTROL_SIZE,
            ),
            dcc.Store(
                id="dsh-player-store", storage_type="memory", data={"player_id": player_id}
            ),
            dcc.Store(id="dsh-filters", storage_type="memory", data=filters_store),
            _filter_bar(seasons, categories, formats, zones, shot_types),
            dmc.Tabs(
                [
                    dmc.TabsList(
                        [
                            dmc.TabsTab(label, value=value, id=f"dsh-tab-{value}")
                            for value, label, _ in tabs
                        ],
                        grow=True,
                    ),
                    *[dmc.TabsPanel(panel, value=value) for value, _, panel in tabs],
                ],
                id="dsh-view",
                value=VIEW_CHART,
                className="hoopster-dash-tabs",
            ),
        ],
    )
