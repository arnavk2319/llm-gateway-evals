import type { Metadata } from "next";
import Link from "next/link";
import { Kpi, LoadError, PageHead } from "@/components/ui";
import { requireUser, scopeOf } from "@/lib/auth";
import { getRun, type RunSpan } from "@/lib/logs";
import { int, ms, usd } from "@/lib/format";

export const metadata: Metadata = { title: "Run" };
export const dynamic = "force-dynamic";

function depth(span: RunSpan, byId: Map<string, RunSpan>): number {
  let d = 0;
  let cur: RunSpan | undefined = span;
  while (cur?.parentSpanId && byId.has(cur.parentSpanId) && d < 6) {
    cur = byId.get(cur.parentSpanId);
    d++;
  }
  return d;
}

const sevPill = (s: string) => (s === "error" || s === "critical" ? "bad" : s === "warn" ? "warn" : s === "info" ? "ok" : "mute");

export default async function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const user = await requireUser();
  const { runId: raw } = await params;
  const runId = decodeURIComponent(raw);

  let run;
  try {
    run = await getRun(runId, scopeOf(user));
  } catch (e) {
    return (
      <>
        <PageHead title="Run" />
        <LoadError title="Can't read this run" error={e} />
      </>
    );
  }
  const { spans, attempts, events } = run;

  if (spans.length === 0 && events.length === 0) {
    return (
      <>
        <PageHead title={`Run ${runId}`} />
        <div className="empty">Nothing recorded for this run id.</div>
        <p>
          <Link href="/traffic/runs">← Back to runs</Link>
        </p>
      </>
    );
  }

  const byId = new Map(spans.map((s) => [s.spanId, s]));
  const starts = spans.map((s) => new Date(s.endedAt).getTime() - (s.latencyMs ?? 0));
  const ends = spans.map((s) => new Date(s.endedAt).getTime());
  const t0 = starts.length ? Math.min(...starts) : 0;
  const total = Math.max(1, (ends.length ? Math.max(...ends) : 0) - t0);

  const external = spans.filter((s) => s.origin === "external");
  const errors = spans.filter((s) => s.status !== "ok").length;
  const cost = spans.reduce((a, s) => a + s.shadowCost, 0);
  const tokens = spans.reduce((a, s) => a + s.tokens, 0);

  return (
    <>
      <PageHead title={`Run ${runId}`} sub="Every call and every event from every source for this run, in one place.">
        <Link className="btn" href={`/traffic/logs?run=${encodeURIComponent(runId)}&hours=720`}>
          Filter logs by this run
        </Link>
      </PageHead>

      <div className="kpis">
        <Kpi label="Calls" value={external.length} sub={`${spans.length - external.length} internal`} />
        <Kpi label="Failed calls" value={errors} tone={errors ? "bad" : undefined} />
        <Kpi label="Wall time" value={ms(total)} />
        <Kpi label="Tokens" value={int(tokens)} />
        <Kpi label="Shadow cost" value={usd(cost)} />
        <Kpi label="Events" value={events.length} />
      </div>

      <section className="tablewrap">
        <div className="panel-title">
          <h2>Calls</h2>
        </div>
        <table>
          <thead>
            <tr>
              <th>Call</th>
              <th>Role</th>
              <th>Model</th>
              <th>Status</th>
              <th className="n">Latency</th>
              <th className="wf">Timeline</th>
            </tr>
          </thead>
          <tbody>
            {spans.map((s, i) => {
              const start = starts[i];
              const left = ((start - t0) / total) * 100;
              const width = Math.max(0.8, ((s.latencyMs ?? 0) / total) * 100);
              const myAttempts = attempts.filter((a) => a.spanId === s.spanId);
              return (
                <tr key={s.spanId} style={{ height: "auto" }}>
                  <td style={{ paddingLeft: 14 + depth(s, byId) * 16, whiteSpace: "normal", paddingTop: 8, paddingBottom: 8 }}>
                    <Link href={`/traffic/logs?span=${s.spanId}&hours=720`}>
                      <code>{s.spanId.slice(0, 13)}</code>
                    </Link>
                    {s.origin === "internal" && <span className="badge"> internal</span>}
                    <details>
                      <summary className="muted">
                        {s.attempts} attempt{s.attempts > 1 ? "s" : ""}, policy v{s.policyVersion ?? "?"}
                      </summary>
                      <div className="muted small-text">{s.routeReason}</div>
                      <ul className="attempts">
                        {myAttempts.map((a) => (
                          <li key={a.n} className={a.outcome === "ok" ? "ok" : "bad"}>
                            #{a.n} <code>{a.model}</code> {a.outcome} {ms(a.latencyMs)}
                            {a.error ? <span className="muted"> — {a.error}</span> : null}
                          </li>
                        ))}
                      </ul>
                      <div className="muted small-text">
                        {Object.entries(s.nodeTimings)
                          .map(([k, v]) => `${k} ${Number(v).toFixed(2)}ms`)
                          .join("  ·  ")}
                      </div>
                    </details>
                  </td>
                  <td>{s.role ?? "–"}</td>
                  <td>
                    <code>{s.model ?? "–"}</code>
                  </td>
                  <td>
                    <span className={`pill ${s.status === "ok" ? "ok" : "bad"}`}>
                      {s.status}
                      {s.errorClass ? ` (${s.errorClass})` : ""}
                    </span>
                  </td>
                  <td className="n">{ms(s.latencyMs)}</td>
                  <td className="wf">
                    <div className="track">
                      <div className={`tbar ${s.status === "ok" ? "okbar" : "badbar"}`} style={{ left: `${left}%`, width: `${width}%` }} title={`${ms(s.latencyMs)}`} />
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      <section className="tablewrap" style={{ marginTop: 14 }}>
        <div className="panel-title">
          <h2>Event timeline (all sources)</h2>
        </div>
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Source</th>
              <th>Severity</th>
              <th>Kind</th>
              <th>Message</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <tr key={e.id}>
                <td title={e.ts}>
                  +{ms(Math.max(0, new Date(e.ts).getTime() - t0))} <span className="muted">{new Date(e.ts).toISOString().slice(11, 23)}</span>
                </td>
                <td>
                  <span className="badge">{e.source}</span>
                </td>
                <td>
                  <span className={`pill ${sevPill(e.severity)}`}>{e.severity}</span>
                </td>
                <td>
                  <code>{e.kind}</code>
                </td>
                <td className="msg">
                  <details>
                    <summary>{e.message}</summary>
                    <pre>{JSON.stringify({ span_id: e.spanId, tags: e.tags, payload: e.payload }, null, 2)}</pre>
                  </details>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
