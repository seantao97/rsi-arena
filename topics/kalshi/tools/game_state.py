"""The score and the clock."""

from __future__ import annotations

from typing import Any

from rsi_arena import Tool, ToolOutput

from .. import gamestate as gs


class GameStateTool(Tool):
    name = "game_state"
    version = 1
    description = (
        "Score, period, clock and situation for one fixture.\n\n"
        "For soccer this is most of what is knowable in-play: the feeds publish "
        "no play-by-play for it while a match is running, so score and clock "
        "are the signal. Both matter — a goalless match at 85 minutes is a "
        "different market from the same scoreline at 20."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"league": {"type": "string"}, "game_id": {"type": "string"}},
        "required": ["league", "game_id"],
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        st = gs.game_state(input["league"], input["game_id"], False)
        out = {"status": st.status, "home": st.home, "away": st.away,
               "home_score": st.home_score, "away_score": st.away_score,
               "period": st.period, "clock": st.clock, "detail": st.detail}
        return ToolOutput(
            response=(f"{st.away} {st.away_score} - {st.home_score} {st.home}, "
                      f"{st.status}, period {st.period} at {st.clock}."),
            raw_output=out)
