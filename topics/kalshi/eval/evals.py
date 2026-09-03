"""The Kalshi evals, each an instance of the core :class:`~rsi_arena.Eval`.

Two questions get asked of a forecast, and they come due at different times.

**Is it self-consistent?** Answerable the moment the agent stops. A forecast
that reports an edge and then takes the other side of it is wrong on its own
terms, and no amount of waiting makes it right. :func:`forecast_eval` scores
that, which is why the supervisor can use it live.

**Was it right?** Answerable five minutes later, when the price it predicted has
printed. :func:`window_eval` replays a past instant so the answer already
exists — that is what makes the harness scoreable in thousands of windows a
night rather than one per contract.

Both are ordinary ``Eval`` objects: an agent, an input, and a function that
scores what came back. Nothing here knows how it will be driven.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rsi_arena import Agent, Eval, EvalOutput

from .. import History, MINUTE
from .load import load_agent
from .replay import replay_tools
from .scorer import score_window
from .trading import HORIZON_MINUTES
from .validation import validate, validate_horizon


def _as_dict(output: Any) -> dict[str, Any]:
    return dict(output) if isinstance(output, dict) else {}


def forecast_eval(agent_name: str, ticker: str, *, tools=None, config=None,
                  mode: str = "horizon", **inputs: Any) -> Eval:
    """Score one live forecast on whether it contradicts itself.

    The check is arithmetic, not judgement: a stake taken against the edge the
    same output reports, a probability that disagrees with the price it quotes.
    Live, this is the only verdict available — the price it predicted has not
    printed yet — and it is the one that catches the defect that actually
    occurred, a position that argued with its own numbers.
    """
    check = validate_horizon if mode == "horizon" else validate
    #: Without at least one of these there is no forecast to be consistent with.
    #: Validation answers "does this contradict itself", and an empty dict does
    #: not — which would score a model that said nothing the same as one that
    #: got it right.
    required = ("delta_cents", "half_width_cents") if mode == "horizon" \
        else ("probability", "position")

    def scored(result) -> EvalOutput:
        out = _as_dict(result.output)
        if not any(key in out for key in required):
            return EvalOutput(
                score=0.0,
                comments=f"no forecast in the output — expected one of "
                         f"{', '.join(required)}",
                metadata={"mode": mode, "ticker": ticker}, output=out)
        verdict = check(out)
        return EvalOutput(
            score=1.0 if verdict.ok else 0.0,
            comments="; ".join(verdict.errors) or "; ".join(verdict.warnings)
                     or "consistent",
            # The corrected forecast is what gets recorded — recomputing what
            # can be recomputed is the point, not just flagging it.
            metadata={"mode": mode, "ticker": ticker,
                      "warnings": verdict.warnings, "errors": verdict.errors,
                      "corrected": verdict.corrected},
            output=out,
        )

    return Eval(
        load_agent(agent_name, tools=tools, config=config),
        scored,
        description=f"{ticker} {mode}",
        input={"question": ticker, **inputs},
    )


def window_eval(ticker: str, at: datetime, *, history: History | None = None,
                minutes: int = HORIZON_MINUTES, agent_name: str = "horizon",
                config=None, spec: dict[str, Any] | None = None,
                **inputs: Any) -> Eval:
    """Score one replayed window on whether the prediction beat no-change.

    The agent is put back at ``at`` with tools that cannot see past it, and the
    answer is read out of the candlestick history ``minutes`` later. Skill is
    reported as ``0.5 + skill/2`` so it lands in [0, 1] with a half point for
    matching the benchmark; the raw number is in the metadata, unsquashed.
    """
    hist = history or History()
    candle = hist.quote_at(ticker, at, MINUTE)
    mid_now = candle.mid if candle is not None else None

    def scored(result) -> EvalOutput:
        out = _as_dict(result.output)
        if mid_now is None:
            return EvalOutput(score=0.0, comments="no two-sided quote at the window",
                              output=out)
        window = score_window(out, ticker, at, mid_now, minutes)
        if window is None:
            return EvalOutput(
                score=0.0, comments="unusable output — nothing to score",
                metadata={"ticker": ticker, "at": at.isoformat()}, output=out)
        return EvalOutput(
            score=0.5 + window.skill / 2,
            comments=(f"predicted {window.predicted:.3f}, market printed "
                      f"{window.realised:.3f}; no-change missed by "
                      f"{window.naive_error:.3f} and this by {window.error:.3f}"),
            metadata={"skill": window.skill, **window.to_dict()},
            output=out,
            ground_truth={"mid": window.realised},
        )

    # A benchmark compares harnesses, so a caller may hand in a mutated spec
    # rather than a name on disk. Both bind their tool names against the same
    # frozen box, which is what lets a rewritten harness be replayed at all.
    box = replay_tools(at, hist)
    agent = (Agent.from_dict(spec, box) if spec
             else load_agent(agent_name, tools=box, config=config))
    return Eval(
        agent,
        scored,
        description=f"{ticker} @ {at.isoformat()[:16]}",
        input={"question": ticker, **inputs},
    )
