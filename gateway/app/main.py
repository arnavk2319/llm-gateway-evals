from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from . import observability
from .auth import Authenticator
from .config import ENV_FILES, Settings
from .events import build_log_events, build_span_event
from .graph.build import build_graph
from .graph.nodes import Deps
from .lineage import Lineage
from .policy import PolicyStore
from .providers.health import ProviderHealth
from .providers.registry import ProviderRegistry
from .schemas import ChatRequest
from .sinks import EventSink, MemorySink, make_sink

log = logging.getLogger("gateway")

for _f in ENV_FILES:  # export provider keys (GROQ_API_KEY, ...) to os.environ
    load_dotenv(_f)


@dataclass
class Runtime:
    settings: Settings
    sink: EventSink
    policy_store: PolicyStore
    graph: Any
    deps: Deps
    idem: dict[str, tuple[float, dict, dict]] = field(default_factory=dict)
    tasks: set[asyncio.Task] = field(default_factory=set)


def _usage_from_body(body: dict[str, Any] | None) -> tuple[dict[str, int], int]:
    body = body or {}
    u = body.get("usage") or {}
    details = u.get("prompt_tokens_details") or {}
    usage = {
        "prompt_tokens": int(u.get("prompt_tokens") or 0),
        "completion_tokens": int(u.get("completion_tokens") or 0),
        "cached_tokens": int(details.get("cached_tokens") or 0),
    }
    chars = 0
    try:
        content = body["choices"][0]["message"].get("content")
        chars = len(content) if isinstance(content, str) else 0
    except (KeyError, IndexError, TypeError):
        pass
    return usage, chars


async def _emit(
    rt: Runtime,
    state: dict[str, Any],
    *,
    graph_ms: float,
    usage: dict[str, int] | None = None,
    completion_chars: int = 0,
    status: str | None = None,
    error_class: str | None = None,
    latency_ms: float | None = None,
) -> None:
    """Write spans and tagged log events to the event store. Never raises."""
    if latency_ms is None:
        latency_ms = (time.monotonic() - state.get("started", time.monotonic())) * 1000
    # Requests rejected before profiling (bad credential, validation) are log events only:
    # anyone can send those, so they must not create spans or runs.
    rejected = bool(state.get("error")) and not state.get("task_profile")

    logs = []
    try:
        logs = build_log_events(state)
        observability.report_logs(logs)  # alert-worthy ones go to Sentry; tags get the event id
    except Exception as e:
        log.exception("failed to build log events")
        observability.capture("log event build failed", tags={"where": "emit"}, exc=e)

    try:
        if not rejected:
            events = [
                build_span_event(
                    state, latency_ms=latency_ms, graph_ms=graph_ms, usage=usage,
                    completion_chars=completion_chars, status=status, error_class=error_class,
                ),
                *state.get("internal_spans", []),
            ]
            await rt.sink.write_many(events)
    except Exception as e:
        log.exception("failed to write spans")
        observability.capture("span write failed", tags={"where": "emit"}, exc=e)

    try:
        await rt.sink.write_logs(logs)
    except Exception as e:
        log.exception("failed to write log events")
        observability.capture("log write failed", tags={"where": "emit"}, exc=e)


class BodyLimitMiddleware:
    """Reject oversized request bodies before they are parsed (declared or streamed)."""

    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        declared = dict(scope["headers"]).get(b"content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            return await self._reject(scope, receive, send)

        seen = 0
        exceeded = False
        started = False

        async def limited_receive():
            nonlocal seen, exceeded
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.max_bytes:
                    exceeded = True
                    raise _TooLarge
            return msg

        async def guarded_send(msg):
            nonlocal started
            if exceeded:  # the app may have turned the cut-off into its own 400: replace it
                return
            if msg["type"] == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _TooLarge:
            pass
        if exceeded and not started:
            await self._reject(scope, receive, send)

    async def _reject(self, scope, receive, send):
        resp = JSONResponse(
            {"error": {"message": f"request body exceeds {self.max_bytes} bytes",
                       "type": "invalid_request_error", "code": "request_too_large"}},
            status_code=413,
        )
        await resp(scope, receive, send)


class _TooLarge(Exception):
    pass


def create_app(
    settings: Settings | None = None,
    sink: EventSink | None = None,
    sentry_overrides: dict[str, Any] | None = None,
) -> FastAPI:
    settings = settings or Settings()
    observability.init_sentry(settings, **(sentry_overrides or {}))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = httpx.AsyncClient(limits=httpx.Limits(max_connections=100, max_keepalive_connections=20))
        sink_ = sink or await make_sink(settings)
        policy_store = PolicyStore(settings.policy_path, settings.policy_ttl_s, sink_)
        await policy_store.startup()
        deps = Deps(
            settings=settings,
            policy_store=policy_store,
            registry=ProviderRegistry(client, settings),
            lineage=Lineage(sink_),
            auth=Authenticator(settings, sink_),
            health=ProviderHealth(),
        )
        app.state.rt = Runtime(
            settings=settings, sink=sink_, policy_store=policy_store, graph=build_graph(deps), deps=deps
        )
        yield
        await client.aclose()
        await sink_.close()

    app = FastAPI(title="LLM Gateway", version="0.1.0", lifespan=lifespan)
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_request_bytes)

    @app.get("/healthz")
    async def healthz(request: Request):
        rt: Runtime = request.app.state.rt
        policy = await rt.policy_store.get()
        return {"ok": True, "sink": type(rt.sink).__name__, "policy_version": policy.version}

    @app.get("/v1/models")
    async def models(request: Request):
        rt: Runtime = request.app.state.rt
        policy = await rt.policy_store.get()
        data = [{"id": "auto", "object": "model", "owned_by": "gateway"}] + [
            {"id": m.id, "object": "model", "owned_by": m.provider, "tier": m.tier, "family": m.family}
            for m in policy.available_models()
        ]
        return {"object": "list", "data": data}

    if settings.debug_enabled:

        @app.get("/internal/policy")
        async def active_policy(request: Request):
            rt: Runtime = request.app.state.rt
            return (await rt.policy_store.get()).model_dump(mode="json")

        @app.get("/internal/events")
        async def recent_events(request: Request, limit: int = 50):
            rt: Runtime = request.app.state.rt
            if not isinstance(rt.sink, MemorySink):
                return JSONResponse({"error": "only available with the in-memory sink"}, status_code=404)
            return [e.to_dict() for e in list(rt.sink.events)[-limit:]]

    @app.post("/v1/chat/completions")
    async def chat_completions(req: ChatRequest, request: Request):
        rt: Runtime = request.app.state.rt
        headers = {k.lower(): v for k, v in request.headers.items()}

        idem = headers.get("idempotency-key")
        if idem and not req.stream:
            hit = rt.idem.get(idem)
            if hit and hit[0] > time.monotonic():
                return JSONResponse(hit[1], headers={**hit[2], "x-gateway-idempotent-replay": "true"})

        state: dict[str, Any] = {"request": req, "headers": headers, "origin": "external", "timings": {}}
        t0 = time.perf_counter()
        final_state = await rt.graph.ainvoke(state, config={"recursion_limit": 60})
        graph_ms = (time.perf_counter() - t0) * 1000
        final = final_state["final"]
        hdr = final["headers"]

        if final["kind"] == "json":
            usage, chars = _usage_from_body(final["body"])
            if idem:
                rt.idem[idem] = (time.monotonic() + settings.idempotency_ttl_s, final["body"], hdr)
            return JSONResponse(
                final["body"], status_code=200, headers=hdr,
                background=BackgroundTask(_emit, rt, final_state, graph_ms=graph_ms, usage=usage,
                                          completion_chars=chars),
            )

        if final["kind"] == "error":
            return JSONResponse(
                final["body"], status_code=final["status"], headers=hdr,
                background=BackgroundTask(_emit, rt, final_state, graph_ms=graph_ms),
            )

        handle = final["handle"]
        outcome = {"status": "ok", "error_class": None}

        async def finalize() -> None:
            try:
                await handle.aclose()
            except Exception:
                log.exception("closing provider stream failed")
            await _emit(
                rt, final_state, graph_ms=graph_ms, usage=handle.usage,
                completion_chars=handle.completion_chars,
                status=outcome["status"], error_class=outcome["error_class"],
            )

        async def body_iter():
            try:
                yield handle.first
                async for chunk in handle.iterator:
                    handle.observe(chunk)
                    yield chunk
            except asyncio.CancelledError:
                outcome.update(status="stream_error", error_class="client_disconnect")
                raise
            except Exception as e:
                outcome.update(status="stream_error", error_class="stream_error")
                err = {"error": {"message": f"upstream stream failed: {type(e).__name__}",
                                 "type": "stream_error"}}
                yield f"data: {json.dumps(err)}\n\n"
            finally:
                # Detached so client disconnects (task cancellation) can't skip it.
                task = asyncio.create_task(finalize())
                rt.tasks.add(task)
                task.add_done_callback(rt.tasks.discard)

        return StreamingResponse(body_iter(), media_type="text/event-stream", headers=hdr)

    return app


app = create_app()
