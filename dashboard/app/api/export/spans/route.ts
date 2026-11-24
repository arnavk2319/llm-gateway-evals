import { NextRequest } from "next/server";
import { currentUser, noStore, scopeOf } from "@/lib/auth";
import { gw } from "@/lib/gateway";
import { WINDOWS } from "@/lib/filters";
import { exportResponse, parseFormat } from "@/lib/export";

export const dynamic = "force-dynamic";

const COLUMNS = [
  "created_at", "run_id", "span_id", "parent_span_id", "origin", "node_role", "task_type", "complexity",
  "provider", "model", "family", "route_reason", "policy_version", "status", "error_class", "http_status",
  "prompt_tokens", "completion_tokens", "cached_tokens", "cost_usd", "shadow_cost_usd", "latency_ms", "ttft_ms",
  "attempt_count", "fell_back", "overhead_ms", "api_key_name", "node_timings",
];

/** One row per gateway call: /api/export/spans?hours=168&format=csv */
export async function GET(req: NextRequest) {
  const user = await currentUser();
  if (!user) return Response.json({ error: "unauthorized" }, { status: 401, headers: noStore });
  const h = Number(req.nextUrl.searchParams.get("hours"));
  const hours = WINDOWS.some((w) => w.h === h) ? h : 24;
  try {
    const rows = await gw<Record<string, unknown>[]>("/exports/spans", { query: { hours, user: scopeOf(user).userId } });
    return exportResponse(
      "spans",
      parseFormat(req.nextUrl.searchParams.get("format")),
      COLUMNS,
      rows,
    );
  } catch (e) {
    return Response.json({ error: e instanceof Error ? e.message : "export failed" }, { status: 500 });
  }
}
