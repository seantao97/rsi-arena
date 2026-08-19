"""Shared plumbing: the parts every call goes through, whatever it is calling.

Nothing here knows about models or vendors. A cache key, a token bucket, a
backoff schedule, a span and a cost record look the same whether the call
underneath was a chat completion or a weather API, which is exactly why they
live below both.

============================  =====================================================
:mod:`~rsi_arena.core.cache`      content-addressed cache plus single-flight
:mod:`~rsi_arena.core.costs`      usage, cost, the ledger and the budget ceiling
:mod:`~rsi_arena.core.ratelimit`  token bucket and concurrency ceiling
:mod:`~rsi_arena.core.retry`      backoff that respects ``Retry-After``
:mod:`~rsi_arena.core.trace`      the span tree a run emits
:mod:`~rsi_arena.core.template`   ``{{placeholder}}`` rendering and safe conditions
============================  =====================================================
"""

from __future__ import annotations

from .cache import Cache, MemoryCache, NullCache, default_cache, make_key, set_default_cache, single_flight
from .costs import BudgetExceeded, Cost, CostRecord, CostTracker, MaxSpendExceeded, Pricing, Usage
from .ratelimit import RateLimit, RateLimiter, Unlimited
from .retry import Attempt, FatalError, RetryableError, RetryPolicy, with_retry
from .template import ConditionError, evaluate, placeholders, render
from .trace import Span, Trace, Tracer, current_span

__all__ = [
    "Cache", "MemoryCache", "NullCache", "default_cache", "make_key", "set_default_cache",
    "single_flight",
    "BudgetExceeded", "MaxSpendExceeded", "Cost", "CostRecord", "CostTracker", "Pricing", "Usage",
    "RateLimit", "RateLimiter", "Unlimited",
    "Attempt", "FatalError", "RetryableError", "RetryPolicy", "with_retry",
    "ConditionError", "evaluate", "placeholders", "render",
    "Span", "Trace", "Tracer", "current_span",
]
