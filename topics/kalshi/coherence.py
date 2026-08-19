"""No-arbitrage checks across related markets.

A coherence violation is a finding that needs no forecast: if "Over 6.5 runs"
can be bought for less than "Over 7.5 runs" can be sold, the first strictly
contains the second and the difference is locked in whatever the game does.

Everything here prices against **executable** quotes — you buy at the ask and
sell at the bid — and nets fees off before calling anything a violation. Using
mids instead manufactures edge that cannot be traded, which is the usual way
these checks lie.

Three structures, all present on Kalshi sports:

* **Complement.** Two markets partition one outcome (SF wins / CLE wins).
  Their yes prices must sum to 1.
* **Partition.** An event flagged ``mutually_exclusive`` whose markets are
  exhaustive. Yes prices must sum to 1.
* **Ladder.** ``strike_type == "greater"`` markets over rising strikes, where
  yes price must fall monotonically and be convex across equal steps.

One event can hold **several independent ladders**. A spread event carries a
full strike ladder per team — SF over 1.5/2.5/3.5 and CLE over 1.5/2.5/3.5 —
and comparing across them is meaningless: both teams having a 1.5 rung is not
an inconsistency. Rungs are grouped by subject, taken from the ticker suffix
with its trailing strike index stripped, before anything is compared.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .client import KalshiClient
from .fees import taker_fee


@dataclass(frozen=True)
class Violation:
    """A priced inconsistency, net of fees."""

    kind: str                      # "monotonicity" | "convexity" | "partition" | "complement"
    tickers: list[str]
    detail: str
    gross: float                   # dollars per contract before fees
    net: float                     # after taker fees on every leg
    size: float                    # contracts executable at the quoted sizes

    @property
    def tradeable(self) -> bool:
        return self.net > 0 and self.size > 0

    @property
    def value(self) -> float:
        return self.net * self.size


def _f(v) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _subject(m: dict) -> str:
    """Which underlying a rung refers to.

    ``...-SF4`` and ``...-SF2`` are two rungs of one San Francisco ladder;
    ``...-CLE2`` belongs to a different one. The trailing digits are the strike
    index, so stripping them leaves the subject.
    """
    tail = m.get("ticker", "").rsplit("-", 1)[-1]
    return re.sub(r"\d+$", "", tail) or tail


@dataclass
class _Leg:
    ticker: str
    bid: float | None
    ask: float | None
    bid_size: float
    ask_size: float
    strike: float | None
    strike_type: str
    subject: str              # which underlying this rung is about

    @classmethod
    def of(cls, m: dict) -> "_Leg":
        return cls(
            ticker=m.get("ticker", ""),
            bid=_f(m.get("yes_bid_dollars")), ask=_f(m.get("yes_ask_dollars")),
            bid_size=float(m.get("yes_bid_size_fp") or 0),
            ask_size=float(m.get("yes_ask_size_fp") or 0),
            strike=_f(m.get("floor_strike")) if m.get("floor_strike") is not None else None,
            strike_type=m.get("strike_type", ""),
            subject=_subject(m),
        )


class Coherence:
    """Run no-arb checks over the markets of an event."""

    def __init__(self, client: KalshiClient | None = None,
                 min_net: float = 0.005) -> None:
        self.client = client or KalshiClient()
        self.min_net = min_net      # ignore sub-half-cent "edges"; they are noise

    # ---------- entry points ----------

    def check_event(self, event_ticker: str) -> list[Violation]:
        """Every applicable check for one event."""
        payload = self.client.get(f"/events/{event_ticker}")
        event = payload.get("event") or payload
        markets = list(self.client.paginate(
            "/markets", "markets", {"event_ticker": event_ticker, "status": "open"}))
        legs = [_Leg.of(m) for m in markets]

        out: list[Violation] = []
        if event.get("mutually_exclusive") and len(legs) > 2:
            out += self.partition(legs)
        elif len(legs) == 2:
            out += self.complement(legs)
        out += self.ladder(legs)
        return [v for v in out if v.tradeable and v.net >= self.min_net]

    def check_series(self, series_ticker: str,
                     max_events: int | None = None) -> dict[str, list[Violation]]:
        """Sweep every open event in a series. Returns only events with findings."""
        found: dict[str, list[Violation]] = {}
        for e in self.client.paginate("/events", "events",
                                      {"series_ticker": series_ticker, "status": "open"},
                                      max_items=max_events):
            hits = self.check_event(e["event_ticker"])
            if hits:
                found[e["event_ticker"]] = hits
        return found

    # ---------- individual checks ----------

    def complement(self, legs: list[_Leg]) -> list[Violation]:
        """Two markets partitioning one outcome must price to 1."""
        if len(legs) != 2:
            return []
        return self._sum_to_one(legs, "complement")

    def partition(self, legs: list[_Leg]) -> list[Violation]:
        """A mutually exclusive, exhaustive set must price to 1."""
        return self._sum_to_one(legs, "partition")

    def _sum_to_one(self, legs: list[_Leg], kind: str) -> list[Violation]:
        out: list[Violation] = []
        asks = [l for l in legs if l.ask is not None]
        bids = [l for l in legs if l.bid is not None]

        if len(asks) == len(legs):
            total = sum(l.ask for l in asks)
            gross = 1.0 - total
            if gross > 0:
                cost = sum(taker_fee(l.ask) for l in asks)
                out.append(Violation(
                    kind, [l.ticker for l in asks],
                    f"buying every leg costs {total:.4f} and always returns 1.00",
                    gross, gross - cost, min(l.ask_size for l in asks)))

        if len(bids) == len(legs):
            total = sum(l.bid for l in bids)
            gross = total - 1.0
            if gross > 0:
                cost = sum(taker_fee(l.bid) for l in bids)
                out.append(Violation(
                    kind, [l.ticker for l in bids],
                    f"selling every leg collects {total:.4f} against a 1.00 liability",
                    gross, gross - cost, min(l.bid_size for l in bids)))
        return out

    def ladder(self, legs: list[_Leg]) -> list[Violation]:
        """Monotonicity and convexity, per independent ladder in the event."""
        groups: dict[str, list[_Leg]] = {}
        for leg in legs:
            if leg.strike is not None and leg.strike_type == "greater":
                groups.setdefault(leg.subject, []).append(leg)

        out: list[Violation] = []
        for rungs in groups.values():
            out += self._one_ladder(sorted(rungs, key=lambda l: l.strike))
        return out

    def _one_ladder(self, rungs: list[_Leg]) -> list[Violation]:
        if len(rungs) < 2:
            return []
        out: list[Violation] = []
        # P(over k) must not exceed P(over j) for k > j: buy the lower strike,
        # sell the higher one, and the difference is locked.
        for low, high in zip(rungs, rungs[1:]):
            if low.ask is None or high.bid is None:
                continue
            gross = high.bid - low.ask
            if gross > 0:
                net = gross - taker_fee(low.ask) - taker_fee(high.bid)
                out.append(Violation(
                    "monotonicity", [low.ticker, high.ticker],
                    f"over {high.strike} bids {high.bid:.2f} above over "
                    f"{low.strike} at {low.ask:.2f}, though it is strictly rarer",
                    gross, net, min(low.ask_size, high.bid_size)))

        # Convexity: equally spaced strikes must satisfy p(a) - 2p(b) + p(c) >= 0.
        for a, b, c in zip(rungs, rungs[1:], rungs[2:]):
            if abs((b.strike - a.strike) - (c.strike - b.strike)) > 1e-9:
                continue
            if a.ask is None or b.bid is None or c.ask is None:
                continue
            gross = 2 * b.bid - a.ask - c.ask
            if gross > 0:
                net = gross - taker_fee(a.ask) - 2 * taker_fee(b.bid) - taker_fee(c.ask)
                out.append(Violation(
                    "convexity", [a.ticker, b.ticker, c.ticker],
                    f"butterfly {a.strike}/{b.strike}/{c.strike} pays "
                    f"{gross:.4f} up front and never costs more",
                    gross, net,
                    min(a.ask_size, b.bid_size / 2, c.ask_size)))
        return out
