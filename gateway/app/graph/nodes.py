from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, cast

from ..auth import Authenticator, AuthError
from ..config import Settings
from ..events import LogEvent, SpanEvent
from ..lineage import Lineage
from ..policy import PolicyStore
from ..pricing import compute_cost
from ..profile import COMPLEXITY, KNOWN_TASKS, TaskProfile, build_profile, last_user_text
from ..providers.base import CallOutcome
from ..providers.health import ProviderHealth
from ..providers.registry import ProviderRegistry
from ..router import select_route
from ..safety import (
    called_tool_names,
    declared_tool_names,
    defang_markup,
    message_chars,
    redact,
    redact_messages,
    reply_is_empty,
    scan,
    scan_messages,
)
from .state import GatewayState

# Outcome classes the in-request feedback loop may retry on another model.
RETRYABLE = frozenset(
    {"timeout", "rate_limit", "server_error", "connection", "malformed", "validator_failed", "auth_error",
     "empty_output", "unsafe_output"}
)

LARGE_PROMPT_CHARS = 50_000

Node = Callable[[GatewayState], Awaitable[dict[str, Any]]]


@dataclass
class Deps:
    settings: Settings
    policy_store: PolicyStore
    registry: ProviderRegistry
    lineage: Lineage
    auth: Authenticator
    health: ProviderHealth


def _err(status: int, type_: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "type": type_, "message": message, **extra}


def parse_mock_behavior(header: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (header or "").split(","):
        key, sep, val = part.strip().partition("=")
        if sep and key:
            out[key.strip()] = val.strip()
    return out


def _headers(state: GatewayState) -> dict[str, str]:
    h = {
        "x-gateway-call-id": state.get("span_id", ""),
        "x-gateway-run-id": state.get("run_id", ""),
        "x-gateway-policy-version": str(state.get("policy_version", "")),
        "x-gateway-attempts": str(len(state.get("attempts", []))),
    }
    sel = state.get("selected_route")
    if sel:
        h["x-gateway-model"] = sel.primary.id
        h["x-gateway-route-reason"] = sel.reason.encode("ascii", "replace").decode()[:300]
    if state.get("safety_notes"):
        h["x-gateway-safety"] = ";".join(state["safety_notes"])[:200]
    return h


def _validate(state: GatewayState, o: CallOutcome) -> str:
    body = o.body or {}
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or "message" not in choices[0]:
        return "malformed"
    if state["task_profile"].needs_json:
        msg = choices[0]["message"]
        content = msg.get("content")
        if isinstance(content, str):
            try:
                json.loads(content)
            except ValueError:
                return "validator_failed"
        elif not msg.get("tool_calls"):
            return "validator_failed"
    return "ok"


def _check_output(state: GatewayState, o: CallOutcome, notes: list[str]) -> str:
    """The safety gate's output half (non-streamed replies only): an empty reply, or a secret in the reply."""
    rules = state["policy"].safety
    body = o.body or {}
    if not rules.enabled:
        return "ok"
    if rules.output.empty == "fallback" and reply_is_empty(body):
        return "empty_output"
    choices = body.get("choices")
    msg = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    if rules.output.tool_calls == "declared_only" and isinstance(msg, dict):
        called = called_tool_names(msg)
        if called and not set(called) <= declared_tool_names(state["request"].model_extra or {}) | declared_tool_names(
            {"tools": state["request"].tools}
        ):
            return "unsafe_output"  # the model asked for a tool the caller never offered: an injected instruction, or a bug
    leaks = (
        isinstance(msg, dict) and isinstance(msg.get("content"), str)
        and rules.output.leaked_secrets != "allow" and bool(scan(msg["content"]).secrets)
    )
    if leaks and isinstance(msg, dict):
        if rules.output.leaked_secrets == "fallback":
            return "unsafe_output"
        msg["content"] = redact(msg["content"], secrets=True)
        notes.append("redacted:reply-secrets")
    if rules.output.markup == "defang" and isinstance(msg, dict):
        for field in ("content", "reasoning"):
            if isinstance(msg.get(field), str):
                msg[field], counts = defang_markup(msg[field])
                if counts and "defanged:markup" not in notes:
                    notes.append("defanged:markup")
    return "ok"


# ---- conditional edge functions (pure) ------------------------------------

def c_valid(state: GatewayState) -> str:
    return "respond_error" if state.get("error") else "guard"


def c_guard(state: GatewayState) -> str:
    return "respond_error" if state.get("error") else "profile"


def c_conf(state: GatewayState) -> str:
    policy = state["policy"]
    cfg = policy.llm_classify
    if state.get("origin") == "internal" or not cfg.enabled or not cfg.model:
        return "route"
    return "route" if state["task_profile"].confidence >= cfg.threshold else "llm_classify"


def c_cand(state: GatewayState) -> str:
    return "respond_error" if state.get("error") else "call"


def c_outcome(state: GatewayState) -> str:
    cls = state["outcome_class"]
    if cls == "ok":
        return "respond"
    if cls not in RETRYABLE:
        return "respond_error"
    # A pinned model with a rejected key cannot be helped by retrying or falling back.
    if cls == "auth_error" and state.get("pinned") and not state.get("allow_fallback"):
        return "respond_error"
    if len(state["attempts"]) >= state["retry_budget"]:
        return "respond_error"
    if state["deadline"] - time.monotonic() < 0.25:
        return "respond_error"
    return "update_context"


_SAFE_ID = re.compile(r"[A-Za-z0-9_.:\-]{1,100}")


def safe_id(value: str | None) -> str | None:
    """Caller-supplied labels end up in logs, tags and dashboards: accept only short plain
    identifiers so they cannot forge log lines, inject markup or blow up tag cardinality."""
    return value if value and _SAFE_ID.fullmatch(value) else None


# ---- nodes -----------------------------------------------------------------

def make_nodes(deps: Deps) -> dict[str, Node]:
    def stamped(state: GatewayState, kind: str, severity: str, message: str, **kw: Any) -> LogEvent:
        return LogEvent(source="gateway", kind=kind, severity=severity, message=message,
                        run_id=state.get("run_id"), span_id=state.get("span_id"), **kw)

    async def intake(state: GatewayState) -> dict[str, Any]:
        req, h = state["request"], state["headers"]
        now = time.monotonic()
        policy = await deps.policy_store.get()
        model = (req.model or "auto").strip()
        update: dict[str, Any] = {
            "span_id": "call_" + uuid.uuid4().hex[:20],
            "run_id": safe_id(h.get("x-run-id")) or "run_" + uuid.uuid4().hex[:16],
            "node_role": safe_id(h.get("x-node-role")) or "unknown",
            "parent_call_id": safe_id(h.get("x-parent-call-id")),
            "idempotency_key": safe_id(h.get("idempotency-key")),
            "task_hint": safe_id(h.get("x-task-type")),
            "quality_hint": safe_id(h.get("x-quality-hint")),
            "origin": state.get("origin", "external"),
            "pinned": None if model in ("auto", "") or model.startswith("gateway/") else model,
            "allow_fallback": h.get("x-gateway-allow-fallback", "").lower() in ("1", "true"),
            "policy": policy,
            "policy_version": policy.version,
            "policy_status_served": "active",
            "started": now,
            "deadline": now + policy.retry.deadline_s,
            "retry_budget": policy.retry.max_attempts,
            "attempts": [],
            "excluded_models": [],
            "escalate_tiers": 0,
            "internal_spans": [],
            "log_events": [],
            "outcome": None,
            "error": None,
            "api_key_name": None,
            "api_key_id": None,
            "user_id": None,
            "allowed_models": None,
            "auth_method": "none",
            "environment": deps.settings.environment,
        }
        try:
            principal = await deps.auth.authenticate(h.get("authorization"))
        except AuthError as e:
            update["error"] = _err(e.status, e.error, e.description, error_class=e.error_class,
                                   headers=e.headers)
            return update
        if principal.auth_method != "none":
            update["api_key_name"] = principal.name
            update["api_key_id"] = principal.key_id
            update["user_id"] = principal.user_id
        update["auth_method"] = principal.auth_method
        update["allowed_models"] = principal.allowed_models

        if update["pinned"] and principal.allowed_models and update["pinned"] not in principal.allowed_models:
            update["error"] = _err(403, "permission_error",
                                   f"This credential may not use model {update['pinned']}",
                                   error_class="forbidden_model")
            return update
        if not req.messages:
            update["error"] = _err(400, "invalid_request_error", "messages must not be empty",
                                   error_class="invalid_request")
        return update

    async def guard(state: GatewayState) -> dict[str, Any]:
        """The safety gate's input half: look at what is about to leave, then block, redact or mark it sensitive."""
        policy_safety = state["policy"].safety
        update: dict[str, Any] = {"sensitive": False, "safety_notes": []}
        if not policy_safety.enabled:
            return update
        rules = policy_safety.input
        msgs = state["request"].messages
        # Regex work on a very large prompt would hold the event loop; hand it to a thread (GIL switching keeps
        # other requests moving). A typical prompt takes about 0.1 ms and stays inline.
        found = await asyncio.to_thread(scan_messages, msgs) if message_chars(msgs) > LARGE_PROMPT_CHARS else scan_messages(msgs)
        notes: list[str] = []
        logs: list[LogEvent] = []
        sensitive = state["headers"].get("x-gateway-sensitivity", "").strip().lower() == "sensitive"
        if sensitive:
            notes.append("sensitive:requested")
        redact_secrets = redact_pii = defang = False

        def blocked(what: str, kinds: Any) -> dict[str, Any]:
            names = ", ".join(sorted(kinds)) or what
            logs.append(stamped(state, "input_blocked", "warn", f"request blocked by the safety gate: {what}",
                                tags={"reason": what, "kinds": names}, fingerprint=f"input-blocked:{what}"))
            return {**update, "safety_notes": [*notes, f"blocked:{what}"], "log_events": state.get("log_events", []) + logs,
                    "error": _err(400, "safety_error",
                                  f"The request was blocked: it appears to contain {what} ({names}). Remove it and retry.",
                                  error_class="input_blocked")}

        if found.secrets:
            if rules.secrets == "block":
                return blocked("a secret", found.secrets)
            if rules.secrets == "redact":
                redact_secrets = True
                notes.append("redacted:secrets")
        if found.pii:
            if rules.pii == "block":
                return blocked("personal data", found.pii)
            if rules.pii == "redact":
                redact_pii = True
                notes.append("redacted:pii")
            elif rules.pii == "sensitive":
                sensitive = True
                notes.append("sensitive:pii")
        if found.injection or found.hidden:
            if rules.injection == "block":
                return blocked("a prompt-injection phrase or hidden text", {"injection"})
            if rules.injection == "defang":
                defang = True
                notes.append("defanged:injection")
            elif rules.injection == "warn":
                notes.append("flagged:injection")
        if found.any:
            logs.append(stamped(
                state, "input_guard", "info", "safety gate acted on the request: " + (", ".join(notes) or "noted only"),
                tags={"secret_kinds": ",".join(sorted(found.secrets)), "pii_kinds": ",".join(sorted(found.pii)),
                      "injection": bool(found.injection), "hidden_text": bool(found.hidden), "sensitive": sensitive}))
        if redact_secrets or redact_pii or defang:
            args = {"secrets": redact_secrets, "pii": redact_pii, "defang": defang}
            cleaned = (
                await asyncio.to_thread(lambda: redact_messages(msgs, **args))
                if message_chars(msgs) > LARGE_PROMPT_CHARS
                else redact_messages(msgs, **args)
            )
            update["request"] = state["request"].model_copy(update={"messages": cleaned})
        update.update({"sensitive": sensitive, "safety_notes": notes, "log_events": state.get("log_events", []) + logs})
        return update

    async def profile(state: GatewayState) -> dict[str, Any]:
        try:
            p = build_profile(
                state["request"],
                task_hint=state.get("task_hint"),
                node_role=state.get("node_role", "unknown"),
                quality_hint=state.get("quality_hint"),
            )
        except Exception:  # fail open: a default profile beats a failed request
            p = TaskProfile(confidence=0.0, source="default")
        return {"task_profile": p}

    async def llm_classify(state: GatewayState) -> dict[str, Any]:
        policy = state["policy"]
        cfg = policy.llm_classify
        spec = policy.model_by_id(cfg.model)
        prof = state["task_profile"]
        if spec is None or spec.provider not in policy.providers:
            return {}
        provider = deps.registry.get(spec.provider, policy.providers[spec.provider])
        text = last_user_text(state["request"])[:2000]
        payload = {
            "model": spec.model,
            "messages": [
                {"role": "system", "content": (
                    "TASK_CLASSIFIER. Reply with JSON only: "
                    '{"task_type": one of ' + json.dumps(sorted(KNOWN_TASKS)) + ', '
                    '"complexity": "low"|"medium"|"high"}.')},
                {"role": "user", "content": text},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 60,
            "temperature": 0,
        }
        # Recursion guard: this call never passes through profile/route.
        outcome = await provider.complete(spec, payload, timeout_s=cfg.timeout_s, extra={})
        usage = outcome.usage or {}
        cost, shadow = compute_cost(spec, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        span = SpanEvent(
            span_id="call_" + uuid.uuid4().hex[:20],
            run_id=state["run_id"],
            parent_span_id=state["span_id"],
            origin="internal",
            node_role="router_classifier",
            task_type="classification",
            policy_version=state.get("policy_version"),
            provider=spec.provider,
            model=spec.id,
            family=spec.family,
            status="ok" if outcome.ok else "error",
            error_class=outcome.error_class,
            http_status=outcome.status_code,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            cost_usd=cost,
            shadow_cost_usd=shadow,
            latency_ms=outcome.latency_ms,
        )
        update: dict[str, Any] = {"internal_spans": [*state.get("internal_spans", []), span]}
        if outcome.ok:
            try:
                content = (outcome.body or {})["choices"][0]["message"]["content"]
                data = json.loads(content)
                task = data.get("task_type")
                cx = data.get("complexity")
                update["task_profile"] = replace(
                    prof,
                    task_type=task if task in KNOWN_TASKS else prof.task_type,
                    complexity=cx if cx in COMPLEXITY else prof.complexity,
                    confidence=0.9,
                    source="llm",
                )
            except (KeyError, IndexError, TypeError, ValueError):
                pass  # keep the rules profile
        return update

    async def route(state: GatewayState) -> dict[str, Any]:
        parent = None
        if state.get("parent_call_id"):
            parent = await deps.lineage.get(str(state["parent_call_id"]))
        allowed = state.get("allowed_models")
        decision = select_route(
            state["policy"],
            state["task_profile"],
            node_role=state.get("node_role", "unknown"),
            parent=parent,
            excluded=set(state.get("excluded_models", [])),
            escalate_tiers=state.get("escalate_tiers", 0),
            pinned=state.get("pinned"),
            stream=bool(state["request"].stream),
            env=os.environ,
            allowed=set(allowed) if allowed else None,
            unhealthy=deps.health.open_providers(),
            sensitive=bool(state.get("sensitive")),
        )
        if decision is None and state.get("sensitive") and not state.get("attempts"):
            # Distinguish "nothing fits" from "something fits, but no provider is cleared to see this content".
            without = select_route(
                state["policy"], state["task_profile"], node_role=state.get("node_role", "unknown"), parent=parent,
                pinned=state.get("pinned"), stream=bool(state["request"].stream), env=os.environ,
                allowed=set(allowed) if allowed else None, unhealthy=deps.health.open_providers(),
            )
            if without is not None:
                return {"error": _err(
                    422, "safety_error",
                    "This request contains sensitive content and no provider cleared for it can serve it.",
                    error_class="no_eligible_provider")}
        if decision is None:
            attempts = state.get("attempts", [])
            if attempts:
                cls = state.get("outcome_class", "internal")
                return {"error": _err(
                    504 if cls == "timeout" else 502, "upstream_error",
                    f"No candidates left after {len(attempts)} failed attempt(s); last error: {cls}",
                    error_class=cls,
                    attempts=[{"model": a["model"], "outcome": a["outcome_class"]} for a in attempts])}
            return {"error": _err(503, "no_candidate", "No model satisfies the request constraints",
                                  error_class="no_candidate")}
        return {"selected_route": decision}

    async def call(state: GatewayState) -> dict[str, Any]:
        policy = state["policy"]
        sel = state["selected_route"].primary
        provider = deps.registry.get(sel.provider, policy.providers[sel.provider])
        req = state["request"]
        payload = req.model_dump(exclude_none=True)
        payload["model"] = sel.model
        remaining = state["deadline"] - time.monotonic()
        timeout = max(0.5, min(policy.retry.per_attempt_timeout_s, remaining))
        extra: dict[str, Any] = {}
        if deps.settings.mock_control_enabled:
            extra["mock_behavior"] = parse_mock_behavior(state["headers"].get("x-mock-behavior"))
        try:
            if req.stream:
                outcome = await provider.open_stream(
                    sel, payload, timeout_s=timeout,
                    first_token_timeout_s=min(policy.retry.first_token_timeout_s, timeout), extra=extra)
            else:
                outcome = await provider.complete(sel, payload, timeout_s=timeout, extra=extra)
        except Exception as e:  # providers should not raise; never let one kill the request
            outcome = CallOutcome(ok=False, error_class="internal", error_message=repr(e)[:200])
        return {"outcome": outcome}

    async def evaluate(state: GatewayState) -> dict[str, Any]:
        o = cast(CallOutcome, state["outcome"])
        sel = state["selected_route"].primary
        cls = "ok" if o.ok else (o.error_class or "internal")
        out_notes: list[str] = []
        if o.ok and not state["request"].stream:
            cls = _validate(state, o)
            if cls == "ok":
                cls = _check_output(state, o, out_notes)
        prev = state["attempts"][-1]["outcome_class"] if state["attempts"] else None
        attempt = {
            "attempt_no": len(state["attempts"]) + 1,
            "model": sel.id,
            "provider": sel.provider,
            "outcome_class": cls,
            "latency_ms": round(o.latency_ms, 2),
            "ttft_ms": None if o.ttft_ms is None else round(o.ttft_ms, 2),
            "error": (o.error_message or None) if cls != "ok" else None,
            "fallback_reason": prev,
        }

        # Provider health: feeds the circuit breaker; surfaces key failover as log events.
        new_logs: list[LogEvent] = []
        opened = deps.health.record(sel.provider, cls, o.retry_after_s)
        if opened:
            new_logs.append(stamped(
                state, "circuit_opened", "error", f"circuit opened for {sel.provider}: {opened}",
                tags={"provider": sel.provider, "error_class": cls, "reason": opened},
                fingerprint=f"circuit:{sel.provider}"))
        for note in o.notes:
            new_logs.append(stamped(
                state, "provider_key_failover", "warn", f"{sel.provider}: {note}",
                tags={"provider": sel.provider, "model": sel.id},
                fingerprint=f"key-failover:{sel.provider}"))

        if out_notes:
            new_logs.append(stamped(state, "output_guard", "warn", "safety gate acted on the reply: " + ", ".join(out_notes),
                                    tags={"model": sel.id, "provider": sel.provider}))
        return {
            "outcome_class": cls,
            "attempts": state["attempts"] + [attempt],
            "log_events": state.get("log_events", []) + new_logs,
            "safety_notes": state.get("safety_notes", []) + out_notes if cls == "ok" else state.get("safety_notes", []),
        }

    async def update_context(state: GatewayState) -> dict[str, Any]:
        policy = state["policy"]
        sel = state["selected_route"].primary
        excluded = list(state["excluded_models"])
        pinned = state.get("pinned")
        cls = state["outcome_class"]

        if not pinned:
            # Exclude what just failed; a rejected key takes the whole provider with it.
            doomed = (
                [m.id for m in policy.models if m.provider == sel.provider]
                if cls == "auth_error" else [sel.id]
            )
            excluded.extend(m for m in doomed if m not in excluded)
        else:
            # A pinned model is retried in place, with jittered exponential backoff.
            n = len(state["attempts"])
            delay = min(policy.retry.backoff_base_s * (2 ** (n - 1)), policy.retry.backoff_max_s)
            delay *= 0.5 + random.random() / 2  # noqa: S311 (backoff jitter, not security)
            retry_after = getattr(state.get("outcome"), "retry_after_s", None)
            if retry_after:
                delay = max(delay, min(retry_after, policy.retry.backoff_max_s))
            delay = max(0.0, min(delay, state["deadline"] - time.monotonic() - 0.5))
            if delay > 0:
                await asyncio.sleep(delay)

        esc = state.get("escalate_tiers", 0) + (1 if cls == "validator_failed" else 0)
        return {"excluded_models": excluded, "escalate_tiers": esc}

    async def respond(state: GatewayState) -> dict[str, Any]:
        o = cast(CallOutcome, state["outcome"])
        sel = state["selected_route"].primary
        await deps.lineage.put(state["span_id"], sel.id, sel.family)
        hdr = _headers(state)
        if o.stream is not None:
            final = {"kind": "stream", "status": 200, "handle": o.stream, "headers": hdr}
        else:
            final = {"kind": "json", "status": 200, "body": o.body, "headers": hdr}
        return {"final": final}

    async def respond_error(state: GatewayState) -> dict[str, Any]:
        err = state.get("error")
        attempts = state.get("attempts", [])
        if err is None:
            cls = state.get("outcome_class") or "internal"
            err = _err(
                504 if cls == "timeout" else 502, "upstream_error",
                f"All {len(attempts)} attempt(s) failed; last error: {cls}",
                error_class=cls,
                attempts=[{"model": a["model"], "outcome": a["outcome_class"]} for a in attempts])
        body: dict[str, Any] = {"error": {
            "message": err["message"], "type": err["type"],
            "code": err.get("error_class") or err["type"]}}
        if "attempts" in err:
            body["error"]["attempts"] = err["attempts"]
        headers = {**_headers(state), **(err.get("headers") or {})}
        final = {"kind": "error", "status": err["status"], "body": body, "headers": headers,
                 "error_class": err.get("error_class") or err["type"]}
        return {"final": final}

    return {
        "intake": intake, "guard": guard, "profile": profile, "llm_classify": llm_classify, "route": route,
        "call": call, "evaluate": evaluate, "update_context": update_context,
        "respond": respond, "respond_error": respond_error,
    }


def timed(name: str, fn: Node) -> Node:
    """Records per-node wall time (ms) into state['timings']."""

    async def wrapper(state: GatewayState) -> dict[str, Any]:
        t0 = time.perf_counter()
        update = await fn(state)
        dt = (time.perf_counter() - t0) * 1000
        timings = dict(state.get("timings") or {})
        timings[name] = timings.get(name, 0.0) + dt
        update = dict(update or {})
        update["timings"] = timings
        return update

    return wrapper


def make_timed_nodes(deps: Deps) -> dict[str, Node]:
    return {name: timed(name, fn) for name, fn in make_nodes(deps).items()}
