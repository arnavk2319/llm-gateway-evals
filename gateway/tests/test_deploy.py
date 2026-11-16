"""Deployment readiness: what the Vercel / Neon / Sentry setup relies on."""

import asyncio
import tomllib
from pathlib import Path

import pytest
from conftest import chat, make_gateway

from app import observability
from app.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_vercel_entrypoint_resolves_to_the_asgi_app():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    module, _, attr = cfg["tool"]["vercel"]["entrypoint"].partition(":")
    mod = __import__(module, fromlist=[attr])
    assert callable(getattr(mod, attr))
    assert (ROOT / (module.replace(".", "/") + ".py")).exists()


def test_vercel_json_targets_the_entrypoint_file():
    import json

    cfg = json.loads((ROOT / "vercel.json").read_text())
    assert "app/main.py" in cfg["functions"]


def test_inline_event_writes_default_on_for_vercel_only(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    assert Settings().emit_events_inline is False
    monkeypatch.setenv("VERCEL", "1")
    assert Settings().emit_events_inline is True
    assert Settings(emit_inline="off").emit_events_inline is False
    monkeypatch.delenv("VERCEL")
    assert Settings(emit_inline="on").emit_events_inline is True


async def test_inline_mode_has_events_stored_by_the_time_the_response_returns():
    async with make_gateway(emit_inline="on") as (c, sink):
        r = await c.post("/v1/chat/completions", json=chat())
        assert r.status_code == 200
        assert len(sink.events) == 1  # no settle(): written before the response was returned


async def test_inline_mode_writes_error_events_before_returning():
    async with make_gateway(emit_inline="on") as (c, sink):
        r = await c.post("/v1/chat/completions", json=chat(), headers={"x-mock-behavior": "*=error_500"})
        assert r.status_code >= 500
        assert any(line.kind == "request_failed" for line in sink.logs)


async def test_inline_mode_streams_write_events_before_the_stream_closes():
    async with make_gateway(emit_inline="on") as (c, sink):
        async with c.stream("POST", "/v1/chat/completions", json=chat(stream=True)) as r:
            async for _ in r.aiter_bytes():
                pass
        assert len(sink.events) == 1


async def test_sentry_is_flushed_before_a_serverless_response_returns(monkeypatch):
    flushed = []
    monkeypatch.setattr(observability, "flush", lambda timeout=2.0: flushed.append(timeout))
    async with make_gateway(emit_inline="on") as (c, _):
        await c.post("/v1/chat/completions", json=chat())
    assert flushed, "flush() was never called"


def test_flush_is_a_no_op_without_sentry():
    observability.flush()  # must not raise


@pytest.mark.skipif(not __import__("os").environ.get("TEST_DATABASE_URL"), reason="needs Postgres")
async def test_concurrent_cold_starts_can_all_migrate_the_same_database():
    import os

    from app.sinks import PostgresSink


    sinks = [PostgresSink(os.environ["TEST_DATABASE_URL"]) for _ in range(6)]
    try:
        await asyncio.gather(*(s.open(migrate=True) for s in sinks))  # would race without the lock
    finally:
        await asyncio.gather(*(s.close() for s in sinks))


def test_render_is_a_long_lived_host_that_still_requires_auth(monkeypatch):
    """Render runs one process, so events are written in the background; but a public host is never open."""
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    assert Settings(api_keys="").auth_required is False
    monkeypatch.setenv("RENDER", "true")
    cfg = Settings(api_keys="")
    assert cfg.auth_required is True
    assert cfg.emit_events_inline is False
    assert cfg.debug_enabled is False


@pytest.mark.skipif(not __import__("os").environ.get("TEST_DATABASE_URL"), reason="needs Postgres")
async def test_migration_replaces_a_log_view_with_a_different_column_set():
    """A release that changes the view's columns must not break startup of one that has not (and vice versa)."""
    import os

    from app.sinks import PostgresSink

    sink = PostgresSink(os.environ["TEST_DATABASE_URL"])
    await sink.open(migrate=True)
    try:
        async with sink.pool.connection() as conn:
            await conn.execute("DROP VIEW IF EXISTS log_stream")
            await conn.execute("CREATE VIEW log_stream AS SELECT 1 AS unrelated, 'x'::text AS other")
    finally:
        await sink.close()
    again = PostgresSink(os.environ["TEST_DATABASE_URL"])
    await again.open(migrate=True)  # must not raise "cannot drop columns from view"
    try:
        async with again.pool.connection() as conn:
            cur = await conn.execute("SELECT user_id FROM log_stream LIMIT 1")
            assert cur.description[0].name == "user_id"
    finally:
        await again.close()
