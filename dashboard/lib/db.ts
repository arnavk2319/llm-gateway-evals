import postgres from "postgres";

const g = globalThis as unknown as { __sql?: ReturnType<typeof postgres> };

/** Lazily created so a missing DATABASE_URL shows a setup message instead of failing the build. */
export function getSql() {
  // Prefer a read-only role for the dashboard (see gateway/scripts/dashboard_role.sql).
  const url = process.env.DATABASE_URL_READONLY || process.env.DATABASE_URL;
  if (!url) throw new Error("DATABASE_URL is not set");
  if (!g.__sql) {
    // prepare: false keeps this safe behind pgbouncer-style poolers (e.g. Neon).
    g.__sql = postgres(url, { max: 3, prepare: false, idle_timeout: 20, connect_timeout: 15 });
  }
  return g.__sql;
}
