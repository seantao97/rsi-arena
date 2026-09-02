"""How much size is actually available, and at what price."""

from __future__ import annotations

from typing import Any

from rsi_arena import Tool, ToolOutput

from ._clients import QUOTES


class OrderBookTool(Tool):
    name = "order_book"
    version = 1
    description = (
        "Resting depth on both sides of one contract, best price first.\n\n"
        "A quote says the price of the next contract; the book says the price "
        "of the hundredth. Read it before sizing anything: a market showing "
        "0.61/0.62 with forty contracts behind the ask is a different market "
        "from the same quote with four thousand."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"ticker": {"type": "string"},
                       "depth": {"type": "integer", "default": 5}},
        "required": ["ticker"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"yes": {"type": "array"}, "no": {"type": "array"}},
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        depth = int(input.get("depth", 5))
        b = QUOTES.get_orderbook(input["ticker"], depth)
        yes, no = b.yes[:depth], b.no[:depth]
        if not yes and not no:
            return ToolOutput.failed(f"no resting orders on {b.ticker}")
        top_yes = f"{yes[0][1]:,} at {yes[0][0]}c" if yes else "nothing"
        top_no = f"{no[0][1]:,} at {no[0][0]}c" if no else "nothing"
        return ToolOutput(
            response=(f"{b.ticker}: best yes {top_yes}, best no {top_no}; "
                      f"{len(yes)} and {len(no)} levels showing."),
            raw_output={"ticker": b.ticker, "yes": yes, "no": no})
