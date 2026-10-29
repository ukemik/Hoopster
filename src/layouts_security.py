"""Settings access UI for Hoopster: Devices with Revoke, Add a device (one-time enrollment code
shown as QR code, link and text) and Password & sign-in (REQ-001, REQ-069, REQ-071; task
2026-10-23_settings_hub).

Built only through the wrappers of src/layouts.py (COD-5-01). Layout modules import nothing from
controllers; the callbacks live in src/controller_security.py. The QR code is drawn with segno,
imported only when a code is shown, so the application starts even without the package.
"""

import logging

import dash_mantine_components as dmc
from dash import dcc, html
from dash.development.base_component import Component

from src.layouts import (
    CONTROL_SIZE,
    confirm_modal,
    form_message,
    page_skeleton,
    primary_button,
    secondary_button,
)
from src.layouts_entry import format_entry_datetime
from src.layouts_settings import hide_test_data_checkbox, settings_back

logger = logging.getLogger(__name__)

# segno's error correction level "M" (about 15 % of the symbol can be damaged) and the quiet
# zone around the code, in modules (4 is the QR standard's minimum).
QR_ERROR_LEVEL = "m"
QR_BORDER_MODULES = 4
QR_DARK = "#000000"
QR_LIGHT = "#ffffff"

ENROLL_STEPS_TEXT = (
    "1. Enter your password and tap Create code. 2. On the new phone or tablet, scan the QR "
    "code with the camera (or open the link). 3. Name the device and tap Add this device. The "
    "code works once, for 15 minutes."
)
SETUP_OFF_QUESTION = (
    "Turn off the setup link? Devices can then be added only with a code from this page. It "
    "can be turned on again only with the setup script."
)
SIGNOUT_ALL_QUESTION = (
    "Sign out everywhere? Your account is signed out on every device, this one included."
)


# --- Devices (REQ-071) ---

def _device_row(device: dict, this_device_id: int | None, zone: str | None) -> Component:
    """
    Description:
        One device of the Devices list.

    Parameters:
        device: a data_access.list_devices row.
        this_device_id: flask.g.device_id, only for the "This device" badge.
        zone: the device's IANA zone (REQ-055).

    Return Values:
        Component: a hoopster-recent-item box: name (+ badge), the enrolled / last seen
        line, and for an unrevoked device a red Revoke button {"type": "devices-revoke",
        "index": device_id}; for a revoked one the "Revoked ... by ..." line.

    Notes:
        Pure apart from the warning format_entry_datetime logs for an unknown zone.
    """
    device_id = int(device["device_id"])
    title: list[Component] = [
        dmc.Text(str(device["device_name"]), style={"overflowWrap": "anywhere"})
    ]
    if this_device_id is not None and device_id == this_device_id:
        title.append(dmc.Badge("This device", id="devices-this-badge", variant="light"))
    enrolled_text = format_entry_datetime(device.get("enrolled_at"), zone)
    seen_text = format_entry_datetime(device.get("last_seen_at"), zone) or "never"
    enrolled_by = device.get("enrolled_by_name") or "unknown"
    content: list[Component] = [
        dmc.Group(title, gap="xs"),
        dmc.Text(
            f"Enrolled {enrolled_text} by {enrolled_by} · last seen {seen_text}",
            size="sm",
            c="dimmed",
        ),
    ]
    if device.get("revoked_at") is None:
        content.append(
            secondary_button({"type": "devices-revoke", "index": device_id}, "Revoke", color="red")
        )
    else:
        revoked_text = format_entry_datetime(device.get("revoked_at"), zone)
        revoked_by = device.get("revoked_by_name") or "unknown"
        content.append(dmc.Text(f"Revoked {revoked_text} by {revoked_by}", size="sm", c="dimmed"))
    return html.Div(dmc.Stack(content, gap=4), className="hoopster-recent-item")


def device_items(
    devices: list[dict], this_device_id: int | None, zone: str | None
) -> list[Component]:
    """
    Description:
        The Devices list: unrevoked devices first, then a "Revoked" divider and the revoked
        ones.

    Parameters:
        devices: data_access.list_devices rows (already filtered for test data).
        this_device_id: flask.g.device_id, only for the badge.
        zone: the device's IANA zone.

    Return Values:
        list[Component]: the rows, or one Text "No devices." (id "devices-empty").

    Notes:
        Pure. list_devices already orders unrevoked devices first.
    """
    if not devices:
        return [dmc.Text("No devices.", id="devices-empty", c="dimmed")]
    live = [device for device in devices if device.get("revoked_at") is None]
    revoked = [device for device in devices if device.get("revoked_at") is not None]
    rows = [_device_row(device, this_device_id, zone) for device in live]
    if revoked:
        rows.append(dmc.Divider(label="Revoked", labelPosition="center"))
        rows.extend(_device_row(device, this_device_id, zone) for device in revoked)
    return rows


def devices_layout(
    devices: list[dict], this_device_id: int | None, zone: str | None
) -> Component:
    """
    Description:
        The Devices page: the Hide test devices checkbox, the list with Revoke, and the
        Revoke confirmation.

    Parameters:
        devices: data_access.list_devices rows, filtered for the default (test devices
            hidden).
        this_device_id: flask.g.device_id, only for the badge.
        zone: the device's IANA zone.

    Return Values:
        Component: the page.

    Notes:
        The dialog is confirm_modal("devices-revoke"): devices-revoke-modal, -text,
        -confirm, -cancel, -feedback. The device to revoke is kept in devices-revoke-id,
        written only by open_revoke (Context Adaptation Table).
    """
    return page_skeleton(
        "Devices",
        [
            settings_back(),
            hide_test_data_checkbox("devices-hide-test", "Hide test devices (ZZ Test)"),
            form_message("devices-feedback"),
            dmc.Text("", id="devices-status", c="green"),
            dmc.Stack(device_items(devices, this_device_id, zone), id="devices-list", gap="xs"),
            dcc.Store(id="devices-revoke-id", storage_type="memory"),
            confirm_modal("devices-revoke", "Revoke device?", "Revoke"),
        ],
    )


# --- Add a device (REQ-069) ---

def enroll_status_items(status: dict, zone: str | None) -> list[Component]:
    """
    Description:
        The status lines of Add a device: the open one-time code, and the setup link.

    Parameters:
        status: data_access.get_enrollment_status.
        zone: the device's IANA zone.

    Return Values:
        list[Component]: "No open code." | "A code made by {name} is open until {time}." |
        "The last code made by {name} expired at {time}.", then "Setup link (from the setup
        script): On" or "... Off".

    Notes:
        Pure apart from the warning format_entry_datetime logs for an unknown zone.
    """
    open_code = status.get("open_code")
    if not open_code:
        code_text = "No open code."
    else:
        name = open_code.get("created_by_name") or "unknown"
        until = format_entry_datetime(open_code.get("expires_at"), zone)
        if open_code.get("is_expired"):
            code_text = f"The last code made by {name} expired at {until}."
        else:
            code_text = f"A code made by {name} is open until {until}."
    setup_text = "On" if status.get("setup_enabled") else "Off"
    return [
        dmc.Text(code_text, id="enroll-code-status"),
        dmc.Text(f"Setup link (from the setup script): {setup_text}", id="enroll-setup-status"),
    ]


def enroll_setup_block(setup_enabled: bool) -> list[Component]:
    """
    Description:
        The Turn off setup link button, shown only while the setup link is on.

    Parameters:
        setup_enabled: h_system.enrollment_settings.is_enabled.

    Return Values:
        list[Component]: [the red "Turn off setup link" button "enroll-setup-off"] or [].

    Notes:
        Pure. The dialog itself stays in the page (enroll_layout).
    """
    if not setup_enabled:
        return []
    return [secondary_button("enroll-setup-off", "Turn off setup link", color="red")]


def qr_data_uri(link: str) -> str | None:
    """
    Description:
        Draws the QR code of a link as an SVG data URI (D15).

    Parameters:
        link: the enrollment link.

    Return Values:
        str | None: "data:image/svg+xml,..." without a fixed width or height (CSS sizes
        it), or None when segno is not installed.

    Notes:
        segno is imported here, not at module level, so the application starts without it;
        the page then shows the link and the code only, and the missing package is logged
        (never the link, which holds the code, COD-4-05). make_qr always draws a full QR
        code (never a Micro QR, which phone cameras do not read).
    """
    try:
        import segno
    except ImportError:
        logger.error("Package segno is not installed; showing the link without a QR code")
        return None
    qr_code = segno.make_qr(link, error=QR_ERROR_LEVEL)
    return str(
        qr_code.svg_data_uri(omitsize=True, border=QR_BORDER_MODULES, dark=QR_DARK, light=QR_LIGHT)
    )


def enroll_result(link: str, code: str, valid_until_text: str) -> list[Component]:
    """
    Description:
        A new one-time code, shown once: QR code, link and code text.

    Parameters:
        link: the enrollment link (app address + /enroll/ + code).
        code: the code. Shown, never logged.
        valid_until_text: the expiry, formatted on the device's clock.

    Return Values:
        list[Component]: the QR image "enroll-qr" (class hoopster-qr; left out without
        segno), the link "enroll-link", "Code: {code}" ("enroll-code") and "Valid until
        {time}, once. This code is shown only once." ("enroll-valid").

    Notes:
        Pure apart from qr_data_uri.
    """
    children: list[Component] = []
    uri = qr_data_uri(link)
    if uri is not None:
        children.append(html.Img(id="enroll-qr", src=uri, alt="QR code", className="hoopster-qr"))
    children.extend(
        [
            dmc.Text("Or open this link on the new device:"),
            dmc.Code(link, id="enroll-link", block=True, className="hoopster-code-wrap"),
            dmc.Text(f"Code: {code}", id="enroll-code", className="hoopster-code-wrap"),
            dmc.Text(
                f"Valid until {valid_until_text}, once. This code is shown only once.",
                id="enroll-valid",
            ),
        ]
    )
    return children


def enroll_layout(status: dict, zone: str | None) -> Component:
    """
    Description:
        The Add a device page: steps, password, Create code, the result area, the status,
        Cancel open code, and the setup link with its Turn off dialog.

    Parameters:
        status: data_access.get_enrollment_status.
        zone: the device's IANA zone.

    Return Values:
        Component: the page.

    Notes:
        The Turn off dialog is confirm_modal("enroll-setup"), opened and cancelled
        clientside; its question is fixed.
    """
    return page_skeleton(
        "Add a device",
        [
            settings_back(),
            dmc.Text(ENROLL_STEPS_TEXT),
            dmc.PasswordInput(
                id="enroll-password",
                label="Your password",
                autoComplete="current-password",
                size=CONTROL_SIZE,
            ),
            primary_button("enroll-create", "Create code"),
            form_message("enroll-feedback"),
            html.Div(id="enroll-result"),
            dmc.Stack(enroll_status_items(status, zone), id="enroll-status", gap=4),
            secondary_button("enroll-cancel", "Cancel open code"),
            html.Div(
                enroll_setup_block(bool(status.get("setup_enabled"))), id="enroll-setup-block"
            ),
            confirm_modal(
                "enroll-setup", "Turn off the setup link?", "Turn off", text=SETUP_OFF_QUESTION
            ),
        ],
    )


# --- Password & sign-in (REQ-001) ---

def _password_input(element_id: str, label: str, autocomplete: str) -> dmc.PasswordInput:
    """
    Description:
        A touch-sized password field.

    Parameters:
        element_id: the stable element id.
        label: the label.
        autocomplete: "current-password" or "new-password".

    Return Values:
        dmc.PasswordInput: the field, empty.

    Notes:
        None.
    """
    return dmc.PasswordInput(
        id=element_id, label=label, autoComplete=autocomplete, size=CONTROL_SIZE
    )


def password_layout(other_accounts: list[dict]) -> Component:
    """
    Description:
        The Password & sign-in page: Change my password, Reset the other account's password
        (when there is one), and Sign out everywhere.

    Parameters:
        other_accounts: data_access.list_other_accounts of the signed-in user.

    Return Values:
        Component: the page.

    Notes:
        The reset account picker's values are user ids as strings, preselected when there is
        one account. The Sign out everywhere dialog is confirm_modal("signout-all"), opened
        and cancelled clientside.
    """
    children: list[Component] = [
        settings_back(),
        dmc.Title("Change my password", order=3),
        _password_input("password-current", "Current password", "current-password"),
        _password_input("password-new", "New password (at least 10 characters)", "new-password"),
        _password_input("password-confirm", "New password again", "new-password"),
        primary_button("password-change-submit", "Change password"),
        dmc.Text("", id="password-change-done", c="green"),
        form_message("password-change-feedback"),
    ]
    if other_accounts:
        options = [
            {"value": str(int(account["user_id"])), "label": str(account["display_name"])}
            for account in other_accounts
        ]
        title_name = options[0]["label"] if len(options) == 1 else "another account"
        children.extend(
            [
                dmc.Title(f"Reset {title_name}'s password", order=3),
                dmc.Select(
                    id="password-reset-account",
                    label="Account",
                    data=options,
                    value=options[0]["value"] if len(options) == 1 else None,
                    allowDeselect=False,
                    size=CONTROL_SIZE,
                ),
                _password_input("password-reset-mine", "Your password", "current-password"),
                _password_input(
                    "password-reset-new", "New password (at least 10 characters)", "new-password"
                ),
                _password_input("password-reset-confirm", "New password again", "new-password"),
                primary_button("password-reset-submit", "Reset password"),
                dmc.Text("", id="password-reset-done", c="green"),
                form_message("password-reset-feedback"),
            ]
        )
    children.extend(
        [
            dmc.Title("Sign out everywhere", order=3),
            dmc.Text("Signs your account out on every device, this one included."),
            secondary_button("signout-all-open", "Sign out everywhere", color="red"),
            confirm_modal(
                "signout-all",
                "Sign out everywhere?",
                "Sign out everywhere",
                text=SIGNOUT_ALL_QUESTION,
            ),
        ]
    )
    return page_skeleton("Password & sign-in", children)
