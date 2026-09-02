"""Which real fixture a Kalshi event is about."""

from __future__ import annotations

from typing import Any

from rsi_arena import Tool, ToolOutput

from .. import gamestate as gs
from ..linking import link_event, names_from_markets
from ._clients import CLIENT, DISCOVERY


class FindGameForMarketTool(Tool):
    name = "find_game_for_market"
    version = 1
    description = (
        "Resolve a Kalshi event ticker to the fixture it refers to, returning "
        "the game id the other game tools take.\n\n"
        "The join between the exchange and the score feed. Ticker codes are not "
        "always abbreviations of the club name — Liverpool trades as LFC — so "
        "this uses the club names the exchange itself publishes. A confidence "
        "below about 0.6 means the match is a guess and should not be relied on."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"event_ticker": {"type": "string"},
                       "league": {"type": "string"}},
        "required": ["event_ticker", "league"],
    }

    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "game_id": {"type": "string"}, "league": {"type": "string"},
            "home": {"type": "string"}, "away": {"type": "string"},
            "confidence": {"type": "number",
                           "description": "Below 0.6 the match is a guess."},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        event, league = input["event_ticker"], input["league"]
        refs = list(DISCOVERY.whats_bettable(league=league, fixtures_only=True))
        names = names_from_markets(refs)
        codes = set(names)
        for ref in refs:
            tail = ref.ticker.rsplit("-", 1)[-1] if "-" in ref.ticker else ""
            stem = tail.rstrip("0123456789")
            if stem and stem in names:
                codes.add(stem)
        link = link_event(CLIENT, event, league,
                          lambda lg, day: gs.todays_games(lg, day),
                          names=names, codes=codes)
        if not link:
            return ToolOutput.failed(f"no fixture matched {event}")
        return ToolOutput(
            response=(f"{event} is {link.away} at {link.home}, game id "
                      f"{link.game_id} (confidence {link.confidence})."),
            raw_output={"game_id": link.game_id, "league": league,
                        "home": link.home, "away": link.away,
                        "confidence": link.confidence})
