"""Where eval results go.

:class:`EvalStore` is the interface; :class:`InMemoryEvalStore` is the only
implementation today. Everything is ``async`` even though the in-memory store
never awaits anything, because the next implementation is a database and a
synchronous interface would have to be rewritten at every call site to get
there. This is the seam, and it costs nothing to leave it open now.

The same shape as :mod:`rsi_arena.core.cache`: an abstract base, a default
instance, and ``set_default_eval_store`` to swap it.

    set_default_eval_store(PostgresEvalStore(dsn))   # later, and nothing else changes

Writes are append-only in spirit. A stored result is a record of what an agent
did on a prompt at a moment; editing one would make the history a worse
record, so there is no update method — only :meth:`EvalStore.save`,
:meth:`EvalStore.delete` and :meth:`EvalStore.clear`.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # avoids a cycle: results import the store's type for typing only
    from .eval import EvalResult, SuiteResult


class EvalStore(ABC):
    """Persistence for eval results. Async so a DB can drop straight in."""

    # -- single results --

    @abstractmethod
    async def save(self, result: "EvalResult") -> str:
        """Store one result and return its id."""

    @abstractmethod
    async def get(self, eval_id: str) -> "EvalResult | None":
        """One result by id, or ``None``."""

    @abstractmethod
    async def list(
        self,
        *,
        agent: str | None = None,
        name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list["EvalResult"]:
        """Results, newest first, optionally filtered by agent or eval name."""

    @abstractmethod
    async def count(self, *, agent: str | None = None, name: str | None = None) -> int:
        """How many results match, ignoring ``limit``/``offset``."""

    @abstractmethod
    async def delete(self, eval_id: str) -> bool:
        """Remove one result. ``True`` if it was there."""

    @abstractmethod
    async def clear(self) -> None:
        """Remove everything. Mostly for tests."""

    # -- suites --

    @abstractmethod
    async def save_suite(self, result: "SuiteResult") -> str:
        """Store one suite result — and its individual results with it."""

    @abstractmethod
    async def get_suite(self, suite_id: str) -> "SuiteResult | None": ...

    @abstractmethod
    async def list_suites(self, *, limit: int = 50, offset: int = 0) -> list["SuiteResult"]: ...

    # -- derived --

    async def leaderboard(self, *, name: str | None = None) -> list[dict[str, Any]]:
        """Mean score and pass rate per agent, over everything stored.

        Not a rating — the same deliberate choice ``server/store.py`` makes for
        votes. Counts and means do not pretend to be more than they are.
        """
        rows: dict[str, dict[str, Any]] = {}
        for result in await self.list(name=name, limit=10_000):
            row = rows.setdefault(
                result.agent,
                {"agent": result.agent, "evals": 0, "passed": 0, "failed": 0,
                 "errors": 0, "bailed_out": 0, "score_sum": 0.0, "cost_usd": 0.0},
            )
            row["evals"] += 1
            row["score_sum"] += result.score.value
            row["cost_usd"] += result.cost_usd
            if result.score.passed is True:
                row["passed"] += 1
            elif result.score.passed is False:
                row["failed"] += 1
            if not result.ok:
                row["errors"] += 1
            if result.bailed_out:
                row["bailed_out"] += 1
        out = []
        for row in rows.values():
            evals = row.pop("evals")
            score_sum = row.pop("score_sum")
            out.append({
                **row,
                "evals": evals,
                "mean_score": round(score_sum / evals, 4) if evals else 0.0,
                "pass_rate": round(row["passed"] / evals, 4) if evals else 0.0,
                "cost_usd": round(row["cost_usd"], 6),
            })
        return sorted(out, key=lambda r: (-r["mean_score"], r["agent"]))


class InMemoryEvalStore(EvalStore):
    """Everything in a dict, newest last. Dies with the process.

    ``max_results`` keeps a long-running backend from growing without bound;
    the oldest results are dropped first. Guarded by a lock because the backend
    runs evals concurrently and two of them finishing at once must not race the
    eviction.
    """

    def __init__(self, max_results: int = 5000) -> None:
        self.max_results = max_results
        self._results: dict[str, "EvalResult"] = {}
        self._suites: dict[str, "SuiteResult"] = {}
        self._lock = asyncio.Lock()

    async def save(self, result: "EvalResult") -> str:
        async with self._lock:
            self._results[result.id] = result
            while len(self._results) > self.max_results:
                self._results.pop(next(iter(self._results)))
        return result.id

    async def get(self, eval_id: str) -> "EvalResult | None":
        return self._results.get(eval_id)

    async def list(
        self,
        *,
        agent: str | None = None,
        name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list["EvalResult"]:
        return self._match(agent, name)[offset : offset + limit]

    async def count(self, *, agent: str | None = None, name: str | None = None) -> int:
        return len(self._match(agent, name))

    async def delete(self, eval_id: str) -> bool:
        async with self._lock:
            return self._results.pop(eval_id, None) is not None

    async def clear(self) -> None:
        async with self._lock:
            self._results.clear()
            self._suites.clear()

    async def save_suite(self, result: "SuiteResult") -> str:
        for one in result.results:
            await self.save(one)
        async with self._lock:
            self._suites[result.id] = result
            while len(self._suites) > self.max_results:
                self._suites.pop(next(iter(self._suites)))
        return result.id

    async def get_suite(self, suite_id: str) -> "SuiteResult | None":
        return self._suites.get(suite_id)

    async def list_suites(self, *, limit: int = 50, offset: int = 0) -> list["SuiteResult"]:
        newest = sorted(self._suites.values(), key=lambda s: s.created_at, reverse=True)
        return newest[offset : offset + limit]

    def _match(self, agent: str | None, name: str | None) -> list["EvalResult"]:
        rows = sorted(self._results.values(), key=lambda r: r.created_at, reverse=True)
        if agent:
            rows = [r for r in rows if r.agent == agent]
        if name:
            rows = [r for r in rows if r.name == name]
        return rows


_default: EvalStore = InMemoryEvalStore()


def default_eval_store() -> EvalStore:
    return _default


def set_default_eval_store(store: EvalStore) -> None:
    """Swap the process-wide store. The seam a database arrives through."""
    global _default
    _default = store
