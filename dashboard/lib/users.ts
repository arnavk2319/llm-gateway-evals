/** Accounts, as the gateway stores them. Every function is a call to /dashboard/v1 (see lib/gateway.ts). */
import { GatewayError, gw, gwOrStatus } from "./gateway.ts";
import type { TokenPurpose } from "./tokens.ts";

export type Role = "user" | "admin";
export type User = { id: string; email: string; role: Role; verified: boolean };
export type Credentials = User & { passwordHash: string; disabled: boolean; locked: boolean };
export type ActiveUser = User & { passwordChangedAt: number }; // epoch ms

const enc = encodeURIComponent;

export const findByEmail = (email: string) => gw<Credentials | null>("/users/lookup", { method: "POST", body: { email } });

/** An active user, or null if the account is gone or disabled. */
export async function findActiveById(id: string): Promise<ActiveUser | null> {
  const r = await gwOrStatus<ActiveUser>(`/users/${enc(id)}`, {}, [404]);
  return r.ok ? r.data : null;
}

/** null when the email is already registered. */
export async function createUser(email: string, passwordHash: string, role: Role, verified = false): Promise<User | null> {
  const r = await gwOrStatus<User>("/users", { method: "POST", body: { email, passwordHash, role, verified } }, [409]);
  return r.ok ? r.data : null;
}

/** Five wrong passwords lock the account for fifteen minutes (enforced by the gateway). */
export const recordFailure = (id: string) => gw<unknown>(`/users/${enc(id)}/login-failure`, { method: "POST" }).then(() => undefined);

export const recordSuccess = (id: string) => gw<unknown>(`/users/${enc(id)}/login-success`, { method: "POST" }).then(() => undefined);

export type UserRow = {
  id: string;
  email: string;
  role: Role;
  disabled: boolean;
  createdAt: string;
  lastLoginAt: string | null;
  activeKeys: number;
  calls24h: number;
};

export const listUsers = () => gw<UserRow[]>("/users");

/** Admins can't be disabled from the dashboard, so nobody can lock themselves out. */
export async function setDisabled(id: string, disabled: boolean): Promise<boolean> {
  const r = await gwOrStatus<unknown>(`/users/${enc(id)}/disabled`, { method: "POST", body: { disabled } }, [404]);
  return r.ok;
}

// ---- emailed links: verify the address, reset the password -----------------------------------

/**
 * Store a link's hash. False when this account asked for one too recently or too often (the gateway
 * allows one a minute and a handful an hour, so the endpoint can't be used to spam an inbox).
 */
export async function issueToken(userId: string, purpose: TokenPurpose, tokenHash: string): Promise<boolean> {
  const r = await gwOrStatus<unknown>(`/users/${enc(userId)}/tokens`, { method: "POST", body: { purpose, tokenHash } }, [429]);
  return r.ok;
}

/** Is this link still usable? (Does not use it up: a mail scanner opening the link must not burn it.) */
export async function tokenUsable(purpose: TokenPurpose, tokenHash: string): Promise<boolean> {
  return (await gw<{ usable: boolean }>("/tokens/usable", { method: "POST", body: { purpose, tokenHash } })).usable;
}

/** Marks the address verified. The token is single-use; false if it is unknown, used or expired. */
export async function verifyEmail(tokenHash: string): Promise<boolean> {
  return (await gw<{ ok: boolean }>("/tokens/verify-email", { method: "POST", body: { tokenHash } })).ok;
}

/** Sets a new password from a reset link: single-use, signs out every older session, clears any lockout. */
export async function resetPassword(tokenHash: string, passwordHash: string): Promise<boolean> {
  return (await gw<{ ok: boolean }>("/tokens/reset-password", { method: "POST", body: { tokenHash, passwordHash } })).ok;
}

/** Signed-in password change. The caller issues a fresh cookie; all older ones stop working. */
export async function changePassword(userId: string, passwordHash: string): Promise<void> {
  await gw<unknown>(`/users/${enc(userId)}/password`, { method: "POST", body: { passwordHash } });
}

export async function passwordHashOf(userId: string): Promise<string | null> {
  try {
    return (await gw<{ passwordHash: string }>(`/users/${enc(userId)}/password-hash`)).passwordHash;
  } catch (e) {
    if (e instanceof GatewayError && e.status === 404) return null;
    throw e;
  }
}
