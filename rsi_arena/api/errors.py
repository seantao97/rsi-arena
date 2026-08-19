"""Errors an API call can raise.

Separate from the client for the same reason as :mod:`rsi_arena.llm.errors`:
deciding whether something is retryable should not require importing a
connection pool.
"""

from __future__ import annotations

class APIError(RuntimeError):
    def __init__(self, api: str, status: int | None, message: str, retry_after: float | None = None):
        super().__init__(f"{api}: [{status}] {message}")
        self.api = api
        self.status = status
        self.message = message
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.status in {408, 409, 425, 429, 500, 502, 503, 504}


class MissingCredential(RuntimeError):
    """The API needs a key and the environment does not have one."""
