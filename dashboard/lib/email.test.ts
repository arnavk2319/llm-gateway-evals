import assert from "node:assert/strict";
import { test } from "node:test";
import { appUrl, emailConfig, resetMail, sendMail, verifyMail } from "./email.ts";
import { generateToken, hashToken, TOKEN_RE } from "./tokens.ts";

test("provider selection never sends mail by accident", () => {
  assert.deepEqual(emailConfig({ NODE_ENV: "development" }), { provider: "log" });
  assert.deepEqual(emailConfig({ NODE_ENV: "production" }), { provider: "none" });
  const r = emailConfig({ NODE_ENV: "production", RESEND_API_KEY: "k", EMAIL_FROM: "Me <a@b.co>" });
  assert.deepEqual(r, { provider: "resend", apiKey: "k", from: "Me <a@b.co>" });
});

test("links come from configuration, never from the request", () => {
  assert.equal(appUrl({}), "http://localhost:3100");
  assert.equal(appUrl({ APP_URL: "https://app.example.com/" }), "https://app.example.com");
  assert.equal(appUrl({ VERCEL_ENV: "production", VERCEL_PROJECT_PRODUCTION_URL: "p.vercel.app" }), "https://p.vercel.app");
  assert.equal(appUrl({ VERCEL_ENV: "preview", VERCEL_BRANCH_URL: "b-git-dev.vercel.app", VERCEL_URL: "x.vercel.app" }), "https://b-git-dev.vercel.app");
  assert.equal(appUrl({ APP_URL: "https://set.example.com", VERCEL_BRANCH_URL: "ignored.vercel.app" }), "https://set.example.com");
});

test("resend: sends the right request and reports failure honestly", async () => {
  const env = { NODE_ENV: "production", RESEND_API_KEY: "re_test", EMAIL_FROM: "G <g@x.co>" };
  let seen: { url: string; init: RequestInit } | undefined;
  const ok = await sendMail(verifyMail("u@x.co", "https://a/verify?token=t"), env, async (url, init) => {
    seen = { url: String(url), init: init! };
    return new Response("{}", { status: 200 });
  });
  assert.equal(ok, true);
  assert.equal(seen!.url, "https://api.resend.com/emails");
  assert.equal((seen!.init.headers as Record<string, string>).authorization, "Bearer re_test");
  const body = JSON.parse(String(seen!.init.body));
  assert.deepEqual(body.to, ["u@x.co"]);
  assert.equal(body.from, "G <g@x.co>");
  assert.match(body.text, /verify\?token=t/);

  assert.equal(await sendMail(resetMail("u@x.co", "l"), env, async () => new Response("no", { status: 422 })), false);
  assert.equal(await sendMail(resetMail("u@x.co", "l"), env, async () => { throw new Error("down"); }), false);
  assert.equal(await sendMail(resetMail("u@x.co", "l"), { NODE_ENV: "production" }, async () => { throw new Error("must not be called"); }), false);
});

test("tokens are random, url-safe and stored only as hashes", () => {
  const a = generateToken();
  assert.match(a, TOKEN_RE);
  assert.notEqual(a, generateToken());
  assert.match(hashToken(a), /^[a-f0-9]{64}$/);
  assert.notEqual(hashToken(a), a);
});
