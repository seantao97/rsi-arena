"""Evals: give an agent a prompt, score what it says, keep the result.

A battle asks a human which of two answers is better. An eval asks a *function*
whether one answer is good — cheaper, repeatable, and runnable without anybody
watching, which is what makes it the thing a nightly job and an optimizer can
use. The arena still needs votes for taste; evals catch the regressions that
never should have reached a voter.

.. code-block:: python

    from rsi_arena import Eval
    from rsi_arena.evals import contains, llm_judge

    ev = Eval(agent, "Did the ECB cut rates in July 2026?", [
        contains("unchanged"),
        llm_judge("Every factual claim carries the URL it came from."),
    ])
    result = await ev.run()
    print(result.score.value, result.score.notes, result.cost_usd)

=========================  ====================================================
:mod:`~rsi_arena.evals.eval`     :class:`Eval`, :class:`EvalSuite` and their results
:mod:`~rsi_arena.evals.scoring`  :class:`Score`, the built-in scorers, the registry
:mod:`~rsi_arena.evals.store`    where results go — in memory now, a DB later
=========================  ====================================================
"""

from __future__ import annotations

from .eval import Eval, EvalResult, EvalSuite, SuiteResult
from .scoring import (
    SCORERS,
    EvalContext,
    Score,
    Scorer,
    all_of,
    apply,
    completed,
    contains,
    get_scorer,
    json_valid,
    llm_judge,
    non_empty,
    not_contains,
    regex,
    register_scorer,
    scorer_from_spec,
    under_cost,
)
from .store import EvalStore, InMemoryEvalStore, default_eval_store, set_default_eval_store

__all__ = [
    "Eval", "EvalSuite", "EvalResult", "SuiteResult",
    "Score", "Scorer", "EvalContext", "apply",
    "contains", "not_contains", "regex", "non_empty", "json_valid", "under_cost",
    "completed", "llm_judge", "all_of",
    "SCORERS", "register_scorer", "get_scorer", "scorer_from_spec",
    "EvalStore", "InMemoryEvalStore", "default_eval_store", "set_default_eval_store",
]
