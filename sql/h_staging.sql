-- =============================================================================
-- sql/h_staging.sql  --  schema h_staging
--
-- Purpose : Every import (REQ-050, REQ-027): the import batch log, one staging
--           table per item type (all data columns text, plus row status and error
--           message), and per item type a check/clean function and a publish
--           function (COD-1-06). Version 1 item type: drills.
-- !!! WARNING: running this file DELETES every object and ALL DATA in schema h_staging !!!
-- It starts with DROP SCHEMA h_staging CASCADE, which also removes objects of other schemas
-- that depend on it (nothing). Run the five schema files together, in order: h_system,
-- h_ref, h_data, h_analytics, h_staging (COD-21-06). While real accounts exist
-- it stops unless the session sets hoopster.allow_wipe = 'yes' (PGOPTIONS=-c hoopster.allow_wipe=yes).
-- Never run it on prod without a fresh backup.
-- Requires: sql/_database.sql, sql/_roles.sql, sql/h_system.sql, sql/h_ref.sql,
--           sql/h_data.sql, sql/h_analytics.sql.
-- Run order (COD-21-05): _database, _roles, h_system, h_ref, h_data, h_analytics,
--           h_staging (this file, last: its publish function writes into h_data).
-- Run as  : psql -v ON_ERROR_STOP=1 -d hoopster_db -f sql/h_staging.sql
-- Re-runnable: drops and rebuilds this schema in one transaction; run the five
-- schema files together, in order (§ run-order table); see COD-21-06.
--
-- Flow: the app parses CSV, XLSX or pasted text into staging rows (a paste screen
-- asks once for primary type, difficulty and result type and copies them onto every
-- line; each line is "Name" or "Name | description", Gate 1 Q6), calls
-- fn_check_drill_batch and commits, previews the problem rows, then calls
-- fn_publish_drill_batch, which is all or nothing. No import writes to h_data any
-- other way. List columns use ";" as the separator: secondary_types
-- "Shooting; Conditioning", spots "16; 24" or "FT". Only shooting and single-number
-- result types can be imported; outcome-button and physical-measurement drills are
-- refused with a message until they can be described in a file (Gate 1 Q8).
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

DROP SCHEMA IF EXISTS h_staging CASCADE;

CREATE SCHEMA h_staging;
COMMENT ON SCHEMA h_staging IS
    'Imports: batch log, text staging tables, check/clean and all-or-nothing publish functions (REQ-050).';


-- =============================================================================
-- Tables
-- =============================================================================

-- -----------------------------------------------------------------------------
-- h_staging.import_batches - the batch log, kept forever
-- -----------------------------------------------------------------------------
CREATE TABLE h_staging.import_batches (
    import_batch_id      bigint      GENERATED BY DEFAULT AS IDENTITY,
    item_type            text        NOT NULL,
    source_kind          text        NOT NULL,
    source_name          text,
    batch_status         text        NOT NULL DEFAULT 'loaded',
    row_count            integer     NOT NULL DEFAULT 0,
    error_row_count      integer     NOT NULL DEFAULT 0,
    checked_at           timestamptz,
    published_at         timestamptz,
    published_row_count  integer,
    rows_purged_at       timestamptz,
    created_by           bigint      NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_by           bigint      NOT NULL,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    deleted_at           timestamptz,
    is_deleted           boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_import_batches PRIMARY KEY (import_batch_id),
    CONSTRAINT ck_import_batches_item_type CHECK (item_type IN ('drill')),
    CONSTRAINT ck_import_batches_source_kind CHECK (source_kind IN ('csv', 'xlsx', 'paste')),
    CONSTRAINT ck_import_batches_source_name CHECK (source_name IS NULL OR length(source_name) <= 200),
    CONSTRAINT ck_import_batches_status CHECK (batch_status IN ('loaded', 'has_errors', 'ready', 'published', 'abandoned')),
    CONSTRAINT ck_import_batches_counts CHECK (
        row_count >= 0 AND error_row_count >= 0
        AND (published_row_count IS NULL OR published_row_count >= 0)),
    CONSTRAINT fk_import_batches_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_import_batches_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_staging.import_batches IS
    'Import batch log (REQ-050), kept forever; created_by is the importer. Staged rows are purged after staging_retention_days and rows_purged_at is stamped.';

-- -----------------------------------------------------------------------------
-- h_staging.drill_import_rows - staged drill rows, all data columns text
-- -----------------------------------------------------------------------------
CREATE TABLE h_staging.drill_import_rows (
    drill_import_row_id  bigint      GENERATED BY DEFAULT AS IDENTITY,
    import_batch_id      bigint      NOT NULL,
    row_no               integer     NOT NULL,
    drill_name           text,
    description          text,
    primary_type         text,
    secondary_types      text,
    difficulty           text,
    result_type          text,
    symmetry_type        text,
    default_target       text,
    fixed_seconds        text,
    spots                text,
    row_status           text        NOT NULL DEFAULT 'pending',
    error_message        text,
    published_drill_id   bigint,
    created_by           bigint      NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_by           bigint      NOT NULL,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    deleted_at           timestamptz,
    is_deleted           boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_drill_import_rows PRIMARY KEY (drill_import_row_id),
    CONSTRAINT ck_drill_import_rows_row_no CHECK (row_no >= 1),
    CONSTRAINT ck_drill_import_rows_status CHECK (row_status IN ('pending', 'valid', 'error', 'published')),
    CONSTRAINT fk_drill_import_rows_batch FOREIGN KEY (import_batch_id) REFERENCES h_staging.import_batches (import_batch_id),
    CONSTRAINT fk_drill_import_rows_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_drill_import_rows_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_staging.drill_import_rows IS
    'Staged drill rows (COD-1-06): text columns as uploaded, cleaned and validated by fn_check_drill_batch. published_drill_id is a trace only (no foreign key, so purges never block).';
CREATE UNIQUE INDEX ux_drill_import_rows_batch_row ON h_staging.drill_import_rows (import_batch_id, row_no) WHERE deleted_at IS NULL;


-- =============================================================================
-- Functions
-- =============================================================================

-- -----------------------------------------------------------------------------
-- h_staging.fn_check_drill_batch (REQ-050, REQ-027, REQ-028, REQ-076, REQ-079)
-- Description : Cleans and validates every live row of a drill import batch.
--               Clean: trims every column and turns empty strings into NULL;
--               rewrites difficulty, result type and symmetry type to their codes
--               (matching code or label, case-insensitive), type names to their
--               canonical spelling, and spots to "n; n" / "FT". Validate: collects
--               every error of a row, joined with "; ". Then sets each row's status
--               (valid / error) and the batch's counts, checked_at and status
--               (ready when there are rows and no errors, else has_errors).
-- Parameters  : p_import_batch_id - h_staging.import_batches id.
-- Return Values: one row per live staged row: row_no, row_status, error_message.
-- Notes       : Raises if the batch is published or abandoned. The app commits
--               after the check, then previews the problem rows. Unknown values
--               are kept as typed so the error message can quote them. Result
--               types outcome_buttons and physical are refused (Gate 1 Q8): they
--               need outcome groups or named numbers that a row cannot carry.
--               A shooting row needs spots; a fixed-time-for-reps row needs
--               fixed_seconds; neither is allowed on other result types.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_staging.fn_check_drill_batch(p_import_batch_id bigint)
RETURNS TABLE (row_no integer, row_status text, error_message text)
LANGUAGE plpgsql
AS $$
#variable_conflict use_column
DECLARE
    v_status  text;
    v_rows    integer;
    v_errors  integer;
BEGIN
    SELECT b.batch_status
    INTO v_status
    FROM h_staging.import_batches b
    WHERE b.import_batch_id = p_import_batch_id
      AND b.deleted_at IS NULL
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Import batch % does not exist', p_import_batch_id;
    END IF;
    IF v_status IN ('published', 'abandoned') THEN
        RAISE EXCEPTION 'Import batch % is % and cannot be checked again', p_import_batch_id, v_status;
    END IF;

    -- 1. Clean: trim, empty -> NULL
    UPDATE h_staging.drill_import_rows r
    SET drill_name         = NULLIF(btrim(r.drill_name, E' \t\r\n'), ''),
        description        = NULLIF(btrim(r.description, E' \t\r\n'), ''),
        primary_type       = NULLIF(btrim(r.primary_type, E' \t\r\n'), ''),
        secondary_types    = NULLIF(btrim(r.secondary_types, E' \t\r\n'), ''),
        difficulty         = NULLIF(btrim(r.difficulty, E' \t\r\n'), ''),
        result_type        = NULLIF(btrim(r.result_type, E' \t\r\n'), ''),
        symmetry_type      = NULLIF(btrim(r.symmetry_type, E' \t\r\n'), ''),
        default_target     = NULLIF(btrim(r.default_target, E' \t\r\n'), ''),
        fixed_seconds      = NULLIF(btrim(r.fixed_seconds, E' \t\r\n'), ''),
        spots              = NULLIF(btrim(r.spots, E' \t\r\n'), '')
    WHERE r.import_batch_id = p_import_batch_id
      AND r.deleted_at IS NULL;

    -- Clean: difficulty, result type and symmetry type to codes (code or label, case-insensitive)
    UPDATE h_staging.drill_import_rows r
    SET difficulty = dl.difficulty_code
    FROM h_ref.difficulty_levels dl
    WHERE r.import_batch_id = p_import_batch_id
      AND r.deleted_at IS NULL
      AND dl.deleted_at IS NULL
      AND lower(r.difficulty) IN (lower(dl.difficulty_code), lower(dl.label));

    UPDATE h_staging.drill_import_rows r
    SET result_type = rt.result_type_code
    FROM h_ref.result_types rt
    WHERE r.import_batch_id = p_import_batch_id
      AND r.deleted_at IS NULL
      AND rt.deleted_at IS NULL
      AND lower(r.result_type) IN (lower(rt.result_type_code), lower(rt.label));

    UPDATE h_staging.drill_import_rows r
    SET symmetry_type = st.symmetry_type_code
    FROM h_ref.symmetry_types st
    WHERE r.import_batch_id = p_import_batch_id
      AND r.deleted_at IS NULL
      AND st.deleted_at IS NULL
      AND lower(r.symmetry_type) IN (lower(st.symmetry_type_code), lower(st.label));

    -- Clean: canonical type names
    UPDATE h_staging.drill_import_rows r
    SET primary_type = dt.type_name
    FROM h_data.drill_types dt
    WHERE r.import_batch_id = p_import_batch_id
      AND r.deleted_at IS NULL
      AND dt.deleted_at IS NULL
      AND lower(r.primary_type) = lower(dt.type_name);

    UPDATE h_staging.drill_import_rows r
    SET secondary_types = (
        SELECT string_agg(COALESCE(dt.type_name, p.part), '; ' ORDER BY p.ord)
        FROM (
            SELECT btrim(u.raw, E' \t\r\n') AS part, u.ord
            FROM unnest(string_to_array(r.secondary_types, ';')) WITH ORDINALITY AS u(raw, ord)
        ) p
        LEFT JOIN h_data.drill_types dt
               ON dt.deleted_at IS NULL
              AND lower(dt.type_name) = lower(p.part)
        WHERE p.part <> '')
    WHERE r.import_batch_id = p_import_batch_id
      AND r.deleted_at IS NULL
      AND r.secondary_types IS NOT NULL;

    -- Clean: spots to "n; n" or "FT"
    UPDATE h_staging.drill_import_rows r
    SET spots = (
        SELECT string_agg(
                   CASE
                       WHEN upper(p.part) = 'FT' THEN 'FT'
                       WHEN p.part ~ '^[0-9]{1,4}$' THEN (p.part::integer)::text
                       ELSE p.part
                   END, '; ' ORDER BY p.ord)
        FROM (
            SELECT btrim(u.raw, E' \t\r\n') AS part, u.ord
            FROM unnest(string_to_array(r.spots, ';')) WITH ORDINALITY AS u(raw, ord)
        ) p
        WHERE p.part <> '')
    WHERE r.import_batch_id = p_import_batch_id
      AND r.deleted_at IS NULL
      AND r.spots IS NOT NULL;

    -- 2. Validate: every error of a row, joined with "; "
    UPDATE h_staging.drill_import_rows r
    SET error_message = e.e_msg,
        row_status = CASE WHEN e.e_msg IS NULL THEN 'valid' ELSE 'error' END
    FROM (
        SELECT x.drill_import_row_id AS e_id,
               NULLIF(array_to_string(ARRAY[
                   CASE WHEN x.drill_name IS NULL THEN 'Name is required' END,
                   CASE WHEN length(x.drill_name) > 100 THEN 'Name is longer than 100 characters' END,
                   CASE WHEN x.drill_name IS NOT NULL AND x.first_row_no < x.row_no
                        THEN 'Name repeats row ' || x.first_row_no END,
                   CASE WHEN x.drill_name IS NOT NULL
                         AND EXISTS (SELECT 1 FROM h_data.drills d
                                     WHERE d.deleted_at IS NULL AND lower(d.drill_name) = lower(x.drill_name))
                        THEN format('The name "%s" belongs to an existing drill; choose a different name', x.drill_name) END,
                   CASE WHEN x.primary_type IS NULL THEN 'Primary drill type is required'
                        WHEN NOT EXISTS (SELECT 1 FROM h_data.drill_types dt
                                         WHERE dt.deleted_at IS NULL AND dt.type_name = x.primary_type)
                        THEN format('Unknown drill type "%s"', x.primary_type) END,
                   (SELECT string_agg(format('Unknown drill type "%s"', s.part), '; ' ORDER BY s.ord)
                    FROM (SELECT btrim(u.raw) AS part, u.ord
                          FROM unnest(string_to_array(x.secondary_types, ';')) WITH ORDINALITY AS u(raw, ord)) s
                    WHERE s.part <> ''
                      AND NOT EXISTS (SELECT 1 FROM h_data.drill_types dt
                                      WHERE dt.deleted_at IS NULL AND dt.type_name = s.part)),
                   (SELECT string_agg(format('Secondary type "%s" is listed more than once', dup.part), '; ')
                    FROM (SELECT min(s.part) AS part
                          FROM (SELECT btrim(u.raw) AS part
                                FROM unnest(string_to_array(x.secondary_types, ';')) AS u(raw)) s
                          WHERE s.part <> ''
                          GROUP BY lower(s.part)
                          HAVING count(*) > 1) dup),
                   (SELECT string_agg(DISTINCT format('Secondary type "%s" is the same as the primary type', s.part), '; ')
                    FROM (SELECT btrim(u.raw) AS part
                          FROM unnest(string_to_array(x.secondary_types, ';')) AS u(raw)) s
                    WHERE x.primary_type IS NOT NULL
                      AND lower(s.part) = lower(x.primary_type)),
                   CASE WHEN x.difficulty IS NULL THEN 'Difficulty is required'
                        WHEN NOT EXISTS (SELECT 1 FROM h_ref.difficulty_levels dl
                                         WHERE dl.deleted_at IS NULL AND dl.difficulty_code = x.difficulty)
                        THEN format('Unknown difficulty "%s"', x.difficulty) END,
                   CASE WHEN x.result_type IS NULL THEN 'Result type is required'
                        WHEN NOT EXISTS (SELECT 1 FROM h_ref.result_types rt
                                         WHERE rt.deleted_at IS NULL AND rt.result_type_code = x.result_type)
                        THEN format('Unknown result type "%s"', x.result_type)
                        WHEN x.result_type IN ('outcome_buttons', 'physical')
                        THEN 'Outcome-button and physical-measurement drills cannot be imported yet; add them in the app' END,
                   CASE WHEN x.symmetry_type IS NOT NULL
                         AND NOT EXISTS (SELECT 1 FROM h_ref.symmetry_types st
                                         WHERE st.deleted_at IS NULL AND st.symmetry_type_code = x.symmetry_type)
                        THEN format('Unknown symmetry type "%s"', x.symmetry_type) END,
                   CASE WHEN x.default_target IS NULL THEN NULL
                        WHEN x.default_target !~ '^[0-9]{1,5}$'
                        THEN 'Default target must be a whole number from 1 to 99999'
                        WHEN x.default_target::integer < 1
                        THEN 'Default target must be a whole number from 1 to 99999' END,
                   CASE WHEN x.fixed_seconds IS NULL THEN
                            CASE WHEN x.result_type = 'fixed_time_reps' THEN 'A fixed-time-for-reps drill needs its fixed time in seconds' END
                        WHEN x.result_type IS DISTINCT FROM 'fixed_time_reps'
                        THEN 'A fixed time is allowed only for fixed-time-for-reps drills'
                        WHEN x.fixed_seconds !~ '^[0-9]{1,5}$'
                        THEN 'Fixed time must be a whole number of seconds from 1 to 99999'
                        WHEN x.fixed_seconds::integer < 1
                        THEN 'Fixed time must be a whole number of seconds from 1 to 99999' END,
                   CASE WHEN x.spots IS NULL THEN
                            CASE WHEN x.result_type = 'shooting' THEN 'A shooting drill needs at least one spot (a spot number or FT)' END
                        WHEN x.result_type IS DISTINCT FROM 'shooting'
                        THEN 'Spots are allowed only for shooting drills' END,
                   (SELECT string_agg(format('Unknown spot "%s"', s.part), '; ' ORDER BY s.ord)
                    FROM (SELECT btrim(u.raw) AS part, u.ord
                          FROM unnest(string_to_array(x.spots, ';')) WITH ORDINALITY AS u(raw, ord)) s
                    WHERE s.part <> ''
                      AND s.part <> 'FT'
                      AND NOT EXISTS (SELECT 1 FROM h_ref.court_spots cs
                                      WHERE cs.deleted_at IS NULL AND cs.is_active
                                        AND cs.spot_number::text = s.part)),
                   CASE WHEN length(x.description) > 1000 THEN 'Description is longer than 1000 characters' END
               ], '; '), '') AS e_msg
        FROM (
            SELECT r2.*,
                   min(r2.row_no) OVER (PARTITION BY lower(r2.drill_name)) AS first_row_no
            FROM h_staging.drill_import_rows r2
            WHERE r2.import_batch_id = p_import_batch_id
              AND r2.deleted_at IS NULL
        ) x
    ) e
    WHERE r.drill_import_row_id = e.e_id;

    -- 3./4. Batch counts and status
    SELECT count(*)::integer,
           (count(*) FILTER (WHERE r.row_status = 'error'))::integer
    INTO v_rows, v_errors
    FROM h_staging.drill_import_rows r
    WHERE r.import_batch_id = p_import_batch_id
      AND r.deleted_at IS NULL;

    UPDATE h_staging.import_batches b
    SET row_count       = v_rows,
        error_row_count = v_errors,
        checked_at      = now(),
        batch_status    = CASE WHEN v_errors = 0 AND v_rows > 0 THEN 'ready' ELSE 'has_errors' END
    WHERE b.import_batch_id = p_import_batch_id;

    RETURN QUERY
    SELECT r.row_no, r.row_status, r.error_message
    FROM h_staging.drill_import_rows r
    WHERE r.import_batch_id = p_import_batch_id
      AND r.deleted_at IS NULL
    ORDER BY r.row_no;
END
$$;
COMMENT ON FUNCTION h_staging.fn_check_drill_batch(bigint) IS
    'REQ-050: cleans and validates a drill import batch (shooting and single-number result types only, Gate 1 Q8); sets row and batch status.';

-- -----------------------------------------------------------------------------
-- h_staging.fn_publish_drill_batch (REQ-050, REQ-027, REQ-080)
-- Description : Publishes a ready drill batch: locks it, re-runs the check in the
--               same transaction (guards against names taken since the preview),
--               then inserts drills (lineage + version 1): h_data.drills, then
--               h_data.drill_versions (result type, symmetry type, default target,
--               fixed seconds), drill_secondary_types, and drill_spots of version 1
--               (spot_order from list order); marks the rows published with
--               published_drill_id and marks the batch published.
-- Parameters  : p_import_batch_id - h_staging.import_batches id.
-- Return Values: integer - number of drills published.
-- Notes       : All or nothing in the caller's transaction. Raises unless the batch
--               is ready; raises 'Batch <id> has <n> rows with errors; nothing was
--               published' when the re-check finds errors (the app then rolls back
--               and re-runs the check to show them). The acting user is required
--               through the audit triggers. version_no is assigned by
--               h_data.tf_version_guard; the spots are inserted in the transaction
--               that creates their version, as the guard requires, and the deferred
--               completeness check (h_data.tf_version_complete) runs at COMMIT.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_staging.fn_publish_drill_batch(p_import_batch_id bigint)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    v_status     text;
    v_rows       integer;
    v_errors     integer;
    v_published  integer;
BEGIN
    SELECT b.batch_status
    INTO v_status
    FROM h_staging.import_batches b
    WHERE b.import_batch_id = p_import_batch_id
      AND b.deleted_at IS NULL
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Import batch % does not exist', p_import_batch_id;
    END IF;
    IF v_status <> 'ready' THEN
        RAISE EXCEPTION 'Import batch % is % and cannot be published; run the check first', p_import_batch_id, v_status;
    END IF;

    PERFORM 1 FROM h_staging.fn_check_drill_batch(p_import_batch_id);

    SELECT count(*)::integer,
           (count(*) FILTER (WHERE r.row_status = 'error'))::integer
    INTO v_rows, v_errors
    FROM h_staging.drill_import_rows r
    WHERE r.import_batch_id = p_import_batch_id
      AND r.deleted_at IS NULL;
    IF v_errors > 0 THEN
        RAISE EXCEPTION 'Batch % has % rows with errors; nothing was published', p_import_batch_id, v_errors;
    END IF;
    IF v_rows = 0 THEN
        RAISE EXCEPTION 'Batch % has no rows; nothing was published', p_import_batch_id;
    END IF;

    WITH ins AS (
        INSERT INTO h_data.drills
            (drill_name, description, primary_drill_type_id, difficulty_code)
        SELECT r.drill_name, r.description, dt.drill_type_id, r.difficulty
        FROM h_staging.drill_import_rows r
        JOIN h_data.drill_types dt
          ON dt.deleted_at IS NULL
         AND dt.type_name = r.primary_type
        WHERE r.import_batch_id = p_import_batch_id
          AND r.deleted_at IS NULL
          AND r.row_status = 'valid'
        RETURNING drill_id AS new_drill_id, drill_name AS new_drill_name
    )
    UPDATE h_staging.drill_import_rows r
    SET published_drill_id = ins.new_drill_id,
        row_status = 'published'
    FROM ins
    WHERE r.import_batch_id = p_import_batch_id
      AND r.deleted_at IS NULL
      AND r.row_status = 'valid'
      AND lower(r.drill_name) = lower(ins.new_drill_name);
    GET DIAGNOSTICS v_published = ROW_COUNT;

    IF v_published <> v_rows THEN
        RAISE EXCEPTION 'Batch % published % of % rows; nothing was published', p_import_batch_id, v_published, v_rows;
    END IF;

    INSERT INTO h_data.drill_versions (drill_id, result_type_code, symmetry_type_code, default_target, fixed_seconds)
    SELECT r.published_drill_id, r.result_type, r.symmetry_type, r.default_target::integer, r.fixed_seconds::integer
    FROM h_staging.drill_import_rows r
    WHERE r.import_batch_id = p_import_batch_id
      AND r.deleted_at IS NULL
      AND r.row_status = 'published';

    INSERT INTO h_data.drill_secondary_types (drill_id, drill_type_id)
    SELECT DISTINCT r.published_drill_id, dt.drill_type_id
    FROM h_staging.drill_import_rows r
    CROSS JOIN LATERAL unnest(string_to_array(r.secondary_types, ';')) AS u(part)
    JOIN h_data.drill_types dt
      ON dt.deleted_at IS NULL
     AND dt.type_name = btrim(u.part)
    WHERE r.import_batch_id = p_import_batch_id
      AND r.deleted_at IS NULL
      AND r.row_status = 'published';

    INSERT INTO h_data.drill_spots (drill_version_id, spot_order, court_spot_id, is_free_throw)
    SELECT dv.drill_version_id, u.ord::smallint, cs.court_spot_id, (btrim(u.part) = 'FT')
    FROM h_staging.drill_import_rows r
    JOIN h_data.drill_versions dv
      ON dv.drill_id = r.published_drill_id
     AND dv.version_no = 1
    CROSS JOIN LATERAL unnest(string_to_array(r.spots, ';')) WITH ORDINALITY AS u(part, ord)
    LEFT JOIN h_ref.court_spots cs
           ON cs.deleted_at IS NULL
          AND cs.is_active
          AND cs.spot_number::text = btrim(u.part)
    WHERE r.import_batch_id = p_import_batch_id
      AND r.deleted_at IS NULL
      AND r.row_status = 'published'
      AND btrim(u.part) <> '';

    UPDATE h_staging.import_batches b
    SET batch_status        = 'published',
        published_at        = now(),
        published_row_count = v_published
    WHERE b.import_batch_id = p_import_batch_id;

    RETURN v_published;
END
$$;
COMMENT ON FUNCTION h_staging.fn_publish_drill_batch(bigint) IS
    'REQ-050: all-or-nothing publish of a ready drill batch into h_data: drills (lineage + version 1), secondary types, spots.';


-- =============================================================================
-- Audit triggers
-- =============================================================================

CREATE TRIGGER trg_import_batches_audit BEFORE INSERT OR UPDATE ON h_staging.import_batches
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_drill_import_rows_audit BEFORE INSERT OR UPDATE ON h_staging.drill_import_rows
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();


-- =============================================================================
-- Grants (COD-1-06, plan section 6.13.3)
-- =============================================================================

GRANT USAGE ON SCHEMA h_staging TO hoopster_app, hoopster_agent_ro;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA h_staging TO hoopster_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA h_staging TO hoopster_app;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA h_staging FROM PUBLIC;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA h_staging TO hoopster_app;

GRANT SELECT ON ALL TABLES IN SCHEMA h_staging TO hoopster_agent_ro;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA h_staging TO hoopster_agent_ro;

ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_staging
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO hoopster_app;
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_staging
    GRANT SELECT ON TABLES TO hoopster_agent_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_staging
    GRANT USAGE, SELECT ON SEQUENCES TO hoopster_app;
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_staging
    GRANT SELECT ON SEQUENCES TO hoopster_agent_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_staging
    GRANT EXECUTE ON FUNCTIONS TO hoopster_app;

COMMIT;

SELECT 'END OF SCRIPT';