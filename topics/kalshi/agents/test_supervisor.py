"""Checks the supervisor is importable and its book-keeping adds up.

The first of these exists because a misplaced import shipped to main. Every
test at the time covered horizon.py and the benchmark; none of them imported
the supervisor, so a file that would not parse passed the suite and was caught
only when the next run failed to start.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..linking import fixture_key
from .horizon import Holding
from .supervisor import Supervisor


def _sup(**kw) -> Supervisor:
    return Supervisor(["SERIEA"], mode="horizon", discover=True,
                      state_dir="/tmp/kalshi-test-sup", **kw)


def test_the_supervisor_imports_and_builds() -> None:
    sup = _sup(max_per_game=3)
    assert sup.max_per_game == 3
    assert sup.leagues == ["SERIEA"]


def test_slots_are_capped_per_match_not_per_market_type() -> None:
    """One fixture is listed under a series per kind of bet.

    Keying the cap on the event ticker let a single match take three slots per
    market type — twelve contracts on one game, while three other leagues were
    an hour from kicking off with nowhere to land.
    """
    same_match = [
        "KXSERIEAGAME-26AUG24BFCLAZ-BFC",
        "KXSERIEASPREAD-26AUG24BFCLAZ-LAZ2",
        "KXSERIEA1H-26AUG24BFCLAZ-TIE",
        "KXSERIEATEAMTOTAL-26AUG24BFCLAZ-BFC2",
    ]
    assert len({fixture_key(t) for t in same_match}) == 1
    # A different fixture in the same league is a different key.
    assert fixture_key("KXSERIEAGAME-26AUG24INTMIL-INT") != fixture_key(same_match[0])
    # And something that is not a fixture market still gets a usable key.
    assert fixture_key("KXNFLWINS-KC") == "KXNFLWINS"


def test_a_resting_order_needs_a_print_and_expires() -> None:
    """No prints in the window means no fill, whatever the quotes did."""
    sup = _sup()
    stale = {"side": "YES", "price": 0.095, "contracts": 100,
             "placed_at": (datetime.now(timezone.utc)
                           - timedelta(days=2)).isoformat()}
    # Placed two days ago: the order is long gone, and any print since then
    # says nothing about whether it was taken while it stood.
    assert sup._resting_filled("KXEPLTEAMTOTAL-26AUG23NEWLFC-LFC3", stale) is False


def test_a_holding_is_worth_its_side() -> None:
    held = Holding(side="NO", price=0.75, contracts=100, fee_paid=1.0,
                   opened_at="t")
    assert held.value_at(0.24) == 0.76
    assert Holding("YES", 0.4, 10, 0.1, "t").value_at(0.24) == 0.24
