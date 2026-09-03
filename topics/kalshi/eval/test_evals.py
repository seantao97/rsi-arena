"""``topics.kalshi.eval.evals`` — the Kalshi evals, as Eval instances.

Two questions, due at different times: is the forecast self-consistent (now),
and was it right (five minutes later). These check that each is asked of the
right thing and that neither can see what it should not.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from rsi_arena import Eval, EvalOutput

from .evals import forecast_eval, window_eval

TICKER = "KXEPLGAME-26AUG23NEWLFC-NEW"
AT = datetime(2026, 8, 23, 15, 30, tzinfo=timezone.utc)


class FakeRun:
    """Stands in for an AgentResult — the eval function only reads ``output``."""

    def __init__(self, output):
        self.output = output


# --- what an eval is ----------------------------------------------------------


def test_a_window_is_an_ordinary_eval() -> None:
    """Nothing here is a Kalshi subclass. It is the core Eval, configured."""
    ev = window_eval(TICKER, AT)
    assert isinstance(ev, Eval)
    assert ev.agent.name == "kalshi-horizon-5m"
    assert ev.input["question"] == TICKER
    assert TICKER in ev.description


def test_a_replayed_window_binds_the_frozen_tools() -> None:
    """The agent is rebound against tools that read history rather than the live
    book. It is the same config either way and cannot tell the difference —
    which is what makes replaying a harness possible at all."""
    ev = window_eval(TICKER, AT)
    assert set(ev.agent.tools) == {"market_quote", "candlesticks", "previous_trades"}


# --- the immediate question: does it contradict itself? -----------------------


def test_a_self_consistent_forecast_scores_one() -> None:
    ev = forecast_eval("horizon", TICKER)
    out = ev.eval_function(FakeRun({"delta_cents": 0.0, "half_width_cents": 2.0}))
    assert out.score == 1.0
    assert out.metadata["mode"] == "horizon"


def test_a_forecast_that_argues_with_itself_scores_zero() -> None:
    """A half width of zero claims a price known to the cent five minutes out.
    A wide one is only a warning — nothing clears, but nothing is claimed."""
    strict = forecast_eval("horizon", TICKER).eval_function(
        FakeRun({"delta_cents": 5.0, "half_width_cents": 0.0}))
    assert strict.score == 0.0 and strict.metadata["errors"]

    wide = forecast_eval("horizon", TICKER).eval_function(
        FakeRun({"delta_cents": 5.0, "half_width_cents": 900.0}))
    assert wide.score == 1.0, "unclearable, but it does not contradict itself"
    assert wide.metadata["warnings"]


def test_the_corrected_forecast_is_kept_not_just_the_verdict() -> None:
    """Recomputing what can be recomputed is the point; a flag alone would throw
    away the only version of the forecast that holds together."""
    out = forecast_eval("horizon", TICKER).eval_function(
        FakeRun({"delta_cents": 1.0, "half_width_cents": 3.0}))
    assert "corrected" in out.metadata


def test_junk_output_is_scored_rather_than_crashing() -> None:
    """A model that returns a string where a dict was asked for is a bad
    forecast, not a broken eval. Nor is an empty one consistent — saying nothing
    would otherwise score the same as getting it right."""
    for junk in ("not a dict", {}, {"commentary": "looks fine to me"}):
        out = forecast_eval("horizon", TICKER).eval_function(FakeRun(junk))
        assert out.score == 0.0, junk
        assert "no forecast" in out.comments


# --- the deferred question: was it right? ------------------------------------


def test_an_unusable_output_scores_zero_with_a_reason() -> None:
    out = window_eval(TICKER, AT).eval_function(FakeRun({}))
    assert out.score == 0.0
    assert "nothing to score" in out.comments


def test_a_window_with_no_two_sided_quote_says_so() -> None:
    """A contract that never traded at that minute cannot be scored, and that is
    a fact about coverage rather than a failure of the harness."""
    ancient = datetime(2020, 1, 1, tzinfo=timezone.utc)
    out = window_eval(TICKER, ancient).eval_function(FakeRun({"delta_cents": 0.0}))
    assert out.score == 0.0
    assert "two-sided" in out.comments


def test_the_score_lands_in_the_unit_interval() -> None:
    """Reported as 0.5 + skill/2, so matching the benchmark is half a point and
    the leaderboard's arithmetic holds. The raw skill stays in the metadata."""
    ev = window_eval(TICKER, AT)
    out = ev.eval_function(FakeRun({"delta_cents": 0.0, "half_width_cents": 2.0}))
    assert 0.0 <= out.score <= 1.0
    if out.metadata:
        assert "skill" in out.metadata or "nothing to score" in out.comments
