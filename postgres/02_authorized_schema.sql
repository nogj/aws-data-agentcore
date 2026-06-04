-- Run with psql variables:
--   psql -v agent_schema=... -v agent_role=... \
--     -v authorized_relations='schema.view_one, schema.view_two' \
--     -f 02_authorized_schema.sql
--
-- Create database-specific security-barrier views in this schema separately,
-- then list only those views and columns in config/data-agent.yaml.

CREATE SCHEMA IF NOT EXISTS :"agent_schema";
REVOKE ALL ON SCHEMA :"agent_schema" FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA :"agent_schema" FROM PUBLIC;
GRANT USAGE ON SCHEMA :"agent_schema" TO :"agent_role";

ALTER DEFAULT PRIVILEGES IN SCHEMA :"agent_schema"
  REVOKE ALL ON TABLES FROM PUBLIC;

-- Grant access only to the concrete views listed in the versioned configuration.
GRANT SELECT ON TABLE :authorized_relations TO :"agent_role";
