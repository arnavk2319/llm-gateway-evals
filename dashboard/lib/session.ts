/**
 * Dashboard sessions: a signed, expiring cookie that carries the user id. Uses Web Crypto only,
 * so it runs in the proxy (edge or Node) and in route handlers alike. The cookie proves who the
 * user was when it was issued; pages and API routes still look the user up (see lib/auth.ts), so
 * disabling an account takes effect immediately.
 */

export const COOKIE = "gw_session";
export const SESSION_TTL_S = 7 * 24 * 60 * 60;

const enc = new TextEncoder();

async function hmac(secret: string, data: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(data)));
  return Array.from(sig, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Compare without leaking where the first difference is. */
export function safeEqual(a: string, b: string): boolean {
  const x = enc.encode(a);
  const y = enc.encode(b);
  let diff = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

const USER_ID_RE = /^usr_[a-f0-9]{12}$/;

export async function signSession(secret: string, userId: string, now = Date.now()): Promise<string> {
  const exp = Math.floor(now / 1000) + SESSION_TTL_S;
  return `${userId}.${exp}.${await hmac(secret, `session:${userId}:${exp}`)}`;
}

export type Session = { uid: string; issuedAt: number }; // issuedAt in seconds

/** Who the cookie was issued to and when, or null if it is missing, forged, malformed or expired. */
export async function verifySession(secret: string, token: string | undefined, now = Date.now()): Promise<Session | null> {
  if (!token) return null;
  const [uid, exp, mac, ...rest] = token.split(".");
  if (!uid || !exp || !mac || rest.length || !USER_ID_RE.test(uid) || !/^\d{1,12}$/.test(exp)) return null;
  if (Number(exp) * 1000 <= now) return null;
  if (!safeEqual(mac, await hmac(secret, `session:${uid}:${exp}`))) return null;
  return { uid, issuedAt: Number(exp) - SESSION_TTL_S };
}

export type AuthConfig = { ok: true; secret: string } | { ok: false; reason: "unconfigured" };

const DEV_SECRET = "development-only-secret-do-not-use-in-production";

/** Production fails closed until DASHBOARD_SESSION_SECRET (32+ characters) is set. */
export function authConfig(env: Record<string, string | undefined> = process.env): AuthConfig {
  const secret = env.DASHBOARD_SESSION_SECRET ?? "";
  if (secret.length >= 32) return { ok: true, secret };
  if (env.NODE_ENV !== "production") return { ok: true, secret: DEV_SECRET };
  return { ok: false, reason: "unconfigured" };
}

/** Only same-site paths: stops "?next=//evil.example" open redirects after login. */
export function safeNext(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/";
  if (/[\r\n]/.test(next)) return "/";
  return next;
}
