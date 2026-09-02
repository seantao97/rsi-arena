"""Markets on matches being played right now."""

from __future__ import annotations

from typing import Any

from rsi_arena import Tool, ToolOutput

from . import _gamestate as gs
from ._linking import link_event, names_from_markets
from ._clients import CLIENT, DISCOVERY


class LiveMarketsTool(Tool):
    name = "live_markets"
    version = 1
    description = (
        "Contracts on fixtures currently in progress, grouped by match with "
        "the score and clock.\n\n"
        "The starting point for in-play work: it finds what is being played, "
        "then the markets written on it. An empty answer usually means the "
        "schedule is empty, not that something is broken — but a match that is "
        "live with no market attached is worth noticing, since it means the "
        "exchange has not listed that fixture."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "league": {"type": "string"},
            "limit": {"type": "integer", "default": 20,
                      "description": "Markets per match."},
        },
        "required": ["league"],
    }

    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "live_games": {"type": "integer",
                           "description": "Fixtures in progress, listed or not."},
            "markets": {"type": "array", "items": {
                "type": "object",
                "properties": {"game_id": {"type": "string"},
                               "score": {"type": "string"},
                               "period": {"type": "string"},
                               "clock": {"type": "string"},
                               "markets": {"type": "array"}}}},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        league = input["league"]
        limit = int(input.get("limit", 20))
        games = gs.live_games([league])
        if not games:
            return ToolOutput(
                response=f"No {league} fixture is in progress right now.",
                raw_output={"live_games": 0, "markets": []})

        by_game: dict[str, dict[str, Any]] = {}
        for _lg, game_id, state in games:
            by_game[game_id] = {
                "game_id": game_id,
                "score": (f"{state.away} {state.away_score} - "
                          f"{state.home_score} {state.home}"),
                "period": state.period, "clock": state.clock, "markets": []}

        # One sweep gives both the markets and the league's own club names.
        # Team codes are not always abbreviations of the feed's name — Liverpool
        # trades as LFC — and without the labels a real fixture scores too low
        # to link and the match is missed silently.
        refs = list(DISCOVERY.whats_bettable(league=league, fixtures_only=True))
        names = names_from_markets(refs)

        seen: dict[str, str | None] = {}
        for m in refs:
            if by_game and all(len(g["markets"]) >= limit for g in by_game.values()):
                break
            event = m.event_ticker
            if event not in seen:
                link = link_event(CLIENT, event, league,
                                  lambda lg, day: gs.todays_games(lg, day),
                                  names=names)
                seen[event] = link.game_id if link else None
            gid = seen[event]
            if gid in by_game and len(by_game[gid]["markets"]) < limit:
                by_game[gid]["markets"].append(
                    {"ticker": m.ticker, "subtitle": m.subtitle,
                     "type": m.market_type, "yes_bid": m.yes_bid,
                     "yes_ask": m.yes_ask, "volume": m.volume})

        linked = [g for g in by_game.values() if g["markets"]]
        if not linked:
            return ToolOutput(
                response=(f"{len(games)} {league} fixture(s) in progress, none "
                          f"with a market listed on the exchange."),
                raw_output={"live_games": len(games), "markets": []})
        said = "; ".join(f"{g['score']} ({g['clock']}) — {len(g['markets'])} markets"
                         for g in linked[:4])
        return ToolOutput(
            response=f"{len(linked)} of {len(games)} live {league} fixtures tradeable: {said}.",
            raw_output={"live_games": len(games), "markets": linked})
