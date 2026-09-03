"""One eval: an agent, an input, and a function that scores what came back.

An eval is the unit the arena ranks on. It holds the agent, the input it is run
against, and a scoring function — and running it produces an
:class:`EvalOutput`, which is the only thing a leaderboard needs to read.

The scoring function takes the agent's output and returns an ``EvalOutput``.
Keeping it a plain callable rather than a registry entry is deliberate: a scorer
is ordinary code, and the moment it needs a name to travel by, that name belongs
to whatever is doing the travelling rather than here.

    ev = Eval(agent, score_it, description="five-minute price", input={...})
    out = await ev.run()
    out.score       # the number the arena ranks on
    out.comments    # why, in a sentence

An eval whose answer is not available yet scores separately from the run that
produced it — ``await ev.score(recorded)`` — which is how a forecast made hours
ago is graded once the match it was about has finished.

A run that failed never reaches the scoring function: it is scored zero and
flagged ``run_failed``, so an expired key does not read as a harness that cannot
answer.
"""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Union

from pydantic import BaseModel, Field

from ..agent.agent import Agent, AgentResult


class EvalOutput(BaseModel):
    """What one eval produced.

    ``score`` is the number and ``comments`` is why. ``output`` and
    ``ground_truth`` are kept side by side so a low score can be read without
    re-running anything — the commonest question about a result is not what it
    scored but what it said, against what.
    """

    description: str = ""
    score: float = 0.0
    comments: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    ground_truth: dict[str, Any] = Field(default_factory=dict)


#: Scores one run. Takes what the agent produced, returns the verdict.
#:
#: Async is allowed but not required. A scorer that only reads the text has no
#: reason to be a coroutine, and one that asks a model to judge has no way not
#: to be, so :meth:`Eval.run` awaits whatever comes back if it is awaitable.
EvalFunction = Callable[[AgentResult], Union[EvalOutput, Awaitable[EvalOutput]]]


class Eval:
    """An agent, the input to run it on, and the function that scores it.

    :meth:`run` checks the input against what the agent's plan actually reads,
    before calling it, so an eval that forgets one costs nothing to find out.
    """

    def __init__(
        self,
        agent: Agent,
        eval_function: EvalFunction,
        *,
        description: str = "",
        input: dict[str, Any] | None = None,
    ) -> None:
        self.agent = agent
        self.description = description
        self.eval_function = eval_function
        self.input = dict(input or {})
        #: The last run's raw agent output, kept so a caller can inspect the
        #: trace behind a score without threading it through the return value.
        #: ``None`` for an eval that scores something recorded earlier.
        self.agent_output: AgentResult | None = None

    @staticmethod
    def _did_not_finish(agent_output: Any) -> str:
        """The error on a run that produced no answer, or ``""``.

        An error alone is not enough. A run stopped at its budget ceiling in
        max-spend mode carries one *and* an answer written from what it already
        had, and that answer is worth scoring — an answered cut-off beats a dead
        run, and grading them the same would remove the reason to bail out at
        all. So a run failed only if it left nothing to grade.
        """
        error = str(getattr(agent_output, "error", "") or "")
        if not error:
            return ""
        answered = getattr(agent_output, "output", None)
        return "" if answered not in (None, "", {}, []) else error

    async def score(self, agent_output: Any) -> EvalOutput:
        """Score an output the agent already produced.

        Not every eval can run its agent at the moment it scores. A forecast
        about the next five minutes is only answerable five minutes later, and
        one about a match is only answerable after the whistle — by which time
        the run is hours gone and lives in a file. Those evals score what was
        recorded, and running an agent to re-derive it would be both wrong and
        expensive.

        :meth:`run` is this with the run in front of it.
        """
        # A run that failed is not an answer to grade. An expired key, a bad
        # template and a harness that genuinely cannot answer would otherwise be
        # indistinguishable at the bottom of a leaderboard, which reads
        # infrastructure failure as evidence about the harness. Handled here so
        # that every eval gets it and no eval has to remember to.
        failure = self._did_not_finish(agent_output)
        if failure:
            out = EvalOutput(
                score=0.0,
                comments=f"the run did not finish: {failure}",
                metadata={"run_failed": True,
                          "error_kind": str(getattr(agent_output, "error_kind", "")
                                            or "")},
            )
        else:
            out = self.eval_function(agent_output)
            if inspect.isawaitable(out):
                out = await out
        if self.description and not out.description:
            out = out.model_copy(update={"description": self.description})
        return out

    async def run(self, **overrides: Any) -> EvalOutput:
        """Run the agent on ``input`` and score what comes back.

        ``overrides`` merge over ``input``, which is what running the same eval
        across a sweep of questions needs.
        """
        merged = {**self.input, **overrides}
        # Checked here rather than in the constructor: an eval that grades a
        # recorded feed never runs its agent and has no business supplying the
        # agent's inputs. Checked before the call rather than after, so a plan
        # that reads a name nobody passed costs nothing — the alternative is a
        # KeyError from render at the step that reads it, once the earlier steps
        # have run and been paid for.
        missing = self.agent.plan.required_inputs() - set(merged)
        if missing:
            raise ValueError(
                f"{self.agent.name} reads {', '.join(sorted(missing))}, which "
                f"this eval does not supply; pass it in `input`")
        question = merged.pop("question", None)
        self.agent_output = await self.agent.run(question, **merged)
        return await self.score(self.agent_output)

    def __repr__(self) -> str:
        return f"Eval(agent={self.agent.name!r}, description={self.description!r})"


def scored_by(scorer: Any, *, prompt: str = "", agent: Any = None,
              llm: Any = None, expected: Any = None) -> EvalFunction:
    """Adapt a :mod:`~rsi_arena.evals.scoring` scorer into an eval function.

    The built-in scorers answer with a :class:`~rsi_arena.evals.scoring.Score` —
    a value, a pass/fail and a note. That is the same verdict an
    :class:`EvalOutput` carries, in a narrower shape, so wrapping is a rename
    rather than a translation. Written this way so a scorer stays usable without
    the eval layer knowing anything about the registry it may have come from.
    """
    from .scoring import EvalContext, apply, scorer_from_spec

    resolved = scorer_from_spec(scorer)

    async def run(result: AgentResult) -> EvalOutput:
        text = str(result.output or "")
        score = await apply(resolved, text, EvalContext(
            prompt=prompt, result=result, agent=agent, llm=llm, expected=expected))
        return EvalOutput(
            score=score.value,
            comments=score.notes or score.label,
            metadata={"passed": score.passed, "label": score.label, **score.details},
            output={"answer": result.output},
            ground_truth={} if expected is None else {"expected": expected},
        )

    return run
