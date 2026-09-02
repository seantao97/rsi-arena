"""How much this contract moves when something happens."""

from __future__ import annotations

from typing import Any

from rsi_arena import Tool, ToolOutput

from .. import gamestate as gs
from ._clients import TIMELINE


class MarketReactionTool(Tool):
    name = "market_reaction"
    version = 1
    description = (
        "How far this exact contract moved on each scoring play so far.\n\n"
        "Sensitivity measured rather than assumed. If a goal moved the price "
        "twelve cents earlier, the next one probably moves it about as much — "
        "which is what says whether the current price has already absorbed what "
        "just happened or is still absorbing it.\n\n"
        "Needs timestamped play-by-play, which soccer does not publish. Use it "
        "on baseball and basketball; on soccer it will say so and return the "
        "state instead."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"league": {"type": "string"}, "game_id": {"type": "string"},
                       "ticker": {"type": "string"}},
        "required": ["league", "game_id", "ticker"],
    }

    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "available": {"type": "boolean"}, "score": {"type": "string"},
            "reactions": {"type": "array", "items": {
                "type": "object",
                "properties": {"play": {"type": "string"},
                               "price_before": {"type": ["number", "null"]},
                               "price_after": {"type": ["number", "null"]},
                               "move": {"type": ["number", "null"]},
                               "max_swing": {"type": ["number", "null"]}}}},
            "coverage": {"type": "object"},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        league, game_id, ticker = input["league"], input["game_id"], input["ticker"]
        state = gs.game_state(league, game_id, with_plays=True)
        if state.status == "scheduled":
            return ToolOutput(response="Game has not started; no plays to react to.",
                              raw_output={"available": False})
        score = (f"{state.away} {state.away_score} - "
                 f"{state.home_score} {state.home}")
        cover = TIMELINE.coverage(state)
        if not cover["timestamped"]:
            return ToolOutput(
                response=(f"{league} publishes no timestamped play-by-play, so no "
                          f"move can be attributed to an event. State: {score}, "
                          f"{state.clock}."),
                raw_output={"available": False, "score": score, "coverage": cover})

        entries = TIMELINE.build(league, game_id, [ticker], state=state)
        moves = TIMELINE.reactions(entries, ticker)
        rows = [{"play": r.play.description[:120], "price_before": r.before,
                 "price_after": r.after,
                 "move": round(r.move, 4) if r.move is not None else None,
                 "max_swing": round(r.swing, 4) if r.swing is not None else None}
                for r in moves]
        sized = [abs(r["move"]) for r in rows if r["move"] is not None]
        typical = (f"typically {sum(sized) / len(sized) * 100:.1f}c a scoring play"
                   if sized else "no measurable moves yet")
        return ToolOutput(
            response=f"{score}. {len(rows)} scoring plays on {ticker}, {typical}.",
            raw_output={"available": True, "score": score, "reactions": rows,
                        "coverage": cover})
