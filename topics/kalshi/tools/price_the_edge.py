"""Whether a view is worth acting on."""

from __future__ import annotations

from typing import Any

from rsi_arena.agent.tool import Tool, ToolOutput

from ..fees import breakeven, edge, kelly, taker_fee


class PriceTheEdgeTool(Tool):
    name = "price_the_edge"
    version = 1
    description = (
        "Turn a probability and a price into an edge after fees, and a stake.\n\n"
        "Run it before claiming an edge, not after. The fee peaks near 50c, so "
        "the same two-cent disagreement is worthless at midprice and real at "
        "90c. The stake is fractional Kelly, which is smaller than it feels it "
        "should be — deliberately, because being right about direction says "
        "little about magnitude."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "probability": {"type": "number", "description": "Your view, 0-1."},
            "yes_price": {"type": "number", "description": "What it costs, 0-1."},
            "bankroll": {"type": "number", "default": 50000.0},
        },
        "required": ["probability", "yes_price"],
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        prob, price = float(input["probability"]), float(input["yes_price"])
        if not 0 < price < 1 or not 0 <= prob <= 1:
            return ToolOutput.failed("probability and price must sit in 0-1")
        bankroll = float(input.get("bankroll", 50000.0))
        ev, frac = edge(prob, price), kelly(prob, price)
        out = {"breakeven_probability": round(breakeven(price), 4),
               "fee_per_contract": round(taker_fee(price), 4),
               "edge_per_contract": round(ev, 4),
               "kelly_fraction": round(frac, 4),
               "suggested_stake_usd": round(frac * bankroll, 2),
               "worth_taking": ev > 0}
        verdict = (f"worth taking: {ev:+.4f} a contract, stake "
                   f"${out['suggested_stake_usd']:,.0f}" if ev > 0 else
                   f"not worth taking: {ev:+.4f} a contract after fees")
        return ToolOutput(
            response=(f"At {price:.2f} you need {out['breakeven_probability']:.1%} "
                      f"to break even and you have {prob:.1%} — {verdict}."),
            raw_output=out)
