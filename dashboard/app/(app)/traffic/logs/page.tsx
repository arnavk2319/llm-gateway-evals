import type { Metadata } from "next";
import Link from "next/link";
import { LoadError, PageHead } from "@/components/ui";
import { requireUser, scopeOf } from "@/lib/auth";
import { countLogs, FACET_TAG_KEYS, getFacets, searchLogs, type Facet } from "@/lib/logs";
import {
  SEVERITIES,
  WINDOWS,
  parseFilter,
  toQuery,
  withTag,
  withoutTag,
  type LogFilter,
  type SearchParams,
} from "@/lib/filters";
import { ago, int } from "@/lib/format";

export const metadata: Metadata = { title: "Logs" };
export const dynamic = "force-dynamic";
const PAGE_SIZE = 100;
const BASE = "/traffic/logs";

function sevPill(s: string) {
  return s === "error" || s === "critical" ? "bad" : s === "warn" ? "warn" : s === "info" ? "ok" : "mute";
}

function FacetList({ title, facets, href, active }: {
  title: string;
  facets: Facet[];
  href: (v: string) => string;
  active: (v: string) => boolean;
}) {
  if (!facets.length) return null;
  return (
    <div className="facet">
      <div className="facet-title">{title}</div>
      {facets.map((f) => (
        <Link key={f.value} href={href(f.value)} className={`chip ${active(f.value) ? "on" : ""}`}>
          {f.value} <span className="muted">{int(f.n)}</span>
        </Link>
      ))}
    </div>
  );
}

export default async function LogsPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const user = await requireUser();
  const scope = scopeOf(user);
  const f: LogFilter = parseFilter(await searchParams);
  let data;
  try {
    const [rows, total, facets] = await Promise.all([searchLogs(f, scope, PAGE_SIZE), countLogs(f, scope), getFacets(f, scope)]);
    data = { rows, total, facets };
  } catch (e) {
    return (
      <>
        <PageHead title="Logs" />
        <LoadError title="Can't read the log stream" error={e} />
      </>
    );
  }
  const { rows, total, facets } = data;
  const q = (c: Partial<LogFilter>) => toQuery(f, { page: 1, ...c });
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const exportQs = toQuery({ ...f, page: 1 });
  const pages = Math.ceil(total / PAGE_SIZE);

  return (
    <>
      <PageHead title="Logs" sub={`One stream for every source, tagged and time-stamped. ${total > 10000 ? "10,000+" : int(total)} matching events.`}>
        <nav className="segs" aria-label="Time range">
          {WINDOWS.map((w) => (
            <Link key={w.h} href={`${BASE}${q({ hours: w.h })}`} className={`seg ${w.h === f.hours ? "on" : ""}`}>
              {w.label}
            </Link>
          ))}
        </nav>
      </PageHead>

      <form method="get" className="filters">
        <input name="q" defaultValue={f.q} placeholder="Search message, tags, fingerprint" />
        <input name="run" defaultValue={f.run} placeholder="run id" />
        <input name="span" defaultValue={f.span} placeholder="call id" />
        <select name="severity" defaultValue={f.minSeverity ?? ""}>
          <option value="">any severity</option>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>
              {s} and above
            </option>
          ))}
        </select>
        {f.hours !== 24 && <input type="hidden" name="hours" value={f.hours} />}
        {f.sources.map((s) => (
          <input key={s} type="hidden" name="source" value={s} />
        ))}
        {f.kinds.map((k) => (
          <input key={k} type="hidden" name="kind" value={k} />
        ))}
        {f.tags.map((t) => (
          <input key={`${t.key}:${t.value}`} type="hidden" name="tag" value={`${t.key}:${t.value}`} />
        ))}
        <button type="submit">Search</button>
        <Link href={BASE} className="btn">
          Clear
        </Link>
      </form>

      {f.tags.length > 0 && (
        <div className="active-filters">
          {f.tags.map((t) => (
            <Link key={`${t.key}:${t.value}`} href={`${BASE}${toQuery(withoutTag(f, t.key, t.value))}`} className="chip on">
              {t.key}: {t.value} ×
            </Link>
          ))}
        </div>
      )}

      <div className="facets">
        <FacetList
          title="Source"
          facets={facets.sources}
          href={(v) => `${BASE}${q({ sources: toggle(f.sources, v) })}`}
          active={(v) => f.sources.includes(v)}
        />
        <FacetList
          title="Severity"
          facets={facets.severities}
          href={(v) => `${BASE}${q({ minSeverity: f.minSeverity === v ? null : (v as LogFilter["minSeverity"]) })}`}
          active={(v) => f.minSeverity === v}
        />
        <FacetList
          title="Kind"
          facets={facets.kinds}
          href={(v) => `${BASE}${q({ kinds: toggle(f.kinds, v) })}`}
          active={(v) => f.kinds.includes(v)}
        />
        {FACET_TAG_KEYS.map((k) => (
          <FacetList
            key={k}
            title={k}
            facets={facets.tags[k] ?? []}
            href={(v) => `${BASE}${toQuery(withTag(f, k, v))}`}
            active={(v) => f.tags.some((t) => t.key === k && t.value === v)}
          />
        ))}
      </div>

      <div className="exports">
        Export these results:
        <a href={`/api/export/logs${exportQs}${exportQs ? "&" : "?"}format=csv`}>CSV</a>
        <a href={`/api/export/logs${exportQs}${exportQs ? "&" : "?"}format=jsonl`}>JSONL</a>
        <a href={`/api/export/logs${exportQs}${exportQs ? "&" : "?"}format=json`}>JSON</a>
      </div>

      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Source</th>
              <th>Severity</th>
              <th>Kind</th>
              <th>Message</th>
              <th>Tags</th>
              <th>Run</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={7} className="muted">
                  No events match these filters
                </td>
              </tr>
            ) : (
              rows.map((r) => (
                <tr key={r.id}>
                  <td title={r.ts}>
                    {new Date(r.ts).toISOString().slice(5, 19).replace("T", " ")} <span className="muted">{ago(r.ts)}</span>
                  </td>
                  <td>
                    <span className="badge">{r.source}</span>
                  </td>
                  <td>
                    <span className={`pill ${sevPill(r.severity)}`}>{r.severity}</span>
                  </td>
                  <td>
                    <code>{r.kind}</code>
                  </td>
                  <td className="msg">
                    <details>
                      <summary>{r.message}</summary>
                      <pre>
                        {JSON.stringify(
                          { id: r.id, span_id: r.spanId, fingerprint: r.fingerprint, tags: r.tags, payload: r.payload },
                          null,
                          2,
                        )}
                      </pre>
                    </details>
                  </td>
                  <td className="tagcell">
                    {Object.entries(r.tags)
                      .filter(([k]) => FACET_TAG_KEYS.includes(k))
                      .map(([k, v]) => (
                        <Link key={k} href={`${BASE}${toQuery(withTag(f, k, String(v)))}`} className="chip small">
                          {k}:{String(v)}
                        </Link>
                      ))}
                  </td>
                  <td>
                    {r.runId ? (
                      <Link href={`/traffic/runs/${encodeURIComponent(r.runId)}`}>
                        <code>{r.runId.slice(0, 14)}</code>
                      </Link>
                    ) : (
                      "–"
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {pages > 1 && (
        <div className="pager">
          {f.page > 1 && <Link href={`${BASE}${toQuery(f, { page: f.page - 1 })}`}>← Newer</Link>}
          <span className="muted">
            Page {f.page} of {total > 10000 ? "100+" : pages}
          </span>
          {f.page < pages && <Link href={`${BASE}${toQuery(f, { page: f.page + 1 })}`}>Older →</Link>}
        </div>
      )}
    </>
  );
}
