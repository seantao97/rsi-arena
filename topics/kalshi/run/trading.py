"""Turning a five-minute price forecast into an action.

**The model predicts; the code decides.** The agent returns a *change* and a
quote width; :func:`decide` turns those into an action against the live book and
the fee schedule. Nothing is left to the model that arithmetic can settle, which
removed an entire class of defect seen live — a position that contradicted the
edge the same output reported.

This is the half of the horizon harness that cannot be a JSON config. The other
half — the prompt, the plan, the tool list — is
``agents/kalshi-horizon-5m.json``, and the reasoning behind its shape is in
``agents/README.md``.

Asking the model for a change rather than a price level is not a detail. Two
failure modes measured over 144 live forecasts both come from asking for a level:

* 63% of forecasts returned the current mid *exactly*. Copying the visible number
  is the path of least resistance when a level is what is wanted, and it scores
  zero by construction. Asked for a change, doing nothing costs the model a
  deliberate ``0``.
* Both trades that run produced came from misreading the book. One explained a
  price "already fading back to 0.105" while the market was at 0.295. Because the
  anchor was the model's own reading, a misreading manufactured an edge out of
  nothing — and the further off it was, the larger the fake edge, so the fee
  threshold selected for exactly these. A change is applied by code to the true
  mid, so the anchor can no longer be wrong.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from .. import maker_fee, taker_fee

#: The horizon the forecast is made over, and scored against.
HORIZON_MINUTES = 5

@dataclass(frozen=True)
class Holding:
    """An open position. Nothing closes it but a decision, or settlement."""

    side: str                      # YES | NO
    price: float                   # what a contract cost
    contracts: float
    fee_paid: float                # entry fee, in dollars, already sunk
    opened_at: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Holding | None":
        return cls(**data) if data else None

    def value_at(self, yes_price: float) -> float:
        """What the position is worth per contract at a given yes price."""
        return yes_price if self.side == "YES" else 1 - yes_price


@dataclass(frozen=True)
class Decision:
    """What the arithmetic says to do, given the prediction and the book.

    Opening and closing are both decisions. Nothing is marked out
    automatically: a position that is never closed is held to settlement and
    pays what the contract pays, which is the honest treatment and usually the
    expensive one.
    """

    action: str                    # OPEN_YES | OPEN_NO | CLOSE | HOLD | PASS
    edge: float                    # per contract, net of fees
    entry_price: float
    size_usd: float
    reason: str
    resting: bool = False          # posted inside the spread rather than taking

    def to_dict(self) -> dict:
        return asdict(self)


def quote_from(mid: float, delta_cents: float,
               half_width_cents: float) -> tuple[float, float, float]:
    """Turn the model's change and width into a price and a two-sided quote.

    Anchored on the exchange's mid, not on anything the model read off. Clamped
    into (0, 1) because a contract cannot be worth less than nothing or more
    than a dollar, however far the stated move goes.
    """
    predicted = min(0.99, max(0.01, mid + delta_cents / 100))
    width = max(0.0, half_width_cents) / 100
    return predicted, max(0.0, predicted - width), min(1.0, predicted + width)


def decide(predicted_mid: float, bid: float | None, ask: float | None,
           confidence: float = 0.5, bankroll: float = 50_000.0,
           min_edge: float = 0.01, max_fraction: float = 0.02,
           interval: list | tuple | None = None,
           allow_maker: bool = True,
           holding: Holding | None = None) -> Decision:
    """Decide what to do, given the prediction, the book, and what is held.

    Two questions, in order. If something is open, the only question is whether
    to close it — no averaging in, no reversing in one step. If nothing is open,
    the question is whether to open.

    The prediction is a two-sided quote of the agent's own: ``interval`` is
    where it thinks the price will be, and a trade is only justified when the
    exchange's quote sits outside it. The edge is therefore measured from the
    **near edge** of the interval, not its middle, so a wide interval stops
    producing trades on its own and no confidence threshold is needed on top.

    Nothing is closed automatically. A position the agent never decides to exit
    is carried to settlement and pays what the contract pays.
    """
    if bid is None or ask is None or not 0 < bid <= ask < 1:
        return Decision("HOLD" if holding else "PASS", 0.0, 0.0, 0.0,
                        "no two-sided market")

    low = high = predicted_mid
    if isinstance(interval, (list, tuple)) and len(interval) == 2:
        try:
            low, high = sorted(float(x) for x in interval)
        except (TypeError, ValueError):
            low = high = predicted_mid
        # An interval that excludes its own point estimate is incoherent; the
        # point estimate is what the model was actually asked for.
        if not low <= predicted_mid <= high:
            low = high = predicted_mid

    half_spread = (ask - bid) / 2
    if holding is not None:
        return _exit(holding, predicted_mid, bid, ask, half_spread, min_edge)

    # A round trip, not a leg. Buying yes costs the ask and a fee; getting out
    # means selling at the *bid* five minutes later and paying another fee. The
    # mid is not a price anyone trades at, so an edge measured to the mid is
    # short by half a spread on the way in and half a spread on the way out.
    yes_edge = _exit_proceeds(low - half_spread) - (ask + taker_fee(ask))
    no_edge = (_exit_proceeds(1 - high - half_spread)
               - ((1 - bid) + taker_fee(1 - bid)))

    if yes_edge >= no_edge and yes_edge > min_edge:
        action, edge, entry, resting = "OPEN_YES", yes_edge, ask, False
    elif no_edge > min_edge:
        action, edge, entry, resting = "OPEN_NO", no_edge, 1 - bid, False
    elif allow_maker:
        return _make(predicted_mid, low, high, bid, ask, confidence,
                     bankroll, min_edge, max_fraction)
    else:
        best = max(yes_edge, no_edge)
        return Decision("PASS", round(best, 4), 0.0, 0.0,
                        f"best edge {best:+.4f} does not clear {min_edge:.0%}")

    fraction = max_fraction * max(0.0, min(1.0, confidence))
    return Decision(action, round(edge, 4), entry,
                    round(bankroll * fraction, 2),
                    f"{action} at {entry:.2f}, edge {edge:+.4f} after fees",
                    resting)


def _exit_proceeds(price: float) -> float:
    """What selling at ``price`` actually nets, after the taker fee."""
    price = min(0.99, max(0.01, price))
    return price - taker_fee(price)


def _exit(holding: Holding, predicted_mid: float, bid: float, ask: float,
          half_spread: float, min_edge: float) -> Decision:
    """Close, or carry on holding.

    Closing is a taker order — a resting exit may never fill, and a position
    that cannot be got out of is not a position that was ever really closed.

    Both sides of the comparison are prices something can actually be sold at.
    Getting out now nets the bid less a fee; holding means getting out later at
    a bid that is half a spread below wherever the mid lands, less a fee then.
    Comparing today's executable price against tomorrow's *mid* would make
    holding look better than it is by half a spread, every time.
    """
    if holding.side == "YES":
        proceeds = _exit_proceeds(bid)
        expected = _exit_proceeds(predicted_mid - half_spread)
    else:
        proceeds = _exit_proceeds(1 - ask)
        expected = _exit_proceeds(1 - predicted_mid - half_spread)

    gain = proceeds - expected
    if gain > min_edge:
        return Decision("CLOSE", round(gain, 4), round(proceeds, 4),
                        round(holding.contracts * proceeds, 2),
                        f"close {holding.side} at {proceeds:.3f} net; holding "
                        f"is only worth {expected:.3f} in five minutes")
    return Decision("HOLD", round(gain, 4), holding.price, 0.0,
                    f"hold {holding.side} from {holding.price:.3f}; exiting "
                    f"nets {proceeds:.3f} against an expected {expected:.3f}")


def _make(predicted: float, low: float, high: float, bid: float, ask: float,
          confidence: float, bankroll: float, min_edge: float,
          max_fraction: float) -> Decision:
    """Post inside the spread instead of crossing it.

    Taking is expensive on these books: the spread is one to three cents and the
    taker fee another one to two, so a taker has to disagree with the market by
    two or three cents before anything clears. Measured over 146 live windows,
    only 4% did — and loosening the threshold did not help, because the binding
    constraint was never the threshold.

    A resting order changes the arithmetic. It does not cross the spread, and
    Kalshi's maker fee is about a quarter of the taker fee. Quoting one cent
    inside the market at a two-cent half width earns the difference rather than
    paying it.

    What it buys instead is adverse selection: a resting bid fills precisely
    when the market is coming to meet it, which is when the forecast is wrong.
    That is a real cost and the scoring has to see it — a fill is only counted
    when the price actually traded through, so being filled on the way down
    shows up as the loss it is.
    """
    # Improve the market rather than join it, and never quote through the far
    # side — a bid above the ask is a taker order wearing a maker's clothes.
    post_bid = min(max(low, bid + 0.01), ask - 0.01)
    post_ask = max(min(high, ask - 0.01), bid + 0.01)

    # Measured from the near edge of the interval, exactly as the taker path
    # does. Using the point estimate here let a wide interval sail through the
    # maker branch after failing the taker one — which defeated the property
    # the interval exists for: a model that does not know says so by widening,
    # and stops producing trades on its own.
    # Resting saves the spread and most of the fee on the way in. It saves
    # nothing on the way out: the exit is still a taker order at the bid.
    half_spread = (ask - bid) / 2
    yes_edge = _exit_proceeds(low - half_spread) - (post_bid + maker_fee(post_bid))
    no_edge = (_exit_proceeds(1 - high - half_spread)
               - ((1 - post_ask) + maker_fee(1 - post_ask)))

    if yes_edge >= no_edge and yes_edge > min_edge and bid < post_bid < ask:
        action, edge, entry = "OPEN_YES", yes_edge, post_bid
    elif no_edge > min_edge and bid < post_ask < ask:
        action, edge, entry = "OPEN_NO", no_edge, 1 - post_ask
    else:
        best = max(yes_edge, no_edge)
        return Decision("PASS", round(best, 4), 0.0, 0.0,
                        f"no restable quote inside {bid:.2f}/{ask:.2f} "
                        f"clears {min_edge:.0%} (best {best:+.4f})")

    fraction = max_fraction * max(0.0, min(1.0, confidence))
    return Decision(action, round(edge, 4), entry,
                    round(bankroll * fraction, 2),
                    f"{action} resting at {entry:.2f} inside {bid:.2f}/{ask:.2f}, "
                    f"edge {edge:+.4f} after maker fees")


def target_time(minutes: int = HORIZON_MINUTES) -> str:
    """The instant a prediction made now should be scored against."""
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
