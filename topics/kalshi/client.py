"""Kalshi REST client: pagination, rate limiting, retries, optional auth.

Read endpoints (``/series``, ``/events``, ``/markets``, ``/markets/*/orderbook``)
are public and need no credentials — verified 2026-08-17. Credentials are only
required for the WebSocket and for anything under ``/portfolio``.

Rate limits use a token-cost model with separate read and write buckets. The
Basic tier refills 200 read tokens per second; most read endpoints cost one.
``RateLimiter`` below is deliberately conservative because a 429 costs more than
the delay it avoids.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterator

API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"

# Per-second read-token budget by account tier.
TIER_READ_BUDGET = {
    "basic": 200, "advanced": 300, "expert": 600,
    "premier": 1000, "paragon": 2000, "prime": 4000,
}


class RateLimiter:
    """Token bucket matching Kalshi's per-second read budget."""

    def __init__(self, per_second: int = 200, safety: float = 0.7) -> None:
        self.capacity = max(1.0, per_second * safety)
        self.rate = self.capacity
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def take(self, cost: int = 1) -> None:
        with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._last) * self.rate
                )
                self._last = now
                if self._tokens >= cost:
                    self._tokens -= cost
                    return
                time.sleep((cost - self._tokens) / self.rate)


@dataclass
class KalshiClient:
    """Minimal Kalshi REST client.

    ``key_id`` and ``private_key_pem`` are optional; supply them only for
    authenticated endpoints. Signing uses RSA-PSS over
    ``timestamp_ms + METHOD + path``.
    """

    key_id: str | None = None
    private_key_pem: str | None = None
    tier: str = "basic"
    timeout: int = 30
    max_retries: int = 4
    _limiter: RateLimiter = field(init=False)

    def __post_init__(self) -> None:
        self._limiter = RateLimiter(TIER_READ_BUDGET.get(self.tier, 200))

    # ---------- auth ----------

    def _sign(self, method: str, path: str) -> dict[str, str]:
        if not (self.key_id and self.private_key_pem):
            return {}
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("pip install cryptography for authenticated calls") from exc

        ts = str(int(time.time() * 1000))
        key = serialization.load_pem_private_key(self.private_key_pem.encode(), password=None)
        sig = key.sign(
            f"{ts}{method}{path}".encode(),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
            "KALSHI-ACCESS-TIMESTAMP": ts,
        }

    def ws_auth_headers(self) -> dict[str, str]:
        """Headers for the WebSocket handshake.

        Kalshi authenticates the socket itself even for public channels. Sign
        ``WS_PATH`` with method GET — not the REST path, and without any query
        string.
        """
        return self._sign("GET", WS_PATH)

    # ---------- transport ----------

    def get(self, path: str, params: dict[str, Any] | None = None, cost: int = 1) -> dict:
        """GET a path under the API base, with retry on 429 and 5xx."""
        qs = urllib.parse.urlencode(
            {k: v for k, v in (params or {}).items() if v is not None}, doseq=True
        )
        full_path = f"/trade-api/v2{path}"
        url = f"{API_BASE}{path}" + (f"?{qs}" if qs else "")

        for attempt in range(self.max_retries + 1):
            self._limiter.take(cost)
            headers = {"User-Agent": "rsi-arena/1.0", "Accept": "application/json"}
            headers.update(self._sign("GET", full_path))
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    time.sleep(min(2 ** attempt * 0.5, 8.0))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError):
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt * 0.5, 8.0))
                    continue
                raise
        raise RuntimeError(f"exhausted retries for {path}")

    def paginate(
        self, path: str, key: str, params: dict[str, Any] | None = None,
        page_size: int = 1000, max_items: int | None = None,
    ) -> Iterator[dict]:
        """Yield every object from a cursor-paginated list endpoint."""
        params = dict(params or {})
        params["limit"] = page_size
        cursor, seen = None, 0
        while True:
            if cursor:
                params["cursor"] = cursor
            page = self.get(path, params)
            items = page.get(key) or []
            for item in items:
                yield item
                seen += 1
                if max_items and seen >= max_items:
                    return
            cursor = page.get("cursor")
            if not cursor or not items:
                return
