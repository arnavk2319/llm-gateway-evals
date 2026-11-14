import httpx
import pytest
import sentry_sdk
from conftest import chat
from sentry_sdk.transport import Transport

from app import observability
from app.config import Settings
from app.main import create_app
from app.sinks import MemorySink


@pytest.fixture(autouse=True)
def reset_sentry():
    yield
    sentry_sdk.init()  # back to a disabled client between tests


class Capture(Transport):
    """Collects the events the SDK would send, without any network."""

    def __init__(self):
        super().__init__({})
        self.events: list[dict] = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.type == "event":
                self.events.append(item.payload.json)


async def gateway_with_sentry(cap: Capture, **cfg):
    sink = MemorySink()
    settings = Settings(mock_latency_scale=0.0, sink="memory", database_url=None, api_keys="",
                        sentry_dsn="https://key@example.invalid/1", **cfg)
    app = create_app(settings, sink=sink, sentry_overrides={"transport": cap})
    return app, sink


async def test_upstream_failure_reaches_sentry_with_correlation_tags():
    cap = Capture()
    app, sink = await gateway_with_sentry(cap)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://g") as c:
            r = await c.post("/v1/chat/completions", json=chat(),
                             headers={"x-mock-behavior": "*=error_500", "x-node-role": "implementer"})
    assert r.status_code == 502
    assert len(cap.events) == 1
    ev = cap.events[0]
    assert ev["message"].startswith("502 server_error") or "server_error" in ev["message"]
    tags = ev["tags"]
    assert tags["run_id"] == r.headers["x-gateway-run-id"]
    assert tags["span_id"] == r.headers["x-gateway-call-id"]
    assert tags["error_class"] == "server_error" and tags["node_role"] == "implementer"
    assert tags["kind"] == "request_failed" and tags["source"] == "gateway"
    assert ev["fingerprint"] == ["failed:server_error:mock"]

    # the gateway's own log row carries the Sentry event id: a two-way link
    failed = [l for l in sink.logs if l.kind == "request_failed"][0]
    assert failed.tags["sentry_event_id"] == ev["event_id"]


async def test_routine_fallbacks_and_client_errors_are_not_sent_to_sentry():
    cap = Capture()
    app, _ = await gateway_with_sentry(cap)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://g") as c:
            await c.post("/v1/chat/completions", json=chat(),
                         headers={"x-mock-behavior": "mock/small=error_500"})  # recovered by fallback
            await c.post("/v1/chat/completions", json={"messages": []})  # 400
    assert cap.events == []


async def test_provider_key_rejection_and_open_circuit_are_reported():
    cap = Capture()
    app, _ = await gateway_with_sentry(cap)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://g") as c:
            await c.post("/v1/chat/completions", json=chat(),
                         headers={"x-mock-behavior": "mock/small=error_401"})
    # With one provider, a rejected key rules out every model, so the request itself fails too.
    kinds = sorted(e["tags"]["kind"] for e in cap.events)
    assert kinds == ["attempt_failed", "circuit_opened", "request_failed"]
    assert all(e["tags"]["provider"] == "mock" for e in cap.events)


def test_events_are_scrubbed_of_credentials_and_bodies():
    event = {"request": {"data": {"messages": ["secret prompt"]}, "cookies": {"a": "b"},
                         "query_string": "x=1",
                         "headers": {"Authorization": "Bearer gw_live_x", "X-Api-Key": "k", "Accept": "*/*"}}}
    out = observability.scrub_event(event)
    assert out["request"] == {"headers": {"Accept": "*/*"}}


def test_nothing_happens_without_a_dsn():
    assert observability.init_sentry(Settings(sentry_dsn=None, database_url=None)) is False
    assert observability.capture("x", tags={}) is None
