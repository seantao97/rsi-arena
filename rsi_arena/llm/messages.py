"""The wire types for a conversation.

Pure data — a message, a tool call the model asked for, a citation it came
back with — with no HTTP anywhere near them. That is what lets a whole
conversation be dropped into a trace and read back out, and what lets a test
build a request without a client.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Sequence

from pydantic import BaseModel

# --- messages ---------------------------------------------------------------


class FunctionCall(BaseModel):
    name: str
    arguments: str = "{}"

    def parsed_arguments(self) -> dict[str, Any]:
        try:
            value = json.loads(self.arguments or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {"value": value}


class ToolCall(BaseModel):
    id: str = ""
    type: str = "function"
    function: FunctionCall


class Citation(BaseModel):
    """One ``url_citation`` annotation returned by the web plugin."""

    url: str = ""
    title: str = ""
    content: str = ""
    start_index: int | None = None
    end_index: int | None = None


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

    def to_wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role, "content": self.content or ""}
        if self.name:
            out["name"] = self.name
        if self.tool_calls:
            out["tool_calls"] = [tc.model_dump(exclude_none=True) for tc in self.tool_calls]
        if self.tool_call_id:
            out["tool_call_id"] = self.tool_call_id
        return out


Prompt = str | Message | Sequence[Message] | Sequence[dict[str, Any]]


def to_messages(prompt: Prompt, system: str | None = None) -> list[Message]:
    """Accept a bare string, a Message, or a list of either, and normalise."""
    if isinstance(prompt, str):
        messages = [Message(role="user", content=prompt)]
    elif isinstance(prompt, Message):
        messages = [prompt]
    else:
        messages = [m if isinstance(m, Message) else Message(**m) for m in prompt]
    if system and not any(m.role == "system" for m in messages):
        messages = [Message(role="system", content=system), *messages]
    return messages
