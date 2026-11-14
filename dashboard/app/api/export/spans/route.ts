import { NextRequest } from "next/server";
import { getSql } from "@/lib/db";
import { WINDOWS } from "@/lib/filters";
import { exportResponse, parseFormat } from "@/lib/export";

export const dynamic = "force-dynamic";

const MAX_ROWS = 50_000;
const COLUMNS = [
  "created_at", "run_id", "span_id", "parent_span_id", "origin", "node_role", "task_type", "complexity",
  "provider", "model", "family", "route_reason", "policy_version", "status", "error_class", "http_status",
  "prompt_tokens", "completion_tokens", "cached_tokens", "cost_usd", "shadow_cost_usd", "latency_ms", "ttft_ms",
  "attempt_count", "fell_back", "overhead_ms", "api_key_name", "node_timings",
];

/** One row per gateway call: /api/export/spans?hours=168&format=csv */
export async function GET(req: NextRequest) {
  const h = Number(req.nextUrl.searchParams.get("hours"));
  const hours = WINDOWS.some((w) => w.h === h) ? h : 24;
  try {
    const sql = getSql();
    const rows = await sql`
      SELECT created_at, run_id, span_id, parent_span_id, origin, node_role, task_type, complexity,
             provider, model, family, route_reason, policy_version, status, error_class, http_status,
             prompt_tokens, completion_tokens, cached_tokens, cost_usd::float AS cost_usd,
             shadow_cost_usd::float AS shadow_cost_usd, latency_ms, ttft_ms, attempt_count, fell_back,
             overhead_ms, api_key_name, node_timings
      FROM spans WHERE created_at > now() - make_interval(hours => ${hours})
      ORDER BY created_at DESC LIMIT ${MAX_ROWS}`;
    return exportResponse(
      "spans",
      parseFormat(req.nextUrl.searchParams.get("format")),
      COLUMNS,
      rows.map((r) => ({ ...r, created_at: new Date(r.created_at).toISOString() })),
    );
  } catch (e) {
    return Response.json({ error: e instanceof Error ? e.message : "export failed" }, { status: 500 });
  }
}
