"""The eval endpoint: run an agent on a prompt, score it, keep the result.

A battle asks a human which answer is better. An eval asks a function whether
one answer is good — which is what a script, a cron job or an optimizer can
run without anybody watching.

======================================  ====================================
``GET  /api/scorers``                   the scorers a spec may name
``POST /api/evals``                     run one eval
``POST /api/evals/suite``               run every case against every agent
``GET  /api/evals``                     stored results, newest first
``GET  /api/evals/leaderboard``         mean score and pass rate per agent
``GET  /api/evals/suites``              stored suite runs
``GET  /api/evals/{id}``                one result
``DELETE /api/evals/{id}``              drop one result
======================================  ====================================

A scorer cannot be sent over HTTP as a function, so it arrives as data:
``"non_empty"``, ``{"type": "contains", "value": "unchanged"}``, or a list of
those, which is scored as a conjunction. ``GET /api/scorers`` lists what is
registered, and :func:`rsi_arena.evals.register_scorer` adds to it — a scorer
registered in Python is immediately selectable here.

Route order matters: ``/leaderboard`` and ``/suites`` are declared before
``/{eval_id}``, or they would be read as ids.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

import asyncio

from rsi_arena import Eval, EvalOutput, scored_by
from rsi_arena.evals import SCORERS, scorer_from_spec

from .catalogue import build
from .state import Limits, check, state

router = APIRouter(prefix="/api", tags=["evals"])

MAX_SUITE_EVALS = 60


class EvalRequest(BaseModel):
    """One agent, one prompt, one scorer."""

    agent: str
    prompt: str
    scorer: Any = Field(
        description='A scorer name, a {"type": ...} spec, or a list of either.'
    )
    name: str = ""
    expected: Any = Field(
        default=None, description="Reference answer, passed to scorers that can use one."
    )
    inputs: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model: str | None = None
    temperature: float | None = None
    max_usd: float = Field(default=2.00, ge=0.001, le=20.0)
    cache: bool = True
    max_spend_mode: bool = Field(
        default=False,
        description="Cut the agent off at max_usd and score its bail-out answer instead of "
                    "scoring nothing. The result carries error_kind='max_spend'.",
    )
    bailout_reserve_usd: float | None = Field(default=None, ge=0.0, le=5.0)

    save: bool = True
    include_trace: bool = False


class EvalCase(BaseModel):
    prompt: str
    scorer: Any
    name: str = ""
    expected: Any = None


class SuiteRequest(BaseModel):
    """Every case against every agent — the arena comparison, minus the votes."""

    agents: list[str] = Field(min_length=1)
    cases: list[EvalCase] = Field(min_length=1)
    name: str = ""

    model: str | None = None
    temperature: float | None = None
    max_usd: float = Field(default=2.00, ge=0.001, le=20.0)
    cache: bool = True
    max_spend_mode: bool = False
    bailout_reserve_usd: float | None = Field(default=None, ge=0.0, le=5.0)

    concurrency: int = Field(default=4, ge=1, le=16)
    save: bool = True
    include_trace: bool = False


def _limits(req: EvalRequest | SuiteRequest) -> Limits:
    return Limits(
        model=req.model,
        temperature=req.temperature,
        max_usd=req.max_usd,
        cache=req.cache,
        max_spend_mode=req.max_spend_mode,
        bailout_reserve_usd=req.bailout_reserve_usd,
    )


def _scorer(spec: Any) -> Any:
    """Resolve a scorer spec, turning a bad one into a 400 rather than a 500.

    Worth doing up front: an unresolvable scorer discovered *after* the agent
    has run is a bill for a result nobody can score.
    """
    try:
        return scorer_from_spec(spec)
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(400, f"bad scorer: {exc}") from None


def _render(ev: Eval, out: Any, include_trace: bool,
            eval_id: str = "") -> dict[str, Any]:
    """One eval, as the API returns it.

    The verdict and the run are separate objects now — ``EvalOutput`` is the
    score and why, and everything about the run that produced it (cost, errors,
    the trace) is on the agent result. The response flattens the two, because a
    caller asking "how did it do" wants both.
    """
    run = ev.agent_output
    payload = {
        "id": eval_id,
        "name": ev.description,
        "agent": ev.agent.name,
        "prompt": ev.input.get("question", ""),
        **out.model_dump(),
        "ok": bool(run and run.error is None),
        "error": getattr(run, "error", None),
        "error_kind": getattr(run, "error_kind", None),
        "bailed_out": bool(getattr(run, "bailed_out", False)),
        # Cost lives on the trace, which is where it is actually tracked;
        # the verdict has no business carrying a bill.
        "cost_usd": (run.trace.costs.total_usd
                     if run is not None and run.trace else 0.0),
    }
    if include_trace and run is not None and getattr(run, "trace", None):
        payload["trace"] = run.trace.model_dump()
    return payload


@router.get("/scorers")
async def scorers() -> list[dict[str, Any]]:
    """What a scorer spec may name, and what each one takes."""
    import inspect

    out = []
    for name, factory in sorted(SCORERS.items()):
        try:
            params = [
                {"name": p.name, "required": p.default is p.empty,
                 "default": None if p.default is p.empty else p.default}
                for p in inspect.signature(factory).parameters.values()
            ]
        except (TypeError, ValueError):
            params = []
        out.append({
            "type": name,
            "description": (inspect.getdoc(factory) or "").split("\n\n")[0],
            "params": params,
        })
    return out


@router.post("/evals")
async def run_eval(req: EvalRequest) -> dict[str, Any]:
    """Run one eval and return its result.

    An agent that fails is still a result, with ``ok: false`` and a score —
    that is the data point. Only a request that cannot be run is an error.
    """
    check(req.agent)
    scorer = _scorer(req.scorer)
    agent = build(req.agent, _limits(req).config(), state.api)
    ev = Eval(
        agent,
        scored_by(scorer, prompt=req.prompt, agent=agent, llm=state.llm,
                  expected=req.expected),
        description=req.name or f"{req.agent}:{req.prompt[:40]}",
        input={"question": req.prompt, **req.inputs},
    )
    out = await ev.run(llm=state.llm)
    # The catalogue id and the agent's own name differ ("plugin" is
    # "researcher-plugin"), and a stored result should be traceable back to the
    # request that made it, not only to the harness that ran.
    out.metadata.update({**req.metadata, "agent_id": req.agent})
    eval_id = ""
    if req.save:
        eval_id = await state.evals.save(out, agent=req.agent, name=ev.description)
    return _render(ev, out, req.include_trace, eval_id)


@router.post("/evals/suite")
async def run_suite(req: SuiteRequest) -> dict[str, Any]:
    """Run every case against every agent, concurrently, and aggregate."""
    for agent_id in req.agents:
        check(agent_id)
    total = len(req.agents) * len(req.cases)
    if total > MAX_SUITE_EVALS:
        raise HTTPException(
            400,
            f"{total} evals ({len(req.agents)} agents x {len(req.cases)} cases) is over the "
            f"limit of {MAX_SUITE_EVALS}; split it up",
        )

    limits = _limits(req)
    scorers_by_case = [_scorer(case.scorer) for case in req.cases]
    pairs = [
        (agent_id,
         Eval(build(agent_id, limits.config(), state.api),
              scored_by(scorer, prompt=case.prompt, llm=state.llm,
                        expected=case.expected),
              description=case.name or f"{agent_id}:{index}",
              input={"question": case.prompt}))
        for agent_id in req.agents
        for index, (case, scorer) in enumerate(zip(req.cases, scorers_by_case))
    ]

    # A suite is a gather over evals. It was a class; nothing it did needed to
    # be one, and a failed member has to become a result rather than take the
    # request down with it.
    limit = asyncio.Semaphore(max(1, req.concurrency))

    async def one(agent_id: str, ev: Eval) -> dict[str, Any]:
        async with limit:
            try:
                out = await ev.run(llm=state.llm)
            except Exception as exc:  # noqa: BLE001 — a failure is a data point
                out = EvalOutput(description=ev.description, score=0.0,
                                 comments=f"{type(exc).__name__}: {exc}")
            eval_id = ""
            if req.save:
                eval_id = await state.evals.save(out, agent=agent_id,
                                                 name=ev.description)
            return _render(ev, out, req.include_trace, eval_id)

    results = await asyncio.gather(*(one(a, e) for a, e in pairs))
    scores = [r["score"] for r in results]
    return {
        "name": req.name or "suite",
        "count": len(results),
        "mean_score": sum(scores) / len(scores) if scores else 0.0,
        "cost_usd": sum(r["cost_usd"] for r in results),
        "results": results,
    }


@router.get("/evals")
async def list_evals(
    agent: str | None = None,
    name: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    results = await state.evals.list(agent=agent, name=name, limit=limit, offset=offset)
    return {
        "total": await state.evals.count(agent=agent, name=name),
        "limit": limit,
        "offset": offset,
        "results": [one.row() for one in results],
    }


@router.get("/evals/leaderboard")
async def eval_leaderboard(name: str | None = None) -> list[dict[str, Any]]:
    """Mean score and pass rate per agent. Counts, not a rating."""
    return await state.evals.leaderboard(name=name)


@router.get("/evals/{eval_id}")
async def get_eval(eval_id: str, include_trace: bool = False) -> dict[str, Any]:
    record = await state.evals.get(eval_id)
    if record is None:
        raise HTTPException(404, f"unknown eval {eval_id!r}")
    # A stored result is the verdict only — the run behind it was not kept.
    return {**record.row(), **record.output.model_dump()}


@router.delete("/evals/{eval_id}")
async def delete_eval(eval_id: str) -> dict[str, Any]:
    if not await state.evals.delete(eval_id):
        raise HTTPException(404, f"unknown eval {eval_id!r}")
    return {"deleted": eval_id}
