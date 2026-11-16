import Link from "next/link";
import { PASSWORD_MIN } from "@/lib/password";
import { hashToken, TOKEN_RE } from "@/lib/tokens";
import { tokenUsable } from "@/lib/users";

export const dynamic = "force-dynamic";

const MESSAGES: Record<string, string> = {
  weak: `Use a password of at least ${PASSWORD_MIN} characters.`,
  mismatch: "The two passwords don't match.",
};

export default async function Reset({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const token = sp.token ?? "";
  const usable = TOKEN_RE.test(token) && (await tokenUsable("reset", hashToken(token)).catch(() => false));
  if (!usable) {
    return (
      <main style={{ maxWidth: 360 }}>
        <h1>Reset your password</h1>
        <p role="alert" className="bad">This link is invalid, has already been used, or has expired.</p>
        <p><Link href="/forgot">Request a new link</Link></p>
      </main>
    );
  }
  return (
    <main style={{ maxWidth: 360 }}>
      <h1>Choose a new password</h1>
      <form method="post" action="/api/reset" className="stack">
        <input type="hidden" name="token" value={token} />
        <input type="password" name="password" placeholder={`New password (${PASSWORD_MIN}+ characters)`} autoComplete="new-password" minLength={PASSWORD_MIN} required autoFocus />
        <input type="password" name="again" placeholder="Repeat the new password" autoComplete="new-password" required />
        <button type="submit">Set password</button>
        {sp.error && MESSAGES[sp.error] ? <p role="alert" className="bad" style={{ margin: 0 }}>{MESSAGES[sp.error]}</p> : null}
      </form>
      <p className="muted">This signs you out everywhere else.</p>
    </main>
  );
}
