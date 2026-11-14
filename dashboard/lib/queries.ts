import { getSql } from "./db";

export type Kpis = {
  requests: number;
  errors: number;
  fallbacks: number;
  cost: number;
  shadow: number;
  p50: number | null;
  p95: number | null;
  promptTokens: number;
  completionTokens: number;
  overheadMs: number | null;
};

export type GroupRow = {
  key: string;
  requests: number;
  errorRate: number;
  fallbackRate: number;
  p50: number | null;
  p95: number | null;
  cost: number;
  shadow: number;
  tokens: number;
};

export type NodeRow = { node: string; avgMs: number; samples: number };
export type AttemptRow = { outcome: string; n: number };
export type InternalRow = { role: string; calls: number; cost: number; shadow: number; avgMs: number | null };
export type RecentRow = {
  at: string;
  role: string | null;
  task: string | null;
  model: string | null;
  status: string;
  attempts: number;
  latencyMs: number | null;
  ttftMs: number | null;
  tokens: number;
  shadow: number;
  runId: string;
  reason: string | null;
};
export type PolicyInfo = { version: number; createdAt: string } | null;

export type Dashboard = {
  hours: number;
  kpis: Kpis;
  byModel: GroupRow[];
  byRole: GroupRow[];
  byTask: GroupRow[];
  nodes: NodeRow[];
  attempts: AttemptRow[];
  internal: InternalRow[];
  recent: RecentRow[];
  policy: PolicyInfo;
};

const num = (v: unknown) => (v === null || v === undefined ? null : Number(v));

export async function getDashboard(hours: number): Promise<Dashboard> {
  const sql = getSql();
  const since = sql`now() - make_interval(hours => ${hours})`;

  const groupQuery = async (col: "model" | "node_role" | "task_type"): Promise<GroupRow[]> => {
    const rows = await sql`
      SELECT coalesce(${sql(col)}, '(none)') AS key,
             count(*)::int AS requests,
             (count(*) FILTER (WHERE status <> 'ok'))::float / count(*) AS error_rate,
             (count(*) FILTER (WHERE fell_back))::float / count(*) AS fallback_rate,
             percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms)::float AS p50,
             percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)::float AS p95,
             coalesce(sum(cost_usd), 0)::float AS cost,
             coalesce(sum(shadow_cost_usd), 0)::float AS shadow,
             coalesce(sum(coalesce(prompt_tokens, 0) + coalesce(completion_tokens, 0)), 0)::int AS tokens
      FROM spans
      WHERE origin = 'external' AND created_at > ${since}
      GROUP BY 1 ORDER BY requests DESC`;
    return rows.map((r) => ({
      key: r.key,
      requests: r.requests,
      errorRate: Number(r.error_rate),
      fallbackRate: Number(r.fallback_rate),
      p50: num(r.p50),
      p95: num(r.p95),
      cost: Number(r.cost),
      shadow: Number(r.shadow),
      tokens: r.tokens,
    }));
  };

  const [kpiRows, byModel, byRole, byTask, nodeRows, attemptRows, internalRows, recentRows, policyRows] =
    await Promise.all([
      sql`
        SELECT count(*)::int AS requests,
               (count(*) FILTER (WHERE status <> 'ok'))::int AS errors,
               (count(*) FILTER (WHERE fell_back))::int AS fallbacks,
               coalesce(sum(cost_usd), 0)::float AS cost,
               coalesce(sum(shadow_cost_usd), 0)::float AS shadow,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms)::float AS p50,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)::float AS p95,
               coalesce(sum(prompt_tokens), 0)::int AS prompt_tokens,
               coalesce(sum(completion_tokens), 0)::int AS completion_tokens,
               avg(overhead_ms)::float AS overhead_ms
        FROM spans WHERE origin = 'external' AND created_at > ${since}`,
      groupQuery("model"),
      groupQuery("node_role"),
      groupQuery("task_type"),
      sql`
        SELECT t.key AS node, avg(t.value::float)::float AS avg_ms, count(*)::int AS samples
        FROM spans s, jsonb_each_text(s.node_timings) AS t
        WHERE s.origin = 'external' AND s.created_at > ${since}
        GROUP BY t.key ORDER BY avg_ms DESC`,
      sql`
        SELECT a.outcome_class AS outcome, count(*)::int AS n
        FROM attempts a JOIN spans s USING (span_id)
        WHERE s.origin = 'external' AND s.created_at > ${since}
        GROUP BY 1 ORDER BY n DESC`,
      sql`
        SELECT node_role AS role, count(*)::int AS calls,
               coalesce(sum(cost_usd), 0)::float AS cost,
               coalesce(sum(shadow_cost_usd), 0)::float AS shadow,
               avg(latency_ms)::float AS avg_ms
        FROM spans WHERE origin = 'internal' AND created_at > ${since}
        GROUP BY 1 ORDER BY calls DESC`,
      sql`
        SELECT created_at, node_role, task_type, model, status, attempt_count, latency_ms, ttft_ms,
               coalesce(prompt_tokens, 0) + coalesce(completion_tokens, 0) AS tokens,
               coalesce(shadow_cost_usd, 0)::float AS shadow, run_id, route_reason
        FROM spans WHERE origin = 'external'
        ORDER BY created_at DESC LIMIT 25`,
      sql`SELECT version, created_at FROM routing_policy WHERE status = 'active' ORDER BY version DESC LIMIT 1`,
    ]);

  const k = kpiRows[0];
  return {
    hours,
    kpis: {
      requests: k.requests,
      errors: k.errors,
      fallbacks: k.fallbacks,
      cost: Number(k.cost),
      shadow: Number(k.shadow),
      p50: num(k.p50),
      p95: num(k.p95),
      promptTokens: k.prompt_tokens,
      completionTokens: k.completion_tokens,
      overheadMs: num(k.overhead_ms),
    },
    byModel,
    byRole,
    byTask,
    nodes: nodeRows.map((r) => ({ node: r.node, avgMs: Number(r.avg_ms), samples: r.samples })),
    attempts: attemptRows.map((r) => ({ outcome: r.outcome, n: r.n })),
    internal: internalRows.map((r) => ({
      role: r.role ?? "(none)",
      calls: r.calls,
      cost: Number(r.cost),
      shadow: Number(r.shadow),
      avgMs: num(r.avg_ms),
    })),
    recent: recentRows.map((r) => ({
      at: new Date(r.created_at).toISOString(),
      role: r.node_role,
      task: r.task_type,
      model: r.model,
      status: r.status,
      attempts: r.attempt_count,
      latencyMs: num(r.latency_ms),
      ttftMs: num(r.ttft_ms),
      tokens: Number(r.tokens),
      shadow: Number(r.shadow),
      runId: r.run_id,
      reason: r.route_reason,
    })),
    policy: policyRows[0]
      ? { version: policyRows[0].version, createdAt: new Date(policyRows[0].created_at).toISOString() }
      : null,
  };
}
