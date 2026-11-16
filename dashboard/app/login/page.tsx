import Link from "next/link";
import { safeNext } from "@/lib/session";

export const dynamic = "force-dynamic";

const MESSAGES: Record<string, string> = {
  invalid: "Wrong email or password, or the account is temporarily locked.",
  unavailable: "The database is unreachable. Try again in a moment.",
  unconfigured: "Dashboard login is not configured on this deployment.",
};

const NOTICES: Record<string, string> = { reset: "Password changed. Sign in with the new one." };

export default async function Login({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const notice = sp.notice ? NOTICES[sp.notice] : undefined;
  const error = sp.error ? (MESSAGES[sp.error] ?? "Sign-in failed.") : null;
  return (
    <main style={{ maxWidth: 360 }}>
      <h1>Sign in</h1>
      <form method="post" action="/api/login" className="stack">
        <input type="hidden" name="next" value={safeNext(sp.next)} />
        <input type="email" name="email" placeholder="Email" autoComplete="email" required autoFocus />
        <input type="password" name="password" placeholder="Password" autoComplete="current-password" required />
        <button type="submit">Sign in</button>
        {notice ? <p role="status" className="ok" style={{ margin: 0 }}>{notice}</p> : null}
        {error ? <p role="alert" className="bad" style={{ margin: 0 }}>{error}</p> : null}
      </form>
      <p className="muted">
        No account? <Link href="/signup">Sign up</Link> · <Link href="/forgot">Forgot password?</Link>
      </p>
    </main>
  );
}
