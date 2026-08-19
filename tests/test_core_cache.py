"""``rsi_arena.core.cache`` — keys, eviction, TTL and single-flight."""

from __future__ import annotations

import asyncio

import pytest

from rsi_arena.core.cache import (
    MemoryCache,
    NullCache,
    default_cache,
    make_key,
    set_default_cache,
    single_flight,
)


def test_key_is_content_addressed():
    assert make_key("llm", {"a": 1, "b": 2}) == make_key("llm", {"b": 2, "a": 1})
    assert make_key("llm", {"a": 1}) != make_key("llm", {"a": 2})
    assert make_key("llm", {"a": 1}) != make_key("api", {"a": 1})


async def test_get_set_and_stats():
    cache = MemoryCache()
    assert await cache.get("nope") is None
    await cache.set("k", {"v": 1})
    assert await cache.get("k") == {"v": 1}
    stats = cache.stats()
    assert stats["hits"] == 1 and stats["misses"] == 1 and stats["entries"] == 1


async def test_ttl_expires():
    cache = MemoryCache()
    await cache.set("k", "v", ttl=-1)
    assert await cache.get("k") is None, "an expired entry must not be served"


async def test_eviction_is_oldest_first():
    cache = MemoryCache(max_entries=2)
    for key in ("a", "b", "c"):
        await cache.set(key, key)
    assert await cache.get("a") is None
    assert await cache.get("c") == "c"


async def test_clear():
    cache = MemoryCache()
    await cache.set("k", "v")
    await cache.clear()
    assert await cache.get("k") is None


async def test_get_or_set_calls_producer_once():
    cache = MemoryCache()
    calls = 0

    async def produce():
        nonlocal calls
        calls += 1
        return "value"

    assert await cache.get_or_set("k", produce) == ("value", False)
    assert await cache.get_or_set("k", produce) == ("value", True)
    assert calls == 1


async def test_null_cache_never_stores():
    cache = NullCache()
    await cache.set("k", "v")
    assert await cache.get("k") is None


async def test_single_flight_collapses_concurrent_producers():
    calls = 0

    async def produce():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return calls

    results = await asyncio.gather(*(single_flight("same", produce) for _ in range(10)))
    assert calls == 1, "ten concurrent callers should produce one call"
    assert results == [1] * 10


async def test_single_flight_propagates_the_error_to_every_waiter():
    async def boom():
        await asyncio.sleep(0.01)
        raise ValueError("no")

    with pytest.raises(ValueError):
        await asyncio.gather(*(single_flight("boom", boom) for _ in range(3)))


def test_default_cache_is_swappable():
    original = default_cache()
    try:
        replacement = MemoryCache()
        set_default_cache(replacement)
        assert default_cache() is replacement
    finally:
        set_default_cache(original)
