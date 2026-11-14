/**
 * Admin login session: a signed, expiring cookie. No database, no third-party service.
 * Uses Web Crypto only, so it runs in the proxy (edge or Node) and in route handlers alike.
 */

export const COOKIE = "gw_session";
export const SESSION_TTL_S = 12 * 60 * 60;

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

export async function signSession(secret: string, now = Date.now()): Promise<string> {
  const exp = Math.floor(now / 1000) + SESSION_TTL_S;
  return `${exp}.${await hmac(secret, `session:${exp}`)}`;
}

export async function verifySession(secret: string, token: string | undefined, now = Date.now()): Promise<boolean> {
  if (!token) return false;
  const [exp, mac, ...rest] = token.split(".");
  if (!exp || !mac || rest.length || !/^\d{1,12}$/.test(exp)) return false;
  if (Number(exp) * 1000 <= now) return false;
  return safeEqual(mac, await hmac(secret, `session:${exp}`));
}

export type AuthConfig =
  | { ok: true; password: string; secret: string }
  | { ok: false; reason: "unconfigured" };

/**
 * The dashboard shows prompts' metadata and costs, so it fails closed: in production it is
 * unreachable until both variables are set. Local development may run without a login.
 */
export function authConfig(env: Record<string, string | undefined> = process.env): AuthConfig | { ok: true; open: true } {
  const password = env.DASHBOARD_PASSWORD ?? "";
  const secret = env.DASHBOARD_SESSION_SECRET ?? "";
  if (password.length >= 12 && secret.length >= 32) return { ok: true, password, secret };
  if (env.NODE_ENV !== "production") return { ok: true, open: true };
  return { ok: false, reason: "unconfigured" };
}

/** Only same-site paths: stops "?next=//evil.example" open redirects after login. */
export function safeNext(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/";
  if (/[\r\n]/.test(next)) return "/";
  return next;
}
