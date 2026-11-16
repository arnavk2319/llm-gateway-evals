import Link from "next/link";
import { requireUser, scopeOf } from "@/lib/auth";
import { getDashboard, type GroupRow } from "@/lib/queries";
import { ago, int, ms, pct, usd } from "@/lib/format";

export const dynamic = "force-dynamic";

const WINDOWS = [
  { h: 1, label: "1h" },
  { h: 24, label: "24h" },
  { h: 168, label: "7d" },
];

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="kpi">
      <div className="v">{value}</div>
      <div className="l">{label}</div>
      {sub ? <div className="s">{sub}</div> : null}
    </div>
  );
}

function GroupTable({ title, rows, keyLabel }: { title: string; rows: GroupRow[]; keyLabel: string }) {
  return (
    <section>
      <h2>{title}</h2>
      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>{keyLabel}</th>
              <th className="n">Requests</th>
              <th className="n">Errors</th>
              <th className="n">Fallbacks</th>
              <th className="n">p50</th>
              <th className="n">p95</th>
              <th className="n">Tokens</th>
              <th className="n">Cost</th>
              <th className="n">Shadow cost</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={9} className="muted">
                  No data in this window
                </td>
              </tr>
            ) : (
              rows.map((r) => (
                <tr key={r.key}>
                  <td>
                    <code>{r.key}</code>
                  </td>
                  <td className="n">{int(r.requests)}</td>
                  <td className={`n ${r.errorRate > 0 ? "bad" : ""}`}>{pct(r.errorRate)}</td>
                  <td className={`n ${r.fallbackRate > 0 ? "warn" : ""}`}>{pct(r.fallbackRate)}</td>
                  <td className="n">{ms(r.p50)}</td>
                  <td className="n">{ms(r.p95)}</td>
                  <td className="n">{int(r.tokens)}</td>
                  <td className="n">{usd(r.cost)}</td>
                  <td className="n">{usd(r.shadow)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default async function Page({ searchParams }: { searchParams: Promise<{ hours?: string }> }) {
  const user = await requireUser();
  const { hours } = await searchParams;
  const h = WINDOWS.some((w) => w.h === Number(hours)) ? Number(hours) : 24;

  let data;
  try {
    data = await getDashboard(h, scopeOf(user));
  } catch (e) {
    return (
      <main>
        <h1>LLM Gateway</h1>
        <h2>Can&apos;t reach the gateway</h2>
        <div className="empty">
          <p>
            Set <code>GATEWAY_URL</code> and <code>GATEWAY_DASHBOARD_TOKEN</code> (see <code>.env.example</code>) and make sure the gateway has applied the
            schema.
          </p>
          <p className="muted">{e instanceof Error ? e.message : String(e)}</p>
        </div>
      </main>
    );
  }

  const { kpis } = data;
  const errRate = kpis.requests ? kpis.errors / kpis.requests : 0;
  const fbRate = kpis.requests ? kpis.fallbacks / kpis.requests : 0;

  return (
    <main>
      <header className="top">
        <div>
          <h1>LLM Gateway</h1>
          <span className="muted">
            {data.policy
              ? `Active routing policy v${data.policy.version}, published ${ago(data.policy.createdAt)}`
              : "No active routing policy recorded yet"}
          </span>
        </div>
        <nav className="windows">
          {WINDOWS.map((w) => (
            <Link key={w.h} href={`/?hours=${w.h}`} className={w.h === h ? "on" : ""}>
              {w.label}
            </Link>
          ))}
        </nav>
      </header>

      {kpis.requests === 0 ? (
        <div className="empty">
          No external requests in the last {h}h. Send one through the gateway and refresh.
        </div>
      ) : null}

      <div className="kpis">
        <Kpi label="Requests" value={int(kpis.requests)} />
        <Kpi label="Error rate" value={pct(errRate)} sub={`${int(kpis.errors)} failed`} />
        <Kpi label="Fallback rate" value={pct(fbRate)} sub={`${int(kpis.fallbacks)} needed a retry`} />
        <Kpi label="Latency p50 / p95" value={`${ms(kpis.p50)} / ${ms(kpis.p95)}`} />
        <Kpi label="Billed cost" value={usd(kpis.cost)} sub={`shadow ${usd(kpis.shadow)}`} />
        <Kpi
          label="Tokens in / out"
          value={`${int(kpis.promptTokens)} / ${int(kpis.completionTokens)}`}
        />
        <Kpi label="Gateway overhead (avg)" value={ms(kpis.overheadMs)} sub="excludes model time" />
      </div>

      <GroupTable title="By model" rows={data.byModel} keyLabel="Model" />
      <GroupTable title="By node role" rows={data.byRole} keyLabel="Role" />
      <GroupTable title="By task type" rows={data.byTask} keyLabel="Task" />

      <div className="cols">
        <section>
          <h2>Attempt outcomes</h2>
          <div className="tablewrap">
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
                    <td colSpan={2} className="muted">
                      No data in this window
                    </td>
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
          </div>
        </section>

        <section>
          <h2>Per-node time (avg)</h2>
          <div className="tablewrap">
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
                    <td colSpan={3} className="muted">
                      No data in this window
                    </td>
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
          </div>
        </section>

        <section>
          <h2>Gateway&apos;s own LLM calls</h2>
          <div className="tablewrap">
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
                    <td colSpan={4} className="muted">
                      None (LLM classify is off or never triggered)
                    </td>
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
          </div>
        </section>
      </div>

      <section>
        <h2>Recent requests</h2>
        <div className="tablewrap">
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
                  <td colSpan={10} className="muted">
                    No requests yet
                  </td>
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
                    <td className={r.status === "ok" ? "ok" : "bad"}>{r.status}</td>
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
        </div>
      </section>
    </main>
  );
}
