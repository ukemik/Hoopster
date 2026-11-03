"""One-time sign-up page of Hoopster: /join/<code> creates one account and adds that device.

Task 2026-10-29_deploy_render_neon (Overseer, 2026-10-30). The link is made on the Overseer's
PC with python -m src.make_signup_link, which stores the code's SHA-256 in
h_system.enrollment_codes with created_by = SYSTEM_USER_ID. Only such codes open this page; a
one-time code made in Settings > Add a device (created_by = a signed-in user) adds a device
only, at /enroll/<code>. On success the account, the device and the used code are written in
one transaction, the device cookie is set and the new user is signed in. REQ-001, REQ-069.
"""

import logging
import time
from datetime import timedelta
from typing import cast

from flask import Flask, Response, render_template_string, request
from flask import session as flask_session
from flask_login import login_user
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from config.app_config import (
    JOIN_ENDPOINT,
    MIN_PASSWORD_LENGTH,
    PASSWORD_HASH_METHOD,
    REMEMBER_ME_DAYS,
    SYSTEM_USER_ID,
    AppSettings,
)
from src import data_handler
from src.app_controller import (
    PLAYER_SESSION_KEY,
    hash_device_key,
    log_failure,
    new_device_key,
    plain_not_found,
    run_write,
)
from src.controller_access import (
    MAX_DEVICE_NAME_LENGTH,
    UNKNOWN_CLIENT,
    AppUser,
    device_cookie_redirect,
    enrollment_throttled,
    is_one_time_code_shape,
    one_time_code_hash,
    record_wrong_code,
    suggest_device_name,
)
from src.data_access import (
    find_open_enrollment_code,
    get_login_user_by_email,
    insert_device,
    insert_user,
    lock_open_enrollment_code,
    mark_enrollment_code_used,
    record_successful_login,
)
from src.data_players import get_default_player_id
from src.layouts_access import SIGNUP_FORM_TEMPLATE
from src.setup_accounts import display_name_problem, email_problem, password_problem

logger = logging.getLogger(__name__)

JOIN_PATH = "/join/<code>"
MESSAGE_DEVICE_NAME = "Enter a device name (up to 100 characters)."
MESSAGE_EMAIL_TAKEN = "An account with that email already exists. Use another email."
MESSAGE_FAILED = "Could not create the account. Reference: {ref}"


# --- Pure helpers ---

def signup_form_problem(
    name: str, email: str, password: str, password_again: str, device_name: str
) -> str | None:
    """
    Description:
        Checks the sign-up form with the setup script's rules.

    Parameters:
        name: the stripped display name.
        email: the stripped email.
        password: the password. Never logged.
        password_again: the confirmation. Never logged.
        device_name: the stripped device name.

    Return Values:
        str | None: the first problem found (never containing a password), or None.

    Notes:
        Pure. Mirrors ck_users_email, ck_users_display_name and ck_devices_name.
    """
    for issue in (
        display_name_problem(name),
        email_problem(email, None),
        password_problem(password, password_again),
    ):
        if issue is not None:
            return issue
    if not device_name or len(device_name) > MAX_DEVICE_NAME_LENGTH:
        return MESSAGE_DEVICE_NAME
    return None


def is_signup_code_row(row: dict[str, object] | None) -> bool:
    """
    Description:
        Whether an open enrollment-code row is a sign-up link (made by make_signup_link).

    Parameters:
        row: the find_open_enrollment_code / lock_open_enrollment_code row, or None.

    Return Values:
        bool: True only for an open code created by SYSTEM_USER_ID.

    Notes:
        Pure. Codes made in Settings carry the signed-in user as created_by.
    """
    return row is not None and row.get("created_by") == SYSTEM_USER_ID


# --- View ---

def _render_signup_form(
    name: str, email: str, device_name: str, message: str | None
) -> str:
    """
    Description:
        Renders the sign-up page (autoescaped); the passwords are never refilled.

    Parameters:
        name: the display name to prefill.
        email: the email to prefill.
        device_name: the device name to prefill.
        message: an error line under the form, or None.

    Return Values:
        str: the HTML page.

    Notes:
        None.
    """
    return render_template_string(
        SIGNUP_FORM_TEMPLATE,
        name=name,
        email=email,
        device_name=device_name,
        min_password=MIN_PASSWORD_LENGTH,
        message=message,
    )


def _signup_code_open(code: str) -> bool:
    """
    Description:
        Whether the URL carries an open sign-up code.

    Parameters:
        code: the code from the URL. Never logged.

    Return Values:
        bool: True for an open (unused, unexpired, uncancelled) code made by
        make_signup_link.

    Notes:
        Read-only; the POST re-checks under lock.
    """
    if not is_one_time_code_shape(code):
        return False
    row = find_open_enrollment_code(data_handler.get_session(), one_time_code_hash(code))
    return is_signup_code_row(row)


def _create_account(
    code: str, name: str, email: str, password: str, device_name: str, settings: AppSettings
) -> Response | str:
    """
    Description:
        Writes the account, the device and the used code in one transaction, then signs the
        new user in and returns the redirect that sets the device cookie.

    Parameters:
        code: the sign-up code. Never logged.
        name: the checked display name.
        email: the checked email.
        password: the checked password. Never logged; only its scrypt hash is stored.
        device_name: the checked device name.
        settings: the application settings.

    Return Values:
        Response | str: the redirect to "/" with the cookie, the plain 404 when the code was
        used meanwhile, or the form with a reference when the write failed.

    Notes:
        Acting user SYSTEM_USER_ID, as for every write made before sign-in.
    """
    password_hash = generate_password_hash(password, method=PASSWORD_HASH_METHOD)
    raw_key = new_device_key()
    key_hash = hash_device_key(raw_key)
    code_hash = one_time_code_hash(code)

    def work(session: Session) -> int | None:
        """
        Description:
            Lock the code, add the account and the device, mark the code used.

        Parameters:
            session: the write session.

        Return Values:
            int | None: the new user id; None (nothing written) when the code is no longer
            an open sign-up code.

        Notes:
            None.
        """
        code_row = lock_open_enrollment_code(session, code_hash)
        if not is_signup_code_row(code_row):
            return None
        code_id = cast(int, cast(dict[str, object], code_row)["enrollment_code_id"])
        user_id = insert_user(session, email, name, password_hash)
        device_id = insert_device(
            session, device_name, key_hash, enrolled_by_user_id=user_id, enrollment_code_id=code_id
        )
        mark_enrollment_code_used(session, code_id)
        record_successful_login(session, user_id, device_id)
        return user_id

    result = run_write(SYSTEM_USER_ID, work, "sign up")
    if result.ok and result.value is None:
        logger.warning("Sign-up code was no longer open")
        return plain_not_found()
    if not result.ok or result.value is None:
        return _render_signup_form(name, email, device_name, MESSAGE_FAILED.format(ref=result.ref))

    user_id = result.value
    login_user(AppUser(user_id, name, 1), remember=True, duration=timedelta(days=REMEMBER_ME_DAYS))
    flask_session.pop(PLAYER_SESSION_KEY, None)
    player_id = get_default_player_id(data_handler.get_session())
    if player_id is not None:
        flask_session[PLAYER_SESSION_KEY] = player_id
    logger.info("Signed up user %s and added device %r", user_id, device_name)
    return device_cookie_redirect(raw_key, settings)


def join_with_code(code: str, settings: AppSettings) -> Response | str:
    """
    Description:
        The plain-Flask view for /join/<code>: GET shows the sign-up form, POST creates the
        account and adds the device. A throttled client or a code that is not an open
        sign-up code gets the plain 404.

    Parameters:
        code: the code from the URL. Never logged.
        settings: the application settings.

    Return Values:
        Response | str: the form, a redirect, or the plain 404.

    Notes:
        A request boundary: a failed lookup is logged and answered with the 404. Wrong codes
        share the enrollment page's in-memory throttle; form mistakes do not count.
    """
    client = request.remote_addr or UNKNOWN_CLIENT
    now = time.monotonic()
    if enrollment_throttled(client, now):
        logger.warning("Sign-up refused for %s: too many wrong codes", client)
        return plain_not_found()
    try:
        is_open = _signup_code_open(code)
    except Exception as exc:
        log_failure(logger, exc, "Sign-up code lookup failed for %s", client)
        return plain_not_found()
    if not is_open:
        record_wrong_code(client, now)
        logger.warning("Wrong, used or expired sign-up code from %s", client)
        return plain_not_found()

    if request.method != "POST":
        suggested = suggest_device_name(request.headers.get("User-Agent", ""))
        return _render_signup_form("", "", suggested, None)

    name = (request.form.get("display_name") or "").strip()
    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""
    password_again = request.form.get("password2") or ""
    device_name = (request.form.get("device_name") or "").strip()
    issue = signup_form_problem(name, email, password, password_again, device_name)
    if issue is None:
        try:
            if get_login_user_by_email(data_handler.get_session(), email) is not None:
                issue = MESSAGE_EMAIL_TAKEN
        except Exception as exc:
            log_failure(logger, exc, "Sign-up email check failed for %s", client)
            return plain_not_found()
    if issue is not None:
        return _render_signup_form(
            name[:MAX_DEVICE_NAME_LENGTH], email, device_name[:MAX_DEVICE_NAME_LENGTH], issue
        )
    return _create_account(code, name, email, password, device_name, settings)


# --- Registration ---

def register_signup(server: Flask, settings: AppSettings) -> None:
    """
    Description:
        Adds the /join/<code> route.

    Parameters:
        server: the Flask server.
        settings: the application settings.

    Return Values:
        None.

    Notes:
        The request guard exempts JOIN_ENDPOINT (no device cookie yet) and masks the code in
        the log (loggable_path).
    """

    def join_view(code: str) -> Response | str:
        """
        Description:
            Flask view for /join/<code>; delegates to join_with_code.

        Parameters:
            code: the code from the URL.

        Return Values:
            Response | str: see join_with_code.

        Notes:
            None.
        """
        return join_with_code(code, settings)

    server.add_url_rule(JOIN_PATH, endpoint=JOIN_ENDPOINT, view_func=join_view, methods=["GET", "POST"])
