"""Every agent in the catalogue, callable over HTTP.

``POST /api/run`` streams, which is right for a UI watching a trace appear and
wrong for everything else — a script, a cron job, another service, an eval
harness. These routes are the plain request/response half: send JSON, wait,
get the answer, the ledger and (if you ask) the trace.

======================================  ====================================
``GET  /api/agents``                    the catalogue
``GET  /api/agents/{id}``               one agent: context, plan, tools, config
``POST /api/agents/{id}/run``           run it, JSON in and JSON out
``POST /api/agents/{id}/answer``        answer now from state, no plan
======================================  ====================================

The last one is :meth:`~rsi_arena.Agent.immediate_answer` exposed directly. It
is what ``max_spend_mode`` calls internally when a run hits its ceiling, and
it is useful on its own: hand back the state from a run that stopped, and get
the best answer that state supports for one model call.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .catalogue import build, catalogue, describe
from .state import Limits, check, missing_keys, state

router = APIRouter(prefix="/api/agents", tags=["agents"])


class AgentRunRequest(BaseModel):
    """One non-streaming run."""

    question: str
    inputs: dict[str, Any] = Field(
        default_factory=dict, description="Extra run state, readable as {{key}} in the plan."
    )
    model: str | None = None
    temperature: float | None = None
    max_usd: float = Field(default=2.00, ge=0.001, le=20.0)
    cache: bool = True
    max_spend_mode: bool = Field(
        default=False,
        description="At the ceiling, answer from state instead of returning nothing. "
                    "The response still reports error_kind='max_spend' and bailed_out=true.",
    )
    bailout_reserve_usd: float | None = Field(
        default=None, ge=0.0, le=5.0,
        description="What the bail-out call may spend on top of max_usd. Default: 5% of it.",
    )
    include_trace: bool = Field(
        default=False, description="Include the full span tree. Large; off by default."
    )


class AnswerRequest(BaseModel):
    """One bail-out call: state in, answer out, no plan run."""

    state: dict[str, Any] = Field(default_factory=dict)
    question: str = ""
    reason: str = Field(default="", description="Why an answer is needed now. Shown to the model.")
    model: str | None = None
    max_tokens: int | None = Field(default=None, ge=16, le=8000)


@router.get("")
async def list_agents() -> list[dict[str, Any]]:
    return [{**entry, "missing_keys": missing_keys(entry["id"])} for entry in catalogue()]


@router.get("/{agent_id}")
async def get_agent(agent_id: str) -> dict[str, Any]:
    try:
        entry = describe(agent_id)
    except KeyError:
        raise HTTPException(404, f"unknown agent {agent_id!r}") from None
    return {**entry, "missing_keys": missing_keys(agent_id)}


@router.post("/{agent_id}/run")
async def run_agent(agent_id: str, req: AgentRunRequest) -> dict[str, Any]:
    """Run one agent to completion and return the result as JSON.

    A failed run is a 200 with ``ok: false``, not a 500. The run happened, it
    cost money, and its partial trace is evidence — throwing that away because
    the agent gave up would lose exactly the data the arena is collecting.
    Only an unrunnable *request* is an error status.
    """
    check(agent_id)
    limits = Limits(
        model=req.model,
        temperature=req.temperature,
        max_usd=req.max_usd,
        cache=req.cache,
        max_spend_mode=req.max_spend_mode,
        bailout_reserve_usd=req.bailout_reserve_usd,
    )
    agent = build(agent_id, limits.config(), state.api)
    result = await agent.run(req.question, llm=state.llm, **req.inputs)
    payload = {
        "agent_id": agent_id,
        "ok": result.ok,
        "error": result.error,
        "error_kind": result.error_kind.value if result.error_kind else None,
        "bailed_out": result.bailed_out,
        "output": result.output,
        "text": result.text,
        "summary": result.summary(),
        "state": result.state,
        "citations": result.state.get("citations", []),
    }
    if req.include_trace:
        payload["trace"] = result.trace.model_dump()
    return payload


@router.post("/{agent_id}/answer")
async def answer_now(agent_id: str, req: AnswerRequest) -> dict[str, Any]:
    """One model call: the state you hand it, and the best answer it supports."""
    check(agent_id)
    agent = build(agent_id, Limits(model=req.model).config(), state.api)
    text = await agent.immediate_answer(
        req.state,
        question=req.question,
        llm=state.llm,
        reason=req.reason,
        model=req.model,
        max_tokens=req.max_tokens,
    )
    return {"agent_id": agent_id, "text": text}
