import { NextResponse } from "next/server";
import { sameOrigin } from "@/lib/auth";
import { validateCredentials } from "@/lib/accounts";
import { hashPassword } from "@/lib/password";
import { hashToken, TOKEN_RE } from "@/lib/tokens";
import { resetPassword } from "@/lib/users";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  if (!sameOrigin(req)) return new Response("cross-site request refused", { status: 403 });
  const form = await req.formData().catch(() => null);
  const token = String(form?.get("token") ?? "");
  const password = String(form?.get("password") ?? "");
  const again = String(form?.get("again") ?? "");
  const back = (error: string) =>
    NextResponse.redirect(new URL(`/reset?token=${encodeURIComponent(token)}&error=${error}`, req.url), 303);

  if (!TOKEN_RE.test(token)) return NextResponse.redirect(new URL("/reset?error=invalid", req.url), 303);
  if (validateCredentials("a@b.co", password)) return back("weak"); // reuse the password rules
  if (password !== again) return back("mismatch");

  const ok = await resetPassword(hashToken(token), await hashPassword(password)).catch(() => false);
  if (!ok) return NextResponse.redirect(new URL("/reset?error=invalid", req.url), 303);
  return NextResponse.redirect(new URL("/login?notice=reset", req.url), 303);
}
