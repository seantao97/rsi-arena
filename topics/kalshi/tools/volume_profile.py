"""Where the volume actually traded."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rsi_arena import Tool, ToolOutput

from ._clients import HISTORY, hours_ago, now


class VolumeProfileTool(Tool):
    name = "volume_profile"
    version = 1
    description = (
        "Contracts traded at each price over a window — where the market has "
        "done business, rather than where it is quoted.\n\n"
        "A price the book has spent an hour at with size behind it is a "
        "different thing from one it touched for a minute. Use it to tell an "
        "established level from a spike, and to see whether a move away from a "
        "heavily traded price has been accepted or is drifting back."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"ticker": {"type": "string"},
                       "hours_back": {"type": "number", "default": 6.0}},
        "required": ["ticker"],
    }

    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "profile": {"type": "object",
                        "description": "Price to contracts traded there."},
            "total": {"type": "number"},
            "heaviest_price": {"type": "string"},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        ticker = input["ticker"]
        profile = HISTORY.volume_profile(
            ticker, hours_ago(float(input.get("hours_back", 6.0))), now())
        rows = {str(k): v for k, v in (profile or {}).items()}
        if not rows:
            return ToolOutput.failed(f"nothing traded on {ticker} in that window")
        total = sum(rows.values())
        top = sorted(rows.items(), key=lambda kv: -kv[1])[:4]
        said = ", ".join(f"{p} ({n:,.0f}, {n / total:.0%})" for p, n in top)
        return ToolOutput(
            response=(f"{ticker}: {total:,.0f} contracts over {len(rows)} price "
                      f"levels. Heaviest: {said}."),
            raw_output={"profile": rows, "total": total,
                        "heaviest_price": top[0][0]})
