"""Tools: typed callables an agent — or a model — can invoke.

A :class:`Tool` is a name, a description, a JSON Schema for its arguments, and
something to call. Three ways to make one, in ascending order of ceremony:

.. code-block:: python

    @tool                                   # from a Python function
    async def word_count(text: str) -> int:
        \"\"\"Count words in a string.\"\"\"
        return len(text.split())

    api_tool(SEARCHAPI, "search")           # from a registered API endpoint
    Tool(name=..., description=..., parameters=..., fn=...)   # by hand

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
import time
from typing import Annotated, Any, Callable, get_args, get_origin, get_type_hints

from pydantic import BaseModel, Field, TypeAdapter

from .api import APIClient, APISpec, Endpoint, get_api
from .costs import Cost
from .trace import Tracer


class ToolResult(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
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
        if isinstance(self.output, str):
            return self.output
        import json

        return json.dumps(self.output, default=str)[:20000]


class Tool:
    """A callable with a schema and a price.

    ``cost_usd`` is a flat per-call charge for tools that cost money but do not
    report it (most vendor APIs). Tools whose underlying call reports its own
    cost — an API endpoint, a nested LLM call — return it instead, and this is
    left at zero so the same money is not counted twice.
    """

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        fn: Callable[..., Any],
        *,
        cost_usd: float = 0.0,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.fn = fn
        self.cost_usd = cost_usd

    def to_openai_schema(self) -> dict[str, Any]:
        """The ``tools`` entry OpenRouter expects (OpenAI function format)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def __call__(self, tracer: Tracer | None = None, **kwargs: Any) -> ToolResult:
        started = time.monotonic()
        async def invoke() -> Any:
            result = self.fn(**kwargs)
            return await result if inspect.isawaitable(result) else result

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
        self, invoke: Callable[[], Any], kwargs: dict[str, Any], started: float, tracer: Tracer | None
    ) -> ToolResult:
        result = ToolResult(name=self.name, args=kwargs)
        try:
            output = await invoke()
        except Exception as exc:  # noqa: BLE001 - surfaced to the model, not raised
            # A failed tool is information, not a crash: the model can read the
            # error and try different arguments. Only the step budget stops it.
            result.error = f"{type(exc).__name__}: {exc}"
        else:
            result.output, result.cost, result.cached = _unwrap(output, self.cost_usd)
        result.latency_s = time.monotonic() - started
        return result


def _unwrap(output: Any, flat_cost: float) -> tuple[Any, Cost, bool]:
    """Pull cost out of results that carry their own (API responses, tools)."""
    cost = getattr(output, "cost", None)
    if isinstance(cost, Cost):
        return getattr(output, "data", output), cost, bool(getattr(output, "cached", False))
    return output, (Cost.flat(flat_cost) if flat_cost else Cost.free()), False


# --- from a Python function -------------------------------------------------

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _schema_from_signature(fn: Callable[..., Any]) -> dict[str, Any]:
    """Build a parameter schema from annotations.

    ``Annotated[str, "what this is for"]`` puts a description on a parameter,
    which is worth doing: descriptions are the main thing that stops a model
    passing the right value to the wrong argument.
    """
    signature = inspect.signature(fn)
    hints = get_type_hints(fn, include_extras=True)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in signature.parameters.items():
        if name in {"self", "cls", "tracer"} or param.kind in {
            param.VAR_POSITIONAL,
            param.VAR_KEYWORD,
        }:
            continue
        annotation = hints.get(name, str)
        description = ""
        if get_origin(annotation) is Annotated:
            args = get_args(annotation)
            annotation = args[0]
            description = next((a for a in args[1:] if isinstance(a, str)), "")
        if annotation in _JSON_TYPES:
            schema: dict[str, Any] = {"type": _JSON_TYPES[annotation]}
        else:
            try:
                schema = TypeAdapter(annotation).json_schema()
            except Exception:  # noqa: BLE001 - unrepresentable annotation
                schema = {"type": "string"}
        if description:
            schema["description"] = description
        properties[name] = schema
        if param.default is inspect.Parameter.empty:
            required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def tool(
    fn: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    cost_usd: float = 0.0,
) -> Any:
    """Turn a function into a :class:`Tool`. Usable bare or with arguments."""

    def wrap(func: Callable[..., Any]) -> Tool:
        return Tool(
            name=name or func.__name__,
            description=description or (inspect.getdoc(func) or "").strip(),
            parameters=_schema_from_signature(func),
            fn=func,
            cost_usd=cost_usd,
        )

    return wrap(fn) if fn is not None else wrap


# --- from an API endpoint ---------------------------------------------------


def api_tool(
    api: str | APISpec,
    endpoint: str,
    *,
    client: APIClient | None = None,
    name: str | None = None,
    description: str | None = None,
    fixed: dict[str, Any] | None = None,
) -> Tool:
    """Expose one API endpoint as a tool.

    ``fixed`` pins parameters the model should not control — a country code, a
    result count, an account id. Pinned parameters are removed from the schema
    so the model never sees them, which is both cheaper and safer than asking
    it politely not to change them.
    """
    spec = api if isinstance(api, APISpec) else get_api(api)
    ep: Endpoint = spec.endpoint(endpoint)
    shared = client or APIClient()
    pinned = fixed or {}

    schema = ep.schema()
    if pinned:
        schema = {
            **schema,
            "properties": {k: v for k, v in schema["properties"].items() if k not in pinned},
            "required": [k for k in schema["required"] if k not in pinned],
        }

    async def call(**kwargs: Any) -> Any:
        return await shared.call(spec, ep.name, **{**pinned, **kwargs})

    return Tool(
        name=name or f"{spec.name}_{ep.name}",
        description=description or ep.description or f"{spec.name} {ep.name}",
        parameters=schema,
        fn=call,
    )


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
