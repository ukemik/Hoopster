"""Access controller of Hoopster: Flask-Login, device enrollment, sign in and log out.

REQ-001 (accounts, remember-me, log out), REQ-002 (sign-in required), REQ-069 (enrollment
through the setup link or a one-time code from Settings), REQ-070 (login lockout, also applied
to the password re-entry of Settings, verify_current_password). Every write goes through
app_controller.run_write.
"""

import functools
import hashlib
import logging
import math
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import cast

from dash import Dash, Input, Output, State, no_update
from dash.exceptions import PreventUpdate
from flask import Flask, Response, g, redirect, render_template_string, request
from flask import session as flask_session
from flask_login import LoginManager, UserMixin, login_user, logout_user
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from config.app_config import (
    DEVICE_COOKIE_MAX_AGE_DAYS,
    DEVICE_COOKIE_NAME,
    ENROLL_ENDPOINT,
    ENROLLMENT_CODE_LENGTH,
    ENROLLMENT_CODE_PATTERN,
    ENROLL_MAX_WRONG_CODES,
    ENROLL_WRONG_WINDOW_MINUTES,
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
    new_reference_code,
    plain_not_found,
    plain_text_response,
    run_write,
    sign_device_key,
)
from src.data_access import (
    find_open_enrollment_code,
    get_account,
    get_enrollment_settings,
    get_lockout_status,
    get_login_user_by_email,
    get_user_for_session,
    insert_device,
    insert_login_attempt,
    lock_open_enrollment_code,
    mark_enrollment_code_used,
    record_successful_login,
)
from src.data_players import get_default_player_id
from src.layouts_access import ENROLL_FORM_TEMPLATE

logger = logging.getLogger(__name__)

SECONDS_PER_MINUTE = 60
SECONDS_PER_DAY = 86400
MILLISECONDS_PER_SECOND = 1000

# h_system.users.email and h_system.login_attempts.attempted_email hold at most 200 characters.
MAX_EMAIL_LENGTH = 200


# --- Signed-in user (REQ-001; plan §3.4) ---

@dataclass(frozen=True)
class AppUser(UserMixin):
    """
    Description:
        The signed-in user as Flask-Login sees it.

    Parameters:
        user_id: h_system.users.user_id; the acting user of every write made by this user.
        display_name: the name shown in the app.
        session_version: the account's session_version when the user signed in.

    Return Values:
        Not applicable (data class).

    Notes:
        get_id() returns "user_id:session_version", so the remember cookie carries the
        version and a password reset (which bumps it) signs the account out everywhere.
        Use user_id, never get_id(), as the acting user (Context Adaptation Table).
    """

    user_id: int
    display_name: str
    session_version: int

    def get_id(self) -> str:
        """
        Description:
            The identifier Flask-Login stores in the session and the remember cookie.

        Parameters:
            None.

        Return Values:
            str: "<user_id>:<session_version>".

        Notes:
            Parsed back by load_user.
        """
        return f"{self.user_id}:{self.session_version}"


USER_KEY_PATTERN = re.compile(r"^(\d+):(\d+)$")


def load_user(user_key: str) -> AppUser | None:
    """
    Description:
        Flask-Login user loader: turns the stored "user_id:session_version" back into the
        user, only if the account is live, active and still on that session version.

    Parameters:
        user_key: the identifier from the Flask session or the remember cookie.

    Return Values:
        AppUser | None: the user, or None (treated as signed out).

    Notes:
        Anything malformed gives None. The query already excludes deleted and system rows.
    """
    match = USER_KEY_PATTERN.match(user_key or "")
    if match is None:
        return None
    user_id = int(match.group(1))
    session_version = int(match.group(2))
    row = get_user_for_session(data_handler.get_session(), user_id)
    if row is None or not row["is_active"]:
        return None
    if int(row["session_version"]) != session_version:
        return None
    return AppUser(user_id, str(row["display_name"]), session_version)


@functools.cache
def dummy_password_hash() -> str:
    """
    Description:
        A scrypt hash of a random throwaway string, checked when the email matches no
        account so an unknown email takes as long as a known one (plan §3.4 step 4).

    Parameters:
        None.

    Return Values:
        str: the hash; the same value for the life of the process.

    Notes:
        Built once per process (register_access warms it at startup) instead of being a
        literal in the code: no shell was available to generate one, and a random input
        means no password-like literal exists in the source. It guards no account.
    """
    return generate_password_hash(secrets.token_urlsafe(16), method=PASSWORD_HASH_METHOD)


# --- Enrollment (REQ-069; plan §3.3) ---

# Process-wide by design: one worker process (scoping C.9, the recorded exception to COD-17-04).
_wrong_codes: dict[str, deque[float]] = {}
_wrong_codes_lock = threading.Lock()

ENROLL_WINDOW_SECONDS = ENROLL_WRONG_WINDOW_MINUTES * SECONDS_PER_MINUTE
MAX_DEVICE_NAME_LENGTH = 100
DEVICE_NAME_MESSAGE = "Enter a device name (up to 100 characters)."
UNKNOWN_CLIENT = "unknown"

# (user-agent marker, suggested name), checked in order: iPhone and iPad user agents also
# contain "Mac OS X", so they come before "Macintosh".
DEVICE_NAME_HINTS: tuple[tuple[str, str], ...] = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android phone"),
    ("Windows", "Windows PC"),
    ("Macintosh", "Mac"),
)
DEFAULT_DEVICE_NAME = "Device"


def suggest_device_name(user_agent: str) -> str:
    """
    Description:
        Suggests a device name from the browser's user agent.

    Parameters:
        user_agent: the User-Agent header (may be empty).

    Return Values:
        str: "iPhone", "iPad", "Android phone", "Windows PC", "Mac" or "Device".

    Notes:
        Pure.
    """
    for marker, name in DEVICE_NAME_HINTS:
        if marker in (user_agent or ""):
            return name
    return DEFAULT_DEVICE_NAME


def _prune_wrong_codes(attempts: deque[float], now: float) -> None:
    """
    Description:
        Drops wrong-code timestamps older than the throttle window.

    Parameters:
        attempts: one client's wrong-code timestamps, oldest first.
        now: the current monotonic time in seconds.

    Return Values:
        None.

    Notes:
        Call with _wrong_codes_lock held.
    """
    while attempts and attempts[0] <= now - ENROLL_WINDOW_SECONDS:
        attempts.popleft()


def enrollment_throttled(ip: str, now: float) -> bool:
    """
    Description:
        Whether a client has entered too many wrong enrollment codes recently.

    Parameters:
        ip: the client address (request.remote_addr).
        now: the current monotonic time in seconds.

    Return Values:
        bool: True when the client has ENROLL_MAX_WRONG_CODES or more wrong codes within
        ENROLL_WRONG_WINDOW_MINUTES.

    Notes:
        In-memory and per process; it resets when the application restarts.
    """
    with _wrong_codes_lock:
        attempts = _wrong_codes.get(ip)
        if attempts is None:
            return False
        _prune_wrong_codes(attempts, now)
        if not attempts:
            del _wrong_codes[ip]
            return False
        return len(attempts) >= ENROLL_MAX_WRONG_CODES


def record_wrong_code(ip: str, now: float) -> None:
    """
    Description:
        Records one wrong enrollment code for a client.

    Parameters:
        ip: the client address (request.remote_addr).
        now: the current monotonic time in seconds.

    Return Values:
        None.

    Notes:
        None.
    """
    with _wrong_codes_lock:
        attempts = _wrong_codes.setdefault(ip, deque())
        _prune_wrong_codes(attempts, now)
        attempts.append(now)


def enrollment_code_valid(code: str) -> bool:
    """
    Description:
        Checks a setup-link code against the stored hash, if the setup link is on.

    Parameters:
        code: the code from the enrollment URL.

    Return Values:
        bool: True only when enrollment is enabled, a hash is stored and the code matches.

    Notes:
        Reads h_system.enrollment_settings. Never logs the code. The setup-link half of
        match_enrollment_code.
    """
    enrollment = get_enrollment_settings(data_handler.get_session())
    code_hash = enrollment["code_hash"]
    if not enrollment["is_enabled"] or not isinstance(code_hash, str) or not code_hash:
        return False
    return check_password_hash(code_hash, code)


# The two kinds of enrollment code (plan §3 Enrollment).
CODE_KIND_ONE_TIME = "one_time"
CODE_KIND_SETUP = "setup"


def one_time_code_hash(code: str) -> str:
    """
    Description:
        The stored form of a one-time enrollment code.

    Parameters:
        code: the code (ASCII, URL-safe characters).

    Return Values:
        str: lower-case hex SHA-256 of the code's ASCII bytes (64 characters), as
        ck_enrollment_codes_hash requires.

    Notes:
        Pure. A 192-bit random value needs no slow hash, and SHA-256 allows an indexed
        lookup (D3). Raises UnicodeEncodeError for a non-ASCII code; callers check the
        pattern first.
    """
    return hashlib.sha256(code.encode("ascii")).hexdigest()


def is_one_time_code_shape(code: str) -> bool:
    """
    Description:
        Whether a code has the shape of a one-time code: ENROLLMENT_CODE_LENGTH URL-safe
        characters.

    Parameters:
        code: the code from the URL.

    Return Values:
        bool: True for the shape secrets.token_urlsafe(ENROLLMENT_CODE_BYTES) produces.

    Notes:
        Pure. Shape only; the database decides whether the code is open.
    """
    if len(code) != ENROLLMENT_CODE_LENGTH:
        return False
    return re.fullmatch(ENROLLMENT_CODE_PATTERN, code) is not None


def match_enrollment_code(code: str) -> str | None:
    """
    Description:
        Decides which kind of enrollment code a URL carries: an open one-time code from
        Settings, else the setup-link code, else none.

    Parameters:
        code: the code from the enrollment URL. Never logged.

    Return Values:
        str | None: CODE_KIND_ONE_TIME, CODE_KIND_SETUP, or None for a wrong, used,
        expired or cancelled code (all count as wrong codes, D5).

    Notes:
        Read-only. The one-time lookup comes first because it is an indexed hash lookup;
        the setup check (a slow scrypt hash) runs only when that finds nothing.
    """
    if is_one_time_code_shape(code):
        found = find_open_enrollment_code(data_handler.get_session(), one_time_code_hash(code))
        if found is not None:
            return CODE_KIND_ONE_TIME
    if enrollment_code_valid(code):
        return CODE_KIND_SETUP
    return None


def _render_enroll_form(device_name: str, message: str | None) -> str:
    """
    Description:
        Renders the enrollment page (autoescaped).

    Parameters:
        device_name: the value to prefill in the device-name field.
        message: an error line to show under the form, or None.

    Return Values:
        str: the HTML page.

    Notes:
        None.
    """
    return render_template_string(
        ENROLL_FORM_TEMPLATE, device_name=device_name, message=message
    )


def _insert_with_one_time_code(
    session: Session, device_name: str, key_hash: str, code_hash: str
) -> int | None:
    """
    Description:
        The one-time-code enrollment writes: lock the open code, add the device with the
        code's creator and id, and mark the code used.

    Parameters:
        session: the write session (inside run_write, acting user SYSTEM_USER_ID).
        device_name: the checked device name.
        key_hash: SHA-256 of the new raw device key.
        code_hash: SHA-256 of the one-time code.

    Return Values:
        int | None: the new device id, or None (nothing written) when the code was used,
        cancelled or expired after the GET.

    Notes:
        enrolled_by_user_id and enrollment_code_id come from the locked row, never from the
        request (Context Adaptation Table).
    """
    code_row = lock_open_enrollment_code(session, code_hash)
    if code_row is None:
        return None
    code_id = cast(int, code_row["enrollment_code_id"])
    device_id = insert_device(
        session,
        device_name,
        key_hash,
        enrolled_by_user_id=cast(int, code_row["created_by"]),
        enrollment_code_id=code_id,
    )
    mark_enrollment_code_used(session, code_id)
    return device_id


def _enroll_this_device(
    settings: AppSettings, code: str, kind: str, client: str, now: float
) -> Response | str:
    """
    Description:
        Handles a valid enrollment POST: checks the device name, stores the new device's key
        hash (with a one-time code: also its creator and the code, which is marked used)
        and sets the signed device cookie.

    Parameters:
        settings: the application settings (device-key secret, production flag).
        code: the code from the URL. Never logged.
        kind: CODE_KIND_ONE_TIME or CODE_KIND_SETUP (match_enrollment_code).
        client: the client address, for the wrong-code throttle.
        now: the monotonic time of the request, for the throttle.

    Return Values:
        Response | str: a 303 redirect to "/" with the cookie, the form again with a
        message, a plain 404 (one-time code used meanwhile; counted as a wrong code), or a
        plain 500 with a reference.

    Notes:
        The raw key exists only in the signed cookie; the database stores its hash. A
        one-time enrollment is one transaction as user 0 (plan §3).
    """
    device_name = (request.form.get("device_name") or "").strip()
    if not device_name or len(device_name) > MAX_DEVICE_NAME_LENGTH:
        return _render_enroll_form(device_name[:MAX_DEVICE_NAME_LENGTH], DEVICE_NAME_MESSAGE)

    raw_key = new_device_key()
    key_hash = hash_device_key(raw_key)
    code_hash = one_time_code_hash(code) if kind == CODE_KIND_ONE_TIME else None

    def work(session: Session) -> int | None:
        """
        Description:
            The enrollment writes of the code's kind.

        Parameters:
            session: the write session.

        Return Values:
            int | None: the new device id; None when a one-time code is no longer open.

        Notes:
            None.
        """
        if code_hash is not None:
            return _insert_with_one_time_code(session, device_name, key_hash, code_hash)
        return insert_device(session, device_name, key_hash)

    result = run_write(SYSTEM_USER_ID, work, "enroll device")
    if result.ok and result.value is None:
        record_wrong_code(client, now)
        logger.warning("One-time enrollment code from %s was no longer open", client)
        return plain_not_found()
    if not result.ok:
        return plain_text_response(
            f"Could not add this device. Reference: {result.ref}", 500
        )

    logger.info("Enrolled device %s named %r (%s code)", result.value, device_name, kind)
    response = redirect("/", code=303)
    response.set_cookie(
        DEVICE_COOKIE_NAME,
        sign_device_key(raw_key, settings.device_key_secret),
        max_age=DEVICE_COOKIE_MAX_AGE_DAYS * SECONDS_PER_DAY,
        httponly=True,
        samesite="Lax",
        secure=settings.is_production,
        path="/",
    )
    return response


def enroll_device(code: str, settings: AppSettings) -> Response | str:
    """
    Description:
        The plain-Flask enrollment view for /enroll/<code> (GET shows the form, POST adds
        the device). A throttled client or a wrong code gets the plain 404.

    Parameters:
        code: the code from the URL. Never logged.
        settings: the application settings.

    Return Values:
        Response | str: the form page, a redirect, or a plain 404 / 500.

    Notes:
        A request boundary: a failed code lookup is logged and answered with the 404.
        The throttle is checked before any hashing. Two code kinds are accepted
        (match_enrollment_code): a one-time code from Settings > Add a device, and the
        setup-link code from the setup script.
    """
    client = request.remote_addr or UNKNOWN_CLIENT
    now = time.monotonic()
    if enrollment_throttled(client, now):
        logger.warning("Enrollment refused for %s: too many wrong codes", client)
        return plain_not_found()
    try:
        kind = match_enrollment_code(code)
    except Exception as exc:
        log_failure(logger, exc, "Enrollment code lookup failed for %s", client)
        return plain_not_found()
    if kind is None:
        record_wrong_code(client, now)
        logger.warning("Wrong or disabled enrollment code from %s", client)
        return plain_not_found()

    if request.method == "POST":
        return _enroll_this_device(settings, code, kind, client, now)
    suggested_name = suggest_device_name(request.headers.get("User-Agent", ""))
    return _render_enroll_form(suggested_name, None)


# --- Sign in and log out (REQ-001, REQ-070; plan §3.4) ---

MESSAGE_MISSING = "Enter your email and password."
MESSAGE_INCORRECT = "Email or password is incorrect."
MESSAGE_LOCKED = "Too many failed sign-in attempts. Try again in {minutes} min."
MESSAGE_ERROR = "Something went wrong. Reference: {ref}"

# The login-password value every sign-in outcome writes: the field is always cleared.
CLEARED_PASSWORD = ""
# What a log line shows in place of an email's hidden characters.
EMAIL_MASK = "***"


def mask_email(email: str | None) -> str:
    """
    Description:
        Masks an email for log output: the first character of the local part, "***", then
        "@" and the domain, e.g. "j***@gmail.com".

    Parameters:
        email: the email as typed or normalised; may be None or empty.

    Return Values:
        str: the masked email, or "***" when the value is empty or has no "@".

    Notes:
        Pure. The domain follows the last "@". An empty local part gives "***@domain".
        Only log output is masked; h_system.login_attempts stores the normalised email.
    """
    if not email or "@" not in email:
        return EMAIL_MASK
    local_part, _, domain = email.rpartition("@")
    return f"{local_part[:1]}{EMAIL_MASK}@{domain}"


def _auth_changed() -> dict[str, int]:
    """
    Description:
        A new auth-state value, so the router re-renders after sign-in or log out.

    Parameters:
        None.

    Return Values:
        dict[str, int]: {"changed_at": <milliseconds since the epoch>}.

    Notes:
        None.
    """
    return {"changed_at": int(time.time() * MILLISECONDS_PER_SECOND)}


def _lock_message(
    status: dict[str, object], settings: AppSettings, template: str = MESSAGE_LOCKED
) -> str:
    """
    Description:
        The lockout message with the minutes left, rounded up, at least 1.

    Parameters:
        status: the lockout row {"is_locked", "locked_until", "recent_failures"}.
        settings: the application settings (lock length when locked_until is missing).
        template: the message with a {minutes} field; the sign-in text by default, the
            password re-entry text for Settings (task 2026-10-23_settings_hub).

    Return Values:
        str: e.g. "Too many failed sign-in attempts. Try again in {n} min."

    Notes:
        The server clock is used only for this duration (Context Adaptation Table).
    """
    locked_until = status.get("locked_until")
    if isinstance(locked_until, datetime):
        seconds_left = (locked_until - datetime.now(timezone.utc)).total_seconds()
        minutes = max(1, math.ceil(seconds_left / SECONDS_PER_MINUTE))
    else:
        minutes = settings.login_lock_minutes
    return template.format(minutes=minutes)


def _lockout_status(device_id: int, email: str, settings: AppSettings) -> dict[str, object]:
    """
    Description:
        Reads the lockout state of this device + email pair (REQ-070).

    Parameters:
        device_id: h_system.devices.device_id from flask.g.device_id. Never a user id.
        email: the normalised email.
        settings: the application settings (the LOGIN_* thresholds).

    Return Values:
        dict[str, object]: {"is_locked", "locked_until", "recent_failures"}.

    Notes:
        None.
    """
    return get_lockout_status(
        data_handler.get_session(),
        device_id,
        email,
        settings.login_max_failures,
        settings.login_window_minutes,
        settings.login_lock_minutes,
    )


def _password_matches(user: dict[str, object] | None, password: str) -> bool:
    """
    Description:
        Checks the password against the account, spending the same hashing time when no
        account matched.

    Parameters:
        user: the get_login_user_by_email row, or None.
        password: the password typed. Never logged.

    Return Values:
        bool: True only for an existing, active account whose hash matches.

    Notes:
        An inactive account is still hashed, so its timing matches an active one.
    """
    if user is None:
        check_password_hash(dummy_password_hash(), password)
        return False
    matches = check_password_hash(str(user["password_hash"]), password)
    return bool(user["is_active"]) and matches


def _record_attempt(
    device_id: int, email: str, user: dict[str, object] | None, succeeded: bool
) -> str | None:
    """
    Description:
        Records the sign-in attempt and, on success, the user's last login and the device's
        enrolling user, in one transaction.

    Parameters:
        device_id: h_system.devices.device_id from flask.g.device_id.
        email: the normalised email.
        user: the matched account row, or None.
        succeeded: whether the password was accepted.

    Return Values:
        str | None: None when saved, else the failure's reference code.

    Notes:
        The acting user is the matched user on success, else SYSTEM_USER_ID (Context
        Adaptation Table).
    """
    matched_user_id = int(user["user_id"]) if user is not None else None

    def work(session: Session) -> None:
        """
        Description:
            The sign-in writes.

        Parameters:
            session: the write session.

        Return Values:
            None.

        Notes:
            None.
        """
        insert_login_attempt(session, device_id, email, matched_user_id, succeeded)
        if succeeded and matched_user_id is not None:
            record_successful_login(session, matched_user_id, device_id)

    acting_user_id = SYSTEM_USER_ID
    if succeeded and matched_user_id is not None:
        acting_user_id = matched_user_id
    result = run_write(acting_user_id, work, "login attempt")
    return None if result.ok else result.ref


def attempt_login(
    email: str | None, password: str | None, settings: AppSettings
) -> tuple[str, object, object]:
    """
    Description:
        The sign-in steps of plan §3.4, in order: normalise, lockout check, user lookup,
        password check, record the attempt, then sign in or explain the failure.

    Parameters:
        email: the email typed.
        password: the password typed. Never logged (COD-4-05).
        settings: the application settings.

    Return Values:
        tuple[str, object, object]: login-feedback text, login-password value (always
        cleared, on success and on every failure) and auth-state (new value on success,
        else no_update).

    Notes:
        While locked, nothing is checked or recorded (REQ-070). A boundary: an unexpected
        failure shows a reference instead of raising (COD-4-04). Log lines show the email
        masked (mask_email), never in full.
    """
    normalised_email = (email or "").strip().lower()
    if not normalised_email or not password:
        return MESSAGE_MISSING, CLEARED_PASSWORD, no_update
    if len(normalised_email) > MAX_EMAIL_LENGTH:
        logger.warning("Sign-in refused: email longer than %s characters", MAX_EMAIL_LENGTH)
        return MESSAGE_INCORRECT, CLEARED_PASSWORD, no_update

    device_id = g.get("device_id")
    try:
        if not isinstance(device_id, int):
            raise RuntimeError("Sign-in without a verified device")
        status = _lockout_status(device_id, normalised_email, settings)
        if status["is_locked"]:
            logger.warning(
                "Sign-in blocked by lockout for %s on device %s",
                mask_email(normalised_email),
                device_id,
            )
            return _lock_message(status, settings), CLEARED_PASSWORD, no_update

        user = get_login_user_by_email(data_handler.get_session(), normalised_email)
        succeeded = _password_matches(user, password)
        ref = _record_attempt(device_id, normalised_email, user, succeeded)
        if ref is not None:
            return MESSAGE_ERROR.format(ref=ref), CLEARED_PASSWORD, no_update

        if succeeded and user is not None:
            app_user = AppUser(
                int(user["user_id"]), str(user["display_name"]), int(user["session_version"])
            )
            login_user(app_user, remember=True, duration=timedelta(days=REMEMBER_ME_DAYS))
            flask_session.pop(PLAYER_SESSION_KEY, None)
            player_id = get_default_player_id(data_handler.get_session())
            if player_id is not None:
                flask_session[PLAYER_SESSION_KEY] = player_id
            logger.info("Signed in user %s on device %s", app_user.user_id, device_id)
            return "", CLEARED_PASSWORD, _auth_changed()

        if user is not None and not user["is_active"]:
            logger.error(
                "Sign-in refused for inactive account %s on device %s",
                mask_email(normalised_email),
                device_id,
            )
        else:
            logger.warning(
                "Failed sign-in for %s on device %s", mask_email(normalised_email), device_id
            )
        status = _lockout_status(device_id, normalised_email, settings)
        if status["is_locked"]:
            return _lock_message(status, settings), CLEARED_PASSWORD, no_update
        return MESSAGE_INCORRECT, CLEARED_PASSWORD, no_update
    except Exception as exc:
        # A failed read was already rolled back by data_handler; a failed write by run_write.
        ref = new_reference_code()
        log_failure(logger, exc, "Sign-in failed (ref %s)", ref)
        return MESSAGE_ERROR.format(ref=ref), CLEARED_PASSWORD, no_update


# --- Password re-entry for Settings (REQ-001, REQ-070, D6; task 2026-10-23_settings_hub) ---

MESSAGE_ENTER_PASSWORD = "Enter your password."
MESSAGE_WRONG_PASSWORD = "Your password is incorrect."
MESSAGE_PASSWORD_LOCKED = "Too many wrong passwords. Try again in {minutes} min."


def _check_and_record_password(
    user_id: int,
    account: dict[str, object],
    password: str,
    device_id: int,
    settings: AppSettings,
) -> str | None:
    """
    Description:
        The lockout check, the password check and the attempt record of
        verify_current_password, for a loaded live account.

    Parameters:
        user_id: the signed-in h_system.users.user_id.
        account: data_access.get_account row of that user.
        password: the password typed. Never logged.
        device_id: flask.g.device_id. Never a user id.
        settings: the application settings (the LOGIN_* thresholds).

    Return Values:
        str | None: None when the password is right, else the message to show.

    Notes:
        Raises on a failed read; verify_current_password turns that into a reference.
    """
    email = str(account["email"]).strip().lower()
    status = _lockout_status(device_id, email, settings)
    if status["is_locked"]:
        logger.warning(
            "Password re-entry blocked by lockout for user %s on device %s", user_id, device_id
        )
        return _lock_message(status, settings, MESSAGE_PASSWORD_LOCKED)
    matches = bool(account["is_active"]) and check_password_hash(
        str(account["password_hash"]), password
    )
    result = run_write(
        user_id,
        lambda session: insert_login_attempt(session, device_id, email, user_id, matches),
        "password re-entry attempt",
    )
    if not result.ok:
        return MESSAGE_ERROR.format(ref=result.ref)
    if matches:
        return None
    logger.warning("Wrong password re-entered by user %s on device %s", user_id, device_id)
    status = _lockout_status(device_id, email, settings)
    if status["is_locked"]:
        return _lock_message(status, settings, MESSAGE_PASSWORD_LOCKED)
    return MESSAGE_WRONG_PASSWORD


def verify_current_password(
    user_id: int, password: str | None, settings: AppSettings
) -> str | None:
    """
    Description:
        Checks the signed-in user's password before a sensitive Settings action (create an
        enrollment code, change or reset a password), with the sign-in lockout (D6).

    Parameters:
        user_id: current_user.user_id (never get_id()).
        password: the password typed. Never logged (COD-4-05).
        settings: the application settings (the LOGIN_* thresholds).

    Return Values:
        str | None: None when the password is right; else "Enter your password.", "Your
        password is incorrect.", "Too many wrong passwords. Try again in {n} min." or
        "Something went wrong. Reference: {ref}".

    Notes:
        A boundary (COD-4-04): unexpected failures give a reference. Every checked attempt
        is recorded in h_system.login_attempts for the device + the account's email, through
        run_write as the user, so wrong re-entries count towards the same lockout as wrong
        sign-ins.
    """
    if not password:
        return MESSAGE_ENTER_PASSWORD
    device_id = g.get("device_id")
    try:
        if not isinstance(device_id, int):
            raise RuntimeError("Password re-entry without a verified device")
        account = get_account(data_handler.get_session(), user_id)
        if account is None:
            raise LookupError(f"account {user_id} is not live")
        return _check_and_record_password(user_id, account, password, device_id, settings)
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Password re-entry failed (ref %s)", ref)
        return MESSAGE_ERROR.format(ref=ref)


def sign_out() -> tuple[dict[str, int], bool]:
    """
    Description:
        Signs the current user out on this device: ends the session, clears the remember
        cookie and forgets the cached player.

    Parameters:
        None.

    Return Values:
        tuple[dict[str, int], bool]: a new auth-state value and False (close the More
        drawer).

    Notes:
        Flask-Login deletes the remember cookie in its after_request hook.
    """
    logout_user()
    flask_session.pop(PLAYER_SESSION_KEY, None)
    return _auth_changed(), False


# --- Registration ---

def register_access(app: Dash, server: Flask, settings: AppSettings) -> None:
    """
    Description:
        Sets up Flask-Login, adds the enrollment route, and registers the sign-in and
        log-out callbacks.

    Parameters:
        app: the Dash application.
        server: its Flask server.
        settings: the application settings.

    Return Values:
        None.

    Notes:
        No login_view: the router shows the sign-in page on every path while signed out.
        The enrollment rule /enroll/<code> is more specific than Dash's /<path:path>, so it
        wins; the request guard exempts it by endpoint. Warms dummy_password_hash so the
        first unknown-email sign-in is not slower than later ones.
    """
    login_manager = LoginManager()
    login_manager.init_app(server)
    login_manager.user_loader(load_user)
    dummy_password_hash()

    def enroll_view(code: str) -> Response | str:
        """
        Description:
            Flask view for /enroll/<code>; delegates to enroll_device with the settings.

        Parameters:
            code: the code from the URL.

        Return Values:
            Response | str: see enroll_device.

        Notes:
            None.
        """
        return enroll_device(code, settings)

    server.add_url_rule(
        "/enroll/<code>",
        endpoint=ENROLL_ENDPOINT,
        view_func=enroll_view,
        methods=["GET", "POST"],
    )

    def submit_login(
        n_clicks: int | None, n_submit: int | None, email: str | None, password: str | None
    ) -> tuple[str, object, object]:
        """
        Description:
            The sign-in callback (Sign in button or Enter in the password field).

        Parameters:
            n_clicks: login-submit.n_clicks.
            n_submit: login-password.n_submit.
            email: login-email.value.
            password: login-password.value. Never logged.

        Return Values:
            tuple[str, object, object]: login-feedback, login-password and auth-state.

        Notes:
            See attempt_login. Raises PreventUpdate unless Sign in or Enter actually fired:
            Dash also runs this callback when the router inserts the sign-in page, because
            its Output auth-state lives in the shell, and prevent_initial_call does not stop
            that run (plan §3.4 step 0). The test is on n_clicks and n_submit, not
            callback_context.triggered_id, which still names login-submit.n_clicks on that
            run.
        """
        # The page-insertion run has no click or Enter; auth-state is outside the page.
        if not n_clicks and not n_submit:
            raise PreventUpdate
        return attempt_login(email, password, settings)

    app.callback(
        Output("login-feedback", "children"),
        Output("login-password", "value"),
        Output("auth-state", "data", allow_duplicate=True),
        Input("login-submit", "n_clicks"),
        Input("login-password", "n_submit"),
        State("login-email", "value"),
        State("login-password", "value"),
        prevent_initial_call=True,
    )(submit_login)

    def logout(n_clicks: int | None) -> tuple[dict[str, int], bool]:
        """
        Description:
            The Log out callback (More drawer).

        Parameters:
            n_clicks: nav-logout.n_clicks.

        Return Values:
            tuple[dict[str, int], bool]: auth-state and nav-more-drawer.opened.

        Notes:
            See sign_out.
        """
        return sign_out()

    app.callback(
        Output("auth-state", "data", allow_duplicate=True),
        Output("nav-more-drawer", "opened", allow_duplicate=True),
        Input("nav-logout", "n_clicks"),
        prevent_initial_call=True,
    )(logout)
