export const SEVERITIES = ["debug", "info", "warn", "error", "critical"] as const;
export type Severity = (typeof SEVERITIES)[number];

const ORDER: Record<Severity, number> = { debug: 0, info: 1, warn: 2, error: 3, critical: 4 };
export const severitiesAtLeast = (min: Severity): Severity[] => SEVERITIES.filter((s) => ORDER[s] >= ORDER[min]);

export const WINDOWS = [
  { h: 1, label: "1h" },
  { h: 6, label: "6h" },
  { h: 24, label: "24h" },
  { h: 168, label: "7d" },
  { h: 720, label: "30d" },
];

export type SearchParams = Record<string, string | string[] | undefined>;

export type LogFilter = {
  hours: number;
  sources: string[];
  minSeverity: Severity | null;
  kinds: string[];
  q: string;
  run: string;
  span: string;
  tags: { key: string; value: string }[];
  page: number;
};

const list = (v: string | string[] | undefined): string[] =>
  v === undefined ? [] : (Array.isArray(v) ? v : [v]).map((s) => s.trim()).filter(Boolean);

const first = (v: string | string[] | undefined): string => list(v)[0] ?? "";

export function parseFilter(sp: SearchParams): LogFilter {
  const hours = Number(first(sp.hours));
  const sev = first(sp.severity) as Severity;
  const page = Number(first(sp.page));
  return {
    hours: WINDOWS.some((w) => w.h === hours) ? hours : 24,
    sources: list(sp.source),
    minSeverity: SEVERITIES.includes(sev) ? sev : null,
    kinds: list(sp.kind),
    q: first(sp.q).slice(0, 200),
    run: first(sp.run).slice(0, 100),
    span: first(sp.span).slice(0, 100),
    tags: list(sp.tag)
      .map((t) => {
        const i = t.indexOf(":");
        return i > 0 ? { key: t.slice(0, i), value: t.slice(i + 1) } : null;
      })
      .filter((t): t is { key: string; value: string } => t !== null)
      .slice(0, 8),
    page: Number.isInteger(page) && page > 0 ? page : 1,
  };
}

/** Serialize a filter back to a query string, optionally changing parts of it. */
export function toQuery(f: LogFilter, change: Partial<LogFilter> = {}): string {
  const g = { ...f, ...change };
  const p = new URLSearchParams();
  if (g.hours !== 24) p.set("hours", String(g.hours));
  g.sources.forEach((s) => p.append("source", s));
  if (g.minSeverity) p.set("severity", g.minSeverity);
  g.kinds.forEach((k) => p.append("kind", k));
  if (g.q) p.set("q", g.q);
  if (g.run) p.set("run", g.run);
  if (g.span) p.set("span", g.span);
  g.tags.forEach((t) => p.append("tag", `${t.key}:${t.value}`));
  if (g.page > 1) p.set("page", String(g.page));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const withTag = (f: LogFilter, key: string, value: string): LogFilter => ({
  ...f,
  page: 1,
  tags: f.tags.some((t) => t.key === key && t.value === value) ? f.tags : [...f.tags, { key, value }],
});

export const withoutTag = (f: LogFilter, key: string, value: string): LogFilter => ({
  ...f,
  page: 1,
  tags: f.tags.filter((t) => !(t.key === key && t.value === value)),
});
