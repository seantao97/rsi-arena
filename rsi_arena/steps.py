"""Steps and plans.

A :class:`Plan` is an ordered list of steps. A step is either simple —
:class:`PromptStep` (the LLM does something) or :class:`ToolStep` (a tool or
API call does something) — or a :class:`LoopStep`, which holds other steps
plus a stopping condition and a loop ceiling.

Every step writes its result into the run state under ``output_key`` (and
always into ``last``), and every later step can interpolate it with
``{{key}}``. That is the whole data-passing story: one flat, JSON-serialisable
dict, no hidden channels. It has to be flat and serialisable because the
arena's optimizer reads traces and writes new plans, and both ends of that
loop are JSON.

Steps are pydantic models with a ``type`` discriminator, so a plan round-trips
through ``Plan.model_validate(json.loads(...))`` unchanged — which is what
lets a generation of agents be stored, mutated and re-run.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from .costs import CostTracker
from .llm import LLMClient, LLMConfig, Message, WebSearch, parse_json_loose
from .template import evaluate, render
from .tools import Toolbox
from .trace import Tracer


class StepContext:
    """Everything a step needs, and the state it reads and writes.

    ``state`` is the interpolation namespace. It starts as the run inputs and
    accumulates one entry per step. ``messages`` is the optional running
    conversation for steps that opt into memory.
    """

    def __init__(
        self,
        *,
        llm: LLMClient,
        tools: Toolbox,
        tracer: Tracer,
        config: LLMConfig,
        context: str = "",
        state: dict[str, Any] | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.tracer = tracer
        self.config = config
        self.context = context
        self.state: dict[str, Any] = dict(state or {})
        self.messages: list[Message] = []

    @property
    def costs(self) -> CostTracker:
        return self.tracer.costs

    def set(self, key: str | None, value: Any) -> None:
        self.state["last"] = value
        if key:
            self.state[key] = value

    def render(self, template: str) -> str:
        return render(template, self.state)


class Step(BaseModel):
    """Base for every step. Subclasses implement :meth:`run`."""

    type: str
    name: str = ""
    description: str = ""
    output_key: str | None = None
    skip_if: str | None = Field(
        default=None,
        description="Restricted expression; when it evaluates true the step is skipped.",
    )

    @abstractmethod
    async def run(self, ctx: StepContext) -> Any: ...

    async def execute(self, ctx: StepContext) -> Any:
        """Trace, skip-check, run, and record the output into state."""
        label = self.name or self.type
        if self.skip_if and evaluate(self.skip_if, ctx.state):
            async with ctx.tracer.span(label, "step") as span:
                span.status = "skipped"
                span.annotate(skip_if=self.skip_if)
            return ctx.state.get("last")
        # Refuse before spending rather than after: a step that starts over
        # budget still bills for whatever it manages to run.
        ctx.costs.check(label)
        async with ctx.tracer.span(label, self._span_kind(), input=self._span_input(ctx)) as span:
            span.annotate(step_type=self.type, output_key=self.output_key)
            result = await self.run(ctx)
            span.set_output(result)
        ctx.set(self.output_key, result)
        return result

    def _span_kind(self) -> str:
        return "step"

    def _span_input(self, ctx: StepContext) -> Any:
        return None


class PromptStep(Step):
    """Ask the model. Optionally with tools, a schema, or web search.

    ``tools`` is where the README's fixed-pipeline / free-form split actually
    lives. Leave it empty and the step is one deterministic call. Set it and
    the step runs a tool-calling loop — the model chooses what to call, in what
    order, and how many times, up to ``max_tool_iterations``. Same tools, same
    model, entirely different agent.
    """

    type: Literal["prompt"] = "prompt"
    prompt: str
    system: str | None = Field(
        default=None, description="Overrides the agent context for this step only."
    )
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    tools: list[str] = Field(
        default_factory=list,
        description="Tool names the model may call. ['*'] exposes every agent tool.",
    )
    tool_choice: str | None = None
    max_tool_iterations: int = 6
    output_schema: dict[str, Any] | None = Field(
        default=None, description="JSON Schema; when set the step returns parsed JSON."
    )
    web_search: bool | WebSearch = False
    memory: bool = Field(
        default=False,
        description="Carry this exchange into later memory-enabled steps.",
    )

    def _span_kind(self) -> str:
        return "step"

    def _span_input(self, ctx: StepContext) -> Any:
        return {"prompt": self.prompt}

    def _tool_schemas(self, ctx: StepContext) -> list[dict[str, Any]] | None:
        if not self.tools:
            return None
        if self.tools == ["*"]:
            return ctx.tools.schemas() or None
        return ctx.tools.schemas(self.tools) or None

    async def run(self, ctx: StepContext) -> Any:
        rendered = ctx.render(self.prompt)
        system = self.system or ctx.context or None
        history = list(ctx.messages) if self.memory else []
        messages = [*history, Message(role="user", content=rendered)]
        schema = (
            {"type": "json_schema",
             # Named after the step: providers echo the schema name back in
             # errors, and "output" in every error is useless.
             "json_schema": {"name": self.name or "output", "strict": True,
                             "schema": self.output_schema}}
            if self.output_schema
            else None
        )
        tool_schemas = self._tool_schemas(ctx)

        completion = None
        for iteration in range(self.max_tool_iterations + 1):
            last_turn = iteration == self.max_tool_iterations
            async with ctx.tracer.span(
                f"llm[{iteration}]" if tool_schemas else "llm", "llm"
            ) as span:
                completion = await ctx.llm.complete(
                    messages,
                    system=system,
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    tools=None if last_turn else tool_schemas,
                    tool_choice=self.tool_choice if iteration == 0 else None,
                    schema=schema,
                    web_search=self.web_search or None,
                    tracer=ctx.tracer,
                )
                span.set_output(completion.text or [tc.function.name for tc in completion.tool_calls])
            if not completion.tool_calls:
                break
            messages.append(completion.message)
            # The model may request several calls at once; they are independent
            # by construction, so run them together rather than in sequence.
            results = await ctx.tools.call_many(
                [(tc.function.name, tc.function.parsed_arguments()) for tc in completion.tool_calls],
                tracer=ctx.tracer,
            )
            for call, result in zip(completion.tool_calls, results):
                messages.append(
                    Message(role="tool", tool_call_id=call.id, name=call.function.name,
                            content=result.for_model())
                )

        assert completion is not None
        if self.memory:
            ctx.messages = [*messages, completion.message]
        if completion.citations:
            ctx.state.setdefault("citations", []).extend(
                c.model_dump() for c in completion.citations
            )
        if self.output_schema:
            return parse_json_loose(completion.text)
        return completion.text


class ToolStep(Step):
    """Call one tool with fixed arguments, templated from state.

    Deterministic on purpose: this is the primitive a fixed pipeline is built
    from, where the plan decides what runs rather than the model.
    """

    type: Literal["tool"] = "tool"
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    fail_ok: bool = Field(
        default=False, description="Return the error instead of raising it."
    )

    def _span_kind(self) -> str:
        return "step"

    def _span_input(self, ctx: StepContext) -> Any:
        return {"tool": self.tool, "args": self.args}

    def _render_args(self, ctx: StepContext) -> dict[str, Any]:
        def walk(value: Any) -> Any:
            if isinstance(value, str):
                return ctx.render(value)
            if isinstance(value, dict):
                return {k: walk(v) for k, v in value.items()}
            if isinstance(value, list):
                return [walk(v) for v in value]
            return value

        return {k: walk(v) for k, v in self.args.items()}

    async def run(self, ctx: StepContext) -> Any:
        result = await ctx.tools.call(self.tool, self._render_args(ctx), tracer=ctx.tracer)
        if not result.ok and not self.fail_ok:
            raise RuntimeError(f"tool {self.tool} failed: {result.error}")
        return result.output if result.ok else {"error": result.error}


class LoopStep(Step):
    """Run inner steps until a condition holds or ``max_loops`` is spent.

    Two ways to stop, and they answer different questions. ``until`` is a
    restricted expression over the run state — cheap, deterministic, and right
    when the condition is countable ("we have five sources"). ``until_prompt``
    asks a model — the only option when the condition is a judgement ("the
    draft answers the question"). Both may be set; either one stops the loop.

    ``max_loops`` is not a safety net, it is a budget. Loop count is one of the
    things the optimizer tunes, and it wants a number it can raise or lower.
    """

    type: Literal["loop"] = "loop"
    steps: list["AnyStep"] = Field(default_factory=list)
    max_loops: int = 3
    until: str | None = Field(
        default=None, description="Restricted expression; loop stops when it is true."
    )
    until_prompt: str | None = Field(
        default=None, description="Asked of the model after each pass; stops on yes."
    )
    until_model: str | None = None
    collect: bool = Field(
        default=True, description="Return every iteration's result as a list."
    )

    def _span_kind(self) -> str:
        return "loop"

    def _span_input(self, ctx: StepContext) -> Any:
        return {"max_loops": self.max_loops, "until": self.until}

    async def run(self, ctx: StepContext) -> Any:
        results: list[Any] = []
        stopped = "max_loops"
        # Saved and restored so a nested loop does not clobber the outer one's
        # bookkeeping. Steps after the loop should read its ``output_key``,
        # not ``loop_results``, which belongs to whatever loop is running.
        outer = {k: ctx.state.get(k) for k in ("loop_index", "loop_iteration", "loop_results")}
        ctx.state["loop_results"] = results
        for index in range(self.max_loops):
            ctx.state["loop_index"] = index
            ctx.state["loop_iteration"] = index + 1
            async with ctx.tracer.span(f"iteration {index + 1}", "iteration") as span:
                iteration_result = None
                for step in self.steps:
                    iteration_result = await step.execute(ctx)
                results.append(iteration_result)
                span.set_output(iteration_result)
            ctx.state["loop_results"] = results
            if await self._should_stop(ctx):
                stopped = "condition"
                break
        ctx.tracer.current().annotate(iterations=len(results), stopped_by=stopped)
        ctx.state.update({k: v for k, v in outer.items() if v is not None})
        return results if self.collect else (results[-1] if results else None)

    async def _should_stop(self, ctx: StepContext) -> bool:
        if self.until and evaluate(self.until, ctx.state):
            return True
        if not self.until_prompt:
            return False
        async with ctx.tracer.span("stop_check", "llm") as span:
            completion = await ctx.llm.complete(
                ctx.render(self.until_prompt),
                system="Answer only with JSON matching the schema. Be strict: say done only "
                       "if the stated condition is genuinely met.",
                model=self.until_model,
                schema={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "stop_check",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "done": {"type": "boolean"},
                                "reason": {"type": "string"},
                            },
                            "required": ["done", "reason"],
                            "additionalProperties": False,
                        },
                    },
                },
                tracer=ctx.tracer,
            )
            try:
                verdict = parse_json_loose(completion.text)
            except ValueError:
                # An unparseable stop check must not end the loop early; the
                # loop ceiling is the backstop that always holds.
                span.annotate(unparseable=completion.text[:200])
                return False
            span.set_output(verdict)
            return bool(verdict.get("done"))


AnyStep = Annotated[Union[PromptStep, ToolStep, LoopStep], Field(discriminator="type")]

LoopStep.model_rebuild()


class Plan(BaseModel):
    """An ordered list of steps. Serialisable in both directions."""

    steps: list[AnyStep] = Field(default_factory=list)

    async def execute(self, ctx: StepContext) -> Any:
        result = None
        for step in self.steps:
            result = await step.execute(ctx)
        return result

    def __len__(self) -> int:
        return len(self.steps)

    def outline(self, indent: int = 0) -> str:
        lines = []
        for step in self.steps:
            label = step.name or step.type
            lines.append(f"{'  ' * indent}{label} ({step.type})")
            if isinstance(step, LoopStep):
                lines.append(Plan(steps=step.steps).outline(indent + 1))
        return "\n".join(line for line in lines if line)
