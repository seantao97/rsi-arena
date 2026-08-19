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

from rsi_arena import Eval, EvalSuite
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


def _render(result: Any, include_trace: bool) -> dict[str, Any]:
    payload = result.model_dump(exclude={"trace"})
    if include_trace and result.trace is not None:
        payload["trace"] = result.trace.model_dump()
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
        req.prompt,
        scorer,
        name=req.name or f"{req.agent}:{req.prompt[:40]}",
        expected=req.expected,
        inputs=req.inputs,
        # The catalogue id and the agent's own name differ ("plugin" is
        # "researcher-plugin"), and a stored result should be traceable back to
        # the request that made it, not only to the harness that ran.
        metadata={**req.metadata, "agent_id": req.agent},
        keep_trace=True,
        store=state.evals,
    )
    result = await ev.run(llm=state.llm, save=req.save)
    return _render(result, req.include_trace)


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
    evals = [
        Eval(
            build(agent_id, limits.config(), state.api),
            case.prompt,
            scorer,
            name=case.name or f"{agent_id}:{index}",
            expected=case.expected,
            metadata={"agent_id": agent_id},
            keep_trace=req.include_trace,
            store=state.evals,
        )
        for agent_id in req.agents
        for index, (case, scorer) in enumerate(zip(req.cases, scorers_by_case))
    ]
    suite = EvalSuite(evals, name=req.name or "suite", concurrency=req.concurrency,
                      store=state.evals)
    result = await suite.run(llm=state.llm, save=req.save)
    return {
        **result.aggregate(),
        "results": [_render(one, req.include_trace) for one in result.results],
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


@router.get("/evals/suites")
async def list_suites(
    limit: int = Query(default=25, ge=1, le=200), offset: int = Query(default=0, ge=0)
) -> list[dict[str, Any]]:
    return [s.aggregate() for s in await state.evals.list_suites(limit=limit, offset=offset)]


@router.get("/evals/suites/{suite_id}")
async def get_suite(suite_id: str) -> dict[str, Any]:
    suite = await state.evals.get_suite(suite_id)
    if suite is None:
        raise HTTPException(404, f"unknown suite {suite_id!r}")
    return {**suite.aggregate(), "results": [one.row() for one in suite.results]}


@router.get("/evals/{eval_id}")
async def get_eval(eval_id: str, include_trace: bool = False) -> dict[str, Any]:
    result = await state.evals.get(eval_id)
    if result is None:
        raise HTTPException(404, f"unknown eval {eval_id!r}")
    return _render(result, include_trace)


@router.delete("/evals/{eval_id}")
async def delete_eval(eval_id: str) -> dict[str, Any]:
    if not await state.evals.delete(eval_id):
        raise HTTPException(404, f"unknown eval {eval_id!r}")
    return {"deleted": eval_id}
