from __future__ import annotations

import asyncio
import os
import time
from typing import Any, AsyncIterator

import httpx

from ..policy import ModelSpec
from .base import CallOutcome, StreamHandle, classify_status, normalize_usage


class OpenAICompatProvider:
    """One adapter for every OpenAI-style chat completions API
    (Ollama, Groq, OpenRouter, Together, vLLM, ...)."""

    def __init__(self, name: str, base_url: str, api_key_env: str | None, client: httpx.AsyncClient):
        self.name = name
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key_env = api_key_env
        self._client = client

    def _headers(self) -> dict[str, str]:
        key = os.environ.get(self._key_env) if self._key_env else None
        return {"Authorization": f"Bearer {key}"} if key else {}

    async def complete(
        self, spec: ModelSpec, payload: dict[str, Any], *, timeout_s: float, extra: dict[str, Any]
    ) -> CallOutcome:
        t0 = time.perf_counter()
        try:
            resp = await self._client.post(
                self._url, json=payload, headers=self._headers(), timeout=timeout_s
            )
        except httpx.TimeoutException:
            return self._fail("timeout", None, "request timed out", t0)
        except httpx.TransportError as e:
            return self._fail("connection", None, repr(e)[:200], t0)

        latency = (time.perf_counter() - t0) * 1000
        if resp.status_code >= 400:
            return self._fail(classify_status(resp.status_code), resp.status_code, resp.text[:300], t0)
        try:
            body = resp.json()
        except ValueError:
            return self._fail("malformed", resp.status_code, "response was not JSON", t0)
        return CallOutcome(
            ok=True,
            status_code=resp.status_code,
            latency_ms=latency,
            ttft_ms=latency,
            body=body,
            usage=normalize_usage(body.get("usage")),
        )

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
        payload = dict(payload)
        payload["stream"] = True
        payload.setdefault("stream_options", {"include_usage": True})
        req = self._client.build_request(
            "POST", self._url, json=payload, headers=self._headers(),
            timeout=httpx.Timeout(timeout_s, connect=min(5.0, timeout_s)),
        )
        try:
            resp = await asyncio.wait_for(self._client.send(req, stream=True), timeout=timeout_s)
        except (httpx.TimeoutException, asyncio.TimeoutError):
            return self._fail("timeout", None, "connect timed out", t0)
        except httpx.TransportError as e:
            return self._fail("connection", None, repr(e)[:200], t0)

        if resp.status_code >= 400:
            text = (await resp.aread()).decode(errors="replace")[:300]
            await resp.aclose()
            return self._fail(classify_status(resp.status_code), resp.status_code, text, t0)

        async def chunks() -> AsyncIterator[str]:
            async for line in resp.aiter_lines():
                if line.strip():
                    yield line + "\n\n"

        it = chunks()
        try:
            first = await asyncio.wait_for(it.__anext__(), timeout=first_token_timeout_s)
        except StopAsyncIteration:
            await resp.aclose()
            return self._fail("malformed", resp.status_code, "empty stream", t0)
        except (asyncio.TimeoutError, httpx.TimeoutException):
            await resp.aclose()
            return self._fail("timeout", resp.status_code, "no first token in time", t0)
        except httpx.TransportError as e:
            await resp.aclose()
            return self._fail("connection", resp.status_code, repr(e)[:200], t0)

        ttft = (time.perf_counter() - t0) * 1000
        handle = StreamHandle(first=first, iterator=it, closer=resp.aclose)
        handle.observe(first)
        return CallOutcome(ok=True, status_code=resp.status_code, latency_ms=ttft, ttft_ms=ttft, stream=handle)

    @staticmethod
    def _fail(cls: str, status: int | None, msg: str, t0: float) -> CallOutcome:
        return CallOutcome(
            ok=False,
            status_code=status,
            error_class=cls,
            error_message=msg,
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
