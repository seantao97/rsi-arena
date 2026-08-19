"""What comes back, and the JSON helpers for reading it.

:class:`Completion` is one response with its usage and its cost attached;
:class:`StreamEvent` is one frame of a streamed one. The helpers below turn a
pydantic model into a schema strict enough for ``json_schema`` mode, and pull
JSON back out of a response that wrapped it in prose.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..core.costs import Cost, Usage
from .messages import Citation, Message, ToolCall

class Completion(BaseModel):
    """One response. ``parsed`` is populated when a schema was requested."""

    id: str = ""
    model: str = ""
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    native_finish_reason: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    cost: Cost = Field(default_factory=Cost)
    cached: bool = False
    latency_s: float = 0.0
    attempts: int = 1
    parsed: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None

    @property
    def message(self) -> Message:
        return Message(role="assistant", content=self.text, tool_calls=self.tool_calls or None)

    def json_output(self) -> Any:
        """The response body parsed as JSON, tolerating a fenced code block."""
        if self.parsed is not None:
            return self.parsed
        return parse_json_loose(self.text)


class StreamEvent(BaseModel):
    """One event from :meth:`LLMClient.stream`.

    ``delta`` events carry incremental text; exactly one ``done`` event
    arrives last and carries the assembled :class:`Completion` with usage and
    cost, which OpenRouter puts in the final SSE message.
    """

    type: Literal["delta", "tool_call", "done"]
    text: str = ""
    tool_call: ToolCall | None = None
    completion: Completion | None = None


# --- JSON helpers -----------------------------------------------------------


def parse_json_loose(text: str) -> Any:
    """Parse JSON that may be wrapped in prose or a ```json fence.

    Strict structured outputs make this unnecessary, but not every model or
    provider endpoint honours ``strict``, and a fenced block is the single
    most common way the contract gets broken.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("empty response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if "```" in text:
        block = text.split("```", 2)[1]
        block = block.split("\n", 1)[1] if block.lower().startswith("json") else block
        try:
            return json.loads(block.strip())
        except json.JSONDecodeError:
            pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"could not parse JSON from response: {text[:200]!r}")


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Turn a pydantic model into a schema that satisfies ``strict`` mode.

    Strict requires every object to set ``additionalProperties: false`` and to
    list every property in ``required`` — including ones pydantic left out
    because they have defaults. Providers reject the schema otherwise, and the
    rejection is a 400 that no retry will fix.
    """
    schema = model.model_json_schema()

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            node = {k: walk(v) for k, v in node.items()}
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            return node
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def response_format_for(model: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model.__name__,
            "strict": True,
            "schema": strict_schema(model),
        },
    }


# --- client -----------------------------------------------------------------
