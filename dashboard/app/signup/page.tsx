import Link from "next/link";
import { signupConfig } from "@/lib/accounts";
import { PASSWORD_MIN } from "@/lib/password";

export const dynamic = "force-dynamic";

const MESSAGES: Record<string, string> = {
  closed: "Sign-up is closed on this deployment.",
  invite: "That invite code is not valid.",
  invalid: `Enter a valid email and a password of at least ${PASSWORD_MIN} characters.`,
  taken: "That email can't be used. Try signing in instead.",
  unavailable: "The database is unreachable. Try again in a moment.",
  unconfigured: "Dashboard login is not configured on this deployment.",
};

export default async function Signup({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const cfg = signupConfig();
  const error = sp.error ? (MESSAGES[sp.error] ?? "Sign-up failed.") : null;
  return (
    <main style={{ maxWidth: 360 }}>
      <h1>Create an account</h1>
      {cfg.mode === "closed" ? (
        <p className="muted">Sign-up is closed on this deployment.</p>
      ) : (
        <form method="post" action="/api/signup" className="stack">
          <input type="email" name="email" placeholder="Email" autoComplete="email" required autoFocus />
          <input type="password" name="password" placeholder={`Password (${PASSWORD_MIN}+ characters)`} autoComplete="new-password" minLength={PASSWORD_MIN} required />
          {cfg.mode === "invite" ? (
            <input type="password" name="code" placeholder="Invite code" autoComplete="off" required />
          ) : (
            <input type="password" name="code" placeholder="Invite code (admins only)" autoComplete="off" />
          )}
          <button type="submit">Create account</button>
        </form>
      )}
      {error ? <p role="alert" className="bad">{error}</p> : null}
      <p className="muted">
        Already have an account? <Link href="/login">Sign in</Link>
      </p>
    </main>
  );
}
