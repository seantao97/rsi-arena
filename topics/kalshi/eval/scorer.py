"""Turn a five-minute forecast into a number the arena can rank on.

The arena's contract is a scorer: it reads what an agent produced and returns a
:class:`~rsi_arena.evals.scoring.Score`. That fits a text answer neatly and an
in-play forecast badly, for two reasons this module exists to fix.

**The answer arrives late.** Live, a forecast cannot be marked for five
minutes. :mod:`.replay` removes that by asking the question at a past instant,
where the next five minutes are already in the candlestick history.

**Being close is not the same as being useful.** A quiet book barely moves, so
predicting no change is nearly always nearly right, and absolute error rewards
it. The score here is *skill against no-change*: the fraction of the
benchmark's error the forecast removed. Zero means the forecast was worth
exactly as much as saying nothing, and that is where a harness that copies the
current mid lands — which, measured over 586 live windows, is where the first
one did land.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from rsi_arena.evals.scoring import Score, register_scorer

from .trading import quote_from
from .. import History
from .replay import HORIZON_MINUTES, realised_mid

# Below this the two errors are the same number to the tenth of a cent, and a
# ratio between them is noise dressed as a result.
_NEGLIGIBLE = 1e-4


@dataclass(frozen=True)
class WindowScore:
    """One forecast, and what the market did instead."""

    ticker: str
    at: str
    mid_now: float
    predicted: float
    realised: float
    half_width: float

    @property
    def error(self) -> float:
        return abs(self.predicted - self.realised)

    @property
    def naive_error(self) -> float:
        """What predicting no change would have cost."""
        return abs(self.mid_now - self.realised)

    @property
    def skill(self) -> float:
        """Fraction of the no-change error removed. Negative is worse than
        silence; on an unmoved market it is zero rather than undefined."""
        if self.naive_error < _NEGLIGIBLE:
            return 0.0
        return 1 - self.error / self.naive_error

    @property
    def echoed(self) -> bool:
        """Predicted the current mid exactly — the degenerate answer, which
        scores zero by construction and costs a model call to produce."""
        return abs(self.predicted - self.mid_now) < _NEGLIGIBLE

    @property
    def covered(self) -> bool:
        """Did the price land inside the quote the agent said it would make?"""
        return abs(self.realised - self.predicted) <= self.half_width

    def to_dict(self) -> dict:
        out = asdict(self)
        out.update(error=round(self.error, 4),
                   naive_error=round(self.naive_error, 4),
                   skill=round(self.skill, 4),
                   echoed=self.echoed, covered=self.covered)
        return out


def score_window(output: dict, ticker: str, at: datetime, mid_now: float,
                 minutes: int = HORIZON_MINUTES,
                 history: History | None = None) -> WindowScore | None:
    """Score one output against the price the market actually printed.

    ``None`` when the market gave no two-sided quote to compare against, or
    when the output carries no usable number. Both are facts about the data
    rather than verdicts on the agent, and scoring them as zero would punish it
    for the book being shut.
    """
    delta = output.get("delta_cents") if isinstance(output, dict) else None
    if not isinstance(delta, (int, float)):
        return None
    realised = realised_mid(ticker, at, minutes, history)
    if realised is None:
        return None

    # The same arithmetic the live supervisor applies, called rather than
    # copied. A second implementation of it would drift silently, and the
    # benchmark would stop measuring the harness that actually runs.
    width = output.get("half_width_cents")
    predicted, low, high = quote_from(
        mid_now, delta, width if isinstance(width, (int, float)) else 0.0)
    return WindowScore(ticker=ticker, at=at.isoformat(), mid_now=mid_now,
                       predicted=predicted, realised=float(realised),
                       half_width=(high - low) / 2)


def horizon_skill(ticker: str, at: datetime, mid_now: float,
                  minutes: int = HORIZON_MINUTES):
    """A scorer for one replayed window, in the arena's own shape.

    Reported as ``0.5 + skill/2`` so it lands in [0, 1] with a half-point for
    matching the benchmark, which is what the leaderboard's arithmetic expects.
    The raw skill is in the metadata, where it is not squashed.
    """
    def scorer(output) -> Score:
        parsed = output if isinstance(output, dict) else {}
        window = score_window(parsed, ticker, at, mid_now, minutes)
        if window is None:
            return Score(value=0.0, passed=False,
                         reason="no two-sided quote at the horizon, "
                                "or no usable prediction",
                         metadata={"scoreable": False})
        return Score(
            value=max(0.0, min(1.0, 0.5 + window.skill / 2)),
            passed=window.skill > 0,
            reason=(f"predicted {window.predicted:.3f}, market printed "
                    f"{window.realised:.3f}; no-change would have missed by "
                    f"{window.naive_error:.3f} and this missed by "
                    f"{window.error:.3f}"),
            metadata=window.to_dict(),
        )

    return scorer


# ``replace=True`` because this runs at import, and a module can be imported
# more than once in a process — a reload, a tool resolving the package by two
# paths, ``importlib.util.find_spec`` on a submodule. Without it the second
# import raises and takes the whole package down with it, which is a strange way
# for a scorer registration to fail.
register_scorer("horizon_skill", horizon_skill, replace=True)
