"""How long the match has been quiet."""

from __future__ import annotations

from typing import Any

from rsi_arena.agent.tools import Tool, ToolOutput

from . import _gamestate as gs
from ._events import key_events


class MinutesSinceGoalTool(Tool):
    name = "minutes_since_goal"
    version = 1
    description = (
        "How long since the last goal, and where in the match that leaves "
        "things.\n\n"
        "Almost everything that moves a soccer price is a goal, so the time "
        "since the last one is the closest thing to a clock on the risk. A "
        "match that has been quiet for forty minutes is not the same as one "
        "that has just restarted after a goal, even at the same scoreline and "
        "the same minute.\n\n"
        "Needs a competition that timestamps its events. Where none is "
        "published this says so rather than guessing — use state_change there, "
        "which falls back to comparing successive looks."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"league": {"type": "string"}, "game_id": {"type": "string"}},
        "required": ["league", "game_id"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "available": {"type": "boolean"},
            "minute_now": {"type": ["integer", "null"]},
            "last_goal_minute": {"type": ["integer", "null"]},
            "minutes_since": {"type": ["integer", "null"]},
            "goals": {"type": "array", "items": {
                "type": "object",
                "properties": {"minute": {"type": "integer"},
                               "team": {"type": "string"}}}},
            "status": {"type": "string"},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        league, game_id = input["league"], input["game_id"]
        try:
            state = gs.game_state(league, game_id, False)
        except Exception as exc:
            return ToolOutput.failed(f"{type(exc).__name__}: {exc}")

        events = key_events(league, game_id)
        goals = [{"minute": e.minute, "team": e.team} for e in events if e.scored]
        if not events:
            return ToolOutput(
                response=(f"{league} publishes no event times, so the minute of "
                          f"the last goal is unknown. Score is "
                          f"{state.away_score}-{state.home_score} at "
                          f"{state.clock}."),
                raw_output={"available": False, "goals": [],
                            "status": state.status, "minute_now": None,
                            "last_goal_minute": None, "minutes_since": None})

        try:
            minute_now = int(str(state.clock).strip("'+ ") or 0)
        except ValueError:
            minute_now = max((e.minute for e in events), default=0)
        # A stopped clock on a finished match would report the last goal as
        # having just happened.
        if state.status == "final":
            since = None
        elif goals:
            since = max(0, minute_now - goals[-1]["minute"])
        else:
            since = minute_now

        out = {"available": True, "minute_now": minute_now,
               "last_goal_minute": goals[-1]["minute"] if goals else None,
               "minutes_since": since, "goals": goals, "status": state.status}
        if state.status == "final":
            said = (f"Full time, {len(goals)} goal(s)"
                    + (f", last at {goals[-1]['minute']}'." if goals else "."))
        elif not goals:
            said = f"Goalless through {minute_now}'."
        else:
            said = (f"{len(goals)} goal(s), last at {goals[-1]['minute']}' — "
                    f"{since} minute(s) ago at {minute_now}'.")
            if since is not None and since <= 3:
                said += " The market may still be absorbing it."
        return ToolOutput(response=said, raw_output=out)
