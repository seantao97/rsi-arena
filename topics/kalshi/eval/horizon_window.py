"""Did the five-minute prediction beat no-change? Replayed, so the answer exists."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rsi_arena import Agent, Eval, EvalOutput

from .. import History, MINUTE
from ._load import load_agent
from ._replay import replay_tools
from ._scorer import score_window
from ._scorer import HORIZON_MINUTES


class HorizonWindow(Eval):
    name = "horizon_window"
    description = (
        "One replayed window: the agent put back at a past instant with tools "
        "that cannot see past it, scored against what the price actually did.\n\n"
        "This is what makes the harness measurable at all. Five minutes after "
        "any instant the answer is already in the candlestick history, so a "
        "night of football yields thousands of labelled windows rather than one "
        "verdict per contract at settlement.\n\n"
        "The benchmark is no change. Predicting the price stays put is free and "
        "right most of the time, so the score is reported as 0.5 + skill/2 — "
        "half a point for matching the benchmark, and the raw skill sits "
        "unsquashed in metadata."
    )

    def __init__(self, ticker: str, at: datetime, *, history: History | None = None,
                 minutes: int = HORIZON_MINUTES, agent_name: str = "horizon",
                 config=None, spec: dict[str, Any] | None = None,
                 game: str = "", **inputs: Any) -> None:
        self.ticker, self.at, self.minutes = ticker, at, minutes
        self.history = history or History()
        candle = self.history.quote_at(ticker, at, MINUTE)
        #: The mid the prediction is anchored to. None means this window was
        #: never two-sided, which is a fact about coverage rather than a failure.
        self.mid_now = candle.mid if candle is not None else None

        # A benchmark compares harnesses, so a caller may hand in a mutated spec
        # rather than a name on disk. Both bind their tool names against the
        # same frozen box, which is what lets a rewritten harness be replayed.
        box = replay_tools(at, self.history)
        super().__init__(
            Agent.from_dict(spec, box) if spec
            else load_agent(agent_name, tools=box, config=config),
            self.grade,
            description=f"{self.name}: {ticker} @ {at.isoformat()[:16]}",
            # The plan reads {{game}} — Eval checks that against the plan and
            # refuses to construct without it. A caller replaying a fixture it
            # has a timeline for passes the real state; one that does not says
            # so, because a thinner forecast is a result and a missing key is
            # a crash four steps in.
            input={"question": ticker, "game": game or "unavailable", **inputs},
        )

    def grade(self, result: Any) -> EvalOutput:
        out = dict(result.output) if isinstance(getattr(result, "output", None), dict) else {}
        if self.mid_now is None:
            return EvalOutput(score=0.0, output=out,
                              comments="no two-sided quote at the window")
        window = score_window(out, self.ticker, self.at, self.mid_now,
                              self.minutes, self.history)
        if window is None:
            return EvalOutput(
                score=0.0, comments="unusable output — nothing to score",
                metadata={"ticker": self.ticker, "at": self.at.isoformat()},
                output=out)
        # No-change has zero error on a market that did not move, so skill is
        # undefined there and scorer.py reports 0 rather than a huge negative.
        # Right for the metric, wrong for a leaderboard: the window scores a
        # flat 0.5 whether the forecast was exactly right or badly wrong.
        # Flagged rather than rescored — changing the metric is a judgement that
        # belongs to whoever owns it, and a flag lets a caller pooling windows
        # drop the ones that carry no signal instead of averaging them as ties.
        unmeasurable = window.naive_error < 1e-9
        said = (f"predicted {window.predicted:.3f}, market printed "
                f"{window.realised:.3f}; no-change missed by "
                f"{window.naive_error:.3f} and this by {window.error:.3f}")
        if unmeasurable:
            said += " — the market did not move, so there is no skill to measure"
        return EvalOutput(
            score=0.5 + window.skill / 2,
            comments=said,
            metadata={"skill": window.skill, "unmeasurable": unmeasurable,
                      **window.to_dict()},
            output=out,
            ground_truth={"mid": window.realised})
