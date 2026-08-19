"""What OpenRouter returns when it says no.

Split out from the client so that the retry policy, the tests and anything
catching a model failure can import the error without importing the client.
"""

from __future__ import annotations

from typing import Any

class OpenRouterError(RuntimeError):
    """An error from OpenRouter, carrying enough to decide whether to retry.

    Documented codes: 400 bad params, 401 bad key, 402 out of credits, 403
    moderation, 408 timeout, 429 rate limited, 502 model down, 503 no provider
    meets the routing requirements.
    """

    def __init__(
        self,
        status: int | None,
        message: str,
        metadata: dict[str, Any] | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(f"[{status}] {message}")
        self.status = status
        self.message = message
        self.metadata = metadata or {}
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        # 402 is explicitly not retryable: no amount of waiting adds credits.
        return self.status in {408, 409, 425, 429, 500, 502, 503, 504}
