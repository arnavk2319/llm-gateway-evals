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
