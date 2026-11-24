from conftest import chat, make_gateway, settle


async def test_fallback_emits_tagged_stamped_log_events(gw):
    c, sink = gw
    r = await c.post("/v1/chat/completions", json=chat("hello"),
                     headers={"x-mock-behavior": "mock/small=error_500", "x-node-role": "implementer"})
    run, span = r.headers["x-gateway-run-id"], r.headers["x-gateway-call-id"]
    by_kind = {l.kind: l for l in sink.logs}
    assert set(by_kind) == {"attempt_failed", "fallback"}

    failed = by_kind["attempt_failed"]
    assert failed.source == "gateway" and failed.severity == "warn"
    assert (failed.run_id, failed.span_id) == (run, span)
    assert failed.tags["model"] == "mock/small" and failed.tags["provider"] == "mock"
    assert failed.tags["error_class"] == "server_error" and failed.tags["node_role"] == "implementer"
    assert failed.tags["task_type"] == "chat" and failed.tags["policy_version"] == 1
    assert failed.tags["env"] == "local" and failed.fingerprint == "attempt:mock:server_error"
    assert by_kind["fallback"].severity == "info" and by_kind["fallback"].tags["attempt_count"] == 2
    assert all(l.run_id == run and l.span_id == span for l in sink.logs)


async def test_total_failure_logs_an_error_with_the_attempt_trail(gw):
    c, sink = gw
    r = await c.post("/v1/chat/completions", json=chat(), headers={"x-mock-behavior": "*=error_500"})
    assert r.status_code == 502
    failed = [l for l in sink.logs if l.kind == "request_failed"]
    assert len(failed) == 1
    assert failed[0].severity == "error" and failed[0].tags["http_status"] == 502
    assert len(failed[0].payload["attempts"]) == 3
    assert sum(l.kind == "attempt_failed" for l in sink.logs) == 3


async def test_successful_requests_add_no_log_events(gw):
    c, sink = gw
    await c.post("/v1/chat/completions", json=chat())
    assert list(sink.logs) == [] and len(sink.events) == 1  # they live in spans, merged by the view


async def test_rejected_requests_are_logs_not_spans():
    async with make_gateway(api_keys="k:x") as (c, sink):
        await c.post("/v1/chat/completions", json=chat())
        assert len(sink.events) == 0
        (log,) = sink.logs
        assert log.kind == "request_rejected" and log.severity == "warn"
        assert log.tags["http_status"] == 401 and log.tags["error_class"] == "unauthorized"
        assert log.run_id and log.span_id  # still stamped, so it can be correlated


async def test_streaming_failures_are_logged_too(gw):
    c, sink = gw
    await c.post("/v1/chat/completions", json=chat(stream=True),
                 headers={"x-mock-behavior": "mock/small=timeout"})
    await settle()
    assert {l.kind for l in sink.logs} == {"attempt_failed", "fallback"}
