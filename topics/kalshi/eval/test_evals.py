"""``topics.kalshi.eval`` — the eval, and what it refuses to score.

Two questions — the fast one five minutes out, the slow one against how the
match ended — both answered by replay. Everything here is about the edges — a window nobody traded, an output that
is not a forecast, a run that never finished, and a market that did not move,
which looks like a tie and is really an absence of evidence.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from rsi_arena import Eval

from . import REGISTRY, HorizonWindow, SettlementOutcome, describe

TICKER = "KXEPLGAME-26AUG23NEWLFC-NEW"
AT = datetime(2026, 8, 23, 15, 30, tzinfo=timezone.utc)


class FakeRun:
    """Stands in for an AgentResult — the eval reads ``output`` and ``error``."""

    error = None
    error_kind = None

    def __init__(self, output):
        self.output = output


# --- the registry, the same shape the tools have -----------------------------


def test_every_eval_is_the_core_eval_not_a_kalshi_one() -> None:
    """No Kalshi type escapes into the framework, exactly as none escapes
    through Tool. An arena that can run one topic's evals can run any."""
    for cls in REGISTRY:
        assert issubclass(cls, Eval), cls


@pytest.mark.parametrize("cls", REGISTRY, ids=lambda c: c.name)
def test_every_eval_declares_itself(cls) -> None:
    """A reader — or a model — picks an eval from its name and description."""
    assert cls.name and cls.name.islower() and " " not in cls.name
    assert 200 <= len(cls.description) <= 1400, (cls.name, len(cls.description))
    assert cls.name in describe()


# --- what the eval is --------------------------------------------------------


def test_a_window_is_an_ordinary_eval() -> None:
    ev = HorizonWindow(TICKER, AT)
    assert isinstance(ev, Eval)
    assert ev.agent.name == "kalshi-horizon-5m"
    assert ev.input["question"] == TICKER
    assert TICKER in ev.description


def test_a_replayed_window_binds_the_frozen_tools() -> None:
    """The agent is rebound against tools that read history rather than the live
    book. It is the same config either way and cannot tell the difference —
    which is what makes replaying a harness possible at all."""
    ev = HorizonWindow(TICKER, AT)
    assert set(ev.agent.tools) == {"market_quote", "candlesticks", "previous_trades"}


def test_an_eval_supplies_every_input_its_agent_reads() -> None:
    """Eval.run() refuses a plan whose inputs are not covered, so this is the
    check that this eval holds up its end of that bargain."""
    ev = HorizonWindow(TICKER, AT)
    assert not ev.agent.plan.required_inputs() - set(ev.input)


def test_nothing_is_collected_to_score_a_window() -> None:
    """The whole reason this is the only eval: the answer is already in public
    candlestick history, so scoring needs no feed, no state and no scheduled
    job. A mid at the window is all it reads before the agent runs."""
    ev = HorizonWindow(TICKER, AT)
    assert ev.mid_now is not None and 0 < ev.mid_now < 1


# --- what it refuses to score ------------------------------------------------


def test_an_unusable_output_scores_zero_with_a_reason() -> None:
    out = HorizonWindow(TICKER, AT).grade(FakeRun({}))
    assert out.score == 0.0
    assert "nothing to score" in out.comments


def test_junk_output_is_scored_rather_than_crashing() -> None:
    """A model that returns a string where a dict was asked for is a bad
    forecast, not a broken eval."""
    out = HorizonWindow(TICKER, AT).grade(FakeRun("not a dict"))
    assert out.score == 0.0


def test_a_window_with_no_two_sided_quote_says_so() -> None:
    """A contract that never traded at that minute cannot be scored, and that is
    a fact about coverage rather than a failure of the harness."""
    ancient = datetime(2020, 1, 1, tzinfo=timezone.utc)
    out = HorizonWindow(TICKER, ancient).grade(FakeRun({"delta_cents": 0.0}))
    assert out.score == 0.0
    assert "two-sided" in out.comments


def test_an_unmoved_market_is_flagged_as_unmeasurable() -> None:
    """No-change has zero error there, so skill is undefined and the window
    scores a flat 0.5 whether the forecast was exactly right or badly wrong.
    Pooling those as ties drags a leaderboard toward the middle, so they carry
    a flag a caller can filter on. Measured on a real fixture, three windows in
    four were unmeasurable."""
    out = HorizonWindow(TICKER, AT).grade(
        FakeRun({"delta_cents": 1.5, "half_width_cents": 3.0}))
    if out.metadata.get("unmeasurable"):
        assert out.score == 0.5
        assert "did not move" in out.comments


def test_the_score_lands_in_the_unit_interval() -> None:
    """Reported as 0.5 + skill/2, so matching the benchmark is half a point and
    the leaderboard's arithmetic holds. The raw skill stays in metadata."""
    out = HorizonWindow(TICKER, AT).grade(
        FakeRun({"delta_cents": 0.0, "half_width_cents": 2.0}))
    assert 0.0 <= out.score <= 1.0


# --- the slow question, also answered by replay ------------------------------


def test_settlement_reads_the_outcome_off_the_settled_contract() -> None:
    """What makes the slow question replayable at all: Kalshi keeps the result
    on every finalised market, so the truth a forecast was about already exists
    the moment the match ends. No feed, no waiting."""
    ev = SettlementOutcome(TICKER, AT, "horizon")
    assert ev.outcome in (True, False)
    assert ev.market_mid is not None


def test_settlement_refuses_a_harness_that_does_not_answer_it() -> None:
    """The horizon harness returns a change in cents, not a probability. That
    is a mismatch between eval and harness, and it says so rather than reading
    a number that is not there."""
    out = SettlementOutcome(TICKER, AT, "horizon").grade(
        FakeRun({"delta_cents": 1.0, "half_width_cents": 2.0}))
    assert out.score == 0.0
    assert "no usable probability" in out.comments


def test_settlement_scores_a_probability_against_what_happened() -> None:
    ev = SettlementOutcome(TICKER, AT, "horizon")
    truth = 1.0 if ev.outcome else 0.0
    exact = ev.grade(FakeRun({"probability": truth}))
    assert exact.score >= 0.5, "being right cannot score below the benchmark"
    assert exact.ground_truth["settled"] is ev.outcome

    wrong = ev.grade(FakeRun({"probability": 1.0 - truth}))
    assert wrong.score < exact.score


def test_a_probability_outside_zero_to_one_is_not_a_probability() -> None:
    out = SettlementOutcome(TICKER, AT, "horizon").grade(FakeRun({"probability": 7}))
    assert out.score == 0.0 and "no usable probability" in out.comments


def test_a_replay_refuses_a_harness_whose_tools_cannot_be_frozen() -> None:
    """The constraint that shapes what a rewritten harness may use. A tool that
    reads the news or live game state cannot be frozen — a story filed after the
    instant would be answering with the future — so binding fails loudly rather
    than quietly leaking it."""
    with pytest.raises(KeyError) as exc:
        SettlementOutcome(TICKER, AT, "horizon",
                          spec={"name": "researcher", "context": "",
                                "tools": ["web_research"],
                                "plan": {"steps": []}})
    assert "web_research" in str(exc.value)


# --- the fixed benchmark ------------------------------------------------------


def test_the_benchmark_names_fixtures_that_still_resolve() -> None:
    """The set is fixed so two harnesses are comparable. A fixture that stops
    resolving silently shrinks the question set, which is the same as changing
    the exam between candidates."""
    import json
    from pathlib import Path

    from .run import BENCHMARK

    fixtures = json.loads(Path(BENCHMARK).read_text())
    assert len(fixtures) >= 3, "too few to pool"
    for f in fixtures:
        assert f["league"] and f["game"] and f["event"]
        assert f["tickers"], f["event"]
        for ticker in f["tickers"]:
            assert ticker.startswith(f["event"]), (ticker, f["event"])


def test_benchmark_fixtures_are_distinct_matches() -> None:
    """Two contracts on one match are correlated observations; two matches are
    independent ones. Pooling the first as though it were the second overstates
    how much evidence a number rests on."""
    import json
    from pathlib import Path

    from .run import BENCHMARK

    fixtures = json.loads(Path(BENCHMARK).read_text())
    assert len({f["game"] for f in fixtures}) == len(fixtures)
