"""Everything around a fixture that is not the score."""

from __future__ import annotations

from typing import Any

from rsi_arena import Tool, ToolOutput

from .. import gamestate as gs


class GameContextTool(Tool):
    name = "game_context"
    version = 1
    description = (
        "Venue, weather, officials, injuries, recent form, head-to-head and "
        "statistical leaders for one fixture, in a single request.\n\n"
        "Pre-match colour rather than in-play signal: little of it changes once "
        "a game is running, and the market has read all of it already. Useful "
        "for understanding why a price sits where it does, not for predicting "
        "where it goes next. Sections a sport does not publish come back empty."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"league": {"type": "string"}, "game_id": {"type": "string"}},
        "required": ["league", "game_id"],
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        d = gs.game_detail(input["league"], input["game_id"])
        out = {"venue": d.venue.get("fullName"), "attendance": d.attendance,
               "weather": d.weather, "officials": d.officials,
               "injuries": d.injuries[:2], "leaders": d.leaders[:2],
               "recent_form": d.last_five, "head_to_head": d.season_series,
               "has_boxscore": bool(d.boxscore.get("teams"))}
        have = [k for k in ("venue", "weather", "injuries", "recent_form",
                            "head_to_head") if out.get(k)]
        return ToolOutput(
            response=(f"{out['venue'] or 'venue unknown'}"
                      f"{f', attendance {d.attendance:,}' if d.attendance else ''}. "
                      f"Sections with data: {', '.join(have) or 'none'}."),
            raw_output=out)
