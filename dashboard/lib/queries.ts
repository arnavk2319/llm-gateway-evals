import { gw } from "./gateway.ts";
import type { Scope } from "./scope.ts";

export type Kpis = {
  requests: number;
  errors: number;
  fallbacks: number;
  cost: number;
  shadow: number;
  p50: number | null;
  p95: number | null;
  promptTokens: number;
  completionTokens: number;
  overheadMs: number | null;
};

export type GroupRow = {
  key: string;
  requests: number;
  errorRate: number;
  fallbackRate: number;
  p50: number | null;
  p95: number | null;
  cost: number;
  shadow: number;
  tokens: number;
};

export type NodeRow = { node: string; avgMs: number; samples: number };
export type AttemptRow = { outcome: string; n: number };
export type InternalRow = { role: string; calls: number; cost: number; shadow: number; avgMs: number | null };
export type RecentRow = {
  at: string;
  role: string | null;
  task: string | null;
  model: string | null;
  status: string;
  attempts: number;
  latencyMs: number | null;
  ttftMs: number | null;
  tokens: number;
  shadow: number;
  runId: string;
  reason: string | null;
};
export type PolicyInfo = { version: number; createdAt: string } | null;

export type Dashboard = {
  hours: number;
  kpis: Kpis;
  byModel: GroupRow[];
  byRole: GroupRow[];
  byTask: GroupRow[];
  nodes: NodeRow[];
  attempts: AttemptRow[];
  internal: InternalRow[];
  recent: RecentRow[];
  policy: PolicyInfo;
};

/** The dashboard page's numbers for the window, computed by the gateway for this scope. */
export const getDashboard = (hours: number, scope: Scope) =>
  gw<Dashboard>("/analytics", { query: { hours, user: scope.userId } });
