"""Fees, breakeven and sizing — the arithmetic between a probability and a trade.

Every trading topic in the arena requires a memo to state its edge after costs,
and on Kalshi costs are not a rounding error. The taker fee peaks at 1.75c per
contract at 50c and falls toward zero in the tails, so a 2c edge at midprice is
nothing and the same edge at 90c is real.

Formula, per contract::

    taker = ceil(0.07 * P * (1 - P) * 100) / 100      capped at $0.035
    maker = taker / 4                                  approximately

Settlement is free: a contract held to expiry pays one fee, on entry.

Nothing here calls the API — these are pure functions, so they are cheap to test
and safe to use inside a loop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

FEE_RATE = 0.07
FEE_CAP = 0.035
MAKER_MULTIPLIER = 0.25


def taker_fee(price: float, contracts: float = 1.0) -> float:
    """Taker fee in dollars. ``price`` is the yes price in dollars, 0..1."""
    price = min(max(price, 0.0), 1.0)
    per = min(math.ceil(FEE_RATE * price * (1 - price) * 100) / 100, FEE_CAP)
    return per * contracts


def maker_fee(price: float, contracts: float = 1.0) -> float:
    """Maker fee — roughly a quarter of taker, and the reason to rest orders."""
    return taker_fee(price, contracts) * MAKER_MULTIPLIER


def fee(price: float, contracts: float = 1.0, maker: bool = False) -> float:
    return maker_fee(price, contracts) if maker else taker_fee(price, contracts)


def breakeven(price: float, maker: bool = False, round_trip: bool = False) -> float:
    """Probability at which a position stops paying.

    Buying yes at 0.68 needs the event to be ~70% likely just to clear the fee.
    ``round_trip`` charges a second fee for exiting before settlement; holding
    to expiry does not.
    """
    per = fee(price, 1.0, maker)
    return price + per * (2 if round_trip else 1)


def edge(probability: float, price: float, maker: bool = False,
         round_trip: bool = False) -> float:
    """Expected value per contract in dollars, after fees.

    Positive means the price is below what the probability justifies.
    """
    return probability - breakeven(price, maker, round_trip)


def kelly(probability: float, price: float, fraction: float = 0.25,
          maker: bool = False) -> float:
    """Fractional Kelly stake as a share of bankroll.

    A binary contract bought at ``price`` wins ``1 - price`` and loses
    ``price``. Full Kelly on an uncertain model is a way to go broke correctly,
    so the default is a quarter.
    """
    cost = breakeven(price, maker)
    if not 0 < cost < 1:
        return 0.0
    win, lose = 1 - cost, cost
    full = (probability * win - (1 - probability) * lose) / win
    return max(0.0, full * fraction)


def clv(entry_price: float, closing_price: float, side: str = "yes") -> float:
    """Closing-line value in dollars per contract.

    Positive means the market moved toward the position after it was taken.
    The lowest-variance skill signal available — a few hundred trades give a
    usable read where P&L needs thousands.
    """
    move = closing_price - entry_price
    return move if side == "yes" else -move


@dataclass(frozen=True)
class Trade:
    """A hypothetical position, priced end to end."""

    ticker: str
    side: str                 # "yes" | "no"
    price: float
    contracts: float
    maker: bool = False

    @property
    def cost(self) -> float:
        return self.price * self.contracts + fee(self.price, self.contracts, self.maker)

    @property
    def max_loss(self) -> float:
        return self.cost

    @property
    def max_win(self) -> float:
        return (1 - self.price) * self.contracts - fee(self.price, self.contracts, self.maker)

    def pnl(self, settled_yes: bool) -> float:
        won = settled_yes if self.side == "yes" else not settled_yes
        return self.max_win if won else -self.max_loss
