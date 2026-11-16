import assert from "node:assert/strict";
import { test } from "node:test";
import { authConfig, safeEqual, safeNext, signSession, verifySession } from "./session.ts";

const SECRET = "a".repeat(40);
const UID = "usr_0123456789ab";

test("a signed session returns its user until it expires", async () => {
  const t = Date.UTC(2026, 0, 1);
  const token = await signSession(SECRET, UID, t);
  assert.deepEqual(await verifySession(SECRET, token, t + 1000), { uid: UID, issuedAt: t / 1000 });
  assert.equal(await verifySession(SECRET, token, t + 8 * 24 * 3600 * 1000), null);
});

test("tampered, forged and malformed sessions are rejected", async () => {
  const t = Date.UTC(2026, 0, 1);
  const token = await signSession(SECRET, UID, t);
  const [uid, exp, mac] = token.split(".");
  assert.equal(await verifySession("b".repeat(40), token, t), null); // other secret
  assert.equal(await verifySession(SECRET, `${uid}.${Number(exp) + 99999}.${mac}`, t), null); // extended expiry
  assert.equal(await verifySession(SECRET, `usr_aaaaaaaaaaaa.${exp}.${mac}`, t), null); // someone else's id
  assert.equal(await verifySession(SECRET, `${uid}.${exp}.${"0".repeat(64)}`, t), null);
  for (const bad of [undefined, "", "x", "1.2.3", "a.b.c.d", `${uid}.${exp}.`, `.${exp}.${mac}`, `${uid}.99999999999999.aa`]) {
    assert.equal(await verifySession(SECRET, bad, t), null, String(bad));
  }
});

test("safeEqual compares whole strings", () => {
  assert.equal(safeEqual("hunter2hunter2", "hunter2hunter2"), true);
  assert.equal(safeEqual("hunter2hunter2", "hunter2hunter3"), false);
  assert.equal(safeEqual("a", "ab"), false);
  assert.equal(safeEqual("", ""), true);
});

test("production fails closed until the session secret is set", () => {
  assert.deepEqual(authConfig({ NODE_ENV: "production" }), { ok: false, reason: "unconfigured" });
  assert.deepEqual(authConfig({ NODE_ENV: "production", DASHBOARD_SESSION_SECRET: "short" }), { ok: false, reason: "unconfigured" });
  const ok = authConfig({ NODE_ENV: "production", DASHBOARD_SESSION_SECRET: SECRET });
  assert.deepEqual(ok, { ok: true, secret: SECRET });
  assert.equal(authConfig({ NODE_ENV: "development" }).ok, true);
});

test("safeNext only allows same-site paths", () => {
  assert.equal(safeNext("/logs?hours=24"), "/logs?hours=24");
  for (const bad of ["//evil.example", "https://evil.example", "/\\evil", "/a\r\nb", "", null, undefined]) {
    assert.equal(safeNext(bad), "/", String(bad));
  }
});
