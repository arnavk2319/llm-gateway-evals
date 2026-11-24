import { NextResponse } from "next/server";
import { sameOrigin, sessionCookie } from "@/lib/auth";
import { normalizeEmail } from "@/lib/accounts";
import { burnPasswordCheck, PASSWORD_MAX, verifyPassword } from "@/lib/password";
import { authConfig, safeNext } from "@/lib/session";
import { findByEmail, recordFailure, recordSuccess } from "@/lib/users";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  if (!sameOrigin(req)) return new Response("cross-site request refused", { status: 403 });
  const form = await req.formData().catch(() => null);
  const next = safeNext(String(form?.get("next") ?? ""));
  const back = (error: string) =>
    NextResponse.redirect(new URL(`/login?error=${error}&next=${encodeURIComponent(next)}`, req.url), 303);

  if (!authConfig().ok) return back("unconfigured");

  const email = normalizeEmail(form?.get("email"));
  const password = String(form?.get("password") ?? "");
  await new Promise((r) => setTimeout(r, 300)); // slows guessing a little; the account lockout does the rest
  if (!email || email.length > 254 || password.length > PASSWORD_MAX) return back("invalid");

  let user;
  try {
    user = await findByEmail(email);
  } catch {
    return back("unavailable");
  }
  if (!user) {
    await burnPasswordCheck(password); // same work whether or not the address exists
    return back("invalid");
  }
  const ok = await verifyPassword(password, user.passwordHash);
  if (!ok || user.locked || user.disabled) {
    if (!ok && !user.locked) await recordFailure(user.id).catch(() => undefined);
    return back("invalid");
  }
  await recordSuccess(user.id).catch(() => undefined);

  // With no deeper link to return to, land on the overview rather than the public home page.
  const res = NextResponse.redirect(new URL(next === "/" ? "/overview" : next, req.url), 303);
  const c = await sessionCookie(user.id, req);
  res.cookies.set(c.name, c.value, c.options);
  return res;
}
