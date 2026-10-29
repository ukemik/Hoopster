"""Settings access controller of Hoopster: Devices with Revoke, Add a device (one-time enrollment
codes and the setup link) and Password & sign-in (REQ-001, REQ-069, REQ-071; task
2026-10-23_settings_hub, D2-D8).

Identifiers (Context Adaptation Table, COD-8-01): the device to revoke comes only from the
devices-revoke-id store written by open_revoke, never flask.g.device_id (used only for the
"This device" badge); the account whose password is reset is re-checked against
list_other_accounts; every write's acting user is current_user.user_id. Every write goes
through app_controller.run_write; every callback is a user-facing boundary (COD-4-04).
Passwords and codes are never logged (COD-4-05).
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone

from dash import ALL, ClientsideFunction, Dash, Input, Output, State, ctx, no_update
from dash.development.base_component import Component
from dash.exceptions import PreventUpdate
from flask import g, request
from flask_login import current_user, login_user
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from config.app_config import (
    ENROLLMENT_CODE_BYTES,
    ENROLLMENT_CODE_VALID_MINUTES,
    PASSWORD_HASH_METHOD,
    REMEMBER_ME_DAYS,
    TEST_DATA_MARKER,
    AppSettings,
)
from src import data_handler
from src.app_controller import (
    DeviceClock,
    log_failure,
    new_reference_code,
    normalise_clock,
    parse_player_value,
    run_write,
    run_write_with_reason,
)
from src.controller_access import (
    AppUser,
    one_time_code_hash,
    sign_out,
    verify_current_password,
)
from src.data_access import (
    REVOKE_DONE,
    REVOKE_LAST_DEVICE,
    bump_session_version,
    cancel_open_enrollment_codes,
    disable_setup_enrollment,
    get_enrollment_status,
    insert_enrollment_code,
    list_devices,
    list_other_accounts,
    revoke_device,
    set_password_by_user_id,
)
from src.layouts_entry import format_entry_datetime
from src.layouts_security import (
    device_items,
    devices_layout,
    enroll_layout,
    enroll_result,
    enroll_status_items,
    password_layout,
)
from src.setup_accounts import password_problem

logger = logging.getLogger(__name__)

ENROLL_PATH_PREFIX = "/enroll/"
CLEARED = ""

MESSAGE_DEVICES_FAILED = "Could not load the devices. Reference: {ref}"
MESSAGE_ALREADY_REVOKED = "This device is already revoked."
MESSAGE_LAST_DEVICE = (
    "Hoopster needs at least one enrolled device, so the last one cannot be revoked."
)
MESSAGE_REVOKED = "Revoked {name}."
MESSAGE_REVOKE_FAILED = "Could not revoke the device. Reference: {ref}"
REVOKE_QUESTION = "Revoke {name}? It will get 'Not Found' until it is enrolled again."
REVOKE_THIS_DEVICE = " This is the device you are using now: Hoopster will stop opening here."

# DEF-040: two codes created at the same moment; the second insert hits the open-code index.
MESSAGE_CODE_RACE = "A code was just created; try again."
OPEN_CODE_INDEX = "ux_enrollment_codes_open"
MESSAGE_CODE_FAILED = "Could not create a code. Reference: {ref}"
MESSAGE_CODE_CANCELLED = "Code cancelled."
MESSAGE_NO_OPEN_CODE = "No open code."
MESSAGE_CANCEL_FAILED = "Could not cancel the code. Reference: {ref}"
MESSAGE_STATUS_FAILED = "Could not load the code status. Reference: {ref}"
MESSAGE_SETUP_OFF = "The setup link is off."
MESSAGE_SETUP_FAILED = "Could not turn off the setup link. Reference: {ref}"

MESSAGE_SAME_PASSWORD = "Choose a password different from the current one."
MESSAGE_PASSWORD_CHANGED = "Password changed. Your account is signed out on every other device."
MESSAGE_PASSWORD_FAILED = "Could not change the password. Reference: {ref}"
MESSAGE_CHOOSE_ACCOUNT = "Choose an account."
MESSAGE_RESET_DONE = "{name}'s password was reset. {name} is signed out on every device."
MESSAGE_RESET_FAILED = "Could not reset the password. Reference: {ref}"
MESSAGE_SIGNOUT_FAILED = "Could not sign out everywhere. Reference: {ref}"


# --- Pure helpers ---

def new_enrollment_code() -> str:
    """
    Description:
        Creates a one-time enrollment code (D3).

    Parameters:
        None.

    Return Values:
        str: 32 URL-safe characters (192 random bits).

    Notes:
        Uses secrets (COD-14-07). The code is shown once and never stored or logged.
    """
    return secrets.token_urlsafe(ENROLLMENT_CODE_BYTES)


def enrollment_link(host_url: str, code: str) -> str:
    """
    Description:
        The enrollment link of a code.

    Parameters:
        host_url: flask.request.host_url, e.g. "http://10.0.0.12:8050/".
        code: the one-time code.

    Return Values:
        str: host_url without its trailing slash + "/enroll/" + code.

    Notes:
        Pure. Behind Render's proxy host_url may say http:// until ProxyFix (plan §9).
    """
    return host_url.rstrip("/") + ENROLL_PATH_PREFIX + code


def revoke_question(name: str, is_this_device: bool) -> str:
    """
    Description:
        The question of the Revoke dialog.

    Parameters:
        name: the device name.
        is_this_device: True when it is the device in use (flask.g.device_id).

    Return Values:
        str: "Revoke {name}? It will get 'Not Found' until it is enrolled again.", plus
        the this-device warning when it applies (D8).

    Notes:
        Pure.
    """
    question = REVOKE_QUESTION.format(name=name)
    return question + REVOKE_THIS_DEVICE if is_this_device else question


def visible_devices(
    devices: list[dict[str, object]], hide_test: object
) -> list[dict[str, object]]:
    """
    Description:
        Filters the Devices list for the Hide test devices checkbox (D11).

    Parameters:
        devices: data_access.list_devices rows.
        hide_test: devices-hide-test.checked.

    Return Values:
        list[dict[str, object]]: the rows, without those whose name starts with
        TEST_DATA_MARKER when hide_test is truthy.

    Notes:
        Pure.
    """
    if not hide_test:
        return devices
    return [row for row in devices if not str(row["device_name"]).startswith(TEST_DATA_MARKER)]


def _this_device_id() -> int | None:
    """
    Description:
        The verified device of this request, for the "This device" badge and warning only.

    Parameters:
        None.

    Return Values:
        int | None: flask.g.device_id, or None.

    Notes:
        Never used as the device to revoke (Context Adaptation Table).
    """
    device_id = g.get("device_id")
    return device_id if isinstance(device_id, int) else None


# --- Page builders ---

def build_devices_page(
    player_id: int | None, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds Devices with test devices hidden.

    Parameters:
        player_id: the selected player (unused).
        clock: the device clock (its zone formats the times).
        params: the route parameters (none).

    Return Values:
        Component: the page.

    Notes:
        Exceptions propagate to the router.
    """
    devices = visible_devices(list_devices(data_handler.get_session()), True)
    return devices_layout(devices, _this_device_id(), clock["zone"])


def build_enroll_page(
    player_id: int | None, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds Add a device.

    Parameters:
        player_id: the selected player (unused).
        clock: the device clock.
        params: the route parameters (none).

    Return Values:
        Component: the page.

    Notes:
        Exceptions propagate to the router.
    """
    return enroll_layout(get_enrollment_status(data_handler.get_session()), clock["zone"])


def build_password_page(
    player_id: int | None, clock: DeviceClock, params: dict[str, str]
) -> Component:
    """
    Description:
        Builds Password & sign-in.

    Parameters:
        player_id: the selected player (unused).
        clock: the device clock (unused).
        params: the route parameters (none).

    Return Values:
        Component: the page.

    Notes:
        The other accounts are those of current_user.user_id.
    """
    others = list_other_accounts(data_handler.get_session(), current_user.user_id)
    return password_layout(others)


# --- Devices callbacks (REQ-071, D8) ---

def _device_list(hide_test: object, clock: object) -> list[Component]:
    """
    Description:
        Reads and builds the Devices list.

    Parameters:
        hide_test: devices-hide-test.checked.
        clock: device-clock.data.

    Return Values:
        list[Component]: devices-list children.

    Notes:
        Raises on a failed read; the callers are the boundaries.
    """
    devices = visible_devices(list_devices(data_handler.get_session()), hide_test)
    return device_items(devices, _this_device_id(), normalise_clock(clock)["zone"])


def refresh_devices(hide_test: object, clock: object) -> tuple[object, str]:
    """
    Description:
        Rebuilds the Devices list when Hide test devices changes.

    Parameters:
        hide_test: devices-hide-test.checked.
        clock: device-clock.data.

    Return Values:
        tuple[object, str]: devices-list children and devices-feedback text.

    Notes:
        A boundary (COD-4-04).
    """
    try:
        return _device_list(hide_test, clock), ""
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Devices read failed (ref %s)", ref)
        return no_update, MESSAGE_DEVICES_FAILED.format(ref=ref)


def _live_device_name(device_id: int) -> str | None:
    """
    Description:
        The name of a live, unrevoked device.

    Parameters:
        device_id: h_system.devices.device_id.

    Return Values:
        str | None: the name, or None when the device is revoked or unknown.

    Notes:
        Read-only.
    """
    for row in list_devices(data_handler.get_session()):
        if row["device_id"] == device_id and row["revoked_at"] is None:
            return str(row["device_name"])
    return None


def open_revoke(n_clicks: list[int | None]) -> tuple[object, object, object, str, str]:
    """
    Description:
        A device's Revoke button: opens the confirmation with the device's name.

    Parameters:
        n_clicks: the n_clicks of every Revoke button (ALL).

    Return Values:
        tuple[object, object, object, str, str]: devices-revoke-modal.opened,
        devices-revoke-text, devices-revoke-id.data, devices-revoke-feedback ("") and
        devices-feedback.

    Notes:
        A boundary (COD-4-04). Raises PreventUpdate for a run without a click (a rebuilt
        list fires ALL callbacks). The store gets the clicked button's index only after the
        live check; a revoked device does not open the dialog.
    """
    triggered = ctx.triggered_id
    if not isinstance(triggered, dict) or triggered.get("type") != "devices-revoke":
        raise PreventUpdate
    if not ctx.triggered or not ctx.triggered[0].get("value"):
        raise PreventUpdate
    device_id = triggered.get("index")
    if not isinstance(device_id, int) or isinstance(device_id, bool):
        raise PreventUpdate
    try:
        name = _live_device_name(device_id)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Revoke dialog read failed (ref %s)", ref)
        return no_update, no_update, no_update, "", MESSAGE_DEVICES_FAILED.format(ref=ref)
    if name is None:
        return no_update, no_update, no_update, "", MESSAGE_ALREADY_REVOKED
    question = revoke_question(name, device_id == _this_device_id())
    return True, question, device_id, "", ""


def _revoke(device_id: int) -> tuple[str | None, str | None, str | None]:
    """
    Description:
        Revokes the stored device as the signed-in user.

    Parameters:
        device_id: devices-revoke-id.data.

    Return Values:
        tuple[str | None, str | None, str | None]: (outcome REVOKE_* or None on failure,
        the device's name or None, the failure reference or None).

    Notes:
        revoke_device re-checks liveness and the last-device rule under lock.
    """
    name = _live_device_name(device_id)
    user_id: int = current_user.user_id
    result = run_write(
        user_id, lambda session: revoke_device(session, device_id, user_id), "revoke device"
    )
    if not result.ok:
        return None, name, result.ref
    return result.value, name, None


def confirm_revoke(
    n_clicks: int | None, device_id: object, hide_test: object, clock: object
) -> tuple[object, object, str, str, str]:
    """
    Description:
        The Revoke button of the dialog.

    Parameters:
        n_clicks: devices-revoke-confirm.n_clicks.
        device_id: devices-revoke-id.data.
        hide_test: devices-hide-test.checked.
        clock: device-clock.data.

    Return Values:
        tuple[object, object, str, str, str]: devices-list children,
        devices-revoke-modal.opened, devices-revoke-feedback, devices-status and
        devices-feedback.

    Notes:
        A boundary (COD-4-04). Revoked: dialog closed, "Revoked {name}.". Last device:
        dialog stays open with the refusal (D8). Already revoked: dialog closed, message.
    """
    if not n_clicks:
        raise PreventUpdate
    if not isinstance(device_id, int) or isinstance(device_id, bool):
        ref = new_reference_code()
        logger.error("Revoke refused (ref %s): devices-revoke-id is not an id", ref)
        return no_update, no_update, MESSAGE_REVOKE_FAILED.format(ref=ref), "", ""
    try:
        outcome, name, ref = _revoke(device_id)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Revoke failed (ref %s)", ref)
        return no_update, no_update, MESSAGE_REVOKE_FAILED.format(ref=ref), "", ""
    if outcome is None:
        return no_update, no_update, MESSAGE_REVOKE_FAILED.format(ref=ref), "", ""
    if outcome == REVOKE_LAST_DEVICE:
        return no_update, no_update, MESSAGE_LAST_DEVICE, "", ""
    if outcome == REVOKE_DONE:
        logger.info("Device %s revoked by user %s", device_id, current_user.user_id)
        status, feedback = MESSAGE_REVOKED.format(name=name or "the device"), ""
    else:
        status, feedback = "", MESSAGE_ALREADY_REVOKED
    try:
        rows: object = _device_list(hide_test, clock)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Devices reload failed (ref %s)", ref)
        rows, feedback = no_update, MESSAGE_DEVICES_FAILED.format(ref=ref)
    return rows, False, "", status, feedback


# --- Add a device callbacks (REQ-069, D3, D4, DEF-040) ---

def _status_items(clock: object) -> list[Component]:
    """
    Description:
        Reads the enrollment status and builds its lines.

    Parameters:
        clock: device-clock.data.

    Return Values:
        list[Component]: enroll-status children.

    Notes:
        Raises on a failed read.
    """
    status = get_enrollment_status(data_handler.get_session())
    return enroll_status_items(status, normalise_clock(clock)["zone"])


def _store_new_code(code: str) -> tuple[dict[str, object] | None, str | None]:
    """
    Description:
        Cancels the open code and stores the new one's hash, as the signed-in user.

    Parameters:
        code: the new code. Only its hash is written; never logged.

    Return Values:
        tuple[dict[str, object] | None, str | None]: ({"enrollment_code_id", "expires_at"},
        None), or (None, the message: DEF-040's "try again" or a reference).

    Notes:
        One transaction (COD-6-03). A concurrent create fails on ux_enrollment_codes_open
        (SQLSTATE 23505), shown as MESSAGE_CODE_RACE.
    """
    code_hash = one_time_code_hash(code)

    def work(session: Session) -> dict[str, object]:
        """
        Description:
            Cancel the open code, then insert the new one.

        Parameters:
            session: the write session.

        Return Values:
            dict[str, object]: the new row's id and expiry.

        Notes:
            None.
        """
        cancel_open_enrollment_codes(session)
        return insert_enrollment_code(session, code_hash, ENROLLMENT_CODE_VALID_MINUTES)

    result, reason = run_write_with_reason(
        current_user.user_id,
        work,
        "create enrollment code",
        {OPEN_CODE_INDEX: MESSAGE_CODE_RACE},
    )
    if result.ok and result.value is not None:
        return result.value, None
    return None, reason or MESSAGE_CODE_FAILED.format(ref=result.ref)


def create_code(
    n_clicks: int | None, password: str | None, clock: object, settings: AppSettings
) -> tuple[object, str, str, object]:
    """
    Description:
        Create code: checks the password, makes a new one-time code (cancelling the open
        one) and shows it once as QR code, link and text.

    Parameters:
        n_clicks: enroll-create.n_clicks.
        password: enroll-password.value. Never logged.
        clock: device-clock.data.
        settings: the application settings (lockout thresholds).

    Return Values:
        tuple[object, str, str, object]: enroll-result children, enroll-password value
        (always ""), enroll-feedback text and enroll-status children.

    Notes:
        A boundary (COD-4-04). Logs the code's id, creator and expiry only (D3).
    """
    if not n_clicks:
        raise PreventUpdate
    problem = verify_current_password(current_user.user_id, password, settings)
    if problem is not None:
        return no_update, CLEARED, problem, no_update
    code = new_enrollment_code()
    row, message = _store_new_code(code)
    if row is None:
        return no_update, CLEARED, message or "", no_update
    expires_at = row["expires_at"]
    expires_utc = (
        expires_at.astimezone(timezone.utc).isoformat()
        if isinstance(expires_at, datetime)
        else str(expires_at)
    )
    logger.info(
        "Enrollment code %s created by user %s, valid until %s",
        row["enrollment_code_id"],
        current_user.user_id,
        expires_utc,
    )
    zone = normalise_clock(clock)["zone"]
    link = enrollment_link(request.host_url, code)
    children = enroll_result(link, code, format_entry_datetime(expires_at, zone))
    try:
        status: object = _status_items(clock)
        feedback = ""
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Enrollment status read failed (ref %s)", ref)
        status, feedback = no_update, MESSAGE_STATUS_FAILED.format(ref=ref)
    return children, CLEARED, feedback, status


def cancel_code(n_clicks: int | None, clock: object) -> tuple[object, object, str]:
    """
    Description:
        Cancel open code.

    Parameters:
        n_clicks: enroll-cancel.n_clicks.
        clock: device-clock.data.

    Return Values:
        tuple[object, object, str]: enroll-status children, enroll-result children ("")
        and enroll-feedback text ("Code cancelled." or "No open code.").

    Notes:
        A boundary (COD-4-04). The acting user is current_user.user_id.
    """
    if not n_clicks:
        raise PreventUpdate
    result = run_write(
        current_user.user_id, cancel_open_enrollment_codes, "cancel enrollment code"
    )
    if not result.ok:
        return no_update, no_update, MESSAGE_CANCEL_FAILED.format(ref=result.ref)
    message = MESSAGE_CODE_CANCELLED if result.value else MESSAGE_NO_OPEN_CODE
    if result.value:
        logger.info("Open enrollment code cancelled by user %s", current_user.user_id)
    try:
        return _status_items(clock), CLEARED, message
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Enrollment status read failed (ref %s)", ref)
        return no_update, CLEARED, MESSAGE_STATUS_FAILED.format(ref=ref)


def turn_off_setup(n_clicks: int | None, clock: object) -> tuple[object, object, str, object]:
    """
    Description:
        The Turn off button of the setup-link dialog (REQ-069 "switched off", D4).

    Parameters:
        n_clicks: enroll-setup-confirm.n_clicks.
        clock: device-clock.data.

    Return Values:
        tuple[object, object, str, object]: enroll-status children,
        enroll-setup-modal.opened (False), enroll-setup-feedback text and
        enroll-setup-block children ([] once off). When the status cannot be re-read after
        the link was turned off, the status and dialog are left as they are and the
        feedback is "Could not load the code status. Reference: {ref}".

    Notes:
        A boundary (COD-4-04). Only the setup script turns it on again
        (setup_accounts --set-enrollment-code).
    """
    if not n_clicks:
        raise PreventUpdate
    result = run_write(current_user.user_id, disable_setup_enrollment, "turn off setup link")
    if not result.ok:
        return no_update, no_update, MESSAGE_SETUP_FAILED.format(ref=result.ref), no_update
    logger.info("Setup link turned off by user %s", current_user.user_id)
    try:
        return _status_items(clock), False, "", []
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Enrollment status read failed (ref %s)", ref)
        # The dialog stays open so its feedback line can show the reference (review_2 Minor).
        return no_update, no_update, MESSAGE_STATUS_FAILED.format(ref=ref), []


# --- Password & sign-in callbacks (REQ-001, D6, D7) ---

def _set_password(account_id: int, new_password: str) -> tuple[int, None] | tuple[None, str]:
    """
    Description:
        Stores a new password for an account, bumping its session_version.

    Parameters:
        account_id: the account whose password changes (checked by the caller).
        new_password: the checked new password. Never logged.

    Return Values:
        tuple[int, None] | tuple[None, str]: (the new session_version, None), or (None, a
        failure reference that is always logged): run_write's reference when the write
        failed, or a new one logged here when no live account has that id.

    Notes:
        The acting user is current_user.user_id. The no-account case (an account deactivated
        or deleted between page load and submit) is logged with logger.error, account id and
        action only (review_2 Medium; COD-4-03, COD-4-05).
    """
    password_hash = generate_password_hash(new_password, method=PASSWORD_HASH_METHOD)
    result = run_write(
        current_user.user_id,
        lambda session: set_password_by_user_id(session, account_id, password_hash),
        "set password",
    )
    if not result.ok:
        return None, result.ref
    if result.value is None:
        ref = new_reference_code()
        logger.error("Set password refused: no live account %s (ref %s)", account_id, ref)
        return None, ref
    return result.value, None


def change_password(
    n_clicks: int | None,
    current: str | None,
    new: str | None,
    confirm: str | None,
    settings: AppSettings,
) -> tuple[str, str, str, str, str]:
    """
    Description:
        Change my password: checks the new password, then the current one, stores the new
        one and keeps this browser signed in (D7).

    Parameters:
        n_clicks: password-change-submit.n_clicks.
        current: password-current.value. Never logged.
        new: password-new.value. Never logged.
        confirm: password-confirm.value. Never logged.
        settings: the application settings (lockout thresholds).

    Return Values:
        tuple[str, str, str, str, str]: password-change-done text, password-change-feedback
        text, and the three field values (always "").

    Notes:
        A boundary (COD-4-04). The bumped session_version signs the account out
        everywhere; login_user with the new version re-signs this browser only.
    """
    if not n_clicks:
        raise PreventUpdate
    cleared = (CLEARED, CLEARED, CLEARED)
    problem = password_problem(new or "", confirm or "")
    if problem is None and new == current:
        problem = MESSAGE_SAME_PASSWORD
    if problem is None:
        problem = verify_current_password(current_user.user_id, current, settings)
    if problem is not None:
        return "", problem, *cleared
    user_id: int = current_user.user_id
    display_name: str = current_user.display_name
    new_version, ref = _set_password(user_id, new or "")
    if new_version is None:
        return "", MESSAGE_PASSWORD_FAILED.format(ref=ref), *cleared
    login_user(
        AppUser(user_id, display_name, new_version),
        remember=True,
        duration=timedelta(days=REMEMBER_ME_DAYS),
    )
    logger.info("User %s changed their password", user_id)
    return MESSAGE_PASSWORD_CHANGED, "", *cleared


def _other_account(account_value: object) -> dict[str, object] | None:
    """
    Description:
        The picked other account, re-checked against list_other_accounts.

    Parameters:
        account_value: password-reset-account.value (a user id as a string).

    Return Values:
        dict[str, object] | None: {"user_id", "display_name"}, or None when the value is
        not one of the signed-in user's other live, active accounts.

    Notes:
        Read-only; raises on a failed read.
    """
    account_id = parse_player_value(account_value)
    if account_id is None:
        return None
    for account in list_other_accounts(data_handler.get_session(), current_user.user_id):
        if account["user_id"] == account_id:
            return account
    return None


def reset_other_password(
    n_clicks: int | None,
    account_value: object,
    mine: str | None,
    new: str | None,
    confirm: str | None,
    settings: AppSettings,
) -> tuple[str, str, str, str, str]:
    """
    Description:
        Reset the other account's password, after the signed-in user's own password. The
        other account is signed out everywhere (D7). No email (REQ-001).

    Parameters:
        n_clicks: password-reset-submit.n_clicks.
        account_value: password-reset-account.value.
        mine: password-reset-mine.value. Never logged.
        new: password-reset-new.value. Never logged.
        confirm: password-reset-confirm.value. Never logged.
        settings: the application settings (lockout thresholds).

    Return Values:
        tuple[str, str, str, str, str]: password-reset-done text, password-reset-feedback
        text, and the three password values (always "").

    Notes:
        A boundary (COD-4-04).
    """
    if not n_clicks:
        raise PreventUpdate
    cleared = (CLEARED, CLEARED, CLEARED)
    try:
        account = _other_account(account_value)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Reset account check failed (ref %s)", ref)
        return "", MESSAGE_RESET_FAILED.format(ref=ref), *cleared
    if account is None:
        return "", MESSAGE_CHOOSE_ACCOUNT, *cleared
    problem = password_problem(new or "", confirm or "")
    if problem is None:
        problem = verify_current_password(current_user.user_id, mine, settings)
    if problem is not None:
        return "", problem, *cleared
    account_id = int(str(account["user_id"]))
    name = str(account["display_name"])
    new_version, ref = _set_password(account_id, new or "")
    if new_version is None:
        return "", MESSAGE_RESET_FAILED.format(ref=ref), *cleared
    logger.info("User %s reset the password of user %s", current_user.user_id, account_id)
    return MESSAGE_RESET_DONE.format(name=name), "", *cleared


def sign_out_everywhere(n_clicks: int | None) -> tuple[object, str]:
    """
    Description:
        The confirm button of Sign out everywhere: bumps the account's session_version,
        then signs this browser out too (D7).

    Parameters:
        n_clicks: signout-all-confirm.n_clicks.

    Return Values:
        tuple[object, str]: auth-state data (a new value re-runs the router to the sign-in
        page) and signout-all-feedback text.

    Notes:
        A boundary (COD-4-04). On failure the user stays signed in and sees a reference that
        is always logged: run_write's, or a new one logged here when bump_session_version
        finds no live account (review_2 Medium).
    """
    if not n_clicks:
        raise PreventUpdate
    user_id: int = current_user.user_id
    result = run_write(
        user_id, lambda session: bump_session_version(session, user_id), "sign out everywhere"
    )
    if not result.ok:
        return no_update, MESSAGE_SIGNOUT_FAILED.format(ref=result.ref)
    if result.value is None:
        ref = new_reference_code()
        logger.error("Sign out everywhere refused: no live account %s (ref %s)", user_id, ref)
        return no_update, MESSAGE_SIGNOUT_FAILED.format(ref=ref)
    logger.info("User %s signed out everywhere", user_id)
    auth_state, _ = sign_out()
    return auth_state, ""


# --- Registration ---

def _register_devices(app: Dash) -> None:
    """
    Description:
        Registers the Devices callbacks.

    Parameters:
        app: the Dash application.

    Return Values:
        None.

    Notes:
        open_revoke is the first writer of the dialog outputs; confirm_revoke and the
        clientside Cancel use allow_duplicate.
    """
    app.callback(
        Output("devices-list", "children"),
        Output("devices-feedback", "children"),
        Input("devices-hide-test", "checked"),
        State("device-clock", "data"),
        prevent_initial_call=True,
    )(refresh_devices)

    app.callback(
        Output("devices-revoke-modal", "opened"),
        Output("devices-revoke-text", "children"),
        Output("devices-revoke-id", "data"),
        Output("devices-revoke-feedback", "children"),
        Output("devices-feedback", "children", allow_duplicate=True),
        Input({"type": "devices-revoke", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )(open_revoke)

    app.clientside_callback(
        ClientsideFunction("hoopster", "close_modal"),
        Output("devices-revoke-modal", "opened", allow_duplicate=True),
        Input("devices-revoke-cancel", "n_clicks"),
        prevent_initial_call=True,
    )

    app.callback(
        Output("devices-list", "children", allow_duplicate=True),
        Output("devices-revoke-modal", "opened", allow_duplicate=True),
        Output("devices-revoke-feedback", "children", allow_duplicate=True),
        Output("devices-status", "children"),
        Output("devices-feedback", "children", allow_duplicate=True),
        Input("devices-revoke-confirm", "n_clicks"),
        State("devices-revoke-id", "data"),
        State("devices-hide-test", "checked"),
        State("device-clock", "data"),
        prevent_initial_call=True,
    )(confirm_revoke)


def _register_enroll(app: Dash, settings: AppSettings) -> None:
    """
    Description:
        Registers the Add a device callbacks.

    Parameters:
        app: the Dash application.
        settings: the application settings.

    Return Values:
        None.

    Notes:
        create_code is the first writer of enroll-result, enroll-feedback and enroll-status;
        cancel_code and turn_off_setup use allow_duplicate. The setup dialog opens and
        cancels clientside.
    """

    def create_code_callback(
        n_clicks: int | None, password: str | None, clock: object
    ) -> tuple[object, str, str, object]:
        """
        Description:
            Dash wrapper of create_code with the settings.

        Parameters:
            n_clicks: enroll-create.n_clicks.
            password: enroll-password.value. Never logged.
            clock: device-clock.data.

        Return Values:
            tuple[object, str, str, object]: see create_code.

        Notes:
            None.
        """
        return create_code(n_clicks, password, clock, settings)

    app.callback(
        Output("enroll-result", "children"),
        Output("enroll-password", "value"),
        Output("enroll-feedback", "children"),
        Output("enroll-status", "children"),
        Input("enroll-create", "n_clicks"),
        State("enroll-password", "value"),
        State("device-clock", "data"),
        prevent_initial_call=True,
    )(create_code_callback)

    app.callback(
        Output("enroll-status", "children", allow_duplicate=True),
        Output("enroll-result", "children", allow_duplicate=True),
        Output("enroll-feedback", "children", allow_duplicate=True),
        Input("enroll-cancel", "n_clicks"),
        State("device-clock", "data"),
        prevent_initial_call=True,
    )(cancel_code)

    app.clientside_callback(
        ClientsideFunction("hoopster", "open_drawer"),
        Output("enroll-setup-modal", "opened"),
        Input("enroll-setup-off", "n_clicks"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        ClientsideFunction("hoopster", "close_modal"),
        Output("enroll-setup-modal", "opened", allow_duplicate=True),
        Input("enroll-setup-cancel", "n_clicks"),
        prevent_initial_call=True,
    )
    app.callback(
        Output("enroll-status", "children", allow_duplicate=True),
        Output("enroll-setup-modal", "opened", allow_duplicate=True),
        Output("enroll-setup-feedback", "children"),
        Output("enroll-setup-block", "children"),
        Input("enroll-setup-confirm", "n_clicks"),
        State("device-clock", "data"),
        prevent_initial_call=True,
    )(turn_off_setup)


def _register_password(app: Dash, settings: AppSettings) -> None:
    """
    Description:
        Registers the Password & sign-in callbacks.

    Parameters:
        app: the Dash application.
        settings: the application settings.

    Return Values:
        None.

    Notes:
        auth-state lives in the shell and is written by other callbacks, so
        sign_out_everywhere uses allow_duplicate and ignores runs without a click.
    """

    def change_password_callback(
        n_clicks: int | None, current: str | None, new: str | None, confirm: str | None
    ) -> tuple[str, str, str, str, str]:
        """
        Description:
            Dash wrapper of change_password with the settings.

        Parameters:
            n_clicks: password-change-submit.n_clicks.
            current: password-current.value. Never logged.
            new: password-new.value. Never logged.
            confirm: password-confirm.value. Never logged.

        Return Values:
            tuple[str, str, str, str, str]: see change_password.

        Notes:
            None.
        """
        return change_password(n_clicks, current, new, confirm, settings)

    def reset_other_password_callback(
        n_clicks: int | None,
        account_value: object,
        mine: str | None,
        new: str | None,
        confirm: str | None,
    ) -> tuple[str, str, str, str, str]:
        """
        Description:
            Dash wrapper of reset_other_password with the settings.

        Parameters:
            n_clicks: password-reset-submit.n_clicks.
            account_value: password-reset-account.value.
            mine: password-reset-mine.value. Never logged.
            new: password-reset-new.value. Never logged.
            confirm: password-reset-confirm.value. Never logged.

        Return Values:
            tuple[str, str, str, str, str]: see reset_other_password.

        Notes:
            None.
        """
        return reset_other_password(n_clicks, account_value, mine, new, confirm, settings)

    app.callback(
        Output("password-change-done", "children"),
        Output("password-change-feedback", "children"),
        Output("password-current", "value"),
        Output("password-new", "value"),
        Output("password-confirm", "value"),
        Input("password-change-submit", "n_clicks"),
        State("password-current", "value"),
        State("password-new", "value"),
        State("password-confirm", "value"),
        prevent_initial_call=True,
    )(change_password_callback)

    app.callback(
        Output("password-reset-done", "children"),
        Output("password-reset-feedback", "children"),
        Output("password-reset-mine", "value"),
        Output("password-reset-new", "value"),
        Output("password-reset-confirm", "value"),
        Input("password-reset-submit", "n_clicks"),
        State("password-reset-account", "value"),
        State("password-reset-mine", "value"),
        State("password-reset-new", "value"),
        State("password-reset-confirm", "value"),
        prevent_initial_call=True,
    )(reset_other_password_callback)

    app.clientside_callback(
        ClientsideFunction("hoopster", "open_drawer"),
        Output("signout-all-modal", "opened"),
        Input("signout-all-open", "n_clicks"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        ClientsideFunction("hoopster", "close_modal"),
        Output("signout-all-modal", "opened", allow_duplicate=True),
        Input("signout-all-cancel", "n_clicks"),
        prevent_initial_call=True,
    )
    app.callback(
        Output("auth-state", "data", allow_duplicate=True),
        Output("signout-all-feedback", "children"),
        Input("signout-all-confirm", "n_clicks"),
        prevent_initial_call=True,
    )(sign_out_everywhere)


def register_security_callbacks(app: Dash, settings: AppSettings) -> None:
    """
    Description:
        Registers the Devices, Add a device and Password & sign-in callbacks.

    Parameters:
        app: the Dash application.
        settings: the application settings (the lockout thresholds of the password
            re-entry).

    Return Values:
        None.

    Notes:
        Every server callback has prevent_initial_call and ignores runs without a click.
    """
    _register_devices(app)
    _register_enroll(app, settings)
    _register_password(app, settings)
