"""What has been reported about a team that the score does not show."""

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


class TeamNewsTool(Tool):
    name = "team_news"
    version = 1
    description = (
        "Recent news about a team or player — injuries, suspensions, a "
        "manager's team-sheet remarks, anything reported that a scoreline does "
        "not carry.\n\n"
        "Most useful before kick-off, where a late withdrawal moves a price and "
        "the feed's injury list has not caught up. Much less useful in play: "
        "reporting lags the market by minutes at best, so a price that has "
        "already moved will not be explained here.\n\n"
        "Search results are stale by nature. Treat a headline as a lead to "
        "check against the game state, not as a fact about the current match, "
        "and read the dates."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "team": {"type": "string",
                     "description": "Club or player name, as reported."},
            "topic": {"type": "string", "default": "team news injury lineup",
                      "description": "What to ask about alongside the name."},
            "limit": {"type": "integer", "default": 5},
        },
        "required": ["team"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "results": {"type": "array", "items": {
                "type": "object",
                "properties": {"title": {"type": "string"},
                               "link": {"type": "string"},
                               "snippet": {"type": "string"},
                               "date": {"type": ["string", "null"]}}}},
            "query": {"type": "string"},
        },
    }

    #: Metered per search by the vendor, and the endpoint does not report it.
    cost_usd = 0.005

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        raise NotImplementedError("team_news answers through aget_tool_output")

    async def aget_tool_output(self, input: dict[str, Any] | None = None,
                               **kwargs: Any) -> ToolOutput:
        args = {**(input or {}), **kwargs}
        query = f"{args['team']} {args.get('topic', 'team news injury lineup')}"
        limit = int(args.get("limit", 5))
        try:
            answer = await APIClient().call(search_api(), "news", q=query)
        except Exception as exc:
            return ToolOutput.failed(_reason(exc))

        rows = [{"title": r.get("title"), "link": r.get("link"),
                 "snippet": r.get("snippet"), "date": r.get("date")}
                for r in (getattr(answer, "data", answer) or [])][:limit]
        if not rows:
            return ToolOutput(response=f"Nothing reported for {query!r}.",
                              raw_output={"results": [], "query": query})
        said = " | ".join(
            f"{r['title']}" + (f" ({r['date']})" if r.get("date") else "")
            for r in rows)
        return ToolOutput(
            response=f"{len(rows)} on {args['team']}: {said}",
            raw_output={"results": rows, "query": query},
            raw_api_data={"results": rows})
