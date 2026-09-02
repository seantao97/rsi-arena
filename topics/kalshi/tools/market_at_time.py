"""What the book looked like at a past instant."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rsi_arena import Tool, ToolOutput

from ..history import MINUTE
from ._clients import HISTORY


class MarketAtTimeTool(Tool):
    name = "market_at_time"
    version = 1
    description = (
        "The quote as it stood at a chosen instant — bid, ask, mid and whether "
        "a real two-sided book existed then.\n\n"
        "Point-in-time by construction: it cannot return a period that ends "
        "after the moment asked for, so it is safe to use for checking what was "
        "knowable at a decision rather than what is known now.\n\n"
        "Use it to reconstruct why a price moved, or to check what the market "
        "said five minutes before a goal. Kalshi publishes a bar only for "
        "periods that traded, so on a thin book the answer may come from "
        "slightly earlier — the timestamp returned says how much earlier."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "when": {"type": "string",
                     "description": "ISO 8601 UTC instant, e.g. 2026-08-23T16:30:00Z."},
        },
        "required": ["ticker", "when"],
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        ticker, raw = input["ticker"], input["when"]
        try:
            when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return ToolOutput.failed(f"{raw!r} is not an ISO 8601 instant")
        candle = HISTORY.quote_at(ticker, when, MINUTE)
        if candle is None:
            return ToolOutput.failed(f"no quote for {ticker} at or before {raw}")
        lag = (when - candle.ts.astimezone(when.tzinfo)).total_seconds()
        out = {"ticker": ticker, "asked_for": when.isoformat(),
               "from": candle.ts.isoformat(), "lag_seconds": round(lag),
               "mid": candle.mid, "last": candle.last, "volume": candle.volume,
               "two_sided": candle.two_sided}
        if not candle.two_sided:
            return ToolOutput(
                response=(f"{ticker} had no two-sided book at {raw} — the "
                          f"quote there is an empty book, not a price."),
                raw_output=out)
        return ToolOutput(
            response=(f"{ticker} at {candle.ts:%H:%M}Z: mid {candle.mid:.3f}, "
                      f"last {candle.last}, volume {candle.volume:,.0f}"
                      f"{f' ({lag:.0f}s before the instant asked for)' if lag > 60 else ''}."),
            raw_output=out)
