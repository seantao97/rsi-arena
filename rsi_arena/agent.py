"""The agent.

An agent is exactly the three things the README says it is:

    agent = LLM + primitives (tools) + orchestration prompt/plan

* **context** — the overarching prompt. Every prompt step inherits it as its
  system message unless it overrides it.
* **plan** — the ordered steps, including loops.
* **tools** — the primitives it may call.
* **config** — model choice and the operating limits, all in one place so a
  mutation can change ``default_model`` or ``max_loops`` without touching code.

Running one produces an :class:`AgentResult`: the answer, the final state, the
full trace and the cost ledger — one JSON object holding everything the arena
needs to show a voter and everything the optimizer needs to rewrite the
harness.

Agents serialise. ``to_dict`` writes the context, the plan and the *names* of
the tools; ``from_dict`` binds those names against a toolbox you supply. Tools
are deliberately not serialised: they are the human-supplied primitive set, so
an agent may reference one but never define one.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Iterable, Sequence

from pydantic import BaseModel, Field

from .cache import Cache
from .costs import BudgetExceeded, CostTracker
from .llm import LLMClient, LLMConfig, WebSearch
from .ratelimit import RateLimit
from .steps import Plan, StepContext
from .tools import Tool, Toolbox
from .trace import Trace, Tracer


class AgentConfig(BaseModel):
    """Model choice and operating limits.

    The defaults are the arena's shared ceiling from the README (``R2``/``R5``:
    $2.00 and a call cap per answer), because an agent that can spend without
    limit is not comparable to one that cannot.
    """

    default_model: str = "anthropic/claude-sonnet-4.5"
    fallback_models: list[str] = Field(default_factory=list)
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    seed: int | None = None
    reasoning: dict[str, Any] | None = None
    provider: dict[str, Any] | None = None

    timeout_s: float = 120.0
    cache: bool = True
    cache_ttl_s: float | None = None
    web_search: bool | WebSearch = False

    max_usd: float | None = 2.00
    max_calls: int | None = 200
    requests_per_second: float = 8.0
    concurrency: int = 8

    def to_llm_config(self) -> LLMConfig:
        return LLMConfig(
            model=self.default_model,
            fallback_models=self.fallback_models,
            temperature=self.temperature,
            top_p=self.top_p,
            max_tokens=self.max_tokens,
            seed=self.seed,
            reasoning=self.reasoning,
            provider=self.provider,
            timeout_s=self.timeout_s,
            cache=self.cache,
            cache_ttl_s=self.cache_ttl_s,
            web_search=self.web_search,
        )

    def rate_limit(self) -> RateLimit:
        return RateLimit(
            per_second=self.requests_per_second,
            burst=max(1.0, self.requests_per_second * 2),
            concurrency=self.concurrency,
        )


class AgentResult(BaseModel):
    """One run, whole. Serialisable, and the unit the arena stores."""

    agent: str
    run_id: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    state: dict[str, Any] = Field(default_factory=dict)
    trace: Trace
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def cost_usd(self) -> float:
        return self.trace.costs.total_usd

    def summary(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "run_id": self.run_id,
            "ok": self.ok,
            "error": self.error,
            "duration_s": round(self.trace.duration_s, 2),
            **self.trace.costs.summary(),
        }


class Agent:
    """LLM + primitives + orchestration."""

    def __init__(
        self,
        name: str,
        context: str,
        plan: Plan | Sequence[Any],
        tools: Toolbox | Iterable[Tool] | None = None,
        config: AgentConfig | None = None,
        *,
        description: str = "",
    ) -> None:
        self.name = name
        self.context = context
        self.plan = plan if isinstance(plan, Plan) else Plan(steps=list(plan))
        self.tools = tools if isinstance(tools, Toolbox) else Toolbox(list(tools or []))
        self.config = config or AgentConfig()
        self.description = description

    # -- running --

    async def run(
        self,
        question: str | None = None,
        *,
        llm: LLMClient | None = None,
        cache: Cache | None = None,
        raise_on_error: bool = False,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        on_token: Callable[[str], None] | None = None,
        label: str | None = None,
        **inputs: Any,
    ) -> AgentResult:
        """Execute the plan once.

        Errors are captured rather than raised by default: a run that blew its
        budget or hit a dead provider still produced a partial trace, and in
        the arena that trace is evidence. Pass ``raise_on_error=True`` when
        you would rather debug than record.
        """
        owned = llm is None
        client = llm or LLMClient(
            config=self.config.to_llm_config(),
            cache=cache,
            rate_limit=self.config.rate_limit(),
        )
        costs = CostTracker(max_usd=self.config.max_usd, max_calls=self.config.max_calls)
        display = label or self.name
        tracer = Tracer(agent=display, costs=costs, on_event=on_event)
        state: dict[str, Any] = {"question": question or "", **inputs}
        tracer.root.set_input(state)
        ctx = StepContext(
            llm=client,
            tools=self.tools,
            tracer=tracer,
            config=self.config.to_llm_config(),
            context=self.context,
            state=state,
            on_token=on_token,
        )

        output: Any = None
        error: BaseException | None = None
        try:
            output = await self.plan.execute(ctx)
        except BudgetExceeded as exc:
            error = exc
            output = ctx.state.get("last")
        except Exception as exc:  # noqa: BLE001 - recorded on the trace
            error = exc
            if raise_on_error:
                tracer.finish(error=exc)
                raise
        finally:
            trace = tracer.finish(output=output, error=error)
            if owned:
                await client.close()

        return AgentResult(
            agent=display,
            run_id=trace.run_id,
            inputs=state_inputs(state, question),
            output=output,
            state=public_state(ctx.state),
            trace=trace,
            error=f"{type(error).__name__}: {error}" if error else None,
        )

    async def run_many(
        self, questions: Iterable[str], *, llm: LLMClient | None = None, **inputs: Any
    ) -> list[AgentResult]:
        """Run the same agent over many questions concurrently.

        Sharing one client is the point: its rate limiter and cache are shared
        too, so a hundred questions respect one budget instead of a hundred.
        """
        owned = llm is None
        client = llm or LLMClient(
            config=self.config.to_llm_config(), rate_limit=self.config.rate_limit()
        )
        try:
            return await asyncio.gather(
                *(self.run(q, llm=client, **inputs) for q in questions)
            )
        finally:
            if owned:
                await client.close()

    # -- serialisation --

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "context": self.context,
            "config": self.config.model_dump(),
            "tools": self.tools.names(),
            "plan": self.plan.model_dump(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], tools: Toolbox | None = None) -> "Agent":
        """Rebuild an agent, binding tool names against ``tools``.

        Unknown names raise here rather than at the first call, so a bad
        mutation fails when it is loaded instead of halfway through a battle.
        """
        available = tools or Toolbox()
        selected = Toolbox([available.get(n) for n in data.get("tools", [])])
        return cls(
            name=data["name"],
            context=data.get("context", ""),
            plan=Plan.model_validate(data.get("plan", {"steps": []})),
            tools=selected,
            config=AgentConfig.model_validate(data.get("config", {})),
            description=data.get("description", ""),
        )

    def outline(self) -> str:
        return (
            f"{self.name} [{self.config.default_model}]\n"
            f"tools: {', '.join(self.tools.names()) or 'none'}\n"
            f"{self.plan.outline()}"
        )

    def __repr__(self) -> str:
        return f"Agent(name={self.name!r}, steps={len(self.plan)}, tools={len(self.tools)})"


def state_inputs(state: dict[str, Any], question: str | None) -> dict[str, Any]:
    inputs = {k: v for k, v in state.items() if k != "question"}
    return {"question": question or "", **{k: v for k, v in inputs.items() if _small(v)}}


def public_state(state: dict[str, Any]) -> dict[str, Any]:
    """State minus the loop bookkeeping, which is noise once the run is over."""
    hidden = {"loop_index", "loop_iteration", "loop_results"}
    return {k: v for k, v in state.items() if k not in hidden}


def _small(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool, type(None))) and len(str(value)) < 500
