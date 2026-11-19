import type { Metadata } from "next";
import Link from "next/link";
import { AuthShell } from "@/components/AuthShell";
import { PasswordField } from "@/components/PasswordField";
import { safeNext } from "@/lib/session";

export const metadata: Metadata = { title: "Sign in" };
export const dynamic = "force-dynamic";

const MESSAGES: Record<string, string> = {
  invalid: "Wrong email or password, or the account is temporarily locked.",
  unavailable: "The gateway is unreachable. Try again in a moment.",
  unconfigured: "Dashboard login is not configured on this deployment.",
};

const NOTICES: Record<string, string> = { reset: "Password changed. Sign in with the new one." };

export default async function Login({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const notice = sp.notice ? NOTICES[sp.notice] : undefined;
  const error = sp.error ? (MESSAGES[sp.error] ?? "Sign-in failed.") : null;
  return (
    <AuthShell side="proof" foot="Too many wrong passwords lock the account for 15 minutes. Reset it by email at any time.">
      <form method="post" action="/api/login" className="stack" style={{ gap: 18 }}>
        <div>
          <h1>Sign in</h1>
          <p className="sub">Pick up where you left off.</p>
        </div>
        <input type="hidden" name="next" value={safeNext(sp.next)} />
        <label className="field">
          Email
          <input className="input" type="email" name="email" autoComplete="email" required autoFocus />
        </label>
        <PasswordField name="password" label="Password" autoComplete="current-password" />
        <Link href="/forgot" className="a" style={{ fontSize: 13, marginTop: -8 }}>
          Forgot password?
        </Link>
        <button type="submit" className="btn pri">Sign in</button>
        {notice ? <p role="status" className="note ok">{notice}</p> : null}
        {error ? <p role="alert" className="note bad">{error}</p> : null}
        <p className="sub" style={{ margin: 0, fontSize: 14 }}>
          New here? <Link href="/signup" className="a">Create an account</Link>
        </p>
      </form>
    </AuthShell>
  );
}
