"""Was the probability right? Answerable only after the whistle."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rsi_arena import Eval, EvalOutput

from ... import History
from ..load import load_agent

FEED = "~/.kalshi-agent/forecasts.jsonl"


class SettlementBrier(Eval):
    name = "settlement_brier"
    description = (
        "Every recorded probability forecast against how the match actually "
        "settled — Brier score, calibration, and paper pnl.\n\n"
        "This is the slow question: not whether the agent can predict the next "
        "five minutes but whether it understands the game. It is answerable "
        "only after the whistle, so it scores forecasts recorded hours or days "
        "earlier rather than running an agent.\n\n"
        "The benchmark is the market's own price at the time of the forecast. "
        "Brier skill is reported as 0.5 + skill/2, so half a point means the "
        "agent matched a market that had watched the same match and usually "
        "knows more. Anything above that is real."
    )

    def __init__(self, agent=None, *, feed: str | Path = FEED,
                 history: History | None = None, agent_name: str = "inplay",
                 **inputs: Any) -> None:
        self.feed = Path(str(feed)).expanduser()
        self.history = history
        super().__init__(agent or load_agent(agent_name), self.grade,
                         description=f"{self.name}: {self.feed.name}",
                         input=inputs)

    def report(self):
        """The settled forecasts, joined to their outcomes."""
        from ..verify import load

        return load(self.feed, self.history)

    def grade(self, agent_output: Any = None) -> EvalOutput:
        """Scores the feed, not the argument.

        The forecasts were made by an agent that finished hours ago; there is no
        run to hand in. Reached through ``Eval.score()`` rather than ``run()``.
        """
        report = self.report()
        if not report.n:
            return EvalOutput(score=0.0, comments="no settled forecasts in the feed",
                              metadata={"feed": str(self.feed), "n": 0})
        return EvalOutput(
            score=max(0.0, min(1.0, 0.5 + report.skill / 2)),
            comments=(f"{report.n} settled: Brier {report.brier:.4f} against the "
                      f"market's {report.market_brier:.4f}, skill "
                      f"{report.skill:+.3f}, bias {report.bias:+.3f}"),
            metadata={"n": report.n, "brier": report.brier,
                      "market_brier": report.market_brier, "skill": report.skill,
                      "bias": report.bias, "unsettled": report.unsettled,
                      "feed": str(self.feed)},
            ground_truth={"benchmark": "the market price at forecast time"})
