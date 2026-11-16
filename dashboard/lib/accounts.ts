/** Sign-up rules. Pure functions so they are easy to test. */
import { safeEqual } from "./session.ts";
import { PASSWORD_MAX, PASSWORD_MIN } from "./password.ts";

export type SignupMode = "open" | "invite" | "closed";
export type SignupConfig = { mode: SignupMode; inviteCode: string; adminEmails: string[]; production: boolean };

export function signupConfig(env: Record<string, string | undefined> = process.env): SignupConfig {
  const production = env.NODE_ENV === "production";
  let inviteCode = (env.SIGNUP_INVITE_CODE ?? "").trim();
  if (production && inviteCode.length < 12) inviteCode = ""; // a guessable code is no code
  const asked = (env.SIGNUP_MODE ?? "").trim().toLowerCase();
  // Production never opens by accident: without a mode, sign-up needs the invite code if one is set, else it is closed.
  const mode: SignupMode =
    asked === "open" || asked === "invite" || asked === "closed" ? asked : inviteCode ? "invite" : production ? "closed" : "open";
  const adminEmails = (env.ADMIN_EMAILS ?? "").split(",").map((e) => e.trim().toLowerCase()).filter(Boolean);
  return { mode, inviteCode, adminEmails, production };
}

/** A valid invite code. Local development with no code configured accepts anything. */
export function inviteOk(cfg: SignupConfig, supplied: string): boolean {
  if (!cfg.inviteCode) return !cfg.production;
  return supplied.length > 0 && safeEqual(supplied.trim(), cfg.inviteCode);
}

export type SignupDecision = { ok: true; role: "user" | "admin" } | { ok: false; error: "closed" | "invite" };

/**
 * Who may sign up, and as what. The admin role needs both an address on ADMIN_EMAILS and the
 * invite code, so someone who merely knows the admin's email can't claim it.
 */
export function decideSignup(cfg: SignupConfig, email: string, code: string): SignupDecision {
  const codeOk = inviteOk(cfg, code);
  const wantsAdmin = cfg.adminEmails.includes(email);
  if (cfg.mode === "closed") return { ok: false, error: "closed" };
  if (cfg.mode === "invite" && !codeOk) return { ok: false, error: "invite" };
  return { ok: true, role: wantsAdmin && codeOk ? "admin" : "user" };
}

const EMAIL_RE = /^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,}$/;

export const normalizeEmail = (v: unknown): string => (typeof v === "string" ? v.trim().toLowerCase() : "");

export function validateCredentials(email: string, password: string): string | null {
  if (email.length > 254 || !EMAIL_RE.test(email)) return "Enter a valid email address.";
  if (password.length < PASSWORD_MIN) return `Use a password of at least ${PASSWORD_MIN} characters.`;
  if (password.length > PASSWORD_MAX) return `Passwords can be at most ${PASSWORD_MAX} characters.`;
  return null;
}

/** Email verification is on unless explicitly disabled (for example while no email provider exists yet). */
export const verificationRequired = (env: Record<string, string | undefined> = process.env): boolean =>
  (env.REQUIRE_EMAIL_VERIFICATION ?? "").trim().toLowerCase() !== "false";

/**
 * Admins already proved themselves with the invite code, so they start verified; everyone else
 * must confirm their address (unless verification is switched off).
 */
export const startsVerified = (role: "user" | "admin", env: Record<string, string | undefined> = process.env): boolean =>
  role === "admin" || !verificationRequired(env);
