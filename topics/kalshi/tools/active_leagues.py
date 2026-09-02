"""What the exchange has open right now."""

from __future__ import annotations

from typing import Any

from rsi_arena import Tool, ToolOutput

from .. import gamestate as gs
from ._clients import DISCOVERY


class ActiveLeaguesTool(Tool):
    name = "active_leagues"
    version = 1
    description = (
        "Which leagues currently have open markets, and which of those have a "
        "live score feed behind them.\n\n"
        "The first question to ask when starting from nothing. It separates a "
        "competition the exchange lists from one it does not: a league with "
        "markets but no feed can be priced and not followed in play; a league "
        "with a feed and no markets is being played and cannot be traded.\n\n"
        "It walks every open event on the exchange, so it takes on the order of "
        "fifteen seconds. Ask it once and keep the answer; do not call it per "
        "league."
    )
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"tradeable": {"type": "array"}, "with_feed": {"type": "array"},
                       "markets_only": {"type": "array"}},
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        listed = sorted(DISCOVERY.active_leagues())
        with_feed = set(gs.supported_leagues())
        both = [lg for lg in listed if lg in with_feed]
        markets_only = [lg for lg in listed if lg not in with_feed]
        if not listed:
            return ToolOutput.failed("no league has an open market right now")
        return ToolOutput(
            response=(f"{len(listed)} leagues with open markets; {len(both)} also "
                      f"have a score feed: {', '.join(both[:12])}"
                      + (f". Markets but no feed: {', '.join(markets_only[:8])}"
                         if markets_only else "") + "."),
            raw_output={"tradeable": listed, "with_feed": both,
                        "markets_only": markets_only})
