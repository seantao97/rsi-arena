"""Checks that run without the network, on the parts that broke in production.

Each of these exists because it went wrong live, in a way that reading the diff
did not catch.
"""

from __future__ import annotations

from .horizon import Holding, decide, horizon_agent, horizon_tools, quote_from


def test_agent_builds_with_no_toolbox_supplied() -> None:
    """The supervisor passes a toolbox; nothing else does.

    A refactor deleted horizon_tools() and the supervisor never noticed, because
    ``tools or horizon_tools()`` short-circuits when a toolbox is given. It
    reached main and broke every other caller — the preflight check, a bare
    script, anything constructing the agent on its own.
    """
    agent = horizon_agent()
    assert [step.name for step in agent.plan.steps] == \
        ["quote", "path", "tape", "predict"]
    assert {tool.name for tool in horizon_tools()} == \
        {"market_quote", "candlesticks", "previous_trades"}


def test_anchor_is_the_exchange_mid_not_the_models_reading() -> None:
    """A change applied to the true mid, so a misread book cannot invent edge."""
    predicted, low, high = quote_from(0.295, -19, 3)
    # Compared with a tolerance: these are cents held as floats, so 0.295 - 0.19
    # lands on 0.10499999999999998 and an equality check would fail on
    # arithmetic rather than on anything the code got wrong.
    assert abs(predicted - 0.105) < 1e-9
    assert abs(low - 0.075) < 1e-9 and abs(high - 0.135) < 1e-9
    # Clamped inside (0, 1) however far the stated move goes.
    assert abs(quote_from(0.05, -30, 2)[0] - 0.01) < 1e-9


def test_widening_the_interval_stops_the_trade() -> None:
    """Same point estimate, less conviction, no position — on both paths.

    The maker branch once measured edge from the point estimate while the taker
    branch used the near edge, so a wide interval failed one test and sailed
    through the other.
    """
    tight = decide(0.30, 0.20, 0.22, 0.8, interval=[0.28, 0.32])
    wide = decide(0.30, 0.20, 0.22, 0.8, interval=[0.10, 0.50])
    assert tight.action == "OPEN_YES"
    assert wide.action == "PASS"
    assert wide.edge < tight.edge


def test_a_held_position_is_only_ever_closed_or_kept() -> None:
    """No averaging in, no reversing in one step."""
    held = Holding(side="NO", price=0.75, contracts=867, fee_paid=4.3,
                   opened_at="t")
    for predicted, bid, ask in [(0.22, 0.24, 0.26), (0.30, 0.24, 0.26),
                                (0.09, 0.10, 0.12), (0.60, 0.60, 0.62)]:
        action = decide(predicted, bid, ask, 0.7,
                        interval=[predicted - 0.02, predicted + 0.02],
                        holding=held).action
        assert action in ("CLOSE", "HOLD"), action


def test_no_book_means_no_decision() -> None:
    assert decide(0.5, None, None).action == "PASS"
    held = Holding(side="YES", price=0.4, contracts=10, fee_paid=0.1,
                   opened_at="t")
    assert decide(0.5, None, None, holding=held).action == "HOLD"
