"""Entry UI shared by the practice and game screens: the court figures (half and full), the
tally text, the entry panel with its controls, dialogs and stores, the Entries list items and
the entry detail body.

REQ-004, REQ-005, REQ-007 to REQ-014, REQ-016, REQ-044, REQ-045, REQ-058. Court spot positions
come from h_ref.court_spots (REQ-008); only the painted court lines are drawn from the geometry
constants below. Imports only layouts, config and libraries; the entry parent is passed in as
a plain dict {"kind": "practice" | "game" | "drill", "id": <int>}, never as
data_entry.EntryParent. Drill mode (practice_drills plan §3.7) adds the drill sub-panel
(court-drill-panel), which every entry page carries, hidden outside drill mode.
"""

import logging
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import dash_mantine_components as dmc
import plotly.graph_objects as go
from dash import dcc, html
from dash.development.base_component import Component

from config.app_config import COURT_WATCHDOG_INTERVAL_MS, ENTRY_ATTEMPTS_MAX
from src.layouts import (
    CONTROL_SIZE,
    entry_button,
    form_message,
    primary_button,
    save_status_badge,
    secondary_button,
)

logger = logging.getLogger(__name__)


# --- Court geometry (feet, NFHS; database plan §6.14.1) ---

# Origin = baseline midpoint under the basket; x right as seen on screen, y into the court.
COURT_HALF_WIDTH_FT = 25
HALF_COURT_LENGTH_FT = 42
BASKET_Y_FT = 5.25
RIM_RADIUS_FT = 0.75
BACKBOARD_Y_FT = 4
BACKBOARD_HALF_WIDTH_FT = 3
LANE_HALF_WIDTH_FT = 6
FREE_THROW_LINE_Y_FT = 19
FT_CIRCLE_RADIUS_FT = 6
THREE_POINT_RADIUS_FT = 19.75
CENTER_CIRCLE_RADIUS_FT = 6

# Margin around the court inside the plot, so the boundary lines are not clipped.
COURT_AXIS_PADDING_FT = 0.5
# Points of each polyline arc (Plotly shape paths have no arc command): 3-degree steps.
ARC_POINT_COUNT = 61

COURT_LINE_COLOR = "#868e96"
COURT_LINE_WIDTH_PX = 2
COURT_BACKGROUND_COLOR = "#ffffff"
COURT_MARGIN_PX = 4
COURT_HOVER_DISTANCE_PX = 40

SPOT_MARKER_SIZE_PX = 28
SPOT_LABEL_FONT_SIZE_PX = 12
SPOT_OUTLINE_WIDTH_PX = 2
# Duplicated in assets/hoopster.js (SPOT_COLOR, SPOT_SELECTED_COLOR); keep them equal.
SPOT_COLOR = "#495057"
SPOT_SELECTED_COLOR = "#fd7e14"

# Full court (plan D1): the 84 ft NFHS court drawn whole; the far half mirrors the near half
# about the half-court line (y' = 2 x 42 - y). Spots stay on the near half.
FULL_COURT_LENGTH_FT = 2 * HALF_COURT_LENGTH_FT
COURT_VIEW_HALF = "half"
COURT_VIEW_FULL = "full"
# Duplicated in assets/hoopster.js (select_spot); the second class sets the taller aspect.
# On the entry panel it is carried by the court-graph-wrap div, not by court-graph itself:
# dcc.Graph does not re-render on a className change, so a view switch would never reach the
# page. Graphs whose class never changes (dashboards, games) still carry it directly.
COURT_CLASS = "hoopster-court"
# court-graph fills its wrapper, which sets the aspect.
COURT_GRAPH_CLASS = "hoopster-court-graph"

COURT_SELECT_PROMPT = "Tap a spot on the court"
RETRY_HIDDEN_STYLE: dict[str, str] = {"display": "none"}
HIDDEN_CLASS = "hoopster-hidden"


def _line_style() -> dict[str, object]:
    """
    Description:
        The line style shared by every court marking.

    Parameters:
        None.

    Return Values:
        dict[str, object]: a Plotly shape "line" dict.

    Notes:
        None.
    """
    return {"color": COURT_LINE_COLOR, "width": COURT_LINE_WIDTH_PX}


def _arc_path(
    center_x: float, center_y: float, radius: float, y_direction: int
) -> str:
    """
    Description:
        Builds an SVG path polyline for a half circle from angle -90 to +90 degrees, with
        x = cx + r*sin(t) and y = cy + direction*r*cos(t).

    Parameters:
        center_x: the circle centre's x, in feet.
        center_y: the circle centre's y, in feet.
        radius: the radius, in feet.
        y_direction: +1 bulges towards larger y (into the court), -1 towards smaller y.

    Return Values:
        str: the path, "M x,y L x,y ...", with ARC_POINT_COUNT points.

    Notes:
        Plotly shape paths have no arc command, hence the polyline.
    """
    points = []
    for index in range(ARC_POINT_COUNT):
        theta = math.radians(-90 + 180 * index / (ARC_POINT_COUNT - 1))
        x = center_x + radius * math.sin(theta)
        y = center_y + y_direction * radius * math.cos(theta)
        points.append(f"{x:.3f},{y:.3f}")
    return "M " + " L ".join(points)


def _court_shapes() -> list[dict[str, object]]:
    """
    Description:
        The painted half-court markings: boundary, lane, free-throw circle, rim, backboard,
        corner threes, three-point arc and the centre half circle.

    Parameters:
        None.

    Return Values:
        list[dict[str, object]]: Plotly layout shapes, drawn below the spot markers.

    Notes:
        Drawing only; spot positions come from the database (REQ-008).
    """
    line = _line_style()
    shapes: list[dict[str, object]] = [
        {  # boundary
            "type": "rect",
            "x0": -COURT_HALF_WIDTH_FT, "x1": COURT_HALF_WIDTH_FT,
            "y0": 0, "y1": HALF_COURT_LENGTH_FT,
        },
        {  # lane
            "type": "rect",
            "x0": -LANE_HALF_WIDTH_FT, "x1": LANE_HALF_WIDTH_FT,
            "y0": 0, "y1": FREE_THROW_LINE_Y_FT,
        },
        {  # free-throw circle
            "type": "circle",
            "x0": -FT_CIRCLE_RADIUS_FT, "x1": FT_CIRCLE_RADIUS_FT,
            "y0": FREE_THROW_LINE_Y_FT - FT_CIRCLE_RADIUS_FT,
            "y1": FREE_THROW_LINE_Y_FT + FT_CIRCLE_RADIUS_FT,
        },
        {  # rim
            "type": "circle",
            "x0": -RIM_RADIUS_FT, "x1": RIM_RADIUS_FT,
            "y0": BASKET_Y_FT - RIM_RADIUS_FT, "y1": BASKET_Y_FT + RIM_RADIUS_FT,
        },
        {  # backboard
            "type": "line",
            "x0": -BACKBOARD_HALF_WIDTH_FT, "x1": BACKBOARD_HALF_WIDTH_FT,
            "y0": BACKBOARD_Y_FT, "y1": BACKBOARD_Y_FT,
        },
        {  # left corner three
            "type": "line",
            "x0": -THREE_POINT_RADIUS_FT, "x1": -THREE_POINT_RADIUS_FT,
            "y0": 0, "y1": BASKET_Y_FT,
        },
        {  # right corner three
            "type": "line",
            "x0": THREE_POINT_RADIUS_FT, "x1": THREE_POINT_RADIUS_FT,
            "y0": 0, "y1": BASKET_Y_FT,
        },
        {  # three-point arc
            "type": "path",
            "path": _arc_path(0, BASKET_Y_FT, THREE_POINT_RADIUS_FT, 1),
        },
        {  # centre half circle, inside the half court (y >= 36)
            "type": "path",
            "path": _arc_path(0, HALF_COURT_LENGTH_FT, CENTER_CIRCLE_RADIUS_FT, -1),
        },
    ]
    for shape in shapes:
        shape["line"] = line
        shape["layer"] = "below"
        shape["xref"] = "x"
        shape["yref"] = "y"
    return shapes


def _mirror_y(y: float) -> float:
    """
    Description:
        Mirrors a near-half y coordinate onto the far half about the half-court line.

    Parameters:
        y: the near-half y, in feet.

    Return Values:
        float: 2 x HALF_COURT_LENGTH_FT - y.

    Notes:
        Pure.
    """
    return FULL_COURT_LENGTH_FT - y


def _full_court_shapes() -> list[dict[str, object]]:
    """
    Description:
        The painted full-court markings: the half-court shapes, the far half mirrored at
        y' = 84 - y (boundary, lane, free-throw circle, rim, backboard, corner threes and an
        arc bulging towards the near half), and the centre circle drawn whole at y = 42.

    Parameters:
        None.

    Return Values:
        list[dict[str, object]]: Plotly layout shapes, drawn below the spot markers.

    Notes:
        The half-court line is the shared boundary edge at y = 42. Drawing only (plan D1).
    """
    far_basket_y = _mirror_y(BASKET_Y_FT)
    far_shapes: list[dict[str, object]] = [
        {  # far boundary
            "type": "rect",
            "x0": -COURT_HALF_WIDTH_FT, "x1": COURT_HALF_WIDTH_FT,
            "y0": HALF_COURT_LENGTH_FT, "y1": FULL_COURT_LENGTH_FT,
        },
        {  # far lane
            "type": "rect",
            "x0": -LANE_HALF_WIDTH_FT, "x1": LANE_HALF_WIDTH_FT,
            "y0": _mirror_y(FREE_THROW_LINE_Y_FT), "y1": FULL_COURT_LENGTH_FT,
        },
        {  # far free-throw circle
            "type": "circle",
            "x0": -FT_CIRCLE_RADIUS_FT, "x1": FT_CIRCLE_RADIUS_FT,
            "y0": _mirror_y(FREE_THROW_LINE_Y_FT) - FT_CIRCLE_RADIUS_FT,
            "y1": _mirror_y(FREE_THROW_LINE_Y_FT) + FT_CIRCLE_RADIUS_FT,
        },
        {  # far rim
            "type": "circle",
            "x0": -RIM_RADIUS_FT, "x1": RIM_RADIUS_FT,
            "y0": far_basket_y - RIM_RADIUS_FT, "y1": far_basket_y + RIM_RADIUS_FT,
        },
        {  # far backboard
            "type": "line",
            "x0": -BACKBOARD_HALF_WIDTH_FT, "x1": BACKBOARD_HALF_WIDTH_FT,
            "y0": _mirror_y(BACKBOARD_Y_FT), "y1": _mirror_y(BACKBOARD_Y_FT),
        },
        {  # far left corner three
            "type": "line",
            "x0": -THREE_POINT_RADIUS_FT, "x1": -THREE_POINT_RADIUS_FT,
            "y0": far_basket_y, "y1": FULL_COURT_LENGTH_FT,
        },
        {  # far right corner three
            "type": "line",
            "x0": THREE_POINT_RADIUS_FT, "x1": THREE_POINT_RADIUS_FT,
            "y0": far_basket_y, "y1": FULL_COURT_LENGTH_FT,
        },
        {  # far three-point arc, bulging towards the near half
            "type": "path",
            "path": _arc_path(0, far_basket_y, THREE_POINT_RADIUS_FT, -1),
        },
        {  # centre circle, whole
            "type": "circle",
            "x0": -CENTER_CIRCLE_RADIUS_FT, "x1": CENTER_CIRCLE_RADIUS_FT,
            "y0": HALF_COURT_LENGTH_FT - CENTER_CIRCLE_RADIUS_FT,
            "y1": HALF_COURT_LENGTH_FT + CENTER_CIRCLE_RADIUS_FT,
        },
    ]
    line = _line_style()
    for shape in far_shapes:
        shape["line"] = line
        shape["layer"] = "below"
        shape["xref"] = "x"
        shape["yref"] = "y"
    return _court_shapes() + far_shapes


# --- Court figure (REQ-007, REQ-008, REQ-009) ---

def build_court_figure(spots: list[dict], view: str = COURT_VIEW_HALF) -> go.Figure:
    """
    Description:
        Builds the interactive court: the painted lines plus one marker per active spot,
        numbered, with the spot's court_spot_id as customdata. "half" is the half court;
        "full" draws the whole 84 ft court with the same spots on the near half (plan D1).

    Parameters:
        spots: rows of data_entry.list_active_court_spots, ordered by spot_number:
            {"court_spot_id", "spot_number", "spot_name", "zone_code", "zone_label", "x_ft",
            "y_ft"}.
        view: "half" (default) or "full"; any other value gives the half court.

    Return Values:
        go.Figure: the figure, basket at the top (reversed y axis), fixed (no zoom or drag).

    Notes:
        A tap selects the nearest marker (hovermode "closest", hoverdistance 40 px). The
        clientside select_spot callback reads customdata (court_spot_id, never spot_number)
        and the trace meta "spots" list (names and zone labels, in marker order). Both views
        share the one spot trace, customdata, meta and uirevision. Both axes have fixed ranges
        (autorange off) and constrain "domain", so the equal scale never pads the range: the
        drawn y range is 42.5 ... -0.5 (half) or 84.5 ... -0.5 (full) at any box size.
    """
    is_full = view == COURT_VIEW_FULL
    court_length = FULL_COURT_LENGTH_FT if is_full else HALF_COURT_LENGTH_FT
    spot_count = len(spots)
    meta_spots = [
        {
            "court_spot_id": int(spot["court_spot_id"]),
            "spot_number": int(spot["spot_number"]),
            "spot_name": str(spot["spot_name"]),
            "zone_label": str(spot["zone_label"]),
        }
        for spot in spots
    ]
    spot_trace = go.Scatter(
        x=[float(spot["x_ft"]) for spot in spots],
        y=[float(spot["y_ft"]) for spot in spots],
        mode="markers+text",
        text=[str(spot["spot_number"]) for spot in meta_spots],
        textposition="middle center",
        textfont={"color": "#ffffff", "size": SPOT_LABEL_FONT_SIZE_PX},
        marker={
            "size": SPOT_MARKER_SIZE_PX,
            "color": [SPOT_COLOR] * spot_count,
            "line": {"color": "#ffffff", "width": SPOT_OUTLINE_WIDTH_PX},
        },
        customdata=[spot["court_spot_id"] for spot in meta_spots],
        hovertext=[
            f"#{spot['spot_number']} {spot['spot_name']} ({spot['zone_label']})"
            for spot in meta_spots
        ],
        hoverinfo="none",
        meta={"spots": meta_spots},
    )
    figure = go.Figure(data=[spot_trace])
    figure.update_layout(
        shapes=_full_court_shapes() if is_full else _court_shapes(),
        # constrain "domain" (AP-1): when the box and the court differ in aspect, the equal
        # scale shrinks the plot area instead of widening the range, so the drawn range is
        # always exactly this one, whatever size the box had before.
        xaxis={
            "range": [
                -COURT_HALF_WIDTH_FT - COURT_AXIS_PADDING_FT,
                COURT_HALF_WIDTH_FT + COURT_AXIS_PADDING_FT,
            ],
            "autorange": False,
            "constrain": "domain",
            "fixedrange": True,
            "visible": False,
        },
        # Reversed range puts the basket (y = 0) at the top of the screen.
        yaxis={
            "range": [court_length + COURT_AXIS_PADDING_FT, -COURT_AXIS_PADDING_FT],
            "autorange": False,
            "constrain": "domain",
            "scaleanchor": "x",
            "scaleratio": 1,
            "fixedrange": True,
            "visible": False,
        },
        dragmode=False,
        hovermode="closest",
        hoverdistance=COURT_HOVER_DISTANCE_PX,
        clickmode="event",
        margin={
            "l": COURT_MARGIN_PX, "r": COURT_MARGIN_PX,
            "t": COURT_MARGIN_PX, "b": COURT_MARGIN_PX,
        },
        showlegend=False,
        uirevision="court",
        plot_bgcolor=COURT_BACKGROUND_COLOR,
        paper_bgcolor=COURT_BACKGROUND_COLOR,
    )
    return figure


# --- Tally and entry times (REQ-010, REQ-055) ---

ENTRY_TIME_FORMAT = "%H:%M"


def format_tally(makes: int, attempts: int) -> str:
    """
    Description:
        Formats a shooting tally as "{makes} / {attempts} ({pct}%)", or "0 / 0" with no
        attempts.

    Parameters:
        makes: shots made.
        attempts: shots attempted.

    Return Values:
        str: the tally text.

    Notes:
        Rounds half up, like Math.round in assets/hoopster.js (court_render), so the text
        does not change when the clientside render takes over.
    """
    if attempts <= 0:
        return "0 / 0"
    percent = math.floor(makes * 100 / attempts + 0.5)
    return f"{makes} / {attempts} ({percent}%)"


def _in_device_zone(instant: datetime, zone_name: str | None) -> datetime:
    """
    Description:
        Converts an instant to the device's time zone (REQ-055).

    Parameters:
        instant: the instant (aware; a naive value is taken as UTC).
        zone_name: the device's IANA zone from device-clock, or None.

    Return Values:
        datetime: the same instant in the device's zone, or in UTC for an unknown zone.

    Notes:
        Falls back to UTC with a warning for an unknown or malformed zone, like
        controller_entry.format_device_time. OSError is also caught because an odd zone
        name can reach the file system lookup.
    """
    zone: ZoneInfo | timezone = timezone.utc
    if zone_name:
        try:
            zone = ZoneInfo(zone_name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            logger.warning("Unknown device time zone %r; showing UTC", zone_name)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(zone)


def format_entry_time(instant: object, zone_name: str | None) -> str:
    """
    Description:
        Formats an entry's instant as "HH:MM" on the device's clock, for the Entries list.

    Parameters:
        instant: the instant (a datetime; anything else gives "").
        zone_name: the device's IANA zone from device-clock, or None (UTC).

    Return Values:
        str: e.g. "14:05", or "" when instant is not a datetime.

    Notes:
        Pure apart from the warning logged for an unknown zone.
    """
    if not isinstance(instant, datetime):
        return ""
    return _in_device_zone(instant, zone_name).strftime(ENTRY_TIME_FORMAT)


def format_entry_datetime(instant: object, zone_name: str | None) -> str:
    """
    Description:
        Formats an entry's instant as "ddd D MMM YYYY HH:MM" (e.g. "Thu 23 Oct 2026 14:05")
        on the device's clock, for the entry detail's created and edited lines (REQ-004).

    Parameters:
        instant: the instant (a datetime; anything else gives "").
        zone_name: the device's IANA zone from device-clock, or None (UTC).

    Return Values:
        str: the text, or "" when instant is not a datetime.

    Notes:
        Pure apart from the warning logged for an unknown zone. The day has no leading zero.
    """
    if not isinstance(instant, datetime):
        return ""
    local = _in_device_zone(instant, zone_name)
    return f"{local.strftime('%a')} {local.day} {local.strftime('%b %Y %H:%M')}"


# --- Entry panel (REQ-010 to REQ-014, REQ-044, REQ-045, REQ-058; plan §5 item 4) ---

# Detail values: "none" is the unset segment of each segmented control (stored as NULL).
DETAIL_NONE = "none"
HAND_OPTIONS: list[dict[str, str]] = [
    {"value": DETAIL_NONE, "label": "–"},
    {"value": "left", "label": "Left"},
    {"value": "right", "label": "Right"},
]
CREATION_OPTIONS: list[dict[str, str]] = [
    {"value": DETAIL_NONE, "label": "–"},
    {"value": "catch_and_shoot", "label": "Catch & shoot"},
    {"value": "off_dribble", "label": "Off dribble"},
]
DEFENSE_OPTIONS: list[dict[str, str]] = [
    {"value": DETAIL_NONE, "label": "–"},
    {"value": "open", "label": "Open"},
    {"value": "contested", "label": "Contested"},
]
VIEW_OPTIONS: list[dict[str, str]] = [
    {"value": COURT_VIEW_HALF, "label": "Half court"},
    {"value": COURT_VIEW_FULL, "label": "Full court"},
]
MODE_OPTIONS: list[dict[str, str]] = [
    {"value": "single", "label": "Make / Miss"},
    {"value": "cumulative", "label": "X of Y"},
    {"value": "manual", "label": "No spot"},
]
POINT_OPTIONS: list[dict[str, str]] = [
    {"value": "2", "label": "2-pointers"},
    {"value": "3", "label": "3-pointers"},
]
MADE_OPTIONS: list[dict[str, str]] = [
    {"value": "1", "label": "Make"},
    {"value": "0", "label": "Miss"},
]
DEFAULT_POINT_VALUE = "2"
SEGMENTED_CLASS = "hoopster-segmented"
DETAILS_EMPTY_SUMMARY = "No details"
EMPTY_TOTALS: dict[str, int] = {"makes": 0, "attempts": 0, "ft_makes": 0, "ft_attempts": 0}

# Drill mode (practice_drills plan §3.7): Make / Miss and X of Y only (no "No spot", D19); the
# last choice is remembered on the device (REQ-031).
DRILL_MODE_OPTIONS: list[dict[str, str]] = [
    {"value": "single", "label": "Make / Miss"},
    {"value": "cumulative", "label": "X of Y"},
]
DRILL_MODE_PERSISTENCE = "drill-entry-mode"
DRILL_MODE_PERSISTENCE_TYPE = "local"
DEFAULT_SIDE = "left"
NO_PERSONAL_BEST = "No personal best yet"
# Keeps the line breaks of the plan progress text (assets/hoopster.css).
PRE_LINE_CLASS = "hoopster-pre-line"
DRILL_PANEL_CLASS = "hoopster-drill-panel"


def _segmented(element_id: str, data: list[dict[str, str]], value: str) -> dmc.SegmentedControl:
    """
    Description:
        A full-width, touch-sized segmented control (every segment at least 44 px tall
        through the hoopster-segmented class, REQ-045).

    Parameters:
        element_id: the stable element id.
        data: the {"value", "label"} options, in display order.
        value: the selected value.

    Return Values:
        dmc.SegmentedControl: the control.

    Notes:
        None.
    """
    return dmc.SegmentedControl(
        id=element_id,
        data=data,
        value=value,
        fullWidth=True,
        size=CONTROL_SIZE,
        className=SEGMENTED_CLASS,
    )


def _count_input(element_id: str, label: str, minimum: int, value: object) -> dmc.NumberInput:
    """
    Description:
        A whole-number input for makes or attempts (REQ-011, REQ-012, REQ-014).

    Parameters:
        element_id: the stable element id.
        label: the field label.
        minimum: 0 for makes, 1 for attempts.
        value: the initial value: "" for an empty field, or an int.

    Return Values:
        dmc.NumberInput: the input, at most ENTRY_ATTEMPTS_MAX, no decimals or negatives.

    Notes:
        clampBehavior "none" keeps a typed out-of-range number as typed, so the form's own
        check shows its message instead of the field silently changing it. The numeric
        keyboard is requested through inputProps (dmc 2.8 has no inputMode prop). An explicit
        value is always passed, so a rebuilt field never keeps stale text.
    """
    return dmc.NumberInput(
        id=element_id,
        label=label,
        value=value,
        min=minimum,
        max=ENTRY_ATTEMPTS_MAX,
        allowDecimal=False,
        allowNegative=False,
        clampBehavior="none",
        size=CONTROL_SIZE,
        hideControls=True,
        inputProps={"inputMode": "numeric"},
    )


def _shot_type_options(shot_types: list[dict]) -> list[dict[str, str]]:
    """
    Description:
        The shot-type picker's options.

    Parameters:
        shot_types: rows of data_entry.list_shot_types: {"shot_type_code", "label"}.

    Return Values:
        list[dict[str, str]]: {"value": code, "label": label} per type, in the rows' order.

    Notes:
        Pure.
    """
    return [
        {"value": str(row["shot_type_code"]), "label": str(row["label"])}
        for row in shot_types
    ]


def _details_controls(
    prefix: str,
    shot_type_options: list[dict[str, str]],
    values: dict[str, object],
) -> list[Component]:
    """
    Description:
        The four shot-detail controls (REQ-013): shot type, hand, creation, defense.

    Parameters:
        prefix: the id prefix, "court-detail" (entry panel) or "entry-edit" (detail dialog).
        shot_type_options: the shot-type picker's options.
        values: {"shot_type_code", "hand", "creation", "defense"}; None or missing is unset.

    Return Values:
        list[Component]: [<prefix>-shot-type, <prefix>-hand, <prefix>-creation,
        <prefix>-defense].

    Notes:
        The three segmented controls use "none" for unset; the shot type uses None.
    """
    def detail_value(key: str) -> str:
        """
        Description:
            A segmented control's value: the stored detail, or "none" when unset.

        Parameters:
            key: "hand", "creation" or "defense".

        Return Values:
            str: the value.

        Notes:
            None.
        """
        value = values.get(key)
        return str(value) if value else DETAIL_NONE

    shot_type = values.get("shot_type_code")
    return [
        dmc.Select(
            id=f"{prefix}-shot-type",
            label="Shot type",
            data=shot_type_options,
            value=str(shot_type) if shot_type else None,
            clearable=True,
            size=CONTROL_SIZE,
        ),
        dmc.Text("Hand", size="sm"),
        _segmented(f"{prefix}-hand", HAND_OPTIONS, detail_value("hand")),
        dmc.Text("Creation", size="sm"),
        _segmented(f"{prefix}-creation", CREATION_OPTIONS, detail_value("creation")),
        dmc.Text("Defense", size="sm"),
        _segmented(f"{prefix}-defense", DEFENSE_OPTIONS, detail_value("defense")),
    ]


def _shot_panels(spot_preselected: bool = False) -> list[Component]:
    """
    Description:
        The three field-goal entry panels of the mode switch: Make / Miss, X of Y, No spot.

    Parameters:
        spot_preselected: True when the page opens with a spot already selected (drill mode
            pre-selects the first plan spot below its target), so Make and Miss start
            enabled.

    Return Values:
        list[Component]: [court-single-panel, court-cumulative-panel (hidden),
        court-manual-panel (hidden)].

    Notes:
        entry_mode_view (clientside) toggles their className. Make and Miss start disabled
        until a spot is tapped, unless one is pre-selected.
    """
    make_button = entry_button("court-make", "Make", "green")
    miss_button = entry_button("court-miss", "Miss", "red")
    if spot_preselected:
        make_button.disabled = False
        miss_button.disabled = False
    single_panel = html.Div(
        dmc.Group(
            [make_button, miss_button],
            grow=True,
        ),
        id="court-single-panel",
        className="",
    )
    cumulative_panel = html.Div(
        dmc.Stack(
            [
                dmc.Group(
                    [
                        _count_input("court-cum-makes", "Makes", 0, ""),
                        _count_input("court-cum-attempts", "Attempts", 1, ""),
                    ],
                    grow=True,
                ),
                primary_button("court-cum-save", "Save X of Y"),
            ]
        ),
        id="court-cumulative-panel",
        className=HIDDEN_CLASS,
    )
    manual_panel = html.Div(
        dmc.Stack(
            [
                _segmented("court-manual-points", POINT_OPTIONS, DEFAULT_POINT_VALUE),
                dmc.Group(
                    [
                        _count_input("court-manual-makes", "Makes", 0, ""),
                        _count_input("court-manual-attempts", "Attempts", 1, ""),
                    ],
                    grow=True,
                ),
                primary_button("court-manual-save", "Save"),
            ]
        ),
        id="court-manual-panel",
        className=HIDDEN_CLASS,
    )
    return [single_panel, cumulative_panel, manual_panel]


def _details_area(shot_types: list[dict]) -> Component:
    """
    Description:
        The shot-details area (REQ-013): one tap ("Shot details") opens the four controls
        under the entry buttons; a summary line shows what is set.

    Parameters:
        shot_types: rows of data_entry.list_shot_types.

    Return Values:
        Component: court-details-area with court-details-toggle, court-details-summary and
        the court-details collapse (closed) holding the controls and court-details-clear.

    Notes:
        The values are remembered for the browser tab session through court-details-memory
        (details_remember / details_restore, clientside).
    """
    return html.Div(
        dmc.Stack(
            [
                secondary_button("court-details-toggle", "Shot details"),
                dmc.Text(DETAILS_EMPTY_SUMMARY, id="court-details-summary", size="sm"),
                dmc.Collapse(
                    dmc.Stack(
                        [
                            *_details_controls(
                                "court-detail", _shot_type_options(shot_types), {}
                            ),
                            secondary_button("court-details-clear", "Clear details"),
                        ]
                    ),
                    id="court-details",
                    opened=False,
                ),
            ]
        ),
        id="court-details-area",
        className="",
    )


def _free_throw_section(totals: dict[str, int]) -> list[Component]:
    """
    Description:
        The free-throw counter (REQ-014, plan D4): its own title and tally, single FT Make /
        FT Miss buttons, and the FT totals form shown in X of Y and No spot modes.

    Parameters:
        totals: the default player's totals: {"ft_makes", "ft_attempts", ...}.

    Return Values:
        list[Component]: [title, court-ft-tally, court-ft-single-panel,
        court-ft-cumulative-panel (hidden)].

    Notes:
        Free throws need no spot, so FT Make and FT Miss start enabled.
    """
    ft_make = entry_button("court-ft-make", "FT Make", "green")
    ft_miss = entry_button("court-ft-miss", "FT Miss", "red")
    # entry_button starts disabled until a spot is tapped; free throws have no spot.
    ft_make.disabled = False
    ft_miss.disabled = False
    return [
        dmc.Title("Free throws", order=4),
        dmc.Text(
            format_tally(int(totals["ft_makes"]), int(totals["ft_attempts"])),
            id="court-ft-tally",
            fw=700,
        ),
        html.Div(
            dmc.Group([ft_make, ft_miss], grow=True),
            id="court-ft-single-panel",
            className="",
        ),
        html.Div(
            dmc.Stack(
                [
                    dmc.Group(
                        [
                            _count_input("court-ft-makes", "Makes", 0, ""),
                            _count_input("court-ft-attempts", "Attempts", 1, ""),
                        ],
                        grow=True,
                    ),
                    primary_button("court-ft-save", "Save free throws"),
                ]
            ),
            id="court-ft-cumulative-panel",
            className=HIDDEN_CLASS,
        ),
    ]


def _entry_stores(
    parent: dict,
    totals: dict[str, dict[str, object]],
    figures: dict[str, object],
    selected_spot: dict | None = None,
) -> list[Component]:
    """
    Description:
        The entry screen's stores and save watchdog (plan §3, §5 item 4).

    Parameters:
        parent: {"kind": "practice" | "game" | "drill", "id": <int>}.
        totals: the court-base value: data_entry.get_entry_totals.
        figures: {"half": figure dict, "full": figure dict}.
        selected_spot: the pre-selected spot {"court_spot_id", "spot_number", "spot_name",
            "zone_label"} (drill mode), or None (nothing selected).

    Return Values:
        list[Component]: entry-parent, court-base, court-figures, court-selected-spot,
        court-queue, court-confirmed, court-outbox, court-ack, court-request, court-failure,
        court-details-memory, court-watchdog.

    Notes:
        All in memory except court-details-memory (session storage, no data argument, so a
        value remembered earlier in the tab survives the page being rebuilt, REQ-013).
    """
    return [
        dcc.Store(
            id="entry-parent",
            storage_type="memory",
            data={"kind": str(parent["kind"]), "id": int(parent["id"])},
        ),
        dcc.Store(id="court-base", storage_type="memory", data=totals),
        dcc.Store(id="court-figures", storage_type="memory", data=figures),
        dcc.Store(id="court-selected-spot", storage_type="memory", data=selected_spot),
        dcc.Store(id="court-queue", storage_type="memory", data=[]),
        dcc.Store(id="court-confirmed", storage_type="memory", data=[]),
        dcc.Store(id="court-outbox", storage_type="memory"),
        dcc.Store(id="court-ack", storage_type="memory"),
        dcc.Store(id="court-request", storage_type="memory"),
        dcc.Store(id="court-failure", storage_type="memory"),
        dcc.Store(id="court-details-memory", storage_type="session"),
        dcc.Interval(id="court-watchdog", interval=COURT_WATCHDOG_INTERVAL_MS, disabled=True),
    ]


def _drill_panel(drill: dict | None, default_player_id: int) -> Component:
    """
    Description:
        The drill sub-panel of the entry panel (practice_drills plan §3.7): the side control,
        New attempt, the plan progress, the personal best and the drill stores. Always
        present, hidden when drill is None, so every id exists on every entry page.

    Parameters:
        drill: the drill-mode dict of entry_panel, or None on the free court and in games.
        default_player_id: the player the page's court-player shows first.

    Return Values:
        Component: court-drill-panel holding court-drill-side (inside
        court-drill-side-area, hidden unless the drill records a side), court-new-attempt,
        court-plan-progress, court-drill-pb and the stores court-attempt-request ({}),
        court-plan (the plan or None) and court-drill-pb-data ({player_id: text}).

    Notes:
        The side control defaults to left (D8); a shooting drill with the court_side
        symmetry has no side control (the side comes from each shot's spot). The progress
        text is written by the clientside drill_progress.
    """
    symmetry = drill.get("symmetry") if drill else None
    left_label = str(symmetry["left_label"]) if symmetry else "Left"
    right_label = str(symmetry["right_label"]) if symmetry else "Right"
    pb_by_player: dict[str, str] = dict(drill.get("pb_by_player") or {}) if drill else {}
    side_area = html.Div(
        dmc.Stack(
            [
                dmc.Text("Side", size="sm"),
                _segmented(
                    "court-drill-side",
                    [
                        {"value": "left", "label": left_label},
                        {"value": "right", "label": right_label},
                    ],
                    DEFAULT_SIDE,
                ),
            ],
            gap="xs",
        ),
        id="court-drill-side-area",
        className="" if symmetry else HIDDEN_CLASS,
    )
    return html.Div(
        dmc.Stack(
            [
                side_area,
                secondary_button("court-new-attempt", "New attempt"),
                dmc.Text("", id="court-plan-progress", className=PRE_LINE_CLASS),
                dmc.Text(
                    pb_by_player.get(str(default_player_id), NO_PERSONAL_BEST),
                    id="court-drill-pb",
                    size="sm",
                    className=PRE_LINE_CLASS,
                ),
                dcc.Store(id="court-attempt-request", storage_type="memory", data={}),
                dcc.Store(
                    id="court-plan",
                    storage_type="memory",
                    data=drill.get("plan") if drill else None,
                ),
                dcc.Store(id="court-drill-pb-data", storage_type="memory", data=pb_by_player),
            ],
            gap="sm",
        ),
        id="court-drill-panel",
        className=DRILL_PANEL_CLASS if drill else f"{DRILL_PANEL_CLASS} {HIDDEN_CLASS}",
    )


def _preselected_spot(spots: list[dict], court_spot_id: object) -> dict | None:
    """
    Description:
        The court-selected-spot value of a pre-selected spot.

    Parameters:
        spots: rows of data_entry.list_active_court_spots.
        court_spot_id: the spot to select, or None.

    Return Values:
        dict | None: {"court_spot_id", "spot_number", "spot_name", "zone_label"} (the shape
        select_spot writes), or None when the id is not an active spot.

    Notes:
        Pure.
    """
    for spot in spots:
        if court_spot_id is not None and int(spot["court_spot_id"]) == court_spot_id:
            return {
                "court_spot_id": int(spot["court_spot_id"]),
                "spot_number": int(spot["spot_number"]),
                "spot_name": str(spot["spot_name"]),
                "zone_label": str(spot["zone_label"]),
            }
    return None


def entry_panel(
    parent: dict,
    totals: dict[str, dict[str, object]],
    default_player_id: int,
    spots: list[dict],
    shot_types: list[dict],
    drill: dict | None = None,
) -> list[Component]:
    """
    Description:
        Every shared control and store of an entry screen, in display order: court view
        toggle, mode switch, field-goal tally, court, selected spot, the drill sub-panel, the
        three field-goal panels, shot details, the free-throw counter, form feedback, Undo /
        save status / Retry, Entries, the entry dialogs and the stores.

    Parameters:
        parent: the entry parent as a plain dict {"kind": "practice" | "game" | "drill",
            "id": <int>}, already confirmed live by the page builder.
        totals: data_entry.get_entry_totals of the parent: {str(player_id): {"makes",
            "attempts", "ft_makes", "ft_attempts", ...}}.
        default_player_id: the player the page's court-player shows first.
        spots: rows of data_entry.list_active_court_spots.
        shot_types: rows of data_entry.list_shot_types.
        drill: drill mode (practice_drills plan §3.7), or None for the free court and games:
            {"plan": {"spots": [...]}, "has_ft_spot": bool, "symmetry": {"left_label",
            "right_label"} or None (no side control), "pb_by_player": {str(player_id):
            text}, "selected_spot": court_spot_id or None}.

    Return Values:
        list[Component]: the components, to be placed in the page after its court-player
        component (the page must contain a component court-player with a value).

    Notes:
        Store contract (plan §3): entry-parent {"kind", "id"}; court-base = the database
        totals of every owner, refreshed by the server after every save, undo, edit and
        delete; court-figures {"half", "full"}; court-queue [] and court-confirmed [] (entries
        carry "player_id" and "table"); court-outbox (the command in flight); court-ack
        (written only by the server through set_props); court-request (a validated form
        entry for the dispatcher); court-failure ({"failed_at", "auto_retries"} or null);
        court-details-memory (session storage). The tallies show base plus queued inserts
        minus entries targeted by queued deletes. Drill mode: court-mode offers Make / Miss
        and X of Y only and remembers the choice on the device; the free-throw section
        (court-ft-area) is hidden unless the version has a free-throw-line spot; Entries is
        hidden (free-form entries only); the first plan spot below its target is
        pre-selected. Without drill the panel is unchanged apart from the hidden drill
        sub-panel and the court-ft-area wrapper.
    """
    default_totals = totals.get(str(default_player_id), EMPTY_TOTALS)
    half_figure = build_court_figure(spots, COURT_VIEW_HALF)
    figures = {
        COURT_VIEW_HALF: half_figure.to_dict(),
        COURT_VIEW_FULL: build_court_figure(spots, COURT_VIEW_FULL).to_dict(),
    }
    selected_spot = _preselected_spot(spots, drill.get("selected_spot")) if drill else None
    selected_label = COURT_SELECT_PROMPT
    if selected_spot is not None:
        # The pre-selected spot is marked like a tap would mark it (select_spot).
        half_figure.data[0].marker.color = [
            SPOT_SELECTED_COLOR if int(spot["court_spot_id"]) == selected_spot["court_spot_id"]
            else SPOT_COLOR
            for spot in spots
        ]
        selected_label = (
            f"#{selected_spot['spot_number']} {selected_spot['spot_name']} · "
            f"{selected_spot['zone_label']}"
        )

    mode_control = _segmented(
        "court-mode", DRILL_MODE_OPTIONS if drill else MODE_OPTIONS, "single"
    )
    if drill:
        mode_control.persistence = DRILL_MODE_PERSISTENCE
        mode_control.persistence_type = DRILL_MODE_PERSISTENCE_TYPE

    retry_button = secondary_button("court-retry", "Retry")
    # Hidden until a save fails; court_render toggles this style.
    retry_button.style = dict(RETRY_HIDDEN_STYLE)
    entries_button = secondary_button("court-entries-open", "Entries")
    if drill:
        entries_button.className = f"hoopster-btn {HIDDEN_CLASS}"
    show_free_throws = not drill or bool(drill.get("has_ft_spot"))

    return [
        _segmented("court-view", VIEW_OPTIONS, COURT_VIEW_HALF),
        mode_control,
        dmc.Text(
            format_tally(int(default_totals["makes"]), int(default_totals["attempts"])),
            id="court-tally",
            fw=700,
        ),
        html.Div(
            dcc.Graph(
                id="court-graph",
                figure=half_figure,
                config={
                    "displayModeBar": False,
                    "scrollZoom": False,
                    "doubleClick": False,
                    "responsive": True,
                },
                className=COURT_GRAPH_CLASS,
            ),
            id="court-graph-wrap",
            className=COURT_CLASS,
        ),
        dmc.Text(selected_label, id="court-selected-label"),
        _drill_panel(drill, default_player_id),
        *_shot_panels(selected_spot is not None),
        _details_area(shot_types),
        html.Div(
            dmc.Stack(_free_throw_section(default_totals)),
            id="court-ft-area",
            className="" if show_free_throws else HIDDEN_CLASS,
        ),
        form_message("court-form-feedback"),
        dmc.Group(
            [
                secondary_button("court-undo", "Undo", disabled=True),
                save_status_badge("court-status"),
                retry_button,
            ]
        ),
        entries_button,
        *entry_dialogs(),
        *_entry_stores(parent, totals, figures, selected_spot),
    ]


def fixed_entry_player(player_id: int) -> Component:
    """
    Description:
        The court-player component of a parent with one fixed player (a game): a hidden
        input whose value is the player id.

    Parameters:
        player_id: the game's player (h_data.games.player_id). Never a user id (COD-8-01).

    Return Values:
        Component: dcc.Input id "court-player", type hidden, value str(player_id).

    Notes:
        For the games task; the entry callbacks read court-player.value either way.
    """
    return dcc.Input(id="court-player", type="hidden", value=str(player_id))


# --- Entries list and detail dialogs (REQ-004 to REQ-006; plan D10 to D12) ---

ENTRY_DELETE_QUESTION = (
    "Delete this entry? It is hidden from totals and charts and can be restored within 30 days."
)
# Above the entry detail dialog it is opened from (Mantine's default modal z-index is 200).
DELETE_MODAL_Z_INDEX = 300
ENTRY_ITEM_CLASS = "hoopster-entry-item"
NO_ENTRIES_TEXT = "Nothing entered for this player yet."
TABLE_FREE_THROW = "free_throw"
MODE_SINGLE = "single"
MODE_MANUAL = "manual"


def entry_dialogs() -> list[Component]:
    """
    Description:
        The Entries list dialog, the entry detail dialog and the delete confirmation
        (REQ-005), plus the entry-detail-target store.

    Parameters:
        None.

    Return Values:
        list[Component]: [entries-modal, entry-detail-modal, entry-delete-modal,
        entry-detail-target]; all dialogs centered and closed.

    Notes:
        entry-detail-target ({"table", "entry_id", "entry_mode"}) is written only by
        open_entry_detail from a server-built list.
    """
    entries_modal = dmc.Modal(
        dmc.Stack([html.Div(id="entries-list"), form_message("entries-feedback")]),
        id="entries-modal",
        title="Entries",
        centered=True,
        opened=False,
    )
    detail_modal = dmc.Modal(
        dmc.Stack(
            [
                html.Div(id="entry-detail-body"),
                primary_button("entry-detail-save", "Save changes"),
                primary_button("entry-detail-delete", "Delete entry", color="red"),
                form_message("entry-detail-feedback"),
            ]
        ),
        id="entry-detail-modal",
        title="Entry",
        centered=True,
        opened=False,
    )
    delete_modal = dmc.Modal(
        dmc.Stack(
            [
                dmc.Text(ENTRY_DELETE_QUESTION),
                primary_button("entry-delete-confirm", "Delete", color="red"),
                secondary_button("entry-delete-cancel", "Cancel"),
                form_message("entry-delete-feedback"),
            ]
        ),
        id="entry-delete-modal",
        title="Delete entry?",
        centered=True,
        opened=False,
        zIndex=DELETE_MODAL_Z_INDEX,
    )
    return [
        entries_modal,
        detail_modal,
        delete_modal,
        dcc.Store(id="entry-detail-target", storage_type="memory"),
    ]


def _made_text(makes: object) -> str:
    """
    Description:
        "Make" or "Miss" for a single entry.

    Parameters:
        makes: the entry's makes (0 or 1).

    Return Values:
        str: "Make" when makes is 1 or more, else "Miss".

    Notes:
        Pure.
    """
    return "Make" if isinstance(makes, int) and makes >= 1 else "Miss"


def entry_summary(row: dict) -> str:
    """
    Description:
        The label of an entry without its time: single field goal "#{n} {spot} · Make|Miss";
        X of Y "#{n} {spot} · {m} of {a}"; No spot "No spot {2|3}-pointers · {m} of {a}"; free
        throw "Free throw · Make|Miss"; free-throw totals "Free throws · {m} of {a}".

    Parameters:
        row: a data_entry.list_entries / get_entry row.

    Return Values:
        str: the label.

    Notes:
        Pure.
    """
    makes = row.get("makes")
    attempts = row.get("attempts")
    mode = row.get("entry_mode")
    if row.get("table") == TABLE_FREE_THROW:
        if mode == MODE_SINGLE:
            return f"Free throw · {_made_text(makes)}"
        return f"Free throws · {makes} of {attempts}"
    if mode == MODE_MANUAL:
        return f"No spot {row.get('manual_point_value')}-pointers · {makes} of {attempts}"
    spot = f"#{row.get('spot_number')} {row.get('spot_name')}"
    if mode == MODE_SINGLE:
        return f"{spot} · {_made_text(makes)}"
    return f"{spot} · {makes} of {attempts}"


def entry_list_items(rows: list[dict], zone_name: str | None) -> list[Component]:
    """
    Description:
        The Entries dialog's list: one full-width button per entry, newest first, labelled
        with entry_summary and " · HH:MM" of its creation on the device's clock.

    Parameters:
        rows: data_entry.list_entries rows, newest first.
        zone_name: the device's IANA zone (device-clock), or None.

    Return Values:
        list[Component]: the buttons, id {"type": "entry-item", "index": "<table>-<id>"},
        or one Text "Nothing entered for this player yet." when there are none.

    Notes:
        open_entry_detail parses the index back into table and id.
    """
    if not rows:
        return [dmc.Text(NO_ENTRIES_TEXT, id="entries-empty")]
    items: list[Component] = []
    for row in rows:
        label = entry_summary(row)
        time_text = format_entry_time(row.get("created_at"), zone_name)
        if time_text:
            label = f"{label} · {time_text}"
        button = secondary_button(
            {"type": "entry-item", "index": f"{row['table']}-{int(row['entry_id'])}"}, label
        )
        button.className = f"hoopster-btn {ENTRY_ITEM_CLASS}"
        items.append(button)
    return items


def _shown(relevant: bool) -> str:
    """
    Description:
        The className of an edit control's wrapper: shown when relevant, else hidden.

    Parameters:
        relevant: whether the control applies to the entry's table and mode.

    Return Values:
        str: "" or "hoopster-hidden".

    Notes:
        Pure.
    """
    return "" if relevant else HIDDEN_CLASS


def entry_detail_body(row: dict, zone_name: str | None, shot_types: list[dict]) -> list[Component]:
    """
    Description:
        The entry detail dialog's body (REQ-004, plan D10): summary, "Created by" and "Last
        edited by" lines, then all eight edit controls, prefilled; those that do not apply to
        the entry's table and mode are hidden but present (Save reads them all as State).

    Parameters:
        row: a data_entry.get_entry row.
        zone_name: the device's IANA zone (device-clock), or None.
        shot_types: rows of data_entry.list_shot_types.

    Return Values:
        list[Component]: entry-detail-summary, entry-detail-created, entry-detail-updated,
        then wrappers holding entry-edit-made (single entries), entry-edit-makes and
        entry-edit-attempts (X of Y, No spot, free-throw totals), entry-edit-points (No
        spot), and entry-edit-shot-type, -hand, -creation, -defense (single and X of Y field
        goals).

    Notes:
        Spot, mode and player are not editable (plan D10). A shot type no longer live stays
        selectable for this entry so the edit does not silently clear it.
    """
    is_shot = row.get("table") != TABLE_FREE_THROW
    mode = row.get("entry_mode")
    makes = int(row.get("makes") or 0)
    attempts = int(row.get("attempts") or 1)
    point_value = row.get("manual_point_value")

    shot_type_options = _shot_type_options(shot_types)
    shot_type_code = row.get("shot_type_code")
    if shot_type_code and all(option["value"] != shot_type_code for option in shot_type_options):
        shot_type_label = row.get("shot_type_label") or shot_type_code
        shot_type_options.append({"value": str(shot_type_code), "label": str(shot_type_label)})

    created = (
        f"Created by {row.get('created_by_name')} · "
        f"{format_entry_datetime(row.get('created_at'), zone_name)}"
    )
    updated = (
        f"Last edited by {row.get('updated_by_name')} · "
        f"{format_entry_datetime(row.get('updated_at'), zone_name)}"
    )
    return [
        dmc.Text(entry_summary(row), id="entry-detail-summary", fw=700),
        dmc.Text(created, id="entry-detail-created", size="sm"),
        dmc.Text(updated, id="entry-detail-updated", size="sm"),
        html.Div(
            _segmented("entry-edit-made", MADE_OPTIONS, "1" if makes >= 1 else "0"),
            className=_shown(mode == MODE_SINGLE),
        ),
        html.Div(
            dmc.Group(
                [
                    _count_input("entry-edit-makes", "Makes", 0, makes),
                    _count_input("entry-edit-attempts", "Attempts", 1, attempts),
                ],
                grow=True,
            ),
            className=_shown(mode != MODE_SINGLE),
        ),
        html.Div(
            _segmented(
                "entry-edit-points",
                POINT_OPTIONS,
                str(point_value) if point_value in (2, 3) else DEFAULT_POINT_VALUE,
            ),
            className=_shown(is_shot and mode == MODE_MANUAL),
        ),
        html.Div(
            dmc.Stack(_details_controls("entry-edit", shot_type_options, row)),
            className=_shown(is_shot and mode != MODE_MANUAL),
        ),
    ]
