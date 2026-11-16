/** Password hashing with scrypt from Node's standard library: salted, slow, no extra dependency. */
import { randomBytes, scrypt as scryptCb, timingSafeEqual } from "node:crypto";

type ScryptOpts = { N: number; r: number; p: number; maxmem: number };
const scrypt = (pw: string, salt: Buffer, len: number, o: ScryptOpts) =>
  new Promise<Buffer>((resolve, reject) =>
    scryptCb(pw, salt, len, o, (err, key) => (err ? reject(err) : resolve(key))),
  );

const N = 2 ** 15;
const R = 8;
const P = 1;
const KEYLEN = 32;
const MAXMEM = 128 * N * R * 2;

export const PASSWORD_MIN = 12;
export const PASSWORD_MAX = 200; // bounds the work one request can ask for

export async function hashPassword(password: string): Promise<string> {
  const salt = randomBytes(16);
  const key = await scrypt(password, salt, KEYLEN, { N, r: R, p: P, maxmem: MAXMEM });
  return `scrypt$${N}$${R}$${P}$${salt.toString("base64")}$${key.toString("base64")}`;
}

export async function verifyPassword(password: string, stored: string): Promise<boolean> {
  const [alg, n, r, p, salt, hash] = stored.split("$");
  if (alg !== "scrypt" || !n || !r || !p || !salt || !hash || password.length > PASSWORD_MAX) return false;
  const params = { N: Number(n), r: Number(r), p: Number(p) };
  if (![params.N, params.r, params.p].every(Number.isInteger) || params.N > 2 ** 17 || params.N < 2) return false;
  const expected = Buffer.from(hash, "base64");
  const key = await scrypt(password, Buffer.from(salt, "base64"), expected.length, {
    ...params,
    maxmem: 128 * params.N * params.r * 2,
  });
  return key.length === expected.length && timingSafeEqual(key, expected);
}

let dummy: Promise<string> | undefined;
/** Spend the same time on an unknown email as on a known one, so response time doesn't leak which exist. */
export async function burnPasswordCheck(password: string): Promise<void> {
  dummy ??= hashPassword("not-a-real-password");
  await verifyPassword(password, await dummy);
}
