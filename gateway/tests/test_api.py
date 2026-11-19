import json

from conftest import chat, make_gateway, settle, write_policy

from app.graph.nodes import make_timed_nodes
from app.graph.plain import run_plain
from app.schemas import ChatRequest


async def test_basic_completion_routes_cheap_and_logs_event(gw):
    c, sink = gw
    r = await c.post("/v1/chat/completions", json=chat("hello there"))
    assert r.status_code == 200
    assert r.headers["x-gateway-model"] == "mock/small"
    assert r.json()["choices"][0]["message"]["content"].startswith("[mock/small]")
    assert r.headers["x-gateway-call-id"].startswith("call_")

    (ev,) = sink.events
    assert ev.span_id == r.headers["x-gateway-call-id"]
    assert ev.status == "ok" and ev.origin == "external"
    assert ev.model == "mock/small" and ev.task_type == "chat"
    assert ev.prompt_tokens > 0 and ev.completion_tokens > 0
    assert ev.shadow_cost_usd > 0 and ev.cost_usd == ev.shadow_cost_usd  # mock models are not free
    assert ev.policy_version == 1 and ev.attempt_count == 1 and not ev.fell_back
    assert {"intake", "profile", "route", "call", "evaluate", "respond"} <= set(ev.node_timings)
    assert ev.overhead_ms is not None and ev.overhead_ms >= 0


async def test_code_task_routes_to_higher_tier(gw):
    c, _ = gw
    r = await c.post("/v1/chat/completions", json=chat("please fix this bug in my function"))
    assert r.headers["x-gateway-model"] == "mock/medium"


async def test_pinned_model_is_honored(gw):
    c, _ = gw
    r = await c.post("/v1/chat/completions", json=chat(model="mock/large"))
    assert r.headers["x-gateway-model"] == "mock/large"


async def test_fallback_to_next_model_when_primary_errors(gw):
    c, sink = gw
    r = await c.post(
        "/v1/chat/completions", json=chat(), headers={"x-mock-behavior": "mock/small=error_500"}
    )
    assert r.status_code == 200
    assert r.headers["x-gateway-model"] == "mock/medium"
    (ev,) = sink.events
    assert ev.fell_back and ev.attempt_count == 2
    assert [a.outcome_class for a in ev.attempts] == ["server_error", "ok"]
    assert ev.attempts[1].fallback_reason == "server_error"


async def test_all_attempts_fail_returns_502_with_attempt_trail(gw):
    c, sink = gw
    r = await c.post("/v1/chat/completions", json=chat(), headers={"x-mock-behavior": "*=error_500"})
    assert r.status_code == 502
    err = r.json()["error"]
    assert err["type"] == "upstream_error" and len(err["attempts"]) == 3  # retry budget
    (ev,) = sink.events
    assert ev.status == "error" and ev.attempt_count == 3 and ev.http_status == 502


async def test_all_timeouts_map_to_504(gw):
    c, _ = gw
    r = await c.post("/v1/chat/completions", json=chat(), headers={"x-mock-behavior": "*=timeout"})
    assert r.status_code == 504


async def test_validator_failure_escalates_tier(gw):
    c, sink = gw
    r = await c.post(
        "/v1/chat/completions",
        json=chat("pull the fields", response_format={"type": "json_object"}),
        headers={"x-mock-behavior": "mock/small=bad_json"},
    )
    assert r.status_code == 200
    json.loads(r.json()["choices"][0]["message"]["content"])
    assert r.headers["x-gateway-model"] != "mock/small"
    (ev,) = sink.events
    assert [a.outcome_class for a in ev.attempts] == ["validator_failed", "ok"]


async def test_malformed_upstream_body_is_retried(gw):
    c, _ = gw
    r = await c.post("/v1/chat/completions", json=chat(), headers={"x-mock-behavior": "mock/small=malformed"})
    assert r.status_code == 200 and r.headers["x-gateway-model"] == "mock/medium"


async def test_streaming_passthrough_and_usage_logged(gw):
    c, sink = gw
    r = await c.post("/v1/chat/completions", json=chat("stream me please", stream=True))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.text.rstrip().endswith("data: [DONE]")
    assert "[mock/small]" in r.text.replace('"', "")
    await settle()
    (ev,) = sink.events
    assert ev.stream and ev.status == "ok" and ev.completion_tokens > 0 and ev.ttft_ms is not None


async def test_streaming_falls_back_before_first_token(gw):
    c, sink = gw
    r = await c.post(
        "/v1/chat/completions", json=chat(stream=True), headers={"x-mock-behavior": "mock/small=timeout"}
    )
    assert r.status_code == 200 and r.headers["x-gateway-model"] == "mock/medium"
    await settle()
    assert sink.events[0].fell_back


async def test_reviewer_is_routed_away_from_the_implementers_family(gw):
    c, sink = gw
    first = await c.post(
        "/v1/chat/completions",
        json=chat("write the function", model="mock/large"),
        headers={"x-run-id": "run_t1", "x-node-role": "implementer"},
    )
    parent_id = first.headers["x-gateway-call-id"]
    second = await c.post(
        "/v1/chat/completions",
        json=chat("review the function"),
        headers={"x-run-id": "run_t1", "x-node-role": "reviewer", "x-parent-call-id": parent_id},
    )
    assert second.headers["x-gateway-model"] != "mock/large"
    ev = [e for e in sink.events if e.span_id == second.headers["x-gateway-call-id"]][0]
    assert ev.run_id == "run_t1" and ev.parent_span_id == parent_id and ev.node_role == "reviewer"


async def test_auth_when_keys_configured():
    async with make_gateway(api_keys="k1:alpha") as (c, sink):
        assert (await c.post("/v1/chat/completions", json=chat())).status_code == 401
        bad = await c.post("/v1/chat/completions", json=chat(), headers={"authorization": "Bearer nope"})
        assert bad.status_code == 401
        ok = await c.post("/v1/chat/completions", json=chat(), headers={"authorization": "Bearer k1"})
        assert ok.status_code == 200
        assert sink.events[-1].api_key_name == "alpha"
        # Rejected requests are log events only; they never create spans or runs.
        assert len(sink.events) == 1
        assert [l.kind for l in sink.logs] == ["request_rejected", "request_rejected"]


async def test_empty_messages_is_a_400(gw):
    c, _ = gw
    r = await c.post("/v1/chat/completions", json={"messages": []})
    assert r.status_code == 400


async def test_idempotency_key_replays_without_a_second_call(gw):
    c, sink = gw
    h = {"idempotency-key": "abc-123"}
    a = await c.post("/v1/chat/completions", json=chat(), headers=h)
    b = await c.post("/v1/chat/completions", json=chat(), headers=h)
    assert b.headers.get("x-gateway-idempotent-replay") == "true"
    assert a.json()["id"] == b.json()["id"] and len(sink.events) == 1


async def test_llm_classify_branch_refines_profile_and_records_internal_span(tmp_path, seed_policy_dict):
    seed_policy_dict["llm_classify"]["enabled"] = True
    path = write_policy(tmp_path, seed_policy_dict)
    async with make_gateway(policy_path=path) as (c, sink):
        r = await c.post("/v1/chat/completions", json=chat("hi MOCK_CLASSIFY:summarization"))
        assert r.status_code == 200
        main = [e for e in sink.events if e.origin == "external"][0]
        internal = [e for e in sink.events if e.origin == "internal"][0]
        assert main.task_type == "summarization" and main.profile["source"] == "llm"
        assert internal.node_role == "router_classifier" and internal.parent_span_id == main.span_id
        assert internal.run_id == main.run_id


async def test_confident_requests_skip_the_llm_branch(tmp_path, seed_policy_dict):
    seed_policy_dict["llm_classify"]["enabled"] = True
    path = write_policy(tmp_path, seed_policy_dict)
    async with make_gateway(policy_path=path) as (c, sink):
        await c.post("/v1/chat/completions", json=chat("anything"), headers={"x-task-type": "chat"})
        assert [e.origin for e in sink.events] == ["external"]


async def test_models_and_health_endpoints(gw):
    c, _ = gw
    ids = [m["id"] for m in (await c.get("/v1/models")).json()["data"]]
    assert ids[0] == "auto" and "mock/small" in ids and "groq/gpt-oss-120b" not in ids
    h = (await c.get("/healthz")).json()
    assert h["ok"] and h["policy_version"] == 1


async def test_plain_pipeline_matches_the_graph(gw):
    c, _ = gw
    deps = c.app.state.rt.deps  # type: ignore[attr-defined]
    nodes = make_timed_nodes(deps)

    def fresh():
        req = ChatRequest(messages=[{"role": "user", "content": "please fix this bug"}])
        return {"request": req, "headers": {"x-mock-behavior": "mock/medium=error_500"},
                "origin": "external", "timings": {}}

    graph_state = await c.app.state.rt.graph.ainvoke(fresh())  # type: ignore[attr-defined]
    plain_state = await run_plain(nodes, fresh())
    for s in (graph_state, plain_state):
        assert s["final"]["kind"] == "json"
    assert graph_state["selected_route"].primary.id == plain_state["selected_route"].primary.id == "mock/large"
    assert [a["outcome_class"] for a in graph_state["attempts"]] == ["server_error", "ok"]
    assert [a["outcome_class"] for a in plain_state["attempts"]] == ["server_error", "ok"]
