"""The Kalshi evals. One class to a file, declared the way the tools are.

Four questions get asked of a harness, and they differ in when the answer
exists rather than in kind:

===========================  =========================  ====================
eval                         asks                       answerable
===========================  =========================  ====================
``forecast_consistency``     does it contradict itself  immediately
``horizon_window``           did it beat no-change      replayed, so already
``horizon_skill``            the same, over live runs   five minutes later
``settlement_brier``         did it understand the game after the whistle
===========================  =========================  ====================

The two that score something recorded earlier are reached through
``Eval.score()`` rather than ``Eval.run()``: the forecasts were made by an agent
that finished hours ago, and running one to re-derive them would be both wrong
and expensive.

Every one is a subclass of the core :class:`~rsi_arena.Eval` — no Kalshi type
escapes into the framework, exactly as no Kalshi type escapes through
:class:`~rsi_arena.Tool`.

Adding one is a file and one line in :data:`REGISTRY`.
"""

from __future__ import annotations

from rsi_arena import Eval

from .forecast_consistency import ForecastConsistency
from .horizon_skill import HorizonSkill
from .horizon_window import HorizonWindow
from .settlement_brier import SettlementBrier

#: Every eval this topic offers. Written by hand, so nothing is registered by
#: accident and the order is the order a reader should meet them in.
REGISTRY: list[type[Eval]] = [
    ForecastConsistency,   # now
    HorizonWindow,         # replayed
    HorizonSkill,          # five minutes later
    SettlementBrier,       # after the whistle
]

EVALS: dict[str, type[Eval]] = {cls.name: cls for cls in REGISTRY}


def describe() -> str:
    """A catalogue, for a prompt or a reader deciding which to run."""
    return "\n".join(f"- {cls.name}: {cls.description.splitlines()[0]}"
                     for cls in REGISTRY)


__all__ = ["REGISTRY", "EVALS", "describe",
           *(cls.__name__ for cls in REGISTRY)]
