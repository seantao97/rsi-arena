"""What a league is playing, and the ids the other game tools need."""

from __future__ import annotations

from typing import Any

from rsi_arena.agent.tool import Tool, ToolOutput

from .. import gamestate as gs


class TodaysFixturesTool(Tool):
    name = "todays_fixtures"
    version = 1
    description = (
        "Fixtures for a league, with kick-off times and the game id every other "
        "game tool takes.\n\n"
        "One caution worth heeding: when a league has nothing on today the feed "
        "answers with the *next round* rather than an empty list, so check the "
        "dates before concluding that a quiet day is a busy one."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"league": {"type": "string"},
                       "date": {"type": "string",
                                "description": "YYYYMMDD. Defaults to today."}},
        "required": ["league"],
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        league = input["league"]
        games = gs.todays_games(league, input.get("date"))
        if not games:
            return ToolOutput(response=f"No {league} fixtures listed.",
                              raw_output={"games": []})
        when = sorted({g.get("start", "")[:10] for g in games if g.get("start")})
        said = "; ".join(f"{g.get('start','')[11:16]}Z {g.get('away')} at {g.get('home')}"
                         for g in games[:5])
        return ToolOutput(
            response=(f"{league}: {len(games)} fixture(s) dated {', '.join(when)} — "
                      f"{said}."),
            raw_output={"games": games, "dates": when})
