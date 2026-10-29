-- install/_roles.template.sql
-- Tracked placeholder template for sql/_roles.sql (rules_documentation.md DOC-12-03): it holds
-- no secret. To install: copy this file to sql/_roles.sql and replace the two placeholder
-- values on the ALTER ROLE lines with generated passwords (letters and digits recommended;
-- 20 or more characters, since Neon rejects weak passwords set through SQL). Never commit the
-- filled copy: sql/_roles.sql is excluded by .gitignore. Whenever sql/_roles.sql changes in
-- anything other than the two password values, this template changes with it (DOC-12-02).

-- sql/_roles.sql
-- Hoopster roles, passwords, memberships and role settings (COD-21-04).
-- EXCLUDED FROM GIT (.gitignore). Agents write placeholders only; the Overseer replaces
-- '<SET_PASSWORD>' values in a private copy and never commits it.
-- Run second (COD-21-05), connected to database "hoopster_db":
--   local PostgreSQL : as the local superuser
--   Neon (main)      : as the project owner role (e.g. neondb_owner)
-- Safe to re-run: creates missing roles, then (re)sets passwords and settings.

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'hoopster_owner') THEN
        CREATE ROLE hoopster_owner NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'hoopster_app') THEN
        CREATE ROLE hoopster_app LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'hoopster_agent_ro') THEN
        CREATE ROLE hoopster_agent_ro LOGIN;
    END IF;
END
$$;

ALTER ROLE hoopster_app      WITH PASSWORD '<SET_PASSWORD>';
ALTER ROLE hoopster_agent_ro WITH PASSWORD '<SET_PASSWORD>';

-- Membership: lets the person running the schema files SET ROLE hoopster_owner,
-- so every object is owned by hoopster_owner.
GRANT hoopster_owner TO CURRENT_USER;

-- Database-level access for the three roles. PUBLIC's default CONNECT and TEMPORARY are
-- removed first, so only the roles granted below can connect, and the read-only role cannot
-- create temporary tables (least privilege; Overseer decision on review_1, 2026-10-17).
REVOKE ALL ON DATABASE hoopster_db FROM PUBLIC;
GRANT CONNECT, CREATE, TEMPORARY ON DATABASE hoopster_db TO hoopster_owner;
GRANT CONNECT ON DATABASE hoopster_db TO hoopster_app, hoopster_agent_ro;

-- Role settings.
ALTER ROLE hoopster_app      SET statement_timeout = '30s';
ALTER ROLE hoopster_agent_ro SET statement_timeout = '30s';
ALTER ROLE hoopster_agent_ro SET default_transaction_read_only = on;

COMMIT;

SELECT 'END OF SCRIPT';
