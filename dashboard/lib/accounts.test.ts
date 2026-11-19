import assert from "node:assert/strict";
import { test } from "node:test";
import { decideSignup, normalizeEmail, signupConfig, startsVerified, validateCredentials, verificationRequired } from "./accounts.ts";
import { hashPassword, verifyPassword } from "./password.ts";

const prod = { NODE_ENV: "production", SIGNUP_INVITE_CODE: "let-me-in-please" };

test("production sign-up is closed unless configured", () => {
  assert.equal(signupConfig({ NODE_ENV: "production" }).mode, "closed");
  assert.equal(signupConfig({ NODE_ENV: "production", SIGNUP_INVITE_CODE: "x" }).mode, "closed"); // too short to be safe
  assert.equal(signupConfig({ NODE_ENV: "production", SIGNUP_INVITE_CODE: "a-long-enough-code" }).mode, "invite");
  assert.equal(signupConfig({ NODE_ENV: "production", SIGNUP_MODE: "open" }).mode, "open");
  assert.equal(signupConfig({ NODE_ENV: "development" }).mode, "open");
  assert.equal(signupConfig({ NODE_ENV: "production", SIGNUP_MODE: "bogus" }).mode, "closed");
});

test("invite mode needs the code; closed mode lets nobody in", () => {
  const cfg = signupConfig(prod);
  assert.deepEqual(decideSignup(cfg, "let-me-in-please"), { ok: true, adminInvite: true });
  assert.deepEqual(decideSignup(cfg, "wrong"), { ok: false, error: "invite" });
  assert.deepEqual(decideSignup(cfg, ""), { ok: false, error: "invite" });
  assert.deepEqual(decideSignup(signupConfig({ NODE_ENV: "production" }), "anything"), { ok: false, error: "closed" });
});

test("an admin invite is only reported when the code is right, even when sign-up is open", () => {
  const open = signupConfig({ ...prod, SIGNUP_MODE: "open" });
  assert.deepEqual(decideSignup(open, "let-me-in-please"), { ok: true, adminInvite: true });
  assert.deepEqual(decideSignup(open, ""), { ok: true, adminInvite: false });
  assert.deepEqual(decideSignup(open, "guess"), { ok: true, adminInvite: false });
});

test("the dashboard no longer knows who the admins are", () => {
  assert.ok(!("adminEmails" in signupConfig({ ...prod, ADMIN_EMAILS: "boss@example.com" })));
});

test("credentials are validated", () => {
  assert.equal(normalizeEmail("  A@B.Co "), "a@b.co");
  assert.equal(validateCredentials("a@b.co", "correct horse battery"), null);
  assert.ok(validateCredentials("not-an-email", "correct horse battery"));
  assert.ok(validateCredentials("a@b.co", "short"));
  assert.ok(validateCredentials("a@b.co", "x".repeat(201)));
});

test("passwords are salted, verified, and malformed hashes fail closed", async () => {
  const a = await hashPassword("correct horse battery");
  const b = await hashPassword("correct horse battery");
  assert.notEqual(a, b);
  assert.equal(await verifyPassword("correct horse battery", a), true);
  assert.equal(await verifyPassword("correct horse batterz", a), false);
  for (const bad of ["", "plain", "scrypt$1$2$3", "bcrypt$a$b$c$d$e", "scrypt$99999999$8$1$AAAA$AAAA"]) {
    assert.equal(await verifyPassword("x", bad), false, bad);
  }
});

test("only verification-off deployments start verified (the gateway verifies admins itself)", () => {
  assert.equal(verificationRequired({}), true);
  assert.equal(verificationRequired({ REQUIRE_EMAIL_VERIFICATION: "false" }), false);
  assert.equal(startsVerified({}), false);
  assert.equal(startsVerified({ REQUIRE_EMAIL_VERIFICATION: "false" }), true);
});
