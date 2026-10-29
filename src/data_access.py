"""Data access for users, devices, enrollment, login attempts and lockout.

REQ-001, REQ-069, REQ-070, REQ-071 (Settings > Devices, Add a device and Password & sign-in,
task 2026-10-23_settings_hub). Every function takes the request session first and runs its SQL
through data_handler. Write functions never commit: they run inside
app_controller.run_write, after the acting user has been set (COD-6-01, COD-7-01).
"""

import logging
from typing import cast

from sqlalchemy.orm import Session

from src.data_handler import execute, fetch_all, fetch_one

logger = logging.getLogger(__name__)

# h_system.enrollment_settings holds exactly one row, with this id (ck_enrollment_settings_single).
ENROLLMENT_SETTINGS_ID = 1


# --- Devices (REQ-069) ---

GET_LIVE_DEVICE_SQL = """
SELECT device_id, last_seen_at
FROM h_system.devices
WHERE key_hash = :key_hash
  AND revoked_at IS NULL
  AND deleted_at IS NULL
"""

TOUCH_DEVICE_LAST_SEEN_SQL = """
UPDATE h_system.devices
SET last_seen_at = now()
WHERE device_id = :device_id
  AND (last_seen_at IS NULL
       OR last_seen_at < now() - make_interval(mins => CAST(:interval_minutes AS integer)))
"""

INSERT_DEVICE_SQL = """
INSERT INTO h_system.devices (device_name, key_hash, enrolled_by_user_id, enrollment_code_id)
VALUES (:device_name, :key_hash, CAST(:enrolled_by_user_id AS bigint),
        CAST(:enrollment_code_id AS bigint))
RETURNING device_id
"""


def get_live_device_by_key_hash(session: Session, key_hash: str) -> dict[str, object] | None:
    """
    Description:
        Looks up the enrolled, unrevoked, undeleted device whose key hash matches.

    Parameters:
        session: the request session.
        key_hash: lower-case hex SHA-256 of the raw device key from the verified cookie.

    Return Values:
        dict[str, object] | None: {"device_id", "last_seen_at"}, or None if no live device
        has that key.

    Notes:
        Read-only; called by the request guard on every request.
    """
    return fetch_one(session, GET_LIVE_DEVICE_SQL, {"key_hash": key_hash})


def touch_device_last_seen(session: Session, device_id: int, interval_minutes: int) -> int:
    """
    Description:
        Sets the device's last_seen_at to now, but only when it is unset or older than the
        interval, so most requests write nothing.

    Parameters:
        session: the write session (inside run_write).
        device_id: h_system.devices.device_id from flask.g.device_id. Never a user id.
        interval_minutes: minimum minutes between two updates (LAST_SEEN_INTERVAL_MINUTES).

    Return Values:
        int: rows updated, 0 or 1.

    Notes:
        last_seen_user_id is left alone (task 9).
    """
    return execute(
        session,
        TOUCH_DEVICE_LAST_SEEN_SQL,
        {"device_id": device_id, "interval_minutes": interval_minutes},
    )


def insert_device(
    session: Session,
    device_name: str,
    key_hash: str,
    enrolled_by_user_id: int | None = None,
    enrollment_code_id: int | None = None,
) -> int:
    """
    Description:
        Enrolls a new device.

    Parameters:
        session: the write session (inside run_write, acting user SYSTEM_USER_ID).
        device_name: the name entered on the enrollment page, stripped, 1-100 characters.
        key_hash: lower-case hex SHA-256 of the new raw device key.
        enrolled_by_user_id: the h_system.users.user_id who created the one-time code (its
            created_by), or None for the setup link.
        enrollment_code_id: the h_system.enrollment_codes.enrollment_code_id used, or None
            for the setup link.

    Return Values:
        int: the new h_system.devices.device_id.

    Notes:
        enrolled_at defaults to now(). With the setup link enrolled_by_user_id is filled at
        the first sign-in (record_successful_login). Both ids come from the locked code row
        (lock_open_enrollment_code), never from the request (Context Adaptation Table); the
        unique index ux_devices_enrollment_code makes a code single-use.
    """
    row = fetch_one(
        session,
        INSERT_DEVICE_SQL,
        {
            "device_name": device_name,
            "key_hash": key_hash,
            "enrolled_by_user_id": enrolled_by_user_id,
            "enrollment_code_id": enrollment_code_id,
        },
    )
    if row is None:
        logger.error("Device insert returned no id")
        raise RuntimeError("Device insert returned no id")
    return cast(int, row["device_id"])


# --- Devices (REQ-071) ---

# Outcomes of revoke_device.
REVOKE_DONE = "revoked"
REVOKE_LAST_DEVICE = "last_device"
REVOKE_NOT_FOUND = "not_found"

LIST_DEVICES_SQL = """
SELECT d.device_id, d.device_name, d.enrolled_at, eu.display_name AS enrolled_by_name,
       d.last_seen_at, d.revoked_at, ru.display_name AS revoked_by_name
FROM h_system.devices d
LEFT JOIN h_system.users eu ON eu.user_id = d.enrolled_by_user_id
LEFT JOIN h_system.users ru ON ru.user_id = d.revoked_by_user_id
WHERE d.deleted_at IS NULL
ORDER BY (d.revoked_at IS NOT NULL), d.last_seen_at DESC NULLS LAST, d.device_id DESC
"""

LOCK_LIVE_DEVICES_SQL = """
SELECT device_id
FROM h_system.devices
WHERE revoked_at IS NULL
  AND deleted_at IS NULL
ORDER BY device_id
FOR UPDATE
"""

REVOKE_DEVICE_SQL = """
UPDATE h_system.devices
SET revoked_at = now(),
    revoked_by_user_id = :user_id
WHERE device_id = :device_id
  AND revoked_at IS NULL
  AND deleted_at IS NULL
"""


def list_devices(session: Session) -> list[dict[str, object]]:
    """
    Description:
        Lists the enrolled devices for Settings > Devices: unrevoked first, then by last
        seen (newest first, never seen last), then newest device first.

    Parameters:
        session: the request session.

    Return Values:
        list[dict[str, object]]: {"device_id", "device_name", "enrolled_at",
        "enrolled_by_name", "last_seen_at", "revoked_at", "revoked_by_name"} per live
        (not deleted) device; the names are None when unknown.

    Notes:
        Read-only. last_seen_user_id is not read (D9).
    """
    return fetch_all(session, LIST_DEVICES_SQL)


def revoke_device(session: Session, device_id: int, user_id: int) -> str:
    """
    Description:
        Revokes one device, refusing the last unrevoked one (D8).

    Parameters:
        session: the write session (inside run_write, acting user = user_id).
        device_id: the h_system.devices.device_id to revoke, from the devices-revoke-id
            store. Never flask.g.device_id (Context Adaptation Table).
        user_id: the acting h_system.users.user_id, stored as revoked_by_user_id.

    Return Values:
        str: REVOKE_DONE, REVOKE_LAST_DEVICE (nothing written) or REVOKE_NOT_FOUND (the
        device is already revoked or does not exist; nothing written).

    Notes:
        Locks every live unrevoked device row in id order first, so two revokes at once
        cannot both pass the last-device check. Raises RuntimeError if the update does not
        change exactly one row after the check (the caller's transaction rolls back).
    """
    locked_rows = fetch_all(session, LOCK_LIVE_DEVICES_SQL)
    live_ids = {cast(int, row["device_id"]) for row in locked_rows}
    if device_id not in live_ids:
        return REVOKE_NOT_FOUND
    if len(live_ids) == 1:
        return REVOKE_LAST_DEVICE
    changed = execute(session, REVOKE_DEVICE_SQL, {"device_id": device_id, "user_id": user_id})
    if changed != 1:
        logger.error("Device revoke changed %s rows, expected 1", changed)
        raise RuntimeError("Device revoke did not change exactly one row")
    return REVOKE_DONE


# --- Enrollment codes (REQ-069) ---

OPEN_ENROLLMENT_CODE_SQL = """
SELECT enrollment_code_id, created_by
FROM h_system.enrollment_codes
WHERE code_hash = :code_hash
  AND deleted_at IS NULL
  AND used_at IS NULL
  AND expires_at > now()
"""

LOCK_OPEN_ENROLLMENT_CODE_SQL = OPEN_ENROLLMENT_CODE_SQL + "FOR UPDATE\n"

MARK_ENROLLMENT_CODE_USED_SQL = """
UPDATE h_system.enrollment_codes
SET used_at = now()
WHERE enrollment_code_id = :enrollment_code_id
  AND used_at IS NULL
  AND deleted_at IS NULL
"""

CANCEL_OPEN_ENROLLMENT_CODES_SQL = """
UPDATE h_system.enrollment_codes
SET deleted_at = now()
WHERE used_at IS NULL
  AND deleted_at IS NULL
"""

INSERT_ENROLLMENT_CODE_SQL = """
INSERT INTO h_system.enrollment_codes (code_hash, expires_at)
VALUES (:code_hash, now() + make_interval(mins => CAST(:valid_minutes AS integer)))
RETURNING enrollment_code_id, expires_at
"""

GET_OPEN_ENROLLMENT_CODE_STATUS_SQL = """
SELECT u.display_name AS created_by_name, c.created_at, c.expires_at,
       c.expires_at <= now() AS is_expired
FROM h_system.enrollment_codes c
JOIN h_system.users u ON u.user_id = c.created_by
WHERE c.used_at IS NULL
  AND c.deleted_at IS NULL
ORDER BY c.enrollment_code_id DESC
LIMIT 1
"""


def find_open_enrollment_code(session: Session, code_hash: str) -> dict[str, object] | None:
    """
    Description:
        Looks up the open (live, unused, unexpired) one-time code with this hash.

    Parameters:
        session: the request session.
        code_hash: lower-case hex SHA-256 of the code from the enrollment URL.

    Return Values:
        dict[str, object] | None: {"enrollment_code_id", "created_by"}, or None.

    Notes:
        Read-only; the enrollment POST re-checks under lock (lock_open_enrollment_code).
    """
    return fetch_one(session, OPEN_ENROLLMENT_CODE_SQL, {"code_hash": code_hash})


def lock_open_enrollment_code(session: Session, code_hash: str) -> dict[str, object] | None:
    """
    Description:
        The same lookup as find_open_enrollment_code, locking the row (FOR UPDATE) so two
        enrollments with one code cannot both use it.

    Parameters:
        session: the write session (inside run_write).
        code_hash: lower-case hex SHA-256 of the code.

    Return Values:
        dict[str, object] | None: {"enrollment_code_id", "created_by"}, or None when the
        code is no longer open.

    Notes:
        None.
    """
    return fetch_one(session, LOCK_OPEN_ENROLLMENT_CODE_SQL, {"code_hash": code_hash})


def mark_enrollment_code_used(session: Session, enrollment_code_id: int) -> None:
    """
    Description:
        Marks a one-time code as used.

    Parameters:
        session: the write session (inside run_write).
        enrollment_code_id: the code row returned by lock_open_enrollment_code.

    Return Values:
        None.

    Notes:
        Raises RuntimeError unless exactly one row changes, so the enrollment rolls back.
    """
    changed = execute(
        session, MARK_ENROLLMENT_CODE_USED_SQL, {"enrollment_code_id": enrollment_code_id}
    )
    if changed != 1:
        logger.error("Enrollment code use changed %s rows, expected 1", changed)
        raise RuntimeError("Enrollment code use did not change exactly one row")


def cancel_open_enrollment_codes(session: Session) -> int:
    """
    Description:
        Cancels every open one-time code (expired ones included) by soft-deleting it.

    Parameters:
        session: the write session (inside run_write, acting user = the signed-in user).

    Return Values:
        int: the number of codes cancelled (0 or 1 while ux_enrollment_codes_open holds).

    Notes:
        A cancelled code is never reopened (COD-22-02); a new code is a new row.
    """
    return execute(session, CANCEL_OPEN_ENROLLMENT_CODES_SQL)


def insert_enrollment_code(
    session: Session, code_hash: str, valid_minutes: int
) -> dict[str, object]:
    """
    Description:
        Stores a new one-time code's hash, valid for valid_minutes from now.

    Parameters:
        session: the write session (inside run_write, acting user = the creator, recorded
            as created_by by the audit trigger).
        code_hash: lower-case hex SHA-256 of the code. Never the code itself.
        valid_minutes: ENROLLMENT_CODE_VALID_MINUTES.

    Return Values:
        dict[str, object]: {"enrollment_code_id", "expires_at"}.

    Notes:
        Call cancel_open_enrollment_codes first in the same transaction: the partial unique
        index ux_enrollment_codes_open allows one open code (a concurrent create fails with
        SQLSTATE 23505). Raises RuntimeError if no row comes back.
    """
    row = fetch_one(
        session,
        INSERT_ENROLLMENT_CODE_SQL,
        {"code_hash": code_hash, "valid_minutes": valid_minutes},
    )
    if row is None:
        logger.error("Enrollment code insert returned no row")
        raise RuntimeError("Enrollment code insert returned no row")
    return row


# --- Enrollment settings (REQ-069) ---

GET_ENROLLMENT_SETTINGS_SQL = """
SELECT is_enabled, code_hash
FROM h_system.enrollment_settings
WHERE enrollment_settings_id = :enrollment_settings_id
  AND deleted_at IS NULL
"""

SET_ENROLLMENT_CODE_SQL = """
UPDATE h_system.enrollment_settings
SET code_hash = :code_hash,
    is_enabled = true,
    code_changed_at = now()
WHERE enrollment_settings_id = :enrollment_settings_id
"""


def get_enrollment_settings(session: Session) -> dict[str, object]:
    """
    Description:
        Reads the single enrollment row: whether enrollment is on and the code's hash.

    Parameters:
        session: the request session.

    Return Values:
        dict[str, object]: {"is_enabled", "code_hash"}; code_hash may be None.

    Notes:
        Raises RuntimeError if the seeded row is missing or deleted (a database build
        problem).
    """
    row = fetch_one(
        session,
        GET_ENROLLMENT_SETTINGS_SQL,
        {"enrollment_settings_id": ENROLLMENT_SETTINGS_ID},
    )
    if row is None:
        logger.error("Enrollment settings row %s is missing", ENROLLMENT_SETTINGS_ID)
        raise RuntimeError("Enrollment settings row is missing")
    return row


def set_enrollment_code(session: Session, code_hash: str) -> None:
    """
    Description:
        Stores a new enrollment code hash and switches enrollment on.

    Parameters:
        session: the write session (inside run_write, acting user SYSTEM_USER_ID).
        code_hash: the password-hasher hash of the enrollment code. Never the code itself.

    Return Values:
        None.

    Notes:
        Raises RuntimeError unless exactly one row changes, so the caller's transaction
        rolls back.
    """
    changed = execute(
        session,
        SET_ENROLLMENT_CODE_SQL,
        {"code_hash": code_hash, "enrollment_settings_id": ENROLLMENT_SETTINGS_ID},
    )
    if changed != 1:
        logger.error("Enrollment code update changed %s rows, expected 1", changed)
        raise RuntimeError("Enrollment code update did not change exactly one row")


DISABLE_SETUP_ENROLLMENT_SQL = """
UPDATE h_system.enrollment_settings
SET is_enabled = false
WHERE enrollment_settings_id = :enrollment_settings_id
  AND deleted_at IS NULL
"""


def disable_setup_enrollment(session: Session) -> None:
    """
    Description:
        Turns the setup link off (REQ-069 "switched off"); the stored hash is kept, and only
        the setup script turns it on again with a new code.

    Parameters:
        session: the write session (inside run_write, acting user = the signed-in user).

    Return Values:
        None.

    Notes:
        Raises RuntimeError unless exactly one row changes. Turning off an already-off link
        still changes the row (same value), so it succeeds.
    """
    changed = execute(
        session,
        DISABLE_SETUP_ENROLLMENT_SQL,
        {"enrollment_settings_id": ENROLLMENT_SETTINGS_ID},
    )
    if changed != 1:
        logger.error("Setup link switch-off changed %s rows, expected 1", changed)
        raise RuntimeError("Setup link switch-off did not change exactly one row")


def get_enrollment_status(session: Session) -> dict[str, object]:
    """
    Description:
        What Settings > Add a device shows: whether the setup link is on, and the open
        one-time code (expired or not), if any.

    Parameters:
        session: the request session.

    Return Values:
        dict[str, object]: {"setup_enabled": bool, "open_code": None | {"created_by_name",
        "created_at", "expires_at", "is_expired"}}.

    Notes:
        Read-only; never reads a hash out to the caller.
    """
    settings_row = get_enrollment_settings(session)
    open_code = fetch_one(session, GET_OPEN_ENROLLMENT_CODE_STATUS_SQL)
    return {"setup_enabled": bool(settings_row["is_enabled"]), "open_code": open_code}


# --- Login lockout and attempts (REQ-070) ---

GET_LOCKOUT_STATUS_SQL = """
SELECT is_locked, locked_until, recent_failures
FROM h_system.fn_login_lockout_status(
    CAST(:device_id AS bigint),
    CAST(:email AS text),
    CAST(:max_failures AS integer),
    CAST(:window_minutes AS integer),
    CAST(:lock_minutes AS integer))
"""

INSERT_LOGIN_ATTEMPT_SQL = """
INSERT INTO h_system.login_attempts (device_id, attempted_email, user_id, succeeded)
VALUES (:device_id, :attempted_email, :user_id, :succeeded)
"""

UPDATE_USER_LAST_LOGIN_SQL = """
UPDATE h_system.users
SET last_login_at = now()
WHERE user_id = :user_id
"""

SET_DEVICE_ENROLLED_BY_SQL = """
UPDATE h_system.devices
SET enrolled_by_user_id = :user_id
WHERE device_id = :device_id
  AND enrolled_by_user_id IS NULL
"""


def get_lockout_status(
    session: Session,
    device_id: int,
    email: str,
    max_failures: int,
    window_minutes: int,
    lock_minutes: int,
) -> dict[str, object]:
    """
    Description:
        Returns the lockout state of one device + email pair from
        h_system.fn_login_lockout_status.

    Parameters:
        session: the request session.
        device_id: h_system.devices.device_id from flask.g.device_id. Never a user id.
        email: the email as typed; the function compares lower(btrim()).
        max_failures: LOGIN_MAX_FAILURES.
        window_minutes: LOGIN_WINDOW_MINUTES.
        lock_minutes: LOGIN_LOCK_MINUTES.

    Return Values:
        dict[str, object]: {"is_locked": bool, "locked_until": datetime | None,
        "recent_failures": int}.

    Notes:
        Read-only. Raises RuntimeError if the function returns no row (it always returns
        one).
    """
    row = fetch_one(
        session,
        GET_LOCKOUT_STATUS_SQL,
        {
            "device_id": device_id,
            "email": email,
            "max_failures": max_failures,
            "window_minutes": window_minutes,
            "lock_minutes": lock_minutes,
        },
    )
    if row is None:
        logger.error("Lockout status function returned no row")
        raise RuntimeError("Lockout status function returned no row")
    return row


def insert_login_attempt(
    session: Session, device_id: int, email: str, user_id: int | None, succeeded: bool
) -> None:
    """
    Description:
        Records one sign-in attempt for the device + email pair.

    Parameters:
        session: the write session (inside run_write).
        device_id: h_system.devices.device_id from flask.g.device_id. Never a user id.
        email: the normalised email, already lower(strip()) by the caller, as
            ck_login_attempts_email requires.
        user_id: the matched h_system.users.user_id, or None when no account matched.
        succeeded: whether the password was accepted.

    Return Values:
        None.

    Notes:
        The password is never passed or stored.
    """
    execute(
        session,
        INSERT_LOGIN_ATTEMPT_SQL,
        {
            "device_id": device_id,
            "attempted_email": email,
            "user_id": user_id,
            "succeeded": succeeded,
        },
    )


def record_successful_login(session: Session, user_id: int, device_id: int) -> None:
    """
    Description:
        After a successful sign-in, stamps the user's last_login_at and, if the device has
        no enrolling user yet, records this user as it.

    Parameters:
        session: the write session (inside run_write, acting user = this user).
        user_id: the signed-in h_system.users.user_id.
        device_id: h_system.devices.device_id from flask.g.device_id.

    Return Values:
        None.

    Notes:
        Two statements in the caller's single transaction (COD-6-03).
    """
    execute(session, UPDATE_USER_LAST_LOGIN_SQL, {"user_id": user_id})
    execute(session, SET_DEVICE_ENROLLED_BY_SQL, {"user_id": user_id, "device_id": device_id})


# --- Users (REQ-001) ---

GET_LOGIN_USER_BY_EMAIL_SQL = """
SELECT user_id, display_name, password_hash, session_version, is_active
FROM h_system.users
WHERE lower(email) = lower(:email)
  AND deleted_at IS NULL
  AND NOT is_system
"""

GET_USER_FOR_SESSION_SQL = """
SELECT user_id, display_name, session_version, is_active
FROM h_system.users
WHERE user_id = :user_id
  AND deleted_at IS NULL
  AND NOT is_system
"""

COUNT_LOGIN_USERS_SQL = """
SELECT count(*)::int AS user_count
FROM h_system.users
WHERE NOT is_system
  AND deleted_at IS NULL
"""

INSERT_USER_SQL = """
INSERT INTO h_system.users (email, display_name, password_hash)
VALUES (:email, :display_name, :password_hash)
RETURNING user_id
"""

RESET_USER_PASSWORD_SQL = """
UPDATE h_system.users
SET password_hash = :password_hash,
    session_version = session_version + 1
WHERE lower(email) = lower(:email)
  AND NOT is_system
  AND deleted_at IS NULL
"""


def get_login_user_by_email(session: Session, email: str) -> dict[str, object] | None:
    """
    Description:
        Finds the live, non-system account with this email (case-insensitive) for the
        password check.

    Parameters:
        session: the request session.
        email: the normalised email typed on the sign-in form.

    Return Values:
        dict[str, object] | None: {"user_id", "display_name", "password_hash",
        "session_version", "is_active"}, or None. Inactive accounts are returned so the
        caller can log them.

    Notes:
        The result holds the password hash: never log it (COD-4-05).
    """
    return fetch_one(session, GET_LOGIN_USER_BY_EMAIL_SQL, {"email": email})


def get_user_for_session(session: Session, user_id: int) -> dict[str, object] | None:
    """
    Description:
        Loads the live, non-system account for Flask-Login's user loader.

    Parameters:
        session: the request session.
        user_id: h_system.users.user_id parsed from the session or remember cookie.

    Return Values:
        dict[str, object] | None: {"user_id", "display_name", "session_version",
        "is_active"}, or None.

    Notes:
        The caller checks is_active and session_version.
    """
    return fetch_one(session, GET_USER_FOR_SESSION_SQL, {"user_id": user_id})


def count_login_users(session: Session) -> int:
    """
    Description:
        Counts the live, non-system accounts, so the setup script runs only once.

    Parameters:
        session: the request session.

    Return Values:
        int: the number of login accounts.

    Notes:
        None.
    """
    row = fetch_one(session, COUNT_LOGIN_USERS_SQL)
    return 0 if row is None else cast(int, row["user_count"])


def insert_user(session: Session, email: str, display_name: str, password_hash: str) -> int:
    """
    Description:
        Creates a login account.

    Parameters:
        session: the write session (inside run_write, acting user SYSTEM_USER_ID).
        email: the account email, validated by the setup script.
        display_name: the name shown in the app, 1-100 characters.
        password_hash: the scrypt hash of the password. Never the password itself.

    Return Values:
        int: the new h_system.users.user_id.

    Notes:
        is_active, is_system and session_version take their column defaults.
    """
    row = fetch_one(
        session,
        INSERT_USER_SQL,
        {"email": email, "display_name": display_name, "password_hash": password_hash},
    )
    if row is None:
        logger.error("User insert returned no id")
        raise RuntimeError("User insert returned no id")
    return cast(int, row["user_id"])


def reset_user_password(session: Session, email: str, password_hash: str) -> int:
    """
    Description:
        Replaces an account's password hash and bumps its session_version, which signs the
        account out of every session and remember cookie.

    Parameters:
        session: the write session (inside run_write, acting user SYSTEM_USER_ID).
        email: the account email (case-insensitive).
        password_hash: the scrypt hash of the new password.

    Return Values:
        int: rows updated; 0 means no live account has that email.

    Notes:
        None.
    """
    return execute(
        session, RESET_USER_PASSWORD_SQL, {"email": email, "password_hash": password_hash}
    )


# --- Accounts (REQ-001) ---

GET_ACCOUNT_SQL = """
SELECT user_id, email, display_name, password_hash, session_version, is_active
FROM h_system.users
WHERE user_id = :user_id
  AND deleted_at IS NULL
  AND NOT is_system
"""

LIST_OTHER_ACCOUNTS_SQL = """
SELECT user_id, display_name
FROM h_system.users
WHERE user_id <> :user_id
  AND deleted_at IS NULL
  AND is_active
  AND NOT is_system
ORDER BY display_name, user_id
"""

SET_PASSWORD_BY_USER_ID_SQL = """
UPDATE h_system.users
SET password_hash = :password_hash,
    session_version = session_version + 1
WHERE user_id = :user_id
  AND deleted_at IS NULL
  AND NOT is_system
RETURNING session_version
"""

BUMP_SESSION_VERSION_SQL = """
UPDATE h_system.users
SET session_version = session_version + 1
WHERE user_id = :user_id
  AND deleted_at IS NULL
  AND NOT is_system
RETURNING session_version
"""


def get_account(session: Session, user_id: int) -> dict[str, object] | None:
    """
    Description:
        Loads a live login account for the password re-entry check.

    Parameters:
        session: the request session.
        user_id: h_system.users.user_id (current_user.user_id).

    Return Values:
        dict[str, object] | None: {"user_id", "email", "display_name", "password_hash",
        "session_version", "is_active"}, or None for a missing, deleted or system user.

    Notes:
        The result holds the password hash: never log it (COD-4-05).
    """
    return fetch_one(session, GET_ACCOUNT_SQL, {"user_id": user_id})


def list_other_accounts(session: Session, user_id: int) -> list[dict[str, object]]:
    """
    Description:
        The other live, active login accounts, for "Reset {name}'s password".

    Parameters:
        session: the request session.
        user_id: the signed-in h_system.users.user_id, which is left out.

    Return Values:
        list[dict[str, object]]: {"user_id", "display_name"} by name.

    Notes:
        Read-only. With the two accounts of REQ-001 this is the one other account.
    """
    return fetch_all(session, LIST_OTHER_ACCOUNTS_SQL, {"user_id": user_id})


def set_password_by_user_id(session: Session, user_id: int, password_hash: str) -> int | None:
    """
    Description:
        Replaces an account's password hash and bumps its session_version, which signs it
        out of every session and remember cookie.

    Parameters:
        session: the write session (inside run_write, acting user = the signed-in user).
        user_id: the account to change: current_user.user_id for "Change my password", or
            the checked other account for "Reset" (Context Adaptation Table).
        password_hash: the scrypt hash of the new password. Never the password itself.

    Return Values:
        int | None: the account's new session_version, or None when no live account has
        that id.

    Notes:
        None.
    """
    row = fetch_one(
        session,
        SET_PASSWORD_BY_USER_ID_SQL,
        {"user_id": user_id, "password_hash": password_hash},
    )
    return None if row is None else cast(int, row["session_version"])


def bump_session_version(session: Session, user_id: int) -> int | None:
    """
    Description:
        Signs an account out everywhere by bumping its session_version.

    Parameters:
        session: the write session (inside run_write, acting user = user_id).
        user_id: current_user.user_id.

    Return Values:
        int | None: the new session_version, or None when no live account has that id.

    Notes:
        None.
    """
    row = fetch_one(session, BUMP_SESSION_VERSION_SQL, {"user_id": user_id})
    return None if row is None else cast(int, row["session_version"])

