import type { Metadata } from "next";
import Link from "next/link";
import { LoadError, PageHead, RangeTabs } from "@/components/ui";
import { requireUser, scopeOf } from "@/lib/auth";
import { parseFilter } from "@/lib/filters";
import { ago, int } from "@/lib/format";
import { searchLogs, type LogRow } from "@/lib/logs";

export const metadata: Metadata = { title: "Runs and traces" };
export const dynamic = "force-dynamic";

type Run = { id: string; calls: number; failed: number; models: Set<string>; roles: Set<string>; last: string };

/** A run groups the calls made with one x-run-id, for example every step of one agent task. */
function groupRuns(rows: LogRow[]): Run[] {
  const runs = new Map<string, Run>();
  for (const r of rows) {
    if (!r.runId) continue;
    const run = runs.get(r.runId) ?? { id: r.runId, calls: 0, failed: 0, models: new Set<string>(), roles: new Set<string>(), last: r.ts };
    run.calls += 1;
    if (r.tags.status !== "ok") run.failed += 1;
    if (r.tags.model) run.models.add(String(r.tags.model));
    if (r.tags.node_role) run.roles.add(String(r.tags.node_role));
    if (r.ts > run.last) run.last = r.ts;
    runs.set(r.runId, run);
  }
  return [...runs.values()].sort((a, b) => (a.last < b.last ? 1 : -1));
}

export default async function RunsPage({ searchParams }: { searchParams: Promise<{ hours?: string }> }) {
  const user = await requireUser();
  const { hours } = await searchParams;
  const filter = parseFilter({ hours, kind: "request", tag: ["origin:external"] });

  let runs: Run[];
  try {
    runs = groupRuns(await searchLogs(filter, scopeOf(user), 500));
  } catch (e) {
    return (
      <>
        <PageHead title="Runs and traces" />
        <LoadError title="Can't read runs" error={e} />
      </>
    );
  }

  return (
    <>
      <PageHead title="Runs and traces" sub="Calls grouped by run id. Open one to see every call, attempt and event in order.">
        <RangeTabs path="/traffic/runs" hours={filter.hours} />
      </PageHead>
      {runs.length === 0 ? (
        <div className="empty">No runs in this window. Send the x-run-id header with your requests to group calls.</div>
      ) : (
        <section className="tablewrap">
          <table>
            <thead>
              <tr>
                <th>Run</th>
                <th className="n">Calls</th>
                <th className="n">Failed</th>
                <th>Roles</th>
                <th>Models</th>
                <th>Last call</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td>
                    <Link className="rowlink" href={`/traffic/runs/${encodeURIComponent(r.id)}`}>
                      {r.id}
                    </Link>
                  </td>
                  <td className="n">{int(r.calls)}</td>
                  <td className={`n ${r.failed ? "bad" : ""}`}>{int(r.failed)}</td>
                  <td>{[...r.roles].join(", ") || "–"}</td>
                  <td>
                    <code>{[...r.models].join(", ") || "–"}</code>
                  </td>
                  <td title={r.last}>{ago(r.last)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}
