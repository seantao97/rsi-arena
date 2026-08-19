"""FastAPI app. Runs on port 3600; the web app on 8050 is its only client.

Routes:

======================  =========================================================
``GET  /api/health``    Which keys are present, so the UI can say so up front
``GET  /api/agents``    The catalogue, with each agent's plan and requirements
``GET  /api/models``    OpenRouter's model list, structured-output capable first
``POST /api/run``       One agent. **SSE.**
``POST /api/battle``    Two agents concurrently, blind by default. **SSE.**
``POST /api/vote``      Record a vote and reveal who was who
``GET  /api/leaderboard`` Win/loss counts per agent
======================  =========================================================

Both streaming routes are POST, so the browser uses ``fetch`` and reads the
body rather than ``EventSource`` (which is GET-only). The event format is
identical either way.

One :class:`LLMClient` is shared by every request. That is the point rather
than an optimisation: its rate limiter and cache are shared too, so two agents
in a battle cannot outspend each other on retries, and a repeated search is
paid for once.
"""

from __future__ import annotations

import asyncio
import os
import random
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from rsi_arena import Agent, AgentConfig, APIClient, LLMClient, MemoryCache

from .catalogue import BUILDERS, REQUIRES, build, catalogue
from .events import RunStream
from .store import Store

WEB_ORIGINS = [
    "http://localhost:8050", "http://127.0.0.1:8050",
    os.environ.get("RSI_ARENA_WEB_ORIGIN", "http://localhost:8050"),
]

# Shown when there is no key to fetch the live list with, and used to order it
# when there is. Everything here is known to support tool calls and json_schema.
SUGGESTED_MODELS = [
    "anthropic/claude-sonnet-4.5",
    "anthropic/claude-opus-4.1",
    "openai/gpt-5.2",
    "openai/gpt-5-mini",
    "google/gemini-2.5-pro",
    "google/gemini-2.5-flash",
    "meta-llama/llama-4-maverick",
    "deepseek/deepseek-chat-v3.1",
]


class RunRequest(BaseModel):
    agent: str
    question: str
    model: str | None = None
    temperature: float | None = None
    max_usd: float = Field(default=2.00, ge=0.001, le=20.0)
    cache: bool = True


class BattleRequest(BaseModel):
    agent_a: str
    agent_b: str
    question: str
    model: str | None = None
    temperature: float | None = None
    max_usd: float = Field(default=2.00, ge=0.001, le=20.0)
    cache: bool = True
    blind: bool = True
    shuffle: bool = True


class VoteRequest(BaseModel):
    battle_id: str
    winner: Literal["a", "b", "tie", "both_bad"]
    reason: str = ""


class State:
    """Process-wide singletons, created on startup and closed on shutdown."""

    llm: LLMClient
    api: APIClient
    store: Store
    battles: dict[str, dict[str, Any]]


state = State()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    cache = MemoryCache(max_entries=8192)
    state.llm = LLMClient(cache=cache)
    state.api = APIClient(cache=cache)
    state.store = Store()
    state.battles = {}
    try:
        yield
    finally:
        await state.llm.close()
        await state.api.close()


app = FastAPI(title="RSI Arena", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(set(WEB_ORIGINS)),
    allow_methods=["*"],
    allow_headers=["*"],
)

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # Nginx buffers text/event-stream by default, which turns a live trace
    # into one burst at the end. Harmless when nothing is proxying.
    "X-Accel-Buffering": "no",
}


def _config(req: RunRequest | BattleRequest) -> AgentConfig:
    overrides: dict[str, Any] = {"max_usd": req.max_usd, "cache": req.cache}
    if req.model:
        overrides["default_model"] = req.model
    if req.temperature is not None:
        overrides["temperature"] = req.temperature
    return AgentConfig(**{**AgentConfig().model_dump(), **overrides})


def _missing_keys(agent_id: str) -> list[str]:
    return [k for k in REQUIRES.get(agent_id, []) if not os.environ.get(k)]


def _check(agent_id: str) -> None:
    if agent_id not in BUILDERS:
        raise HTTPException(404, f"unknown agent {agent_id!r}")
    missing = _missing_keys(agent_id)
    if missing:
        raise HTTPException(400, f"{agent_id} needs {', '.join(missing)} in the environment")


async def _run_agent(
    agent: Agent, question: str, stream: RunStream, side: str, display: str | None = None
) -> dict[str, Any]:
    """Run one agent, streaming its events, and return the ``run_end`` payload.

    ``display`` replaces the agent's name everywhere the client can see it.
    Blinding has to happen here rather than in the UI: a name the browser
    received is a name a voter can read out of devtools, and then the arena is
    measuring brand recognition instead of the harness.
    """
    on_event, on_token = stream.hooks(side)
    label = display or agent.name
    stream.queue.put_nowait({"type": "run_start", "side": side, "agent": label,
                             "steps": len(agent.plan)})
    result = await agent.run(
        question, llm=state.llm, on_event=on_event, on_token=on_token, label=label
    )
    return {
        "ok": result.ok,
        "error": result.error,
        "output": result.output,
        "summary": result.summary(),
        "trace": result.trace.model_dump(),
        "citations": result.state.get("citations", []),
    }


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "version": app.version,
        "keys": {
            "OPENROUTER_API_KEY": bool(os.environ.get("OPENROUTER_API_KEY")),
            "SEARCHAPI_API_KEY": bool(os.environ.get("SEARCHAPI_API_KEY")),
        },
    }


@app.get("/api/agents")
async def agents() -> list[dict[str, Any]]:
    return [{**entry, "missing_keys": _missing_keys(entry["id"])} for entry in catalogue()]


@app.get("/api/models")
async def models() -> list[dict[str, Any]]:
    """Live catalogue when reachable, the suggested shortlist when not."""
    try:
        data = await state.llm.list_models()
    except Exception:  # noqa: BLE001 - offline is a normal state here
        return [{"id": m, "name": m, "suggested": True} for m in SUGGESTED_MODELS]
    usable = [
        {
            "id": m["id"],
            "name": m.get("name") or m["id"],
            "context_length": m.get("context_length"),
            "pricing": m.get("pricing"),
            "suggested": m["id"] in SUGGESTED_MODELS,
            # Structured-output steps set provider.require_parameters, so a
            # model without this support routes nowhere and 503s.
            "structured_outputs": "structured_outputs" in (m.get("supported_parameters") or []),
        }
        for m in data
    ]
    usable.sort(key=lambda m: (not m["suggested"], not m["structured_outputs"], m["id"]))
    return usable


@app.post("/api/run")
async def run(req: RunRequest) -> StreamingResponse:
    _check(req.agent)
    agent = build(req.agent, _config(req), state.api)
    stream = RunStream()
    stream.add("a", lambda: _run_agent(agent, req.question, stream, "a"))
    return StreamingResponse(stream.sse(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/battle")
async def battle(req: BattleRequest) -> StreamingResponse:
    for agent_id in (req.agent_a, req.agent_b):
        _check(agent_id)

    left, right = req.agent_a, req.agent_b
    # Sides are randomised so a voter's position bias does not always land on
    # the same agent. The UI never learns which is which until the vote.
    if req.shuffle and random.random() < 0.5:
        left, right = right, left

    config = _config(req)
    agent_a = build(left, config, state.api)
    agent_b = build(right, config, state.api)
    battle_id = state.store.open_battle(
        req.question, left, right, config.default_model, req.blind
    )
    state.battles[battle_id] = {"a": left, "b": right, "revealed": not req.blind}

    stream = RunStream()
    results: dict[str, Any] = {}

    def side(agent: Agent, agent_id: str, label: str):
        display = f"Agent {label.upper()}" if req.blind else None

        async def go() -> dict[str, Any]:
            payload = await _run_agent(agent, req.question, stream, label, display)
            results[label] = payload["summary"]
            if len(results) == 2:
                state.store.close_battle(battle_id, results)
            # Identity is withheld in a blind battle until a vote is cast.
            return {**payload, "agent_id": None if req.blind else agent_id}

        return go

    stream.queue.put_nowait({"type": "battle_start", "side": "a", "battle_id": battle_id,
                             "blind": req.blind})
    stream.add("a", side(agent_a, left, "a"))
    stream.add("b", side(agent_b, right, "b"))
    return StreamingResponse(stream.sse(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/vote")
async def vote(req: VoteRequest) -> dict[str, Any]:
    pairing = state.battles.get(req.battle_id) or state.store.battle(req.battle_id)
    if pairing is None:
        raise HTTPException(404, f"unknown battle {req.battle_id!r}")
    vote_id = state.store.record_vote(req.battle_id, req.winner, req.reason)
    reveal = (
        {"a": pairing["a"], "b": pairing["b"]}
        if "a" in pairing
        else {"a": pairing["agent_a"], "b": pairing["agent_b"]}
    )
    return {"vote_id": vote_id, "reveal": reveal, "leaderboard": state.store.tally()}


@app.get("/api/leaderboard")
async def leaderboard() -> list[dict[str, Any]]:
    return state.store.tally()
