"""A fixture's handicap and total lines, in order."""

from __future__ import annotations

import re
from typing import Any

from rsi_arena.agent.tools import Tool, ToolOutput

from ..linking import fixture_key
from ._clients import DISCOVERY

#: Ladders end in the line: NEW2 is Newcastle by 2+, TOTAL-4 is over 4.
_RUNG = re.compile(r"^(?P<code>[A-Z]*?)(?P<line>\d+(?:\.\d+)?)$")


class SpreadLadderTool(Tool):
    name = "spread_ladder"
    version = 1
    description = (
        "The handicap and total ladders on one fixture, sorted by line, with "
        "each rung's price and the step between rungs.\n\n"
        "A ladder has to be monotonic: winning by three or more cannot be more "
        "likely than winning by two or more. Seeing the rungs together is how "
        "an inconsistency becomes visible, and how the shape of the market's "
        "view becomes readable — a steep step between two lines says the market "
        "thinks the game turns on exactly that margin.\n\n"
        "Reading rungs one at a time cannot show either. Give any event on the "
        "fixture and a league."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "event_ticker": {"type": "string",
                             "description": "Any event on the fixture."},
            "league": {"type": "string"},
        },
        "required": ["event_ticker", "league"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "ladders": {"type": "object",
                        "description": "Series to its rungs, each with line, "
                                       "price and the step from the rung below."},
            "breaks": {"type": "array", "items": {"type": "string"},
                       "description": "Rungs whose prices are out of order."},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        want = fixture_key(input["event_ticker"])
        markets = [m for m in DISCOVERY.whats_bettable(league=input["league"],
                                                       fixtures_only=True)
                   if fixture_key(m.ticker) == want]
        if not markets:
            return ToolOutput.failed(f"no markets on the fixture behind "
                                     f"{input['event_ticker']}")

        rungs: dict[str, list[dict[str, Any]]] = {}
        for m in markets:
            tail = m.ticker.rsplit("-", 1)[-1]
            hit = _RUNG.match(tail)
            if not hit or not m.yes_bid or not m.yes_ask:
                continue
            key = f"{m.ticker.split('-')[0]}:{hit.group('code') or 'line'}"
            rungs.setdefault(key, []).append({
                "ticker": m.ticker, "line": float(hit.group("line")),
                "subtitle": m.subtitle,
                "mid": round((m.yes_bid + m.yes_ask) / 2, 4),
                "yes_bid": m.yes_bid, "yes_ask": m.yes_ask})

        # A single rung is not a ladder; there is nothing to compare it against.
        ladders = {k: sorted(v, key=lambda r: r["line"])
                   for k, v in rungs.items() if len(v) > 1}
        if not ladders:
            return ToolOutput(
                response=f"{want}: no multi-rung ladder is listed.",
                raw_output={"ladders": {}, "breaks": []})

        breaks = []
        for key, rows in ladders.items():
            for lower, higher in zip(rows, rows[1:]):
                higher["step"] = round(lower["mid"] - higher["mid"], 4)
                # A harder line must be cheaper. When it is not, one of the two
                # is mispriced and the pair is money without a forecast.
                if higher["mid"] > lower["mid"] + 1e-9:
                    breaks.append(
                        f"{key} {higher['line']:g} at {higher['mid']:.3f} is "
                        f"dearer than {lower['line']:g} at {lower['mid']:.3f}")
            rows[0]["step"] = None

        said = "; ".join(
            f"{key} " + " ".join(f"{r['line']:g}@{r['mid']:.2f}" for r in rows)
            for key, rows in list(ladders.items())[:3])
        return ToolOutput(
            response=(f"{want}: {len(ladders)} ladder(s). {said}."
                      + (f" {len(breaks)} out of order: {breaks[0]}"
                         if breaks else " Monotonic throughout.")),
            raw_output={"ladders": ladders, "breaks": breaks})
