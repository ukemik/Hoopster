"""Settings UI for Hoopster: the Settings hub and its data pages - Recently deleted with Restore,
Export data, Retention period, About and the user manual (REQ-006, REQ-043, REQ-071; task
2026-10-23_settings_hub).

The access pages (Devices, Add a device, Password & sign-in) are in src/layouts_security.py.
Built only through the wrappers of src/layouts.py (COD-5-01). Layout modules import nothing
from controllers; the callbacks live in src/controller_settings.py.
"""

import logging

import dash_mantine_components as dmc
from dash import dcc, html
from dash.development.base_component import Component

from config.app_config import APP_VERSION, RETENTION_DAYS_MAX, RETENTION_DAYS_MIN
from src.layouts import (
    CONTROL_SIZE,
    form_message,
    nav_link,
    page_skeleton,
    primary_button,
    secondary_button,
)
from src.layouts_entry import format_entry_datetime

logger = logging.getLogger(__name__)

SETTINGS_PATH = "/settings"

# The hub's three groups: (group title, [(element id, label, path)]). Seasons and the drill
# library are the existing screens (task 2026-10-23_games, practice_drills 2a).
SETTINGS_GROUPS: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...] = (
    (
        "Data",
        (
            ("settings-link-deleted", "Recently deleted", "/settings/deleted"),
            ("settings-link-export", "Export data", "/settings/export"),
            # The general Import page (task 2026-10-23_drill_import, REQ-050).
            ("settings-link-import", "Import drills", "/import"),
            ("settings-link-retention", "Retention period", "/settings/retention"),
            ("settings-link-players", "Players", "/players"),
        ),
    ),
    (
        "Access",
        (
            ("settings-link-devices", "Devices", "/settings/devices"),
            ("settings-link-enroll", "Add a device", "/settings/enroll"),
            ("settings-link-password", "Password & sign-in", "/settings/password"),
        ),
    ),
    (
        "Training",
        (
            ("settings-link-seasons", "Seasons", "/seasons"),
            ("settings-link-drills", "Drill library", "/drills"),
            # The shared physical-test list (task 2026-10-23_goals_measurements, D2).
            ("settings-link-physical-tests", "Physical tests", "/physical-tests"),
            ("settings-link-about", "About", "/settings/about"),
        ),
    ),
)

# The "Show" filter of Recently deleted: (value, label); the values are the keys of
# data_settings.DELETED_KIND_ENTITIES.
DELETED_KIND_OPTIONS: list[dict[str, str]] = [
    {"value": "all", "label": "All"},
    {"value": "players", "label": "Players"},
    {"value": "practices", "label": "Practices"},
    {"value": "games", "label": "Games"},
    {"value": "shots", "label": "Shots"},
    {"value": "seasons", "label": "Seasons"},
    {"value": "goals", "label": "Goals & measurements"},
    {"value": "library", "label": "Drill library"},
]
DELETED_KIND_DEFAULT = "all"

RETENTION_NOTE = (
    "Deleted items can be restored for {days} days. Automatic cleanup is not switched on yet, "
    "so nothing is removed for now."
)
NOTHING_DELETED_TEXT = "Nothing deleted."
COUNT_TEXT = "{shown} of {total} items"
CLEANUP_OFF_TEXT = "Automatic cleanup is not switched on yet."
MANUAL_MISSING_TEXT = "The user manual is not available on this server."


# --- Shared pieces ---

def settings_back() -> Component:
    """
    Description:
        The "‹ Settings" link at the top of every Settings sub-page.

    Parameters:
        None.

    Return Values:
        Component: the link (id "settings-back") in a button-style wrapper.

    Notes:
        None.
    """
    return html.Div(
        nav_link("settings-back", "‹ Settings", SETTINGS_PATH),
        className="hoopster-link-button",
    )


def hide_test_data_checkbox(element_id: str, label: str) -> Component:
    """
    Description:
        A "Hide test data" checkbox, on by default (D11).

    Parameters:
        element_id: the stable element id, e.g. "deleted-hide-test".
        label: the label text.

    Return Values:
        Component: a 44 px dmc.Checkbox (class hoopster-check), checked.

    Notes:
        A Checkbox, not a Switch, so the 44 px touch-size rule of hoopster-check applies;
        callbacks read its "checked" property either way.
    """
    return dmc.Checkbox(
        id=element_id,
        label=label,
        checked=True,
        size=CONTROL_SIZE,
        className="hoopster-check",
    )


# --- Hub ---

def settings_hub_layout() -> Component:
    """
    Description:
        The Settings hub: three groups of links (Data, Access, Training).

    Parameters:
        None.

    Return Values:
        Component: the page.

    Notes:
        Each link is a nav_link in a button-style wrapper (44 px and more).
    """
    children: list[Component] = []
    for group_title, links in SETTINGS_GROUPS:
        children.append(dmc.Title(group_title, order=3))
        children.extend(
            html.Div(nav_link(element_id, label, path), className="hoopster-link-button")
            for element_id, label, path in links
        )
    return page_skeleton("Settings", children)


# --- Recently deleted (REQ-006) ---

def deleted_count_text(shown: int, total: int) -> str:
    """
    Description:
        The count line of Recently deleted.

    Parameters:
        shown: the number of items listed.
        total: the number of matching items before the limit.

    Return Values:
        str: "{shown} of {total} items", or "Nothing deleted." when total is 0.

    Notes:
        Pure.
    """
    if total <= 0:
        return NOTHING_DELETED_TEXT
    return COUNT_TEXT.format(shown=shown, total=total)


def deleted_items(items: list[dict], zone: str | None) -> list[Component]:
    """
    Description:
        The rows of Recently deleted: label, who deleted it and when, until when it is
        kept, and a Restore button.

    Parameters:
        items: data_settings.list_recently_deleted rows.
        zone: the device's IANA zone (REQ-055).

    Return Values:
        list[Component]: one hoopster-recent-item box per item; the Restore button's id is
        {"type": "deleted-restore", "index": "<entity>:<id>"}.

    Notes:
        Pure apart from the warning format_entry_datetime logs for an unknown zone.
    """
    rows: list[Component] = []
    for item in items:
        key = f"{item['entity']}:{int(item['entity_id'])}"
        deleted_text = format_entry_datetime(item.get("deleted_at"), zone)
        kept_text = format_entry_datetime(item.get("purge_after"), zone)
        who = item.get("deleted_by_name") or "unknown"
        rows.append(
            html.Div(
                dmc.Stack(
                    [
                        dmc.Text(str(item["label"]), style={"overflowWrap": "anywhere"}),
                        dmc.Text(
                            f"Deleted {deleted_text} by {who} · kept until {kept_text}",
                            size="sm",
                            c="dimmed",
                        ),
                        secondary_button({"type": "deleted-restore", "index": key}, "Restore"),
                    ],
                    gap=4,
                ),
                className="hoopster-recent-item",
            )
        )
    return rows


def deleted_layout(
    items: list[dict], total: int, retention_days: int, zone: str | None
) -> Component:
    """
    Description:
        The Recently deleted page: the retention note, the Show / Search / Hide test data
        filters, the count line and the list.

    Parameters:
        items: data_settings.list_recently_deleted rows for the default filters.
        total: the number of matching items before the limit.
        retention_days: the current retention period.
        zone: the device's IANA zone.

    Return Values:
        Component: the page.

    Notes:
        refresh_deleted and restore_deleted rebuild deleted-list and deleted-count.
    """
    return page_skeleton(
        "Recently deleted",
        [
            settings_back(),
            dmc.Text(RETENTION_NOTE.format(days=retention_days), id="deleted-retention-note"),
            dmc.Select(
                id="deleted-kind",
                label="Show",
                data=DELETED_KIND_OPTIONS,
                value=DELETED_KIND_DEFAULT,
                allowDeselect=False,
                size=CONTROL_SIZE,
            ),
            dmc.TextInput(id="deleted-search", label="Search", debounce=True, size=CONTROL_SIZE),
            hide_test_data_checkbox("deleted-hide-test", "Hide test data (ZZ Test)"),
            form_message("deleted-feedback"),
            dmc.Text("", id="deleted-status", c="green"),
            dmc.Text(deleted_count_text(len(items), total), id="deleted-count", size="sm"),
            dmc.Stack(deleted_items(items, zone), id="deleted-list", gap="xs"),
        ],
    )


# --- Export (REQ-043) ---

def export_layout(table_options: list[str]) -> Component:
    """
    Description:
        The Export data page: Export all (ZIP) and a one-table CSV download.

    Parameters:
        table_options: the exportable table names (data_settings.EXPORT_TABLES).

    Return Values:
        Component: the page, with the dcc.Download "export-download".

    Notes:
        None.
    """
    return page_skeleton(
        "Export data",
        [
            settings_back(),
            dmc.Text("Downloads every table as CSV (deleted rows are left out)."),
            primary_button("export-all", "Export all (ZIP)"),
            dmc.Select(
                id="export-table",
                label="One table",
                data=list(table_options),
                searchable=True,
                size=CONTROL_SIZE,
            ),
            secondary_button("export-table-download", "Download CSV"),
            form_message("export-feedback"),
            dcc.Download(id="export-download"),
        ],
    )


# --- Retention period (REQ-006, D12) ---

def retention_layout(days: int) -> Component:
    """
    Description:
        The Retention period page: the number of days deleted items stay restorable.

    Parameters:
        days: the current value.

    Return Values:
        Component: the page.

    Notes:
        clampBehavior "none" and decimals allowed, so a wrong value reaches save_retention
        and gets its message instead of being silently changed by the input.
    """
    return page_skeleton(
        "Retention period",
        [
            settings_back(),
            dmc.NumberInput(
                id="retention-days",
                label="Days deleted items stay restorable",
                value=days,
                min=RETENTION_DAYS_MIN,
                max=RETENTION_DAYS_MAX,
                step=1,
                clampBehavior="none",
                hideControls=True,
                size=CONTROL_SIZE,
            ),
            primary_button("retention-save", "Save"),
            dmc.Text("", id="retention-saved", c="green"),
            form_message("retention-feedback"),
            dmc.Text(CLEANUP_OFF_TEXT, size="sm", c="dimmed"),
        ],
    )


# --- About and the user manual (D16) ---

def about_layout(info: dict) -> Component:
    """
    Description:
        The About page: version, database status, signed-in user and the user manual link.

    Parameters:
        info: {"server_version": str | None, "error_ref": str | None, "user_name": str}.

    Return Values:
        Component: the page.

    Notes:
        With error_ref the database line says "not reachable" with the reference.
    """
    if info.get("error_ref"):
        database_text = f"Database: not reachable. Reference: {info['error_ref']}"
    else:
        database_text = f"Database: connected (PostgreSQL {info.get('server_version')})"
    return page_skeleton(
        "About",
        [
            settings_back(),
            dmc.Text(f"Hoopster version {APP_VERSION}", id="about-version"),
            dmc.Text(database_text, id="about-database"),
            dmc.Text(f"Signed in as {info.get('user_name')}", id="about-user"),
            html.Div(
                nav_link("about-manual", "User manual", "/settings/manual"),
                className="hoopster-link-button",
            ),
        ],
    )


def manual_layout(markdown_text: str | None) -> Component:
    """
    Description:
        The User manual page: the manual rendered from Markdown.

    Parameters:
        markdown_text: the manual's text, or None when the file is not on this server.

    Return Values:
        Component: the page.

    Notes:
        dcc.Markdown without HTML (dangerously_allow_html stays False). Wide tables scroll
        inside hoopster-manual, never the page.
    """
    if markdown_text is None:
        body: Component = dmc.Text(MANUAL_MISSING_TEXT, id="manual-missing")
    else:
        body = dcc.Markdown(markdown_text, id="manual-text", className="hoopster-manual")
    return page_skeleton("User manual", [settings_back(), body])
