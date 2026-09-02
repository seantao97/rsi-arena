"""``topics.kalshi.run.supervisor`` — that it loads, and that the book adds up.

The first of these exists because a misplaced import shipped to main. Every
test at the time covered horizon.py and the benchmark; none of them imported
the supervisor, so a file that would not parse passed the suite and was caught
only when the next run failed to start.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .. import fixture_key
from .trading import Holding
from .supervisor import Supervisor


def _sup(**kw) -> Supervisor:
    return Supervisor(["SERIEA"], mode="horizon", discover=True,
                      state_dir="/tmp/kalshi-test-sup", **kw)


# --- it loads at all ----------------------------------------------------------


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


# --- the book -----------------------------------------------------------------


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


def test_a_second_division_is_not_the_top_flight() -> None:
    """A bare digit after the stem marks a tier; a digit before H marks a half.

    KXLALIGA2GAME is the Spanish second division. Reading it as La Liga swept
    36 second-tier markets into the top flight, and the reserve side listed as
    "Real Sociedad B" then claimed the RSO code.
    """
    from .. import match_competition
    assert match_competition("LALIGAGAME")[0] == "LALIGA"
    assert match_competition("LALIGA1HTOTAL")[0] == "LALIGA"
    assert match_competition("LALIGA2HSPREAD")[0] == "LALIGA"
    assert match_competition("SERIEA1HSPREAD")[0] == "SERIEA"
    for tier_two in ("LALIGA2GAME", "LALIGA2SPREAD", "LALIGA2TOTAL"):
        assert match_competition(tier_two) is None, tier_two


def test_the_shortest_name_wins_a_code() -> None:
    """A club is named several ways across a league's series; the bare name is
    the one the fixture feed prints."""
    from .. import names_from_markets

    class Ref:
        def __init__(self, ticker, subtitle):
            self.ticker, self.subtitle = ticker, subtitle

    got = names_from_markets([
        Ref("KXLALIGA2GAME-26AUG31BURRSO-RSO", "Real Sociedad B"),
        Ref("KXLALIGAFTTS-26AUG29RSOESP-RSO", "Real Sociedad San Sebastian"),
        Ref("KXLALIGAGAME-26SEP07ELCRSO-RSO", "Real Sociedad"),
    ])
    assert got["RSO"] == "Real Sociedad"


def test_a_name_matches_itself() -> None:
    """Every other rule reads the left side as an abbreviation.

    Passing the club name the exchange publishes fell through to a character
    sequence fallback and scored *lower* the longer the name: "Real Madrid"
    against "Real Madrid" came out at 0.455, below the 0.6 linking threshold.
    """
    from ..tools._linking import _score_match
    assert _score_match("Real Madrid", "Real Madrid") == 1.0
    assert _score_match("Real Sociedad", "Real Sociedad San Sebastian") >= 0.9
    assert _score_match("Newcastle", "Newcastle United") >= 0.9
    # And a code still beats a wrong name.
    assert _score_match("RMA", "Real Madrid") > _score_match("Real Madrid",
                                                             "Real Sociedad")
