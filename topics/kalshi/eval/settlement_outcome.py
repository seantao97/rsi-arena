"""Did the probability hold up? Replayed against a match that has since ended."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rsi_arena import Agent, Eval, EvalOutput

from .. import History, MINUTE
from ._load import load_agent
from ._replay import replay_tools
from ._scorer import settled_result


class SettlementOutcome(Eval):
    name = "settlement_outcome"
    description = (
        "One replayed instant of a finished match: the agent's probability "
        "against what actually happened, scored as Brier skill over the "
        "market's own price at that moment.\n\n"
        "The slow question — whether the harness understands the game rather "
        "than the next five minutes. It used to need forecasts collected live "
        "and graded days later; it does not, because Kalshi keeps the result on "
        "every settled contract. Put the agent back at minute sixty of a match "
        "that has since ended and the truth it was forecasting already exists.\n\n"
        "The benchmark is the market at that instant, which had watched the "
        "same match and usually knows more. Half a point means matching it, and "
        "anything above that is real.\n\n"
        "Needs a harness every one of whose tools can be frozen. One that "
        "researches the news or reads live game state cannot be replayed at "
        "all — a story filed after the instant would be answering with the "
        "future."
    )

    def __init__(self, ticker: str, at: datetime, agent_name: str, *,
                 history: History | None = None, config=None,
                 spec: dict[str, Any] | None = None, game: str = "",
                 **inputs: Any) -> None:
        # No default harness. Binding raises if the config names a tool the
        # frozen box does not have, which is the check that keeps a replay
        # honest — and the reason there is nothing sensible to default to.
        self.ticker, self.at = ticker, at
        self.history = history or History()
        candle = self.history.quote_at(ticker, at, MINUTE)
        #: What the market thought at that instant — the benchmark to beat.
        self.market_mid = candle.mid if candle is not None else None
        #: What actually happened. ``None`` means the contract has not settled,
        #: which makes the window unscoreable rather than the harness wrong.
        self.outcome = settled_result(ticker)

        box = replay_tools(at, self.history)
        super().__init__(
            Agent.from_dict(spec, box) if spec
            else load_agent(agent_name, tools=box, config=config),
            self.grade,
            description=f"{self.name}: {ticker} @ {at.isoformat()[:16]}",
            # Eval.run() checks this against what the plan reads, so a harness
            # that starts asking for a new name fails before it is paid for.
            input={"question": ticker, "game": game or "unavailable", **inputs},
        )

    def grade(self, result: Any) -> EvalOutput:
        out = dict(result.output) if isinstance(getattr(result, "output", None), dict) else {}
        if self.outcome is None or self.market_mid is None:
            return EvalOutput(
                score=0.0, output=out,
                comments=("not settled yet" if self.outcome is None
                          else "no two-sided quote at that instant"))

        probability = out.get("probability")
        if not isinstance(probability, (int, float)) or not 0 <= probability <= 1:
            return EvalOutput(
                score=0.0, output=out,
                comments=f"no usable probability in the output ({probability!r})",
                ground_truth={"settled": self.outcome})

        truth = 1.0 if self.outcome else 0.0
        brier = (probability - truth) ** 2
        market_brier = (self.market_mid - truth) ** 2
        # Undefined when the market was exactly right, which is the same absence
        # of evidence an unmoved window is for the horizon eval.
        unmeasurable = market_brier < 1e-9
        skill = 0.0 if unmeasurable else 1 - brier / market_brier

        said = (f"said {probability:.3f}, market said {self.market_mid:.3f}, "
                f"it settled {'YES' if self.outcome else 'NO'}; Brier "
                f"{brier:.4f} against {market_brier:.4f}")
        if unmeasurable:
            said += " — the market was exactly right, so there is no skill to measure"
        return EvalOutput(
            score=max(0.0, min(1.0, 0.5 + skill / 2)),
            comments=said,
            metadata={"skill": skill, "brier": brier, "market_brier": market_brier,
                      "probability": probability, "market_mid": self.market_mid,
                      "unmeasurable": unmeasurable},
            output=out,
            ground_truth={"settled": self.outcome, "truth": truth})
