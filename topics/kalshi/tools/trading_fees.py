"""What a trade costs, before deciding whether it is worth making."""

from __future__ import annotations

from typing import Any

from rsi_arena import Tool, ToolOutput

from ..fees import breakeven, edge, maker_fee, taker_fee


class TradingFeesTool(Tool):
    name = "trading_fees"
    version = 1
    description = (
        "What it costs to trade a contract at a given price, and how far the "
        "price has to move before that cost is paid back.\n\n"
        "Kalshi charges on a curve, not a flat rate: the fee peaks near 50c and "
        "falls to almost nothing in the tails, so the same edge is worth trading "
        "at 0.05 and not at 0.50. Resting an order costs about a quarter of "
        "crossing the spread.\n\n"
        "The number that usually decides a five-minute trade is the round trip — "
        "getting in and out both cost, and on a two-cent book that is most of "
        "the move being predicted. Ask this before committing to a view, not "
        "after."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "price": {"type": "number",
                      "description": "Contract price in dollars, 0-1."},
            "contracts": {"type": "number", "default": 1.0},
            "probability": {"type": "number",
                            "description": "Optional. Your own probability, to "
                                           "get the edge net of fees."},
        },
        "required": ["price"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "taker_fee": {"type": "number"}, "maker_fee": {"type": "number"},
            "breakeven_taker": {"type": "number"},
            "breakeven_round_trip": {"type": "number"},
            "edge_after_fees": {"type": "number"},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        price = float(input["price"])
        if not 0 < price < 1:
            return ToolOutput.failed(f"price {price} is outside (0, 1)")
        n = float(input.get("contracts", 1.0))

        take, make = taker_fee(price, n), maker_fee(price, n)
        be_taker = breakeven(price, maker=False)
        be_round = breakeven(price, maker=False, round_trip=True)
        out: dict[str, Any] = {
            "price": price, "contracts": n,
            "taker_fee": round(take, 4), "maker_fee": round(make, 4),
            "breakeven_taker": round(be_taker, 4),
            "breakeven_round_trip": round(be_round, 4),
        }
        said = (f"At {price:.2f}: taking costs {take / n:.4f} a contract, resting "
                f"{make / n:.4f}. One leg breaks even at {be_taker:.1%}; a round "
                f"trip needs {be_round:.1%}.")

        prob = input.get("probability")
        if isinstance(prob, (int, float)):
            net = edge(float(prob), price, maker=False)
            out["edge_after_fees"] = round(net, 4)
            said += (f" At a {float(prob):.0%} view the edge is {net:+.4f} after "
                     f"taker fees{'' if net > 0 else ' — not worth taking'}.")

        return ToolOutput(response=said, raw_output=out)
