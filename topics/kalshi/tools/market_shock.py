"""A sudden move, and whether it has come back."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from rsi_arena.agent.tools import Tool, ToolOutput

from ..history import MINUTE
from ._clients import HISTORY, hours_ago, now


class MarketShockTool(Tool):
    name = "market_shock"
    version = 1
    description = (
        "Sudden moves in this contract over the last stretch: how far, when, "
        "and how much of it the market has taken back since.\n\n"
        "A price that jumps forty cents in four minutes has either learned "
        "something or overshot, and the retrace is what separates them. A move "
        "that holds was information; one that is already coming back was an "
        "overreaction, and that is the shape of the single most profitable "
        "trade this agent has made — a spread that fell 0.73 to 0.33 on a goal "
        "and did not stay there.\n\n"
        "Finding this by reading a price path is possible and slow. This names "
        "the jumps, in order of size, and says whether each is still where it "
        "landed."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "minutes_back": {"type": "number", "default": 30.0},
            "threshold_cents": {"type": "number", "default": 5.0,
                                "description": "Smallest jump worth naming."},
            "ending": {"type": "string",
                       "description": "ISO 8601 UTC. Look at the window ending "
                                      "here instead of now — for reading a "
                                      "past match."},
        },
        "required": ["ticker"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "shocks": {"type": "array", "items": {
                "type": "object",
                "properties": {"ts": {"type": "string"},
                               "from": {"type": "number"}, "to": {"type": "number"},
                               "move_cents": {"type": "number"},
                               "minutes_ago": {"type": "number"},
                               "retraced_cents": {"type": "number"},
                               "retraced_fraction": {"type": "number"}}}},
            "current": {"type": ["number", "null"]},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        ticker = input["ticker"]
        back = float(input.get("minutes_back", 30.0))
        floor = float(input.get("threshold_cents", 5.0))
        end_at = now()
        if input.get("ending"):
            try:
                end_at = datetime.fromisoformat(
                    str(input["ending"]).replace("Z", "+00:00"))
            except ValueError:
                return ToolOutput.failed(f"{input['ending']!r} is not an ISO instant")
        # An unknown or delisted ticker 404s. The agent reaches for this on
        # markets that may never have traded, so that has to read as a refusal.
        try:
            path = HISTORY.price_path(ticker, end_at - timedelta(minutes=back),
                                      end_at, MINUTE)
        except Exception as exc:
            return ToolOutput.failed(f"no history for {ticker}: "
                                     f"{type(exc).__name__}")
        bars = [c for c in path if c.two_sided and c.mid is not None]
        if len(bars) < 3:
            return ToolOutput.failed(f"too few two-sided bars on {ticker}")

        latest = bars[-1]
        shocks = []
        for before, after in zip(bars, bars[1:]):
            move = (after.mid - before.mid) * 100
            if abs(move) < floor:
                continue
            # How much of the jump the market has given back since.
            given = (latest.mid - after.mid) * 100
            retraced = given if (given * move) < 0 else 0.0
            shocks.append({
                "ts": after.ts.isoformat(), "from": before.mid, "to": after.mid,
                "move_cents": round(move, 1),
                "minutes_ago": round((latest.ts - after.ts).total_seconds() / 60, 1),
                "retraced_cents": round(abs(retraced), 1),
                "retraced_fraction": round(min(1.0, abs(retraced) / abs(move)), 3),
            })
        shocks.sort(key=lambda s: -abs(s["move_cents"]))

        if not shocks:
            return ToolOutput(
                response=(f"{ticker} at {latest.mid:.3f}: no move of {floor:.0f}c "
                          f"or more in the last {back:.0f} minutes."),
                raw_output={"shocks": [], "current": latest.mid})

        top = shocks[0]
        held = top["retraced_fraction"] < 0.25
        return ToolOutput(
            response=(f"{ticker} at {latest.mid:.3f}: {len(shocks)} jump(s). "
                      f"Largest {top['move_cents']:+.0f}c "
                      f"({top['from']:.3f} to {top['to']:.3f}) "
                      f"{top['minutes_ago']:.0f} minutes ago, since retraced "
                      f"{top['retraced_fraction']:.0%}. "
                      + ("It has held, which reads as information rather than "
                         "an overshoot." if held else
                         "It is coming back, which is what an overreaction "
                         "looks like.")),
            raw_output={"shocks": shocks[:8], "current": latest.mid})
