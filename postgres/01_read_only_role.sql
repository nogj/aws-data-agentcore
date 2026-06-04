-- Run with psql variables:
--   psql -v database_name=... -v agent_role=... -f 01_read_only_role.sql
-- Set the role password through your approved secret-management process.

CREATE ROLE :"agent_role"
  LOGIN
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOINHERIT
  NOREPLICATION
  CONNECTION LIMIT 10;

ALTER ROLE :"agent_role" SET default_transaction_read_only = on;
ALTER ROLE :"agent_role" SET statement_timeout = '30s';
ALTER ROLE :"agent_role" SET lock_timeout = '5s';
ALTER ROLE :"agent_role" SET idle_in_transaction_session_timeout = '30s';

REVOKE ALL ON DATABASE :"database_name" FROM :"agent_role";
GRANT CONNECT ON DATABASE :"database_name" TO :"agent_role";

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM :"agent_role";
