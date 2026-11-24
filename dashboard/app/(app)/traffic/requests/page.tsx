import type { Metadata } from "next";
import Link from "next/link";
import { LoadError, PageHead, RangeTabs } from "@/components/ui";
import { requireUser, scopeOf } from "@/lib/auth";
import { parseFilter, type LogFilter } from "@/lib/filters";
import { ago, int, ms, usd } from "@/lib/format";
import { countLogs, getRun, searchLogs, type LogRow } from "@/lib/logs";

export const metadata: Metadata = { title: "Requests" };
export const dynamic = "force-dynamic";

const PAGE_SIZE = 50;
const OUTCOMES = [
  { key: "all", label: "All" },
  { key: "failed", label: "Failed" },
  { key: "fallbacks", label: "Fallbacks" },
] as const;
type Outcome = (typeof OUTCOMES)[number]["key"];

const STEP_COLORS = ["var(--bright)", "var(--sig)", "var(--ink)", "var(--patch)", "var(--stone)"];

type Params = Record<string, string | string[] | undefined>;
const first = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) ?? "";
const many = (v: string | string[] | undefined) => (v === undefined ? [] : Array.isArray(v) ? v : [v]);
const num = (v: unknown): number | null => (typeof v === "number" ? v : null);

/** The state of this page, kept entirely in the URL so a view can be bookmarked or shared. */
type View = { hours: number; outcome: Outcome; q: string; tags: string[]; page: number; sel: string };

function readView(sp: Params, filter: LogFilter): View {
  const outcome = first(sp.outcome) as Outcome;
  return {
    hours: filter.hours,
    outcome: OUTCOMES.some((o) => o.key === outcome) ? outcome : "all",
    q: filter.q,
    tags: many(sp.tag).filter((t) => /^(model|node_role):.+/.test(t)).slice(0, 4),
    page: filter.page,
    sel: first(sp.sel).slice(0, 100),
  };
}

function href(v: View, change: Partial<View> = {}) {
  const g = { ...v, ...change };
  const p = new URLSearchParams();
  if (g.hours !== 24) p.set("hours", String(g.hours));
  if (g.outcome !== "all") p.set("outcome", g.outcome);
  if (g.q) p.set("q", g.q);
  g.tags.forEach((t) => p.append("tag", t));
  if (g.page > 1) p.set("page", String(g.page));
  if (g.sel) p.set("sel", g.sel);
  const s = p.toString();
  return `/traffic/requests${s ? `?${s}` : ""}`;
}

function outcomeOf(r: LogRow): { cls: "ok" | "warn" | "bad"; text: string } {
  if (r.tags.status !== "ok") return { cls: "bad", text: "failed" };
  if (r.tags.fell_back === true) return { cls: "warn", text: "ok after fallback" };
  return { cls: "ok", text: "ok" };
}

export default async function RequestsPage({ searchParams }: { searchParams: Promise<Params> }) {
  const user = await requireUser();
  const scope = scopeOf(user);
  const sp = await searchParams;

  // Requests are the "request" rows of the log stream, minus the gateway's own internal calls.
  const base = parseFilter({ hours: first(sp.hours), q: first(sp.q), page: first(sp.page) });
  const view = readView(sp, base);
  const tags = ["origin:external", ...view.tags, ...(view.outcome === "fallbacks" ? ["fell_back:true"] : [])];
  const filter = parseFilter({
    hours: String(view.hours),
    q: view.q,
    page: String(view.page),
    kind: "request",
    tag: tags,
    severity: view.outcome === "failed" ? "error" : undefined,
  });

  let rows: LogRow[];
  let total: number;
  try {
    [rows, total] = await Promise.all([searchLogs(filter, scope, PAGE_SIZE), countLogs(filter, scope)]);
  } catch (e) {
    return (
      <>
        <PageHead title="Requests" />
        <LoadError title="Can't read requests" error={e} />
      </>
    );
  }

  const selected = rows.find((r) => r.spanId === view.sel) ?? rows[0];
  let attempts: Awaited<ReturnType<typeof getRun>>["attempts"] = [];
  if (selected?.runId) {
    try {
      attempts = (await getRun(selected.runId, scope)).attempts.filter((a) => a.spanId === selected.spanId);
    } catch {
      /* the drawer still shows everything the row carries */
    }
  }
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const exportQs = new URLSearchParams({ format: "csv", hours: String(view.hours), kind: "request" });
  tags.forEach((t) => exportQs.append("tag", t));

  return (
    <>
      <PageHead title="Requests" sub={`${total > 10000 ? "10,000+" : int(total)} calls match. Select a row to see its trace.`}>
        <RangeTabs path="/traffic/requests" hours={view.hours} extra={view.outcome !== "all" ? `&outcome=${view.outcome}` : ""} />
        <a className="btn" href={`/api/export/logs?${exportQs.toString()}`}>
          Export CSV
        </a>
      </PageHead>

      <div className="filters" style={{ alignItems: "center" }}>
        <nav className="segs" aria-label="Outcome">
          {OUTCOMES.map((o) => (
            <Link key={o.key} href={href(view, { outcome: o.key, page: 1, sel: "" })} className={`seg ${view.outcome === o.key ? "on" : ""}`}>
              {o.label}
            </Link>
          ))}
        </nav>
        <form method="get" style={{ display: "flex", gap: 8, flex: "1 1 260px" }}>
          {view.hours !== 24 && <input type="hidden" name="hours" value={view.hours} />}
          {view.outcome !== "all" && <input type="hidden" name="outcome" value={view.outcome} />}
          {view.tags.map((t) => (
            <input key={t} type="hidden" name="tag" value={t} />
          ))}
          <input name="q" defaultValue={view.q} placeholder="Search model, role, run, error class" aria-label="Search requests" style={{ flex: 1 }} />
          <button type="submit" className="btn">
            Search
          </button>
        </form>
        {view.tags.map((t) => (
          <Link key={t} href={href(view, { tags: view.tags.filter((x) => x !== t), page: 1, sel: "" })} className="chip on">
            {t.replace(":", ": ")} ×
          </Link>
        ))}
      </div>

      {rows.length === 0 ? (
        <div className="empty">No requests match these filters in the last {view.hours} hours.</div>
      ) : (
        <div className="work">
          <section className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Run</th>
                  <th>Role</th>
                  <th>Model</th>
                  <th>Outcome</th>
                  <th className="n">Tries</th>
                  <th className="n">Latency</th>
                  <th className="n">Tokens</th>
                  <th className="n">Cost</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const o = outcomeOf(r);
                  const p = r.payload as Record<string, unknown> | null;
                  const isSel = r.spanId === selected?.spanId;
                  return (
                    <tr key={r.id} className={isSel ? "sel" : ""}>
                      <td>
                        <Link className="rowlink" href={href(view, { sel: r.spanId ?? "" })} aria-current={isSel ? "true" : undefined} title={r.ts}>
                          {new Date(r.ts).toISOString().slice(11, 19)}
                        </Link>
                      </td>
                      <td>{r.runId ? <Link href={`/traffic/runs/${encodeURIComponent(r.runId)}`}><code>{r.runId.slice(0, 16)}</code></Link> : "–"}</td>
                      <td>{String(r.tags.node_role ?? "–")}</td>
                      <td>
                        <code>{String(r.tags.model ?? "–")}</code>
                      </td>
                      <td>
                        <span className={`pill ${o.cls}`}>{o.text}</span>
                      </td>
                      <td className="n">{p?.attempts !== undefined ? int(Number(p.attempts)) : "–"}</td>
                      <td className="n">{ms(num(p?.latency_ms))}</td>
                      <td className="n">{p?.tokens !== undefined ? int(Number(p.tokens)) : "–"}</td>
                      <td className="n">{num(p?.cost_usd) === null ? "–" : usd(Number(p?.cost_usd))}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {pages > 1 ? (
              <div className="pager" style={{ margin: 0, padding: "10px 14px", borderTop: "1px solid var(--line)" }}>
                {view.page > 1 ? <Link href={href(view, { page: view.page - 1, sel: "" })}>← Newer</Link> : null}
                <span className="muted">
                  Page {view.page} of {total > 10000 ? "200+" : pages}
                </span>
                {view.page < pages ? <Link href={href(view, { page: view.page + 1, sel: "" })}>Older →</Link> : null}
              </div>
            ) : null}
          </section>

          {selected ? <Drawer row={selected} attempts={attempts} /> : null}
        </div>
      )}
      <p className="muted" style={{ fontSize: 12.5 }}>
        Last updated {rows[0] ? ago(rows[0].ts) : "n/a"}. Times are UTC.
      </p>
    </>
  );
}

function Drawer({ row, attempts }: { row: LogRow; attempts: Awaited<ReturnType<typeof getRun>>["attempts"] }) {
  const o = outcomeOf(row);
  const p = (row.payload ?? {}) as Record<string, unknown>;
  const timings = Object.entries((p.node_timings ?? {}) as Record<string, number>).filter(([, v]) => typeof v === "number");
  const total = timings.reduce((a, [, v]) => a + v, 0) || 1;
  const tokens = num(p.tokens);

  return (
    <aside className="drawer" aria-label="Request details">
      <header>
        <div style={{ minWidth: 0 }}>
          <div className="mono" style={{ fontWeight: 500, overflowWrap: "anywhere" }}>
            {row.runId ?? row.spanId}
          </div>
          <div className="muted" style={{ fontSize: 12 }}>
            {new Date(row.ts).toISOString().slice(0, 19).replace("T", " ")} UTC{row.tags.key ? ` · key ${String(row.tags.key)}` : ""}
          </div>
        </div>
        <span className={`pill ${o.cls}`}>{o.text}</span>
      </header>
      <div className="body">
        <div className="kv">
          <div>
            <div className="k">Model</div>
            <div className="v mono">{String(row.tags.model ?? "–")}</div>
          </div>
          <div>
            <div className="k">Role and task</div>
            <div className="v">
              {String(row.tags.node_role ?? "–")} · {String(row.tags.task_type ?? "–")}
            </div>
          </div>
          <div>
            <div className="k">Latency</div>
            <div className="v">
              {ms(num(p.latency_ms))}
              {num(p.ttft_ms) !== null ? <span className="muted"> · first token {ms(num(p.ttft_ms))}</span> : null}
            </div>
          </div>
          <div>
            <div className="k">Tokens</div>
            <div className="v">{tokens === null ? "–" : int(tokens)}</div>
          </div>
          <div>
            <div className="k">Cost</div>
            <div className="v">{num(p.cost_usd) === null ? "–" : usd(Number(p.cost_usd))}</div>
          </div>
          <div>
            <div className="k">Shadow cost</div>
            <div className="v">{num(p.shadow_cost_usd) === null ? "–" : usd(Number(p.shadow_cost_usd))}</div>
          </div>
        </div>

        {row.tags.error_class ? (
          <div>
            <p className="lab">Error</p>
            <div className="reasonbox">{String(row.tags.error_class)}</div>
          </div>
        ) : null}

        <div>
          <p className="lab">Why this route</p>
          <div className="reasonbox">{typeof p.route_reason === "string" && p.route_reason ? p.route_reason : "No reason recorded."}</div>
        </div>

        {attempts.length > 0 ? (
          <div>
            <p className="lab">Attempts</p>
            <ol className="attemptlist">
              {attempts.map((a) => (
                <li key={a.n}>
                  <span className="mono muted">{a.n}</span>
                  <span className="mono" style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
                    {a.model ?? "–"}
                  </span>
                  <span className={`pill ${a.outcome === "ok" ? "ok" : "bad"}`}>{a.outcome}</span>
                  <span className="muted" style={{ minWidth: 48, textAlign: "right" }}>
                    {ms(a.latencyMs)}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        ) : null}

        {timings.length > 0 ? (
          <div>
            <p className="lab">Where the time went</p>
            <div className="timeline">
              {timings.map(([name, v], i) => (
                <span key={name} title={`${name} ${v.toFixed(1)} ms`} style={{ width: `${Math.max(1.5, (v / total) * 100)}%`, background: STEP_COLORS[i % STEP_COLORS.length] }} />
              ))}
            </div>
            <div className="timeline-key">
              {timings.map(([name, v], i) => (
                <span key={name}>
                  <i style={{ background: STEP_COLORS[i % STEP_COLORS.length] }} />
                  {name} {v.toFixed(v < 10 ? 1 : 0)} ms
                </span>
              ))}
            </div>
          </div>
        ) : null}

        {row.runId ? (
          <div>
            <Link className="btn pri" href={`/traffic/runs/${encodeURIComponent(row.runId)}`}>
              Open full run
            </Link>
          </div>
        ) : null}
      </div>
    </aside>
  );
}
