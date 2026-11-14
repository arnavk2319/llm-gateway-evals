import copy
from types import SimpleNamespace

import httpx
import pytest

from app.config import DATA_DIR
from app.policy import Policy, load_policy_file
from app.profile import TaskProfile
from app.providers.health import ProviderHealth
from app.providers.openai_compat import OpenAICompatProvider
from app.router import select_route

from conftest import chat, make_gateway, settle, write_policy

POLICY = load_policy_file(DATA_DIR / "seed_policy.yaml")
SPEC = POLICY.models[0]
OK_BODY = {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
           "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


# ---- circuit breaker ------------------------------------------------------------

class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_circuit_opens_after_repeated_failures_and_closes_on_cooldown():
    clock = Clock()
    h = ProviderHealth(failure_threshold=3, window_s=60, open_s=30, clock=clock)
    assert h.record("p", "server_error") is None
    assert h.record("p", "timeout") is None
    assert "3 failures" in h.record("p", "connection")
    assert h.is_open("p") and h.open_providers() == {"p"}

    clock.t += 31
    assert not h.is_open("p")  # half-open
    assert h.record("p", "server_error") is not None  # one more failure reopens immediately
    assert h.is_open("p")


def test_success_resets_the_failure_count_and_bad_requests_do_not_count():
    h = ProviderHealth(failure_threshold=2, clock=Clock())
    h.record("p", "server_error")
    h.record("p", "ok")
    assert h.record("p", "server_error") is None
    for _ in range(10):
        h.record("p", "client_error")
        h.record("p", "validator_failed")
    assert not h.is_open("p")


def test_auth_error_opens_immediately_and_retry_after_is_capped():
    clock = Clock()
    h = ProviderHealth(auth_open_s=300, max_retry_after_s=60, clock=clock)
    assert "authentication failed" in h.record("a", "auth_error")
    clock.t += 299
    assert h.is_open("a")
    clock.t += 2
    assert not h.is_open("a")

    assert "retry after 60s" in h.record("b", "rate_limit", retry_after_s=9999)
    clock.t += 61
    assert not h.is_open("b")
    assert h.record("c", "rate_limit") is None  # no Retry-After: no cooldown


# ---- routing around open circuits ---------------------------------------------------

def two_provider_policy() -> Policy:
    d = copy.deepcopy(POLICY.model_dump(mode="json"))
    d["providers"]["mock2"] = {"kind": "mock"}
    d["models"] = [m for m in d["models"] if m["provider"] == "mock"] + [
        {"id": "mock2/cheap", "provider": "mock2", "model": "m2", "family": "mock-d", "tier": 1,
         "capabilities": {"tools": True, "json_mode": True, "max_context": 16000},
         "price": {"input_per_1m": 0.01, "output_per_1m": 0.01}, "latency_ms_p50": 100,
         "quality": {"default": 0.9}},
    ]
    return Policy.model_validate(d)


def test_router_skips_providers_with_open_circuits_unless_nothing_else_is_left():
    p = two_provider_policy()
    prof = TaskProfile(task_type="chat", context_tokens=100, expected_output_tokens=100)
    assert select_route(p, prof, env={}).primary.id == "mock2/cheap"
    d = select_route(p, prof, env={}, unhealthy={"mock2"})
    assert d.primary.provider == "mock"
    d = select_route(p, prof, env={}, unhealthy={"mock", "mock2"})
    assert d is not None and "circuit-open:ignored" in d.relaxed


# ---- provider key failover (gateway -> provider auth) --------------------------------

def provider(handler, *envs):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatProvider("p", "http://upstream/v1", list(envs), client)


async def test_a_rejected_provider_key_fails_over_to_the_secondary_and_is_remembered(monkeypatch):
    monkeypatch.setenv("K1", "bad")
    monkeypatch.setenv("K2", "good")
    seen = []

    def handler(req):
        seen.append(req.headers["authorization"])
        return httpx.Response(401, json={"error": "invalid key"}) if seen[-1] == "Bearer bad" \
            else httpx.Response(200, json=OK_BODY)

    prov = provider(handler, "K1", "K2")
    out = await prov.complete(SPEC, {"messages": []}, timeout_s=5, extra={})
    assert out.ok
    assert out.notes == ["api key #1 rejected with HTTP 401", "served using api key #2"]
    assert seen == ["Bearer bad", "Bearer good"]

    seen.clear()
    out = await prov.complete(SPEC, {"messages": []}, timeout_s=5, extra={})
    assert out.ok and seen == ["Bearer good"]  # the bad key is skipped during its cooldown


async def test_all_keys_rejected_is_an_auth_error(monkeypatch):
    monkeypatch.setenv("K1", "a")
    monkeypatch.setenv("K2", "b")
    prov = provider(lambda req: httpx.Response(403, json={"error": "forbidden"}), "K1", "K2")
    out = await prov.complete(SPEC, {"messages": []}, timeout_s=5, extra={})
    assert not out.ok and out.error_class == "auth_error" and len(out.notes) == 2


async def test_unset_secondary_key_is_ignored(monkeypatch):
    monkeypatch.setenv("K1", "only")
    monkeypatch.delenv("K2", raising=False)
    calls = []
    prov = provider(lambda req: (calls.append(1), httpx.Response(401))[1], "K1", "K2")
    out = await prov.complete(SPEC, {"messages": []}, timeout_s=5, extra={})
    assert out.error_class == "auth_error" and len(calls) == 1


async def test_streaming_fails_over_too(monkeypatch):
    monkeypatch.setenv("K1", "bad")
    monkeypatch.setenv("K2", "good")
    sse = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'

    def handler(req):
        if req.headers["authorization"] == "Bearer bad":
            return httpx.Response(401, json={})
        return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})

    out = await provider(handler, "K1", "K2").open_stream(
        SPEC, {"messages": []}, timeout_s=5, first_token_timeout_s=5, extra={})
    assert out.ok and out.stream is not None and "hi" in out.stream.first
    assert "served using api key #2" in out.notes
    await out.stream.aclose()


async def test_retry_after_header_is_parsed(monkeypatch):
    monkeypatch.delenv("K1", raising=False)
    prov = provider(lambda req: httpx.Response(429, headers={"retry-after": "3"}), "K1")
    out = await prov.complete(SPEC, {"messages": []}, timeout_s=5, extra={})
    assert out.error_class == "rate_limit" and out.retry_after_s == 3.0
    prov = provider(lambda req: httpx.Response(429, headers={"retry-after": "soon"}), "K1")
    assert (await prov.complete(SPEC, {"messages": []}, timeout_s=5, extra={})).retry_after_s is None


# ---- the in-request loop -------------------------------------------------------------

async def test_provider_auth_failure_falls_back_to_another_provider_and_opens_the_circuit(tmp_path):
    path = write_policy(tmp_path, two_provider_policy().model_dump(mode="json"))
    async with make_gateway(policy_path=path) as (c, sink):
        r = await c.post("/v1/chat/completions", json=chat(),
                         headers={"x-mock-behavior": "mock2/cheap=error_401"})
        assert r.status_code == 200 and r.headers["x-gateway-model"].startswith("mock/")
        (ev,) = sink.events
        assert [a.outcome_class for a in ev.attempts] == ["auth_error", "ok"]
        kinds = {l.kind: l for l in sink.logs}
        assert kinds["circuit_opened"].tags["provider"] == "mock2"
        assert kinds["attempt_failed"].severity == "error"

        # the circuit is open: the next request never touches the broken provider
        r2 = await c.post("/v1/chat/completions", json=chat(),
                          headers={"x-mock-behavior": "mock2/cheap=error_401"})
        assert r2.status_code == 200 and r2.headers["x-gateway-attempts"] == "1"
        assert not r2.headers["x-gateway-model"].startswith("mock2/")


async def test_pinned_model_with_a_rejected_key_fails_fast():
    async with make_gateway() as (c, sink):
        r = await c.post("/v1/chat/completions", json=chat(model="mock/small"),
                         headers={"x-mock-behavior": "mock/small=error_401"})
        assert r.status_code == 502 and r.json()["error"]["code"] == "auth_error"
        assert r.headers["x-gateway-attempts"] == "1"


async def test_pinned_retries_back_off_with_jitter_and_honor_retry_after(monkeypatch):
    from app.graph import nodes

    delays: list[float] = []

    async def fake_sleep(d):
        if d > 0:
            delays.append(d)

    monkeypatch.setattr(nodes, "asyncio", SimpleNamespace(sleep=fake_sleep))
    async with make_gateway() as (c, _):
        r = await c.post("/v1/chat/completions", json=chat(model="mock/small"),
                         headers={"x-mock-behavior": "mock/small=error_500"})
        assert r.status_code == 502 and r.headers["x-gateway-attempts"] == "3"
        assert len(delays) == 2
        assert 0.1 <= delays[0] <= 0.2 and 0.2 <= delays[1] <= 0.4  # base 0.2, doubling, jittered

        delays.clear()
        await c.post("/v1/chat/completions", json=chat(model="mock/small"),
                     headers={"x-mock-behavior": "mock/small=error_429_ra"})
        assert delays and delays[0] == pytest.approx(2.0)  # Retry-After wins over the small backoff
