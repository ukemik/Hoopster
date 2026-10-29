"""Home controller of Hoopster: builds the Home page and handles its player picker (REQ-041,
REQ-055, REQ-075) and the automatic resume (REQ-057). Home shows the selected player's active
goals with progress bars, after a quiet refresh of every player's goal statuses (task
2026-10-23_goals_measurements, item 8, D4, D15)."""

import logging
from datetime import UTC, date, datetime

from dash import ClientsideFunction, Dash, Input, Output, State, no_update
from dash.development.base_component import Component
from dash.exceptions import PreventUpdate
from flask_login import current_user

from config.app_config import (
    GOAL_REFRESH_MAX_DATE_SKEW_DAYS,
    HOME_GOALS_LIMIT,
    RECENT_ACTIVITY_LIMIT,
    SYSTEM_USER_ID,
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
from src.data_goals import list_goals_with_progress, refresh_goal_statuses
from src.data_home import get_resume_target, list_recent_activity
from src.data_players import list_live_players
from src.layouts_goals import STATUS_ACTIVE, home_goals_section
from src.layouts_home import home_layout, recent_section

logger = logging.getLogger(__name__)

MESSAGE_RECENT_FAILED = "Could not load the recent entries. Reference: {ref}"
MESSAGE_GOALS_FAILED = "Could not load the goals. Reference: {ref}"


# --- Home page (REQ-041, REQ-075) ---

def build_home_page(player_id: int, clock: DeviceClock, params: dict[str, str]) -> Component:
    """
    Description:
        Builds the Home page: the resume card, the greeting, the player picker, quick
        starts, the selected player's active goals with progress bars and most recent
        entries.

    Parameters:
        player_id: the selected player, h_data.players.player_id from
            app_controller.current_player_id() (never None here: the router shows the
            no-player page instead). Never a user id (COD-8-01).
        clock: the device clock; its today picks the resume target and is the date of the
            goal progress and status refresh (recent dates are calendar dates shown as
            stored, REQ-055).
        params: the route parameters (none for Home).

    Return Values:
        Component: the page.

    Notes:
        Called by the router only for a signed-in user, so current_user is an AppUser.
        Exceptions propagate to the router, which shows the error page with a reference.
        The resume target (REQ-057) uses the device's date; a failed read of it is logged
        and treated as nothing to resume, so it never blocks Home (task 2026-10-23_games).
        The goal status refresh (as SYSTEM_USER_ID, D4) runs first; it and the goals read
        never block Home either (task 2026-10-23_goals_measurements, item 8).
    """
    _refresh_goal_statuses(clock)
    session = data_handler.get_session()
    recent = list_recent_activity(session, player_id, RECENT_ACTIVITY_LIMIT)
    players = list_live_players(session)
    resume = _resume_target(clock)
    goals_children = _goals_section(player_id, clock)
    return home_layout(
        current_user.display_name, recent, players, player_id, resume, goals_children
    )


def _clock_date(clock: object) -> date | None:
    """
    Description:
        The device's today from the device clock (REQ-055).

    Parameters:
        clock: the router's DeviceClock or device-clock.data.

    Return Values:
        date | None: the device date, or None when unknown.

    Notes:
        Pure. Never the server's date.
    """
    today = normalise_clock(clock)["today"]
    return date.fromisoformat(today) if today else None


def _refresh_goal_statuses(clock: DeviceClock) -> None:
    """
    Description:
        Refreshes every live player's goal statuses as SYSTEM_USER_ID before Home is built
        (REQ-032, D4), so missed goals are marked whenever the app is opened (COD-20-03).

    Parameters:
        clock: the device clock; nothing runs while its date is unknown.

    Return Values:
        None.

    Notes:
        Its own copy of controller_goals._refresh_all, because controllers never import
        each other. Runs only when the device date is within GOAL_REFRESH_MAX_DATE_SKEW_DAYS
        of the server's UTC date (D4 amendment, 2026-10-26); otherwise it is skipped with a
        warning giving the two dates only. The device date stays the date used (REQ-055).
        run_write logs a failure with its reference; it is also logged here as a warning and
        never blocks Home.
    """
    today = _clock_date(clock)
    if today is None:
        return
    server_today = datetime.now(UTC).date()
    if abs((today - server_today).days) > GOAL_REFRESH_MAX_DATE_SKEW_DAYS:
        logger.warning(
            "Goal status refresh skipped: device date %s is far from server UTC date %s",
            today.isoformat(),
            server_today.isoformat(),
        )
        return
    result = run_write(
        SYSTEM_USER_ID,
        lambda session: refresh_goal_statuses(session, today, None),
        "refresh goal statuses",
    )
    if not result.ok:
        logger.warning("Goal status refresh failed (ref %s); Home shown without it", result.ref)


def _goals_section(player_id: int, clock: object) -> list:
    """
    Description:
        The children of Home's goals panel: up to HOME_GOALS_LIMIT active goals of the
        player with progress bars, earliest deadline first (REQ-041, D15).

    Parameters:
        player_id: the selected player. Never a user id (COD-8-01).
        clock: the device clock (today for progress).

    Return Values:
        list: layouts_goals.home_goals_section, or ["Could not load the goals. Reference:
        {ref}"] when the read failed or the device date is unknown.

    Notes:
        A boundary for this one read: a failure is logged with a reference and the rest of
        Home still loads.
    """
    try:
        today = _clock_date(clock)
        if today is None:
            raise ValueError("device date unknown")
        goals = list_goals_with_progress(
            data_handler.get_session(), player_id, today, (STATUS_ACTIVE,), HOME_GOALS_LIMIT
        )
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Home goals read failed (ref %s)", ref)
        return [MESSAGE_GOALS_FAILED.format(ref=ref)]
    return home_goals_section(goals)


def _resume_target(clock: DeviceClock) -> dict | None:
    """
    Description:
        Reads the resume target for the device's today (REQ-057, plan §3.4).

    Parameters:
        clock: the device clock; clock["today"] is "YYYY-MM-DD" or None.

    Return Values:
        dict | None: data_home.get_resume_target result, or None when the device date is
        unknown, there is nothing to resume, or the read failed.

    Notes:
        A boundary for this one read: a failure is logged with a reference and ignored.
        Never the server's date (REQ-055).
    """
    today_text = clock.get("today")
    if not today_text:
        return None
    try:
        return get_resume_target(data_handler.get_session(), date.fromisoformat(today_text))
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Resume target read failed (ref %s); Home shown without it", ref)
        return None


def select_home_player(value: str | None, clock: object) -> tuple[object, object]:
    """
    Description:
        The Home player picker's callback: makes the picked player the selected player and
        shows that player's recent entries and active goals.

    Parameters:
        value: home-player.value, the player id as a string.
        clock: device-clock.data (State; today for the goal progress, REQ-055).

    Return Values:
        tuple[object, object]: the new children of home-recent (or a message with a
        reference when the entries could not be read; the goals are then left unchanged)
        and of home-goals (_goals_section, which carries its own message on failure).

    Notes:
        A boundary (COD-4-04). Raises PreventUpdate for a value that is not a player id,
        and when the player is no longer live (logged as a warning; nothing changes). The
        selected player changes only through set_current_player_id (Context Adaptation
        Table). The second output was added by task 2026-10-23_goals_measurements.
    """
    player_id = parse_player_value(value)
    if player_id is None:
        raise PreventUpdate
    try:
        selected = set_current_player_id(player_id)
        recent = (
            list_recent_activity(data_handler.get_session(), player_id, RECENT_ACTIVITY_LIMIT)
            if selected
            else None
        )
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Home player change failed (ref %s)", ref)
        return MESSAGE_RECENT_FAILED.format(ref=ref), no_update
    if recent is None:
        logger.warning("Home picker chose player %s, which is not a live player", player_id)
        raise PreventUpdate
    return recent_section(recent), _goals_section(player_id, clock)


# --- Registration ---

def register_home_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the Home page's player picker callback (outputs home-recent and
        home-goals) and the automatic resume.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        Input and Outputs all live in the Home page, so prevent_initial_call stops the run
        when the router inserts the page. auto_resume (task 2026-10-23_games, REQ-057) must
        run on that insertion, so it keeps the initial call; the session flag resume-checked
        makes it navigate at most once per browser session. It writes url.href, not
        url.pathname: it runs inside the url.pathname -> device_clock -> router chain that
        built Home, and Dash drops the device_clock re-run its own pathname output would
        cause as a circular call, so the page never changed. On an href change dcc.Location
        (refresh="callback-nav") pushes the path and reports the new pathname itself, the way
        a dcc.Link click does, which starts a fresh chain.
    """
    app.callback(
        Output("home-recent", "children"),
        Output("home-goals", "children"),
        Input("home-player", "value"),
        State("device-clock", "data"),
        prevent_initial_call=True,
    )(select_home_player)

    app.clientside_callback(
        ClientsideFunction("hoopster_games", "auto_resume"),
        Output("url", "href"),
        Output("resume-checked", "data"),
        Input("home-resume-target", "data"),
        State("resume-checked", "data"),
    )
