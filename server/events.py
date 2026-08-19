"""Turning an agent run into a Server-Sent Events stream.

An agent run takes ten to sixty seconds, so the UI cannot wait for it. The
runtime already emits span starts and ends through ``Tracer.on_event`` and
token deltas through ``StepContext.on_token``; this module funnels both into
one queue and encodes them as SSE.

The merge has to be lossless — a battle runs two agents concurrently into one
stream, and dropping a ``span_end`` leaves a row spinning forever in the UI.
So instead of racing a queue read against task completion, every run pushes a
private sentinel when it finishes and the drainer counts sentinels. Nothing is
ever cancelled mid-read.

Event types, all carrying ``side`` (``"a"``/``"b"``, or ``"a"`` for a single run):

``run_start``  the run began; carries the agent label
``span_start`` a step, tool or model call opened
``span_end``   it closed, with status, duration and cost
``cost``       a call was billed; carries the running total
``token``      a delta from a step marked ``stream=True``
``run_end``    finished, with the answer, the full trace and the ledger
``run_error``  the run raised before producing a result
``ping``       keepalive, emitted when nothing has happened for a while
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Awaitable, Callable

HEARTBEAT_S = 15.0
_SENTINEL = "__side_done__"


def encode(event: dict[str, Any]) -> str:
    """One SSE message. The event name is also in the payload, so a client
    that only listens to ``onmessage`` still knows what it received."""
    return f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"


class RunStream:
    """Collects events from one or more concurrent runs into one SSE stream."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []

    def hooks(self, side: str) -> tuple[Callable[[dict], None], Callable[[str], None]]:
        """The ``on_event`` and ``on_token`` callbacks for one side."""

        def on_event(event: dict[str, Any]) -> None:
            self.queue.put_nowait({**event, "side": side})

        def on_token(text: str) -> None:
            self.queue.put_nowait({"type": "token", "side": side, "text": text})

        return on_event, on_token

    def add(self, side: str, coro: Callable[[], Awaitable[dict[str, Any]]]) -> None:
        """Register one run. ``coro`` returns the ``run_end`` payload."""

        async def wrapped() -> None:
            try:
                payload = await coro()
                self.queue.put_nowait({"type": "run_end", "side": side, **payload})
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reported to the client
                self.queue.put_nowait({
                    "type": "run_error", "side": side,
                    "message": f"{type(exc).__name__}: {exc}",
                })
            finally:
                self.queue.put_nowait({"type": _SENTINEL, "side": side})

        self._tasks.append(asyncio.create_task(wrapped()))

    async def sse(self) -> AsyncIterator[str]:
        """Drain until every run has signalled done, then stop."""
        remaining = len(self._tasks)
        try:
            while remaining:
                try:
                    event = await asyncio.wait_for(self.queue.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    # Long model calls produce no events; without this a proxy
                    # or an idle-timeout kills the connection mid-run.
                    yield ": keepalive\n\n"
                    continue
                if event["type"] == _SENTINEL:
                    remaining -= 1
                    continue
                yield encode(event)
            # Drain anything queued between the last read and the final sentinel.
            while not self.queue.empty():
                event = self.queue.get_nowait()
                if event["type"] != _SENTINEL:
                    yield encode(event)
            yield encode({"type": "done"})
        finally:
            # The client hung up, or we finished. Either way nothing should
            # keep spending money on a run nobody is watching.
            for task in self._tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
