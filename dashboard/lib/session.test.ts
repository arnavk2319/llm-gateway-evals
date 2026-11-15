import assert from "node:assert/strict";
import { test } from "node:test";
import { authConfig, safeEqual, safeNext, signSession, verifySession } from "./session.ts";

const SECRET = "a".repeat(40);

test("a signed session verifies until it expires", async () => {
  const t = Date.UTC(2026, 0, 1);
  const token = await signSession(SECRET, t);
  assert.equal(await verifySession(SECRET, token, t + 1000), true);
  assert.equal(await verifySession(SECRET, token, t + 13 * 3600 * 1000), false);
});

test("tampered, forged and malformed sessions are rejected", async () => {
  const t = Date.UTC(2026, 0, 1);
  const token = await signSession(SECRET, t);
  const [exp, mac] = token.split(".");
  assert.equal(await verifySession("b".repeat(40), token, t), false); // other secret
  assert.equal(await verifySession(SECRET, `${Number(exp) + 99999}.${mac}`, t), false); // extended expiry
  assert.equal(await verifySession(SECRET, `${exp}.${"0".repeat(64)}`, t), false);
  for (const bad of [undefined, "", "x", "1.2.3", "abc.def", `${exp}.`, `.${mac}`, "99999999999999.aa"]) {
    assert.equal(await verifySession(SECRET, bad, t), false, String(bad));
  }
});

test("safeEqual compares whole strings", () => {
  assert.equal(safeEqual("hunter2hunter2", "hunter2hunter2"), true);
  assert.equal(safeEqual("hunter2hunter2", "hunter2hunter3"), false);
  assert.equal(safeEqual("a", "ab"), false);
  assert.equal(safeEqual("", ""), true);
});

test("safeNext allows only same-site paths", () => {
  assert.equal(safeNext("/logs?severity=error"), "/logs?severity=error");
  for (const evil of ["//evil.example", "https://evil.example", "/\\evil.example", "javascript:alert(1)", "/a\r\nSet-Cookie: x=1", "", null, undefined]) {
    assert.equal(safeNext(evil as string | null | undefined), "/", String(evil));
  }
});

test("auth fails closed in production and is open only in development", () => {
  assert.deepEqual(authConfig({ NODE_ENV: "production" }), { ok: false, reason: "unconfigured" });
  assert.deepEqual(authConfig({ NODE_ENV: "production", DASHBOARD_PASSWORD: "short", DASHBOARD_SESSION_SECRET: SECRET }), {
    ok: false,
    reason: "unconfigured",
  });
  assert.deepEqual(authConfig({ NODE_ENV: "production", DASHBOARD_PASSWORD: "x".repeat(12), DASHBOARD_SESSION_SECRET: "tiny" }), {
    ok: false,
    reason: "unconfigured",
  });
  assert.equal("open" in authConfig({ NODE_ENV: "development" }), true);
  const ok = authConfig({ NODE_ENV: "production", DASHBOARD_PASSWORD: "x".repeat(12), DASHBOARD_SESSION_SECRET: SECRET });
  assert.equal(ok.ok && "password" in ok, true);
});
