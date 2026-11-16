import assert from "node:assert/strict";
import { test } from "node:test";
import { parseNewKey } from "./keys.ts";

test("new-key input is validated", () => {
  const ok = parseNewKey({ name: "my app", expiresDays: "30", models: "mock/small, groq/llama-3.1-8b-instant", rpm: "50" });
  assert.deepEqual(ok, { ok: true, value: { name: "my app", expiresDays: 30, models: ["mock/small", "groq/llama-3.1-8b-instant"], rpm: 50 } });
  // No rate limit asked for: the gateway applies its own default.
  assert.deepEqual(parseNewKey({ name: "x" }), { ok: true, value: { name: "x", expiresDays: null, models: [], rpm: null } });
  for (const bad of [
    null, {}, { name: "" }, { name: "a".repeat(61) }, { name: "<script>" },
    { name: "x", expiresDays: 0 }, { name: "x", expiresDays: 366 }, { name: "x", expiresDays: 1.5 },
    { name: "x", rpm: 0 }, { name: "x", rpm: 1.5 },
    { name: "x", models: "bad model!" }, { name: "x", models: Array(21).fill("m").join(",") },
  ]) {
    assert.equal(parseNewKey(bad).ok, false, JSON.stringify(bad));
  }
});
