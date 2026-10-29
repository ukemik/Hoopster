-- =============================================================================
-- sql/h_data.sql  --  schema h_data
--
-- Purpose : The data the application collects: player profiles, seasons, games,
--           practice sessions and their players, shot and free-throw entries,
--           drills and their versions (spots, outcome groups and outcomes), drill
--           types and links, drill sets and their versions, practice templates and
--           their versions, practice drills, drill results and attempts, goals and
--           physical measurements (COD-1-06). Also the cross-row triggers, the
--           version guard, the draw, placement, participant, library-removal,
--           soft-delete and restore functions, the selection views, the Recently
--           deleted view and the drill catalogue seed.
--
-- !!! WARNING: running this file DELETES every object and ALL DATA in schema h_data !!!
-- It starts with DROP SCHEMA h_data CASCADE, which also removes objects of other
-- schemas that depend on it (every h_analytics view). Run the five schema files
-- together, in order: h_system, h_ref, h_data, h_analytics, h_staging (COD-21-06).
-- While real accounts exist it stops unless the session sets
-- hoopster.allow_wipe = 'yes' (PGOPTIONS=-c hoopster.allow_wipe=yes).
-- Never run it on prod without a fresh backup.
--
-- Requires: sql/_database.sql, sql/_roles.sql, sql/h_system.sql, sql/h_ref.sql.
-- Run order (COD-21-05): _database, _roles, h_system, h_ref, h_data (this file),
--           h_analytics, h_staging.
-- Run as  : psql -v ON_ERROR_STOP=1 -d hoopster_db -f sql/h_data.sql
-- Re-runnable: drops and rebuilds this schema in one transaction; run the five
--           schema files together, in order (§ run-order table); see COD-21-06.
--
-- Identifier rules (plan Context Adaptation Table, COD-8-01):
--   player_id      = h_data.players.player_id, never the acting user id.
--   court_spot_id  = h_ref.court_spots.court_spot_id, never spot_number.
--   An entry belongs to exactly one of game_id / practice_session_id.
--   A practice has no player: its players are h_data.practice_players rows.
--   Every practice record (practice entry, result, attempt) carries
--   practice_player_id; its player_id and practice_session_id always equal
--   that row's (three-column foreign key, COD-22-01).
--   A removed participant row is never restored; re-adding the player creates
--   a new row (COD-22-02).
--   player_id on an entry, result or attempt = the player who did it.
--   A game entry's player_id always equals its game's (two-column foreign key
--   on ux_games_player, COD-22-01).
--   Lineage ids (drill_id, drill_set_id, practice_template_id) vs version ids
--   (*_version_id) are never interchanged.
--   No live row ever has a deleted parent, direct or indirect (COD-22-03):
--   deletes cascade one deleted_at, inserts under a deleted parent are
--   refused, restores go parent first.
--   Versions and their contents are hidden and restored only with their
--   lineage (drill, drill set, practice template), never on their own.
--   Removing a player profile cascades its deleted_at to everything of that
--   player, also in shared practices; restoring the profile brings it back.
-- All foreign keys are ON DELETE NO ACTION: nothing cascades physically.
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

DROP SCHEMA IF EXISTS h_data CASCADE;

CREATE SCHEMA h_data;
COMMENT ON SCHEMA h_data IS
    'Collected data: players, seasons, games, practices and their players, entries, drills, drill sets and practice templates (with versions), results and attempts, goals, measurements.';


-- =============================================================================
-- Part A: tables, constraints, indexes
-- =============================================================================

-- -----------------------------------------------------------------------------
-- h_data.players (REQ-042, REQ-073)
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.players (
    player_id      bigint      GENERATED BY DEFAULT AS IDENTITY,
    display_name   text        NOT NULL,
    birth_date     date,
    position       text,
    dominant_hand  text,
    created_by     bigint      NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_by     bigint      NOT NULL,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    deleted_at     timestamptz,
    is_deleted     boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_players PRIMARY KEY (player_id),
    CONSTRAINT ck_players_display_name CHECK (btrim(display_name) <> '' AND length(display_name) <= 100),
    CONSTRAINT ck_players_position CHECK (position IS NULL OR length(position) <= 50),
    CONSTRAINT ck_players_dominant_hand CHECK (dominant_hand IS NULL OR dominant_hand IN ('left', 'right')),
    CONSTRAINT fk_players_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_players_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.players IS
    'Player profiles (REQ-073): managed in the app, not sign-in accounts. Every measured record belongs to one player (REQ-075). Names unique case-insensitively among live profiles. Removing a profile hides everything of the player, also in shared practices; restoring brings it back.';
CREATE UNIQUE INDEX ux_players_name ON h_data.players (lower(display_name)) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_data.seasons (REQ-018)
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.seasons (
    season_id    bigint      GENERATED BY DEFAULT AS IDENTITY,
    player_id    bigint      NOT NULL,
    season_name  text        NOT NULL,
    start_date   date        NOT NULL,
    end_date     date        NOT NULL,
    created_by   bigint      NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   bigint      NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    deleted_at   timestamptz,
    is_deleted   boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_seasons PRIMARY KEY (season_id),
    CONSTRAINT ck_seasons_name CHECK (btrim(season_name) <> '' AND length(season_name) <= 100),
    CONSTRAINT ck_seasons_dates CHECK (end_date >= start_date),
    CONSTRAINT fk_seasons_player FOREIGN KEY (player_id) REFERENCES h_data.players (player_id),
    CONSTRAINT fk_seasons_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_seasons_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.seasons IS
    'User-defined seasons (REQ-018). Non-overlapping per player (trigger, Open Question 1). Games and practices are auto-assigned to the covering season.';
CREATE UNIQUE INDEX ux_seasons_player_name ON h_data.seasons (player_id, lower(season_name)) WHERE deleted_at IS NULL;
CREATE INDEX ix_seasons_player_start ON h_data.seasons (player_id, start_date) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_data.games (REQ-018 to REQ-023, REQ-057, REQ-061 to REQ-063)
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.games (
    game_id             bigint        GENERATED BY DEFAULT AS IDENTITY,
    player_id           bigint        NOT NULL,
    season_id           bigint,
    season_is_manual    boolean       NOT NULL DEFAULT false,
    game_date           date          NOT NULL,
    game_category_code  text          NOT NULL,
    game_format_code    text          NOT NULL,
    team_name           text,
    opponent_name       text,
    location            text,
    teammate_names      text,
    home_away           text,
    team_score          smallint,
    opponent_score      smallint,
    result              text          GENERATED ALWAYS AS (
                            CASE
                                WHEN team_score IS NULL OR opponent_score IS NULL THEN NULL
                                WHEN team_score > opponent_score THEN 'win'
                                WHEN team_score < opponent_score THEN 'loss'
                                ELSE 'tie'
                            END) STORED,
    target_score        smallint,
    minutes_played      numeric(4,1),
    off_rebounds        smallint      NOT NULL DEFAULT 0,
    def_rebounds        smallint      NOT NULL DEFAULT 0,
    assists             smallint      NOT NULL DEFAULT 0,
    steals              smallint      NOT NULL DEFAULT 0,
    blocks              smallint      NOT NULL DEFAULT 0,
    turnovers           smallint      NOT NULL DEFAULT 0,
    fouls               smallint      NOT NULL DEFAULT 0,
    entry_status        text          NOT NULL DEFAULT 'in_progress',
    notes               text,
    created_by          bigint        NOT NULL,
    created_at          timestamptz   NOT NULL DEFAULT now(),
    updated_by          bigint        NOT NULL,
    updated_at          timestamptz   NOT NULL DEFAULT now(),
    deleted_at          timestamptz,
    is_deleted          boolean       GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_games PRIMARY KEY (game_id),
    CONSTRAINT ux_games_player UNIQUE (game_id, player_id),
    CONSTRAINT ck_games_team_name CHECK (team_name IS NULL OR length(team_name) <= 100),
    CONSTRAINT ck_games_opponent_name CHECK (opponent_name IS NULL OR length(opponent_name) <= 100),
    CONSTRAINT ck_games_location CHECK (location IS NULL OR length(location) <= 100),
    CONSTRAINT ck_games_teammate_names CHECK (teammate_names IS NULL OR length(teammate_names) <= 200),
    CONSTRAINT ck_games_home_away CHECK (home_away IS NULL OR home_away IN ('home', 'away', 'neutral')),
    CONSTRAINT ck_games_scores CHECK ((team_score IS NULL OR team_score >= 0) AND (opponent_score IS NULL OR opponent_score >= 0)),
    CONSTRAINT ck_games_target CHECK (target_score IS NULL OR (target_score > 0 AND game_category_code = 'practice_game')),
    CONSTRAINT ck_games_minutes CHECK (minutes_played IS NULL OR minutes_played BETWEEN 0 AND 99),
    CONSTRAINT ck_games_counters CHECK (
        off_rebounds >= 0 AND def_rebounds >= 0 AND assists >= 0 AND steals >= 0
        AND blocks >= 0 AND turnovers >= 0 AND fouls >= 0),
    CONSTRAINT ck_games_entry_status CHECK (entry_status IN ('in_progress', 'completed')),
    CONSTRAINT ck_games_notes CHECK (notes IS NULL OR length(notes) <= 4000),
    CONSTRAINT fk_games_player FOREIGN KEY (player_id) REFERENCES h_data.players (player_id),
    CONSTRAINT fk_games_season FOREIGN KEY (season_id) REFERENCES h_data.seasons (season_id),
    CONSTRAINT fk_games_category FOREIGN KEY (game_category_code) REFERENCES h_ref.game_categories (game_category_code),
    CONSTRAINT fk_games_format FOREIGN KEY (game_format_code) REFERENCES h_ref.game_formats (game_format_code),
    CONSTRAINT fk_games_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_games_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.games IS
    'Games with the full box score (REQ-018 to REQ-023). Non-shooting counters live on this row; shooting is derived from entries (REQ-019). entry_status serves resume and the post-game summary (REQ-057, REQ-061). ux_games_player is the target of the entries'' two-column game foreign key, so player_id cannot change once the game has entries (COD-22-01).';
CREATE INDEX ix_games_player_date ON h_data.games (player_id, game_date) WHERE deleted_at IS NULL;
CREATE INDEX ix_games_season ON h_data.games (season_id);

-- -----------------------------------------------------------------------------
-- h_data.drill_types (REQ-029) - editable list
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.drill_types (
    drill_type_id  bigint      GENERATED BY DEFAULT AS IDENTITY,
    type_name      text        NOT NULL,
    sort_order     smallint    NOT NULL,
    description    text,
    created_by     bigint      NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_by     bigint      NOT NULL,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    deleted_at     timestamptz,
    is_deleted     boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_drill_types PRIMARY KEY (drill_type_id),
    CONSTRAINT ck_drill_types_name CHECK (btrim(type_name) <> '' AND length(type_name) <= 100),
    CONSTRAINT ck_drill_types_description CHECK (description IS NULL OR length(description) <= 4000),
    CONSTRAINT fk_drill_types_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_drill_types_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.drill_types IS 'Editable list of drill types (REQ-029).';
CREATE UNIQUE INDEX ux_drill_types_name ON h_data.drill_types (lower(type_name)) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_data.drills (REQ-026, REQ-028, REQ-080, REQ-081) - drill lineage
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.drills (
    drill_id               bigint      GENERATED BY DEFAULT AS IDENTITY,
    drill_name             text        NOT NULL,
    description            text,
    primary_drill_type_id  bigint      NOT NULL,
    difficulty_code        text        NOT NULL,
    retired_at             timestamptz,
    created_by             bigint      NOT NULL,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_by             bigint      NOT NULL,
    updated_at             timestamptz NOT NULL DEFAULT now(),
    deleted_at             timestamptz,
    is_deleted             boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_drills PRIMARY KEY (drill_id),
    CONSTRAINT ck_drills_name CHECK (btrim(drill_name) <> '' AND length(drill_name) <= 100 AND drill_name = btrim(drill_name)),
    CONSTRAINT ck_drills_description CHECK (description IS NULL OR length(description) <= 1000),
    CONSTRAINT fk_drills_primary_type FOREIGN KEY (primary_drill_type_id) REFERENCES h_data.drill_types (drill_type_id),
    CONSTRAINT fk_drills_difficulty FOREIGN KEY (difficulty_code) REFERENCES h_ref.difficulty_levels (difficulty_code),
    CONSTRAINT fk_drills_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_drills_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.drills IS
    'Drill lineage (REQ-026, REQ-028, REQ-080): name, description, primary type and difficulty, edited in place. What is recorded lives in h_data.drill_versions. retired_at = retired (REQ-081): hidden from selection, kept in history.';
CREATE UNIQUE INDEX ux_drills_name ON h_data.drills (lower(drill_name)) WHERE deleted_at IS NULL;
CREATE INDEX ix_drills_draw ON h_data.drills (difficulty_code, primary_drill_type_id) WHERE deleted_at IS NULL AND retired_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_data.drill_versions (REQ-076 to REQ-080)
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.drill_versions (
    drill_version_id          bigint      GENERATED BY DEFAULT AS IDENTITY,
    drill_id                  bigint      NOT NULL,
    version_no                smallint    NOT NULL,
    result_type_code          text        NOT NULL,
    symmetry_type_code        text,
    default_target            integer,
    fixed_seconds             integer,
    value_1_label             text,
    value_1_unit              text,
    value_1_higher_is_better  boolean,
    value_2_label             text,
    value_2_unit              text,
    value_2_higher_is_better  boolean,
    created_by                bigint      NOT NULL,
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_by                bigint      NOT NULL,
    updated_at                timestamptz NOT NULL DEFAULT now(),
    deleted_at                timestamptz,
    is_deleted                boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_drill_versions PRIMARY KEY (drill_version_id),
    CONSTRAINT ux_drill_versions_no UNIQUE (drill_id, version_no),
    CONSTRAINT ck_drill_versions_no CHECK (version_no >= 1),
    CONSTRAINT ck_drill_versions_target CHECK (default_target IS NULL OR default_target > 0),
    CONSTRAINT ck_drill_versions_fixed_seconds CHECK (fixed_seconds IS NULL OR fixed_seconds > 0),
    CONSTRAINT ck_drill_versions_value_1 CHECK (
        num_nonnulls(value_1_label, value_1_unit, value_1_higher_is_better) IN (0, 3)
        AND (value_1_label IS NULL OR (btrim(value_1_label) <> '' AND length(value_1_label) <= 50))),
    CONSTRAINT ck_drill_versions_value_2 CHECK (
        num_nonnulls(value_2_label, value_2_unit, value_2_higher_is_better) IN (0, 3)
        AND (value_2_label IS NULL OR (btrim(value_2_label) <> '' AND length(value_2_label) <= 50))
        AND (value_2_label IS NULL OR value_1_label IS NOT NULL)),
    CONSTRAINT ck_drill_versions_units CHECK (
        (value_1_unit IS NULL OR value_1_unit IN ('in', 'cm', 'ft', 'm', 'lb', 'kg', 's', 'ms', 'count', 'deg'))
        AND (value_2_unit IS NULL OR value_2_unit IN ('in', 'cm', 'ft', 'm', 'lb', 'kg', 's', 'ms', 'count', 'deg'))),
    CONSTRAINT fk_drill_versions_drill FOREIGN KEY (drill_id) REFERENCES h_data.drills (drill_id),
    CONSTRAINT fk_drill_versions_result_type FOREIGN KEY (result_type_code) REFERENCES h_ref.result_types (result_type_code),
    CONSTRAINT fk_drill_versions_symmetry FOREIGN KEY (symmetry_type_code) REFERENCES h_ref.symmetry_types (symmetry_type_code),
    CONSTRAINT fk_drill_versions_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_drill_versions_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.drill_versions IS
    'Versions of a drill (REQ-076 to REQ-080): result type, symmetry, target (the main number per type: shots per attempt, reps, seconds or count), fixed time (fixed time for reps) and, for physical measurements, up to two named numbers. Immutable except labels (tf_version_guard); version_no assigned by trigger. Latest live version = selectable. Hidden and restored only together with its drill, never on its own; a changed recording is a new version (COD-22-02).';

-- -----------------------------------------------------------------------------
-- h_data.drill_secondary_types (REQ-028)
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.drill_secondary_types (
    drill_secondary_type_id  bigint      GENERATED BY DEFAULT AS IDENTITY,
    drill_id                 bigint      NOT NULL,
    drill_type_id            bigint      NOT NULL,
    created_by               bigint      NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_by               bigint      NOT NULL,
    updated_at               timestamptz NOT NULL DEFAULT now(),
    deleted_at               timestamptz,
    is_deleted               boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_drill_secondary_types PRIMARY KEY (drill_secondary_type_id),
    CONSTRAINT fk_drill_secondary_types_drill FOREIGN KEY (drill_id) REFERENCES h_data.drills (drill_id),
    CONSTRAINT fk_drill_secondary_types_type FOREIGN KEY (drill_type_id) REFERENCES h_data.drill_types (drill_type_id),
    CONSTRAINT fk_drill_secondary_types_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_drill_secondary_types_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.drill_secondary_types IS
    'Secondary drill types of a drill (REQ-028). The app and the import check refuse a secondary type equal to the primary. Draws use the primary type only (Gate 1, Q2).';
CREATE UNIQUE INDEX ux_drill_secondary_types ON h_data.drill_secondary_types (drill_id, drill_type_id) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_data.drill_spots (REQ-031, REQ-080)
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.drill_spots (
    drill_spot_id     bigint      GENERATED BY DEFAULT AS IDENTITY,
    drill_version_id  bigint      NOT NULL,
    spot_order        smallint    NOT NULL,
    court_spot_id     bigint,
    is_free_throw     boolean     NOT NULL DEFAULT false,
    attempts_target   smallint,
    created_by        bigint      NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_by        bigint      NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    deleted_at        timestamptz,
    is_deleted        boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_drill_spots PRIMARY KEY (drill_spot_id),
    CONSTRAINT ck_drill_spots_order CHECK (spot_order >= 1),
    CONSTRAINT ck_drill_spots_target CHECK ((court_spot_id IS NOT NULL) <> is_free_throw),
    CONSTRAINT ck_drill_spots_attempts CHECK (attempts_target IS NULL OR attempts_target > 0),
    CONSTRAINT fk_drill_spots_version FOREIGN KEY (drill_version_id) REFERENCES h_data.drill_versions (drill_version_id),
    CONSTRAINT fk_drill_spots_court_spot FOREIGN KEY (court_spot_id) REFERENCES h_ref.court_spots (court_spot_id),
    CONSTRAINT fk_drill_spots_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_drill_spots_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.drill_spots IS
    'Spots of a shooting drill version, in order (REQ-031, REQ-080): a court spot or the free-throw line; attempts_target optional per spot. Hidden and restored only together with its drill (after the creating transaction); never added under a deleted version (tf_version_guard).';
CREATE UNIQUE INDEX ux_drill_spots_order ON h_data.drill_spots (drill_version_id, spot_order) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_data.drill_outcome_groups (REQ-077)
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.drill_outcome_groups (
    outcome_group_id  bigint      GENERATED BY DEFAULT AS IDENTITY,
    drill_version_id  bigint      NOT NULL,
    group_no          smallint    NOT NULL,
    group_name        text        NOT NULL,
    created_by        bigint      NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_by        bigint      NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    deleted_at        timestamptz,
    is_deleted        boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_drill_outcome_groups PRIMARY KEY (outcome_group_id),
    CONSTRAINT ck_drill_outcome_groups_no CHECK (group_no BETWEEN 1 AND 3),
    CONSTRAINT ck_drill_outcome_groups_name CHECK (btrim(group_name) <> '' AND length(group_name) <= 50),
    CONSTRAINT fk_drill_outcome_groups_version FOREIGN KEY (drill_version_id) REFERENCES h_data.drill_versions (drill_version_id),
    CONSTRAINT fk_drill_outcome_groups_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_drill_outcome_groups_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.drill_outcome_groups IS
    'Outcome groups of an outcome-button drill version (REQ-077): at most 3, each one judged aspect of a rep. Hidden and restored only together with its drill (after the creating transaction); never added under a deleted version (tf_version_guard).';
CREATE UNIQUE INDEX ux_drill_outcome_groups_no ON h_data.drill_outcome_groups (drill_version_id, group_no) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_data.drill_outcomes (REQ-077)
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.drill_outcomes (
    drill_outcome_id  bigint      GENERATED BY DEFAULT AS IDENTITY,
    outcome_group_id  bigint      NOT NULL,
    outcome_no        smallint    NOT NULL,
    outcome_label     text        NOT NULL,
    is_success        boolean     NOT NULL,
    created_by        bigint      NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_by        bigint      NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    deleted_at        timestamptz,
    is_deleted        boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_drill_outcomes PRIMARY KEY (drill_outcome_id),
    CONSTRAINT ux_drill_outcomes_pair UNIQUE (drill_outcome_id, outcome_group_id),
    CONSTRAINT ck_drill_outcomes_no CHECK (outcome_no BETWEEN 1 AND 6),
    CONSTRAINT ck_drill_outcomes_label CHECK (btrim(outcome_label) <> '' AND length(outcome_label) <= 30),
    CONSTRAINT fk_drill_outcomes_group FOREIGN KEY (outcome_group_id) REFERENCES h_data.drill_outcome_groups (outcome_group_id),
    CONSTRAINT fk_drill_outcomes_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_drill_outcomes_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.drill_outcomes IS
    'Buttons of an outcome group (REQ-077): 2 to 6, each marked success or not. Hidden and restored only together with its drill (after the creating transaction); never added under a deleted outcome group (tf_version_guard).';
CREATE UNIQUE INDEX ux_drill_outcomes_no ON h_data.drill_outcomes (outcome_group_id, outcome_no) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_data.drill_sets (REQ-051 to REQ-054, REQ-080, REQ-081) - drill set lineage
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.drill_sets (
    drill_set_id  bigint      GENERATED BY DEFAULT AS IDENTITY,
    set_name      text        NOT NULL,
    description   text,
    retired_at    timestamptz,
    created_by    bigint      NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_by    bigint      NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    deleted_at    timestamptz,
    is_deleted    boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_drill_sets PRIMARY KEY (drill_set_id),
    CONSTRAINT ck_drill_sets_name CHECK (btrim(set_name) <> '' AND length(set_name) <= 100),
    CONSTRAINT ck_drill_sets_description CHECK (description IS NULL OR length(description) <= 4000),
    CONSTRAINT fk_drill_sets_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_drill_sets_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.drill_sets IS
    'Drill set lineage (REQ-051 to REQ-054, REQ-080): name and description edited in place; kind, drills and lines live in h_data.drill_set_versions. retired_at as for drills (REQ-081).';
CREATE UNIQUE INDEX ux_drill_sets_name ON h_data.drill_sets (lower(set_name)) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_data.drill_set_versions (REQ-080)
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.drill_set_versions (
    drill_set_version_id  bigint      GENERATED BY DEFAULT AS IDENTITY,
    drill_set_id          bigint      NOT NULL,
    version_no            smallint    NOT NULL,
    set_kind              text        NOT NULL,
    created_by            bigint      NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_by            bigint      NOT NULL,
    updated_at            timestamptz NOT NULL DEFAULT now(),
    deleted_at            timestamptz,
    is_deleted            boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_drill_set_versions PRIMARY KEY (drill_set_version_id),
    CONSTRAINT ux_drill_set_versions_no UNIQUE (drill_set_id, version_no),
    CONSTRAINT ck_drill_set_versions_no CHECK (version_no >= 1),
    CONSTRAINT ck_drill_set_versions_kind CHECK (set_kind IN ('manual_fixed', 'quick_fixed', 'dynamic')),
    CONSTRAINT fk_drill_set_versions_set FOREIGN KEY (drill_set_id) REFERENCES h_data.drill_sets (drill_set_id),
    CONSTRAINT fk_drill_set_versions_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_drill_set_versions_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.drill_set_versions IS
    'Versions of a drill set (REQ-080): kind, ordered drills (items, by drill lineage) and draw lines. Immutable; a change of drills, order or lines is a new version. Hidden and restored only together with its drill set, never on its own (COD-22-02).';

-- -----------------------------------------------------------------------------
-- h_data.drill_set_items - the drills of a fixed set version
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.drill_set_items (
    drill_set_item_id     bigint      GENERATED BY DEFAULT AS IDENTITY,
    drill_set_version_id  bigint      NOT NULL,
    item_position         smallint    NOT NULL,
    drill_id              bigint      NOT NULL,
    created_by            bigint      NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_by            bigint      NOT NULL,
    updated_at            timestamptz NOT NULL DEFAULT now(),
    deleted_at            timestamptz,
    is_deleted            boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_drill_set_items PRIMARY KEY (drill_set_item_id),
    CONSTRAINT ck_drill_set_items_position CHECK (item_position >= 1),
    CONSTRAINT fk_drill_set_items_version FOREIGN KEY (drill_set_version_id) REFERENCES h_data.drill_set_versions (drill_set_version_id),
    CONSTRAINT fk_drill_set_items_drill FOREIGN KEY (drill_id) REFERENCES h_data.drills (drill_id),
    CONSTRAINT fk_drill_set_items_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_drill_set_items_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.drill_set_items IS
    'Drills of a manual or quick fixed set version, by drill lineage; the latest drill version is used when a practice is built. Order by (item_position, drill_set_item_id). Hidden and restored only together with its drill set (after the creating transaction); never added under a deleted version (tf_version_guard).';
CREATE INDEX ix_drill_set_items_version ON h_data.drill_set_items (drill_set_version_id, item_position) WHERE deleted_at IS NULL;
CREATE INDEX ix_drill_set_items_drill ON h_data.drill_set_items (drill_id);

-- -----------------------------------------------------------------------------
-- h_data.drill_set_lines - the draw recipe of a set version
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.drill_set_lines (
    drill_set_line_id     bigint      GENERATED BY DEFAULT AS IDENTITY,
    drill_set_version_id  bigint      NOT NULL,
    line_no               smallint    NOT NULL,
    drill_count           smallint    NOT NULL,
    difficulty_code       text        NOT NULL,
    drill_type_id         bigint,
    created_by            bigint      NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_by            bigint      NOT NULL,
    updated_at            timestamptz NOT NULL DEFAULT now(),
    deleted_at            timestamptz,
    is_deleted            boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_drill_set_lines PRIMARY KEY (drill_set_line_id),
    CONSTRAINT ck_drill_set_lines_no CHECK (line_no >= 1),
    CONSTRAINT ck_drill_set_lines_count CHECK (drill_count BETWEEN 1 AND 50),
    CONSTRAINT fk_drill_set_lines_version FOREIGN KEY (drill_set_version_id) REFERENCES h_data.drill_set_versions (drill_set_version_id),
    CONSTRAINT fk_drill_set_lines_difficulty FOREIGN KEY (difficulty_code) REFERENCES h_ref.difficulty_levels (difficulty_code),
    CONSTRAINT fk_drill_set_lines_type FOREIGN KEY (drill_type_id) REFERENCES h_data.drill_types (drill_type_id),
    CONSTRAINT fk_drill_set_lines_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_drill_set_lines_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.drill_set_lines IS
    'Draw lines of a quick fixed or dynamic set version (trigger-enforced): drill_count drills of a difficulty and, optionally, a primary drill type. Hidden and restored only together with its drill set (after the creating transaction); never added under a deleted version (tf_version_guard).';
CREATE UNIQUE INDEX ux_drill_set_lines_no ON h_data.drill_set_lines (drill_set_version_id, line_no) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_data.practice_templates (REQ-072, REQ-080, REQ-081) - template lineage
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.practice_templates (
    practice_template_id  bigint      GENERATED BY DEFAULT AS IDENTITY,
    template_name         text        NOT NULL,
    description           text,
    retired_at            timestamptz,
    created_by            bigint      NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_by            bigint      NOT NULL,
    updated_at            timestamptz NOT NULL DEFAULT now(),
    deleted_at            timestamptz,
    is_deleted            boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_practice_templates PRIMARY KEY (practice_template_id),
    CONSTRAINT ck_practice_templates_name CHECK (btrim(template_name) <> '' AND length(template_name) <= 100),
    CONSTRAINT ck_practice_templates_description CHECK (description IS NULL OR length(description) <= 4000),
    CONSTRAINT fk_practice_templates_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_practice_templates_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.practice_templates IS
    'Practice template lineage (REQ-072, REQ-080): practice template -> drill set -> drill. No location, duration or results (REQ-025). retired_at as for drills (REQ-081).';
CREATE UNIQUE INDEX ux_practice_templates_name ON h_data.practice_templates (lower(template_name)) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_data.practice_template_versions (REQ-080)
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.practice_template_versions (
    practice_template_version_id  bigint      GENERATED BY DEFAULT AS IDENTITY,
    practice_template_id          bigint      NOT NULL,
    version_no                    smallint    NOT NULL,
    created_by                    bigint      NOT NULL,
    created_at                    timestamptz NOT NULL DEFAULT now(),
    updated_by                    bigint      NOT NULL,
    updated_at                    timestamptz NOT NULL DEFAULT now(),
    deleted_at                    timestamptz,
    is_deleted                    boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_practice_template_versions PRIMARY KEY (practice_template_version_id),
    CONSTRAINT ux_practice_template_versions_no UNIQUE (practice_template_id, version_no),
    CONSTRAINT ck_practice_template_versions_no CHECK (version_no >= 1),
    CONSTRAINT fk_practice_template_versions_template FOREIGN KEY (practice_template_id) REFERENCES h_data.practice_templates (practice_template_id),
    CONSTRAINT fk_practice_template_versions_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_practice_template_versions_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.practice_template_versions IS
    'Versions of a practice template (REQ-080): the ordered drill sets. Immutable. Hidden and restored only together with its practice template, never on its own (COD-22-02).';

-- -----------------------------------------------------------------------------
-- h_data.practice_template_sets (REQ-072, REQ-080)
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.practice_template_sets (
    practice_template_set_id      bigint      GENERATED BY DEFAULT AS IDENTITY,
    practice_template_version_id  bigint      NOT NULL,
    template_position             smallint    NOT NULL,
    drill_set_id                  bigint      NOT NULL,
    created_by                    bigint      NOT NULL,
    created_at                    timestamptz NOT NULL DEFAULT now(),
    updated_by                    bigint      NOT NULL,
    updated_at                    timestamptz NOT NULL DEFAULT now(),
    deleted_at                    timestamptz,
    is_deleted                    boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_practice_template_sets PRIMARY KEY (practice_template_set_id),
    CONSTRAINT ck_practice_template_sets_position CHECK (template_position >= 1),
    CONSTRAINT fk_practice_template_sets_version FOREIGN KEY (practice_template_version_id) REFERENCES h_data.practice_template_versions (practice_template_version_id),
    CONSTRAINT fk_practice_template_sets_set FOREIGN KEY (drill_set_id) REFERENCES h_data.drill_sets (drill_set_id),
    CONSTRAINT fk_practice_template_sets_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_practice_template_sets_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.practice_template_sets IS
    'Drill sets of a template version, by set lineage, in order (template_position, id). A set appears at most once per template (REQ-080). Hidden and restored only together with its practice template (after the creating transaction); never added under a deleted version (tf_version_guard).';
CREATE INDEX ix_practice_template_sets_version ON h_data.practice_template_sets (practice_template_version_id, template_position) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX ux_practice_template_sets_set ON h_data.practice_template_sets (practice_template_version_id, drill_set_id) WHERE deleted_at IS NULL;
CREATE INDEX ix_practice_template_sets_set ON h_data.practice_template_sets (drill_set_id);

-- -----------------------------------------------------------------------------
-- h_data.practice_sessions (REQ-025, REQ-057, REQ-074, REQ-082)
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.practice_sessions (
    practice_session_id           bigint      GENERATED BY DEFAULT AS IDENTITY,
    session_date                  date        NOT NULL,
    practice_type_code            text        NOT NULL,
    practice_template_version_id  bigint,
    duration_minutes              smallint,
    location                      text,
    notes                         text,
    entry_status                  text        NOT NULL DEFAULT 'in_progress',
    created_by                    bigint      NOT NULL,
    created_at                    timestamptz NOT NULL DEFAULT now(),
    updated_by                    bigint      NOT NULL,
    updated_at                    timestamptz NOT NULL DEFAULT now(),
    deleted_at                    timestamptz,
    is_deleted                    boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_practice_sessions PRIMARY KEY (practice_session_id),
    CONSTRAINT ck_practice_sessions_duration CHECK (duration_minutes IS NULL OR duration_minutes BETWEEN 1 AND 600),
    CONSTRAINT ck_practice_sessions_location CHECK (location IS NULL OR length(location) <= 100),
    CONSTRAINT ck_practice_sessions_notes CHECK (notes IS NULL OR length(notes) <= 4000),
    CONSTRAINT ck_practice_sessions_entry_status CHECK (entry_status IN ('in_progress', 'completed')),
    CONSTRAINT fk_practice_sessions_type FOREIGN KEY (practice_type_code) REFERENCES h_ref.practice_types (practice_type_code),
    CONSTRAINT fk_practice_sessions_template_version FOREIGN KEY (practice_template_version_id) REFERENCES h_data.practice_template_versions (practice_template_version_id),
    CONSTRAINT fk_practice_sessions_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_practice_sessions_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.practice_sessions IS
    'Practice sessions (REQ-025, REQ-074, REQ-082). Players take part through h_data.practice_players. practice_template_version_id = the exact template version it was started from (REQ-080), NULL otherwise. Never locked (REQ-082).';
CREATE INDEX ix_practice_sessions_date ON h_data.practice_sessions (session_date) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_data.practice_players (REQ-018, REQ-074)
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.practice_players (
    practice_player_id   bigint      GENERATED BY DEFAULT AS IDENTITY,
    practice_session_id  bigint      NOT NULL,
    player_id            bigint      NOT NULL,
    season_id            bigint,
    season_is_manual     boolean     NOT NULL DEFAULT false,
    created_by           bigint      NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_by           bigint      NOT NULL,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    deleted_at           timestamptz,
    is_deleted           boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_practice_players PRIMARY KEY (practice_player_id),
    -- Exists only as the target of the practice records' three-column foreign keys
    -- (practice_player_id plus the copied practice_session_id and player_id, COD-22-01).
    CONSTRAINT ux_practice_players_triple UNIQUE (practice_player_id, practice_session_id, player_id),
    CONSTRAINT fk_practice_players_session FOREIGN KEY (practice_session_id) REFERENCES h_data.practice_sessions (practice_session_id),
    CONSTRAINT fk_practice_players_player FOREIGN KEY (player_id) REFERENCES h_data.players (player_id),
    CONSTRAINT fk_practice_players_season FOREIGN KEY (season_id) REFERENCES h_data.seasons (season_id),
    CONSTRAINT fk_practice_players_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_practice_players_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.practice_players IS
    'Players taking part in a practice (REQ-074): one row per participation. Every practice record (shot and free-throw entries of a practice, drill results, drill attempts) carries practice_player_id, with practice_session_id and player_id equal to this row''s (three-column foreign key). At most one live row per practice and player (ux_practice_players_live). A removed row is never restored; re-adding the player creates a new row (h_data.fn_add_practice_player), so the removed row''s records are never shown again (COD-22-02). season_id = that player''s covering season for the practice date unless season_is_manual (REQ-018).';
CREATE UNIQUE INDEX ux_practice_players_live ON h_data.practice_players (practice_session_id, player_id) WHERE deleted_at IS NULL;
CREATE INDEX ix_practice_players_player ON h_data.practice_players (player_id) WHERE deleted_at IS NULL;
CREATE INDEX ix_practice_players_season ON h_data.practice_players (season_id);

-- -----------------------------------------------------------------------------
-- h_data.practice_drill_sets - a drill set version placed in a practice
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.practice_drill_sets (
    practice_drill_set_id  bigint      GENERATED BY DEFAULT AS IDENTITY,
    practice_session_id    bigint      NOT NULL,
    drill_set_version_id   bigint      NOT NULL,
    practice_position      smallint    NOT NULL,
    draw_notice            text,
    created_by             bigint      NOT NULL,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_by             bigint      NOT NULL,
    updated_at             timestamptz NOT NULL DEFAULT now(),
    deleted_at             timestamptz,
    is_deleted             boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_practice_drill_sets PRIMARY KEY (practice_drill_set_id),
    CONSTRAINT ck_practice_drill_sets_position CHECK (practice_position >= 1),
    CONSTRAINT ck_practice_drill_sets_notice CHECK (draw_notice IS NULL OR length(draw_notice) <= 4000),
    CONSTRAINT ux_practice_drill_sets_pair UNIQUE (practice_drill_set_id, practice_session_id),
    CONSTRAINT fk_practice_drill_sets_session FOREIGN KEY (practice_session_id) REFERENCES h_data.practice_sessions (practice_session_id),
    CONSTRAINT fk_practice_drill_sets_version FOREIGN KEY (drill_set_version_id) REFERENCES h_data.drill_set_versions (drill_set_version_id),
    CONSTRAINT fk_practice_drill_sets_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_practice_drill_sets_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.practice_drill_sets IS
    'A drill set placed in a practice (REQ-051); records the exact set version used (REQ-080). practice_position shares one numbering with ad-hoc drills. draw_notice keeps any short-draw or left-out notice (REQ-054).';
CREATE INDEX ix_practice_drill_sets_session ON h_data.practice_drill_sets (practice_session_id);
CREATE INDEX ix_practice_drill_sets_version ON h_data.practice_drill_sets (drill_set_version_id);

-- -----------------------------------------------------------------------------
-- h_data.practice_drills - the actual drills of a practice
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.practice_drills (
    practice_drill_id      bigint      GENERATED BY DEFAULT AS IDENTITY,
    practice_session_id    bigint      NOT NULL,
    practice_drill_set_id  bigint,
    practice_position      smallint,
    set_position           smallint,
    drill_version_id       bigint      NOT NULL,
    source                 text        NOT NULL,
    line_no                smallint,
    draw_difficulty_code   text,
    draw_drill_type_id     bigint,
    created_by             bigint      NOT NULL,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_by             bigint      NOT NULL,
    updated_at             timestamptz NOT NULL DEFAULT now(),
    deleted_at             timestamptz,
    is_deleted             boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_practice_drills PRIMARY KEY (practice_drill_id),
    CONSTRAINT ck_practice_drills_source CHECK (source IN ('fixed', 'drawn', 'swapped', 'ad_hoc')),
    CONSTRAINT ck_practice_drills_set CHECK ((practice_drill_set_id IS NULL) = (source = 'ad_hoc')),
    CONSTRAINT ck_practice_drills_position CHECK (
        (source = 'ad_hoc' AND practice_position IS NOT NULL AND set_position IS NULL)
        OR (source <> 'ad_hoc' AND set_position IS NOT NULL AND practice_position IS NULL)),
    CONSTRAINT ck_practice_drills_draw CHECK (
        source NOT IN ('drawn', 'swapped') OR (line_no IS NOT NULL AND draw_difficulty_code IS NOT NULL)),
    CONSTRAINT ck_practice_drills_numbers CHECK (
        (practice_position IS NULL OR practice_position >= 1)
        AND (set_position IS NULL OR set_position >= 1)
        AND (line_no IS NULL OR line_no >= 1)),
    CONSTRAINT ux_practice_drills_pair UNIQUE (practice_drill_id, practice_session_id),
    CONSTRAINT fk_practice_drills_session FOREIGN KEY (practice_session_id) REFERENCES h_data.practice_sessions (practice_session_id),
    CONSTRAINT fk_practice_drills_set FOREIGN KEY (practice_drill_set_id, practice_session_id)
        REFERENCES h_data.practice_drill_sets (practice_drill_set_id, practice_session_id),
    CONSTRAINT fk_practice_drills_version FOREIGN KEY (drill_version_id) REFERENCES h_data.drill_versions (drill_version_id),
    CONSTRAINT fk_practice_drills_draw_difficulty FOREIGN KEY (draw_difficulty_code) REFERENCES h_ref.difficulty_levels (difficulty_code),
    CONSTRAINT fk_practice_drills_draw_type FOREIGN KEY (draw_drill_type_id) REFERENCES h_data.drill_types (drill_type_id),
    CONSTRAINT fk_practice_drills_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_practice_drills_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.practice_drills IS
    'Drills of a practice: copied from a fixed set, drawn, swapped, or ad hoc. drill_version_id = the exact drill version used (REQ-080); it may change (swap) even with results (REQ-082). Run order (REQ-056): COALESCE(set practice_position, ad-hoc practice_position), then set_position. Draw snapshot columns serve swaps (REQ-053).';
CREATE INDEX ix_practice_drills_session ON h_data.practice_drills (practice_session_id);
CREATE INDEX ix_practice_drills_set ON h_data.practice_drills (practice_drill_set_id);
CREATE INDEX ix_practice_drills_version ON h_data.practice_drills (drill_version_id);

-- -----------------------------------------------------------------------------
-- h_data.drill_results (REQ-025, REQ-075, REQ-078)
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.drill_results (
    drill_result_id      bigint      GENERATED BY DEFAULT AS IDENTITY,
    practice_session_id  bigint      NOT NULL,
    practice_player_id   bigint      NOT NULL,
    practice_drill_id    bigint      NOT NULL,
    player_id            bigint      NOT NULL,
    drill_version_id     bigint      NOT NULL,
    notes                text,
    created_by           bigint      NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_by           bigint      NOT NULL,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    deleted_at           timestamptz,
    is_deleted           boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_drill_results PRIMARY KEY (drill_result_id),
    CONSTRAINT ck_drill_results_notes CHECK (notes IS NULL OR length(notes) <= 4000),
    CONSTRAINT ux_drill_results_participant UNIQUE (drill_result_id, practice_player_id),
    CONSTRAINT fk_drill_results_practice_drill FOREIGN KEY (practice_drill_id, practice_session_id)
        REFERENCES h_data.practice_drills (practice_drill_id, practice_session_id),
    CONSTRAINT fk_drill_results_practice_player FOREIGN KEY (practice_player_id, practice_session_id, player_id)
        REFERENCES h_data.practice_players (practice_player_id, practice_session_id, player_id),
    CONSTRAINT fk_drill_results_version FOREIGN KEY (drill_version_id) REFERENCES h_data.drill_versions (drill_version_id),
    CONSTRAINT fk_drill_results_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_drill_results_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.drill_results IS
    'One participant''s result for one practice drill (REQ-075, REQ-078): holds the attempts. practice_player_id = the participant row (h_data.practice_players) whose result this is; practice_session_id and player_id equal that row''s (three-column foreign key). No new result for a removed participant row (trigger). drill_version_id = the version recorded under, copied from the practice drill on insert (trigger); values live on h_data.drill_attempts.';
CREATE UNIQUE INDEX ux_drill_results_slot_participant ON h_data.drill_results (practice_drill_id, practice_player_id) WHERE deleted_at IS NULL;
CREATE INDEX ix_drill_results_practice_player ON h_data.drill_results (practice_player_id);
CREATE INDEX ix_drill_results_session_player ON h_data.drill_results (practice_session_id, player_id);

-- -----------------------------------------------------------------------------
-- h_data.drill_attempts (REQ-076, REQ-078, REQ-079)
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.drill_attempts (
    drill_attempt_id     bigint        GENERATED BY DEFAULT AS IDENTITY,
    drill_result_id      bigint        NOT NULL,
    practice_session_id  bigint        NOT NULL,
    practice_player_id   bigint        NOT NULL,
    player_id            bigint        NOT NULL,
    attempt_no           smallint      NOT NULL,
    side                 text,
    value_1              numeric(9,3),
    value_2              numeric(9,3),
    attempted_at         timestamptz   NOT NULL DEFAULT now(),
    created_by           bigint        NOT NULL,
    created_at           timestamptz   NOT NULL DEFAULT now(),
    updated_by           bigint        NOT NULL,
    updated_at           timestamptz   NOT NULL DEFAULT now(),
    deleted_at           timestamptz,
    is_deleted           boolean       GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_drill_attempts PRIMARY KEY (drill_attempt_id),
    CONSTRAINT ck_drill_attempts_no CHECK (attempt_no >= 1),
    CONSTRAINT ck_drill_attempts_side CHECK (side IS NULL OR side IN ('left', 'right')),
    CONSTRAINT ck_drill_attempts_values CHECK ((value_1 IS NULL OR value_1 >= 0) AND (value_2 IS NULL OR value_2 >= 0)),
    CONSTRAINT ux_drill_attempts_participant UNIQUE (drill_attempt_id, practice_player_id),
    CONSTRAINT fk_drill_attempts_result FOREIGN KEY (drill_result_id, practice_player_id)
        REFERENCES h_data.drill_results (drill_result_id, practice_player_id),
    CONSTRAINT fk_drill_attempts_practice_player FOREIGN KEY (practice_player_id, practice_session_id, player_id)
        REFERENCES h_data.practice_players (practice_player_id, practice_session_id, player_id),
    CONSTRAINT fk_drill_attempts_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_drill_attempts_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.drill_attempts IS
    'Attempts of a drill result (REQ-078): attempt_no set by trigger (deleted ones counted), side for drills with a symmetry type (REQ-079), up to two values per the result type (REQ-076). For outcome drills an attempt is one rep; for shooting drills its shots link here. practice_player_id = its result''s participant row (foreign key on result id + participant id); practice_session_id and player_id equal that row''s (three-column foreign key). No new attempt for a removed participant row (trigger).';
CREATE UNIQUE INDEX ux_drill_attempts_no ON h_data.drill_attempts (drill_result_id, attempt_no);
CREATE INDEX ix_drill_attempts_practice_player ON h_data.drill_attempts (practice_player_id);
CREATE INDEX ix_drill_attempts_session_player ON h_data.drill_attempts (practice_session_id, player_id);

-- -----------------------------------------------------------------------------
-- h_data.drill_attempt_outcomes (REQ-077)
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.drill_attempt_outcomes (
    drill_attempt_outcome_id  bigint      GENERATED BY DEFAULT AS IDENTITY,
    drill_attempt_id          bigint      NOT NULL,
    outcome_group_id          bigint      NOT NULL,
    drill_outcome_id          bigint      NOT NULL,
    created_by                bigint      NOT NULL,
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_by                bigint      NOT NULL,
    updated_at                timestamptz NOT NULL DEFAULT now(),
    deleted_at                timestamptz,
    is_deleted                boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_drill_attempt_outcomes PRIMARY KEY (drill_attempt_outcome_id),
    CONSTRAINT fk_drill_attempt_outcomes_attempt FOREIGN KEY (drill_attempt_id) REFERENCES h_data.drill_attempts (drill_attempt_id),
    CONSTRAINT fk_drill_attempt_outcomes_outcome FOREIGN KEY (drill_outcome_id, outcome_group_id)
        REFERENCES h_data.drill_outcomes (drill_outcome_id, outcome_group_id),
    CONSTRAINT fk_drill_attempt_outcomes_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_drill_attempt_outcomes_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.drill_attempt_outcomes IS
    'The button tapped in one outcome group for one attempt (rep) (REQ-077). One per group per attempt. Belongs to the attempt''s participant row (h_data.drill_attempts.practice_player_id); no tap can be added to a deleted attempt (trigger).';
CREATE UNIQUE INDEX ux_drill_attempt_outcomes_group ON h_data.drill_attempt_outcomes (drill_attempt_id, outcome_group_id) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_data.shot_entries (REQ-010 to REQ-017, REQ-031, REQ-075)
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.shot_entries (
    shot_entry_id        bigint      GENERATED BY DEFAULT AS IDENTITY,
    game_id              bigint,
    practice_session_id  bigint,
    practice_player_id   bigint,
    player_id            bigint      NOT NULL,
    context              text        GENERATED ALWAYS AS (
                             CASE WHEN game_id IS NOT NULL THEN 'game' ELSE 'practice' END) STORED,
    entry_mode           text        NOT NULL,
    court_spot_id        bigint,
    manual_point_value   smallint,
    makes                smallint    NOT NULL,
    attempts             smallint    NOT NULL,
    seq_in_context       integer     NOT NULL,
    shot_at              timestamptz NOT NULL DEFAULT now(),
    shot_type_code       text,
    hand                 text,
    creation             text,
    defense              text,
    drill_attempt_id     bigint,
    created_by           bigint      NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_by           bigint      NOT NULL,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    deleted_at           timestamptz,
    is_deleted           boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_shot_entries PRIMARY KEY (shot_entry_id),
    CONSTRAINT ck_shot_entries_parent CHECK (num_nonnulls(game_id, practice_session_id) = 1),
    CONSTRAINT ck_shot_entries_mode CHECK (entry_mode IN ('single', 'cumulative', 'manual')),
    CONSTRAINT ck_shot_entries_counts CHECK (
        attempts >= 1 AND makes BETWEEN 0 AND attempts AND (entry_mode <> 'single' OR attempts = 1)),
    CONSTRAINT ck_shot_entries_mode_spot CHECK (
        (entry_mode = 'manual' AND court_spot_id IS NULL AND manual_point_value IN (2, 3))
        OR (entry_mode <> 'manual' AND court_spot_id IS NOT NULL AND manual_point_value IS NULL)),
    CONSTRAINT ck_shot_entries_hand CHECK (hand IS NULL OR hand IN ('left', 'right')),
    CONSTRAINT ck_shot_entries_creation CHECK (creation IS NULL OR creation IN ('catch_and_shoot', 'off_dribble')),
    CONSTRAINT ck_shot_entries_defense CHECK (defense IS NULL OR defense IN ('open', 'contested')),
    -- MATCH SIMPLE skips a composite FK when any column is NULL, so a practice entry
    -- must carry its participant row and a drill link must carry it too.
    CONSTRAINT ck_shot_entries_participant CHECK ((practice_player_id IS NULL) = (practice_session_id IS NULL)),
    CONSTRAINT ck_shot_entries_drill_practice CHECK (drill_attempt_id IS NULL OR practice_player_id IS NOT NULL),
    -- Game rows: player_id is a copy of the game's, held equal by the two-column FK
    -- (COD-22-01). Practice rows have no game_id, so MATCH SIMPLE skips them.
    CONSTRAINT fk_shot_entries_game FOREIGN KEY (game_id, player_id) REFERENCES h_data.games (game_id, player_id),
    CONSTRAINT fk_shot_entries_practice FOREIGN KEY (practice_session_id) REFERENCES h_data.practice_sessions (practice_session_id),
    CONSTRAINT fk_shot_entries_spot FOREIGN KEY (court_spot_id) REFERENCES h_ref.court_spots (court_spot_id),
    CONSTRAINT fk_shot_entries_shot_type FOREIGN KEY (shot_type_code) REFERENCES h_ref.shot_types (shot_type_code),
    CONSTRAINT fk_shot_entries_player FOREIGN KEY (player_id) REFERENCES h_data.players (player_id),
    CONSTRAINT fk_shot_entries_practice_player FOREIGN KEY (practice_player_id, practice_session_id, player_id)
        REFERENCES h_data.practice_players (practice_player_id, practice_session_id, player_id),
    CONSTRAINT fk_shot_entries_drill_attempt FOREIGN KEY (drill_attempt_id, practice_player_id)
        REFERENCES h_data.drill_attempts (drill_attempt_id, practice_player_id),
    CONSTRAINT fk_shot_entries_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_shot_entries_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.shot_entries IS
    'Field-goal entries (REQ-011 to REQ-017): single (attempts = 1), cumulative (totals at a spot) or manual (no spot; manual_point_value 2 or 3). player_id = the shooter; in a practice the entry carries practice_player_id, its participant row, with practice_session_id and player_id equal to that row''s (three-column FK; no new entry for a removed participant row, trigger), in a game the game''s player (two-column FK on ux_games_player) and no practice_player_id. No new entry under a deleted game, practice or drill attempt (trigger). seq_in_context is set by trigger in entry order. context is generated: practice-game shots are game shots (REQ-015).';
-- The seq indexes include deleted rows on purpose, so a restored row never collides.
CREATE UNIQUE INDEX ux_shot_entries_game_seq ON h_data.shot_entries (game_id, seq_in_context) WHERE game_id IS NOT NULL;
CREATE UNIQUE INDEX ux_shot_entries_practice_seq ON h_data.shot_entries (practice_session_id, seq_in_context) WHERE practice_session_id IS NOT NULL;
CREATE INDEX ix_shot_entries_drill_attempt ON h_data.shot_entries (drill_attempt_id);
CREATE INDEX ix_shot_entries_spot ON h_data.shot_entries (court_spot_id);
CREATE INDEX ix_shot_entries_player ON h_data.shot_entries (player_id);
CREATE INDEX ix_shot_entries_practice_player ON h_data.shot_entries (practice_player_id);

-- -----------------------------------------------------------------------------
-- h_data.free_throw_entries (REQ-014, REQ-015, REQ-031, REQ-075)
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.free_throw_entries (
    free_throw_entry_id  bigint      GENERATED BY DEFAULT AS IDENTITY,
    game_id              bigint,
    practice_session_id  bigint,
    practice_player_id   bigint,
    player_id            bigint      NOT NULL,
    context              text        GENERATED ALWAYS AS (
                             CASE WHEN game_id IS NOT NULL THEN 'game' ELSE 'practice' END) STORED,
    entry_mode           text        NOT NULL,
    makes                smallint    NOT NULL,
    attempts             smallint    NOT NULL,
    seq_in_context       integer     NOT NULL,
    shot_at              timestamptz NOT NULL DEFAULT now(),
    drill_attempt_id     bigint,
    created_by           bigint      NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_by           bigint      NOT NULL,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    deleted_at           timestamptz,
    is_deleted           boolean     GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_free_throw_entries PRIMARY KEY (free_throw_entry_id),
    CONSTRAINT ck_free_throw_entries_parent CHECK (num_nonnulls(game_id, practice_session_id) = 1),
    CONSTRAINT ck_free_throw_entries_mode CHECK (entry_mode IN ('single', 'cumulative')),
    CONSTRAINT ck_free_throw_entries_counts CHECK (
        attempts >= 1 AND makes BETWEEN 0 AND attempts AND (entry_mode <> 'single' OR attempts = 1)),
    CONSTRAINT ck_free_throw_entries_participant CHECK ((practice_player_id IS NULL) = (practice_session_id IS NULL)),
    CONSTRAINT ck_free_throw_entries_drill_practice CHECK (drill_attempt_id IS NULL OR practice_player_id IS NOT NULL),
    CONSTRAINT fk_free_throw_entries_game FOREIGN KEY (game_id, player_id) REFERENCES h_data.games (game_id, player_id),
    CONSTRAINT fk_free_throw_entries_practice FOREIGN KEY (practice_session_id) REFERENCES h_data.practice_sessions (practice_session_id),
    CONSTRAINT fk_free_throw_entries_player FOREIGN KEY (player_id) REFERENCES h_data.players (player_id),
    CONSTRAINT fk_free_throw_entries_practice_player FOREIGN KEY (practice_player_id, practice_session_id, player_id)
        REFERENCES h_data.practice_players (practice_player_id, practice_session_id, player_id),
    CONSTRAINT fk_free_throw_entries_drill_attempt FOREIGN KEY (drill_attempt_id, practice_player_id)
        REFERENCES h_data.drill_attempts (drill_attempt_id, practice_player_id),
    CONSTRAINT fk_free_throw_entries_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_free_throw_entries_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.free_throw_entries IS
    'Free-throw entries, separate from spots (REQ-014): single or cumulative, with their own seq_in_context per game or practice. player_id = the shooter; in a practice the entry carries practice_player_id, its participant row, with practice_session_id and player_id equal to that row''s (three-column FK; no new entry for a removed participant row, trigger), in a game the game''s player (two-column FK on ux_games_player) and no practice_player_id. No new entry under a deleted game, practice or drill attempt (trigger).';
CREATE UNIQUE INDEX ux_free_throw_entries_game_seq ON h_data.free_throw_entries (game_id, seq_in_context) WHERE game_id IS NOT NULL;
CREATE UNIQUE INDEX ux_free_throw_entries_practice_seq ON h_data.free_throw_entries (practice_session_id, seq_in_context) WHERE practice_session_id IS NOT NULL;
CREATE INDEX ix_free_throw_entries_drill_attempt ON h_data.free_throw_entries (drill_attempt_id);
CREATE INDEX ix_free_throw_entries_player ON h_data.free_throw_entries (player_id);
CREATE INDEX ix_free_throw_entries_practice_player ON h_data.free_throw_entries (practice_player_id);

-- -----------------------------------------------------------------------------
-- h_data.goals (REQ-032, REQ-033)
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.goals (
    goal_id            bigint         GENERATED BY DEFAULT AS IDENTITY,
    player_id          bigint         NOT NULL,
    goal_name          text           NOT NULL,
    goal_type          text           NOT NULL,
    target_value       numeric(10,3)  NOT NULL,
    target_direction   text           NOT NULL DEFAULT 'at_least',
    start_date         date           NOT NULL,
    deadline           date,
    status             text           NOT NULL DEFAULT 'active',
    status_is_manual   boolean        NOT NULL DEFAULT false,
    achieved_on        date,
    stat_code          text,
    window_kind        text,
    window_n           smallint,
    window_season_id   bigint,
    window_from        date,
    window_to          date,
    window_scope       text,
    drill_id           bigint,
    drill_metric       text,
    drill_group_no     smallint,
    drill_basis        text,
    physical_test_id   bigint,
    notes              text,
    created_by         bigint         NOT NULL,
    created_at         timestamptz    NOT NULL DEFAULT now(),
    updated_by         bigint         NOT NULL,
    updated_at         timestamptz    NOT NULL DEFAULT now(),
    deleted_at         timestamptz,
    is_deleted         boolean        GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_goals PRIMARY KEY (goal_id),
    CONSTRAINT ck_goals_name CHECK (btrim(goal_name) <> '' AND length(goal_name) <= 100),
    CONSTRAINT ck_goals_type CHECK (goal_type IN ('stat', 'drill', 'habit', 'physical')),
    CONSTRAINT ck_goals_target CHECK (target_value >= 0),
    CONSTRAINT ck_goals_direction CHECK (target_direction IN ('at_least', 'at_most')),
    CONSTRAINT ck_goals_deadline CHECK (deadline IS NULL OR deadline >= start_date),
    CONSTRAINT ck_goals_status CHECK (status IN ('active', 'achieved', 'missed', 'archived')),
    CONSTRAINT ck_goals_achieved CHECK (status <> 'achieved' OR achieved_on IS NOT NULL),
    CONSTRAINT ck_goals_window_kind CHECK (window_kind IS NULL OR window_kind IN ('last_n', 'season', 'date_range')),
    CONSTRAINT ck_goals_window_n CHECK (window_n IS NULL OR window_n BETWEEN 1 AND 200),
    CONSTRAINT ck_goals_window_scope CHECK (window_scope IS NULL OR window_scope IN ('game', 'practice', 'both')),
    CONSTRAINT ck_goals_drill_metric CHECK (drill_metric IS NULL OR drill_metric IN ('makes', 'make_pct', 'value_1', 'value_2', 'clean_pct')),
    CONSTRAINT ck_goals_drill_group CHECK (drill_group_no IS NULL OR drill_group_no BETWEEN 1 AND 3),
    CONSTRAINT ck_goals_drill_basis CHECK (drill_basis IS NULL OR drill_basis IN ('best', 'latest')),
    CONSTRAINT ck_goals_notes CHECK (notes IS NULL OR length(notes) <= 4000),
    CONSTRAINT ck_goals_type_fields CHECK (
        CASE goal_type
            WHEN 'stat' THEN
                stat_code IS NOT NULL AND window_kind IS NOT NULL AND window_scope IS NOT NULL
                AND drill_id IS NULL AND drill_metric IS NULL AND drill_basis IS NULL
                AND physical_test_id IS NULL AND drill_group_no IS NULL
                AND CASE window_kind
                        WHEN 'last_n' THEN
                            window_n IS NOT NULL AND window_season_id IS NULL
                            AND window_from IS NULL AND window_to IS NULL
                        WHEN 'season' THEN
                            window_season_id IS NOT NULL AND window_n IS NULL
                            AND window_from IS NULL AND window_to IS NULL
                        WHEN 'date_range' THEN
                            window_from IS NOT NULL AND window_to IS NOT NULL AND window_from <= window_to
                            AND window_n IS NULL AND window_season_id IS NULL
                        ELSE false
                    END
            WHEN 'drill' THEN
                drill_id IS NOT NULL AND drill_metric IS NOT NULL AND drill_basis IS NOT NULL
                AND stat_code IS NULL AND window_kind IS NULL AND window_n IS NULL
                AND window_season_id IS NULL AND window_from IS NULL AND window_to IS NULL
                AND window_scope IS NULL AND physical_test_id IS NULL
                AND ((drill_metric = 'clean_pct') = (drill_group_no IS NOT NULL))
            WHEN 'habit' THEN
                target_value = trunc(target_value) AND target_value BETWEEN 1 AND 7
                AND stat_code IS NULL AND window_kind IS NULL AND window_n IS NULL
                AND window_season_id IS NULL AND window_from IS NULL AND window_to IS NULL
                AND window_scope IS NULL AND drill_id IS NULL AND drill_metric IS NULL
                AND drill_basis IS NULL AND physical_test_id IS NULL AND drill_group_no IS NULL
            WHEN 'physical' THEN
                physical_test_id IS NOT NULL
                AND stat_code IS NULL AND window_kind IS NULL AND window_n IS NULL
                AND window_season_id IS NULL AND window_from IS NULL AND window_to IS NULL
                AND window_scope IS NULL AND drill_id IS NULL AND drill_metric IS NULL
                AND drill_basis IS NULL AND drill_group_no IS NULL
            ELSE false
        END),
    CONSTRAINT fk_goals_player FOREIGN KEY (player_id) REFERENCES h_data.players (player_id),
    CONSTRAINT fk_goals_stat FOREIGN KEY (stat_code) REFERENCES h_ref.stat_definitions (stat_code),
    CONSTRAINT fk_goals_window_season FOREIGN KEY (window_season_id) REFERENCES h_data.seasons (season_id),
    CONSTRAINT fk_goals_drill FOREIGN KEY (drill_id) REFERENCES h_data.drills (drill_id),
    CONSTRAINT fk_goals_physical_test FOREIGN KEY (physical_test_id) REFERENCES h_ref.physical_tests (physical_test_id),
    CONSTRAINT fk_goals_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_goals_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.goals IS
    'Goals (REQ-032, REQ-033): stat, drill, habit (days per Monday-Sunday week) or physical. ck_goals_type_fields requires exactly the fields of the type; drill goals read attempt metrics, or clean_pct of drill_group_no. Status is automatic unless status_is_manual.';
CREATE INDEX ix_goals_player_status ON h_data.goals (player_id, status) WHERE deleted_at IS NULL;

-- -----------------------------------------------------------------------------
-- h_data.physical_measurements (REQ-034, REQ-035)
-- -----------------------------------------------------------------------------
CREATE TABLE h_data.physical_measurements (
    physical_measurement_id  bigint        GENERATED BY DEFAULT AS IDENTITY,
    player_id                bigint        NOT NULL,
    physical_test_id         bigint        NOT NULL,
    measured_on              date          NOT NULL,
    measured_value           numeric(7,2)  NOT NULL,
    notes                    text,
    created_by               bigint        NOT NULL,
    created_at               timestamptz   NOT NULL DEFAULT now(),
    updated_by               bigint        NOT NULL,
    updated_at               timestamptz   NOT NULL DEFAULT now(),
    deleted_at               timestamptz,
    is_deleted               boolean       GENERATED ALWAYS AS (deleted_at IS NOT NULL) STORED,
    CONSTRAINT pk_physical_measurements PRIMARY KEY (physical_measurement_id),
    CONSTRAINT ck_physical_measurements_value CHECK (measured_value > 0),
    CONSTRAINT ck_physical_measurements_notes CHECK (notes IS NULL OR length(notes) <= 4000),
    CONSTRAINT fk_physical_measurements_player FOREIGN KEY (player_id) REFERENCES h_data.players (player_id),
    CONSTRAINT fk_physical_measurements_test FOREIGN KEY (physical_test_id) REFERENCES h_ref.physical_tests (physical_test_id),
    CONSTRAINT fk_physical_measurements_created_by FOREIGN KEY (created_by) REFERENCES h_system.users (user_id),
    CONSTRAINT fk_physical_measurements_updated_by FOREIGN KEY (updated_by) REFERENCES h_system.users (user_id)
);
COMMENT ON TABLE h_data.physical_measurements IS
    'Physical measurements (REQ-034, REQ-035): historical by date, any number per test and day. The unit comes from the test.';
CREATE INDEX ix_physical_measurements_player_test_date
    ON h_data.physical_measurements (player_id, physical_test_id, measured_on);


-- =============================================================================
-- Part A2: selection views (REQ-072, REQ-080, REQ-081)
-- =============================================================================

-- h_data.v_current_drills: one row per selectable drill (live, not retired) with
-- its latest live version. Used by the draws and by placement.
CREATE VIEW h_data.v_current_drills AS
SELECT DISTINCT ON (d.drill_id)
       d.drill_id, d.drill_name, d.description, d.primary_drill_type_id, d.difficulty_code,
       dv.drill_version_id, dv.version_no, dv.result_type_code, dv.symmetry_type_code,
       dv.default_target, dv.fixed_seconds,
       dv.value_1_label, dv.value_1_unit, dv.value_1_higher_is_better,
       dv.value_2_label, dv.value_2_unit, dv.value_2_higher_is_better
FROM h_data.drills d
JOIN h_data.drill_versions dv ON dv.drill_id = d.drill_id AND dv.deleted_at IS NULL
WHERE d.deleted_at IS NULL AND d.retired_at IS NULL
ORDER BY d.drill_id, dv.version_no DESC;
COMMENT ON VIEW h_data.v_current_drills IS
    'REQ-080/081: selectable drills - live, not retired, with their latest live version only.';

-- h_data.v_current_drill_sets: one row per selectable drill set with its latest
-- live version.
CREATE VIEW h_data.v_current_drill_sets AS
SELECT DISTINCT ON (s.drill_set_id)
       s.drill_set_id, s.set_name, s.description, sv.drill_set_version_id, sv.version_no, sv.set_kind
FROM h_data.drill_sets s
JOIN h_data.drill_set_versions sv ON sv.drill_set_id = s.drill_set_id AND sv.deleted_at IS NULL
WHERE s.deleted_at IS NULL AND s.retired_at IS NULL
ORDER BY s.drill_set_id, sv.version_no DESC;
COMMENT ON VIEW h_data.v_current_drill_sets IS
    'REQ-080/081: selectable drill sets with their latest live version only.';

-- h_data.v_current_practice_templates: one row per selectable practice template
-- with its latest live version and that version's live set count.
CREATE VIEW h_data.v_current_practice_templates AS
SELECT DISTINCT ON (t.practice_template_id)
       t.practice_template_id, t.template_name, t.description,
       tv.practice_template_version_id, tv.version_no,
       (SELECT count(*)::integer FROM h_data.practice_template_sets ts
        WHERE ts.practice_template_version_id = tv.practice_template_version_id AND ts.deleted_at IS NULL) AS set_count
FROM h_data.practice_templates t
JOIN h_data.practice_template_versions tv ON tv.practice_template_id = t.practice_template_id AND tv.deleted_at IS NULL
WHERE t.deleted_at IS NULL AND t.retired_at IS NULL
ORDER BY t.practice_template_id, tv.version_no DESC;
COMMENT ON VIEW h_data.v_current_practice_templates IS
    'REQ-072/080/081: selectable practice templates with their latest live version and its set count.';


-- =============================================================================
-- Part B1: trigger functions and triggers (plan 6.8)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- h_data.tf_assign_season (REQ-018)
-- Description : BEFORE INSERT OR UPDATE on games and practice_players. When the
--               season is not manual, sets season_id to the player's covering
--               season of the event date (or NULL). When manual, keeps season_id
--               (it may be NULL) but refuses a season of another player.
--               INSERT: refuses a game or participant row for a removed profile,
--               and a participant row for a deleted practice (COD-22-03).
--               Every UPDATE recomputes, including one that changes only
--               deleted_at, so a restored row gets the covering live season
--               (Amendment A4.1).
-- Parameters  : none (trigger).
-- Return Values: trigger - the modified NEW row.
-- Notes       : The event date is game_date for games and the practice's
--               session_date for practice players. The parent checks read the
--               practice and player rows FOR SHARE, so an insert and a concurrent
--               soft delete of that parent run one after the other (Amendment A4).
--               The profile cascade relies on h_data.fn_soft_delete('player')
--               marking seasons last and h_data.fn_restore('player') clearing
--               them first, so the recomputation during the cascade yields the
--               same seasons.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.tf_assign_season()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_event_date     date;
    v_season_player  bigint;
    v_deleted        timestamptz;
    v_name           text;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF TG_TABLE_NAME = 'practice_players' THEN
            SELECT ps.deleted_at
            INTO v_deleted
            FROM h_data.practice_sessions ps
            WHERE ps.practice_session_id = NEW.practice_session_id
            FOR SHARE;
            IF FOUND AND v_deleted IS NOT NULL THEN
                RAISE EXCEPTION 'This practice was deleted; no new players can be added to it';
            END IF;
        END IF;
        SELECT p.deleted_at, p.display_name
        INTO v_deleted, v_name
        FROM h_data.players p
        WHERE p.player_id = NEW.player_id
        FOR SHARE;
        IF FOUND AND v_deleted IS NOT NULL THEN
            RAISE EXCEPTION '% was removed; restore the player before recording anything for them', v_name;
        END IF;
    END IF;

    IF TG_TABLE_NAME = 'games' THEN
        v_event_date := NEW.game_date;
    ELSE
        SELECT ps.session_date
        INTO v_event_date
        FROM h_data.practice_sessions ps
        WHERE ps.practice_session_id = NEW.practice_session_id;
    END IF;

    IF NOT NEW.season_is_manual THEN
        NEW.season_id := h_data.fn_season_for_date(NEW.player_id, v_event_date);
    ELSIF NEW.season_id IS NOT NULL THEN
        SELECT s.player_id
        INTO v_season_player
        FROM h_data.seasons s
        WHERE s.season_id = NEW.season_id;
        IF v_season_player IS DISTINCT FROM NEW.player_id THEN
            RAISE EXCEPTION 'Season % does not belong to player %', NEW.season_id, NEW.player_id;
        END IF;
    END IF;
    RETURN NEW;
END
$$;
COMMENT ON FUNCTION h_data.tf_assign_season() IS
    'REQ-018: covering live season for games and for each practice player, unless season_is_manual; recomputed on every update, including a restore (A4.1). COD-22-03: no game or participant row for a removed profile, no participant row for a deleted practice (share locks).';

-- -----------------------------------------------------------------------------
-- h_data.tf_seasons_reassign (REQ-018)
-- Description : AFTER INSERT OR UPDATE on seasons. Re-assigns the player's live,
--               non-manual games and practice participations (practice_players of
--               live practices) whose season no longer matches the covering season.
--               A soft delete or restore is an UPDATE, so both re-assign too.
-- Parameters  : none (trigger).
-- Return Values: trigger - NULL (AFTER trigger).
-- Notes       : Deleted games/participations are left alone so their updated_by
--               (who deleted them) is preserved; a restore re-assigns them through
--               tf_assign_season. In the profile cascade seasons are marked last
--               and cleared first, so this finds no live rows of the player then
--               (Amendment A4.1).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.tf_seasons_reassign()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_old_player bigint;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        v_old_player := OLD.player_id;
    END IF;

    UPDATE h_data.games g
    SET season_id = h_data.fn_season_for_date(g.player_id, g.game_date)
    WHERE g.player_id IN (NEW.player_id, v_old_player)
      AND NOT g.season_is_manual
      AND g.deleted_at IS NULL
      AND g.season_id IS DISTINCT FROM h_data.fn_season_for_date(g.player_id, g.game_date);

    UPDATE h_data.practice_players pp
    SET season_id = h_data.fn_season_for_date(pp.player_id, ps.session_date)
    FROM h_data.practice_sessions ps
    WHERE ps.practice_session_id = pp.practice_session_id
      AND pp.player_id IN (NEW.player_id, v_old_player)
      AND NOT pp.season_is_manual
      AND pp.deleted_at IS NULL
      AND ps.deleted_at IS NULL
      AND pp.season_id IS DISTINCT FROM h_data.fn_season_for_date(pp.player_id, ps.session_date);

    RETURN NULL;
END
$$;
COMMENT ON FUNCTION h_data.tf_seasons_reassign() IS
    'REQ-018: after a season changes, re-assigns the player''s live non-manual games and practice participations, also on a season soft delete or restore (A4.1).';

-- -----------------------------------------------------------------------------
-- h_data.tf_seasons_no_overlap (Open Question 1)
-- Description : BEFORE INSERT OR UPDATE on seasons. Raises when another
--               non-deleted season of the same player overlaps the new dates.
--               INSERT: refuses a season for a removed profile (Amendment A4).
--               UPDATE that makes a season live (deleted_at cleared, i.e. a
--               restore): refuses it too while the profile is removed (review_3).
-- Parameters  : none (trigger).
-- Return Values: trigger - NEW unchanged.
-- Notes       : Skipped when NEW is deleted, so deleting is always allowed and a
--               restore re-checks against the player's live seasons only. Locks
--               the player row first, so two concurrent writes of seasons for the
--               same player run the check one after the other and cannot both
--               pass under READ COMMITTED; the same lock orders an insert or a
--               restore after a concurrent profile removal.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.tf_seasons_no_overlap()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_other_name      text;
    v_player_deleted  timestamptz;
    v_player_name     text;
BEGIN
    IF NEW.deleted_at IS NOT NULL THEN
        RETURN NEW;
    END IF;

    SELECT p.deleted_at, p.display_name
    INTO v_player_deleted, v_player_name
    FROM h_data.players p
    WHERE p.player_id = NEW.player_id
    FOR UPDATE;
    IF TG_OP = 'INSERT' AND v_player_deleted IS NOT NULL THEN
        RAISE EXCEPTION '% was removed; restore the player before recording anything for them', v_player_name;
    END IF;
    -- review_3: a restore (deleted_at cleared) is refused while the profile is removed.
    IF TG_OP = 'UPDATE' AND v_player_deleted IS NOT NULL THEN
        IF OLD.deleted_at IS NOT NULL THEN
            RAISE EXCEPTION '% was removed; restore the player first', v_player_name;
        END IF;
    END IF;

    SELECT s.season_name
    INTO v_other_name
    FROM h_data.seasons s
    WHERE s.player_id = NEW.player_id
      AND s.deleted_at IS NULL
      AND s.season_id <> NEW.season_id
      AND s.start_date <= NEW.end_date
      AND s.end_date >= NEW.start_date
    ORDER BY s.start_date
    LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION 'Season dates overlap season "%"', v_other_name;
    END IF;
    RETURN NEW;
END
$$;
COMMENT ON FUNCTION h_data.tf_seasons_no_overlap() IS
    'Refuses overlapping non-deleted seasons for a player (Gate 1, Q1), and a new or restored season for a removed profile (COD-22-03, review_3).';

-- -----------------------------------------------------------------------------
-- h_data.tf_practice_sessions_redate (REQ-018)
-- Description : AFTER UPDATE on practice_sessions. When session_date changes,
--               re-assigns the covering season of each live, non-manual
--               participant (practice_players row) for the new date.
-- Parameters  : none (trigger).
-- Return Values: trigger - NULL (AFTER trigger).
-- Notes       : Does nothing when the date is unchanged (soft delete, restore,
--               other columns).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.tf_practice_sessions_redate()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.session_date IS DISTINCT FROM OLD.session_date THEN
        UPDATE h_data.practice_players pp
        SET season_id = h_data.fn_season_for_date(pp.player_id, NEW.session_date)
        WHERE pp.practice_session_id = NEW.practice_session_id
          AND NOT pp.season_is_manual
          AND pp.deleted_at IS NULL
          AND pp.season_id IS DISTINCT FROM h_data.fn_season_for_date(pp.player_id, NEW.session_date);
    END IF;
    RETURN NULL;
END
$$;
COMMENT ON FUNCTION h_data.tf_practice_sessions_redate() IS
    'REQ-018: a changed practice date re-assigns each participant''s season.';

-- -----------------------------------------------------------------------------
-- h_data.tf_entry_before_write (REQ-017, REQ-031, REQ-075)
-- Description : BEFORE INSERT OR UPDATE on shot_entries and free_throw_entries.
--               INSERT: locks the parent game or practice row and sets
--               seq_in_context to the next number over the same table and parent,
--               deleted rows included (any supplied value is ignored); refuses
--               the entry when its game or practice is soft-deleted; a game
--               entry must belong to the game's player; a practice entry is
--               refused when its participant row (practice_player_id) is
--               soft-deleted. UPDATE: refuses a change of parent, of
--               seq_in_context, of player_id or of practice_player_id. Both: a
--               linked drill attempt must be live, must belong to a shooting drill
--               version, and a free throw may link only to a version with a
--               free-throw-line spot.
-- Parameters  : none (trigger).
-- Return Values: trigger - the modified NEW row.
-- Notes       : The parent-row lock makes the sequence exact under concurrency;
--               the sequence is per practice, across its players. In a practice
--               the shooter must be a participant: the three-column foreign key to
--               practice_players (practice_player_id, practice_session_id,
--               player_id) enforces the right row, this trigger a live one (a
--               foreign key cannot test deleted_at); a missing participant row is
--               left to the foreign key. The participant row and a linked drill
--               attempt are read FOR SHARE, so an insert and a concurrent soft
--               delete of either run one after the other (COD-22-03, Amendment
--               A4). The drill link is checked on INSERT and when
--               drill_attempt_id changes, so a later soft delete never fails on it.
--               A game entry's player_id is also held equal to the game's by the
--               two-column foreign key on ux_games_player; this check gives the
--               friendly message.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.tf_entry_before_write()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_check_link        boolean;
    v_game_player       bigint;
    v_parent_deleted    timestamptz;
    v_attempt_deleted   timestamptz;
    v_result_type       text;
    v_has_ft_spot       boolean;
BEGIN
    IF TG_OP = 'INSERT' THEN
        v_check_link := true;
        IF NEW.game_id IS NOT NULL THEN
            SELECT g.player_id, g.deleted_at
            INTO v_game_player, v_parent_deleted
            FROM h_data.games g
            WHERE g.game_id = NEW.game_id
            FOR UPDATE;
            IF FOUND AND v_parent_deleted IS NOT NULL THEN
                RAISE EXCEPTION 'This game was deleted; no new entries can be added to it';
            END IF;
            IF FOUND AND v_game_player IS DISTINCT FROM NEW.player_id THEN
                RAISE EXCEPTION 'An entry of game % must belong to the game''s player %', NEW.game_id, v_game_player;
            END IF;
            IF TG_TABLE_NAME = 'shot_entries' THEN
                SELECT COALESCE(max(e.seq_in_context), 0) + 1
                INTO NEW.seq_in_context
                FROM h_data.shot_entries e
                WHERE e.game_id = NEW.game_id;
            ELSE
                SELECT COALESCE(max(e.seq_in_context), 0) + 1
                INTO NEW.seq_in_context
                FROM h_data.free_throw_entries e
                WHERE e.game_id = NEW.game_id;
            END IF;
        ELSIF NEW.practice_session_id IS NOT NULL THEN
            SELECT ps.deleted_at
            INTO v_parent_deleted
            FROM h_data.practice_sessions ps
            WHERE ps.practice_session_id = NEW.practice_session_id
            FOR UPDATE;
            IF FOUND AND v_parent_deleted IS NOT NULL THEN
                RAISE EXCEPTION 'This practice was deleted; no new entries can be added to it';
            END IF;
            SELECT pp.deleted_at
            INTO v_parent_deleted
            FROM h_data.practice_players pp
            WHERE pp.practice_player_id = NEW.practice_player_id
            FOR SHARE;
            IF FOUND AND v_parent_deleted IS NOT NULL THEN
                RAISE EXCEPTION 'The player was removed from this practice; no new records can be added for that participant';
            END IF;
            IF TG_TABLE_NAME = 'shot_entries' THEN
                SELECT COALESCE(max(e.seq_in_context), 0) + 1
                INTO NEW.seq_in_context
                FROM h_data.shot_entries e
                WHERE e.practice_session_id = NEW.practice_session_id;
            ELSE
                SELECT COALESCE(max(e.seq_in_context), 0) + 1
                INTO NEW.seq_in_context
                FROM h_data.free_throw_entries e
                WHERE e.practice_session_id = NEW.practice_session_id;
            END IF;
        END IF;
    ELSE
        IF NEW.game_id IS DISTINCT FROM OLD.game_id
           OR NEW.practice_session_id IS DISTINCT FROM OLD.practice_session_id THEN
            RAISE EXCEPTION 'An entry cannot move to another game or practice';
        END IF;
        IF NEW.seq_in_context IS DISTINCT FROM OLD.seq_in_context THEN
            RAISE EXCEPTION 'seq_in_context of an entry cannot change';
        END IF;
        IF NEW.player_id IS DISTINCT FROM OLD.player_id THEN
            RAISE EXCEPTION 'An entry cannot move to another player';
        END IF;
        IF NEW.practice_player_id IS DISTINCT FROM OLD.practice_player_id THEN
            RAISE EXCEPTION 'An entry cannot move to another player or participant';
        END IF;
        v_check_link := NEW.drill_attempt_id IS DISTINCT FROM OLD.drill_attempt_id;
    END IF;

    IF v_check_link AND NEW.drill_attempt_id IS NOT NULL THEN
        SELECT a.deleted_at
        INTO v_attempt_deleted
        FROM h_data.drill_attempts a
        WHERE a.drill_attempt_id = NEW.drill_attempt_id
        FOR SHARE;
        IF FOUND AND v_attempt_deleted IS NOT NULL THEN
            RAISE EXCEPTION 'This drill attempt was deleted; no shots can be linked to it';
        END IF;
        SELECT dv.result_type_code,
               EXISTS (SELECT 1 FROM h_data.drill_spots s
                       WHERE s.drill_version_id = dv.drill_version_id AND s.is_free_throw AND s.deleted_at IS NULL)
        INTO v_result_type, v_has_ft_spot
        FROM h_data.drill_attempts a
        JOIN h_data.drill_results r ON r.drill_result_id = a.drill_result_id
        JOIN h_data.drill_versions dv ON dv.drill_version_id = r.drill_version_id
        WHERE a.drill_attempt_id = NEW.drill_attempt_id;
        IF FOUND AND v_result_type <> 'shooting' THEN
            RAISE EXCEPTION 'Shots can be linked only to an attempt of a shooting drill';
        END IF;
        IF FOUND AND TG_TABLE_NAME = 'free_throw_entries' AND NOT v_has_ft_spot THEN
            RAISE EXCEPTION 'Free throws can be linked only to a drill defined with a free-throw-line spot';
        END IF;
    END IF;
    RETURN NEW;
END
$$;
COMMENT ON FUNCTION h_data.tf_entry_before_write() IS
    'REQ-017/075: exact per-parent sequence; immutable parent, sequence, player and participant row; game entries belong to the game''s player; COD-22-03: no new entry under a deleted game or practice or for a removed participant row, and no link to a deleted drill attempt (share locks); drill links only to shooting attempts, free throws only with a free-throw spot.';

-- -----------------------------------------------------------------------------
-- h_data.tf_drill_results_validate (REQ-078, REQ-080)
-- Description : BEFORE INSERT OR UPDATE on drill_results. INSERT: refuses the
--               row when its practice, its practice drill or its participant row
--               (practice_player_id) is soft-deleted, then sets drill_version_id
--               to the practice drill's current version (any supplied value is
--               ignored). UPDATE: refuses a change of practice_drill_id,
--               practice_session_id, practice_player_id, player_id or
--               drill_version_id.
-- Parameters  : none (trigger).
-- Return Values: trigger - the modified NEW row.
-- Notes       : A result therefore always records the version it was entered
--               under, whatever later happens to its practice slot (swap, REQ-082).
--               Values live on h_data.drill_attempts. The foreign keys guarantee
--               the right parents, this trigger live ones; a missing parent is
--               left to the foreign key. Practice, participant row and practice
--               drill are read FOR SHARE, so an insert and a concurrent soft
--               delete of a parent run one after the other (COD-22-03,
--               Amendment A4).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.tf_drill_results_validate()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_deleted  timestamptz;
    v_version  bigint;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT ps.deleted_at
        INTO v_deleted
        FROM h_data.practice_sessions ps
        WHERE ps.practice_session_id = NEW.practice_session_id
        FOR SHARE;
        IF FOUND AND v_deleted IS NOT NULL THEN
            RAISE EXCEPTION 'This practice was deleted; no new results can be added to it';
        END IF;
        SELECT pp.deleted_at
        INTO v_deleted
        FROM h_data.practice_players pp
        WHERE pp.practice_player_id = NEW.practice_player_id
        FOR SHARE;
        IF FOUND AND v_deleted IS NOT NULL THEN
            RAISE EXCEPTION 'The player was removed from this practice; no new results can be added for that participant';
        END IF;
        SELECT pd.deleted_at, pd.drill_version_id
        INTO v_deleted, v_version
        FROM h_data.practice_drills pd
        WHERE pd.practice_drill_id = NEW.practice_drill_id
        FOR SHARE;
        IF FOUND AND v_deleted IS NOT NULL THEN
            RAISE EXCEPTION 'This drill was removed from the practice; no new results can be added to it';
        END IF;
        NEW.drill_version_id := v_version;
        RETURN NEW;
    END IF;
    IF NEW.practice_drill_id IS DISTINCT FROM OLD.practice_drill_id
       OR NEW.practice_session_id IS DISTINCT FROM OLD.practice_session_id
       OR NEW.practice_player_id IS DISTINCT FROM OLD.practice_player_id
       OR NEW.player_id IS DISTINCT FROM OLD.player_id
       OR NEW.drill_version_id IS DISTINCT FROM OLD.drill_version_id THEN
        RAISE EXCEPTION 'A drill result cannot move to another practice drill, player or drill version (drill_result %)', OLD.drill_result_id;
    END IF;
    RETURN NEW;
END
$$;
COMMENT ON FUNCTION h_data.tf_drill_results_validate() IS
    'REQ-078/080: a result takes the practice drill''s current version on insert and never moves; COD-22-03: no new result under a deleted practice or practice drill or for a removed participant row (share locks).';

-- -----------------------------------------------------------------------------
-- h_data.tf_drill_attempts_before_write (REQ-076, REQ-078, REQ-079)
-- Description : BEFORE INSERT OR UPDATE on drill_attempts. INSERT: refuses the
--               row when its participant row (practice_player_id) or its drill
--               result is soft-deleted, then sets attempt_no to the next number
--               of the result, deleted attempts included. UPDATE:
--               refuses a change of result, practice, participant row, player or
--               attempt_no. Both (INSERT, or UPDATE of side or
--               values): side is allowed only when the version has a symmetry
--               type (never court_side on a shooting drill, whose side comes from
--               the shot's spot); values only for a 'values' result type; value_2
--               only when the version names a second number.
-- Parameters  : none (trigger).
-- Return Values: trigger - the modified NEW row.
-- Notes       : A soft delete or restore changes neither side nor values, so it
--               is never refused here. The foreign keys guarantee the right
--               participant row and result, this trigger live ones; a missing
--               parent is left to the foreign key. The participant row is read
--               FOR SHARE and the result row FOR UPDATE (which also serialises
--               the numbering), so an insert and a concurrent soft delete of
--               either run one after the other (COD-22-03, Amendment A4).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.tf_drill_attempts_before_write()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_check        boolean;
    v_deleted      timestamptz;
    v_result_type  text;
    v_symmetry     text;
    v_capture      text;
    v_has_value_2  boolean;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT pp.deleted_at
        INTO v_deleted
        FROM h_data.practice_players pp
        WHERE pp.practice_player_id = NEW.practice_player_id
        FOR SHARE;
        IF FOUND AND v_deleted IS NOT NULL THEN
            RAISE EXCEPTION 'The player was removed from this practice; no new attempts can be added for that participant';
        END IF;
        SELECT r.deleted_at
        INTO v_deleted
        FROM h_data.drill_results r
        WHERE r.drill_result_id = NEW.drill_result_id
        FOR UPDATE;
        IF FOUND AND v_deleted IS NOT NULL THEN
            RAISE EXCEPTION 'This drill result was deleted; no new attempts can be added to it';
        END IF;
        SELECT COALESCE(max(a.attempt_no), 0) + 1
        INTO NEW.attempt_no
        FROM h_data.drill_attempts a
        WHERE a.drill_result_id = NEW.drill_result_id;
        v_check := true;
    ELSE
        IF NEW.drill_result_id IS DISTINCT FROM OLD.drill_result_id
           OR NEW.practice_session_id IS DISTINCT FROM OLD.practice_session_id
           OR NEW.practice_player_id IS DISTINCT FROM OLD.practice_player_id
           OR NEW.player_id IS DISTINCT FROM OLD.player_id
           OR NEW.attempt_no IS DISTINCT FROM OLD.attempt_no THEN
            RAISE EXCEPTION 'An attempt cannot move or change its number (drill_attempt %)', OLD.drill_attempt_id;
        END IF;
        v_check := NEW.side IS DISTINCT FROM OLD.side
                   OR NEW.value_1 IS DISTINCT FROM OLD.value_1
                   OR NEW.value_2 IS DISTINCT FROM OLD.value_2;
    END IF;
    IF NOT v_check THEN
        RETURN NEW;
    END IF;

    SELECT dv.result_type_code, dv.symmetry_type_code, rt.capture_kind, (dv.value_2_label IS NOT NULL)
    INTO v_result_type, v_symmetry, v_capture, v_has_value_2
    FROM h_data.drill_results r
    JOIN h_data.drill_versions dv ON dv.drill_version_id = r.drill_version_id
    JOIN h_ref.result_types rt ON rt.result_type_code = dv.result_type_code
    WHERE r.drill_result_id = NEW.drill_result_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- the foreign key reports it
    END IF;

    IF NEW.side IS NOT NULL AND (v_symmetry IS NULL OR (v_result_type = 'shooting' AND v_symmetry = 'court_side')) THEN
        RAISE EXCEPTION 'This drill records no side per attempt%',
            CASE WHEN v_symmetry = 'court_side' THEN ' (the court side comes from each shot''s spot)' ELSE '' END;
    END IF;
    IF v_capture <> 'values' AND (NEW.value_1 IS NOT NULL OR NEW.value_2 IS NOT NULL) THEN
        RAISE EXCEPTION 'An attempt of result type % stores no values', v_result_type;
    END IF;
    IF NEW.value_2 IS NOT NULL AND NOT v_has_value_2 THEN
        RAISE EXCEPTION 'This drill records one value per attempt';
    END IF;
    RETURN NEW;
END
$$;
COMMENT ON FUNCTION h_data.tf_drill_attempts_before_write() IS
    'REQ-076/078/079: attempt numbers per result (deleted counted); side and values allowed by the version; attempts never move; COD-22-03: no new attempt under a deleted drill result or for a removed participant row (row locks).';

-- -----------------------------------------------------------------------------
-- h_data.tf_drill_attempt_outcomes_validate (REQ-077)
-- Description : BEFORE INSERT OR UPDATE on drill_attempt_outcomes. INSERT:
--               refuses a tap whose attempt is soft-deleted (this covers a
--               removed participant row, whose removal deletes its attempts).
--               Both: the outcome group must belong to the drill version of the
--               attempt's result, and that version must be an outcome-button drill.
-- Parameters  : none (trigger).
-- Return Values: trigger - NEW unchanged.
-- Notes       : On UPDATE it checks only when the attempt or the group changes, so
--               a soft delete or restore is never refused. The pairing of outcome
--               and group is enforced by the composite foreign key; a missing
--               attempt is left to the foreign key. On INSERT the attempt is read
--               FOR SHARE, so a tap and a concurrent soft delete of the attempt
--               run one after the other (COD-22-03, Amendment A4).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.tf_drill_attempt_outcomes_validate()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_ok       boolean;
    v_deleted  timestamptz;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.drill_attempt_id IS NOT DISTINCT FROM OLD.drill_attempt_id
       AND NEW.outcome_group_id IS NOT DISTINCT FROM OLD.outcome_group_id THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'INSERT' THEN
        SELECT a.deleted_at
        INTO v_deleted
        FROM h_data.drill_attempts a
        WHERE a.drill_attempt_id = NEW.drill_attempt_id
        FOR SHARE;
        IF FOUND AND v_deleted IS NOT NULL THEN
            RAISE EXCEPTION 'The attempt is deleted; outcomes cannot be added to it';
        END IF;
    END IF;
    SELECT (dv.result_type_code = 'outcome_buttons' AND g.drill_version_id = r.drill_version_id)
    INTO v_ok
    FROM h_data.drill_attempts a
    JOIN h_data.drill_results r ON r.drill_result_id = a.drill_result_id
    JOIN h_data.drill_versions dv ON dv.drill_version_id = r.drill_version_id
    JOIN h_data.drill_outcome_groups g ON g.outcome_group_id = NEW.outcome_group_id
    WHERE a.drill_attempt_id = NEW.drill_attempt_id;
    IF FOUND AND NOT v_ok THEN
        RAISE EXCEPTION 'The outcome group does not belong to the drill version of this attempt';
    END IF;
    RETURN NEW;
END
$$;
COMMENT ON FUNCTION h_data.tf_drill_attempt_outcomes_validate() IS
    'REQ-077: an outcome tap belongs to a group of the attempt''s own outcome-button drill version; COD-22-03: no new tap on a deleted attempt (share lock).';

-- -----------------------------------------------------------------------------
-- h_data.tf_version_guard (REQ-080)
-- Description : BEFORE INSERT OR UPDATE on drill_versions, drill_spots,
--               drill_outcome_groups, drill_outcomes, drill_set_versions,
--               drill_set_items, drill_set_lines, practice_template_versions and
--               practice_template_sets.
--               UPDATE: refuses any change of what a version records (its parent,
--               number, result type, symmetry, target, fixed time, units and
--               directions; a child's parent, order and content). Labels
--               (value_n_label, group_name, outcome_label) may change.
--               deleted_at (rule V1, Amendment A4) may change only:
--                 - with the lineage's deletion: OLD is live and the new value
--                   equals the lineage row's deleted_at, which is already set;
--                 - with the lineage's restore: cleared while the lineage row and
--                   the row's direct parent are live (and, for a content row,
--                   not a row that was removed inside its version's creating
--                   transaction);
--                 - for a content row, inside the transaction that created its
--                   version (version created_at = now()).
--               Every other change of deleted_at is refused.
--               INSERT of a version: the lineage must be live and not retired;
--               locks the lineage row and sets version_no to max + 1 (any supplied
--               value is ignored). A drill version also gets its fixed-time and
--               physical-measurement rules checked against the result type.
--               INSERT of a content row (rule V2): its direct parent (the version;
--               the outcome group for an outcome) must be live, and its version
--               created in this same transaction (version created_at = now()).
-- Parameters  : none (trigger).
-- Return Values: trigger - the modified NEW row.
-- Notes       : DEPENDENCY: relies on h_system.tf_set_audit_fields stamping
--               created_at := now() (transaction time) on INSERT and keeping it on
--               UPDATE. If that ever used clock_timestamp() or let callers supply
--               created_at, every content insert would be refused or this guard
--               would be bypassed. So a version's contents can only be written
--               together with it; any later change is a new version. Lineage of a
--               version or content row = the drills / drill_sets /
--               practice_templates row it belongs to (an outcome through group ->
--               version -> drill). h_data.fn_soft_delete marks the lineage first
--               and h_data.fn_restore clears it first, then versions, then
--               contents (outcome groups before outcomes).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.tf_version_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_changed           boolean := false;
    v_deleted           timestamptz;
    v_retired           timestamptz;
    v_created           timestamptz;
    v_needs_seconds     boolean;
    v_lineage_deleted   timestamptz;
    v_parent_deleted    timestamptz;
    v_is_content        boolean;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF TG_TABLE_NAME = 'drill_versions' THEN
            v_changed := ROW(NEW.drill_id, NEW.version_no, NEW.result_type_code, NEW.symmetry_type_code, NEW.default_target,
                             NEW.fixed_seconds, NEW.value_1_unit, NEW.value_1_higher_is_better, NEW.value_2_unit, NEW.value_2_higher_is_better)
                IS DISTINCT FROM ROW(OLD.drill_id, OLD.version_no, OLD.result_type_code, OLD.symmetry_type_code, OLD.default_target,
                             OLD.fixed_seconds, OLD.value_1_unit, OLD.value_1_higher_is_better, OLD.value_2_unit, OLD.value_2_higher_is_better);
        ELSIF TG_TABLE_NAME = 'drill_spots' THEN
            v_changed := ROW(NEW.drill_version_id, NEW.spot_order, NEW.court_spot_id, NEW.is_free_throw, NEW.attempts_target)
                IS DISTINCT FROM ROW(OLD.drill_version_id, OLD.spot_order, OLD.court_spot_id, OLD.is_free_throw, OLD.attempts_target);
        ELSIF TG_TABLE_NAME = 'drill_outcome_groups' THEN
            v_changed := ROW(NEW.drill_version_id, NEW.group_no) IS DISTINCT FROM ROW(OLD.drill_version_id, OLD.group_no);
        ELSIF TG_TABLE_NAME = 'drill_outcomes' THEN
            v_changed := ROW(NEW.outcome_group_id, NEW.outcome_no, NEW.is_success)
                IS DISTINCT FROM ROW(OLD.outcome_group_id, OLD.outcome_no, OLD.is_success);
        ELSIF TG_TABLE_NAME = 'drill_set_versions' THEN
            v_changed := ROW(NEW.drill_set_id, NEW.version_no, NEW.set_kind) IS DISTINCT FROM ROW(OLD.drill_set_id, OLD.version_no, OLD.set_kind);
        ELSIF TG_TABLE_NAME = 'drill_set_items' THEN
            v_changed := ROW(NEW.drill_set_version_id, NEW.item_position, NEW.drill_id)
                IS DISTINCT FROM ROW(OLD.drill_set_version_id, OLD.item_position, OLD.drill_id);
        ELSIF TG_TABLE_NAME = 'drill_set_lines' THEN
            v_changed := ROW(NEW.drill_set_version_id, NEW.line_no, NEW.drill_count, NEW.difficulty_code, NEW.drill_type_id)
                IS DISTINCT FROM ROW(OLD.drill_set_version_id, OLD.line_no, OLD.drill_count, OLD.difficulty_code, OLD.drill_type_id);
        ELSIF TG_TABLE_NAME = 'practice_template_versions' THEN
            v_changed := ROW(NEW.practice_template_id, NEW.version_no) IS DISTINCT FROM ROW(OLD.practice_template_id, OLD.version_no);
        ELSIF TG_TABLE_NAME = 'practice_template_sets' THEN
            v_changed := ROW(NEW.practice_template_version_id, NEW.template_position, NEW.drill_set_id)
                IS DISTINCT FROM ROW(OLD.practice_template_version_id, OLD.template_position, OLD.drill_set_id);
        END IF;
        IF v_changed THEN
            RAISE EXCEPTION 'A version cannot change what it records (%); create a new version instead (REQ-080)', TG_TABLE_NAME;
        END IF;
        IF NEW.deleted_at IS NOT DISTINCT FROM OLD.deleted_at THEN
            RETURN NEW;
        END IF;

        -- Rule V1: deleted_at moves only together with the lineage.
        v_is_content := TG_TABLE_NAME NOT IN ('drill_versions', 'drill_set_versions', 'practice_template_versions');
        IF TG_TABLE_NAME = 'drill_versions' THEN
            SELECT d.deleted_at, d.deleted_at
            INTO v_lineage_deleted, v_parent_deleted
            FROM h_data.drills d
            WHERE d.drill_id = NEW.drill_id;
        ELSIF TG_TABLE_NAME IN ('drill_spots', 'drill_outcome_groups') THEN
            SELECT d.deleted_at, v.deleted_at, v.created_at
            INTO v_lineage_deleted, v_parent_deleted, v_created
            FROM h_data.drill_versions v
            JOIN h_data.drills d ON d.drill_id = v.drill_id
            WHERE v.drill_version_id = NEW.drill_version_id;
        ELSIF TG_TABLE_NAME = 'drill_outcomes' THEN
            SELECT d.deleted_at, g.deleted_at, v.created_at
            INTO v_lineage_deleted, v_parent_deleted, v_created
            FROM h_data.drill_outcome_groups g
            JOIN h_data.drill_versions v ON v.drill_version_id = g.drill_version_id
            JOIN h_data.drills d ON d.drill_id = v.drill_id
            WHERE g.outcome_group_id = NEW.outcome_group_id;
        ELSIF TG_TABLE_NAME = 'drill_set_versions' THEN
            SELECT s.deleted_at, s.deleted_at
            INTO v_lineage_deleted, v_parent_deleted
            FROM h_data.drill_sets s
            WHERE s.drill_set_id = NEW.drill_set_id;
        ELSIF TG_TABLE_NAME IN ('drill_set_items', 'drill_set_lines') THEN
            SELECT s.deleted_at, v.deleted_at, v.created_at
            INTO v_lineage_deleted, v_parent_deleted, v_created
            FROM h_data.drill_set_versions v
            JOIN h_data.drill_sets s ON s.drill_set_id = v.drill_set_id
            WHERE v.drill_set_version_id = NEW.drill_set_version_id;
        ELSIF TG_TABLE_NAME = 'practice_template_versions' THEN
            SELECT t.deleted_at, t.deleted_at
            INTO v_lineage_deleted, v_parent_deleted
            FROM h_data.practice_templates t
            WHERE t.practice_template_id = NEW.practice_template_id;
        ELSE
            SELECT t.deleted_at, v.deleted_at, v.created_at
            INTO v_lineage_deleted, v_parent_deleted, v_created
            FROM h_data.practice_template_versions v
            JOIN h_data.practice_templates t ON t.practice_template_id = v.practice_template_id
            WHERE v.practice_template_version_id = NEW.practice_template_version_id;
        END IF;

        IF FOUND THEN
            -- Contents during creation: free inside the version's creating transaction.
            IF v_is_content AND v_created = now() THEN
                RETURN NEW;
            END IF;
            -- Deleted with the lineage (lineage marked first, same timestamp).
            IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
               AND v_lineage_deleted IS NOT NULL AND NEW.deleted_at = v_lineage_deleted THEN
                RETURN NEW;
            END IF;
            -- Restored with the lineage (lineage and direct parent cleared first).
            -- A content row removed inside its version's creating transaction
            -- carries that transaction's time and is never brought back.
            IF OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL
               AND v_lineage_deleted IS NULL AND v_parent_deleted IS NULL
               AND (NOT v_is_content OR OLD.deleted_at <> v_created) THEN
                RETURN NEW;
            END IF;
        END IF;
        RAISE EXCEPTION 'A version and its contents can be removed or restored only together with the whole drill / drill set / practice template; to change what is recorded, create a new version (REQ-080)';
    END IF;

    IF TG_TABLE_NAME = 'drill_versions' THEN
        SELECT d.deleted_at, d.retired_at
        INTO v_deleted, v_retired
        FROM h_data.drills d
        WHERE d.drill_id = NEW.drill_id
        FOR UPDATE;
        IF NOT FOUND THEN
            RETURN NEW;
        END IF;
        IF v_deleted IS NOT NULL OR v_retired IS NOT NULL THEN
            RAISE EXCEPTION 'Drill % is deleted or retired and cannot get a new version', NEW.drill_id;
        END IF;
        SELECT COALESCE(max(v.version_no), 0) + 1
        INTO NEW.version_no
        FROM h_data.drill_versions v
        WHERE v.drill_id = NEW.drill_id;
        SELECT rt.needs_fixed_seconds
        INTO v_needs_seconds
        FROM h_ref.result_types rt
        WHERE rt.result_type_code = NEW.result_type_code;
        IF FOUND THEN
            IF v_needs_seconds <> (NEW.fixed_seconds IS NOT NULL) THEN
                RAISE EXCEPTION 'A fixed time in seconds is % for result type %',
                    CASE WHEN v_needs_seconds THEN 'required' ELSE 'not allowed' END, NEW.result_type_code;
            END IF;
            IF NEW.result_type_code = 'physical' THEN
                IF NEW.value_1_label IS NULL THEN
                    RAISE EXCEPTION 'A physical-measurement drill needs at least one named number with a unit and a better direction';
                END IF;
                IF NEW.default_target IS NOT NULL THEN
                    RAISE EXCEPTION 'A physical-measurement drill has no default target';
                END IF;
            ELSIF NEW.value_1_label IS NOT NULL THEN
                RAISE EXCEPTION 'Only a physical-measurement drill names its numbers; result type % fixes them', NEW.result_type_code;
            END IF;
        END IF;
        RETURN NEW;
    ELSIF TG_TABLE_NAME = 'drill_set_versions' THEN
        SELECT s.deleted_at, s.retired_at
        INTO v_deleted, v_retired
        FROM h_data.drill_sets s
        WHERE s.drill_set_id = NEW.drill_set_id
        FOR UPDATE;
        IF NOT FOUND THEN
            RETURN NEW;
        END IF;
        IF v_deleted IS NOT NULL OR v_retired IS NOT NULL THEN
            RAISE EXCEPTION 'Drill set % is deleted or retired and cannot get a new version', NEW.drill_set_id;
        END IF;
        SELECT COALESCE(max(v.version_no), 0) + 1
        INTO NEW.version_no
        FROM h_data.drill_set_versions v
        WHERE v.drill_set_id = NEW.drill_set_id;
        RETURN NEW;
    ELSIF TG_TABLE_NAME = 'practice_template_versions' THEN
        SELECT t.deleted_at, t.retired_at
        INTO v_deleted, v_retired
        FROM h_data.practice_templates t
        WHERE t.practice_template_id = NEW.practice_template_id
        FOR UPDATE;
        IF NOT FOUND THEN
            RETURN NEW;
        END IF;
        IF v_deleted IS NOT NULL OR v_retired IS NOT NULL THEN
            RAISE EXCEPTION 'Practice template % is deleted or retired and cannot get a new version', NEW.practice_template_id;
        END IF;
        SELECT COALESCE(max(v.version_no), 0) + 1
        INTO NEW.version_no
        FROM h_data.practice_template_versions v
        WHERE v.practice_template_id = NEW.practice_template_id;
        RETURN NEW;
    END IF;

    -- A content row (rule V2): its direct parent must be live (the outcome group
    -- for an outcome, otherwise the version), and its version created in this
    -- transaction.
    IF TG_TABLE_NAME IN ('drill_spots', 'drill_outcome_groups') THEN
        SELECT v.created_at, v.deleted_at
        INTO v_created, v_deleted
        FROM h_data.drill_versions v
        WHERE v.drill_version_id = NEW.drill_version_id;
    ELSIF TG_TABLE_NAME = 'drill_outcomes' THEN
        SELECT v.created_at, COALESCE(g.deleted_at, v.deleted_at)
        INTO v_created, v_deleted
        FROM h_data.drill_outcome_groups g
        JOIN h_data.drill_versions v ON v.drill_version_id = g.drill_version_id
        WHERE g.outcome_group_id = NEW.outcome_group_id;
    ELSIF TG_TABLE_NAME IN ('drill_set_items', 'drill_set_lines') THEN
        SELECT v.created_at, v.deleted_at
        INTO v_created, v_deleted
        FROM h_data.drill_set_versions v
        WHERE v.drill_set_version_id = NEW.drill_set_version_id;
    ELSE
        SELECT v.created_at, v.deleted_at
        INTO v_created, v_deleted
        FROM h_data.practice_template_versions v
        WHERE v.practice_template_version_id = NEW.practice_template_version_id;
    END IF;
    IF NOT FOUND THEN
        RETURN NEW;  -- the foreign key reports the missing version
    END IF;
    IF v_deleted IS NOT NULL THEN
        RAISE EXCEPTION 'Rows of % cannot be added under a deleted %; create a new version instead (REQ-080)',
            TG_TABLE_NAME, CASE WHEN TG_TABLE_NAME = 'drill_outcomes' THEN 'outcome group or version' ELSE 'version' END;
    END IF;
    IF v_created <> now() THEN
        RAISE EXCEPTION 'Rows of % can be added only in the transaction that creates their version; create a new version instead (REQ-080)', TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END
$$;
COMMENT ON FUNCTION h_data.tf_version_guard() IS
    'REQ-080, COD-22-02: version numbers per lineage; versions and their contents are immutable except labels; deleted_at moves only together with the lineage (deleted with it, restored with it) or, for contents, inside the creating transaction; no content row under a deleted version or outcome group; contents only in the creating transaction (parent created_at = now()). Depends on h_system.tf_set_audit_fields stamping created_at with now() on INSERT and keeping it on UPDATE.';

-- -----------------------------------------------------------------------------
-- h_data.tf_version_complete (REQ-077, REQ-080)
-- Description : Deferred constraint trigger, AFTER INSERT on drill_versions,
--               drill_set_versions and practice_template_versions; runs at COMMIT.
--               Drill version: shooting <=> at least one live spot; outcome
--               buttons <=> at least one live outcome group, and every live group
--               has at least 2 live outcomes (maxima of 3 groups and 6 outcomes
--               are CHECK constraints). Set version: manual_fixed needs at least
--               one item; quick_fixed and dynamic need at least one line. Template
--               version: at least one set.
-- Parameters  : none (trigger).
-- Return Values: trigger - NULL (AFTER trigger).
-- Notes       : An error here appears at COMMIT; screens should validate first
--               for a friendly message (plan Risk R3).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.tf_version_complete()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_a integer;
    v_b integer;
    v_c integer;
BEGIN
    IF TG_TABLE_NAME = 'drill_versions' THEN
        SELECT count(*) INTO v_a
        FROM h_data.drill_spots s
        WHERE s.drill_version_id = NEW.drill_version_id AND s.deleted_at IS NULL;
        SELECT count(*) INTO v_b
        FROM h_data.drill_outcome_groups g
        WHERE g.drill_version_id = NEW.drill_version_id AND g.deleted_at IS NULL;
        SELECT count(*) INTO v_c
        FROM h_data.drill_outcome_groups g
        WHERE g.drill_version_id = NEW.drill_version_id AND g.deleted_at IS NULL
          AND (SELECT count(*) FROM h_data.drill_outcomes o
               WHERE o.outcome_group_id = g.outcome_group_id AND o.deleted_at IS NULL) < 2;
        IF (NEW.result_type_code = 'shooting') <> (v_a > 0) THEN
            RAISE EXCEPTION 'Drill version %: a shooting drill needs at least one spot, and only a shooting drill has spots', NEW.drill_version_id;
        END IF;
        IF (NEW.result_type_code = 'outcome_buttons') <> (v_b > 0) OR v_c > 0 THEN
            RAISE EXCEPTION 'Drill version %: an outcome-button drill needs 1 to 3 outcome groups of 2 to 6 buttons, and only it has groups', NEW.drill_version_id;
        END IF;
    ELSIF TG_TABLE_NAME = 'drill_set_versions' THEN
        SELECT count(*) INTO v_a
        FROM h_data.drill_set_items i
        WHERE i.drill_set_version_id = NEW.drill_set_version_id AND i.deleted_at IS NULL;
        SELECT count(*) INTO v_b
        FROM h_data.drill_set_lines l
        WHERE l.drill_set_version_id = NEW.drill_set_version_id AND l.deleted_at IS NULL;
        IF NEW.set_kind = 'manual_fixed' AND v_a = 0 THEN
            RAISE EXCEPTION 'Drill set version %: a manual fixed set needs at least one drill', NEW.drill_set_version_id;
        ELSIF NEW.set_kind IN ('quick_fixed', 'dynamic') AND v_b = 0 THEN
            RAISE EXCEPTION 'Drill set version %: a % set needs at least one draw line', NEW.drill_set_version_id, NEW.set_kind;
        END IF;
    ELSE
        SELECT count(*) INTO v_a
        FROM h_data.practice_template_sets ts
        WHERE ts.practice_template_version_id = NEW.practice_template_version_id AND ts.deleted_at IS NULL;
        IF v_a = 0 THEN
            RAISE EXCEPTION 'Practice template version %: a template needs at least one drill set', NEW.practice_template_version_id;
        END IF;
    END IF;
    RETURN NULL;
END
$$;
COMMENT ON FUNCTION h_data.tf_version_complete() IS
    'REQ-077/080: deferred check at commit that a new drill, set or template version is complete.';

-- -----------------------------------------------------------------------------
-- h_data.tf_drill_set_child_kind (REQ-051 to REQ-054)
-- Description : BEFORE INSERT OR UPDATE on drill_set_items and drill_set_lines.
--               Items need a manual_fixed or quick_fixed set version; lines need a
--               quick_fixed or dynamic set version.
-- Parameters  : none (trigger).
-- Return Values: trigger - NEW unchanged.
-- Notes       : On UPDATE it checks only when drill_set_version_id changes (which
--               tf_version_guard refuses anyway).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.tf_drill_set_child_kind()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_kind text;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.drill_set_version_id IS NOT DISTINCT FROM OLD.drill_set_version_id THEN
        RETURN NEW;
    END IF;

    SELECT v.set_kind
    INTO v_kind
    FROM h_data.drill_set_versions v
    WHERE v.drill_set_version_id = NEW.drill_set_version_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- the foreign key reports the missing set version
    END IF;

    IF TG_TABLE_NAME = 'drill_set_items' AND v_kind NOT IN ('manual_fixed', 'quick_fixed') THEN
        RAISE EXCEPTION 'Only fixed drill sets have a drill list; this set is %', v_kind;
    ELSIF TG_TABLE_NAME = 'drill_set_lines' AND v_kind NOT IN ('quick_fixed', 'dynamic') THEN
        RAISE EXCEPTION 'Only quick fixed and dynamic drill sets have draw lines; this set is %', v_kind;
    END IF;
    RETURN NEW;
END
$$;
COMMENT ON FUNCTION h_data.tf_drill_set_child_kind() IS
    'Items only for fixed set versions; draw lines only for quick fixed and dynamic set versions.';

-- Audit triggers (all 28 tables)
CREATE TRIGGER trg_players_audit BEFORE INSERT OR UPDATE ON h_data.players
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_seasons_audit BEFORE INSERT OR UPDATE ON h_data.seasons
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_games_audit BEFORE INSERT OR UPDATE ON h_data.games
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_drill_types_audit BEFORE INSERT OR UPDATE ON h_data.drill_types
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_drills_audit BEFORE INSERT OR UPDATE ON h_data.drills
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_drill_versions_audit BEFORE INSERT OR UPDATE ON h_data.drill_versions
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_drill_secondary_types_audit BEFORE INSERT OR UPDATE ON h_data.drill_secondary_types
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_drill_spots_audit BEFORE INSERT OR UPDATE ON h_data.drill_spots
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_drill_outcome_groups_audit BEFORE INSERT OR UPDATE ON h_data.drill_outcome_groups
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_drill_outcomes_audit BEFORE INSERT OR UPDATE ON h_data.drill_outcomes
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_drill_sets_audit BEFORE INSERT OR UPDATE ON h_data.drill_sets
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_drill_set_versions_audit BEFORE INSERT OR UPDATE ON h_data.drill_set_versions
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_drill_set_items_audit BEFORE INSERT OR UPDATE ON h_data.drill_set_items
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_drill_set_lines_audit BEFORE INSERT OR UPDATE ON h_data.drill_set_lines
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_practice_templates_audit BEFORE INSERT OR UPDATE ON h_data.practice_templates
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_practice_template_versions_audit BEFORE INSERT OR UPDATE ON h_data.practice_template_versions
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_practice_template_sets_audit BEFORE INSERT OR UPDATE ON h_data.practice_template_sets
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_practice_sessions_audit BEFORE INSERT OR UPDATE ON h_data.practice_sessions
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_practice_players_audit BEFORE INSERT OR UPDATE ON h_data.practice_players
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_practice_drill_sets_audit BEFORE INSERT OR UPDATE ON h_data.practice_drill_sets
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_practice_drills_audit BEFORE INSERT OR UPDATE ON h_data.practice_drills
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_drill_results_audit BEFORE INSERT OR UPDATE ON h_data.drill_results
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_drill_attempts_audit BEFORE INSERT OR UPDATE ON h_data.drill_attempts
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_drill_attempt_outcomes_audit BEFORE INSERT OR UPDATE ON h_data.drill_attempt_outcomes
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_shot_entries_audit BEFORE INSERT OR UPDATE ON h_data.shot_entries
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_free_throw_entries_audit BEFORE INSERT OR UPDATE ON h_data.free_throw_entries
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_goals_audit BEFORE INSERT OR UPDATE ON h_data.goals
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();
CREATE TRIGGER trg_physical_measurements_audit BEFORE INSERT OR UPDATE ON h_data.physical_measurements
    FOR EACH ROW EXECUTE FUNCTION h_system.tf_set_audit_fields();

-- Domain triggers (24)
CREATE TRIGGER trg_games_season BEFORE INSERT OR UPDATE ON h_data.games
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_assign_season();
CREATE TRIGGER trg_practice_players_season BEFORE INSERT OR UPDATE ON h_data.practice_players
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_assign_season();
CREATE TRIGGER trg_practice_sessions_redate AFTER UPDATE ON h_data.practice_sessions
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_practice_sessions_redate();
CREATE TRIGGER trg_seasons_no_overlap BEFORE INSERT OR UPDATE ON h_data.seasons
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_seasons_no_overlap();
CREATE TRIGGER trg_seasons_reassign AFTER INSERT OR UPDATE ON h_data.seasons
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_seasons_reassign();
CREATE TRIGGER trg_shot_entries_entry BEFORE INSERT OR UPDATE ON h_data.shot_entries
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_entry_before_write();
CREATE TRIGGER trg_free_throw_entries_entry BEFORE INSERT OR UPDATE ON h_data.free_throw_entries
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_entry_before_write();
CREATE TRIGGER trg_drill_results_validate BEFORE INSERT OR UPDATE ON h_data.drill_results
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_drill_results_validate();
CREATE TRIGGER trg_drill_attempts_before_write BEFORE INSERT OR UPDATE ON h_data.drill_attempts
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_drill_attempts_before_write();
CREATE TRIGGER trg_drill_attempt_outcomes_validate BEFORE INSERT OR UPDATE ON h_data.drill_attempt_outcomes
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_drill_attempt_outcomes_validate();
CREATE TRIGGER trg_drill_set_items_kind BEFORE INSERT OR UPDATE ON h_data.drill_set_items
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_drill_set_child_kind();
CREATE TRIGGER trg_drill_set_lines_kind BEFORE INSERT OR UPDATE ON h_data.drill_set_lines
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_drill_set_child_kind();

-- Version guard (REQ-080): 9 triggers
CREATE TRIGGER trg_drill_versions_version_guard BEFORE INSERT OR UPDATE ON h_data.drill_versions
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_version_guard();
CREATE TRIGGER trg_drill_spots_version_guard BEFORE INSERT OR UPDATE ON h_data.drill_spots
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_version_guard();
CREATE TRIGGER trg_drill_outcome_groups_version_guard BEFORE INSERT OR UPDATE ON h_data.drill_outcome_groups
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_version_guard();
CREATE TRIGGER trg_drill_outcomes_version_guard BEFORE INSERT OR UPDATE ON h_data.drill_outcomes
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_version_guard();
CREATE TRIGGER trg_drill_set_versions_version_guard BEFORE INSERT OR UPDATE ON h_data.drill_set_versions
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_version_guard();
CREATE TRIGGER trg_drill_set_items_version_guard BEFORE INSERT OR UPDATE ON h_data.drill_set_items
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_version_guard();
CREATE TRIGGER trg_drill_set_lines_version_guard BEFORE INSERT OR UPDATE ON h_data.drill_set_lines
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_version_guard();
CREATE TRIGGER trg_practice_template_versions_version_guard BEFORE INSERT OR UPDATE ON h_data.practice_template_versions
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_version_guard();
CREATE TRIGGER trg_practice_template_sets_version_guard BEFORE INSERT OR UPDATE ON h_data.practice_template_sets
    FOR EACH ROW EXECUTE FUNCTION h_data.tf_version_guard();

-- Version completeness (REQ-077, REQ-080): 3 deferred constraint triggers
CREATE CONSTRAINT TRIGGER trg_drill_versions_complete AFTER INSERT ON h_data.drill_versions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION h_data.tf_version_complete();
CREATE CONSTRAINT TRIGGER trg_drill_set_versions_complete AFTER INSERT ON h_data.drill_set_versions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION h_data.tf_version_complete();
CREATE CONSTRAINT TRIGGER trg_practice_template_versions_complete AFTER INSERT ON h_data.practice_template_versions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION h_data.tf_version_complete();


-- =============================================================================
-- Part B2: functions (plan 6.9)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- h_data.fn_season_for_date (REQ-018)
-- Description : The non-deleted season of the player that covers the date.
-- Parameters  : p_player_id - h_data.players.player_id.
--               p_date      - the event date.
-- Return Values: bigint - season_id, or NULL when no season covers the date.
-- Notes       : If several cover it (only possible if overlaps were allowed), the
--               latest start_date wins.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.fn_season_for_date(p_player_id bigint, p_date date)
RETURNS bigint
LANGUAGE sql
STABLE
AS $$
    SELECT s.season_id
    FROM h_data.seasons s
    WHERE s.player_id = p_player_id
      AND s.deleted_at IS NULL
      AND s.start_date <= p_date
      AND s.end_date >= p_date
    ORDER BY s.start_date DESC, s.season_id DESC
    LIMIT 1
$$;
COMMENT ON FUNCTION h_data.fn_season_for_date(bigint, date) IS
    'REQ-018: covering non-deleted season of a player for a date, or NULL.';

-- -----------------------------------------------------------------------------
-- h_data.fn_split_target (REQ-031, plan 6.6 rule 2)
-- Description : Splits a total evenly over parts: each gets floor(total / parts)
--               and the first (total mod parts) parts get one more.
-- Parameters  : p_total - total attempts to split.
--               p_parts - number of parts.
-- Return Values: one row per part: part_no (1..p_parts), part_target. Empty when
--               p_parts < 1. part_target is NULL when p_total is NULL or <= 0
--               (rule 3: no auto-advance).
-- Notes       : Example: (50, 3) gives 17, 17, 16. Used by the app when the user
--               logs at spots other than the defaults.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.fn_split_target(p_total integer, p_parts integer)
RETURNS TABLE (part_no integer, part_target integer)
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT gs.n::integer,
           (CASE
                WHEN p_total IS NULL OR p_total <= 0 THEN NULL
                ELSE p_total / NULLIF(p_parts, 0)
                     + CASE WHEN gs.n <= p_total % NULLIF(p_parts, 0) THEN 1 ELSE 0 END
            END)::integer
    FROM generate_series(1, p_parts) AS gs(n)
    WHERE p_parts >= 1
    ORDER BY gs.n
$$;
COMMENT ON FUNCTION h_data.fn_split_target(integer, integer) IS
    'Even split of a total over parts, remainder to the first parts (plan 6.6).';

-- -----------------------------------------------------------------------------
-- h_data.fn_drill_spot_targets (REQ-031, REQ-080, plan 6.6)
-- Description : Attempts target of each non-deleted spot of a drill version's
--               spots. An explicit attempts_target wins; the remainder of the
--               version's default_target is split evenly over the other spots in
--               spot_order.
-- Parameters  : p_drill_version_id - h_data.drill_versions.drill_version_id
--               (a version id, never a drill lineage id).
-- Return Values: one row per spot: spot_order, court_spot_id, spot_number (NULL for
--               the free-throw line), is_free_throw, attempts_target (NULL = no
--               auto-advance: no default target, or nothing left to split).
-- Notes       : Example: version 1 of drill "Around the World Mid-Range" (25 over
--               5 spots) gives five rows of 5.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.fn_drill_spot_targets(p_drill_version_id bigint)
RETURNS TABLE (spot_order smallint, court_spot_id bigint, spot_number smallint,
               is_free_throw boolean, attempts_target integer)
LANGUAGE sql
STABLE
AS $$
    WITH spots AS (
        SELECT s.spot_order AS s_order,
               s.court_spot_id AS s_court_spot_id,
               cs.spot_number AS s_spot_number,
               s.is_free_throw AS s_is_free_throw,
               s.attempts_target AS s_target,
               row_number() OVER (PARTITION BY (s.attempts_target IS NULL)
                                  ORDER BY s.spot_order, s.drill_spot_id) AS s_open_rank
        FROM h_data.drill_spots s
        LEFT JOIN h_ref.court_spots cs ON cs.court_spot_id = s.court_spot_id
        WHERE s.drill_version_id = p_drill_version_id
          AND s.deleted_at IS NULL
    ),
    totals AS (
        SELECT v.default_target - COALESCE((SELECT sum(sp.s_target) FROM spots sp), 0) AS remaining,
               (SELECT count(*) FROM spots sp WHERE sp.s_target IS NULL) AS open_count
        FROM h_data.drill_versions v
        WHERE v.drill_version_id = p_drill_version_id
    )
    SELECT sp.s_order,
           sp.s_court_spot_id,
           sp.s_spot_number,
           sp.s_is_free_throw,
           (CASE
                WHEN sp.s_target IS NOT NULL THEN sp.s_target
                WHEN t.remaining IS NULL OR t.remaining <= 0 THEN NULL
                ELSE t.remaining / t.open_count
                     + CASE WHEN sp.s_open_rank <= t.remaining % t.open_count THEN 1 ELSE 0 END
            END)::integer
    FROM spots sp
    CROSS JOIN totals t
    ORDER BY sp.s_order
$$;
COMMENT ON FUNCTION h_data.fn_drill_spot_targets(bigint) IS
    'REQ-031/080: per-spot attempts targets of a drill version''s spots (explicit override, else even split).';

-- -----------------------------------------------------------------------------
-- h_data.fn_draw_line (REQ-052 to REQ-054, REQ-081) - internal helper
-- Description : Draws up to p_drill_count random drills - live, not retired, with
--               a current version (h_data.v_current_drills) - of a difficulty and,
--               when given, a primary drill type (Gate 1, Q2: primary type only),
--               excluding drills already chosen in this draw.
-- Parameters  : p_line_no           - line number, for the notice.
--               p_drill_count       - drills requested.
--               p_difficulty_code   - h_ref.difficulty_levels code.
--               p_drill_type_id     - h_data.drill_types id, or NULL for any type.
--               p_exclude_drill_ids - drill lineage ids to exclude (may be empty
--                                     or NULL).
-- Return Values: drill_ids (drill lineage ids, in draw order, possibly fewer than
--               requested) and notice ('Line <n> asked for <k> <Difficulty>[ <Type>]
--               drills; only <m> available.' when short, else NULL).
-- Notes       : VOLATILE (random()). Never blocks a practice: a short draw returns
--               what exists. Callers resolve each id to its current version.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.fn_draw_line(
    p_line_no           smallint,
    p_drill_count       smallint,
    p_difficulty_code   text,
    p_drill_type_id     bigint,
    p_exclude_drill_ids bigint[],
    OUT drill_ids       bigint[],
    OUT notice          text)
LANGUAGE plpgsql
VOLATILE
AS $$
DECLARE
    v_difficulty_label  text;
    v_type_name         text;
BEGIN
    SELECT COALESCE(array_agg(c.candidate_id ORDER BY c.draw_key), '{}'::bigint[])
    INTO drill_ids
    FROM (
        SELECT d.drill_id AS candidate_id, random() AS draw_key
        FROM h_data.v_current_drills d
        WHERE d.difficulty_code = p_difficulty_code
          AND (p_drill_type_id IS NULL OR d.primary_drill_type_id = p_drill_type_id)
          AND d.drill_id <> ALL (COALESCE(p_exclude_drill_ids, '{}'::bigint[]))
        ORDER BY 2
        LIMIT p_drill_count
    ) c;

    notice := NULL;
    IF cardinality(drill_ids) < p_drill_count THEN
        SELECT dl.label
        INTO v_difficulty_label
        FROM h_ref.difficulty_levels dl
        WHERE dl.difficulty_code = p_difficulty_code;
        IF p_drill_type_id IS NOT NULL THEN
            SELECT dt.type_name
            INTO v_type_name
            FROM h_data.drill_types dt
            WHERE dt.drill_type_id = p_drill_type_id;
        END IF;
        notice := format('Line %s asked for %s %s%s drills; only %s available.',
                         p_line_no, p_drill_count,
                         COALESCE(v_difficulty_label, p_difficulty_code),
                         COALESCE(' ' || v_type_name, ''),
                         cardinality(drill_ids));
    END IF;
END
$$;
COMMENT ON FUNCTION h_data.fn_draw_line(smallint, smallint, text, bigint, bigint[]) IS
    'Internal draw helper: random current drills of a difficulty and optional primary type, no repeats, with a short-draw notice.';

-- -----------------------------------------------------------------------------
-- h_data.fn_draw_quick_fixed_set (REQ-054, REQ-080)
-- Description : Draws every line of a quick fixed set version once, in line_no
--               order, and stores the drawn drills (lineage ids) as the version's
--               items (positions 1..n in draw order). After this the version
--               behaves as a fixed set.
-- Parameters  : p_drill_set_version_id - h_data.drill_set_versions id of a
--               quick_fixed version (a version id, never a set lineage id).
-- Return Values: one row per line: line_no, requested, drawn, notice (NULL unless short).
-- Notes       : Raises unless the version is live, quick_fixed and has no live
--               items. No drill repeats within the draw. Must run in the
--               transaction that creates the version: tf_version_guard refuses
--               items added to an older version.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.fn_draw_quick_fixed_set(p_drill_set_version_id bigint)
RETURNS TABLE (line_no smallint, requested smallint, drawn smallint, notice text)
LANGUAGE plpgsql
AS $$
#variable_conflict use_column
DECLARE
    v_kind      text;
    v_line      record;
    v_ids       bigint[];
    v_notice    text;
    v_chosen    bigint[] := '{}';
    v_position  integer := 0;
BEGIN
    SELECT v.set_kind
    INTO v_kind
    FROM h_data.drill_set_versions v
    WHERE v.drill_set_version_id = p_drill_set_version_id
      AND v.deleted_at IS NULL
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Drill set version % does not exist or is deleted', p_drill_set_version_id;
    END IF;
    IF v_kind <> 'quick_fixed' THEN
        RAISE EXCEPTION 'Only a quick fixed drill set is drawn once; this set is %', v_kind;
    END IF;
    IF EXISTS (SELECT 1 FROM h_data.drill_set_items i
               WHERE i.drill_set_version_id = p_drill_set_version_id AND i.deleted_at IS NULL) THEN
        RAISE EXCEPTION 'This quick fixed drill set has already been drawn';
    END IF;

    FOR v_line IN
        SELECT l.line_no AS l_no, l.drill_count AS l_count,
               l.difficulty_code AS l_difficulty, l.drill_type_id AS l_type
        FROM h_data.drill_set_lines l
        WHERE l.drill_set_version_id = p_drill_set_version_id
          AND l.deleted_at IS NULL
        ORDER BY l.line_no
    LOOP
        SELECT dl.drill_ids, dl.notice
        INTO v_ids, v_notice
        FROM h_data.fn_draw_line(v_line.l_no, v_line.l_count, v_line.l_difficulty,
                                 v_line.l_type, v_chosen) dl;

        INSERT INTO h_data.drill_set_items (drill_set_version_id, item_position, drill_id)
        SELECT p_drill_set_version_id, (v_position + u.ord)::smallint, u.chosen_id
        FROM unnest(v_ids) WITH ORDINALITY AS u(chosen_id, ord);

        v_position := v_position + cardinality(v_ids);
        v_chosen := v_chosen || v_ids;

        line_no := v_line.l_no;
        requested := v_line.l_count;
        drawn := cardinality(v_ids)::smallint;
        notice := v_notice;
        RETURN NEXT;
    END LOOP;
END
$$;
COMMENT ON FUNCTION h_data.fn_draw_quick_fixed_set(bigint) IS
    'REQ-054: one-time draw of a quick fixed set version into its items, with short-draw notices.';

-- -----------------------------------------------------------------------------
-- h_data.fn_add_drill_set_to_practice (REQ-051, REQ-053, REQ-080, REQ-081)
-- Description : Places the current version of a drill set in a practice at the
--               next practice position (max + 1 over the practice's live sets and
--               ad-hoc drills) and records that version on the placement. Fixed
--               kinds: copies the version's live items in (item_position, id)
--               order as source 'fixed', each with the drill's current version; a
--               drill no longer in the library (deleted, retired or without a
--               live version) is left out with a notice, also kept in
--               draw_notice. Dynamic: draws every line fresh as source 'drawn'
--               with the line snapshot, and stores the joined notices in
--               draw_notice.
-- Parameters  : p_practice_session_id - h_data.practice_sessions id (live).
--               p_drill_set_id        - h_data.drill_sets id: the set LINEAGE
--                                       (live, not retired), never a version id.
-- Return Values: practice_drill_set_id plus, per line, line_no, requested, drawn,
--               notice. Fixed kinds return one row with line_no NULL, requested =
--               items in the version, drawn = drills copied, notice = left-out
--               drills (or NULL).
-- Notes       : Raises if the set is not current, a fixed version has no drills
--               (a quick fixed version's draw came up empty) or a dynamic version
--               has no lines. Locks the practice row so concurrent additions get
--               distinct positions.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.fn_add_drill_set_to_practice(p_practice_session_id bigint, p_drill_set_id bigint)
RETURNS TABLE (practice_drill_set_id bigint, line_no smallint, requested smallint, drawn smallint, notice text)
LANGUAGE plpgsql
AS $$
#variable_conflict use_column
DECLARE
    v_kind      text;
    v_version   bigint;
    v_items     integer;
    v_skipped   text;
    v_position  integer;
    v_pds_id    bigint;
    v_count     integer;
    v_line      record;
    v_ids       bigint[];
    v_notice    text;
    v_notices   text[] := '{}';
    v_chosen    bigint[] := '{}';
    v_set_pos   integer := 0;
BEGIN
    PERFORM 1
    FROM h_data.practice_sessions ps
    WHERE ps.practice_session_id = p_practice_session_id
      AND ps.deleted_at IS NULL
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Practice % does not exist or is deleted', p_practice_session_id;
    END IF;

    SELECT cs.drill_set_version_id, cs.set_kind
    INTO v_version, v_kind
    FROM h_data.v_current_drill_sets cs
    WHERE cs.drill_set_id = p_drill_set_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Drill set % does not exist, is deleted or is retired', p_drill_set_id;
    END IF;

    IF v_kind IN ('manual_fixed', 'quick_fixed') THEN
        SELECT count(*)
        INTO v_items
        FROM h_data.drill_set_items i
        WHERE i.drill_set_version_id = v_version
          AND i.deleted_at IS NULL;
        IF v_items = 0 THEN
            RAISE EXCEPTION 'This drill set has no drills yet%',
                CASE WHEN v_kind = 'quick_fixed' THEN '; its draw came up empty' ELSE '' END;
        END IF;
    END IF;
    IF v_kind = 'dynamic'
       AND NOT EXISTS (SELECT 1 FROM h_data.drill_set_lines l
                       WHERE l.drill_set_version_id = v_version AND l.deleted_at IS NULL) THEN
        RAISE EXCEPTION 'This dynamic drill set has no lines';
    END IF;

    SELECT COALESCE(GREATEST(
               (SELECT max(pds.practice_position) FROM h_data.practice_drill_sets pds
                WHERE pds.practice_session_id = p_practice_session_id AND pds.deleted_at IS NULL),
               (SELECT max(pd.practice_position) FROM h_data.practice_drills pd
                WHERE pd.practice_session_id = p_practice_session_id AND pd.deleted_at IS NULL)),
           0) + 1
    INTO v_position;

    INSERT INTO h_data.practice_drill_sets AS pds (practice_session_id, drill_set_version_id, practice_position)
    VALUES (p_practice_session_id, v_version, v_position::smallint)
    RETURNING pds.practice_drill_set_id INTO v_pds_id;

    IF v_kind IN ('manual_fixed', 'quick_fixed') THEN
        INSERT INTO h_data.practice_drills
            (practice_session_id, practice_drill_set_id, set_position, drill_version_id, source)
        SELECT p_practice_session_id, v_pds_id,
               (row_number() OVER (ORDER BY i.item_position, i.drill_set_item_id))::smallint,
               cd.drill_version_id, 'fixed'
        FROM h_data.drill_set_items i
        JOIN h_data.v_current_drills cd ON cd.drill_id = i.drill_id
        WHERE i.drill_set_version_id = v_version
          AND i.deleted_at IS NULL;
        GET DIAGNOSTICS v_count = ROW_COUNT;

        SELECT string_agg(format('Drill "%s" is no longer in the library and was left out.', d.drill_name),
                          ' ' ORDER BY i.item_position, i.drill_set_item_id)
        INTO v_skipped
        FROM h_data.drill_set_items i
        JOIN h_data.drills d ON d.drill_id = i.drill_id
        WHERE i.drill_set_version_id = v_version
          AND i.deleted_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM h_data.v_current_drills cd WHERE cd.drill_id = i.drill_id);
        IF v_skipped IS NOT NULL THEN
            UPDATE h_data.practice_drill_sets pds
            SET draw_notice = left(v_skipped, 4000)
            WHERE pds.practice_drill_set_id = v_pds_id;
        END IF;

        practice_drill_set_id := v_pds_id;
        line_no := NULL;
        requested := v_items::smallint;
        drawn := v_count::smallint;
        notice := v_skipped;
        RETURN NEXT;
        RETURN;
    END IF;

    FOR v_line IN
        SELECT l.line_no AS l_no, l.drill_count AS l_count,
               l.difficulty_code AS l_difficulty, l.drill_type_id AS l_type
        FROM h_data.drill_set_lines l
        WHERE l.drill_set_version_id = v_version
          AND l.deleted_at IS NULL
        ORDER BY l.line_no
    LOOP
        SELECT dl.drill_ids, dl.notice
        INTO v_ids, v_notice
        FROM h_data.fn_draw_line(v_line.l_no, v_line.l_count, v_line.l_difficulty,
                                 v_line.l_type, v_chosen) dl;

        INSERT INTO h_data.practice_drills
            (practice_session_id, practice_drill_set_id, set_position, drill_version_id, source,
             line_no, draw_difficulty_code, draw_drill_type_id)
        SELECT p_practice_session_id, v_pds_id, (v_set_pos + u.ord)::smallint, cd.drill_version_id, 'drawn',
               v_line.l_no, v_line.l_difficulty, v_line.l_type
        FROM unnest(v_ids) WITH ORDINALITY AS u(chosen_id, ord)
        JOIN h_data.v_current_drills cd ON cd.drill_id = u.chosen_id;

        v_set_pos := v_set_pos + cardinality(v_ids);
        v_chosen := v_chosen || v_ids;
        IF v_notice IS NOT NULL THEN
            v_notices := v_notices || v_notice;
        END IF;

        practice_drill_set_id := v_pds_id;
        line_no := v_line.l_no;
        requested := v_line.l_count;
        drawn := cardinality(v_ids)::smallint;
        notice := v_notice;
        RETURN NEXT;
    END LOOP;

    IF cardinality(v_notices) > 0 THEN
        UPDATE h_data.practice_drill_sets pds
        SET draw_notice = left(array_to_string(v_notices, ' '), 4000)
        WHERE pds.practice_drill_set_id = v_pds_id;
    END IF;
END
$$;
COMMENT ON FUNCTION h_data.fn_add_drill_set_to_practice(bigint, bigint) IS
    'REQ-051/053/080: places the current version of a drill set (by lineage id) in a practice; copies a fixed set with current drill versions (left-out drills noticed) or draws a dynamic one fresh.';

-- -----------------------------------------------------------------------------
-- h_data.fn_add_template_to_practice (REQ-072, REQ-080, REQ-081)
-- Description : Starts a practice from a practice template: records the
--               template's latest live version on the practice
--               (practice_template_version_id) and places that version's sets in
--               (template_position, id) order through
--               h_data.fn_add_drill_set_to_practice. A set no longer in the
--               library is skipped with a notice; a set that cannot be placed
--               (e.g. no drills) is added without drills, with the error as its
--               notice.
-- Parameters  : p_practice_session_id  - h_data.practice_sessions id (live).
--               p_practice_template_id - h_data.practice_templates id: the template
--                                        LINEAGE (live, not retired).
-- Return Values: one row per placed line or skipped set: template_position,
--               drill_set_id, set_name, practice_drill_set_id (NULL when skipped),
--               line_no, requested, drawn, notice.
-- Notes       : Locks the practice row. Raises when the practice is not live, was
--               already started from a template (at most once per practice), or
--               the template is not current.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.fn_add_template_to_practice(p_practice_session_id bigint, p_practice_template_id bigint)
RETURNS TABLE (template_position smallint, drill_set_id bigint, set_name text, practice_drill_set_id bigint,
               line_no smallint, requested smallint, drawn smallint, notice text)
LANGUAGE plpgsql
AS $$
#variable_conflict use_column
DECLARE
    v_current      bigint;
    v_version      bigint;
    v_set          record;
    v_placed       record;
    v_message      text;
    v_notice       text;
    v_position     integer;
    v_pds_id       bigint;
    v_set_version  bigint;
BEGIN
    SELECT ps.practice_template_version_id
    INTO v_current
    FROM h_data.practice_sessions ps
    WHERE ps.practice_session_id = p_practice_session_id AND ps.deleted_at IS NULL
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Practice % does not exist or is deleted', p_practice_session_id;
    END IF;
    IF v_current IS NOT NULL THEN
        RAISE EXCEPTION 'Practice % was already started from a template', p_practice_session_id;
    END IF;

    SELECT ct.practice_template_version_id
    INTO v_version
    FROM h_data.v_current_practice_templates ct
    WHERE ct.practice_template_id = p_practice_template_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Practice template % does not exist, is deleted or is retired', p_practice_template_id;
    END IF;
    UPDATE h_data.practice_sessions ps
    SET practice_template_version_id = v_version
    WHERE ps.practice_session_id = p_practice_session_id;

    FOR v_set IN
        SELECT ts.template_position AS s_position, ts.drill_set_id AS s_set_id, ds.set_name AS s_name,
               (cs.drill_set_id IS NOT NULL) AS s_current
        FROM h_data.practice_template_sets ts
        JOIN h_data.drill_sets ds ON ds.drill_set_id = ts.drill_set_id
        LEFT JOIN h_data.v_current_drill_sets cs ON cs.drill_set_id = ts.drill_set_id
        WHERE ts.practice_template_version_id = v_version AND ts.deleted_at IS NULL
        ORDER BY ts.template_position, ts.practice_template_set_id
    LOOP
        template_position := v_set.s_position;
        drill_set_id := v_set.s_set_id;
        set_name := v_set.s_name;
        IF NOT v_set.s_current THEN
            practice_drill_set_id := NULL; line_no := NULL; requested := 0; drawn := 0;
            notice := format('Drill set "%s" is no longer in the library and was skipped.', v_set.s_name);
            RETURN NEXT;
            CONTINUE;
        END IF;
        BEGIN
            FOR v_placed IN
                SELECT a.practice_drill_set_id AS a_pds_id, a.line_no AS a_line_no, a.requested AS a_requested,
                       a.drawn AS a_drawn, a.notice AS a_notice
                FROM h_data.fn_add_drill_set_to_practice(p_practice_session_id, v_set.s_set_id) a
            LOOP
                practice_drill_set_id := v_placed.a_pds_id; line_no := v_placed.a_line_no;
                requested := v_placed.a_requested; drawn := v_placed.a_drawn; notice := v_placed.a_notice;
                RETURN NEXT;
            END LOOP;
        EXCEPTION WHEN raise_exception THEN
            GET STACKED DIAGNOSTICS v_message = MESSAGE_TEXT;
            v_notice := left(format('Drill set "%s" was added without drills: %s.', v_set.s_name, v_message), 4000);
            SELECT cs.drill_set_version_id
            INTO v_set_version
            FROM h_data.v_current_drill_sets cs
            WHERE cs.drill_set_id = v_set.s_set_id;
            SELECT COALESCE(GREATEST(
                       (SELECT max(pds.practice_position) FROM h_data.practice_drill_sets pds
                        WHERE pds.practice_session_id = p_practice_session_id AND pds.deleted_at IS NULL),
                       (SELECT max(pd.practice_position) FROM h_data.practice_drills pd
                        WHERE pd.practice_session_id = p_practice_session_id AND pd.deleted_at IS NULL)), 0) + 1
            INTO v_position;
            INSERT INTO h_data.practice_drill_sets AS pds (practice_session_id, drill_set_version_id, practice_position, draw_notice)
            VALUES (p_practice_session_id, v_set_version, v_position::smallint, v_notice)
            RETURNING pds.practice_drill_set_id INTO v_pds_id;
            practice_drill_set_id := v_pds_id; line_no := NULL; requested := 0; drawn := 0; notice := v_notice;
            RETURN NEXT;
        END;
    END LOOP;
END
$$;
COMMENT ON FUNCTION h_data.fn_add_template_to_practice(bigint, bigint) IS
    'REQ-072/080: records the template''s latest version on the practice and places its current sets in order; a set no longer in the library is skipped, one that cannot be placed is added without drills, each with a notice.';

-- -----------------------------------------------------------------------------
-- h_data.fn_swap_practice_drill (REQ-053, REQ-082)
-- Description : Replaces a drawn drill with another random current drill matching
--               the line snapshot (difficulty and primary type), excluding every
--               drill (lineage) live in the same placed set, the current one
--               included. The slot's live results are soft-deleted first
--               (h_data.fn_soft_delete 'drill_result', recoverable), then the slot
--               gets the new drill's current version and source 'swapped'.
-- Parameters  : p_practice_drill_id - h_data.practice_drills id (source drawn or swapped).
-- Return Values: one row: practice_drill_id, old_drill_version_id,
--               new_drill_version_id (NULL when nothing was swapped),
--               results_removed (results soft-deleted), notice ('No other
--               matching drill is available.' when nothing matches, else NULL).
-- Notes       : REQ-082: never refused because of results; the app warns first.
--               Raises for non-drawn drills. When nothing matches, nothing
--               changes. A soft-deleted result cannot be restored onto the swapped
--               slot (fn_restore checks the version).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.fn_swap_practice_drill(p_practice_drill_id bigint)
RETURNS TABLE (practice_drill_id bigint, old_drill_version_id bigint, new_drill_version_id bigint,
               results_removed integer, notice text)
LANGUAGE plpgsql
AS $$
#variable_conflict use_column
DECLARE
    v_pd           record;
    v_exclude      bigint[];
    v_ids          bigint[];
    v_new_version  bigint;
    v_result       record;
    v_removed      integer := 0;
BEGIN
    SELECT pd.practice_drill_set_id AS pd_set_id, pd.drill_version_id AS pd_version, pd.source AS pd_source,
           pd.line_no AS pd_line_no, pd.draw_difficulty_code AS pd_difficulty, pd.draw_drill_type_id AS pd_type
    INTO v_pd
    FROM h_data.practice_drills pd
    WHERE pd.practice_drill_id = p_practice_drill_id AND pd.deleted_at IS NULL
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Practice drill % does not exist or is deleted', p_practice_drill_id;
    END IF;
    IF v_pd.pd_source NOT IN ('drawn', 'swapped') THEN
        RAISE EXCEPTION 'Only a drill drawn for a dynamic drill set can be swapped';
    END IF;

    SELECT COALESCE(array_agg(DISTINCT dv.drill_id), '{}'::bigint[])
    INTO v_exclude
    FROM h_data.practice_drills x
    JOIN h_data.drill_versions dv ON dv.drill_version_id = x.drill_version_id
    WHERE x.practice_drill_set_id = v_pd.pd_set_id AND x.deleted_at IS NULL;

    SELECT dl.drill_ids
    INTO v_ids
    FROM h_data.fn_draw_line(v_pd.pd_line_no, 1::smallint, v_pd.pd_difficulty, v_pd.pd_type, v_exclude) dl;

    practice_drill_id := p_practice_drill_id;
    old_drill_version_id := v_pd.pd_version;
    IF cardinality(v_ids) = 0 THEN
        new_drill_version_id := NULL; results_removed := 0; notice := 'No other matching drill is available.';
        RETURN NEXT;
        RETURN;
    END IF;

    -- REQ-082: no lock. The slot's live results are soft-deleted (recoverable); the app warns first.
    FOR v_result IN
        SELECT r.drill_result_id AS r_id
        FROM h_data.drill_results r
        WHERE r.practice_drill_id = p_practice_drill_id AND r.deleted_at IS NULL
    LOOP
        PERFORM h_data.fn_soft_delete('drill_result', v_result.r_id);
        v_removed := v_removed + 1;
    END LOOP;

    SELECT cd.drill_version_id
    INTO v_new_version
    FROM h_data.v_current_drills cd
    WHERE cd.drill_id = v_ids[1];
    UPDATE h_data.practice_drills pd
    SET drill_version_id = v_new_version,
        source = 'swapped'
    WHERE pd.practice_drill_id = p_practice_drill_id;

    new_drill_version_id := v_new_version; results_removed := v_removed; notice := NULL;
    RETURN NEXT;
END
$$;
COMMENT ON FUNCTION h_data.fn_swap_practice_drill(bigint) IS
    'REQ-053/082: swaps a drawn drill for another current matching drill; existing results are soft-deleted first (recoverable), never refused.';

-- -----------------------------------------------------------------------------
-- h_data.fn_practice_usage (REQ-081)
-- Description : Number of practices not yet purged (live or soft-deleted, i.e.
--               also those in Recently deleted) that use any version of a drill,
--               drill set or practice template ("used in N practices").
-- Parameters  : p_entity - 'drill', 'drill_set' or 'practice_template'.
--               p_id     - the LINEAGE id (drill_id, drill_set_id or
--                          practice_template_id).
-- Return Values: integer - the count; NULL for any other entity name.
-- Notes       : Read-only. Drills count through practice drills, sets through
--               placements, templates through the version recorded on the
--               practice; rows in any state count, because a soft-deleted
--               practice can still be restored (REQ-081, COD-22-03). A practice
--               counts once. Goals that target a drill are counted separately
--               (h_data.fn_remove_library_item, h_data.fn_soft_delete).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.fn_practice_usage(p_entity text, p_id bigint)
RETURNS integer
LANGUAGE sql
STABLE
AS $$
    SELECT CASE p_entity
        WHEN 'drill' THEN (
            SELECT count(DISTINCT pd.practice_session_id)::integer
            FROM h_data.practice_drills pd
            JOIN h_data.drill_versions dv ON dv.drill_version_id = pd.drill_version_id
            WHERE dv.drill_id = p_id)
        WHEN 'drill_set' THEN (
            SELECT count(DISTINCT pds.practice_session_id)::integer
            FROM h_data.practice_drill_sets pds
            JOIN h_data.drill_set_versions sv ON sv.drill_set_version_id = pds.drill_set_version_id
            WHERE sv.drill_set_id = p_id)
        WHEN 'practice_template' THEN (
            SELECT count(*)::integer
            FROM h_data.practice_sessions ps
            JOIN h_data.practice_template_versions tv ON tv.practice_template_version_id = ps.practice_template_version_id
            WHERE tv.practice_template_id = p_id)
    END
$$;
COMMENT ON FUNCTION h_data.fn_practice_usage(text, bigint) IS
    'REQ-081: number of practices not yet purged (live or in Recently deleted) using any version of a drill, drill set or practice template (NULL for another entity).';

-- -----------------------------------------------------------------------------
-- h_data.fn_remove_library_item (REQ-081)
-- Description : The only way the app removes a drill, drill set or practice
--               template from the library (REQ-081, Amendment A4). Use = practices
--               not yet purged (h_data.fn_practice_usage, including any in
--               Recently deleted) plus, for a drill, goals not yet purged that
--               target it. No use -> soft delete through h_data.fn_soft_delete
--               (lineage, versions, contents; also a retired item whose using
--               practices and goals have all been purged). Used and not retired
--               -> retire (retired_at = now()): hidden from selection, kept in
--               full in history. Used and already retired -> refused.
-- Parameters  : p_entity - 'drill', 'drill_set' or 'practice_template'.
--               p_id     - the LINEAGE id.
-- Return Values: one row: action ('deleted' or 'retired'), practice_count (the
--               h_data.fn_practice_usage count), goal_count (goals targeting
--               the drill; 0 for drill sets and templates).
-- Notes       : Locks the live lineage row. Raises for an unknown entity, when
--               the item does not exist or is deleted, and when it is already
--               retired and still in use. The app warns "used in N practices
--               (including any in Recently deleted) and M goals" first. Un-retire
--               is not offered.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.fn_remove_library_item(p_entity text, p_id bigint)
RETURNS TABLE (action text, practice_count integer, goal_count integer)
LANGUAGE plpgsql
AS $$
#variable_conflict use_column
DECLARE
    v_count    integer;
    v_goals    integer := 0;
    v_name     text;
    v_retired  timestamptz;
    v_label    text;
BEGIN
    IF p_entity = 'drill' THEN
        SELECT t.drill_name, t.retired_at INTO v_name, v_retired FROM h_data.drills t
        WHERE t.drill_id = p_id AND t.deleted_at IS NULL FOR UPDATE;
        v_label := 'Drill';
    ELSIF p_entity = 'drill_set' THEN
        SELECT t.set_name, t.retired_at INTO v_name, v_retired FROM h_data.drill_sets t
        WHERE t.drill_set_id = p_id AND t.deleted_at IS NULL FOR UPDATE;
        v_label := 'Drill set';
    ELSIF p_entity = 'practice_template' THEN
        SELECT t.template_name, t.retired_at INTO v_name, v_retired FROM h_data.practice_templates t
        WHERE t.practice_template_id = p_id AND t.deleted_at IS NULL FOR UPDATE;
        v_label := 'Practice template';
    ELSE
        RAISE EXCEPTION 'Unknown library entity "%"', p_entity;
    END IF;
    IF NOT FOUND THEN
        RAISE EXCEPTION '% % does not exist or is deleted', p_entity, p_id;
    END IF;

    v_count := h_data.fn_practice_usage(p_entity, p_id);
    IF p_entity = 'drill' THEN
        SELECT count(*)::integer INTO v_goals FROM h_data.goals g WHERE g.drill_id = p_id;
    END IF;

    IF v_count = 0 AND v_goals = 0 THEN
        PERFORM h_data.fn_soft_delete(p_entity, p_id);
        action := 'deleted';
    ELSIF v_retired IS NOT NULL THEN
        RAISE EXCEPTION '% "%" is already retired and is still in use', v_label, v_name;
    ELSE
        IF p_entity = 'drill' THEN
            UPDATE h_data.drills t SET retired_at = now() WHERE t.drill_id = p_id;
        ELSIF p_entity = 'drill_set' THEN
            UPDATE h_data.drill_sets t SET retired_at = now() WHERE t.drill_set_id = p_id;
        ELSE
            UPDATE h_data.practice_templates t SET retired_at = now() WHERE t.practice_template_id = p_id;
        END IF;
        action := 'retired';
    END IF;
    practice_count := v_count;
    goal_count := v_goals;
    RETURN NEXT;
END
$$;
COMMENT ON FUNCTION h_data.fn_remove_library_item(text, bigint) IS
    'REQ-081: the only library removal route. Unused by practices and goals not yet purged (incl. Recently deleted) -> soft delete; used -> retire (hidden from selection, kept in history); used and already retired -> refused. Returns action, practice_count, goal_count.';

-- -----------------------------------------------------------------------------
-- h_data.fn_practice_player_record_count (REQ-074, Amendment A2, Amendment A3)
-- Description : Number of live records the live participant row of a player in
--               a practice carries: shot entries, free-throw entries and drill
--               attempts with that row's practice_player_id. A drill result
--               without live attempts is not counted.
-- Parameters  : p_practice_session_id - h_data.practice_sessions id.
--               p_player_id           - h_data.players.player_id.
-- Return Values: integer - the count (0 = nothing recorded, or no live
--               participant row).
-- Notes       : Read-only; raises nothing. Records of removed participant rows of
--               the same player never count. The app uses it for the "cannot
--               remove" message before calling fn_remove_practice_player, which
--               re-checks under a lock.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.fn_practice_player_record_count(p_practice_session_id bigint, p_player_id bigint)
RETURNS integer
LANGUAGE sql
STABLE
AS $$
    SELECT COALESCE((
        SELECT ((SELECT count(*) FROM h_data.shot_entries se
                 WHERE se.practice_player_id = pp.practice_player_id AND se.deleted_at IS NULL)
              + (SELECT count(*) FROM h_data.free_throw_entries fe
                 WHERE fe.practice_player_id = pp.practice_player_id AND fe.deleted_at IS NULL)
              + (SELECT count(*) FROM h_data.drill_attempts a
                 WHERE a.practice_player_id = pp.practice_player_id AND a.deleted_at IS NULL))::integer
        FROM h_data.practice_players pp
        WHERE pp.practice_session_id = p_practice_session_id
          AND pp.player_id = p_player_id
          AND pp.deleted_at IS NULL), 0)
$$;
COMMENT ON FUNCTION h_data.fn_practice_player_record_count(bigint, bigint) IS
    'REQ-074: live shots, free throws and drill attempts of the live participant row of a player in a practice (0 = nothing recorded or no live row).';

-- -----------------------------------------------------------------------------
-- h_data.fn_add_practice_player (REQ-074, REQ-082, Amendment A2, Amendment A3)
-- Description : Adds a live player to a live practice by always inserting a new
--               practice_players row. A row of the same player removed earlier is
--               never restored; it stays deleted with all its records (COD-22-02).
-- Parameters  : p_practice_session_id - h_data.practice_sessions id (live).
--               p_player_id           - h_data.players.player_id (live).
-- Return Values: bigint - the new practice_player_id.
-- Notes       : Locks the practice row. Raises when the practice or the player is
--               not live, or the player already has a live participant row in the
--               practice. ux_practice_players_live is the backstop against a
--               concurrent duplicate. The season is assigned by
--               trg_practice_players_season.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.fn_add_practice_player(p_practice_session_id bigint, p_player_id bigint)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_pp_id  bigint;
BEGIN
    PERFORM 1 FROM h_data.practice_sessions ps
    WHERE ps.practice_session_id = p_practice_session_id AND ps.deleted_at IS NULL
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Practice % does not exist or is deleted', p_practice_session_id;
    END IF;
    PERFORM 1 FROM h_data.players p WHERE p.player_id = p_player_id AND p.deleted_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Player % does not exist or is removed', p_player_id;
    END IF;

    PERFORM 1 FROM h_data.practice_players pp
    WHERE pp.practice_session_id = p_practice_session_id AND pp.player_id = p_player_id AND pp.deleted_at IS NULL;
    IF FOUND THEN
        RAISE EXCEPTION 'Player % is already in practice %', p_player_id, p_practice_session_id;
    END IF;

    INSERT INTO h_data.practice_players AS pp (practice_session_id, player_id)
    VALUES (p_practice_session_id, p_player_id)
    RETURNING pp.practice_player_id INTO v_pp_id;
    RETURN v_pp_id;
END
$$;
COMMENT ON FUNCTION h_data.fn_add_practice_player(bigint, bigint) IS
    'REQ-074/082: adds a player to an existing practice as a new participant row (never restores a removed one; the new row starts with no records).';

-- -----------------------------------------------------------------------------
-- h_data.fn_remove_practice_player (REQ-074, REQ-082, Amendment A2, Amendment A3)
-- Description : Removes a player who has nothing recorded from a practice by soft
--               deleting the live practice_players row and everything carrying its
--               practice_player_id (h_data.fn_soft_delete, one deleted_at). The
--               row is never restored or listed in Recently deleted; re-adding the
--               player creates a new row. Purged with its records after 30 days.
-- Parameters  : p_practice_session_id - h_data.practice_sessions id (live).
--               p_player_id           - h_data.players.player_id.
-- Return Values: integer - rows marked (the participant plus any empty drill results).
-- Notes       : Locks the practice row, then the live participant row FOR UPDATE,
--               before counting records (DEF-021). Every insert of a practice
--               record takes a share lock on that participant row (shots and free
--               throws also lock the practice row), so a record and a removal never
--               both succeed: either the record commits first and is counted, or
--               the insert waits and then finds the participant removed. Lock order
--               as in h_data.fn_soft_delete. Raises when the player has no live
--               participant row, has live records (fn_practice_player_record_count
--               > 0) or is the last live player.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.fn_remove_practice_player(p_practice_session_id bigint, p_player_id bigint)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    v_pp_id    bigint;
    v_records  integer;
    v_others   integer;
BEGIN
    PERFORM 1 FROM h_data.practice_sessions ps
    WHERE ps.practice_session_id = p_practice_session_id AND ps.deleted_at IS NULL
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Practice % does not exist or is deleted', p_practice_session_id;
    END IF;

    SELECT pp.practice_player_id INTO v_pp_id
    FROM h_data.practice_players pp
    WHERE pp.practice_session_id = p_practice_session_id AND pp.player_id = p_player_id AND pp.deleted_at IS NULL
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Player % is not in practice %', p_player_id, p_practice_session_id;
    END IF;

    v_records := h_data.fn_practice_player_record_count(p_practice_session_id, p_player_id);
    IF v_records > 0 THEN
        RAISE EXCEPTION 'Player % has % record(s) in practice % and cannot be removed', p_player_id, v_records, p_practice_session_id;
    END IF;

    SELECT count(*) INTO v_others
    FROM h_data.practice_players pp
    WHERE pp.practice_session_id = p_practice_session_id AND pp.deleted_at IS NULL AND pp.player_id <> p_player_id;
    IF v_others = 0 THEN
        RAISE EXCEPTION 'A practice needs at least one player';
    END IF;

    RETURN h_data.fn_soft_delete('practice_player', v_pp_id);
END
$$;
COMMENT ON FUNCTION h_data.fn_remove_practice_player(bigint, bigint) IS
    'REQ-074/082: soft-deletes a practice participant who has nothing recorded; never the last player. The participant row is locked FOR UPDATE before the records are counted, so a concurrent record is either counted or refused (DEF-021).';

-- -----------------------------------------------------------------------------
-- h_data.fn_soft_delete (REQ-006, REQ-074, REQ-081)
-- Description : Soft-deletes a whitelisted entity and its live children in one
--               statement group, so they share the exact deleted_at (transaction time).
--               game -> entries;
--               practice_session -> attempt outcomes, attempts, entries, results,
--                   practice drills, placed sets, practice players;
--               practice_player (p_id = practice_player_id) -> the attempt
--                   outcomes, attempts, entries and results carrying that
--                   practice_player_id (never a newer row's records);
--               drill_result -> its attempt outcomes, linked entries, attempts;
--               drill_attempt -> its outcomes and linked entries;
--               practice_drill -> its results' outcomes, entries and attempts,
--                   then the results;
--               practice_drill_set -> the same chain for its drills, then the drills;
--               drill -> (refused while used) the lineage, secondary-type links,
--                   versions, spots, outcome groups, outcomes (no set items: sets
--                   name lineages);
--               drill_set -> (refused while used) the lineage, versions, their
--                   items and lines;
--               practice_template -> (refused while used) the lineage, versions,
--                   their sets;
--               player -> the profile; then, per live participant row of the
--                   player, its attempt outcomes, attempts, shot and free-throw
--                   entries, results and the participant row; then the entries
--                   of the player's live games and the games; goals; physical
--                   measurements; seasons last (Amendment A4). Seasons last is
--                   binding (A4.1, REQ-018): the season triggers then re-assign
--                   nothing that changes.
--               shot_entry, free_throw_entry, season, goal and
--               physical_measurement have no children. drill_type and
--               physical_test raise while still in use.
-- Parameters  : p_entity - one of the names above.
--               p_id     - the entity's primary key.
-- Return Values: integer - number of rows marked (the entity plus its children).
-- Notes       : Unknown entity, missing row or already deleted -> raises. No dynamic
--               SQL. The app confirms first (REQ-005). Devices are revoked by the
--               app, not soft-deleted. Who deleted = updated_by (audit trigger).
--               Library items: "used" = practices not yet purged
--               (h_data.fn_practice_usage) and, for a drill, goals not yet purged
--               that target it; the app removes them only through
--               h_data.fn_remove_library_item (delete or retire, REQ-081). The
--               lineage is marked first so tf_version_guard lets its versions and
--               contents follow with the same timestamp (REQ-080). Rows deleted
--               earlier keep their own timestamps (e.g. a participant removed
--               from a practice before the profile, or a practice deleted
--               before), so restore can tell them apart (COD-22-03).
--               LOCK ORDER (review_2 F1, binding): every multi-level branch first
--               marks its top row (an UPDATE, which locks it), then locks FOR
--               UPDATE, parent to child and by primary key within a table, every
--               live row in scope that an insert trigger locks:
--                 practice -> participant row -> practice drill -> drill result
--                 -> drill attempt; games after attempts (player branch only);
--               and only then marks the children bottom-up. This is the order in
--               which the insert triggers lock (tf_entry_before_write: practice or
--               game, participant row, linked attempt; tf_drill_results_validate:
--               practice, participant row, practice drill;
--               tf_drill_attempts_before_write: participant row, result;
--               tf_drill_attempt_outcomes_validate: attempt). So a concurrent
--               insert either commits before the cascade's marking statements run
--               (and is marked with them) or waits and is then refused because its
--               parent is deleted, and the two never wait on each other in a cycle.
--               game and drill_attempt need no extra lock: their insert triggers
--               lock the top row itself.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.fn_soft_delete(p_entity text, p_id bigint)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    v_now         timestamptz := now();
    v_rows        integer;
    v_total       integer := 0;
    v_used        bigint;
    v_goals       bigint;
    v_name        text;
BEGIN
    CASE p_entity
    WHEN 'game' THEN
        UPDATE h_data.games t SET deleted_at = v_now WHERE t.game_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows = 0 THEN RAISE EXCEPTION 'game % does not exist or is already deleted', p_id; END IF;
        v_total := v_rows;
        UPDATE h_data.shot_entries t SET deleted_at = v_now WHERE t.game_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.free_throw_entries t SET deleted_at = v_now WHERE t.game_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'practice_session' THEN
        UPDATE h_data.practice_sessions t SET deleted_at = v_now WHERE t.practice_session_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows = 0 THEN RAISE EXCEPTION 'practice_session % does not exist or is already deleted', p_id; END IF;
        v_total := v_rows;
        -- Lock order (header): participant rows, practice drills, results, attempts.
        PERFORM 1 FROM h_data.practice_players x
        WHERE x.practice_session_id = p_id AND x.deleted_at IS NULL ORDER BY x.practice_player_id FOR UPDATE;
        PERFORM 1 FROM h_data.practice_drills x
        WHERE x.practice_session_id = p_id AND x.deleted_at IS NULL ORDER BY x.practice_drill_id FOR UPDATE;
        PERFORM 1 FROM h_data.drill_results x
        WHERE x.practice_session_id = p_id AND x.deleted_at IS NULL ORDER BY x.drill_result_id FOR UPDATE;
        PERFORM 1 FROM h_data.drill_attempts x
        WHERE x.practice_session_id = p_id AND x.deleted_at IS NULL ORDER BY x.drill_attempt_id FOR UPDATE;
        UPDATE h_data.drill_attempt_outcomes t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.drill_attempt_id IN (SELECT a.drill_attempt_id FROM h_data.drill_attempts a WHERE a.practice_session_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_attempts t SET deleted_at = v_now WHERE t.practice_session_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.shot_entries t SET deleted_at = v_now WHERE t.practice_session_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.free_throw_entries t SET deleted_at = v_now WHERE t.practice_session_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_results t SET deleted_at = v_now WHERE t.practice_session_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.practice_drills t SET deleted_at = v_now WHERE t.practice_session_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.practice_drill_sets t SET deleted_at = v_now WHERE t.practice_session_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.practice_players t SET deleted_at = v_now WHERE t.practice_session_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'practice_player' THEN
        -- Matched by practice_player_id only: a newer live row of the same player
        -- in the same practice keeps its records (Amendment A3).
        UPDATE h_data.practice_players t SET deleted_at = v_now
        WHERE t.practice_player_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows = 0 THEN RAISE EXCEPTION 'practice_player % does not exist or is already deleted', p_id; END IF;
        v_total := v_rows;
        -- Lock order (header): results, attempts of this participant row.
        PERFORM 1 FROM h_data.drill_results x
        WHERE x.practice_player_id = p_id AND x.deleted_at IS NULL ORDER BY x.drill_result_id FOR UPDATE;
        PERFORM 1 FROM h_data.drill_attempts x
        WHERE x.practice_player_id = p_id AND x.deleted_at IS NULL ORDER BY x.drill_attempt_id FOR UPDATE;
        UPDATE h_data.drill_attempt_outcomes t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.drill_attempt_id IN (SELECT a.drill_attempt_id FROM h_data.drill_attempts a
                                     WHERE a.practice_player_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_attempts t SET deleted_at = v_now
        WHERE t.practice_player_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.shot_entries t SET deleted_at = v_now
        WHERE t.practice_player_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.free_throw_entries t SET deleted_at = v_now
        WHERE t.practice_player_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_results t SET deleted_at = v_now
        WHERE t.practice_player_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'drill_result' THEN
        UPDATE h_data.drill_results t SET deleted_at = v_now WHERE t.drill_result_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows = 0 THEN RAISE EXCEPTION 'drill_result % does not exist or is already deleted', p_id; END IF;
        v_total := v_rows;
        -- Lock order (header): attempts of this result.
        PERFORM 1 FROM h_data.drill_attempts x
        WHERE x.drill_result_id = p_id AND x.deleted_at IS NULL ORDER BY x.drill_attempt_id FOR UPDATE;
        UPDATE h_data.drill_attempt_outcomes t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.drill_attempt_id IN (SELECT a.drill_attempt_id FROM h_data.drill_attempts a WHERE a.drill_result_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.shot_entries t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.drill_attempt_id IN (SELECT a.drill_attempt_id FROM h_data.drill_attempts a WHERE a.drill_result_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.free_throw_entries t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.drill_attempt_id IN (SELECT a.drill_attempt_id FROM h_data.drill_attempts a WHERE a.drill_result_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_attempts t SET deleted_at = v_now WHERE t.drill_result_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'drill_attempt' THEN
        UPDATE h_data.drill_attempts t SET deleted_at = v_now WHERE t.drill_attempt_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows = 0 THEN RAISE EXCEPTION 'drill_attempt % does not exist or is already deleted', p_id; END IF;
        v_total := v_rows;
        UPDATE h_data.drill_attempt_outcomes t SET deleted_at = v_now WHERE t.drill_attempt_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.shot_entries t SET deleted_at = v_now WHERE t.drill_attempt_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.free_throw_entries t SET deleted_at = v_now WHERE t.drill_attempt_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'practice_drill' THEN
        UPDATE h_data.practice_drills t SET deleted_at = v_now WHERE t.practice_drill_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows = 0 THEN RAISE EXCEPTION 'practice_drill % does not exist or is already deleted', p_id; END IF;
        v_total := v_rows;
        -- Lock order (header): results of this practice drill, their attempts.
        PERFORM 1 FROM h_data.drill_results x
        WHERE x.practice_drill_id = p_id AND x.deleted_at IS NULL ORDER BY x.drill_result_id FOR UPDATE;
        PERFORM 1 FROM h_data.drill_attempts x
        WHERE x.deleted_at IS NULL
          AND x.drill_result_id IN (SELECT r.drill_result_id FROM h_data.drill_results r WHERE r.practice_drill_id = p_id)
        ORDER BY x.drill_attempt_id FOR UPDATE;
        UPDATE h_data.drill_attempt_outcomes t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.drill_attempt_id IN (
              SELECT a.drill_attempt_id FROM h_data.drill_attempts a
              WHERE a.drill_result_id IN (SELECT r.drill_result_id FROM h_data.drill_results r WHERE r.practice_drill_id = p_id));
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.shot_entries t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.drill_attempt_id IN (
              SELECT a.drill_attempt_id FROM h_data.drill_attempts a
              WHERE a.drill_result_id IN (SELECT r.drill_result_id FROM h_data.drill_results r WHERE r.practice_drill_id = p_id));
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.free_throw_entries t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.drill_attempt_id IN (
              SELECT a.drill_attempt_id FROM h_data.drill_attempts a
              WHERE a.drill_result_id IN (SELECT r.drill_result_id FROM h_data.drill_results r WHERE r.practice_drill_id = p_id));
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_attempts t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.drill_result_id IN (SELECT r.drill_result_id FROM h_data.drill_results r WHERE r.practice_drill_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_results t SET deleted_at = v_now WHERE t.practice_drill_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'practice_drill_set' THEN
        UPDATE h_data.practice_drill_sets t SET deleted_at = v_now WHERE t.practice_drill_set_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows = 0 THEN RAISE EXCEPTION 'practice_drill_set % does not exist or is already deleted', p_id; END IF;
        v_total := v_rows;
        -- Lock order (header): practice drills of this placement, their results,
        -- those results' attempts.
        PERFORM 1 FROM h_data.practice_drills x
        WHERE x.practice_drill_set_id = p_id AND x.deleted_at IS NULL ORDER BY x.practice_drill_id FOR UPDATE;
        PERFORM 1 FROM h_data.drill_results x
        WHERE x.deleted_at IS NULL
          AND x.practice_drill_id IN (SELECT pd.practice_drill_id FROM h_data.practice_drills pd WHERE pd.practice_drill_set_id = p_id)
        ORDER BY x.drill_result_id FOR UPDATE;
        PERFORM 1 FROM h_data.drill_attempts x
        WHERE x.deleted_at IS NULL
          AND x.drill_result_id IN (
              SELECT r.drill_result_id FROM h_data.drill_results r
              WHERE r.practice_drill_id IN (SELECT pd.practice_drill_id FROM h_data.practice_drills pd WHERE pd.practice_drill_set_id = p_id))
        ORDER BY x.drill_attempt_id FOR UPDATE;
        UPDATE h_data.drill_attempt_outcomes t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.drill_attempt_id IN (
              SELECT a.drill_attempt_id FROM h_data.drill_attempts a
              WHERE a.drill_result_id IN (
                  SELECT r.drill_result_id FROM h_data.drill_results r
                  WHERE r.practice_drill_id IN (SELECT pd.practice_drill_id FROM h_data.practice_drills pd WHERE pd.practice_drill_set_id = p_id)));
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.shot_entries t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.drill_attempt_id IN (
              SELECT a.drill_attempt_id FROM h_data.drill_attempts a
              WHERE a.drill_result_id IN (
                  SELECT r.drill_result_id FROM h_data.drill_results r
                  WHERE r.practice_drill_id IN (SELECT pd.practice_drill_id FROM h_data.practice_drills pd WHERE pd.practice_drill_set_id = p_id)));
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.free_throw_entries t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.drill_attempt_id IN (
              SELECT a.drill_attempt_id FROM h_data.drill_attempts a
              WHERE a.drill_result_id IN (
                  SELECT r.drill_result_id FROM h_data.drill_results r
                  WHERE r.practice_drill_id IN (SELECT pd.practice_drill_id FROM h_data.practice_drills pd WHERE pd.practice_drill_set_id = p_id)));
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_attempts t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.drill_result_id IN (
              SELECT r.drill_result_id FROM h_data.drill_results r
              WHERE r.practice_drill_id IN (SELECT pd.practice_drill_id FROM h_data.practice_drills pd WHERE pd.practice_drill_set_id = p_id));
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_results t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.practice_drill_id IN (SELECT pd.practice_drill_id FROM h_data.practice_drills pd WHERE pd.practice_drill_set_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.practice_drills t SET deleted_at = v_now WHERE t.practice_drill_set_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'drill' THEN
        -- REQ-081: refused while practices or goals not yet purged use it.
        v_used := h_data.fn_practice_usage('drill', p_id);
        SELECT count(*) INTO v_goals FROM h_data.goals g WHERE g.drill_id = p_id;
        IF v_used > 0 OR v_goals > 0 THEN
            SELECT d.drill_name INTO v_name FROM h_data.drills d WHERE d.drill_id = p_id;
            RAISE EXCEPTION 'Drill "%" is used in % practice(s) and % goal(s), including any in Recently deleted; retire it instead', v_name, v_used, v_goals;
        END IF;
        -- Lineage first, then versions, then contents (tf_version_guard, REQ-080).
        UPDATE h_data.drills t SET deleted_at = v_now WHERE t.drill_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows = 0 THEN RAISE EXCEPTION 'drill % does not exist or is already deleted', p_id; END IF;
        v_total := v_rows;
        UPDATE h_data.drill_secondary_types t SET deleted_at = v_now WHERE t.drill_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_versions t SET deleted_at = v_now WHERE t.drill_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_spots t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.drill_version_id IN (SELECT v.drill_version_id FROM h_data.drill_versions v WHERE v.drill_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_outcome_groups t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.drill_version_id IN (SELECT v.drill_version_id FROM h_data.drill_versions v WHERE v.drill_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_outcomes t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.outcome_group_id IN (
              SELECT g.outcome_group_id FROM h_data.drill_outcome_groups g
              JOIN h_data.drill_versions v ON v.drill_version_id = g.drill_version_id
              WHERE v.drill_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'drill_set' THEN
        -- REQ-081: refused while practices not yet purged use it.
        v_used := h_data.fn_practice_usage('drill_set', p_id);
        IF v_used > 0 THEN
            SELECT s.set_name INTO v_name FROM h_data.drill_sets s WHERE s.drill_set_id = p_id;
            RAISE EXCEPTION 'Drill set "%" is used in % practice(s), including any in Recently deleted; retire it instead', v_name, v_used;
        END IF;
        -- Lineage first, then versions, then contents (tf_version_guard, REQ-080).
        UPDATE h_data.drill_sets t SET deleted_at = v_now WHERE t.drill_set_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows = 0 THEN RAISE EXCEPTION 'drill_set % does not exist or is already deleted', p_id; END IF;
        v_total := v_rows;
        UPDATE h_data.drill_set_versions t SET deleted_at = v_now WHERE t.drill_set_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_set_items t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.drill_set_version_id IN (SELECT v.drill_set_version_id FROM h_data.drill_set_versions v WHERE v.drill_set_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_set_lines t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.drill_set_version_id IN (SELECT v.drill_set_version_id FROM h_data.drill_set_versions v WHERE v.drill_set_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'practice_template' THEN
        -- REQ-081: refused while practices not yet purged use it.
        v_used := h_data.fn_practice_usage('practice_template', p_id);
        IF v_used > 0 THEN
            SELECT pt.template_name INTO v_name FROM h_data.practice_templates pt WHERE pt.practice_template_id = p_id;
            RAISE EXCEPTION 'Practice template "%" is used in % practice(s), including any in Recently deleted; retire it instead', v_name, v_used;
        END IF;
        -- Lineage first, then versions, then contents (tf_version_guard, REQ-080).
        UPDATE h_data.practice_templates t SET deleted_at = v_now WHERE t.practice_template_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows = 0 THEN RAISE EXCEPTION 'practice_template % does not exist or is already deleted', p_id; END IF;
        v_total := v_rows;
        UPDATE h_data.practice_template_versions t SET deleted_at = v_now WHERE t.practice_template_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.practice_template_sets t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.practice_template_version_id IN (
              SELECT v.practice_template_version_id FROM h_data.practice_template_versions v WHERE v.practice_template_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'player' THEN
        -- Amendment A4 (OQ 14 option A): the profile's deleted_at cascades to
        -- everything of the player, also inside shared practices. Live rows only;
        -- rows deleted earlier keep their own timestamps.
        UPDATE h_data.players t SET deleted_at = v_now WHERE t.player_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows = 0 THEN RAISE EXCEPTION 'player % does not exist or is already deleted', p_id; END IF;
        v_total := v_rows;
        -- Lock order (header): participant rows, results, attempts, then games.
        PERFORM 1 FROM h_data.practice_players x
        WHERE x.player_id = p_id AND x.deleted_at IS NULL ORDER BY x.practice_player_id FOR UPDATE;
        PERFORM 1 FROM h_data.drill_results x
        WHERE x.player_id = p_id AND x.deleted_at IS NULL ORDER BY x.drill_result_id FOR UPDATE;
        PERFORM 1 FROM h_data.drill_attempts x
        WHERE x.player_id = p_id AND x.deleted_at IS NULL ORDER BY x.drill_attempt_id FOR UPDATE;
        PERFORM 1 FROM h_data.games x
        WHERE x.player_id = p_id AND x.deleted_at IS NULL ORDER BY x.game_id FOR UPDATE;
        -- Live participant rows: the A3 chain by practice_player_id, then the rows.
        UPDATE h_data.drill_attempt_outcomes t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.drill_attempt_id IN (
              SELECT a.drill_attempt_id FROM h_data.drill_attempts a
              WHERE a.practice_player_id IN (SELECT pp.practice_player_id FROM h_data.practice_players pp
                                             WHERE pp.player_id = p_id AND pp.deleted_at IS NULL));
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_attempts t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.practice_player_id IN (SELECT pp.practice_player_id FROM h_data.practice_players pp
                                       WHERE pp.player_id = p_id AND pp.deleted_at IS NULL);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.shot_entries t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.practice_player_id IN (SELECT pp.practice_player_id FROM h_data.practice_players pp
                                       WHERE pp.player_id = p_id AND pp.deleted_at IS NULL);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.free_throw_entries t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.practice_player_id IN (SELECT pp.practice_player_id FROM h_data.practice_players pp
                                       WHERE pp.player_id = p_id AND pp.deleted_at IS NULL);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_results t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.practice_player_id IN (SELECT pp.practice_player_id FROM h_data.practice_players pp
                                       WHERE pp.player_id = p_id AND pp.deleted_at IS NULL);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.practice_players t SET deleted_at = v_now
        WHERE t.player_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        -- Live games: their entries, then the games.
        UPDATE h_data.shot_entries t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.game_id IN (SELECT g.game_id FROM h_data.games g WHERE g.player_id = p_id AND g.deleted_at IS NULL);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.free_throw_entries t SET deleted_at = v_now
        WHERE t.deleted_at IS NULL
          AND t.game_id IN (SELECT g.game_id FROM h_data.games g WHERE g.player_id = p_id AND g.deleted_at IS NULL);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.games t SET deleted_at = v_now WHERE t.player_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        -- Goals, then physical measurements; seasons last.
        UPDATE h_data.goals t SET deleted_at = v_now WHERE t.player_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.physical_measurements t SET deleted_at = v_now WHERE t.player_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.seasons t SET deleted_at = v_now WHERE t.player_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'drill_type' THEN
        SELECT (SELECT count(*) FROM h_data.drills d WHERE d.primary_drill_type_id = p_id AND d.deleted_at IS NULL)
             + (SELECT count(*) FROM h_data.drill_secondary_types st WHERE st.drill_type_id = p_id AND st.deleted_at IS NULL)
             + (SELECT count(*) FROM h_data.drill_set_lines l WHERE l.drill_type_id = p_id AND l.deleted_at IS NULL)
        INTO v_used;
        IF v_used > 0 THEN
            RAISE EXCEPTION 'This drill type is still used by % drill(s), secondary-type link(s) or drill-set line(s); change them first', v_used;
        END IF;
        UPDATE h_data.drill_types t SET deleted_at = v_now WHERE t.drill_type_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows = 0 THEN RAISE EXCEPTION 'drill_type % does not exist or is already deleted', p_id; END IF;
        v_total := v_rows;

    WHEN 'physical_test' THEN
        SELECT (SELECT count(*) FROM h_data.physical_measurements pm WHERE pm.physical_test_id = p_id AND pm.deleted_at IS NULL)
             + (SELECT count(*) FROM h_data.goals g WHERE g.physical_test_id = p_id AND g.deleted_at IS NULL)
        INTO v_used;
        IF v_used > 0 THEN
            RAISE EXCEPTION 'This physical test is still used by % measurement(s) or goal(s); delete them first', v_used;
        END IF;
        UPDATE h_ref.physical_tests t SET deleted_at = v_now WHERE t.physical_test_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows = 0 THEN RAISE EXCEPTION 'physical_test % does not exist or is already deleted', p_id; END IF;
        v_total := v_rows;

    WHEN 'shot_entry' THEN
        UPDATE h_data.shot_entries t SET deleted_at = v_now WHERE t.shot_entry_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows = 0 THEN RAISE EXCEPTION 'shot_entry % does not exist or is already deleted', p_id; END IF;
        v_total := v_rows;

    WHEN 'free_throw_entry' THEN
        UPDATE h_data.free_throw_entries t SET deleted_at = v_now WHERE t.free_throw_entry_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows = 0 THEN RAISE EXCEPTION 'free_throw_entry % does not exist or is already deleted', p_id; END IF;
        v_total := v_rows;

    WHEN 'season' THEN
        UPDATE h_data.seasons t SET deleted_at = v_now WHERE t.season_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows = 0 THEN RAISE EXCEPTION 'season % does not exist or is already deleted', p_id; END IF;
        v_total := v_rows;

    WHEN 'goal' THEN
        UPDATE h_data.goals t SET deleted_at = v_now WHERE t.goal_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows = 0 THEN RAISE EXCEPTION 'goal % does not exist or is already deleted', p_id; END IF;
        v_total := v_rows;

    WHEN 'physical_measurement' THEN
        UPDATE h_data.physical_measurements t SET deleted_at = v_now WHERE t.physical_measurement_id = p_id AND t.deleted_at IS NULL;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        IF v_rows = 0 THEN RAISE EXCEPTION 'physical_measurement % does not exist or is already deleted', p_id; END IF;
        v_total := v_rows;

    ELSE
        RAISE EXCEPTION 'Unknown entity "%" for soft delete', p_entity;
    END CASE;

    RETURN v_total;
END
$$;
COMMENT ON FUNCTION h_data.fn_soft_delete(text, bigint) IS
    'REQ-006, COD-22-03: soft-deletes a whitelisted entity and its live children with one shared deleted_at. Drills, drill sets and practice templates are refused while used (REQ-081) and are marked lineage first; a player profile cascades to everything of that player (Amendment A4). BINDING (A4.1, REQ-018): the player branch marks seasons LAST, so the season triggers leave every assignment as it was; do not reorder. BINDING (review_2 F1): multi-level branches lock the live participant rows, practice drills, results and attempts (and games) in scope FOR UPDATE, parent to child, before marking the children, in the same order as the insert triggers.';

-- -----------------------------------------------------------------------------
-- h_data.fn_restore (REQ-006, REQ-074, REQ-081, REQ-082)
-- Description : Restores a soft-deleted entity and only the children whose
--               deleted_at equals the entity's (deleted with it). A child deleted
--               separately earlier stays deleted. When a practice drill or placed
--               set is restored, a result comes back only if its participant row
--               (practice_player_id) is live and its recorded drill version is
--               still the slot's version. A removed participant row
--               (practice_player) is never restored, so its records are never
--               re-attached (Amendment A3, COD-22-02).
--               Amendment A4:
--               - practice_session, practice_drill_set, practice_drill: before
--                 anything is cleared, a restorable participant row or result
--                 that collides with a live one (same practice and player; same
--                 practice drill and participant row) makes the whole restore
--                 refuse with a message; nothing is restored (Overseer, OQ 16).
--                 A restorable participant row or result whose player profile is
--                 removed is not made live: it (and its chain) takes the
--                 profile's deleted_at, so it returns with the profile.
--               - drill, drill_set, practice_template: lineage first, then
--                   versions, then contents (outcome groups before outcomes).
--                   Refused, changing nothing, when the lineage or one of its
--                   versions was created in the same transaction that deleted it
--                   (its contents cannot be told apart from rows removed during
--                   creation; A4.1).
--               - player: the profile, then every row of the player with the
--                 profile's deleted_at, parents first (seasons first is binding,
--                 A4.1, REQ-018): seasons; games and their
--                 entries; goals; measurements; participant rows, then results,
--                 attempts, outcome taps and entries. A participant row (and its
--                 chain) whose practice is now deleted takes the practice's
--                 deleted_at instead; a result whose practice drill is now
--                 deleted takes the practice drill's deleted_at (with its chain);
--                 a result whose practice drill now holds another drill version
--                 stays deleted (as for a practice-drill restore).
--               - single-row branches for player-owned rows (game, season,
--                 goal, physical_measurement, shot_entry, free_throw_entry,
--                 drill_result, drill_attempt) refuse while the player's profile
--                 is removed.
-- Parameters  : p_entity - the same names as h_data.fn_soft_delete.
--               p_id     - the entity's primary key.
-- Return Values: integer - number of rows restored (made live; rows handed over
--               to another parent's deleted_at are not counted).
-- Notes       : Raises when the entity is practice_player (always); when the row
--               is missing or not deleted; when its parent is still deleted
--               ("restore the game/practice/drill result/drill attempt first");
--               when the row's player profile is removed ("% was removed;
--               restore the player first"); when the entry's or result's
--               participant row was removed from the practice; when a result's
--               slot now holds another drill version; when the slot already has
--               a live result for that participant row; on a bulk-restore
--               collision ("This practice / drill set / drill / player cannot be
--               restored: ..."); when a drill, drill set, practice template,
--               player, drill type, physical test or season name is now used by
--               another live row; and, through the trigger, on a season overlap.
--               No live row is ever left with a deleted parent (COD-22-03).
--               LOCK ORDER (review_3, binding): every branch that checks a parent
--               or the player profile first locks the checked rows, top-down and
--               by primary key within a table, and only then reads their
--               deleted_at and clears anything:
--                 player -> practice or game -> participant row -> placed set
--                 -> practice drill -> drill result -> drill attempt.
--               Single-row and bulk branches lock FOR SHARE, except: season
--               locks the player FOR UPDATE (tf_seasons_no_overlap takes that
--               lock in the same transaction, so a share lock would be upgraded
--               and two season restores of one player could deadlock); player
--               locks its own profile FOR NO KEY UPDATE (the strength of its
--               UPDATE below). This is the order of the insert triggers and of
--               h_data.fn_soft_delete's cascades (top row first, then children
--               parent to child). So a concurrent cascade either commits first
--               (the restore then reads the parent as deleted and refuses or
--               hands over) or waits until the restore commits (its next lock
--               and marking statements then see the restored rows and mark
--               them), and the two never wait on each other in a cycle.
--               drill, drill_set, practice_template, drill_type and
--               physical_test check no parent and take no lock.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_data.fn_restore(p_entity text, p_id bigint)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    v_at           timestamptz;
    v_rows         integer;
    v_total        integer := 0;
    v_name         text;
    v_drill        text;
    v_game_id      bigint;
    v_session_id   bigint;
    v_result_id    bigint;
    v_attempt_id   bigint;
    v_version_id   bigint;
    v_pd_id        bigint;
    v_pds_id       bigint;
    v_player_id    bigint;
    v_pp_id        bigint;
    v_player_at    timestamptz;
    v_player_name  text;
    v_date         text;
    v_ids          bigint[];
BEGIN
    CASE p_entity
    WHEN 'game' THEN
        SELECT t.deleted_at, t.player_id INTO v_at, v_player_id FROM h_data.games t WHERE t.game_id = p_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'game % does not exist', p_id; END IF;
        IF v_at IS NULL THEN RAISE EXCEPTION 'game % is not deleted', p_id; END IF;
        -- Lock order (header): the player.
        PERFORM 1 FROM h_data.players x WHERE x.player_id = v_player_id FOR SHARE;
        SELECT p.display_name, p.deleted_at INTO v_name, v_player_at FROM h_data.players p WHERE p.player_id = v_player_id;
        IF v_player_at IS NOT NULL THEN RAISE EXCEPTION '% was removed; restore the player first', v_name; END IF;
        UPDATE h_data.games t SET deleted_at = NULL WHERE t.game_id = p_id;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_rows;
        UPDATE h_data.shot_entries t SET deleted_at = NULL WHERE t.game_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.free_throw_entries t SET deleted_at = NULL WHERE t.game_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'practice_session' THEN
        SELECT t.deleted_at INTO v_at FROM h_data.practice_sessions t WHERE t.practice_session_id = p_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'practice_session % does not exist', p_id; END IF;
        IF v_at IS NULL THEN RAISE EXCEPTION 'practice_session % is not deleted', p_id; END IF;
        -- Lock order (header): the players of the restorable participant rows,
        -- whose profile state decides restore or hand-over below.
        PERFORM 1 FROM h_data.players x
        WHERE x.player_id IN (SELECT pp.player_id FROM h_data.practice_players pp
                              WHERE pp.practice_session_id = p_id AND pp.deleted_at = v_at)
        ORDER BY x.player_id FOR SHARE;

        -- A4 collision check (backstop), before any row is cleared. Rows of a
        -- removed profile are handed over below and are not restored here.
        SELECT p.display_name INTO v_name
        FROM h_data.practice_players pp
        JOIN h_data.players p ON p.player_id = pp.player_id
        WHERE pp.practice_session_id = p_id AND pp.deleted_at = v_at AND p.deleted_at IS NULL
          AND EXISTS (SELECT 1 FROM h_data.practice_players x
                      WHERE x.practice_session_id = pp.practice_session_id AND x.player_id = pp.player_id
                        AND x.deleted_at IS NULL)
        LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'This practice cannot be restored: % was added to it again; remove that player first.', v_name;
        END IF;
        SELECT p.display_name, d.drill_name INTO v_name, v_drill
        FROM h_data.drill_results r
        JOIN h_data.players p ON p.player_id = r.player_id
        JOIN h_data.drill_versions dv ON dv.drill_version_id = r.drill_version_id
        JOIN h_data.drills d ON d.drill_id = dv.drill_id
        WHERE r.practice_session_id = p_id AND r.deleted_at = v_at AND p.deleted_at IS NULL
          AND EXISTS (SELECT 1 FROM h_data.drill_results x
                      WHERE x.practice_drill_id = r.practice_drill_id AND x.practice_player_id = r.practice_player_id
                        AND x.deleted_at IS NULL)
        LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'This practice cannot be restored: % already has another result for %; delete that result first.', v_name, v_drill;
        END IF;

        -- A4 hand-over: participant rows and records of a removed profile take
        -- the profile's deleted_at, so they return when the profile is restored.
        UPDATE h_data.drill_attempt_outcomes t SET deleted_at = p.deleted_at
        FROM h_data.drill_attempts a
        JOIN h_data.players p ON p.player_id = a.player_id
        WHERE t.drill_attempt_id = a.drill_attempt_id AND a.practice_session_id = p_id
          AND p.deleted_at IS NOT NULL AND t.deleted_at = v_at;
        UPDATE h_data.drill_attempts t SET deleted_at = p.deleted_at
        FROM h_data.players p
        WHERE p.player_id = t.player_id AND p.deleted_at IS NOT NULL
          AND t.practice_session_id = p_id AND t.deleted_at = v_at;
        UPDATE h_data.shot_entries t SET deleted_at = p.deleted_at
        FROM h_data.players p
        WHERE p.player_id = t.player_id AND p.deleted_at IS NOT NULL
          AND t.practice_session_id = p_id AND t.deleted_at = v_at;
        UPDATE h_data.free_throw_entries t SET deleted_at = p.deleted_at
        FROM h_data.players p
        WHERE p.player_id = t.player_id AND p.deleted_at IS NOT NULL
          AND t.practice_session_id = p_id AND t.deleted_at = v_at;
        UPDATE h_data.drill_results t SET deleted_at = p.deleted_at
        FROM h_data.players p
        WHERE p.player_id = t.player_id AND p.deleted_at IS NOT NULL
          AND t.practice_session_id = p_id AND t.deleted_at = v_at;
        UPDATE h_data.practice_players t SET deleted_at = p.deleted_at
        FROM h_data.players p
        WHERE p.player_id = t.player_id AND p.deleted_at IS NOT NULL
          AND t.practice_session_id = p_id AND t.deleted_at = v_at;

        UPDATE h_data.practice_sessions t SET deleted_at = NULL WHERE t.practice_session_id = p_id;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_rows;
        UPDATE h_data.practice_players t SET deleted_at = NULL WHERE t.practice_session_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.practice_drill_sets t SET deleted_at = NULL WHERE t.practice_session_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.practice_drills t SET deleted_at = NULL WHERE t.practice_session_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_results t SET deleted_at = NULL WHERE t.practice_session_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_attempts t SET deleted_at = NULL WHERE t.practice_session_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_attempt_outcomes t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.drill_attempt_id IN (SELECT a.drill_attempt_id FROM h_data.drill_attempts a WHERE a.practice_session_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.shot_entries t SET deleted_at = NULL WHERE t.practice_session_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.free_throw_entries t SET deleted_at = NULL WHERE t.practice_session_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'practice_player' THEN
        -- A removed participant row is never restored (Amendment A3, COD-22-02).
        RAISE EXCEPTION 'A player removed from a practice cannot be restored; add the player to the practice again (the new participant starts with no records)';

    WHEN 'shot_entry', 'free_throw_entry' THEN
        IF p_entity = 'shot_entry' THEN
            SELECT t.deleted_at, t.game_id, t.practice_session_id, t.practice_player_id, t.drill_attempt_id, t.player_id
            INTO v_at, v_game_id, v_session_id, v_pp_id, v_attempt_id, v_player_id
            FROM h_data.shot_entries t WHERE t.shot_entry_id = p_id;
        ELSE
            SELECT t.deleted_at, t.game_id, t.practice_session_id, t.practice_player_id, t.drill_attempt_id, t.player_id
            INTO v_at, v_game_id, v_session_id, v_pp_id, v_attempt_id, v_player_id
            FROM h_data.free_throw_entries t WHERE t.free_throw_entry_id = p_id;
        END IF;
        IF NOT FOUND THEN RAISE EXCEPTION '% % does not exist', p_entity, p_id; END IF;
        IF v_at IS NULL THEN RAISE EXCEPTION '% % is not deleted', p_entity, p_id; END IF;
        -- Lock order (header): player, game or practice, participant row, attempt
        -- (a NULL id locks nothing).
        PERFORM 1 FROM h_data.players x WHERE x.player_id = v_player_id FOR SHARE;
        PERFORM 1 FROM h_data.games x WHERE x.game_id = v_game_id FOR SHARE;
        PERFORM 1 FROM h_data.practice_sessions x WHERE x.practice_session_id = v_session_id FOR SHARE;
        PERFORM 1 FROM h_data.practice_players x WHERE x.practice_player_id = v_pp_id FOR SHARE;
        PERFORM 1 FROM h_data.drill_attempts x WHERE x.drill_attempt_id = v_attempt_id FOR SHARE;
        SELECT p.display_name, p.deleted_at INTO v_name, v_player_at FROM h_data.players p WHERE p.player_id = v_player_id;
        IF v_player_at IS NOT NULL THEN RAISE EXCEPTION '% was removed; restore the player first', v_name; END IF;
        IF v_game_id IS NOT NULL
           AND (SELECT g.deleted_at FROM h_data.games g WHERE g.game_id = v_game_id) IS NOT NULL THEN
            RAISE EXCEPTION 'The game of this entry is deleted; restore the game first';
        END IF;
        IF v_session_id IS NOT NULL
           AND (SELECT ps.deleted_at FROM h_data.practice_sessions ps WHERE ps.practice_session_id = v_session_id) IS NOT NULL THEN
            RAISE EXCEPTION 'The practice of this entry is deleted; restore the practice first';
        END IF;
        IF v_pp_id IS NOT NULL
           AND (SELECT pp.deleted_at FROM h_data.practice_players pp
                WHERE pp.practice_player_id = v_pp_id) IS NOT NULL THEN
            RAISE EXCEPTION 'The player of this entry was removed from the practice; the entry cannot be restored';
        END IF;
        IF v_attempt_id IS NOT NULL
           AND (SELECT a.deleted_at FROM h_data.drill_attempts a WHERE a.drill_attempt_id = v_attempt_id) IS NOT NULL THEN
            RAISE EXCEPTION 'The drill attempt of this entry is deleted; restore the drill attempt first';
        END IF;
        IF p_entity = 'shot_entry' THEN
            UPDATE h_data.shot_entries t SET deleted_at = NULL WHERE t.shot_entry_id = p_id;
        ELSE
            UPDATE h_data.free_throw_entries t SET deleted_at = NULL WHERE t.free_throw_entry_id = p_id;
        END IF;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_rows;

    WHEN 'drill_result' THEN
        SELECT t.deleted_at, t.practice_session_id, t.practice_drill_id, t.practice_player_id, t.drill_version_id, t.player_id
        INTO v_at, v_session_id, v_pd_id, v_pp_id, v_version_id, v_player_id
        FROM h_data.drill_results t WHERE t.drill_result_id = p_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'drill_result % does not exist', p_id; END IF;
        IF v_at IS NULL THEN RAISE EXCEPTION 'drill_result % is not deleted', p_id; END IF;
        -- Lock order (header): player, practice, participant row, practice drill.
        PERFORM 1 FROM h_data.players x WHERE x.player_id = v_player_id FOR SHARE;
        PERFORM 1 FROM h_data.practice_sessions x WHERE x.practice_session_id = v_session_id FOR SHARE;
        PERFORM 1 FROM h_data.practice_players x WHERE x.practice_player_id = v_pp_id FOR SHARE;
        PERFORM 1 FROM h_data.practice_drills x WHERE x.practice_drill_id = v_pd_id FOR SHARE;
        SELECT p.display_name, p.deleted_at INTO v_name, v_player_at FROM h_data.players p WHERE p.player_id = v_player_id;
        IF v_player_at IS NOT NULL THEN RAISE EXCEPTION '% was removed; restore the player first', v_name; END IF;
        IF (SELECT ps.deleted_at FROM h_data.practice_sessions ps WHERE ps.practice_session_id = v_session_id) IS NOT NULL THEN
            RAISE EXCEPTION 'The practice of this drill result is deleted; restore the practice first';
        END IF;
        IF (SELECT pd.deleted_at FROM h_data.practice_drills pd WHERE pd.practice_drill_id = v_pd_id) IS NOT NULL THEN
            RAISE EXCEPTION 'The practice drill of this result is deleted; restore it first';
        END IF;
        IF (SELECT pp.deleted_at FROM h_data.practice_players pp
            WHERE pp.practice_player_id = v_pp_id) IS NOT NULL THEN
            RAISE EXCEPTION 'The player of this result was removed from the practice; the result cannot be restored';
        END IF;
        IF (SELECT pd.drill_version_id FROM h_data.practice_drills pd WHERE pd.practice_drill_id = v_pd_id) IS DISTINCT FROM v_version_id THEN
            RAISE EXCEPTION 'This result was recorded for a drill that is no longer in its practice slot; it cannot be restored there';
        END IF;
        IF EXISTS (SELECT 1 FROM h_data.drill_results r
                   WHERE r.practice_drill_id = v_pd_id AND r.practice_player_id = v_pp_id AND r.deleted_at IS NULL) THEN
            RAISE EXCEPTION 'This player already has another result for this drill; delete that one first';
        END IF;
        UPDATE h_data.drill_results t SET deleted_at = NULL WHERE t.drill_result_id = p_id;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_rows;
        UPDATE h_data.drill_attempts t SET deleted_at = NULL WHERE t.drill_result_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_attempt_outcomes t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.drill_attempt_id IN (SELECT a.drill_attempt_id FROM h_data.drill_attempts a WHERE a.drill_result_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.shot_entries t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.drill_attempt_id IN (SELECT a.drill_attempt_id FROM h_data.drill_attempts a WHERE a.drill_result_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.free_throw_entries t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.drill_attempt_id IN (SELECT a.drill_attempt_id FROM h_data.drill_attempts a WHERE a.drill_result_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'drill_attempt' THEN
        SELECT t.deleted_at, t.drill_result_id, t.player_id
        INTO v_at, v_result_id, v_player_id
        FROM h_data.drill_attempts t WHERE t.drill_attempt_id = p_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'drill_attempt % does not exist', p_id; END IF;
        IF v_at IS NULL THEN RAISE EXCEPTION 'drill_attempt % is not deleted', p_id; END IF;
        -- Lock order (header): player, drill result.
        PERFORM 1 FROM h_data.players x WHERE x.player_id = v_player_id FOR SHARE;
        PERFORM 1 FROM h_data.drill_results x WHERE x.drill_result_id = v_result_id FOR SHARE;
        SELECT p.display_name, p.deleted_at INTO v_name, v_player_at FROM h_data.players p WHERE p.player_id = v_player_id;
        IF v_player_at IS NOT NULL THEN RAISE EXCEPTION '% was removed; restore the player first', v_name; END IF;
        IF (SELECT r.deleted_at FROM h_data.drill_results r WHERE r.drill_result_id = v_result_id) IS NOT NULL THEN
            RAISE EXCEPTION 'The drill result of this attempt is deleted; restore the drill result first';
        END IF;
        UPDATE h_data.drill_attempts t SET deleted_at = NULL WHERE t.drill_attempt_id = p_id;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_rows;
        UPDATE h_data.drill_attempt_outcomes t SET deleted_at = NULL WHERE t.drill_attempt_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.shot_entries t SET deleted_at = NULL WHERE t.drill_attempt_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.free_throw_entries t SET deleted_at = NULL WHERE t.drill_attempt_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'practice_drill' THEN
        SELECT t.deleted_at, t.practice_session_id, t.practice_drill_set_id
        INTO v_at, v_session_id, v_pds_id
        FROM h_data.practice_drills t WHERE t.practice_drill_id = p_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'practice_drill % does not exist', p_id; END IF;
        IF v_at IS NULL THEN RAISE EXCEPTION 'practice_drill % is not deleted', p_id; END IF;
        -- Lock order (header): the players of the restorable results, the
        -- practice, their participant rows, the placed set.
        PERFORM 1 FROM h_data.players x
        WHERE x.player_id IN (SELECT r.player_id FROM h_data.drill_results r
                              WHERE r.practice_drill_id = p_id AND r.deleted_at = v_at)
        ORDER BY x.player_id FOR SHARE;
        PERFORM 1 FROM h_data.practice_sessions x WHERE x.practice_session_id = v_session_id FOR SHARE;
        PERFORM 1 FROM h_data.practice_players x
        WHERE x.practice_player_id IN (SELECT r.practice_player_id FROM h_data.drill_results r
                                       WHERE r.practice_drill_id = p_id AND r.deleted_at = v_at)
        ORDER BY x.practice_player_id FOR SHARE;
        PERFORM 1 FROM h_data.practice_drill_sets x WHERE x.practice_drill_set_id = v_pds_id FOR SHARE;
        IF (SELECT ps.deleted_at FROM h_data.practice_sessions ps WHERE ps.practice_session_id = v_session_id) IS NOT NULL THEN
            RAISE EXCEPTION 'The practice of this drill is deleted; restore the practice first';
        END IF;
        IF v_pds_id IS NOT NULL
           AND (SELECT pds.deleted_at FROM h_data.practice_drill_sets pds WHERE pds.practice_drill_set_id = v_pds_id) IS NOT NULL THEN
            RAISE EXCEPTION 'The drill set of this practice drill is deleted; restore the drill set first';
        END IF;
        SELECT pd.drill_version_id INTO v_version_id FROM h_data.practice_drills pd WHERE pd.practice_drill_id = p_id;

        -- A4 collision check (backstop), before any row is cleared.
        SELECT p.display_name, d.drill_name INTO v_name, v_drill
        FROM h_data.drill_results r
        JOIN h_data.practice_players pp ON pp.practice_player_id = r.practice_player_id
        JOIN h_data.players p ON p.player_id = r.player_id
        JOIN h_data.drill_versions dv ON dv.drill_version_id = r.drill_version_id
        JOIN h_data.drills d ON d.drill_id = dv.drill_id
        WHERE r.practice_drill_id = p_id AND r.deleted_at = v_at AND r.drill_version_id = v_version_id
          AND pp.deleted_at IS NULL
          AND EXISTS (SELECT 1 FROM h_data.drill_results x
                      WHERE x.practice_drill_id = r.practice_drill_id AND x.practice_player_id = r.practice_player_id
                        AND x.deleted_at IS NULL)
        LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'This drill cannot be restored: % already has another result for %; delete that result first.', v_name, v_drill;
        END IF;

        -- A4 hand-over: a result whose participant row was removed together with
        -- its profile takes the profile's deleted_at (with its chain), so it
        -- returns when the profile is restored. Participants removed on their
        -- own earlier keep their results deleted (A3).
        SELECT array_agg(r.drill_result_id) INTO v_ids
        FROM h_data.drill_results r
        JOIN h_data.practice_players pp ON pp.practice_player_id = r.practice_player_id
        JOIN h_data.players p ON p.player_id = r.player_id
        WHERE r.practice_drill_id = p_id AND r.deleted_at = v_at AND r.drill_version_id = v_version_id
          AND p.deleted_at IS NOT NULL AND pp.deleted_at = p.deleted_at;
        IF v_ids IS NOT NULL THEN
            UPDATE h_data.drill_attempt_outcomes t SET deleted_at = p.deleted_at
            FROM h_data.drill_attempts a
            JOIN h_data.players p ON p.player_id = a.player_id
            WHERE t.drill_attempt_id = a.drill_attempt_id AND a.drill_result_id = ANY (v_ids) AND t.deleted_at = v_at;
            UPDATE h_data.shot_entries t SET deleted_at = p.deleted_at
            FROM h_data.drill_attempts a
            JOIN h_data.players p ON p.player_id = a.player_id
            WHERE t.drill_attempt_id = a.drill_attempt_id AND a.drill_result_id = ANY (v_ids) AND t.deleted_at = v_at;
            UPDATE h_data.free_throw_entries t SET deleted_at = p.deleted_at
            FROM h_data.drill_attempts a
            JOIN h_data.players p ON p.player_id = a.player_id
            WHERE t.drill_attempt_id = a.drill_attempt_id AND a.drill_result_id = ANY (v_ids) AND t.deleted_at = v_at;
            UPDATE h_data.drill_attempts t SET deleted_at = p.deleted_at
            FROM h_data.players p
            WHERE p.player_id = t.player_id AND t.drill_result_id = ANY (v_ids) AND t.deleted_at = v_at;
            UPDATE h_data.drill_results t SET deleted_at = p.deleted_at
            FROM h_data.players p
            WHERE p.player_id = t.player_id AND t.drill_result_id = ANY (v_ids);
        END IF;

        UPDATE h_data.practice_drills t SET deleted_at = NULL WHERE t.practice_drill_id = p_id;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_rows;
        UPDATE h_data.drill_results t SET deleted_at = NULL
        WHERE t.practice_drill_id = p_id
          AND t.deleted_at = v_at
          AND t.drill_version_id = (SELECT pd.drill_version_id FROM h_data.practice_drills pd WHERE pd.practice_drill_id = p_id)
          AND EXISTS (SELECT 1 FROM h_data.practice_players pp
                      WHERE pp.practice_player_id = t.practice_player_id
                        AND pp.deleted_at IS NULL);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_attempts t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.drill_result_id IN (SELECT r.drill_result_id FROM h_data.drill_results r
                                    WHERE r.practice_drill_id = p_id AND r.deleted_at IS NULL);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_attempt_outcomes t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.drill_attempt_id IN (
              SELECT a.drill_attempt_id FROM h_data.drill_attempts a
              WHERE a.drill_result_id IN (SELECT r.drill_result_id FROM h_data.drill_results r
                                          WHERE r.practice_drill_id = p_id AND r.deleted_at IS NULL));
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.shot_entries t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.drill_attempt_id IN (
              SELECT a.drill_attempt_id FROM h_data.drill_attempts a
              WHERE a.drill_result_id IN (SELECT r.drill_result_id FROM h_data.drill_results r
                                          WHERE r.practice_drill_id = p_id AND r.deleted_at IS NULL));
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.free_throw_entries t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.drill_attempt_id IN (
              SELECT a.drill_attempt_id FROM h_data.drill_attempts a
              WHERE a.drill_result_id IN (SELECT r.drill_result_id FROM h_data.drill_results r
                                          WHERE r.practice_drill_id = p_id AND r.deleted_at IS NULL));
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'practice_drill_set' THEN
        SELECT t.deleted_at, t.practice_session_id
        INTO v_at, v_session_id
        FROM h_data.practice_drill_sets t WHERE t.practice_drill_set_id = p_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'practice_drill_set % does not exist', p_id; END IF;
        IF v_at IS NULL THEN RAISE EXCEPTION 'practice_drill_set % is not deleted', p_id; END IF;
        -- Lock order (header): the players of the restorable results, the
        -- practice, their participant rows.
        PERFORM 1 FROM h_data.players x
        WHERE x.player_id IN (
            SELECT r.player_id FROM h_data.drill_results r
            JOIN h_data.practice_drills pd ON pd.practice_drill_id = r.practice_drill_id
            WHERE pd.practice_drill_set_id = p_id AND pd.deleted_at = v_at AND r.deleted_at = v_at)
        ORDER BY x.player_id FOR SHARE;
        PERFORM 1 FROM h_data.practice_sessions x WHERE x.practice_session_id = v_session_id FOR SHARE;
        PERFORM 1 FROM h_data.practice_players x
        WHERE x.practice_player_id IN (
            SELECT r.practice_player_id FROM h_data.drill_results r
            JOIN h_data.practice_drills pd ON pd.practice_drill_id = r.practice_drill_id
            WHERE pd.practice_drill_set_id = p_id AND pd.deleted_at = v_at AND r.deleted_at = v_at)
        ORDER BY x.practice_player_id FOR SHARE;
        IF (SELECT ps.deleted_at FROM h_data.practice_sessions ps WHERE ps.practice_session_id = v_session_id) IS NOT NULL THEN
            RAISE EXCEPTION 'The practice of this drill set is deleted; restore the practice first';
        END IF;

        -- A4 collision check (backstop), before any row is cleared: results that
        -- would come back with the placement's slots (same deleted_at, version
        -- still the slot's, participant row live).
        SELECT p.display_name, d.drill_name INTO v_name, v_drill
        FROM h_data.drill_results r
        JOIN h_data.practice_drills pd ON pd.practice_drill_id = r.practice_drill_id
        JOIN h_data.practice_players pp ON pp.practice_player_id = r.practice_player_id
        JOIN h_data.players p ON p.player_id = r.player_id
        JOIN h_data.drill_versions dv ON dv.drill_version_id = r.drill_version_id
        JOIN h_data.drills d ON d.drill_id = dv.drill_id
        WHERE pd.practice_drill_set_id = p_id AND pd.deleted_at = v_at
          AND r.deleted_at = v_at AND r.drill_version_id = pd.drill_version_id
          AND pp.deleted_at IS NULL
          AND EXISTS (SELECT 1 FROM h_data.drill_results x
                      WHERE x.practice_drill_id = r.practice_drill_id AND x.practice_player_id = r.practice_player_id
                        AND x.deleted_at IS NULL)
        LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'This drill set cannot be restored: % already has another result for %; delete that result first.', v_name, v_drill;
        END IF;

        -- A4 hand-over, as for practice_drill: results whose participant row was
        -- removed together with its profile take the profile's deleted_at.
        SELECT array_agg(r.drill_result_id) INTO v_ids
        FROM h_data.drill_results r
        JOIN h_data.practice_drills pd ON pd.practice_drill_id = r.practice_drill_id
        JOIN h_data.practice_players pp ON pp.practice_player_id = r.practice_player_id
        JOIN h_data.players p ON p.player_id = r.player_id
        WHERE pd.practice_drill_set_id = p_id AND pd.deleted_at = v_at
          AND r.deleted_at = v_at AND r.drill_version_id = pd.drill_version_id
          AND p.deleted_at IS NOT NULL AND pp.deleted_at = p.deleted_at;
        IF v_ids IS NOT NULL THEN
            UPDATE h_data.drill_attempt_outcomes t SET deleted_at = p.deleted_at
            FROM h_data.drill_attempts a
            JOIN h_data.players p ON p.player_id = a.player_id
            WHERE t.drill_attempt_id = a.drill_attempt_id AND a.drill_result_id = ANY (v_ids) AND t.deleted_at = v_at;
            UPDATE h_data.shot_entries t SET deleted_at = p.deleted_at
            FROM h_data.drill_attempts a
            JOIN h_data.players p ON p.player_id = a.player_id
            WHERE t.drill_attempt_id = a.drill_attempt_id AND a.drill_result_id = ANY (v_ids) AND t.deleted_at = v_at;
            UPDATE h_data.free_throw_entries t SET deleted_at = p.deleted_at
            FROM h_data.drill_attempts a
            JOIN h_data.players p ON p.player_id = a.player_id
            WHERE t.drill_attempt_id = a.drill_attempt_id AND a.drill_result_id = ANY (v_ids) AND t.deleted_at = v_at;
            UPDATE h_data.drill_attempts t SET deleted_at = p.deleted_at
            FROM h_data.players p
            WHERE p.player_id = t.player_id AND t.drill_result_id = ANY (v_ids) AND t.deleted_at = v_at;
            UPDATE h_data.drill_results t SET deleted_at = p.deleted_at
            FROM h_data.players p
            WHERE p.player_id = t.player_id AND t.drill_result_id = ANY (v_ids);
        END IF;

        UPDATE h_data.practice_drill_sets t SET deleted_at = NULL WHERE t.practice_drill_set_id = p_id;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_rows;
        UPDATE h_data.practice_drills t SET deleted_at = NULL WHERE t.practice_drill_set_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_results t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.practice_drill_id IN (SELECT pd.practice_drill_id FROM h_data.practice_drills pd
                                      WHERE pd.practice_drill_set_id = p_id AND pd.deleted_at IS NULL)
          AND t.drill_version_id = (SELECT pd.drill_version_id FROM h_data.practice_drills pd WHERE pd.practice_drill_id = t.practice_drill_id)
          AND EXISTS (SELECT 1 FROM h_data.practice_players pp
                      WHERE pp.practice_player_id = t.practice_player_id
                        AND pp.deleted_at IS NULL);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_attempts t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.drill_result_id IN (
              SELECT r.drill_result_id FROM h_data.drill_results r
              JOIN h_data.practice_drills pd ON pd.practice_drill_id = r.practice_drill_id
              WHERE pd.practice_drill_set_id = p_id AND pd.deleted_at IS NULL AND r.deleted_at IS NULL);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_attempt_outcomes t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.drill_attempt_id IN (
              SELECT a.drill_attempt_id FROM h_data.drill_attempts a
              WHERE a.drill_result_id IN (
                  SELECT r.drill_result_id FROM h_data.drill_results r
                  JOIN h_data.practice_drills pd ON pd.practice_drill_id = r.practice_drill_id
                  WHERE pd.practice_drill_set_id = p_id AND pd.deleted_at IS NULL AND r.deleted_at IS NULL));
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.shot_entries t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.drill_attempt_id IN (
              SELECT a.drill_attempt_id FROM h_data.drill_attempts a
              WHERE a.drill_result_id IN (
                  SELECT r.drill_result_id FROM h_data.drill_results r
                  JOIN h_data.practice_drills pd ON pd.practice_drill_id = r.practice_drill_id
                  WHERE pd.practice_drill_set_id = p_id AND pd.deleted_at IS NULL AND r.deleted_at IS NULL));
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.free_throw_entries t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.drill_attempt_id IN (
              SELECT a.drill_attempt_id FROM h_data.drill_attempts a
              WHERE a.drill_result_id IN (
                  SELECT r.drill_result_id FROM h_data.drill_results r
                  JOIN h_data.practice_drills pd ON pd.practice_drill_id = r.practice_drill_id
                  WHERE pd.practice_drill_set_id = p_id AND pd.deleted_at IS NULL AND r.deleted_at IS NULL));
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'drill' THEN
        SELECT t.deleted_at, t.drill_name INTO v_at, v_name FROM h_data.drills t WHERE t.drill_id = p_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'drill % does not exist', p_id; END IF;
        IF v_at IS NULL THEN RAISE EXCEPTION 'drill % is not deleted', p_id; END IF;
        -- A4.1: deleted in the step that created it (or one of its versions) -> its
        -- contents cannot be told apart from rows removed during creation.
        IF EXISTS (SELECT 1 FROM h_data.drills d WHERE d.drill_id = p_id AND d.created_at = v_at)
           OR EXISTS (SELECT 1 FROM h_data.drill_versions v WHERE v.drill_id = p_id AND v.created_at = v_at) THEN
            RAISE EXCEPTION 'This drill was removed in the same step that created it and cannot be restored; create it again';
        END IF;
        IF EXISTS (SELECT 1 FROM h_data.drills d
                   WHERE lower(d.drill_name) = lower(v_name) AND d.deleted_at IS NULL) THEN
            RAISE EXCEPTION 'The name "%" is now used by another drill; rename that drill before restoring this one', v_name;
        END IF;
        UPDATE h_data.drills t SET deleted_at = NULL WHERE t.drill_id = p_id;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_rows;
        UPDATE h_data.drill_secondary_types t SET deleted_at = NULL WHERE t.drill_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_versions t SET deleted_at = NULL WHERE t.drill_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_spots t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.drill_version_id IN (SELECT v.drill_version_id FROM h_data.drill_versions v WHERE v.drill_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_outcome_groups t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.drill_version_id IN (SELECT v.drill_version_id FROM h_data.drill_versions v WHERE v.drill_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_outcomes t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.outcome_group_id IN (
              SELECT g.outcome_group_id FROM h_data.drill_outcome_groups g
              JOIN h_data.drill_versions v ON v.drill_version_id = g.drill_version_id
              WHERE v.drill_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'drill_set' THEN
        SELECT t.deleted_at, t.set_name INTO v_at, v_name FROM h_data.drill_sets t WHERE t.drill_set_id = p_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'drill_set % does not exist', p_id; END IF;
        IF v_at IS NULL THEN RAISE EXCEPTION 'drill_set % is not deleted', p_id; END IF;
        -- A4.1: as for drills.
        IF EXISTS (SELECT 1 FROM h_data.drill_sets ds WHERE ds.drill_set_id = p_id AND ds.created_at = v_at)
           OR EXISTS (SELECT 1 FROM h_data.drill_set_versions v WHERE v.drill_set_id = p_id AND v.created_at = v_at) THEN
            RAISE EXCEPTION 'This drill set was removed in the same step that created it and cannot be restored; create it again';
        END IF;
        IF EXISTS (SELECT 1 FROM h_data.drill_sets ds
                   WHERE lower(ds.set_name) = lower(v_name) AND ds.deleted_at IS NULL) THEN
            RAISE EXCEPTION 'The name "%" is now used by another drill set; rename that set before restoring this one', v_name;
        END IF;
        UPDATE h_data.drill_sets t SET deleted_at = NULL WHERE t.drill_set_id = p_id;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_rows;
        UPDATE h_data.drill_set_versions t SET deleted_at = NULL WHERE t.drill_set_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_set_items t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.drill_set_version_id IN (SELECT v.drill_set_version_id FROM h_data.drill_set_versions v WHERE v.drill_set_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_set_lines t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.drill_set_version_id IN (SELECT v.drill_set_version_id FROM h_data.drill_set_versions v WHERE v.drill_set_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'practice_template' THEN
        SELECT t.deleted_at, t.template_name INTO v_at, v_name FROM h_data.practice_templates t WHERE t.practice_template_id = p_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'practice_template % does not exist', p_id; END IF;
        IF v_at IS NULL THEN RAISE EXCEPTION 'practice_template % is not deleted', p_id; END IF;
        -- A4.1: as for drills.
        IF EXISTS (SELECT 1 FROM h_data.practice_templates pt WHERE pt.practice_template_id = p_id AND pt.created_at = v_at)
           OR EXISTS (SELECT 1 FROM h_data.practice_template_versions v WHERE v.practice_template_id = p_id AND v.created_at = v_at) THEN
            RAISE EXCEPTION 'This practice template was removed in the same step that created it and cannot be restored; create it again';
        END IF;
        IF EXISTS (SELECT 1 FROM h_data.practice_templates pt
                   WHERE lower(pt.template_name) = lower(v_name) AND pt.deleted_at IS NULL) THEN
            RAISE EXCEPTION 'The name "%" is now used by another practice template; rename that template before restoring this one', v_name;
        END IF;
        UPDATE h_data.practice_templates t SET deleted_at = NULL WHERE t.practice_template_id = p_id;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_rows;
        UPDATE h_data.practice_template_versions t SET deleted_at = NULL WHERE t.practice_template_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.practice_template_sets t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.practice_template_version_id IN (
              SELECT v.practice_template_version_id FROM h_data.practice_template_versions v WHERE v.practice_template_id = p_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'player' THEN
        SELECT t.deleted_at, t.display_name INTO v_at, v_name FROM h_data.players t WHERE t.player_id = p_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'player % does not exist', p_id; END IF;
        IF v_at IS NULL THEN RAISE EXCEPTION 'player % is not deleted', p_id; END IF;
        -- Lock order (header): the profile (as strongly as its UPDATE below), then
        -- the practices of its restorable participant rows, then the practice
        -- drills of its restorable results; their state decides restore or
        -- hand-over below.
        PERFORM 1 FROM h_data.players x WHERE x.player_id = p_id FOR NO KEY UPDATE;
        PERFORM 1 FROM h_data.practice_sessions x
        WHERE x.practice_session_id IN (SELECT pp.practice_session_id FROM h_data.practice_players pp
                                        WHERE pp.player_id = p_id AND pp.deleted_at = v_at)
        ORDER BY x.practice_session_id FOR SHARE;
        PERFORM 1 FROM h_data.practice_drills x
        WHERE x.practice_drill_id IN (SELECT r.practice_drill_id FROM h_data.drill_results r
                                      WHERE r.player_id = p_id AND r.deleted_at = v_at)
        ORDER BY x.practice_drill_id FOR SHARE;
        IF EXISTS (SELECT 1 FROM h_data.players p
                   WHERE lower(p.display_name) = lower(v_name) AND p.deleted_at IS NULL) THEN
            RAISE EXCEPTION 'The name "%" is now used by another player; rename that player before restoring this one', v_name;
        END IF;

        -- A4 collision check (backstop; impossible through the app because
        -- nothing can be added for a removed profile), before any row is cleared.
        SELECT to_char(ps.session_date, 'YYYY-MM-DD') INTO v_date
        FROM h_data.practice_players pp
        JOIN h_data.practice_sessions ps ON ps.practice_session_id = pp.practice_session_id
        WHERE pp.player_id = p_id AND pp.deleted_at = v_at AND ps.deleted_at IS NULL
          AND EXISTS (SELECT 1 FROM h_data.practice_players x
                      WHERE x.practice_session_id = pp.practice_session_id AND x.player_id = pp.player_id
                        AND x.deleted_at IS NULL)
        LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'This player cannot be restored: % was added again to the practice of %; remove that player first.', v_name, v_date;
        END IF;
        SELECT d.drill_name INTO v_drill
        FROM h_data.drill_results r
        JOIN h_data.drill_versions dv ON dv.drill_version_id = r.drill_version_id
        JOIN h_data.drills d ON d.drill_id = dv.drill_id
        WHERE r.player_id = p_id AND r.deleted_at = v_at
          AND EXISTS (SELECT 1 FROM h_data.drill_results x
                      WHERE x.practice_drill_id = r.practice_drill_id AND x.practice_player_id = r.practice_player_id
                        AND x.deleted_at IS NULL)
        LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'This player cannot be restored: % already has another result for %; delete that result first.', v_name, v_drill;
        END IF;

        -- 1. The profile.
        UPDATE h_data.players t SET deleted_at = NULL WHERE t.player_id = p_id;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_rows;
        -- 2. Seasons first, so restored rows find their seasons intact.
        UPDATE h_data.seasons t SET deleted_at = NULL WHERE t.player_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        -- 3. Games, then their entries.
        UPDATE h_data.games t SET deleted_at = NULL WHERE t.player_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.shot_entries t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.game_id IN (SELECT g.game_id FROM h_data.games g WHERE g.player_id = p_id AND g.deleted_at IS NULL);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.free_throw_entries t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.game_id IN (SELECT g.game_id FROM h_data.games g WHERE g.player_id = p_id AND g.deleted_at IS NULL);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        -- 4. Goals, then physical measurements.
        UPDATE h_data.goals t SET deleted_at = NULL WHERE t.player_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.physical_measurements t SET deleted_at = NULL WHERE t.player_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

        -- 5. Hand-over to a deleted practice: a participant row (and its chain)
        --    whose practice is now deleted takes the practice's deleted_at, so it
        --    returns when the practice is restored.
        UPDATE h_data.drill_attempt_outcomes t SET deleted_at = ps.deleted_at
        FROM h_data.drill_attempts a
        JOIN h_data.practice_sessions ps ON ps.practice_session_id = a.practice_session_id
        WHERE t.drill_attempt_id = a.drill_attempt_id AND a.player_id = p_id
          AND ps.deleted_at IS NOT NULL AND t.deleted_at = v_at;
        UPDATE h_data.drill_attempts t SET deleted_at = ps.deleted_at
        FROM h_data.practice_sessions ps
        WHERE ps.practice_session_id = t.practice_session_id AND ps.deleted_at IS NOT NULL
          AND t.player_id = p_id AND t.deleted_at = v_at;
        UPDATE h_data.shot_entries t SET deleted_at = ps.deleted_at
        FROM h_data.practice_sessions ps
        WHERE ps.practice_session_id = t.practice_session_id AND ps.deleted_at IS NOT NULL
          AND t.player_id = p_id AND t.deleted_at = v_at;
        UPDATE h_data.free_throw_entries t SET deleted_at = ps.deleted_at
        FROM h_data.practice_sessions ps
        WHERE ps.practice_session_id = t.practice_session_id AND ps.deleted_at IS NOT NULL
          AND t.player_id = p_id AND t.deleted_at = v_at;
        UPDATE h_data.drill_results t SET deleted_at = ps.deleted_at
        FROM h_data.practice_sessions ps
        WHERE ps.practice_session_id = t.practice_session_id AND ps.deleted_at IS NOT NULL
          AND t.player_id = p_id AND t.deleted_at = v_at;
        UPDATE h_data.practice_players t SET deleted_at = ps.deleted_at
        FROM h_data.practice_sessions ps
        WHERE ps.practice_session_id = t.practice_session_id AND ps.deleted_at IS NOT NULL
          AND t.player_id = p_id AND t.deleted_at = v_at;

        -- 6. Participant rows of live practices.
        UPDATE h_data.practice_players t SET deleted_at = NULL WHERE t.player_id = p_id AND t.deleted_at = v_at;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

        -- 7. Hand-over to a deleted practice drill (live practice): the result
        --    and its chain take the practice drill's deleted_at, so they return
        --    when the practice drill (or its drill set) is restored.
        SELECT array_agg(r.drill_result_id) INTO v_ids
        FROM h_data.drill_results r
        JOIN h_data.practice_drills pd ON pd.practice_drill_id = r.practice_drill_id
        WHERE r.player_id = p_id AND r.deleted_at = v_at AND pd.deleted_at IS NOT NULL;
        IF v_ids IS NOT NULL THEN
            UPDATE h_data.drill_attempt_outcomes t SET deleted_at = pd.deleted_at
            FROM h_data.drill_attempts a
            JOIN h_data.drill_results r ON r.drill_result_id = a.drill_result_id
            JOIN h_data.practice_drills pd ON pd.practice_drill_id = r.practice_drill_id
            WHERE t.drill_attempt_id = a.drill_attempt_id AND a.drill_result_id = ANY (v_ids) AND t.deleted_at = v_at;
            UPDATE h_data.shot_entries t SET deleted_at = pd.deleted_at
            FROM h_data.drill_attempts a
            JOIN h_data.drill_results r ON r.drill_result_id = a.drill_result_id
            JOIN h_data.practice_drills pd ON pd.practice_drill_id = r.practice_drill_id
            WHERE t.drill_attempt_id = a.drill_attempt_id AND a.drill_result_id = ANY (v_ids) AND t.deleted_at = v_at;
            UPDATE h_data.free_throw_entries t SET deleted_at = pd.deleted_at
            FROM h_data.drill_attempts a
            JOIN h_data.drill_results r ON r.drill_result_id = a.drill_result_id
            JOIN h_data.practice_drills pd ON pd.practice_drill_id = r.practice_drill_id
            WHERE t.drill_attempt_id = a.drill_attempt_id AND a.drill_result_id = ANY (v_ids) AND t.deleted_at = v_at;
            UPDATE h_data.drill_attempts t SET deleted_at = pd.deleted_at
            FROM h_data.drill_results r
            JOIN h_data.practice_drills pd ON pd.practice_drill_id = r.practice_drill_id
            WHERE r.drill_result_id = t.drill_result_id AND t.drill_result_id = ANY (v_ids) AND t.deleted_at = v_at;
            UPDATE h_data.drill_results t SET deleted_at = pd.deleted_at
            FROM h_data.practice_drills pd
            WHERE pd.practice_drill_id = t.practice_drill_id AND t.drill_result_id = ANY (v_ids);
        END IF;

        -- 8. Results on live practice drills whose participant row is live and
        --    whose drill version is still the slot's (a result recorded for a
        --    drill since swapped out stays deleted), then attempts, outcome taps
        --    and entries under live parents.
        UPDATE h_data.drill_results t SET deleted_at = NULL
        WHERE t.player_id = p_id AND t.deleted_at = v_at
          AND EXISTS (SELECT 1 FROM h_data.practice_players pp
                      WHERE pp.practice_player_id = t.practice_player_id AND pp.deleted_at IS NULL)
          AND EXISTS (SELECT 1 FROM h_data.practice_drills pd
                      WHERE pd.practice_drill_id = t.practice_drill_id AND pd.deleted_at IS NULL
                        AND pd.drill_version_id = t.drill_version_id);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_attempts t SET deleted_at = NULL
        WHERE t.player_id = p_id AND t.deleted_at = v_at
          AND EXISTS (SELECT 1 FROM h_data.drill_results r
                      WHERE r.drill_result_id = t.drill_result_id AND r.deleted_at IS NULL);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.drill_attempt_outcomes t SET deleted_at = NULL
        WHERE t.deleted_at = v_at
          AND t.drill_attempt_id IN (SELECT a.drill_attempt_id FROM h_data.drill_attempts a
                                     WHERE a.player_id = p_id AND a.deleted_at IS NULL);
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.shot_entries t SET deleted_at = NULL
        WHERE t.player_id = p_id AND t.deleted_at = v_at AND t.practice_player_id IS NOT NULL
          AND EXISTS (SELECT 1 FROM h_data.practice_players pp
                      WHERE pp.practice_player_id = t.practice_player_id AND pp.deleted_at IS NULL)
          AND (t.drill_attempt_id IS NULL
               OR EXISTS (SELECT 1 FROM h_data.drill_attempts a
                          WHERE a.drill_attempt_id = t.drill_attempt_id AND a.deleted_at IS NULL));
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;
        UPDATE h_data.free_throw_entries t SET deleted_at = NULL
        WHERE t.player_id = p_id AND t.deleted_at = v_at AND t.practice_player_id IS NOT NULL
          AND EXISTS (SELECT 1 FROM h_data.practice_players pp
                      WHERE pp.practice_player_id = t.practice_player_id AND pp.deleted_at IS NULL)
          AND (t.drill_attempt_id IS NULL
               OR EXISTS (SELECT 1 FROM h_data.drill_attempts a
                          WHERE a.drill_attempt_id = t.drill_attempt_id AND a.deleted_at IS NULL));
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_total + v_rows;

    WHEN 'drill_type' THEN
        SELECT t.deleted_at, t.type_name INTO v_at, v_name FROM h_data.drill_types t WHERE t.drill_type_id = p_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'drill_type % does not exist', p_id; END IF;
        IF v_at IS NULL THEN RAISE EXCEPTION 'drill_type % is not deleted', p_id; END IF;
        IF EXISTS (SELECT 1 FROM h_data.drill_types dt
                   WHERE lower(dt.type_name) = lower(v_name) AND dt.deleted_at IS NULL) THEN
            RAISE EXCEPTION 'The name "%" is now used by another drill type; rename it before restoring this one', v_name;
        END IF;
        UPDATE h_data.drill_types t SET deleted_at = NULL WHERE t.drill_type_id = p_id;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_rows;

    WHEN 'physical_test' THEN
        SELECT t.deleted_at, t.test_name INTO v_at, v_name FROM h_ref.physical_tests t WHERE t.physical_test_id = p_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'physical_test % does not exist', p_id; END IF;
        IF v_at IS NULL THEN RAISE EXCEPTION 'physical_test % is not deleted', p_id; END IF;
        IF EXISTS (SELECT 1 FROM h_ref.physical_tests pt
                   WHERE lower(pt.test_name) = lower(v_name) AND pt.deleted_at IS NULL) THEN
            RAISE EXCEPTION 'The name "%" is now used by another physical test; rename it before restoring this one', v_name;
        END IF;
        UPDATE h_ref.physical_tests t SET deleted_at = NULL WHERE t.physical_test_id = p_id;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_rows;

    WHEN 'season' THEN
        SELECT t.deleted_at, t.season_name, t.player_id INTO v_at, v_name, v_player_id
        FROM h_data.seasons t WHERE t.season_id = p_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'season % does not exist', p_id; END IF;
        IF v_at IS NULL THEN RAISE EXCEPTION 'season % is not deleted', p_id; END IF;
        -- Lock order (header): the player, FOR UPDATE because tf_seasons_no_overlap
        -- locks it so in this transaction (a share lock here would be upgraded).
        PERFORM 1 FROM h_data.players x WHERE x.player_id = v_player_id FOR UPDATE;
        SELECT p.display_name, p.deleted_at INTO v_player_name, v_player_at FROM h_data.players p WHERE p.player_id = v_player_id;
        IF v_player_at IS NOT NULL THEN RAISE EXCEPTION '% was removed; restore the player first', v_player_name; END IF;
        IF EXISTS (SELECT 1 FROM h_data.seasons s
                   WHERE s.player_id = v_player_id AND lower(s.season_name) = lower(v_name) AND s.deleted_at IS NULL) THEN
            RAISE EXCEPTION 'The name "%" is now used by another season; rename it before restoring this one', v_name;
        END IF;
        -- The overlap trigger raises here if the dates now overlap another season.
        UPDATE h_data.seasons t SET deleted_at = NULL WHERE t.season_id = p_id;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_rows;

    WHEN 'goal' THEN
        SELECT t.deleted_at, t.player_id INTO v_at, v_player_id FROM h_data.goals t WHERE t.goal_id = p_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'goal % does not exist', p_id; END IF;
        IF v_at IS NULL THEN RAISE EXCEPTION 'goal % is not deleted', p_id; END IF;
        -- Lock order (header): the player.
        PERFORM 1 FROM h_data.players x WHERE x.player_id = v_player_id FOR SHARE;
        SELECT p.display_name, p.deleted_at INTO v_name, v_player_at FROM h_data.players p WHERE p.player_id = v_player_id;
        IF v_player_at IS NOT NULL THEN RAISE EXCEPTION '% was removed; restore the player first', v_name; END IF;
        UPDATE h_data.goals t SET deleted_at = NULL WHERE t.goal_id = p_id;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_rows;

    WHEN 'physical_measurement' THEN
        SELECT t.deleted_at, t.player_id INTO v_at, v_player_id FROM h_data.physical_measurements t WHERE t.physical_measurement_id = p_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'physical_measurement % does not exist', p_id; END IF;
        IF v_at IS NULL THEN RAISE EXCEPTION 'physical_measurement % is not deleted', p_id; END IF;
        -- Lock order (header): the player.
        PERFORM 1 FROM h_data.players x WHERE x.player_id = v_player_id FOR SHARE;
        SELECT p.display_name, p.deleted_at INTO v_name, v_player_at FROM h_data.players p WHERE p.player_id = v_player_id;
        IF v_player_at IS NOT NULL THEN RAISE EXCEPTION '% was removed; restore the player first', v_name; END IF;
        UPDATE h_data.physical_measurements t SET deleted_at = NULL WHERE t.physical_measurement_id = p_id;
        GET DIAGNOSTICS v_rows = ROW_COUNT; v_total := v_rows;

    ELSE
        RAISE EXCEPTION 'Unknown entity "%" for restore', p_entity;
    END CASE;

    RETURN v_total;
END
$$;
COMMENT ON FUNCTION h_data.fn_restore(text, bigint) IS
    'REQ-006, COD-22-02/03: restores a soft-deleted entity and the children deleted with it (same deleted_at), parents first; undo only. Bulk restores refuse on a collision and change nothing; rows whose other parent (practice, practice drill or player profile) is deleted are handed over to that parent''s deleted_at instead of being made live; player-owned rows are refused while their profile is removed (Amendment A4). A drill, drill set or practice template deleted in the step that created it (or created a version of it) is refused (A4.1). BINDING (A4.1, REQ-018): the player branch clears seasons FIRST, so restored games and participant rows recompute against them; do not reorder. BINDING (review_3): every branch that checks a parent or the profile locks the checked rows first, top-down (player, practice or game, participant row, placed set, practice drill, drill result, drill attempt), before reading deleted_at, in the same order as the insert triggers and fn_soft_delete.';


-- =============================================================================
-- Part B3: view
-- =============================================================================

-- h_data.v_recently_deleted (REQ-006): independently deleted rows only (a child
-- deleted together with its parent is listed through the parent). A removed
-- participant row (practice_players) is never listed, and neither is any record
-- carrying its practice_player_id: a practice record is listed only when its
-- participant row is live or was deleted together with the whole practice (same
-- deleted_at; the practice is then listed and restores it) (Amendment A3). A
-- player-owned row (game, season, goal, measurement, entry, result, attempt) is
-- listed only while its player's profile is live; a removed profile is listed
-- once, as "Player: name", and stands for everything removed with it (Amendment
-- A4). Versions and version contents have no arm: they are never deleted on
-- their own. Reads the retention setting from the table, not through
-- h_system.fn_setting_int, so the read-only role (which has no EXECUTE in
-- h_system) can use the view.
CREATE VIEW h_data.v_recently_deleted AS
WITH items AS (
    SELECT 'game'::text AS entity,
           g.game_id AS entity_id,
           'Game ' || to_char(g.game_date, 'YYYY-MM-DD') || COALESCE(' vs ' || g.opponent_name, '') AS label,
           g.deleted_at,
           g.updated_by AS deleted_by
    FROM h_data.games g
    JOIN h_data.players p ON p.player_id = g.player_id
    WHERE g.deleted_at IS NOT NULL
      AND p.deleted_at IS NULL

    UNION ALL
    SELECT 'practice_session', ps.practice_session_id,
           'Practice ' || to_char(ps.session_date, 'YYYY-MM-DD') || ' (' || pt.label || ')',
           ps.deleted_at, ps.updated_by
    FROM h_data.practice_sessions ps
    JOIN h_ref.practice_types pt ON pt.practice_type_code = ps.practice_type_code
    WHERE ps.deleted_at IS NOT NULL

    UNION ALL
    SELECT 'shot_entry', se.shot_entry_id,
           'Shot: '
           || CASE WHEN se.court_spot_id IS NOT NULL THEN 'spot ' || cs.spot_number
                   ELSE 'manual ' || se.manual_point_value || '-pointer' END
           || ', ' || se.makes || ' of ' || se.attempts
           || ', ' || p.display_name
           || ', ' || to_char(COALESCE(g.game_date, ps.session_date), 'YYYY-MM-DD'),
           se.deleted_at, se.updated_by
    FROM h_data.shot_entries se
    JOIN h_data.players p ON p.player_id = se.player_id
    LEFT JOIN h_data.games g ON g.game_id = se.game_id
    LEFT JOIN h_data.practice_sessions ps ON ps.practice_session_id = se.practice_session_id
    LEFT JOIN h_ref.court_spots cs ON cs.court_spot_id = se.court_spot_id
    LEFT JOIN h_data.practice_players pp ON pp.practice_player_id = se.practice_player_id
    LEFT JOIN h_data.drill_attempts da ON da.drill_attempt_id = se.drill_attempt_id
    WHERE se.deleted_at IS NOT NULL
      AND p.deleted_at IS NULL
      AND se.deleted_at IS DISTINCT FROM COALESCE(g.deleted_at, ps.deleted_at)
      AND (se.practice_player_id IS NULL OR pp.deleted_at IS NULL OR pp.deleted_at = ps.deleted_at)
      AND se.deleted_at IS DISTINCT FROM da.deleted_at

    UNION ALL
    SELECT 'free_throw_entry', fe.free_throw_entry_id,
           'Free throws: ' || fe.makes || ' of ' || fe.attempts
           || ', ' || p.display_name
           || ', ' || to_char(COALESCE(g.game_date, ps.session_date), 'YYYY-MM-DD'),
           fe.deleted_at, fe.updated_by
    FROM h_data.free_throw_entries fe
    JOIN h_data.players p ON p.player_id = fe.player_id
    LEFT JOIN h_data.games g ON g.game_id = fe.game_id
    LEFT JOIN h_data.practice_sessions ps ON ps.practice_session_id = fe.practice_session_id
    LEFT JOIN h_data.practice_players pp ON pp.practice_player_id = fe.practice_player_id
    LEFT JOIN h_data.drill_attempts da ON da.drill_attempt_id = fe.drill_attempt_id
    WHERE fe.deleted_at IS NOT NULL
      AND p.deleted_at IS NULL
      AND fe.deleted_at IS DISTINCT FROM COALESCE(g.deleted_at, ps.deleted_at)
      AND (fe.practice_player_id IS NULL OR pp.deleted_at IS NULL OR pp.deleted_at = ps.deleted_at)
      AND fe.deleted_at IS DISTINCT FROM da.deleted_at

    UNION ALL
    SELECT 'drill_result', r.drill_result_id,
           'Drill result: ' || d.drill_name || ' (' || p.display_name || '), ' || to_char(ps.session_date, 'YYYY-MM-DD'),
           r.deleted_at, r.updated_by
    FROM h_data.drill_results r
    JOIN h_data.practice_sessions ps ON ps.practice_session_id = r.practice_session_id
    JOIN h_data.practice_drills pd ON pd.practice_drill_id = r.practice_drill_id
    JOIN h_data.practice_players pp ON pp.practice_player_id = r.practice_player_id
    JOIN h_data.players p ON p.player_id = r.player_id
    JOIN h_data.drill_versions dv ON dv.drill_version_id = r.drill_version_id
    JOIN h_data.drills d ON d.drill_id = dv.drill_id
    WHERE r.deleted_at IS NOT NULL
      AND p.deleted_at IS NULL
      AND r.deleted_at IS DISTINCT FROM ps.deleted_at
      AND r.deleted_at IS DISTINCT FROM pd.deleted_at
      AND (pp.deleted_at IS NULL OR pp.deleted_at = ps.deleted_at)

    UNION ALL
    SELECT 'drill_attempt', a.drill_attempt_id,
           'Drill attempt ' || a.attempt_no || ': ' || d.drill_name || ' (' || p.display_name || '), '
           || to_char(ps.session_date, 'YYYY-MM-DD'),
           a.deleted_at, a.updated_by
    FROM h_data.drill_attempts a
    JOIN h_data.drill_results r ON r.drill_result_id = a.drill_result_id
    JOIN h_data.drill_versions dv ON dv.drill_version_id = r.drill_version_id
    JOIN h_data.drills d ON d.drill_id = dv.drill_id
    JOIN h_data.players p ON p.player_id = a.player_id
    JOIN h_data.practice_sessions ps ON ps.practice_session_id = a.practice_session_id
    JOIN h_data.practice_players pp ON pp.practice_player_id = a.practice_player_id
    WHERE a.deleted_at IS NOT NULL
      AND p.deleted_at IS NULL
      AND a.deleted_at IS DISTINCT FROM r.deleted_at
      AND (pp.deleted_at IS NULL OR pp.deleted_at = ps.deleted_at)

    UNION ALL
    SELECT 'practice_drill', pd.practice_drill_id,
           'Practice drill: ' || d.drill_name || ', ' || to_char(ps.session_date, 'YYYY-MM-DD'),
           pd.deleted_at, pd.updated_by
    FROM h_data.practice_drills pd
    JOIN h_data.practice_sessions ps ON ps.practice_session_id = pd.practice_session_id
    JOIN h_data.drill_versions dv ON dv.drill_version_id = pd.drill_version_id
    JOIN h_data.drills d ON d.drill_id = dv.drill_id
    LEFT JOIN h_data.practice_drill_sets pds ON pds.practice_drill_set_id = pd.practice_drill_set_id
    WHERE pd.deleted_at IS NOT NULL
      AND pd.deleted_at IS DISTINCT FROM ps.deleted_at
      AND pd.deleted_at IS DISTINCT FROM pds.deleted_at

    UNION ALL
    SELECT 'practice_drill_set', pds.practice_drill_set_id,
           'Drill set in practice: ' || ds.set_name || ', ' || to_char(ps.session_date, 'YYYY-MM-DD'),
           pds.deleted_at, pds.updated_by
    FROM h_data.practice_drill_sets pds
    JOIN h_data.practice_sessions ps ON ps.practice_session_id = pds.practice_session_id
    JOIN h_data.drill_set_versions sv ON sv.drill_set_version_id = pds.drill_set_version_id
    JOIN h_data.drill_sets ds ON ds.drill_set_id = sv.drill_set_id
    WHERE pds.deleted_at IS NOT NULL
      AND pds.deleted_at IS DISTINCT FROM ps.deleted_at

    UNION ALL
    SELECT 'drill', d.drill_id, 'Drill: ' || d.drill_name, d.deleted_at, d.updated_by
    FROM h_data.drills d
    WHERE d.deleted_at IS NOT NULL

    UNION ALL
    SELECT 'drill_set', ds.drill_set_id, 'Drill set: ' || ds.set_name, ds.deleted_at, ds.updated_by
    FROM h_data.drill_sets ds
    WHERE ds.deleted_at IS NOT NULL

    UNION ALL
    SELECT 'practice_template', t.practice_template_id, 'Practice template: ' || t.template_name, t.deleted_at, t.updated_by
    FROM h_data.practice_templates t
    WHERE t.deleted_at IS NOT NULL

    UNION ALL
    SELECT 'player', p.player_id, 'Player: ' || p.display_name, p.deleted_at, p.updated_by
    FROM h_data.players p
    WHERE p.deleted_at IS NOT NULL

    UNION ALL
    SELECT 'drill_type', dt.drill_type_id, 'Drill type: ' || dt.type_name, dt.deleted_at, dt.updated_by
    FROM h_data.drill_types dt
    WHERE dt.deleted_at IS NOT NULL

    UNION ALL
    SELECT 'season', s.season_id, 'Season: ' || s.season_name, s.deleted_at, s.updated_by
    FROM h_data.seasons s
    JOIN h_data.players p ON p.player_id = s.player_id
    WHERE s.deleted_at IS NOT NULL
      AND p.deleted_at IS NULL

    UNION ALL
    SELECT 'goal', gl.goal_id, 'Goal: ' || gl.goal_name, gl.deleted_at, gl.updated_by
    FROM h_data.goals gl
    JOIN h_data.players p ON p.player_id = gl.player_id
    WHERE gl.deleted_at IS NOT NULL
      AND p.deleted_at IS NULL

    UNION ALL
    SELECT 'physical_measurement', pm.physical_measurement_id,
           'Measurement: ' || pt.test_name || ' ' || pm.measured_value || ' ' || pt.unit
           || ', ' || to_char(pm.measured_on, 'YYYY-MM-DD'),
           pm.deleted_at, pm.updated_by
    FROM h_data.physical_measurements pm
    JOIN h_ref.physical_tests pt ON pt.physical_test_id = pm.physical_test_id
    JOIN h_data.players p ON p.player_id = pm.player_id
    WHERE pm.deleted_at IS NOT NULL
      AND p.deleted_at IS NULL

    UNION ALL
    SELECT 'physical_test', pt.physical_test_id, 'Physical test: ' || pt.test_name, pt.deleted_at, pt.updated_by
    FROM h_ref.physical_tests pt
    WHERE pt.deleted_at IS NOT NULL
)
SELECT i.entity,
       i.entity_id,
       i.label,
       i.deleted_at,
       i.deleted_by,
       u.display_name AS deleted_by_name,
       i.deleted_at + make_interval(days => r.retention_days) AS purge_after
FROM items i
JOIN h_system.users u ON u.user_id = i.deleted_by
LEFT JOIN (
    SELECT s.setting_value::integer AS retention_days
    FROM h_system.app_settings s
    WHERE s.setting_key = 'soft_delete_retention_days'
      AND s.deleted_at IS NULL
) r ON true;
COMMENT ON VIEW h_data.v_recently_deleted IS
    'REQ-006 Recently deleted page: independently deleted rows with label, who deleted them and when they will be purged. Entity names match h_data.fn_restore (incl. drill_attempt, practice_template, player). Removed practice participants and their records are never listed (they cannot be restored). Entity names match h_data.fn_restore. Removed practice participants and their records are not listed (A3). Rows of a removed profile are not listed; the profile is (A4).';


-- =============================================================================
-- Part C: seed data (plan 6.14.7; player_drill_model plan 6.3.6). Acting user 0.
-- Looked up by name and spot number, never by literal id. Each seeded drill is a
-- lineage plus version 1 (Gate 1, Q4).
-- =============================================================================

INSERT INTO h_data.drill_types (type_name, sort_order) VALUES
    ('Shooting',                 1),
    ('Finishing',                2),
    ('Ball handling',            3),
    ('Passing',                  4),
    ('Footwork and post moves',  5),
    ('Defense',                  6),
    ('Rebounding',               7),
    ('Conditioning',             8),
    ('Strength and agility',     9),
    ('Game situations',         10);

-- The drill catalogue: one row per drill, read by the lineage and version inserts.
CREATE TEMP TABLE seed_drill_catalogue (
    drill_name text, primary_type text, difficulty_code text, result_type_code text,
    default_target integer, fixed_seconds integer, description text
) ON COMMIT DROP;

INSERT INTO seed_drill_catalogue VALUES
    ('Form Shooting Close Range', 'Shooting', 'beginner', 'shooting', 30, NULL,
     'One-hand form shots 3–5 ft from the rim; elbow under the ball, hold the follow-through.'),
    ('Free Throw Routine 50', 'Shooting', 'beginner', 'shooting', 50, NULL,
     '50 free throws using the same pre-shot routine every time.'),
    ('Catch-and-Shoot Corner Threes', 'Shooting', 'intermediate', 'shooting', 20, NULL,
     'Feet set on the catch; 10 threes from each corner.'),
    ('Around the World Mid-Range', 'Shooting', 'intermediate', 'shooting', 25, NULL,
     'Five shots from each of five mid-range spots, baseline to baseline.'),
    ('Elbow Pull-Up Jumpers', 'Shooting', 'intermediate', 'shooting', 20, NULL,
     'One-dribble pull-up jumpers, alternating left and right elbow.'),
    ('Five-Spot Three-Point Shooting', 'Shooting', 'advanced', 'shooting', 50, NULL,
     'Ten threes from each of five spots around the arc.'),
    ('Deep Range Threes', 'Shooting', 'elite', 'shooting', 20, NULL,
     'Threes from well behind the top of the arc, holding form at range.'),
    ('Mikan Drill', 'Finishing', 'beginner', 'fixed_time_reps', 20, 60,
     'Alternating right- and left-hand layups under the rim without the ball touching the floor; count makes.'),
    ('Weak-Hand Layups', 'Finishing', 'beginner', 'shooting', 20, NULL,
     'Layups from both sides finished only with the weaker hand.'),
    ('Full-Court Layup Circuit', 'Finishing', 'intermediate', 'fixed_task_time', 45, NULL,
     'Layup at one end, sprint and layup at the other, four layups in all; time to finish.'),
    ('Floater Series', 'Finishing', 'advanced', 'shooting', 20, NULL,
     'Drive and release a one-hand floater over an imaginary shot-blocker.'),
    ('Contact Finishes with Pad', 'Finishing', 'elite', 'shooting', 20, NULL,
     'A partner bumps with a pad on the way up; finish through the contact.'),
    ('Stationary Dribble Series', 'Ball handling', 'beginner', 'fixed_task_time', 120, NULL,
     'Pound, crossover, between-the-legs and behind-the-back, 20 each hand; time to finish without losing the ball.'),
    ('Cone Zig-Zag Dribble', 'Ball handling', 'intermediate', 'fixed_task_time', 20, NULL,
     'Dribble baseline to baseline around zig-zag cones, changing hands at each cone.'),
    ('Two-Ball Dribble Routine', 'Ball handling', 'advanced', 'fixed_task_time', 150, NULL,
     'Ten two-ball moves, ten reps each; time to finish without losing either ball.'),
    ('Tennis Ball Toss Dribbling', 'Ball handling', 'elite', 'fixed_time_reps', 20, 60,
     'Dribble with one hand while tossing and catching a tennis ball with the other; count clean catches.'),
    ('Partner Chest and Bounce Passes', 'Passing', 'beginner', 'fixed_time_reps', 50, 60,
     'Alternate chest and bounce passes with a partner; count completed passes.'),
    ('Wall Passing Speed Drill', 'Passing', 'intermediate', 'fixed_time_reps', 40, 30,
     'Rapid two-hand passes against a wall from 6 ft; count catches.'),
    ('Two-Man Full-Court Passing', 'Passing', 'advanced', 'fixed_task_time', 12, NULL,
     'Two players pass the length of the court without dribbling and finish with a layup; time to finish.'),
    ('Pocket Pass Reads off the Drive', 'Passing', 'elite', 'outcome_buttons', 10, NULL,
     'Drive, read the help defender and deliver a pocket or kick-out pass; count correct reads out of ten.'),
    ('Jump Stop and Pivot Series', 'Footwork and post moves', 'beginner', 'outcome_buttons', 20, NULL,
     'Jump stop, then front and reverse pivots on each foot; count clean reps without travelling.'),
    ('Drop Step Power Finish', 'Footwork and post moves', 'intermediate', 'shooting', 20, NULL,
     'Catch on the block, drop step to the baseline and finish with a two-foot power layup.'),
    ('Baby Hook Series', 'Footwork and post moves', 'advanced', 'shooting', 20, NULL,
     'Catch on the block, one dribble to the middle, and a short hook with either hand.'),
    ('Spin Move Post Counters', 'Footwork and post moves', 'elite', 'shooting', 20, NULL,
     'Spin baseline or middle from the post and finish; counter a cut-off spin with a step-back.'),
    ('Defensive Slide Lane Drill', 'Defense', 'beginner', 'fixed_time_reps', 12, 30,
     'Slide lane line to lane line in a low stance; count line touches.'),
    ('Closeout and Contain', 'Defense', 'intermediate', 'outcome_buttons', 10, NULL,
     'Sprint to close out on a shooter, chop the feet and contain the first dribble; count stops out of ten.'),
    ('One-on-One Wing Containment', 'Defense', 'advanced', 'outcome_buttons', 10, NULL,
     'Defend a live one-on-one from the wing; count stops out of ten possessions.'),
    ('Deny and Recover', 'Defense', 'elite', 'outcome_buttons', 10, NULL,
     'Deny the wing pass, open up on the back cut and recover to the ball; count successful reps out of ten.'),
    ('Box-Out and Outlet', 'Rebounding', 'beginner', 'outcome_buttons', 10, NULL,
     'Box out on the shot, secure the rebound, pivot and outlet; count clean reps.'),
    ('Putback Finishes', 'Rebounding', 'intermediate', 'shooting', 20, NULL,
     'Toss the ball off the rim, rebound and put it back up without bringing it down.'),
    ('Superman Rebounding', 'Rebounding', 'advanced', 'fixed_time_reps', 10, 30,
     'Throw the ball off the backboard to the far side and sprint across to rebound it above the rim; count rebounds.'),
    ('Rebound Gauntlet with Contact', 'Rebounding', 'elite', 'outcome_buttons', 10, NULL,
     'Fight through a partner''s bump to secure a contested rebound; count wins out of ten.'),
    ('Jump Rope Intervals', 'Conditioning', 'beginner', 'fixed_time_reps', 100, 60,
     'Fast single-unders; count jumps.'),
    ('Line Drills (Suicides)', 'Conditioning', 'intermediate', 'fixed_task_time', 32, NULL,
     'Sprint to the free-throw line, half court, far free-throw line and far baseline, returning each time.'),
    ('Sideline 17s', 'Conditioning', 'advanced', 'fixed_task_time', 65, NULL,
     'Seventeen sideline-to-sideline sprints without stopping.'),
    ('Free Throws Under Fatigue', 'Conditioning', 'elite', 'shooting', 20, NULL,
     'Sprint a full-court lap, then shoot two free throws; repeat ten times.'),
    ('Bodyweight Squats', 'Strength and agility', 'beginner', 'failure_streak', 30, NULL,
     'Full-depth bodyweight squats with good form; count reps.'),
    ('Lane Agility Drill', 'Strength and agility', 'intermediate', 'fixed_task_time', 12, NULL,
     'Sprint, slide, back-pedal and slide around the lane; time to finish.'),
    ('Single-Leg Lateral Bounds', 'Strength and agility', 'advanced', 'failure_streak', 20, NULL,
     'Bound sideways from one leg to the other, sticking each landing; count landings.'),
    ('Depth Jumps', 'Strength and agility', 'elite', 'outcome_buttons', 10, NULL,
     'Step off a box and explode straight up on landing; count quality reps.'),
    ('Fast Break 2-on-1', 'Game situations', 'intermediate', 'outcome_buttons', 10, NULL,
     'Two attackers against one defender in transition; count scores out of ten.'),
    ('Game-Speed Shots off Screens', 'Game situations', 'advanced', 'shooting', 20, NULL,
     'Sprint off an imaginary screen, catch and shoot at game speed.'),
    ('Pick-and-Roll Reads', 'Game situations', 'advanced', 'outcome_buttons', 10, NULL,
     'Use a ball screen and make the right read (shoot, drive or pass); count correct reads out of ten.'),
    ('Last-Shot Situations', 'Game situations', 'elite', 'shooting', 10, NULL,
     'Five-second clock: one or two dribbles into a pull-up from the top.');

INSERT INTO h_data.drills (drill_name, description, primary_drill_type_id, difficulty_code)
SELECT c.drill_name, c.description, dt.drill_type_id, c.difficulty_code
FROM seed_drill_catalogue c
JOIN h_data.drill_types dt ON dt.type_name = c.primary_type AND dt.deleted_at IS NULL;

INSERT INTO h_data.drill_versions (drill_id, result_type_code, default_target, fixed_seconds)
SELECT d.drill_id, c.result_type_code, c.default_target, c.fixed_seconds
FROM seed_drill_catalogue c
JOIN h_data.drills d ON d.drill_name = c.drill_name AND d.deleted_at IS NULL;

INSERT INTO h_data.drill_outcome_groups (drill_version_id, group_no, group_name)
SELECT dv.drill_version_id, 1, v.group_name
FROM (VALUES
    ('Jump Stop and Pivot Series', 'Footwork'), ('Pocket Pass Reads off the Drive', 'Read'),
    ('Closeout and Contain', 'Possession'), ('One-on-One Wing Containment', 'Possession'),
    ('Deny and Recover', 'Rep'), ('Box-Out and Outlet', 'Rep'), ('Rebound Gauntlet with Contact', 'Rebound'),
    ('Depth Jumps', 'Rep'), ('Fast Break 2-on-1', 'Possession'), ('Pick-and-Roll Reads', 'Read')
) AS v (drill_name, group_name)
JOIN h_data.drills d ON d.drill_name = v.drill_name AND d.deleted_at IS NULL
JOIN h_data.drill_versions dv ON dv.drill_id = d.drill_id AND dv.version_no = 1;

INSERT INTO h_data.drill_outcomes (outcome_group_id, outcome_no, outcome_label, is_success)
SELECT g.outcome_group_id, v.outcome_no, v.outcome_label, v.is_success
FROM (VALUES
    ('Jump Stop and Pivot Series', 1, 'Clean', true),        ('Jump Stop and Pivot Series', 2, 'Travel', false),
    ('Pocket Pass Reads off the Drive', 1, 'Correct', true), ('Pocket Pass Reads off the Drive', 2, 'Wrong', false),
    ('Closeout and Contain', 1, 'Stop', true),               ('Closeout and Contain', 2, 'Beaten', false),
    ('One-on-One Wing Containment', 1, 'Stop', true),        ('One-on-One Wing Containment', 2, 'Score', false),
    ('Deny and Recover', 1, 'Success', true),                ('Deny and Recover', 2, 'Fail', false),
    ('Box-Out and Outlet', 1, 'Clean', true),                ('Box-Out and Outlet', 2, 'Missed', false),
    ('Rebound Gauntlet with Contact', 1, 'Won', true),       ('Rebound Gauntlet with Contact', 2, 'Lost', false),
    ('Depth Jumps', 1, 'Quality', true),                     ('Depth Jumps', 2, 'Poor', false),
    ('Fast Break 2-on-1', 1, 'Score', true),                 ('Fast Break 2-on-1', 2, 'No score', false),
    ('Pick-and-Roll Reads', 1, 'Correct', true),             ('Pick-and-Roll Reads', 2, 'Wrong', false)
) AS v (drill_name, outcome_no, outcome_label, is_success)
JOIN h_data.drills d ON d.drill_name = v.drill_name AND d.deleted_at IS NULL
JOIN h_data.drill_versions dv ON dv.drill_id = d.drill_id AND dv.version_no = 1
JOIN h_data.drill_outcome_groups g ON g.drill_version_id = dv.drill_version_id AND g.group_no = 1;

INSERT INTO h_data.drill_secondary_types (drill_id, drill_type_id)
SELECT d.drill_id, dt.drill_type_id
FROM (VALUES
    ('Elbow Pull-Up Jumpers',            'Ball handling'),
    ('Mikan Drill',                      'Footwork and post moves'),
    ('Full-Court Layup Circuit',         'Conditioning'),
    ('Floater Series',                   'Shooting'),
    ('Contact Finishes with Pad',        'Strength and agility'),
    ('Cone Zig-Zag Dribble',             'Conditioning'),
    ('Two-Man Full-Court Passing',       'Finishing'),
    ('Two-Man Full-Court Passing',       'Conditioning'),
    ('Pocket Pass Reads off the Drive',  'Game situations'),
    ('Drop Step Power Finish',           'Finishing'),
    ('Baby Hook Series',                 'Finishing'),
    ('Defensive Slide Lane Drill',       'Conditioning'),
    ('One-on-One Wing Containment',      'Game situations'),
    ('Box-Out and Outlet',               'Passing'),
    ('Putback Finishes',                 'Finishing'),
    ('Superman Rebounding',              'Conditioning'),
    ('Rebound Gauntlet with Contact',    'Strength and agility'),
    ('Jump Rope Intervals',              'Strength and agility'),
    ('Free Throws Under Fatigue',        'Shooting'),
    ('Lane Agility Drill',               'Defense'),
    ('Fast Break 2-on-1',                'Passing'),
    ('Fast Break 2-on-1',                'Finishing'),
    ('Game-Speed Shots off Screens',     'Shooting'),
    ('Pick-and-Roll Reads',              'Passing'),
    ('Last-Shot Situations',             'Shooting')
) AS v (drill_name, type_name)
JOIN h_data.drills d ON d.drill_name = v.drill_name AND d.deleted_at IS NULL
JOIN h_data.drill_types dt ON dt.type_name = v.type_name AND dt.deleted_at IS NULL;

-- Spot number NULL = the free-throw line (FT). Spots are looked up by number and
-- attached to version 1 of each shooting drill.
INSERT INTO h_data.drill_spots (drill_version_id, spot_order, court_spot_id, is_free_throw)
SELECT dv.drill_version_id, v.spot_order, cs.court_spot_id, (v.spot_number IS NULL)
FROM (VALUES
    ('Form Shooting Close Range',       1,  1),
    ('Free Throw Routine 50',           1,  NULL::integer),
    ('Catch-and-Shoot Corner Threes',   1, 16),
    ('Catch-and-Shoot Corner Threes',   2, 24),
    ('Around the World Mid-Range',      1,  6),
    ('Around the World Mid-Range',      2,  9),
    ('Around the World Mid-Range',      3, 14),
    ('Around the World Mid-Range',      4, 12),
    ('Around the World Mid-Range',      5,  7),
    ('Elbow Pull-Up Jumpers',           1, 10),
    ('Elbow Pull-Up Jumpers',           2, 11),
    ('Five-Spot Three-Point Shooting',  1, 16),
    ('Five-Spot Three-Point Shooting',  2, 18),
    ('Five-Spot Three-Point Shooting',  3, 20),
    ('Five-Spot Three-Point Shooting',  4, 22),
    ('Five-Spot Three-Point Shooting',  5, 24),
    ('Deep Range Threes',               1, 25),
    ('Weak-Hand Layups',                1,  1),
    ('Floater Series',                  1,  4),
    ('Contact Finishes with Pad',       1,  1),
    ('Drop Step Power Finish',          1,  2),
    ('Drop Step Power Finish',          2,  3),
    ('Baby Hook Series',                1,  2),
    ('Baby Hook Series',                2,  3),
    ('Spin Move Post Counters',         1,  2),
    ('Spin Move Post Counters',         2,  3),
    ('Putback Finishes',                1,  1),
    ('Free Throws Under Fatigue',       1,  NULL),
    ('Game-Speed Shots off Screens',    1, 13),
    ('Game-Speed Shots off Screens',    2, 15),
    ('Game-Speed Shots off Screens',    3, 18),
    ('Game-Speed Shots off Screens',    4, 22),
    ('Last-Shot Situations',            1, 14),
    ('Last-Shot Situations',            2, 20)
) AS v (drill_name, spot_order, spot_number)
JOIN h_data.drills d ON d.drill_name = v.drill_name AND d.deleted_at IS NULL
JOIN h_data.drill_versions dv ON dv.drill_id = d.drill_id AND dv.version_no = 1
LEFT JOIN h_ref.court_spots cs ON cs.spot_number = v.spot_number AND cs.deleted_at IS NULL;

-- Seed self-check: a misspelt name in a VALUES list would silently drop a row
-- through the joins above, so the counts are asserted.
DO $$
BEGIN
    IF (SELECT count(*) FROM h_data.drill_types) <> 10 THEN
        RAISE EXCEPTION 'h_data seed check failed: drill types';
    END IF;
    IF (SELECT count(*) FROM h_data.drills) <> 44
       OR (SELECT count(*) FROM h_data.drills WHERE difficulty_code = 'beginner') <> 11
       OR (SELECT count(*) FROM h_data.drills WHERE difficulty_code = 'intermediate') <> 12
       OR (SELECT count(*) FROM h_data.drills WHERE difficulty_code = 'advanced') <> 11
       OR (SELECT count(*) FROM h_data.drills WHERE difficulty_code = 'elite') <> 10 THEN
        RAISE EXCEPTION 'h_data seed check failed: drills';
    END IF;
    IF (SELECT count(*) FROM h_data.drill_versions) <> 44
       OR EXISTS (SELECT 1 FROM h_data.drill_versions v WHERE v.version_no <> 1) THEN
        RAISE EXCEPTION 'h_data seed check failed: drill versions';
    END IF;
    IF (SELECT count(*) FROM h_data.drill_versions WHERE result_type_code = 'shooting') <> 17
       OR (SELECT count(*) FROM h_data.drill_versions WHERE result_type_code = 'outcome_buttons') <> 10
       OR (SELECT count(*) FROM h_data.drill_versions WHERE result_type_code = 'fixed_task_time') <> 8
       OR (SELECT count(*) FROM h_data.drill_versions WHERE result_type_code = 'fixed_time_reps') <> 7
       OR (SELECT count(*) FROM h_data.drill_versions WHERE result_type_code = 'failure_streak') <> 2 THEN
        RAISE EXCEPTION 'h_data seed check failed: drill result types';
    END IF;
    IF (SELECT count(*) FROM h_data.drill_versions WHERE fixed_seconds IS NOT NULL) <> 7 THEN
        RAISE EXCEPTION 'h_data seed check failed: fixed seconds';
    END IF;
    IF (SELECT count(*) FROM h_data.drill_secondary_types) <> 25 THEN
        RAISE EXCEPTION 'h_data seed check failed: secondary types';
    END IF;
    IF (SELECT count(*) FROM h_data.drill_spots) <> 34 THEN
        RAISE EXCEPTION 'h_data seed check failed: drill spots';
    END IF;
    IF EXISTS (SELECT 1 FROM h_data.drill_versions v
               WHERE v.result_type_code = 'shooting'
                 AND NOT EXISTS (SELECT 1 FROM h_data.drill_spots s WHERE s.drill_version_id = v.drill_version_id)) THEN
        RAISE EXCEPTION 'h_data seed check failed: a shooting drill version has no spots';
    END IF;
    IF (SELECT count(*) FROM h_data.drill_outcome_groups) <> 10 THEN
        RAISE EXCEPTION 'h_data seed check failed: outcome groups';
    END IF;
    IF (SELECT count(*) FROM h_data.drill_outcomes) <> 20
       OR (SELECT count(*) FROM h_data.drill_outcomes WHERE is_success) <> 10 THEN
        RAISE EXCEPTION 'h_data seed check failed: outcomes';
    END IF;
END
$$;


-- =============================================================================
-- Grants (COD-1-06, plan section 6.13.3; player_drill_model plan 6.3.7)
-- =============================================================================

GRANT USAGE ON SCHEMA h_data TO hoopster_app, hoopster_agent_ro;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA h_data TO hoopster_app;
-- The views are read-only for the app (ALL TABLES includes views).
REVOKE INSERT, UPDATE, DELETE ON h_data.v_recently_deleted, h_data.v_current_drills,
    h_data.v_current_drill_sets, h_data.v_current_practice_templates FROM hoopster_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA h_data TO hoopster_app;

GRANT SELECT ON ALL TABLES IN SCHEMA h_data TO hoopster_agent_ro;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA h_data TO hoopster_agent_ro;

REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA h_data FROM PUBLIC;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA h_data TO hoopster_app;
GRANT EXECUTE ON FUNCTION h_data.fn_season_for_date(bigint, date) TO hoopster_agent_ro;
GRANT EXECUTE ON FUNCTION h_data.fn_split_target(integer, integer) TO hoopster_agent_ro;
GRANT EXECUTE ON FUNCTION h_data.fn_drill_spot_targets(bigint) TO hoopster_agent_ro;
GRANT EXECUTE ON FUNCTION h_data.fn_practice_usage(text, bigint) TO hoopster_agent_ro;
GRANT EXECUTE ON FUNCTION h_data.fn_practice_player_record_count(bigint, bigint) TO hoopster_agent_ro;

ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_data
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO hoopster_app;
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_data
    GRANT SELECT ON TABLES TO hoopster_agent_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_data
    GRANT USAGE, SELECT ON SEQUENCES TO hoopster_app;
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_data
    GRANT SELECT ON SEQUENCES TO hoopster_agent_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_data
    GRANT EXECUTE ON FUNCTIONS TO hoopster_app;

COMMIT;

SELECT 'END OF SCRIPT';
