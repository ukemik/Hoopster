-- sql/_database.sql
-- Creates the Hoopster database (COD-21-03). Run first, once per environment:
--   local PostgreSQL : connected to database "postgres" as the local superuser
--   Neon (main)      : connected to database "neondb" as the project owner role
-- CREATE DATABASE cannot run inside a transaction block: run this file without BEGIN/COMMIT.
-- Requires PostgreSQL 17 or later (builtin locale provider). On 16, delete the
-- LOCALE_PROVIDER and BUILTIN_LOCALE lines; nothing else changes.

CREATE DATABASE hoopster_db
    WITH TEMPLATE = template0
         ENCODING = 'UTF8'
         LOCALE_PROVIDER = builtin
         BUILTIN_LOCALE = 'C.UTF-8';

ALTER DATABASE hoopster_db SET timezone TO 'UTC';

COMMENT ON DATABASE hoopster_db IS
    'Hoopster: basketball development tracker. Built from sql/_database.sql, sql/_roles.sql, '
    'then h_system, h_ref, h_data, h_analytics, h_staging.';
