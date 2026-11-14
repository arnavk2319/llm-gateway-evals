from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .pricing import compute_cost


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
