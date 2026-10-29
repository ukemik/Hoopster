"""Players controller of Hoopster: the Players and Edit player page builders, and the Add,
Save and Remove player callbacks (REQ-073, REQ-005; Amendment A4).

A player is a profile whose statistics the app records, never a login account: the edited
player's id comes from the URL through the player-edit-id store and is never a user id
(COD-8-01). Every write goes through app_controller.run_write.
"""

import logging
from dataclasses import dataclass
from datetime import date

from dash import ClientsideFunction, Dash, Input, Output, State, no_update
from dash.development.base_component import Component
from dash.exceptions import PreventUpdate
from flask_login import current_user
from sqlalchemy.orm import Session

from config.app_config import PLAYER_POSITION_MAX_LENGTH
from src import data_handler
from src.app_controller import DeviceClock, log_failure, new_reference_code, run_write
from src.data_players import (
    get_live_player,
    insert_player,
    list_live_players,
    player_name_taken,
    soft_delete_player,
    update_player,
)
from src.layouts import not_found_page
from src.layouts_players import (
    HAND_NOT_SET,
    REMOVE_PLAYER_TEXT,
    player_edit_layout,
    player_list_items,
    players_layout,
)
from src.setup_accounts import display_name_problem

logger = logging.getLogger(__name__)

# The values h_data.players.dominant_hand accepts (ck_players_dominant_hand); NULL = not set.
STORED_HANDS: frozenset[str] = frozenset({"left", "right"})

PLAYERS_PATH = "/players"

MESSAGE_NAME_TAKEN = "A player named {name} already exists."
MESSAGE_POSITION = f"Enter a position of up to {PLAYER_POSITION_MAX_LENGTH} characters."
MESSAGE_BIRTH_DATE = "Choose a valid birth date."
MESSAGE_SAVE_FAILED = "Could not save the player. Reference: {ref}"
MESSAGE_REMOVE_FAILED = "Could not remove the player. Reference: {ref}"
MESSAGE_LIST_FAILED = "The player was added, but the list could not be reloaded. Reference: {ref}"
MESSAGE_SAVED = "Saved."


# --- Page builders (REQ-073) ---

def build_players_page(
    player_id: int | None, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds the Players screen: the live player profiles and the Add player form.

    Parameters:
        player_id: the selected player, or None when no live profile exists (this route
            works without one). Unused here.
        clock: the device clock (unused).
        params: the route parameters (none).

    Return Values:
        Component: the page.

    Notes:
        Exceptions propagate to the router, which shows the error page with a reference.
    """
    return players_layout(list_live_players(data_handler.get_session()))


def build_player_edit_page(
    player_id: int | None, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds the Edit player screen for the profile in the URL.

    Parameters:
        player_id: the selected player, or None (unused: the edited player comes from the
            URL, Context Adaptation Table).
        clock: the device clock (unused).
        params: {"player_id": "<digits>"} from /players/<id>.

    Return Values:
        Component: the page, or the in-app not-found page when the profile does not exist
        or was removed.

    Notes:
        The player-edit-id store is written by the layout only after this get_live_player
        check.
    """
    player = get_live_player(data_handler.get_session(), int(params["player_id"]))
    if player is None:
        return not_found_page()
    return player_edit_layout(player)


# --- Form checks (pure) ---

@dataclass(frozen=True)
class PlayerForm:
    """
    Description:
        The checked values of the Edit player form, ready to store.

    Parameters:
        display_name: the stripped name, 1-100 characters.
        birth_date: the date of birth, or None.
        position: the stripped position, 1-50 characters, or None when empty.
        dominant_hand: "left", "right" or None (the control's "none" is stored as NULL).

    Return Values:
        Not applicable (data class).

    Notes:
        Built only by parse_player_form.
    """

    display_name: str
    birth_date: date | None
    position: str | None
    dominant_hand: str | None


def parse_player_form(
    name: object, birth_date: object, position: object, hand: object
) -> tuple[PlayerForm | None, str | None]:
    """
    Description:
        Checks the Edit player form, in the order of the fields on screen.

    Parameters:
        name: player-edit-name.value.
        birth_date: player-edit-birth-date.value, "YYYY-MM-DD" or None when cleared.
        position: player-edit-position.value ("" when empty).
        hand: player-edit-hand.value: "none", "left" or "right".

    Return Values:
        tuple[PlayerForm | None, str | None]: (the values, None) when the form is
        acceptable, else (None, the message to show).

    Notes:
        Pure. The name check is setup_accounts.display_name_problem ("Enter a name of 1 to
        100 characters."), shared with the setup script. A dominant hand other than "left"
        or "right" (including HAND_NOT_SET) is stored as NULL. Whether the name is taken
        needs the database and is checked by the callers.
    """
    display_name = name.strip() if isinstance(name, str) else ""
    problem = display_name_problem(display_name)
    if problem is not None:
        return None, problem

    parsed_birth_date: date | None = None
    if birth_date not in (None, ""):
        if not isinstance(birth_date, str):
            return None, MESSAGE_BIRTH_DATE
        try:
            # The picker may send a date-time string; its first 10 characters are the date.
            parsed_birth_date = date.fromisoformat(birth_date[:10])
        except ValueError:
            return None, MESSAGE_BIRTH_DATE

    position_text = position.strip() if isinstance(position, str) else ""
    if len(position_text) > PLAYER_POSITION_MAX_LENGTH:
        return None, MESSAGE_POSITION

    dominant_hand = hand if isinstance(hand, str) and hand in STORED_HANDS else None
    if hand not in (None, HAND_NOT_SET) and dominant_hand is None:
        logger.warning("Unknown dominant-hand value %r stored as not set", hand)
    return (
        PlayerForm(display_name, parsed_birth_date, position_text or None, dominant_hand),
        None,
    )


def _stored_player_id(value: object) -> int | None:
    """
    Description:
        Reads the player-edit-id store.

    Parameters:
        value: player-edit-id.data.

    Return Values:
        int | None: the edited player's id, or None when the store does not hold an int (a
        bool is not an id).

    Notes:
        Pure.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


# --- Callbacks (REQ-073, REQ-005) ---

def add_player(n_clicks: int | None, name: str | None) -> tuple[object, str, object]:
    """
    Description:
        The Add player callback: checks the name, creates the profile and rebuilds the list.

    Parameters:
        n_clicks: players-add-submit.n_clicks.
        name: players-add-name.value.

    Return Values:
        tuple[object, str, object]: players-list.children (no_update unless added),
        players-add-feedback text, players-add-name.value ("" after an add, else
        no_update).

    Notes:
        A boundary (COD-4-04): failures show a reference. The acting user is
        current_user.user_id. The other fields are set later on the Edit player screen.
    """
    if not n_clicks:
        raise PreventUpdate
    display_name = name.strip() if isinstance(name, str) else ""
    problem = display_name_problem(display_name)
    if problem is not None:
        return no_update, problem, no_update
    try:
        taken = player_name_taken(data_handler.get_session(), display_name, None)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Player name check failed (ref %s)", ref)
        return no_update, MESSAGE_SAVE_FAILED.format(ref=ref), no_update
    if taken:
        return no_update, MESSAGE_NAME_TAKEN.format(name=display_name), no_update

    result = run_write(
        current_user.user_id,
        lambda session: insert_player(session, display_name),
        "add player",
    )
    if not result.ok:
        return no_update, MESSAGE_SAVE_FAILED.format(ref=result.ref), no_update
    logger.info("Added player %s", result.value)

    try:
        players = list_live_players(data_handler.get_session())
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Players list reload failed (ref %s)", ref)
        return no_update, MESSAGE_LIST_FAILED.format(ref=ref), ""
    return player_list_items(players), "", ""


def save_player(
    n_clicks: int | None,
    player_id: object,
    name: str | None,
    birth_date: str | None,
    position: str | None,
    hand: str | None,
) -> tuple[str, str, object]:
    """
    Description:
        The Save callback of Edit player: checks the form and saves all four fields.

    Parameters:
        n_clicks: player-edit-save.n_clicks.
        player_id: player-edit-id.data, the edited player (written by the builder after
            get_live_player). Never a user id (COD-8-01).
        name: player-edit-name.value.
        birth_date: player-edit-birth-date.value.
        position: player-edit-position.value.
        hand: player-edit-hand.value.

    Return Values:
        tuple[str, str, object]: player-edit-saved text ("Saved." on success, else ""),
        player-edit-feedback text, and player-remove-text.children (the removal question
        with the saved name, else no_update).

    Notes:
        A boundary (COD-4-04). A profile removed meanwhile makes the update match no row;
        the write then fails on purpose so the message carries a reference.
    """
    if not n_clicks:
        raise PreventUpdate
    stored_player_id = _stored_player_id(player_id)
    if stored_player_id is None:
        ref = new_reference_code()
        logger.error("Save player refused (ref %s): the player-edit-id store is not an id", ref)
        return "", MESSAGE_SAVE_FAILED.format(ref=ref), no_update
    edited_player_id: int = stored_player_id
    form, problem = parse_player_form(name, birth_date, position, hand)
    if form is None:
        return "", problem or "", no_update
    try:
        taken = player_name_taken(
            data_handler.get_session(), form.display_name, edited_player_id
        )
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Player name check failed (ref %s)", ref)
        return "", MESSAGE_SAVE_FAILED.format(ref=ref), no_update
    if taken:
        return "", MESSAGE_NAME_TAKEN.format(name=form.display_name), no_update

    def work(session: Session) -> None:
        """
        Description:
            Updates the live profile, failing when it is no longer live.

        Parameters:
            session: the write session.

        Return Values:
            None.

        Notes:
            The LookupError rolls the transaction back and gives the user a reference.
        """
        updated = update_player(
            session,
            edited_player_id,
            form.display_name,
            form.birth_date,
            form.position,
            form.dominant_hand,
        )
        if not updated:
            raise LookupError(f"player {edited_player_id} is not a live profile")

    result = run_write(current_user.user_id, work, "save player")
    if not result.ok:
        return "", MESSAGE_SAVE_FAILED.format(ref=result.ref), no_update
    logger.info("Saved player %s", edited_player_id)
    return MESSAGE_SAVED, "", REMOVE_PLAYER_TEXT.format(name=form.display_name)


def remove_player(n_clicks: int | None, player_id: object) -> tuple[object, str]:
    """
    Description:
        The Remove button of the confirmation dialog: removes the profile, which hides
        everything recorded for the player, then returns to the Players list.

    Parameters:
        n_clicks: player-remove-confirm.n_clicks.
        player_id: player-edit-id.data, the edited player. Never a user id (COD-8-01).

    Return Values:
        tuple[object, str]: url.pathname ("/players" on success, else no_update) and
        player-remove-feedback text.

    Notes:
        A boundary (COD-4-04). h_data.fn_soft_delete('player') cascades one deleted_at to
        every row of the player, also in shared practices (Amendment A4, OQ 14 option A).
        Raises PreventUpdate unless Remove was actually clicked: url lives in the shell, so
        Dash also runs this callback when the router inserts the page, and
        prevent_initial_call does not stop that run. The selected player needs no reset:
        current_player_id re-validates it on the next request.
    """
    # The page-insertion run has no click; url is outside the page, so Dash queues it anyway.
    if not n_clicks:
        raise PreventUpdate
    stored_player_id = _stored_player_id(player_id)
    if stored_player_id is None:
        ref = new_reference_code()
        logger.error(
            "Remove player refused (ref %s): the player-edit-id store is not an id", ref
        )
        return no_update, MESSAGE_REMOVE_FAILED.format(ref=ref)
    removed_player_id: int = stored_player_id
    result = run_write(
        current_user.user_id,
        lambda session: soft_delete_player(session, removed_player_id),
        "remove player",
    )
    if not result.ok:
        return no_update, MESSAGE_REMOVE_FAILED.format(ref=result.ref)
    logger.info("Removed player %s (%s rows marked)", removed_player_id, result.value)
    return PLAYERS_PATH, ""


# --- Registration ---

def register_players_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the Players screen's callbacks: Add player, Save, the removal dialog's
        open and Cancel (clientside), and Remove.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        url.pathname is also written by create_practice, so remove_player's Output uses
        allow_duplicate; so does Cancel's Output on the dialog, whose first writer is the
        open callback. Every callback has prevent_initial_call and an n_clicks guard (Dash
        traps, plan §3.3).
    """
    app.callback(
        Output("players-list", "children"),
        Output("players-add-feedback", "children"),
        Output("players-add-name", "value"),
        Input("players-add-submit", "n_clicks"),
        State("players-add-name", "value"),
        prevent_initial_call=True,
    )(add_player)

    app.callback(
        Output("player-edit-saved", "children"),
        Output("player-edit-feedback", "children"),
        Output("player-remove-text", "children"),
        Input("player-edit-save", "n_clicks"),
        State("player-edit-id", "data"),
        State("player-edit-name", "value"),
        State("player-edit-birth-date", "value"),
        State("player-edit-position", "value"),
        State("player-edit-hand", "value"),
        prevent_initial_call=True,
    )(save_player)

    app.clientside_callback(
        ClientsideFunction("hoopster", "open_drawer"),
        Output("player-remove-modal", "opened"),
        Input("player-edit-remove", "n_clicks"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        ClientsideFunction("hoopster", "close_modal"),
        Output("player-remove-modal", "opened", allow_duplicate=True),
        Input("player-remove-cancel", "n_clicks"),
        prevent_initial_call=True,
    )

    app.callback(
        Output("url", "pathname", allow_duplicate=True),
        Output("player-remove-feedback", "children"),
        Input("player-remove-confirm", "n_clicks"),
        State("player-edit-id", "data"),
        prevent_initial_call=True,
    )(remove_player)
