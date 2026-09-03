"""Did the five-minute forecasts beat no-change, pooled over runs?"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from rsi_arena import Eval, EvalOutput

from ... import History
from ..load import load_agent

FEED = "~/.kalshi-agent/forecasts.jsonl"


class HorizonSkill(Eval):
    name = "horizon_skill"
    description = (
        "Every recorded five-minute forecast against what the price did — "
        "skill, fill rate, coverage and pnl, pooled across runs.\n\n"
        "The same question as horizon_window, asked of live forecasts instead "
        "of replayed ones, and pooled deliberately: one evening covers a "
        "handful of contracts, which is not enough to separate a real number "
        "from a lucky one. Every run so far has swung tens of percent before "
        "settling.\n\n"
        "The benchmark is no change, so 0.5 means the agent matched a forecast "
        "that costs nothing to make. Fees are priced into the pnl, because a "
        "round trip is two to three cents and the predictions are the same size."
    )

    def __init__(self, agent=None, *, feed: str | Path | Iterable[str | Path] = FEED,
                 history: History | None = None, agent_name: str = "horizon",
                 **inputs: Any) -> None:
        self.feeds = ([Path(str(feed)).expanduser()]
                      if isinstance(feed, (str, Path))
                      else [Path(str(f)).expanduser() for f in feed])
        self.history = history
        super().__init__(agent or load_agent(agent_name), self.grade,
                         description=f"{self.name}: {len(self.feeds)} feed(s)",
                         input=inputs)

    def report(self):
        from ..verify_horizon import load, load_many

        return (load(self.feeds[0], self.history) if len(self.feeds) == 1
                else load_many(self.feeds, self.history))

    def grade(self, agent_output: Any = None) -> EvalOutput:
        """Scores the feeds, not the argument — see SettlementBrier.grade."""
        report = self.report()
        if not report.n:
            return EvalOutput(score=0.0, comments="no resolved windows in the feed",
                              metadata={"n": 0, "unresolved": report.unresolved})
        return EvalOutput(
            score=max(0.0, min(1.0, 0.5 + report.skill / 2)),
            comments=(f"{report.n} windows: MAE {report.mae:.4f} against "
                      f"no-change {report.naive_mae:.4f}, skill "
                      f"{report.skill:+.3f}"),
            metadata={"n": report.n, "mae": report.mae,
                      "naive_mae": report.naive_mae, "skill": report.skill,
                      "unresolved": report.unresolved, "stale": report.stale,
                      "moves": len(report.moves)},
            ground_truth={"benchmark": "no change over the horizon"})
