"""Integration test against a real Postgres. Skipped unless TEST_DATABASE_URL is set.

    docker compose up -d postgres
    TEST_DATABASE_URL=postgresql://gateway:gateway@localhost:5433/gateway uv run pytest tests/test_postgres.py
"""

import os
import uuid

import pytest

from app.lineage import Lineage
from app.sinks import PostgresSink

from conftest import chat, make_gateway

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set"
)


async def test_spans_attempts_policy_and_lineage_persist_in_postgres():
    dsn = os.environ["TEST_DATABASE_URL"]
    sink = PostgresSink(dsn)
    await sink.open(migrate=True)
    run = "run_pg_" + uuid.uuid4().hex[:8]
    try:
        async with make_gateway(sink_obj=sink, sink="postgres", database_url=dsn) as (c, _):
            r1 = await c.post(
                "/v1/chat/completions", json=chat("write it", model="mock/large"),
                headers={"x-run-id": run, "x-node-role": "implementer"},
            )
            parent = r1.headers["x-gateway-call-id"]
            r2 = await c.post(
                "/v1/chat/completions", json=chat("review it"),
                headers={"x-run-id": run, "x-node-role": "reviewer", "x-parent-call-id": parent,
                         "x-mock-behavior": "mock/medium=error_500"},
            )
            assert r2.status_code == 200

            async with sink._pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT node_role, model, status, attempt_count, fell_back, policy_version, "
                    "node_timings ? 'route' FROM spans WHERE run_id = %s ORDER BY created_at", (run,)
                )
                rows = await cur.fetchall()
                assert [r[0] for r in rows] == ["implementer", "reviewer"]
                assert rows[0][1] == "mock/large" and rows[1][2] == "ok"
                assert rows[1][3] >= 2 and rows[1][4] is True
                assert rows[0][5] is not None and rows[0][6] is True

                cur = await conn.execute(
                    "SELECT outcome_class FROM attempts WHERE span_id = %s ORDER BY attempt_no",
                    (r2.headers["x-gateway-call-id"],),
                )
                assert [r[0] for r in await cur.fetchall()][0] in {"server_error", "ok"}

                cur = await conn.execute("SELECT count(*) FROM routing_policy WHERE status = 'active'")
                assert (await cur.fetchone())[0] == 1

            # Lineage survives a cold in-process cache by falling back to the database.
            ref = await Lineage(sink).get(parent)
            assert ref is not None and ref.model_id == "mock/large" and ref.family == "mock-c"
    finally:
        await sink.close()


async def test_api_keys_log_events_and_the_unified_stream_in_postgres():
    import asyncio
    from datetime import datetime, timedelta, timezone

    from app.auth import generate_key, hash_key
    from app.events import LogEvent

    dsn = os.environ["TEST_DATABASE_URL"]
    sink = PostgresSink(dsn)
    await sink.open(migrate=True)
    run = "run_pgk_" + uuid.uuid4().hex[:8]
    kid = "key_" + uuid.uuid4().hex[:8]
    key = generate_key()
    try:
        await sink.create_api_key({
            "id": kid, "name": "pg-test", "key_prefix": key[:12], "key_hash": hash_key(key),
            "allowed_models": ["mock/small", "mock/medium"], "rate_limit_per_min": 100,
            "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
        })
        async with make_gateway(sink_obj=sink, sink="postgres", database_url=dsn,
                                auth="required", auth_cache_ttl_s=0) as (c, _):
            ok = await c.post(
                "/v1/chat/completions", json=chat("hello"),
                headers={"authorization": f"Bearer {key}", "x-run-id": run,
                         "x-mock-behavior": "mock/small=error_500"},
            )
            assert ok.status_code == 200 and ok.headers["x-gateway-model"] == "mock/medium"
            bad = await c.post(
                "/v1/chat/completions", json=chat("hello"),
                headers={"authorization": "Bearer gw_live_wrong", "x-run-id": run},
            )
            assert bad.status_code == 401
            await asyncio.sleep(0.2)  # last_used_at is touched in the background

            rec = await sink.get_api_key(hash_key(key))
            assert rec["last_used_at"] is not None and rec["allowed_models"] == ["mock/small", "mock/medium"]

            async with sink._pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT source, kind, severity, tags->>'model', tags->>'key' "
                    "FROM log_stream WHERE run_id = %s ORDER BY ts", (run,)
                )
                rows = await cur.fetchall()
            kinds = {r[1] for r in rows}
            assert kinds == {"request", "attempt_failed", "fallback", "request_rejected"}
            assert all(r[0] == "gateway" for r in rows)
            assert [r for r in rows if r[1] == "attempt_failed"][0][3] == "mock/small"
            assert [r for r in rows if r[1] == "request"][0][4] == "pg-test"

            # the rejected call must not have created a span
            async with sink._pool.connection() as conn:
                cur = await conn.execute("SELECT count(*) FROM spans WHERE run_id = %s", (run,))
                assert (await cur.fetchone())[0] == 1

            # revocation takes effect (cache ttl is 0 here)
            await sink.set_api_key_status(kid, "revoked")
            r = await c.post("/v1/chat/completions", json=chat("x"),
                             headers={"authorization": f"Bearer {key}"})
            assert r.status_code == 401

            # ingestion from outside is idempotent on (source, external_id)
            ext = LogEvent(source="vercel", kind="runtime_log", severity="info", message="hello",
                           external_id="vc_" + uuid.uuid4().hex, tags={"deployment": "dpl_1"})
            await sink.write_logs([ext, ext])
            async with sink._pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT count(*) FROM log_events WHERE external_id = %s", (ext.external_id,)
                )
                assert (await cur.fetchone())[0] == 1
    finally:
        try:
            await sink.close()  # the app already closed it; closing twice is harmless
        except Exception:
            pass
