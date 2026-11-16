import { appUrl, resetMail, sendMail, verifyMail } from "./email.ts";
import { generateToken, hashToken, type TokenPurpose } from "./tokens.ts";
import { issueToken } from "./users.ts";

export type IssueResult = "sent" | "wait" | "failed";

/** Create a one-time link for the account and email it. "wait" means too soon after the last one. */
export async function issueLink(user: { id: string; email: string }, purpose: TokenPurpose): Promise<IssueResult> {
  const token = generateToken();
  if (!(await issueToken(user.id, purpose, hashToken(token)))) return "wait";
  const path = purpose === "verify" ? "verify" : "reset";
  const link = `${appUrl()}/${path}?token=${token}`;
  const ok = await sendMail((purpose === "verify" ? verifyMail : resetMail)(user.email, link));
  return ok ? "sent" : "failed";
}
