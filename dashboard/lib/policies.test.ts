import assert from "node:assert/strict";
import { test } from "node:test";
import { MAX_REFS, VERSION, formatRefs, parseRefs, shortRelease } from "./policies.ts";

test("references parse one key=value per line and round-trip", () => {
  const r = parseRefs("app_version=0.1.0\n\n release = 5f1c9ab \nticket=OPS-12=urgent\r\n");
  assert.ok(r.ok);
  assert.deepEqual(r.refs, { app_version: "0.1.0", release: "5f1c9ab", ticket: "OPS-12=urgent" });
  const again = parseRefs(formatRefs(r.refs));
  assert.ok(again.ok);
  assert.deepEqual(again.refs, r.refs);
});

test("an empty text is no references", () => {
  assert.deepEqual(parseRefs("  \n"), { ok: true, refs: {} });
});

test("malformed references are refused with the line number", () => {
  for (const text of ["no equals sign", "=value", "has space=1", `${"k".repeat(41)}=1`, `k=${"v".repeat(201)}`, "a=1\na=2"]) {
    const r = parseRefs(text);
    assert.ok(!r.ok, text);
    assert.match(r.error, /Line \d+/);
  }
});

test("more than the allowed number of references is refused", () => {
  const text = Array.from({ length: MAX_REFS + 1 }, (_, i) => `k${i}=v`).join("\n");
  assert.ok(!parseRefs(text).ok);
  assert.ok(parseRefs(text.split("\n").slice(0, MAX_REFS).join("\n")).ok);
});

test("version ids are positive integers only", () => {
  for (const ok of ["1", "42", "999999999"]) assert.ok(VERSION.test(ok), ok);
  for (const bad of ["0", "01", "-1", "1.5", "abc", "1e3", "", "1000000000"]) assert.ok(!VERSION.test(bad), bad);
});

test("a release shows as a short commit", () => {
  assert.equal(shortRelease("a4e26bd3d9eb1f6c33e6970979da5c9d79f8ebba"), "a4e26bd");
  assert.equal(shortRelease(null), "–");
});
