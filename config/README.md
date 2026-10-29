# config/ - Configuration

`rules_coding.md` COD-2-01 assigns configuration to this folder (`COD-19-02`, `COD-19-03`).
It holds this contract and the Python modules that apply it: `app_config.py` (the settings
object `AppSettings`, the loader `load_settings`, and the application constants),
`database_config.py` (engine pool options) and `logging_config.py` (log setup).

## The configuration contract

The application reads every variable below from the **process environment**. The values come
from two places, decided at Gate 1 of task `2026-10-17_dev_environment` (2026-10-18):

- **Secrets are Windows user environment variables** on the Overseer's PC, never a file inside
  the project folder (the folder is OneDrive-synced). A newly set user variable is visible only
  to shells and processes started afterwards.
- **Non-secret settings come from `.env`** at the project root (git-excluded). In development,
  `python-dotenv` loads `.env` first; a variable that already exists in the environment is
  **not** overridden, so a non-secret value may also be set as a user variable if preferred.
- **On Render** the service's environment settings supply all of them under the same names, and
  there is no `.env`.

| Variable | Source | Meaning | Default / format |
|---|---|---|---|
| `DATABASE_URL` | user environment variable (secret) | SQLAlchemy URL for role `hoopster_app` (REQ-048) | `postgresql+psycopg://hoopster_app:<password>@localhost:5432/hoopster_db` (Neon: append `?sslmode=require`); percent-encode reserved characters in the password |
| `SECRET_KEY` | user environment variable (secret) | Signs the Flask session and the remember-me cookie (REQ-001) | 64 hex characters from `secrets.token_hex(32)` |
| `DEVICE_KEY_SECRET` | user environment variable (secret) | Signs the device-key cookie (REQ-069); separate so each key can be rotated alone | 64 hex characters, different from `SECRET_KEY` |
| `HEALTH_PATH` | user environment variable (secret) | Secret path of the no-database health page the uptime pinger calls (REQ-046) | `/health-<random>`, suffix from `secrets.token_urlsafe(24)`; must start with `/` |
| `APP_ENV` | `.env` | `development` or `production`. `production` sets the Secure flag on the session, remember-me and device cookies; `development` leaves it off so a phone can use plain http on the local network. Debug mode and hot reload are never enabled (REQ-047, REQ-067) | `development` |
| `LOGIN_MAX_FAILURES` | `.env` | Failures that lock a device + email pair (REQ-070) | `5` |
| `LOGIN_WINDOW_MINUTES` | `.env` | Window the failures must fall within (REQ-070) | `15` |
| `LOGIN_LOCK_MINUTES` | `.env` | Lock duration (REQ-070) | `15` |
| `LOG_LEVEL` | `.env` | `DEBUG`, `INFO`, `WARNING` or `ERROR` (`COD-4`) | `INFO` |
| `LOG_TIMINGS` | `.env` | `true` logs every request's and database call's duration (REQ-068, `COD-20-05`) | `false` |
| `PORT` | `.env` | Local development server port; Render sets its own (REQ-047) | `8050` |

The three `LOGIN_*` values are the `p_max_failures`, `p_window_minutes` and `p_lock_minutes`
arguments of `h_system.fn_login_lockout_status` (reuse; no database change).

How `load_settings` applies the table (once, at startup of the app and of the setup script):
- The four secrets are required; a missing or blank secret stops startup.
- A non-secret variable that is unset **or blank** takes the default in the table.
- Values are checked: `DATABASE_URL` starts with `postgresql+psycopg://`; `HEALTH_PATH` starts
  with `/`, is at least 16 characters and uses only letters, digits, `_`, `/` and `-`;
  `SECRET_KEY` differs from `DEVICE_KEY_SECRET`; the `LOGIN_*` values are whole numbers of at
  least 1; `LOG_LEVEL` is one of the four names **in upper case**; `LOG_TIMINGS` is `true` or
  `false` in any case; `PORT` is 1 to 65535.
- A failed check stops startup with a message that names the variable, never its value.
  `AppSettings` keeps the four secrets out of its repr.

Rules:
- **The four secrets never appear in any file inside the project**: not in `.env`, not in a test
  script, not in a brief. Agents never open, print or copy `.env` or any secret value
  (`COD-14-01`, `COD-4-05`, `COD-11.4-01`); they check that a variable is present, never what it
  holds.
- `env.example` (no leading dot) in the project root is the template for `.env`. It carries placeholders only
  and documents both groups, saying which names are user variables. When a task adds a variable
  it updates `env.example`, this table and `system_design.md` in the same change. Later tasks
  may append variables; they never rename these.
- `DATABASE_URL` is the application's write connection. Tests and agents use
  `HOOPSTER_RO_DATABASE_URL` (role `hoopster_agent_ro`) and never `DATABASE_URL`.
- The remember-me duration (30 days, REQ-001) is a constant in code, not configuration.
- Test credentials (`TEST_ENROLLMENT_CODE`, `TEST_PW_PARENT`, `TEST_PW_PLAYER`; app scoping plan)
  are user environment variables too, catalogued in `notes_for_agents.md` section 9 when set.
  The tests take the two account emails read-only from the database (parent = the lower
  `user_id`), so no email variable is needed. These are not application settings: the
  application never reads them.

## Creating the environment (the Human Overseer runs these; agents never install, COD-18-06)

See `system_design.md` section 4 "Environment and configuration" for the commands:
`conda env create -f environment.yaml` from the project root, with `C:\conda_envs` first in conda's
`envs_dirs` so the environment lands at `C:\conda_envs\Hoopster_env`, then
`python -m playwright install chromium chromium-headless-shell` with the environment's
interpreter (Playwright 1.64 launches headless through the separate `chromium-headless-shell`
build; each Playwright version needs its own build).
