"""What has actually traded, as opposed to what is quoted."""

from __future__ import annotations

from collections import Counter
from typing import Any

from rsi_arena import Tool, ToolOutput

from ._clients import HISTORY


class PreviousTradesTool(Tool):
    name = "previous_trades"
    version = 1
    description = (
        "The print tape: trades that happened, with price, size and which side "
        "crossed the spread to make them happen.\n\n"
        "Quotes say what someone is willing to do; prints say what someone did. "
        "The difference matters most on a thin book, where the best bid can "
        "collapse with nothing traded at all — a quote that moved on no volume "
        "is makers stepping away, not an opinion changing.\n\n"
        "Newest first. Taker side tells you who was impatient, which is the "
        "closest thing here to a direction of pressure."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "limit": {"type": "integer", "default": 30,
                      "description": "How many prints, newest first."},
        },
        "required": ["ticker"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "trades": {"type": "array"}, "volume": {"type": "number"},
            "taker_yes": {"type": "integer"}, "taker_no": {"type": "integer"},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        ticker = input["ticker"]
        limit = int(input.get("limit", 30))
        raw = HISTORY.trades(ticker, None, None, limit)
        trades = [{"ts": t.get("created_time"),
                   "yes_price": t.get("yes_price_dollars"),
                   "count": t.get("count_fp"),
                   "taker_side": t.get("taker_side")} for t in raw]
        if not trades:
            return ToolOutput.failed(f"no prints on {ticker}")

        sides = Counter(t["taker_side"] for t in trades)
        size = sum(float(t["count"] or 0) for t in trades)
        prices = [float(t["yes_price"]) for t in trades if t["yes_price"]]
        span = (f"{min(prices):.2f}-{max(prices):.2f}" if prices else "—")
        lean = ("balanced" if sides["yes"] == sides["no"]
                else f"{'yes' if sides['yes'] > sides['no'] else 'no'} takers "
                     f"{max(sides['yes'], sides['no'])}/{len(trades)}")
        return ToolOutput(
            response=(f"{ticker}: {len(trades)} prints, {size:,.0f} contracts, "
                      f"traded {span}, {lean}. Newest "
                      f"{trades[0]['yes_price']} at {trades[0]['ts']}."),
            raw_output={"trades": trades, "volume": size,
                        "taker_yes": sides["yes"], "taker_no": sides["no"]},
            raw_api_data={"trades": raw[:5]},
        )
