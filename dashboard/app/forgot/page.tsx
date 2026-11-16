import Link from "next/link";

export const dynamic = "force-dynamic";

export default async function Forgot({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  return (
    <main style={{ maxWidth: 360 }}>
      <h1>Reset your password</h1>
      {sp.sent ? (
        <p role="status">If an account exists for that address, we have emailed a link to reset the password. It works once and expires in an hour.</p>
      ) : (
        <form method="post" action="/api/forgot" className="stack">
          <input type="email" name="email" placeholder="Email" autoComplete="email" required autoFocus />
          <button type="submit">Email me a reset link</button>
        </form>
      )}
      <p className="muted"><Link href="/login">Back to sign in</Link></p>
    </main>
  );
}
