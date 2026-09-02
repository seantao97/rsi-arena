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

    @classmethod
    def failed(cls, reason: str) -> "ToolOutput":
        """A tool that cannot answer says so rather than raising.

        The model can read a refusal and try different arguments; it cannot
        read a traceback.
        """
        return cls(response=f"unavailable: {reason}", error=reason)


class ToolResult(BaseModel):
    """One invocation, as both callers need it.

    ``output`` is the structure — what a ``ToolStep`` writes into run state and
    the next step interpolates. ``response`` is the sentence the model reads.
    They are separate because they are read by different things: a fixed
    pipeline wants the list it can index, and a model choosing its next call
    wants the summary rather than the payload behind it.
    """

    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    response: str = ""
    error: str | None = None
    cached: bool = False
    latency_s: float = 0.0
    cost: Cost = Field(default_factory=Cost)

    @property
    def ok(self) -> bool:
        return self.error is None

    def for_model(self) -> str:
        """What gets sent back as the ``tool`` message content."""
        if self.error:
            return f"ERROR: {self.error}"
        if self.response:
            return self.response
        if isinstance(self.output, str):
            return self.output
        return json.dumps(self.output, default=str)[:20000]


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
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    #: JSON Schema for :attr:`ToolOutput.raw_output`. Worth writing when a
    #: harness is expected to read particular fields back out.
    output_schema: dict[str, Any] = {"type": "object"}

    #: Bump when the output shape changes.
    version: int = 1
    cost_usd: float = 0.0

    # ---------- what a subclass provides ----------

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        """Answer, synchronously.

        Sync on purpose: a tool body is the easiest thing in the system to
        write and to test, and the async paths below run it off the event loop
        so nothing blocks.
        """
        raise NotImplementedError(f"{type(self).__name__} implements no get_tool_output")

    # ---------- reading it ----------

    def get_tool_input_schema(self) -> str:
        return json.dumps(self.parameters)

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
                "parameters": self.parameters,
            },
        }

    # ---------- running it ----------

    async def aget_tool_output(self, input: dict[str, Any] | None = None,
                               **kwargs: Any) -> ToolOutput:
        """:meth:`get_tool_output`, off the event loop.

        Two callers want different things from the same body. The model wants
        the sentence and gets it through :meth:`__call__` as a
        :class:`ToolResult`; code wants the structure — a game id, a list of
        markets — and gets the whole :class:`ToolOutput` here.
        """
        return await asyncio.to_thread(self.get_tool_output,
                                       {**(input or {}), **kwargs})

    async def __call__(self, tracer: Tracer | None = None, **kwargs: Any) -> ToolResult:
        started = time.monotonic()

        async def invoke() -> Any:
            out = await self.aget_tool_output(kwargs)
            # An api_tool answers with the endpoint's own response rather than
            # a ToolOutput, so that its cost survives the trip; only a
            # ToolOutput can refuse.
            if isinstance(out, ToolOutput) and not out.ok:
                raise RuntimeError(out.error)
            return out

        if tracer is None:
            return await self._run(invoke, kwargs, started, None)
        async with tracer.span(self.name, "tool", input=kwargs) as span:
            result = await self._run(invoke, kwargs, started, tracer)
            span.set_output(result.output if result.ok else result.error)
            if result.cost.usd or result.cost.cached:
                tracer.record_cost("tool", self.name, result.cost, span)
            if not result.ok:
                span.status = "error"
                span.error = result.error
            return result

    async def _run(
        self, invoke: Callable[[], Any], kwargs: dict[str, Any], started: float,
        tracer: Tracer | None,
    ) -> ToolResult:
        result = ToolResult(name=self.name, args=kwargs)
        try:
            output = await invoke()
        except Exception as exc:  # noqa: BLE001 - surfaced to the model, not raised
            # A failed tool is information, not a crash: the model can read the
            # error and try different arguments. Only the step budget stops it.
            result.error = f"{type(exc).__name__}: {exc}"
        else:
            if isinstance(output, ToolOutput):
                # The structure goes to code, the sentence to the model. A tool
                # that only writes a sentence still has to put something in
                # output, since a pipeline step may read it.
                result.response = output.response
                output = output.raw_output or output.response
            result.output, result.cost, result.cached = _unwrap(output, self.cost_usd)
        result.latency_s = time.monotonic() - started
        return result

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r}, v{self.version})"


def _unwrap(output: Any, flat_cost: float) -> tuple[Any, Cost, bool]:
    """Pull cost out of results that carry their own (API responses, tools)."""
    cost = getattr(output, "cost", None)
    if isinstance(cost, Cost):
        return getattr(output, "data", output), cost, bool(getattr(output, "cached", False))
    return output, (Cost.flat(flat_cost) if flat_cost else Cost.free()), False


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


class Toolbox:
    """The set of tools an agent may use. Ordered, addressable by name."""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for item in tools or []:
            self.add(item)

    def add(self, item: Tool) -> Tool:
        self._tools[item.name] = item
        return item

    def add_api(self, api: str | APISpec, endpoint: str, **kwargs: Any) -> Tool:
        return self.add(api_tool(api, endpoint, **kwargs))

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            known = ", ".join(sorted(self._tools)) or "none"
            raise KeyError(f"unknown tool {name!r} (have: {known})") from None

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self, only: list[str] | None = None) -> list[dict[str, Any]]:
        """OpenRouter ``tools`` payload, optionally narrowed to a subset."""
        chosen = [self._tools[n] for n in (only or self._tools) if n in self._tools]
        return [t.to_openai_schema() for t in chosen]

    def describe(self) -> str:
        """Plain-text catalogue for a prompt, when tools are described not passed."""
        return "\n".join(f"- {t.name}: {t.description}" for t in self._tools.values())

    async def call(self, name: str, args: dict[str, Any], tracer: Tracer | None = None) -> ToolResult:
        return await self.get(name)(tracer=tracer, **args)

    async def call_many(
        self, calls: list[tuple[str, dict[str, Any]]], tracer: Tracer | None = None
    ) -> list[ToolResult]:
        """Run independent tool calls concurrently — a model may emit several."""
        return await asyncio.gather(*(self.call(n, a, tracer) for n, a in calls))

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Any:
        return iter(self._tools.values())

    def __contains__(self, name: object) -> bool:
        return name in self._tools
