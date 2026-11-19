import type { Metadata } from "next";
import Link from "next/link";
import { GroupTable, Kpi, LoadError, PageHead, RangeTabs } from "@/components/ui";
import { RequestsChart } from "@/components/RequestsChart";
import { requireUser, scopeOf } from "@/lib/auth";
import { WINDOWS } from "@/lib/filters";
import { ago, int, ms, msPair, pct, usd } from "@/lib/format";
import { getDashboard } from "@/lib/queries";

export const metadata: Metadata = { title: "Overview" };
export const dynamic = "force-dynamic";

const rangeLabel = (h: number) => WINDOWS.find((w) => w.h === h)?.label ?? `${h}h`;
const ERROR_ALERT = 0.02;
const MIN_SAMPLE = 20;

export default async function OverviewPage({ searchParams }: { searchParams: Promise<{ hours?: string }> }) {
  const user = await requireUser();
  const { hours } = await searchParams;
  const h = WINDOWS.some((w) => w.h === Number(hours)) ? Number(hours) : 24;

  let data;
  try {
    data = await getDashboard(h, scopeOf(user));
  } catch (e) {
    return (
      <>
        <PageHead title="Overview" />
        <LoadError title="Can't reach the gateway" error={e} />
      </>
    );
  }

  const { kpis } = data;
  const errRate = kpis.requests ? kpis.errors / kpis.requests : 0;
  const fbRate = kpis.requests ? kpis.fallbacks / kpis.requests : 0;
  const saved = Math.max(0, kpis.shadow - kpis.cost);
  const worst = [...data.byModel].filter((m) => m.requests >= MIN_SAMPLE && m.errorRate >= ERROR_ALERT).sort((a, b) => b.errorRate - a.errorRate)[0];

  return (
    <>
      <PageHead
        title="Overview"
        sub={
          data.policy
            ? `Last ${rangeLabel(h)} · routing policy v${data.policy.version}, published ${ago(data.policy.createdAt)}`
            : `Last ${rangeLabel(h)} · no active routing policy recorded yet`
        }
      >
        <RangeTabs path="/overview" hours={h} />
        <a className="btn" href={`/api/export/spans?hours=${h}&format=csv`}>
          Export calls
        </a>
      </PageHead>

      {kpis.requests === 0 ? <div className="empty" style={{ marginBottom: 14 }}>No external requests in the last {rangeLabel(h)}. Send one through the gateway and refresh.</div> : null}

      <div className="kpis">
        <Kpi label="Requests" value={int(kpis.requests)} />
        <Kpi label="Error rate" value={pct(errRate)} sub={`${int(kpis.errors)} failed`} tone={errRate > 0 ? "bad" : undefined} />
        <Kpi label="Fallback rate" value={pct(fbRate)} sub={`${int(kpis.fallbacks)} needed a retry`} />
        <Kpi label="Latency p50 / p95" value={msPair(kpis.p50, kpis.p95)} sub={`Gateway overhead ${ms(kpis.overheadMs)}`} />
        <Kpi label="Billed cost" value={usd(kpis.cost)} sub={`Shadow ${usd(kpis.shadow)}`} />
        <Kpi label="Tokens in / out" value={`${int(kpis.promptTokens)} / ${int(kpis.completionTokens)}`} />
      </div>

      <div className="cols">
        <section className="card" style={{ flex: "2 1 460px", margin: 0 }}>
          <div className="panel-title">
            <h2>Requests</h2>
            <span className="note">per {data.bucket}, UTC</span>
          </div>
          <RequestsChart series={data.series} hours={h} bucket={data.bucket} />
        </section>

        <section className="dk attention" style={{ flex: "1 1 280px" }} aria-label="Needs attention">
          <h2>Needs attention</h2>
          <ul>
            {worst ? (
              <li>
                <span className="pill bad">{pct(worst.errorRate)} errors</span>
                <span>
                  <code>{worst.key}</code> is failing more than most. <Link href={`/traffic/requests?hours=${h}&outcome=failed`}>See failed requests</Link>
                </span>
              </li>
            ) : null}
            {kpis.fallbacks > 0 ? (
              <li>
                <span className="pill warn">{int(kpis.fallbacks)} retries</span>
                <span>
                  Calls that moved to another model. <Link href={`/traffic/requests?hours=${h}&outcome=fallbacks`}>See attempts</Link>
                </span>
              </li>
            ) : null}
            {!worst && kpis.fallbacks === 0 ? (
              <li>
                <span className="pill ok">Healthy</span>
                <span>No model is above {pct(ERROR_ALERT)} errors and nothing needed a fallback.</span>
              </li>
            ) : null}
          </ul>
          {saved > 0 ? (
            <div className="saved">
              Routing saved this period
              <div className="big">{usd(saved)}</div>
              {usd(kpis.cost)} billed against {usd(kpis.shadow)} on the premium model
            </div>
          ) : null}
        </section>
      </div>

      <section style={{ marginTop: 14 }}>
        <GroupTable title="By model" rows={data.byModel} keyLabel="Model" note={<Link href={`/traffic/requests?hours=${h}`}>View requests</Link>} />
      </section>
      <section style={{ marginTop: 14 }}>
        <GroupTable title="By node role" rows={data.byRole} keyLabel="Role" />
      </section>
      <section style={{ marginTop: 14 }}>
        <GroupTable title="By task type" rows={data.byTask} keyLabel="Task" />
      </section>

      <div className="cols">
        <section className="tablewrap" style={{ flex: "1 1 260px" }}>
          <div className="panel-title">
            <h2>Attempt outcomes</h2>
          </div>
          <table>
            <thead>
              <tr>
                <th>Outcome</th>
                <th className="n">Attempts</th>
              </tr>
            </thead>
            <tbody>
              {data.attempts.length === 0 ? (
                <tr>
                  <td colSpan={2} className="muted">No data in this window</td>
                </tr>
              ) : (
                data.attempts.map((a) => (
                  <tr key={a.outcome}>
                    <td className={a.outcome === "ok" ? "ok" : "bad"}>
                      <code>{a.outcome}</code>
                    </td>
                    <td className="n">{int(a.n)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </section>

        <section className="tablewrap" style={{ flex: "1 1 260px" }}>
          <div className="panel-title">
            <h2>Per-node time (avg)</h2>
          </div>
          <table>
            <thead>
              <tr>
                <th>Node</th>
                <th className="n">Avg</th>
                <th className="n">Samples</th>
              </tr>
            </thead>
            <tbody>
              {data.nodes.length === 0 ? (
                <tr>
                  <td colSpan={3} className="muted">No data in this window</td>
                </tr>
              ) : (
                data.nodes.map((n) => (
                  <tr key={n.node}>
                    <td>
                      <code>{n.node}</code>
                    </td>
                    <td className="n">{n.avgMs.toFixed(2)} ms</td>
                    <td className="n">{int(n.samples)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </section>

        <section className="tablewrap" style={{ flex: "1 1 260px" }}>
          <div className="panel-title">
            <h2>Gateway&apos;s own LLM calls</h2>
          </div>
          <table>
            <thead>
              <tr>
                <th>Role</th>
                <th className="n">Calls</th>
                <th className="n">Avg</th>
                <th className="n">Shadow cost</th>
              </tr>
            </thead>
            <tbody>
              {data.internal.length === 0 ? (
                <tr>
                  <td colSpan={4} className="muted">None (LLM classify is off or never triggered)</td>
                </tr>
              ) : (
                data.internal.map((r) => (
                  <tr key={r.role}>
                    <td>
                      <code>{r.role}</code>
                    </td>
                    <td className="n">{int(r.calls)}</td>
                    <td className="n">{ms(r.avgMs)}</td>
                    <td className="n">{usd(r.shadow)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </section>
      </div>

      <section className="tablewrap" style={{ marginTop: 14 }}>
        <div className="panel-title">
          <h2>Recent requests</h2>
          <Link href="/traffic/requests" className="note">
            Open all requests
          </Link>
        </div>
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>Role</th>
              <th>Task</th>
              <th>Model</th>
              <th>Status</th>
              <th className="n">Attempts</th>
              <th className="n">Latency</th>
              <th className="n">TTFT</th>
              <th className="n">Tokens</th>
              <th>Why this model</th>
            </tr>
          </thead>
          <tbody>
            {data.recent.length === 0 ? (
              <tr>
                <td colSpan={10} className="muted">No requests yet</td>
              </tr>
            ) : (
              data.recent.map((r, i) => (
                <tr key={`${r.runId}-${i}`}>
                  <td>{ago(r.at)}</td>
                  <td>{r.role ?? "–"}</td>
                  <td>{r.task ?? "–"}</td>
                  <td>
                    <code>{r.model ?? "–"}</code>
                  </td>
                  <td>
                    <span className={`pill ${r.status === "ok" ? "ok" : "bad"}`}>{r.status}</span>
                  </td>
                  <td className={`n ${r.attempts > 1 ? "warn" : ""}`}>{r.attempts}</td>
                  <td className="n">{ms(r.latencyMs)}</td>
                  <td className="n">{ms(r.ttftMs)}</td>
                  <td className="n">{int(r.tokens)}</td>
                  <td className="reason" title={r.reason ?? ""}>
                    {r.reason ?? "–"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </section>
    </>
  );
}
