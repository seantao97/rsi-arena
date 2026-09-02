"""Tools declared as classes.

:mod:`.tools` builds a tool from a Python function: the signature becomes the
schema and the docstring becomes the description. That is the right shape for a
one-line primitive, and the wrong one as soon as a tool has to say more about
itself than a docstring holds.

A :class:`Tool` here is a class instead. It carries a version, a description
written for the model rather than for a reader of the source, a schema for what
goes in and a schema for what comes out, and it returns a :class:`ToolOutput`
that separates what the model reads from what the exchange actually sent.

Two things follow from that, and both matter to an arena whose point is
rewriting harnesses:

**A tool can be versioned.** ``version`` is on the class, so a harness can name
the tool *and* the revision it was written against, and a later revision that
changes an output shape does not silently alter what an older harness meant.

**A tool can be selected on its description.** When a model is choosing among
twenty primitives it has nothing to go on but the text, so the description says
what the tool answers, what it costs, and when it is the wrong one to reach for.

Declared tools run on the same machinery as every other one. ``as_runtime``
returns a :class:`~rsi_arena.agent.tools.Tool`, so a :class:`Toolbox`, a
``ToolStep`` and a ``PromptStep`` treat both kinds identically and neither the
agent nor the model can tell which is which.

.. code-block:: python

    class TradingFeesTool(Tool):
        name = "trading_fees"
        description = "What a trade costs at a given price, both sides."
        input_schema = {"type": "object",
                        "properties": {"price": {"type": "number"}},
                        "required": ["price"]}

        def get_tool_output(self, input: dict) -> ToolOutput:
            ...

    Toolbox([TradingFeesTool().as_runtime()])
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from .tools import Tool as RuntimeTool


@dataclass
class ToolOutput:
    """What a tool returns, in the three forms someone will want it.

    ``response`` is the sentence the model reads. It is written, not dumped —
    a model choosing its next call is served better by "Chelsea 2-1, 63rd
    minute, market 0.81/0.83" than by the JSON those numbers came from.

    ``raw_api_data`` is what the upstream service actually sent, kept whole. It
    is the difference between a trace that can be re-read later and one that
    only holds this run's interpretation of the data.

    ``raw_output`` is the structured result: parsed, named, and safe for code to
    index into.
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
        """A tool that could not answer says so rather than raising.

        The model can read a refusal and try different arguments; it cannot
        read a traceback.
        """
        return cls(response=f"unavailable: {reason}", error=reason)


class Tool:
    """One primitive, declared.

    Subclasses set ``name``, ``description`` and ``input_schema``, and
    implement :meth:`get_tool_output`. Everything else — the model-facing
    schema, the async wrapper, the error handling — comes from here.
    """

    name: str = ""
    version: int = 1
    description: str = ""

    #: JSON Schema for the argument object. An empty properties block means the
    #: tool takes no arguments, which is different from taking anything.
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}

    #: JSON Schema for ``raw_output``. Optional, and worth writing when a
    #: harness is expected to read specific fields out of the result.
    output_schema: dict[str, Any] = {"type": "object"}

    #: Flat charge per call for tools that cost money and do not report it.
    cost_usd: float = 0.0

    def get_tool_input_schema(self) -> str:
        return json.dumps(self.input_schema)

    def get_tool_output_schema(self) -> str:
        return json.dumps(self.output_schema)

    def get_tool_output(self, input: dict[str, Any]) -> ToolOutput:
        raise NotImplementedError

    # ---------- running it ----------

    def __call__(self, **kwargs: Any) -> ToolOutput:
        return self.get_tool_output(kwargs)

    async def acall(self, **kwargs: Any) -> ToolOutput:
        """Off the event loop, because these calls block on the network.

        Tool bodies are written synchronously — that is the simpler thing to
        write and to test — and this is where that gets reconciled with an
        async agent.
        """
        return await asyncio.to_thread(self.get_tool_output, kwargs)

    def as_runtime(self) -> RuntimeTool:
        """Adapt to the callable tool the agent machinery already runs.

        The model is handed ``response`` and nothing else: it is the part
        written for reading, and passing the raw payload as well would spend
        thousands of tokens on data the sentence already summarises. The
        structured result stays in the trace.
        """
        async def invoke(**kwargs: Any) -> Any:
            out = await self.acall(**kwargs)
            if not out.ok:
                raise RuntimeError(out.error)
            return out.response or out.raw_output

        return RuntimeTool(
            name=self.name,
            description=self.described(),
            parameters=self.input_schema,
            fn=invoke,
            cost_usd=self.cost_usd,
        )

    def described(self) -> str:
        """The description the model sees, stamped with the revision.

        A harness that names a tool is naming a version of it, and the stamp is
        what makes that visible when a trace is read months later.
        """
        return f"{self.description.strip()} (v{self.version})"

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r}, v{self.version})"


def toolbox(*tools: Tool):
    """A :class:`~rsi_arena.agent.tools.Toolbox` from declared tools."""
    from .tools import Toolbox

    return Toolbox([t.as_runtime() for t in tools])
