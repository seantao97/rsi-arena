"""Persistence for eval results.

Async throughout so a real database drops in where the in-memory store sits.

Identity lives here rather than on :class:`EvalOutput`. An eval result is a
verdict — a score, why, and what it was scored against — and an id is a fact
about having stored it. Keeping the two apart means an eval can be run and read
without a store anywhere in the picture, which is most of the time.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoids a cycle: the store is typed against results only
    from .eval import EvalOutput


@dataclass
class StoredEval:
    """One result, plus what is needed to find it again."""

    output: "EvalOutput"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    agent: str = ""
    name: str = ""
    created_at: float = field(default_factory=time.time)

    def row(self) -> dict:
        """The compact shape a listing returns — no trace, no payloads."""
        return {"id": self.id, "agent": self.agent, "name": self.name,
                "score": self.output.score, "comments": self.output.comments,
                "created_at": self.created_at}


class EvalStore(ABC):
    """Persistence for eval results."""

    @abstractmethod
    async def save(self, output: "EvalOutput", *, agent: str = "",
                   name: str = "") -> str:
        """Store one result and return its id."""

    @abstractmethod
    async def get(self, eval_id: str) -> StoredEval | None:
        """One result by id, or ``None``."""

    @abstractmethod
    async def list(self, *, agent: str | None = None, name: str | None = None,
                   limit: int = 100, offset: int = 0) -> list[StoredEval]:
        """Results, newest first, optionally filtered by agent or eval name."""

    @abstractmethod
    async def count(self, *, agent: str | None = None,
                    name: str | None = None) -> int:
        """How many results match, ignoring ``limit``/``offset``."""

    @abstractmethod
    async def delete(self, eval_id: str) -> bool:
        """Remove one result. ``True`` if it was there."""

    @abstractmethod
    async def clear(self) -> None:
        """Drop everything. Mostly for tests."""

    async def leaderboard(self, *, name: str | None = None) -> list[dict]:
        """Mean score per agent, best first. Counts, not a rating.

        Defined on the base class because it is derivable from ``list`` — a
        backend with a GROUP BY can override it, and one without still answers.
        """
        rows = await self.list(name=name, limit=100_000)
        by_agent: dict[str, list[float]] = {}
        for row in rows:
            by_agent.setdefault(row.agent, []).append(row.output.score)
        table = [{"agent": agent, "runs": len(scores),
                  "mean_score": sum(scores) / len(scores)}
                 for agent, scores in by_agent.items() if scores]
        return sorted(table, key=lambda r: r["mean_score"], reverse=True)


class InMemoryEvalStore(EvalStore):
    """The default. Bounded, so a long-running server cannot grow without end."""

    def __init__(self, max_results: int = 5000) -> None:
        self.max_results = max_results
        self._results: dict[str, StoredEval] = {}
        self._lock = asyncio.Lock()

    async def save(self, output: "EvalOutput", *, agent: str = "",
                   name: str = "") -> str:
        record = StoredEval(output=output, agent=agent,
                            name=name or output.description)
        async with self._lock:
            self._results[record.id] = record
            while len(self._results) > self.max_results:
                self._results.pop(next(iter(self._results)))
        return record.id

    async def get(self, eval_id: str) -> StoredEval | None:
        return self._results.get(eval_id)

    def _matching(self, agent: str | None, name: str | None) -> list[StoredEval]:
        rows = list(self._results.values())
        if agent:
            rows = [r for r in rows if r.agent == agent]
        if name:
            rows = [r for r in rows if r.name == name]
        return sorted(rows, key=lambda r: r.created_at, reverse=True)

    async def list(self, *, agent: str | None = None, name: str | None = None,
                   limit: int = 100, offset: int = 0) -> list[StoredEval]:
        return self._matching(agent, name)[offset:offset + limit]

    async def count(self, *, agent: str | None = None,
                    name: str | None = None) -> int:
        return len(self._matching(agent, name))

    async def delete(self, eval_id: str) -> bool:
        async with self._lock:
            return self._results.pop(eval_id, None) is not None

    async def clear(self) -> None:
        async with self._lock:
            self._results.clear()


_default: EvalStore = InMemoryEvalStore()


def default_eval_store() -> EvalStore:
    return _default


def set_default_eval_store(store: EvalStore) -> None:
    global _default
    _default = store
