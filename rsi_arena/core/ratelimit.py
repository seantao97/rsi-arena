"""Async rate limiting: a token bucket plus a concurrency ceiling.

Two different limits matter and they are not interchangeable. A token bucket
caps the *rate* (requests per second), which is what providers meter. A
semaphore caps *concurrency* (requests in flight), which is what keeps a
fan-out of 500 prompts from opening 500 sockets. :class:`RateLimiter` applies
both, because in practice you always want both.

Deliberately conservative, for the same reason ``topics/kalshi/client.py`` is:
a 429 costs more time than the delay that avoids it, and on OpenRouter a
retried request can cost real money if the first attempt already generated
tokens.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimit:
    """Declarative limit, safe to put in a config or an API spec.

    ``per_second`` of ``None`` means unmetered rate; ``concurrency`` of
    ``None`` means unbounded in-flight requests.
    """

    per_second: float | None = None
    burst: float | None = None
    concurrency: int | None = 8

    def build(self) -> "RateLimiter":
        return RateLimiter(
            per_second=self.per_second,
            burst=self.burst,
            concurrency=self.concurrency,
        )


class RateLimiter:
    """Token bucket + semaphore. ``async with limiter:`` to take one slot."""

    def __init__(
        self,
        per_second: float | None = None,
        burst: float | None = None,
        concurrency: int | None = 8,
    ) -> None:
        self.per_second = per_second
        self.capacity = burst if burst is not None else max(1.0, per_second or 1.0)
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(concurrency) if concurrency else None
        # Set by the client when a provider hands back Retry-After; every
        # waiter respects it, so one 429 backs off the whole fleet rather
        # than just the unlucky request.
        self._paused_until = 0.0

    async def acquire(self, cost: float = 1.0) -> None:
        if self._sem is not None:
            await self._sem.acquire()
        try:
            await self._wait_for_tokens(cost)
        except BaseException:
            if self._sem is not None:
                self._sem.release()
            raise

    def release(self) -> None:
        if self._sem is not None:
            self._sem.release()

    async def _wait_for_tokens(self, cost: float) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                if now < self._paused_until:
                    delay = self._paused_until - now
                else:
                    if self.per_second is None:
                        return
                    self._tokens = min(
                        self.capacity,
                        self._tokens + (now - self._last) * self.per_second,
                    )
                    self._last = now
                    if self._tokens >= cost:
                        self._tokens -= cost
                        return
                    delay = (cost - self._tokens) / self.per_second
            await asyncio.sleep(delay)

    def pause_for(self, seconds: float) -> None:
        """Hold every waiter for ``seconds`` — the response to a Retry-After."""
        self._paused_until = max(self._paused_until, time.monotonic() + seconds)

    async def __aenter__(self) -> "RateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.release()


class Unlimited(RateLimiter):
    """No rate and no concurrency cap. Mostly for tests and cached replays."""

    def __init__(self) -> None:
        super().__init__(per_second=None, burst=None, concurrency=None)
