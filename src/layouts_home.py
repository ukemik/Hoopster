"""Home page UI for Hoopster: the player picker, quick starts, the active goals with progress
bars (layouts_goals.home_goals_section) and the recent-entries list.

REQ-041, REQ-055 (calendar dates shown as stored), REQ-075 (Home player picker).
"""

import logging
from datetime import date

import dash_mantine_components as dmc
from dash import dcc, html
from dash.development.base_component import Component

from src.layouts import (
    CONTROL_SIZE,
    nav_link,
    page_skeleton,
    player_options,
    secondary_button,
)
from src.layouts_goals import home_goals_section

logger = logging.getLogger(__name__)

# h_analytics.v_recent_activity labels practices with this entity value.
PRACTICE_ENTITY = "practice_session"
# ... and games with this one (task 2026-10-23_games).
GAME_ENTITY = "game"
# Where a resume target opens (REQ-057): data_home.get_resume_target kind -> path pattern.
RESUME_PATHS: dict[str, str] = {
    "game": "/games/{id}/live",
    "practice": "/practice/{id}/court",
}
# A practice with drills resumes at its overview, as the recent list opens it (practice_drills
# D3; games review_1 Minor 1).
RESUME_PRACTICE_KIND = "practice"
RESUME_PRACTICE_OVERVIEW_PATH = "/practice/{id}"

RECENT_DATE_FORMAT = "%a %d %b %Y"


# --- Home page (REQ-041) ---

def _format_event_date(event_date: object) -> str:
    """
    Description:
        Formats a recent item's calendar date as stored, e.g. "Sat 18 Oct 2026" (REQ-055).

    Parameters:
        event_date: the item's event_date (a date from the view).

    Return Values:
        str: the formatted date, or the value's text when it is not a date.

    Notes:
        A datetime is also a date, so it formats the same way; no time zone is applied.
    """
    if isinstance(event_date, date):
        return event_date.strftime(RECENT_DATE_FORMAT)
    return str(event_date)


def _recent_item(item: dict) -> Component:
    """
    Description:
        Builds one recent entry: the date above the label. A practice links to its overview
        when it has drills, else to its court screen (practice_drills D3); a game links to
        its game page (task 2026-10-23_games); measurements are plain text.

    Parameters:
        item: one row of data_home.list_recent_activity: {"entity", "entity_id",
            "event_date", "label", "has_drills"}.

    Return Values:
        Component: a dcc.Link with id {"type": "home-recent-item", "index": entity_id} for
        a practice, {"type": "home-recent-game", "index": game_id} for a game, else an
        html.Div.

    Notes:
        A row without "has_drills" links to the court, as before.
    """
    content = [
        dmc.Text(_format_event_date(item["event_date"]), size="sm", c="dimmed"),
        dmc.Text(str(item["label"])),
    ]
    if item["entity"] == PRACTICE_ENTITY:
        entity_id = int(item["entity_id"])
        # practice_drills D3: practices with drills open their overview.
        href = (
            f"/practice/{entity_id}"
            if item.get("has_drills")
            else f"/practice/{entity_id}/court"
        )
        return dcc.Link(
            content,
            id={"type": "home-recent-item", "index": entity_id},
            href=href,
            className="hoopster-recent-item hoopster-recent-link",
        )
    if item["entity"] == GAME_ENTITY:
        # Task 2026-10-23_games item 3.5: a game links to its game page.
        game_id = int(item["entity_id"])
        return dcc.Link(
            content,
            id={"type": "home-recent-game", "index": game_id},
            href=f"/games/{game_id}",
            className="hoopster-recent-item hoopster-recent-link",
        )
    return html.Div(content, className="hoopster-recent-item")


def recent_section(recent: list[dict]) -> list[Component]:
    """
    Description:
        Builds the contents of the Recent section: its heading and the entries, or "Nothing
        logged yet." when there are none.

    Parameters:
        recent: rows of data_home.list_recent_activity for the selected player, newest
            first.

    Return Values:
        list[Component]: the children of the home-recent div.

    Notes:
        Used by home_layout and by the Home player picker's callback, which replaces
        home-recent's children when another player is chosen.
    """
    if recent:
        recent_items: list[Component] = [_recent_item(item) for item in recent]
    else:
        recent_items = [dmc.Text("Nothing logged yet.", c="dimmed", id="home-recent-empty")]
    return [dmc.Title("Recent", order=3), dmc.Stack(recent_items, gap="xs")]


def resume_path(resume: dict | None) -> str | None:
    """
    Description:
        The path a resume target opens (REQ-057): a game's live page; a practice's overview
        when it has drills, else its court, the same choice as the recent list
        (practice_drills D3).

    Parameters:
        resume: data_home.get_resume_target result ({"kind", "id", "label", "has_drills"})
            or None.

    Return Values:
        str | None: "/games/<id>/live", "/practice/<id>" or "/practice/<id>/court", or None.

    Notes:
        Pure. A target without "has_drills" opens the court, as before.
    """
    if not resume or resume.get("kind") not in RESUME_PATHS:
        return None
    kind = str(resume["kind"])
    if kind == RESUME_PRACTICE_KIND and resume.get("has_drills"):
        return RESUME_PRACTICE_OVERVIEW_PATH.format(id=int(resume["id"]))
    return RESUME_PATHS[kind].format(id=int(resume["id"]))


def _resume_card(resume: dict | None) -> list[Component]:
    """
    Description:
        The resume card and its store (task 2026-10-23_games item 3.5).

    Parameters:
        resume: data_home.get_resume_target result or None.

    Return Values:
        list[Component]: [home-resume-target store (the path or None)] plus, with a target,
        the card home-resume holding the link home-resume-link "Resume: {label}".

    Notes:
        The clientside hoopster_games.auto_resume reads the store on the first Home render
        of a browser session.
    """
    path = resume_path(resume)
    store = dcc.Store(id="home-resume-target", storage_type="memory", data=path)
    if path is None or resume is None:
        return [store]
    card = dmc.Paper(
        html.Div(
            nav_link("home-resume-link", f"Resume: {resume.get('label')}", path),
            className="hoopster-link-button hoopster-link-primary",
        ),
        id="home-resume",
        withBorder=True,
        p="xs",
    )
    return [store, card]


def home_layout(
    display_name: str,
    recent: list[dict],
    players: list[dict],
    selected_player_id: int,
    resume: dict | None = None,
    goals_children: list | None = None,
) -> Component:
    """
    Description:
        The Home page: the resume card (when something is in progress today), the player
        picker, New practice, New game, Games, the selected player's active goals with
        progress bars (REQ-041) and most recent entries.

    Parameters:
        display_name: the signed-in user's display name, shown in the greeting.
        recent: rows of data_home.list_recent_activity for the selected player, newest
            first.
        players: rows of data_players.list_live_players, in display order (the picker's
            options).
        selected_player_id: h_data.players.player_id from current_player_id(), the
            picker's value. Never a user id (COD-8-01).
        resume: data_home.get_resume_target result, or None (REQ-057).
        goals_children: the children of the home-goals panel, normally
            layouts_goals.home_goals_section of the player's active goals, or a message
            when they could not be read; None gives the empty section ("No active goals.").

    Return Values:
        Component: the page.

    Notes:
        With no rows, the list shows "Nothing logged yet." Picker values are strings, as dmc
        requires; the callback converts them back with int(). The goals panel replaces the
        former placeholder (task 2026-10-23_goals_measurements, item 13).
    """
    goals_content = goals_children if goals_children is not None else home_goals_section([])
    return page_skeleton(
        f"Hi, {display_name}",
        [
            *_resume_card(resume),
            dmc.Select(
                id="home-player",
                label="Player",
                data=player_options(players),
                value=str(selected_player_id),
                allowDeselect=False,
                size=CONTROL_SIZE,
            ),
            html.Div(
                nav_link("home-new-practice", "New practice", "/practice/new"),
                className="hoopster-link-button hoopster-link-primary",
            ),
            html.Div(
                nav_link("home-new-game", "New game", "/games/new"),
                className="hoopster-link-button hoopster-link-primary",
            ),
            html.Div(
                nav_link("home-games", "Games", "/games"),
                className="hoopster-link-button",
            ),
            dmc.Paper(
                goals_content,
                id="home-goals",
                withBorder=True,
                p="md",
            ),
            html.Div(recent_section(recent), id="home-recent"),
        ],
    )
