"""Players UI for Hoopster: the list of player profiles with Add player, and the edit page with
Save and Remove player (REQ-073).

A player is a profile whose statistics the app records, never a login account (COD-8-01).
Layout modules import nothing from controllers; the callbacks live in
src/controller_players.py.
"""

import logging
from datetime import date

import dash_mantine_components as dmc
from dash import dcc, html
from dash.development.base_component import Component

from src.layouts import (
    CONTROL_SIZE,
    form_message,
    nav_link,
    page_skeleton,
    primary_button,
    secondary_button,
)

logger = logging.getLogger(__name__)

# The dominant-hand control's "nothing chosen" value; the controller stores it as NULL.
HAND_NOT_SET = "none"
HAND_OPTIONS: list[dict[str, str]] = [
    {"value": HAND_NOT_SET, "label": "Not set"},
    {"value": "left", "label": "Left"},
    {"value": "right", "label": "Right"},
]
# List detail text per stored h_data.players.dominant_hand value.
HAND_DETAIL_LABELS: dict[str, str] = {"left": "Left hand", "right": "Right hand"}

BIRTH_DATE_DISPLAY_FORMAT = "D MMM YYYY"
BIRTH_DATE_DETAIL_FORMAT = "%d %b %Y"
DETAIL_SEPARATOR = " · "

REMOVE_PLAYER_TEXT = (
    "Remove {name}? Everything recorded for {name} — games, shots, drill results, goals and "
    "measurements, including their part in shared practices — will be hidden. Restore the "
    "profile from Settings › Recently deleted to bring it all back."
)


# --- Players list (REQ-073) ---

def _player_detail(player: dict) -> str:
    """
    Description:
        The dimmed second line of a player in the list: position, dominant hand and date of
        birth, whichever are set, joined by " · ".

    Parameters:
        player: one row of data_players.list_live_players: {"player_id", "display_name",
            "birth_date", "position", "dominant_hand"}.

    Return Values:
        str: the detail text, or "" when none of the three is set.

    Notes:
        The birth date is a calendar date shown as stored (REQ-055).
    """
    parts: list[str] = []
    position = player.get("position")
    if position:
        parts.append(str(position))
    hand = player.get("dominant_hand")
    if hand in HAND_DETAIL_LABELS:
        parts.append(HAND_DETAIL_LABELS[str(hand)])
    birth_date = player.get("birth_date")
    if isinstance(birth_date, date):
        parts.append(f"Born {birth_date.strftime(BIRTH_DATE_DETAIL_FORMAT)}")
    return DETAIL_SEPARATOR.join(parts)


def player_list_items(players: list[dict]) -> list[Component]:
    """
    Description:
        Builds the entries of the Players list: one link per live player to its edit page,
        showing the name above a dimmed detail line.

    Parameters:
        players: rows of data_players.list_live_players, already in display order.

    Return Values:
        list[Component]: per player a dcc.Link with id {"type": "players-item", "index":
        player_id} and href "/players/<player_id>"; with no players, one dmc.Text "No
        players yet." with id "players-empty".

    Notes:
        The children of the players-list stack, so the Add player callback can rebuild
        them. The detail line is left out when the player has no details set.
    """
    if not players:
        return [dmc.Text("No players yet.", id="players-empty", c="dimmed")]
    items: list[Component] = []
    for player in players:
        player_id = int(player["player_id"])
        content: list[Component] = [dmc.Text(str(player["display_name"]))]
        detail = _player_detail(player)
        if detail:
            content.append(dmc.Text(detail, size="sm", c="dimmed"))
        items.append(
            dcc.Link(
                content,
                id={"type": "players-item", "index": player_id},
                href=f"/players/{player_id}",
                className="hoopster-recent-item hoopster-recent-link",
            )
        )
    return items


def players_layout(players: list[dict]) -> Component:
    """
    Description:
        The Players screen: a link back to Settings, the list of live player profiles and
        the Add player form.

    Parameters:
        players: rows of data_players.list_live_players, in display order.

    Return Values:
        Component: the page.

    Notes:
        players-list's children are player_list_items(players). Players is reached from
        Settings > Data (task 2026-10-23_settings_hub, D1), hence players-settings-link.
    """
    return page_skeleton(
        "Players",
        [
            html.Div(
                nav_link("players-settings-link", "‹ Settings", "/settings"),
                className="hoopster-link-button",
            ),
            dmc.Stack(player_list_items(players), id="players-list", gap="xs"),
            dmc.TextInput(
                id="players-add-name", label="New player's name", size=CONTROL_SIZE
            ),
            primary_button("players-add-submit", "Add player"),
            form_message("players-add-feedback"),
        ],
    )


# --- Edit player (REQ-073, REQ-005; Amendment A4) ---

def _remove_player_modal(display_name: str) -> dmc.Modal:
    """
    Description:
        The confirmation dialog of Remove player, stating that everything recorded for the
        player is hidden with the profile and comes back with a restore (Amendment A4).

    Parameters:
        display_name: the player's name, shown in the question.

    Return Values:
        dmc.Modal: the dialog "player-remove-modal", closed.

    Notes:
        Opened by player-edit-remove and closed by player-remove-cancel (clientside); the
        controller's remove_player confirms through player-remove-confirm.
    """
    return dmc.Modal(
        dmc.Stack(
            [
                dmc.Text(
                    REMOVE_PLAYER_TEXT.format(name=display_name), id="player-remove-text"
                ),
                primary_button("player-remove-confirm", "Remove", color="red"),
                secondary_button("player-remove-cancel", "Cancel"),
                form_message("player-remove-feedback"),
            ]
        ),
        id="player-remove-modal",
        title="Remove player?",
        centered=True,
        opened=False,
    )


def player_edit_layout(player: dict) -> Component:
    """
    Description:
        The Edit player screen: name, date of birth, position and dominant hand with Save,
        Remove player with its confirmation, and a link back to the list.

    Parameters:
        player: the row of data_players.get_live_player for the URL's /players/<id>:
            {"player_id", "display_name", "birth_date", "position", "dominant_hand"}.

    Return Values:
        Component: the page.

    Notes:
        The edited player's id is kept in the player-edit-id store, written here only after
        the builder's get_live_player check (Context Adaptation Table). The dominant-hand
        control uses "none" for an unset value; the date picker's value is "YYYY-MM-DD".
    """
    birth_date = player.get("birth_date")
    birth_date_value = birth_date.isoformat() if isinstance(birth_date, date) else None
    position = player.get("position")
    hand = player.get("dominant_hand")
    display_name = str(player["display_name"])

    return page_skeleton(
        "Edit player",
        [
            dmc.TextInput(
                id="player-edit-name", label="Name", value=display_name, size=CONTROL_SIZE
            ),
            dmc.DatePickerInput(
                id="player-edit-birth-date",
                label="Date of birth",
                value=birth_date_value,
                valueFormat=BIRTH_DATE_DISPLAY_FORMAT,
                clearable=True,
                clearButtonProps={"size": "xl"},
                size=CONTROL_SIZE,
            ),
            dmc.TextInput(
                id="player-edit-position",
                label="Position",
                value=str(position) if position else "",
                size=CONTROL_SIZE,
            ),
            dmc.Stack(
                [
                    dmc.Text("Dominant hand", size="sm", fw=500),
                    dmc.SegmentedControl(
                        id="player-edit-hand",
                        data=HAND_OPTIONS,
                        value=str(hand) if hand in HAND_DETAIL_LABELS else HAND_NOT_SET,
                        fullWidth=True,
                        size=CONTROL_SIZE,
                    ),
                ],
                gap=4,
            ),
            primary_button("player-edit-save", "Save"),
            dmc.Text("", id="player-edit-saved", c="green"),
            form_message("player-edit-feedback"),
            secondary_button("player-edit-remove", "Remove player", color="red"),
            html.Div(
                nav_link("players-back", "All players", "/players"),
                className="hoopster-link-button",
            ),
            dcc.Store(id="player-edit-id", storage_type="memory", data=int(player["player_id"])),
            _remove_player_modal(display_name),
        ],
    )
