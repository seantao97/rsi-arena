"""One eval: an agent, a prompt, and a function that scores what comes back.

.. code-block:: python

    ev = Eval(agent, "Did the ECB cut rates in July 2026?", contains("unchanged"))
    result = await ev.run()
    print(result.score.value, result.cost_usd)
    # stored automatically; fetch it later with default_eval_store().get(result.id)

The constructor takes the scoring function and resolves it there — a plain
callable, a registered name, a ``{"type": ...}`` spec from a request body, or
a list of any of those — so an :class:`Eval` is invalid at construction rather
than halfway through a paid run. Everything else it needs it already has: the
agent carries its own model, tools, plan and ceiling.

Running one is separate from constructing one because running is I/O: it costs
money and takes a minute, and a constructor that quietly spends $2 is a
constructor nobody can call from a request handler.

An :class:`EvalSuite` is several of these over one agent or several, run
concurrently against a shared client, with an aggregate at the end.

**Max spend.** ``max_spend_mode=True`` (or an ``max_usd`` low enough to bite)
cuts the agent off at its ceiling and makes it answer from state instead —
see :meth:`~rsi_arena.agent.agent.Agent.immediate_answer`. The result is still
scored, and it carries ``bailed_out=True`` and ``error_kind="max_spend"`` so
a cut-off answer is never silently counted as a clean one.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from pydantic import BaseModel, Field

from ..agent import Agent, ErrorKind
from ..core.trace import Trace
from ..llm import LLMClient
from .scoring import EvalContext, Score, Scorer, apply, scorer_from_spec
from .store import EvalStore, default_eval_store


class EvalResult(BaseModel):
    """One eval, run. Serialisable, and the unit the store holds."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    agent: str = ""
    prompt: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    output: str = ""
    score: Score = Field(default_factory=Score)
    ok: bool = True
    error: str | None = None
    error_kind: ErrorKind | None = None
    bailed_out: bool = False
    cost_usd: float = 0.0
    duration_s: float = 0.0
    run_id: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)
    trace: Trace | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)

    @property
    def passed(self) -> bool:
        """The verdict, falling back to the number when there is no verdict."""
        return bool(self.score)

    def row(self) -> dict[str, Any]:
        """A flat line for a table — no trace, no nesting."""
        return {
            "id": self.id,
            "name": self.name,
            "agent": self.agent,
            "score": round(self.score.value, 4),
            "passed": self.score.passed,
            "ok": self.ok,
            "error_kind": self.error_kind.value if self.error_kind else None,
            "bailed_out": self.bailed_out,
            "cost_usd": round(self.cost_usd, 6),
            "duration_s": round(self.duration_s, 2),
            "notes": self.score.notes[:200],
        }


class SuiteResult(BaseModel):
    """A suite, run: every result plus the aggregate over them."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    results: list[EvalResult] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)

    @property
    def mean_score(self) -> float:
        return sum(r.score.value for r in self.results) / len(self.results) if self.results else 0.0

    @property
    def cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.results)

    def aggregate(self) -> dict[str, Any]:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.score.passed is True)
        by_kind: dict[str, int] = {}
        for result in self.results:
            if result.error_kind:
                key = result.error_kind.value
                by_kind[key] = by_kind.get(key, 0) + 1
        return {
            "id": self.id,
            "name": self.name,
            "evals": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / total, 4) if total else 0.0,
            "mean_score": round(self.mean_score, 4),
            "cost_usd": round(self.cost_usd, 6),
            "bailed_out": sum(1 for r in self.results if r.bailed_out),
            "errors_by_kind": by_kind,
        }

    def table(self) -> str:
        """The suite as text, one line per eval. For a terminal."""
        lines = [f"{'eval':22s} {'agent':22s} {'score':>6s} {'cost':>8s}  notes"]
        for result in self.results:
            lines.append(
                f"{result.name[:22]:22s} {result.agent[:22]:22s} "
                f"{result.score.value:6.2f} {result.cost_usd:8.4f}  {result.score.notes[:60]}"
            )
        agg = self.aggregate()
        lines.append(
            f"\n{agg['evals']} evals, {agg['passed']} passed, mean {agg['mean_score']:.2f}, "
            f"${agg['cost_usd']:.4f}"
        )
        return "\n".join(lines)


class Eval:
    """An agent, a prompt, and a scoring function.

    ``scorer`` is resolved in the constructor — see the module docstring — so
    a bad scorer name raises here rather than after the agent has spent money.
    """

    def __init__(
        self,
        agent: Agent,
        prompt: str,
        scorer: Any,
        *,
        name: str = "",
        expected: Any = None,
        inputs: dict[str, Any] | None = None,
        max_usd: float | None = None,
        max_spend_mode: bool | None = None,
        store: EvalStore | None = None,
        keep_trace: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.agent = agent
        self.prompt = prompt
        self.scorer: Scorer = scorer_from_spec(scorer)
        self.name = name or f"{agent.name}:{prompt[:40]}"
        self.expected = expected
        self.inputs = dict(inputs or {})
        self.max_usd = max_usd
        self.max_spend_mode = max_spend_mode
        self.store = store
        self.keep_trace = keep_trace
        self.metadata = dict(metadata or {})

    async def run(
        self,
        *,
        llm: LLMClient | None = None,
        store: EvalStore | None = None,
        save: bool = True,
    ) -> EvalResult:
        """Run the agent on the prompt, score the text, store the result.

        Never raises for an agent failure: a run that blew its budget or hit a
        dead provider is a *data point*, and the score records what actually
        happened. Only a genuinely broken eval — an unusable scorer — raises,
        and that happens in the constructor.
        """
        agent = self._agent_for_run()
        owned = llm is None
        client = llm or LLMClient(
            config=agent.config.to_llm_config(), rate_limit=agent.config.rate_limit()
        )
        try:
            result = await agent.run(self.prompt, llm=client, **self.inputs)
            score = await apply(
                self.scorer,
                result.text,
                EvalContext(
                    prompt=self.prompt,
                    result=result,
                    agent=agent,
                    llm=client,
                    expected=self.expected,
                    metadata=self.metadata,
                ),
            )
        finally:
            if owned:
                await client.close()

        record = EvalResult(
            name=self.name,
            agent=self.agent.name,
            prompt=self.prompt,
            inputs=self.inputs,
            output=result.text,
            score=score,
            ok=result.ok,
            error=result.error,
            error_kind=result.error_kind,
            bailed_out=result.bailed_out,
            cost_usd=result.cost_usd,
            duration_s=result.trace.duration_s,
            run_id=result.run_id,
            summary=result.summary(),
            trace=result.trace if self.keep_trace else None,
            metadata=self.metadata,
        )
        if save:
            await (store or self.store or default_eval_store()).save(record)
        return record

    def _agent_for_run(self) -> Agent:
        """The agent, with this eval's ceiling applied if it set one.

        A copy rather than a mutation: the same ``Agent`` object is usually
        shared by a whole suite, and an eval that quietly rewrote its ceiling
        would change every eval after it.
        """
        overrides: dict[str, Any] = {}
        if self.max_usd is not None:
            overrides["max_usd"] = self.max_usd
        if self.max_spend_mode is not None:
            overrides["max_spend_mode"] = self.max_spend_mode
        if not overrides:
            return self.agent
        return Agent(
            name=self.agent.name,
            context=self.agent.context,
            plan=self.agent.plan,
            tools=self.agent.tools,
            config=self.agent.config.model_copy(update=overrides),
            description=self.agent.description,
        )

    def __repr__(self) -> str:
        return f"Eval(name={self.name!r}, agent={self.agent.name!r})"


class EvalSuite:
    """Several evals, run together and aggregated.

    Concurrent against one shared client on purpose — the same reason
    ``Agent.run_many`` shares one: a suite of thirty evals should respect one
    rate limit and pay for a repeated search once, not thirty times.
    """

    def __init__(
        self,
        evals: list[Eval],
        *,
        name: str = "",
        store: EvalStore | None = None,
        concurrency: int = 4,
    ) -> None:
        self.evals = list(evals)
        self.name = name or "suite"
        self.store = store
        self.concurrency = max(1, concurrency)

    @classmethod
    def over(
        cls,
        agents: list[Agent],
        cases: list[tuple[str, Any]],
        *,
        name: str = "",
        **eval_kwargs: Any,
    ) -> "EvalSuite":
        """Every agent against every ``(prompt, scorer)`` case.

        The comparison the arena is for, minus the votes: same prompts, same
        scorers, one row per agent.
        """
        return cls(
            [
                Eval(agent, prompt, scorer, name=f"{agent.name}:{index}", **eval_kwargs)
                for index, (prompt, scorer) in enumerate(cases)
                for agent in agents
            ],
            name=name,
        )

    async def run(
        self,
        *,
        llm: LLMClient | None = None,
        store: EvalStore | None = None,
        save: bool = True,
    ) -> SuiteResult:
        owned = llm is None
        client = llm or LLMClient()
        gate = asyncio.Semaphore(self.concurrency)

        async def one(ev: Eval) -> EvalResult:
            async with gate:
                # save=False here: the suite saves the whole set at the end, so
                # a half-finished suite does not leave half its rows behind.
                return await ev.run(llm=client, save=False)

        try:
            results = await asyncio.gather(*(one(ev) for ev in self.evals))
        finally:
            if owned:
                await client.close()

        suite = SuiteResult(name=self.name, results=list(results))
        if save:
            await (store or self.store or default_eval_store()).save_suite(suite)
        return suite
