"""Load balancing and backpressure: spreading traffic, per-provider and process-wide limits, and the retry budget."""

from __future__ import annotations

import asyncio
import copy
import random
from collections import Counter

import pytest
from conftest import chat, make_gateway, write_policy

from app.load import LoadTracker, RetryBudget
from app.policy import Policy, ProviderSpec
from app.profile import TaskProfile
from app.router import select_route

PROFILE = TaskProfile(task_type="chat", context_tokens=50, expected_output_tokens=100)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# ---- the tracker ------------------------------------------------------------------------------------------


def test_concurrency_is_capped_and_slots_are_returned():
    load, spec = LoadTracker(), ProviderSpec(kind="mock", max_concurrency=2)
    a, b = load.acquire("p", spec), load.acquire("p", spec)
    assert a and b and load.acquire("p", spec) is None and load.saturated({"p": spec}) == {"p"}
    a.release()
    a.release()  # releasing twice does not free a second slot
    assert load.inflight("p") == 1 and load.acquire("p", spec) is not None
    assert load.acquire("other", ProviderSpec(kind="mock")) is not None  # no limit configured means no limit


def test_calls_per_minute_are_budgeted_with_a_rolling_window():
    clock = Clock()
    load, spec = LoadTracker(clock=clock), ProviderSpec(kind="mock", rpm_limit=3)
    for _ in range(3):
        slot = load.acquire("p", spec)
        assert slot
        slot.release()  # finished calls still count against the minute
        clock.now += 10
    assert load.acquire("p", spec) is None
    assert 29 <= load.retry_after("p", spec) <= 31  # the oldest call leaves the window in 30 seconds
    clock.now += 31
    assert load.acquire("p", spec) is not None


def test_load_factor_runs_from_idle_to_full_and_has_a_default_scale():
    load = LoadTracker()
    capped, free = ProviderSpec(kind="mock", max_concurrency=4), ProviderSpec(kind="mock")
    assert load.load_factor("p", capped) == 0
    for _ in range(2):
        load.acquire("p", capped)
    assert load.load_factor("p", capped) == 0.5
    for _ in range(30):
        load.acquire("q", free)
    assert load.load_factor("q", free) == 1.0


def test_the_whole_process_admits_a_fixed_number_of_requests():
    load = LoadTracker(global_cap=2)
    assert load.enter() and load.enter() and not load.enter() and load.shed_global == 1
    load.leave()
    assert load.enter()
    unlimited = LoadTracker(global_cap=0)
    assert all(unlimited.enter() for _ in range(1000))


def test_the_retry_budget_allows_a_burst_then_a_fraction_of_traffic():
    budget = RetryBudget()
    assert [budget.try_spend(3) for _ in range(4)] == [True, True, True, False]  # a burst of three, then none
    for _ in range(8):
        budget.deposit(0.25, 3)  # eight ordinary requests earn two retries
    assert [budget.try_spend(3) for _ in range(3)] == [True, True, False]
    assert budget.denied == 2
    for _ in range(100):
        budget.deposit(0.25, 3)
    assert budget.tokens == 3  # never more than the burst


# ---- spreading traffic ------------------------------------------------------------------------------------


def two_providers(seed: dict, *, twin_quality: float | None = None, balance: dict | None = None) -> Policy:
    """Two mock providers each serving one model; by default the models are exact twins."""
    d = copy.deepcopy(seed)
    small = copy.deepcopy(next(m for m in d["models"] if m["id"] == "mock/small"))
    d["providers"] = {"east": {"kind": "mock"}, "west": {"kind": "mock"}}
    east, west = copy.deepcopy(small), copy.deepcopy(small)
    east.update(id="east/m", provider="east")
    west.update(id="west/m", provider="west")
    if twin_quality is not None:
        west["quality"] = {"default": twin_quality}
    d["models"] = [east, west]
    if balance is not None:
        d["balance"] = balance
    return Policy.model_validate(d)


def picks(policy: Policy, n: int = 400, **kw) -> Counter:
    rng = random.Random(7)
    return Counter(select_route(policy, PROFILE, env={}, rng=rng, **kw).primary.id for _ in range(n))


def test_equal_models_share_the_traffic(seed_policy_dict):
    counts = picks(two_providers(seed_policy_dict))
    assert counts["east/m"] > 140 and counts["west/m"] > 140  # not all to whichever sorts first


def test_a_clearly_better_model_still_gets_everything(seed_policy_dict):
    counts = picks(two_providers(seed_policy_dict, twin_quality=0.05))
    assert counts == {"east/m": 400}


def test_spreading_can_be_turned_off(seed_policy_dict):
    for balance in ({"enabled": False}, {"margin": 0.0}):
        assert picks(two_providers(seed_policy_dict, balance=balance)) == {"east/m": 400}


def test_a_busier_provider_gets_a_smaller_share(seed_policy_dict):
    policy = two_providers(seed_policy_dict)
    counts = picks(policy, load={"east": 0.9, "west": 0.0})
    assert counts["west/m"] > 3 * counts["east/m"]
    flat = picks(two_providers(seed_policy_dict, balance={"load_aware": False}), load={"east": 0.9, "west": 0.0})
    assert abs(flat["east/m"] - flat["west/m"]) < 120  # ignoring load, the split is about even


def test_the_first_fallback_is_on_another_provider(seed_policy_dict):
    d = copy.deepcopy(seed_policy_dict)
    base = next(m for m in d["models"] if m["id"] == "mock/small")
    d["providers"] = {"east": {"kind": "mock"}, "west": {"kind": "mock"}}
    models = []
    for name, provider, quality in (("e1", "east", 0.9), ("e2", "east", 0.8), ("w1", "west", 0.7)):
        m = copy.deepcopy(base)
        m.update(id=name, provider=provider, quality={"default": quality})
        models.append(m)
    d["models"] = models
    decision = select_route(Policy.model_validate(d), PROFILE, env={})
    assert decision and decision.primary.id == "e1"
    assert [m.id for m in decision.fallbacks] == ["w1", "e2"]  # another source first, even though e2 scores higher
    d["balance"] = {"diverse_fallback": False}
    plain = select_route(Policy.model_validate(d), PROFILE, env={})
    assert plain and [m.id for m in plain.fallbacks] == ["e2", "w1"]


def test_a_provider_at_its_limit_is_never_chosen_even_when_pinned(seed_policy_dict):
    policy = two_providers(seed_policy_dict)
    assert picks(policy, saturated={"east"}) == {"west/m": 400}
    assert select_route(policy, PROFILE, env={}, saturated={"east", "west"}) is None
    assert select_route(policy, PROFILE, env={}, pinned="east/m", saturated={"east"}) is None
    assert select_route(policy, PROFILE, env={}, pinned="east/m") is not None


# ---- through the gateway ----------------------------------------------------------------------------------


def slow_policy(seed: dict, *, east: dict | None = None, west: dict | None = None, **extra) -> dict:
    d = copy.deepcopy(seed)
    base = next(m for m in d["models"] if m["id"] == "mock/small")
    d["providers"] = {"east": {"kind": "mock", **(east or {})}}
    d["models"] = []
    for name, provider in (("e", "east"), ("w", "west")):
        if provider == "west":
            if west is None:
                continue
            d["providers"]["west"] = {"kind": "mock", **west}
        m = copy.deepcopy(base)
        m.update(id=f"{provider}/m", provider=provider, latency_ms_p50=300.0)
        d["models"].append(m)
    d.update(extra)
    return d


async def fire(c, n: int, **kw):
    return await asyncio.gather(*(c.post("/v1/chat/completions", json=chat(f"hello {i}"), **kw) for i in range(n)))


async def test_twin_models_really_do_share_real_traffic(tmp_path, seed_policy_dict):
    d = slow_policy(seed_policy_dict, east={}, west={})
    d["models"][1]["latency_ms_p50"] = d["models"][0]["latency_ms_p50"]
    async with make_gateway(policy_path=write_policy(tmp_path, d), mock_latency_scale=0.0) as (c, _):
        got = Counter(r.headers["x-gateway-model"] for r in await fire(c, 60))
    assert got["east/m"] >= 15 and got["west/m"] >= 15


async def test_a_flood_is_refused_quickly_with_a_retry_hint_and_nothing_leaks(tmp_path, seed_policy_dict):
    path = write_policy(tmp_path, slow_policy(seed_policy_dict))
    async with make_gateway(policy_path=path, mock_latency_scale=1.0, max_inflight=3) as (c, _):
        rt = c.app.state.rt
        results = await fire(c, 10)
        codes = Counter(r.status_code for r in results)
        shed = [r for r in results if r.status_code == 503]
        assert codes[200] == 3 and codes[503] == 7  # exactly the cap was served, the rest refused at once
        assert all(r.headers["retry-after"] == "1" and r.json()["error"]["code"] == "overloaded" for r in shed)
        assert rt.deps.load.global_inflight == 0  # every request let go of its place
        assert (await c.post("/v1/chat/completions", json=chat("again"))).status_code == 200  # and service resumed
        assert rt.deps.load.shed_global == 7


async def test_a_provider_at_its_concurrency_limit_sends_traffic_to_another(tmp_path, seed_policy_dict):
    d = slow_policy(seed_policy_dict, east={"max_concurrency": 1}, west={})
    async with make_gateway(policy_path=write_policy(tmp_path, d), mock_latency_scale=1.0) as (c, _):
        results = await fire(c, 6)
    assert all(r.status_code == 200 for r in results)
    got = Counter(r.headers["x-gateway-model"] for r in results)
    assert got["west/m"] >= 5 and got["east/m"] <= 1  # east took at most one at a time; the rest went west


async def test_when_every_provider_is_full_the_answer_is_a_quick_503_not_a_queue(tmp_path, seed_policy_dict):
    d = slow_policy(seed_policy_dict, east={"max_concurrency": 1})
    async with make_gateway(policy_path=write_policy(tmp_path, d), mock_latency_scale=1.0) as (c, sink):
        results = await fire(c, 5)
        codes = Counter(r.status_code for r in results)
        assert codes[200] == 1 and codes[503] == 4
        shed = next(r for r in results if r.status_code == 503)
        assert shed.json()["error"]["code"] == "overloaded" and int(shed.headers["retry-after"]) >= 1
        assert c.app.state.rt.deps.load.inflight("east") == 0
        assert c.app.state.rt.deps.load.shed_provider["east"] >= 4


async def test_a_pinned_model_on_a_full_provider_is_refused_too(tmp_path, seed_policy_dict):
    d = slow_policy(seed_policy_dict, east={"max_concurrency": 1}, west={})
    async with make_gateway(policy_path=write_policy(tmp_path, d), mock_latency_scale=1.0) as (c, _):
        results = await asyncio.gather(*(c.post("/v1/chat/completions", json=chat("hi", model="east/m")) for _ in range(3)))
    assert Counter(r.status_code for r in results) == {200: 1, 503: 2}


async def test_the_per_minute_budget_stops_calls_to_a_provider(tmp_path, seed_policy_dict):
    d = slow_policy(seed_policy_dict, east={"rpm_limit": 3})
    async with make_gateway(policy_path=write_policy(tmp_path, d), mock_latency_scale=0.0) as (c, _):
        codes = [(await c.post("/v1/chat/completions", json=chat(f"hi {i}"))).status_code for i in range(5)]
        last = await c.post("/v1/chat/completions", json=chat("again"))
    assert codes == [200, 200, 200, 503, 503]
    assert last.status_code == 503 and 1 <= int(last.headers["retry-after"]) <= 60


async def test_a_stream_keeps_its_place_until_it_ends(tmp_path, seed_policy_dict):
    d = slow_policy(seed_policy_dict, east={"max_concurrency": 1})
    async with make_gateway(policy_path=write_policy(tmp_path, d), mock_latency_scale=0.0) as (c, _):
        rt = c.app.state.rt
        long_reply = "word " * 12  # about 0.6 s as a slow stream
        stream = asyncio.create_task(
            c.post("/v1/chat/completions", json=chat(long_reply, stream=True), headers={"x-mock-behavior": "*=slow_stream"})
        )
        await asyncio.sleep(0.2)  # the stream is open and still producing
        assert rt.deps.load.inflight("east") == 1 and rt.deps.load.global_inflight == 1
        during = await c.post("/v1/chat/completions", json=chat("meanwhile"))
        assert during.status_code == 503 and during.json()["error"]["code"] == "overloaded"
        assert (await stream).status_code == 200
        await asyncio.sleep(0.05)  # the stream's cleanup runs just after the last byte
        assert rt.deps.load.inflight("east") == 0 and rt.deps.load.global_inflight == 0
        assert (await c.post("/v1/chat/completions", json=chat("after"))).status_code == 200


async def test_a_provider_that_fills_up_between_routing_and_calling_causes_a_fallback_not_an_error(tmp_path, seed_policy_dict):
    d = slow_policy(seed_policy_dict, east={}, west={}, balance={"enabled": False})
    d["models"][1]["quality"] = {"default": 0.01}  # east is tried first
    async with make_gateway(policy_path=write_policy(tmp_path, d), mock_latency_scale=0.0) as (c, _):
        rt = c.app.state.rt
        load, state = rt.deps.load, {"east_full": False}
        real_acquire, real_has = load.acquire, load.has_capacity

        def acquire(provider, spec):  # east fills up after it was chosen but before the call started
            if provider == "east":
                state["east_full"] = True
                return None
            return real_acquire(provider, spec)

        load.acquire = acquire  # type: ignore[method-assign]
        load.has_capacity = lambda provider, spec: False if (provider == "east" and state["east_full"]) else real_has(provider, spec)  # type: ignore[method-assign]
        r = await c.post("/v1/chat/completions", json=chat("hi"))
        assert r.status_code == 200 and r.headers["x-gateway-model"] == "west/m" and r.headers["x-gateway-attempts"] == "2"
        assert rt.deps.retry_budget.denied == 0
        assert rt.deps.retry_budget.tokens == 10  # the saturated attempt added no upstream load, so it spent nothing


async def test_a_busy_provider_is_not_replaced_by_a_broken_one(tmp_path, seed_policy_dict):
    """The healthy provider is full and the other one is down: the answer is 'busy, retry shortly', and the dead provider
    is not called just because the healthy one is occupied."""
    d = slow_policy(seed_policy_dict, east={}, west={"max_concurrency": 2}, balance={"enabled": False})
    d["models"][0]["quality"] = {"default": 0.9}  # east preferred, but it is down
    d["models"][1]["quality"] = {"default": 0.8}
    down = {"x-mock-behavior": "east/m=error_500"}
    async with make_gateway(policy_path=write_policy(tmp_path, d), mock_latency_scale=1.0) as (c, _):
        rt = c.app.state.rt
        await fire(c, 8, headers=down)  # east's circuit opens during this first wave
        assert "east" in rt.deps.health.open_providers()
        east_calls = rt.deps.load.snapshot()["providers"]["east"]["calls_last_minute"]
        results = await fire(c, 12, headers=down)
        assert Counter(r.status_code for r in results) == {200: 2, 503: 10}  # west's two slots, the rest told to retry
        assert rt.deps.load.snapshot()["providers"]["east"]["calls_last_minute"] == east_calls  # east was left alone


async def test_fallbacks_are_limited_by_the_retry_budget(tmp_path, seed_policy_dict):
    d = slow_policy(seed_policy_dict, east={}, west={}, retry={"budget_ratio": 0.0, "budget_burst": 2, "max_attempts": 3})
    d["balance"] = {"enabled": False}
    d["models"][1]["quality"] = {"default": 0.01}  # east is always tried first
    fail = {"x-mock-behavior": "east/m=error_500"}
    async with make_gateway(policy_path=write_policy(tmp_path, d), mock_latency_scale=0.0) as (c, _):
        rt = c.app.state.rt
        results = [await c.post("/v1/chat/completions", json=chat(f"hi {i}"), headers=fail) for i in range(3)]
        assert [r.status_code for r in results] == [200, 200, 503]  # two fallbacks were affordable, the third was not
        denied = results[2].json()["error"]
        assert denied["code"] == "retry_budget_exhausted" and results[2].headers["retry-after"] == "1"
        assert rt.deps.retry_budget.denied == 1
        view = (await c.get("/internal/load")).json()
        assert view["retry_budget"]["denied"] == 1 and view["global_inflight"] == 0


async def test_ordinary_traffic_earns_the_budget_back(tmp_path, seed_policy_dict):
    d = slow_policy(seed_policy_dict, east={}, west={}, retry={"budget_ratio": 1.0, "budget_burst": 1, "max_attempts": 3})
    d["balance"] = {"enabled": False}
    d["models"][1]["quality"] = {"default": 0.01}
    async with make_gateway(policy_path=write_policy(tmp_path, d), mock_latency_scale=0.0) as (c, _):
        for i in range(4):  # each request earns a full token and spends one on its fallback
            r = await c.post("/v1/chat/completions", json=chat(f"hi {i}"), headers={"x-mock-behavior": "east/m=error_500"})
            assert r.status_code == 200, i


def test_the_load_settings_round_trip_through_the_policy(seed_policy_dict):
    d = copy.deepcopy(seed_policy_dict)
    d["providers"]["mock"].update(max_concurrency=5, rpm_limit=60)
    p = Policy.model_validate(d)
    assert p.providers["mock"].max_concurrency == 5 and p.providers["mock"].rpm_limit == 60
    assert p.balance.enabled and p.retry.budget_ratio == 0.2 and p.retry.budget_burst == 10
    with pytest.raises(ValueError):
        Policy.model_validate({**d, "providers": {"mock": {"kind": "mock", "max_concurrency": 0}}})
