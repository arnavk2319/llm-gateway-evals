/** A user's API keys, as the gateway stores them. Every function is a call to /dashboard/v1. */
import { gw, gwOrStatus } from "./gateway.ts";
import type { KeyLimits, NewKeyInput } from "./keys.ts";

export type KeyRow = {
  id: string;
  name: string;
  prefix: string;
  status: string;
  models: string[] | null;
  rpm: number | null;
  expiresAt: string | null;
  createdAt: string;
  lastUsedAt: string | null;
};

export type CreatedKey = { key: string; id: string; name: string; prefix: string; rpm: number; expiresAt: string | null };

const enc = encodeURIComponent;

/** What the gateway allows, so the form can show it. They live on the gateway, in one place. */
export const getKeyLimits = () => gw<KeyLimits>("/key-limits");

/** Creates a key owned by the user. The secret is returned once; the gateway stores only its hash. */
export async function createKey(
  userId: string,
  input: NewKeyInput,
): Promise<{ ok: true; key: CreatedKey } | { ok: false; status: 400 | 403 | 409; error: string }> {
  const r = await gwOrStatus<CreatedKey>(`/users/${enc(userId)}/keys`, { method: "POST", body: input }, [400, 403, 409]);
  return r.ok ? { ok: true, key: r.data } : { ok: false, status: r.status as 400 | 403 | 409, error: r.message };
}

export const listKeys = (userId: string) => gw<KeyRow[]>(`/users/${enc(userId)}/keys`);

/** Only the owner can revoke. The gateway stops accepting the key within its cache window (60s). */
export async function revokeKey(userId: string, keyId: string): Promise<boolean> {
  const r = await gwOrStatus<unknown>(`/users/${enc(userId)}/keys/${enc(keyId)}/revoke`, { method: "POST" }, [404]);
  return r.ok;
}
