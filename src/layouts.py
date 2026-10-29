"""Shared UI for Hoopster: the app shell, the bottom bar with its drawers (the More drawer links
Goals, Measurements, the drill library, Games and Settings in a two-column grid), the
centralised wrappers (COD-5-01) including the confirmation dialog
confirm_modal, and the generic pages (coming soon, not found, no player, error).

REQ-045 (phone first: every control at least 44 px), REQ-066 (fixed bottom bar). Layout
modules import nothing from controllers; callbacks are registered elsewhere.
"""

import logging

import dash_mantine_components as dmc
from dash import dcc, html
from dash.development.base_component import Component

from config.app_config import MANTINE_THEME, NAV_ITEMS

logger = logging.getLogger(__name__)

# dmc size "lg" is 50 px tall and "xl" 60 px, both above the 44 px touch minimum (REQ-045).
CONTROL_SIZE = "lg"
ENTRY_CONTROL_SIZE = "xl"

STATUS_READY_TEXT = "Ready"
STATUS_READY_COLOR = "gray"

# Labels of the bottom-bar items, keyed by element id (config NAV_ITEMS keeps the order).
NAV_LABELS: dict[str, str] = dict(NAV_ITEMS)

# The drill-library links of the More drawer, above Games: (element id, label, path).
MORE_LIBRARY_LINKS: tuple[tuple[str, str, str], ...] = (
    ("nav-more-drills", "Drills", "/drills"),
    ("nav-more-drill-sets", "Drill sets", "/drill-sets"),
    ("nav-more-templates", "Practice templates", "/templates"),
    ("nav-more-drill-types", "Drill types", "/drill-types"),
)

# The game links of the More drawer, directly above Settings (task 2026-10-23_games). Seasons
# and Players moved into Settings (task 2026-10-23_settings_hub, D1).
MORE_GAME_LINKS: tuple[tuple[str, str, str], ...] = (
    ("nav-more-games", "Games", "/games"),
)

# The player links of the More drawer, first in its grid (task 2026-10-23_goals_measurements,
# D1).
MORE_PLAYER_LINKS: tuple[tuple[str, str, str], ...] = (
    ("nav-more-goals", "Goals", "/goals"),
    ("nav-more-measurements", "Measurements", "/measurements"),
)
# The More drawer's link grid: two columns, read row by row (D1).
MORE_GRID_COLUMNS = 2


# --- Wrappers: buttons, links, messages, badges (COD-5-01) ---

def _standard_button(
    element_id: str | dict,
    label: str,
    variant: str,
    disabled: bool,
    color: str | None,
) -> dmc.Button:
    """
    Description:
        Builds a full-width, touch-sized dmc button; shared by primary_button and
        secondary_button.

    Parameters:
        element_id: the stable element id (a string, or a pattern-matching dict).
        label: the button text.
        variant: the dmc variant ("filled" or "default").
        disabled: True renders the button disabled.
        color: a Mantine colour name, or None for the theme's primary colour.

    Return Values:
        dmc.Button: the button.

    Notes:
        color is passed only when set, so the theme default applies otherwise.
    """
    options: dict[str, object] = {}
    if color is not None:
        options["color"] = color
    return dmc.Button(
        label,
        id=element_id,
        variant=variant,
        size=CONTROL_SIZE,
        fullWidth=True,
        disabled=disabled,
        className="hoopster-btn",
        **options,
    )


def primary_button(
    element_id: str | dict, label: str, *, disabled: bool = False, color: str | None = None
) -> dmc.Button:
    """
    Description:
        The main action button of a screen: filled, full width, 50 px tall.

    Parameters:
        element_id: the stable element id (a string, or a pattern-matching dict).
        label: the button text.
        disabled: True renders the button disabled.
        color: a Mantine colour name, or None for the theme's primary colour (orange).

    Return Values:
        dmc.Button: the button.

    Notes:
        None.
    """
    return _standard_button(element_id, label, "filled", disabled, color)


def secondary_button(
    element_id: str | dict, label: str, *, disabled: bool = False, color: str | None = None
) -> dmc.Button:
    """
    Description:
        A secondary action button: outlined ("default" variant), full width, 50 px tall.

    Parameters:
        element_id: the stable element id (a string, or a pattern-matching dict).
        label: the button text.
        disabled: True renders the button disabled.
        color: a Mantine colour name, or None for the default.

    Return Values:
        dmc.Button: the button.

    Notes:
        None.
    """
    return _standard_button(element_id, label, "default", disabled, color)


def entry_button(element_id: str | dict, label: str, color: str) -> dmc.Button:
    """
    Description:
        A large shot-entry button (Make, Miss): 60 px tall, full width, disabled until a
        court spot is selected.

    Parameters:
        element_id: the stable element id (a string, or a pattern-matching dict such as the
            live game's {"type": "game-stat-add", "stat": code}).
        label: the button text.
        color: a Mantine colour name ("green" for Make, "red" for Miss).

    Return Values:
        dmc.Button: the button, initially disabled.

    Notes:
        The clientside select_spot callback enables it when a spot is tapped.
    """
    return dmc.Button(
        label,
        id=element_id,
        color=color,
        size=ENTRY_CONTROL_SIZE,
        fullWidth=True,
        disabled=True,
        className="hoopster-btn hoopster-entry-btn",
    )


def nav_link(element_id: str, label: str, href: str) -> dcc.Link:
    """
    Description:
        An in-app navigation link (no page reload), styled as a touch-sized nav item.

    Parameters:
        element_id: the stable element id.
        label: the link text.
        href: the in-app path, e.g. "/practice/new".

    Return Values:
        dcc.Link: the link.

    Notes:
        Wrap it in a div with classes "hoopster-link-button hoopster-link-primary" to make it
        look like a primary button (assets/hoopster.css).
    """
    return dcc.Link(label, id=element_id, href=href, className="hoopster-nav-item")


def form_message(element_id: str) -> dmc.Text:
    """
    Description:
        An empty feedback line under a form, filled by its callback with an error message.

    Parameters:
        element_id: the stable element id.

    Return Values:
        dmc.Text: the red, initially empty, text line.

    Notes:
        None.
    """
    return dmc.Text("", id=element_id, c="red")


def save_status_badge(element_id: str) -> dmc.Badge:
    """
    Description:
        The save-status badge of an entry screen (REQ-058), initially "Ready".

    Parameters:
        element_id: the stable element id.

    Return Values:
        dmc.Badge: the badge.

    Notes:
        Its text and colour are driven by the clientside court_render callback.
    """
    return dmc.Badge(
        STATUS_READY_TEXT, id=element_id, size=CONTROL_SIZE, color=STATUS_READY_COLOR
    )


def confirm_modal(
    prefix: str, title: str, confirm_label: str, color: str = "red", text: str = ""
) -> dmc.Modal:
    """
    Description:
        A confirmation dialog (REQ-005): a question, a confirm button, Cancel and a feedback
        line.

    Parameters:
        prefix: the id prefix; the ids are "{prefix}-modal", "{prefix}-text",
            "{prefix}-confirm", "{prefix}-cancel" and "{prefix}-feedback".
        title: the dialog title, e.g. "Remove drill?".
        confirm_label: the confirm button's text, e.g. "Remove".
        color: the confirm button's Mantine colour (red by default).
        text: a fixed question, for a dialog opened clientside (settings_hub); "" (the
            default) when the opening callback fills it.

    Return Values:
        dmc.Modal: the dialog, closed, with the given (by default empty) question text.

    Notes:
        The opening callback fills "{prefix}-text" unless the question is fixed. Open it
        with the clientside hoopster.open_drawer (or a server callback for pattern-matching
        buttons) and close it with hoopster.close_modal on "{prefix}-cancel" (plan §3.8.7).
    """
    return dmc.Modal(
        dmc.Stack(
            [
                dmc.Text(text, id=f"{prefix}-text"),
                primary_button(f"{prefix}-confirm", confirm_label, color=color),
                secondary_button(f"{prefix}-cancel", "Cancel"),
                form_message(f"{prefix}-feedback"),
            ]
        ),
        id=f"{prefix}-modal",
        title=title,
        centered=True,
        opened=False,
    )


# --- Wrappers: stat tile and audit lines (task 2026-10-23_games; REQ-004, REQ-064) ---

STAT_TILE_CLASS = "hoopster-stat-tile"
STAT_LABEL_CLASS = "hoopster-stat-label"


def _stat_popover(stat: dict, target: Component) -> Component:
    """
    Description:
        The tap-to-explain popover (REQ-064) around a given target: the stat's label, its
        formula (when it has one) and its plain-language explanation. Shared by stat_tile
        and stat_label.

    Parameters:
        stat: an h_ref.stat_definitions row: {"stat_code", "label", "formula_text" (or
            None), "explanation"}.
        target: the tappable component that opens the popover.

    Return Values:
        Component: a dmc.Popover (bottom, with arrow) holding the target and the dropdown.

    Notes:
        Uncontrolled: Mantine opens it on the target's click and closes it on an outside
        click, so no callback is needed.
    """
    code = str(stat.get("stat_code", ""))
    formula = stat.get("formula_text")
    dropdown: list[Component] = [dmc.Text(str(stat.get("label", code)), fw=700)]
    if formula:
        dropdown.append(dmc.Text(str(formula), size="sm", c="dimmed"))
    dropdown.append(dmc.Text(str(stat.get("explanation", "")), size="sm"))
    return dmc.Popover(
        [
            dmc.PopoverTarget(target),
            dmc.PopoverDropdown(dmc.Stack(dropdown, gap=4), maw=320),
        ],
        position="bottom",
        withArrow=True,
        shadow="md",
    )


def stat_label(stat: dict, scope: str) -> Component:
    """
    Description:
        A tappable stat name (REQ-064) for a table row label: shows only the stat's
        abbreviation; a tap opens the same popover as stat_tile.

    Parameters:
        stat: an h_ref.stat_definitions row: {"stat_code", "label", "abbreviation",
            "formula_text" (or None), "explanation"}.
        scope: the screen part the label belongs to (e.g. "dsh-season", "dsh-cmp"), so one
            stat can appear in several tables with unique ids.

    Return Values:
        Component: a dmc.Popover whose target is a button with id {"type": "stat-label",
        "scope": scope, "stat": stat_code} (class hoopster-stat-label, at least 44 px tall).

    Notes:
        Task 2026-10-23_dashboards (D11).
    """
    code = str(stat.get("stat_code", ""))
    target = dmc.UnstyledButton(
        dmc.Text(str(stat.get("abbreviation", code)), fw=600, size="sm"),
        id={"type": "stat-label", "scope": scope, "stat": code},
        className=STAT_LABEL_CLASS,
    )
    return _stat_popover(stat, target)


def stat_tile(stat: dict, value_text: str, scope: str) -> Component:
    """
    Description:
        A tappable stat (REQ-064): a tile showing the stat's abbreviation over its value;
        a tap opens a popover with the stat's label, its formula (when it has one) and its
        plain-language explanation. A tap outside closes it.

    Parameters:
        stat: an h_ref.stat_definitions row: {"stat_code", "label", "abbreviation",
            "formula_text" (or None), "explanation"}.
        value_text: the value, already formatted (e.g. "48.5%", "12", "–").
        scope: the screen part the tile belongs to (e.g. "games-avg", "game-page",
            "summary"), so one stat can appear on several screens with unique ids.

    Return Values:
        Component: a dmc.Popover whose target is a button with id {"type": "stat-tile",
        "scope": scope, "stat": stat_code} (at least 44 px tall, class hoopster-stat-tile)
        holding the value text with id {"type": "stat-tile-value", "scope": scope, "stat":
        stat_code}.

    Notes:
        Reusable by dashboards. The popover is uncontrolled: Mantine opens it on the
        target's click and closes it on an outside click, so no callback is needed. The
        popover is built by _stat_popover, shared with stat_label (task
        2026-10-23_dashboards); the output is unchanged.
    """
    code = str(stat.get("stat_code", ""))
    target = dmc.UnstyledButton(
        dmc.Stack(
            [
                dmc.Text(str(stat.get("abbreviation", code)), size="xs", c="dimmed"),
                dmc.Text(
                    value_text,
                    id={"type": "stat-tile-value", "scope": scope, "stat": code},
                    fw=700,
                ),
            ],
            gap=0,
            align="center",
        ),
        id={"type": "stat-tile", "scope": scope, "stat": code},
        className=STAT_TILE_CLASS,
    )
    return _stat_popover(stat, target)


def audit_fields_block(
    prefix: str,
    created_by_name: object,
    created_text: str,
    updated_by_name: object,
    updated_text: str,
) -> Component:
    """
    Description:
        Who added a record and who last edited it, and when (REQ-004): "Added by {name} on
        {date time}" and "Last edited by {name} on {date time}".

    Parameters:
        prefix: the id prefix; the lines are "{prefix}-created" and "{prefix}-updated".
        created_by_name: h_system.users.display_name of created_by.
        created_text: created_at already formatted on the device's clock (e.g. with
            layouts_entry.format_entry_datetime, "Thu 23 Oct 2026 14:05").
        updated_by_name: h_system.users.display_name of updated_by.
        updated_text: updated_at formatted the same way.

    Return Values:
        Component: a stack of the two dimmed lines.

    Notes:
        Takes formatted times rather than the zone (plan item 3.2) because the device-zone
        formatter lives in layouts_entry, which imports this module.
    """
    return dmc.Stack(
        [
            dmc.Text(
                f"Added by {created_by_name} on {created_text}",
                id=f"{prefix}-created",
                size="sm",
                c="dimmed",
            ),
            dmc.Text(
                f"Last edited by {updated_by_name} on {updated_text}",
                id=f"{prefix}-updated",
                size="sm",
                c="dimmed",
            ),
        ],
        gap=0,
    )


def page_skeleton(title: str, children: list) -> Component:
    """
    Description:
        The standard page frame: a level-2 title above the page's content, in one vertical
        stack, inside the page's inner container.

    Parameters:
        title: the page title.
        children: the page's components, top to bottom.

    Return Values:
        Component: the page.

    Notes:
        None.
    """
    return html.Div(
        dmc.Stack([dmc.Title(title, order=2), *children]),
        className="hoopster-page-inner",
    )


def player_options(players: list[dict]) -> list[dict[str, str]]:
    """
    Description:
        Turns player rows into dmc picker options, ids as strings (dmc values are strings).
        The one source of every player picker's options: Home, New practice, the court
        player picker and the court Add player dialog.

    Parameters:
        players: rows with {"player_id", "display_name"}, in display order.

    Return Values:
        list[dict[str, str]]: {"value": str(player_id), "label": display_name} per row, in
        the rows' order.

    Notes:
        Pure. app_controller.parse_player_value reverses the value format.
    """
    return [
        {"value": str(int(player["player_id"])), "label": str(player["display_name"])}
        for player in players
    ]


# --- App shell, bottom bar and drawers (REQ-066; plan §3.5) ---

def bottom_nav() -> html.Nav:
    """
    Description:
        The fixed bottom bar: Home and Dashboards are links; Log and More are buttons that
        open their drawers.

    Parameters:
        None.

    Return Values:
        html.Nav: the bar, hidden until the router shows it to a signed-in user.

    Notes:
        The router sets its className to "hoopster-nav" (shown) or
        "hoopster-nav hoopster-hidden".
    """
    return html.Nav(
        [
            nav_link("nav-home", NAV_LABELS["nav-home"], "/"),
            html.Button(NAV_LABELS["nav-log"], id="nav-log", className="hoopster-nav-item"),
            nav_link("nav-dashboards", NAV_LABELS["nav-dashboards"], "/dashboards"),
            html.Button(NAV_LABELS["nav-more"], id="nav-more", className="hoopster-nav-item"),
        ],
        id="app-nav",
        className="hoopster-nav hoopster-hidden",
    )


def _nav_drawers() -> list[Component]:
    """
    Description:
        Builds the two bottom sheets: Log (New practice, New game) and More: a two-column
        grid of Goals, Measurements, Drills, Drill sets, Practice templates, Drill types,
        Games and Settings, read row by row, with Log out full width below it.

    Parameters:
        None.

    Return Values:
        list[Component]: [nav-log-drawer, nav-more-drawer], both closed.

    Notes:
        Goals and Measurements come first (task 2026-10-23_goals_measurements, D1), then
        the library links (plan §3.3), then Games (task 2026-10-23_games), then Settings
        (nav-more-settings), the home of the management items: Players, Seasons, Recently
        deleted, Export, Devices, passwords (task 2026-10-23_settings_hub, D1).
        nav-more-players and nav-more-seasons are gone; /players and /seasons are linked
        from Settings. The grid keeps the 8 links (50 px each) and Log out on a 740 px
        phone without scrolling; every element id is unchanged.
    """
    grid_links = [
        html.Div(nav_link(element_id, label, href), className="hoopster-link-button")
        for element_id, label, href in (
            *MORE_PLAYER_LINKS,
            *MORE_LIBRARY_LINKS,
            *MORE_GAME_LINKS,
            ("nav-more-settings", "Settings", "/settings"),
        )
    ]
    log_drawer = dmc.Drawer(
        dmc.Stack(
            [
                html.Div(
                    nav_link("nav-log-new-practice", "New practice", "/practice/new"),
                    className="hoopster-link-button hoopster-link-primary",
                ),
                html.Div(
                    nav_link("nav-log-new-game", "New game", "/games/new"),
                    className="hoopster-link-button hoopster-link-primary",
                ),
            ]
        ),
        id="nav-log-drawer",
        title=NAV_LABELS["nav-log"],
        position="bottom",
        opened=False,
    )
    more_drawer = dmc.Drawer(
        dmc.Stack(
            [
                dmc.SimpleGrid(
                    grid_links,
                    cols=MORE_GRID_COLUMNS,
                    spacing="xs",
                    verticalSpacing="xs",
                    id="nav-more-grid",
                ),
                secondary_button("nav-logout", "Log out"),
            ]
        ),
        id="nav-more-drawer",
        title=NAV_LABELS["nav-more"],
        position="bottom",
        opened=False,
    )
    return [log_drawer, more_drawer]


def app_shell() -> Component:
    """
    Description:
        The application layout: the URL, the shared stores, the page container, the bottom
        bar and its drawers, inside the Mantine provider (light theme only).

    Parameters:
        None.

    Return Values:
        Component: the shell.

    Notes:
        Assigned as a function (app.layout = app_shell), so every page load gets fresh
        stores. The router fills page-content. Court stores live in the court page, not here
        (plan §3.5, Open Question 6). resume-checked (session storage, no data argument so a
        rebuilt shell never overwrites it) records that Home's automatic resume already ran
        in this browser session (REQ-057, task 2026-10-23_games).
    """
    return dmc.MantineProvider(
        [
            dcc.Location(id="url", refresh="callback-nav"),
            dcc.Store(id="device-clock"),
            dcc.Store(id="auth-state"),
            dcc.Store(id="resume-checked", storage_type="session"),
            html.Div(id="page-content", className="hoopster-page"),
            bottom_nav(),
            *_nav_drawers(),
        ],
        theme=MANTINE_THEME,
        forceColorScheme="light",
    )


# --- Generic pages ---

def coming_soon_page(title: str, message: str | None = None) -> Component:
    """
    Description:
        A placeholder page for a feature that arrives in a later version.

    Parameters:
        title: the page title, e.g. "Dashboards".
        message: the full sentence to show; None gives "{title} are coming in a later
            version." (which reads correctly for plural titles such as "Dashboards").

    Return Values:
        Component: the page.

    Notes:
        Pass message for a title the default sentence does not fit.
    """
    text = message if message is not None else f"{title} are coming in a later version."
    return page_skeleton(title, [dmc.Text(text, id="coming-soon-message")])


def not_found_page() -> Component:
    """
    Description:
        The in-app page for an unknown address (the device is already verified, so this is
        not the plain 404 a stranger sees).

    Parameters:
        None.

    Return Values:
        Component: the page.

    Notes:
        None.
    """
    return page_skeleton(
        "Not found", [dmc.Text("This page does not exist.", id="not-found-message")]
    )


def no_player_page() -> Component:
    """
    Description:
        The page shown instead of any player-scoped screen while no live player profile
        exists (REQ-073), with a link to the Players screen.

    Parameters:
        None.

    Return Values:
        Component: the page.

    Notes:
        The router shows it for every route except /players and /players/<id>.
    """
    return page_skeleton(
        "No player yet",
        [
            dmc.Text("Add a player profile before logging anything.", id="no-player-message"),
            html.Div(
                nav_link("no-player-add", "Add a player", "/players"),
                className="hoopster-link-button hoopster-link-primary",
            ),
        ],
    )


def error_page(ref: str | None, message: str = "Something went wrong.") -> Component:
    """
    Description:
        The page shown when a page could not be built.

    Parameters:
        ref: the reference code logged with the error (E-XXXXXX), or None when there is none.
        message: the sentence to show.

    Return Values:
        Component: the page, with "Reference: {ref}" under the message when ref is given.

    Notes:
        None.
    """
    children: list = [dmc.Text(message, id="error-message")]
    if ref:
        children.append(dmc.Text(f"Reference: {ref}", id="error-reference"))
    return page_skeleton("Error", children)
