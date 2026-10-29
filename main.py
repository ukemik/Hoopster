"""Hoopster entry point: builds the Dash application and exposes the WSGI ``server``.

REQ-047/REQ-067 (a plain WSGI server object, configuration only from the environment, no
host-specific code), REQ-068 (timing logs). Importing this module loads the configuration,
sets up logging, creates the lazy database engine (no connection is opened) and registers the
request pipeline, sign-in, router, Home, Players, practice, entry (court), drill library,
games and seasons, dashboards, practice run-mode, Settings (data and access pages), Import, goals
and measurements callbacks. REQ-059: the page head links the web app manifest and carries the
theme and Apple home-screen meta tags, so the app can be added to a phone's home screen and
opens full screen.

Run locally:  C:\\conda_envs\\Hoopster_env\\python.exe main.py   (127.0.0.1 only, never debug)
LAN phones:   C:\\conda_envs\\Hoopster_env\\python.exe -m waitress --listen=0.0.0.0:8050 main:server
"""

import logging
import os
from datetime import timedelta

from dash import Dash
from dotenv import load_dotenv

from config.app_config import APP_TITLE, DEV_LISTEN_HOST, REMEMBER_ME_DAYS, load_settings
from config.logging_config import configure_logging
from src import controller_home, controller_players, controller_practice
from src.app_controller import register_request_pipeline, register_router
from src.controller_access import register_access
from src.controller_entry import register_entry_callbacks
from src.controller_home import register_home_callbacks
from src.controller_players import register_players_callbacks
from src.controller_practice import register_practice_callbacks
from src import controller_drills, controller_sets
from src.controller_drills import register_drills_callbacks
from src.controller_sets import register_sets_callbacks
from src import controller_games, controller_seasons
from src.controller_games import register_games_callbacks
from src.controller_seasons import register_seasons_callbacks
from src import controller_dashboards
from src.controller_dashboards import register_dashboards_callbacks
from src import controller_run
from src.controller_run import register_run_callbacks
from src import controller_security, controller_settings
from src.controller_security import register_security_callbacks
from src.controller_settings import register_settings_callbacks
from src import controller_import
from src.controller_import import register_import_callbacks
from src import controller_goals, controller_measurements
from src.controller_goals import register_goals_callbacks
from src.controller_measurements import register_measurements_callbacks
from src.data_handler import init_engine
from src.layouts import app_shell

logger = logging.getLogger(__name__)

# The Werkzeug development server writes every request line, including the secret health
# path and /enroll/<code>, at INFO. Only its warnings and errors are kept (COD-4-05).
WERKZEUG_LOGGER_NAME = "werkzeug"

# REQ-059: Dash 4.4.1's default page template plus the manifest link. crossorigin
# "use-credentials" makes the browser send the device cookie with the manifest request, so
# the device check serves it; a browser without the cookie still gets the plain 404 (plan D14).
INDEX_TEMPLATE = """<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        <link rel="manifest" href="/assets/manifest.json" crossorigin="use-credentials">
        {%css%}
    </head>
    <body>
        <!--[if IE]><script>
        alert("Dash v2.7+ does not support Internet Explorer. Please use a newer browser.");
        </script><![endif]-->
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>"""

# The brand orange of the theme (Mantine "orange" 6), also in assets/manifest.json.
THEME_COLOR = "#fd7e14"


# --- Startup (plan §3.7) ---

load_dotenv()
try:
    settings = load_settings(os.environ)
except ValueError as exc:
    # The message names the variable, never its value.
    logger.error("Configuration error: %s", exc)
    raise
configure_logging(settings.log_level)
logging.getLogger(WERKZEUG_LOGGER_NAME).setLevel(logging.WARNING)
init_engine(settings.database_url, settings.log_timings)


# --- Application ---

app = Dash(
    __name__,
    title=APP_TITLE,
    update_title=None,
    suppress_callback_exceptions=True,
    use_pages=False,
    meta_tags=[
        {
            "name": "viewport",
            "content": "width=device-width, initial-scale=1, viewport-fit=cover",
        },
        {"name": "robots", "content": "noindex, nofollow"},
        # REQ-059: home-screen install, full screen, title and status bar.
        {"name": "theme-color", "content": THEME_COLOR},
        {"name": "mobile-web-app-capable", "content": "yes"},
        {"name": "apple-mobile-web-app-capable", "content": "yes"},
        {"name": "apple-mobile-web-app-title", "content": APP_TITLE},
        {"name": "apple-mobile-web-app-status-bar-style", "content": "default"},
    ],
)
app.index_string = INDEX_TEMPLATE
app.layout = app_shell
server = app.server

# Cookies (plan §3.4). Secure only in production, so a phone can use plain http on the LAN.
server.secret_key = settings.secret_key
server.config.update(
    SESSION_COOKIE_NAME="hoopster_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=settings.is_production,
    REMEMBER_COOKIE_NAME="hoopster_remember",
    REMEMBER_COOKIE_DURATION=timedelta(days=REMEMBER_ME_DAYS),
    REMEMBER_COOKIE_HTTPONLY=True,
    REMEMBER_COOKIE_SAMESITE="Lax",
    REMEMBER_COOKIE_SECURE=settings.is_production,
)

register_request_pipeline(server, settings)
register_access(app, server, settings)
register_router(
    app,
    {
        "home": controller_home.build_home_page,
        "practice_new": controller_practice.build_practice_new_page,
        "practice_court": controller_practice.build_court_page,
        "players": controller_players.build_players_page,
        "player_edit": controller_players.build_player_edit_page,
        # Drill library (practice_drills 2a; routes need no player, D5).
        "drills": controller_drills.build_drills_page,
        "drill_new": controller_drills.build_drill_form_page,
        "drill_edit": controller_drills.build_drill_form_page,
        "drill_types": controller_drills.build_drill_types_page,
        "drill_sets": controller_sets.build_sets_page,
        "drill_set_new": controller_sets.build_set_form_page,
        "drill_set_edit": controller_sets.build_set_form_page,
        "templates": controller_sets.build_templates_page,
        "template_new": controller_sets.build_template_form_page,
        "template_edit": controller_sets.build_template_form_page,
        # Games and seasons (task 2026-10-23_games; need a live player).
        "games": controller_games.build_games_page,
        "game_new": controller_games.build_game_new_page,
        "game": controller_games.build_game_page,
        "game_live": controller_games.build_game_live_page,
        "game_summary": controller_games.build_game_summary_page,
        "seasons": controller_seasons.build_seasons_page,
        "season_edit": controller_seasons.build_season_edit_page,
        # Dashboards (task 2026-10-23_dashboards; need a live player).
        "dashboards": controller_dashboards.build_dashboards_page,
        # Practice overview and run mode (practice_drills 2b; need a live player).
        "practice_overview": controller_practice.build_practice_overview_page,
        "practice_run": controller_run.build_run_page,
        "practice_run_drill": controller_run.build_run_page,
        # Settings hub (task 2026-10-23_settings_hub; no player needed).
        "settings": controller_settings.build_settings_page,
        "settings_deleted": controller_settings.build_deleted_page,
        "settings_export": controller_settings.build_export_page,
        "settings_retention": controller_settings.build_retention_page,
        "settings_devices": controller_security.build_devices_page,
        "settings_enroll": controller_security.build_enroll_page,
        "settings_password": controller_security.build_password_page,
        "settings_about": controller_settings.build_about_page,
        "settings_manual": controller_settings.build_manual_page,
        # Import page (task 2026-10-23_drill_import; no player needed).
        "import": controller_import.build_import_page,
        # Goals and measurements (task 2026-10-23_goals_measurements; physical tests need
        # no player).
        "goals": controller_goals.build_goals_page,
        "goal_new": controller_goals.build_goal_new_page,
        "goal": controller_goals.build_goal_page,
        "measurements": controller_measurements.build_measurements_page,
        "measurement": controller_measurements.build_measurement_page,
        "physical_tests": controller_measurements.build_physical_tests_page,
    },
)
register_home_callbacks(app)
register_players_callbacks(app)
register_practice_callbacks(app)
register_entry_callbacks(app)
register_drills_callbacks(app)
register_import_callbacks(app)
register_sets_callbacks(app)
register_games_callbacks(app)
register_dashboards_callbacks(app)
register_seasons_callbacks(app)
register_run_callbacks(app)
register_settings_callbacks(app)
register_security_callbacks(app, settings)
register_goals_callbacks(app)
register_measurements_callbacks(app)


if __name__ == "__main__":
    # Never debug or hot reload: the Werkzeug debugger allows code execution (plan §9.1 Q4).
    app.run(host=DEV_LISTEN_HOST, port=settings.port, debug=False)
