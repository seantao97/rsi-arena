"""Locate Kalshi credentials.

Follows the environment convention already used elsewhere in this account's
projects, so a key configured for one tool works here unchanged:

    KALSHI_API_KEY_ID        the key id from the Kalshi dashboard
    KALSHI_PRIVATE_KEY_PATH  path to the PKCS#8 PEM file
    KALSHI_PRIVATE_KEY       the PEM inline, as an alternative to the path
    KALSHI_BASE_URL          override the API base (rarely needed)

Market data needs none of this — reads are public. Credentials are only
required for the WebSocket handshake and anything under ``/portfolio``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE = "https://api.elections.kalshi.com/trade-api/v2"


@dataclass(frozen=True)
class Credentials:
    key_id: str | None
    private_key_pem: str | None
    base_url: str

    @property
    def is_authenticated(self) -> bool:
        return bool(self.key_id and self.private_key_pem)

    def require(self) -> None:
        """Raise with an actionable message if credentials are missing."""
        if not self.is_authenticated:
            raise RuntimeError(
                "This call needs Kalshi API-key auth. Set KALSHI_API_KEY_ID and "
                "either KALSHI_PRIVATE_KEY_PATH or KALSHI_PRIVATE_KEY. "
                "Market-data reads do not need it."
            )


def load(env: dict[str, str] | None = None) -> Credentials:
    """Read credentials from the environment. Never raises on absence."""
    src = env if env is not None else os.environ
    pem = src.get("KALSHI_PRIVATE_KEY")
    if not pem:
        path = src.get("KALSHI_PRIVATE_KEY_PATH")
        if path:
            candidate = Path(path).expanduser()
            if candidate.is_file():
                pem = candidate.read_text()
    return Credentials(
        key_id=src.get("KALSHI_API_KEY_ID"),
        private_key_pem=pem,
        base_url=src.get("KALSHI_BASE_URL", DEFAULT_BASE).rstrip("/"),
    )


def status() -> str:
    """One-line human summary. Never prints key material."""
    creds = load()
    if creds.is_authenticated:
        tail = creds.key_id[-4:] if creds.key_id else "????"
        return f"authenticated (key ...{tail}), base {creds.base_url}"
    return f"public reads only — no key configured, base {creds.base_url}"
