"""The exchange against the sportsbook."""

from __future__ import annotations

from typing import Any

from rsi_arena import Tool, ToolOutput

from ..implied import kalshi_vs_book


class KalshiVsBookTool(Tool):
    name = "kalshi_vs_book"
    version = 1
    description = (
        "Compare one Kalshi price against a de-vigged sportsbook line and "
        "report the gap.\n\n"
        "The only cross-check available against an opinion formed elsewhere. A "
        "wide gap is either an edge or a misreading of what the contract "
        "settles on, and the second is far more common — check the rules before "
        "acting on it. Pass every book outcome, draw included, or the de-vig is "
        "meaningless."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "kalshi_price": {"type": "number", "description": "0-1."},
            "book_odds": {"type": "array", "items": {"type": "number"},
                          "description": "American odds for every outcome."},
            "index": {"type": "integer", "default": 0,
                      "description": "Which outcome the Kalshi price is on."},
            "method": {"type": "string", "enum": ["proportional", "power"],
                       "default": "proportional"},
        },
        "required": ["kalshi_price", "book_odds"],
    }

    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "kalshi_price": {"type": "number"},
            "book_fair": {"type": "number"},
            "difference": {"type": "number"},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        price = float(input["kalshi_price"])
        odds = list(input["book_odds"])
        if not 0 < price < 1:
            return ToolOutput.failed(f"kalshi_price {price} is outside (0, 1)")
        if len(odds) < 2:
            return ToolOutput.failed("pass every book outcome, not one")
        index = int(input.get("index", 0))
        if not 0 <= index < len(odds):
            return ToolOutput.failed(f"index {index} is outside the odds given")
        out = kalshi_vs_book(price, odds, index, input.get("method", "proportional"))
        gap = out.get("difference") or out.get("edge") or 0.0
        return ToolOutput(
            response=(f"Kalshi {price:.3f} against a de-vigged book line of "
                      f"{out.get('book_fair', float('nan')):.3f}: gap "
                      f"{gap:+.3f}. Check the settlement rules before treating "
                      f"that as an edge."),
            raw_output=out)
