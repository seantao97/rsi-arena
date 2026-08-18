"""Execution traces.

A run produces one :class:`Trace`: a tree of :class:`Span` objects, each with
its inputs, outputs, timing, cost and error. Everything is a pydantic model,
so ``trace.model_dump_json()`` is the whole artifact — which is the point,
since the arena shows traces to voters and feeds them back to the optimizer.

Nesting is automatic. The current span lives in a :mod:`contextvars` variable,
so an LLM call made three frames deep inside a step attaches to that step with
nobody passing a parent around. ``contextvars`` also copies per task, so spans
created inside ``asyncio.gather`` land under the right parent instead of
racing for a shared "current" pointer.

Long values are truncated on the way in rather than on the way out. A scraped
page is a megabyte and there is no reason to hold twenty of them in memory to
show the first 2 kB.
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator, Iterator, Literal

from pydantic import BaseModel, Field

from .costs import Cost, CostTracker

SpanKind = Literal["agent", "step", "loop", "iteration", "llm", "tool", "api"]
SpanStatus = Literal["running", "ok", "error", "skipped"]

MAX_VALUE_CHARS = 2000


def _truncate(value: Any, limit: int = MAX_VALUE_CHARS) -> Any:
    """Shrink big payloads at record time, keeping the shape recognisable."""
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return value[:limit] + f"… [+{len(value) - limit} chars]"
    if isinstance(value, dict):
        return {k: _truncate(v, limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        items = [_truncate(v, limit) for v in value[:50]]
        if len(value) > 50:
            items.append(f"… [+{len(value) - 50} items]")
        return items
    if isinstance(value, BaseModel):
        return _truncate(value.model_dump(), limit)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _truncate(repr(value), limit)


class Span(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    parent_id: str | None = None
    name: str
    kind: SpanKind = "step"
    status: SpanStatus = "running"
    started_at: float = Field(default_factory=time.time)
    ended_at: float | None = None
    input: Any = None
    output: Any = None
    error: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    cost: Cost | None = None
    children: list["Span"] = Field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return (self.ended_at or time.time()) - self.started_at

    def set_input(self, value: Any) -> None:
        self.input = _truncate(value)

    def set_output(self, value: Any) -> None:
        self.output = _truncate(value)

    def annotate(self, **attributes: Any) -> None:
        for key, value in attributes.items():
            self.attributes[key] = _truncate(value)

    def total_usd(self) -> float:
        own = self.cost.usd if self.cost else 0.0
        return own + sum(child.total_usd() for child in self.children)

    def walk(self) -> Iterator["Span"]:
        yield self
        for child in self.children:
            yield from child.walk()

    def render(self, indent: int = 0) -> str:
        mark = {"ok": "✓", "error": "✗", "running": "…", "skipped": "-"}[self.status]
        bits = [f"{'  ' * indent}{mark} {self.name} ({self.kind}) {self.duration_s:.2f}s"]
        if self.cost and self.cost.usd:
            bits.append(f"${self.cost.usd:.5f}")
        if self.cost and self.cost.cached:
            bits.append("[cached]")
        if self.error:
            bits.append(f"!! {self.error}")
        line = " ".join(bits)
        return "\n".join([line] + [c.render(indent + 1) for c in self.children])


Span.model_rebuild()


class Trace(BaseModel):
    """One run. ``root`` is the agent span; everything else hangs off it."""

    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    agent: str = ""
    started_at: float = Field(default_factory=time.time)
    ended_at: float | None = None
    root: Span
    costs: CostTracker = Field(default_factory=CostTracker)

    @property
    def duration_s(self) -> float:
        return (self.ended_at or time.time()) - self.started_at

    def spans(self) -> Iterator[Span]:
        return self.root.walk()

    def find(self, name: str) -> list[Span]:
        return [s for s in self.spans() if s.name == name]

    def render(self) -> str:
        header = (
            f"trace {self.run_id} agent={self.agent!r} "
            f"{self.duration_s:.2f}s ${self.costs.total_usd:.5f} "
            f"({self.costs.calls} calls)"
        )
        return header + "\n" + self.root.render()

    def to_json(self, indent: int | None = 2) -> str:
        return self.model_dump_json(indent=indent)


_current_span: ContextVar[Span | None] = ContextVar("rsi_arena_current_span", default=None)


class Tracer:
    """Creates spans and keeps the parent pointer straight.

    A tracer is per-run and cheap. Pass one down, or let the context variable
    do it — :meth:`span` reads the current span from the context, so a caller
    that never sees the tracer still nests correctly.
    """

    def __init__(self, agent: str = "", costs: CostTracker | None = None) -> None:
        root = Span(name=agent or "run", kind="agent")
        self.trace = Trace(agent=agent, root=root, costs=costs or CostTracker())
        self._token = _current_span.set(root)

    @property
    def root(self) -> Span:
        return self.trace.root

    @property
    def costs(self) -> CostTracker:
        return self.trace.costs

    def current(self) -> Span:
        return _current_span.get() or self.root

    @contextmanager
    def _attach(self, span: Span) -> Iterator[Span]:
        parent = self.current()
        span.parent_id = parent.id
        parent.children.append(span)
        token = _current_span.set(span)
        try:
            yield span
        finally:
            _current_span.reset(token)

    @asynccontextmanager
    async def span(
        self,
        name: str,
        kind: SpanKind = "step",
        *,
        input: Any = None,
        **attributes: Any,
    ) -> AsyncIterator[Span]:
        """Open a span; it closes ``ok`` or ``error`` depending on the body."""
        span = Span(name=name, kind=kind)
        if input is not None:
            span.set_input(input)
        if attributes:
            span.annotate(**attributes)
        with self._attach(span):
            try:
                yield span
            except BaseException as exc:  # noqa: BLE001 - recorded then re-raised
                span.status = "error"
                span.error = f"{type(exc).__name__}: {exc}"
                span.ended_at = time.time()
                raise
            else:
                if span.status == "running":
                    span.status = "ok"
                span.ended_at = time.time()

    def record_cost(self, kind: str, name: str, cost: Cost, span: Span | None = None) -> None:
        """Attach a cost to a span *and* to the run ledger.

        Both, because the span answers "what did this step cost" and the
        ledger answers "have we hit the ceiling" — and the ledger raises
        :class:`~rsi_arena.costs.BudgetExceeded` from here when it has.
        """
        target = span or self.current()
        target.cost = cost
        self.costs.add(kind, name, cost, span_id=target.id)

    def finish(self, output: Any = None, error: BaseException | None = None) -> Trace:
        if output is not None:
            self.root.set_output(output)
        if error is not None:
            self.root.status = "error"
            self.root.error = f"{type(error).__name__}: {error}"
        elif self.root.status == "running":
            self.root.status = "ok"
        self.root.ended_at = time.time()
        self.trace.ended_at = self.root.ended_at
        try:
            _current_span.reset(self._token)
        except ValueError:
            _current_span.set(None)
        return self.trace


def current_span() -> Span | None:
    """The span an inner call should attach to, if a run is in progress."""
    return _current_span.get()
