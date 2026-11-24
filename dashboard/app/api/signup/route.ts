import { NextResponse } from "next/server";
import { decideSignup, normalizeEmail, signupConfig, startsVerified, validateCredentials } from "@/lib/accounts";
import { issueLink } from "@/lib/mailflow";
import { sameOrigin, sessionCookie } from "@/lib/auth";
import { hashPassword } from "@/lib/password";
import { authConfig } from "@/lib/session";
import { createUser } from "@/lib/users";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  if (!sameOrigin(req)) return new Response("cross-site request refused", { status: 403 });
  const form = await req.formData().catch(() => null);
  const back = (error: string) => NextResponse.redirect(new URL(`/signup?error=${error}`, req.url), 303);

  if (!authConfig().ok) return back("unconfigured");

  const email = normalizeEmail(form?.get("email"));
  const password = String(form?.get("password") ?? "");
  const code = String(form?.get("code") ?? "").slice(0, 200);
  await new Promise((r) => setTimeout(r, 400)); // the invite code is guessable only this fast

  const decision = decideSignup(signupConfig(), code);
  if (!decision.ok) return back(decision.error);
  if (validateCredentials(email, password)) return back("invalid");

  let user;
  try {
    user = await createUser(email, await hashPassword(password), decision.adminInvite, startsVerified());
  } catch {
    return back("unavailable");
  }
  if (!user) return back("taken");

  // Not signed-in-and-stuck if the email fails: the account page offers "send again".
  if (!user.verified) await issueLink(user, "verify").catch(() => undefined);

  const res = NextResponse.redirect(new URL(user.verified ? "/access/keys" : "/account?notice=check-email", req.url), 303);
  const c = await sessionCookie(user.id, req);
  res.cookies.set(c.name, c.value, c.options);
  return res;
}
