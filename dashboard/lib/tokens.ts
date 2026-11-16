/** Emailed one-time links: a random token goes in the email, only its hash is stored (by the gateway). */
import { createHash, randomBytes } from "node:crypto";

export type TokenPurpose = "verify" | "reset";

export const generateToken = (): string => randomBytes(32).toString("base64url");
export const hashToken = (token: string): string => createHash("sha256").update(token, "utf8").digest("hex");
export const TOKEN_RE = /^[A-Za-z0-9_-]{43}$/;
