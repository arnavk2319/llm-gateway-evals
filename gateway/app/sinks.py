from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .config import DATA_DIR, Settings
from .events import SpanEvent

log = logging.getLogger("gateway.sink")


class EventSink(Protocol):
    persists_policy: bool

    async def write_many(self, events: list[SpanEvent]) -> None: ...
    async def get_span_model(self, span_id: str) -> tuple[str, str] | None: ...
    async def load_active_policy(self) -> tuple[int, dict[str, Any]] | None: ...
    async def save_policy(
        self, body: dict[str, Any], status: str = "active", created_by: str = "manual",
        parent_version: int | None = None,
    ) -> int: ...
    async def close(self) -> None: ...


class NullSink:
    persists_policy = False

    async def write_many(self, events: list[SpanEvent]) -> None:
        return None

    async def get_span_model(self, span_id: str) -> tuple[str, str] | None:
        return None

    async def load_active_policy(self) -> tuple[int, dict[str, Any]] | None:
        return None

    async def save_policy(self, body, status="active", created_by="manual", parent_version=None) -> int:
        raise NotImplementedError

    async def close(self) -> None:
        return None


class MemorySink(NullSink):
    """Bounded in-memory store for local dev and tests."""

    def __init__(self, maxlen: int = 5000) -> None:
        self.events: deque[SpanEvent] = deque(maxlen=maxlen)

    async def write_many(self, events: list[SpanEvent]) -> None:
        self.events.extend(events)

    async def get_span_model(self, span_id: str) -> tuple[str, str] | None:
        for e in reversed(self.events):
            if e.span_id == span_id and e.model and e.family:
                return e.model, e.family
        return None


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
            sql = Path(DATA_DIR / "schema.sql").read_text(encoding="utf-8")
            async with self._pool.connection() as conn:
                await conn.execute(sql)

    async def write_many(self, events: list[SpanEvent]) -> None:
        from psycopg.types.json import Jsonb

        if not events:
            return
        async with self._pool.connection() as conn:
            async with conn.transaction():
                for e in events:
                    await conn.execute(
                        """INSERT INTO runs (run_id, api_key_name, origin)
                           VALUES (%s, %s, %s)
                           ON CONFLICT (run_id) DO UPDATE SET last_span_at = now()""",
                        (e.run_id, e.api_key_name, e.origin),
                    )
                    ts = datetime.fromtimestamp(e.created_at, tz=timezone.utc)
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

    async def get_span_model(self, span_id: str) -> tuple[str, str] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT model, family FROM spans WHERE span_id = %s AND model IS NOT NULL", (span_id,)
            )
            row = await cur.fetchone()
        return (row[0], row[1]) if row else None

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

        activated = datetime.now(timezone.utc) if status == "active" else None
        async with self._pool.connection() as conn:
            async with conn.transaction():
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
        return row[0]

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
