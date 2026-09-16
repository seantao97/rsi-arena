"""The price path read as probability rather than as price."""

from __future__ import annotations

from typing import Any

from rsi_arena import Tool, ToolOutput

from ._history import HOUR, MINUTE
from ._implied import devig, overround
from ._clients import DISCOVERY, HISTORY, hours_ago, now


class CandlestickProbTool(Tool):
    name = "candlestick_probabilities"
    version = 1
    description = (
        "The implied probability of every outcome on one fixture, with the "
        "bookmaker's margin removed, and how it has moved.\n\n"
        "A single contract's mid is not a probability. The outcomes of an event "
        "sum to more than one — that surplus is the exchange's margin — so 0.61 "
        "on the favourite is not a 61% chance. This de-vigs the whole set so the "
        "outcomes sum to one, which is the number to compare a view against.\n\n"
        "Give it the event ticker, not a single market. Use it when the question "
        "is what the market believes; use candlesticks when the question is what "
        "a contract has cost."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "event_ticker": {"type": "string",
                             "description": "Fixture event, e.g. KXEPLGAME-26AUG23NEWLFC."},
            "hours_back": {"type": "number", "default": 2.0},
            "hourly": {"type": "boolean", "default": False},
            "method": {"type": "string", "enum": ["proportional", "power"],
                       "default": "proportional",
                       "description": "How to remove the margin. Proportional "
                                      "assumes nothing; power shades longshots down."},
        },
        "required": ["event_ticker"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "now": {"type": "object", "description": "Outcome code to probability."},
            "overround": {"type": "number"},
            "path": {"type": "array"},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        event = input["event_ticker"]
        # Any state, so a finished fixture can still be read back.
        markets = [m for m in DISCOVERY.markets(event_ticker=event, status=None)
                   if m.yes_bid and m.yes_ask]
        if len(markets) < 2:
            return ToolOutput.failed(f"{event} has fewer than two quoted outcomes")

        interval = HOUR if input.get("hourly", False) else MINUTE
        start = hours_ago(float(input.get("hours_back", 2.0)))
        method = input.get("method", "proportional")

        # A shared clock, because de-vigging needs every outcome priced at the
        # same instant. Kalshi emits a bar only for periods that traded, so the
        # outcomes have holes in different places; a timestamp missing from any
        # leg is skipped rather than filled.
        series: dict[str, dict[str, float]] = {}
        for m in markets:
            code = m.ticker.rsplit("-", 1)[-1]
            series[code] = {c.ts.isoformat(): c.mid
                            for c in HISTORY.price_path(m.ticker, start, now(), interval)
                            if c.two_sided and c.mid is not None}

        codes = list(series)
        shared = sorted(set.intersection(*(set(s) for s in series.values()))
                        if series else [])
        path = []
        for ts in shared:
            raw = [series[c][ts] for c in codes]
            fair = devig(raw, method)
            path.append({"ts": ts, "overround": round(overround(raw), 4),
                         **{c: round(p, 4) for c, p in zip(codes, fair)}})

        mids = [m.yes_bid + (m.yes_ask - m.yes_bid) / 2 for m in markets]
        fair_now = devig(mids, method)
        codes_now = [m.ticker.rsplit("-", 1)[-1] for m in markets]

        # De-vig the mids, because that is the market's own centre of opinion.
        # But quote the margin from the asks: the cost of actually covering
        # every outcome is what the exchange charges, and mids sum to slightly
        # *under* one, which would read as a free lunch rather than a fee.
        cover = overround([m.yes_ask for m in markets])
        said = ", ".join(f"{c} {p:.1%}" for c, p in zip(codes_now, fair_now))
        return ToolOutput(
            response=(f"{event} de-vigged ({method}): {said}. Buying every "
                      f"outcome would cost {(cover + 1):.3f}, so the margin is "
                      f"{cover * 100:+.1f}c before fees. {len(path)} shared bars."),
            raw_output={"now": dict(zip(codes_now, [round(p, 4) for p in fair_now])),
                        "overround": round(cover, 4),
                        "mid_sum": round(sum(mids), 4), "path": path},
        )
