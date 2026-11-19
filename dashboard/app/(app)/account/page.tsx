import type { Metadata } from "next";
import { PageHead } from "@/components/ui";
import { requireUser } from "@/lib/auth";
import { PASSWORD_MIN } from "@/lib/password";

export const metadata: Metadata = { title: "Account" };
export const dynamic = "force-dynamic";

const NOTICES: Record<string, [string, "ok" | "bad"]> = {
  "check-email": ["We sent a confirmation link to your email address. It works once and expires in 24 hours.", "ok"],
  wait: ["A link was sent a moment ago. Check your inbox (and spam), or try again in a minute.", "ok"],
  "mail-failed": ["We could not send the email. Try again shortly.", "bad"],
  verified: ["Your email address is confirmed. You can create API keys.", "ok"],
  "password-changed": ["Password changed. Every other session was signed out.", "ok"],
  "wrong-password": ["The current password is wrong.", "bad"],
  "weak-password": [`Use a new password of at least ${PASSWORD_MIN} characters.`, "bad"],
};

export default async function Account({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const user = await requireUser();
  const sp = await searchParams;
  const notice = sp.notice ? NOTICES[sp.notice] : undefined;
  return (
    <div style={{ maxWidth: 520 }}>
      <PageHead title="Account" />
      {notice ? (
        <p role="status" className={`pill ${notice[1]}`} style={{ height: "auto", padding: "8px 12px", whiteSpace: "normal" }}>
          {notice[0]}
        </p>
      ) : null}

      <section className="card">
        <h2 style={{ marginTop: 0 }}>Email</h2>
        <p style={{ margin: "0 0 8px" }}>
          {user.email} <span className={`pill ${user.verified ? "ok" : "warn"}`}>{user.verified ? "confirmed" : "not confirmed"}</span>
        </p>
        {user.verified ? null : (
          <form method="post" action="/api/verify/send">
            <p className="muted">Confirm your address to create API keys.</p>
            <button type="submit" className="btn">Send the confirmation email again</button>
          </form>
        )}
      </section>

      <section className="card">
        <h2 style={{ marginTop: 0 }}>Change password</h2>
        <form method="post" action="/api/account/password" className="stack">
          <label className="field">
            Current password
            <input className="input" type="password" name="current" autoComplete="current-password" required />
          </label>
          <label className="field">
            New password
            <input className="input" type="password" name="password" autoComplete="new-password" minLength={PASSWORD_MIN} required />
            <span className="hint">At least {PASSWORD_MIN} characters. Changing it signs out every other browser.</span>
          </label>
          <div>
            <button type="submit" className="btn pri">Change password</button>
          </div>
        </form>
      </section>
    </div>
  );
}
