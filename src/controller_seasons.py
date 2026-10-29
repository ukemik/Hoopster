"""Seasons controller of Hoopster: the Seasons and Edit season page builders, the player
picker, and the Add, Save and Delete season callbacks (REQ-018, REQ-005; task
2026-10-23_games, plan item 2.5, D7).

The seasons list and Add season use the selected player (current_player_id(), changed by
seasons-player through set_current_player_id after a live check); the edited season comes from
the URL through the season-edit-id store, and its player from its own row (Context Adaptation
Table, COD-8-01). Every write goes through app_controller.run_write.
"""

import logging
from dataclasses import dataclass
from datetime import date

from dash import ClientsideFunction, Dash, Input, Output, State, no_update
from dash.development.base_component import Component
from dash.exceptions import PreventUpdate
from flask_login import current_user
from sqlalchemy.orm import Session

from config.app_config import SEASON_NAME_MAX_LENGTH
from src import data_handler
from src.app_controller import (
    DeviceClock,
    current_player_id,
    log_failure,
    new_reference_code,
    parse_player_value,
    run_write,
    set_current_player_id,
)
from src.data_players import get_live_player, list_live_players
from src.data_seasons import (
    get_season,
    insert_season,
    list_seasons,
    season_name_taken,
    season_overlap,
    soft_delete_season,
    update_season,
)
from src.layouts import not_found_page
from src.layouts_seasons import (
    SEASONS_PATH,
    season_edit_layout,
    season_list_items,
    seasons_layout,
)

logger = logging.getLogger(__name__)

MESSAGE_NAME = f"Enter a season name of 1 to {SEASON_NAME_MAX_LENGTH} characters."
MESSAGE_DATES = "Choose a start and an end date."
MESSAGE_END_BEFORE_START = "The end date cannot be before the start date."
MESSAGE_NAME_TAKEN = "{player} already has a season named {name}."
MESSAGE_OVERLAP = "These dates overlap the season {name}."
MESSAGE_SAVE_FAILED = "Could not save the season. Reference: {ref}"
MESSAGE_DELETE_FAILED = "Could not delete the season. Reference: {ref}"
MESSAGE_LIST_FAILED = "Could not load the seasons. Reference: {ref}"
MESSAGE_SAVED = "Saved."
MESSAGE_DELETE_QUESTION = (
    "Delete the season {name}? Its games and practices are kept; games assigned to it "
    "automatically move to whichever season covers their date, if any."
)


# --- Page builders (REQ-018) ---

def build_seasons_page(player_id: int, clock: DeviceClock, params: dict[str, str]) -> Component:
    """
    Description:
        Builds the Seasons screen for the selected player.

    Parameters:
        player_id: the selected player from current_player_id() (never None here: the
            router shows the no-player page instead). Never a user id (COD-8-01).
        clock: the device clock (unused).
        params: the route parameters (none).

    Return Values:
        Component: the page.

    Notes:
        Exceptions propagate to the router, which shows the error page with a reference.
    """
    session = data_handler.get_session()
    return seasons_layout(list_live_players(session), player_id, list_seasons(session, player_id))


def build_season_edit_page(
    player_id: int, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds the Edit season page for the season in the URL.

    Parameters:
        player_id: the selected player (unused: the season's player comes from its row).
        clock: the device clock (unused).
        params: {"season_id": "<digits>"} from /seasons/<id>.

    Return Values:
        Component: the page, or the in-app not-found page when the season or its profile is
        not live.

    Notes:
        season-edit-id is written by the layout only after this get_season check.
    """
    season = get_season(data_handler.get_session(), int(params["season_id"]))
    if season is None:
        return not_found_page()
    return season_edit_layout(season)


# --- Form check (pure) ---

@dataclass(frozen=True)
class SeasonForm:
    """
    Description:
        The checked values of a season form.

    Parameters:
        season_name: the trimmed name, 1 to SEASON_NAME_MAX_LENGTH characters.
        start_date: the first day.
        end_date: the last day, not before start_date.

    Return Values:
        Not applicable (data class).

    Notes:
        Built only by parse_season_form.
    """

    season_name: str
    start_date: date
    end_date: date


def _parse_date(value: object) -> date | None:
    """
    Description:
        Reads a date picker's value.

    Parameters:
        value: "YYYY-MM-DD" (a date-time string is cut to its date), or None.

    Return Values:
        date | None: the date, or None when missing or unreadable.

    Notes:
        Pure.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def parse_season_form(
    name: object, start: object, end: object
) -> tuple[SeasonForm | None, str | None]:
    """
    Description:
        Checks a season form in screen order: the name (trimmed, 1 to 100 characters), both
        dates, then that the end is not before the start.

    Parameters:
        name: the name field's value.
        start: the start date picker's value.
        end: the end date picker's value.

    Return Values:
        tuple[SeasonForm | None, str | None]: (the values, None), or (None, the message).

    Notes:
        Pure. Whether the name is free and the dates do not overlap needs the database and
        is checked by the callers.
    """
    season_name = name.strip() if isinstance(name, str) else ""
    if not season_name or len(season_name) > SEASON_NAME_MAX_LENGTH:
        return None, MESSAGE_NAME
    start_date = _parse_date(start)
    end_date = _parse_date(end)
    if start_date is None or end_date is None:
        return None, MESSAGE_DATES
    if end_date < start_date:
        return None, MESSAGE_END_BEFORE_START
    return SeasonForm(season_name, start_date, end_date), None


def _conflict_message(
    session: Session, player_id: int, form: SeasonForm, exclude_id: int | None
) -> str | None:
    """
    Description:
        The database checks of a season form: the name is free for the player and the dates
        overlap no other live season of the player.

    Parameters:
        session: the request session.
        player_id: the season's player.
        form: the checked form.
        exclude_id: the edited season's id, or None when adding.

    Return Values:
        str | None: "{Player} already has a season named {name}." or "These dates overlap
        the season {name}.", or None.

    Notes:
        Read only; the unique index and the no-overlap trigger re-check at write time.
    """
    if season_name_taken(session, player_id, form.season_name, exclude_id):
        player = get_live_player(session, player_id)
        player_name = player["display_name"] if player else "This player"
        return MESSAGE_NAME_TAKEN.format(player=player_name, name=form.season_name)
    overlapping = season_overlap(session, player_id, form.start_date, form.end_date, exclude_id)
    if overlapping is not None:
        return MESSAGE_OVERLAP.format(name=overlapping)
    return None


def _stored_id(value: object) -> int | None:
    """
    Description:
        Reads an id store.

    Parameters:
        value: the store's data.

    Return Values:
        int | None: the id, or None when it is not an int (a bool is not an id).

    Notes:
        Pure.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


# --- Callbacks (REQ-018, REQ-005) ---

def select_seasons_player(value: str | None) -> list[Component] | str:
    """
    Description:
        The Seasons player picker: makes the picked player the selected player and shows
        that player's seasons.

    Parameters:
        value: seasons-player.value, the player id as a string.

    Return Values:
        list[Component] | str: the new children of seasons-list, or a message with a
        reference when the seasons could not be read.

    Notes:
        A boundary (COD-4-04). Raises PreventUpdate for a value that is not a player id,
        and when the player is no longer live (logged as a warning), as select_home_player.
    """
    player_id = parse_player_value(value)
    if player_id is None:
        raise PreventUpdate
    try:
        selected = set_current_player_id(player_id)
        seasons = list_seasons(data_handler.get_session(), player_id) if selected else None
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Seasons player change failed (ref %s)", ref)
        return MESSAGE_LIST_FAILED.format(ref=ref)
    if seasons is None:
        logger.warning("Seasons picker chose player %s, which is not a live player", player_id)
        raise PreventUpdate
    return season_list_items(seasons)


def add_season(
    n_clicks: int | None, name: str | None, start: str | None, end: str | None
) -> tuple[object, str, object]:
    """
    Description:
        Add season: checks the form, the name and the dates, adds the season to the
        selected player and rebuilds the list.

    Parameters:
        n_clicks: seasons-add-submit.n_clicks.
        name: seasons-add-name.value.
        start: seasons-add-start.value.
        end: seasons-add-end.value.

    Return Values:
        tuple[object, str, object]: seasons-list.children (no_update unless added),
        seasons-add-feedback text, seasons-add-name.value ("" after an add, else
        no_update).

    Notes:
        A boundary (COD-4-04). The season's player is the selected player,
        current_player_id() (Context Adaptation Table); the acting user is
        current_user.user_id.
    """
    if not n_clicks:
        raise PreventUpdate
    form, problem = parse_season_form(name, start, end)
    if form is None:
        return no_update, problem or "", no_update
    try:
        session = data_handler.get_session()
        player_id = current_player_id()
        if player_id is None:
            raise LookupError("no live player profile")
        conflict = _conflict_message(session, player_id, form, None)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Season check failed (ref %s)", ref)
        return no_update, MESSAGE_SAVE_FAILED.format(ref=ref), no_update
    if conflict is not None:
        return no_update, conflict, no_update
    season_player_id: int = player_id
    checked: SeasonForm = form

    result = run_write(
        current_user.user_id,
        lambda write_session: insert_season(
            write_session,
            season_player_id,
            checked.season_name,
            checked.start_date,
            checked.end_date,
        ),
        "add season",
    )
    if not result.ok:
        return no_update, MESSAGE_SAVE_FAILED.format(ref=result.ref), no_update
    logger.info("Added season %s for player %s", result.value, season_player_id)
    try:
        seasons = list_seasons(data_handler.get_session(), season_player_id)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Seasons list reload failed (ref %s)", ref)
        return no_update, MESSAGE_LIST_FAILED.format(ref=ref), ""
    return season_list_items(seasons), "", ""


def save_season(
    n_clicks: int | None,
    season_id: object,
    name: str | None,
    start: str | None,
    end: str | None,
) -> tuple[str, str]:
    """
    Description:
        Save on Edit season: the same checks as Add season, excluding the season itself,
        then renames it or changes its dates.

    Parameters:
        n_clicks: season-edit-save.n_clicks.
        season_id: season-edit-id.data (written after get_season).
        name: season-edit-name.value.
        start: season-edit-start.value.
        end: season-edit-end.value.

    Return Values:
        tuple[str, str]: season-edit-saved text ("Saved." on success, else "") and
        season-edit-feedback text.

    Notes:
        A boundary (COD-4-04). The season's player comes from its row (get_season), never
        the selected player. A season deleted meanwhile fails with a reference.
    """
    if not n_clicks:
        raise PreventUpdate
    stored_id = _stored_id(season_id)
    if stored_id is None:
        ref = new_reference_code()
        logger.error("Save season refused (ref %s): season-edit-id is not an id", ref)
        return "", MESSAGE_SAVE_FAILED.format(ref=ref)
    form, problem = parse_season_form(name, start, end)
    if form is None:
        return "", problem or ""
    try:
        session = data_handler.get_session()
        season = get_season(session, stored_id)
        if season is None:
            raise LookupError(f"season {stored_id} is not live")
        conflict = _conflict_message(session, int(season["player_id"]), form, stored_id)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Season check failed (ref %s)", ref)
        return "", MESSAGE_SAVE_FAILED.format(ref=ref)
    if conflict is not None:
        return "", conflict
    edited_id: int = stored_id
    checked: SeasonForm = form

    def work(write_session: Session) -> None:
        """
        Description:
            Updates the live season, failing when it is no longer live.

        Parameters:
            write_session: the write session.

        Return Values:
            None.

        Notes:
            The LookupError rolls back and gives the user a reference.
        """
        if not update_season(
            write_session, edited_id, checked.season_name, checked.start_date, checked.end_date
        ):
            raise LookupError(f"season {edited_id} is not live")

    result = run_write(current_user.user_id, work, "save season")
    if not result.ok:
        return "", MESSAGE_SAVE_FAILED.format(ref=result.ref)
    logger.info("Saved season %s", edited_id)
    return MESSAGE_SAVED, ""


def open_delete_season(n_clicks: int | None, season_id: object) -> tuple[bool, str, str]:
    """
    Description:
        Delete season: opens the confirmation (REQ-005) with the season's current name.

    Parameters:
        n_clicks: season-delete-open.n_clicks.
        season_id: season-edit-id.data.

    Return Values:
        tuple[bool, str, str]: season-delete-modal.opened (True), season-delete-text and
        season-delete-feedback ("" or a message with a reference).

    Notes:
        A boundary (COD-4-04): a failed read still opens the dialog with a reference, and
        the Delete button then fails safely.
    """
    if not n_clicks:
        raise PreventUpdate
    try:
        stored_id = _stored_id(season_id)
        season = (
            get_season(data_handler.get_session(), stored_id) if stored_id is not None else None
        )
        if season is None:
            raise LookupError("season is not live")
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Season delete dialog failed (ref %s)", ref)
        return True, "", MESSAGE_DELETE_FAILED.format(ref=ref)
    return True, MESSAGE_DELETE_QUESTION.format(name=season["season_name"]), ""


def delete_season(n_clicks: int | None, season_id: object) -> tuple[object, str]:
    """
    Description:
        The Delete button of the confirmation: deletes the season through
        h_data.fn_soft_delete('season') and returns to the Seasons list.

    Parameters:
        n_clicks: season-delete-confirm.n_clicks.
        season_id: season-edit-id.data.

    Return Values:
        tuple[object, str]: url.pathname ("/seasons" on success, else no_update) and
        season-delete-feedback text.

    Notes:
        A boundary (COD-4-04). Raises PreventUpdate without a click: url lives in the shell,
        so Dash also runs this when the router inserts the page.
    """
    if not n_clicks:
        raise PreventUpdate
    stored_id = _stored_id(season_id)
    if stored_id is None:
        ref = new_reference_code()
        logger.error("Delete season refused (ref %s): season-edit-id is not an id", ref)
        return no_update, MESSAGE_DELETE_FAILED.format(ref=ref)
    deleted_id: int = stored_id
    result = run_write(
        current_user.user_id,
        lambda write_session: soft_delete_season(write_session, deleted_id),
        "delete season",
    )
    if not result.ok:
        return no_update, MESSAGE_DELETE_FAILED.format(ref=result.ref)
    logger.info("Deleted season %s", deleted_id)
    return SEASONS_PATH, ""


# --- Registration ---

def register_seasons_callbacks(app: Dash) -> None:
    """
    Description:
        Registers the Seasons callbacks: the player picker, Add season, Save, and the delete
        dialog's open, Cancel (clientside) and Delete.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        url.pathname is written by several callbacks, so delete_season uses
        allow_duplicate and ignores runs without a click (router insertion trap,
        notes_for_agents §8). The dialog's opened and feedback are first written by
        open_delete_season; Cancel and Delete use allow_duplicate.
    """
    app.callback(
        Output("seasons-list", "children"),
        Input("seasons-player", "value"),
        prevent_initial_call=True,
    )(select_seasons_player)

    app.callback(
        Output("seasons-list", "children", allow_duplicate=True),
        Output("seasons-add-feedback", "children"),
        Output("seasons-add-name", "value"),
        Input("seasons-add-submit", "n_clicks"),
        State("seasons-add-name", "value"),
        State("seasons-add-start", "value"),
        State("seasons-add-end", "value"),
        prevent_initial_call=True,
    )(add_season)

    app.callback(
        Output("season-edit-saved", "children"),
        Output("season-edit-feedback", "children"),
        Input("season-edit-save", "n_clicks"),
        State("season-edit-id", "data"),
        State("season-edit-name", "value"),
        State("season-edit-start", "value"),
        State("season-edit-end", "value"),
        prevent_initial_call=True,
    )(save_season)

    app.callback(
        Output("season-delete-modal", "opened"),
        Output("season-delete-text", "children"),
        Output("season-delete-feedback", "children"),
        Input("season-delete-open", "n_clicks"),
        State("season-edit-id", "data"),
        prevent_initial_call=True,
    )(open_delete_season)

    app.clientside_callback(
        ClientsideFunction("hoopster", "close_modal"),
        Output("season-delete-modal", "opened", allow_duplicate=True),
        Input("season-delete-cancel", "n_clicks"),
        prevent_initial_call=True,
    )

    app.callback(
        Output("url", "pathname", allow_duplicate=True),
        Output("season-delete-feedback", "children", allow_duplicate=True),
        Input("season-delete-confirm", "n_clicks"),
        State("season-edit-id", "data"),
        prevent_initial_call=True,
    )(delete_season)
