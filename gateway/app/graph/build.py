from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes import Deps, c_cand, c_conf, c_outcome, c_valid, make_timed_nodes
from .state import GatewayState


def build_graph(deps: Deps):
    """The hot path as a LangGraph graph (compiled once at startup, no checkpointer)."""
    nodes = make_timed_nodes(deps)
    g = StateGraph(GatewayState)
    for name, fn in nodes.items():
        g.add_node(name, fn)

    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", c_valid, {"profile": "profile", "respond_error": "respond_error"})
    g.add_conditional_edges("profile", c_conf, {"route": "route", "llm_classify": "llm_classify"})
    g.add_edge("llm_classify", "route")
    g.add_conditional_edges("route", c_cand, {"call": "call", "respond_error": "respond_error"})
    g.add_edge("call", "evaluate")
    g.add_conditional_edges(
        "evaluate",
        c_outcome,
        {"respond": "respond", "update_context": "update_context", "respond_error": "respond_error"},
    )
    g.add_edge("update_context", "route")
    g.add_edge("respond", END)
    g.add_edge("respond_error", END)
    return g.compile()
