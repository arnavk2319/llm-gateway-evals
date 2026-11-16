import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { authConfig, COOKIE, SESSION_TTL_S, signSession, verifySession } from "./session.ts";
import type { Scope } from "./scope.ts";
import { findActiveById, type User } from "./users.ts";

/** The signed-in, still-active user for this request, or null. */
export async function currentUser(): Promise<User | null> {
  const cfg = authConfig();
  if (!cfg.ok) return null;
  const session = await verifySession(cfg.secret, (await cookies()).get(COOKIE)?.value);
  if (!session) return null;
  try {
    const user = await findActiveById(session.uid);
    // A password change or reset signs out every session issued before it.
    if (!user || session.issuedAt < Math.floor(user.passwordChangedAt / 1000)) return null;
    return { id: user.id, email: user.email, role: user.role, verified: user.verified };
  } catch {
    return null;
  }
}

export async function requireUser(): Promise<User> {
  const user = await currentUser();
  if (!user) redirect("/login");
  return user;
}

export async function requireAdmin(): Promise<User> {
  const user = await requireUser();
  if (user.role !== "admin") redirect("/");
  return user;
}

/** Admins see everything; everyone else sees only calls made with their own keys. */
export const scopeOf = (u: User): Scope => ({ userId: u.role === "admin" ? null : u.id });

export async function sessionCookie(userId: string, req: Request) {
  const cfg = authConfig();
  if (!cfg.ok) throw new Error("dashboard is not configured");
  return {
    name: COOKIE,
    value: await signSession(cfg.secret, userId),
    options: {
      httpOnly: true,
      secure: new URL(req.url).protocol === "https:",
      sameSite: "lax" as const,
      path: "/",
      maxAge: SESSION_TTL_S,
    },
  };
}

/**
 * Refuse cross-site requests. Browsers state where a request came from in `Sec-Fetch-Site`, which
 * page scripts cannot forge; the Origin header is the fallback for clients without it. (A strict
 * `Referrer-Policy: no-referrer` would make browsers send `Origin: null` even from our own forms,
 * which is why the site uses `same-origin`.)
 */
export function sameOrigin(req: Request): boolean {
  const site = req.headers.get("sec-fetch-site");
  if (site) return site === "same-origin" || site === "none";
  const origin = req.headers.get("origin");
  if (!origin) return true; // not a browser: curl, server-side clients
  try {
    const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host") ?? new URL(req.url).host;
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

export const noStore = { "cache-control": "no-store" };
