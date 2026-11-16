"""Keys created from the dashboard (owned by a user) work against the OpenAI-style API."""

from __future__ import annotations

import pytest
from conftest import chat, make_gateway, settle

from app.auth import hash_key

# The dashboard (Node) and the gateway (Python) must hash keys identically, or a key created
# on the dashboard would never authenticate. dashboard/lib/keys.test.ts asserts the same pair.
DASHBOARD_KEY = "gw_live_" + "A" * 43
DASHBOARD_KEY_HASH = "ec79a8961331af28b81422a7ebc02da7d0886db6c0fd1fd663f5ba98ba8ff252"


def test_hash_matches_the_dashboard_implementation():
    assert hash_key(DASHBOARD_KEY) == DASHBOARD_KEY_HASH


async def add_dashboard_key(sink, user_id="usr_alice", **kw):
    """Insert a record shaped like the one the dashboard writes."""
    await sink.create_api_key({
        "id": "key_abc", "name": "my-app", "key_prefix": DASHBOARD_KEY[:12],
        "key_hash": DASHBOARD_KEY_HASH, "user_id": user_id, **kw,
    })


def headers(key=DASHBOARD_KEY):
    return {"authorization": f"Bearer {key}"}


async def test_dashboard_key_works_as_an_openai_bearer_token():
    async with make_gateway(auth="required") as (c, sink):
        await add_dashboard_key(sink)
        r = await c.post("/v1/chat/completions", json=chat(), headers=headers())
        assert r.status_code == 200
        assert r.json()["choices"]


async def test_calls_are_stamped_with_the_key_owner():
    async with make_gateway(auth="required") as (c, sink):
        await add_dashboard_key(sink)
        await c.post("/v1/chat/completions", json=chat(), headers=headers())
        await settle()
        span = sink.events[-1]
        assert span.user_id == "usr_alice"
        assert span.api_key_id == "key_abc"
        assert span.api_key_name == "my-app"


async def test_failed_requests_log_the_owner_too():
    async with make_gateway(auth="required") as (c, sink):
        await add_dashboard_key(sink)
        r = await c.post("/v1/chat/completions", json={"model": "auto", "messages": []}, headers=headers())
        assert r.status_code == 400
        await settle()
        assert sink.logs, "a rejected request still produces a log event"
        assert {log.user_id for log in sink.logs} == {"usr_alice"}


async def test_disabled_owner_stops_all_their_keys():
    async with make_gateway(auth="required", auth_cache_ttl_s=0) as (c, sink):
        await add_dashboard_key(sink, user_disabled=True)
        r = await c.post("/v1/chat/completions", json=chat(), headers=headers())
        assert r.status_code == 401
        assert 'error_description="revoked"' in r.headers["www-authenticate"]


async def test_revoked_dashboard_key_is_rejected_and_never_reaches_a_provider():
    async with make_gateway(auth="required", auth_cache_ttl_s=0) as (c, sink):
        await add_dashboard_key(sink, status="revoked")
        r = await c.post("/v1/chat/completions", json=chat(), headers=headers())
        assert r.status_code == 401
        await settle()
        assert not [e for e in sink.events if e.provider]


@pytest.mark.parametrize("key", ["gw_live_" + "B" * 43, "gw_live_", "gw_live_" + "A" * 42])
async def test_near_miss_keys_are_rejected(key):
    async with make_gateway(auth="required") as (c, sink):
        await add_dashboard_key(sink)
        r = await c.post("/v1/chat/completions", json=chat(), headers=headers(key))
        assert r.status_code == 401
