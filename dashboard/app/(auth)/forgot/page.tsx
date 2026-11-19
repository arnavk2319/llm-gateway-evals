import type { Metadata } from "next";
import Link from "next/link";
import { AuthShell } from "@/components/AuthShell";

export const metadata: Metadata = { title: "Reset your password" };
export const dynamic = "force-dynamic";

export default async function Forgot({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  return (
    <AuthShell side="proof">
      <div>
        <h1>Reset your password</h1>
        <p className="sub">We will email you a link that works once.</p>
      </div>
      {sp.sent ? (
        <p role="status" className="note ok">
          If an account exists for that address, we have emailed a link to reset the password. It works once and expires in an hour.
        </p>
      ) : (
        <form method="post" action="/api/forgot" className="stack" style={{ gap: 18 }}>
          <label className="field">
            Email
            <input className="input" type="email" name="email" autoComplete="email" required autoFocus />
          </label>
          <button type="submit" className="btn pri">Email me a reset link</button>
        </form>
      )}
      <p className="sub" style={{ margin: 0, fontSize: 14 }}>
        <Link href="/login" className="a">Back to sign in</Link>
      </p>
    </AuthShell>
  );
}
