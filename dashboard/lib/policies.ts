/** Routing policy versions, as the gateway stores them. Every function is a call to /dashboard/v1/policies. */
import { gw, gwOrStatus } from "./gateway.ts";

export type PolicyModel = {
  id: string;
  provider: string;
  model: string;
  family: string;
  tier: number;
  free?: boolean;
  capabilities?: { max_context?: number; tools?: boolean; json_mode?: boolean };
  price?: { input_per_1m?: number; output_per_1m?: number };
};

export type PolicyMeta = {
  version: number;
  status: string; // draft | shadow | canary | active | retired | rolled_back
  createdBy: string | null;
  source: string | null; // seed | file_sync | push_policy | api
  createdAt: string;
  activatedAt: string | null;
  parentVersion: number | null;
  modelCount: number | null;
  appVersion: string | null;
  release: string | null; // git commit of the deploy that published it
  gitBranch: string | null;
  notes: string | null;
  externalRefs: Record<string, string>;
  contentHash: string | null;
};

export type PolicyDetail = PolicyMeta & { body: { models?: PolicyModel[] } };

export const VERSION = /^[1-9][0-9]{0,8}$/;

export const REF_KEY = /^[A-Za-z0-9_.-]{1,40}$/;
export const MAX_REFS = 20;
export const MAX_REF_VALUE = 200;
export const MAX_NOTES = 1000;

export const shortRelease = (release: string | null) => (release ? release.slice(0, 7) : "–");

export type Outcome<T> = { ok: true; data: T } | { ok: false; status: number; message: string };

export const listPolicies = () => gw<PolicyMeta[]>("/policies");

export async function getPolicy(version: number): Promise<PolicyDetail | null> {
  const r = await gwOrStatus<PolicyDetail>(`/policies/${version}`, {}, [404]);
  return r.ok ? r.data : null;
}

export const updatePolicyMeta = (version: number, patch: { notes?: string | null; externalRefs?: Record<string, string> }) =>
  gwOrStatus<PolicyMeta>(`/policies/${version}`, { method: "PATCH", body: patch }, [404, 422]);

export const activatePolicy = (version: number) => gwOrStatus<PolicyMeta>(`/policies/${version}/activate`, { method: "POST" }, [404]);

export const deletePolicy = (version: number) => gwOrStatus<unknown>(`/policies/${version}`, { method: "DELETE" }, [404, 409]);

/** One `key=value` per line. Blank lines are ignored; the first `=` splits key from value. */
export function parseRefs(text: string): { ok: true; refs: Record<string, string> } | { ok: false; error: string } {
  const refs: Record<string, string> = {};
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    const eq = line.indexOf("=");
    if (eq < 1) return { ok: false, error: `Line ${i + 1}: write it as key=value.` };
    const key = line.slice(0, eq).trim();
    const value = line.slice(eq + 1).trim();
    if (!REF_KEY.test(key)) return { ok: false, error: `Line ${i + 1}: keys use letters, numbers, . _ - (up to 40 characters).` };
    if (value.length > MAX_REF_VALUE) return { ok: false, error: `Line ${i + 1}: values are at most ${MAX_REF_VALUE} characters.` };
    if (key in refs) return { ok: false, error: `Line ${i + 1}: "${key}" is listed twice.` };
    refs[key] = value;
    if (Object.keys(refs).length > MAX_REFS) return { ok: false, error: `At most ${MAX_REFS} references.` };
  }
  return { ok: true, refs };
}

export const formatRefs = (refs: Record<string, string>) =>
  Object.entries(refs)
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
