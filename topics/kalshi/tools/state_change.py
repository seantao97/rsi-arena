"""What has happened since the last look."""

from __future__ import annotations

from typing import Any

from rsi_arena.agent.tools import Tool, ToolOutput

from . import _gamestate as gs
from ._events import NOTABLE, key_events, remember, seconds_since


class StateChangeTool(Tool):
    name = "state_change"
    version = 1
    description = (
        "What has changed in this match recently — whether the score moved, "
        "how long ago, and what else happened.\n\n"
        "A scoreline on its own does not say this. A contract on a match at 1-0 "
        "is a completely different thing depending on whether the goal went in "
        "thirty seconds ago or half an hour ago: one price has absorbed it and "
        "the other has not. Asking only for the state cannot tell the two "
        "apart, and pricing them the same is how a market that is still moving "
        "gets treated as settled.\n\n"
        "Prefers the feed's timestamped events, which give the minute a goal "
        "went in. Where a competition publishes none it falls back to comparing "
        "against the previous look in this session, which answers the narrower "
        "'what changed since I last asked'. It says which of the two it used."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "league": {"type": "string"},
            "game_id": {"type": "string"},
            "recent_minutes": {"type": "number", "default": 10.0,
                               "description": "How far back counts as recent."},
        },
        "required": ["league", "game_id"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "enum": ["events", "baseline", "first_look"],
                       "description": "Where the change came from."},
            "score": {"type": "string"},
            "clock": {"type": "string"},
            "minutes_since_score": {"type": ["number", "null"]},
            "recent": {"type": "array", "items": {
                "type": "object",
                "properties": {"minute": {"type": "integer"},
                               "kind": {"type": "string"},
                               "team": {"type": "string"},
                               "text": {"type": "string"}}}},
            "score_changed": {"type": ["boolean", "null"],
                              "description": "Against the previous look, when "
                                             "that is the source."},
            "since_last_look_seconds": {"type": ["number", "null"]},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        league, game_id = input["league"], input["game_id"]
        window = float(input.get("recent_minutes", 10.0))
        try:
            state = gs.game_state(league, game_id, False)
        except Exception as exc:
            return ToolOutput.failed(f"{type(exc).__name__}: {exc}")

        score = f"{state.away} {state.away_score} - {state.home_score} {state.home}"
        # A finished match has a stopped clock, so "minutes since" measured off
        # it reads as though the last goal just went in. Nothing is absorbing
        # anything here.
        if state.status == "final":
            events = key_events(league, game_id)
            scored = [e for e in events if e.scored]
            return ToolOutput(
                response=(f"{score}, full time. "
                          + (f"Last goal at {scored[-1].minute}'."
                             if scored else "No goals.")
                          + " Nothing left to move."),
                raw_output={"source": "events" if events else "first_look",
                            "score": score, "clock": state.clock,
                            "status": "final", "recent": [],
                            "minutes_since_score": None, "score_changed": False,
                            "since_last_look_seconds": None})
        out: dict[str, Any] = {"score": score, "clock": state.clock,
                               "status": state.status, "recent": [],
                               "minutes_since_score": None,
                               "score_changed": None,
                               "since_last_look_seconds": None}

        # Always record this look, whichever source ends up being used, so the
        # fallback has a baseline the next time round.
        previous = remember(f"{league}:{game_id}",
                            {"score": score, "clock": state.clock,
                             "home": state.home_score, "away": state.away_score})
        if previous:
            out["since_last_look_seconds"] = seconds_since(previous.get("at"))
            out["score_changed"] = (previous.get("home") != state.home_score
                                    or previous.get("away") != state.away_score)

        events = key_events(league, game_id)
        if events:
            now_minute = max((e.minute for e in events), default=0)
            try:
                now_minute = max(now_minute, int(str(state.clock).strip("'+ ") or 0))
            except ValueError:
                pass
            recent = [e for e in events if now_minute - e.minute <= window]
            scored = [e for e in events if e.scored]
            out["source"] = "events"
            out["recent"] = [{"minute": e.minute, "kind": e.kind, "team": e.team,
                              "text": e.text[:140]} for e in recent[-6:]]
            if scored:
                out["minutes_since_score"] = max(0, now_minute - scored[-1].minute)

            if out["minutes_since_score"] is not None and out["minutes_since_score"] <= 3:
                said = (f"{score} at {state.clock}. A goal went in "
                        f"{out['minutes_since_score']} minute(s) ago — the price "
                        f"may still be absorbing it.")
            elif out["minutes_since_score"] is not None:
                said = (f"{score} at {state.clock}. Last goal was "
                        f"{out['minutes_since_score']} minutes ago.")
            else:
                said = f"{score} at {state.clock}. No goal yet."
            notable = [e for e in recent if e.kind in NOTABLE]
            if notable:
                said += (" Also in the last "
                         f"{window:.0f} minutes: "
                         + ", ".join(f"{e.minute}' {e.kind}" for e in notable[:3])
                         + ".")
            return ToolOutput(response=said, raw_output=out)

        # No timestamped events for this competition — soccer, usually.
        if previous is None:
            out["source"] = "first_look"
            return ToolOutput(
                response=(f"{score} at {state.clock}. No timestamped events for "
                          f"{league}, and nothing to compare against yet — ask "
                          f"again to see what changed."),
                raw_output=out)

        out["source"] = "baseline"
        gap = out["since_last_look_seconds"] or 0
        if out["score_changed"]:
            said = (f"{score} at {state.clock}. The score CHANGED from "
                    f"{previous.get('away')}-{previous.get('home')} in the last "
                    f"{gap / 60:.0f} minutes — the price may still be absorbing it.")
        else:
            said = (f"{score} at {state.clock}. Unchanged over the last "
                    f"{gap / 60:.0f} minutes.")
        return ToolOutput(response=said + f" ({league} publishes no event times.)",
                          raw_output=out)
