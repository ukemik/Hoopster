"""Practice UI for Hoopster: the New practice form (with template and details), the practice
court screen with its player picker, and the practice overview (practice_drills 2b).

REQ-025, REQ-051, REQ-053, REQ-072, REQ-074, REQ-081, REQ-082. The court figure, the entry
controls, dialogs and stores are the shared entry panel of src/layouts_entry.py (REQ-007 to
REQ-014, REQ-058). The court screen's stores live in this page, so they are created when the
screen opens and discarded when it is left (plan §3.5, §3.6).
"""

import logging
from datetime import date

import dash_mantine_components as dmc
from dash import dcc, html
from dash.development.base_component import Component

from config.app_config import COURT_PLAYER_BUTTONS_MAX
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
from src.layouts_entry import entry_panel
from src.layouts_sets import SET_KIND_LABELS

logger = logging.getLogger(__name__)

# Practice detail limits (ck_practice_sessions_duration, _location, _notes in sql/h_data.sql).
DURATION_MIN = 1
DURATION_MAX = 600
LOCATION_MAX_LENGTH = 100
NOTES_MAX_LENGTH = 4000
TEMPLATE_PLACEHOLDER = "No template - free shooting"


# --- New practice form (REQ-025, REQ-055) ---

PRACTICE_DATE_DISPLAY_FORMAT = "ddd D MMM YYYY"


def _detail_fields(
    prefix: str, duration: object, location: object, notes: object
) -> list[Component]:
    """
    Description:
        The three practice detail fields (REQ-025): duration in minutes, location and notes.

    Parameters:
        prefix: the id prefix, "practice-new" (New practice) or "ov" (overview).
        duration: the current duration (int) or None / "" for empty.
        location: the current location or None.
        notes: the current notes or None.

    Return Values:
        list[Component]: [<prefix>-duration, <prefix>-location, <prefix>-notes].

    Notes:
        The controller validates the limits; the inputs only guide typing.
    """
    return [
        dmc.NumberInput(
            id=f"{prefix}-duration",
            label="Duration (minutes, optional)",
            value=duration if isinstance(duration, int) else "",
            min=DURATION_MIN,
            max=DURATION_MAX,
            allowDecimal=False,
            allowNegative=False,
            clampBehavior="none",
            size=CONTROL_SIZE,
            hideControls=True,
            inputProps={"inputMode": "numeric"},
        ),
        dmc.TextInput(
            id=f"{prefix}-location",
            label="Location (optional)",
            value=str(location) if location else "",
            inputProps={"maxLength": LOCATION_MAX_LENGTH},
            size=CONTROL_SIZE,
        ),
        dmc.Textarea(
            id=f"{prefix}-notes",
            label="Notes (optional)",
            value=str(notes) if notes else "",
            inputProps={"maxLength": NOTES_MAX_LENGTH},
            autosize=True,
            minRows=2,
            size=CONTROL_SIZE,
        ),
    ]


def template_options(templates: list[dict]) -> list[dict[str, str]]:
    """
    Description:
        The New practice template picker's options: "Name (n sets)".

    Parameters:
        templates: rows of data_practice.list_selectable_templates.

    Return Values:
        list[dict[str, str]]: {"value": str(practice_template_id), "label"} per template.

    Notes:
        Pure. The value is the template LINEAGE id.
    """
    options = []
    for row in templates:
        count = int(row["set_count"] or 0)
        noun = "set" if count == 1 else "sets"
        options.append(
            {
                "value": str(int(row["practice_template_id"])),
                "label": f"{row['template_name']} ({count} {noun})",
            }
        )
    return options


def practice_new_layout(
    practice_types: list[dict],
    default_date: str | None,
    players: list[dict],
    default_player_id: int,
    templates: list[dict],
) -> Component:
    """
    Description:
        The New practice form: type, date, the players taking part, an optional practice
        template, the optional details (duration, location, notes), and Start practice.

    Parameters:
        practice_types: rows of data_practice.list_practice_types: {"practice_type_code",
            "label"}, in display order.
        default_date: the device's date "YYYY-MM-DD" from the device-clock store, or None
            when the clock is unknown (the field then starts empty).
        players: rows of data_players.list_live_players, in display order (the players
            picker's options).
        default_player_id: the selected player (current_player_id()), preselected in the
            players picker. Only a default; the practice's participants are the picker's
            value (Context Adaptation Table).
        templates: rows of data_practice.list_selectable_templates (current templates).

    Return Values:
        Component: the page.

    Notes:
        The date defaults to the device's date, never the server's (REQ-055). The players
        picker's value is a list of player ids as strings, in the order they were picked.
        Without a template the practice opens on the court (D2).
    """
    type_options = [
        {"value": str(row["practice_type_code"]), "label": str(row["label"])}
        for row in practice_types
    ]
    return page_skeleton(
        "New practice",
        [
            dmc.Select(
                id="practice-new-type",
                label="Type",
                data=type_options,
                size=CONTROL_SIZE,
                allowDeselect=False,
            ),
            dmc.DatePickerInput(
                id="practice-new-date",
                label="Date",
                value=default_date,
                valueFormat=PRACTICE_DATE_DISPLAY_FORMAT,
                size=CONTROL_SIZE,
            ),
            dmc.MultiSelect(
                id="practice-new-players",
                label="Players",
                data=player_options(players),
                value=[str(default_player_id)],
                hidePickedOptions=True,
                clearable=False,
                size=CONTROL_SIZE,
            ),
            dmc.Select(
                id="practice-new-template",
                label="Practice template",
                data=template_options(templates),
                value=None,
                placeholder=TEMPLATE_PLACEHOLDER,
                clearable=True,
                searchable=True,
                size=CONTROL_SIZE,
            ),
            *_detail_fields("practice-new", None, None, None),
            primary_button("practice-new-submit", "Start practice"),
            form_message("practice-new-feedback"),
        ],
    )


# --- Court entry screen (REQ-010, REQ-044, REQ-058, REQ-074; plan §3.6) ---

COURT_DATE_FORMAT = "%a %d %b %Y"


def _court_header(practice: dict) -> dmc.Text:
    """
    Description:
        The court screen's header line: "{practice type} · {date}".

    Parameters:
        practice: data_practice.get_practice row: {"practice_session_id", "session_date",
            "practice_type_code", "practice_type_label"}.

    Return Values:
        dmc.Text: the header, id "court-header".

    Notes:
        The date is the calendar date as stored (REQ-055).
    """
    session_date = practice["session_date"]
    date_text = (
        session_date.strftime(COURT_DATE_FORMAT)
        if isinstance(session_date, date)
        else str(session_date)
    )
    return dmc.Text(f"{practice['practice_type_label']} · {date_text}", id="court-header")


def court_player_picker(
    participants: list[dict], default_player_id: int, element_id: str = "court-player"
) -> Component:
    """
    Description:
        The court's entry-player picker (REQ-074): buttons when the practice has at most
        COURT_PLAYER_BUTTONS_MAX players, a drop-down list otherwise. Both have the id
        element_id ("court-player" by default).

    Parameters:
        participants: rows of data_practice.list_practice_participants: {"player_id",
            "display_name"}, in participant-row order; at least one row.
        default_player_id: the participant selected when the picker appears: the selected
            player if a live participant, else the first participant (decided by the
            builder or callback).
        element_id: "court-player" (court and shooting drills) or "drill-player" (the run
            page's value and outcome panels, D4).

    Return Values:
        Component: a dmc.SegmentedControl or a dmc.Select, value str(default_player_id).

    Notes:
        The value is the entry player of every court command or drill write (Context
        Adaptation Table); the server resolves the participant row itself. The Add and
        Remove player callbacks rebuild the court picker inside court-player-area.
    """
    options = player_options(participants)
    value = str(default_player_id)
    if len(options) <= COURT_PLAYER_BUTTONS_MAX:
        return dmc.SegmentedControl(
            id=element_id,
            data=options,
            value=value,
            fullWidth=True,
            size=CONTROL_SIZE,
            className="hoopster-player-picker",
        )
    return dmc.Select(
        id=element_id,
        label="Player",
        data=options,
        value=value,
        allowDeselect=False,
        size=CONTROL_SIZE,
    )


def court_player_controls() -> list[Component]:
    """
    Description:
        The "+ Add player" and "Remove player" buttons under the court picker, with their
        two dialogs (Amendment A2).

    Parameters:
        None.

    Return Values:
        list[Component]: [the button row, court-add-player-modal, court-remove-player-modal];
        both dialogs closed.

    Notes:
        The add dialog's list (court-add-player-select) starts empty; its callback fills it
        with data_practice.list_addable_players when the dialog opens. The remove dialog's
        question (court-remove-player-text) is likewise written when it opens.
    """
    buttons = dmc.Group(
        [
            secondary_button("court-add-player-open", "+ Add player"),
            secondary_button("court-remove-player-open", "Remove player"),
        ],
        grow=True,
    )
    add_modal = dmc.Modal(
        dmc.Stack(
            [
                dmc.Select(
                    id="court-add-player-select",
                    label="Player",
                    data=[],
                    allowDeselect=False,
                    size=CONTROL_SIZE,
                ),
                primary_button("court-add-player-confirm", "Add"),
                secondary_button("court-add-player-cancel", "Cancel"),
                form_message("court-add-player-feedback"),
            ]
        ),
        id="court-add-player-modal",
        title="Add a player",
        centered=True,
        opened=False,
    )
    remove_modal = dmc.Modal(
        dmc.Stack(
            [
                dmc.Text("", id="court-remove-player-text"),
                primary_button("court-remove-player-confirm", "Remove", color="red"),
                secondary_button("court-remove-player-cancel", "Cancel"),
                form_message("court-remove-player-feedback"),
            ]
        ),
        id="court-remove-player-modal",
        title="Remove player?",
        centered=True,
        opened=False,
    )
    return [buttons, add_modal, remove_modal]


def court_no_players_layout(practice: dict) -> Component:
    """
    Description:
        The court screen of a live practice without a live participant, for example after
        every participant's profile was removed (Amendment A4).

    Parameters:
        practice: data_practice.get_practice row (see _court_header).

    Return Values:
        Component: the page: header and "This practice has no players."

    Notes:
        No entry controls and no stores, so nothing can be recorded.
    """
    return page_skeleton(
        "Practice",
        [
            _court_header(practice),
            dmc.Text("This practice has no players.", id="court-no-players"),
        ],
    )


# A participant with nothing entered starts at 0 in court-base.
EMPTY_PARTICIPANT_TOTALS: dict[str, int] = {
    "makes": 0,
    "attempts": 0,
    "ft_makes": 0,
    "ft_attempts": 0,
}


def court_layout(
    practice: dict,
    participants: list[dict],
    default_player_id: int,
    totals: dict[str, dict[str, int]],
    spots: list[dict],
    shot_types: list[dict],
) -> Component:
    """
    Description:
        The practice court screen: header, player picker with Add and Remove player, then
        the shared entry panel (court view toggle, modes, tallies, court, shot details,
        free throws, Undo / save status / Retry, Entries, dialogs and stores).

    Parameters:
        practice: data_practice.get_practice row: {"practice_session_id", "session_date",
            "practice_type_code", "practice_type_label"}, already confirmed live.
        participants: rows of data_practice.list_practice_participants (live participant
            rows with live profiles), in participant-row order; at least one row (else the
            builder shows court_no_players_layout).
        default_player_id: the participant selected first: the selected player if a live
            participant, else the first participant.
        totals: data_entry.get_entry_totals of the practice: {str(player_id): {"makes",
            "attempts", "ft_makes", "ft_attempts"}}; a participant missing from it starts at
            0.
        spots: rows of data_entry.list_active_court_spots.
        shot_types: rows of data_entry.list_shot_types.

    Return Values:
        Component: the page.

    Notes:
        The entry parent is passed to entry_panel as a plain dict {"kind": "practice", "id"}
        (layouts never import data_*). All stores belong to this page, so the undo stack
        covers exactly the entries made since the screen was opened (REQ-010). court-base is
        patched by the Add and Remove player callbacks and replaced by the server after
        every save, undo, edit and delete (plan D8). court-overview-link opens the practice
        overview, so a shooting practice can gain drills later (practice_drills plan §3.5).
    """
    base: dict[str, dict[str, int]] = {}
    for participant in participants:
        key = str(int(participant["player_id"]))
        base[key] = dict(totals.get(key, EMPTY_PARTICIPANT_TOTALS))
    practice_session_id = int(practice["practice_session_id"])
    parent = {"kind": "practice", "id": practice_session_id}

    return page_skeleton(
        "Practice",
        [
            _court_header(practice),
            html.Div(
                nav_link(
                    "court-overview-link",
                    "Practice overview",
                    f"/practice/{practice_session_id}",
                ),
                className="hoopster-link-button",
            ),
            html.Div(
                court_player_picker(participants, default_player_id), id="court-player-area"
            ),
            *court_player_controls(),
            *entry_panel(parent, base, default_player_id, spots, shot_types),
        ],
    )


# --- Practice overview (practice_drills 2b; REQ-025, REQ-051, REQ-053, REQ-081, REQ-082) ---

OVERVIEW_EMPTY_TEXT = "No drills yet. Add a drill set or a single drill below."
SET_WITHOUT_DRILLS_TEXT = "No drills in this set."
RUN_AREA_CLASS = "hoopster-link-button hoopster-link-primary"
OVERVIEW_BLOCK_CLASS = "hoopster-overview-block"
DETAIL_SEPARATOR = " · "


def run_area_class(has_drills: bool) -> str:
    """
    Description:
        The className of the overview's "Run practice" link wrapper: shown only when the
        practice has at least one drill.

    Parameters:
        has_drills: whether the practice has a live drill.

    Return Values:
        str: the wrapper classes, with "hoopster-hidden" when there is no drill.

    Notes:
        Pure. Used by the builder and by every callback that re-renders ov-body.
    """
    return RUN_AREA_CLASS if has_drills else f"{RUN_AREA_CLASS} hoopster-hidden"


def set_options(sets: list[dict]) -> list[dict[str, str]]:
    """
    Description:
        The overview's Add drill set options: "Name · Kind · v<n>".

    Parameters:
        sets: rows of data_practice.list_selectable_sets.

    Return Values:
        list[dict[str, str]]: {"value": str(drill_set_id), "label"} per set.

    Notes:
        Pure. The value is the set LINEAGE id.
    """
    return [
        {
            "value": str(int(row["drill_set_id"])),
            "label": DETAIL_SEPARATOR.join(
                [
                    str(row["set_name"]),
                    SET_KIND_LABELS.get(str(row["set_kind"]), str(row["set_kind"])),
                    f"v{row['version_no']}",
                ]
            ),
        }
        for row in sets
    ]


def drill_options(drills: list[dict]) -> list[dict[str, str]]:
    """
    Description:
        The overview's Add drill options: "Name · Result type".

    Parameters:
        drills: rows of data_practice.list_selectable_drills.

    Return Values:
        list[dict[str, str]]: {"value": str(drill_id), "label"} per drill.

    Notes:
        Pure. The value is the drill LINEAGE id.
    """
    return [
        {
            "value": str(int(row["drill_id"])),
            "label": f"{row['drill_name']}{DETAIL_SEPARATOR}{row['result_type_label']}",
        }
        for row in drills
    ]


def _overview_drill_row(
    practice_session_id: int, drill: dict, participant_count: int
) -> Component:
    """
    Description:
        One drill of the overview: name, result-type badge, "Done by x of y players", a link
        to run it and Remove drill.

    Parameters:
        practice_session_id: the practice.
        drill: a data_practice.list_practice_drills row.
        participant_count: the practice's live participants (y).

    Return Values:
        Component: the row, with the link "ov-open-<pd>" and the button {"type":
        "ov-remove-drill", "index": practice_drill_id}.

    Notes:
        None.
    """
    practice_drill_id = int(drill["practice_drill_id"])
    return dmc.Paper(
        dmc.Stack(
            [
                dmc.Group(
                    [
                        dmc.Text(str(drill["drill_name"]), fw=600),
                        dmc.Badge(str(drill["result_type_label"]), variant="light"),
                    ],
                    gap="xs",
                ),
                dmc.Text(
                    f"Done by {int(drill['players_done'] or 0)} of {participant_count} players",
                    size="sm",
                    c="dimmed",
                ),
                html.Div(
                    nav_link(
                        f"ov-open-{practice_drill_id}",
                        "Run this drill",
                        f"/practice/{practice_session_id}/run/{practice_drill_id}",
                    ),
                    className="hoopster-link-button",
                ),
                secondary_button(
                    {"type": "ov-remove-drill", "index": practice_drill_id},
                    "Remove drill",
                    color="red",
                ),
            ],
            gap="xs",
        ),
        withBorder=True,
        p="sm",
    )


def overview_body(
    practice_session_id: int,
    sets: list[dict],
    drills: list[dict],
    participant_count: int,
) -> list[Component]:
    """
    Description:
        The overview body in run order (plan §3.5): each placed set ("Set k · Name (vN) ·
        Kind", its draw notice in a yellow alert, its drills and Remove set) and each ad-hoc
        drill ("Single drill"), interleaved by practice position.

    Parameters:
        practice_session_id: the practice (for the run links).
        sets: rows of data_practice.list_placed_sets.
        drills: rows of data_practice.list_practice_drills, in run order.
        participant_count: the practice's live participants, for "Done by x of y".

    Return Values:
        list[Component]: the children of ov-body; "No drills yet ..." when there is nothing.

    Notes:
        Pure. A placed set without drills (short draw) is shown with its notice. Remove set
        buttons have the id {"type": "ov-remove-set", "index": practice_drill_set_id}.
    """
    blocks: list[tuple[int, int, Component]] = []
    for set_number, placed in enumerate(sets, start=1):
        placement_id = int(placed["practice_drill_set_id"])
        kind = SET_KIND_LABELS.get(str(placed["set_kind"]), str(placed["set_kind"]))
        title = (
            f"Set {set_number}{DETAIL_SEPARATOR}{placed['set_name']} "
            f"(v{placed['version_no']}){DETAIL_SEPARATOR}{kind}"
        )
        children: list[Component] = [dmc.Text(title, fw=700)]
        if placed.get("draw_notice"):
            children.append(dmc.Alert(str(placed["draw_notice"]), color="yellow"))
        set_drills = [
            _overview_drill_row(practice_session_id, drill, participant_count)
            for drill in drills
            if drill["practice_drill_set_id"] == placement_id
        ]
        children.extend(set_drills or [dmc.Text(SET_WITHOUT_DRILLS_TEXT, c="dimmed")])
        children.append(
            secondary_button(
                {"type": "ov-remove-set", "index": placement_id}, "Remove set", color="red"
            )
        )
        blocks.append(
            (
                int(placed["practice_position"]),
                0,
                html.Div(dmc.Stack(children, gap="xs"), className=OVERVIEW_BLOCK_CLASS),
            )
        )
    for drill in drills:
        if drill["practice_drill_set_id"] is not None:
            continue
        block = html.Div(
            dmc.Stack(
                [
                    dmc.Text("Single drill", fw=700),
                    _overview_drill_row(practice_session_id, drill, participant_count),
                ],
                gap="xs",
            ),
            className=OVERVIEW_BLOCK_CLASS,
        )
        blocks.append((int(drill["run_position"]), 1, block))
    if not blocks:
        return [dmc.Text(OVERVIEW_EMPTY_TEXT, id="ov-empty", c="dimmed")]
    blocks.sort(key=lambda item: (item[0], item[1]))
    return [block for _, _, block in blocks]


def _overview_header(practice: dict) -> list[Component]:
    """
    Description:
        The overview header: "{type} · {date}", "From template: Name (vN)" when started from
        a template, and the participants' names.

    Parameters:
        practice: data_practice.get_practice_overview row plus "participants" (rows of
            list_practice_participants).

    Return Values:
        list[Component]: ov-header, ov-template (only with a template) and ov-players.

    Notes:
        The date is the calendar date as stored (REQ-055).
    """
    header: list[Component] = [_court_header(practice)]
    header[0].id = "ov-header"
    if practice.get("template_name"):
        header.append(
            dmc.Text(
                f"From template: {practice['template_name']} "
                f"(v{practice['template_version_no']})",
                id="ov-template",
            )
        )
    names = ", ".join(str(row["display_name"]) for row in practice.get("participants") or [])
    header.append(dmc.Text(f"Players: {names}" if names else "No players", id="ov-players"))
    return header


def practice_overview_layout(
    practice: dict, sets: list[dict], drills: list[dict], options: dict
) -> Component:
    """
    Description:
        The practice overview /practice/<id> (plan §3.5): header, Run practice (when it has
        drills) and Shoot on court, the body in run order with Remove drill / Remove set,
        Add drill set and Add drill, and the details form.

    Parameters:
        practice: data_practice.get_practice_overview row plus "participants" (rows of
            list_practice_participants), already confirmed live.
        sets: rows of data_practice.list_placed_sets.
        drills: rows of data_practice.list_practice_drills.
        options: {"sets": list_selectable_sets rows, "drills": list_selectable_drills rows}.

    Return Values:
        Component: the page.

    Notes:
        ov-context {"practice_session_id"} is written here after the builder's live check;
        every write re-checks it in SQL. ov-remove-target {"kind": "drill" | "set", "id"} is
        written only by the open-remove callback.
    """
    practice_session_id = int(practice["practice_session_id"])
    participant_count = len(practice.get("participants") or [])
    return page_skeleton(
        "Practice overview",
        [
            *_overview_header(practice),
            html.Div(
                nav_link("ov-run", "Run practice", f"/practice/{practice_session_id}/run"),
                id="ov-run-area",
                className=run_area_class(bool(drills)),
            ),
            html.Div(
                nav_link("ov-court", "Shoot on court", f"/practice/{practice_session_id}/court"),
                className="hoopster-link-button",
            ),
            html.Div(
                overview_body(practice_session_id, sets, drills, participant_count),
                id="ov-body",
            ),
            dmc.Title("Add to this practice", order=4),
            dmc.Select(
                id="ov-add-set",
                label="Drill set",
                data=set_options(options.get("sets") or []),
                value=None,
                searchable=True,
                clearable=True,
                size=CONTROL_SIZE,
            ),
            secondary_button("ov-add-set-go", "Add drill set"),
            dmc.Select(
                id="ov-add-drill",
                label="Single drill",
                data=drill_options(options.get("drills") or []),
                value=None,
                searchable=True,
                clearable=True,
                size=CONTROL_SIZE,
            ),
            secondary_button("ov-add-drill-go", "Add drill"),
            form_message("ov-add-feedback"),
            dmc.Title("Details", order=4),
            *_detail_fields(
                "ov", practice.get("duration_minutes"), practice.get("location"),
                practice.get("notes"),
            ),
            primary_button("ov-save", "Save details"),
            dmc.Text("", id="ov-details-saved", c="green"),
            form_message("ov-details-feedback"),
            confirm_modal("ov-remove", "Remove from practice?", "Remove"),
            dcc.Store(
                id="ov-context",
                storage_type="memory",
                data={"practice_session_id": practice_session_id},
            ),
            dcc.Store(id="ov-remove-target", storage_type="memory"),
        ],
    )
