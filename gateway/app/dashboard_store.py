"""All the SQL behind the dashboard API (see docs-internal/adr/0001).

The dashboard has no database connection of its own; every read and write it needs lives here, so
the schema, the per-user scoping rule and the account invariants have one owner. Methods return
plain JSON-ready dicts in the shape the dashboard renders (camelCase, ISO-8601 timestamps).

Every dynamic value is a bound parameter. Column names and SQL fragments are constants chosen in
this file, never taken from a request.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, LiteralString

from psycopg import sql
from psycopg.rows import dict_row

from .admins import AdminAllowlist
from .auth import generate_key, hash_key

MAX_FAILURES = 5
LOCK_MINUTES = 15
TOKEN_TTL_HOURS = {"verify": 24, "reset": 1}
TOKEN_COOLDOWN_S = 60
TOKEN_HOURLY_CAP = 5
STATEMENT_TIMEOUT = "10s"  # analytics must not starve the request path's connections

SEVERITIES = ("debug", "info", "warn", "error", "critical")
FACET_TAG_KEYS = ("model", "provider", "node_role", "task_type", "error_class", "key", "env")
KEY_COLUMNS = (
    "SELECT id, name, key_prefix, status, allowed_models, rate_limit_per_min, expires_at, created_at, "
    "last_used_at FROM api_keys"
)

SPAN_EXPORT_COLUMNS = (
    "created_at", "run_id", "span_id", "parent_span_id", "origin", "node_role", "task_type", "complexity",
    "provider", "model", "family", "route_reason", "policy_version", "status", "error_class", "http_status",
    "prompt_tokens", "completion_tokens", "cached_tokens", "cost_usd", "shadow_cost_usd", "latency_ms",
    "ttft_ms", "attempt_count", "fell_back", "overhead_ms", "api_key_name", "node_timings",
)
EXPORT_MAX_ROWS = 50_000


def iso(v: datetime | None) -> str | None:
    return None if v is None else v.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


def severities_at_least(minimum: str) -> list[str]:
    return list(SEVERITIES[SEVERITIES.index(minimum):]) if minimum in SEVERITIES else []


def _scope(user_id: str | None, col: LiteralString = "user_id") -> tuple[sql.Composable, list[Any]]:
    """`AND <col> = <user>` for an ordinary user; nothing for the admin view (user_id None)."""
    if user_id is None:
        return sql.SQL(""), []
    return sql.SQL(" AND {} = %s").format(sql.Identifier(*col.split("."))), [user_id]


class DashboardStore:
    def __init__(self, pool: Any, admins: AdminAllowlist | None = None) -> None:
        self._pool = pool
        self._admins = admins or AdminAllowlist(None, None)  # no allowlist configured: nobody is an admin

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[Any]:
        """A cursor inside a transaction with a statement timeout that ends with it."""
        async with self._pool.connection() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            # SET cannot take bind parameters; the value is a module constant quoted by psycopg.
            await cur.execute(  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
                sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(STATEMENT_TIMEOUT))
            )
            yield cur

    async def _all(self, query: sql.Composable | LiteralString, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        async with self._tx() as cur:
            await cur.execute(query, list(params))
            return list(await cur.fetchall())

    async def _one(self, query: sql.Composable | LiteralString, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = await self._all(query, params)
        return rows[0] if rows else None

    # ---- accounts -------------------------------------------------------------------------------

    async def find_by_email(self, email: str) -> dict[str, Any] | None:
        r = await self._one(
            "SELECT id, email, role, password_hash, disabled, email_verified_at IS NOT NULL AS verified, "
            "(locked_until IS NOT NULL AND locked_until > now()) AS locked FROM users WHERE lower(email) = %s",
            [email.lower()],
        )
        if not r:
            return None
        return {
            "id": r["id"], "email": r["email"], "role": self._admins.effective_role(r["role"], r["email"]),
            "verified": r["verified"], "passwordHash": r["password_hash"], "disabled": r["disabled"], "locked": r["locked"],
        }

    async def find_active_by_id(self, user_id: str) -> dict[str, Any] | None:
        r = await self._one(
            "SELECT id, email, role, email_verified_at IS NOT NULL AS verified, password_changed_at "
            "FROM users WHERE id = %s AND NOT disabled",
            [user_id],
        )
        if not r:
            return None
        return {
            "id": r["id"], "email": r["email"], "role": self._admins.effective_role(r["role"], r["email"]),
            "verified": r["verified"], "passwordChangedAt": int(r["password_changed_at"].timestamp() * 1000),
        }

    async def create_user(
        self, email: str, password_hash: str, admin_invite: bool, verified: bool
    ) -> dict[str, Any] | None:
        """None when the address is already registered (case-insensitive).

        The account becomes an admin only when the caller vouches that a valid admin invite was presented
        AND this address is on the admin allowlist; anyone else is an ordinary user. An admin has proved
        both, so starts verified.
        """
        role = "admin" if admin_invite and self._admins.allows(email) else "user"
        verified = verified or role == "admin"
        user_id = "usr_" + secrets.token_hex(6)
        r = await self._one(
            "INSERT INTO users (id, email, password_hash, role, last_login_at, password_changed_at, "
            "email_verified_at) VALUES (%s, %s, %s, %s, now(), now(), CASE WHEN %s THEN now() END) "
            "ON CONFLICT (lower(email)) DO NOTHING RETURNING id",
            [user_id, email, password_hash, role, verified],
        )
        return {"id": user_id, "email": email, "role": role, "verified": verified} if r else None

    async def record_failure(self, user_id: str) -> None:
        """Five wrong passwords lock the account for fifteen minutes, whoever is guessing."""
        await self._all(
            "UPDATE users SET failed_logins = CASE WHEN failed_logins + 1 >= %s THEN 0 ELSE failed_logins + 1 END, "
            "locked_until = CASE WHEN failed_logins + 1 >= %s THEN now() + make_interval(mins => %s) "
            "ELSE locked_until END WHERE id = %s RETURNING id",
            [MAX_FAILURES, MAX_FAILURES, LOCK_MINUTES, user_id],
        )

    async def record_success(self, user_id: str) -> None:
        await self._all(
            "UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = now() "
            "WHERE id = %s RETURNING id",
            [user_id],
        )

    async def list_users(self) -> list[dict[str, Any]]:
        rows = await self._all(
            "SELECT u.id, u.email, u.role, u.disabled, u.created_at, u.last_login_at, "
            "(SELECT count(*)::int FROM api_keys k WHERE k.user_id = u.id AND k.status = 'active') AS active_keys, "
            "(SELECT count(*)::int FROM spans s WHERE s.user_id = u.id "
            "AND s.created_at > now() - interval '24 hours') AS calls_24h "
            "FROM users u ORDER BY u.created_at DESC LIMIT 500"
        )
        return [
            {
                "id": r["id"], "email": r["email"], "role": self._admins.effective_role(r["role"], r["email"]),
                "disabled": r["disabled"],
                "createdAt": iso(r["created_at"]), "lastLoginAt": iso(r["last_login_at"]),
                "activeKeys": r["active_keys"], "calls24h": r["calls_24h"],
            }
            for r in rows
        ]

    async def set_disabled(self, user_id: str, disabled: bool) -> bool:
        """Admins can't be disabled through this path, so nobody can lock themselves out."""
        rows = await self._all(
            "UPDATE users SET disabled = %s WHERE id = %s AND role <> 'admin' RETURNING id", [disabled, user_id]
        )
        return bool(rows)

    async def password_hash_of(self, user_id: str) -> str | None:
        r = await self._one("SELECT password_hash FROM users WHERE id = %s", [user_id])
        return r["password_hash"] if r else None

    async def change_password(self, user_id: str, password_hash: str) -> bool:
        """Signed-in change. Cookies issued before password_changed_at stop working."""
        rows = await self._all(
            "UPDATE users SET password_hash = %s, password_changed_at = now() WHERE id = %s RETURNING id",
            [password_hash, user_id],
        )
        return bool(rows)

    # ---- emailed links (verify the address, reset the password) ---------------------------------

    async def issue_token(self, user_id: str, purpose: str, token_hash: str) -> bool:
        """Store a link's hash, unless this account asked too recently or too often (returns False)."""
        async with self._tx() as cur:
            await cur.execute(
                "SELECT count(*) FILTER (WHERE created_at > now() - make_interval(secs => %s))::int AS recent, "
                "count(*)::int AS hourly FROM auth_tokens "
                "WHERE user_id = %s AND purpose = %s AND created_at > now() - interval '1 hour'",
                [TOKEN_COOLDOWN_S, user_id, purpose],
            )
            r = await cur.fetchone()
            if r["recent"] or r["hourly"] >= TOKEN_HOURLY_CAP:
                return False
            await cur.execute(
                "INSERT INTO auth_tokens (id, user_id, purpose, token_hash, expires_at) "
                "VALUES (%s, %s, %s, %s, now() + make_interval(hours => %s))",
                ["tok_" + secrets.token_hex(8), user_id, purpose, token_hash, TOKEN_TTL_HOURS[purpose]],
            )
            return True

    async def token_usable(self, purpose: str, token_hash: str) -> bool:
        """Does not use the link up: a mail scanner opening it must not burn it."""
        return bool(await self._all(
            "SELECT 1 FROM auth_tokens WHERE token_hash = %s AND purpose = %s AND used_at IS NULL "
            "AND expires_at > now()",
            [token_hash, purpose],
        ))

    async def verify_email(self, token_hash: str) -> bool:
        async with self._tx() as cur:
            await cur.execute(
                "UPDATE auth_tokens SET used_at = now() WHERE token_hash = %s AND purpose = 'verify' "
                "AND used_at IS NULL AND expires_at > now() RETURNING user_id",
                [token_hash],
            )
            t = await cur.fetchone()
            if not t:
                return False
            await cur.execute(
                "UPDATE users SET email_verified_at = coalesce(email_verified_at, now()) WHERE id = %s",
                [t["user_id"]],
            )
            return True

    async def reset_password(self, token_hash: str, password_hash: str) -> bool:
        """Single-use. Signs out every session, clears any lockout, and proves the inbox is theirs."""
        async with self._tx() as cur:
            await cur.execute(
                "UPDATE auth_tokens SET used_at = now() WHERE token_hash = %s AND purpose = 'reset' "
                "AND used_at IS NULL AND expires_at > now() RETURNING user_id",
                [token_hash],
            )
            t = await cur.fetchone()
            if not t:
                return False
            await cur.execute(
                "UPDATE users SET password_hash = %s, password_changed_at = now(), failed_logins = 0, "
                "locked_until = NULL, email_verified_at = coalesce(email_verified_at, now()) WHERE id = %s",
                [password_hash, t["user_id"]],
            )
            await cur.execute(
                "UPDATE auth_tokens SET used_at = now() WHERE user_id = %s AND purpose = 'reset' AND used_at IS NULL",
                [t["user_id"]],
            )
            return True

    # ---- API keys ---------------------------------------------------------------------------------

    async def create_key(
        self, user_id: str, name: str, expires_days: int | None, models: list[str], rpm: int, max_per_user: int
    ) -> dict[str, Any] | Literal["limit", "unverified", "unknown_user"]:
        """The secret is returned once and only its hash is stored.

        Only an active user with a confirmed email may hold keys, whoever asks: a key can spend provider
        quota, so this rule lives here and not only in the dashboard's form handler.
        """
        owner = await self._one(
            "SELECT disabled, email_verified_at IS NOT NULL AS verified FROM users WHERE id = %s", [user_id]
        )
        if owner is None or owner["disabled"]:
            return "unknown_user"
        if not owner["verified"]:
            return "unverified"
        key = generate_key()
        key_id = "key_" + secrets.token_hex(6)
        prefix = key[:12]
        expires_at = datetime.now(UTC) + timedelta(days=expires_days) if expires_days else None
        rows = await self._all(
            "INSERT INTO api_keys (id, name, key_prefix, key_hash, status, allowed_models, rate_limit_per_min, "
            "expires_at, user_id) "
            "SELECT %s::text, %s::text, %s::text, %s::text, 'active', %s::text[], %s::int, %s::timestamptz, %s::text "
            "WHERE (SELECT count(*) FROM api_keys WHERE user_id = %s AND status = 'active') < %s RETURNING id",
            [key_id, name, prefix, hash_key(key), models or None, rpm, expires_at, user_id, user_id, max_per_user],
        )
        if not rows:
            return "limit"
        return {"key": key, "id": key_id, "name": name, "prefix": prefix, "rpm": rpm, "expiresAt": iso(expires_at)}

    async def list_keys(self, user_id: str) -> list[dict[str, Any]]:
        rows = await self._all(KEY_COLUMNS + " WHERE user_id = %s ORDER BY created_at DESC LIMIT 200", [user_id])
        return [
            {
                "id": r["id"], "name": r["name"], "prefix": r["key_prefix"], "status": r["status"],
                "models": r["allowed_models"], "rpm": r["rate_limit_per_min"], "expiresAt": iso(r["expires_at"]),
                "createdAt": iso(r["created_at"]), "lastUsedAt": iso(r["last_used_at"]),
            }
            for r in rows
        ]

    async def revoke_key(self, user_id: str, key_id: str) -> bool:
        """Only the owner can revoke. The gateway stops accepting the key within its cache window."""
        rows = await self._all(
            "UPDATE api_keys SET status = 'revoked' WHERE id = %s AND user_id = %s RETURNING id", [key_id, user_id]
        )
        return bool(rows)

    # ---- analytics --------------------------------------------------------------------------------

    async def analytics(self, hours: int, user_id: str | None) -> dict[str, Any]:
        mine, mine_p = _scope(user_id)
        mine_s, mine_s_p = _scope(user_id, "s.user_id")

        async def group(col: LiteralString) -> list[dict[str, Any]]:
            q = sql.SQL(
                "SELECT coalesce({col}, '(none)') AS key, count(*)::int AS requests, "
                "(count(*) FILTER (WHERE status <> 'ok'))::float / count(*) AS error_rate, "
                "(count(*) FILTER (WHERE fell_back))::float / count(*) AS fallback_rate, "
                "percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms)::float AS p50, "
                "percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)::float AS p95, "
                "coalesce(sum(cost_usd), 0)::float AS cost, coalesce(sum(shadow_cost_usd), 0)::float AS shadow, "
                "coalesce(sum(coalesce(prompt_tokens, 0) + coalesce(completion_tokens, 0)), 0)::int AS tokens "
                "FROM spans WHERE origin = 'external' AND created_at > now() - make_interval(hours => %s) {mine} "
                "GROUP BY 1 ORDER BY requests DESC"
            ).format(col=sql.Identifier(col), mine=mine)
            rows = await self._all(q, [hours, *mine_p])
            return [
                {"key": r["key"], "requests": r["requests"], "errorRate": float(r["error_rate"]),
                 "fallbackRate": float(r["fallback_rate"]), "p50": _num(r["p50"]), "p95": _num(r["p95"]),
                 "cost": float(r["cost"]), "shadow": float(r["shadow"]), "tokens": r["tokens"]}
                for r in rows
            ]

        kpi = await self._one(
            sql.SQL(
                "SELECT count(*)::int AS requests, (count(*) FILTER (WHERE status <> 'ok'))::int AS errors, "
                "(count(*) FILTER (WHERE fell_back))::int AS fallbacks, "
                "coalesce(sum(cost_usd), 0)::float AS cost, coalesce(sum(shadow_cost_usd), 0)::float AS shadow, "
                "percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms)::float AS p50, "
                "percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)::float AS p95, "
                "coalesce(sum(prompt_tokens), 0)::int AS prompt_tokens, "
                "coalesce(sum(completion_tokens), 0)::int AS completion_tokens, avg(overhead_ms)::float AS overhead_ms "
                "FROM spans WHERE origin = 'external' AND created_at > now() - make_interval(hours => %s) {mine}"
            ).format(mine=mine),
            [hours, *mine_p],
        )
        assert kpi is not None  # an aggregate query always returns one row
        by_model, by_role, by_task = await group("model"), await group("node_role"), await group("task_type")
        nodes = await self._all(
            sql.SQL(
                "SELECT t.key AS node, avg(t.value::float)::float AS avg_ms, count(*)::int AS samples "
                "FROM spans s, jsonb_each_text(s.node_timings) AS t "
                "WHERE s.origin = 'external' AND s.created_at > now() - make_interval(hours => %s) {mine} "
                "GROUP BY t.key ORDER BY avg_ms DESC"
            ).format(mine=mine_s),
            [hours, *mine_s_p],
        )
        attempts = await self._all(
            sql.SQL(
                "SELECT a.outcome_class AS outcome, count(*)::int AS n FROM attempts a JOIN spans s USING (span_id) "
                "WHERE s.origin = 'external' AND s.created_at > now() - make_interval(hours => %s) {mine} GROUP BY 1 ORDER BY n DESC"
            ).format(mine=mine_s),
            [hours, *mine_s_p],
        )
        internal = await self._all(
            sql.SQL(
                "SELECT node_role AS role, count(*)::int AS calls, coalesce(sum(cost_usd), 0)::float AS cost, "
                "coalesce(sum(shadow_cost_usd), 0)::float AS shadow, avg(latency_ms)::float AS avg_ms "
                "FROM spans WHERE origin = 'internal' AND created_at > now() - make_interval(hours => %s) {mine} "
                "GROUP BY 1 ORDER BY calls DESC"
            ).format(mine=mine),
            [hours, *mine_p],
        )
        recent = await self._all(
            sql.SQL(
                "SELECT created_at, node_role, task_type, model, status, attempt_count, latency_ms, ttft_ms, "
                "coalesce(prompt_tokens, 0) + coalesce(completion_tokens, 0) AS tokens, "
                "coalesce(shadow_cost_usd, 0)::float AS shadow, run_id, route_reason "
                "FROM spans WHERE origin = 'external' {mine} ORDER BY created_at DESC LIMIT 25"
            ).format(mine=mine),
            mine_p,
        )
        policy = await self._one(
            "SELECT version, created_at FROM routing_policy WHERE status = 'active' ORDER BY version DESC LIMIT 1"
        )
        # Bars for the overview chart: a minute for a one-hour window, an hour up to a week, else a day.
        bucket = "minute" if hours <= 1 else "hour" if hours <= 168 else "day"
        series = await self._all(
            sql.SQL(
                "SELECT date_trunc(%s, created_at) AS at, count(*)::int AS requests, "
                "(count(*) FILTER (WHERE fell_back))::int AS fallbacks "
                "FROM spans WHERE origin = 'external' AND created_at > now() - make_interval(hours => %s) {mine} "
                "GROUP BY 1 ORDER BY 1"
            ).format(mine=mine),
            [bucket, hours, *mine_p],
        )
        return {
            "hours": hours,
            "bucket": bucket,
            "series": [{"at": iso(r["at"]), "requests": r["requests"], "fallbacks": r["fallbacks"]} for r in series],
            "kpis": {
                "requests": kpi["requests"], "errors": kpi["errors"], "fallbacks": kpi["fallbacks"],
                "cost": float(kpi["cost"]), "shadow": float(kpi["shadow"]), "p50": _num(kpi["p50"]),
                "p95": _num(kpi["p95"]), "promptTokens": kpi["prompt_tokens"],
                "completionTokens": kpi["completion_tokens"], "overheadMs": _num(kpi["overhead_ms"]),
            },
            "byModel": by_model, "byRole": by_role, "byTask": by_task,
            "nodes": [{"node": r["node"], "avgMs": float(r["avg_ms"]), "samples": r["samples"]} for r in nodes],
            "attempts": [{"outcome": r["outcome"], "n": r["n"]} for r in attempts],
            "internal": [
                {"role": r["role"] or "(none)", "calls": r["calls"], "cost": float(r["cost"]),
                 "shadow": float(r["shadow"]), "avgMs": _num(r["avg_ms"])}
                for r in internal
            ],
            "recent": [
                {"at": iso(r["created_at"]), "role": r["node_role"], "task": r["task_type"], "model": r["model"],
                 "status": r["status"], "attempts": r["attempt_count"], "latencyMs": _num(r["latency_ms"]),
                 "ttftMs": _num(r["ttft_ms"]), "tokens": int(r["tokens"]), "shadow": float(r["shadow"]),
                 "runId": r["run_id"], "reason": r["route_reason"]}
                for r in recent
            ],
            "policy": {"version": policy["version"], "createdAt": iso(policy["created_at"])} if policy else None,
        }

    # ---- logs -------------------------------------------------------------------------------------

    @staticmethod
    def _log_where(f: dict[str, Any], user_id: str | None) -> tuple[sql.Composable, list[Any]]:
        """Every filter becomes one WHERE clause over the `log_stream` view."""
        mine, mine_params = _scope(user_id)
        parts: list[LiteralString] = ["ts > now() - make_interval(hours => %s)"]
        params: list[Any] = [f["hours"]]
        if f["sources"]:
            parts.append("source = ANY(%s)")
            params.append(list(f["sources"]))
        if f["minSeverity"]:
            parts.append("severity = ANY(%s)")
            params.append(severities_at_least(f["minSeverity"]))
        if f["kinds"]:
            parts.append("kind = ANY(%s)")
            params.append(list(f["kinds"]))
        if f["q"]:
            like = "%" + f["q"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            parts.append("(message ILIKE %s OR tags::text ILIKE %s OR fingerprint ILIKE %s)")
            params += [like, like, like]
        if f["run"]:
            parts.append("run_id = %s")
            params.append(f["run"])
        if f["span"]:
            parts.append("span_id = %s")
            params.append(f["span"])
        for t in f["tags"]:
            parts.append("tags ->> %s = %s")
            params += [t["key"], t["value"]]
        # The scope clause comes last in the text, so its parameter goes last too.
        return sql.SQL("{} {}").format(sql.SQL(" AND ".join(parts)), mine), [*params, *mine_params]

    @staticmethod
    def _log_row(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": r["id"], "ts": iso(r["ts"]), "source": r["source"], "kind": r["kind"], "severity": r["severity"],
            "message": r["message"], "tags": r["tags"] or {}, "runId": r["run_id"], "spanId": r["span_id"],
            "fingerprint": r["fingerprint"], "payload": r["payload"],
        }

    async def search_logs(self, f: dict[str, Any], user_id: str | None, limit: int) -> list[dict[str, Any]]:
        where, params = self._log_where(f, user_id)
        rows = await self._all(
            sql.SQL(
                "SELECT id, ts, source, kind, severity, message, tags, run_id, span_id, fingerprint, payload "
                "FROM log_stream WHERE {w} ORDER BY ts DESC LIMIT %s OFFSET %s"
            ).format(w=where),
            [*params, limit, (f["page"] - 1) * limit],
        )
        return [self._log_row(r) for r in rows]

    async def count_logs(self, f: dict[str, Any], user_id: str | None, cap: int) -> int:
        where, params = self._log_where(f, user_id)
        r = await self._one(
            sql.SQL("SELECT count(*)::int AS n FROM (SELECT 1 FROM log_stream WHERE {w} LIMIT %s) t").format(w=where),
            [*params, cap + 1],
        )
        return min(r["n"] if r else 0, cap + 1)

    async def log_facets(self, f: dict[str, Any], user_id: str | None) -> dict[str, Any]:
        where, params = self._log_where(f, user_id)

        async def facet(col: LiteralString, limit: int | None = None) -> list[dict[str, Any]]:
            q = sql.SQL("SELECT {c} AS value, count(*)::int AS n FROM log_stream WHERE {w} GROUP BY 1 ORDER BY n DESC").format(
                c=sql.Identifier(col), w=where
            )
            if limit:
                q = sql.SQL("{} LIMIT {}").format(q, sql.Literal(limit))
            return [{"value": r["value"], "n": r["n"]} for r in await self._all(q, params)]

        tag_rows = await self._all(
            sql.SQL(
                "SELECT t.key, t.value, count(*)::int AS n FROM "
                "(SELECT tags FROM log_stream WHERE {w} ORDER BY ts DESC LIMIT 20000) s, "
                "jsonb_each_text(s.tags) AS t WHERE t.key = ANY(%s) GROUP BY 1, 2 ORDER BY n DESC"
            ).format(w=where),
            [*params, list(FACET_TAG_KEYS)],
        )
        tags: dict[str, list[dict[str, Any]]] = {}
        for r in tag_rows:
            bucket = tags.setdefault(r["key"], [])
            if len(bucket) < 8:
                bucket.append({"value": r["value"], "n": r["n"]})
        return {
            "sources": await facet("source"), "severities": await facet("severity"),
            "kinds": await facet("kind", 12), "tags": tags,
        }

    async def get_run(self, run_id: str, user_id: str | None) -> dict[str, Any]:
        mine, mine_p = _scope(user_id)
        mine_s, mine_s_p = _scope(user_id, "s.user_id")
        spans = await self._all(
            sql.SQL(
                "SELECT span_id, parent_span_id, origin, node_role, task_type, model, provider, status, error_class, "
                "latency_ms, ttft_ms, attempt_count, coalesce(prompt_tokens, 0) + coalesce(completion_tokens, 0) AS tokens, "
                "coalesce(shadow_cost_usd, 0)::float AS shadow, route_reason, policy_version, created_at, node_timings "
                "FROM spans WHERE run_id = %s {mine} ORDER BY created_at"
            ).format(mine=mine),
            [run_id, *mine_p],
        )
        attempts = await self._all(
            sql.SQL(
                "SELECT a.span_id, a.attempt_no, a.model, a.outcome_class, a.latency_ms, a.error, a.fallback_reason "
                "FROM attempts a JOIN spans s USING (span_id) WHERE s.run_id = %s {mine} ORDER BY a.span_id, a.attempt_no"
            ).format(mine=mine_s),
            [run_id, *mine_s_p],
        )
        events = await self._all(
            sql.SQL(
                "SELECT id, ts, source, kind, severity, message, tags, run_id, span_id, fingerprint, payload "
                "FROM log_stream WHERE run_id = %s {mine} ORDER BY ts, id LIMIT 1000"
            ).format(mine=mine),
            [run_id, *mine_p],
        )
        return {
            "spans": [
                {
                    "spanId": r["span_id"], "parentSpanId": r["parent_span_id"], "origin": r["origin"],
                    "role": r["node_role"], "task": r["task_type"], "model": r["model"], "provider": r["provider"],
                    "status": r["status"], "errorClass": r["error_class"], "latencyMs": _num(r["latency_ms"]),
                    "ttftMs": _num(r["ttft_ms"]), "attempts": r["attempt_count"], "tokens": int(r["tokens"]),
                    "shadowCost": float(r["shadow"]), "routeReason": r["route_reason"],
                    "policyVersion": r["policy_version"], "endedAt": iso(r["created_at"]),
                    "nodeTimings": r["node_timings"] or {},
                }
                for r in spans
            ],
            "attempts": [
                {
                    "spanId": r["span_id"], "n": r["attempt_no"], "model": r["model"], "outcome": r["outcome_class"],
                    "latencyMs": _num(r["latency_ms"]), "error": r["error"], "fallbackReason": r["fallback_reason"],
                }
                for r in attempts
            ],
            "events": [self._log_row(r) for r in events],
        }

    async def export_spans(self, hours: int, user_id: str | None) -> list[dict[str, Any]]:
        mine, mine_p = _scope(user_id)
        rows = await self._all(
            sql.SQL(
                "SELECT created_at, run_id, span_id, parent_span_id, origin, node_role, task_type, complexity, "
                "provider, model, family, route_reason, policy_version, status, error_class, http_status, "
                "prompt_tokens, completion_tokens, cached_tokens, cost_usd::float AS cost_usd, "
                "shadow_cost_usd::float AS shadow_cost_usd, latency_ms, ttft_ms, attempt_count, fell_back, "
                "overhead_ms, api_key_name, node_timings FROM spans "
                "WHERE created_at > now() - make_interval(hours => %s) {mine} ORDER BY created_at DESC LIMIT %s"
            ).format(mine=mine),
            [hours, *mine_p, EXPORT_MAX_ROWS],
        )
        return [{**r, "created_at": iso(r["created_at"])} for r in rows]
