"""Talking to models, through OpenRouter.

One endpoint does everything: ``POST https://openrouter.ai/api/v1/chat/completions``.
What this package adds on top of it:

* **Retries and timeouts** — via :mod:`rsi_arena.core.retry`, which knows that
  400, 401 and 402 are permanent and that ``Retry-After`` beats guessing.
* **Rate limiting** — a shared token bucket plus a concurrency ceiling, so
  ``complete_many`` over 200 prompts does not open 200 sockets.
* **Caching** — every request is content-addressed through
  :mod:`rsi_arena.core.cache`; identical requests never leave the process
  twice, and concurrent identical ones collapse into one via single-flight.
* **Structured outputs** — a pydantic model in, a validated instance out,
  using ``response_format: {"type": "json_schema", ...}`` with ``strict``.
* **Web search** — either the ``:online`` model suffix or the ``web`` plugin,
  with the returned ``url_citation`` annotations lifted onto the result.
* **Streaming** — SSE, yielding deltas, with usage and cost arriving on the
  final chunk exactly as OpenRouter sends them.
* **Cost** — ``usage.cost`` is read straight off the response, falling back to
  a price table only if absent.

========================  =========================================================
:mod:`~rsi_arena.llm.messages`  message, tool call and citation types
:mod:`~rsi_arena.llm.config`    :class:`LLMConfig` and :class:`WebSearch`
:mod:`~rsi_arena.llm.results`   :class:`Completion`, :class:`StreamEvent`, JSON helpers
:mod:`~rsi_arena.llm.client`    :class:`LLMClient`
:mod:`~rsi_arena.llm.errors`    :class:`OpenRouterError`
========================  =========================================================
"""

from __future__ import annotations

from .client import LLMClient
from .config import DEFAULT_BASE_URL, DEFAULT_MODEL, LLMConfig, WebSearch
from .errors import OpenRouterError
from .messages import Citation, FunctionCall, Message, Prompt, ToolCall, to_messages
from .results import (
    Completion,
    StreamEvent,
    parse_json_loose,
    response_format_for,
    strict_schema,
)

__all__ = [
    "LLMClient", "LLMConfig", "WebSearch", "DEFAULT_MODEL", "DEFAULT_BASE_URL",
    "OpenRouterError",
    "Message", "Citation", "FunctionCall", "ToolCall", "Prompt", "to_messages",
    "Completion", "StreamEvent", "parse_json_loose", "strict_schema", "response_format_for",
]
