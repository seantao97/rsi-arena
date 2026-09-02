"""Every way to bet on one fixture."""

from __future__ import annotations

from collections import Counter
from typing import Any

from rsi_arena import Tool, ToolOutput

from ._clients import DISCOVERY


class EventMarketsTool(Tool):
    name = "event_markets"
    version = 1
    description = (
        "All markets on a single fixture — every outcome, spread line, total "
        "and half.\n\n"
        "Worth calling before committing to the obvious contract. The "
        "match-winner book is usually a cent wide and untradeable, while "
        "corners, team totals and spreads on the same match can be ten cents "
        "wide, which is where a resting order can actually sit."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"event_ticker": {"type": "string"}},
        "required": ["event_ticker"],
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        event = input["event_ticker"]
        markets = list(DISCOVERY.markets(event_ticker=event))
        if not markets:
            return ToolOutput.failed(f"no markets on {event}")
        rows = [{"ticker": m.ticker, "subtitle": m.subtitle, "type": m.market_type,
                 "yes_bid": m.yes_bid, "yes_ask": m.yes_ask, "volume": m.volume}
                for m in markets]
        kinds = Counter(r["type"] for r in rows)
        widest = max((r for r in rows if r["yes_bid"] and r["yes_ask"]),
                     key=lambda r: r["yes_ask"] - r["yes_bid"], default=None)
        note = ("" if not widest else
                f" Widest book: {widest['ticker']} at "
                f"{widest['yes_bid']:.2f}/{widest['yes_ask']:.2f}.")
        return ToolOutput(
            response=(f"{event}: {len(rows)} markets — "
                      f"{', '.join(f'{k} x{n}' for k, n in kinds.most_common(5))}."
                      f"{note}"),
            raw_output={"markets": rows, "by_type": dict(kinds)})
