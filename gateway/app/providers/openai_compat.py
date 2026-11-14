from __future__ import annotations

import asyncio
import os
import time
from typing import Any, AsyncIterator

import httpx

from ..policy import ModelSpec
from .base import CallOutcome, StreamHandle, classify_status, normalize_usage

# How long a provider key that returned 401/403 is skipped before being tried again.
AUTH_COOLDOWN_S = 300.0


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    try:
        return max(0.0, float(raw)) if raw else None
    except ValueError:
        return None


class OpenAICompatProvider:
    """One adapter for every OpenAI-style chat completions API
    (Ollama, Groq, OpenRouter, Together, vLLM, ...).

    Supports a primary and a secondary API key. A 401/403 marks that key as bad
    for a cooldown and the request is retried once on the next key, so a rotated
    or revoked key does not take the provider down.
    """

    def __init__(self, name: str, base_url: str, api_key_envs: list[str], client: httpx.AsyncClient):
        self.name = name
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key_envs = list(api_key_envs)
        self._client = client
        self._bad_until: dict[int, float] = {}

    # ---- key handling -------------------------------------------------------

    def _key_order(self) -> list[int]:
        """Key indices to try, in order. -1 means 'no key' (e.g. local Ollama)."""
        present = [i for i, env in enumerate(self._key_envs) if os.environ.get(env)]
        if not present:
            return [-1]
        now = time.monotonic()
        good = [i for i in present if self._bad_until.get(i, 0.0) <= now]
        return good or present

    def _headers(self, i: int) -> dict[str, str]:
        if i < 0:
            return {}
        key = os.environ.get(self._key_envs[i])
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _mark_bad(self, i: int, status: int | None) -> str:
        self._bad_until[i] = time.monotonic() + AUTH_COOLDOWN_S
        return f"api key #{i + 1} rejected with HTTP {status}"

    # ---- non-streaming ------------------------------------------------------

    async def complete(
        self, spec: ModelSpec, payload: dict[str, Any], *, timeout_s: float, extra: dict[str, Any]
    ) -> CallOutcome:
        notes: list[str] = []
        last: CallOutcome | None = None
        for i in self._key_order():
            out = await self._complete_once(payload, timeout_s, i)
            if out.error_class == "auth_error" and i >= 0:
                notes.append(self._mark_bad(i, out.status_code))
                last = out
                continue
            if notes and out.ok:
                notes.append(f"served using api key #{i + 1}")
            out.notes = notes
            return out
        assert last is not None
        last.notes = notes
        return last

    async def _complete_once(self, payload: dict[str, Any], timeout_s: float, i: int) -> CallOutcome:
        t0 = time.perf_counter()
        try:
            resp = await self._client.post(
                self._url, json=payload, headers=self._headers(i), timeout=timeout_s
            )
        except httpx.TimeoutException:
            return self._fail("timeout", None, "request timed out", t0)
        except httpx.TransportError as e:
            return self._fail("connection", None, repr(e)[:200], t0)

        latency = (time.perf_counter() - t0) * 1000
        if resp.status_code >= 400:
            return self._fail(
                classify_status(resp.status_code), resp.status_code, resp.text[:300], t0,
                retry_after=_retry_after(resp),
            )
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

    # ---- streaming ----------------------------------------------------------

    async def open_stream(
        self,
        spec: ModelSpec,
        payload: dict[str, Any],
        *,
        timeout_s: float,
        first_token_timeout_s: float,
        extra: dict[str, Any],
    ) -> CallOutcome:
        notes: list[str] = []
        last: CallOutcome | None = None
        for i in self._key_order():
            out = await self._open_once(payload, timeout_s, first_token_timeout_s, i)
            if out.error_class == "auth_error" and i >= 0:
                notes.append(self._mark_bad(i, out.status_code))
                last = out
                continue
            if notes and out.ok:
                notes.append(f"served using api key #{i + 1}")
            out.notes = notes
            return out
        assert last is not None
        last.notes = notes
        return last

    async def _open_once(
        self, payload: dict[str, Any], timeout_s: float, first_token_timeout_s: float, i: int
    ) -> CallOutcome:
        t0 = time.perf_counter()
        payload = dict(payload)
        payload["stream"] = True
        payload.setdefault("stream_options", {"include_usage": True})
        req = self._client.build_request(
            "POST", self._url, json=payload, headers=self._headers(i),
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
            retry_after = _retry_after(resp)
            await resp.aclose()
            return self._fail(
                classify_status(resp.status_code), resp.status_code, text, t0, retry_after=retry_after
            )

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
    def _fail(
        cls: str, status: int | None, msg: str, t0: float, retry_after: float | None = None
    ) -> CallOutcome:
        return CallOutcome(
            ok=False,
            status_code=status,
            error_class=cls,
            error_message=msg,
            latency_ms=(time.perf_counter() - t0) * 1000,
            retry_after_s=retry_after,
        )
