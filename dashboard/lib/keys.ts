/**
 * API keys created from the dashboard. The gateway generates and hashes the secret (see
 * gateway/app/dashboard_store.py), so the key format lives next to the code that verifies it. This file
 * only checks the shape of what the form sends, for fast feedback; the gateway validates again.
 */

export type KeyLimits = { maxPerUser: number; defaultRpm: number; maxRpm: number };

export type NewKeyInput = { name: string; expiresDays: number | null; models: string[]; rpm: number | null };

const MODEL_RE = /^[A-Za-z0-9_.:/-]{1,100}$/;

/** Validate untrusted JSON from the create-key form. Returns the cleaned input or an error message. */
export function parseNewKey(body: unknown): { ok: true; value: NewKeyInput } | { ok: false; error: string } {
  const b = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
  const name = typeof b.name === "string" ? b.name.trim() : "";
  if (!/^[A-Za-z0-9 _.\-]{1,60}$/.test(name)) return { ok: false, error: "Name: 1-60 letters, numbers, spaces, . _ -" };

  let expiresDays: number | null = null;
  if (b.expiresDays !== undefined && b.expiresDays !== null && b.expiresDays !== "") {
    const d = Number(b.expiresDays);
    if (!Number.isInteger(d) || d < 1 || d > 365) return { ok: false, error: "Expiry must be 1-365 days, or empty for none" };
    expiresDays = d;
  }

  const raw = typeof b.models === "string" ? b.models : Array.isArray(b.models) ? b.models.join(",") : "";
  const models = raw.split(/[,\s]+/).filter(Boolean);
  if (models.length > 20 || models.some((m) => !MODEL_RE.test(m))) return { ok: false, error: "Models: up to 20 model ids, e.g. groq/llama-3.1-8b-instant" };

  let rpm: number | null = null; // the gateway applies its default and enforces its ceiling
  if (b.rpm !== undefined && b.rpm !== null && b.rpm !== "") {
    rpm = Number(b.rpm);
    if (!Number.isInteger(rpm) || rpm < 1) return { ok: false, error: "Rate limit must be a whole number of requests per minute" };
  }
  return { ok: true, value: { name, expiresDays, models: [...new Set(models)], rpm } };
}
