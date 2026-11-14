"""Hot-path overhead: LangGraph graph vs the same pipeline as a plain async function.

Mock provider with zero latency, so what's measured is the gateway itself
(intake, profile, route, call plumbing, evaluate, respond), not the model.

    uv run python bench/overhead.py [iterations]
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.graph.nodes import make_nodes, make_timed_nodes  # noqa: E402
from app.graph.plain import run_plain  # noqa: E402
from app.main import create_app  # noqa: E402
from app.schemas import ChatRequest  # noqa: E402
from app.sinks import NullSink  # noqa: E402


def fresh(content: str, headers: dict[str, str] | None = None) -> dict:
    return {
        "request": ChatRequest(messages=[{"role": "user", "content": content}]),
        "headers": headers or {},
        "origin": "external",
        "timings": {},
    }


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


async def measure(label: str, runner, make_state, n: int) -> tuple[str, list[float]]:
    for _ in range(min(200, n)):  # warmup
        await runner(make_state())
    samples = []
    for _ in range(n):
        s = make_state()
        t0 = time.perf_counter()
        await runner(s)
        samples.append((time.perf_counter() - t0) * 1000)
    return label, samples


async def main(n: int) -> None:
    app = create_app(Settings(mock_latency_scale=0.0, sink="null", database_url=None), sink=NullSink())
    async with app.router.lifespan_context(app):
        rt = app.state.rt
        timed = make_timed_nodes(rt.deps)
        raw = make_nodes(rt.deps)

        scenarios = {
            "happy path (1 attempt)": lambda: fresh("hello there"),
            "with 1 fallback (2 attempts)": lambda: fresh(
                "hello there", {"x-mock-behavior": "mock/small=error_500"}
            ),
        }
        print(f"iterations per cell: {n}; times in ms per request (gateway only, mock provider, 0 latency)\n")
        print("| scenario | pipeline | mean | p50 | p95 | p99 |")
        print("|---|---|---:|---:|---:|---:|")
        for name, mk in scenarios.items():
            rows = [
                await measure("LangGraph graph", lambda s: rt.graph.ainvoke(s), mk, n),
                await measure("plain async (timed nodes)", lambda s: run_plain(timed, s), mk, n),
                await measure("plain async (raw nodes)", lambda s: run_plain(raw, s), mk, n),
            ]
            for label, xs in rows:
                print(
                    f"| {name} | {label} | {statistics.fmean(xs):.3f} | {pct(xs, .5):.3f} | "
                    f"{pct(xs, .95):.3f} | {pct(xs, .99):.3f} |"
                )

        # per-node breakdown from the graph's own timings
        acc: dict[str, list[float]] = {}
        for _ in range(n):
            out = await rt.graph.ainvoke(fresh("hello there"))
            for k, v in out["timings"].items():
                acc.setdefault(k, []).append(v)
        print("\nPer-node mean (ms), happy path, graph:")
        print("| node | mean ms |")
        print("|---|---:|")
        for k, v in acc.items():
            print(f"| {k} | {statistics.fmean(v):.4f} |")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 2000))
