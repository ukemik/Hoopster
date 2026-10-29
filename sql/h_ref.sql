-- =============================================================================
-- sql/h_ref.sql  --  schema h_ref
--
-- Purpose : Reference data that screens and data both rely on: court spots, zones,
--           lookup lists (incl. drill result types and symmetry types), heat-map
--           bands, stat definitions and the physical test list (COD-1-06).
--
-- !!! WARNING: running this file DELETES every object and ALL DATA in schema h_ref !!!
-- It starts with DROP SCHEMA h_ref CASCADE, which also removes objects of other schemas
-- that depend on it (h_data's foreign keys to the lookups and h_analytics views). Run
-- the five schema files together, in order: h_system, h_ref, h_data, h_analytics,
-- h_staging (COD-21-06). While real accounts exist it stops unless the session sets
-- hoopster.allow_wipe = 'yes' (PGOPTIONS=-c hoopster.allow_wipe=yes).
-- Never run it on prod without a fresh backup.
--
-- Requires: sql/_database.sql, sql/_roles.sql, sql/h_system.sql.
-- Run order (COD-21-05): _database, _roles, h_system, h_ref (this file), h_data,
--           h_analytics, h_staging.
-- Run as  : psql -v ON_ERROR_STOP=1 -d hoopster_db -f sql/h_ref.sql
-- Re-runnable: drops and rebuilds this schema in one transaction; run the five
--           schema files together, in order (§ run-order table); see COD-21-06.
--
-- Lookup tables use a readable text code as primary key. Small fixed value sets the
-- code branches on (hand, entry mode, statuses...) are CHECK constraints in h_data,
-- not tables. Seeds are inserted as acting user 0.
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

DROP SCHEMA IF EXISTS h_ref CASCADE;

CREATE SCHEMA h_ref;
COMMENT ON SCHEMA h_ref IS
    'Reference data: court spots, zones, lookup lists, heat-map bands, stat definitions, physical tests.';


-- =============================================================================
-- Lookup tables (code PK, label, sort_order, audit block)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- h_ref.zones (REQ-016, REQ-019)
-- -----------------------------------------------------------------------------
CREATE TABLE h_ref.zones (
    zone_code    text        NOT NULL,
    label        text        NOT NULL,
    sort_order   smallint    NOT NULL,
    point_value  smallint    NOT NULL,
    created_by   bigint      NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   bigint      NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    deleted_at   timestamptz,
    is_deleted   boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_zones PRIMARY KEY (zone_code),
    CONSTRAINT ck_zones_code CHECK (zone_code ~ '^[a-z0-9_]+$' AND length(zone_code) <= 50),
    CONSTRAINT ck_zones_label CHECK (btrim(label) <> '' AND length(label) <= 100),
    CONSTRAINT ck_zones_point_value CHECK (point_value IN (2, 3)),
    CONSTRAINT fk_zones_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_zones_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_ref.zones IS
    'Court zones (paint, mid-range, three). point_value gives a spot shot its 2 or 3 points (REQ-016, REQ-019).';
CREATE UNIQUE INDEX ux_zones_label ON h_ref.zones (label) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_ref.shot_types (REQ-013)
-- -----------------------------------------------------------------------------
CREATE TABLE h_ref.shot_types (
    shot_type_code  text        NOT NULL,
    label           text        NOT NULL,
    sort_order      smallint    NOT NULL,
    created_by      bigint      NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_by      bigint      NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz,
    is_deleted      boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_shot_types PRIMARY KEY (shot_type_code),
    CONSTRAINT ck_shot_types_code CHECK (shot_type_code ~ '^[a-z0-9_]+$' AND length(shot_type_code) <= 50),
    CONSTRAINT ck_shot_types_label CHECK (btrim(label) <> '' AND length(label) <= 100),
    CONSTRAINT fk_shot_types_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_shot_types_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_ref.shot_types IS 'Optional shot-type detail of a shot entry (REQ-013).';
CREATE UNIQUE INDEX ux_shot_types_label ON h_ref.shot_types (label) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_ref.game_categories (REQ-018)
-- -----------------------------------------------------------------------------
CREATE TABLE h_ref.game_categories (
    game_category_code  text        NOT NULL,
    label               text        NOT NULL,
    sort_order          smallint    NOT NULL,
    created_by          bigint      NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_by          bigint      NOT NULL,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,
    is_deleted          boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_game_categories PRIMARY KEY (game_category_code),
    CONSTRAINT ck_game_categories_code CHECK (game_category_code ~ '^[a-z0-9_]+$' AND length(game_category_code) <= 50),
    CONSTRAINT ck_game_categories_label CHECK (btrim(label) <> '' AND length(label) <= 100),
    CONSTRAINT fk_game_categories_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_game_categories_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_ref.game_categories IS
    'Game categories (REQ-018). Shots in a practice game count as game shots (REQ-015).';
CREATE UNIQUE INDEX ux_game_categories_label ON h_ref.game_categories (label) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_ref.game_formats (REQ-018)
-- -----------------------------------------------------------------------------
CREATE TABLE h_ref.game_formats (
    game_format_code  text        NOT NULL,
    label             text        NOT NULL,
    sort_order        smallint    NOT NULL,
    created_by        bigint      NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_by        bigint      NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    deleted_at        timestamptz,
    is_deleted        boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_game_formats PRIMARY KEY (game_format_code),
    CONSTRAINT ck_game_formats_code CHECK (game_format_code ~ '^[a-z0-9_]+$' AND length(game_format_code) <= 50),
    CONSTRAINT ck_game_formats_label CHECK (btrim(label) <> '' AND length(label) <= 100),
    CONSTRAINT fk_game_formats_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_game_formats_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_ref.game_formats IS 'Game formats: 5v5, 3v3, 2v2, 1v1 (REQ-018).';
CREATE UNIQUE INDEX ux_game_formats_label ON h_ref.game_formats (label) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_ref.practice_types (REQ-025)
-- -----------------------------------------------------------------------------
CREATE TABLE h_ref.practice_types (
    practice_type_code  text        NOT NULL,
    label               text        NOT NULL,
    sort_order          smallint    NOT NULL,
    created_by          bigint      NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_by          bigint      NOT NULL,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,
    is_deleted          boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_practice_types PRIMARY KEY (practice_type_code),
    CONSTRAINT ck_practice_types_code CHECK (practice_type_code ~ '^[a-z0-9_]+$' AND length(practice_type_code) <= 50),
    CONSTRAINT ck_practice_types_label CHECK (btrim(label) <> '' AND length(label) <= 100),
    CONSTRAINT fk_practice_types_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_practice_types_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_ref.practice_types IS 'Practice session types (REQ-025).';
CREATE UNIQUE INDEX ux_practice_types_label ON h_ref.practice_types (label) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_ref.difficulty_levels (REQ-028)
-- -----------------------------------------------------------------------------
CREATE TABLE h_ref.difficulty_levels (
    difficulty_code  text        NOT NULL,
    label            text        NOT NULL,
    sort_order       smallint    NOT NULL,
    level_rank       smallint    NOT NULL,
    created_by       bigint      NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_by       bigint      NOT NULL,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    deleted_at       timestamptz,
    is_deleted       boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_difficulty_levels PRIMARY KEY (difficulty_code),
    CONSTRAINT ck_difficulty_levels_code CHECK (difficulty_code ~ '^[a-z0-9_]+$' AND length(difficulty_code) <= 50),
    CONSTRAINT ck_difficulty_levels_label CHECK (btrim(label) <> '' AND length(label) <= 100),
    CONSTRAINT ux_difficulty_levels_rank UNIQUE (level_rank),
    CONSTRAINT fk_difficulty_levels_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_difficulty_levels_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_ref.difficulty_levels IS 'Drill difficulty: Beginner, Intermediate, Advanced, Elite (REQ-028).';
CREATE UNIQUE INDEX ux_difficulty_levels_label ON h_ref.difficulty_levels (label) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_ref.result_types (REQ-076; replaces the measurement types of REQ-030)
-- -----------------------------------------------------------------------------
CREATE TABLE h_ref.result_types (
    result_type_code     text        NOT NULL,
    label                text        NOT NULL,
    sort_order           smallint    NOT NULL,
    capture_kind         text        NOT NULL,
    value_count          smallint    NOT NULL,
    value_unit           text,
    higher_is_better     boolean,
    needs_fixed_seconds  boolean     NOT NULL DEFAULT false,
    description          text        NOT NULL,
    created_by           bigint      NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_by           bigint      NOT NULL,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    deleted_at           timestamptz,
    is_deleted           boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_result_types PRIMARY KEY (result_type_code),
    CONSTRAINT ck_result_types_code CHECK (result_type_code ~ '^[a-z0-9_]+$' AND length(result_type_code) <= 50),
    CONSTRAINT ck_result_types_label CHECK (btrim(label) <> '' AND length(label) <= 100),
    CONSTRAINT ck_result_types_capture CHECK (capture_kind IN ('shots', 'outcomes', 'values')),
    CONSTRAINT ck_result_types_value_count CHECK (
        (capture_kind = 'values' AND value_count BETWEEN 1 AND 2) OR (capture_kind <> 'values' AND value_count = 0)),
    CONSTRAINT ck_result_types_unit CHECK (value_unit IS NULL OR (capture_kind = 'values' AND value_unit IN ('s', 'count'))),
    CONSTRAINT ck_result_types_fixed_seconds CHECK (NOT needs_fixed_seconds OR capture_kind = 'values'),
    CONSTRAINT ck_result_types_description CHECK (btrim(description) <> '' AND length(description) <= 1000),
    CONSTRAINT fk_result_types_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_result_types_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_ref.result_types IS
    'Drill result types (REQ-076). capture_kind: shots (linked shot entries), outcomes (outcome buttons) or values (value_count numbers per attempt). value_unit / higher_is_better NULL = set per number on the drill version (physical measurement). needs_fixed_seconds = the drill version sets a fixed time.';
CREATE UNIQUE INDEX ux_result_types_label ON h_ref.result_types (label) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_ref.symmetry_types (REQ-079)
-- -----------------------------------------------------------------------------
CREATE TABLE h_ref.symmetry_types (
    symmetry_type_code  text        NOT NULL,
    label               text        NOT NULL,
    sort_order          smallint    NOT NULL,
    left_label          text        NOT NULL,
    right_label         text        NOT NULL,
    created_by          bigint      NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_by          bigint      NOT NULL,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,
    is_deleted          boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_symmetry_types PRIMARY KEY (symmetry_type_code),
    CONSTRAINT ck_symmetry_types_code CHECK (symmetry_type_code ~ '^[a-z0-9_]+$' AND length(symmetry_type_code) <= 50),
    CONSTRAINT ck_symmetry_types_label CHECK (btrim(label) <> '' AND length(label) <= 100),
    CONSTRAINT ck_symmetry_types_sides CHECK (
        btrim(left_label) <> '' AND length(left_label) <= 50 AND btrim(right_label) <> '' AND length(right_label) <= 50),
    CONSTRAINT fk_symmetry_types_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_symmetry_types_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_ref.symmetry_types IS
    'Symmetry types (REQ-079): a drill has at most one; each attempt then records left or right. For shooting drills with court_side the side comes from each shot''s spot.';
CREATE UNIQUE INDEX ux_symmetry_types_label ON h_ref.symmetry_types (label) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_ref.stat_definitions (REQ-024, REQ-033, REQ-064)
-- -----------------------------------------------------------------------------
CREATE TABLE h_ref.stat_definitions (
    stat_code         text        NOT NULL,
    label             text        NOT NULL,
    sort_order        smallint    NOT NULL,
    abbreviation      text        NOT NULL,
    unit              text        NOT NULL,
    higher_is_better  boolean     NOT NULL,
    applies_to        text        NOT NULL,
    is_goal_eligible  boolean     NOT NULL,
    formula_text      text,
    explanation       text        NOT NULL,
    created_by        bigint      NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_by        bigint      NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    deleted_at        timestamptz,
    is_deleted        boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_stat_definitions PRIMARY KEY (stat_code),
    CONSTRAINT ck_stat_definitions_code CHECK (stat_code ~ '^[a-z0-9_]+$' AND length(stat_code) <= 50),
    CONSTRAINT ck_stat_definitions_label CHECK (btrim(label) <> '' AND length(label) <= 100),
    CONSTRAINT ck_stat_definitions_abbreviation CHECK (btrim(abbreviation) <> '' AND length(abbreviation) <= 20),
    CONSTRAINT ck_stat_definitions_unit CHECK (unit IN ('percent', 'ratio', 'count')),
    CONSTRAINT ck_stat_definitions_applies_to CHECK (applies_to IN ('game', 'both')),
    CONSTRAINT ck_stat_definitions_formula CHECK (formula_text IS NULL OR length(formula_text) <= 200),
    CONSTRAINT ck_stat_definitions_explanation CHECK (btrim(explanation) <> '' AND length(explanation) <= 4000),
    CONSTRAINT fk_stat_definitions_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_stat_definitions_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_ref.stat_definitions IS
    'Every calculated and counted stat: formula, plain-language explanation (REQ-064), whether goals may target it (REQ-033). applies_to = game means it has no practice value.';
CREATE UNIQUE INDEX ux_stat_definitions_label ON h_ref.stat_definitions (label) WHERE deleted_at IS NULL;


-- =============================================================================
-- Surrogate-key reference tables
-- =============================================================================

-- -----------------------------------------------------------------------------
-- h_ref.court_spots (REQ-007, REQ-008)
-- -----------------------------------------------------------------------------
CREATE TABLE h_ref.court_spots (
    court_spot_id  bigint        GENERATED BY DEFAULT AS IDENTITY,
    spot_number    smallint      NOT NULL,
    spot_name      text          NOT NULL,
    zone_code      text          NOT NULL,
    x_ft           numeric(5,2)  NOT NULL,
    y_ft           numeric(5,2)  NOT NULL,
    is_active      boolean       NOT NULL DEFAULT true,
    created_by     bigint        NOT NULL,
    created_at     timestamptz   NOT NULL DEFAULT now(),
    updated_by     bigint        NOT NULL,
    updated_at     timestamptz   NOT NULL DEFAULT now(),
    deleted_at     timestamptz,
    is_deleted     boolean       GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_court_spots PRIMARY KEY (court_spot_id),
    CONSTRAINT ck_court_spots_number CHECK (spot_number BETWEEN 1 AND 99),
    CONSTRAINT ck_court_spots_name CHECK (btrim(spot_name) <> '' AND length(spot_name) <= 100),
    CONSTRAINT ck_court_spots_x CHECK (x_ft BETWEEN -25 AND 25),
    CONSTRAINT ck_court_spots_y CHECK (y_ft BETWEEN 0 AND 42),
    CONSTRAINT fk_court_spots_zone FOREIGN KEY (zone_code) REFERENCES h_ref.zones (zone_code),
    CONSTRAINT fk_court_spots_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_court_spots_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_ref.court_spots IS
    'The 25 court spots (REQ-007, REQ-008, Appendix A). Feet; origin = baseline midpoint under the basket; x right as seen on screen (-25..25), y into the court (0..42). Reference spots by court_spot_id, never by spot_number.';
CREATE UNIQUE INDEX ux_court_spots_number ON h_ref.court_spots (spot_number) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX ux_court_spots_name ON h_ref.court_spots (lower(spot_name)) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_ref.heat_map_bands (REQ-038)
-- -----------------------------------------------------------------------------
CREATE TABLE h_ref.heat_map_bands (
    heat_map_band_id  bigint        GENERATED BY DEFAULT AS IDENTITY,
    zone_code         text          NOT NULL,
    band_rank         smallint      NOT NULL,
    band_label        text          NOT NULL,
    min_pct           numeric(5,2)  NOT NULL,
    max_pct           numeric(5,2),
    created_by        bigint        NOT NULL,
    created_at        timestamptz   NOT NULL DEFAULT now(),
    updated_by        bigint        NOT NULL,
    updated_at        timestamptz   NOT NULL DEFAULT now(),
    deleted_at        timestamptz,
    is_deleted        boolean       GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_heat_map_bands PRIMARY KEY (heat_map_band_id),
    CONSTRAINT ck_heat_map_bands_rank CHECK (band_rank BETWEEN 1 AND 5),
    CONSTRAINT ck_heat_map_bands_label CHECK (btrim(band_label) <> '' AND length(band_label) <= 100),
    CONSTRAINT ck_heat_map_bands_min CHECK (min_pct BETWEEN 0 AND 100),
    CONSTRAINT ck_heat_map_bands_range CHECK (max_pct IS NULL OR max_pct > min_pct),
    CONSTRAINT fk_heat_map_bands_zone FOREIGN KEY (zone_code) REFERENCES h_ref.zones (zone_code),
    CONSTRAINT fk_heat_map_bands_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_heat_map_bands_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_ref.heat_map_bands IS
    'Fixed make-percentage bands per zone for heat-map colours (REQ-038). min_pct inclusive, max_pct exclusive, NULL max = open-ended. The app maps band_rank to a colour.';
CREATE UNIQUE INDEX ux_heat_map_bands_zone_rank ON h_ref.heat_map_bands (zone_code, band_rank) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_ref.physical_tests (REQ-034) - editable list
-- -----------------------------------------------------------------------------
CREATE TABLE h_ref.physical_tests (
    physical_test_id  bigint      GENERATED BY DEFAULT AS IDENTITY,
    test_name         text        NOT NULL,
    unit              text        NOT NULL,
    lower_is_better   boolean     NOT NULL DEFAULT false,
    sort_order        smallint    NOT NULL,
    description       text,
    created_by        bigint      NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_by        bigint      NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    deleted_at        timestamptz,
    is_deleted        boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_physical_tests PRIMARY KEY (physical_test_id),
    CONSTRAINT ck_physical_tests_name CHECK (btrim(test_name) <> '' AND length(test_name) <= 100),
    CONSTRAINT ck_physical_tests_unit CHECK (unit IN ('in', 'lb', 's')),
    CONSTRAINT ck_physical_tests_description CHECK (description IS NULL OR length(description) <= 4000),
    CONSTRAINT fk_physical_tests_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_physical_tests_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_ref.physical_tests IS
    'Editable list of physical tests (REQ-034); units in, lb, s. A deleted test still used by measurements stays hidden, not purged.';
CREATE UNIQUE INDEX ux_physical_tests_name ON h_ref.physical_tests (lower(test_name)) WHERE deleted_at IS NULL;


-- =============================================================================
-- Audit triggers
-- =============================================================================

CREATE TRIGGER trg_zones_audit BEFORE INSERT OR UPDATE ON h_ref.zones
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_shot_types_audit BEFORE INSERT OR UPDATE ON h_ref.shot_types
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_game_categories_audit BEFORE INSERT OR UPDATE ON h_ref.game_categories
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_game_formats_audit BEFORE INSERT OR UPDATE ON h_ref.game_formats
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_practice_types_audit BEFORE INSERT OR UPDATE ON h_ref.practice_types
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_difficulty_levels_audit BEFORE INSERT OR UPDATE ON h_ref.difficulty_levels
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_result_types_audit BEFORE INSERT OR UPDATE ON h_ref.result_types
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_symmetry_types_audit BEFORE INSERT OR UPDATE ON h_ref.symmetry_types
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_stat_definitions_audit BEFORE INSERT OR UPDATE ON h_ref.stat_definitions
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_court_spots_audit BEFORE INSERT OR UPDATE ON h_ref.court_spots
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_heat_map_bands_audit BEFORE INSERT OR UPDATE ON h_ref.heat_map_bands
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_physical_tests_audit BEFORE INSERT OR UPDATE ON h_ref.physical_tests
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();


-- =============================================================================
-- Seed data (plan section 6.14). Acting user 0. Other rows referenced by code.
-- =============================================================================

-- 6.14.2 Lookups
INSERT INTO h_ref.zones (zone_code, label, sort_order, point_value) VALUES
    ('paint',     'Paint',       1, 2),
    ('mid_range', 'Mid-range',   2, 2),
    ('three',     'Three-point', 3, 3);

INSERT INTO h_ref.shot_types (shot_type_code, label, sort_order) VALUES
    ('layup',   'Layup',   1),
    ('jumper',  'Jumper',  2),
    ('floater', 'Floater', 3),
    ('hook',    'Hook',    4),
    ('dunk',    'Dunk',    5),
    ('tip_in',  'Tip-in',  6);

INSERT INTO h_ref.game_categories (game_category_code, label, sort_order) VALUES
    ('club_aau',      'Club/AAU',      1),
    ('rec_league',    'Rec league',    2),
    ('practice_game', 'Practice game', 3);

INSERT INTO h_ref.game_formats (game_format_code, label, sort_order) VALUES
    ('5v5', '5v5', 1),
    ('3v3', '3v3', 2),
    ('2v2', '2v2', 3),
    ('1v1', '1v1', 4);

INSERT INTO h_ref.practice_types (practice_type_code, label, sort_order) VALUES
    ('team_practice',         'Team practice',            1),
    ('individual_workout',    'Individual workout',       2),
    ('strength_conditioning', 'Strength and conditioning', 3),
    ('camp_clinic',           'Camp/clinic',              4);

INSERT INTO h_ref.difficulty_levels (difficulty_code, label, sort_order, level_rank) VALUES
    ('beginner',     'Beginner',     1, 1),
    ('intermediate', 'Intermediate', 2, 2),
    ('advanced',     'Advanced',     3, 3),
    ('elite',        'Elite',        4, 4);

INSERT INTO h_ref.result_types
    (result_type_code, label, sort_order, capture_kind, value_count, value_unit, higher_is_better, needs_fixed_seconds, description)
VALUES
    ('shooting',        'Shooting',               1, 'shots',    0, NULL,    true,  false, 'Make or miss at court spots or the free-throw line; results are makes of attempts. Free throws only for drills with a free-throw-line spot.'),
    ('outcome_buttons', 'Outcome buttons',        2, 'outcomes', 0, NULL,    true,  false, 'One tap per outcome group records one rep; buttons marked success give a clean-rep percentage per group.'),
    ('fixed_task_time', 'Fixed task for time',    3, 'values',   1, 's',     false, false, 'Seconds to complete a fixed task; lower is better.'),
    ('fixed_time_reps', 'Fixed time for reps',    4, 'values',   1, 'count', true,  true,  'Count within the drill''s fixed time; the rate per minute is derived.'),
    ('failure_streak',  'To failure: max streak', 5, 'values',   1, 'count', true,  false, 'Count reached before failure; higher is better.'),
    ('failure_time',    'To failure: time',       6, 'values',   1, 's',     true,  false, 'Seconds held before failure; higher is better.'),
    ('physical',        'Physical measurement',   7, 'values',   2, NULL,    NULL,  false, 'Up to two named numbers per attempt, each with a unit and a better direction set on the drill.'),
    ('reaction_time',   'Reaction time',          8, 'values',   1, 's',     false, false, 'Seconds to react; lower is better.');

INSERT INTO h_ref.symmetry_types (symmetry_type_code, label, sort_order, left_label, right_label) VALUES
    ('hand',       'Hand',                        1, 'Left hand',          'Right hand'),
    ('foot',       'Takeoff/pivot foot',          2, 'Left foot',          'Right foot'),
    ('direction',  'Direction of movement',       3, 'Attacking left',     'Attacking right'),
    ('shuffle',    'Defensive shuffle direction', 4, 'Shuffle left',       'Shuffle right'),
    ('court_side', 'Court side',                  5, 'Left side of floor', 'Right side of floor'),
    ('rotation',   'Rotation',                    6, 'Over left shoulder', 'Over right shoulder');

-- 6.14.4 Stat definitions
INSERT INTO h_ref.stat_definitions
    (stat_code, label, sort_order, abbreviation, unit, higher_is_better, applies_to, is_goal_eligible, formula_text, explanation)
VALUES
    ('fg_pct',         'Field goal %',              1, 'FG%',    'percent', true,  'both', true,
        'FGM ÷ FGA',
        'Share of your two- and three-point shots that went in (free throws not included).'),
    ('three_pct',      'Three-point %',             2, '3P%',    'percent', true,  'both', true,
        '3PM ÷ 3PA',
        'Share of your three-point shots that went in.'),
    ('ft_pct',         'Free throw %',              3, 'FT%',    'percent', true,  'both', true,
        'FTM ÷ FTA',
        'Share of your free throws that went in.'),
    ('efg_pct',        'Effective field goal %',    4, 'eFG%',   'percent', true,  'both', true,
        '(FGM + 0.5 × 3PM) ÷ FGA',
        'Like FG%, but a made three counts extra because it is worth 50% more than a two.'),
    ('ts_pct',         'True shooting %',           5, 'TS%',    'percent', true,  'both', true,
        'PTS ÷ (2 × (FGA + 0.44 × FTA))',
        'Overall scoring efficiency including free throws; 0.44 estimates how many free throws use up a possession.'),
    ('ast_to',         'Assist-to-turnover ratio',  6, 'AST/TO', 'ratio',   true,  'game', true,
        'AST ÷ TOV',
        'Assists for every turnover; shown as a dash when there are no turnovers.'),
    ('points',         'Points',                    7, 'PTS',    'count',   true,  'game', true,
        '2 × two-point makes + 3 × three-point makes + FTM',
        'Total points scored.'),
    ('fgm',            'Field goals made',          8, 'FGM',    'count',   true,  'both', false,
        NULL,
        'Two- and three-point shots made.'),
    ('fga',            'Field goals attempted',     9, 'FGA',    'count',   true,  'both', false,
        NULL,
        'Two- and three-point shots attempted.'),
    ('three_pm',       'Threes made',              10, '3PM',    'count',   true,  'both', false,
        NULL,
        'Three-point shots made.'),
    ('three_pa',       'Threes attempted',         11, '3PA',    'count',   true,  'both', false,
        NULL,
        'Three-point shots attempted.'),
    ('ftm',            'Free throws made',         12, 'FTM',    'count',   true,  'both', false,
        NULL,
        'Free throws made.'),
    ('fta',            'Free throws attempted',    13, 'FTA',    'count',   true,  'both', false,
        NULL,
        'Free throws attempted.'),
    ('off_rebounds',   'Offensive rebounds',       14, 'OREB',   'count',   true,  'game', true,
        NULL,
        'Rebounds grabbed after your own team''s miss.'),
    ('def_rebounds',   'Defensive rebounds',       15, 'DREB',   'count',   true,  'game', true,
        NULL,
        'Rebounds grabbed after the other team''s miss.'),
    ('total_rebounds', 'Total rebounds',           16, 'REB',    'count',   true,  'game', true,
        'OREB + DREB',
        'All rebounds grabbed, offensive and defensive.'),
    ('assists',        'Assists',                  17, 'AST',    'count',   true,  'game', true,
        NULL,
        'Passes that led straight to a teammate''s basket.'),
    ('steals',         'Steals',                   18, 'STL',    'count',   true,  'game', true,
        NULL,
        'Times you took the ball away from the other team.'),
    ('blocks',         'Blocks',                   19, 'BLK',    'count',   true,  'game', true,
        NULL,
        'Opponent shots you blocked.'),
    ('turnovers',      'Turnovers',                20, 'TOV',    'count',   false, 'game', true,
        NULL,
        'Times you lost the ball to the other team.'),
    ('fouls',          'Personal fouls',           21, 'PF',     'count',   false, 'game', false,
        NULL,
        'Personal fouls committed.'),
    ('minutes',        'Minutes played',           22, 'MIN',    'count',   true,  'game', false,
        NULL,
        'Minutes played.');

-- 6.14.1 Court spots (NFHS geometry; zone by code)
INSERT INTO h_ref.court_spots (spot_number, spot_name, zone_code, x_ft, y_ft) VALUES
    ( 1, 'At the rim',               'paint',       0.00,  7.00),
    ( 2, 'Left block',               'paint',      -4.50,  7.50),
    ( 3, 'Right block',              'paint',       4.50,  7.50),
    ( 4, 'Middle paint (floater)',   'paint',       0.00, 13.00),
    ( 5, 'Left baseline, long two',  'mid_range', -17.00,  3.00),
    ( 6, 'Left baseline, short',     'mid_range', -10.50,  3.00),
    ( 7, 'Right baseline, short',    'mid_range',  10.50,  3.00),
    ( 8, 'Right baseline, long two', 'mid_range',  17.00,  3.00),
    ( 9, 'Left wing, mid-range',     'mid_range',  -8.50, 13.70),
    (10, 'Left elbow',               'mid_range',  -6.50, 19.50),
    (11, 'Right elbow',              'mid_range',   6.50, 19.50),
    (12, 'Right wing, mid-range',    'mid_range',   8.50, 13.70),
    (13, 'Left wing, long two',      'mid_range', -12.40, 17.60),
    (14, 'Top of the key',           'mid_range',   0.00, 23.00),
    (15, 'Right wing, long two',     'mid_range',  12.40, 17.60),
    (16, 'Left corner three',        'three',     -22.00,  3.00),
    (17, 'Left short wing three',    'three',     -20.40, 12.70),
    (18, 'Left wing three',          'three',     -15.40, 20.60),
    (19, 'Left top three',           'three',      -8.30, 25.30),
    (20, 'Top three',                'three',       0.00, 27.00),
    (21, 'Right top three',          'three',       8.30, 25.30),
    (22, 'Right wing three',         'three',      15.40, 20.60),
    (23, 'Right short wing three',   'three',      20.40, 12.70),
    (24, 'Right corner three',       'three',      22.00,  3.00),
    (25, 'Deep three',               'three',       0.00, 32.00);

-- 6.14.3 Heat-map bands (lower bound inclusive, upper bound exclusive)
INSERT INTO h_ref.heat_map_bands (zone_code, band_rank, band_label, min_pct, max_pct) VALUES
    ('paint',     1, 'Cold',     0, 45),
    ('paint',     2, 'Cool',    45, 55),
    ('paint',     3, 'Average', 55, 65),
    ('paint',     4, 'Warm',    65, 75),
    ('paint',     5, 'Hot',     75, NULL),
    ('mid_range', 1, 'Cold',     0, 28),
    ('mid_range', 2, 'Cool',    28, 35),
    ('mid_range', 3, 'Average', 35, 42),
    ('mid_range', 4, 'Warm',    42, 50),
    ('mid_range', 5, 'Hot',     50, NULL),
    ('three',     1, 'Cold',     0, 20),
    ('three',     2, 'Cool',    20, 28),
    ('three',     3, 'Average', 28, 34),
    ('three',     4, 'Warm',    34, 40),
    ('three',     5, 'Hot',     40, NULL);

-- 6.14.5 Physical tests
INSERT INTO h_ref.physical_tests (test_name, unit, lower_is_better, sort_order) VALUES
    ('Height',            'in', false, 1),
    ('Weight',            'lb', false, 2),
    ('Standing reach',    'in', false, 3),
    ('Vertical jump',     'in', false, 4),
    ('Max touch height',  'in', false, 5),
    ('3/4-court sprint',  's',  true,  6),
    ('Lane agility',      's',  true,  7);

-- Seed self-check: fail the whole file if a count is off.
DO $$
BEGIN
    IF (SELECT count(*) FROM h_ref.court_spots) <> 25
       OR (SELECT count(*) FROM h_ref.court_spots WHERE zone_code = 'paint') <> 4
       OR (SELECT count(*) FROM h_ref.court_spots WHERE zone_code = 'mid_range') <> 11
       OR (SELECT count(*) FROM h_ref.court_spots WHERE zone_code = 'three') <> 10 THEN
        RAISE EXCEPTION 'h_ref seed check failed: court spots';
    END IF;
    IF (SELECT count(*) FROM h_ref.heat_map_bands) <> 15 THEN
        RAISE EXCEPTION 'h_ref seed check failed: heat-map bands';
    END IF;
    IF (SELECT count(*) FROM h_ref.stat_definitions) <> 22 THEN
        RAISE EXCEPTION 'h_ref seed check failed: stat definitions';
    END IF;
    IF (SELECT count(*) FROM h_ref.physical_tests) <> 7 THEN
        RAISE EXCEPTION 'h_ref seed check failed: physical tests';
    END IF;
    IF (SELECT count(*) FROM h_ref.result_types) <> 8
       OR (SELECT count(*) FROM h_ref.symmetry_types) <> 6 THEN
        RAISE EXCEPTION 'h_ref seed check failed: result or symmetry types';
    END IF;
END
$$;


-- =============================================================================
-- Grants (COD-1-06, plan section 6.13.3)
-- =============================================================================

GRANT USAGE ON SCHEMA h_ref TO hoopster_app, hoopster_agent_ro;

GRANT SELECT ON ALL TABLES IN SCHEMA h_ref TO hoopster_app;
GRANT INSERT, UPDATE, DELETE ON h_ref.physical_tests TO hoopster_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA h_ref TO hoopster_app;

GRANT SELECT ON ALL TABLES IN SCHEMA h_ref TO hoopster_agent_ro;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA h_ref TO hoopster_agent_ro;

REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA h_ref FROM PUBLIC;

ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_ref
    GRANT SELECT ON TABLES TO hoopster_app;
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_ref
    GRANT SELECT ON TABLES TO hoopster_agent_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_ref
    GRANT USAGE, SELECT ON SEQUENCES TO hoopster_app;
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_ref
    GRANT SELECT ON SEQUENCES TO hoopster_agent_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_ref
    GRANT EXECUTE ON FUNCTIONS TO hoopster_app;

COMMIT;

SELECT 'END OF SCRIPT';