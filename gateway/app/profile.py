from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from .schemas import ChatRequest

KNOWN_TASKS = frozenset(
    {"chat", "extraction", "classification", "summarization", "code", "agent_tool", "review"}
)
COMPLEXITY = ("low", "medium", "high")

_CODE_RE = re.compile(
    r"```|\bdef \w+\(|\bfunction\b|\bclass \w+|\bimport \w+|traceback|stack trace|refactor|"
    r"\bimplement\b|unit test|\bbug\b",
    re.I,
)
_DEFAULT_OUTPUT = {
    "classification": 16,
    "extraction": 200,
    "summarization": 300,
    "code": 600,
    "agent_tool": 300,
    "review": 300,
    "chat": 300,
}


@dataclass
class TaskProfile:
    task_type: str = "chat"
    complexity: str = "medium"
    needs_tools: bool = False
    needs_json: bool = False
    context_tokens: int = 0
    expected_output_tokens: int = 300
    risk: str = "low"
    confidence: float = 0.5
    source: str = "rules"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def text_of(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        if isinstance(part, dict):
            parts.append(str(part.get("text", "")))
        else:
            parts.append(str(part))
    return " ".join(parts)


def estimate_tokens(req: ChatRequest) -> int:
    chars = sum(len(text_of(m.content)) for m in req.messages)
    return chars // 4 + 4 * len(req.messages)


def last_user_text(req: ChatRequest) -> str:
    for m in reversed(req.messages):
        if m.role == "user":
            return text_of(m.content)
    return text_of(req.messages[-1].content) if req.messages else ""


def _bump(level: str, by: int = 1) -> str:
    i = min(COMPLEXITY.index(level) + by, len(COMPLEXITY) - 1)
    return COMPLEXITY[max(i, 0)]


def build_profile(
    req: ChatRequest,
    *,
    task_hint: str | None = None,
    node_role: str = "unknown",
    quality_hint: str | None = None,
) -> TaskProfile:
    needs_tools = bool(req.tools)
    rf_type = (req.response_format or {}).get("type")
    needs_json = rf_type in ("json_object", "json_schema")
    text = last_user_text(req).lower()
    ctx = estimate_tokens(req)

    if task_hint and task_hint in KNOWN_TASKS:
        task, conf = task_hint, 1.0
    elif needs_tools:
        task, conf = "agent_tool", 0.9
    elif needs_json:
        task, conf = "extraction", 0.85
    elif node_role == "reviewer":
        task, conf = "review", 0.8
    elif req.max_tokens is not None and req.max_tokens <= 16:
        task, conf = "classification", 0.8
    elif re.search(r"\b(classify|categori[sz]e|label)\b", text):
        task, conf = "classification", 0.75
    elif re.search(r"\bextract\b", text):
        task, conf = "extraction", 0.75
    elif re.search(r"\b(summari[sz]e|tl;dr)\b", text):
        task, conf = "summarization", 0.75
    elif _CODE_RE.search(text):
        task, conf = "code", 0.65
    else:
        task, conf = "chat", 0.5

    complexity = "low" if ctx < 500 else "medium" if ctx < 4000 else "high"
    if task in ("code", "agent_tool", "review"):
        complexity = _bump(complexity)
    if quality_hint == "high":
        complexity = "high"
    elif quality_hint == "low":
        complexity = "low"

    risk = "high" if quality_hint == "high" else "medium" if node_role in ("reviewer", "planner") else "low"
    out_tokens = req.max_tokens or _DEFAULT_OUTPUT.get(task, 300)
    return TaskProfile(
        task_type=task,
        complexity=complexity,
        needs_tools=needs_tools,
        needs_json=needs_json,
        context_tokens=ctx,
        expected_output_tokens=out_tokens,
        risk=risk,
        confidence=conf,
        source="rules",
    )
