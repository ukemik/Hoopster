"""Seasons UI for Hoopster: the Seasons list of the selected player with Add season, and the
Edit season page with Save and Delete (REQ-018, REQ-005; task 2026-10-23_games, plan D7).

Routes /seasons and /seasons/<id>; Settings > Training links here (task 2026-10-23_settings_hub). Built only through the
wrappers of src/layouts.py (COD-5-01).
"""

import logging
from datetime import date

import dash_mantine_components as dmc
from dash import dcc, html
from dash.development.base_component import Component

from src.layouts import (
    CONTROL_SIZE,
    confirm_modal,
    form_message,
    nav_link,
    page_skeleton,
    player_options,
    primary_button,
    secondary_button,
)

logger = logging.getLogger(__name__)

SEASONS_PATH = "/seasons"
DATE_PICKER_FORMAT = "ddd D MMM YYYY"
NO_SEASONS_TEXT = "No seasons yet."


# --- Seasons list (REQ-018) ---

def format_season_date(value: object) -> str:
    """
    Description:
        A season boundary as "1 Sep 2026" (calendar date as stored, REQ-055).

    Parameters:
        value: the date.

    Return Values:
        str: the text, or the value's text when it is not a date.

    Notes:
        Pure.
    """
    if isinstance(value, date):
        return f"{value.day} {value.strftime('%b %Y')}"
    return str(value)


def season_label(season: dict) -> str:
    """
    Description:
        A season's list label: "{name} — 1 Sep 2026 to 31 May 2027".

    Parameters:
        season: a data_seasons.list_seasons row.

    Return Values:
        str: the label.

    Notes:
        Pure.
    """
    return (
        f"{season['season_name']} — {format_season_date(season['start_date'])} to "
        f"{format_season_date(season['end_date'])}"
    )


def season_list_items(seasons: list[dict]) -> list[Component]:
    """
    Description:
        The seasons list: one link per season to its edit page, or "No seasons yet.".

    Parameters:
        seasons: data_seasons.list_seasons rows, by start date.

    Return Values:
        list[Component]: links with id {"type": "seasons-item", "index": season_id}, or one
        Text with id "seasons-empty".

    Notes:
        The link style is the Home recent list's (44 px items).
    """
    if not seasons:
        return [dmc.Text(NO_SEASONS_TEXT, id="seasons-empty", c="dimmed")]
    return [
        dcc.Link(
            season_label(season),
            id={"type": "seasons-item", "index": int(season["season_id"])},
            href=f"{SEASONS_PATH}/{int(season['season_id'])}",
            className="hoopster-recent-item hoopster-recent-link",
        )
        for season in seasons
    ]


def seasons_layout(
    players: list[dict], selected_player_id: int, seasons: list[dict]
) -> Component:
    """
    Description:
        The Seasons screen: the player picker, the player's seasons, and the Add season form
        (name, start, end).

    Parameters:
        players: data_players.list_live_players (the picker's options).
        selected_player_id: current_player_id(), the picker's value. Never a user id.
        seasons: data_seasons.list_seasons of that player.

    Return Values:
        Component: the page.

    Notes:
        Picking a player runs select_seasons_player, which makes it the selected player and
        rebuilds seasons-list. Seasons is reached from Settings > Training (task
        2026-10-23_settings_hub, D1), hence the seasons-settings-link back link.
    """
    return page_skeleton(
        "Seasons",
        [
            html.Div(
                nav_link("seasons-settings-link", "‹ Settings", "/settings"),
                className="hoopster-link-button",
            ),
            dmc.Select(
                id="seasons-player",
                label="Player",
                data=player_options(players),
                value=str(selected_player_id),
                allowDeselect=False,
                size=CONTROL_SIZE,
            ),
            dmc.Stack(season_list_items(seasons), id="seasons-list", gap="xs"),
            dmc.Title("Add a season", order=4),
            dmc.TextInput(id="seasons-add-name", label="Name", size=CONTROL_SIZE),
            dmc.DatePickerInput(
                id="seasons-add-start",
                label="Start date",
                valueFormat=DATE_PICKER_FORMAT,
                size=CONTROL_SIZE,
            ),
            dmc.DatePickerInput(
                id="seasons-add-end",
                label="End date",
                valueFormat=DATE_PICKER_FORMAT,
                size=CONTROL_SIZE,
            ),
            primary_button("seasons-add-submit", "Add season"),
            form_message("seasons-add-feedback"),
        ],
    )


# --- Edit season (REQ-018, REQ-005) ---

def season_edit_layout(season: dict) -> Component:
    """
    Description:
        The Edit season page: name and dates with Save, Delete with its confirmation, and a
        link back to the list.

    Parameters:
        season: data_seasons.get_season row, already confirmed live: {"season_id",
            "player_id", "player_name", "season_name", "start_date", "end_date"}.

    Return Values:
        Component: the page.

    Notes:
        The season's id is kept in season-edit-id, written here only after the builder's
        get_season check (Context Adaptation Table). The delete dialog's question is written
        by open_delete_season with the season's current name.
    """
    start = season.get("start_date")
    end = season.get("end_date")
    return page_skeleton(
        "Edit season",
        [
            dmc.Text(f"Player: {season.get('player_name')}", id="season-edit-player"),
            dcc.Store(id="season-edit-id", storage_type="memory", data=int(season["season_id"])),
            dmc.TextInput(
                id="season-edit-name",
                label="Name",
                value=str(season.get("season_name") or ""),
                size=CONTROL_SIZE,
            ),
            dmc.DatePickerInput(
                id="season-edit-start",
                label="Start date",
                value=start.isoformat() if isinstance(start, date) else None,
                valueFormat=DATE_PICKER_FORMAT,
                size=CONTROL_SIZE,
            ),
            dmc.DatePickerInput(
                id="season-edit-end",
                label="End date",
                value=end.isoformat() if isinstance(end, date) else None,
                valueFormat=DATE_PICKER_FORMAT,
                size=CONTROL_SIZE,
            ),
            primary_button("season-edit-save", "Save"),
            dmc.Text("", id="season-edit-saved", c="green"),
            form_message("season-edit-feedback"),
            secondary_button("season-delete-open", "Delete season", color="red"),
            html.Div(
                nav_link("seasons-back", "All seasons", SEASONS_PATH),
                className="hoopster-link-button",
            ),
            confirm_modal("season-delete", "Delete season?", "Delete"),
        ],
    )
