"""Application settings and constants for Hoopster.

Reads the eleven configuration variables of the contract in ``config/README.md`` from the
process environment and validates them. Error messages name the variable, never its value,
because four of them are secrets (COD-4-05, COD-14-01).
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

logger = logging.getLogger(__name__)


# --- Application constants ---

APP_TITLE = "Hoopster"

# h_system.users row 0: the acting user for writes made before sign-in and by the setup script.
SYSTEM_USER_ID = 0

# REQ-001: remember-me lasts 30 days; a constant in code per config/README.md.
REMEMBER_ME_DAYS = 30

# REQ-069 device key cookie.
DEVICE_COOKIE_NAME = "hoopster_device"
DEVICE_COOKIE_MAX_AGE_DAYS = 400
DEVICE_KEY_SALT = "hoopster-device-key"
LAST_SEEN_INTERVAL_MINUTES = 60

# REQ-069 enrollment route and its in-memory wrong-code throttle.
ENROLL_ENDPOINT = "hoopster_enroll"
ENROLL_MAX_WRONG_CODES = 5
ENROLL_WRONG_WINDOW_MINUTES = 15

# Password and enrollment-code hashing and the setup script's input rules.
PASSWORD_HASH_METHOD = "scrypt:32768:8:1"
MIN_PASSWORD_LENGTH = 10
MIN_ENROLLMENT_CODE_LENGTH = 16
ENROLLMENT_CODE_PATTERN = r"^[A-Za-z0-9_-]+$"

# REQ-041 home page.
RECENT_ACTIVITY_LIMIT = 10

# REQ-058 court save watchdog (milliseconds, used by the clientside dispatcher).
COURT_SEND_TIMEOUT_MS = 10000
COURT_WATCHDOG_INTERVAL_MS = 2000

# REQ-058 automatic resend of a failed court save (duplicated in assets/hoopster.js): every
# 5 s, at most 12 times (about a minute), then the user is asked to tap Retry.
COURT_AUTO_RETRY_INTERVAL_MS = 5000
COURT_AUTO_RETRY_MAX = 12

# Largest attempts of one X-of-Y, No-spot or free-throw totals entry (plan D7); catches a
# mistyped extra digit well inside the smallint columns. Duplicated in assets/hoopster.js.
ENTRY_ATTEMPTS_MAX = 500

# The Entries dialog lists at most this many of the picked player's newest entries (plan D12).
ENTRY_LIST_LIMIT = 200

# A device tap instant further than this from the server clock is replaced by server time.
TAPPED_AT_MAX_SKEW_HOURS = 24

# REQ-074 court player picker: up to this many participants show as buttons, more as a list.
COURT_PLAYER_BUTTONS_MAX = 3

# REQ-073 player profile: longest position text (ck_players_position in sql/h_data.sql).
PLAYER_POSITION_MAX_LENGTH = 50

# REQ-056 run-mode stopwatch and countdown: display refresh interval (practice_drills D17).
RUN_TIMER_INTERVAL_MS = 250

# --- Games and seasons (task 2026-10-23_games) ---

# The seven live +1 counters of h_data.games, in screen order (REQ-020, REQ-021). Each code is
# both the games column and the h_ref.stat_definitions stat_code. Duplicated in
# assets/hoopster_games.js through the game-stat-labels store, never as a literal.
GAME_COUNTER_CODES: tuple[str, ...] = (
    "off_rebounds",
    "def_rebounds",
    "assists",
    "steals",
    "blocks",
    "turnovers",
    "fouls",
)
# Form limits (plan D13), inside the database CHECKs (ck_games_*, smallint, numeric(4,1)).
GAME_SCORE_MAX = 999
GAME_COUNTER_MAX = 999
GAME_TARGET_MAX = 999
GAME_MINUTES_MAX = 99
# Text limits of h_data.games (ck_games_team_name, _opponent_name, _location, _teammate_names,
# _notes) and of h_data.seasons.season_name (ck_seasons_name).
GAME_TEXT_MAX_LENGTH = 100
GAME_TEAMMATES_MAX_LENGTH = 200
GAME_NOTES_MAX_LENGTH = 4000
SEASON_NAME_MAX_LENGTH = 100
# REQ-062: at most this many team, opponent and location suggestions each.
AUTOCOMPLETE_LIMIT = 50
# REQ-061 game shot chart: marker colour per h_ref.heat_map_bands band_rank (1 Cold ... 5 Hot);
# a spot without attempts is white with a grey outline.
HEAT_MAP_BAND_COLORS: dict[int, str] = {
    1: "#1c7ed6",
    2: "#74c0fc",
    3: "#adb5bd",
    4: "#ff922b",
    5: "#e03131",
}
HEAT_MAP_NO_ATTEMPTS_COLOR = "#ffffff"
HEAT_MAP_OUTLINE_COLOR = "#868e96"
# REQ-021 live +1 stats watchdog (milliseconds); duplicated in assets/hoopster_games.js.
GAME_STAT_SEND_TIMEOUT_MS = 10000
GAME_STAT_WATCHDOG_MS = 2000

# --- Settings hub (task 2026-10-23_settings_hub) ---

# Shown on Settings > About (D16); bumped at each Gate 2.
APP_VERSION = "0.1.0"
# One-time enrollment codes (REQ-069, D3): secrets.token_urlsafe(24) gives 32 URL-safe
# characters (192 random bits); a code is valid for 15 minutes and works once.
ENROLLMENT_CODE_BYTES = 24
ENROLLMENT_CODE_LENGTH = 32
ENROLLMENT_CODE_VALID_MINUTES = 15
# Retention period limits of Settings > Retention period (REQ-006, D12).
RETENTION_DAYS_MIN = 7
RETENTION_DAYS_MAX = 365
# Settings > Recently deleted shows at most this many items (D11).
RECENTLY_DELETED_LIMIT = 100
# Names of test data start with this text; the "Hide test data" filters hide them (D11).
TEST_DATA_MARKER = "ZZ Test"
# The user manual shown on Settings > About > User manual, relative to the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
USER_MANUAL_PATH = ".agent/operations/documentation/user_manual.md"

# --- Import (task 2026-10-23_drill_import; REQ-050, D4) ---

# Largest import file (bytes) and most data rows per import; ample for a drill list and safe
# on the free host (REQ-046, REQ-047).
IMPORT_MAX_BYTES = 1_000_000
IMPORT_MAX_ROWS = 500
# Resource limits on what a 1 MB file may expand to (drill_import review_1, High). An .xlsx is
# a zip: refuse it when its members would unpack to more than this many bytes in total, or when
# it has more members than any real workbook needs (a normal one has about 10-20).
IMPORT_XLSX_MAX_UNPACKED_BYTES = 20_000_000
IMPORT_XLSX_MAX_MEMBERS = 200
# Columns read from an Excel sheet beyond the widest item type's column count, whatever the
# sheet claims; enough for an unknown extra column to be reported by name.
IMPORT_EXTRA_COLUMNS = 10
# Records read from a file in total, header and blank rows included (DEF-058). Blank rows are
# counted but not kept, so they never count toward IMPORT_MAX_ROWS; this ceiling only bounds the
# time spent scanning a file padded with empty or formatted-only rows. Past it, reading stops
# and the file is refused as having too many empty rows.
IMPORT_MAX_SCANNED_RECORDS = 50_000

# --- Dashboards (task 2026-10-23_dashboards; REQ-036 to REQ-040, REQ-065) ---

# Warm-up vs fatigue chart: single field goals per bucket (shots 1-10, 11-20, ...; REQ-037).
DASH_POSITION_BUCKET_SIZE = 10
# Bests: how many longest made-shot streaks are listed (REQ-040, D13).
DASH_STREAK_LIMIT = 3
# Compare "Last N vs previous N" (REQ-065, D9): the default N and the largest N accepted.
DASH_COMPARE_N_DEFAULT = 5
DASH_COMPARE_N_MAX = 50
# Height of the trend and warm-up charts (pixels), sized for a 380 px wide phone.
DASH_CHART_HEIGHT_PX = 260
# Trend line colours: games in the theme orange, practices in blue.
DASH_GAME_LINE_COLOR = "#fd7e14"
DASH_PRACTICE_LINE_COLOR = "#1c7ed6"

# --- Goals and physical measurements (task 2026-10-23_goals_measurements) ---

# Goal form limits, inside the h_data.goals CHECKs (ck_goals_name, ck_goals_notes,
# ck_goals_window_n, target_value numeric(10,3)).
GOAL_NAME_MAX_LENGTH = 100
GOAL_NOTES_MAX_LENGTH = 4000
GOAL_TARGET_MAX = 9999999
GOAL_TARGET_DECIMALS = 3
GOAL_PERCENT_MAX = 100
GOAL_WINDOW_N_MAX = 200
# Habit goals: whole days per Monday-Sunday week (ck_goals_type_fields, D10).
HABIT_DAYS_MAX = 7
# Home lists at most this many active goals (REQ-041, D15).
HOME_GOALS_LIMIT = 5
# The all-players goal-status refresh run as user 0 (D4 amendment, 2026-10-26) runs only when
# the device date is within this many days of the server's UTC date; a device with a wrong date
# must not mark every player's goals Missed or Achieved.
GOAL_REFRESH_MAX_DATE_SKEW_DAYS = 1
# Measurement limits: measured_value numeric(7,2) > 0, notes up to 4000 characters (D13).
MEASUREMENT_VALUE_MAX = Decimal("99999.99")
MEASUREMENT_DECIMALS = 2
MEASUREMENT_NOTES_MAX_LENGTH = 4000
# The history list under the chart shows at most this many rows of one test.
MEASUREMENT_HISTORY_LIMIT = 100
# The earliest date a measurement may carry.
EARLIEST_ENTRY_DATE = date(1900, 1, 1)
# Physical tests (h_ref.physical_tests: ck_physical_tests_name, ck_physical_tests_unit); the
# dict order is the option order.
PHYSICAL_TEST_NAME_MAX_LENGTH = 100
PHYSICAL_TEST_UNITS: dict[str, str] = {"in": "inches", "lb": "pounds", "s": "seconds"}

# The development server listens on the loopback interface only; LAN use goes through waitress.
DEV_LISTEN_HOST = "127.0.0.1"

MANTINE_THEME: dict[str, str] = {"primaryColor": "orange", "defaultRadius": "md"}

# Bottom bar items (REQ-066): (element id, label), in display order.
NAV_ITEMS: tuple[tuple[str, str], ...] = (
    ("nav-home", "Home"),
    ("nav-log", "Log"),
    ("nav-dashboards", "Dashboards"),
    ("nav-more", "More"),
)


# --- Configuration contract: names, defaults and validation rules ---

SECRET_VARIABLES: tuple[str, ...] = ("DATABASE_URL", "SECRET_KEY", "DEVICE_KEY_SECRET", "HEALTH_PATH")

DATABASE_URL_PREFIX = "postgresql+psycopg://"
HEALTH_PATH_MIN_LENGTH = 16
HEALTH_PATH_PATTERN = r"^[A-Za-z0-9_/-]+$"

APP_ENV_DEVELOPMENT = "development"
APP_ENV_PRODUCTION = "production"
APP_ENV_VALUES: frozenset[str] = frozenset({APP_ENV_DEVELOPMENT, APP_ENV_PRODUCTION})
LOG_LEVEL_VALUES: frozenset[str] = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})
BOOLEAN_TRUE = "true"
BOOLEAN_FALSE = "false"

PORT_MIN = 1
PORT_MAX = 65535
LOGIN_SETTING_MIN = 1
UNSIGNED_INTEGER_PATTERN = r"^[0-9]+$"

DEFAULT_APP_ENV = APP_ENV_DEVELOPMENT
DEFAULT_LOGIN_MAX_FAILURES = "5"
DEFAULT_LOGIN_WINDOW_MINUTES = "15"
DEFAULT_LOGIN_LOCK_MINUTES = "15"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_TIMINGS = BOOLEAN_FALSE
DEFAULT_PORT = "8050"


# --- Settings object and loader ---

@dataclass(frozen=True)
class AppSettings:
    """
    Description:
        The validated configuration of one application process, read once at startup.

    Parameters:
        database_url: SQLAlchemy URL of role hoopster_app (secret).
        secret_key: Flask session and remember-me signing key (secret).
        device_key_secret: device-key cookie signing key (secret).
        health_path: secret path of the no-database health page (secret).
        app_env: "development" or "production".
        login_max_failures: failures that lock a device + email pair (REQ-070).
        login_window_minutes: window the failures must fall within (REQ-070).
        login_lock_minutes: lock duration in minutes (REQ-070).
        log_level: DEBUG, INFO, WARNING or ERROR.
        log_timings: True logs request and SQL durations (REQ-068).
        port: local development server port.

    Return Values:
        Not applicable (data class).

    Notes:
        The four secret fields are excluded from repr, but never log their values directly.
    """

    # repr=False keeps the four secrets out of any accidental log of the object (COD-4-05).
    database_url: str = field(repr=False)
    secret_key: str = field(repr=False)
    device_key_secret: str = field(repr=False)
    health_path: str = field(repr=False)
    app_env: str
    login_max_failures: int
    login_window_minutes: int
    login_lock_minutes: int
    log_level: str
    log_timings: bool
    port: int

    @property
    def is_production(self) -> bool:
        """
        Description:
            Whether the process runs in production, which sets the Secure flag on cookies.

        Parameters:
            None.

        Return Values:
            bool: True when app_env is "production".

        Notes:
            None.
        """
        return self.app_env == APP_ENV_PRODUCTION


def _read_secret(environ: Mapping[str, str], name: str) -> str:
    """
    Description:
        Reads one secret variable and refuses a missing or blank value.

    Parameters:
        environ: the process environment (or a test mapping).
        name: the variable name.

    Return Values:
        str: the value exactly as set.

    Notes:
        The error message names the variable only, never the value.
    """
    value = environ.get(name)
    if value is None or not value.strip():
        raise ValueError(f"{name} is missing or empty")
    return value


def _read_setting(environ: Mapping[str, str], name: str, default: str) -> str:
    """
    Description:
        Reads one non-secret variable, stripped of surrounding whitespace; an unset or blank
        variable takes the documented default.

    Parameters:
        environ: the process environment (or a test mapping).
        name: the variable name.
        default: the documented default from config/README.md.

    Return Values:
        str: the stripped value or the default.

    Notes:
        None.
    """
    value = environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _parse_int_in_range(name: str, raw: str, minimum: int, maximum: int | None) -> int:
    """
    Description:
        Parses a plain decimal integer and checks its range.

    Parameters:
        name: the variable name, used in the error message.
        raw: the text to parse (digits only; no sign, spaces or underscores).
        minimum: the smallest allowed value.
        maximum: the largest allowed value, or None for no upper bound.

    Return Values:
        int: the parsed value.

    Notes:
        The text is matched against a digits-only pattern before int() is called, so no
        exception message can carry the value.
    """
    if not re.fullmatch(UNSIGNED_INTEGER_PATTERN, raw):
        raise ValueError(f"{name} must be a whole number")
    value = int(raw)
    if value < minimum or (maximum is not None and value > maximum):
        upper = "" if maximum is None else f" and at most {maximum}"
        raise ValueError(f"{name} must be at least {minimum}{upper}")
    return value


def _validate_health_path(health_path: str) -> None:
    """
    Description:
        Checks the shape of HEALTH_PATH: a leading slash, a minimum length, and only URL-safe
        characters.

    Parameters:
        health_path: the HEALTH_PATH value.

    Return Values:
        None. Raises ValueError naming HEALTH_PATH when a rule fails.

    Notes:
        None.
    """
    if not health_path.startswith("/"):
        raise ValueError("HEALTH_PATH must start with /")
    if len(health_path) < HEALTH_PATH_MIN_LENGTH:
        raise ValueError(f"HEALTH_PATH must be at least {HEALTH_PATH_MIN_LENGTH} characters long")
    if not re.fullmatch(HEALTH_PATH_PATTERN, health_path):
        raise ValueError("HEALTH_PATH may contain only letters, digits, _, / and -")


def load_settings(environ: Mapping[str, str]) -> AppSettings:
    """
    Description:
        Reads and validates the eleven configuration variables of config/README.md and
        returns them as one immutable settings object.

    Parameters:
        environ: the process environment, normally os.environ after load_dotenv().

    Return Values:
        AppSettings: the validated settings.

    Notes:
        Raises ValueError whose message starts with the variable name and never contains a
        value. The seven non-secrets fall back to their documented defaults when unset or
        blank; the four secrets are required.
    """
    database_url = _read_secret(environ, "DATABASE_URL")
    secret_key = _read_secret(environ, "SECRET_KEY")
    device_key_secret = _read_secret(environ, "DEVICE_KEY_SECRET")
    health_path = _read_secret(environ, "HEALTH_PATH")

    if not database_url.startswith(DATABASE_URL_PREFIX):
        raise ValueError(f"DATABASE_URL must start with {DATABASE_URL_PREFIX}")
    _validate_health_path(health_path)
    if secret_key == device_key_secret:
        raise ValueError("SECRET_KEY and DEVICE_KEY_SECRET must be different")

    app_env = _read_setting(environ, "APP_ENV", DEFAULT_APP_ENV)
    if app_env not in APP_ENV_VALUES:
        raise ValueError("APP_ENV must be development or production")

    login_max_failures = _parse_int_in_range(
        "LOGIN_MAX_FAILURES",
        _read_setting(environ, "LOGIN_MAX_FAILURES", DEFAULT_LOGIN_MAX_FAILURES),
        LOGIN_SETTING_MIN,
        None,
    )
    login_window_minutes = _parse_int_in_range(
        "LOGIN_WINDOW_MINUTES",
        _read_setting(environ, "LOGIN_WINDOW_MINUTES", DEFAULT_LOGIN_WINDOW_MINUTES),
        LOGIN_SETTING_MIN,
        None,
    )
    login_lock_minutes = _parse_int_in_range(
        "LOGIN_LOCK_MINUTES",
        _read_setting(environ, "LOGIN_LOCK_MINUTES", DEFAULT_LOGIN_LOCK_MINUTES),
        LOGIN_SETTING_MIN,
        None,
    )

    log_level = _read_setting(environ, "LOG_LEVEL", DEFAULT_LOG_LEVEL)
    if log_level not in LOG_LEVEL_VALUES:
        raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR")

    log_timings_text = _read_setting(environ, "LOG_TIMINGS", DEFAULT_LOG_TIMINGS).lower()
    if log_timings_text not in (BOOLEAN_TRUE, BOOLEAN_FALSE):
        raise ValueError("LOG_TIMINGS must be true or false")

    port = _parse_int_in_range(
        "PORT", _read_setting(environ, "PORT", DEFAULT_PORT), PORT_MIN, PORT_MAX
    )

    return AppSettings(
        database_url=database_url,
        secret_key=secret_key,
        device_key_secret=device_key_secret,
        health_path=health_path,
        app_env=app_env,
        login_max_failures=login_max_failures,
        login_window_minutes=login_window_minutes,
        login_lock_minutes=login_lock_minutes,
        log_level=log_level,
        log_timings=log_timings_text == BOOLEAN_TRUE,
        port=port,
    )
