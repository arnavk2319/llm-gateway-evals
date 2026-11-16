import Link from "next/link";
import { hashToken, TOKEN_RE } from "@/lib/tokens";
import { tokenUsable } from "@/lib/users";

export const dynamic = "force-dynamic";

/** A button, not an automatic confirm: mail scanners that open links must not use the token up. */
export default async function Verify({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const token = sp.token ?? "";
  const usable = !sp.error && TOKEN_RE.test(token) && (await tokenUsable("verify", hashToken(token)).catch(() => false));
  return (
    <main style={{ maxWidth: 420 }}>
      <h1>Confirm your email</h1>
      {usable ? (
        <form method="post" action="/api/verify" className="stack">
          <input type="hidden" name="token" value={token} />
          <p>Press the button to confirm this address and unlock API keys.</p>
          <button type="submit">Confirm email</button>
        </form>
      ) : (
        <>
          <p role="alert" className="bad">This link is invalid, has already been used, or has expired.</p>
          <p><Link href="/account">Go to your account</Link> to send a new one.</p>
        </>
      )}
    </main>
  );
}
