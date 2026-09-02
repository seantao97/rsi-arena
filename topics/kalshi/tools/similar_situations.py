"""What happened last time the board looked like this."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from rsi_arena.agent.tools import Tool, ToolOutput

from ._history import MINUTE
from ._linking import fixture_key
from ._clients import CLIENT, HISTORY


class SimilarSituationsTool(Tool):
    name = "similar_situations"
    version = 1
    description = (
        "Settled contracts of the same kind that were priced near this one at "
        "the same stage, and what each did next.\n\n"
        "Precedent, which nothing else here provides. A view formed from the "
        "board alone has no way to know whether a draw contract at 0.70 with "
        "ten minutes left usually finishes at one or gets broken — this "
        "answers that from the record instead of from intuition.\n\n"
        "Matches on series, on price at the same point before close, and "
        "reports the realised move and the settlement for each. Slow: it reads "
        "one price history per past fixture, so it takes several seconds and is "
        "worth calling once per contract, not once per tick. Fewer than a "
        "handful of precedents is not evidence, and it says how many it found."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "ticker": {"type": "string",
                       "description": "The contract you are pricing."},
            "league": {"type": "string"},
            "minutes_before_close": {"type": "number", "default": 15.0,
                                     "description": "The stage to compare at."},
            "price_tolerance": {"type": "number", "default": 0.10,
                                "description": "How near in price counts."},
            "max_fixtures": {"type": "integer", "default": 8,
                             "description": "Bounds the cost."},
        },
        "required": ["ticker", "league"],
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "reference_price": {"type": ["number", "null"]},
            "matches": {"type": "array", "items": {
                "type": "object",
                "properties": {"ticker": {"type": "string"},
                               "price_then": {"type": "number"},
                               "settled": {"type": ["string", "null"]},
                               "move": {"type": "number"}}}},
            "resolved_yes": {"type": "integer"},
            "resolved_no": {"type": "integer"},
            "examined": {"type": "integer"},
        },
    }

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        ticker = input["ticker"]
        series = ticker.split("-")[0]
        outcome = ticker.rsplit("-", 1)[-1]
        lead = float(input.get("minutes_before_close", 15.0))
        tolerance = float(input.get("price_tolerance", 0.10))
        cap = int(input.get("max_fixtures", 8))

        # Where this contract stands now, which is what precedent is sought for.
        try:
            live = HISTORY.quote_at(ticker, datetime.now(timezone.utc), MINUTE)
        except Exception as exc:
            return ToolOutput.failed(f"{ticker}: {type(exc).__name__}: {exc}")
        if live is None or not live.two_sided or live.mid is None:
            return ToolOutput.failed(f"no two-sided quote on {ticker} to compare")
        reference = live.mid

        # Past fixtures under the same series. Two wrong sets to avoid here:
        # whats_bettable lists only *open* markets, and an unfiltered event
        # listing answers with the fixtures still to come. A precedent has to
        # have settled, so that is what is asked for.
        here = fixture_key(ticker)
        try:
            events = list(CLIENT.paginate("/events", "events",
                                          {"series_ticker": series,
                                           "status": "settled"},
                                          max_items=cap * 6))
        except Exception as exc:
            return ToolOutput.failed(f"could not list settled {series}: {exc}")
        candidates = [f"{e['event_ticker']}-{outcome}" for e in events
                      if fixture_key(e.get("event_ticker", "")) != here]

        matches, examined = [], 0
        for other in candidates:
            if len(matches) >= cap or examined >= cap * 3:
                break
            examined += 1
            try:
                result = HISTORY.settlement(other)
                if result is None:
                    continue                      # not decided yet, so no lesson
                close = HISTORY.closing_quote(other)
                if close is None or not close.two_sided or close.mid is None:
                    continue
                then = HISTORY.quote_at(
                    other, close.ts - timedelta(minutes=lead), MINUTE)
            except Exception:
                continue
            if then is None or not then.two_sided or then.mid is None:
                continue
            if abs(then.mid - reference) > tolerance:
                continue
            matches.append({"ticker": other, "price_then": round(then.mid, 4),
                            "settled": result,
                            "move": round(close.mid - then.mid, 4)})

        if not matches:
            return ToolOutput(
                response=(f"No settled {series} {outcome} contract was priced "
                          f"within {tolerance:.2f} of {reference:.3f} at "
                          f"{lead:.0f} minutes out. {examined} examined — too "
                          f"few precedents to lean on."),
                raw_output={"reference_price": reference, "matches": [],
                            "resolved_yes": 0, "resolved_no": 0,
                            "examined": examined})

        yes = sum(1 for m in matches if m["settled"] == "yes")
        drift = sum(m["move"] for m in matches) / len(matches)
        return ToolOutput(
            response=(f"{len(matches)} settled {series} {outcome} contracts sat "
                      f"near {reference:.3f} at {lead:.0f} minutes out: "
                      f"{yes} resolved yes, {len(matches) - yes} no. They moved "
                      f"{drift * 100:+.1f}c on average over that stretch. "
                      + ("Too few to be evidence." if len(matches) < 4 else
                         "Treat as a prior, not a forecast.")),
            raw_output={"reference_price": reference, "matches": matches,
                        "resolved_yes": yes, "resolved_no": len(matches) - yes,
                        "examined": examined})
