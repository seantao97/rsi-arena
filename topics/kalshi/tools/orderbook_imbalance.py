"""Which side of the book is heavier."""

from __future__ import annotations

from typing import Any

from rsi_arena.agent.tools import Tool, ToolOutput

from ._clients import QUOTES


class OrderbookImbalanceTool(Tool):
    name = "orderbook_imbalance"
    version = 1
    description = (
        "How much size is resting on each side of a book, and how lopsided "
        "that makes it.\n\n"
        "The classic short-horizon signal, and the one that matches a "
        "five-minute question: a book with four thousand contracts bid against "
        "four hundred offered is more likely to tick up than down, whatever the "
        "game is doing. It says nothing about who wins.\n\n"
        "Depth near the touch is what moves a price, so the ratio is reported "
        "both across the whole book and within a couple of cents of the best "
        "price. Thin books flip on one order, so the totals are there to judge "
        "whether the ratio means anything."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "near_cents": {"type": "integer", "default": 2,
                           "description": "How close to the touch counts as near."},
        },
        "required": ["ticker"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "yes_size": {"type": "number"}, "no_size": {"type": "number"},
            "yes_near": {"type": "number"}, "no_near": {"type": "number"},
            "ratio": {"type": ["number", "null"],
                      "description": "Yes size over no size. Above one leans up."},
            "near_ratio": {"type": ["number", "null"]},
            "levels": {"type": "object"},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        ticker = input["ticker"]
        near = int(input.get("near_cents", 2))
        book = QUOTES.get_orderbook(ticker, 20)
        if not book.yes and not book.no:
            return ToolOutput.failed(f"no resting orders on {ticker}")

        yes_all = sum(q for _, q in book.yes)
        no_all = sum(q for _, q in book.no)
        yes_near = book.depth_within("yes", near)
        no_near = book.depth_within("no", near)

        def lean(a: float, b: float) -> float | None:
            return round(a / b, 3) if b else None

        ratio, near_ratio = lean(yes_all, no_all), lean(yes_near, no_near)
        if ratio is None:
            verdict = "one-sided book"
        elif ratio > 1.5:
            verdict = "leaning yes"
        elif ratio < 0.67:
            verdict = "leaning no"
        else:
            verdict = "balanced"
        thin = (yes_all + no_all) < 500
        return ToolOutput(
            response=(f"{ticker}: {yes_all:,.0f} bid against {no_all:,.0f} offered "
                      f"({verdict}"
                      + (f", {ratio:.2f}x" if ratio is not None else "") + "). "
                      f"Within {near}c of the touch: {yes_near:,.0f} vs "
                      f"{no_near:,.0f}."
                      + (" Thin enough that one order flips it."
                         if thin else "")),
            raw_output={"yes_size": yes_all, "no_size": no_all,
                        "yes_near": yes_near, "no_near": no_near,
                        "ratio": ratio, "near_ratio": near_ratio,
                        "levels": {"yes": book.yes[:6], "no": book.no[:6]}})
