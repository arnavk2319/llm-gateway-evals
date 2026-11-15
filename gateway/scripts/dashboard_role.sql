-- A read-only database role for the dashboard, so a dashboard bug or leak can never write.
-- Run once against your database as the owner (Neon: SQL Editor, or psql with the owner URL),
-- after the gateway has started once (it creates the tables):
--
--   psql "$DATABASE_URL" -v pw="'choose-a-long-random-password'" -f scripts/dashboard_role.sql
--
-- Then use that role's connection string as DATABASE_URL_READONLY in the dashboard project.

CREATE ROLE dashboard_ro LOGIN PASSWORD :pw;
GRANT USAGE ON SCHEMA public TO dashboard_ro;
GRANT SELECT ON runs, spans, attempts, eval_scores, routing_policy, log_events, log_stream TO dashboard_ro;
-- api_keys is deliberately not granted: hashes never need to leave the gateway.
