"""Whether this price is moving, and how fast for this book."""

from __future__ import annotations

import statistics
from datetime import timedelta
from typing import Any

from rsi_arena.agent.tools import Tool, ToolOutput

from ._history import MINUTE
from ._clients import HISTORY, hours_ago, now


class PriceVelocityTool(Tool):
    name = "price_velocity"
    version = 1
    description = (
        "How fast this contract has been moving over the last one, three and "
        "five minutes, against how much it normally moves.\n\n"
        "Two cents a minute means nothing on its own — it is fast on a book "
        "that usually drifts a tenth of a cent and ordinary on one that swings "
        "constantly. This scores the recent rate against the contract's own "
        "hour, so 'fast' is measured rather than asserted.\n\n"
        "The reason to ask before resting an order: a quote placed into a price "
        "that is already running gets filled by the thing that is moving it, "
        "which is the wrong side of it. A still book is where a resting order "
        "is worth having."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"ticker": {"type": "string"}},
        "required": ["ticker"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "mid": {"type": ["number", "null"]},
            "cents_1m": {"type": ["number", "null"]},
            "cents_3m": {"type": ["number", "null"]},
            "cents_5m": {"type": ["number", "null"]},
            "typical_cents_per_minute": {"type": ["number", "null"],
                                         "description": "Median absolute "
                                                        "minute move this hour."},
            "z": {"type": ["number", "null"],
                  "description": "Recent rate over the typical one."},
            "verdict": {"type": "string",
                        "enum": ["still", "drifting", "moving", "running"]},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        ticker = input["ticker"]
        try:
            path = HISTORY.price_path(ticker, hours_ago(1.0), now(), MINUTE)
        except Exception as exc:
            return ToolOutput.failed(f"no history for {ticker}: "
                                     f"{type(exc).__name__}")
        bars = [c for c in path if c.two_sided and c.mid is not None]
        if len(bars) < 4:
            return ToolOutput.failed(f"too few two-sided bars on {ticker} this hour")

        latest = bars[-1]
        end = latest.ts

        def move_over(minutes: int) -> float | None:
            cutoff = end - timedelta(minutes=minutes)
            earlier = [c for c in bars if c.ts <= cutoff]
            if not earlier:
                return None
            return round((latest.mid - earlier[-1].mid) * 100, 2)

        one, three, five = move_over(1), move_over(3), move_over(5)
        steps = [abs(b.mid - a.mid) * 100 for a, b in zip(bars, bars[1:])]
        typical = round(statistics.median(steps), 3) if steps else None

        recent = next((abs(v) for v in (one, three) if v is not None), None)
        z = (round(recent / typical, 2)
             if recent is not None and typical and typical > 0.01 else None)
        if recent is None or recent < 0.5:
            verdict = "still"
        elif z is None:
            verdict = "moving"
        elif z >= 6:
            verdict = "running"
        elif z >= 2.5:
            verdict = "moving"
        else:
            verdict = "drifting"

        parts = [f"{lbl} {v:+.1f}c" for lbl, v in
                 (("1m", one), ("3m", three), ("5m", five)) if v is not None]
        return ToolOutput(
            response=(f"{ticker} at {latest.mid:.3f}: {', '.join(parts) or 'flat'}. "
                      f"Normally {typical:.2f}c a minute"
                      + (f", so this is {z:.1f}x that" if z else "")
                      + f" — {verdict}."
                      + (" A resting order here gets taken by whatever is moving it."
                         if verdict == "running" else "")),
            raw_output={"mid": latest.mid, "cents_1m": one, "cents_3m": three,
                        "cents_5m": five, "typical_cents_per_minute": typical,
                        "z": z, "verdict": verdict})
