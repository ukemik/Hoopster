"""One-time setup, password reset and setup-link code for Hoopster (REQ-001, REQ-069).

Run from the project root:
    C:\\conda_envs\\Hoopster_env\\python.exe -m src.setup_accounts
    C:\\conda_envs\\Hoopster_env\\python.exe -m src.setup_accounts --reset-password EMAIL
    C:\\conda_envs\\Hoopster_env\\python.exe -m src.setup_accounts --set-enrollment-code

The initial setup creates the parent and player accounts, the player row and the enrollment
code in one transaction. --set-enrollment-code replaces the setup-link code and turns the
setup link on again (it can be turned off in Settings > Add a device). Secrets (passwords, the enrollment code) are typed at hidden prompts,
validated before anything is written, stored only as hashes, and never logged or echoed
(COD-4-05). The enrollment code is entered by the Overseer, never generated or shown (plan
§9.1 Q2). Output goes through logging only; there is no print() (COD-4-01).
"""

import argparse
import getpass
import logging
import os
import re
import sys
import unicodedata
from collections.abc import Callable, Sequence

from dotenv import load_dotenv
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from config.app_config import (
    ENROLLMENT_CODE_PATTERN,
    MIN_ENROLLMENT_CODE_LENGTH,
    MIN_PASSWORD_LENGTH,
    PASSWORD_HASH_METHOD,
    SYSTEM_USER_ID,
    load_settings,
)
from config.logging_config import configure_logging
from src import data_handler
from src.app_controller import log_failure, new_reference_code, run_write
from src.controller_access import MAX_EMAIL_LENGTH
from src.data_access import (
    count_login_users,
    insert_user,
    reset_user_password,
    set_enrollment_code,
)
from src.data_players import insert_player

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG_ERROR = 2

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+$")
MAX_DISPLAY_NAME_LENGTH = 100
# Unicode category of control characters (e.g. NUL), refused in every text input.
CONTROL_CATEGORY = "Cc"


# --- Input validation (pure) ---

def _has_control_characters(value: str) -> bool:
    """
    Description:
        Whether a text contains a control character such as NUL or a tab.

    Parameters:
        value: the text.

    Return Values:
        bool: True when any character is in Unicode category Cc.

    Notes:
        Pure. PostgreSQL text cannot hold NUL, so such input is refused before any write.
    """
    return any(unicodedata.category(character) == CONTROL_CATEGORY for character in value)


def email_problem(email: str, other_email: str | None) -> str | None:
    """
    Description:
        Checks an account email.

    Parameters:
        email: the stripped email.
        other_email: the other account's email, which this one must differ from
            (case-insensitive), or None.

    Return Values:
        str | None: what is wrong, or None when the email is acceptable.

    Notes:
        Pure. Mirrors ck_users_email (one @, no spaces, at most 200 characters).
    """
    if not EMAIL_PATTERN.match(email) or _has_control_characters(email):
        return "Enter an email like name@example.com."
    if len(email) > MAX_EMAIL_LENGTH:
        return f"The email must be at most {MAX_EMAIL_LENGTH} characters."
    if other_email is not None and email.lower() == other_email.lower():
        return "The two accounts need different emails."
    return None


def display_name_problem(name: str) -> str | None:
    """
    Description:
        Checks a display name.

    Parameters:
        name: the stripped name.

    Return Values:
        str | None: what is wrong, or None when the name is acceptable.

    Notes:
        Pure. Mirrors ck_users_display_name and ck_players_display_name (1-100 characters).
    """
    if not name or len(name) > MAX_DISPLAY_NAME_LENGTH or _has_control_characters(name):
        return f"Enter a name of 1 to {MAX_DISPLAY_NAME_LENGTH} characters."
    return None


def password_problem(first: str, second: str) -> str | None:
    """
    Description:
        Checks a new password and its confirmation.

    Parameters:
        first: the password. Never logged.
        second: the confirmation. Never logged.

    Return Values:
        str | None: what is wrong (without the password), or None when acceptable.

    Notes:
        Pure.
    """
    if len(first) < MIN_PASSWORD_LENGTH:
        return f"The password must be at least {MIN_PASSWORD_LENGTH} characters."
    if first != second:
        return "The two passwords do not match."
    return None


def enrollment_code_problem(first: str, second: str) -> str | None:
    """
    Description:
        Checks the enrollment code and its confirmation.

    Parameters:
        first: the code. Never logged.
        second: the confirmation. Never logged.

    Return Values:
        str | None: what is wrong (without the code), or None when acceptable.

    Notes:
        Pure. The code becomes part of the enrollment URL, so only URL-safe characters are
        allowed.
    """
    if len(first) < MIN_ENROLLMENT_CODE_LENGTH:
        return f"The code must be at least {MIN_ENROLLMENT_CODE_LENGTH} characters."
    if not re.fullmatch(ENROLLMENT_CODE_PATTERN, first):
        return "The code may contain only letters, digits, _ and -."
    if first != second:
        return "The two codes do not match."
    return None


# --- Prompts (input() for plain text, getpass for secrets) ---

def _ask_until_valid(ask: Callable[[], str], problem: Callable[[str], str | None]) -> str:
    """
    Description:
        Repeats a prompt until its answer passes the check, logging what was wrong.

    Parameters:
        ask: reads one answer.
        problem: returns what is wrong with an answer, or None.

    Return Values:
        str: the accepted answer.

    Notes:
        The problem text never contains the answer.
    """
    while True:
        answer = ask()
        issue = problem(answer)
        if issue is None:
            return answer
        logger.error(issue)


def prompt_email(prompt: str, other_email: str | None = None) -> str:
    """
    Description:
        Asks for an account email until it is valid.

    Parameters:
        prompt: the prompt text.
        other_email: the other account's email it must differ from, or None.

    Return Values:
        str: the stripped email.

    Notes:
        None.
    """
    return _ask_until_valid(
        lambda: input(prompt).strip(), lambda email: email_problem(email, other_email)
    )


def prompt_display_name(prompt: str, default: str | None = None) -> str:
    """
    Description:
        Asks for a display name until it is valid; an empty answer takes the default when
        one is given.

    Parameters:
        prompt: the prompt text.
        default: the value used for an empty answer, or None.

    Return Values:
        str: the stripped name.

    Notes:
        None.
    """

    def ask() -> str:
        """
        Description:
            Reads one answer, applying the default.

        Parameters:
            None.

        Return Values:
            str: the stripped answer, or the default for an empty one.

        Notes:
            None.
        """
        answer = input(prompt).strip()
        return default if not answer and default is not None else answer

    return _ask_until_valid(ask, display_name_problem)


def prompt_secret_twice(
    label: str, problem: Callable[[str, str], str | None]
) -> str:
    """
    Description:
        Asks for a secret twice at hidden prompts until both entries pass the check.

    Parameters:
        label: what is being asked for, e.g. "Parent's password".
        problem: checks (first, second) and returns what is wrong, or None.

    Return Values:
        str: the accepted secret.

    Notes:
        The secret is never logged or echoed (COD-4-05).
    """
    while True:
        first = getpass.getpass(f"{label}: ")
        second = getpass.getpass(f"{label} (again): ")
        issue = problem(first, second)
        if issue is None:
            return first
        logger.error(issue)


# --- Initial setup ---

def run_initial_setup() -> int:
    """
    Description:
        Creates the parent account, the player account, the player and the enrollment code,
        after asking for and validating every input.

    Parameters:
        None.

    Return Values:
        int: 0 on success, 1 when setup has already run or the write failed.

    Notes:
        One transaction, acting user SYSTEM_USER_ID. The parent is inserted first, then the
        player account (the tests rely on this order, plan §7.2). Nothing is written before
        all input is valid.
    """
    try:
        existing_users = count_login_users(data_handler.get_session())
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Could not read the accounts; nothing was saved (ref %s)", ref)
        return EXIT_FAILED
    if existing_users > 0:
        logger.error(
            "Setup has already been run; use --reset-password EMAIL to change a password."
        )
        return EXIT_FAILED

    parent_email = prompt_email("Parent's email: ")
    parent_name = prompt_display_name("Parent's display name: ")
    parent_password = prompt_secret_twice("Parent's password", password_problem)
    player_email = prompt_email("Player account's email: ", other_email=parent_email)
    player_user_name = prompt_display_name("Player account's display name: ")
    player_password = prompt_secret_twice("Player account's password", password_problem)
    player_name = prompt_display_name(
        f"First player profile's name (Enter = {player_user_name}): ",
        default=player_user_name,
    )
    enrollment_code = prompt_secret_twice("Enrollment code", enrollment_code_problem)

    parent_hash = generate_password_hash(parent_password, method=PASSWORD_HASH_METHOD)
    player_hash = generate_password_hash(player_password, method=PASSWORD_HASH_METHOD)
    code_hash = generate_password_hash(enrollment_code, method=PASSWORD_HASH_METHOD)

    def work(session: Session) -> dict[str, int]:
        """
        Description:
            The setup writes, in order: parent account, player account, player, enrollment
            code.

        Parameters:
            session: the write session.

        Return Values:
            dict[str, int]: the new parent_user_id, player_user_id and player_id.

        Notes:
            None.
        """
        parent_user_id = insert_user(session, parent_email, parent_name, parent_hash)
        player_user_id = insert_user(session, player_email, player_user_name, player_hash)
        player_id = insert_player(session, player_name)
        set_enrollment_code(session, code_hash)
        return {
            "parent_user_id": parent_user_id,
            "player_user_id": player_user_id,
            "player_id": player_id,
        }

    result = run_write(SYSTEM_USER_ID, work, "initial setup")
    if not result.ok or result.value is None:
        logger.error("Setup failed; nothing was saved. Reference: %s", result.ref)
        return EXIT_FAILED

    created = result.value
    logger.info("Created parent account %s (user %s)", parent_email, created["parent_user_id"])
    logger.info("Created player account %s (user %s)", player_email, created["player_user_id"])
    logger.info("Created player profile %s (player %s)", player_name, created["player_id"])
    logger.info(
        "Enrollment is ON. On each device open <app address>/enroll/<the code you entered>."
    )
    return EXIT_OK


# --- Password reset ---

def run_password_reset(email: str) -> int:
    """
    Description:
        Sets a new password for one account and signs that account out on every device
        (session_version is bumped).

    Parameters:
        email: the account's email (case-insensitive).

    Return Values:
        int: 0 on success, 1 when no account has that email or the write failed.

    Notes:
        Acting user SYSTEM_USER_ID. The new password is asked for twice at hidden prompts.
    """
    account_email = email.strip()
    new_password = prompt_secret_twice("New password", password_problem)
    password_hash = generate_password_hash(new_password, method=PASSWORD_HASH_METHOD)
    result = run_write(
        SYSTEM_USER_ID,
        lambda session: reset_user_password(session, account_email, password_hash),
        "reset password",
    )
    if not result.ok:
        logger.error("Password reset failed; nothing was changed. Reference: %s", result.ref)
        return EXIT_FAILED
    if not result.value:
        logger.error("No account with that email.")
        return EXIT_FAILED
    logger.info(
        "Password changed for %s; that account is now signed out on every device.",
        account_email,
    )
    return EXIT_OK


# --- Setup link (REQ-069 "changed", D4; task 2026-10-23_settings_hub) ---

def run_set_enrollment_code() -> int:
    """
    Description:
        Sets a new setup-link enrollment code and turns the setup link on (it may have been
        turned off in Settings > Add a device).

    Parameters:
        None.

    Return Values:
        int: 0 on success, 1 when the write failed.

    Notes:
        Acting user SYSTEM_USER_ID. The code is asked for twice at hidden prompts, checked by
        enrollment_code_problem, stored only as a scrypt hash, and never logged or echoed
        (COD-4-05).
    """
    enrollment_code = prompt_secret_twice("New enrollment code", enrollment_code_problem)
    code_hash = generate_password_hash(enrollment_code, method=PASSWORD_HASH_METHOD)
    result = run_write(
        SYSTEM_USER_ID,
        lambda session: set_enrollment_code(session, code_hash),
        "set enrollment code",
    )
    if not result.ok:
        logger.error(
            "Setting the enrollment code failed; nothing was changed. Reference: %s", result.ref
        )
        return EXIT_FAILED
    logger.info("Setup link is ON. Open <app address>/enroll/<the code you entered>.")
    return EXIT_OK


# --- Entry point ---

def main(argv: Sequence[str] | None = None) -> int:
    """
    Description:
        Loads the configuration, sets up logging and the database engine, then runs the
        initial setup, a password reset (--reset-password) or a new setup-link code
        (--set-enrollment-code; the two options exclude each other).

    Parameters:
        argv: the command-line arguments (without the program name), or None for sys.argv.

    Return Values:
        int: 0 on success, 1 on refusal, failure or cancel, 2 on a configuration error.

    Notes:
        Ctrl+C or end of input at a prompt cancels with nothing saved. The engine is lazy;
        the first query opens the connection.
    """
    load_dotenv()
    try:
        settings = load_settings(os.environ)
    except ValueError as exc:
        # The message names the variable, never its value.
        logger.error("Configuration error: %s", exc)
        return EXIT_CONFIG_ERROR
    configure_logging(settings.log_level)
    data_handler.init_engine(settings.database_url, False)

    parser = argparse.ArgumentParser(
        prog="python -m src.setup_accounts",
        description=(
            "Create the Hoopster accounts once, reset one account's password, or set a new "
            "setup-link enrollment code."
        ),
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--reset-password",
        metavar="EMAIL",
        help="set a new password for this account and sign it out everywhere",
    )
    actions.add_argument(
        "--set-enrollment-code",
        action="store_true",
        help="set a new setup-link enrollment code and turn the setup link on",
    )
    args = parser.parse_args(argv)

    try:
        if args.reset_password:
            return run_password_reset(args.reset_password)
        if args.set_enrollment_code:
            return run_set_enrollment_code()
        return run_initial_setup()
    except (KeyboardInterrupt, EOFError):
        logger.error("Setup cancelled; nothing was saved.")
        return EXIT_FAILED
    finally:
        data_handler.remove_session()


if __name__ == "__main__":
    sys.exit(main())
