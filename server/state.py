"""Process-wide singletons, and the helpers every route needs.

Lives apart from ``app.py`` so the routers can reach the shared clients without
importing the app that mounts them. One :class:`~rsi_arena.LLMClient`, one
:class:`~rsi_arena.APIClient` and one eval store serve every request — the
point rather than an optimisation, since their rate limiter and cache are
shared and two agents in a battle therefore cannot outspend each other on
retries.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException

from rsi_arena import APIClient, AgentConfig, EvalStore, LLMClient

from .catalogue import BUILDERS, REQUIRES
from .store import Store


class State:
    """Created on startup, closed on shutdown. See ``app.lifespan``."""

    llm: LLMClient
    api: APIClient
    store: Store
    evals: EvalStore
    battles: dict[str, dict[str, Any]]


state = State()


class Limits:
    """The knobs a request may set on an agent, in one place.

    Every route that runs an agent accepts the same four, so they are parsed
    once here rather than four times with three subtly different defaults.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_usd: float = 2.00,
        cache: bool = True,
        max_spend_mode: bool = False,
        bailout_reserve_usd: float | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_usd = max_usd
        self.cache = cache
        self.max_spend_mode = max_spend_mode
        self.bailout_reserve_usd = bailout_reserve_usd

    def config(self) -> AgentConfig:
        overrides: dict[str, Any] = {
            "max_usd": self.max_usd,
            "cache": self.cache,
            "max_spend_mode": self.max_spend_mode,
        }
        if self.model:
            overrides["default_model"] = self.model
        if self.temperature is not None:
            overrides["temperature"] = self.temperature
        if self.bailout_reserve_usd is not None:
            overrides["bailout_reserve_usd"] = self.bailout_reserve_usd
        return AgentConfig(**{**AgentConfig().model_dump(), **overrides})


def missing_keys(agent_id: str) -> list[str]:
    return [k for k in REQUIRES.get(agent_id, []) if not os.environ.get(k)]


def check(agent_id: str) -> None:
    """404 for an agent that does not exist, 400 for one that cannot run.

    The distinction matters to the UI: the first is a bug in the request, the
    second is a missing key it should tell the user about by name rather than
    letting them find out as a 401 twenty seconds into a run.
    """
    if agent_id not in BUILDERS:
        raise HTTPException(404, f"unknown agent {agent_id!r}")
    absent = missing_keys(agent_id)
    if absent:
        raise HTTPException(400, f"{agent_id} needs {', '.join(absent)} in the environment")
