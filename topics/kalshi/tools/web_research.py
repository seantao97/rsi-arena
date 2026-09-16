"""A general question the exchange and the feed cannot answer."""

from __future__ import annotations

from typing import Any

from rsi_arena.agent.tools import Tool, ToolOutput
from rsi_arena.api import APIClient

from ._search import search_api


def _reason(exc: Exception) -> str:
    """Say what is actually missing. A search tool with no key should read as
    unconfigured, not as broken."""
    text = f"{type(exc).__name__}: {exc}"
    if "SEARCHAPI_API_KEY" in text or "auth" in text.lower():
        return "search is not configured — SEARCHAPI_API_KEY is unset"
    return text


class WebResearchTool(Tool):
    name = "web_research"
    version = 1
    description = (
        "A web search, for the questions the exchange and the score feed do not "
        "cover: a competition's rules, whether a match is being replayed, "
        "weather at a venue, what a settlement source actually publishes.\n\n"
        "Not a price source and not a substitute for game_state — both of those "
        "are faster, exact, and current, and the web is none of the three. "
        "Reach for this when the question is about the world rather than the "
        "market.\n\n"
        "Ask one specific question at a time. Results are cached for an hour, "
        "so repeating a query within a run is free."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "One specific question, not a topic."},
            "limit": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "results": {"type": "array", "items": {
                "type": "object",
                "properties": {"title": {"type": "string"},
                               "link": {"type": "string"},
                               "snippet": {"type": "string"}}}},
            "query": {"type": "string"},
        },
    }

    cost_usd = 0.005

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        """The synchronous door onto an asynchronous body.

        This used to raise, which made the tool unusable from any caller that
        was not already in an event loop — and a plan is one of those.
        """
        return self._run_sync(self.aget_tool_output(input))

    async def aget_tool_output(self, input: dict[str, Any] | None = None,
                               **kwargs: Any) -> ToolOutput:
        args = {**(input or {}), **kwargs}
        query = args["query"]
        limit = int(args.get("limit", 5))
        try:
            answer = await APIClient().call(search_api(), "search", q=query)
        except Exception as exc:
            return ToolOutput.failed(_reason(exc))

        rows = [{"title": r.get("title"), "link": r.get("link"),
                 "snippet": r.get("snippet")}
                for r in (getattr(answer, "data", answer) or [])][:limit]
        if not rows:
            return ToolOutput(response=f"Nothing found for {query!r}.",
                              raw_output={"results": [], "query": query})
        said = " | ".join(f"{r['title']}: {(r['snippet'] or '')[:110]}"
                          for r in rows[:3])
        return ToolOutput(response=f"{len(rows)} results. {said}",
                          raw_output={"results": rows, "query": query},
                          raw_api_data={"results": rows})
