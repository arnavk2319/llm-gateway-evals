import { NextResponse } from "next/server";
import { after } from "next/server";
import { normalizeEmail } from "@/lib/accounts";
import { sameOrigin } from "@/lib/auth";
import { issueLink } from "@/lib/mailflow";
import { findByEmail } from "@/lib/users";

export const dynamic = "force-dynamic";

/**
 * Always answers the same way, whether or not the address has an account, and does the lookup and the
 * sending after the response, so neither the message nor the timing reveals who is registered.
 */
export async function POST(req: Request) {
  if (!sameOrigin(req)) return new Response("cross-site request refused", { status: 403 });
  const email = normalizeEmail((await req.formData().catch(() => null))?.get("email"));
  if (email && email.length <= 254) {
    after(async () => {
      try {
        const user = await findByEmail(email);
        if (user && !user.disabled) await issueLink(user, "reset");
      } catch (e) {
        console.error("password reset request failed", e instanceof Error ? e.message : e);
      }
    });
  }
  return NextResponse.redirect(new URL("/forgot?sent=1", req.url), 303);
}
