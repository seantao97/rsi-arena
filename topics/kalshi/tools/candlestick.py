"""How a contract's price has moved."""

from __future__ import annotations

from typing import Any

from rsi_arena import Tool, ToolOutput

from ._history import HOUR, MINUTE
from ._clients import HISTORY, hours_ago, now


class CandlestickTool(Tool):
    name = "candlesticks"
    version = 1
    description = (
        "The price path of one contract as OHLC bars — where it has been, not "
        "where it is. Use it to tell a level that has held for an hour from one "
        "the market reached thirty seconds ago, and to see whether a move has "
        "stalled or is still running.\n\n"
        "Minute bars for anything in-play; hourly for context over a day. Kalshi "
        "publishes a bar only for periods that traded, so gaps are quiet stretches "
        "rather than missing data. Works on settled markets as well as open ones."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "ticker": {"type": "string", "description": "Market ticker."},
            "hours_back": {"type": "number", "default": 24.0,
                           "description": "How far back to look."},
            "hourly": {"type": "boolean", "default": True,
                       "description": "Hourly bars when true, minute bars when false."},
        },
        "required": ["ticker"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "bars": {"type": "array", "items": {
                "type": "object",
                "properties": {"ts": {"type": "string"}, "mid": {"type": "number"},
                               "last": {"type": "number"}, "volume": {"type": "number"}}}},
            "first": {"type": "number"}, "last": {"type": "number"},
            "low": {"type": "number"}, "high": {"type": "number"},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        ticker = input["ticker"]
        interval = HOUR if input.get("hourly", True) else MINUTE
        candles = HISTORY.price_path(
            ticker, hours_ago(float(input.get("hours_back", 24.0))), now(), interval)
        bars = [{"ts": c.ts.isoformat(), "mid": c.mid, "last": c.last,
                 "volume": c.volume, "open_interest": c.open_interest}
                for c in candles if c.mid is not None]
        if not bars:
            return ToolOutput.failed(f"no bars for {ticker} in that window")

        mids = [b["mid"] for b in bars]
        unit = "hourly" if input.get("hourly", True) else "minute"
        move = mids[-1] - mids[0]
        drift = ("flat" if abs(move) < 0.005
                 else f"{'up' if move > 0 else 'down'} {abs(move) * 100:.1f}c")
        return ToolOutput(
            response=(f"{ticker}: {len(bars)} {unit} bars, {mids[0]:.3f} -> "
                      f"{mids[-1]:.3f} ({drift}), ranging {min(mids):.3f}-"
                      f"{max(mids):.3f}."),
            raw_output={"bars": bars, "first": mids[0], "last": mids[-1],
                        "low": min(mids), "high": max(mids)},
        )
