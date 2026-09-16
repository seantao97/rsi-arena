"""Tools: typed callables an agent — or a model — can invoke.

A :class:`Tool` is a name, a version, a description, a JSON Schema for its
arguments and one for its result, and a method that answers. Two ways to make
one:

.. code-block:: python

    class WordCount(Tool):                  # declared as a class
        name = "word_count"
        version = 2
        description = "Count words. Whitespace-separated, no tokenising."
        parameters = {"type": "object",
                      "properties": {"text": {"type": "string"}},
                      "required": ["text"]}

        def get_tool_output(self, input: dict) -> ToolOutput:
            n = len(input["text"].split())
            return ToolOutput(response=f"{n} words", raw_output={"count": n})

    api_tool(SEARCHAPI, "search")           # from a registered API endpoint

Tools were once built from a function, with the signature becoming the schema
and the docstring the description. That is right for a one-line primitive and
wrong as soon as a tool has more to say about itself than a docstring holds —
which is most of them, once a model is choosing among twenty and has nothing to
go on but that text.

The same object serves both callers described in the README: a ``ToolStep``
invokes it directly (fixed pipeline), and a ``PromptStep`` can hand its schema
to the model as an OpenRouter ``tools`` entry and let the model decide (free
form). Nothing about the tool changes between the two — which is the point,
since the arena exists to compare exactly those two orchestrations over the
same primitive set.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from typing import Any, Callable

from dataclasses import asdict, dataclass, field
from pydantic import BaseModel, Field

from ..api import APIClient, APISpec, Endpoint, get_api
from ..core.costs import Cost
from ..core.trace import Tracer


@dataclass
class ToolOutput:
    """What a declared tool returns, in the three forms someone will want it.

    ``response`` is the sentence the model reads. It is written, not dumped —
    a model choosing its next call is served better by "Chelsea 2-1, 63rd
    minute, market 0.81/0.83" than by the JSON those numbers came from.

    ``raw_api_data`` is what the upstream service sent, kept whole. It is the
    difference between a trace that can be re-read later and one that holds
    only this run's interpretation of the data.

    ``raw_output`` is the structured result: parsed, named, safe to index into.
    """

    response: str = ""
    raw_api_data: dict[str, Any] = field(default_factory=dict)
    raw_output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def for_model(self) -> str:
        """What goes back as the ``tool`` message content.

        The sentence if there is one, the structure otherwise. A model choosing
        its next call wants the summary; falling back to the payload is for
        tools that only ever produce data.
        """
        if self.error:
            return f"ERROR: {self.error}"
        if self.response:
            return self.response
        return json.dumps(self.raw_output, default=str)[:20000]

    @classmethod
    def failed(cls, reason: str) -> "ToolOutput":
        """A tool that cannot answer says so rather than raising.

        The model can read a refusal and try different arguments; it cannot
        read a traceback.
        """
        return cls(response=f"unavailable: {reason}", error=reason)


class Tool:
    """One primitive an agent — or a model — can invoke.

    A tool is declared, not wrapped. Subclass it, set the class attributes,
    and implement :meth:`get_tool_output`:

    .. code-block:: python

        class WordCount(Tool):
            name = "word_count"
            version = 2
            description = "Count words. Whitespace-separated, no tokenising."
            parameters = {"type": "object",
                          "properties": {"text": {"type": "string"}},
                          "required": ["text"]}

            def get_tool_output(self, input: dict) -> ToolOutput:
                n = len(input["text"].split())
                return ToolOutput(response=f"{n} words", raw_output={"count": n})

    Declaring rather than wrapping buys three things a decorated function
    cannot give. A ``version``, so a harness can name the tool *and* the
    revision it was written against and a later change of output shape does
    not silently alter what an older harness meant. A description written for
    the model rather than for a reader of the source — which matters because a
    model choosing among twenty primitives has nothing else to go on. And a
    :class:`ToolOutput`, which separates the sentence the model reads from the
    structure that code indexes into and the payload the service actually sent.

    ``cost_usd`` is a flat per-call charge for tools that cost money but do not
    report it (most vendor APIs). Tools whose underlying call reports its own
    cost — an API endpoint, a nested LLM call — return it instead, and this is
    left at zero so the same money is not counted twice.
    """

    #: Set on the subclass. The model selects on ``name`` and ``description``
    #: and nothing else, so both are part of the interface.
    name: str = ""
    description: str = ""

    #: JSON Schema for the argument object. An empty properties block means the
    #: tool takes no arguments, which is not the same as taking anything.
    #:
    #: ``input_schema`` is accepted as a synonym and every Kalshi tool uses it.
    #: When this class replaced the decorator the two names diverged and nothing
    #: noticed: all forty tools declared ``input_schema``, the base class read
    #: ``parameters``, and every one of them reported taking no arguments at all.
    #: A model choosing its own calls would have had nothing to fill in.
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    input_schema: dict[str, Any] | None = None

    #: JSON Schema for :attr:`ToolOutput.raw_output`. Worth writing when a
    #: harness is expected to read particular fields back out.
    output_schema: dict[str, Any] = {"type": "object"}

    #: Bump when the output shape changes.
    version: int = 1
    cost_usd: float = 0.0

    # ---------- what a subclass provides ----------

    def answer(self, input: dict[str, Any]) -> ToolOutput:
        """:meth:`get_tool_output` with the same net ``__call__`` puts under it.

        A missing argument and a dropped connection are both things a caller can
        read and act on; both used to arrive as exceptions. `__call__` has always
        caught them, but a plan reaches `get_tool_output` directly and got
        nothing — so a network blip during a scheduled run handed the model a
        stack trace instead of a sentence.
        """
        gap = self.missing(input)
        if gap:
            return ToolOutput.failed(gap)
        try:
            return self.get_tool_output(input)
        except Exception as exc:  # noqa: BLE001 - a tool's failure is information
            return ToolOutput.failed(f"{type(exc).__name__}: {exc}")

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        """Answer, synchronously.

        Sync on purpose: a tool body is the easiest thing in the system to
        write and to test, and the async paths below run it off the event loop
        so nothing blocks.
        """
        raise NotImplementedError(f"{type(self).__name__} implements no get_tool_output")

    # ---------- reading it ----------

    @property
    def _arguments(self) -> dict[str, Any]:
        """Whichever of the two names this subclass declared."""
        declared = type(self).__dict__.get("input_schema") or self.input_schema
        return declared if declared is not None else self.parameters

    def get_tool_input_schema(self) -> str:
        return json.dumps(self._arguments)

    def get_tool_output_schema(self) -> str:
        return json.dumps(self.output_schema)

    def described(self) -> str:
        """The description the model sees, stamped with the revision."""
        text = self.description.strip()
        return f"{text} (v{self.version})" if self.version else text

    def to_openai_schema(self) -> dict[str, Any]:
        """The ``tools`` entry OpenRouter expects (OpenAI function format)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.described(),
                "parameters": self._arguments,
            },
        }

    # ---------- running it ----------

    @staticmethod
    def _run_sync(coro: Any) -> Any:
        """Drive a coroutine from synchronous code.

        A tool whose body is genuinely async — one that makes an HTTP call —
        used to raise NotImplementedError from `get_tool_output`, so a
        synchronous caller got a crash instead of an answer. `web_research` and
        `team_news` both did, and both are reachable from a plan.

        Inside a running loop the work goes to a thread with its own, because
        `asyncio.run` refuses to nest.
        """
        import concurrent.futures

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()

    async def aget_tool_output(self, input: dict[str, Any] | None = None,
                               **kwargs: Any) -> ToolOutput:
        """:meth:`get_tool_output`, off the event loop.

        Two callers want different things from the same body. The model wants
        the sentence and gets it through :meth:`__call__` as a
        the sentence; code wants the structure — a game id, a list of
        markets — and gets the whole :class:`ToolOutput` here.
        """
        return await asyncio.to_thread(self.get_tool_output,
                                       {**(input or {}), **kwargs})

    async def __call__(self, tracer: Tracer | None = None, **kwargs: Any) -> ToolOutput:
        """Invoke the tool. Never raises — a failure is an answer.

        A failed tool is information, not a crash: the model reads the error and
        tries different arguments, and only the step budget stops it.

        What the call *cost* and how long it took are not on the answer. They go
        to the trace span, which is where a run's accounting already lives, and
        duplicating them onto the result was how the same money got counted in
        two places.
        """
        started = time.monotonic()
        if tracer is None:
            return await self._invoke(kwargs)
        async with tracer.span(self.name, "tool", input=kwargs) as span:
            out = await self._invoke(kwargs)
            span.set_output(out.raw_output or out.response if out.ok else out.error)
            span.attributes["latency_s"] = time.monotonic() - started
            cost = out.raw_api_data.get("cost")
            if isinstance(cost, Cost) and (cost.usd or cost.cached):
                tracer.record_cost("tool", self.name, cost, span)
            if not out.ok:
                span.status = "error"
                span.error = out.error
            return out

    def missing(self, args: dict[str, Any]) -> str:
        """The required arguments this call left out, as a sentence, or ``""``.

        Checked before the body runs, because leaving one out is the commonest
        thing a model gets wrong and a `KeyError` is the least useful thing to
        tell it. Twenty-three of forty tools raised one; a model can act on
        "needs league, game_id" and cannot act on a stack trace.
        """
        required = self._arguments.get("required") or []
        absent = [name for name in required if args.get(name) is None]
        if not absent:
            return ""
        return (f"{self.name} needs {', '.join(absent)}"
                + (f"; got {', '.join(sorted(args))}" if args else " and got nothing"))

    async def _invoke(self, args: dict[str, Any]) -> ToolOutput:
        gap = self.missing(args)
        if gap:
            return ToolOutput.failed(gap)
        try:
            answer = await self.aget_tool_output(args)
        except Exception as exc:  # noqa: BLE001 - surfaced to the model, not raised
            return ToolOutput.failed(f"{type(exc).__name__}: {exc}")
        return _as_output(answer, self.cost_usd)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r}, v{self.version})"


def _as_output(answer: Any, flat_cost: float) -> ToolOutput:
    """Normalise whatever a tool body returned into one ToolOutput.

    A declared tool answers with a ToolOutput already. An api_tool answers with
    the endpoint's response, returned whole so its own cost and cached flag
    survive the trip rather than being buried under a repackaged payload.
    """
    if isinstance(answer, ToolOutput):
        out = answer
    else:
        cost = getattr(answer, "cost", None)
        data = getattr(answer, "data", answer) if isinstance(cost, Cost) else answer
        out = ToolOutput(
            response=data if isinstance(data, str) else json.dumps(data, default=str)[:20000],
            raw_output=data if isinstance(data, dict) else {"value": data},
        )
        if isinstance(cost, Cost):
            out.raw_api_data["cost"] = cost
            out.raw_api_data["cached"] = bool(getattr(answer, "cached", False))
    if flat_cost and "cost" not in out.raw_api_data:
        out.raw_api_data["cost"] = Cost.flat(flat_cost)
    return out


# --- from a registered API endpoint ------------------------------------------


def api_tool(
    api: str | APISpec,
    endpoint: str,
    *,
    name: str | None = None,
    description: str | None = None,
    client: APIClient | None = None,
    fixed: dict[str, Any] | None = None,
) -> Tool:
    """A tool from a registered API endpoint.

    Returns an instance of a subclass built here, so an endpoint is declared
    like any other tool and nothing downstream has to know where it came from.

    ``fixed`` pins arguments the model should not choose — an API key, a
    market the caller has already decided on. Pinned names are removed from the
    schema, because an argument the model cannot usefully vary is one more
    thing for it to get wrong.
    """
    spec = get_api(api) if isinstance(api, str) else api
    ep = spec.endpoint(endpoint)
    shared = client or APIClient()
    fixed = fixed or {}

    schema = ep.schema()
    if fixed:
        schema = {
            **schema,
            "properties": {k: v for k, v in schema["properties"].items() if k not in fixed},
            "required": [k for k in schema["required"] if k not in fixed],
        }

    class _APITool(Tool):
        """One endpoint of one API."""

    _APITool.name = name or f"{spec.name}_{ep.name}"
    _APITool.description = description or ep.description or f"{spec.name} {ep.name}"
    _APITool.parameters = schema
    _APITool.__name__ = f"{spec.name.title()}{ep.name.title()}Tool"

    def get_tool_output(self: Tool, input: dict[str, Any]) -> ToolOutput:
        raise NotImplementedError(
            "an api tool answers through aget_tool_output, which awaits the "
            "endpoint rather than threading a synchronous body")

    async def aget_tool_output(self: Tool, input: dict[str, Any] | None = None,
                               **kwargs: Any) -> Any:
        # The endpoint is already async, so this is the one place a tool body
        # is written async rather than threaded.
        # Returned whole rather than repackaged: the response carries its own
        # cost and cached flag, which _unwrap reads on the way back. Wrapping
        # the payload here would bury the parsed result a level down and charge
        # the call twice.
        args = {**(input or {}), **kwargs}
        return await shared.call(spec, ep.name, **{**fixed, **args})

    _APITool.get_tool_output = get_tool_output
    _APITool.aget_tool_output = aget_tool_output
    return _APITool()


# --- collection -------------------------------------------------------------


class Toolbox(dict):
    """The tools an agent may use, by name.

    A dict, because that is what it is. Only three things here are not dict
    behaviour: construction from a list (a tool already knows its own name), an
    error that lists what *is* available, and a concurrent call — a model may
    emit several tool calls in one turn and they are independent.

    Iteration follows dict semantics and yields names; ``.values()`` gives the
    tools.
    """

    def __init__(self, tools: "list[Tool] | None" = None) -> None:
        super().__init__((t.name, t) for t in (tools or []) if t is not None)

    def __missing__(self, name: str) -> "Tool":
        known = ", ".join(sorted(self)) or "none"
        raise KeyError(f"unknown tool {name!r} (have: {known})")

    def add(self, tool: "Tool") -> "Tool":
        self[tool.name] = tool
        return tool

    def add_api(self, api: "str | APISpec", endpoint: str, **kwargs: Any) -> "Tool":
        return self.add(api_tool(api, endpoint, **kwargs))

    def names(self) -> list[str]:
        return list(self)

    def schemas(self, only: list[str] | None = None) -> list[dict[str, Any]]:
        """OpenRouter ``tools`` payload, optionally narrowed to a subset."""
        return [self[n].to_openai_schema() for n in (only or self) if n in self]

    def describe(self) -> str:
        """Plain-text catalogue for a prompt, when tools are described not passed."""
        return "\n".join(f"- {t.name}: {t.description}" for t in self.values())

    async def call(self, name: str, args: dict[str, Any],
                   tracer: Tracer | None = None) -> ToolOutput:
        return await self[name](tracer=tracer, **args)

    async def call_many(self, calls: list[tuple[str, dict[str, Any]]],
                        tracer: Tracer | None = None) -> list[ToolOutput]:
        """Run independent tool calls concurrently — a model may emit several."""
        return await asyncio.gather(*(self.call(n, a, tracer) for n, a in calls))
