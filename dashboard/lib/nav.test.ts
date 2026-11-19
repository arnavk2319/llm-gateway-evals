import assert from "node:assert/strict";
import { test } from "node:test";
import { crumbsFor, GROUPS, isActive } from "./nav.ts";

test("every page appears once and sits under a group", () => {
  const hrefs = GROUPS.flatMap((g) => g.items.map((i) => i.href));
  assert.equal(new Set(hrefs).size, hrefs.length);
  assert.ok(hrefs.every((h) => /^\/[a-z]+\/[a-z]+$/.test(h)));
});

test("only the admin group is admin-only", () => {
  assert.deepEqual(GROUPS.filter((g) => g.adminOnly).map((g) => g.id), ["admin"]);
});

test("a path is active for its own page and anything beneath it, not a sibling with the same prefix", () => {
  assert.equal(isActive("/traffic/runs/run_1", "/traffic/runs"), true);
  assert.equal(isActive("/traffic/runs", "/traffic/runs"), true);
  assert.equal(isActive("/traffic/requests", "/traffic/runs"), false);
  assert.equal(isActive("/traffic/runsx", "/traffic/runs"), false);
});

test("breadcrumbs name the group, the page and a detail id", () => {
  assert.deepEqual(crumbsFor("/overview"), ["Overview"]);
  assert.deepEqual(crumbsFor("/traffic/requests"), ["Traffic", "Requests"]);
  assert.deepEqual(crumbsFor("/traffic/runs/run_demo%207"), ["Traffic", "Runs and traces", "run_demo 7"]);
  assert.deepEqual(crumbsFor("/account"), ["Account"]);
  assert.deepEqual(crumbsFor("/nowhere"), []);
});
