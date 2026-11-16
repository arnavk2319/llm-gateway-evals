import { NextResponse } from "next/server";
import { currentUser, sameOrigin } from "@/lib/auth";
import { issueLink } from "@/lib/mailflow";

export const dynamic = "force-dynamic";

/** "Send the confirmation email again" from the account page. */
export async function POST(req: Request) {
  if (!sameOrigin(req)) return new Response("cross-site request refused", { status: 403 });
  const user = await currentUser();
  if (!user) return NextResponse.redirect(new URL("/login?next=%2Faccount", req.url), 303);
  if (user.verified) return NextResponse.redirect(new URL("/account", req.url), 303);
  const result = await issueLink(user, "verify").catch(() => "failed" as const);
  return NextResponse.redirect(new URL(`/account?notice=${result === "sent" ? "check-email" : result === "wait" ? "wait" : "mail-failed"}`, req.url), 303);
}
