"""Where on a fixture an order can actually be placed."""

from __future__ import annotations

from typing import Any

from rsi_arena.agent.tools import Tool, ToolOutput

from ._linking import fixture_key
from ._clients import DISCOVERY


class TradeableSpreadsTool(Tool):
    name = "tradeable_spreads"
    version = 1
    description = (
        "Every market on one fixture ranked by how wide its book is, with the "
        "room inside each.\n\n"
        "The number that decides whether a view can be acted on. A one-cent "
        "book cannot be improved on — there is no price between the bid and the "
        "ask — so an order can only cross, and crossing plus the fee costs more "
        "than most five-minute moves are worth. A ten-cent book has room for a "
        "resting order at a quarter of the fee.\n\n"
        "Measured on a live slate, about seven in ten books are a cent or two "
        "wide, and the wide ones are almost never the match winner: corners, "
        "team totals and spreads are where the room is. Call this before "
        "deciding which contract to have a view about, not after.\n\n"
        "Give it any event ticker on the fixture and a league, and it scans "
        "every kind of bet on that match — the winner, the spreads, the totals, "
        "the corners, the halves — because they live under different series and "
        "asking about one tells you nothing about the others."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "event_ticker": {"type": "string",
                             "description": "Any event on the fixture."},
            "league": {"type": "string",
                       "description": "Needed to sweep the other market types."},
            "min_spread_cents": {"type": "number", "default": 2.0,
                                 "description": "Ignore books tighter than this."},
        },
        "required": ["event_ticker", "league"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "tradeable": {"type": "array", "items": {
                "type": "object",
                "properties": {"ticker": {"type": "string"},
                               "subtitle": {"type": "string"},
                               "type": {"type": "string"},
                               "yes_bid": {"type": "number"},
                               "yes_ask": {"type": "number"},
                               "spread_cents": {"type": "number"},
                               "room_cents": {"type": "number",
                                              "description": "Prices strictly "
                                                             "inside the book."},
                               "volume": {"type": "number"}}}},
            "too_tight": {"type": "integer"},
            "unquoted": {"type": "integer"},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        event = input["event_ticker"]
        floor = float(input.get("min_spread_cents", 2.0))
        # One cached league sweep, filtered to this fixture. A fixture appears
        # under a series per kind of bet, so asking the event alone would miss
        # exactly the wide books worth finding.
        want = fixture_key(event)
        markets = [m for m in DISCOVERY.whats_bettable(league=input["league"],
                                                       fixtures_only=True)
                   if fixture_key(m.ticker) == want]
        if not markets:
            return ToolOutput.failed(f"no markets on the fixture behind {event}")

        rows, tight, unquoted = [], 0, 0
        for m in markets:
            if not m.yes_bid or not m.yes_ask:
                unquoted += 1
                continue
            spread = round((m.yes_ask - m.yes_bid) * 100, 1)
            if spread < floor:
                tight += 1
                continue
            rows.append({"ticker": m.ticker, "subtitle": m.subtitle,
                         "series": m.ticker.split("-")[0],
                         "type": m.market_type, "yes_bid": m.yes_bid,
                         "yes_ask": m.yes_ask, "spread_cents": spread,
                         # Whole cents strictly between bid and ask. Zero means
                         # a resting order can only join the queue, not improve.
                         "room_cents": max(0, int(round(spread)) - 1),
                         "volume": m.volume})
        rows.sort(key=lambda r: -r["spread_cents"])

        if not rows:
            return ToolOutput(
                response=(f"{event}: nothing wider than {floor:.0f}c. "
                          f"{tight} books too tight to rest an order in, "
                          f"{unquoted} unquoted. Taking is the only option here, "
                          f"and it has to beat the fee on its own."),
                raw_output={"tradeable": [], "too_tight": tight,
                            "unquoted": unquoted})
        top = ", ".join(f"{r['subtitle'] or r['ticker'].rsplit('-', 1)[-1]} "
                        f"{r['spread_cents']:.0f}c" for r in rows[:5])
        return ToolOutput(
            response=(f"{want}: {len(rows)} books at least {floor:.0f}c wide — "
                      f"{top}. {tight} too tight, {unquoted} unquoted. Widest is "
                      f"{rows[0]['ticker']} at {rows[0]['yes_bid']:.2f}/"
                      f"{rows[0]['yes_ask']:.2f}."),
            raw_output={"tradeable": rows, "too_tight": tight,
                        "unquoted": unquoted})
