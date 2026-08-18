"""Cache interface shared by the LLM and API clients.

The interface is deliberately two methods — ``get`` and ``set`` — so the
in-memory implementation here can be swapped for Redis without touching a
caller. Both are ``async`` even though :class:`MemoryCache` never awaits
anything, because a network-backed cache will, and changing the signature
later would be a breaking change everywhere.

Keys are content addresses: :func:`make_key` hashes a canonicalised copy of
the request, so an identical request always produces an identical key and a
changed parameter always produces a different one.

:func:`single_flight` deduplicates *concurrent* identical calls. That is a
separate problem from caching: a cache stops the second call once the first
has finished, while single-flight stops it when the first is still in the air.
Since the point of this module is running many calls in parallel, fanning out
the same prompt twice is a real and expensive mistake.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")

_MISSING = object()


def canonical(payload: Any) -> str:
    """Stable JSON for hashing: sorted keys, no incidental whitespace."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def make_key(namespace: str, payload: Any) -> str:
    """Content address for a request. Namespace keeps clients from colliding."""
    digest = hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()
    return f"{namespace}:{digest}"


class Cache(ABC):
    """Minimal cache contract. Implement these three and you can plug it in."""

    @abstractmethod
    async def get(self, key: str) -> Any | None:
        """Return the cached value, or ``None`` if absent or expired."""

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        """Store ``value``. ``ttl`` is in seconds; ``None`` means no expiry."""

    @abstractmethod
    async def clear(self) -> None:
        """Drop everything. Mostly for tests."""

    async def get_or_set(
        self,
        key: str,
        producer: Callable[[], Awaitable[T]],
        ttl: float | None = None,
    ) -> tuple[T, bool]:
        """Return ``(value, was_cached)``, computing and storing on a miss.

        The miss path runs under :func:`single_flight` on ``key``, so N
        concurrent misses produce one call to ``producer``.
        """
        hit = await self.get(key)
        if hit is not None:
            return hit, True

        async def produce() -> T:
            value = await producer()
            await self.set(key, value, ttl=ttl)
            return value

        return await single_flight(key, produce), False


class MemoryCache(Cache):
    """Process-local LRU with per-entry expiry.

    Bounded on purpose: an agent run that scrapes a hundred pages should not
    pin all of them for the life of the process. Eviction is strict LRU on
    ``max_entries``.
    """

    def __init__(self, max_entries: int = 4096) -> None:
        self.max_entries = max_entries
        self._data: OrderedDict[str, tuple[Any, float | None]] = OrderedDict()
        self._lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._data.get(key, _MISSING)
            if entry is _MISSING:
                self.misses += 1
                return None
            value, expires_at = entry  # type: ignore[misc]
            if expires_at is not None and expires_at < time.monotonic():
                del self._data[key]
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return value

    async def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        async with self._lock:
            expires_at = time.monotonic() + ttl if ttl is not None else None
            self._data[key] = (value, expires_at)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    async def clear(self) -> None:
        async with self._lock:
            self._data.clear()

    def stats(self) -> dict[str, int]:
        return {"entries": len(self._data), "hits": self.hits, "misses": self.misses}


class NullCache(Cache):
    """Caches nothing. Use when you want every call to actually go out."""

    async def get(self, key: str) -> Any | None:
        return None

    async def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        return None

    async def clear(self) -> None:
        return None


# --- single flight ----------------------------------------------------------

_inflight: dict[str, asyncio.Future] = {}


async def single_flight(key: str, producer: Callable[[], Awaitable[T]]) -> T:
    """Run ``producer`` once per key even if called concurrently N times.

    Followers await the leader's future, so an exception propagates to all of
    them — which is correct: they would each have raised it anyway.
    """
    existing = _inflight.get(key)
    if existing is not None:
        return await asyncio.shield(existing)

    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    _inflight[key] = future
    try:
        result = await producer()
    except BaseException as exc:  # noqa: BLE001 - re-raised below
        if not future.done():
            future.set_exception(exc)
        # Followers get the exception via the future; swallow the "never
        # retrieved" warning for the leader's own copy.
        future.exception()
        raise
    else:
        if not future.done():
            future.set_result(result)
        return result
    finally:
        _inflight.pop(key, None)


# --- default cache ----------------------------------------------------------

_default_cache: Cache = MemoryCache()


def default_cache() -> Cache:
    return _default_cache


def set_default_cache(cache: Cache) -> None:
    """Swap the process-wide default. This is the Redis seam."""
    global _default_cache
    _default_cache = cache
