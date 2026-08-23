"""Checks that the benchmark measures forecasting and not hindsight.

A replay benchmark has one way to be silently worthless: let something through
that had not happened yet. Every read is bounded, so every read is checked.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from ..history import MINUTE, History
from .replay import SCORING_KINDS, replay_tools, timeline
from .scorer import WindowScore, score_window

GAME = "401879319"          # Newcastle 2-2 Liverpool, 23 Aug 2026
TICKER = "KXEPLGAME-26AUG23NEWLFC-NEW"
AT = datetime(2026, 8, 23, 16, 30, tzinfo=timezone.utc)


def test_the_score_is_rebuilt_not_read_off() -> None:
    """Goals count when they happened, and a penalty is a goal.

    The feed files a converted penalty as ``penalty---scored``. Counting only
    ``goal`` rebuilt this match as 2-1 when it finished 2-2, which would have
    fed the agent a false history for every window after the 90th minute.
    """
    line = timeline("EPL", GAME)
    assert line is not None
    assert line.final_score() == (2, 2)
    assert line.score_at(line.kickoff + timedelta(minutes=5))[:2] == (1, 0)
    assert line.score_at(line.kickoff + timedelta(minutes=55))[:2] == (1, 1)
    assert line.score_at(line.kickoff + timedelta(minutes=57))[:2] == (2, 1)
    # Before kickoff nothing has happened yet.
    assert line.score_at(line.kickoff - timedelta(minutes=1))[:2] == (0, 0)
    assert "penalty---scored" in SCORING_KINDS


def test_no_event_leaks_from_the_future() -> None:
    line = timeline("EPL", GAME)
    for minute in (10, 30, 50, 70):
        when = line.kickoff + timedelta(minutes=minute)
        state = line.state_at(when)
        played = state["home_score"] + state["away_score"]
        later = [e for e in line.events if e.scored and e.seconds > minute * 60]
        # Everything still to come is excluded, and what is included happened.
        assert played + len(later) == sum(line.final_score())


def test_no_price_leaks_from_the_future() -> None:
    """Every tool stops at the instant, and the answer lies beyond it."""
    tools = replay_tools(AT)

    async def go() -> None:
        quote = await tools.get("market_quote")(ticker=TICKER)
        assert quote.ok and quote.output.get("mid") is not None

        bars = await tools.get("price_history")(ticker=TICKER, hours_back=0.75)
        assert bars.ok and bars.output
        assert all(datetime.fromisoformat(b["ts"]) <= AT for b in bars.output)

        tape = await tools.get("recent_trades")(ticker=TICKER, limit=12)
        assert tape.ok and tape.output, "the tape must not fail silently"
        assert all(t["ts"] and t["ts"][:19] <= AT.isoformat()[:19]
                   for t in tape.output)
        # And it must carry real prices, not Nones from a renamed field.
        assert all(t["yes_price"] is not None for t in tape.output)

    asyncio.run(go())


def test_the_answer_is_after_the_question() -> None:
    """What the window is scored against comes from beyond its own edge."""
    history = History()
    asked = history.quote_at(TICKER, AT, MINUTE)
    answer = history.quote_at(TICKER, AT + timedelta(minutes=5), MINUTE)
    assert asked is not None and answer is not None
    assert asked.ts <= AT < answer.ts


def test_skill_is_zero_for_repeating_the_market() -> None:
    """However far the price moves, echoing it scores exactly nothing."""
    for realised in (0.40, 0.55, 0.10):
        assert WindowScore("T", "t", 0.40, 0.40, realised, 0.03).skill == 0.0
    # And a real call scores against how much was at stake, not in the absolute.
    assert WindowScore("T", "t", 0.40, 0.53, 0.55, 0.03).skill > 0.8


def test_an_unusable_output_scores_nothing_rather_than_zero() -> None:
    """A missing prediction is not a wrong prediction — it cannot be scored,
    and scoring it as zero would punish the agent for a shut book."""
    assert score_window({}, TICKER, AT, 0.5) is None
    assert score_window({"delta_cents": "x"}, TICKER, AT, 0.5) is None
