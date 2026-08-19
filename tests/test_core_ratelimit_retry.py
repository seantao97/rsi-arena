"""``rsi_arena.core.ratelimit`` and ``rsi_arena.core.retry``."""

from __future__ import annotations

import asyncio
import time

import pytest

from rsi_arena.core.ratelimit import RateLimit, RateLimiter, Unlimited
from rsi_arena.core.retry import Attempt, FatalError, RetryPolicy, with_retry


# --- rate limiting ----------------------------------------------------------


def test_rate_limit_builds_a_limiter():
    limiter = RateLimit(per_second=5, burst=10, concurrency=2).build()
    assert isinstance(limiter, RateLimiter) and limiter.per_second == 5


async def test_burst_is_free_then_the_rate_bites():
    limiter = RateLimiter(per_second=100, burst=2, concurrency=None)
    started = time.monotonic()
    for _ in range(2):
        await limiter.acquire()
    assert time.monotonic() - started < 0.02, "the burst should not wait"
    await limiter.acquire()
    assert time.monotonic() - started >= 0.008, "past the burst, the rate applies"


async def test_concurrency_ceiling_holds_the_third_caller():
    limiter = RateLimiter(per_second=None, concurrency=2)
    await limiter.acquire()
    await limiter.acquire()
    third = asyncio.create_task(limiter.acquire())
    await asyncio.sleep(0.01)
    assert not third.done(), "a third caller must wait for a slot"
    limiter.release()
    await asyncio.wait_for(third, timeout=1.0)


async def test_pause_for_holds_every_waiter():
    # One 429 should back off the whole fleet, not just the request that lost.
    limiter = RateLimiter(per_second=1000, concurrency=None)
    limiter.pause_for(0.05)
    started = time.monotonic()
    await limiter.acquire()
    assert time.monotonic() - started >= 0.04


async def test_unlimited_never_waits():
    limiter = Unlimited()
    started = time.monotonic()
    await asyncio.gather(*(limiter.acquire() for _ in range(50)))
    assert time.monotonic() - started < 0.05


async def test_a_cancelled_acquire_gives_its_slot_back():
    limiter = RateLimiter(per_second=0.001, burst=0, concurrency=1)
    task = asyncio.create_task(limiter.acquire())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The semaphore slot must be free, or one cancelled request wedges the client.
    await asyncio.wait_for(RateLimiter(concurrency=1).acquire(), timeout=1.0)


# --- retry ------------------------------------------------------------------


def test_backoff_grows_and_is_capped():
    policy = RetryPolicy(initial_backoff=1.0, multiplier=2.0, max_backoff=4.0, jitter=False)
    assert [policy.backoff(n) for n in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 4.0]


def test_jitter_stays_within_the_computed_delay():
    policy = RetryPolicy(initial_backoff=1.0, jitter=True)
    assert all(0.0 <= policy.backoff(1) <= 1.0 for _ in range(20))


async def test_retries_until_it_succeeds():
    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ValueError("not yet")
        return "ok"

    result = await with_retry(
        flaky,
        RetryPolicy(max_attempts=4, initial_backoff=0.0, jitter=False),
        is_retryable=lambda _: True,
    )
    assert result == "ok" and calls == 3


async def test_gives_up_after_max_attempts_and_reraises():
    async def always():
        raise ValueError("no")

    with pytest.raises(ValueError):
        await with_retry(
            always,
            RetryPolicy(max_attempts=2, initial_backoff=0.0, jitter=False),
            is_retryable=lambda _: True,
        )


async def test_a_non_retryable_error_is_raised_immediately():
    calls = 0

    async def permanent():
        nonlocal calls
        calls += 1
        raise ValueError("402: out of credits")

    with pytest.raises(ValueError):
        await with_retry(permanent, RetryPolicy(initial_backoff=0.0),
                         is_retryable=lambda _: False)
    assert calls == 1, "no amount of waiting adds credits"


async def test_fatal_error_stops_the_loop_regardless_of_policy():
    calls = 0

    async def fatal():
        nonlocal calls
        calls += 1
        raise FatalError("stop")

    with pytest.raises(FatalError):
        await with_retry(fatal, RetryPolicy(initial_backoff=0.0), is_retryable=lambda _: True)
    assert calls == 1


async def test_retry_after_beats_the_computed_backoff():
    seen: list[Attempt] = []
    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("429")
        return "ok"

    await with_retry(
        flaky,
        RetryPolicy(initial_backoff=10.0, jitter=False),
        is_retryable=lambda _: True,
        retry_after=lambda _: 0.0,
        status_of=lambda _: 429,
        on_retry=seen.append,
    )
    assert len(seen) == 1
    assert seen[0].delay == 0.0, "the server's Retry-After wins over guessing"
    assert seen[0].to_dict()["status"] == 429


async def test_cancellation_is_never_retried():
    async def cancelled():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await with_retry(cancelled, RetryPolicy(initial_backoff=0.0),
                         is_retryable=lambda _: True)
