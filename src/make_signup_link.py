"""Makes a one-time sign-up link for Hoopster (task 2026-10-29_deploy_render_neon; REQ-069).

Run from the project root on the Overseer's PC:
    C:\\conda_envs\\Hoopster_env\\python.exe -m src.make_signup_link --env-file <file>

<file> is a settings file kept outside the project, e.g. %USERPROFILE%\\hoopster_prod.env, with
two lines:
    DATABASE_URL=postgresql+psycopg://hoopster_app:<password>@<neon host>/hoopster_db?sslmode=require
    APP_BASE_URL=https://hoopster-nend.onrender.com
Its values override the local .env; the other settings come from the local .env. Without
--env-file the local .env alone is used (the local database).

The link opens /join/<code>, where one person creates an account and their device is added.
It works once and expires after SIGNUP_LINK_VALID_MINUTES. Making a new link cancels any open
one-time code, including one made in Settings > Add a device. Only the code's SHA-256 is
stored. Output goes through logging only (COD-4-01).
"""

import argparse
import logging
import os
import secrets
import sys
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from config.app_config import (
    APP_BASE_URL_VARIABLE,
    ENROLLMENT_CODE_BYTES,
    SIGNUP_LINK_VALID_MINUTES,
    SYSTEM_USER_ID,
    load_settings,
)
from config.logging_config import configure_logging
from src import data_handler
from src.app_controller import run_write
from src.controller_access import one_time_code_hash
from src.data_access import cancel_open_enrollment_codes, insert_enrollment_code

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG_ERROR = 2

JOIN_PATH_PREFIX = "/join/"
LOCAL_BASE_URL = "http://127.0.0.1:8050"


def signup_link(base_url: str, code: str) -> str:
    """
    Description:
        The sign-up link of a code.

    Parameters:
        base_url: the app's public address, e.g. "https://hoopster-nend.onrender.com".
        code: the one-time code.

    Return Values:
        str: base_url without its trailing slash + "/join/" + code.

    Notes:
        Pure.
    """
    return base_url.strip().rstrip("/") + JOIN_PATH_PREFIX + code


def store_signup_code(code: str) -> dict[str, object] | None:
    """
    Description:
        Cancels any open one-time code and stores the new code's hash as user 0.

    Parameters:
        code: the new code. Only its hash is written; never logged.

    Return Values:
        dict[str, object] | None: {"enrollment_code_id", "expires_at"}, or None when the
        write failed (logged with a reference by run_write).

    Notes:
        One transaction. created_by = SYSTEM_USER_ID marks the code as a sign-up link
        (controller_signup.is_signup_code_row).
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
        cancelled = cancel_open_enrollment_codes(session)
        if cancelled:
            logger.info("Cancelled %s open one-time code(s)", cancelled)
        return insert_enrollment_code(session, code_hash, SIGNUP_LINK_VALID_MINUTES)

    result = run_write(SYSTEM_USER_ID, work, "make sign-up link")
    if not result.ok or result.value is None:
        logger.error("Could not make a sign-up link; nothing was saved. Reference: %s", result.ref)
        return None
    return result.value


def main(argv: Sequence[str] | None = None) -> int:
    """
    Description:
        Loads the settings (the --env-file first, then the local .env), makes one sign-up
        code and logs its link.

    Parameters:
        argv: the command-line arguments (without the program name), or None for sys.argv.

    Return Values:
        int: 0 on success, 1 when the write failed, 2 on a configuration error.

    Notes:
        The link is a secret that works once; it is written to the console only.
    """
    parser = argparse.ArgumentParser(
        prog="python -m src.make_signup_link",
        description="Make a one-time link that creates one Hoopster account and adds its device.",
    )
    parser.add_argument(
        "--env-file",
        help="settings file with DATABASE_URL and APP_BASE_URL (overrides the local .env)",
    )
    args = parser.parse_args(argv)

    if args.env_file:
        env_file = Path(args.env_file).expanduser()
        if not env_file.is_file():
            configure_logging("INFO")
            logger.error("Settings file not found: %s", env_file)
            return EXIT_CONFIG_ERROR
        load_dotenv(env_file, override=True)
    load_dotenv()
    try:
        settings = load_settings(os.environ)
    except ValueError as exc:
        configure_logging("INFO")
        # The message names the variable, never its value.
        logger.error("Configuration error: %s", exc)
        return EXIT_CONFIG_ERROR
    configure_logging(settings.log_level)
    data_handler.init_engine(settings.database_url, False)

    base_url = os.environ.get(APP_BASE_URL_VARIABLE, "").strip() or LOCAL_BASE_URL
    code = secrets.token_urlsafe(ENROLLMENT_CODE_BYTES)
    try:
        stored = store_signup_code(code)
    finally:
        data_handler.remove_session()
    if stored is None:
        return EXIT_FAILED

    logger.info(
        "One-time sign-up link (works once, valid %s minutes): %s",
        SIGNUP_LINK_VALID_MINUTES,
        signup_link(base_url, code),
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
