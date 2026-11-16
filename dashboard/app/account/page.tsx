import { requireUser } from "@/lib/auth";
import { PASSWORD_MIN } from "@/lib/password";

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
    <main style={{ maxWidth: 520 }}>
      <h1>Account</h1>
      {notice ? <p role="status" className={notice[1]}>{notice[0]}</p> : null}

      <section>
        <h2>Email</h2>
        <p>
          {user.email} <span className={user.verified ? "ok" : "warn"}>{user.verified ? "confirmed" : "not confirmed"}</span>
        </p>
        {user.verified ? null : (
          <form method="post" action="/api/verify/send">
            <p className="muted">Confirm your address to create API keys.</p>
            <button type="submit">Send the confirmation email again</button>
          </form>
        )}
      </section>

      <section>
        <h2>Change password</h2>
        <form method="post" action="/api/account/password" className="stack">
          <input type="password" name="current" placeholder="Current password" autoComplete="current-password" required />
          <input type="password" name="password" placeholder={`New password (${PASSWORD_MIN}+ characters)`} autoComplete="new-password" minLength={PASSWORD_MIN} required />
          <button type="submit">Change password</button>
        </form>
        <p className="muted">Changing it signs out every other browser.</p>
      </section>
    </main>
  );
}
