-- LLM Gateway schema (phase 1). Idempotent: safe to run on every startup.

CREATE TABLE IF NOT EXISTS runs (
  run_id        text PRIMARY KEY,
  api_key_name  text,
  origin        text NOT NULL DEFAULT 'external',
  started_at    timestamptz NOT NULL DEFAULT now(),
  last_span_at  timestamptz NOT NULL DEFAULT now(),
  final_status  text
);

CREATE TABLE IF NOT EXISTS spans (
  span_id             text PRIMARY KEY,
  run_id              text NOT NULL REFERENCES runs(run_id),
  parent_span_id      text,
  origin              text NOT NULL DEFAULT 'external',  -- external | internal
  node_role           text,
  task_type           text,
  complexity          text,
  profile             jsonb,
  profile_confidence  real,
  policy_version      integer,
  policy_status_served text,
  provider            text,
  model               text,
  family              text,
  route_reason        text,
  route_relaxed       text[],
  stream              boolean NOT NULL DEFAULT false,
  status              text NOT NULL,                      -- ok | error | stream_error
  error_class         text,
  http_status         integer,
  prompt_tokens       integer,
  completion_tokens   integer,
  cached_tokens       integer,
  cost_usd            numeric(14,8),
  shadow_cost_usd     numeric(14,8),
  latency_ms          integer,
  ttft_ms             integer,
  attempt_count       integer NOT NULL DEFAULT 1,
  fell_back           boolean NOT NULL DEFAULT false,
  node_timings        jsonb,
  overhead_ms         real,
  idempotency_key     text,
  api_key_name        text,
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS spans_created_at_idx ON spans (created_at DESC);
CREATE INDEX IF NOT EXISTS spans_run_idx        ON spans (run_id);
CREATE INDEX IF NOT EXISTS spans_model_idx      ON spans (model);
CREATE INDEX IF NOT EXISTS spans_role_idx       ON spans (node_role);

CREATE TABLE IF NOT EXISTS attempts (
  span_id         text NOT NULL REFERENCES spans(span_id) ON DELETE CASCADE,
  attempt_no      integer NOT NULL,
  model           text,
  provider        text,
  outcome_class   text NOT NULL,
  latency_ms      integer,
  ttft_ms         integer,
  error           text,
  fallback_reason text,
  PRIMARY KEY (span_id, attempt_no)
);

-- Written by the control plane (phase 2).
CREATE TABLE IF NOT EXISTS eval_scores (
  id          bigserial PRIMARY KEY,
  span_id     text NOT NULL REFERENCES spans(span_id) ON DELETE CASCADE,
  evaluator   text NOT NULL,
  score       real,
  verdict     text,
  rationale   text,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS eval_scores_span_idx ON eval_scores (span_id);

-- Versioned routing policy. Exactly one row may be 'active'.
CREATE TABLE IF NOT EXISTS routing_policy (
  version        serial PRIMARY KEY,
  status         text NOT NULL CHECK (status IN ('draft','shadow','canary','active','retired','rolled_back')),
  body           jsonb NOT NULL,
  parent_version integer,
  created_by     text,
  eval_summary   jsonb,
  created_at     timestamptz NOT NULL DEFAULT now(),
  activated_at   timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS routing_policy_one_active ON routing_policy ((status)) WHERE status = 'active';

-- ---------------------------------------------------------------------------
-- API keys (only a SHA-256 hash of each key is stored)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS api_keys (
  id                 text PRIMARY KEY,
  name               text NOT NULL,
  key_prefix         text NOT NULL,            -- first characters, for display only
  key_hash           text NOT NULL UNIQUE,
  status             text NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
  allowed_models     text[],                   -- NULL = every model
  rate_limit_per_min integer,                  -- NULL = gateway default
  expires_at         timestamptz,
  rotated_from       text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  last_used_at       timestamptz
);
CREATE INDEX IF NOT EXISTS api_keys_name_idx ON api_keys (name);

-- ---------------------------------------------------------------------------
-- Unified log stream: one normalized, tagged, timestamped record per event,
-- from any source (gateway, vercel, neon, sentry, hooks).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS log_events (
  id           bigserial PRIMARY KEY,
  ts           timestamptz NOT NULL DEFAULT now(),   -- when it happened at the source
  ingested_at  timestamptz NOT NULL DEFAULT now(),   -- when the gateway stored it
  source       text NOT NULL,                        -- gateway | vercel | neon | sentry | hook
  kind         text NOT NULL,                        -- attempt_failed | request_failed | runtime_log | deploy ...
  severity     text NOT NULL CHECK (severity IN ('debug','info','warn','error','critical')),
  message      text NOT NULL,
  tags         jsonb NOT NULL DEFAULT '{}'::jsonb,   -- the dimensions you filter on
  run_id       text,
  span_id      text,
  trace_id     text,
  fingerprint  text,                                 -- groups repeats of the same problem
  payload      jsonb,                                -- source-specific detail
  external_id  text                                  -- source's own id, makes ingestion idempotent
);
CREATE INDEX IF NOT EXISTS log_events_ts_idx          ON log_events (ts DESC);
CREATE INDEX IF NOT EXISTS log_events_source_ts_idx   ON log_events (source, ts DESC);
CREATE INDEX IF NOT EXISTS log_events_severity_ts_idx ON log_events (severity, ts DESC);
CREATE INDEX IF NOT EXISTS log_events_run_idx         ON log_events (run_id);
CREATE INDEX IF NOT EXISTS log_events_span_idx        ON log_events (span_id);
CREATE INDEX IF NOT EXISTS log_events_fingerprint_idx ON log_events (fingerprint);
CREATE INDEX IF NOT EXISTS log_events_tags_idx        ON log_events USING gin (tags jsonb_path_ops);
CREATE UNIQUE INDEX IF NOT EXISTS log_events_external_idx
  ON log_events (source, external_id) WHERE external_id IS NOT NULL;

-- The single place to read from: log events plus one 'request' row per span.
CREATE OR REPLACE VIEW log_stream AS
SELECT l.ts, l.source, l.kind, l.severity, l.message, l.tags, l.run_id, l.span_id,
       l.trace_id, l.fingerprint, l.payload, 'log:' || l.id::text AS id
FROM log_events l
UNION ALL
SELECT s.created_at, 'gateway', 'request',
       CASE WHEN s.status = 'ok' THEN 'info' ELSE 'error' END,
       coalesce(s.model, '-') || ' ' || s.status || coalesce(' (' || s.error_class || ')', ''),
       jsonb_strip_nulls(jsonb_build_object(
         'provider', s.provider, 'model', s.model, 'node_role', s.node_role,
         'task_type', s.task_type, 'origin', s.origin, 'status', s.status,
         'error_class', s.error_class, 'policy_version', s.policy_version,
         'key', s.api_key_name, 'fell_back', s.fell_back)),
       s.run_id, s.span_id, NULL::text, NULL::text,
       jsonb_build_object(
         'latency_ms', s.latency_ms, 'ttft_ms', s.ttft_ms,
         'tokens', coalesce(s.prompt_tokens, 0) + coalesce(s.completion_tokens, 0),
         'cost_usd', s.cost_usd, 'shadow_cost_usd', s.shadow_cost_usd,
         'attempts', s.attempt_count, 'route_reason', s.route_reason,
         'node_timings', s.node_timings),
       'span:' || s.span_id
FROM spans s;
