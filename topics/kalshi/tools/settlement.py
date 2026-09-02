"""How a market resolved, and whether the entry was any good."""

from __future__ import annotations

from typing import Any

from rsi_arena.agent.tool import Tool, ToolOutput

from ..fees import clv
from ._clients import HISTORY


class SettlementTool(Tool):
    name = "market_settlement"
    version = 1
    description = (
        "Whether a market has settled, which way, and what the last two-sided "
        "quote was before it closed.\n\n"
        "Two uses. During a match it answers whether a contract is still live "
        "at all — a settled market quotes 0.00/1.00 and any view formed on it "
        "is a view on nothing. Afterwards, that closing quote is the fairest "
        "available benchmark for an entry: beating the close is a better test "
        "of a decision than the settlement, which is one sample of a "
        "probability.\n\n"
        "Pass entry_price to get that comparison directly."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "entry_price": {"type": "number",
                            "description": "Optional. What you paid, to score "
                                           "it against the close."},
            "side": {"type": "string", "enum": ["yes", "no"], "default": "yes"},
        },
        "required": ["ticker"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"settled": {"type": "boolean"},
                       "result": {"type": ["string", "null"]},
                       "closing_price": {"type": ["number", "null"]},
                       "clv": {"type": ["number", "null"]}},
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        ticker = input["ticker"]
        result = HISTORY.settlement(ticker)
        close = HISTORY.closing_quote(ticker)
        closing = close.mid if close and close.two_sided else None
        out: dict[str, Any] = {"ticker": ticker, "settled": result is not None,
                               "result": result, "closing_price": closing}

        if result is None:
            said = f"{ticker} has not settled."
        else:
            said = f"{ticker} settled {result.upper()}."
        if closing is not None:
            said += f" Last two-sided quote before close: {closing:.3f}."

        entry = input.get("entry_price")
        if isinstance(entry, (int, float)) and closing is not None:
            side = input.get("side", "yes")
            value = clv(float(entry), closing, side)
            out["clv"] = round(value, 4)
            said += (f" Entering {side} at {float(entry):.3f} was "
                     f"{value:+.4f} a contract against the close.")
        return ToolOutput(response=said, raw_output=out)
