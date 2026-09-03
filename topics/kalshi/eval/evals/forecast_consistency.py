"""Does the forecast contradict itself? Answerable the moment the agent stops."""

from __future__ import annotations

from typing import Any

from rsi_arena import Eval, EvalOutput

from ..load import load_agent
from ..validation import validate, validate_horizon


class ForecastConsistency(Eval):
    name = "forecast_consistency"
    description = (
        "Whether one live forecast holds together on its own terms.\n\n"
        "This is the only verdict available while a match is running — the "
        "price the forecast is about has not printed yet — and it is the one "
        "that catches the defect that actually occurred live: a position taken "
        "against the edge the same output reported.\n\n"
        "The check is arithmetic, not judgement. A half width of zero claims a "
        "price known to the cent five minutes out; a fifty-cent move in five "
        "minutes is a different contract, not a forecast. Scores 1 or 0, and "
        "keeps the corrected forecast in metadata — recomputing what can be "
        "recomputed is the point, not just flagging it."
    )

    #: Without one of these there is no forecast to be consistent *with*, and an
    #: empty dict does not contradict itself — so a model that said nothing
    #: would otherwise score the same as one that got it right.
    REQUIRED = {"horizon": ("delta_cents", "half_width_cents"),
                "probability": ("probability", "position")}

    def __init__(self, agent=None, *, ticker: str = "", mode: str = "horizon",
                 tools=None, config=None, agent_name: str = "horizon",
                 **inputs: Any) -> None:
        self.mode = mode
        self.ticker = ticker
        self.check = validate_horizon if mode == "horizon" else validate
        super().__init__(
            agent or load_agent(agent_name, tools=tools, config=config),
            self.grade,
            description=f"{self.name}: {ticker} {mode}".strip(),
            input={"question": ticker, **inputs},
        )

    def grade(self, result: Any) -> EvalOutput:
        out = dict(result.output) if isinstance(getattr(result, "output", None), dict) else {}
        required = self.REQUIRED[self.mode]
        if not any(key in out for key in required):
            return EvalOutput(
                score=0.0,
                comments=f"no forecast in the output — expected one of "
                         f"{', '.join(required)}",
                metadata={"mode": self.mode, "ticker": self.ticker}, output=out)
        verdict = self.check(out)
        return EvalOutput(
            score=1.0 if verdict.ok else 0.0,
            comments="; ".join(verdict.errors) or "; ".join(verdict.warnings)
                     or "consistent",
            metadata={"mode": self.mode, "ticker": self.ticker,
                      "warnings": verdict.warnings, "errors": verdict.errors,
                      "corrected": verdict.corrected},
            output=out)
