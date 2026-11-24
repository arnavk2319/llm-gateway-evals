"""Which OpenRouter free models can this account use under our data policy, and do they work?

    read -rs OPENROUTER_API_KEY && export OPENROUTER_API_KEY     # never put the key in a file or in chat
    uv run python scripts/probe_openrouter.py                    # probes every free model OpenRouter lists
    uv run python scripts/probe_openrouter.py google/gemma-4-31b-it:free nvidia/nemotron-3.5-super-vl:free

Each model gets one tiny request with `provider: {data_collection: "deny"}` (what the gateway sends). A model that
answers is served by an endpoint OpenRouter classifies as not storing or training on prompts. A 404 "no endpoints"
means every endpoint for it stores or trains, so keep it out of the policy. The key is read from the environment and is
never printed. Needs network access; each call is a single short completion (free models have daily request limits).
"""

from __future__ import annotations

import os
import sys
import time

import httpx

BASE = "https://openrouter.ai/api/v1"


def free_models() -> list[str]:
    data = httpx.get(BASE + "/models", timeout=30).raise_for_status().json()["data"]
    return sorted(
        m["id"] for m in data
        if m["id"].endswith(":free") and str(m["pricing"].get("prompt")) == "0" and str(m["pricing"].get("completion")) == "0"
    )


def probe(model: str, key: str, *, tools: bool, attempts: int = 3) -> tuple[str, float, str]:
    """Retry a temporary upstream rate limit a few times; a 429 says the endpoint exists, not that it is unusable."""
    for n in range(attempts):
        result = _probe_once(model, key, tools=tools)
        if result[0] != "RATE LIMITED" or n == attempts - 1:
            return result
        time.sleep(8.0 * (n + 1))
    return result


def _probe_once(model: str, key: str, *, tools: bool) -> tuple[str, float, str]:
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
        "max_tokens": 400,  # reasoning models spend tokens thinking before they answer; 64 often returns nothing
        "provider": {"data_collection": "deny"},
    }
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": "ping", "description": "ping", "parameters": {"type": "object", "properties": {}}}}]
    t0 = time.perf_counter()
    try:
        resp = httpx.post(BASE + "/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"}, timeout=60)
    except httpx.HTTPError as e:  # network trouble
        return "ERROR", time.perf_counter() - t0, repr(e)[:160]
    secs = time.perf_counter() - t0
    if resp.status_code >= 400:
        detail = resp.text[:160].replace("\n", " ")
        label = {404: "NO ENDPOINT under data_collection=deny", 429: "RATE LIMITED", 402: "NEEDS CREDITS"}.get(
            resp.status_code, f"HTTP {resp.status_code}"
        )
        return label, secs, detail
    msg = (resp.json().get("choices") or [{}])[0].get("message") or {}
    got = (msg.get("content") or "").strip()
    if got:
        return "OK", secs, got[:40]
    if msg.get("tool_calls"):
        return "OK", secs, "(tool call)"
    return "OK BUT EMPTY REPLY", secs, "the router would treat this reply as empty and fall back"


def main(argv: list[str]) -> None:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise SystemExit("Set OPENROUTER_API_KEY in your shell first (read -rs OPENROUTER_API_KEY && export OPENROUTER_API_KEY).")
    models = argv or free_models()
    print(f"{'model':52} {'result':40} {'secs':>5}  detail")
    for m in models:
        result, secs, detail = probe(m, key, tools=False)
        tool = ""
        if result.startswith("OK"):
            t_result, _, _ = probe(m, key, tools=True)
            tool = "  tools:" + ("ok" if t_result.startswith("OK") else t_result)
        print(f"{m:52} {result:40} {secs:5.1f}  {detail}{tool}")
        time.sleep(1.0)  # be gentle with the free-tier rate limits


if __name__ == "__main__":
    main(sys.argv[1:])
