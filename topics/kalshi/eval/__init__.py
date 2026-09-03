"""Running the Kalshi harnesses and saying how they did.

Same convention as ``tools/``: **no underscore is public, an underscore is
machinery**, and :data:`REGISTRY` is the definition of what an eval is. The one
difference is that some public names here are commands rather than classes —
``supervisor``, ``verify``, ``preflight``, ``slate`` and ``run`` are invoked with
``python -m``, so hiding them behind an underscore would be a lie.

Three evals, which are three genuinely different questions:

=====================  ===========================  ======================
eval                   asks                         needs
=====================  ===========================  ======================
``horizon_window``     did it beat no-change        nothing — replayed
``horizon_skill``      the same, over live runs     a recorded feed
``settlement_brier``   did it understand the game   a recorded feed and
                                                    a finished match
=====================  ===========================  ======================

``horizon_window`` is the one that makes the harness measurable at all: five
minutes after any past instant the answer is already in the candlestick
history, so a night of football yields thousands of labelled windows without
collecting anything. The other two grade forecasts that were actually made, and
reach their verdict through ``Eval.score()`` rather than ``Eval.run()``.

Adding one is a file and one line in :data:`REGISTRY`.
"""

from __future__ import annotations

from rsi_arena import Eval

from .horizon_skill import HorizonSkill
from .horizon_window import HorizonWindow
from .settlement_brier import SettlementBrier

#: Every eval this topic offers. Written by hand, so nothing is registered by
#: accident and a command in this directory is never mistaken for one.
REGISTRY: list[type[Eval]] = [
    HorizonWindow,         # replayed — the answer already exists
    HorizonSkill,          # the same metric, over live runs
    SettlementBrier,       # after the whistle
]

EVALS: dict[str, type[Eval]] = {cls.name: cls for cls in REGISTRY}


def describe() -> str:
    """A catalogue, for a prompt or a reader deciding which to run."""
    return "\n".join(f"- {cls.name}: {cls.description.splitlines()[0]}"
                     for cls in REGISTRY)


__all__ = ["REGISTRY", "EVALS", "describe",
           *(cls.__name__ for cls in REGISTRY)]
