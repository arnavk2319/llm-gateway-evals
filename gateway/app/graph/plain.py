from __future__ import annotations

from typing import Any

from .nodes import Node, c_cand, c_conf, c_guard, c_outcome, c_valid
from .state import GatewayState


async def run_plain(nodes: dict[str, Node], state: GatewayState) -> dict[str, Any]:
    """The same pipeline as build_graph, as a plain async function.

    Exists to measure what LangGraph costs per request (bench/overhead.py).
    """

    async def step(name: str) -> None:
        state.update(await nodes[name](state))  # type: ignore[typeddict-item]

    await step("intake")
    if c_valid(state) == "respond_error":
        await step("respond_error")
        return state  # type: ignore[return-value]
    await step("guard")
    if c_guard(state) == "respond_error":
        await step("respond_error")
        return state  # type: ignore[return-value]
    await step("profile")
    if c_conf(state) == "llm_classify":
        await step("llm_classify")
    while True:
        await step("route")
        if c_cand(state) == "respond_error":
            await step("respond_error")
            return state  # type: ignore[return-value]
        await step("call")
        await step("evaluate")
        nxt = c_outcome(state)
        if nxt != "update_context":
            await step(nxt)
            return state  # type: ignore[return-value]
        await step("update_context")
