/**
 * Transactional email. Resend over plain HTTPS (no SDK): set RESEND_API_KEY and EMAIL_FROM.
 * Without a key, development prints the message to the server console so the flows can be tried
 * locally, and production sends nothing and says so (callers decide how to tell the user).
 */

export type Mail = { to: string; subject: string; text: string };
export type EmailConfig =
  | { provider: "resend"; apiKey: string; from: string }
  | { provider: "log" }
  | { provider: "none" };

type Env = Record<string, string | undefined>;

export function emailConfig(env: Env = process.env): EmailConfig {
  const apiKey = (env.RESEND_API_KEY ?? "").trim();
  if (apiKey) return { provider: "resend", apiKey, from: (env.EMAIL_FROM ?? "").trim() || "LLM Gateway <onboarding@resend.dev>" };
  return env.NODE_ENV === "production" ? { provider: "none" } : { provider: "log" };
}

/** Where links in emails point. Never derived from the request, so a forged Host header can't poison them. */
export function appUrl(env: Env = process.env): string {
  const explicit = (env.APP_URL ?? "").trim();
  const host =
    env.VERCEL_ENV === "production" ? env.VERCEL_PROJECT_PRODUCTION_URL : env.VERCEL_BRANCH_URL || env.VERCEL_URL;
  const url = explicit || (host ? `https://${host}` : "http://localhost:3100");
  return url.replace(/\/+$/, "");
}

export async function sendMail(mail: Mail, env: Env = process.env, fetchImpl: typeof fetch = fetch): Promise<boolean> {
  const cfg = emailConfig(env);
  if (cfg.provider === "log") {
    console.log(`[email:dev] to=${mail.to}\n${mail.subject}\n${mail.text}\n`);
    return true;
  }
  if (cfg.provider === "none") {
    console.error("email is not configured (set RESEND_API_KEY and EMAIL_FROM); message not sent");
    return false;
  }
  try {
    const res = await fetchImpl("https://api.resend.com/emails", {
      method: "POST",
      headers: { authorization: `Bearer ${cfg.apiKey}`, "content-type": "application/json" },
      body: JSON.stringify({ from: cfg.from, to: [mail.to], subject: mail.subject, text: mail.text }),
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) console.error(`email provider answered ${res.status}`);
    return res.ok;
  } catch (e) {
    console.error("email send failed", e instanceof Error ? e.message : e);
    return false;
  }
}

export const verifyMail = (to: string, link: string): Mail => ({
  to,
  subject: "Confirm your email for LLM Gateway",
  text: `Confirm your email address to start creating API keys:\n\n${link}\n\nThe link works once and expires in 24 hours. If you didn't create an account, ignore this email.`,
});

export const resetMail = (to: string, link: string): Mail => ({
  to,
  subject: "Reset your LLM Gateway password",
  text: `Someone asked to reset the password for this account. To choose a new one:\n\n${link}\n\nThe link works once and expires in 1 hour. If this wasn't you, ignore this email; your password is unchanged.`,
});
