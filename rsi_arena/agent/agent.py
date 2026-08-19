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

Every agent can also answer *now*, mid-plan, from whatever it has gathered so
far — :meth:`Agent.immediate_answer`. That is one model call with the run
state as its input, and it is what ``max_spend_mode`` reaches for when the
ceiling is hit: the difference between a run that cost $2 and produced nothing
and one that cost $2 and produced its best guess.
"""

from __future__ import annotations

import asyncio
import json
from enum import Enum
from typing import Any, Callable, Iterable, Sequence

import httpx
from pydantic import BaseModel, Field

from ..core.cache import Cache
from ..core.costs import BudgetExceeded, CostTracker, MaxSpendExceeded
from ..core.ratelimit import RateLimit
from ..core.trace import Trace, Tracer
from ..llm import LLMClient, LLMConfig, Message, OpenRouterError, WebSearch
from .steps import Plan, StepContext
from .tools import Tool, Toolbox


class ErrorKind(str, Enum):
    """Why a run stopped, coarse enough to count and specific enough to act on.

    The arena needs to tell "this harness is bad" from "this harness ran out of
    money" from "the provider was down", because only the first is the agent's
    fault and only the first should count against it.
    """

    MAX_SPEND = "max_spend"
    """The ceiling was hit with ``max_spend_mode`` on, so the agent bailed out
    through :meth:`Agent.immediate_answer` instead of returning nothing."""

    BUDGET = "budget"
    """The ceiling was hit without ``max_spend_mode``. The run stopped where it
    stood."""

    PROVIDER = "provider"
    """OpenRouter or an upstream model refused, timed out or fell over."""

    API = "api"
    """A tool's underlying HTTP API failed hard enough to stop the plan."""

    PLAN = "plan"
    """The plan itself is wrong — an unresolved ``{{placeholder}}``, a missing
    tool, a condition naming something that is not in state."""

    OTHER = "other"


def classify(exc: BaseException) -> ErrorKind:
    """Map an exception to the kind of failure it represents."""
    from ..api import APIError  # local: keeps agent -> api a runtime edge only
    from ..core.template import ConditionError

    if isinstance(exc, MaxSpendExceeded):
        return ErrorKind.MAX_SPEND
    if isinstance(exc, BudgetExceeded):
        return ErrorKind.BUDGET
    if isinstance(exc, APIError):
        return ErrorKind.API
    if isinstance(exc, (OpenRouterError, httpx.TimeoutException, httpx.NetworkError)):
        return ErrorKind.PROVIDER
    if isinstance(exc, (ConditionError, KeyError)):
        return ErrorKind.PLAN
    return ErrorKind.OTHER


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

    max_spend_mode: bool = Field(
        default=False,
        description="On hitting the ceiling, bail out through immediate_answer() instead "
                    "of returning nothing. The run is still an error, kind 'max_spend'.",
    )
    bailout_reserve_usd: float | None = Field(
        default=None,
        description="Money held back from max_usd for the bail-out call. None derives it: "
                    "5% of max_usd, floor $0.02.",
    )
    bailout_model: str | None = Field(
        default=None, description="Model for the bail-out call. Defaults to default_model."
    )
    bailout_max_tokens: int = 900

    def reserve_usd(self) -> float:
        """What the bail-out call may spend, on top of ``max_usd``.

        An allowance rather than a guarantee, because a model call's price is
        only known after it has been made. Sized so one final call comfortably
        fits — and deliberately not sized so the bail-out can go do more
        research, which is the thing the ceiling exists to stop.
        """
        if not self.max_spend_mode:
            return 0.0
        if self.bailout_reserve_usd is not None:
            return max(0.0, self.bailout_reserve_usd)
        if self.max_usd is None:
            return 0.05
        return max(0.02, self.max_usd * 0.05)

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
    error_kind: ErrorKind | None = None
    bailed_out: bool = Field(
        default=False,
        description="The output came from immediate_answer() after the ceiling was hit, "
                    "not from finishing the plan.",
    )

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def cost_usd(self) -> float:
        return self.trace.costs.total_usd

    @property
    def text(self) -> str:
        """The output as text, which is what an eval scores.

        A step with an ``output_schema`` returns a dict, so this is not always
        ``str(output)`` — a scorer wants readable JSON, not a Python repr with
        single quotes in it.
        """
        if self.output is None:
            return ""
        if isinstance(self.output, str):
            return self.output
        return json.dumps(self.output, default=str, indent=2)

    def summary(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "run_id": self.run_id,
            "ok": self.ok,
            "error": self.error,
            "error_kind": self.error_kind.value if self.error_kind else None,
            "bailed_out": self.bailed_out,
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
        costs = CostTracker(
            max_usd=self.config.max_usd,
            max_calls=self.config.max_calls,
            reserve_usd=self.config.reserve_usd(),
        )
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
        bailed_out = False
        try:
            output = await self.plan.execute(ctx)
        except BudgetExceeded as exc:
            error = exc
            output = ctx.state.get("last")
            if self.config.max_spend_mode:
                # Out of money is not the same as out of ideas: spend the
                # reserve on one call that turns whatever state we gathered
                # into an answer. Still an error — just an answered one.
                answer, error = await self._bail_out(ctx, exc)
                bailed_out = answer is not None
                if bailed_out:
                    output = answer
                    ctx.state["bailout_answer"] = answer
        except Exception as exc:  # noqa: BLE001 - recorded on the trace
            error = exc
            if raise_on_error:
                # The ``finally`` below still closes the trace with this error
                # on it before the exception propagates; finishing here too
                # would raise from the tracer and hide the real failure.
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
            error_kind=classify(error) if error else None,
            bailed_out=bailed_out,
        )

    # -- answering now --

    async def _bail_out(
        self, ctx: StepContext, cause: BudgetExceeded
    ) -> tuple[str | None, MaxSpendExceeded]:
        """Open the reserve and try for an answer. Never raises.

        Returns the answer (or ``None`` if even the reserve could not buy one)
        and the :class:`MaxSpendExceeded` to record. The failure of a bail-out
        must not replace the reason the run stopped, which is why the original
        cause is kept in the message and only the outcome changes.
        """
        allowance = ctx.costs.open_reserve()
        ctx.tracer.current().annotate(
            max_spend_bailout=True, reserve_usd=round(allowance, 6), cause=str(cause)
        )
        answer: str | None = None
        try:
            answer = await self.immediate_answer(
                ctx.state,
                llm=ctx.llm,
                tracer=ctx.tracer,
                reason=str(cause),
                question=str(ctx.state.get("question") or ""),
            )
        except Exception as exc:  # noqa: BLE001 - the reserve ran out, or the provider did
            ctx.tracer.current().annotate(bailout_failed=f"{type(exc).__name__}: {exc}")
        return answer, MaxSpendExceeded(
            ctx.costs.total_usd,
            cause.limit,
            cause.what,
            reserve_usd=ctx.costs.reserve_usd,
            answered=bool(answer),
        )

    async def immediate_answer(
        self,
        state: dict[str, Any] | None = None,
        *,
        question: str = "",
        llm: LLMClient | None = None,
        tracer: Tracer | None = None,
        reason: str = "",
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Answer right now, from internal state, without running the plan.

        One model call. Everything the agent has gathered goes in — the
        question, each state key, the agent's own context — and the best answer
        available comes out, explicitly allowed to be hedged and required to
        say what is missing. It is deliberately *not* allowed tools: this is
        the call you make when there is no budget left to gather anything more.

        Useful on its own ("what do you think so far?"), and the thing
        ``max_spend_mode`` calls when the ceiling is hit.
        """
        owned = llm is None
        client = llm or LLMClient(
            config=self.config.to_llm_config(), rate_limit=self.config.rate_limit()
        )
        prompt = bailout_prompt(question or str((state or {}).get("question") or ""), state or {},
                                reason)
        # Sent explicitly, for the same reason steps do it: the client is
        # usually shared, and it cannot carry this agent's settings.
        settings = {
            **{k: v for k, v in self.config.to_llm_config().model_dump(
                exclude={"extra_body", "web_search", "model", "max_tokens", "fallback_models"},
            ).items() if v is not None},
            "model": model or self.config.bailout_model or self.config.default_model,
            "max_tokens": max_tokens or self.config.bailout_max_tokens,
        }
        message = [Message(role="user", content=prompt)]
        try:
            if tracer is not None:
                async with tracer.span("immediate_answer", "llm", input={"reason": reason}) as span:
                    completion = await client.complete(
                        message, system=(self.context or None), tracer=tracer, **settings
                    )
                    span.set_output(completion.text)
            else:
                completion = await client.complete(
                    message, system=(self.context or None), **settings
                )
        finally:
            if owned:
                await client.close()
        return completion.text

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


BAILOUT_INSTRUCTIONS = """You have run out of budget and must answer NOW, from what you \
already have. There will be no further tool calls, searches or model calls.

Write the best answer the notes below support. Be specific where they are specific and \
hedged where they are thin — an honest partial answer is worth more here than a confident \
invented one. Finish with one short line naming what you would have checked next, so the \
reader knows what this answer is missing."""


def bailout_prompt(question: str, state: dict[str, Any], reason: str = "") -> str:
    """Assemble the one prompt :meth:`Agent.immediate_answer` sends."""
    parts = [BAILOUT_INSTRUCTIONS]
    if reason:
        parts.append(f"Why this is happening now: {reason}")
    parts.append(f"The question:\n{question or '(none recorded)'}")
    notes = render_state(state)
    parts.append(f"Everything gathered so far:\n{notes or '(nothing — the run stopped early)'}")
    return "\n\n".join(parts)


def render_state(state: dict[str, Any], *, budget_chars: int = 24000) -> str:
    """The run state as readable notes, newest keys first and truncated to fit.

    Newest first because the ceiling is usually hit deep in a loop, and the
    last thing gathered is the most relevant. Truncation is per value and then
    overall, so one enormous scrape cannot crowd out ten useful notes.
    """
    hidden = {"question", "loop_index", "loop_iteration", "loop_results", "bailout_answer"}
    lines: list[str] = []
    spent = 0
    for key, value in reversed(list(state.items())):
        if key in hidden or value in (None, "", [], {}):
            continue
        body = value if isinstance(value, str) else json.dumps(value, default=str, indent=2)
        room = max(200, budget_chars - spent)
        if len(body) > room:
            body = body[:room] + f"\n… [{len(body) - room} more characters dropped]"
        lines.append(f"## {key}\n{body}")
        spent += len(body)
        if spent >= budget_chars:
            break
    return "\n\n".join(lines)


def state_inputs(state: dict[str, Any], question: str | None) -> dict[str, Any]:
    inputs = {k: v for k, v in state.items() if k != "question"}
    return {"question": question or "", **{k: v for k, v in inputs.items() if _small(v)}}


def public_state(state: dict[str, Any]) -> dict[str, Any]:
    """State minus the loop bookkeeping, which is noise once the run is over."""
    hidden = {"loop_index", "loop_iteration", "loop_results"}
    return {k: v for k, v in state.items() if k not in hidden}


def _small(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool, type(None))) and len(str(value)) < 500
