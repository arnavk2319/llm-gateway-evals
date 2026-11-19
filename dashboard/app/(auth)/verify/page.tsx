import type { Metadata } from "next";
import Link from "next/link";
import { AuthShell } from "@/components/AuthShell";
import { hashToken, TOKEN_RE } from "@/lib/tokens";
import { tokenUsable } from "@/lib/users";

export const metadata: Metadata = { title: "Confirm your email" };
export const dynamic = "force-dynamic";

/** A button, not an automatic confirm: mail scanners that open links must not use the token up. */
export default async function Verify({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const token = sp.token ?? "";
  const usable = !sp.error && TOKEN_RE.test(token) && (await tokenUsable("verify", hashToken(token)).catch(() => false));
  return (
    <AuthShell side="steps">
      <div>
        <h1>Confirm your email</h1>
      </div>
      {usable ? (
        <form method="post" action="/api/verify" className="stack" style={{ gap: 18 }}>
          <input type="hidden" name="token" value={token} />
          <p className="sub" style={{ margin: 0 }}>Press the button to confirm this address and unlock API keys.</p>
          <button type="submit" className="btn pri">Confirm email</button>
        </form>
      ) : (
        <>
          <p role="alert" className="note bad">This link is invalid, has already been used, or has expired.</p>
          <p className="sub" style={{ margin: 0, fontSize: 14 }}>
            <Link href="/account" className="a">Go to your account</Link> to send a new one.
          </p>
        </>
      )}
    </AuthShell>
  );
}
