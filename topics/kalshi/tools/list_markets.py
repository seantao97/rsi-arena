"""What a league currently offers."""

from __future__ import annotations

from typing import Any

from rsi_arena.agent.tool import Tool, ToolOutput

from ._clients import DISCOVERY


class ListMarketsTool(Tool):
    name = "list_markets"
    version = 1
    description = (
        "Open fixture markets for one league, with price and volume.\n\n"
        "Use it to see what a competition has on offer. It answers what is "
        "listed, not what is being played — a fixture appears here days before "
        "kick-off. For markets on matches in progress, use live_markets."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "league": {"type": "string",
                       "description": "League code: EPL, LALIGA, SERIEA, MLS, "
                                      "LIGAMX, ARGENTINA, MLB, NFL, NBA."},
            "limit": {"type": "integer", "default": 25},
        },
        "required": ["league"],
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        league = input["league"]
        limit = int(input.get("limit", 25))
        found = list(DISCOVERY.whats_bettable(league=league, fixtures_only=True))
        rows = [{"ticker": m.ticker, "event": m.event_ticker, "title": m.title,
                 "subtitle": m.subtitle, "type": m.market_type,
                 "yes_bid": m.yes_bid, "yes_ask": m.yes_ask,
                 "volume": m.volume, "close_time": m.close_time}
                for m in found[:limit]]
        if not rows:
            return ToolOutput.failed(f"{league} has no open fixture markets")
        events = len({m.event_ticker for m in found})
        return ToolOutput(
            response=(f"{league}: {len(found)} open fixture markets across "
                      f"{events} fixtures; showing {len(rows)}. First: "
                      f"{rows[0]['ticker']} at {rows[0]['yes_bid']}/"
                      f"{rows[0]['yes_ask']}."),
            raw_output={"markets": rows, "total": len(found), "events": events})
