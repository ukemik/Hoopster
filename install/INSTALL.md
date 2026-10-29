# Hoopster - Installation Guide

The complete, self-contained guide to installing Hoopster on a fresh environment
(`rules_documentation.md` DOC-12-01). Two targets are covered: a Windows 11 PC with local
PostgreSQL and conda (Part A), and the hosted pair Neon + Render (Part B). Everything here is
ASCII so it can be pasted into psql and Windows PowerShell 5.1 unchanged.

Conventions: `<SET_PASSWORD>` and `<value>` are placeholders you replace. **No real password or
secret is ever written into this file, into any file inside the project folder, or into a
command that is logged.** PowerShell 5.1 has no `&&`: run commands one line at a time.

Last updated: 2026-10-22 (task `2026-10-20_player_drill_model`, Pass 5).

---

## 1. What you are installing

Hoopster is a private web app in which a parent and a player track the player's basketball
development: shots by court location, game box scores, practice drills, goals and physical
growth, turned into trends, shot charts and personal bests. It is Python 3.15, Dash 4 and
PostgreSQL, and runs on free hosting. The installable parts are: seven SQL files in `sql/` that
build the database (`hoopster_db`, five schemas, three roles, seed data); `environment.yaml`
with `requirements.txt` / `requirements-dev.txt`, which build the conda environment
`Hoopster_env`; and the application (`main.py`, `src/`, `config/`, `assets/`), with a one-time
setup script that creates the two accounts and the first player profile. The application
currently covers device enrollment, sign-in, Home, player profiles, and logging practice shots on
the court for one or more players (tasks `app_thin_slice` and `player_drill_model`). Part A has been
built on the reference PC; Part B (Neon + Render) is not yet verified.

| Component | Built from | Local (Part A) | Hosted (Part B) |
|---|---|---|---|
| Database `hoopster_db` | `sql/_database.sql`, `sql/_roles.sql`, five schema files | PostgreSQL 18 on the PC | Neon (free tier), US East |
| Python environment | `environment.yaml`, `requirements.txt`, `requirements-dev.txt` | conda env `Hoopster_env` at `C:\conda_envs\Hoopster_env` | Render installs `requirements.txt` natively |
| Configuration | `env.example` -> `.env` + user environment variables | Windows user variables (secrets) + `.env` (non-secrets) | Render service environment variables |
| Application | `main.py`, `src/`, `config/`, `assets/` | `python main.py` (this PC) or waitress (phones on the LAN); setup script `python -m src.setup_accounts` (section 3.5) | gunicorn on Render, `main:server` (section 4.3; *not yet verified*) |

---

## 2. Prerequisites

| Prerequisite | Version | Notes |
|---|---|---|
| **PostgreSQL** | 17 or later; **18** is what the reference PC runs | The SQL needs the PostgreSQL 17 builtin locale provider (`C.UTF-8`) and nothing newer; no extensions. On Windows the standard installer puts the client at `C:\Program Files\PostgreSQL\18\bin\psql.exe` (not on `PATH` by default; the commands below use the full path). psql is the only client needed, and the same psql connects to Neon. |
| **Miniconda** | conda 25.x (reference PC: Miniconda 25.11.1 at `C:\Users\<user>\AppData\Local\miniconda3`) | Conda 25 uses the libmamba solver by default. The environment lives outside the project folder, at `C:\conda_envs\Hoopster_env`; create the folder `C:\conda_envs` if it does not exist. Use the Anaconda Prompt, or PowerShell after a one-time `conda init powershell`. |
| **Git for Windows** | current | Needed later to push the code to GitHub, from which Render auto-deploys. **Not yet installed on the reference PC** and not needed for Part A today (the project folder is copied, not cloned). |
| **Neon account** (Part B only) | free tier | PostgreSQL 18 if offered, otherwise 17; region AWS `us-east-1` (US East). |
| **Render account** (Part B only) | free tier | Web service, region Virginia (US East), auto-deploy from the GitHub `main` branch. |
| **A free uptime pinger** (Part B only) | any | Calls the health page about every 10 minutes so the service does not sleep; see section 4.5. |

Disk and network: the conda create downloads a few hundred MB (Python, the packages, then the
Playwright Chromium browser). Expect a few minutes.

---

## 3. Part A - Windows 11 local install

### 3.1 Get the project folder

Copy the whole `hoopster/` project folder to the new PC (once the code is on GitHub:
`git clone`, then the notes below about `sql/_roles.sql` apply). The folder may live anywhere;
on the reference PC it is under OneDrive, so **every path that contains a space is quoted** in the
commands below. The commands call the project root `<project root>`: the folder holding
`CLAUDE.md`, `environment.yaml` and `sql/`.

Two files are git-excluded (`.gitignore`) and therefore absent from a clone:

- `sql/_roles.sql` - the role file. Copy the tracked template `install/_roles.template.sql`
  (placeholder passwords only, DOC-12-03) to `sql/_roles.sql` and set both passwords as
  described in section 3.2 Step 2. The template is kept in step with the private file
  (DOC-12-02); never commit the filled copy.
- `.env` - produced in section 3.4 from `env.example`.

### 3.2 Database (PostgreSQL 18)

Seven files, run in this order, all with `-v ON_ERROR_STOP=1` so a failure stops the run
(`rules_coding.md` COD-21-05). Each schema file is one transaction: a failure leaves nothing
behind, and you re-run from the failed file. psql prompts for the `postgres` password each time.
Run every SQL file with psql from PowerShell as shown, not from the VS Code PostgreSQL extension
(it ignores `PGOPTIONS`, so a rebuild stops at the wipe guard below).

> **WARNING - the schema files delete data.** Running any `sql/h_*.sql` file DELETES every
> object and all data in its schema: each file starts with `DROP SCHEMA ... CASCADE`, which also
> removes the objects of later schemas that depend on it. Run `h_system`, `h_ref`, `h_data`,
> `h_analytics`, `h_staging` together, in that order. On a database with accounts the files stop
> unless you set `PGOPTIONS=-c hoopster.allow_wipe=yes`. Never do this on prod without a fresh
> backup. (`rules_coding.md` COD-21-06.)

Open PowerShell in `<project root>` (the `-f` paths below are relative to it):

```powershell
cd "<project root>"
$psql = "C:\Program Files\PostgreSQL\18\bin\psql.exe"
```

**Step 1 - create the database.** Connected to `postgres` as the local superuser. This file
must not run inside a transaction (do not add `-1`):

```powershell
& $psql -v ON_ERROR_STOP=1 -h localhost -U postgres -d postgres -f sql\_database.sql
```

It creates `hoopster_db` (UTF8, builtin locale `C.UTF-8`, time zone UTC). *Locale fallback*: if
`CREATE DATABASE` rejects the locale options (PostgreSQL 16, or a server that lacks the builtin
provider), delete the two lines `LOCALE_PROVIDER = builtin` and `BUILTIN_LOCALE = 'C.UTF-8';`
from `sql/_database.sql` (and end the `ENCODING = 'UTF8'` line with `;`); nothing else changes.
The only effect is that sorting may differ between Windows and Linux. The file fails harmlessly
if the database already exists.

**Step 2 - roles.** First set both passwords in your private copy of `sql/_roles.sql`: replace
the two `'<SET_PASSWORD>'` values on the `ALTER ROLE hoopster_app ...` and
`ALTER ROLE hoopster_agent_ro ...` lines with generated passwords (20 or more random characters;
Neon rejects weak passwords set through SQL, so use the same strength locally). Keep the copy
private; never commit it. Then run it connected to `hoopster_db` as the superuser:

```powershell
& $psql -v ON_ERROR_STOP=1 -h localhost -U postgres -d hoopster_db -f sql\_roles.sql
```

It creates `hoopster_owner` (NOLOGIN, owns every object), `hoopster_app` (the application's
write role) and `hoopster_agent_ro` (read-only, `default_transaction_read_only = on`), grants
`hoopster_owner` to the running user, revokes PUBLIC's database-level access and grants CONNECT
to the two login roles. The file is re-runnable: it creates only missing roles and (re)sets
passwords and settings. If `log_statement` is enabled on the server, the password lines would
reach the server log; in that case set the passwords with `\password hoopster_app` and
`\password hoopster_agent_ro` inside psql instead and delete the two lines from the private
copy.

**Step 3 - the five schema files, in this order**, connected to `hoopster_db`:

```powershell
& $psql -v ON_ERROR_STOP=1 -h localhost -U postgres -d hoopster_db -f sql\h_system.sql
& $psql -v ON_ERROR_STOP=1 -h localhost -U postgres -d hoopster_db -f sql\h_ref.sql
& $psql -v ON_ERROR_STOP=1 -h localhost -U postgres -d hoopster_db -f sql\h_data.sql
& $psql -v ON_ERROR_STOP=1 -h localhost -U postgres -d hoopster_db -f sql\h_analytics.sql
& $psql -v ON_ERROR_STOP=1 -h localhost -U postgres -d hoopster_db -f sql\h_staging.sql
```

The order matters: `h_system` holds the users table and the audit trigger every other table
uses; `h_data` references `h_ref`; `h_analytics` views reference `h_data` and `h_ref`;
`h_staging` publishes into `h_data`. Each file runs a wipe guard, drops its schema
(`DROP SCHEMA IF EXISTS ... CASCADE`) and rebuilds it, all in one transaction (see the warning
above). On a fresh database there are no accounts yet, so the guard lets every file through
without `PGOPTIONS`. The files also seed the reference data (25 NFHS court spots, lookup lists
including 8 drill result types and 6 symmetry types, the 44-drill starter catalogue, system user
0).

**Rebuilding the schemas** of an existing database (for example to pick up a changed schema
file in development). This deletes all data, accounts and enrolled devices; never on prod without
a fresh backup. In PowerShell, from `<project root>`:

1. Stop the application. Optionally back up first:
   `& "C:\Program Files\PostgreSQL\18\bin\pg_dump.exe" -Fc -h localhost -U postgres -d hoopster_db -f <backup file outside the project>`.
2. Allow the wipe for this terminal only:

   ```powershell
   $env:PGOPTIONS = '-c hoopster.allow_wipe=yes'
   ```

3. Run the five schema files of Step 3, in order, exactly as above. `_database.sql` and
   `_roles.sql` are **not** re-run. If a file fails, fix the cause and re-run from that file.
4. Remove the setting again:

   ```powershell
   Remove-Item Env:PGOPTIONS
   ```

5. Run the setup script again (section 3.5 Step 2): the accounts and the first player profile
   were deleted with the schemas. You can reuse the same enrollment code and passwords, so the
   test variables of section 3.4 Step 6 stay valid.
6. Re-enroll every device (section 3.5 Steps 3 and 4): the device list was deleted too, so
   every browser sees `Not Found` until it opens the enrollment link again.

Without step 2, a file run on a database that has accounts stops at once with "This file deletes
Hoopster data and accounts exist in this database. Back up first, then run with
`PGOPTIONS="-c hoopster.allow_wipe=yes"`." and changes nothing.

### 3.3 Conda environment `Hoopster_env`

The environment is defined by three files at the project root. Conda supplies only Python 3.15
and pip; pip installs `requirements.txt` (runtime, the same file Render installs) and then
`requirements-dev.txt` (Playwright for the Tester; never on Render). Nothing is ever installed
by hand into the environment (`COD-18-06`): a package change is a change to these files, then an
update command.

**Step 1 - make named environments land in `C:\conda_envs`.** `environment.yaml` carries
`name: Hoopster_env` and no `prefix:`, so the environment is created in the first entry of conda's
`envs_dirs`. In a conda-initialised shell:

```powershell
conda config --show envs_dirs
```

If `C:\conda_envs` is not the **first** entry:

```powershell
conda config --prepend envs_dirs C:\conda_envs
conda config --show envs_dirs
```

(This writes one line to `%USERPROFILE%\.condarc`.) Fallback if you prefer no `.condarc` change:
add `--prefix C:\conda_envs\Hoopster_env` to the create command and to every later update
command; activation is then by path and `name:` is ignored.

**Step 2 - create the environment**, from the project root (the pip `-r` paths resolve from the
current directory):

```powershell
cd "<project root>"
conda env create -f environment.yaml
conda env list
```

`conda env list` must show `Hoopster_env` at `C:\conda_envs\Hoopster_env`. If the pip step fails
part-way (typically a missing wheel), the environment exists half-built: fix the cause in the
files, then `conda env update -f environment.yaml --prune`, or remove and recreate
(`conda env remove -n Hoopster_env`, then the create command again). Never work around a
missing wheel by installing a compiler or a package by hand. If the solve itself hangs, retry
with `conda env create -f environment.yaml --solver=libmamba`.

**Step 3 - the Playwright browser** (one time; Playwright itself came from
`requirements-dev.txt`, the browser is a separate download). Both builds are needed: Playwright
1.64 launches headless through the separate `chromium-headless-shell` build, and each Playwright
version needs its own build, so repeat this step after any Playwright re-pin:

```powershell
C:\conda_envs\Hoopster_env\python.exe -m playwright install chromium chromium-headless-shell
```

**Step 4 - verify the interpreter and the imports.** The project's only interpreter is
`C:\conda_envs\Hoopster_env\python.exe`, always called by that absolute path, never through
`conda activate` (`COD-18-04`). Run one line at a time:

```powershell
C:\conda_envs\Hoopster_env\python.exe -c "import sys; print(sys.version); print(sys.executable)"
C:\conda_envs\Hoopster_env\python.exe -c "import importlib.metadata as m; import dash, dash_mantine_components, dash_ag_grid, plotly, flask, werkzeug, flask_login, sqlalchemy, psycopg, dotenv, openpyxl, segno, waitress, playwright; print(' | '.join(d + ' ' + m.version(d) for d in ['dash', 'dash-mantine-components', 'dash-ag-grid', 'plotly', 'Flask', 'Werkzeug', 'Flask-Login', 'SQLAlchemy', 'psycopg', 'python-dotenv', 'openpyxl', 'segno', 'waitress', 'playwright'])); print('pq impl', psycopg.pq.__impl__, '| libpq', psycopg.pq.version())"
C:\conda_envs\Hoopster_env\python.exe -c "import importlib.util; print('gunicorn present:', importlib.util.find_spec('gunicorn') is not None)"
```

Expected: the first prints `3.15.x` and `C:\conda_envs\Hoopster_env\python.exe`; the second
prints every package with the version pinned in `requirements.txt` / `requirements-dev.txt`
(no traceback), then `pq impl binary` and a six-digit libpq version number (e.g. `180004`);
the third prints `gunicorn present: False` - gunicorn is absent on Windows by design (platform
marker; waitress is the Windows server). Versions are read with `importlib.metadata.version`
because Flask's `__version__` attribute is deprecated.

### 3.4 Configuration

The application reads eleven variables from the process environment; the contract (meaning,
defaults, sources) is `config/README.md`. On the PC they come from two places, decided at Gate 1
of task `2026-10-17_dev_environment`:

- **Four secrets are Windows user environment variables**, never a file inside the project
  folder (the folder is cloud-synced): `DATABASE_URL`, `SECRET_KEY`, `DEVICE_KEY_SECRET`,
  `HEALTH_PATH`.
- **Seven non-secret settings live in `.env`** at the project root (git-excluded): `APP_ENV`,
  `LOGIN_MAX_FAILURES`, `LOGIN_WINDOW_MINUTES`, `LOGIN_LOCK_MINUTES`, `LOG_LEVEL`,
  `LOG_TIMINGS`, `PORT`. `python-dotenv` loads `.env` without overriding a variable that already
  exists in the environment, so any of these may also be set as a user variable instead.

**Step 1 - generate the three random values** with the environment's interpreter (each line
prints one value; keep the output private):

```powershell
C:\conda_envs\Hoopster_env\python.exe -c "import secrets; print(secrets.token_hex(32))"
C:\conda_envs\Hoopster_env\python.exe -c "import secrets; print(secrets.token_hex(32))"
C:\conda_envs\Hoopster_env\python.exe -c "import secrets; print('/health-' + secrets.token_urlsafe(24))"
```

**Step 2 - set the four secrets as user environment variables.** Paste the first value as
`SECRET_KEY`, the second as `DEVICE_KEY_SECRET` (they must differ), the third as `HEALTH_PATH`
(it must start with `/`). `DATABASE_URL` is the SQLAlchemy URL for role `hoopster_app` with the
password you set in section 3.2 Step 2; percent-encode reserved characters in the password
(`@` -> `%40`, `:` -> `%3A`, `/` -> `%2F`, `#` -> `%23`, `?` -> `%3F`, `%` -> `%25`):

```powershell
[Environment]::SetEnvironmentVariable('DATABASE_URL', 'postgresql+psycopg://hoopster_app:<SET_PASSWORD>@localhost:5432/hoopster_db', 'User')
[Environment]::SetEnvironmentVariable('SECRET_KEY', '<value>', 'User')
[Environment]::SetEnvironmentVariable('DEVICE_KEY_SECRET', '<value>', 'User')
[Environment]::SetEnvironmentVariable('HEALTH_PATH', '/health-<value>', 'User')
```

A newly set user variable is visible only to shells and processes started afterwards: open a
new terminal (and restart any editor or agent session) before relying on it.

**Step 3 - the agents' read-only connection string.** Every agent command and every test
connects as `hoopster_agent_ro`, never as `hoopster_app` (`COD-11.4-01`). Set it as a user
variable too, with the `hoopster_agent_ro` password from section 3.2 (plain `postgresql://`
form; tests rewrite the prefix for SQLAlchemy themselves):

```powershell
[Environment]::SetEnvironmentVariable('HOOPSTER_RO_DATABASE_URL', 'postgresql://hoopster_agent_ro:<SET_PASSWORD>@localhost:5432/hoopster_db', 'User')
```

Agents check that this variable is present and never print it.

**Step 4 - create `.env` from the template.** From the project root, copy `env.example` (no
leading dot; the template is tracked, `.env` is not) and keep only the non-secret lines:

```powershell
Copy-Item env.example .env
```

Then open `.env` in an editor and **delete the four secret lines** (`DATABASE_URL=...`,
`SECRET_KEY=...`, `DEVICE_KEY_SECRET=...`, `HEALTH_PATH=...`); the seven remaining values are
the development defaults and need no change. Equivalent one-liner that writes only the non-secret
lines (ASCII encoding, so PowerShell 5.1 cannot garble it):

```powershell
Get-Content env.example | Where-Object { $_ -notmatch '^(DATABASE_URL|SECRET_KEY|DEVICE_KEY_SECRET|HEALTH_PATH)=' } | Set-Content -Encoding ascii .env
```

**Step 5 - confirm, without printing any value**, that the five user variables exist and that no
placeholder is left in `.env`:

```powershell
foreach ($n in 'DATABASE_URL','SECRET_KEY','DEVICE_KEY_SECRET','HEALTH_PATH','HOOPSTER_RO_DATABASE_URL') { "$n set: $([bool][Environment]::GetEnvironmentVariable($n, 'User'))" }
Select-String -Path .env -Pattern 'CHANGE_ME'
```

Expected: five `True` lines and no output from `Select-String`.

**Step 6 (optional) - variables for running the project's tests.** Only needed where the
project's browser tests run, and only after section 3.5 Step 2, because they hold values you
choose there. Set three more user variables: `TEST_ENROLLMENT_CODE` (the enrollment code you
entered in the setup script), `TEST_PW_PARENT` and `TEST_PW_PLAYER` (the two account passwords).
The tests read the two account emails from the database, so no email variable is needed. Never
put the values in a file or a logged command:

```powershell
[Environment]::SetEnvironmentVariable('TEST_ENROLLMENT_CODE', '<value>', 'User')
[Environment]::SetEnvironmentVariable('TEST_PW_PARENT', '<value>', 'User')
[Environment]::SetEnvironmentVariable('TEST_PW_PLAYER', '<value>', 'User')
```

Then, in a new terminal, check presence only:

```powershell
foreach ($n in 'TEST_ENROLLMENT_CODE','TEST_PW_PARENT','TEST_PW_PLAYER') { "$n set: $([bool][Environment]::GetEnvironmentVariable($n, 'User'))" }
```

Expected: three `True` lines. Restart any agent session so it inherits them.

### 3.5 Application

Run every command from `<project root>` in a terminal opened **after** section 3.4 (so it sees
the user variables). The application needs the database (section 3.2), the environment (section
3.3) and the configuration (section 3.4).

**Step 1 - choose an enrollment code.** The enrollment code is the secret part of the link that
adds a phone, tablet or computer to Hoopster (REQ-069). Generate one and keep it private (it is
never stored in the project; the database keeps only its hash):

```powershell
C:\conda_envs\Hoopster_env\python.exe -c "import secrets; print(secrets.token_urlsafe(24))"
```

**Step 2 - run the one-time setup script.** It creates the parent account, the player account,
the first player profile and the enrollment code, in one transaction. More player profiles are
added later in the app (More, then Players). The accounts are never in the SQL
files, so no personal data or secret reaches the repository.

```powershell
C:\conda_envs\Hoopster_env\python.exe -m src.setup_accounts
```

Answer the prompts, in this order (passwords and the code are typed at hidden prompts, twice
each, and nothing is shown):

1. the parent's email, display name and password;
2. the player account's email, display name and password;
3. the first player profile's name, at the prompt "First player profile's name (Enter =
   <player account's display name>): " (press Enter to use the player account's display name);
4. the enrollment code from Step 1.

Rules, re-asked until met: emails of the form `name@example.com`, at most 200 characters, and
different from each other; names 1 to 100 characters; passwords at least 10 characters; the code
at least 16 characters of letters, digits, `_` and `-` (the Step 1 code qualifies). Nothing is
saved until every answer is valid; Ctrl+C at any prompt cancels with nothing saved. On success
the script logs the created accounts, "Created player profile <name> (player <id>)" and
"Enrollment is ON. ...". Exit code 0 = success,
1 = refused, failed or cancelled, 2 = configuration error (the message names the variable).
The script runs only once: a second run is refused with "Setup has already been run; use
--reset-password EMAIL to change a password."

**Step 3 - start the application on this PC** and add the PC's browser:

```powershell
C:\conda_envs\Hoopster_env\python.exe main.py
```

Wait about 10 seconds, then open `http://127.0.0.1:8050/enroll/<code>` (the Step 1 code) in the
browser, keep or change the suggested device name, and click **Add this device**. The sign-in
page appears; sign in with either account. The server listens on `127.0.0.1` only, on `PORT`
from `.env` (8050). Debug mode and hot reload are never on: stop the server with Ctrl+C and start
it again after any code change. Logs go to the terminal (stdout); nothing is written to disk.

**Step 4 - phones and tablets on the same Wi-Fi.** Stop the Step 3 server and start waitress,
which listens on every network interface of the PC:

```powershell
C:\conda_envs\Hoopster_env\python.exe -m waitress --listen=0.0.0.0:8050 main:server
```

The first time, Windows Firewall asks whether to allow Python: allow it for **Private** networks
only, never Public. Find the PC's address with `ipconfig` (the IPv4 address of the Wi-Fi
adapter, e.g. `192.168.1.20`), then on the phone open `http://<PC-IP>:8050/enroll/<code>`, add
the device and sign in. The port is in this command (`PORT` is read only by `main.py`). Keep
`APP_ENV=development` for this: it leaves the cookies' `Secure` flag off so plain http works on
the local network.

**Step 5 - reset a password** (also signs that account out on every device):

```powershell
C:\conda_envs\Hoopster_env\python.exe -m src.setup_accounts --reset-password <email>
```

It asks for the new password twice at a hidden prompt. "No account with that email." means the
email matches neither account. If you used section 3.4 Step 6, update `TEST_PW_PARENT` or
`TEST_PW_PLAYER` too. (A signed-in user can also change their own password, or reset the other
account's, in Settings > Password & sign-in.)

**Step 5a - set a new setup-link code** (turns the setup link on again after it was turned off in
Settings > Add a device, or replaces a code that leaked):

```powershell
C:\conda_envs\Hoopster_env\python.exe -m src.setup_accounts --set-enrollment-code
```

It asks for the new code twice at a hidden prompt (at least 16 characters of letters, digits,
`_` and `-`) and logs "Setup link is ON. Open <app address>/enroll/<the code you entered>." It
cannot be combined with `--reset-password`. If you used section 3.4 Step 6, update
`TEST_ENROLLMENT_CODE` too, and keep the setup link on in development: the browser tests enroll
through it. Once the real devices are added, more devices are best added with the one-time QR
code of Settings > Add a device, and the setup link can be turned off there.

**Step 6 - compile check** (the Orchestrator runs it after every Developer pass; harmless to run
any time; no output means success):

```powershell
C:\conda_envs\Hoopster_env\python.exe -m compileall -q main.py src config
```

A new device always needs the enrollment link once. A browser that has not been added - or has
had its data cleared - sees only a plain `Not Found` page on every address.

### 3.6 Verification checklist (Part A)

| # | Check | Command / where | Expected |
|---|---|---|---|
| 1 | Database reachable as the read-only role | `& $psql -h localhost -U hoopster_agent_ro -d hoopster_db -c "SELECT current_user, current_database(), version();"` | `hoopster_agent_ro`, `hoopster_db`, a `PostgreSQL 18` (or 17) version string |
| 2 | Five schemas present | `& $psql -h localhost -U hoopster_agent_ro -d hoopster_db -c "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'h\_%' ORDER BY 1;"` | `h_analytics`, `h_data`, `h_ref`, `h_staging`, `h_system` |
| 3 | Seed data present | `& $psql -h localhost -U hoopster_agent_ro -d hoopster_db -c "SELECT (SELECT count(*) FROM h_ref.court_spots) AS spots, (SELECT count(*) FROM h_data.drills) AS drills, (SELECT count(*) FROM h_ref.result_types) AS result_types, (SELECT count(*) FROM h_system.users WHERE user_id = 0) AS system_user;"` | `25`, `44`, `8`, `1` |
| 4 | Read-only role cannot write | `& $psql -h localhost -U hoopster_agent_ro -d hoopster_db -c "UPDATE h_system.app_settings SET setting_value = setting_value;"` | an error: cannot execute UPDATE in a read-only transaction |
| 5 | Environment location | `conda env list` | `Hoopster_env` at `C:\conda_envs\Hoopster_env` |
| 6 | Interpreter | section 3.3 Step 4, first line | `3.15.x`, `C:\conda_envs\Hoopster_env\python.exe` |
| 7 | Imports and pins | section 3.3 Step 4, second line | every version equals its pin; `pq impl binary`; libpq number |
| 8 | gunicorn absent on Windows | section 3.3 Step 4, third line | `gunicorn present: False` |
| 9 | Playwright browser | `C:\conda_envs\Hoopster_env\python.exe -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); b = p.chromium.launch(); print('chromium', b.version); b.close(); p.stop()"` | `chromium <version>`, no error |
| 10 | Secrets and read-only URL set | section 3.4 Step 5 | five `True` |
| 11 | `.env` complete | section 3.4 Step 5 | no `CHANGE_ME` |
| 12 | Application starts and refuses unknown devices | Start the app (section 3.5 Step 3), wait about 10 seconds, then in a second terminal: `curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:8050/` and `curl.exe -s "http://127.0.0.1:8050$env:HEALTH_PATH"` | the first prints `404` (curl sends no device cookie; the body is `Not Found`); the second prints `ok`. A browser that has been enrolled shows the sign-in page at `http://127.0.0.1:8050/` |

The project's own test for checks 6 to 10 is `test006_environment.py` in
`.agent/operations/testing/py/`, run from the project root as
`C:\conda_envs\Hoopster_env\python.exe .agent\operations\testing\py\test006_environment.py`.
It prints one `PASS`, `FAIL` or `SKIP` line per check and ends with
`RESULT test006_environment : PASS` (exit code 0) or `RESULT test006_environment : FAIL`
(exit code 3), and never prints a secret. Checks 5 and 11 are manual (the script never
reads `.env`).

---

## 4. Part B - Hosted install (Neon + Render)

Prod is deferred; nothing in this part has been run yet. It records what is decided and marks
every unverified point **"not yet verified on Neon/Render"**. When the deployment task runs it,
the Overseer reports what differed and this part is corrected (DOC-12-02).

### 4.1 Neon project

1. Create a Neon project (free tier), **PostgreSQL 18 if offered** (17 is the minimum), region
   **AWS us-east-1**. Neon gives you a project owner role (`neondb_owner`) and a default database
   `neondb`. Prod uses the `main` branch only; there is no dev branch (dev is the local PC).
2. Take the connection string from the Neon console. Every Neon connection needs
   `sslmode=require`; psql from the local PostgreSQL installation connects to Neon with it.

### 4.2 Database on Neon

The **same seven files, in the same order** as section 3.2, run with psql against Neon as the
project owner. The Neon SQL Editor could also be used, but its handling of `BEGIN`/`COMMIT` in a
multi-statement run is unconfirmed, so psql is preferred. From the project root:

```powershell
cd "<project root>"
$psql = "C:\Program Files\PostgreSQL\18\bin\psql.exe"
& $psql -v ON_ERROR_STOP=1 "postgresql://neondb_owner:<SET_PASSWORD>@<neon-host>/neondb?sslmode=require" -f sql\_database.sql
& $psql -v ON_ERROR_STOP=1 "postgresql://neondb_owner:<SET_PASSWORD>@<neon-host>/hoopster_db?sslmode=require" -f sql\_roles.sql
& $psql -v ON_ERROR_STOP=1 "postgresql://neondb_owner:<SET_PASSWORD>@<neon-host>/hoopster_db?sslmode=require" -f sql\h_system.sql
& $psql -v ON_ERROR_STOP=1 "postgresql://neondb_owner:<SET_PASSWORD>@<neon-host>/hoopster_db?sslmode=require" -f sql\h_ref.sql
& $psql -v ON_ERROR_STOP=1 "postgresql://neondb_owner:<SET_PASSWORD>@<neon-host>/hoopster_db?sslmode=require" -f sql\h_data.sql
& $psql -v ON_ERROR_STOP=1 "postgresql://neondb_owner:<SET_PASSWORD>@<neon-host>/hoopster_db?sslmode=require" -f sql\h_analytics.sql
& $psql -v ON_ERROR_STOP=1 "postgresql://neondb_owner:<SET_PASSWORD>@<neon-host>/hoopster_db?sslmode=require" -f sql\h_staging.sql
```

Points specific to Neon:

- The warning box of section 3.2 applies in full: this is for a **fresh** Neon database only.
  Never re-run a schema file on prod; a later change to prod uses a one-time change script
  (section 5). If a rebuild of prod is ever decided, take a fresh backup first.
- `_database.sql` runs connected to **`neondb`** (there is no `postgres` database to connect to)
  and creates `hoopster_db`. If Neon's server rejects the builtin locale options, apply the
  locale fallback from section 3.2 Step 1.
- `_roles.sql` runs as the project owner (`neondb_owner`) with the same private copy and
  passwords as locally, or a fresh copy of `install/_roles.template.sql` with different ones
  (section 3.1); use passwords of 20 or more random characters, since Neon rejects weak
  passwords set through SQL. `GRANT hoopster_owner TO CURRENT_USER` then
  grants it to `neondb_owner`, which is what lets the schema files `SET LOCAL ROLE
  hoopster_owner`.
- **Not yet verified on Neon** (database plan section 9, risk R5): whether `neondb_owner` may run
  `CREATE DATABASE` and `GRANT hoopster_owner TO CURRENT_USER`; Neon's password-strength rule;
  SQL Editor transaction behaviour. If a statement is refused, report it; no workaround is
  improvised (the plan is amended instead).
- **Not yet verified on Neon** (review_2 of the database task): after
  `REVOKE ALL ON DATABASE hoopster_db FROM PUBLIC`, only the database owner, `hoopster_owner`,
  `hoopster_app` and `hoopster_agent_ro` can connect to `hoopster_db`. Any other role Neon's
  console or SQL Editor uses will be refused CONNECT. That is intended; confirm the console
  still works.
- Housekeeping has no scheduler on Neon or Render (`COD-20-03`). The daily job exists in the
  database but the application does not call it yet (a later task adds the call). Nothing to
  install for it.

Agents' read-only access to prod: a Windows user variable `HOOPSTER_RO_DATABASE_URL_PROD`
holding the `hoopster_agent_ro` connection string to Neon with `sslmode=require` (database plan
section 6.13.4); set it the same way as section 3.4 Step 3 when prod exists.

### 4.3 Render web service

1. Push the project to GitHub (needs Git for Windows, section 2). `.gitignore` keeps
   `sql/_roles.sql` and `.env` out of the repository.
2. Create a Render **web service** from the GitHub repository, branch `main`, region **Virginia
   (US East)**, auto-deploy on push. Render builds with Python and installs **`requirements.txt`
   natively** (`pip install -r requirements.txt`); it must **never** install
   `requirements-dev.txt` (Playwright is local tooling). On Linux the PEP 508 markers in
   `requirements.txt` install `gunicorn` and skip `waitress`.
3. Python version: Render reads a `.python-version` file at the repository root; the deployment
   task adds it (3.15 if Render offers it, else 3.14, which this pure-Python stack tolerates).
   *Not yet verified on Render.*
4. **Start command:**
   `gunicorn main:server --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT`.
   `server` is the plain WSGI object `main.py` exposes (`COD-20-02`); Render supplies `PORT`.
   Exactly **one worker**: the enrollment wrong-code throttle lives in process memory. Threads
   serve concurrent requests. *Not yet verified on Render.*
5. **Accounts on Neon.** Run the setup script of section 3.5 Step 2 once against Neon, from the
   PC, with `DATABASE_URL` overridden for that terminal only (the user variable is untouched;
   choose a new enrollment code for prod):
   `$env:DATABASE_URL = 'postgresql+psycopg://hoopster_app:<SET_PASSWORD>@<neon-host>/hoopster_db?sslmode=require'`,
   then `C:\conda_envs\Hoopster_env\python.exe -m src.setup_accounts`, then close that terminal.
   *Not yet verified on Neon.*

### 4.4 Render environment variables

Set the **same names as section 3.4** in the service's environment settings; there is no `.env`
on Render and nothing is read from a file:

| Variable | Value on Render |
|---|---|
| `APP_ENV` | `production` (secure-cookie flags on, no debug or hot reload) |
| `DATABASE_URL` | `postgresql+psycopg://hoopster_app:<SET_PASSWORD>@<neon-host>/hoopster_db?sslmode=require` (the Neon host and the `hoopster_app` password set in section 4.2; percent-encode reserved characters) |
| `SECRET_KEY` | a fresh `secrets.token_hex(32)` value (not the PC's) |
| `DEVICE_KEY_SECRET` | a fresh `secrets.token_hex(32)` value, different from `SECRET_KEY` |
| `HEALTH_PATH` | a fresh `/health-` + `secrets.token_urlsafe(24)` value; the pinger calls it |
| `LOGIN_MAX_FAILURES`, `LOGIN_WINDOW_MINUTES`, `LOGIN_LOCK_MINUTES` | `5`, `15`, `15` (or your own thresholds) |
| `LOG_LEVEL` | `INFO` |
| `LOG_TIMINGS` | `false` |
| `PORT` | **supplied by Render itself**; do not set it |

Generate the three random values on the PC exactly as in section 3.4 Step 1 and paste them into
the Render dashboard; they go nowhere else. A later task may append variables (for example the
test credentials `TEST_ENROLLMENT_CODE`, `TEST_PW_PARENT`, `TEST_PW_PLAYER`, which are local user
variables, not Render settings); it updates `env.example`, `config/README.md` and this guide
together.

### 4.5 Uptime pinger

Register the URL `https://<your-service>.onrender.com` + the value of `HEALTH_PATH` with a free
uptime pinger, checking about every 10 minutes. The health page never touches the database, so
Neon's compute can still sleep and its free hours last; only the Render service is kept awake.
The health page answers `ok` before the device check, needs no cookie, and never opens a
database connection. *Not yet verified on Render.*

### 4.6 Verification checklist (Part B)

| # | Check | Expected |
|---|---|---|
| 1 | psql as `hoopster_agent_ro` against Neon (`?sslmode=require`): `SELECT current_user, current_database(), version();` | `hoopster_agent_ro`, `hoopster_db`, `PostgreSQL 18` (or 17) |
| 2 | Seed counts as in section 3.6 check 3 | `25`, `44`, `8`, `1` |
| 3 | Neon console / SQL Editor still connects after the PUBLIC revoke | works (*not yet verified on Neon*) |
| 4 | Render build log | `requirements.txt` installed, `gunicorn` present, no `requirements-dev.txt`, no compiler invoked |
| 5 | `https://<your-service>.onrender.com<HEALTH_PATH>`; also `https://<your-service>.onrender.com/` from a browser never enrolled on prod | the health URL returns `200` with body `ok` and opens no database connection; the plain address returns `404 Not Found` (*not yet verified on Render*) |
| 6 | On a phone, open `https://<your-service>.onrender.com/enroll/<prod code>`, add the device, sign in | the sign-in page, then Home with the bottom bar; the device cookie is marked `Secure` because `APP_ENV=production` (*not yet verified on Render*) |

---

## 5. Upgrading an existing install

**Python environment.** Any approved change to `environment.yaml`, `requirements.txt` or
`requirements-dev.txt` is applied with, from the project root:

```powershell
cd "<project root>"
conda env update -f environment.yaml --prune
```

(`--prefix C:\conda_envs\Hoopster_env` added if you chose the `--prefix` fallback in section
3.3.) `--prune` removes packages the files no longer list. Then re-run the verification lines of
section 3.3 Step 4. On Render the next push rebuilds from `requirements.txt` automatically.

**`requirements.lock.txt`** at the project root is the exact `pip freeze` snapshot of the built
environment: every package, direct and transitive, at the version actually installed. The
Orchestrator regenerates it after every environment change, so it always describes the
environment as last built. Neither conda nor Render installs it (Render installs
`requirements.txt` only). It exists for a byte-identical rebuild when one is wanted: with the
environment already created from `environment.yaml`, run inside it
`C:\conda_envs\Hoopster_env\python.exe -m pip install -r requirements.lock.txt`.

**Database.** The schema files are destructive rebuilds (section 3.2): re-running them deletes
all data, so they are never re-run on a database whose data must be kept. The local development
database may be rebuilt with the procedure in section 3.2 when a task says so. A change to an
object that already exists is delivered two ways at once
(`rules_coding.md` COD-1-02): the schema file in `sql/` is updated (so a fresh install gets the
new state from section 3.2), **and** a one-time change script is written to
`.agent/operations/development/sql/`. On an existing install run only the change script,
connected to `hoopster_db` as the superuser (local) or project owner (Neon), with
`-v ON_ERROR_STOP=1`; the script's header says what it changes and whether it is re-runnable.
Run each change script once, in date order, on every database that has not had it yet (local,
then Neon). Agents never run any of them.

**Roles.** Re-running `sql/_roles.sql` is safe at any time: it creates only missing roles and
(re)sets passwords and settings. Use it to rotate a password (then update `DATABASE_URL` /
`HOOPSTER_RO_DATABASE_URL` wherever they are set). A schema rebuild (section 3.2) does not need
it: the roles live outside the schemas and survive.

**Configuration.** When a task adds a variable it updates `env.example`, `config/README.md` and
this guide together. Compare your `.env` and user variables with `env.example` after pulling such
a change; variables are only ever appended, never renamed.

---

## 6. Keeping this guide current

This file is the single installation record for Hoopster (DOC-12-01), owned by the Developer; the
Orchestrator may correct facts it verifies itself. **Every task that changes anything affecting
installation updates it in the same task** (DOC-12-02): a new or changed dependency, a new SQL
file or a change to the run order, a new configuration variable, a new role or grant, a new
external service, a new setup or seed step, or a changed command. Such a task lists
`install/INSTALL.md` in its plan's Affected Files; the Reviewer checks it, and a task that
changes installation without updating this guide is not accepted at Gate 2. The `install/`
folder may also hold scripts this guide tells the installer to run, for example a PowerShell
script that runs the SQL files in order; scripts contain no secrets (DOC-12-03).

Files this guide depends on, and who keeps each current:

| File | What it is | Kept current by |
|---|---|---|
| `install/INSTALL.md` | this guide | Developer, in the task that changes installation |
| `install/_roles.template.sql` | tracked template of the git-excluded `sql/_roles.sql`, placeholder passwords only | Developer, in step with `sql/_roles.sql` (DOC-12-02) |
| `environment.yaml`, `requirements.txt`, `requirements-dev.txt` | the environment definition (section 3.3) | Developer, as an approved dependency change |
| `requirements.lock.txt` | the exact `pip freeze` snapshot of the built environment, for a byte-identical rebuild (section 5); not what Render installs | Orchestrator, regenerated after every environment change |
| `env.example`, `config/README.md` | the configuration contract (section 3.4) | the task that adds a variable |

Known gaps to close in later tasks: section 4.3 (`.python-version`) and every "not yet verified
on Neon/Render" mark in Part B are cleared by the deployment task (task 10), which also adds
proxy handling so the enrollment throttle sees each client's own address behind Render.
