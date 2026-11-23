"""Quick production-hardening fixes: API docs hidden on a public gateway, a bounded and caller-scoped idempotency
cache, and a daily per-user request cap."""

from __future__ import annotations

from datetime import UTC, datetime

from conftest import chat, make_gateway

from app.config import Settings
from app.idempotency import MAX_BODY_BYTES, MAX_KEY_CHARS, IdempotencyCache, namespace

ALICE = {"authorization": "Bearer key-alice-0123456789"}
BOB = {"authorization": "Bearer key-bob-0123456789"}
KEYS = "key-alice-0123456789:alice,key-bob-0123456789:bob"


# ---- API docs ---------------------------------------------------------------------------------------------


async def test_the_api_docs_are_served_only_when_the_gateway_is_open():
    async with make_gateway() as (c, _):  # local development: authentication off
        assert (await c.get("/docs")).status_code == 200 and (await c.get("/openapi.json")).status_code == 200
    async with make_gateway(api_keys=KEYS) as (c, _):  # a gateway that requires keys is a public one
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await c.get(path)).status_code == 404, path
        assert (await c.get("/healthz")).status_code == 200  # everything else is unchanged
    async with make_gateway(api_keys=KEYS, docs_enabled=True) as (c, _):
        assert (await c.get("/openapi.json")).status_code == 200


def test_render_counts_as_public(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    assert Settings(api_keys="").docs_on is True
    monkeypatch.setenv("RENDER", "true")
    assert Settings(api_keys="").docs_on is False


# ---- idempotency ------------------------------------------------------------------------------------------


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


def test_the_cache_is_scoped_to_the_credential():
    cache = IdempotencyCache(300, 10)
    mine, theirs = namespace("Bearer a"), namespace("Bearer b")
    assert cache.put(mine, "1", {"for": "alice"}, {"h": "1"})
    assert cache.get(mine, "1") == ({"for": "alice"}, {"h": "1"})
    assert cache.get(theirs, "1") is None  # the same key from someone else is not a replay
    assert namespace(None) != namespace("Bearer a") and len(namespace("Bearer a")) == 24


def test_entries_expire_and_the_cache_has_a_ceiling():
    clock = Clock()
    cache = IdempotencyCache(10, 3, clock=clock)
    for i in range(5):
        cache.put("ns", f"k{i}", {"i": i}, {})
    assert len(cache) == 3 and cache.get("ns", "k0") is None and cache.get("ns", "k4") == ({"i": 4}, {})  # oldest went first
    clock.now = 11
    assert cache.get("ns", "k4") is None and len(cache) == 0  # expired entries are swept, not just ignored


def test_oversized_keys_and_responses_are_not_kept():
    cache = IdempotencyCache(300, 10)
    assert not cache.put("ns", "k" * (MAX_KEY_CHARS + 1), {"a": 1}, {})
    assert not cache.put("ns", "big", {"blob": "x" * (MAX_BODY_BYTES + 1)}, {})
    assert cache.get("ns", "k" * (MAX_KEY_CHARS + 1)) is None and len(cache) == 0


def test_putting_a_key_again_refreshes_it_without_duplicating():
    cache = IdempotencyCache(300, 2)
    cache.put("ns", "a", {"v": 1}, {})
    cache.put("ns", "b", {"v": 2}, {})
    cache.put("ns", "a", {"v": 3}, {})  # a is now the newest
    cache.put("ns", "c", {"v": 4}, {})  # evicts b, the oldest
    assert cache.get("ns", "a") == ({"v": 3}, {}) and cache.get("ns", "b") is None and len(cache) == 2


async def test_another_user_cannot_replay_my_response():
    async with make_gateway(api_keys=KEYS) as (c, _):
        mine = await c.post("/v1/chat/completions", json=chat("my private question"), headers={**ALICE, "idempotency-key": "1"})
        theirs = await c.post("/v1/chat/completions", json=chat("something else"), headers={**BOB, "idempotency-key": "1"})
        again = await c.post("/v1/chat/completions", json=chat("my private question"), headers={**ALICE, "idempotency-key": "1"})
    assert mine.status_code == theirs.status_code == 200
    assert "x-gateway-idempotent-replay" not in theirs.headers  # bob got his own answer, computed for bob
    assert "something else" in theirs.json()["choices"][0]["message"]["content"]
    assert "private" not in theirs.text
    assert again.headers.get("x-gateway-idempotent-replay") == "true"  # alice's own replay still works


async def test_a_replay_is_refused_once_the_credential_stops_working(monkeypatch):
    async with make_gateway(api_keys=KEYS) as (c, _):
        first = await c.post("/v1/chat/completions", json=chat("hello"), headers={**ALICE, "idempotency-key": "k"})
        assert first.status_code == 200
        auth = c.app.state.rt.deps.auth
        monkeypatch.setattr(auth, "_env_keys", {})  # the key is revoked
        replay = await c.post("/v1/chat/completions", json=chat("hello"), headers={**ALICE, "idempotency-key": "k"})
    assert replay.status_code == 401  # not served from the cache


async def test_the_gateway_cache_stays_bounded():
    async with make_gateway(idempotency_max_entries=5) as (c, _):
        for i in range(20):
            assert (await c.post("/v1/chat/completions", json=chat(f"hello {i}"), headers={"idempotency-key": f"k{i}"})).status_code == 200
        assert len(c.app.state.rt.idem) == 5
        long_key = "k" * 500
        r = await c.post("/v1/chat/completions", json=chat("hello"), headers={"idempotency-key": long_key})
        assert r.status_code == 200 and len(c.app.state.rt.idem) == 5  # served normally, just not remembered


# ---- daily quota ------------------------------------------------------------------------------------------


async def test_a_user_is_stopped_at_the_daily_limit_and_others_are_not():
    async with make_gateway(api_keys=KEYS, user_daily_requests=3) as (c, _):
        codes = [(await c.post("/v1/chat/completions", json=chat(f"hi {i}"), headers=ALICE)).status_code for i in range(5)]
        blocked = await c.post("/v1/chat/completions", json=chat("again"), headers=ALICE)
        bob = await c.post("/v1/chat/completions", json=chat("hi"), headers=BOB)
    assert codes == [200, 200, 200, 429, 429]
    err = blocked.json()["error"]
    assert err["code"] == "quota_exceeded" and "00:00 UTC" in err["message"] and 1 <= int(blocked.headers["retry-after"]) <= 86400
    assert bob.status_code == 200  # the cap is per user


async def test_the_quota_resets_at_midnight_utc(monkeypatch):
    async with make_gateway(api_keys=KEYS, user_daily_requests=1) as (c, _):
        auth = c.app.state.rt.deps.auth
        monkeypatch.setattr(auth, "_now", lambda: datetime(2026, 11, 22, 23, 59, 0, tzinfo=UTC))
        assert (await c.post("/v1/chat/completions", json=chat("a"), headers=ALICE)).status_code == 200
        blocked = await c.post("/v1/chat/completions", json=chat("b"), headers=ALICE)
        assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) == 60  # one minute to midnight
        monkeypatch.setattr(auth, "_now", lambda: datetime(2026, 11, 23, 0, 0, 1, tzinfo=UTC))
        assert (await c.post("/v1/chat/completions", json=chat("c"), headers=ALICE)).status_code == 200


async def test_the_quota_is_off_by_default_and_rejected_requests_do_not_use_it():
    async with make_gateway(api_keys=KEYS) as (c, _):
        assert all([(await c.post("/v1/chat/completions", json=chat(f"hi {i}"), headers=ALICE)).status_code == 200 for i in range(30)])
    async with make_gateway(api_keys=KEYS, user_daily_requests=2) as (c, _):
        for _ in range(5):
            assert (await c.post("/v1/chat/completions", json=chat("hi"), headers={"authorization": "Bearer wrong"})).status_code == 401
        assert (await c.post("/v1/chat/completions", json=chat("hi"), headers=ALICE)).status_code == 200  # bad keys cost nothing
