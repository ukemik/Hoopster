-- =============================================================================
-- sql/h_system.sql  --  schema h_system
--
-- Purpose : How the system runs: users, app settings, device enrollment, login
--           attempts, the job log, the audit-stamping trigger function and the
--           daily housekeeping functions (COD-1-06, COD-20-03).
-- !!! WARNING: running this file DELETES every object and ALL DATA in schema h_system !!!
-- It starts with DROP SCHEMA h_system CASCADE, which also removes objects of other schemas
-- that depend on it (the audit foreign keys and audit triggers of h_ref, h_data and
-- h_staging). Run the five schema files together, in order: h_system, h_ref, h_data,
-- h_analytics, h_staging (COD-21-06). While real accounts exist
-- it stops unless the session sets hoopster.allow_wipe = 'yes' (PGOPTIONS=-c hoopster.allow_wipe=yes).
-- Never run it on prod without a fresh backup.
-- Requires: sql/_database.sql and sql/_roles.sql have been run.
-- Run order (COD-21-05): _database, _roles, h_system (this file), h_ref, h_data,
--           h_analytics, h_staging.
-- Run as  : psql -v ON_ERROR_STOP=1 -d hoopster_db -f sql/h_system.sql
--           (as the local superuser, or neondb_owner on Neon; objects are owned by
--           hoopster_owner through SET LOCAL ROLE).
-- Re-runnable: drops and rebuilds this schema in one transaction; run the five
-- schema files together, in order (§ run-order table); see COD-21-06. Dropping
-- h_system also deletes the accounts, devices, enrollment code, one-time enrollment
-- codes, login attempts and job runs; app settings are re-seeded with their defaults. Afterwards run the setup
-- script (src.setup_accounts) and re-enroll every device.
--
-- The housekeeping functions refer forward to h_data, h_analytics and h_staging.
-- They are plpgsql, whose bodies are resolved when they run, not when created.
---------------
-- ┌─────┬─────────────────────┬─────────────┬─────────────────────────────────────────────────────────────┐
-- │  #  │        File         │ Connect to  │                        What it does                         │
-- ├─────┼─────────────────────┼─────────────┼─────────────────────────────────────────────────────────────┤
-- │ 1   │ sql/_database.sql   │ postgres    │ Creates the hoopster_db database                            │
-- ├─────┼─────────────────────┼─────────────┼─────────────────────────────────────────────────────────────┤
-- │ 2   │ sql/_roles.sql      │ hoopster_db │ Creates the three roles, passwords and database access      │
-- ├─────┼─────────────────────┼─────────────┼─────────────────────────────────────────────────────────────┤
-- │ 3   │ sql/h_system.sql    │ hoopster_db │ Users, settings, devices, login attempts, audit stamping    │
-- ├─────┼─────────────────────┼─────────────┼─────────────────────────────────────────────────────────────┤
-- │ 4   │ sql/h_ref.sql       │ hoopster_db │ Lookup lists, the 25 court spots, heat-map bands            │
-- ├─────┼─────────────────────┼─────────────┼─────────────────────────────────────────────────────────────┤
-- │ 5   │ sql/h_data.sql      │ hoopster_db │ Players, games, practices, shots, drills (44 seeded), goals │
-- ├─────┼─────────────────────┼─────────────┼─────────────────────────────────────────────────────────────┤
-- │ 6   │ sql/h_analytics.sql │ hoopster_db │ Views and functions for stats and dashboards                │
-- ├─────┼─────────────────────┼─────────────┼─────────────────────────────────────────────────────────────┤
-- │ 7   │ sql/h_staging.sql   │ hoopster_db │ Import staging tables and the check and publish functions   │
-- └─────┴─────────────────────┴─────────────┴─────────────────────────────────────────────────────────────┘
-- =============================================================================

SET client_encoding = 'UTF8';

BEGIN;

SET LOCAL ROLE hoopster_owner;
SELECT pg_catalog.set_config('hoopster.acting_user_id', '0', true);

-- =============================================================================
-- Drop section: guard, then remove the whole schema (Amendment A1, 2026-10-20)
-- =============================================================================
DO $$
BEGIN
    IF to_regclass('h_system.users') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM h_system.users u WHERE NOT u.is_system)
           AND current_setting('hoopster.allow_wipe', true) IS DISTINCT FROM 'yes' THEN
            RAISE EXCEPTION 'This file deletes Hoopster data and accounts exist in this database. Back up first, then run with PGOPTIONS="-c hoopster.allow_wipe=yes".';
        END IF;
    END IF;
END
$$;

DROP SCHEMA IF EXISTS h_system CASCADE;

CREATE SCHEMA h_system;
COMMENT ON SCHEMA h_system IS
    'How the system runs: users, settings, devices, login attempts, job log, audit and housekeeping functions.';

-- Global default (not per schema): per-schema defaults can only add privileges, so the
-- PUBLIC execute default on functions is removed here for every later file too.
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;


-- =============================================================================
-- Tables
-- =============================================================================

-- -----------------------------------------------------------------------------
-- h_system.users (REQ-001, REQ-003, REQ-004)
-- -----------------------------------------------------------------------------
CREATE TABLE h_system.users (
    user_id          bigint      GENERATED BY DEFAULT AS IDENTITY,
    email            text        NOT NULL,
    display_name     text        NOT NULL,
    password_hash    text,
    is_active        boolean     NOT NULL DEFAULT true,
    is_system        boolean     NOT NULL DEFAULT false,
    session_version  integer     NOT NULL DEFAULT 1,
    last_login_at    timestamptz,
    created_by       bigint      NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_by       bigint      NOT NULL,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    deleted_at       timestamptz,
    is_deleted       boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_users PRIMARY KEY (user_id),
    CONSTRAINT ck_users_email CHECK (email ~ '^[^@\s]+@[^@\s]+$' AND length(email) <= 200),
    CONSTRAINT ck_users_display_name CHECK (btrim(display_name) <> '' AND length(display_name) <= 100),
    CONSTRAINT ck_users_login CHECK (
        (is_system AND password_hash IS NULL) OR (NOT is_system AND password_hash IS NOT NULL)),
    CONSTRAINT ck_users_session_version CHECK (session_version >= 1),
    CONSTRAINT fk_users_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_users_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_system.users IS
    'Login accounts (REQ-001). User 0 is the system user that acts for writes made before login and for housekeeping. password_hash is withheld from the read-only role.';

CREATE UNIQUE INDEX ux_users_email ON h_system.users (lower(email)) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_system.app_settings
-- -----------------------------------------------------------------------------
CREATE TABLE h_system.app_settings (
    setting_key    text        NOT NULL,
    setting_value  text        NOT NULL,
    value_type     text        NOT NULL,
    description    text,
    created_by     bigint      NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_by     bigint      NOT NULL,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    deleted_at     timestamptz,
    is_deleted     boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_app_settings PRIMARY KEY (setting_key),
    CONSTRAINT ck_app_settings_key CHECK (setting_key ~ '^[a-z0-9_]+$' AND length(setting_key) <= 100),
    CONSTRAINT ck_app_settings_type CHECK (value_type IN ('integer', 'text', 'boolean')),
    CONSTRAINT ck_app_settings_value CHECK (
        (value_type <> 'integer' OR setting_value ~ '^-?[0-9]{1,9}$')
        AND (value_type <> 'boolean' OR setting_value IN ('true', 'false'))
        AND length(setting_value) <= 4000),
    CONSTRAINT ck_app_settings_description CHECK (description IS NULL OR length(description) <= 4000),
    CONSTRAINT fk_app_settings_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_app_settings_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_system.app_settings IS
    'Database-side settings read by functions (retention periods). App configuration such as lockout thresholds lives in config/, not here.';

-- -----------------------------------------------------------------------------
-- h_system.job_runs (COD-20-03)
-- -----------------------------------------------------------------------------
CREATE TABLE h_system.job_runs (
    job_run_id    bigint      GENERATED BY DEFAULT AS IDENTITY,
    job_code      text        NOT NULL,
    run_date_utc  date        NOT NULL,
    finished_at   timestamptz NOT NULL,
    detail        text,
    created_by    bigint      NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_by    bigint      NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    deleted_at    timestamptz,
    is_deleted    boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_job_runs PRIMARY KEY (job_run_id),
    CONSTRAINT ck_job_runs_code CHECK (job_code IN ('daily_housekeeping')),
    CONSTRAINT ck_job_runs_detail CHECK (detail IS NULL OR length(detail) <= 4000),
    CONSTRAINT fk_job_runs_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_job_runs_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_system.job_runs IS
    'One row per completed run of a recurring database job, so the daily housekeeping runs at most once per UTC day.';

CREATE UNIQUE INDEX ux_job_runs_job_date ON h_system.job_runs (job_code, run_date_utc) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_system.enrollment_codes (REQ-069) - one-time codes made in Settings
-- (task 2026-10-23_settings_hub). Before h_system.devices, which references it.
-- -----------------------------------------------------------------------------
CREATE TABLE h_system.enrollment_codes (
    enrollment_code_id  bigint      GENERATED BY DEFAULT AS IDENTITY,
    code_hash           text        NOT NULL,
    expires_at          timestamptz NOT NULL,
    used_at             timestamptz,
    created_by          bigint      NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_by          bigint      NOT NULL,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,
    is_deleted          boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_enrollment_codes PRIMARY KEY (enrollment_code_id),
    CONSTRAINT ck_enrollment_codes_hash CHECK (code_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_enrollment_codes_expiry CHECK (
        expires_at > created_at AND expires_at - created_at <= interval '1 hour'),
    CONSTRAINT ux_enrollment_codes_hash UNIQUE (code_hash),
    CONSTRAINT fk_enrollment_codes_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_enrollment_codes_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_system.enrollment_codes IS
    'One-time device enrollment codes made in Settings (REQ-069). code_hash is the lower-case hex SHA-256 of the code and is withheld from the read-only role. created_by is who made the code; deleted_at means cancelled; used_at is set once at enrollment. At most one open (unused, live) code. Never purged (audit history).';

-- At most one open code: a partial unique index on a constant expression.
CREATE UNIQUE INDEX ux_enrollment_codes_open ON h_system.enrollment_codes ((true))
    WHERE used_at IS NULL AND deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_system.devices (REQ-069, REQ-071)
-- -----------------------------------------------------------------------------
CREATE TABLE h_system.devices (
    device_id            bigint      GENERATED BY DEFAULT AS IDENTITY,
    device_name          text        NOT NULL,
    key_hash             text        NOT NULL,
    enrolled_at          timestamptz NOT NULL DEFAULT now(),
    enrolled_by_user_id  bigint,
    enrollment_code_id   bigint,
    last_seen_at         timestamptz,
    last_seen_user_id    bigint,
    revoked_at           timestamptz,
    revoked_by_user_id   bigint,
    is_revoked           boolean     GENERATED ALWAYS AS (revoked_at IS NOT NULL) STORED,
    created_by           bigint      NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_by           bigint      NOT NULL,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    deleted_at           timestamptz,
    is_deleted           boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_devices PRIMARY KEY (device_id),
    CONSTRAINT ck_devices_name CHECK (btrim(device_name) <> '' AND length(device_name) <= 100),
    CONSTRAINT ck_devices_key_hash CHECK (key_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_devices_revoked CHECK ((revoked_at IS NULL) = (revoked_by_user_id IS NULL)),
    CONSTRAINT ux_devices_key_hash UNIQUE (key_hash),
    CONSTRAINT fk_devices_enrolled_by FOREIGN KEY (enrolled_by_user_id) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_devices_enrollment_code FOREIGN KEY (enrollment_code_id) REFERENCES h_system.enrollment_codes (enrollment_code_id),
    CONSTRAINT fk_devices_last_seen_user FOREIGN KEY (last_seen_user_id) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_devices_revoked_by FOREIGN KEY (revoked_by_user_id) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_devices_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_devices_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_system.devices IS
    'Enrolled devices (REQ-069, REQ-071). key_hash is the lower-case hex SHA-256 of the raw device key and is withheld from the read-only role. enrollment_code_id is the one-time code the device was enrolled with (one device per code); NULL = enrolled with the setup link. Devices are revoked, not soft-deleted; re-enrolling creates a new row.';

CREATE INDEX ix_devices_active ON h_system.devices (device_id) WHERE revoked_at IS NULL AND deleted_at IS NULL;
-- One device per one-time code (single use as a database rule).
CREATE UNIQUE INDEX ux_devices_enrollment_code ON h_system.devices (enrollment_code_id)
    WHERE enrollment_code_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- h_system.enrollment_settings (REQ-069) - a single row
-- -----------------------------------------------------------------------------
CREATE TABLE h_system.enrollment_settings (
    enrollment_settings_id  smallint    NOT NULL,
    is_enabled              boolean     NOT NULL DEFAULT false,
    code_hash               text,
    code_changed_at         timestamptz,
    created_by              bigint      NOT NULL,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_by              bigint      NOT NULL,
    updated_at              timestamptz NOT NULL DEFAULT now(),
    deleted_at              timestamptz,
    is_deleted              boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_enrollment_settings PRIMARY KEY (enrollment_settings_id),
    CONSTRAINT ck_enrollment_settings_single CHECK (enrollment_settings_id = 1),
    CONSTRAINT ck_enrollment_enabled_code CHECK (NOT is_enabled OR code_hash IS NOT NULL),
    CONSTRAINT fk_enrollment_settings_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_enrollment_settings_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_system.enrollment_settings IS
    'The single enrollment row (REQ-069): whether enrollment is on, and a password-hasher hash of the enrollment code (withheld from the read-only role).';

-- -----------------------------------------------------------------------------
-- h_system.login_attempts (REQ-070) - a log; purged by age, not soft-deleted
-- -----------------------------------------------------------------------------
CREATE TABLE h_system.login_attempts (
    login_attempt_id  bigint      GENERATED BY DEFAULT AS IDENTITY,
    device_id         bigint      NOT NULL,
    attempted_email   text        NOT NULL,
    user_id           bigint,
    succeeded         boolean     NOT NULL,
    attempted_at      timestamptz NOT NULL DEFAULT now(),
    created_by        bigint      NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_by        bigint      NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    deleted_at        timestamptz,
    is_deleted        boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_login_attempts PRIMARY KEY (login_attempt_id),
    CONSTRAINT ck_login_attempts_email CHECK (
        attempted_email = lower(btrim(attempted_email)) AND length(attempted_email) <= 200),
    CONSTRAINT fk_login_attempts_device FOREIGN KEY (device_id) REFERENCES h_system.devices (device_id),
    CONSTRAINT fk_login_attempts_user FOREIGN KEY (user_id) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_login_attempts_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_login_attempts_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_system.login_attempts IS
    'Every login attempt from an enrolled device (REQ-070). The lockout key is device + email. The password is never stored. Purged after login_attempt_retention_days.';

CREATE INDEX ix_login_attempts_device_email_time
    ON h_system.login_attempts (device_id, attempted_email, attempted_at DESC);


-- =============================================================================
-- Functions: acting user, audit stamping, settings, lockout
-- =============================================================================

-- -----------------------------------------------------------------------------
-- h_system.fn_acting_user_id
-- Description : Returns the acting user set for the current transaction by
--               h_system.fn_set_acting_user. Raises when none is set, which is the
--               database-side backstop of COD-7-01.
-- Parameters  : none.
-- Return Values: bigint - h_system.users.user_id of the acting user.
-- Notes       : An empty string is what PostgreSQL returns for the setting after a
--               transaction-local value has ended in the same session.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_system.fn_acting_user_id()
RETURNS bigint
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_raw text;
BEGIN
    v_raw := pg_catalog.current_setting('hoopster.acting_user_id', true);
    IF v_raw IS NULL OR v_raw = '' THEN
        RAISE EXCEPTION 'Acting user not set: every write needs h_system.fn_set_acting_user (COD-7-01)';
    END IF;
    RETURN v_raw::bigint;
END
$$;
COMMENT ON FUNCTION h_system.fn_acting_user_id() IS
    'Acting user of the current transaction; raises if none is set (COD-7-01).';

-- -----------------------------------------------------------------------------
-- h_system.fn_set_acting_user
-- Description : Validates a user and makes it the acting user for the rest of the
--               current transaction (transaction-local setting, so it survives a
--               transaction-mode pooler and never leaks to the next transaction).
-- Parameters  : p_user_id - h_system.users.user_id: Flask-Login current_user.id, or
--               0 (system) before login and in housekeeping. Never a player or device id.
-- Return Values: void.
-- Notes       : Call first in every write transaction. Raises unless the user exists,
--               is active and is not deleted.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_system.fn_set_acting_user(p_user_id bigint)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_user_id IS NULL THEN
        RAISE EXCEPTION 'Acting user id is required (COD-7-01)';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM h_system.users u
        WHERE u.user_id = p_user_id
          AND u.is_active
          AND u.deleted_at IS NULL
    ) THEN
        RAISE EXCEPTION 'User % does not exist, is inactive or is deleted', p_user_id;
    END IF;
    PERFORM pg_catalog.set_config('hoopster.acting_user_id', p_user_id::text, true);
END
$$;
COMMENT ON FUNCTION h_system.fn_set_acting_user(bigint) IS
    'Validates the user and sets it as the acting user for the current transaction only.';

-- -----------------------------------------------------------------------------
-- h_system.tf_set_audit_fields
-- Description : BEFORE INSERT OR UPDATE row trigger on every table. INSERT: sets
--               created_by/created_at/updated_by/updated_at from the acting user and
--               now(), ignoring any supplied values. UPDATE: keeps created_by and
--               created_at from OLD and sets updated_by/updated_at.
-- Parameters  : none (trigger).
-- Return Values: trigger - the modified NEW row.
-- Notes       : Raises through h_system.fn_acting_user_id when no acting user is set,
--               so no write can happen without one (COD-7-01).
--               DEPENDENCY (Amendment A4): h_data.tf_version_guard relies on INSERT
--               stamping created_at with now() (the transaction start time) and on
--               UPDATE keeping it; it allows version contents only in the transaction
--               that created the version (version created_at = now()). Do not change
--               this to clock_timestamp() or let callers supply created_at: every
--               version-content insert would then be refused, or the guard bypassed.
--               Test 013 asserts the stamping.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_system.tf_set_audit_fields()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_user_id bigint;
BEGIN
    v_user_id := h_system.fn_acting_user_id();
    IF TG_OP = 'INSERT' THEN
        NEW.created_by := v_user_id;
        NEW.created_at := now();
    ELSE
        NEW.created_by := OLD.created_by;
        NEW.created_at := OLD.created_at;
    END IF;
    NEW.updated_by := v_user_id;
    NEW.updated_at := now();
    RETURN NEW;
END
$$;
COMMENT ON FUNCTION h_system.tf_set_audit_fields() IS
    'Audit trigger function for every table: stamps created/updated by and at from the acting user; raises without one. Dependency (Amendment A4): h_data.tf_version_guard relies on INSERT stamping created_at = now() and UPDATE keeping it; changing this to clock_timestamp() or accepting a supplied created_at would refuse every version-content insert or bypass the guard.';

-- -----------------------------------------------------------------------------
-- h_system.fn_setting_int
-- Description : Reads an integer setting from h_system.app_settings.
-- Parameters  : p_key - setting_key.
-- Return Values: integer - the setting value.
-- Notes       : Raises if the setting is missing, deleted or not an integer.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_system.fn_setting_int(p_key text)
RETURNS integer
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_value text;
    v_type  text;
BEGIN
    SELECT s.setting_value, s.value_type
    INTO v_value, v_type
    FROM h_system.app_settings s
    WHERE s.setting_key = p_key
      AND s.deleted_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Setting "%" is missing', p_key;
    END IF;
    IF v_type <> 'integer' OR v_value !~ '^-?[0-9]{1,9}$' THEN
        RAISE EXCEPTION 'Setting "%" is not an integer', p_key;
    END IF;
    RETURN v_value::integer;
END
$$;
COMMENT ON FUNCTION h_system.fn_setting_int(text) IS
    'Integer value of an app setting; raises if missing or not an integer.';

-- -----------------------------------------------------------------------------
-- h_system.fn_login_lockout_status (REQ-070)
-- Description : Lockout state of one device + email pair. Only failures after the
--               latest successful login for the pair count. The pair is locked when
--               at least p_max_failures failures fall within p_window_minutes of the
--               latest failure, until that latest failure + p_lock_minutes.
-- Parameters  : p_device_id      - h_system.devices.device_id of the verified device
--                                  cookie (never a user id).
--               p_email          - the email as typed; compared as lower(btrim()).
--               p_max_failures   - failures that trigger a lock (app configuration).
--               p_window_minutes - span the failures must fall within (app configuration).
--               p_lock_minutes   - lock length after the latest failure (app configuration).
-- Return Values: one row: is_locked, locked_until (NULL when not locked), and
--               recent_failures (failures after the latest success within the last
--               p_window_minutes, for display).
-- Notes       : While locked the app refuses to check the password and records no
--               further attempts, so an attacker cannot extend the lock. The window is
--               anchored on the latest failure so a lock always lasts its full length.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_system.fn_login_lockout_status(
    p_device_id      bigint,
    p_email          text,
    p_max_failures   integer,
    p_window_minutes integer,
    p_lock_minutes   integer)
RETURNS TABLE (is_locked boolean, locked_until timestamptz, recent_failures integer)
LANGUAGE plpgsql
STABLE
AS $$
#variable_conflict use_column
DECLARE
    v_email          text := lower(btrim(p_email));
    v_last_success   timestamptz;
    v_last_failure   timestamptz;
    v_recent         integer;
    v_lock_failures  integer := 0;
BEGIN
    IF p_max_failures IS NULL OR p_max_failures < 1
       OR p_window_minutes IS NULL OR p_window_minutes < 1
       OR p_lock_minutes IS NULL OR p_lock_minutes < 1 THEN
        RAISE EXCEPTION 'Lockout thresholds must be positive integers';
    END IF;

    SELECT max(la.attempted_at)
    INTO v_last_success
    FROM h_system.login_attempts la
    WHERE la.device_id = p_device_id
      AND la.attempted_email = v_email
      AND la.succeeded
      AND la.deleted_at IS NULL;

    SELECT max(la.attempted_at),
           (count(*) FILTER (
                WHERE la.attempted_at > now() - make_interval(mins => p_window_minutes)))::integer
    INTO v_last_failure, v_recent
    FROM h_system.login_attempts la
    WHERE la.device_id = p_device_id
      AND la.attempted_email = v_email
      AND NOT la.succeeded
      AND la.deleted_at IS NULL
      AND la.attempted_at > COALESCE(v_last_success, '-infinity'::timestamptz);

    IF v_last_failure IS NOT NULL
       AND v_last_failure + make_interval(mins => p_lock_minutes) > now() THEN
        SELECT count(*)::integer
        INTO v_lock_failures
        FROM h_system.login_attempts la
        WHERE la.device_id = p_device_id
          AND la.attempted_email = v_email
          AND NOT la.succeeded
          AND la.deleted_at IS NULL
          AND la.attempted_at > COALESCE(v_last_success, '-infinity'::timestamptz)
          AND la.attempted_at >= v_last_failure - make_interval(mins => p_window_minutes);
    END IF;

    is_locked := v_lock_failures >= p_max_failures;
    locked_until := CASE WHEN is_locked
                         THEN v_last_failure + make_interval(mins => p_lock_minutes) END;
    recent_failures := COALESCE(v_recent, 0);
    RETURN NEXT;
END
$$;
COMMENT ON FUNCTION h_system.fn_login_lockout_status(bigint, text, integer, integer, integer) IS
    'REQ-070 lockout state for a device + email pair; thresholds are passed in from app configuration.';


-- =============================================================================
-- Audit triggers
-- =============================================================================

CREATE TRIGGER trg_users_audit BEFORE INSERT OR UPDATE ON h_system.users
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_app_settings_audit BEFORE INSERT OR UPDATE ON h_system.app_settings
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_job_runs_audit BEFORE INSERT OR UPDATE ON h_system.job_runs
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_enrollment_codes_audit BEFORE INSERT OR UPDATE ON h_system.enrollment_codes
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_devices_audit BEFORE INSERT OR UPDATE ON h_system.devices
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_enrollment_settings_audit BEFORE INSERT OR UPDATE ON h_system.enrollment_settings
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_login_attempts_audit BEFORE INSERT OR UPDATE ON h_system.login_attempts
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();


-- =============================================================================
-- Seed data (acting user 0, set at the top of this file)
-- =============================================================================

-- System user 0 first: every audit foreign key in every schema points at it.
-- The identity column is BY DEFAULT, so 0 can be given explicitly and real users
-- (created later by the app's setup script) start at 1.
INSERT INTO h_system.users (user_id, email, display_name, password_hash, is_active, is_system)
VALUES (0, 'system@hoopster.invalid', 'System', NULL, true, true);

INSERT INTO h_system.app_settings (setting_key, setting_value, value_type, description)
VALUES
    ('soft_delete_retention_days',   '30', 'integer', 'Days a soft-deleted item stays recoverable before the daily purge removes it (REQ-006).'),
    ('staging_retention_days',       '30', 'integer', 'Days staged import rows are kept; the batch log is kept forever (REQ-050).'),
    ('login_attempt_retention_days', '90', 'integer', 'Days login attempts are kept as history (REQ-070).');

INSERT INTO h_system.enrollment_settings (enrollment_settings_id, is_enabled, code_hash, code_changed_at)
VALUES (1, false, NULL, NULL);


-- =============================================================================
-- Housekeeping functions (COD-20-03). Forward references to later schemas are
-- resolved at run time (plpgsql).
-- =============================================================================

-- -----------------------------------------------------------------------------
-- h_system.fn_purge_soft_deleted (REQ-006)
-- Description : Permanently deletes soft-deleted data rows whose deleted_at is older
--               than the retention period and that no other row (deleted or not)
--               still references. Children are deleted before parents, so a whole
--               deleted game or practice goes in one run. Also deletes login attempts
--               older than login_attempt_retention_days, whatever their state.
-- Parameters  : p_retention_days - days to keep; NULL means the
--               soft_delete_retention_days setting (30).
-- Return Values: 31 rows, one per table: table_name (schema-qualified), rows_purged.
-- Notes       : Order: outcome taps and entries; attempts; results; practice drills
--               and drill sets; practice players (REQ-074); practices and games;
--               version children (set items and lines, template sets, outcomes,
--               outcome groups, spots); secondary types, goals and physical
--               measurements; then versions before their lineages (templates, drill
--               sets, drills, REQ-080); drill types, seasons, physical tests,
--               players; login attempts and devices. A participant removed in the
--               app is never restored; it and everything carrying its
--               practice_player_id share one deleted_at, so they are purged together
--               after the retention period (Amendment A3). users,
--               enrollment_settings and the fixed h_ref lookups are never purged.
--               A deleted drill, drill set, template, version, season, drill type
--               or physical test still used by history
--               stays hidden instead. Only DELETEs are issued, so no audit trigger
--               fires; foreign keys are NO ACTION, so nothing cascades.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_system.fn_purge_soft_deleted(p_retention_days integer DEFAULT NULL)
RETURNS TABLE (table_name text, rows_purged bigint)
LANGUAGE plpgsql
AS $$
#variable_conflict use_column
DECLARE
    v_days        integer;
    v_login_days  integer;
    v_cutoff      timestamptz;
    v_rows        bigint;
BEGIN
    v_days := COALESCE(p_retention_days, h_system.fn_setting_int('soft_delete_retention_days'));
    IF v_days < 0 THEN
        RAISE EXCEPTION 'Retention days must not be negative';
    END IF;
    v_login_days := h_system.fn_setting_int('login_attempt_retention_days');
    v_cutoff := now() - make_interval(days => v_days);

    -- 1. Outcome taps and entries (nothing references them)
    DELETE FROM h_data.drill_attempt_outcomes t WHERE t.deleted_at < v_cutoff;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.drill_attempt_outcomes'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.shot_entries t WHERE t.deleted_at < v_cutoff;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.shot_entries'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.free_throw_entries t WHERE t.deleted_at < v_cutoff;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.free_throw_entries'; rows_purged := v_rows; RETURN NEXT;

    -- 2. Drill attempts, drill results, practice drills, practice drill sets
    DELETE FROM h_data.drill_attempts t
    WHERE t.deleted_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_attempt_outcomes c WHERE c.drill_attempt_id = t.drill_attempt_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.shot_entries c WHERE c.drill_attempt_id = t.drill_attempt_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.free_throw_entries c WHERE c.drill_attempt_id = t.drill_attempt_id);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.drill_attempts'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.drill_results t
    WHERE t.deleted_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_attempts c WHERE c.drill_result_id = t.drill_result_id);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.drill_results'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.practice_drills t
    WHERE t.deleted_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_results c WHERE c.practice_drill_id = t.practice_drill_id);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.practice_drills'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.practice_drill_sets t
    WHERE t.deleted_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM h_data.practice_drills c WHERE c.practice_drill_set_id = t.practice_drill_set_id);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.practice_drill_sets'; rows_purged := v_rows; RETURN NEXT;

    -- 3. Practice players (REQ-074): referenced by practice_player_id (Amendment A3)
    DELETE FROM h_data.practice_players t
    WHERE t.deleted_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM h_data.shot_entries c WHERE c.practice_player_id = t.practice_player_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.free_throw_entries c WHERE c.practice_player_id = t.practice_player_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_results c WHERE c.practice_player_id = t.practice_player_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_attempts c WHERE c.practice_player_id = t.practice_player_id);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.practice_players'; rows_purged := v_rows; RETURN NEXT;

    -- 4. Practice sessions and games
    DELETE FROM h_data.practice_sessions t
    WHERE t.deleted_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM h_data.shot_entries c WHERE c.practice_session_id = t.practice_session_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.free_throw_entries c WHERE c.practice_session_id = t.practice_session_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.practice_drill_sets c WHERE c.practice_session_id = t.practice_session_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.practice_drills c WHERE c.practice_session_id = t.practice_session_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_results c WHERE c.practice_session_id = t.practice_session_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_attempts c WHERE c.practice_session_id = t.practice_session_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.practice_players c WHERE c.practice_session_id = t.practice_session_id);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.practice_sessions'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.games t
    WHERE t.deleted_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM h_data.shot_entries c WHERE c.game_id = t.game_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.free_throw_entries c WHERE c.game_id = t.game_id);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.games'; rows_purged := v_rows; RETURN NEXT;

    -- 5. Version children (REQ-080)
    DELETE FROM h_data.drill_set_items t WHERE t.deleted_at < v_cutoff;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.drill_set_items'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.drill_set_lines t WHERE t.deleted_at < v_cutoff;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.drill_set_lines'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.practice_template_sets t WHERE t.deleted_at < v_cutoff;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.practice_template_sets'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.drill_outcomes t
    WHERE t.deleted_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_attempt_outcomes c WHERE c.drill_outcome_id = t.drill_outcome_id);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.drill_outcomes'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.drill_outcome_groups t
    WHERE t.deleted_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_outcomes c WHERE c.outcome_group_id = t.outcome_group_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_attempt_outcomes c WHERE c.outcome_group_id = t.outcome_group_id);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.drill_outcome_groups'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.drill_spots t WHERE t.deleted_at < v_cutoff;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.drill_spots'; rows_purged := v_rows; RETURN NEXT;

    -- 6. Secondary types, goals and physical measurements (nothing references them)
    DELETE FROM h_data.drill_secondary_types t WHERE t.deleted_at < v_cutoff;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.drill_secondary_types'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.goals t WHERE t.deleted_at < v_cutoff;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.goals'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.physical_measurements t WHERE t.deleted_at < v_cutoff;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.physical_measurements'; rows_purged := v_rows; RETURN NEXT;

    -- 7. Versions before their lineages; parents that history may still reference
    DELETE FROM h_data.practice_template_versions t
    WHERE t.deleted_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM h_data.practice_template_sets c WHERE c.practice_template_version_id = t.practice_template_version_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.practice_sessions c WHERE c.practice_template_version_id = t.practice_template_version_id);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.practice_template_versions'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.practice_templates t
    WHERE t.deleted_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM h_data.practice_template_versions c WHERE c.practice_template_id = t.practice_template_id);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.practice_templates'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.drill_set_versions t
    WHERE t.deleted_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_set_items c WHERE c.drill_set_version_id = t.drill_set_version_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_set_lines c WHERE c.drill_set_version_id = t.drill_set_version_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.practice_drill_sets c WHERE c.drill_set_version_id = t.drill_set_version_id);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.drill_set_versions'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.drill_sets t
    WHERE t.deleted_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_set_versions c WHERE c.drill_set_id = t.drill_set_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.practice_template_sets c WHERE c.drill_set_id = t.drill_set_id);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.drill_sets'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.drill_versions t
    WHERE t.deleted_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_spots c WHERE c.drill_version_id = t.drill_version_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_outcome_groups c WHERE c.drill_version_id = t.drill_version_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.practice_drills c WHERE c.drill_version_id = t.drill_version_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_results c WHERE c.drill_version_id = t.drill_version_id);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.drill_versions'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.drills t
    WHERE t.deleted_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_versions c WHERE c.drill_id = t.drill_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_secondary_types c WHERE c.drill_id = t.drill_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_set_items c WHERE c.drill_id = t.drill_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.goals c WHERE c.drill_id = t.drill_id);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.drills'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.drill_types t
    WHERE t.deleted_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM h_data.drills c WHERE c.primary_drill_type_id = t.drill_type_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_secondary_types c WHERE c.drill_type_id = t.drill_type_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.drill_set_lines c WHERE c.drill_type_id = t.drill_type_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.practice_drills c WHERE c.draw_drill_type_id = t.drill_type_id);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.drill_types'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.seasons t
    WHERE t.deleted_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM h_data.games c WHERE c.season_id = t.season_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.practice_players c WHERE c.season_id = t.season_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.goals c WHERE c.window_season_id = t.season_id);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.seasons'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_ref.physical_tests t
    WHERE t.deleted_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM h_data.physical_measurements c WHERE c.physical_test_id = t.physical_test_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.goals c WHERE c.physical_test_id = t.physical_test_id);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_ref.physical_tests'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_data.players t
    WHERE t.deleted_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM h_data.seasons c WHERE c.player_id = t.player_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.games c WHERE c.player_id = t.player_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.practice_players c WHERE c.player_id = t.player_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.goals c WHERE c.player_id = t.player_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.physical_measurements c WHERE c.player_id = t.player_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.shot_entries c WHERE c.player_id = t.player_id)
      AND NOT EXISTS (SELECT 1 FROM h_data.free_throw_entries c WHERE c.player_id = t.player_id);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_data.players'; rows_purged := v_rows; RETURN NEXT;

    -- Login attempts by age (whatever their soft-delete state), before devices
    DELETE FROM h_system.login_attempts t
    WHERE t.attempted_at < now() - make_interval(days => v_login_days);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_system.login_attempts'; rows_purged := v_rows; RETURN NEXT;

    DELETE FROM h_system.devices t
    WHERE t.deleted_at < v_cutoff
      AND NOT EXISTS (SELECT 1 FROM h_system.login_attempts c WHERE c.device_id = t.device_id);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    table_name := 'h_system.devices'; rows_purged := v_rows; RETURN NEXT;
END
$$;
COMMENT ON FUNCTION h_system.fn_purge_soft_deleted(integer) IS
    'REQ-006 daily purge (31 tables): deletes soft-deleted rows past retention that nothing references, children first and versions before their lineages; also old login attempts.';

-- -----------------------------------------------------------------------------
-- h_system.fn_purge_staging (REQ-050)
-- Description : Deletes the staged rows of import batches created before the
--               retention cutoff and stamps rows_purged_at on those batches. The
--               batch log itself is kept forever.
-- Parameters  : p_retention_days - days to keep; NULL means the
--               staging_retention_days setting (30).
-- Return Values: one row: batches (batches stamped), rows_purged (staged rows deleted).
-- Notes       : Only batches not already stamped are touched.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_system.fn_purge_staging(p_retention_days integer DEFAULT NULL)
RETURNS TABLE (batches integer, rows_purged bigint)
LANGUAGE plpgsql
AS $$
#variable_conflict use_column
DECLARE
    v_days    integer;
    v_cutoff  timestamptz;
    v_rows    bigint;
    v_batches integer;
BEGIN
    v_days := COALESCE(p_retention_days, h_system.fn_setting_int('staging_retention_days'));
    IF v_days < 0 THEN
        RAISE EXCEPTION 'Retention days must not be negative';
    END IF;
    v_cutoff := now() - make_interval(days => v_days);

    DELETE FROM h_staging.drill_import_rows r
    WHERE r.import_batch_id IN (
        SELECT b.import_batch_id
        FROM h_staging.import_batches b
        WHERE b.created_at < v_cutoff
          AND b.rows_purged_at IS NULL);
    GET DIAGNOSTICS v_rows = ROW_COUNT;

    UPDATE h_staging.import_batches b
    SET rows_purged_at = now()
    WHERE b.created_at < v_cutoff
      AND b.rows_purged_at IS NULL;
    GET DIAGNOSTICS v_batches = ROW_COUNT;

    batches := v_batches;
    rows_purged := v_rows;
    RETURN NEXT;
END
$$;
COMMENT ON FUNCTION h_system.fn_purge_staging(integer) IS
    'REQ-050: deletes staged import rows of batches older than retention; keeps the batch log.';

-- -----------------------------------------------------------------------------
-- h_system.fn_refresh_goal_statuses (REQ-032)
-- Description : Applies the automatic goal status to non-deleted, active goals whose
--               status is not manual: met -> achieved (achieved_on = p_today);
--               otherwise a deadline before p_today -> missed. Achieved goals never
--               revert automatically.
-- Parameters  : p_today     - the device-local date (REQ-055), never the server date.
--               p_player_id - h_data.players.player_id to limit to; NULL means every
--                             non-deleted player.
-- Return Values: one row per changed goal: goal_id, old_status, new_status.
-- Notes       : is_met comes from h_analytics.fn_goal_progress, which carries the
--               Open Question 4 rules. Called by the app after saves that can change
--               progress, and by the daily housekeeping.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_system.fn_refresh_goal_statuses(p_today date, p_player_id bigint DEFAULT NULL)
RETURNS TABLE (goal_id bigint, old_status text, new_status text)
LANGUAGE plpgsql
AS $$
#variable_conflict use_column
BEGIN
    IF p_today IS NULL THEN
        RAISE EXCEPTION 'p_today (the device-local date) is required';
    END IF;

    RETURN QUERY
    WITH prog AS (
        SELECT gp.goal_id AS progress_goal_id, gp.is_met
        FROM h_data.players p
        CROSS JOIN LATERAL h_analytics.fn_goal_progress(p_today, p.player_id) gp
        WHERE p.deleted_at IS NULL
          AND (p_player_id IS NULL OR p.player_id = p_player_id)
    ),
    upd AS (
        UPDATE h_data.goals g
        SET status      = CASE WHEN prog.is_met THEN 'achieved' ELSE 'missed' END,
            achieved_on = CASE WHEN prog.is_met THEN p_today ELSE g.achieved_on END
        FROM prog
        WHERE g.goal_id = prog.progress_goal_id
          AND g.deleted_at IS NULL
          AND g.status = 'active'
          AND NOT g.status_is_manual
          AND (prog.is_met OR (g.deadline IS NOT NULL AND g.deadline < p_today))
        RETURNING g.goal_id AS changed_goal_id, g.status AS changed_status
    )
    SELECT upd.changed_goal_id, 'active'::text, upd.changed_status
    FROM upd;
END
$$;
COMMENT ON FUNCTION h_system.fn_refresh_goal_statuses(date, bigint) IS
    'REQ-032 automatic goal status: active goals become achieved when met, missed after the deadline.';

-- -----------------------------------------------------------------------------
-- h_system.fn_run_daily_housekeeping (COD-20-03)
-- Description : Runs the daily jobs at most once per UTC day: soft-delete purge
--               (with login attempts), staging purge and goal-status refresh, then
--               records a job_runs row.
-- Parameters  : p_today - the device-local date (REQ-055), passed to the goal refresh.
-- Return Values: one row per step: step, ran, detail. A single row with ran = false
--               when another session holds the lock or today's run already exists.
-- Notes       : The app calls it as user 0 on the first request after login each
--               day. run_date_utc uses the server's UTC date only to stop a second
--               run on the same day; it never decides a calendar date for user data.
--               A failure rolls everything back.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_system.fn_run_daily_housekeeping(p_today date)
RETURNS TABLE (step text, ran boolean, detail text)
LANGUAGE plpgsql
AS $$
#variable_conflict use_column
DECLARE
    v_run_date      date;
    v_purge_detail  text;
    v_stage_detail  text;
    v_goal_detail   text;
    v_goals         integer;
BEGIN
    IF p_today IS NULL THEN
        RAISE EXCEPTION 'p_today (the device-local date) is required';
    END IF;

    IF NOT pg_try_advisory_xact_lock(hashtext('hoopster.daily_housekeeping')) THEN
        step := 'daily_housekeeping'; ran := false;
        detail := 'skipped: another session is running it';
        RETURN NEXT;
        RETURN;
    END IF;

    v_run_date := (now() AT TIME ZONE 'UTC')::date;

    IF EXISTS (
        SELECT 1
        FROM h_system.job_runs jr
        WHERE jr.job_code = 'daily_housekeeping'
          AND jr.run_date_utc = v_run_date
          AND jr.deleted_at IS NULL
    ) THEN
        step := 'daily_housekeeping'; ran := false;
        detail := 'already ran for ' || to_char(v_run_date, 'YYYY-MM-DD') || ' (UTC)';
        RETURN NEXT;
        RETURN;
    END IF;

    SELECT COALESCE(string_agg(pr.table_name || '=' || pr.rows_purged, ', ')
                        FILTER (WHERE pr.rows_purged > 0), 'nothing to purge')
    INTO v_purge_detail
    FROM h_system.fn_purge_soft_deleted() pr;

    SELECT 'batches=' || ps.batches || ', rows=' || ps.rows_purged
    INTO v_stage_detail
    FROM h_system.fn_purge_staging() ps;

    SELECT count(*)::integer
    INTO v_goals
    FROM h_system.fn_refresh_goal_statuses(p_today) rg;
    v_goal_detail := 'goals changed=' || v_goals;

    INSERT INTO h_system.job_runs (job_code, run_date_utc, finished_at, detail)
    VALUES ('daily_housekeeping', v_run_date, clock_timestamp(),
            left('purge: ' || v_purge_detail || '; staging: ' || v_stage_detail || '; ' || v_goal_detail, 4000));

    step := 'purge_soft_deleted'; ran := true; detail := v_purge_detail; RETURN NEXT;
    step := 'purge_staging'; ran := true; detail := v_stage_detail; RETURN NEXT;
    step := 'refresh_goal_statuses'; ran := true; detail := v_goal_detail; RETURN NEXT;
END
$$;
COMMENT ON FUNCTION h_system.fn_run_daily_housekeeping(date) IS
    'Daily jobs (purges, goal statuses), at most once per UTC day, guarded by an advisory lock and h_system.job_runs.';


-- =============================================================================
-- Grants (COD-1-06, plan section 6.13.3). Default privileges come last.
-- =============================================================================

GRANT USAGE ON SCHEMA h_system TO hoopster_app, hoopster_agent_ro;

-- hoopster_app
GRANT SELECT, INSERT, UPDATE ON h_system.users, h_system.job_runs, h_system.devices TO hoopster_app;
GRANT DELETE ON h_system.devices TO hoopster_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON h_system.login_attempts TO hoopster_app;
GRANT SELECT, UPDATE ON h_system.enrollment_settings TO hoopster_app;
GRANT SELECT, INSERT, UPDATE ON h_system.enrollment_codes TO hoopster_app;
-- UPDATE only for the retention period on Settings (task 2026-10-23_settings_hub).
GRANT SELECT, UPDATE ON h_system.app_settings TO hoopster_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA h_system TO hoopster_app;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA h_system FROM PUBLIC;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA h_system TO hoopster_app;

-- hoopster_agent_ro: no secret columns, no EXECUTE
GRANT SELECT ON h_system.app_settings, h_system.job_runs, h_system.login_attempts TO hoopster_agent_ro;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA h_system TO hoopster_agent_ro;
GRANT SELECT (user_id, email, display_name, is_active, is_system, session_version, last_login_at,
              created_by, created_at, updated_by, updated_at, deleted_at, is_deleted)
    ON h_system.users TO hoopster_agent_ro;
GRANT SELECT (device_id, device_name, enrolled_at, enrolled_by_user_id, enrollment_code_id,
              last_seen_at, last_seen_user_id, revoked_at, revoked_by_user_id, is_revoked,
              created_by, created_at, updated_by, updated_at, deleted_at, is_deleted)
    ON h_system.devices TO hoopster_agent_ro;
GRANT SELECT (enrollment_settings_id, is_enabled, code_changed_at,
              created_by, created_at, updated_by, updated_at, deleted_at, is_deleted)
    ON h_system.enrollment_settings TO hoopster_agent_ro;
-- enrollment_codes: no table-level SELECT (a default privilege would give one where
-- the table is created after the defaults below), only the columns without code_hash.
-- The table-level revoke also clears column grants, so it comes first.
REVOKE SELECT ON h_system.enrollment_codes FROM hoopster_agent_ro;
GRANT SELECT (enrollment_code_id, expires_at, used_at,
              created_by, created_at, updated_by, updated_at, deleted_at, is_deleted)
    ON h_system.enrollment_codes TO hoopster_agent_ro;

-- Default privileges for future objects in h_system. A future table here grants SELECT to
-- the read-only role: never add a secret column to one without a column-level revoke.
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_system
    GRANT SELECT, INSERT, UPDATE ON TABLES TO hoopster_app;
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_system
    GRANT SELECT ON TABLES TO hoopster_agent_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_system
    GRANT USAGE, SELECT ON SEQUENCES TO hoopster_app;
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_system
    GRANT SELECT ON SEQUENCES TO hoopster_agent_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_system
    GRANT EXECUTE ON FUNCTIONS TO hoopster_app;

COMMIT;

SELECT 'END OF SCRIPT';