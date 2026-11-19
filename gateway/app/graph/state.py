from __future__ import annotations

from typing import Any, TypedDict

from ..events import LogEvent, SpanEvent
from ..policy import Policy
from ..profile import TaskProfile
from ..providers.base import CallOutcome
from ..router import RouteDecision
from ..schemas import ChatRequest


class GatewayState(TypedDict, total=False):
    # inputs
    request: ChatRequest
    headers: dict[str, str]

    # identity
    span_id: str
    run_id: str
    node_role: str
    parent_call_id: str | None
    origin: str  # external | internal
    api_key_name: str | None
    api_key_id: str | None
    user_id: str | None
    idempotency_key: str | None
    task_hint: str | None
    quality_hint: str | None
    pinned: str | None
    allow_fallback: bool
    allowed_models: list[str] | None
    auth_method: str
    environment: str

    # safety gate
    sensitive: bool  # personal data present, or the caller said so: only eligible providers may see it
    safety_notes: list[str]  # what the gate did, for the x-gateway-safety response header

    # policy
    policy: Policy
    policy_version: int
    policy_status_served: str

    # decision
    task_profile: TaskProfile
    selected_route: RouteDecision
    excluded_models: list[str]
    escalate_tiers: int

    # execution
    started: float  # time.monotonic()
    deadline: float  # time.monotonic()
    retry_budget: int
    attempts: list[dict[str, Any]]
    outcome: CallOutcome | None
    outcome_class: str

    # result
    final: dict[str, Any]
    error: dict[str, Any] | None

    # observability
    timings: dict[str, float]
    internal_spans: list[SpanEvent]
    log_events: list[LogEvent]
