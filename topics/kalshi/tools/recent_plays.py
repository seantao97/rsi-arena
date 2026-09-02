"""What just happened, where a feed says so."""

from __future__ import annotations

from typing import Any

from rsi_arena import Tool, ToolOutput

from . import _gamestate as gs


class RecentPlaysTool(Tool):
    name = "recent_plays"
    version = 1
    description = (
        "The last plays in a game, oldest first, with scoring plays flagged.\n\n"
        "Coverage is narrower than it looks and knowing where is part of using "
        "it. Baseball gives every pitch and basketball gives full play lists, "
        "but **no soccer play-by-play is published while a match is live** — an "
        "EPL fixture in progress returns nothing. Soccer in-play is score and "
        "clock only, and this returns those instead so the caller is not left "
        "blind."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"league": {"type": "string"}, "game_id": {"type": "string"},
                       "limit": {"type": "integer", "default": 12}},
        "required": ["league", "game_id"],
    }

    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "available": {"type": "boolean",
                          "description": "False for soccer, which publishes none."},
            "plays": {"type": "array", "items": {
                "type": "object",
                "properties": {"ts": {"type": "string"}, "period": {"type": "string"},
                               "clock": {"type": "string"},
                               "description": {"type": "string"},
                               "scoring": {"type": "boolean"}}}},
            "score": {"type": "string"}, "period": {"type": "string"},
            "clock": {"type": "string"},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        limit = int(input.get("limit", 12))
        st = gs.game_state(input["league"], input["game_id"], True)
        score = f"{st.away} {st.away_score} - {st.home_score} {st.home}"
        if not st.plays:
            return ToolOutput(
                response=(f"No play-by-play published for {input['league']}. "
                          f"State: {score}, {st.status}, {st.clock}."),
                raw_output={"available": False, "status": st.status,
                            "score": score, "period": st.period, "clock": st.clock})
        plays = [{"ts": p.ts, "period": p.period, "clock": p.clock,
                  "description": p.description, "scoring": p.scoring}
                 for p in st.plays[-limit:]]
        scoring = sum(1 for p in plays if p["scoring"])
        return ToolOutput(
            response=(f"{score}. Last {len(plays)} plays, {scoring} scoring. "
                      f"Most recent: {plays[-1]['description'][:110]}"),
            raw_output={"available": True, "plays": plays})
