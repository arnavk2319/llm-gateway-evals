import { safeNext } from "@/lib/session";

export const dynamic = "force-dynamic";

const MESSAGES: Record<string, string> = {
  invalid: "Wrong password.",
  unconfigured: "Dashboard login is not configured on this deployment.",
};

export default async function Login({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const error = sp.error ? (MESSAGES[sp.error] ?? "Sign-in failed.") : null;
  return (
    <main style={{ maxWidth: 360 }}>
      <h1>Sign in</h1>
      <form method="post" action="/api/login" className="filters" style={{ flexDirection: "column", alignItems: "stretch" }}>
        <input type="hidden" name="next" value={safeNext(sp.next)} />
        <input type="password" name="password" placeholder="Admin password" autoComplete="current-password" required autoFocus />
        <button type="submit">Sign in</button>
        {error ? <p role="alert" style={{ color: "var(--bad)", margin: 0 }}>{error}</p> : null}
      </form>
    </main>
  );
}
