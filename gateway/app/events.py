from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .pricing import compute_cost


@dataclass
class LogEvent:
    """One normalized, tagged and timestamped log record, from any source.

    `source` says where it came from (gateway, vercel, neon, sentry, hook), `kind` what it
    is, and `tags` carry the dimensions you filter on. `run_id` / `span_id` correlate it with
    the rest of an agent run. `external_id` makes ingestion from outside idempotent.
    """

    source: str
    kind: str
    severity: str  # debug | info | warn | error | critical
    message: str
    ts: float = field(default_factory=time.time)
    tags: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    span_id: str | None = None
    trace_id: str | None = None
    fingerprint: str | None = None
    payload: dict[str, Any] | None = None
    external_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AttemptEvent:
    attempt_no: int
    model: str | None
    provider: str | None
    outcome_class: str
    latency_ms: float | None = None
    ttft_ms: float | None = None
    error: str | None = None
    fallback_reason: str | None = None


@dataclass
class SpanEvent:
    span_id: str
    run_id: str
    parent_span_id: str | None = None
    origin: str = "external"
    node_role: str | None = None
    task_type: str | None = None
    complexity: str | None = None
    profile: dict[str, Any] | None = None
    profile_confidence: float | None = None
    policy_version: int | None = None
    policy_status_served: str | None = None
    provider: str | None = None
    model: str | None = None
    family: str | None = None
    route_reason: str | None = None
    route_relaxed: list[str] = field(default_factory=list)
    stream: bool = False
    status: str = "ok"  # ok | error | stream_error
    error_class: str | None = None
    http_status: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None
    cost_usd: float | None = None
    shadow_cost_usd: float | None = None
    latency_ms: float | None = None
    ttft_ms: float | None = None
    attempt_count: int = 1
    fell_back: bool = False
    node_timings: dict[str, float] = field(default_factory=dict)
    overhead_ms: float | None = None
    idempotency_key: str | None = None
    api_key_name: str | None = None
    attempts: list[AttemptEvent] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_span_event(
    state: dict[str, Any],
    *,
    latency_ms: float,
    graph_ms: float,
    usage: dict[str, int] | None = None,
    completion_chars: int = 0,
    status: str | None = None,
    error_class: str | None = None,
) -> SpanEvent:
    final = state.get("final") or {}
    sel = state.get("selected_route")
    spec = sel.primary if sel else None
    prof = state.get("task_profile")
    attempts = [AttemptEvent(**a) for a in state.get("attempts", [])]
    ok = final.get("kind") in ("json", "stream")
    usage = usage or {}

    in_tok = usage.get("prompt_tokens") or (prof.context_tokens if prof else 0)
    out_tok = usage.get("completion_tokens") or completion_chars // 4
    cost, shadow = compute_cost(spec, in_tok, out_tok) if (spec and ok) else (0.0, 0.0)

    timings = state.get("timings") or {}
    overhead = max(0.0, graph_ms - timings.get("call", 0.0) - timings.get("llm_classify", 0.0))
    outcome = state.get("outcome")

    return SpanEvent(
        span_id=state["span_id"],
        run_id=state["run_id"],
        parent_span_id=state.get("parent_call_id"),
        origin=state.get("origin", "external"),
        node_role=state.get("node_role"),
        task_type=prof.task_type if prof else None,
        complexity=prof.complexity if prof else None,
        profile=prof.to_dict() if prof else None,
        profile_confidence=prof.confidence if prof else None,
        policy_version=state.get("policy_version"),
        policy_status_served=state.get("policy_status_served"),
        provider=spec.provider if spec else None,
        model=spec.id if spec else None,
        family=spec.family if spec else None,
        route_reason=sel.reason if sel else None,
        route_relaxed=list(sel.relaxed) if sel else [],
        stream=bool(state["request"].stream) if state.get("request") else False,
        status=status or ("ok" if ok else "error"),
        error_class=error_class or final.get("error_class"),
        http_status=final.get("status"),
        prompt_tokens=in_tok if ok else None,
        completion_tokens=out_tok if ok else None,
        cached_tokens=usage.get("cached_tokens", 0) if ok else None,
        cost_usd=cost,
        shadow_cost_usd=shadow,
        latency_ms=latency_ms,
        ttft_ms=outcome.ttft_ms if (outcome is not None and ok) else None,
        attempt_count=max(1, len(attempts)),
        fell_back=len(attempts) > 1,
        node_timings={k: round(v, 3) for k, v in timings.items()},
        overhead_ms=round(overhead, 3),
        idempotency_key=state.get("idempotency_key"),
        api_key_name=state.get("api_key_name"),
        attempts=attempts,
    )


def build_log_events(state: dict[str, Any]) -> list[LogEvent]:
    """Tagged, stamped log events for everything noteworthy that happened to one request.

    Successful requests are not duplicated here; they live in `spans` and are merged into
    the same stream by the `log_stream` view.
    """
    run_id, span_id = state.get("run_id"), state.get("span_id")
    prof = state.get("task_profile")
    base = {
        k: v
        for k, v in {
            "node_role": state.get("node_role"),
            "task_type": prof.task_type if prof else None,
            "policy_version": state.get("policy_version"),
            "key": state.get("api_key_name"),
            "env": state.get("environment"),
        }.items()
        if v is not None
    }

    out: list[LogEvent] = list(state.get("log_events", []))  # already stamped by the nodes
    attempts = state.get("attempts", [])
    for a in attempts:
        if a["outcome_class"] == "ok":
            continue
        out.append(
            LogEvent(
                source="gateway",
                kind="attempt_failed",
                severity="error" if a["outcome_class"] == "auth_error" else "warn",
                message=f"{a['model']} attempt {a['attempt_no']} failed: {a['outcome_class']}",
                tags={**base, "provider": a["provider"], "model": a["model"],
                      "error_class": a["outcome_class"], "attempt_no": a["attempt_no"]},
                run_id=run_id,
                span_id=span_id,
                fingerprint=f"attempt:{a['provider']}:{a['outcome_class']}",
                payload={"error": a["error"], "latency_ms": a["latency_ms"],
                         "fallback_reason": a["fallback_reason"]},
            )
        )

    final = state.get("final") or {}
    sel = state.get("selected_route")
    if final.get("kind") == "error":
        body = (final.get("body") or {}).get("error", {})
        ec = final.get("error_class")
        rejected = not prof  # failed before the request was even profiled (auth, validation)
        tags = {**base, "error_class": ec, "http_status": final.get("status")}
        if sel:
            tags["model"], tags["provider"] = sel.primary.id, sel.primary.provider
        out.append(
            LogEvent(
                source="gateway",
                kind="request_rejected" if rejected else "request_failed",
                severity="warn" if rejected or final.get("status", 500) < 500 else "error",
                message=f"{final.get('status')} {ec}: {body.get('message', '')}"[:300],
                tags=tags,
                run_id=run_id,
                span_id=span_id,
                fingerprint=f"{'rejected' if rejected else 'failed'}:{ec}:{tags.get('provider', '-')}",
                payload={"attempts": body.get("attempts")},
            )
        )
    elif len(attempts) > 1:
        out.append(
            LogEvent(
                source="gateway",
                kind="fallback",
                severity="info",
                message=f"served by {attempts[-1]['model']} after {len(attempts)} attempts",
                tags={**base, "provider": attempts[-1]["provider"], "model": attempts[-1]["model"],
                      "attempt_count": len(attempts)},
                run_id=run_id,
                span_id=span_id,
                fingerprint=f"fallback:{attempts[0]['model']}",
            )
        )
    return out
