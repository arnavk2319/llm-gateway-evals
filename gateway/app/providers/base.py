from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

from ..policy import ModelSpec


def normalize_usage(u: dict[str, Any] | None) -> dict[str, int]:
    u = u or {}
    details = u.get("prompt_tokens_details") or {}
    return {
        "prompt_tokens": int(u.get("prompt_tokens") or 0),
        "completion_tokens": int(u.get("completion_tokens") or 0),
        "cached_tokens": int(details.get("cached_tokens") or 0),
    }


def classify_status(code: int) -> str:
    if code in (401, 403):
        return "auth_error"
    if code == 429:
        return "rate_limit"
    if code in (408, 504):
        return "timeout"
    if code >= 500:
        return "server_error"
    return "client_error"


@dataclass
class StreamHandle:
    """A provider stream that has already produced its first chunk."""

    first: str
    iterator: AsyncIterator[str]
    closer: Callable[[], Awaitable[None]]
    usage: dict[str, int] = field(default_factory=dict)
    completion_chars: int = 0
    closed: bool = False

    def observe(self, chunk: str) -> None:
        for line in chunk.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            if isinstance(obj, dict):
                if obj.get("usage"):
                    self.usage = normalize_usage(obj["usage"])
                for ch in obj.get("choices") or []:
                    content = (ch.get("delta") or {}).get("content")
                    if isinstance(content, str):
                        self.completion_chars += len(content)

    async def aclose(self) -> None:
        if not self.closed:
            self.closed = True
            await self.closer()


@dataclass
class CallOutcome:
    ok: bool
    status_code: int | None = None
    error_class: str | None = None
    error_message: str | None = None
    latency_ms: float = 0.0
    ttft_ms: float | None = None
    body: dict[str, Any] | None = None
    stream: StreamHandle | None = None
    usage: dict[str, int] = field(default_factory=dict)
    retry_after_s: float | None = None  # from a 429 Retry-After header
    notes: list[str] = field(default_factory=list)  # e.g. provider key failover events


class Provider(Protocol):
    """Providers never raise: every failure becomes a CallOutcome(ok=False)."""

    async def complete(
        self, spec: ModelSpec, payload: dict[str, Any], *, timeout_s: float, extra: dict[str, Any]
    ) -> CallOutcome: ...

    async def open_stream(
        self,
        spec: ModelSpec,
        payload: dict[str, Any],
        *,
        timeout_s: float,
        first_token_timeout_s: float,
        extra: dict[str, Any],
    ) -> CallOutcome: ...
