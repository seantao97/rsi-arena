"""What one contract costs right now."""

from __future__ import annotations

from typing import Any

from rsi_arena import Tool, ToolOutput

from ._clients import QUOTES


class MarketQuoteTool(Tool):
    name = "market_quote"
    version = 1
    description = (
        "The live book on one contract: bid, ask, mid, spread, last trade, "
        "volume and open interest.\n\n"
        "The spread is the number to read first. A one-cent book cannot be "
        "improved on, so a resting order there is impossible and taking costs "
        "the whole edge; a ten-cent book is where a quote can sit. Volume "
        "distinguishes a real price from a stale one."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"ticker": {"type": "string"}},
        "required": ["ticker"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"yes_bid": {"type": "number"}, "yes_ask": {"type": "number"},
                       "mid": {"type": "number"}, "spread": {"type": "number"},
                       "last": {"type": "number"}, "volume": {"type": "number"}},
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        q = QUOTES.get_market(input["ticker"])
        out = {"ticker": q.ticker, "yes_bid": q.yes_bid, "yes_ask": q.yes_ask,
               "mid": q.mid, "spread": q.spread, "last": q.last,
               "volume": q.volume, "open_interest": q.open_interest,
               "status": q.status}
        if q.yes_bid is None or q.yes_ask is None:
            return ToolOutput(
                response=f"{q.ticker}: no two-sided quote (status {q.status}).",
                raw_output=out)
        return ToolOutput(
            response=(f"{q.ticker}: {q.yes_bid:.2f}/{q.yes_ask:.2f}, mid "
                      f"{q.mid:.3f}, spread {q.spread * 100:.0f}c, last "
                      f"{q.last}, volume {q.volume:,.0f}."),
            raw_output=out)
