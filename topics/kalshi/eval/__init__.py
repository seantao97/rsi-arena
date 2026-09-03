"""Scoring a Kalshi harness, by replaying it against a past it cannot see.

Same convention as ``tools/``: **no underscore is public, an underscore is
machinery**, and :data:`REGISTRY` is the definition of what an eval is. ``run``
is public without being a class because it is invoked with ``python -m``, and an
underscore on a command would be a lie about what it is.

Two evals, which are the two questions worth asking of a forecaster:

    horizon_window       did it beat no change, five minutes out
    settlement_outcome   did it beat the market on how the match ended

Both are answered by replay, from public data. Five minutes after any past
instant the price it predicted is in the candlestick history; after the whistle
the result is on the settled contract. So a harness is scored by putting it back
at an instant with tools frozen there and reading what actually happened — with
nothing collected, no state on disk, no scheduled job and no key spent waiting
for football.

The live pipeline that used to sit beside this — a supervisor collecting
forecasts overnight, reports over the feed it wrote, a preflight check and a
scheduled workflow — is gone, about two thousand lines of it. It answered the
settlement question by waiting days for forecasts to come due. Replay answers
the same question in a second, because the match it is about has already been
played.

Adding an eval is a file and one line in :data:`REGISTRY`.
"""

from __future__ import annotations

from rsi_arena import Eval

from .horizon_window import HorizonWindow
from .settlement_outcome import SettlementOutcome

#: Every eval this topic offers. Written by hand, so a command in this directory
#: is never mistaken for one.
REGISTRY: list[type[Eval]] = [
    HorizonWindow,        # the fast question, five minutes out
    SettlementOutcome,    # the slow one, against how it ended
]

EVALS: dict[str, type[Eval]] = {cls.name: cls for cls in REGISTRY}


def describe() -> str:
    """A catalogue, for a prompt or a reader deciding which to run."""
    return "\n".join(f"- {cls.name}: {cls.description.splitlines()[0]}"
                     for cls in REGISTRY)


__all__ = ["REGISTRY", "EVALS", "describe", "HorizonWindow",
           "SettlementOutcome"]
