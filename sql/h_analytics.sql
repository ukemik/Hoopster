-- =============================================================================
-- sql/h_analytics.sql  --  schema h_analytics
--
-- Purpose : Views and functions behind charts and dashboards: shot facts, box
--           scores, shooting totals and trends, heat maps, averages, career highs,
--           personal bests, streaks, stat values and goal progress (COD-1-06).
--           No tables.
-- !!! WARNING: running this file DELETES every object and ALL DATA in schema h_analytics !!!
-- It starts with DROP SCHEMA h_analytics CASCADE, which also removes objects of other schemas
-- that depend on it (nothing). Run the five schema files together, in order: h_system,
-- h_ref, h_data, h_analytics, h_staging (COD-21-06). While real accounts exist
-- it stops unless the session sets hoopster.allow_wipe = 'yes' (PGOPTIONS=-c hoopster.allow_wipe=yes).
-- Never run it on prod without a fresh backup.
-- Requires: sql/_database.sql, sql/_roles.sql, sql/h_system.sql, sql/h_ref.sql,
--           sql/h_data.sql.
-- Run order (COD-21-05): _database, _roles, h_system, h_ref, h_data,
--           h_analytics (this file), h_staging.
-- Run as  : psql -v ON_ERROR_STOP=1 -d hoopster_db -f sql/h_analytics.sql
-- Re-runnable: drops and rebuilds this schema in one transaction; run the five
-- schema files together, in order (§ run-order table); see COD-21-06.
--
-- Every view excludes deleted rows and rows whose parent game or practice is
-- deleted. Percentages are unrounded, on a 0 to 100 scale; the app rounds them.
-- A practice has several players: every practice fact is filtered by the entry's
-- own player_id.
-- Practice facts belong to a participant row (practice_player_id); only live
-- participants are read (Amendment A3).
-- No row is shown whose direct or indirect parent is deleted, including the
-- player's profile (Amendment A4). The cascades already hide such rows; the views
-- also test a live player profile, a live practice drill (drill views) and a live
-- linked drill attempt (fact views) as a backstop (COD-22-03).
-- Every function here only reads, so both the app and the read-only role may
-- execute them. Order in this file: formula helpers, views, then functions (SQL
-- functions are validated at creation, so what they use must already exist).
--
-- Standard filter set F (REQ-036), always the trailing parameters in this order:
--   p_player_id bigint, p_season_id bigint DEFAULT NULL, p_date_from date DEFAULT NULL,
--   p_date_to date DEFAULT NULL, p_context text DEFAULT NULL,
--   p_game_category_code text DEFAULT NULL, p_game_format_code text DEFAULT NULL,
--   p_zone_code text DEFAULT NULL, p_shot_type_code text DEFAULT NULL
-- p_player_id is h_data.players.player_id, never the acting user id (COD-8-01).
-- Run them in this order. Each later file depends on objects created by the earlier ones.
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

DROP SCHEMA IF EXISTS h_analytics CASCADE;

CREATE SCHEMA h_analytics;
COMMENT ON SCHEMA h_analytics IS
    'Read-only views and functions for dashboards: box scores, shooting, heat maps, bests, streaks, goal progress.';


-- =============================================================================
-- Formula helpers (REQ-024). SQL, IMMUTABLE. NULL instead of division by zero.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- h_analytics.fn_pct
-- Description : Make percentage, 100 x makes / attempts.
-- Parameters  : p_makes, p_attempts - counts.
-- Return Values: numeric 0..100, unrounded; NULL when attempts is 0 or NULL.
-- Notes       : Sums of makes over sums of attempts (REQ-017) are passed in.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_analytics.fn_pct(p_makes numeric, p_attempts numeric)
RETURNS numeric
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT 100.0 * p_makes / NULLIF(p_attempts, 0)
$$;
COMMENT ON FUNCTION h_analytics.fn_pct(numeric, numeric) IS
    '100 x makes / attempts, NULL when attempts = 0 (unrounded).';

-- -----------------------------------------------------------------------------
-- h_analytics.fn_efg_pct
-- Description : Effective field goal %, 100 x (FGM + 0.5 x 3PM) / FGA.
-- Parameters  : p_fgm, p_three_pm, p_fga - field-goal counts.
-- Return Values: numeric, unrounded; NULL when FGA is 0.
-- Notes       : none.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_analytics.fn_efg_pct(p_fgm numeric, p_three_pm numeric, p_fga numeric)
RETURNS numeric
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT 100.0 * (p_fgm + 0.5 * p_three_pm) / NULLIF(p_fga, 0)
$$;
COMMENT ON FUNCTION h_analytics.fn_efg_pct(numeric, numeric, numeric) IS
    'eFG% = 100 x (FGM + 0.5 x 3PM) / FGA, NULL when FGA = 0.';

-- -----------------------------------------------------------------------------
-- h_analytics.fn_ts_pct
-- Description : True shooting %, 100 x PTS / (2 x (FGA + 0.44 x FTA)).
-- Parameters  : p_points, p_fga, p_fta.
-- Return Values: numeric, unrounded; NULL when the denominator is 0.
-- Notes       : none.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_analytics.fn_ts_pct(p_points numeric, p_fga numeric, p_fta numeric)
RETURNS numeric
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT 100.0 * p_points / NULLIF(2 * (p_fga + 0.44 * p_fta), 0)
$$;
COMMENT ON FUNCTION h_analytics.fn_ts_pct(numeric, numeric, numeric) IS
    'TS% = 100 x PTS / (2 x (FGA + 0.44 x FTA)), NULL when the denominator is 0.';

-- -----------------------------------------------------------------------------
-- h_analytics.fn_ratio
-- Description : a / b.
-- Parameters  : p_a, p_b.
-- Return Values: numeric; NULL when b is 0 (shown as a dash, e.g. AST/TO).
-- Notes       : none.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_analytics.fn_ratio(p_a numeric, p_b numeric)
RETURNS numeric
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT p_a / NULLIF(p_b, 0)
$$;
COMMENT ON FUNCTION h_analytics.fn_ratio(numeric, numeric) IS 'a / b, NULL when b = 0 (AST/TO).';


-- =============================================================================
-- Views
-- =============================================================================

-- h_analytics.v_shot_facts (REQ-074, REQ-075, REQ-079): one row per live field-goal
-- entry with a live parent. player_id is the entry's own shooter; the season is the
-- game's, or the entry's participant row in the practice (joined by
-- practice_player_id). A practice entry counts only while its participant row is
-- live (Amendment A3); game entries are unaffected. Backstops (Amendment A4): the
-- shooter's profile must be live, and an entry linked to a drill attempt counts
-- only while that attempt is live.
CREATE VIEW h_analytics.v_shot_facts AS
SELECT se.shot_entry_id,
       se.context,
       se.game_id,
       se.practice_session_id,
       se.practice_player_id,
       se.player_id,
       COALESCE(g.game_date, ps.session_date) AS event_date,
       COALESCE(g.season_id, pp.season_id) AS season_id,
       g.game_category_code,
       g.game_format_code,
       ps.practice_type_code,
       se.entry_mode,
       se.court_spot_id,
       cs.spot_number,
       cs.zone_code,
       CASE WHEN cs.x_ft < 0 THEN 'left' WHEN cs.x_ft > 0 THEN 'right' END AS court_side,
       COALESCE(se.manual_point_value, z.point_value)::integer AS point_value,
       (COALESCE(se.manual_point_value, z.point_value) = 3) AS is_three,
       se.makes::integer AS makes,
       se.attempts::integer AS attempts,
       se.makes::integer * COALESCE(se.manual_point_value, z.point_value) AS points,
       se.shot_type_code,
       se.hand,
       se.creation,
       se.defense,
       se.drill_attempt_id,
       se.seq_in_context,
       se.shot_at
FROM h_data.shot_entries se
JOIN h_data.players pl ON pl.player_id = se.player_id
LEFT JOIN h_data.games g ON g.game_id = se.game_id
LEFT JOIN h_data.practice_sessions ps ON ps.practice_session_id = se.practice_session_id
LEFT JOIN h_data.practice_players pp ON pp.practice_player_id = se.practice_player_id
LEFT JOIN h_ref.court_spots cs ON cs.court_spot_id = se.court_spot_id
LEFT JOIN h_ref.zones z ON z.zone_code = cs.zone_code
WHERE se.deleted_at IS NULL
  AND pl.deleted_at IS NULL
  AND COALESCE(g.deleted_at, ps.deleted_at) IS NULL
  AND (se.practice_session_id IS NULL
       OR (pp.practice_player_id IS NOT NULL AND pp.deleted_at IS NULL))
  AND (se.drill_attempt_id IS NULL
       OR EXISTS (SELECT 1
                  FROM h_data.drill_attempts da
                  WHERE da.drill_attempt_id = se.drill_attempt_id
                    AND da.deleted_at IS NULL));
COMMENT ON VIEW h_analytics.v_shot_facts IS
    'One row per live shot entry with a live parent: event date, season, category, zone, point value (manual entries carry their own), makes, attempts, points, details. player_id is the shooter; season is the game''s or the participant''s; court_side from the spot (REQ-079). Practice entries carry practice_player_id and appear only while that participant row is live (Amendment A3). Backstops (Amendment A4, COD-22-03): the shooter''s profile must be live, and an entry linked to a drill attempt appears only while that attempt is live.';

-- h_analytics.v_free_throw_facts (REQ-074, REQ-075): one row per live free-throw
-- entry with a live parent; player_id, season, the live-participant rule and the
-- Amendment A4 backstops (live profile, live linked attempt) as in v_shot_facts.
CREATE VIEW h_analytics.v_free_throw_facts AS
SELECT fe.free_throw_entry_id,
       fe.context,
       fe.game_id,
       fe.practice_session_id,
       fe.practice_player_id,
       fe.player_id,
       COALESCE(g.game_date, ps.session_date) AS event_date,
       COALESCE(g.season_id, pp.season_id) AS season_id,
       g.game_category_code,
       g.game_format_code,
       ps.practice_type_code,
       fe.entry_mode,
       fe.makes::integer AS makes,
       fe.attempts::integer AS attempts,
       fe.drill_attempt_id,
       fe.seq_in_context,
       fe.shot_at
FROM h_data.free_throw_entries fe
JOIN h_data.players pl ON pl.player_id = fe.player_id
LEFT JOIN h_data.games g ON g.game_id = fe.game_id
LEFT JOIN h_data.practice_sessions ps ON ps.practice_session_id = fe.practice_session_id
LEFT JOIN h_data.practice_players pp ON pp.practice_player_id = fe.practice_player_id
WHERE fe.deleted_at IS NULL
  AND pl.deleted_at IS NULL
  AND COALESCE(g.deleted_at, ps.deleted_at) IS NULL
  AND (fe.practice_session_id IS NULL
       OR (pp.practice_player_id IS NOT NULL AND pp.deleted_at IS NULL))
  AND (fe.drill_attempt_id IS NULL
       OR EXISTS (SELECT 1
                  FROM h_data.drill_attempts da
                  WHERE da.drill_attempt_id = fe.drill_attempt_id
                    AND da.deleted_at IS NULL));
COMMENT ON VIEW h_analytics.v_free_throw_facts IS
    'One row per live free-throw entry with a live parent. player_id is the shooter; season is the game''s or the participant''s. Practice entries carry practice_player_id and appear only while that participant row is live (Amendment A3). Backstops (Amendment A4, COD-22-03): the shooter''s profile must be live, and an entry linked to a drill attempt appears only while that attempt is live.';

-- h_analytics.v_game_box_scores (REQ-019, REQ-020, REQ-024, REQ-061, REQ-065)
-- Backstop (Amendment A4): the game's player profile must be live.
CREATE VIEW h_analytics.v_game_box_scores AS
SELECT g.*,
       s.fgm,
       s.fga,
       s.three_pm,
       s.three_pa,
       f.ftm,
       f.fta,
       (s.fg_points + f.ftm) AS points,
       (g.off_rebounds::integer + g.def_rebounds) AS total_rebounds,
       h_analytics.fn_pct(s.fgm, s.fga) AS fg_pct,
       h_analytics.fn_pct(s.three_pm, s.three_pa) AS three_pct,
       h_analytics.fn_pct(f.ftm, f.fta) AS ft_pct,
       h_analytics.fn_efg_pct(s.fgm, s.three_pm, s.fga) AS efg_pct,
       h_analytics.fn_ts_pct(s.fg_points + f.ftm, s.fga, f.fta) AS ts_pct,
       h_analytics.fn_ratio(g.assists, g.turnovers) AS ast_to
FROM h_data.games g
CROSS JOIN LATERAL (
    SELECT COALESCE(sum(sf.makes), 0)::bigint AS fgm,
           COALESCE(sum(sf.attempts), 0)::bigint AS fga,
           COALESCE(sum(sf.makes) FILTER (WHERE sf.is_three), 0)::bigint AS three_pm,
           COALESCE(sum(sf.attempts) FILTER (WHERE sf.is_three), 0)::bigint AS three_pa,
           COALESCE(sum(sf.points), 0)::bigint AS fg_points
    FROM h_analytics.v_shot_facts sf
    WHERE sf.game_id = g.game_id
) s
CROSS JOIN LATERAL (
    SELECT COALESCE(sum(ff.makes), 0)::bigint AS ftm,
           COALESCE(sum(ff.attempts), 0)::bigint AS fta
    FROM h_analytics.v_free_throw_facts ff
    WHERE ff.game_id = g.game_id
) f
WHERE g.deleted_at IS NULL
  AND EXISTS (SELECT 1
              FROM h_data.players pl
              WHERE pl.player_id = g.player_id
                AND pl.deleted_at IS NULL);
COMMENT ON VIEW h_analytics.v_game_box_scores IS
    'One row per live game of a live player profile (backstop, Amendment A4): every game column plus derived shooting (FGM/FGA, 3PM/3PA, FTM/FTA, points), total rebounds and FG%, 3P%, FT%, eFG%, TS%, AST/TO.';

-- h_analytics.v_practice_shooting (REQ-074): one row per live participant row of a
-- live practice, with the shooting recorded on that participant row (summed from the
-- fact views by practice_player_id, Amendment A3). Backstop (Amendment A4): the
-- participant's player profile must be live.
CREATE VIEW h_analytics.v_practice_shooting AS
SELECT ps.practice_session_id,
       pp.practice_player_id,
       pp.player_id,
       ps.session_date,
       pp.season_id,
       ps.practice_type_code,
       ps.practice_template_version_id,
       ps.duration_minutes,
       ps.location,
       ps.entry_status,
       s.fgm,
       s.fga,
       s.three_pm,
       s.three_pa,
       f.ftm,
       f.fta,
       (s.fg_points + f.ftm) AS points,
       h_analytics.fn_pct(s.fgm, s.fga) AS fg_pct,
       h_analytics.fn_pct(s.three_pm, s.three_pa) AS three_pct,
       h_analytics.fn_pct(f.ftm, f.fta) AS ft_pct,
       h_analytics.fn_efg_pct(s.fgm, s.three_pm, s.fga) AS efg_pct,
       h_analytics.fn_ts_pct(s.fg_points + f.ftm, s.fga, f.fta) AS ts_pct
FROM h_data.practice_sessions ps
JOIN h_data.practice_players pp ON pp.practice_session_id = ps.practice_session_id
JOIN h_data.players pl ON pl.player_id = pp.player_id
CROSS JOIN LATERAL (
    SELECT COALESCE(sum(sf.makes), 0)::bigint AS fgm,
           COALESCE(sum(sf.attempts), 0)::bigint AS fga,
           COALESCE(sum(sf.makes) FILTER (WHERE sf.is_three), 0)::bigint AS three_pm,
           COALESCE(sum(sf.attempts) FILTER (WHERE sf.is_three), 0)::bigint AS three_pa,
           COALESCE(sum(sf.points), 0)::bigint AS fg_points
    FROM h_analytics.v_shot_facts sf
    WHERE sf.practice_player_id = pp.practice_player_id
) s
CROSS JOIN LATERAL (
    SELECT COALESCE(sum(ff.makes), 0)::bigint AS ftm,
           COALESCE(sum(ff.attempts), 0)::bigint AS fta
    FROM h_analytics.v_free_throw_facts ff
    WHERE ff.practice_player_id = pp.practice_player_id
) f
WHERE ps.deleted_at IS NULL
  AND pp.deleted_at IS NULL
  AND pl.deleted_at IS NULL;
COMMENT ON VIEW h_analytics.v_practice_shooting IS
    'One row per live participant row of a live practice (REQ-074): practice columns plus the shooting recorded on that participant row (by practice_player_id; Amendment A3). Backstop (Amendment A4, COD-22-03): the player profile must be live.';

-- h_analytics.v_drill_attempts (REQ-031, REQ-076, REQ-078, REQ-079): one row per live
-- attempt of a live result in a live practice, whose participant row (joined by
-- practice_player_id) is live (Amendment A3). rate_per_minute only for fixed time
-- for reps; makes, attempts and make_pct only for shooting drills, derived from the
-- attempt's linked live shot and free-throw entries (never stored). Backstops
-- (Amendment A4): the result's practice drill and the player profile must be live.
CREATE VIEW h_analytics.v_drill_attempts AS
SELECT a.drill_attempt_id,
       a.drill_result_id,
       a.practice_session_id,
       a.practice_player_id,
       ps.session_date,
       a.player_id,
       pp.season_id,
       r.practice_drill_id,
       dv.drill_id,
       r.drill_version_id,
       dv.version_no,
       d.drill_name,
       dv.result_type_code,
       a.attempt_no,
       a.side,
       a.value_1,
       a.value_2,
       CASE WHEN dv.result_type_code = 'fixed_time_reps'
            THEN 60.0 * a.value_1 / NULLIF(dv.fixed_seconds, 0) END AS rate_per_minute,
       CASE WHEN dv.result_type_code = 'shooting' THEN e.makes END AS makes,
       CASE WHEN dv.result_type_code = 'shooting' THEN e.attempts END AS attempts,
       CASE WHEN dv.result_type_code = 'shooting'
            THEN h_analytics.fn_pct(e.makes, e.attempts) END AS make_pct,
       a.attempted_at
FROM h_data.drill_attempts a
JOIN h_data.drill_results r ON r.drill_result_id = a.drill_result_id
JOIN h_data.practice_drills pd ON pd.practice_drill_id = r.practice_drill_id
JOIN h_data.practice_sessions ps ON ps.practice_session_id = a.practice_session_id
JOIN h_data.practice_players pp ON pp.practice_player_id = a.practice_player_id
JOIN h_data.players pl ON pl.player_id = a.player_id
JOIN h_data.drill_versions dv ON dv.drill_version_id = r.drill_version_id
JOIN h_data.drills d ON d.drill_id = dv.drill_id
CROSS JOIN LATERAL (
    SELECT (COALESCE((SELECT sum(se.makes) FROM h_data.shot_entries se
                      WHERE se.drill_attempt_id = a.drill_attempt_id AND se.deleted_at IS NULL), 0)
          + COALESCE((SELECT sum(fe.makes) FROM h_data.free_throw_entries fe
                      WHERE fe.drill_attempt_id = a.drill_attempt_id AND fe.deleted_at IS NULL), 0))::bigint AS makes,
           (COALESCE((SELECT sum(se.attempts) FROM h_data.shot_entries se
                      WHERE se.drill_attempt_id = a.drill_attempt_id AND se.deleted_at IS NULL), 0)
          + COALESCE((SELECT sum(fe.attempts) FROM h_data.free_throw_entries fe
                      WHERE fe.drill_attempt_id = a.drill_attempt_id AND fe.deleted_at IS NULL), 0))::bigint AS attempts
) e
WHERE a.deleted_at IS NULL
  AND r.deleted_at IS NULL
  AND pd.deleted_at IS NULL
  AND ps.deleted_at IS NULL
  AND pp.deleted_at IS NULL
  AND pl.deleted_at IS NULL;
COMMENT ON VIEW h_analytics.v_drill_attempts IS
    'One row per live attempt of a live participant row (REQ-078, Amendment A3): values, derived rate, and for shooting makes/attempts from linked live entries (REQ-031). Backstops (Amendment A4, COD-22-03): the result''s practice drill and the player profile must be live.';

-- h_analytics.v_drill_attempt_outcomes (REQ-077): one row per live outcome tap of a
-- live attempt, with its group and button (and whether that button is a success).
-- The attempt's participant row (joined by practice_player_id) must be live
-- (Amendment A3). Backstops (Amendment A4): the result's practice drill and the
-- player profile must be live.
CREATE VIEW h_analytics.v_drill_attempt_outcomes AS
SELECT o.drill_attempt_outcome_id,
       o.drill_attempt_id,
       a.drill_result_id,
       a.practice_session_id,
       a.practice_player_id,
       ps.session_date,
       a.player_id,
       r.drill_version_id,
       dv.drill_id,
       g.group_no,
       g.group_name,
       oc.outcome_no,
       oc.outcome_label,
       oc.is_success
FROM h_data.drill_attempt_outcomes o
JOIN h_data.drill_attempts a ON a.drill_attempt_id = o.drill_attempt_id
JOIN h_data.drill_results r ON r.drill_result_id = a.drill_result_id
JOIN h_data.practice_drills pd ON pd.practice_drill_id = r.practice_drill_id
JOIN h_data.practice_sessions ps ON ps.practice_session_id = a.practice_session_id
JOIN h_data.practice_players pp ON pp.practice_player_id = a.practice_player_id
JOIN h_data.players pl ON pl.player_id = a.player_id
JOIN h_data.drill_versions dv ON dv.drill_version_id = r.drill_version_id
JOIN h_data.drill_outcome_groups g ON g.outcome_group_id = o.outcome_group_id
JOIN h_data.drill_outcomes oc ON oc.drill_outcome_id = o.drill_outcome_id
WHERE o.deleted_at IS NULL
  AND a.deleted_at IS NULL
  AND r.deleted_at IS NULL
  AND pd.deleted_at IS NULL
  AND ps.deleted_at IS NULL
  AND pp.deleted_at IS NULL
  AND pl.deleted_at IS NULL;
COMMENT ON VIEW h_analytics.v_drill_attempt_outcomes IS
    'One row per live outcome tap of a live participant row (REQ-077, Amendment A3). Backstops (Amendment A4, COD-22-03): the result''s practice drill and the player profile must be live.';

-- h_analytics.v_drill_results (REQ-031, REQ-075, REQ-077, REQ-078): one row per live
-- result of one player for one practice drill, aggregated over its live attempts.
-- The result's participant row (joined by practice_player_id) must be live
-- (Amendment A3). Backstops (Amendment A4): the result's practice drill and the
-- player profile must be live.
-- best_value_n follows the result type's direction, or the drill version's for a
-- physical measurement; clean_pct_n = success taps / taps of outcome group n.
CREATE VIEW h_analytics.v_drill_results AS
SELECT r.drill_result_id,
       r.practice_session_id,
       r.practice_player_id,
       ps.session_date,
       r.player_id,
       pp.season_id,
       r.practice_drill_id,
       dv.drill_id,
       r.drill_version_id,
       dv.version_no,
       d.drill_name,
       dv.result_type_code,
       ag.attempt_count,
       ag.total_makes,
       ag.total_attempts,
       h_analytics.fn_pct(ag.total_makes, ag.total_attempts) AS total_make_pct,
       ag.best_value_1,
       ag.avg_value_1,
       ag.total_value_1,
       ag.best_value_2,
       ag.avg_value_2,
       ag.total_value_2,
       oc.reps_1,
       oc.clean_pct_1,
       oc.reps_2,
       oc.clean_pct_2,
       oc.reps_3,
       oc.clean_pct_3,
       r.notes
FROM h_data.drill_results r
JOIN h_data.practice_drills pd ON pd.practice_drill_id = r.practice_drill_id
JOIN h_data.practice_sessions ps ON ps.practice_session_id = r.practice_session_id
JOIN h_data.practice_players pp ON pp.practice_player_id = r.practice_player_id
JOIN h_data.players pl ON pl.player_id = r.player_id
JOIN h_data.drill_versions dv ON dv.drill_version_id = r.drill_version_id
JOIN h_ref.result_types rt ON rt.result_type_code = dv.result_type_code
JOIN h_data.drills d ON d.drill_id = dv.drill_id
CROSS JOIN LATERAL (
    SELECT count(*)::integer AS attempt_count,
           CASE WHEN dv.result_type_code = 'shooting' THEN COALESCE(sum(x.makes), 0)::bigint END AS total_makes,
           CASE WHEN dv.result_type_code = 'shooting' THEN COALESCE(sum(x.attempts), 0)::bigint END AS total_attempts,
           CASE WHEN COALESCE(rt.higher_is_better, dv.value_1_higher_is_better)
                THEN max(x.value_1) ELSE min(x.value_1) END AS best_value_1,
           avg(x.value_1) AS avg_value_1,
           sum(x.value_1) AS total_value_1,
           CASE WHEN dv.value_2_higher_is_better
                THEN max(x.value_2) ELSE min(x.value_2) END AS best_value_2,
           avg(x.value_2) AS avg_value_2,
           sum(x.value_2) AS total_value_2
    FROM h_analytics.v_drill_attempts x
    WHERE x.drill_result_id = r.drill_result_id
) ag
CROSS JOIN LATERAL (
    SELECT count(*) FILTER (WHERE o.group_no = 1)::integer AS reps_1,
           h_analytics.fn_pct(count(*) FILTER (WHERE o.group_no = 1 AND o.is_success),
                              count(*) FILTER (WHERE o.group_no = 1)) AS clean_pct_1,
           count(*) FILTER (WHERE o.group_no = 2)::integer AS reps_2,
           h_analytics.fn_pct(count(*) FILTER (WHERE o.group_no = 2 AND o.is_success),
                              count(*) FILTER (WHERE o.group_no = 2)) AS clean_pct_2,
           count(*) FILTER (WHERE o.group_no = 3)::integer AS reps_3,
           h_analytics.fn_pct(count(*) FILTER (WHERE o.group_no = 3 AND o.is_success),
                              count(*) FILTER (WHERE o.group_no = 3)) AS clean_pct_3
    FROM h_analytics.v_drill_attempt_outcomes o
    WHERE o.drill_result_id = r.drill_result_id
) oc
WHERE r.deleted_at IS NULL
  AND pd.deleted_at IS NULL
  AND ps.deleted_at IS NULL
  AND pp.deleted_at IS NULL
  AND pl.deleted_at IS NULL;
COMMENT ON VIEW h_analytics.v_drill_results IS
    'One row per live result of a live participant row (REQ-078, Amendment A3): attempt count; shooting totals; best (type or drill direction), average and total of each value; reps and clean-rep % per outcome group. Backstops (Amendment A4, COD-22-03): the practice drill and the player profile must be live.';

-- h_analytics.v_recent_activity (REQ-041, REQ-075): a practice appears once per live
-- participant, so each player's list holds only their own practices. Backstop
-- (Amendment A4): every arm requires the row's player profile to be live.
CREATE VIEW h_analytics.v_recent_activity AS
SELECT 'game'::text AS entity,
       g.game_id AS entity_id,
       g.player_id,
       g.game_date AS event_date,
       'Game' || COALESCE(' vs ' || g.opponent_name, '')
       || CASE WHEN g.result IS NOT NULL
               THEN ', ' || upper(left(g.result, 1)) || ' ' || g.team_score || '-' || g.opponent_score
               ELSE '' END AS label,
       g.created_at,
       g.updated_at,
       g.updated_by
FROM h_data.games g
JOIN h_data.players pl ON pl.player_id = g.player_id
WHERE g.deleted_at IS NULL
  AND pl.deleted_at IS NULL
UNION ALL
SELECT 'practice_session', ps.practice_session_id, pp.player_id, ps.session_date,
       'Practice (' || pt.label || ')',
       ps.created_at, ps.updated_at, ps.updated_by
FROM h_data.practice_players pp
JOIN h_data.players pl ON pl.player_id = pp.player_id
JOIN h_data.practice_sessions ps ON ps.practice_session_id = pp.practice_session_id
JOIN h_ref.practice_types pt ON pt.practice_type_code = ps.practice_type_code
WHERE pp.deleted_at IS NULL
  AND pl.deleted_at IS NULL
  AND ps.deleted_at IS NULL
UNION ALL
SELECT 'physical_measurement', pm.physical_measurement_id, pm.player_id, pm.measured_on,
       pt.test_name || ': ' || pm.measured_value || ' ' || pt.unit,
       pm.created_at, pm.updated_at, pm.updated_by
FROM h_data.physical_measurements pm
JOIN h_data.players pl ON pl.player_id = pm.player_id
JOIN h_ref.physical_tests pt ON pt.physical_test_id = pm.physical_test_id
WHERE pm.deleted_at IS NULL
  AND pl.deleted_at IS NULL;
COMMENT ON VIEW h_analytics.v_recent_activity IS
    'REQ-041 recent activity: live games, practices (one row per live participant row) and physical measurements, each only while the player profile is live (backstop, Amendment A4). The app takes the top N by updated_at.';


-- =============================================================================
-- Filtered facts (filter set F, REQ-036)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- h_analytics.fn_filtered_shots (REQ-036)
-- Description : Live shot facts of a player after filter set F. Season uses the
--               parent's season; dates are inclusive on event_date; context NULL
--               means both; category/format keep game rows only; zone/shot type
--               keep field-goal rows with that value (a zone excludes manual rows).
-- Parameters  : filter set F (see file header).
-- Return Values: SETOF h_analytics.v_shot_facts.
-- Notes       : none.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_analytics.fn_filtered_shots(
    p_player_id          bigint,
    p_season_id          bigint DEFAULT NULL,
    p_date_from          date   DEFAULT NULL,
    p_date_to            date   DEFAULT NULL,
    p_context            text   DEFAULT NULL,
    p_game_category_code text   DEFAULT NULL,
    p_game_format_code   text   DEFAULT NULL,
    p_zone_code          text   DEFAULT NULL,
    p_shot_type_code     text   DEFAULT NULL)
RETURNS SETOF h_analytics.v_shot_facts
LANGUAGE sql
STABLE
AS $$
    SELECT sf.*
    FROM h_analytics.v_shot_facts sf
    WHERE sf.player_id = p_player_id
      AND (p_season_id IS NULL OR sf.season_id = p_season_id)
      AND (p_date_from IS NULL OR sf.event_date >= p_date_from)
      AND (p_date_to IS NULL OR sf.event_date <= p_date_to)
      AND (p_context IS NULL OR sf.context = p_context)
      AND (p_game_category_code IS NULL OR sf.game_category_code = p_game_category_code)
      AND (p_game_format_code IS NULL OR sf.game_format_code = p_game_format_code)
      AND (p_zone_code IS NULL OR sf.zone_code = p_zone_code)
      AND (p_shot_type_code IS NULL OR sf.shot_type_code = p_shot_type_code)
$$;
COMMENT ON FUNCTION h_analytics.fn_filtered_shots(bigint, bigint, date, date, text, text, text, text, text) IS
    'REQ-036: shot facts after the standard filter set F.';

-- -----------------------------------------------------------------------------
-- h_analytics.fn_filtered_free_throws (REQ-036)
-- Description : Live free-throw facts of a player after filter set F. Free throws
--               have no zone or shot type, so those two filters are ignored.
-- Parameters  : filter set F (see file header).
-- Return Values: SETOF h_analytics.v_free_throw_facts.
-- Notes       : p_zone_code and p_shot_type_code are accepted for a uniform call
--               shape and deliberately unused.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_analytics.fn_filtered_free_throws(
    p_player_id          bigint,
    p_season_id          bigint DEFAULT NULL,
    p_date_from          date   DEFAULT NULL,
    p_date_to            date   DEFAULT NULL,
    p_context            text   DEFAULT NULL,
    p_game_category_code text   DEFAULT NULL,
    p_game_format_code   text   DEFAULT NULL,
    p_zone_code          text   DEFAULT NULL,
    p_shot_type_code     text   DEFAULT NULL)
RETURNS SETOF h_analytics.v_free_throw_facts
LANGUAGE sql
STABLE
AS $$
    SELECT ff.*
    FROM h_analytics.v_free_throw_facts ff
    WHERE ff.player_id = p_player_id
      AND (p_season_id IS NULL OR ff.season_id = p_season_id)
      AND (p_date_from IS NULL OR ff.event_date >= p_date_from)
      AND (p_date_to IS NULL OR ff.event_date <= p_date_to)
      AND (p_context IS NULL OR ff.context = p_context)
      AND (p_game_category_code IS NULL OR ff.game_category_code = p_game_category_code)
      AND (p_game_format_code IS NULL OR ff.game_format_code = p_game_format_code)
$$;
COMMENT ON FUNCTION h_analytics.fn_filtered_free_throws(bigint, bigint, date, date, text, text, text, text, text) IS
    'REQ-036: free-throw facts after filter set F (zone and shot-type filters ignored).';


-- =============================================================================
-- Shooting totals and trends
-- =============================================================================

-- -----------------------------------------------------------------------------
-- h_analytics.fn_shooting_totals (REQ-017, REQ-024, REQ-036)
-- Description : Shooting totals and percentages after filter set F, as sums of
--               makes over sums of attempts.
-- Parameters  : filter set F.
-- Return Values: one row: fgm, fga, fg_pct, three_pm, three_pa, three_pct, ftm,
--               fta, ft_pct, efg_pct, ts_pct, points.
-- Notes       : Free throws ignore zone/shot-type filters, so points = filtered
--               field-goal points + all free throws in the other filters.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_analytics.fn_shooting_totals(
    p_player_id          bigint,
    p_season_id          bigint DEFAULT NULL,
    p_date_from          date   DEFAULT NULL,
    p_date_to            date   DEFAULT NULL,
    p_context            text   DEFAULT NULL,
    p_game_category_code text   DEFAULT NULL,
    p_game_format_code   text   DEFAULT NULL,
    p_zone_code          text   DEFAULT NULL,
    p_shot_type_code     text   DEFAULT NULL)
RETURNS TABLE (fgm bigint, fga bigint, fg_pct numeric, three_pm bigint, three_pa bigint, three_pct numeric,
               ftm bigint, fta bigint, ft_pct numeric, efg_pct numeric, ts_pct numeric, points bigint)
LANGUAGE sql
STABLE
AS $$
    WITH s AS (
        SELECT COALESCE(sum(x.makes), 0)::bigint AS s_fgm,
               COALESCE(sum(x.attempts), 0)::bigint AS s_fga,
               COALESCE(sum(x.makes) FILTER (WHERE x.is_three), 0)::bigint AS s_3pm,
               COALESCE(sum(x.attempts) FILTER (WHERE x.is_three), 0)::bigint AS s_3pa,
               COALESCE(sum(x.points), 0)::bigint AS s_points
        FROM h_analytics.fn_filtered_shots(p_player_id, p_season_id, p_date_from, p_date_to, p_context,
                                           p_game_category_code, p_game_format_code, p_zone_code, p_shot_type_code) x
    ),
    f AS (
        SELECT COALESCE(sum(y.makes), 0)::bigint AS f_ftm,
               COALESCE(sum(y.attempts), 0)::bigint AS f_fta
        FROM h_analytics.fn_filtered_free_throws(p_player_id, p_season_id, p_date_from, p_date_to, p_context,
                                                 p_game_category_code, p_game_format_code, p_zone_code, p_shot_type_code) y
    )
    SELECT s.s_fgm,
           s.s_fga,
           h_analytics.fn_pct(s.s_fgm, s.s_fga),
           s.s_3pm,
           s.s_3pa,
           h_analytics.fn_pct(s.s_3pm, s.s_3pa),
           f.f_ftm,
           f.f_fta,
           h_analytics.fn_pct(f.f_ftm, f.f_fta),
           h_analytics.fn_efg_pct(s.s_fgm, s.s_3pm, s.s_fga),
           h_analytics.fn_ts_pct(s.s_points + f.f_ftm, s.s_fga, f.f_fta),
           (s.s_points + f.f_ftm)::bigint
    FROM s
    CROSS JOIN f
$$;
COMMENT ON FUNCTION h_analytics.fn_shooting_totals(bigint, bigint, date, date, text, text, text, text, text) IS
    'Shooting totals and percentages after filter set F.';

-- -----------------------------------------------------------------------------
-- h_analytics.fn_shooting_trend (REQ-037)
-- Description : FG%, 3P% and FT% per week or month, game and practice as separate
--               rows (separate chart lines), after filter set F.
-- Parameters  : p_period - 'week' (ISO weeks, starting Monday) or 'month'.
--               filter set F.
-- Return Values: one row per (period_start, context): makes, attempts and pct of
--               field goals, threes and free throws.
-- Notes       : Any other p_period returns no rows.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_analytics.fn_shooting_trend(
    p_period             text,
    p_player_id          bigint,
    p_season_id          bigint DEFAULT NULL,
    p_date_from          date   DEFAULT NULL,
    p_date_to            date   DEFAULT NULL,
    p_context            text   DEFAULT NULL,
    p_game_category_code text   DEFAULT NULL,
    p_game_format_code   text   DEFAULT NULL,
    p_zone_code          text   DEFAULT NULL,
    p_shot_type_code     text   DEFAULT NULL)
RETURNS TABLE (period_start date, context text, fgm bigint, fga bigint, fg_pct numeric,
               three_pm bigint, three_pa bigint, three_pct numeric, ftm bigint, fta bigint, ft_pct numeric)
LANGUAGE sql
STABLE
AS $$
    WITH s AS (
        SELECT date_trunc(p_period, x.event_date::timestamp)::date AS s_period,
               x.context AS s_context,
               COALESCE(sum(x.makes), 0)::bigint AS s_fgm,
               COALESCE(sum(x.attempts), 0)::bigint AS s_fga,
               COALESCE(sum(x.makes) FILTER (WHERE x.is_three), 0)::bigint AS s_3pm,
               COALESCE(sum(x.attempts) FILTER (WHERE x.is_three), 0)::bigint AS s_3pa
        FROM h_analytics.fn_filtered_shots(p_player_id, p_season_id, p_date_from, p_date_to, p_context,
                                           p_game_category_code, p_game_format_code, p_zone_code, p_shot_type_code) x
        WHERE p_period IN ('week', 'month')
        GROUP BY 1, 2
    ),
    f AS (
        SELECT date_trunc(p_period, y.event_date::timestamp)::date AS f_period,
               y.context AS f_context,
               COALESCE(sum(y.makes), 0)::bigint AS f_ftm,
               COALESCE(sum(y.attempts), 0)::bigint AS f_fta
        FROM h_analytics.fn_filtered_free_throws(p_player_id, p_season_id, p_date_from, p_date_to, p_context,
                                                 p_game_category_code, p_game_format_code, p_zone_code, p_shot_type_code) y
        WHERE p_period IN ('week', 'month')
        GROUP BY 1, 2
    )
    SELECT COALESCE(s.s_period, f.f_period),
           COALESCE(s.s_context, f.f_context),
           COALESCE(s.s_fgm, 0)::bigint,
           COALESCE(s.s_fga, 0)::bigint,
           h_analytics.fn_pct(s.s_fgm, s.s_fga),
           COALESCE(s.s_3pm, 0)::bigint,
           COALESCE(s.s_3pa, 0)::bigint,
           h_analytics.fn_pct(s.s_3pm, s.s_3pa),
           COALESCE(f.f_ftm, 0)::bigint,
           COALESCE(f.f_fta, 0)::bigint,
           h_analytics.fn_pct(f.f_ftm, f.f_fta)
    FROM s
    FULL JOIN f ON f.f_period = s.s_period AND f.f_context = s.s_context
    ORDER BY 1, 2
$$;
COMMENT ON FUNCTION h_analytics.fn_shooting_trend(text, bigint, bigint, date, date, text, text, text, text, text) IS
    'REQ-037: shooting percentages per week or month, game and practice separately.';

-- -----------------------------------------------------------------------------
-- h_analytics.fn_shot_position_trend (REQ-037, Open Question 3)
-- Description : Warm-up vs fatigue view: make % by shot position within a game or
--               practice, single field-goal shots only, in buckets (1-10, 11-20...).
--               Each shot's position is numbered among the event's live single
--               shots BEFORE filter set F is applied.
-- Parameters  : p_bucket_size - shots per bucket (the app passes 10).
--               filter set F.
-- Return Values: one row per bucket: bucket_no, bucket_label, makes, attempts,
--               make_pct, events (games/practices contributing).
-- Notes       : p_bucket_size has no default because a defaulted parameter cannot
--               precede p_player_id. Returns no rows when p_bucket_size < 1.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_analytics.fn_shot_position_trend(
    p_bucket_size        integer,
    p_player_id          bigint,
    p_season_id          bigint DEFAULT NULL,
    p_date_from          date   DEFAULT NULL,
    p_date_to            date   DEFAULT NULL,
    p_context            text   DEFAULT NULL,
    p_game_category_code text   DEFAULT NULL,
    p_game_format_code   text   DEFAULT NULL,
    p_zone_code          text   DEFAULT NULL,
    p_shot_type_code     text   DEFAULT NULL)
RETURNS TABLE (bucket_no integer, bucket_label text, makes bigint, attempts bigint, make_pct numeric, events integer)
LANGUAGE sql
STABLE
AS $$
    WITH numbered AS (
        SELECT sf.shot_entry_id AS n_id,
               row_number() OVER (PARTITION BY sf.game_id, sf.practice_session_id
                                  ORDER BY sf.seq_in_context) AS n_pos
        FROM h_analytics.v_shot_facts sf
        WHERE sf.entry_mode = 'single'
          AND sf.player_id = p_player_id
    ),
    b AS (
        SELECT ((n.n_pos - 1) / p_bucket_size + 1)::integer AS b_no,
               x.makes AS b_makes,
               x.attempts AS b_attempts,
               COALESCE('g' || x.game_id, 'p' || x.practice_session_id) AS b_event
        FROM h_analytics.fn_filtered_shots(p_player_id, p_season_id, p_date_from, p_date_to, p_context,
                                           p_game_category_code, p_game_format_code, p_zone_code, p_shot_type_code) x
        JOIN numbered n ON n.n_id = x.shot_entry_id
        WHERE x.entry_mode = 'single'
          AND p_bucket_size >= 1
    )
    SELECT b.b_no,
           (((b.b_no - 1) * p_bucket_size + 1)::text || '–' || (b.b_no * p_bucket_size)::text),
           sum(b.b_makes)::bigint,
           sum(b.b_attempts)::bigint,
           h_analytics.fn_pct(sum(b.b_makes), sum(b.b_attempts)),
           count(DISTINCT b.b_event)::integer
    FROM b
    GROUP BY b.b_no
    ORDER BY b.b_no
$$;
COMMENT ON FUNCTION h_analytics.fn_shot_position_trend(integer, bigint, bigint, date, date, text, text, text, text, text) IS
    'REQ-037: make % by shot position within an event (single field-goal shots), in buckets.';


-- =============================================================================
-- Heat maps (REQ-038)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- h_analytics.fn_heat_map_spots (REQ-038)
-- Description : Makes, attempts, make % and colour band per court spot after filter
--               set F. Spot shots only (manual entries excluded, REQ-012). Every
--               live active spot appears, plus inactive or deleted spots that have
--               data. The band is the zone's band with min_pct <= pct < max_pct
--               (open-ended when max_pct is NULL).
-- Parameters  : filter set F.
-- Return Values: one row per spot: court_spot_id, spot_number, spot_name,
--               zone_code, x_ft, y_ft, makes, attempts, make_pct, band_rank,
--               band_label (pct and band NULL with no attempts).
-- Notes       : The app maps band_rank to a colour (theme in config/).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_analytics.fn_heat_map_spots(
    p_player_id          bigint,
    p_season_id          bigint DEFAULT NULL,
    p_date_from          date   DEFAULT NULL,
    p_date_to            date   DEFAULT NULL,
    p_context            text   DEFAULT NULL,
    p_game_category_code text   DEFAULT NULL,
    p_game_format_code   text   DEFAULT NULL,
    p_zone_code          text   DEFAULT NULL,
    p_shot_type_code     text   DEFAULT NULL)
RETURNS TABLE (court_spot_id bigint, spot_number smallint, spot_name text, zone_code text,
               x_ft numeric, y_ft numeric, makes bigint, attempts bigint, make_pct numeric,
               band_rank smallint, band_label text)
LANGUAGE sql
STABLE
AS $$
    WITH agg AS (
        SELECT x.court_spot_id AS a_spot,
               sum(x.makes)::bigint AS a_makes,
               sum(x.attempts)::bigint AS a_attempts
        FROM h_analytics.fn_filtered_shots(p_player_id, p_season_id, p_date_from, p_date_to, p_context,
                                           p_game_category_code, p_game_format_code, p_zone_code, p_shot_type_code) x
        WHERE x.entry_mode <> 'manual'
        GROUP BY x.court_spot_id
    ),
    spots AS (
        SELECT cs.court_spot_id AS c_id,
               cs.spot_number AS c_number,
               cs.spot_name AS c_name,
               cs.zone_code AS c_zone,
               cs.x_ft AS c_x,
               cs.y_ft AS c_y,
               a.a_makes AS c_makes,
               a.a_attempts AS c_attempts,
               h_analytics.fn_pct(a.a_makes, a.a_attempts) AS c_pct
        FROM h_ref.court_spots cs
        LEFT JOIN agg a ON a.a_spot = cs.court_spot_id
        WHERE (cs.deleted_at IS NULL AND cs.is_active)
           OR a.a_attempts > 0
    )
    SELECT sp.c_id,
           sp.c_number,
           sp.c_name,
           sp.c_zone,
           sp.c_x::numeric,
           sp.c_y::numeric,
           COALESCE(sp.c_makes, 0)::bigint,
           COALESCE(sp.c_attempts, 0)::bigint,
           sp.c_pct,
           hb.band_rank,
           hb.band_label
    FROM spots sp
    LEFT JOIN h_ref.heat_map_bands hb
           ON hb.zone_code = sp.c_zone
          AND hb.deleted_at IS NULL
          AND sp.c_pct >= hb.min_pct
          AND sp.c_pct < COALESCE(hb.max_pct, 1000)
    ORDER BY sp.c_number
$$;
COMMENT ON FUNCTION h_analytics.fn_heat_map_spots(bigint, bigint, date, date, text, text, text, text, text) IS
    'REQ-038: per-spot makes/attempts/pct and fixed colour band, after filter set F.';

-- -----------------------------------------------------------------------------
-- h_analytics.fn_heat_map_zones (REQ-038)
-- Description : The same as fn_heat_map_spots, grouped by zone (the zone toggle).
-- Parameters  : filter set F.
-- Return Values: one row per live zone: zone_code, zone_label, makes, attempts,
--               make_pct, band_rank, band_label.
-- Notes       : Spot shots only (manual entries excluded).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_analytics.fn_heat_map_zones(
    p_player_id          bigint,
    p_season_id          bigint DEFAULT NULL,
    p_date_from          date   DEFAULT NULL,
    p_date_to            date   DEFAULT NULL,
    p_context            text   DEFAULT NULL,
    p_game_category_code text   DEFAULT NULL,
    p_game_format_code   text   DEFAULT NULL,
    p_zone_code          text   DEFAULT NULL,
    p_shot_type_code     text   DEFAULT NULL)
RETURNS TABLE (zone_code text, zone_label text, makes bigint, attempts bigint, make_pct numeric,
               band_rank smallint, band_label text)
LANGUAGE sql
STABLE
AS $$
    WITH agg AS (
        SELECT x.zone_code AS a_zone,
               sum(x.makes)::bigint AS a_makes,
               sum(x.attempts)::bigint AS a_attempts
        FROM h_analytics.fn_filtered_shots(p_player_id, p_season_id, p_date_from, p_date_to, p_context,
                                           p_game_category_code, p_game_format_code, p_zone_code, p_shot_type_code) x
        WHERE x.entry_mode <> 'manual'
        GROUP BY x.zone_code
    ),
    zs AS (
        SELECT z.zone_code AS z_code,
               z.label AS z_label,
               z.sort_order AS z_sort,
               a.a_makes AS z_makes,
               a.a_attempts AS z_attempts,
               h_analytics.fn_pct(a.a_makes, a.a_attempts) AS z_pct
        FROM h_ref.zones z
        LEFT JOIN agg a ON a.a_zone = z.zone_code
        WHERE z.deleted_at IS NULL
    )
    SELECT zs.z_code,
           zs.z_label,
           COALESCE(zs.z_makes, 0)::bigint,
           COALESCE(zs.z_attempts, 0)::bigint,
           zs.z_pct,
           hb.band_rank,
           hb.band_label
    FROM zs
    LEFT JOIN h_ref.heat_map_bands hb
           ON hb.zone_code = zs.z_code
          AND hb.deleted_at IS NULL
          AND zs.z_pct >= hb.min_pct
          AND zs.z_pct < COALESCE(hb.max_pct, 1000)
    ORDER BY zs.z_sort
$$;
COMMENT ON FUNCTION h_analytics.fn_heat_map_zones(bigint, bigint, date, date, text, text, text, text, text) IS
    'REQ-038: per-zone makes/attempts/pct and fixed colour band, after filter set F.';

-- -----------------------------------------------------------------------------
-- h_analytics.fn_game_heat_map_spots (REQ-061)
-- Description : The shot chart of one game (post-game summary): makes, attempts,
--               make % and colour band per court spot from that game's spot shots
--               only (manual entries excluded, REQ-012). Every live active spot
--               appears, plus inactive or deleted spots that have attempts in this
--               game. The band is the zone's band with min_pct <= pct < max_pct
--               (open-ended when max_pct is NULL).
-- Parameters  : p_game_id - h_data.games.game_id (never a player or user id).
-- Return Values: one row per spot, the same columns, types and order as
--               fn_heat_map_spots: court_spot_id, spot_number, spot_name,
--               zone_code, x_ft, y_ft, makes, attempts, make_pct, band_rank,
--               band_label (pct and band NULL with no attempts).
-- Notes       : Reads through v_shot_facts, so deleted entries, a deleted game and
--               a removed profile contribute nothing (COD-22-03). An unknown or
--               deleted game gives only the live active spots with 0/0. Task
--               2026-10-23_games; the filter-set functions are unchanged.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_analytics.fn_game_heat_map_spots(p_game_id bigint)
RETURNS TABLE (court_spot_id bigint, spot_number smallint, spot_name text, zone_code text,
               x_ft numeric, y_ft numeric, makes bigint, attempts bigint, make_pct numeric,
               band_rank smallint, band_label text)
LANGUAGE sql
STABLE
SECURITY INVOKER
AS $$
    WITH agg AS (
        SELECT sf.court_spot_id AS a_spot,
               sum(sf.makes)::bigint AS a_makes,
               sum(sf.attempts)::bigint AS a_attempts
        FROM h_analytics.v_shot_facts sf
        WHERE sf.game_id = p_game_id
          AND sf.entry_mode <> 'manual'
        GROUP BY sf.court_spot_id
    ),
    spots AS (
        SELECT cs.court_spot_id AS c_id,
               cs.spot_number AS c_number,
               cs.spot_name AS c_name,
               cs.zone_code AS c_zone,
               cs.x_ft AS c_x,
               cs.y_ft AS c_y,
               a.a_makes AS c_makes,
               a.a_attempts AS c_attempts,
               h_analytics.fn_pct(a.a_makes, a.a_attempts) AS c_pct
        FROM h_ref.court_spots cs
        LEFT JOIN agg a ON a.a_spot = cs.court_spot_id
        WHERE (cs.deleted_at IS NULL AND cs.is_active)
           OR a.a_attempts > 0
    )
    SELECT sp.c_id,
           sp.c_number,
           sp.c_name,
           sp.c_zone,
           sp.c_x::numeric,
           sp.c_y::numeric,
           COALESCE(sp.c_makes, 0)::bigint,
           COALESCE(sp.c_attempts, 0)::bigint,
           sp.c_pct,
           hb.band_rank,
           hb.band_label
    FROM spots sp
    LEFT JOIN h_ref.heat_map_bands hb
           ON hb.zone_code = sp.c_zone
          AND hb.deleted_at IS NULL
          AND sp.c_pct >= hb.min_pct
          AND sp.c_pct < COALESCE(hb.max_pct, 1000)
    ORDER BY sp.c_number
$$;
COMMENT ON FUNCTION h_analytics.fn_game_heat_map_spots(bigint) IS
    'REQ-061: per-spot makes/attempts/pct and fixed colour band of one game''s spot shots (manual entries excluded); same columns as fn_heat_map_spots.';


-- =============================================================================
-- Averages, seasons, highs, bests, streaks (REQ-039, REQ-040, REQ-060, REQ-061, REQ-065)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- h_analytics.fn_game_averages (REQ-039, REQ-065)
-- Description : Per-game averages and totals of every box-score stat over the live
--               games selected by filter set F, plus the shooting percentages and
--               AST/TO. Zone and shot-type filters apply to the shooting columns
--               only. p_last_n / p_skip_last take the most recent N games after
--               skipping M, so "last 5 vs previous 5" is two calls.
-- Parameters  : filter set F;
--               p_last_n    - number of most recent games; NULL = all.
--               p_skip_last - most recent games to skip first (default 0).
-- Return Values: one row: games_played, then total_ and avg_ of points, fgm, fga,
--               three_pm, three_pa, ftm, fta, off/def/total rebounds, assists,
--               steals, blocks, turnovers, fouls, minutes, then fg_pct, three_pct,
--               ft_pct, efg_pct, ts_pct, ast_to.
-- Notes       : p_context = 'practice' selects no games. avg_minutes averages only
--               games with minutes recorded. Two single games are compared with
--               v_game_box_scores instead.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_analytics.fn_game_averages(
    p_player_id          bigint,
    p_season_id          bigint  DEFAULT NULL,
    p_date_from          date    DEFAULT NULL,
    p_date_to            date    DEFAULT NULL,
    p_context            text    DEFAULT NULL,
    p_game_category_code text    DEFAULT NULL,
    p_game_format_code   text    DEFAULT NULL,
    p_zone_code          text    DEFAULT NULL,
    p_shot_type_code     text    DEFAULT NULL,
    p_last_n             integer DEFAULT NULL,
    p_skip_last          integer DEFAULT 0)
RETURNS TABLE (
    games_played integer,
    total_points bigint, avg_points numeric,
    total_fgm bigint, avg_fgm numeric, total_fga bigint, avg_fga numeric,
    total_three_pm bigint, avg_three_pm numeric, total_three_pa bigint, avg_three_pa numeric,
    total_ftm bigint, avg_ftm numeric, total_fta bigint, avg_fta numeric,
    total_off_rebounds bigint, avg_off_rebounds numeric, total_def_rebounds bigint, avg_def_rebounds numeric,
    total_rebounds bigint, avg_rebounds numeric, total_assists bigint, avg_assists numeric,
    total_steals bigint, avg_steals numeric, total_blocks bigint, avg_blocks numeric,
    total_turnovers bigint, avg_turnovers numeric, total_fouls bigint, avg_fouls numeric,
    total_minutes numeric, avg_minutes numeric,
    fg_pct numeric, three_pct numeric, ft_pct numeric, efg_pct numeric, ts_pct numeric, ast_to numeric)
LANGUAGE sql
STABLE
AS $$
    WITH sel AS (
        SELECT g.game_id AS s_game_id,
               g.off_rebounds AS s_oreb, g.def_rebounds AS s_dreb, g.assists AS s_ast,
               g.steals AS s_stl, g.blocks AS s_blk, g.turnovers AS s_tov, g.fouls AS s_pf,
               g.minutes_played AS s_min
        FROM h_data.games g
        WHERE g.deleted_at IS NULL
          AND g.player_id = p_player_id
          AND (p_season_id IS NULL OR g.season_id = p_season_id)
          AND (p_date_from IS NULL OR g.game_date >= p_date_from)
          AND (p_date_to IS NULL OR g.game_date <= p_date_to)
          AND (p_context IS NULL OR p_context = 'game')
          AND (p_game_category_code IS NULL OR g.game_category_code = p_game_category_code)
          AND (p_game_format_code IS NULL OR g.game_format_code = p_game_format_code)
        ORDER BY g.game_date DESC, g.game_id DESC
        OFFSET GREATEST(COALESCE(p_skip_last, 0), 0)
        LIMIT p_last_n
    ),
    box AS (
        SELECT count(*)::integer AS games,
               COALESCE(sum(sel.s_oreb), 0)::bigint AS oreb,
               COALESCE(sum(sel.s_dreb), 0)::bigint AS dreb,
               COALESCE(sum(sel.s_ast), 0)::bigint AS ast,
               COALESCE(sum(sel.s_stl), 0)::bigint AS stl,
               COALESCE(sum(sel.s_blk), 0)::bigint AS blk,
               COALESCE(sum(sel.s_tov), 0)::bigint AS tov,
               COALESCE(sum(sel.s_pf), 0)::bigint AS pf,
               sum(sel.s_min) AS min_total,
               avg(sel.s_min) AS min_avg
        FROM sel
    ),
    sh AS (
        SELECT COALESCE(sum(x.makes), 0)::bigint AS fgm,
               COALESCE(sum(x.attempts), 0)::bigint AS fga,
               COALESCE(sum(x.makes) FILTER (WHERE x.is_three), 0)::bigint AS three_pm,
               COALESCE(sum(x.attempts) FILTER (WHERE x.is_three), 0)::bigint AS three_pa,
               COALESCE(sum(x.points), 0)::bigint AS fg_points
        FROM h_analytics.fn_filtered_shots(p_player_id, NULL, NULL, NULL, 'game', NULL, NULL,
                                           p_zone_code, p_shot_type_code) x
        WHERE x.game_id IN (SELECT sel.s_game_id FROM sel)
    ),
    ft AS (
        SELECT COALESCE(sum(y.makes), 0)::bigint AS ftm,
               COALESCE(sum(y.attempts), 0)::bigint AS fta
        FROM h_analytics.fn_filtered_free_throws(p_player_id, NULL, NULL, NULL, 'game', NULL, NULL, NULL, NULL) y
        WHERE y.game_id IN (SELECT sel.s_game_id FROM sel)
    )
    SELECT box.games,
           (sh.fg_points + ft.ftm)::bigint, (sh.fg_points + ft.ftm)::numeric / NULLIF(box.games, 0),
           sh.fgm, sh.fgm::numeric / NULLIF(box.games, 0),
           sh.fga, sh.fga::numeric / NULLIF(box.games, 0),
           sh.three_pm, sh.three_pm::numeric / NULLIF(box.games, 0),
           sh.three_pa, sh.three_pa::numeric / NULLIF(box.games, 0),
           ft.ftm, ft.ftm::numeric / NULLIF(box.games, 0),
           ft.fta, ft.fta::numeric / NULLIF(box.games, 0),
           box.oreb, box.oreb::numeric / NULLIF(box.games, 0),
           box.dreb, box.dreb::numeric / NULLIF(box.games, 0),
           (box.oreb + box.dreb)::bigint, (box.oreb + box.dreb)::numeric / NULLIF(box.games, 0),
           box.ast, box.ast::numeric / NULLIF(box.games, 0),
           box.stl, box.stl::numeric / NULLIF(box.games, 0),
           box.blk, box.blk::numeric / NULLIF(box.games, 0),
           box.tov, box.tov::numeric / NULLIF(box.games, 0),
           box.pf, box.pf::numeric / NULLIF(box.games, 0),
           box.min_total, box.min_avg,
           h_analytics.fn_pct(sh.fgm, sh.fga),
           h_analytics.fn_pct(sh.three_pm, sh.three_pa),
           h_analytics.fn_pct(ft.ftm, ft.fta),
           h_analytics.fn_efg_pct(sh.fgm, sh.three_pm, sh.fga),
           h_analytics.fn_ts_pct(sh.fg_points + ft.ftm, sh.fga, ft.fta),
           h_analytics.fn_ratio(box.ast, box.tov)
    FROM box
    CROSS JOIN sh
    CROSS JOIN ft
$$;
COMMENT ON FUNCTION h_analytics.fn_game_averages(bigint, bigint, date, date, text, text, text, text, text, integer, integer) IS
    'REQ-039/065: per-game averages and totals over filtered games, optionally the last N after skipping M.';

-- -----------------------------------------------------------------------------
-- h_analytics.fn_season_summaries (REQ-039)
-- Description : Season vs season: one row per live season of the player, plus a
--               "No season" row for games without a season (only when there are
--               such games), with games played, totals, per-game averages and
--               percentages.
-- Parameters  : p_player_id - h_data.players.player_id.
-- Return Values: season_id (NULL for "No season"), season_name, start_date,
--               end_date, then the same columns as fn_game_averages.
-- Notes       : Ordered by season start date, "No season" last.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_analytics.fn_season_summaries(p_player_id bigint)
RETURNS TABLE (
    season_id bigint, season_name text, start_date date, end_date date,
    games_played integer,
    total_points bigint, avg_points numeric,
    total_fgm bigint, avg_fgm numeric, total_fga bigint, avg_fga numeric,
    total_three_pm bigint, avg_three_pm numeric, total_three_pa bigint, avg_three_pa numeric,
    total_ftm bigint, avg_ftm numeric, total_fta bigint, avg_fta numeric,
    total_off_rebounds bigint, avg_off_rebounds numeric, total_def_rebounds bigint, avg_def_rebounds numeric,
    total_rebounds bigint, avg_rebounds numeric, total_assists bigint, avg_assists numeric,
    total_steals bigint, avg_steals numeric, total_blocks bigint, avg_blocks numeric,
    total_turnovers bigint, avg_turnovers numeric, total_fouls bigint, avg_fouls numeric,
    total_minutes numeric, avg_minutes numeric,
    fg_pct numeric, three_pct numeric, ft_pct numeric, efg_pct numeric, ts_pct numeric, ast_to numeric)
LANGUAGE sql
STABLE
AS $$
    WITH keys AS (
        SELECT s.season_id AS k_id, s.season_name AS k_name, s.start_date AS k_start,
               s.end_date AS k_end, 0 AS k_group
        FROM h_data.seasons s
        WHERE s.player_id = p_player_id
          AND s.deleted_at IS NULL
        UNION ALL
        SELECT NULL::bigint, 'No season'::text, NULL::date, NULL::date, 1
    ),
    agg AS (
        SELECT k.k_id, k.k_name, k.k_start, k.k_end, k.k_group,
               count(b.game_id)::integer AS games,
               COALESCE(sum(b.points), 0)::bigint AS pts,
               COALESCE(sum(b.fgm), 0)::bigint AS fgm,
               COALESCE(sum(b.fga), 0)::bigint AS fga,
               COALESCE(sum(b.three_pm), 0)::bigint AS three_pm,
               COALESCE(sum(b.three_pa), 0)::bigint AS three_pa,
               COALESCE(sum(b.ftm), 0)::bigint AS ftm,
               COALESCE(sum(b.fta), 0)::bigint AS fta,
               COALESCE(sum(b.off_rebounds), 0)::bigint AS oreb,
               COALESCE(sum(b.def_rebounds), 0)::bigint AS dreb,
               COALESCE(sum(b.assists), 0)::bigint AS ast,
               COALESCE(sum(b.steals), 0)::bigint AS stl,
               COALESCE(sum(b.blocks), 0)::bigint AS blk,
               COALESCE(sum(b.turnovers), 0)::bigint AS tov,
               COALESCE(sum(b.fouls), 0)::bigint AS pf,
               sum(b.minutes_played) AS min_total,
               avg(b.minutes_played) AS min_avg
        FROM keys k
        LEFT JOIN h_analytics.v_game_box_scores b
               ON b.player_id = p_player_id
              AND b.season_id IS NOT DISTINCT FROM k.k_id
        GROUP BY k.k_id, k.k_name, k.k_start, k.k_end, k.k_group
    )
    SELECT a.k_id, a.k_name, a.k_start, a.k_end,
           a.games,
           a.pts, a.pts::numeric / NULLIF(a.games, 0),
           a.fgm, a.fgm::numeric / NULLIF(a.games, 0),
           a.fga, a.fga::numeric / NULLIF(a.games, 0),
           a.three_pm, a.three_pm::numeric / NULLIF(a.games, 0),
           a.three_pa, a.three_pa::numeric / NULLIF(a.games, 0),
           a.ftm, a.ftm::numeric / NULLIF(a.games, 0),
           a.fta, a.fta::numeric / NULLIF(a.games, 0),
           a.oreb, a.oreb::numeric / NULLIF(a.games, 0),
           a.dreb, a.dreb::numeric / NULLIF(a.games, 0),
           (a.oreb + a.dreb)::bigint, (a.oreb + a.dreb)::numeric / NULLIF(a.games, 0),
           a.ast, a.ast::numeric / NULLIF(a.games, 0),
           a.stl, a.stl::numeric / NULLIF(a.games, 0),
           a.blk, a.blk::numeric / NULLIF(a.games, 0),
           a.tov, a.tov::numeric / NULLIF(a.games, 0),
           a.pf, a.pf::numeric / NULLIF(a.games, 0),
           a.min_total, a.min_avg,
           h_analytics.fn_pct(a.fgm, a.fga),
           h_analytics.fn_pct(a.three_pm, a.three_pa),
           h_analytics.fn_pct(a.ftm, a.fta),
           h_analytics.fn_efg_pct(a.fgm, a.three_pm, a.fga),
           h_analytics.fn_ts_pct(a.pts, a.fga, a.fta),
           h_analytics.fn_ratio(a.ast, a.tov)
    FROM agg a
    WHERE a.k_id IS NOT NULL OR a.games > 0
    ORDER BY a.k_group, a.k_start
$$;
COMMENT ON FUNCTION h_analytics.fn_season_summaries(bigint) IS
    'REQ-039: per-season totals, averages and percentages, plus a "No season" row when needed.';

-- -----------------------------------------------------------------------------
-- h_analytics.fn_career_highs (REQ-040)
-- Description : Career highs in single-game points, total rebounds, assists,
--               steals and blocks. The earliest game reaching the maximum is shown.
-- Parameters  : p_player_id - h_data.players.player_id.
-- Return Values: one row per stat: stat_code, value, game_id, game_date.
-- Notes       : Live games only. No rows when the player has no games.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_analytics.fn_career_highs(p_player_id bigint)
RETURNS TABLE (stat_code text, value integer, game_id bigint, game_date date)
LANGUAGE sql
STABLE
AS $$
    SELECT q.q_code, q.q_value, q.q_game, q.q_date
    FROM (
        SELECT DISTINCT ON (u.u_code)
               u.u_code AS q_code, u.u_value AS q_value, u.u_game AS q_game,
               u.u_date AS q_date, u.u_ord AS q_ord
        FROM (
            SELECT v.code AS u_code, v.val AS u_value, b.game_id AS u_game,
                   b.game_date AS u_date, v.ord AS u_ord
            FROM h_analytics.v_game_box_scores b
            CROSS JOIN LATERAL (VALUES
                ('points'::text,  b.points::integer,          1),
                ('total_rebounds', b.total_rebounds::integer, 2),
                ('assists',        b.assists::integer,        3),
                ('steals',         b.steals::integer,         4),
                ('blocks',         b.blocks::integer,         5)
            ) AS v (code, val, ord)
            WHERE b.player_id = p_player_id
        ) u
        ORDER BY u.u_code, u.u_value DESC, u.u_date, u.u_game
    ) q
    ORDER BY q.q_ord
$$;
COMMENT ON FUNCTION h_analytics.fn_career_highs(bigint) IS
    'REQ-040: single-game career highs (points, rebounds, assists, steals, blocks), earliest game reaching each.';

-- -----------------------------------------------------------------------------
-- h_analytics.fn_game_highlights (REQ-061)
-- Description : For the post-game summary, compares this game's points, total
--               rebounds, assists, steals and blocks with the maximum over the
--               player's earlier live games (by game_date, then game_id).
-- Parameters  : p_game_id - h_data.games.game_id.
-- Return Values: one row per stat: stat_code, value, previous_high (NULL when no
--               earlier game), is_new_high (value > 0 and above the previous high).
-- Notes       : A tie with the previous high is not a new high.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_analytics.fn_game_highlights(p_game_id bigint)
RETURNS TABLE (stat_code text, value integer, previous_high integer, is_new_high boolean)
LANGUAGE sql
STABLE
AS $$
    WITH this_game AS (
        SELECT b.game_id AS t_game, b.player_id AS t_player, b.game_date AS t_date,
               b.points AS t_points, b.total_rebounds AS t_reb, b.assists AS t_ast,
               b.steals AS t_stl, b.blocks AS t_blk
        FROM h_analytics.v_game_box_scores b
        WHERE b.game_id = p_game_id
    ),
    cur AS (
        SELECT v.code AS c_code, v.val AS c_value, v.ord AS c_ord
        FROM this_game t
        CROSS JOIN LATERAL (VALUES
            ('points'::text,  t.t_points::integer, 1),
            ('total_rebounds', t.t_reb::integer,   2),
            ('assists',        t.t_ast::integer,   3),
            ('steals',         t.t_stl::integer,   4),
            ('blocks',         t.t_blk::integer,   5)
        ) AS v (code, val, ord)
    ),
    earlier AS (
        SELECT v.code AS e_code, max(v.val) AS e_high
        FROM this_game t
        JOIN h_analytics.v_game_box_scores b
          ON b.player_id = t.t_player
         AND (b.game_date < t.t_date OR (b.game_date = t.t_date AND b.game_id < t.t_game))
        CROSS JOIN LATERAL (VALUES
            ('points'::text,  b.points::integer),
            ('total_rebounds', b.total_rebounds::integer),
            ('assists',        b.assists::integer),
            ('steals',         b.steals::integer),
            ('blocks',         b.blocks::integer)
        ) AS v (code, val)
        GROUP BY v.code
    )
    SELECT cur.c_code,
           cur.c_value,
           earlier.e_high,
           (cur.c_value > 0 AND (earlier.e_high IS NULL OR cur.c_value > earlier.e_high))
    FROM cur
    LEFT JOIN earlier ON earlier.e_code = cur.c_code
    ORDER BY cur.c_ord
$$;
COMMENT ON FUNCTION h_analytics.fn_game_highlights(bigint) IS
    'REQ-061: this game''s key stats against the previous career highs.';

-- -----------------------------------------------------------------------------
-- h_analytics.fn_drill_personal_bests (REQ-040, REQ-060, REQ-076 to REQ-080)
-- Description : Personal bests of one player per drill lineage, over every version
--               of the drill whose result type is the latest live version's type.
--               Shooting: the most makes in one attempt among attempts with
--               attempts >= that version's default target (all attempts with shots
--               when there is no target); ties go to fewer attempts, then the
--               earlier date. Single numbers: the best attempt value in the result
--               type's direction (or, for a physical measurement, the drill's), only
--               over versions with the same unit as the latest. Physical second
--               number: the same for value_2. Outcome buttons: per outcome group
--               (matched by group number), the best per-practice clean-rep % among
--               results with reps >= the version's default target (Gate 1 Q7).
-- Parameters  : p_player_id - h_data.players.player_id (never the acting user).
--               p_drill_id  - one drill lineage (h_data.drills.drill_id), or NULL
--                             for every drill with a qualifying attempt or result.
-- Return Values: one row per drill and metric: drill_id, drill_name,
--               result_type_code (latest version's), metric ('makes', 'value_1',
--               'value_2' or 'clean_pct_<group>'), metric_label, unit ('shots',
--               the value unit, or 'pct'), higher_is_better, best_value,
--               best_makes and best_attempts (shooting; for outcomes best_attempts
--               holds the reps), achieved_on, practice_session_id, drill_result_id,
--               drill_attempt_id (NULL for outcome groups), drill_version_id.
-- Notes       : Remaining ties go to the earlier session date, then the lower
--               attempt or result id. Ordered by drill name, then metric. "Latest"
--               = the highest live version of a live drill; retired drills still
--               have bests. Attempts count only through live results of live
--               practices (v_drill_attempts).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_analytics.fn_drill_personal_bests(p_player_id bigint, p_drill_id bigint DEFAULT NULL)
RETURNS TABLE (drill_id bigint, drill_name text, result_type_code text, metric text, metric_label text, unit text,
               higher_is_better boolean, best_value numeric, best_makes bigint, best_attempts bigint, achieved_on date,
               practice_session_id bigint, drill_result_id bigint, drill_attempt_id bigint, drill_version_id bigint)
LANGUAGE sql
STABLE
AS $$
    WITH latest AS (
        SELECT DISTINCT ON (dv.drill_id)
               dv.drill_id AS l_drill_id,
               d.drill_name AS l_name,
               dv.drill_version_id AS l_version,
               dv.result_type_code AS l_type,
               dv.value_1_label AS l_v1_label,
               dv.value_1_unit AS l_v1_unit,
               dv.value_1_higher_is_better AS l_v1_higher,
               dv.value_2_label AS l_v2_label,
               dv.value_2_unit AS l_v2_unit,
               dv.value_2_higher_is_better AS l_v2_higher
        FROM h_data.drill_versions dv
        JOIN h_data.drills d ON d.drill_id = dv.drill_id
        WHERE dv.deleted_at IS NULL
          AND d.deleted_at IS NULL
          AND (p_drill_id IS NULL OR dv.drill_id = p_drill_id)
        ORDER BY dv.drill_id, dv.version_no DESC
    ),
    shooting AS (
        SELECT DISTINCT ON (a.drill_id)
               a.drill_id AS c_drill_id,
               'makes'::text AS c_metric,
               'Makes'::text AS c_label,
               'shots'::text AS c_unit,
               true AS c_higher,
               a.makes::numeric AS c_value,
               a.makes AS c_makes,
               a.attempts AS c_attempts,
               a.session_date AS c_date,
               a.practice_session_id AS c_session,
               a.drill_result_id AS c_result,
               a.drill_attempt_id AS c_attempt,
               a.drill_version_id AS c_version
        FROM h_analytics.v_drill_attempts a
        JOIN latest l ON l.l_drill_id = a.drill_id AND l.l_type = 'shooting'
        JOIN h_data.drill_versions dv ON dv.drill_version_id = a.drill_version_id
        WHERE a.player_id = p_player_id
          AND a.result_type_code = 'shooting'
          AND a.attempts > 0
          AND a.attempts >= COALESCE(dv.default_target, 0)
        ORDER BY a.drill_id, a.makes DESC, a.attempts ASC, a.session_date, a.drill_attempt_id
    ),
    first_values AS (
        SELECT DISTINCT ON (a.drill_id)
               a.drill_id AS c_drill_id,
               'value_1'::text AS c_metric,
               COALESCE(l.l_v1_label, rt.label) AS c_label,
               COALESCE(rt.value_unit, l.l_v1_unit) AS c_unit,
               COALESCE(rt.higher_is_better, l.l_v1_higher) AS c_higher,
               a.value_1::numeric AS c_value,
               NULL::bigint AS c_makes,
               NULL::bigint AS c_attempts,
               a.session_date AS c_date,
               a.practice_session_id AS c_session,
               a.drill_result_id AS c_result,
               a.drill_attempt_id AS c_attempt,
               a.drill_version_id AS c_version
        FROM h_analytics.v_drill_attempts a
        JOIN latest l ON l.l_drill_id = a.drill_id AND l.l_type = a.result_type_code
        JOIN h_ref.result_types rt ON rt.result_type_code = a.result_type_code
        JOIN h_data.drill_versions dv ON dv.drill_version_id = a.drill_version_id
        WHERE a.player_id = p_player_id
          AND rt.capture_kind = 'values'
          AND a.value_1 IS NOT NULL
          AND dv.value_1_unit IS NOT DISTINCT FROM l.l_v1_unit
        ORDER BY a.drill_id,
                 CASE WHEN COALESCE(rt.higher_is_better, l.l_v1_higher) THEN a.value_1 END DESC NULLS LAST,
                 CASE WHEN NOT COALESCE(rt.higher_is_better, l.l_v1_higher) THEN a.value_1 END ASC NULLS LAST,
                 a.session_date,
                 a.drill_attempt_id
    ),
    second_values AS (
        SELECT DISTINCT ON (a.drill_id)
               a.drill_id AS c_drill_id,
               'value_2'::text AS c_metric,
               l.l_v2_label AS c_label,
               l.l_v2_unit AS c_unit,
               l.l_v2_higher AS c_higher,
               a.value_2::numeric AS c_value,
               NULL::bigint AS c_makes,
               NULL::bigint AS c_attempts,
               a.session_date AS c_date,
               a.practice_session_id AS c_session,
               a.drill_result_id AS c_result,
               a.drill_attempt_id AS c_attempt,
               a.drill_version_id AS c_version
        FROM h_analytics.v_drill_attempts a
        JOIN latest l ON l.l_drill_id = a.drill_id AND l.l_type = 'physical' AND l.l_v2_unit IS NOT NULL
        JOIN h_data.drill_versions dv ON dv.drill_version_id = a.drill_version_id
        WHERE a.player_id = p_player_id
          AND a.result_type_code = 'physical'
          AND a.value_2 IS NOT NULL
          AND dv.value_2_unit = l.l_v2_unit
        ORDER BY a.drill_id,
                 CASE WHEN l.l_v2_higher THEN a.value_2 END DESC NULLS LAST,
                 CASE WHEN NOT l.l_v2_higher THEN a.value_2 END ASC NULLS LAST,
                 a.session_date,
                 a.drill_attempt_id
    ),
    outcomes AS (
        SELECT DISTINCT ON (r.drill_id, g.g_no)
               r.drill_id AS c_drill_id,
               ('clean_pct_' || g.g_no)::text AS c_metric,
               ('Clean reps % (' || COALESCE(lg.group_name, 'group ' || g.g_no) || ')')::text AS c_label,
               'pct'::text AS c_unit,
               true AS c_higher,
               g.g_pct AS c_value,
               NULL::bigint AS c_makes,
               g.g_reps::bigint AS c_attempts,
               r.session_date AS c_date,
               r.practice_session_id AS c_session,
               r.drill_result_id AS c_result,
               NULL::bigint AS c_attempt,
               r.drill_version_id AS c_version
        FROM h_analytics.v_drill_results r
        JOIN latest l ON l.l_drill_id = r.drill_id AND l.l_type = 'outcome_buttons'
        JOIN h_data.drill_versions dv ON dv.drill_version_id = r.drill_version_id
        CROSS JOIN LATERAL (VALUES (1, r.reps_1, r.clean_pct_1),
                                   (2, r.reps_2, r.clean_pct_2),
                                   (3, r.reps_3, r.clean_pct_3)) AS g (g_no, g_reps, g_pct)
        LEFT JOIN h_data.drill_outcome_groups lg
               ON lg.drill_version_id = l.l_version
              AND lg.group_no = g.g_no
              AND lg.deleted_at IS NULL
        WHERE r.player_id = p_player_id
          AND r.result_type_code = 'outcome_buttons'
          AND g.g_reps > 0
          AND g.g_reps >= COALESCE(dv.default_target, 0)
        ORDER BY r.drill_id, g.g_no, g.g_pct DESC, g.g_reps DESC, r.session_date, r.drill_result_id
    ),
    candidates AS (
        SELECT * FROM shooting
        UNION ALL
        SELECT * FROM first_values
        UNION ALL
        SELECT * FROM second_values
        UNION ALL
        SELECT * FROM outcomes
    )
    SELECT c.c_drill_id, l.l_name, l.l_type, c.c_metric, c.c_label, c.c_unit, c.c_higher, c.c_value,
           c.c_makes, c.c_attempts, c.c_date, c.c_session, c.c_result, c.c_attempt, c.c_version
    FROM candidates c
    JOIN latest l ON l.l_drill_id = c.c_drill_id
    ORDER BY l.l_name, c.c_metric
$$;
COMMENT ON FUNCTION h_analytics.fn_drill_personal_bests(bigint, bigint) IS
    'REQ-040/060/078: personal bests per drill over versions of its latest result type: best shooting attempt, best value per number, best clean-rep % per outcome group.';

-- -----------------------------------------------------------------------------
-- h_analytics.fn_longest_make_streak (REQ-040, Open Question 3)
-- Description : Longest runs of consecutive made single field-goal shots within one
--               game or practice (gaps-and-islands over seq_in_context). A streak
--               resets at any miss and at every event boundary. The streak groups
--               are built over ALL the player's live single field-goal shots in
--               order, BEFORE filter set F; F then picks the made shots counted. So
--               with a zone or shot-type filter, a streak counts the made shots of
--               that zone or type, and a miss of any zone or type still ends it
--               (Overseer decision on review_1, 2026-10-17).
-- Parameters  : filter set F;
--               p_limit - number of streaks to return (default 1).
-- Return Values: streak_length, context, game_id, practice_session_id, event_date,
--               first_shot_entry_id, last_shot_entry_id; longest first.
-- Notes       : Ties go to the earliest event (event_date, then first shot id).
--               Made shots outside F inside a streak are not counted in its length.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_analytics.fn_longest_make_streak(
    p_player_id          bigint,
    p_season_id          bigint  DEFAULT NULL,
    p_date_from          date    DEFAULT NULL,
    p_date_to            date    DEFAULT NULL,
    p_context            text    DEFAULT NULL,
    p_game_category_code text    DEFAULT NULL,
    p_game_format_code   text    DEFAULT NULL,
    p_zone_code          text    DEFAULT NULL,
    p_shot_type_code     text    DEFAULT NULL,
    p_limit              integer DEFAULT 1)
RETURNS TABLE (streak_length integer, context text, game_id bigint, practice_session_id bigint,
               event_date date, first_shot_entry_id bigint, last_shot_entry_id bigint)
LANGUAGE sql
STABLE
AS $$
    WITH islands AS (
        SELECT sf.shot_entry_id AS i_id,
               count(*) FILTER (WHERE sf.makes = 0)
                   OVER (PARTITION BY sf.game_id, sf.practice_session_id
                         ORDER BY sf.seq_in_context
                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS i_island
        FROM h_analytics.v_shot_facts sf
        WHERE sf.entry_mode = 'single'
          AND sf.player_id = p_player_id
    ),
    s AS (
        SELECT x.shot_entry_id AS s_id,
               x.context AS s_context,
               x.game_id AS s_game,
               x.practice_session_id AS s_practice,
               x.event_date AS s_date,
               x.seq_in_context AS s_seq,
               x.makes AS s_makes,
               i.i_island AS s_island
        FROM h_analytics.fn_filtered_shots(p_player_id, p_season_id, p_date_from, p_date_to, p_context,
                                           p_game_category_code, p_game_format_code, p_zone_code, p_shot_type_code) x
        JOIN islands i ON i.i_id = x.shot_entry_id
        WHERE x.entry_mode = 'single'
    ),
    streaks AS (
        SELECT s.s_context AS st_context,
               s.s_game AS st_game,
               s.s_practice AS st_practice,
               s.s_date AS st_date,
               count(*)::integer AS st_len,
               (array_agg(s.s_id ORDER BY s.s_seq))[1] AS st_first,
               (array_agg(s.s_id ORDER BY s.s_seq DESC))[1] AS st_last
        FROM s
        WHERE s.s_makes = 1
        GROUP BY s.s_context, s.s_game, s.s_practice, s.s_date, s.s_island
    )
    SELECT st.st_len, st.st_context, st.st_game, st.st_practice, st.st_date, st.st_first, st.st_last
    FROM streaks st
    ORDER BY st.st_len DESC, st.st_date, st.st_first
    LIMIT GREATEST(COALESCE(p_limit, 1), 1)
$$;
COMMENT ON FUNCTION h_analytics.fn_longest_make_streak(bigint, bigint, date, date, text, text, text, text, text, integer) IS
    'REQ-040: longest made-shot streaks (single field goals) within an event; any miss ends a streak, filter set F picks the made shots counted.';


-- =============================================================================
-- Stat values and goal progress (REQ-033, REQ-041)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- h_analytics.fn_stat_value (REQ-033)
-- Description : Value of one stat over a goal window. The window holds the
--               player's live events (games, practices or both per p_scope) on or
--               after p_on_or_after and on or before p_today: last_n = the N most
--               recent events (by date, then id); season = the season's events;
--               date_range = events in the range. Percent and ratio stats come from
--               summed facts; count stats are averages per event in the window:
--               game-only stats per game; the "both"-scope shot counts (fgm, fga,
--               three_pm, three_pa, ftm, fta) per game or practice, so with scope
--               'both' they divide by games + practices.
-- Parameters  : p_player_id   - h_data.players.player_id.
--               p_stat_code   - h_ref.stat_definitions code.
--               p_scope       - 'game', 'practice' or 'both'.
--               p_window_kind - 'last_n', 'season' or 'date_range'.
--               p_window_n    - N for last_n.
--               p_season_id   - season for 'season'.
--               p_date_from, p_date_to - range for 'date_range'.
--               p_on_or_after - earliest event date counted (NULL = no limit).
--               p_today       - the device-local date (REQ-055).
-- Return Values: one row: stat_value (NULL when there is no data, or for a
--               game-only stat with scope 'practice'), sample_events.
-- Notes       : A stat that applies to games only counts games only, whatever the
--               scope. Raises on an unknown stat, scope or window kind. A practice
--               counts when the player is a live participant (REQ-074), with the
--               participant's season; only that player's shots and free throws are
--               summed.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_analytics.fn_stat_value(
    p_player_id   bigint,
    p_stat_code   text,
    p_scope       text,
    p_window_kind text,
    p_window_n    integer,
    p_season_id   bigint,
    p_date_from   date,
    p_date_to     date,
    p_on_or_after date,
    p_today       date)
RETURNS TABLE (stat_value numeric, sample_events integer)
LANGUAGE plpgsql
STABLE
AS $$
#variable_conflict use_column
DECLARE
    v_applies_to     text;
    v_use_games      boolean;
    v_use_practices  boolean;
    v_game_ids       bigint[];
    v_practice_ids   bigint[];
    v_events         integer;
    v_games          integer;
    v_fgm            bigint;
    v_fga            bigint;
    v_3pm            bigint;
    v_3pa            bigint;
    v_fg_points      bigint;
    v_ftm            bigint;
    v_fta            bigint;
    v_oreb           bigint;
    v_dreb           bigint;
    v_ast            bigint;
    v_stl            bigint;
    v_blk            bigint;
    v_tov            bigint;
    v_pf             bigint;
    v_min_avg        numeric;
BEGIN
    IF p_today IS NULL THEN
        RAISE EXCEPTION 'p_today (the device-local date) is required';
    END IF;
    SELECT sd.applies_to
    INTO v_applies_to
    FROM h_ref.stat_definitions sd
    WHERE sd.stat_code = p_stat_code
      AND sd.deleted_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Unknown stat "%"', p_stat_code;
    END IF;
    IF p_scope IS NULL OR p_scope NOT IN ('game', 'practice', 'both') THEN
        RAISE EXCEPTION 'Unknown scope "%"', p_scope;
    END IF;
    IF p_window_kind IS NULL OR p_window_kind NOT IN ('last_n', 'season', 'date_range') THEN
        RAISE EXCEPTION 'Unknown window kind "%"', p_window_kind;
    END IF;

    IF v_applies_to = 'game' AND p_scope = 'practice' THEN
        stat_value := NULL;
        sample_events := 0;
        RETURN NEXT;
        RETURN;
    END IF;

    v_use_games := p_scope IN ('game', 'both');
    v_use_practices := p_scope IN ('practice', 'both') AND v_applies_to = 'both';

    SELECT COALESCE(array_agg(w.w_id) FILTER (WHERE w.w_kind = 'game'), '{}'::bigint[]),
           COALESCE(array_agg(w.w_id) FILTER (WHERE w.w_kind = 'practice'), '{}'::bigint[]),
           count(*)::integer
    INTO v_game_ids, v_practice_ids, v_events
    FROM (
        SELECT ev.e_kind AS w_kind, ev.e_id AS w_id
        FROM (
            SELECT 'game'::text AS e_kind, g.game_id AS e_id, g.game_date AS e_date
            FROM h_data.games g
            WHERE v_use_games
              AND g.player_id = p_player_id
              AND g.deleted_at IS NULL
              AND g.game_date <= p_today
              AND (p_on_or_after IS NULL OR g.game_date >= p_on_or_after)
              AND (p_window_kind <> 'season' OR g.season_id = p_season_id)
              AND (p_window_kind <> 'date_range' OR g.game_date BETWEEN p_date_from AND p_date_to)
            UNION ALL
            SELECT 'practice'::text, ps.practice_session_id, ps.session_date
            FROM h_data.practice_players pp
            JOIN h_data.practice_sessions ps ON ps.practice_session_id = pp.practice_session_id
            WHERE v_use_practices
              AND pp.player_id = p_player_id
              AND pp.deleted_at IS NULL
              AND ps.deleted_at IS NULL
              AND ps.session_date <= p_today
              AND (p_on_or_after IS NULL OR ps.session_date >= p_on_or_after)
              AND (p_window_kind <> 'season' OR pp.season_id = p_season_id)
              AND (p_window_kind <> 'date_range' OR ps.session_date BETWEEN p_date_from AND p_date_to)
        ) ev
        ORDER BY ev.e_date DESC, ev.e_kind, ev.e_id DESC
        LIMIT CASE WHEN p_window_kind = 'last_n' THEN p_window_n END
    ) w;

    SELECT COALESCE(sum(sf.makes), 0),
           COALESCE(sum(sf.attempts), 0),
           COALESCE(sum(sf.makes) FILTER (WHERE sf.is_three), 0),
           COALESCE(sum(sf.attempts) FILTER (WHERE sf.is_three), 0),
           COALESCE(sum(sf.points), 0)
    INTO v_fgm, v_fga, v_3pm, v_3pa, v_fg_points
    FROM h_analytics.v_shot_facts sf
    WHERE sf.player_id = p_player_id
      AND (sf.game_id = ANY (v_game_ids)
           OR sf.practice_session_id = ANY (v_practice_ids));

    SELECT COALESCE(sum(ff.makes), 0),
           COALESCE(sum(ff.attempts), 0)
    INTO v_ftm, v_fta
    FROM h_analytics.v_free_throw_facts ff
    WHERE ff.player_id = p_player_id
      AND (ff.game_id = ANY (v_game_ids)
           OR ff.practice_session_id = ANY (v_practice_ids));

    SELECT count(*)::integer,
           COALESCE(sum(g.off_rebounds), 0),
           COALESCE(sum(g.def_rebounds), 0),
           COALESCE(sum(g.assists), 0),
           COALESCE(sum(g.steals), 0),
           COALESCE(sum(g.blocks), 0),
           COALESCE(sum(g.turnovers), 0),
           COALESCE(sum(g.fouls), 0),
           avg(g.minutes_played)
    INTO v_games, v_oreb, v_dreb, v_ast, v_stl, v_blk, v_tov, v_pf, v_min_avg
    FROM h_data.games g
    WHERE g.game_id = ANY (v_game_ids);

    stat_value := CASE p_stat_code
        WHEN 'fg_pct'         THEN h_analytics.fn_pct(v_fgm, v_fga)
        WHEN 'three_pct'      THEN h_analytics.fn_pct(v_3pm, v_3pa)
        WHEN 'ft_pct'         THEN h_analytics.fn_pct(v_ftm, v_fta)
        WHEN 'efg_pct'        THEN h_analytics.fn_efg_pct(v_fgm, v_3pm, v_fga)
        WHEN 'ts_pct'         THEN h_analytics.fn_ts_pct(v_fg_points + v_ftm, v_fga, v_fta)
        WHEN 'ast_to'         THEN h_analytics.fn_ratio(v_ast, v_tov)
        WHEN 'points'         THEN (v_fg_points + v_ftm)::numeric / NULLIF(v_events, 0)
        WHEN 'fgm'            THEN v_fgm::numeric / NULLIF(v_events, 0)
        WHEN 'fga'            THEN v_fga::numeric / NULLIF(v_events, 0)
        WHEN 'three_pm'       THEN v_3pm::numeric / NULLIF(v_events, 0)
        WHEN 'three_pa'       THEN v_3pa::numeric / NULLIF(v_events, 0)
        WHEN 'ftm'            THEN v_ftm::numeric / NULLIF(v_events, 0)
        WHEN 'fta'            THEN v_fta::numeric / NULLIF(v_events, 0)
        WHEN 'off_rebounds'   THEN v_oreb::numeric / NULLIF(v_games, 0)
        WHEN 'def_rebounds'   THEN v_dreb::numeric / NULLIF(v_games, 0)
        WHEN 'total_rebounds' THEN (v_oreb + v_dreb)::numeric / NULLIF(v_games, 0)
        WHEN 'assists'        THEN v_ast::numeric / NULLIF(v_games, 0)
        WHEN 'steals'         THEN v_stl::numeric / NULLIF(v_games, 0)
        WHEN 'blocks'         THEN v_blk::numeric / NULLIF(v_games, 0)
        WHEN 'turnovers'      THEN v_tov::numeric / NULLIF(v_games, 0)
        WHEN 'fouls'          THEN v_pf::numeric / NULLIF(v_games, 0)
        WHEN 'minutes'        THEN v_min_avg
        ELSE NULL
    END;
    sample_events := v_events;
    RETURN NEXT;
END
$$;
COMMENT ON FUNCTION h_analytics.fn_stat_value(bigint, text, text, text, integer, bigint, date, date, date, date) IS
    'REQ-033: value of one stat over a goal window (percent/ratio from sums; counts as averages per game, or per game or practice for the both-scope shot counts).';

-- -----------------------------------------------------------------------------
-- h_analytics.fn_goal_progress (REQ-033, REQ-041, Open Question 4)
-- Description : Progress of the player's non-deleted goals.
--               stat     - fn_stat_value. (a) last_n windows count events on or
--                          after start_date only; (b) last_n goals are met only once
--                          N events exist; (c) season and date-range goals are met
--                          only after the window has ended (p_today > window end).
--               drill    - best or latest attempt metric (v_drill_attempts:
--                          makes, make_pct, value_1, value_2), or clean-rep % of
--                          group drill_group_no per result (v_drill_results), on or
--                          after start_date ("best" in the target direction).
--               physical - latest measurement on or after start_date.
--               habit    - progress = distinct days with a live game or practice
--                          (as a live participant) in the current Monday-Sunday
--                          week (on or after start_date).
--                          (d) Met only from the deadline on, when every complete
--                          Monday-Sunday week from start to deadline reached the
--                          target; never met without a deadline.
-- Parameters  : p_today     - the device-local date (REQ-055).
--               p_player_id - h_data.players.player_id.
--               p_goal_id   - one goal, or NULL for all of the player's goals.
-- Return Values: one row per goal: goal_id, goal_type, progress_value,
--               target_value, target_direction, is_met, progress_ratio (0..1,
--               capped; target/value for at_most goals), sample_events, detail.
-- Notes       : Used by h_system.fn_refresh_goal_statuses. If the habit window
--               holds no complete week, the whole start-to-deadline span must
--               reach the target instead (Developer choice, flagged for review).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION h_analytics.fn_goal_progress(p_today date, p_player_id bigint, p_goal_id bigint DEFAULT NULL)
RETURNS TABLE (goal_id bigint, goal_type text, progress_value numeric, target_value numeric,
               target_direction text, is_met boolean, progress_ratio numeric, sample_events integer,
               detail text)
LANGUAGE plpgsql
STABLE
AS $$
#variable_conflict use_column
DECLARE
    v_goal           record;
    v_value          numeric;
    v_events         integer;
    v_judgeable      boolean;
    v_met            boolean;
    v_detail         text;
    v_on_or_after    date;
    v_window_end     date;
    v_week_start     date;
    v_active_dates   date[];
    v_first_monday   date;
    v_last_sunday    date;
    v_weeks          integer;
    v_weeks_met      integer;
    v_span_days      integer;
BEGIN
    IF p_today IS NULL THEN
        RAISE EXCEPTION 'p_today (the device-local date) is required';
    END IF;

    FOR v_goal IN
        SELECT g.goal_id AS g_id, g.goal_type AS g_type, g.target_value AS g_target,
               g.target_direction AS g_direction, g.start_date AS g_start, g.deadline AS g_deadline,
               g.stat_code AS g_stat, g.window_kind AS g_window_kind, g.window_n AS g_window_n,
               g.window_season_id AS g_season, g.window_from AS g_from, g.window_to AS g_to,
               g.window_scope AS g_scope, g.drill_id AS g_drill, g.drill_metric AS g_metric,
               g.drill_basis AS g_basis, g.physical_test_id AS g_test, g.drill_group_no AS g_group
        FROM h_data.goals g
        WHERE g.player_id = p_player_id
          AND g.deleted_at IS NULL
          AND (p_goal_id IS NULL OR g.goal_id = p_goal_id)
        ORDER BY g.goal_id
    LOOP
        v_value := NULL;
        v_events := 0;
        v_judgeable := true;
        v_met := NULL;
        v_detail := NULL;

        IF v_goal.g_type = 'stat' THEN
            v_on_or_after := CASE WHEN v_goal.g_window_kind = 'last_n' THEN v_goal.g_start END;
            SELECT sv.stat_value, sv.sample_events
            INTO v_value, v_events
            FROM h_analytics.fn_stat_value(p_player_id, v_goal.g_stat, v_goal.g_scope, v_goal.g_window_kind,
                                           v_goal.g_window_n::integer, v_goal.g_season, v_goal.g_from,
                                           v_goal.g_to, v_on_or_after, p_today) sv;
            IF v_goal.g_window_kind = 'last_n' THEN
                v_judgeable := v_events >= v_goal.g_window_n;
                IF NOT v_judgeable THEN
                    v_detail := format('%s of %s events so far since %s', v_events, v_goal.g_window_n,
                                       to_char(v_goal.g_start, 'YYYY-MM-DD'));
                END IF;
            ELSE
                IF v_goal.g_window_kind = 'season' THEN
                    SELECT s.end_date INTO v_window_end FROM h_data.seasons s WHERE s.season_id = v_goal.g_season;
                ELSE
                    v_window_end := v_goal.g_to;
                END IF;
                v_judgeable := v_window_end IS NOT NULL AND p_today > v_window_end;
                IF NOT v_judgeable THEN
                    v_detail := 'Judged after the window ends on ' || COALESCE(to_char(v_window_end, 'YYYY-MM-DD'), '?');
                END IF;
            END IF;

        ELSIF v_goal.g_type = 'drill' THEN
            SELECT CASE
                       WHEN v_goal.g_basis = 'latest'
                           THEN (array_agg(m.m_val ORDER BY m.m_date DESC, m.m_order DESC))[1]
                       WHEN v_goal.g_direction = 'at_most' THEN min(m.m_val)
                       ELSE max(m.m_val)
                   END,
                   count(*)::integer
            INTO v_value, v_events
            FROM (
                SELECT a.session_date AS m_date,
                       a.drill_attempt_id AS m_order,
                       CASE v_goal.g_metric
                           WHEN 'makes'    THEN a.makes::numeric
                           WHEN 'make_pct' THEN a.make_pct
                           WHEN 'value_1'  THEN a.value_1
                           WHEN 'value_2'  THEN a.value_2
                       END AS m_val
                FROM h_analytics.v_drill_attempts a
                WHERE v_goal.g_metric IN ('makes', 'make_pct', 'value_1', 'value_2')
                  AND a.player_id = p_player_id
                  AND a.drill_id = v_goal.g_drill
                  AND a.session_date >= v_goal.g_start
                  AND a.session_date <= p_today
                UNION ALL
                SELECT r.session_date,
                       r.drill_result_id,
                       CASE v_goal.g_group
                           WHEN 1 THEN r.clean_pct_1
                           WHEN 2 THEN r.clean_pct_2
                           WHEN 3 THEN r.clean_pct_3
                       END
                FROM h_analytics.v_drill_results r
                WHERE v_goal.g_metric = 'clean_pct'
                  AND r.player_id = p_player_id
                  AND r.drill_id = v_goal.g_drill
                  AND r.session_date >= v_goal.g_start
                  AND r.session_date <= p_today
            ) m
            WHERE m.m_val IS NOT NULL;
            v_detail := initcap(v_goal.g_basis) || ' result since ' || to_char(v_goal.g_start, 'YYYY-MM-DD');

        ELSIF v_goal.g_type = 'physical' THEN
            SELECT pm.measured_value::numeric
            INTO v_value
            FROM h_data.physical_measurements pm
            WHERE pm.player_id = p_player_id
              AND pm.physical_test_id = v_goal.g_test
              AND pm.deleted_at IS NULL
              AND pm.measured_on >= v_goal.g_start
              AND pm.measured_on <= p_today
            ORDER BY pm.measured_on DESC, pm.physical_measurement_id DESC
            LIMIT 1;
            v_events := CASE WHEN FOUND THEN 1 ELSE 0 END;
            v_detail := 'Latest measurement since ' || to_char(v_goal.g_start, 'YYYY-MM-DD');

        ELSIF v_goal.g_type = 'habit' THEN
            SELECT COALESCE(array_agg(DISTINCT a.active_date), '{}'::date[])
            INTO v_active_dates
            FROM (
                SELECT g.game_date AS active_date
                FROM h_data.games g
                WHERE g.player_id = p_player_id AND g.deleted_at IS NULL
                UNION ALL
                SELECT ps.session_date
                FROM h_data.practice_players pp
                JOIN h_data.practice_sessions ps ON ps.practice_session_id = pp.practice_session_id
                WHERE pp.player_id = p_player_id AND pp.deleted_at IS NULL AND ps.deleted_at IS NULL
            ) a
            WHERE a.active_date >= v_goal.g_start
              AND a.active_date <= p_today;

            v_week_start := p_today - (EXTRACT(isodow FROM p_today)::integer - 1);
            SELECT count(*)::integer
            INTO v_events
            FROM unnest(v_active_dates) AS ad(d)
            WHERE ad.d BETWEEN v_week_start AND v_week_start + 6;
            v_value := v_events;
            v_detail := format('%s of %s days this week', v_events, trunc(v_goal.g_target)::integer);

            IF v_goal.g_deadline IS NULL OR p_today < v_goal.g_deadline THEN
                v_met := false;
            ELSE
                v_first_monday := v_goal.g_start + ((8 - EXTRACT(isodow FROM v_goal.g_start)::integer) % 7);
                v_last_sunday := v_goal.g_deadline - (EXTRACT(isodow FROM v_goal.g_deadline)::integer % 7);
                v_weeks := GREATEST((v_last_sunday - v_first_monday + 1) / 7, 0);
                IF v_weeks > 0 THEN
                    SELECT (count(*) FILTER (WHERE wk.days >= v_goal.g_target))::integer
                    INTO v_weeks_met
                    FROM (
                        SELECT (SELECT count(*) FROM unnest(v_active_dates) AS ad(d)
                                WHERE ad.d BETWEEN v_first_monday + 7 * w.i
                                               AND v_first_monday + 7 * w.i + 6) AS days
                        FROM generate_series(0, v_weeks - 1) AS w(i)
                    ) wk;
                    v_met := v_weeks_met = v_weeks;
                ELSE
                    SELECT count(*)::integer
                    INTO v_span_days
                    FROM unnest(v_active_dates) AS ad(d)
                    WHERE ad.d BETWEEN v_goal.g_start AND v_goal.g_deadline;
                    v_met := v_span_days >= v_goal.g_target;
                END IF;
            END IF;
        END IF;

        IF v_met IS NULL THEN
            v_met := v_judgeable
                     AND v_value IS NOT NULL
                     AND CASE WHEN v_goal.g_direction = 'at_most' THEN v_value <= v_goal.g_target
                              ELSE v_value >= v_goal.g_target END;
        END IF;

        goal_id := v_goal.g_id;
        goal_type := v_goal.g_type;
        progress_value := v_value;
        target_value := v_goal.g_target;
        target_direction := v_goal.g_direction;
        is_met := COALESCE(v_met, false);
        progress_ratio := CASE
            WHEN v_value IS NULL THEN 0
            WHEN v_goal.g_direction = 'at_most' THEN
                CASE WHEN v_value <= 0 THEN 1 ELSE LEAST(1, GREATEST(0, v_goal.g_target / v_value)) END
            ELSE
                CASE WHEN v_goal.g_target <= 0 THEN 1 ELSE LEAST(1, GREATEST(0, v_value / v_goal.g_target)) END
        END;
        sample_events := v_events;
        detail := v_detail;
        RETURN NEXT;
    END LOOP;
END
$$;
COMMENT ON FUNCTION h_analytics.fn_goal_progress(date, bigint, bigint) IS
    'REQ-033/041: progress and is_met of goals under the Gate 1 Q4 rules (a)-(d).';


-- =============================================================================
-- Grants (COD-1-06, plan section 6.13.3). All objects here are read-only.
-- =============================================================================

GRANT USAGE ON SCHEMA h_analytics TO hoopster_app, hoopster_agent_ro;

GRANT SELECT ON ALL TABLES IN SCHEMA h_analytics TO hoopster_app, hoopster_agent_ro;

-- Covers every function above, fn_game_heat_map_spots (2026-10-23) included.
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA h_analytics FROM PUBLIC;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA h_analytics TO hoopster_app, hoopster_agent_ro;

ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_analytics
    GRANT SELECT ON TABLES TO hoopster_app;
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_analytics
    GRANT SELECT ON TABLES TO hoopster_agent_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_analytics
    GRANT EXECUTE ON FUNCTIONS TO hoopster_app;
ALTER DEFAULT PRIVILEGES FOR ROLE hoopster_owner IN SCHEMA h_analytics
    GRANT EXECUTE ON FUNCTIONS TO hoopster_agent_ro;

COMMIT;

SELECT 'END OF SCRIPT';
