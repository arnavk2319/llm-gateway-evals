"""OpenRouter as a source: the data-policy request fields it is always sent, and a 404 from it falling back."""

from __future__ import annotations

import copy
import json

import httpx
import pytest
from conftest import chat, make_gateway, write_policy

from app.config import DATA_DIR
from app.graph.nodes import merge_extra
from app.policy import load_policy_file
from app.providers.base import classify_status


def test_a_provider_s_fixed_fields_win_over_the_caller_s_even_when_nested():
    payload = {"model": "m", "provider": {"data_collection": "allow", "order": ["fast"]}, "temperature": 0.2}
    merge_extra(payload, {"provider": {"data_collection": "deny"}})
    assert payload["provider"] == {"data_collection": "deny", "order": ["fast"]}  # the policy wins, the rest is kept
    assert merge_extra({"model": "m"}, {"provider": {"data_collection": "deny"}})["provider"] == {"data_collection": "deny"}
    assert merge_extra({"model": "m"}, {}) == {"model": "m"}


def test_not_found_and_out_of_credit_are_provider_problems_not_the_callers():
    assert classify_status(404) == "model_unavailable"
    assert classify_status(402) == "auth_error"
    assert classify_status(400) == "client_error" and classify_status(429) == "rate_limit"


def test_the_seed_policy_keeps_openrouter_private_and_free():
    policy = load_policy_file(DATA_DIR / "seed_policy.yaml")
    spec = policy.providers["openrouter"]
    assert spec.request_extra == {"provider": {"data_collection": "deny"}} and spec.sensitive_ok is False
    models = [m for m in policy.models if m.provider == "openrouter"]
    assert models and all(m.free and m.price.input_per_1m == 0 and m.price.output_per_1m == 0 for m in models)
    assert all(m.model.endswith(":free") for m in models)
    assert len({m.family for m in models}) >= 2  # reviewer independence needs more than one family


# ---- through the gateway, with OpenRouter replaced by a fake -----------------------------------------------


def upstream_policy(seed: dict, models: list[str]) -> dict:
    d = copy.deepcopy(seed)
    d["providers"] = {"orx": {
        "kind": "openai_compat", "base_url": "http://orx.test/api/v1", "api_key_env": "ORX_KEY",
        "requires_env": ["ORX_KEY"], "sensitive_ok": False, "request_extra": {"provider": {"data_collection": "deny"}},
    }}
    template = next(m for m in d["models"] if m["id"] == "groq/gpt-oss-20b")
    d["models"] = []
    for i, name in enumerate(models):
        m = copy.deepcopy(template)
        m.update(id=f"orx/{name}", provider="orx", model=f"vendor/{name}:free", tier=2, free=True,
                 latency_ms_p50=100.0 + i, quality={"default": 0.9 - 0.1 * i})
        m["price"] = {"input_per_1m": 0.0, "output_per_1m": 0.0}
        d["models"].append(m)
    return d


@pytest.fixture
def upstream(monkeypatch):
    """Replace the network under the gateway's provider client; `seen` records what OpenRouter would have received."""
    seen: list[dict] = []
    unavailable: set[str] = set()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append({"auth": request.headers.get("authorization"), "url": str(request.url), **body})
        if body["model"] in unavailable:
            return httpx.Response(404, json={"error": {"message": "No endpoints found matching your data policy", "code": 404}})
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "model": body["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        })

    real = httpx.AsyncClient

    def factory(**kw):
        kw.setdefault("transport", httpx.MockTransport(handler))  # the test's own client passes a transport and is untouched
        return real(**kw)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    monkeypatch.setenv("ORX_KEY", "test-key-not-a-secret")
    return seen, unavailable


async def test_every_call_carries_the_data_policy_and_the_callers_cannot_weaken_it(tmp_path, seed_policy_dict, upstream):
    seen, _ = upstream
    path = write_policy(tmp_path, upstream_policy(seed_policy_dict, ["alpha"]))
    async with make_gateway(policy_path=path) as (c, _):
        plain = await c.post("/v1/chat/completions", json=chat("hi"))
        sneaky = await c.post("/v1/chat/completions", json=chat("hi", provider={"data_collection": "allow", "order": ["x"]}))
    assert plain.status_code == 200 and sneaky.status_code == 200
    assert seen[0]["provider"] == {"data_collection": "deny"} and seen[0]["model"] == "vendor/alpha:free"
    assert seen[0]["url"] == "http://orx.test/api/v1/chat/completions" and seen[0]["auth"] == "Bearer test-key-not-a-secret"
    assert seen[1]["provider"] == {"data_collection": "deny", "order": ["x"]}


async def test_a_model_without_a_private_endpoint_falls_back_to_the_next_one(tmp_path, seed_policy_dict, upstream):
    seen, unavailable = upstream
    unavailable.add("vendor/alpha:free")  # what OpenRouter answers when every endpoint stores or trains
    path = write_policy(tmp_path, upstream_policy(seed_policy_dict, ["alpha", "beta"]))
    async with make_gateway(policy_path=path) as (c, sink):
        r = await c.post("/v1/chat/completions", json=chat("hi"))
    assert r.status_code == 200 and r.headers["x-gateway-model"] == "orx/beta" and r.headers["x-gateway-attempts"] == "2"
    assert [s["model"] for s in seen[:2]] == ["vendor/alpha:free", "vendor/beta:free"]
    assert sink.events[0].fell_back


async def test_when_no_model_has_a_private_endpoint_the_caller_gets_a_clear_upstream_error(tmp_path, seed_policy_dict, upstream):
    _, unavailable = upstream
    unavailable.update({"vendor/alpha:free", "vendor/beta:free"})
    path = write_policy(tmp_path, upstream_policy(seed_policy_dict, ["alpha", "beta"]))
    async with make_gateway(policy_path=path) as (c, _):
        r = await c.post("/v1/chat/completions", json=chat("hi"))
    assert r.status_code == 502 and r.json()["error"]["type"] == "upstream_error"
    assert "model_unavailable" in json.dumps(r.json())


async def test_sensitive_requests_never_reach_openrouter(tmp_path, seed_policy_dict, upstream):
    seen, _ = upstream
    path = write_policy(tmp_path, upstream_policy(seed_policy_dict, ["alpha"]))
    async with make_gateway(policy_path=path) as (c, _):
        r = await c.post("/v1/chat/completions", json=chat("write to jo.smith@example.com"))
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_eligible_provider" and seen == []
