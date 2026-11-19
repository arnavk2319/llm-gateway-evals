import type { Metadata } from "next";
import Link from "next/link";
import { AuthShell } from "@/components/AuthShell";
import { PasswordField } from "@/components/PasswordField";
import { PASSWORD_MIN } from "@/lib/password";
import { hashToken, TOKEN_RE } from "@/lib/tokens";
import { tokenUsable } from "@/lib/users";

export const metadata: Metadata = { title: "Choose a new password" };
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
      <AuthShell side="proof">
        <div>
          <h1>Reset your password</h1>
        </div>
        <p role="alert" className="note bad">This link is invalid, has already been used, or has expired.</p>
        <p className="sub" style={{ margin: 0, fontSize: 14 }}>
          <Link href="/forgot" className="a">Request a new link</Link>
        </p>
      </AuthShell>
    );
  }
  return (
    <AuthShell side="proof" foot="This signs you out everywhere else.">
      <form method="post" action="/api/reset" className="stack" style={{ gap: 18 }}>
        <div>
          <h1>Choose a new password</h1>
        </div>
        <input type="hidden" name="token" value={token} />
        <PasswordField name="password" label="New password" autoComplete="new-password" min={PASSWORD_MIN} autoFocus />
        <PasswordField name="again" label="Repeat the new password" autoComplete="new-password" />
        <button type="submit" className="btn pri">Set password</button>
        {sp.error && MESSAGES[sp.error] ? <p role="alert" className="note bad">{MESSAGES[sp.error]}</p> : null}
      </form>
    </AuthShell>
  );
}
