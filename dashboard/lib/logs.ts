import { getSql } from "./db";
import { type LogFilter, severitiesAtLeast } from "./filters";

export type LogRow = {
  id: string;
  ts: string;
  source: string;
  kind: string;
  severity: string;
  message: string;
  tags: Record<string, unknown>;
  runId: string | null;
  spanId: string | null;
  fingerprint: string | null;
  payload: unknown;
};

export type Facet = { value: string; n: number };
export type Facets = {
  sources: Facet[];
  severities: Facet[];
  kinds: Facet[];
  tags: Record<string, Facet[]>;
};

/** Tag keys offered as quick filters. */
export const FACET_TAG_KEYS = ["model", "provider", "node_role", "task_type", "error_class", "key", "env"];

/** All filters become one WHERE clause over the `log_stream` view. */
function conditions(f: LogFilter) {
  const sql = getSql();
  let w = sql`ts > now() - make_interval(hours => ${f.hours})`;
  if (f.sources.length) w = sql`${w} AND source IN ${sql(f.sources)}`;
  if (f.minSeverity) w = sql`${w} AND severity IN ${sql(severitiesAtLeast(f.minSeverity))}`;
  if (f.kinds.length) w = sql`${w} AND kind IN ${sql(f.kinds)}`;
  if (f.q) {
    const like = `%${f.q.replace(/[\\%_]/g, (c) => `\\${c}`)}%`;
    w = sql`${w} AND (message ILIKE ${like} OR tags::text ILIKE ${like} OR fingerprint ILIKE ${like})`;
  }
  if (f.run) w = sql`${w} AND run_id = ${f.run}`;
  if (f.span) w = sql`${w} AND span_id = ${f.span}`;
  for (const t of f.tags) w = sql`${w} AND tags ->> ${t.key} = ${t.value}`;
  return w;
}

const toRow = (r: Record<string, any>): LogRow => ({
  id: r.id,
  ts: new Date(r.ts).toISOString(),
  source: r.source,
  kind: r.kind,
  severity: r.severity,
  message: r.message,
  tags: r.tags ?? {},
  runId: r.run_id,
  spanId: r.span_id,
  fingerprint: r.fingerprint,
  payload: r.payload,
});

export async function searchLogs(f: LogFilter, limit = 100): Promise<LogRow[]> {
  const sql = getSql();
  const rows = await sql`
    SELECT id, ts, source, kind, severity, message, tags, run_id, span_id, fingerprint, payload
    FROM log_stream WHERE ${conditions(f)}
    ORDER BY ts DESC LIMIT ${limit} OFFSET ${(f.page - 1) * limit}`;
  return rows.map(toRow);
}

/** Matching rows, capped so the count stays cheap. */
export async function countLogs(f: LogFilter, cap = 10000): Promise<number> {
  const sql = getSql();
  const [r] = await sql`
    SELECT count(*)::int AS n FROM (SELECT 1 FROM log_stream WHERE ${conditions(f)} LIMIT ${cap + 1}) t`;
  return Math.min(r.n, cap + 1);
}

export async function getFacets(f: LogFilter): Promise<Facets> {
  const sql = getSql();
  const w = conditions(f);
  const [sources, severities, kinds, tagRows] = await Promise.all([
    sql`SELECT source AS value, count(*)::int AS n FROM log_stream WHERE ${w} GROUP BY 1 ORDER BY n DESC`,
    sql`SELECT severity AS value, count(*)::int AS n FROM log_stream WHERE ${w} GROUP BY 1 ORDER BY n DESC`,
    sql`SELECT kind AS value, count(*)::int AS n FROM log_stream WHERE ${w} GROUP BY 1 ORDER BY n DESC LIMIT 12`,
    sql`
      SELECT t.key, t.value, count(*)::int AS n
      FROM (SELECT tags FROM log_stream WHERE ${w} ORDER BY ts DESC LIMIT 20000) s,
           jsonb_each_text(s.tags) AS t
      WHERE t.key IN ${sql(FACET_TAG_KEYS)}
      GROUP BY 1, 2 ORDER BY n DESC`,
  ]);
  const tags: Record<string, Facet[]> = {};
  for (const r of tagRows) {
    const bucket = (tags[r.key] ??= []);
    if (bucket.length < 8) bucket.push({ value: r.value, n: r.n });
  }
  const f2 = (rows: readonly Record<string, any>[]): Facet[] => rows.map((r) => ({ value: r.value, n: r.n }));
  return { sources: f2(sources), severities: f2(severities), kinds: f2(kinds), tags };
}

// ---- one run, end to end ---------------------------------------------------------

export type RunSpan = {
  spanId: string;
  parentSpanId: string | null;
  origin: string;
  role: string | null;
  task: string | null;
  model: string | null;
  provider: string | null;
  status: string;
  errorClass: string | null;
  latencyMs: number | null;
  ttftMs: number | null;
  attempts: number;
  tokens: number;
  shadowCost: number;
  routeReason: string | null;
  policyVersion: number | null;
  endedAt: string;
  nodeTimings: Record<string, number>;
};
export type RunAttempt = {
  spanId: string;
  n: number;
  model: string | null;
  outcome: string;
  latencyMs: number | null;
  error: string | null;
  fallbackReason: string | null;
};

export async function getRun(runId: string) {
  const sql = getSql();
  const [spans, attempts, events] = await Promise.all([
    sql`
      SELECT span_id, parent_span_id, origin, node_role, task_type, model, provider, status, error_class,
             latency_ms, ttft_ms, attempt_count, coalesce(prompt_tokens, 0) + coalesce(completion_tokens, 0) AS tokens,
             coalesce(shadow_cost_usd, 0)::float AS shadow, route_reason, policy_version, created_at, node_timings
      FROM spans WHERE run_id = ${runId} ORDER BY created_at`,
    sql`
      SELECT a.span_id, a.attempt_no, a.model, a.outcome_class, a.latency_ms, a.error, a.fallback_reason
      FROM attempts a JOIN spans s USING (span_id) WHERE s.run_id = ${runId}
      ORDER BY a.span_id, a.attempt_no`,
    sql`
      SELECT id, ts, source, kind, severity, message, tags, run_id, span_id, fingerprint, payload
      FROM log_stream WHERE run_id = ${runId} ORDER BY ts, id LIMIT 1000`,
  ]);
  return {
    spans: spans.map(
      (r): RunSpan => ({
        spanId: r.span_id,
        parentSpanId: r.parent_span_id,
        origin: r.origin,
        role: r.node_role,
        task: r.task_type,
        model: r.model,
        provider: r.provider,
        status: r.status,
        errorClass: r.error_class,
        latencyMs: r.latency_ms === null ? null : Number(r.latency_ms),
        ttftMs: r.ttft_ms === null ? null : Number(r.ttft_ms),
        attempts: r.attempt_count,
        tokens: Number(r.tokens),
        shadowCost: Number(r.shadow),
        routeReason: r.route_reason,
        policyVersion: r.policy_version,
        endedAt: new Date(r.created_at).toISOString(),
        nodeTimings: r.node_timings ?? {},
      }),
    ),
    attempts: attempts.map(
      (r): RunAttempt => ({
        spanId: r.span_id,
        n: r.attempt_no,
        model: r.model,
        outcome: r.outcome_class,
        latencyMs: r.latency_ms === null ? null : Number(r.latency_ms),
        error: r.error,
        fallbackReason: r.fallback_reason,
      }),
    ),
    events: events.map(toRow),
  };
}
