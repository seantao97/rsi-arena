"""``topics.kalshi.eval`` — the Kalshi evals, as Eval subclasses.

Two questions, due at different times: is the forecast self-consistent (now),
and was it right (five minutes later). These check that each is asked of the
right thing and that neither can see what it should not.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from rsi_arena import Eval, EvalOutput

from . import (REGISTRY, HorizonSkill, HorizonWindow, SettlementBrier,
               describe)

TICKER = "KXEPLGAME-26AUG23NEWLFC-NEW"
AT = datetime(2026, 8, 23, 15, 30, tzinfo=timezone.utc)


class FakeRun:
    """Stands in for an AgentResult — the eval function only reads ``output``."""

    def __init__(self, output):
        self.output = output


# --- what an eval is ----------------------------------------------------------


def test_a_window_is_an_ordinary_eval() -> None:
    """Nothing here is a Kalshi subclass. It is the core Eval, configured."""
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


# --- the deferred question: was it right? ------------------------------------


def test_an_unusable_output_scores_zero_with_a_reason() -> None:
    out = HorizonWindow(TICKER, AT).eval_function(FakeRun({}))
    assert out.score == 0.0
    assert "nothing to score" in out.comments


def test_a_window_with_no_two_sided_quote_says_so() -> None:
    """A contract that never traded at that minute cannot be scored, and that is
    a fact about coverage rather than a failure of the harness."""
    ancient = datetime(2020, 1, 1, tzinfo=timezone.utc)
    out = HorizonWindow(TICKER, ancient).eval_function(FakeRun({"delta_cents": 0.0}))
    assert out.score == 0.0
    assert "two-sided" in out.comments


def test_the_score_lands_in_the_unit_interval() -> None:
    """Reported as 0.5 + skill/2, so matching the benchmark is half a point and
    the leaderboard's arithmetic holds. The raw skill stays in the metadata."""
    ev = HorizonWindow(TICKER, AT)
    out = ev.eval_function(FakeRun({"delta_cents": 0.0, "half_width_cents": 2.0}))
    assert 0.0 <= out.score <= 1.0
    if out.metadata:
        assert "skill" in out.metadata or "nothing to score" in out.comments


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


def test_the_catalogue_lists_them_all() -> None:
    listing = describe()
    for cls in REGISTRY:
        assert cls.name in listing


def test_names_are_unique() -> None:
    """EVALS is keyed by name; a collision would silently drop one."""
    assert len({cls.name for cls in REGISTRY}) == len(REGISTRY)


# --- the deferred pair: scored without running an agent ----------------------


async def test_a_recorded_feed_is_scored_without_running_anything(tmp_path) -> None:
    """The forecasts were made hours ago by a run that is gone. Re-running an
    agent to re-derive them would be both wrong and expensive, so these reach
    the verdict through Eval.score() rather than Eval.run()."""
    empty = tmp_path / "forecasts.jsonl"
    empty.write_text("")
    for cls in (SettlementBrier, HorizonSkill):
        ev = cls(feed=empty)
        out = await ev.score(None)
        assert out.score == 0.0
        assert "no " in out.comments, cls.name
        assert ev.agent_output is None, "nothing ran"


async def test_a_missing_feed_is_an_empty_score_not_a_crash(tmp_path) -> None:
    """A night with no football and a broken collector produce the same silence,
    and neither is an exception."""
    out = await SettlementBrier(feed=tmp_path / "nope.jsonl").score(None)
    assert out.score == 0.0 and out.metadata["n"] == 0


async def test_the_description_is_filled_in_by_the_eval(tmp_path) -> None:
    empty = tmp_path / "f.jsonl"
    empty.write_text("")
    out = await HorizonSkill(feed=empty).score(None)
    assert out.description.startswith("horizon_skill")


def test_a_deferred_eval_names_the_benchmark_it_scores_against() -> None:
    """A skill number is meaningless without saying skill against what. Both
    benchmarks are free to match, which is the point of quoting them."""
    assert "market" in SettlementBrier.description
    assert "no change" in HorizonSkill.description


def test_an_eval_supplies_every_input_its_agent_reads() -> None:
    """Eval.run() refuses a plan whose inputs are not covered, so this is the
    check that the Kalshi evals hold up their end of that bargain."""
    for ev in (HorizonWindow(TICKER, AT),):
        assert not ev.agent.plan.required_inputs() - set(ev.input), ev.name


def test_an_unmoved_market_is_flagged_as_unmeasurable() -> None:
    """No-change has zero error there, so skill is undefined and the window
    scores a flat 0.5 whether the forecast was exactly right or badly wrong.
    Pooling those as ties drags a leaderboard toward the middle."""
    out = HorizonWindow(TICKER, AT).grade(
        FakeRun({"delta_cents": 1.5, "half_width_cents": 3.0}))
    if out.metadata.get("unmeasurable"):
        assert out.score == 0.5
        assert "did not move" in out.comments
