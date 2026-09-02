"""Prices on one fixture that cannot all be right."""

from __future__ import annotations

from typing import Any

from rsi_arena import Tool, ToolOutput

from ._clients import COHERENCE


class CoherenceCheckTool(Tool):
    name = "coherence_check"
    version = 1
    description = (
        "Inconsistent pricing across the markets on one fixture — a spread and "
        "a moneyline that disagree, a ladder out of order, outcomes summing "
        "wrong.\n\n"
        "A violation is money available without needing a forecast, which makes "
        "it the cheapest kind of edge there is. Only findings that survive fees "
        "at the size actually quoted are returned, so an empty answer is the "
        "usual one and means the book is coherent."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"event_ticker": {"type": "string"}},
        "required": ["event_ticker"],
    }

    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "violations": {"type": "array", "items": {
                "type": "object",
                "properties": {"kind": {"type": "string"},
                               "tickers": {"type": "array"},
                               "detail": {"type": "string"},
                               "net_per_contract": {"type": "number"},
                               "size": {"type": "number"},
                               "value_usd": {"type": "number"}}}},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        event = input["event_ticker"]
        rows = [{"kind": v.kind, "tickers": v.tickers, "detail": v.detail,
                 "net_per_contract": round(v.net, 4), "size": v.size,
                 "value_usd": round(v.value, 2)}
                for v in COHERENCE.check_event(event)]
        if not rows:
            return ToolOutput(response=f"{event}: prices are coherent, nothing free.",
                              raw_output={"violations": []})
        best = max(rows, key=lambda r: r["value_usd"])
        return ToolOutput(
            response=(f"{event}: {len(rows)} inconsistency(ies) surviving fees. "
                      f"Best is {best['kind']} worth ${best['value_usd']:,.2f} at "
                      f"quoted size — {best['detail'][:100]}"),
            raw_output={"violations": rows})
