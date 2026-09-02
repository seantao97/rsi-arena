"""How this contract has drifted as the clock has run down."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from rsi_arena.agent.tools import Tool, ToolOutput

from ..history import MINUTE
from ._clients import HISTORY


class TimeDecayTool(Tool):
    name = "time_decay"
    version = 1
    description = (
        "This contract's own price at each stage of the match so far, and how "
        "fast it has been drifting.\n\n"
        "A contract on something that has not happened yet decays toward zero "
        "as time runs out, and a contract on the absence of it drifts toward "
        "one. The rate is not a constant — it steepens near the whistle, which "
        "is where the largest predictable moves in a quiet match live.\n\n"
        "This measures the drift on this contract rather than assuming it. Take "
        "the recent cents-per-minute as the base rate and expect it to steepen, "
        "not hold, as the clock runs down. It says nothing about what a goal "
        "would do."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "kickoff": {"type": "string",
                        "description": "ISO 8601 UTC. Anchors the match clock."},
            "step_minutes": {"type": "integer", "default": 10},
        },
        "required": ["ticker", "kickoff"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "array", "items": {
                "type": "object",
                "properties": {"minute": {"type": "integer"},
                               "mid": {"type": "number"}}}},
            "cents_per_minute_recent": {"type": ["number", "null"]},
            "cents_per_minute_overall": {"type": ["number", "null"]},
            "direction": {"type": "string", "enum": ["up", "down", "flat"]},
            "window": {"type": "string",
                       "description": "The wall-clock span actually read."},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        try:
            kickoff = datetime.fromisoformat(input["kickoff"].replace("Z", "+00:00"))
        except ValueError:
            return ToolOutput.failed(f"{input['kickoff']!r} is not an ISO instant")
        ticker = input["ticker"]
        step = max(1, int(input.get("step_minutes", 10)))
        now = datetime.now(timezone.utc)
        end = min(now, kickoff + timedelta(minutes=100))
        if end <= kickoff:
            return ToolOutput.failed("kick-off is in the future")

        candles = HISTORY.price_path(ticker, kickoff, end, MINUTE)
        usable = [c for c in candles if c.two_sided and c.mid is not None]
        if len(usable) < 2:
            return ToolOutput.failed(f"not enough two-sided bars on {ticker}")

        # One reading per step, taken from the last bar at or before that
        # minute — Kalshi publishes a bar only for minutes that traded, so
        # sampling on the dot would leave holes on a quiet book.
        path, cursor = [], 0
        for minute in range(0, int((end - kickoff).total_seconds() // 60) + 1, step):
            when = kickoff + timedelta(minutes=minute)
            while cursor + 1 < len(usable) and usable[cursor + 1].ts <= when:
                cursor += 1
            if usable[cursor].ts <= when:
                path.append({"minute": minute, "mid": round(usable[cursor].mid, 4)})
        if len(path) < 2:
            return ToolOutput.failed("not enough of the match has been priced")

        span = path[-1]["minute"] - path[0]["minute"]
        overall = ((path[-1]["mid"] - path[0]["mid"]) * 100 / span) if span else None
        recent = None
        if len(path) >= 3:
            back = path[-3]
            gap = path[-1]["minute"] - back["minute"]
            recent = ((path[-1]["mid"] - back["mid"]) * 100 / gap) if gap else None

        rate = recent if recent is not None else overall
        direction = ("flat" if rate is None or abs(rate) < 0.02
                     else "up" if rate > 0 else "down")
        # A wrong kick-off produces a plausible-looking path over the wrong
        # window — a settled market reads as a flat line at its result. Naming
        # the wall-clock span makes that visible instead of silent.
        window = (f"{(kickoff + timedelta(minutes=path[0]['minute'])):%H:%M}-"
                  f"{(kickoff + timedelta(minutes=path[-1]['minute'])):%H:%M}Z")
        # A market that has stopped moving has usually stopped trading. The
        # tell is a tail of identical readings, which happens either because
        # the match is over or because the kick-off given puts the window past
        # the whistle — and a caller should know which before reading a drift
        # rate off it.
        tail = [p["mid"] for p in path[-4:]]
        flat = len(tail) >= 3 and max(tail) - min(tail) < 0.005
        return ToolOutput(
            response=(f"{ticker} from kick-off ({window}): "
                      + " ".join(f"{p['minute']}'={p['mid']:.2f}" for p in path[-6:])
                      + (f". Drifting {direction}"
                         + (f" at {abs(rate):.2f}c a minute lately"
                            if rate is not None else "")
                         + (f", {abs(overall):.2f}c a minute across the match"
                            if overall is not None else ""))
                      + (". The last readings are identical, so the market has "
                         "stopped moving — either the match is over, or the "
                         "kick-off given puts this window past the whistle."
                         if flat else
                         ". Expect the rate to steepen toward the whistle.")),
            raw_output={"path": path,
                        "cents_per_minute_recent": (round(recent, 3)
                                                    if recent is not None else None),
                        "cents_per_minute_overall": (round(overall, 3)
                                                     if overall is not None else None),
                        "direction": direction, "window": window})
