"""Everything about one contract right now, in one call."""

from __future__ import annotations

from typing import Any

from rsi_arena.agent.tools import Tool, ToolOutput

from ._clients import HISTORY, QUOTES
from .state_change import StateChangeTool


class LiveSnapshotTool(Tool):
    name = "live_snapshot"
    version = 1
    description = (
        "The book, the tape, the score and what has just changed — one call "
        "instead of four.\n\n"
        "This is the state a forecast is made from, and assembling it a piece "
        "at a time costs four round trips and four sets of tokens every time "
        "the agent wakes up. Ask for the pieces separately when a question is "
        "about one of them; ask for this when the question is 'what is "
        "happening'.\n\n"
        "Needs the league and game id as well as the ticker — use "
        "find_game_for_market once and keep them."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "league": {"type": "string"},
            "game_id": {"type": "string"},
            "trades": {"type": "integer", "default": 8},
        },
        "required": ["ticker", "league", "game_id"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "book": {"type": "object",
                     "properties": {"yes_bid": {"type": ["number", "null"]},
                                    "yes_ask": {"type": ["number", "null"]},
                                    "mid": {"type": ["number", "null"]},
                                    "spread": {"type": ["number", "null"]},
                                    "volume": {"type": "number"}}},
            "game": {"type": "object",
                     "description": "The state_change payload."},
            "tape": {"type": "array"},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        ticker = input["ticker"]
        try:
            quote = QUOTES.get_market(ticker)
        except Exception as exc:
            return ToolOutput.failed(f"{type(exc).__name__}: {exc}")

        change = StateChangeTool().get_tool_output(
            {"league": input["league"], "game_id": input["game_id"]})
        raw = HISTORY.trades(ticker, None, None, int(input.get("trades", 8)))
        tape = [{"ts": t.get("created_time"),
                 "yes_price": t.get("yes_price_dollars"),
                 "count": t.get("count_fp"),
                 "taker_side": t.get("taker_side")} for t in raw]

        two_sided = quote.yes_bid is not None and quote.yes_ask is not None
        book = {"yes_bid": quote.yes_bid, "yes_ask": quote.yes_ask,
                "mid": quote.mid if two_sided else None,
                "spread": quote.spread if two_sided else None,
                "volume": quote.volume}
        said = (f"{change.response} Book "
                + (f"{quote.yes_bid:.2f}/{quote.yes_ask:.2f}, mid {quote.mid:.3f}, "
                   f"spread {quote.spread * 100:.0f}c" if two_sided
                   else "not two-sided")
                + f". {len(tape)} recent prints"
                + (f", newest {tape[0]['yes_price']}." if tape else "."))
        return ToolOutput(response=said,
                          raw_output={"book": book, "game": change.raw_output,
                                      "tape": tape})
