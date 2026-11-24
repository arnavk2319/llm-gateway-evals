import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { GatewayError, gatewayConfig, gw, gwOrStatus } from "./gateway.ts";

const realFetch = globalThis.fetch;
const saved = { url: process.env.GATEWAY_URL, token: process.env.GATEWAY_DASHBOARD_TOKEN };
let calls: { url: string; init: RequestInit }[] = [];

function reply(status: number, body: unknown) {
  globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init: init ?? {} });
    return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  }) as typeof fetch;
}

beforeEach(() => {
  calls = [];
  process.env.GATEWAY_URL = "https://gw.example.com/";
  process.env.GATEWAY_DASHBOARD_TOKEN = "s".repeat(40);
});
afterEach(() => {
  globalThis.fetch = realFetch;
  for (const [k, v] of [["GATEWAY_URL", saved.url], ["GATEWAY_DASHBOARD_TOKEN", saved.token]] as const) {
    if (v === undefined) delete process.env[k];
    else process.env[k] = v;
  }
});

test("both settings are needed, and a trailing slash is ignored", () => {
  assert.deepEqual(gatewayConfig({ GATEWAY_URL: "https://g.test//", GATEWAY_DASHBOARD_TOKEN: " tok " }), { url: "https://g.test", token: "tok" });
  assert.equal(gatewayConfig({ GATEWAY_URL: "https://g.test" }), null);
  assert.equal(gatewayConfig({ GATEWAY_DASHBOARD_TOKEN: "tok" }), null);
});

test("calls go to /dashboard/v1 with the bearer token, never cached, and skip empty query values", async () => {
  reply(200, { ok: true });
  assert.deepEqual(await gw("/analytics", { query: { hours: 24, user: null, other: "" } }), { ok: true });
  assert.equal(calls[0].url, "https://gw.example.com/dashboard/v1/analytics?hours=24");
  const headers = calls[0].init.headers as Record<string, string>;
  assert.equal(headers.authorization, `Bearer ${"s".repeat(40)}`);
  assert.equal(calls[0].init.cache, "no-store");
  assert.equal(calls[0].init.method, "GET");
});

test("a body is sent as JSON", async () => {
  reply(201, { id: "usr_000000000000" });
  await gw("/users", { method: "POST", body: { email: "a@b.co" } });
  assert.equal(calls[0].init.body, '{"email":"a@b.co"}');
  assert.equal((calls[0].init.headers as Record<string, string>)["content-type"], "application/json");
});

test("an error response becomes a GatewayError carrying the gateway's message and status", async () => {
  reply(409, { detail: "You can have at most 3 active keys. Revoke one first." });
  await assert.rejects(gw("/users/x/keys", { method: "POST", body: {} }), (e: unknown) => {
    assert.ok(e instanceof GatewayError);
    assert.equal(e.status, 409);
    assert.match(e.message, /at most 3/);
    return true;
  });
  reply(422, { detail: [{ msg: "bad" }] }); // validation lists are not shown raw
  await assert.rejects(gw("/x"), (e: unknown) => e instanceof GatewayError && e.message === "gateway answered 422");
});

test("gwOrStatus turns an expected status into a value and rethrows the rest", async () => {
  reply(404, { detail: "no such key" });
  assert.deepEqual(await gwOrStatus("/x", {}, [404]), { ok: false, status: 404, message: "no such key" });
  reply(500, { detail: "boom" });
  await assert.rejects(gwOrStatus("/x", {}, [404]), GatewayError);
  reply(200, { v: 1 });
  assert.deepEqual(await gwOrStatus("/x", {}, [404]), { ok: true, data: { v: 1 } });
});

test("an unreachable gateway and a missing configuration both fail with a clear GatewayError(0)", async () => {
  globalThis.fetch = (async () => {
    throw new TypeError("fetch failed");
  }) as typeof fetch;
  await assert.rejects(gw("/x"), (e: unknown) => e instanceof GatewayError && e.status === 0 && /unreachable/.test(e.message));
  delete process.env.GATEWAY_DASHBOARD_TOKEN;
  await assert.rejects(gw("/x"), (e: unknown) => e instanceof GatewayError && e.status === 0 && /GATEWAY_DASHBOARD_TOKEN/.test(e.message));
});
