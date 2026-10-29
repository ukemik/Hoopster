"""Access UI for Hoopster: the sign-in layout (Dash) and the enrollment page template (plain
HTML, served by Flask so a stranger's browser never loads Dash).

REQ-001 (sign in), REQ-069 (device enrollment).
"""

import logging

import dash_mantine_components as dmc
from dash.development.base_component import Component

from src.layouts import CONTROL_SIZE, form_message, page_skeleton, primary_button

logger = logging.getLogger(__name__)


# --- Sign in (REQ-001) ---

def login_layout() -> Component:
    """
    Description:
        The sign-in page: email, password, Sign in button and a feedback line.

    Parameters:
        None.

    Return Values:
        Component: the page.

    Notes:
        The router shows it on every path while signed out (REQ-002). The submit_login
        callback reads login-email and login-password and writes login-feedback.
        dmc 2.8.0 TextInput has no "type" prop, so the email type is passed to the input
        element through inputProps.
    """
    return page_skeleton(
        "Sign in",
        [
            dmc.TextInput(
                id="login-email",
                label="Email",
                inputProps={"type": "email"},
                autoComplete="username",
                size=CONTROL_SIZE,
            ),
            dmc.PasswordInput(
                id="login-password",
                label="Password",
                autoComplete="current-password",
                size=CONTROL_SIZE,
            ),
            primary_button("login-submit", "Sign in"),
            form_message("login-feedback"),
        ],
    )


# --- Enrollment page (REQ-069) ---

# Rendered with flask.render_template_string (autoescaped). Variables: device_name, message.
# Self-contained: inline CSS, no script, so nothing of the app is exposed before enrollment.
ENROLL_FORM_TEMPLATE: str = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Hoopster - add this device</title>
<style>
  body { margin: 0; padding: 16px; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: #212529; background: #fff; }
  main { max-width: 480px; margin: 0 auto; }
  h1 { font-size: 1.5rem; margin: 0 0 16px; }
  label { display: block; font-weight: 600; margin-bottom: 6px; }
  input, button { box-sizing: border-box; width: 100%; min-height: 48px; font-size: 1rem; border-radius: 8px; }
  input { padding: 8px 12px; border: 1px solid #ced4da; margin-bottom: 16px; }
  button { border: none; background: #fd7e14; color: #fff; font-weight: 600; cursor: pointer; touch-action: manipulation; }
  #enroll-message { color: #c92a2a; margin-top: 16px; }
</style>
</head>
<body>
<main>
<h1>Hoopster - add this device</h1>
<form id="enroll-form" method="post">
  <label for="enroll-device-name">Device name</label>
  <input id="enroll-device-name" name="device_name" maxlength="100" required value="{{ device_name }}">
  <button id="enroll-submit" type="submit">Add this device</button>
</form>
{% if message %}<p id="enroll-message">{{ message }}</p>{% endif %}
</main>
</body>
</html>
"""
