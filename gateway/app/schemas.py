from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: str | list[Any] | None = None


class ChatRequest(BaseModel):
    """OpenAI-compatible chat completion request. Unknown fields pass through."""

    model_config = ConfigDict(extra="allow")

    model: str = "auto"
    messages: list[ChatMessage]
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None
    tools: list[Any] | None = None
    tool_choice: Any | None = None
    response_format: dict[str, Any] | None = None
    stream_options: dict[str, Any] | None = None
