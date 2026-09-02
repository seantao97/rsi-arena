"""Price moves with nothing behind them."""

from __future__ import annotations

from typing import Any

from rsi_arena import Tool, ToolOutput

from .. import gamestate as gs
from ._clients import TIMELINE


class UnexplainedMovesTool(Tool):
    name = "unexplained_moves"
    version = 1
    description = (
        "Price moves with no play to account for them.\n\n"
        "Two readings and both are worth having. Either the market knows "
        "something the feed has not reported — in which case the price is ahead "
        "of the score you can see — or the feed lags the market, in which case "
        "the game state you are pricing off is stale and should be weighted "
        "down.\n\n"
        "Needs timestamped play-by-play, so not available for soccer."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"league": {"type": "string"}, "game_id": {"type": "string"},
                       "ticker": {"type": "string"},
                       "threshold": {"type": "number", "default": 0.05,
                                     "description": "Smallest move to report."}},
        "required": ["league", "game_id", "ticker"],
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        league, game_id, ticker = input["league"], input["game_id"], input["ticker"]
        state = gs.game_state(league, game_id, with_plays=True)
        if state.status == "scheduled":
            return ToolOutput(response="Game has not started.",
                              raw_output={"available": False})
        if not TIMELINE.coverage(state)["timestamped"]:
            return ToolOutput(
                response=f"{league} has no timestamped play-by-play.",
                raw_output={"available": False})
        entries = TIMELINE.build(league, game_id, [ticker], state=state)
        found = TIMELINE.leading_moves(entries, ticker,
                                       threshold=float(input.get("threshold", 0.05)))
        rows = [{"ts": m["ts"].isoformat(), "from": m["from"], "to": m["to"],
                 "move": round(m["move"], 4)} for m in found[:8]]
        if not rows:
            return ToolOutput(response=f"No unexplained moves on {ticker}.",
                              raw_output={"available": True, "count": 0, "moves": []})
        biggest = max(rows, key=lambda r: abs(r["move"]))
        return ToolOutput(
            response=(f"{len(found)} moves on {ticker} with no play behind them; "
                      f"largest {biggest['from']} -> {biggest['to']} at "
                      f"{biggest['ts'][11:19]}."),
            raw_output={"available": True, "count": len(found), "moves": rows})
