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
