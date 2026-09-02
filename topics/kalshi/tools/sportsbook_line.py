"""The one outside opinion available."""

from __future__ import annotations

from typing import Any

from rsi_arena import Tool, ToolOutput

from .. import gamestate as gs


class SportsbookLineTool(Tool):
    name = "sportsbook_line"
    version = 1
    description = (
        "The sportsbook's price on a fixture, with the bookmaker's margin "
        "removed.\n\n"
        "The only reference outside the exchange. A Kalshi price far from a "
        "de-vigged book line is either an edge or a misreading of what the "
        "contract settles on — check the rules before assuming the first.\n\n"
        "Published for soccer competitions and not, as of this writing, for the "
        "US leagues. An absent line is a coverage fact, not a failure: price "
        "off the market and the game state instead."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"league": {"type": "string"}, "game_id": {"type": "string"}},
        "required": ["league", "game_id"],
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        league = input["league"]
        detail = gs.game_detail(league, input["game_id"])
        fair = detail.fair_probabilities()
        if not fair:
            return ToolOutput(
                response=(f"No sportsbook odds published for {league}. Soccer "
                          f"competitions carry them; the US leagues do not."),
                raw_output={"available": False})
        # Only the *_fair keys are probabilities. "spread" and "total" are the
        # handicap and the goal line — printing 0.5 and 3.5 as percentages read
        # as a 50% spread and a 350% total, which is worse than not saying it.
        probs = ", ".join(
            f"{k[:-5]} {v:.1%}" for k, v in fair.items()
            if k.endswith("_fair") and isinstance(v, (int, float)))
        lines = []
        if isinstance(fair.get("spread"), (int, float)):
            lines.append(f"handicap {fair['spread']:+g}")
        if isinstance(fair.get("total"), (int, float)):
            lines.append(f"total {fair['total']:g}")
        margin = fair.get("overround")
        return ToolOutput(
            response=(f"{fair.get('provider', 'book')} de-vigged: {probs}"
                      + (f". Lines: {', '.join(lines)}" if lines else "")
                      + (f". Margin {margin:.1%}"
                         if isinstance(margin, (int, float)) else "")
                      + ("" if fair.get("complete") else
                         ". Incomplete — not every outcome is priced")),
            raw_output={"available": True, **fair})
