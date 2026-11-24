import Link from "next/link";
import type { ReactNode } from "react";
import { WINDOWS } from "@/lib/filters";
import { int, ms, pct, usd } from "@/lib/format";
import type { GroupRow } from "@/lib/queries";

/** Title block with an optional right-hand area for ranges and actions. */
export function PageHead({ title, sub, children }: { title: string; sub?: ReactNode; children?: ReactNode }) {
  return (
    <header className="page-head">
      <div>
        <h1>{title}</h1>
        {sub ? <p>{sub}</p> : null}
      </div>
      {children ? <div className="actions">{children}</div> : null}
    </header>
  );
}

/** Time-range links. The current window is read from the URL, so it survives reloads and sharing. */
export function RangeTabs({ path, hours, extra = "" }: { path: string; hours: number; extra?: string }) {
  return (
    <nav className="segs" aria-label="Time range">
      {WINDOWS.map((w) => (
        <Link key={w.h} href={`${path}?hours=${w.h}${extra}`} className={`seg ${w.h === hours ? "on" : ""}`} aria-current={w.h === hours ? "true" : undefined}>
          {w.label}
        </Link>
      ))}
    </nav>
  );
}

export function Kpi({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "bad" }) {
  return (
    <div className="kpi">
      <div className="l">{label}</div>
      <div className={`v ${tone ?? ""}`}>{value}</div>
      {sub ? <div className="s">{sub}</div> : null}
    </div>
  );
}

/** Shown when the gateway can't be reached or a request fails. */
export function LoadError({ title, error }: { title: string; error: unknown }) {
  return (
    <div className="empty" role="alert">
      <p style={{ margin: "0 0 6px", color: "var(--ink)", fontWeight: 600 }}>{title}</p>
      <p style={{ margin: 0 }}>{error instanceof Error ? error.message : String(error)}</p>
    </div>
  );
}

export function ComingSoon({ title, blurb, points }: { title: string; blurb: string; points: string[] }) {
  return (
    <div className="coming">
      <PageHead title={title} sub={blurb} />
      <div className="card">
        <span className="pill mute">Coming soon</span>
        <ul>
          {points.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/** One breakdown (by model, role or task) with a share bar. */
export function GroupTable({ title, rows, keyLabel, note }: { title: string; rows: GroupRow[]; keyLabel: string; note?: ReactNode }) {
  const max = Math.max(1, ...rows.map((r) => r.requests));
  return (
    <section className="tablewrap">
      <div className="panel-title">
        <h2>{title}</h2>
        {note ? <span className="note">{note}</span> : null}
      </div>
      <table>
        <thead>
          <tr>
            <th>{keyLabel}</th>
            <th className="n">Requests</th>
            <th>Share</th>
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
              <td colSpan={10} className="muted">
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
                <td>
                  <span className="share">
                    <i style={{ width: `${Math.round((r.requests / max) * 100)}%` }} />
                  </span>
                </td>
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
    </section>
  );
}
