import type { Metadata } from "next";
import Link from "next/link";
import { AuthShell } from "@/components/AuthShell";
import { PasswordField } from "@/components/PasswordField";
import { signupConfig } from "@/lib/accounts";
import { PASSWORD_MIN } from "@/lib/password";

export const metadata: Metadata = { title: "Create an account" };
export const dynamic = "force-dynamic";

const MESSAGES: Record<string, string> = {
  closed: "Sign-up is closed on this deployment.",
  invite: "That invite code is not valid.",
  invalid: `Enter a valid email and a password of at least ${PASSWORD_MIN} characters.`,
  taken: "That email can't be used. Try signing in instead.",
  unavailable: "The gateway is unreachable. Try again in a moment.",
  unconfigured: "Dashboard login is not configured on this deployment.",
};

export default async function Signup({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const cfg = signupConfig();
  const error = sp.error ? (MESSAGES[sp.error] ?? "Sign-up failed.") : null;
  return (
    <AuthShell side="steps" foot="By creating an account you agree to keep your keys private.">
      {cfg.mode === "closed" ? (
        <>
          <div>
            <h1>Create your account</h1>
            <p className="sub">Sign-up is closed on this deployment.</p>
          </div>
          <p className="sub" style={{ margin: 0, fontSize: 14 }}>
            Already have an account? <Link href="/login" className="a">Sign in</Link>
          </p>
        </>
      ) : (
        <form method="post" action="/api/signup" className="stack" style={{ gap: 18 }}>
          <div>
            <h1>Create your account</h1>
            <p className="sub">You will confirm your email before you can create keys.</p>
          </div>
          <label className="field">
            Email
            <input className="input" type="email" name="email" autoComplete="email" required autoFocus />
          </label>
          <PasswordField name="password" label="Password" autoComplete="new-password" min={PASSWORD_MIN} />
          <label className="field">
            {cfg.mode === "invite" ? "Invite code" : "Invite code (admins only)"}
            <input className="input mono" type="password" name="code" autoComplete="off" required={cfg.mode === "invite"} />
            <span className="hint">Ask the person who runs this gateway.</span>
          </label>
          <button type="submit" className="btn pri">Create account</button>
          {error ? <p role="alert" className="note bad">{error}</p> : null}
          <p className="sub" style={{ margin: 0, fontSize: 14 }}>
            Already have an account? <Link href="/login" className="a">Sign in</Link>
          </p>
        </form>
      )}
      {cfg.mode === "closed" && error ? <p role="alert" className="note bad">{error}</p> : null}
    </AuthShell>
  );
}
