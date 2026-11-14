from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .config import DATA_DIR, Settings
from .events import LogEvent, SpanEvent

log = logging.getLogger("gateway.sink")


class EventSink(Protocol):
    persists_policy: bool

    async def write_many(self, events: list[SpanEvent]) -> None: ...
    async def write_logs(self, logs: list[LogEvent]) -> None: ...
    async def get_span_model(self, span_id: str) -> tuple[str, str] | None: ...
    async def load_active_policy(self) -> tuple[int, dict[str, Any]] | None: ...
    async def save_policy(
        self, body: dict[str, Any], status: str = "active", created_by: str = "manual",
        parent_version: int | None = None,
    ) -> int: ...
    # API keys
    async def get_api_key(self, key_hash: str) -> dict[str, Any] | None: ...
    async def touch_api_key(self, key_id: str) -> None: ...
    async def create_api_key(self, record: dict[str, Any]) -> None: ...
    async def list_api_keys(self) -> list[dict[str, Any]]: ...
    async def set_api_key_status(self, key_id: str, status: str) -> bool: ...
    async def set_api_key_expiry(self, key_id: str, expires_at: datetime | None) -> bool: ...
    async def close(self) -> None: ...


class NullSink:
    persists_policy = False

    async def write_many(self, events: list[SpanEvent]) -> None:
        return None

    async def write_logs(self, logs: list[LogEvent]) -> None:
        return None

    async def get_span_model(self, span_id: str) -> tuple[str, str] | None:
        return None

    async def load_active_policy(self) -> tuple[int, dict[str, Any]] | None:
        return None

    async def save_policy(self, body, status="active", created_by="manual", parent_version=None) -> int:
        raise NotImplementedError

    async def get_api_key(self, key_hash: str) -> dict[str, Any] | None:
        return None

    async def touch_api_key(self, key_id: str) -> None:
        return None

    async def create_api_key(self, record: dict[str, Any]) -> None:
        raise NotImplementedError

    async def list_api_keys(self) -> list[dict[str, Any]]:
        return []

    async def set_api_key_status(self, key_id: str, status: str) -> bool:
        return False

    async def set_api_key_expiry(self, key_id: str, expires_at: datetime | None) -> bool:
        return False

    async def close(self) -> None:
        return None


class MemorySink(NullSink):
    """Bounded in-memory store for local dev and tests."""

    def __init__(self, maxlen: int = 5000) -> None:
        self.events: deque[SpanEvent] = deque(maxlen=maxlen)
        self.logs: deque[LogEvent] = deque(maxlen=maxlen)
        self.api_keys: dict[str, dict[str, Any]] = {}  # key_hash -> record

    async def write_many(self, events: list[SpanEvent]) -> None:
        self.events.extend(events)

    async def write_logs(self, logs: list[LogEvent]) -> None:
        self.logs.extend(logs)

    async def get_span_model(self, span_id: str) -> tuple[str, str] | None:
        for e in reversed(self.events):
            if e.span_id == span_id and e.model and e.family:
                return e.model, e.family
        return None

    async def get_api_key(self, key_hash: str) -> dict[str, Any] | None:
        rec = self.api_keys.get(key_hash)
        return dict(rec) if rec else None

    async def touch_api_key(self, key_id: str) -> None:
        for rec in self.api_keys.values():
            if rec["id"] == key_id:
                rec["last_used_at"] = datetime.now(UTC)

    async def create_api_key(self, record: dict[str, Any]) -> None:
        rec = {"status": "active", "allowed_models": None, "rate_limit_per_min": None,
               "expires_at": None, "rotated_from": None, "last_used_at": None,
               "created_at": datetime.now(UTC), **record}
        self.api_keys[rec["key_hash"]] = rec

    async def list_api_keys(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.api_keys.values()]

    async def set_api_key_status(self, key_id: str, status: str) -> bool:
        for rec in self.api_keys.values():
            if rec["id"] == key_id:
                rec["status"] = status
                return True
        return False

    async def set_api_key_expiry(self, key_id: str, expires_at: datetime | None) -> bool:
        for rec in self.api_keys.values():
            if rec["id"] == key_id:
                rec["expires_at"] = expires_at
                return True
        return False


class PostgresSink:
    persists_policy = True

    def __init__(self, dsn: str) -> None:
        from psycopg_pool import AsyncConnectionPool

        # prepare_threshold=None keeps this safe behind pgbouncer-style poolers (e.g. Neon).
        self._pool = AsyncConnectionPool(
            dsn, min_size=1, max_size=5, open=False, kwargs={"prepare_threshold": None}
        )

    async def open(self, migrate: bool = True) -> None:
        await self._pool.open()
        if migrate:
            sql = await asyncio.to_thread(Path(DATA_DIR / "schema.sql").read_text, encoding="utf-8")
            async with self._pool.connection() as conn:
                await conn.execute(sql)

    # ---- spans / attempts ---------------------------------------------------

    async def write_many(self, events: list[SpanEvent]) -> None:
        from psycopg.types.json import Jsonb

        if not events:
            return
        async with self._pool.connection() as conn, conn.transaction():
            for e in events:
                await conn.execute(
                    """INSERT INTO runs (run_id, api_key_name, origin)
                           VALUES (%s, %s, %s)
                           ON CONFLICT (run_id) DO UPDATE SET last_span_at = now()""",
                    (e.run_id, e.api_key_name, e.origin),
                )
                ts = datetime.fromtimestamp(e.created_at, tz=UTC)
                await conn.execute(
                    """INSERT INTO spans (
                             span_id, run_id, parent_span_id, origin, node_role, task_type, complexity,
                             profile, profile_confidence, policy_version, policy_status_served,
                             provider, model, family, route_reason, route_relaxed, stream, status,
                             error_class, http_status, prompt_tokens, completion_tokens, cached_tokens,
                             cost_usd, shadow_cost_usd, latency_ms, ttft_ms, attempt_count, fell_back,
                             node_timings, overhead_ms, idempotency_key, api_key_name, created_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                   %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (span_id) DO NOTHING""",
                    (
                        e.span_id, e.run_id, e.parent_span_id, e.origin, e.node_role, e.task_type,
                        e.complexity, Jsonb(e.profile) if e.profile is not None else None,
                        e.profile_confidence, e.policy_version, e.policy_status_served, e.provider,
                        e.model, e.family, e.route_reason, e.route_relaxed, e.stream, e.status,
                        e.error_class, e.http_status, e.prompt_tokens, e.completion_tokens,
                        e.cached_tokens, e.cost_usd, e.shadow_cost_usd,
                        None if e.latency_ms is None else int(e.latency_ms),
                        None if e.ttft_ms is None else int(e.ttft_ms),
                        e.attempt_count, e.fell_back, Jsonb(e.node_timings), e.overhead_ms,
                        e.idempotency_key, e.api_key_name, ts,
                    ),
                )
                for a in e.attempts:
                    await conn.execute(
                        """INSERT INTO attempts (span_id, attempt_no, model, provider, outcome_class,
                                                     latency_ms, ttft_ms, error, fallback_reason)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT DO NOTHING""",
                        (
                            e.span_id, a.attempt_no, a.model, a.provider, a.outcome_class,
                            None if a.latency_ms is None else int(a.latency_ms),
                            None if a.ttft_ms is None else int(a.ttft_ms),
                            a.error, a.fallback_reason,
                        ),
                    )

    async def write_logs(self, logs: list[LogEvent]) -> None:
        from psycopg.types.json import Jsonb

        if not logs:
            return
        async with self._pool.connection() as conn, conn.transaction():
            for e in logs:
                await conn.execute(
                    """INSERT INTO log_events (ts, source, kind, severity, message, tags, run_id,
                                                   span_id, trace_id, fingerprint, payload, external_id)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT DO NOTHING""",
                    (
                        datetime.fromtimestamp(e.ts, tz=UTC), e.source, e.kind, e.severity,
                        e.message, Jsonb(e.tags), e.run_id, e.span_id, e.trace_id, e.fingerprint,
                        Jsonb(e.payload) if e.payload is not None else None, e.external_id,
                    ),
                )

    async def get_span_model(self, span_id: str) -> tuple[str, str] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT model, family FROM spans WHERE span_id = %s AND model IS NOT NULL", (span_id,)
            )
            row = await cur.fetchone()
        return (row[0], row[1]) if row else None

    # ---- routing policy -----------------------------------------------------

    async def load_active_policy(self) -> tuple[int, dict[str, Any]] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT version, body FROM routing_policy WHERE status = 'active' "
                "ORDER BY version DESC LIMIT 1"
            )
            row = await cur.fetchone()
        return (row[0], row[1]) if row else None

    async def save_policy(
        self, body: dict[str, Any], status: str = "active", created_by: str = "manual",
        parent_version: int | None = None,
    ) -> int:
        from psycopg.types.json import Jsonb

        activated = datetime.now(UTC) if status == "active" else None
        async with self._pool.connection() as conn, conn.transaction():
            if status == "active":
                await conn.execute(
                    "UPDATE routing_policy SET status = 'retired' WHERE status = 'active'"
                )
            cur = await conn.execute(
                """INSERT INTO routing_policy (status, body, parent_version, created_by, activated_at)
                       VALUES (%s, %s, %s, %s, %s) RETURNING version""",
                (status, Jsonb(body), parent_version, created_by, activated),
            )
            row = await cur.fetchone()
        assert row is not None
        return row[0]

    # ---- API keys -----------------------------------------------------------

    async def get_api_key(self, key_hash: str) -> dict[str, Any] | None:
        from psycopg.rows import dict_row

        async with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM api_keys WHERE key_hash = %s", (key_hash,))
            return await cur.fetchone()

    async def touch_api_key(self, key_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute("UPDATE api_keys SET last_used_at = now() WHERE id = %s", (key_id,))

    async def create_api_key(self, record: dict[str, Any]) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """INSERT INTO api_keys (id, name, key_prefix, key_hash, status, allowed_models,
                                         rate_limit_per_min, expires_at, rotated_from)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    record["id"], record["name"], record["key_prefix"], record["key_hash"],
                    record.get("status", "active"), record.get("allowed_models"),
                    record.get("rate_limit_per_min"), record.get("expires_at"), record.get("rotated_from"),
                ),
            )

    async def list_api_keys(self) -> list[dict[str, Any]]:
        from psycopg.rows import dict_row

        async with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, name, key_prefix, status, allowed_models, rate_limit_per_min, "
                "expires_at, rotated_from, created_at, last_used_at FROM api_keys ORDER BY created_at"
            )
            return await cur.fetchall()

    async def set_api_key_status(self, key_id: str, status: str) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute("UPDATE api_keys SET status = %s WHERE id = %s", (status, key_id))
            return cur.rowcount > 0

    async def set_api_key_expiry(self, key_id: str, expires_at: datetime | None) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute("UPDATE api_keys SET expires_at = %s WHERE id = %s", (expires_at, key_id))
            return cur.rowcount > 0

    async def close(self) -> None:
        await self._pool.close()


async def make_sink(settings: Settings) -> EventSink:
    kind = settings.sink
    if kind == "auto":
        kind = "postgres" if settings.database_url else "memory"
    if kind == "postgres":
        if not settings.database_url:
            raise RuntimeError("GATEWAY_SINK=postgres requires DATABASE_URL")
        sink = PostgresSink(settings.database_url)
        await sink.open(migrate=settings.auto_migrate)
        return sink
    if kind == "null":
        return NullSink()
    return MemorySink()
