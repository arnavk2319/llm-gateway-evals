/**
 * Architecture guard (docs-internal/adr/0001): the dashboard reaches data only through the gateway
 * API. This fails if someone adds a database driver or a connection string back to the dashboard.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

const root = new URL("..", import.meta.url).pathname;

function* sources(dir: string): Generator<string> {
  for (const name of readdirSync(dir)) {
    if (["node_modules", ".next"].includes(name)) continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* sources(p);
    else if (/\.(ts|tsx|mjs|js)$/.test(name) && !name.endsWith(".test.ts")) yield p;
  }
}

test("no source file imports a database driver or reads a database URL", () => {
  const banned = [/from\s+["'](postgres|pg|@neondatabase\/[^"']*|mysql2?|mongodb|ioredis|redis)["']/, /DATABASE_URL/];
  for (const file of sources(root)) {
    const text = readFileSync(file, "utf8");
    for (const re of banned) assert.ok(!re.test(text), `${file.slice(root.length)} matches ${re}`);
  }
});

test("package.json declares no database driver", () => {
  const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
  const deps = Object.keys({ ...pkg.dependencies, ...pkg.devDependencies });
  assert.deepEqual(deps.filter((d) => /^(postgres|pg|@neondatabase\/.*|mysql2?|mongodb)$/.test(d)), []);
});
