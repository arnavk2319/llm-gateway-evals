import assert from "node:assert/strict";
import { test } from "node:test";
import { parseFormat, toCsv } from "./export.ts";

test("csv neutralizes spreadsheet formula injection from log text", () => {
  for (const evil of ["=HYPERLINK(\"http://evil\")", "+1+1", "-2+3", "@SUM(A1)", "\t=1", "\r=1"]) {
    const line = toCsv(["m"], [{ m: evil }]).split("\n")[1];
    assert.ok(line.startsWith("'") || line.startsWith("\"'"), `not neutralized: ${evil}`);
  }
});

test("csv quotes commas, quotes and newlines", () => {
  assert.equal(toCsv(["a"], [{ a: 'x,"y"\nz' }]), 'a\n"x,""y""\nz"\n');
});

test("csv renders null and objects safely", () => {
  assert.equal(toCsv(["a", "b"], [{ a: null, b: { k: 1 } }]), 'a,b\n,"{""k"":1}"\n');
});

test("unknown export formats fall back to csv", () => {
  assert.equal(parseFormat("<script>"), "csv");
  assert.equal(parseFormat("jsonl"), "jsonl");
});
