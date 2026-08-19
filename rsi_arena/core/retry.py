"""Retry policy shared by the LLM and API clients.

The rules that matter, both learned from OpenRouter's documented behaviour:

* **Not every failure is retryable.** 400 (bad params), 401 (bad key) and 402
  (out of credits) will fail identically forever; retrying them burns the
  clock. Only timeouts, connection errors, 408, 429 and 5xx get another try.
* **``Retry-After`` beats exponential backoff.** When the provider says how
  long to wait, waiting exactly that long is both faster and politer than a
  doubling guess.

Backoff is exponential with full jitter. Full rather than partial jitter
because the common failure here is a fan-out of parallel calls all hitting a
limit at once, and synchronised retries just reproduce the collision.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")

# 408 request timeout, 409 conflict, 425 too early, 429 rate limited, 5xx.
DEFAULT_RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class RetryableError(Exception):
    """Raised by a caller to force another attempt (e.g. a bad JSON parse)."""


class FatalError(Exception):
    """Raised to stop retrying immediately regardless of policy."""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    initial_backoff: float = 0.5
    max_backoff: float = 30.0
    multiplier: float = 2.0
    jitter: bool = True
    retry_status: frozenset[int] = field(default=DEFAULT_RETRY_STATUS)

    def backoff(self, attempt: int) -> float:
        """Delay before ``attempt`` (1-indexed: attempt 1 already happened)."""
        raw = min(self.max_backoff, self.initial_backoff * self.multiplier ** (attempt - 1))
        return random.uniform(0.0, raw) if self.jitter else raw


@dataclass
class Attempt:
    """What happened on one try, recorded so the trace can show the retries."""

    number: int
    error: str
    status: int | None
    delay: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.number,
            "error": self.error,
            "status": self.status,
            "delay_s": round(self.delay, 3),
        }


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    is_retryable: Callable[[BaseException], bool],
    retry_after: Callable[[BaseException], float | None] = lambda _: None,
    status_of: Callable[[BaseException], int | None] = lambda _: None,
    on_retry: Callable[[Attempt], None] | None = None,
) -> T:
    """Call ``fn`` until it succeeds or the policy runs out.

    ``retry_after`` lets the caller pull a server-specified delay out of the
    exception; when it returns a number, that wins over the computed backoff.
    """
    last: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await fn()
        except FatalError:
            raise
        except BaseException as exc:  # noqa: BLE001 - classified below
            if isinstance(exc, asyncio.CancelledError):
                raise
            if attempt >= policy.max_attempts or not is_retryable(exc):
                raise
            last = exc
            server_delay = retry_after(exc)
            delay = server_delay if server_delay is not None else policy.backoff(attempt)
            if on_retry is not None:
                on_retry(Attempt(attempt, f"{type(exc).__name__}: {exc}", status_of(exc), delay))
            await asyncio.sleep(delay)
    raise last if last is not None else RuntimeError("retry loop exited without result")
