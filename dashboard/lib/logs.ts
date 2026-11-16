import type { LogFilter } from "./filters.ts";
import { gw } from "./gateway.ts";
import type { Scope } from "./scope.ts";

export type LogRow = {
  id: string;
  ts: string;
  source: string;
  kind: string;
  severity: string;
  message: string;
  tags: Record<string, unknown>;
  runId: string | null;
  spanId: string | null;
  fingerprint: string | null;
  payload: unknown;
};

export type Facet = { value: string; n: number };
export type Facets = {
  sources: Facet[];
  severities: Facet[];
  kinds: Facet[];
  tags: Record<string, Facet[]>;
};

/** Tag keys offered as quick filters (the gateway counts the same list). */
export const FACET_TAG_KEYS = ["model", "provider", "node_role", "task_type", "error_class", "key", "env"];

const query = (f: LogFilter, scope: Scope, extra: Record<string, unknown> = {}) => ({
  method: "POST" as const,
  body: { filter: f, user: scope.userId, ...extra },
});

export const searchLogs = (f: LogFilter, scope: Scope, limit = 100) => gw<LogRow[]>("/logs/search", query(f, scope, { limit }));

/** Matching rows, capped so the count stays cheap. */
export async function countLogs(f: LogFilter, scope: Scope): Promise<number> {
  return (await gw<{ count: number }>("/logs/count", query(f, scope))).count;
}

export const getFacets = (f: LogFilter, scope: Scope) => gw<Facets>("/logs/facets", query(f, scope));

// ---- one run, end to end ---------------------------------------------------------

export type RunSpan = {
  spanId: string;
  parentSpanId: string | null;
  origin: string;
  role: string | null;
  task: string | null;
  model: string | null;
  provider: string | null;
  status: string;
  errorClass: string | null;
  latencyMs: number | null;
  ttftMs: number | null;
  attempts: number;
  tokens: number;
  shadowCost: number;
  routeReason: string | null;
  policyVersion: number | null;
  endedAt: string;
  nodeTimings: Record<string, number>;
};
export type RunAttempt = {
  spanId: string;
  n: number;
  model: string | null;
  outcome: string;
  latencyMs: number | null;
  error: string | null;
  fallbackReason: string | null;
};

export const getRun = (runId: string, scope: Scope) =>
  gw<{ spans: RunSpan[]; attempts: RunAttempt[]; events: LogRow[] }>(`/runs/${encodeURIComponent(runId)}`, {
    query: { user: scope.userId },
  });
