"""How long this contract has left, and what usually happens in it."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from rsi_arena.agent.tools import Tool, ToolOutput

from ._clients import DISCOVERY, HISTORY, QUOTES

#: What stops a market from being decidable, by the series it trades under.
#: Not every clock runs to the final whistle.
_FREEZES = {
    "1H": "the half-time whistle, not full time",
    "1HSPREAD": "the half-time whistle, not full time",
    "1HTOTAL": "the half-time whistle, not full time",
    "1HBTTS": "the half-time whistle, not full time",
    "1HSCORE": "the half-time whistle, not full time",
    "CORNERS": "the final whistle, and the count freezes during stoppages",
    "TCORNERS": "the final whistle, and the count freezes during stoppages",
}


class SettlementCountdownTool(Tool):
    name = "settlement_countdown"
    version = 1
    description = (
        "How long a contract has before it closes, and what actually stops it "
        "from being decidable.\n\n"
        "The two are not the same, and the difference has cost money. A "
        "first-half market is settled at the half-time whistle while the match "
        "runs on for another hour; a corners market stops accruing when play "
        "stops. Trading either as though it had until full time is a bet on a "
        "clock that has already run out.\n\n"
        "Reports the close time, the time remaining, and how much the price has "
        "moved in the last stretch, so a quiet market can be told from one "
        "still resolving."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"ticker": {"type": "string"}},
        "required": ["ticker"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "close_time": {"type": ["string", "null"]},
            "minutes_left": {"type": ["number", "null"]},
            "settled": {"type": "boolean"},
            "result": {"type": ["string", "null"]},
            "freezes_on": {"type": ["string", "null"],
                           "description": "What decides it, when that is not "
                                          "the final whistle."},
            "mid": {"type": ["number", "null"]},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        ticker = input["ticker"]
        try:
            result = HISTORY.settlement(ticker)
            quote = QUOTES.get_market(ticker)
        except Exception as exc:
            return ToolOutput.failed(f"{ticker}: {type(exc).__name__}: {exc}")

        # The market type sits between the league stem and the fixture.
        series = ticker.split("-")[0]
        freeze = next((note for suffix, note in _FREEZES.items()
                       if series.endswith(suffix)), None)

        close_raw, minutes = None, None
        for ref in DISCOVERY.markets(event_ticker=ticker.rsplit("-", 1)[0]):
            if ref.ticker == ticker and ref.close_time:
                close_raw = ref.close_time
                try:
                    close = datetime.fromisoformat(close_raw.replace("Z", "+00:00"))
                    minutes = round(
                        (close - datetime.now(timezone.utc)).total_seconds() / 60, 1)
                except ValueError:
                    minutes = None
                break

        if result is not None:
            said = f"{ticker} has settled {result.upper()}. Nothing left to trade."
        elif minutes is None:
            said = f"{ticker} is open; the exchange publishes no close time."
        elif minutes <= 0:
            said = (f"{ticker} closed {abs(minutes):.0f} minutes ago and has not "
                    f"been settled yet.")
        else:
            said = f"{ticker} closes in {minutes:.0f} minutes."
        if freeze:
            said += f" It is decided by {freeze}."
        # A closed book quotes 0.00/1.00, whose midpoint is a fictional 0.50.
        # Reporting it as a price is how a settled market comes to look like a
        # coin flip.
        two_sided = quote.yes_bid not in (None, 0.0) and quote.yes_ask not in (None, 1.0)
        if quote.mid is not None and two_sided:
            said += f" Currently {quote.mid:.3f}."
        elif result is None:
            said += " No two-sided quote right now."

        return ToolOutput(
            response=said,
            raw_output={"ticker": ticker, "close_time": close_raw,
                        "minutes_left": minutes, "settled": result is not None,
                        "result": result, "freezes_on": freeze,
                        "mid": quote.mid if two_sided else None})
