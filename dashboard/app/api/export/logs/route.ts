import { NextRequest } from "next/server";
import { currentUser, noStore, scopeOf } from "@/lib/auth";
import { parseFilter } from "@/lib/filters";
import { searchLogs } from "@/lib/logs";
import { exportResponse, parseFormat } from "@/lib/export";

export const dynamic = "force-dynamic";

const MAX_ROWS = 50_000;
const COLUMNS = ["ts", "source", "kind", "severity", "message", "run_id", "span_id", "fingerprint", "tags", "payload", "id"];

/** Same filters as the Logs page, e.g. /api/export/logs?source=gateway&severity=error&hours=168&format=csv */
export async function GET(req: NextRequest) {
  const user = await currentUser();
  if (!user) return Response.json({ error: "unauthorized" }, { status: 401, headers: noStore });
  const sp = Object.fromEntries(
    [...new Set(req.nextUrl.searchParams.keys())].map((k) => {
      const all = req.nextUrl.searchParams.getAll(k);
      return [k, all.length > 1 ? all : all[0]];
    }),
  );
  const filter = { ...parseFilter(sp), page: 1 };
  try {
    const rows = await searchLogs(filter, scopeOf(user), MAX_ROWS);
    return exportResponse(
      "logs",
      parseFormat(req.nextUrl.searchParams.get("format")),
      COLUMNS,
      rows.map((r) => ({
        ts: r.ts,
        source: r.source,
        kind: r.kind,
        severity: r.severity,
        message: r.message,
        run_id: r.runId,
        span_id: r.spanId,
        fingerprint: r.fingerprint,
        tags: r.tags,
        payload: r.payload,
        id: r.id,
      })),
    );
  } catch (e) {
    return Response.json({ error: e instanceof Error ? e.message : "export failed" }, { status: 500 });
  }
}
