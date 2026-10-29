"""Request pipeline, device-key crypto, router and the single commit helper of Hoopster.

REQ-002 (nothing without sign-in), REQ-046 (secret no-database health page), REQ-055 (device
clock), REQ-066 (bottom bar), REQ-068 (request timing), REQ-069 (device key before anything
else). run_write is the only place in the code base where a transaction is committed
(COD-6-01). This module imports no controller_* module: main.py passes the page builders to
register_router (plan §3.1).
"""

import hashlib
import logging
import re
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Generic, TypedDict, TypeVar

from dash import ClientsideFunction, Dash, Input, Output, State
from dash.development.base_component import Component
from flask import Flask, Response, g, request
from flask import session as flask_session
from flask_login import current_user
from itsdangerous import BadData, URLSafeSerializer
from sqlalchemy.orm import Session

from config.app_config import (
    DEVICE_COOKIE_NAME,
    DEVICE_KEY_SALT,
    ENROLL_ENDPOINT,
    LAST_SEEN_INTERVAL_MINUTES,
    SYSTEM_USER_ID,
    AppSettings,
)
from config.logging_config import TIMING_LOGGER_NAME
from src import data_handler
from src.data_access import get_live_device_by_key_hash, touch_device_last_seen
from src.data_players import get_default_player_id, get_live_player
from src.layouts import error_page, no_player_page, not_found_page
from src.layouts_access import login_layout

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger(TIMING_LOGGER_NAME)

T = TypeVar("T")


# --- Transactions and error references (COD-6-01, COD-6-03, COD-4-04) ---

REFERENCE_PREFIX = "E-"
REFERENCE_BYTES = 3


@dataclass(frozen=True)
class WriteResult(Generic[T]):
    """
    Description:
        The outcome of one run_write call.

    Parameters:
        ok: True when the work ran and the transaction was committed.
        value: the work's return value when ok, else None.
        ref: the reference code (E-XXXXXX) logged with the failure, else None.

    Return Values:
        Not applicable (data class).

    Notes:
        Callers show "Reference: {ref}" to the user on failure.
    """

    ok: bool
    value: T | None
    ref: str | None


def new_reference_code() -> str:
    """
    Description:
        Creates a short reference code that ties a message on the screen to its log line.

    Parameters:
        None.

    Return Values:
        str: "E-" followed by six upper-case hex characters, e.g. "E-3FA91C".

    Notes:
        Not a secret; only an identifier the user can quote.
    """
    return REFERENCE_PREFIX + secrets.token_hex(REFERENCE_BYTES).upper()


def log_failure(
    log: logging.Logger, exc: BaseException, message: str, *args: object
) -> None:
    """
    Description:
        Logs a failure caught at a user-facing boundary. A non-database exception is logged
        with its traceback; a database error is logged with its class and SQLSTATE only,
        never its text.

    Parameters:
        log: the calling module's logger.
        exc: the exception caught.
        message: a %-style message (no secrets), normally including the reference code.
        *args: the message arguments.

    Return Values:
        None.

    Notes:
        Call only inside the except block of a boundary (COD-4-04). stacklevel=2 makes the
        log line name the caller's function, not this helper (COD-4-02). The database-error
        test is data_handler.database_error_summary, shared with the query helpers.
    """
    summary = data_handler.database_error_summary(exc)
    if summary is None:
        log.error(message, *args, exc_info=exc, stacklevel=2)
        return
    error_class, sqlstate = summary
    log.error(
        message + " [database error %s, SQLSTATE %s]",
        *args,
        error_class,
        sqlstate,
        stacklevel=2,
    )


def run_write(user_id: int, work: Callable[[Session], T], action: str) -> WriteResult[T]:
    """
    Description:
        Runs one group of related writes in a single transaction: sets the acting user,
        runs the work, and commits; on any failure rolls back and returns a reference.

    Parameters:
        user_id: the acting h_system.users.user_id: current_user.user_id (never
            current_user.get_id()), or SYSTEM_USER_ID for enrollment, last-seen, failed
            logins and the setup script (Context Adaptation Table).
        work: a function of the session that performs the writes and returns a value.
        action: a short description for the log, e.g. "save shot" (no secrets).

    Return Values:
        WriteResult[T]: ok with the work's value, or not ok with a reference code.

    Notes:
        The only commit in the code base (COD-6-01). A boundary (COD-4-04, COD-10-01): it
        logs and returns instead of re-raising, so the caller can show the reference.
    """
    session = data_handler.get_session()
    if session.in_transaction():
        # End the read-only transaction opened by earlier reads in this request, so the
        # acting user is set at the very start of the write transaction (scoping C.3).
        session.rollback()
    try:
        data_handler.set_acting_user(session, user_id)
        value = work(session)
        session.commit()
        return WriteResult(True, value, None)
    except Exception as exc:
        ref = new_reference_code()
        session.rollback()
        log_failure(logger, exc, "Write failed: %s (ref %s)", action, ref)
        return WriteResult(False, None, ref)


# --- The database's own message to the user (plan §3.2) ---

# SQLSTATE of our own RAISE EXCEPTION in plpgsql, and of a unique violation.
SQLSTATE_RAISE = "P0001"
SQLSTATE_UNIQUE_VIOLATION = "23505"


def user_facing_database_message(
    exc: BaseException, constraint_messages: Mapping[str, str] | None
) -> str | None:
    """
    Description:
        The message a failed write may show the user: the text of the database's own RAISE
        (SQLSTATE P0001), or the caller's message for a unique index (SQLSTATE 23505).

    Parameters:
        exc: the exception the work raised.
        constraint_messages: unique index name -> message to show, or None.

    Return Values:
        str | None: the message; None for any other failure (the caller then shows a
        reference instead).

    Notes:
        Pure apart from reading the exception. The message is shown on screen and never
        logged: only P0001's primary message (no DETAIL line, which can carry row data) and
        our own fixed texts can reach the user (COD-4-05).
    """
    database_error = data_handler.find_database_error(exc)
    if database_error is None:
        return None
    original = getattr(database_error, "orig", None) or database_error
    sqlstate = getattr(original, "sqlstate", None)
    diag = getattr(original, "diag", None)
    if diag is None:
        return None
    if sqlstate == SQLSTATE_RAISE:
        message = getattr(diag, "message_primary", None)
        return str(message) if message else None
    if sqlstate == SQLSTATE_UNIQUE_VIOLATION and constraint_messages:
        constraint = getattr(diag, "constraint_name", None)
        return constraint_messages.get(str(constraint)) if constraint else None
    return None


def run_write_with_reason(
    user_id: int,
    work: Callable[[Session], T],
    action: str,
    constraint_messages: Mapping[str, str] | None = None,
) -> tuple[WriteResult[T], str | None]:
    """
    Description:
        run_write, plus the database's own message for the user when the write fails
        (REQ-028 name clash, the database's refusals).

    Parameters:
        user_id: the acting h_system.users.user_id (current_user.user_id), as for run_write.
        work: a function of the session that performs the writes and returns a value.
        action: a short description for the log (no secrets).
        constraint_messages: unique index name -> message to show for a 23505, or None.

    Return Values:
        tuple[WriteResult[T], str | None]: run_write's result, and on failure the message of
        user_facing_database_message (None when there is none; callers then show
        "... failed. Reference: {ref}").

    Notes:
        The commit stays in run_write (COD-6-01). The wrapper only records the exception and
        re-raises it unchanged; it does not log, because run_write already logs the failure
        once, by class and SQLSTATE (COD-4-04, COD-4-05).
    """
    caught: list[BaseException] = []

    def recording_work(session: Session) -> T:
        """
        Description:
            Runs the work and remembers any exception it raises before re-raising it.

        Parameters:
            session: the write session.

        Return Values:
            T: the work's value.

        Notes:
            No logging here: run_write logs the failure once (COD-4-04).
        """
        try:
            return work(session)
        except Exception as exc:
            caught.append(exc)
            raise

    result = run_write(user_id, recording_work, action)
    if result.ok or not caught:
        return result, None
    return result, user_facing_database_message(caught[0], constraint_messages)


# --- Player context (COD-8-01) ---

PLAYER_SESSION_KEY = "player_id"
# A player picker's value: a player id as a string of digits (dmc values are strings). At most
# 18 digits, so the id always fits PostgreSQL's bigint.
PLAYER_VALUE_PATTERN = re.compile(r"[0-9]{1,18}")


def current_player_id() -> int | None:
    """
    Description:
        Returns the selected player: the id cached in the Flask session when that profile is
        still live; otherwise the default player (the live player with the lowest id),
        which is then cached. With no live player the cached id is dropped.

    Parameters:
        None.

    Return Values:
        int | None: h_data.players.player_id of a live profile, or None when no live player
        exists.

    Notes:
        Never current_user.user_id (Context Adaptation Table). Needs a request context. The
        cached id is re-validated on every call, so a profile removed on another device is
        never used (Amendment A4: live profiles only).
    """
    session = data_handler.get_session()
    cached = flask_session.get(PLAYER_SESSION_KEY)
    if (
        isinstance(cached, int)
        and not isinstance(cached, bool)
        and get_live_player(session, cached) is not None
    ):
        return cached
    player_id = get_default_player_id(session)
    if player_id is None:
        flask_session.pop(PLAYER_SESSION_KEY, None)
    else:
        flask_session[PLAYER_SESSION_KEY] = player_id
    return player_id


def set_current_player_id(player_id: int) -> bool:
    """
    Description:
        Makes a player the selected player of this browser session, after checking that the
        profile is live.

    Parameters:
        player_id: h_data.players.player_id chosen in the Home player picker. Never a user
            id (COD-8-01).

    Return Values:
        bool: True when the player is live and is now selected; False when it does not
        exist or was removed (the selection is then unchanged).

    Notes:
        The only way the selected player changes apart from the fallback in
        current_player_id (Context Adaptation Table). Needs a request context.
    """
    if get_live_player(data_handler.get_session(), player_id) is None:
        return False
    flask_session[PLAYER_SESSION_KEY] = player_id
    return True


def parse_player_value(value: object) -> int | None:
    """
    Description:
        Reads a player picker's value (a player id as a string, as dmc sends it).

    Parameters:
        value: the picker's value, e.g. "12".

    Return Values:
        int | None: the player id, or None when the value is missing or not 1 to 18 ASCII
        digits.

    Notes:
        Pure. Shape only: callers check that the player is live (or a live participant)
        before using the id.
    """
    # fullmatch, not match with "$", which would also accept a trailing newline.
    if not isinstance(value, str) or PLAYER_VALUE_PATTERN.fullmatch(value) is None:
        return None
    return int(value)


# --- Device-key crypto (REQ-069) ---

DEVICE_KEY_BYTES = 32


def new_device_key() -> str:
    """
    Description:
        Creates a new random raw device key.

    Parameters:
        None.

    Return Values:
        str: a URL-safe random string (43 characters).

    Notes:
        The raw key lives only in the signed cookie; the database stores its hash.
    """
    return secrets.token_urlsafe(DEVICE_KEY_BYTES)


def hash_device_key(raw_key: str) -> str:
    """
    Description:
        Hashes a raw device key for storage and lookup.

    Parameters:
        raw_key: the raw device key (ASCII).

    Return Values:
        str: lower-case hex SHA-256 of the key's ASCII bytes (64 characters).

    Notes:
        Raises UnicodeEncodeError for a non-ASCII key; read_device_key refuses those first.
    """
    return hashlib.sha256(raw_key.encode("ascii")).hexdigest()


def _device_key_serializer(secret: str) -> URLSafeSerializer:
    """
    Description:
        Builds the signer for the device cookie.

    Parameters:
        secret: DEVICE_KEY_SECRET.

    Return Values:
        URLSafeSerializer: the serializer, salted with DEVICE_KEY_SALT.

    Notes:
        None.
    """
    return URLSafeSerializer(secret, salt=DEVICE_KEY_SALT)


def sign_device_key(raw_key: str, secret: str) -> str:
    """
    Description:
        Signs a raw device key for the hoopster_device cookie.

    Parameters:
        raw_key: the raw device key.
        secret: DEVICE_KEY_SECRET.

    Return Values:
        str: the signed cookie value.

    Notes:
        None.
    """
    return _device_key_serializer(secret).dumps(raw_key)


def read_device_key(cookie_value: str | None, secret: str) -> str | None:
    """
    Description:
        Verifies the device cookie and returns the raw key inside it.

    Parameters:
        cookie_value: the hoopster_device cookie, or None when absent.
        secret: DEVICE_KEY_SECRET.

    Return Values:
        str | None: the raw key, or None for a missing cookie, a bad signature or payload,
        or a key that is not a non-empty ASCII string.

    Notes:
        Pure apart from the crypto; no database access.
    """
    if not cookie_value:
        return None
    try:
        payload = _device_key_serializer(secret).loads(cookie_value)
    except BadData:
        return None
    if not isinstance(payload, str) or not payload or not payload.isascii():
        return None
    return payload


# --- Request pipeline (REQ-002, REQ-046, REQ-068, REQ-069; plan §3.2) ---

ROBOTS_TXT_PATH = "/robots.txt"
ROBOTS_TXT_BODY = "User-agent: *\nDisallow: /\n"
HEALTH_BODY = "ok"
NOT_FOUND_BODY = "Not Found"
UNAUTHORIZED_BODY = "Unauthorized"
TEXT_MIMETYPE = "text/plain"
X_ROBOTS_TAG = "noindex, nofollow"
CALLBACK_PATH = "/_dash-update-component"

# Placeholders written to logs instead of the two secret paths (COD-4-05).
HEALTH_PATH_LOG_TEXT = "[health path]"
ENROLL_PATH_LOG_TEXT = "/enroll/[code]"
# First path segment of an enrollment URL; any path in it is masked in the log.
ENROLL_PATH_SEGMENT = "enroll"

# Dash callbacks a signed-out browser may call: the router and the login form (REQ-002).
PUBLIC_CALLBACK_OUTPUT_IDS: frozenset[str] = frozenset(
    {"page-content", "app-nav", "login-feedback", "login-password", "auth-state"}
)

MILLISECONDS_PER_SECOND = 1000.0


def plain_text_response(body: str, status: int) -> Response:
    """
    Description:
        Builds a plain-text response.

    Parameters:
        body: the response text.
        status: the HTTP status code.

    Return Values:
        Response: the response, mimetype text/plain.

    Notes:
        None.
    """
    return Response(body, status=status, mimetype=TEXT_MIMETYPE)


def plain_not_found() -> Response:
    """
    Description:
        The plain 404 a browser without a valid device key sees for every address.

    Parameters:
        None.

    Return Values:
        Response: 404, body "Not Found", text/plain, no Dash markup.

    Notes:
        None.
    """
    return plain_text_response(NOT_FOUND_BODY, 404)


def dash_output_ids(output: str) -> set[str]:
    """
    Description:
        Extracts the component ids from a Dash callback's "output" string, e.g.
        "..page-content.children...app-nav.className.." gives {"page-content", "app-nav"}.

    Parameters:
        output: the "output" field of a /_dash-update-component request body.

    Return Values:
        set[str]: the output component ids (a pattern-matching id stays as its JSON text).

    Notes:
        Pure. Steps: strip the outer "..", split on "...", drop the last ".property" of each
        part; this also drops the "@<hash>" that allow_duplicate appends to the property.
    """
    if not isinstance(output, str):
        return set()
    trimmed = output.removeprefix("..").removesuffix("..")
    return {part.rsplit(".", 1)[0] for part in trimmed.split("...") if part}


def loggable_path(settings: AppSettings) -> str:
    """
    Description:
        The current request's path, with the secret health path and the enrollment code
        replaced by placeholders.

    Parameters:
        settings: the application settings (for the health path).

    Return Values:
        str: "[health path]" for any path that starts with the health path; otherwise
        "/enroll/[code]" for any path whose first segment is "enroll" (or that matched the
        enrollment endpoint); otherwise the path unchanged.

    Notes:
        The enrollment code and HEALTH_PATH are secrets and never reach the log (COD-4-05).
        Matching is by prefix, not by exact path or endpoint, so a trailing slash or an extra
        segment (e.g. /enroll/<code>/ or <health path>/x, which fall to Dash's catch-all
        route and fail the device check) is masked too. Both sides are compared with repeated
        leading slashes collapsed, trailing slashes of the health path ignored, and case
        folded. This masks some paths that merely begin with the secret (e.g. <health path>XYZ
        or /Enroll); masking too much is the safe side.
    """
    folded = ("/" + request.path.lstrip("/")).casefold()
    health_prefix = ("/" + settings.health_path.strip("/")).casefold()
    if folded.startswith(health_prefix):
        return HEALTH_PATH_LOG_TEXT
    first_segment = folded.split("/", 2)[1]
    if first_segment == ENROLL_PATH_SEGMENT or request.endpoint == ENROLL_ENDPOINT:
        return ENROLL_PATH_LOG_TEXT
    return request.path


def start_timer() -> None:
    """
    Description:
        before_request hook: records the request's start time for the timing log.

    Parameters:
        None.

    Return Values:
        None.

    Notes:
        Registered first, so the duration covers the device check too.
    """
    g.request_started = time.perf_counter()


def _verify_device(settings: AppSettings) -> Response | None:
    """
    Description:
        The device check (REQ-069): verifies the signed device cookie, looks the key's hash
        up, sets g.device_id, and refreshes last_seen_at at most hourly.

    Parameters:
        settings: the application settings.

    Return Values:
        Response | None: the plain 404 when the device is not recognised, else None.

    Notes:
        A failed lookup is logged and answered with the 404 (request boundary, no
        re-raise). A failed last-seen write is logged inside run_write and ignored.
    """
    raw_key = read_device_key(
        request.cookies.get(DEVICE_COOKIE_NAME), settings.device_key_secret
    )
    if raw_key is None:
        logger.info("No valid device cookie for %s", loggable_path(settings))
        return plain_not_found()
    try:
        device = get_live_device_by_key_hash(
            data_handler.get_session(), hash_device_key(raw_key)
        )
    except Exception as exc:
        log_failure(logger, exc, "Device lookup failed for %s", loggable_path(settings))
        return plain_not_found()
    if device is None:
        logger.info("Unknown or revoked device key for %s", loggable_path(settings))
        return plain_not_found()

    device_id = int(device["device_id"])
    g.device_id = device_id
    last_seen_at = device["last_seen_at"]
    stale_before = datetime.now(timezone.utc) - timedelta(minutes=LAST_SEEN_INTERVAL_MINUTES)
    if not isinstance(last_seen_at, datetime) or last_seen_at < stale_before:
        run_write(
            SYSTEM_USER_ID,
            lambda session: touch_device_last_seen(
                session, device_id, LAST_SEEN_INTERVAL_MINUTES
            ),
            "device last seen",
        )
    return None


def guard_request(settings: AppSettings) -> Response | None:
    """
    Description:
        before_request hook body (plan §3.2): the exact-path exemptions (health page,
        robots.txt, the enrollment endpoint), then the device check, then the signed-out
        callback guard.

    Parameters:
        settings: the application settings.

    Return Values:
        Response | None: a response that ends the request (health, robots, 404, 401), or
        None to let the request continue.

    Notes:
        Enrollment is exempted by endpoint, never by path prefix: /enroll/a/b falls through
        to Dash's catch-all route and must get the 404.
    """
    if request.path == settings.health_path:
        response = plain_text_response(HEALTH_BODY, 200)
        response.headers["Cache-Control"] = "no-store"
        return response
    if request.path == ROBOTS_TXT_PATH:
        return plain_text_response(ROBOTS_TXT_BODY, 200)
    if request.endpoint == ENROLL_ENDPOINT:
        return None

    refused = _verify_device(settings)
    if refused is not None:
        return refused

    if request.path == CALLBACK_PATH:
        body = request.get_json(silent=True) or {}
        output = body.get("output", "") if isinstance(body, dict) else ""
        ids = dash_output_ids(output)
        if not current_user.is_authenticated and not ids <= PUBLIC_CALLBACK_OUTPUT_IDS:
            logger.warning("Refused signed-out callback for outputs %s", sorted(ids))
            return plain_text_response(UNAUTHORIZED_BODY, 401)
    return None


def finish_response(response: Response, settings: AppSettings) -> Response:
    """
    Description:
        after_request hook body: adds X-Robots-Tag to every response and, when timing is
        on, logs the request's method, path, status and duration (REQ-068).

    Parameters:
        response: the outgoing response (404s included).
        settings: the application settings.

    Return Values:
        Response: the same response.

    Notes:
        For a Dash callback only the "output" string is logged, never inputs or state: the
        login callback carries a password (COD-4-05).
    """
    response.headers["X-Robots-Tag"] = X_ROBOTS_TAG
    if not settings.log_timings:
        return response
    started = g.get("request_started")
    if started is None:
        return response
    elapsed_ms = (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
    path = loggable_path(settings)
    if request.path == CALLBACK_PATH:
        body = request.get_json(silent=True) or {}
        output = body.get("output", "") if isinstance(body, dict) else ""
        timing_logger.info(
            "%s %s %s %.1f ms output=%s",
            request.method, path, response.status_code, elapsed_ms, output,
        )
    else:
        timing_logger.info(
            "%s %s %s %.1f ms", request.method, path, response.status_code, elapsed_ms
        )
    return response


def register_request_pipeline(server: Flask, settings: AppSettings) -> None:
    """
    Description:
        Registers the request pipeline on the Flask server, in this order: start_timer,
        guard_request, finish_response, and data_handler.remove_session at app-context
        teardown (one database session per request).

    Parameters:
        server: the Flask server (app.server).
        settings: the application settings.

    Return Values:
        None.

    Notes:
        Dash's own before_request (_setup_server) was registered earlier by Dash() and
        touches neither the database nor the response.
    """

    def run_guard() -> Response | None:
        """
        Description:
            Flask before_request hook; runs guard_request with the settings.

        Parameters:
            None.

        Return Values:
            Response | None: see guard_request.

        Notes:
            None.
        """
        return guard_request(settings)

    def run_finish(response: Response) -> Response:
        """
        Description:
            Flask after_request hook; runs finish_response with the settings.

        Parameters:
            response: the outgoing response.

        Return Values:
            Response: the same response.

        Notes:
            None.
        """
        return finish_response(response, settings)

    server.before_request(start_timer)
    server.before_request(run_guard)
    server.after_request(run_finish)
    server.teardown_appcontext(data_handler.remove_session)


# --- Router (REQ-002, REQ-055, REQ-066; plan §3.5) ---

class DeviceClock(TypedDict):
    """
    Description:
        The device-clock store: the browser's own date and IANA time zone (REQ-055).

    Parameters:
        today: "YYYY-MM-DD" on the device, or None when unknown.
        zone: the IANA zone name, "UTC" when unknown.

    Return Values:
        Not applicable (typed dict).

    Notes:
        today may be None; every page builder handles that.
    """

    today: str | None
    zone: str


# Arguments: player_id (the selected h_data.players.player_id; None only for the routes in
# ROUTES_WITHOUT_PLAYER), the device clock, the path parameters.
PageBuilder = Callable[[int | None, DeviceClock, dict[str, str]], Component]

ROUTE_HOME = "home"
ROUTE_PRACTICE_NEW = "practice_new"
ROUTE_PRACTICE_COURT = "practice_court"
ROUTE_PLAYERS = "players"
ROUTE_PLAYER_EDIT = "player_edit"
ROUTE_DASHBOARDS = "dashboards"
ROUTE_NOT_FOUND = "not_found"
# Library routes (practice_drills 2a, plan §3.3).
ROUTE_DRILLS = "drills"
ROUTE_DRILL_NEW = "drill_new"
ROUTE_DRILL_EDIT = "drill_edit"
ROUTE_DRILL_TYPES = "drill_types"
ROUTE_DRILL_SETS = "drill_sets"
ROUTE_DRILL_SET_NEW = "drill_set_new"
ROUTE_DRILL_SET_EDIT = "drill_set_edit"
ROUTE_TEMPLATES = "templates"
ROUTE_TEMPLATE_NEW = "template_new"
ROUTE_TEMPLATE_EDIT = "template_edit"
# Game and season routes (task 2026-10-23_games, item 2.1); all need a live player.
ROUTE_GAMES = "games"
ROUTE_GAME_NEW = "game_new"
ROUTE_GAME = "game"
ROUTE_GAME_LIVE = "game_live"
ROUTE_GAME_SUMMARY = "game_summary"
ROUTE_SEASONS = "seasons"
ROUTE_SEASON_EDIT = "season_edit"
# Practice overview and run mode (practice_drills 2b, plan §3.3); they need a live player.
ROUTE_PRACTICE_OVERVIEW = "practice_overview"
ROUTE_PRACTICE_RUN = "practice_run"
ROUTE_PRACTICE_RUN_DRILL = "practice_run_drill"
# Settings hub and its pages (task 2026-10-23_settings_hub, item 14). Seasons is the existing
# /seasons route, so there is no settings_seasons route.
ROUTE_SETTINGS = "settings"
ROUTE_SETTINGS_DELETED = "settings_deleted"
ROUTE_SETTINGS_EXPORT = "settings_export"
ROUTE_SETTINGS_RETENTION = "settings_retention"
ROUTE_SETTINGS_DEVICES = "settings_devices"
ROUTE_SETTINGS_ENROLL = "settings_enroll"
ROUTE_SETTINGS_PASSWORD = "settings_password"
ROUTE_SETTINGS_ABOUT = "settings_about"
ROUTE_SETTINGS_MANUAL = "settings_manual"
# The general Import page (task 2026-10-23_drill_import, REQ-050); not player-scoped.
ROUTE_IMPORT = "import"
# Goals, measurements and the shared physical-test list (task 2026-10-23_goals_measurements,
# item 5). Physical tests are not player-scoped (D2).
ROUTE_GOALS = "goals"
ROUTE_GOAL_NEW = "goal_new"
ROUTE_GOAL = "goal"
ROUTE_MEASUREMENTS = "measurements"
ROUTE_MEASUREMENT = "measurement"
ROUTE_PHYSICAL_TESTS = "physical_tests"

# Routes that work without a live player profile: Players, where the first profile is added,
# the drill library, which is not player-scoped (D5), every Settings page (restoring a
# removed profile must work with none live), Import, and the shared physical-test list.
ROUTES_WITHOUT_PLAYER: frozenset[str] = frozenset(
    {
        ROUTE_SETTINGS,
        ROUTE_SETTINGS_DELETED,
        ROUTE_SETTINGS_EXPORT,
        ROUTE_SETTINGS_RETENTION,
        ROUTE_SETTINGS_DEVICES,
        ROUTE_SETTINGS_ENROLL,
        ROUTE_SETTINGS_PASSWORD,
        ROUTE_SETTINGS_ABOUT,
        ROUTE_SETTINGS_MANUAL,
        ROUTE_PLAYERS,
        ROUTE_PLAYER_EDIT,
        ROUTE_DRILLS,
        ROUTE_DRILL_NEW,
        ROUTE_DRILL_EDIT,
        ROUTE_DRILL_TYPES,
        ROUTE_DRILL_SETS,
        ROUTE_DRILL_SET_NEW,
        ROUTE_DRILL_SET_EDIT,
        ROUTE_TEMPLATES,
        ROUTE_TEMPLATE_NEW,
        ROUTE_TEMPLATE_EDIT,
        ROUTE_IMPORT,
        ROUTE_PHYSICAL_TESTS,
    }
)

COURT_PATH_PATTERN = re.compile(r"^/practice/(\d+)/court$")
PLAYER_EDIT_PATH_PATTERN = re.compile(r"^/players/(\d+)$")
STATIC_ROUTES: dict[str, str] = {
    "/": ROUTE_HOME,
    "/practice/new": ROUTE_PRACTICE_NEW,
    "/players": ROUTE_PLAYERS,
    "/dashboards": ROUTE_DASHBOARDS,
    "/drills": ROUTE_DRILLS,
    "/drills/new": ROUTE_DRILL_NEW,
    "/drill-types": ROUTE_DRILL_TYPES,
    "/drill-sets": ROUTE_DRILL_SETS,
    "/drill-sets/new": ROUTE_DRILL_SET_NEW,
    "/templates": ROUTE_TEMPLATES,
    "/templates/new": ROUTE_TEMPLATE_NEW,
    "/games": ROUTE_GAMES,
    "/games/new": ROUTE_GAME_NEW,
    "/seasons": ROUTE_SEASONS,
    "/settings": ROUTE_SETTINGS,
    "/settings/deleted": ROUTE_SETTINGS_DELETED,
    "/settings/export": ROUTE_SETTINGS_EXPORT,
    "/settings/retention": ROUTE_SETTINGS_RETENTION,
    "/settings/devices": ROUTE_SETTINGS_DEVICES,
    "/settings/enroll": ROUTE_SETTINGS_ENROLL,
    "/settings/password": ROUTE_SETTINGS_PASSWORD,
    "/settings/about": ROUTE_SETTINGS_ABOUT,
    "/settings/manual": ROUTE_SETTINGS_MANUAL,
    "/import": ROUTE_IMPORT,
    "/goals": ROUTE_GOALS,
    "/goals/new": ROUTE_GOAL_NEW,
    "/measurements": ROUTE_MEASUREMENTS,
    "/physical-tests": ROUTE_PHYSICAL_TESTS,
}
# (compiled pattern, route name, parameter names) for paths with ids, tried in order after
# STATIC_ROUTES, so "/drills/new" never reaches the "/drills/<id>" pattern.
PATTERN_ROUTES: tuple[tuple[re.Pattern[str], str, tuple[str, ...]], ...] = (
    (COURT_PATH_PATTERN, ROUTE_PRACTICE_COURT, ("practice_session_id",)),
    (PLAYER_EDIT_PATH_PATTERN, ROUTE_PLAYER_EDIT, ("player_id",)),
    (re.compile(r"^/drills/(\d+)$"), ROUTE_DRILL_EDIT, ("drill_id",)),
    (re.compile(r"^/drill-sets/(\d+)$"), ROUTE_DRILL_SET_EDIT, ("drill_set_id",)),
    (re.compile(r"^/templates/(\d+)$"), ROUTE_TEMPLATE_EDIT, ("practice_template_id",)),
    (re.compile(r"^/games/(\d+)$"), ROUTE_GAME, ("game_id",)),
    (re.compile(r"^/games/(\d+)/live$"), ROUTE_GAME_LIVE, ("game_id",)),
    (re.compile(r"^/games/(\d+)/summary$"), ROUTE_GAME_SUMMARY, ("game_id",)),
    (re.compile(r"^/seasons/(\d+)$"), ROUTE_SEASON_EDIT, ("season_id",)),
    # practice_drills 2b ("/practice/new" is static, so it never reaches these).
    (re.compile(r"^/practice/(\d+)$"), ROUTE_PRACTICE_OVERVIEW, ("practice_session_id",)),
    (re.compile(r"^/practice/(\d+)/run$"), ROUTE_PRACTICE_RUN, ("practice_session_id",)),
    (
        re.compile(r"^/practice/(\d+)/run/(\d+)$"),
        ROUTE_PRACTICE_RUN_DRILL,
        ("practice_session_id", "practice_drill_id"),
    ),
    # Task 2026-10-23_goals_measurements ("/goals/new" is static, so it never reaches these).
    (re.compile(r"^/goals/(\d+)$"), ROUTE_GOAL, ("goal_id",)),
    (re.compile(r"^/measurements/(\d+)$"), ROUTE_MEASUREMENT, ("physical_measurement_id",)),
)

NAV_SHOWN_CLASS = "hoopster-nav"
NAV_HIDDEN_CLASS = "hoopster-nav hoopster-hidden"
DEFAULT_ZONE = "UTC"
MAX_ZONE_NAME_LENGTH = 64
ISO_DATE_LENGTH = 10


def resolve_route(pathname: str) -> tuple[str, dict[str, str]]:
    """
    Description:
        Maps a URL path to a route name and its parameters.

    Parameters:
        pathname: the URL path, e.g. "/practice/12/court".

    Return Values:
        tuple[str, dict[str, str]]: (route name, parameters). Names: home, practice_new,
        practice_court (parameter "practice_session_id"), players, player_edit (parameter
        "player_id"), dashboards, drills, drill_new, drill_edit (parameter "drill_id"),
        drill_types, drill_sets, drill_set_new, drill_set_edit (parameter "drill_set_id"),
        templates, template_new, template_edit (parameter "practice_template_id"), games,
        game_new, game, game_live and game_summary (parameter "game_id"), seasons,
        season_edit (parameter "season_id"), practice_overview and practice_run (parameter
        "practice_session_id"), practice_run_drill (parameters "practice_session_id" and
        "practice_drill_id"), settings, settings_deleted, settings_export,
        settings_retention, settings_devices, settings_enroll, settings_password,
        settings_about, settings_manual (task 2026-10-23_settings_hub), import (task
        2026-10-23_drill_import), goals, goal_new, goal (parameter "goal_id"),
        measurements, measurement (parameter "physical_measurement_id"), physical_tests
        (task 2026-10-23_goals_measurements), not_found.

    Notes:
        Pure. Static paths (STATIC_ROUTES) are matched before the id patterns
        (PATTERN_ROUTES); parameters are the matched digits as strings. So "/games/new" is
        the New game form, never a game id.
    """
    path = pathname or "/"
    if path in STATIC_ROUTES:
        return STATIC_ROUTES[path], {}
    for pattern, route, param_names in PATTERN_ROUTES:
        match = pattern.match(path)
        if match:
            return route, dict(zip(param_names, match.groups(), strict=True))
    return ROUTE_NOT_FOUND, {}


def normalise_clock(clock: object) -> DeviceClock:
    """
    Description:
        Validates the device-clock store sent by the browser.

    Parameters:
        clock: the store's data, expected {"today": "YYYY-MM-DD", "zone": "<IANA zone>"}.

    Return Values:
        DeviceClock: the valid parts; an invalid date becomes None and an invalid zone
        becomes "UTC".

    Notes:
        Pure. The zone is checked for shape only; format_device_time falls back to UTC for
        an unknown zone.
    """
    today: str | None = None
    zone = DEFAULT_ZONE
    if isinstance(clock, dict):
        raw_today = clock.get("today")
        if isinstance(raw_today, str) and len(raw_today) == ISO_DATE_LENGTH:
            try:
                date.fromisoformat(raw_today)
                today = raw_today
            except ValueError:
                today = None
        raw_zone = clock.get("zone")
        if isinstance(raw_zone, str) and 0 < len(raw_zone) <= MAX_ZONE_NAME_LENGTH:
            zone = raw_zone
    return {"today": today, "zone": zone}


def route_page(
    clock: dict | None,
    auth_state: dict | None,
    pathname: str | None,
    page_builders: Mapping[str, PageBuilder],
) -> tuple[Component, str]:
    """
    Description:
        Builds the page for the current address: the sign-in page while signed out (every
        path), otherwise the routed page, with the bottom bar shown.

    Parameters:
        clock: the device-clock store (validated here).
        auth_state: the auth-state store; only its change matters (it re-runs the router).
        pathname: the URL path.
        page_builders: the builders keyed by route name: "home", "practice_new",
            "practice_court", "players", "player_edit" and the library routes "drills",
            "drill_new", "drill_edit", "drill_types", "drill_sets", "drill_set_new",
            "drill_set_edit", "templates", "template_new", "template_edit", and the game
            routes "games", "game_new", "game", "game_live", "game_summary", "seasons",
            "season_edit" (task 2026-10-23_games), "practice_overview", "practice_run",
            "practice_run_drill" (practice_drills 2b), and the Settings routes "settings",
            "settings_deleted", "settings_export", "settings_retention",
            "settings_devices", "settings_enroll", "settings_password", "settings_about",
            "settings_manual" (task 2026-10-23_settings_hub), and "import" (task
            2026-10-23_drill_import), and "dashboards" (task 2026-10-23_dashboards; no
            longer a coming-soon page), and "goals", "goal_new", "goal", "measurements",
            "measurement", "physical_tests" (task 2026-10-23_goals_measurements; physical
            tests need no player).

    Return Values:
        tuple[Component, str]: page-content children and the app-nav className.

    Notes:
        A boundary: a builder's exception is logged with a reference and shown as the error
        page. Without a live player profile, every route except those in
        ROUTES_WITHOUT_PLAYER (Players, the drill library, D5, and Settings) shows the
        no-player page,
        which links to Players (REQ-073). A route without a registered builder shows the
        not-found page.
    """
    if not current_user.is_authenticated:
        return login_layout(), NAV_HIDDEN_CLASS

    route, params = resolve_route(pathname or "/")
    if route == ROUTE_NOT_FOUND or route not in page_builders:
        return not_found_page(), NAV_SHOWN_CLASS

    try:
        player_id = current_player_id()
        if player_id is None and route not in ROUTES_WITHOUT_PLAYER:
            logger.warning("No live player profile; showing the no-player page")
            return no_player_page(), NAV_SHOWN_CLASS
        return page_builders[route](player_id, normalise_clock(clock), params), NAV_SHOWN_CLASS
    except Exception as exc:
        ref = new_reference_code()
        log_failure(logger, exc, "Page build failed for route %s (ref %s)", route, ref)
        return error_page(ref), NAV_SHOWN_CLASS


def register_router(app: Dash, page_builders: Mapping[str, PageBuilder]) -> None:
    """
    Description:
        Registers the device clock, the router and the bottom-bar drawer callbacks.

    Parameters:
        app: the Dash application.
        page_builders: route name to builder (see route_page for the names, the Settings
            routes of task 2026-10-23_settings_hub and "dashboards" included).

    Return Values:
        None.

    Notes:
        Clock first, router second: the clock (Input url.pathname) runs on every
        navigation, and the router (prevent_initial_call) runs on each new clock value or
        auth change. The drawer outputs are also written by close_drawers and logout, so
        those writers use allow_duplicate.
    """
    app.clientside_callback(
        ClientsideFunction("hoopster", "device_clock"),
        Output("device-clock", "data"),
        Input("url", "pathname"),
    )

    def route_current_page(
        clock: dict | None, auth_state: dict | None, pathname: str | None
    ) -> tuple[Component, str]:
        """
        Description:
            The router callback; delegates to route_page with the page builders.

        Parameters:
            clock: device-clock.data.
            auth_state: auth-state.data.
            pathname: url.pathname (State).

        Return Values:
            tuple[Component, str]: page-content children and the app-nav className.

        Notes:
            None.
        """
        return route_page(clock, auth_state, pathname, page_builders)

    app.callback(
        Output("page-content", "children"),
        Output("app-nav", "className"),
        Input("device-clock", "data"),
        Input("auth-state", "data"),
        State("url", "pathname"),
        prevent_initial_call=True,
    )(route_current_page)

    app.clientside_callback(
        ClientsideFunction("hoopster", "open_drawer"),
        Output("nav-log-drawer", "opened"),
        Input("nav-log", "n_clicks"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        ClientsideFunction("hoopster", "open_drawer"),
        Output("nav-more-drawer", "opened"),
        Input("nav-more", "n_clicks"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        ClientsideFunction("hoopster", "close_drawers"),
        Output("nav-log-drawer", "opened", allow_duplicate=True),
        Output("nav-more-drawer", "opened", allow_duplicate=True),
        Input("url", "pathname"),
        prevent_initial_call=True,
    )
