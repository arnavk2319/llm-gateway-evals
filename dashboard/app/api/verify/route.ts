import { NextResponse } from "next/server";
import { sameOrigin } from "@/lib/auth";
import { hashToken, TOKEN_RE } from "@/lib/tokens";
import { verifyEmail } from "@/lib/users";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  if (!sameOrigin(req)) return new Response("cross-site request refused", { status: 403 });
  const token = String((await req.formData().catch(() => null))?.get("token") ?? "");
  const ok = TOKEN_RE.test(token) && (await verifyEmail(hashToken(token)).catch(() => false));
  return NextResponse.redirect(new URL(ok ? "/account?notice=verified" : "/verify?error=invalid", req.url), 303);
}
