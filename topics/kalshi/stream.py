"""WebSocket consumer — true tick resolution instead of 1Hz polling.

Kalshi authenticates the socket itself, even for public channels, so this is the
one part of the package that needs credentials. Sign ``/trade-api/ws/v2`` with
method GET, no query string — signing the REST path instead is the usual cause
of a handshake rejection.

The order book is maintained locally: one ``orderbook_snapshot`` establishes
state, then ``orderbook_delta`` messages apply on top. Deltas before the
snapshot are dropped, because applying them to an empty book yields a book that
is quietly wrong rather than obviously broken.

Wire format, confirmed against the live socket on 2026-08-17 — it does not
match what the schema names suggest:

* ``seq`` sits at the **top level** of the frame, not inside ``msg``
* snapshot levels are ``yes_dollars_fp`` / ``no_dollars_fp``, as
  ``[["0.0100", "16.00"], ...]`` — dollar strings, not integer cents
* a delta carries ``price_dollars``, ``delta_fp`` and ``side``
* the channel is ``ticker``; ``ticker_v2`` is rejected as an unknown channel
* **``seq`` counts per subscription, not per market.** Every frame on one
  ``sid`` shares the counter, so a given market sees gaps that are simply
  other markets' messages. Continuity must be checked at the subscription
  level; checking it per book reports a desync on essentially every delta.

    stream = KalshiStream(["KXMLBGAME-26AUG17STLCIN-CIN"])
    stream.on_book = lambda t, b: print(t, b.best_bid, b.best_ask)
    asyncio.run(stream.run())
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from .client import WS_URL, KalshiClient

CHANNELS = ("orderbook_delta", "ticker", "trade")


def _cents(value) -> int:
    """Dollar string or number to integer cents. ``"0.0100"`` -> 1."""
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return 0


def _levels(raw) -> dict[int, float]:
    """``[["0.0100", "16.00"], ...]`` to ``{1: 16.0, ...}``."""
    out: dict[int, float] = {}
    for entry in raw or []:
        try:
            price, size = entry[0], entry[1]
        except (IndexError, TypeError):
            continue
        qty = float(size)
        if qty > 0:
            out[_cents(price)] = qty
    return out


@dataclass
class LiveBook:
    """Local order book, rebuilt from a snapshot plus deltas.

    Levels are ``{price_cents: contracts}`` on each side. The wire carries
    dollar strings; they are normalised to integer cents here so the two sides
    are directly comparable. Kalshi quotes yes and no separately — a no bid at
    40c is a yes offer at 60c.
    """

    ticker: str
    yes: dict[int, float] = field(default_factory=dict)
    no: dict[int, float] = field(default_factory=dict)
    seq: int = 0
    synced: bool = False
    updated_at: str = ""

    def apply_snapshot(self, msg: dict, seq: int = 0) -> None:
        self.yes = _levels(msg.get("yes_dollars_fp") or msg.get("yes"))
        self.no = _levels(msg.get("no_dollars_fp") or msg.get("no"))
        self.seq = seq
        self.synced = True
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def apply_delta(self, msg: dict, seq: int = 0) -> bool:
        """Apply one delta. Returns False if this book has no snapshot yet.

        Sequence continuity is not checked here — ``seq`` belongs to the
        subscription, not the market, so the stream checks it once per frame
        and marks every book on that subscription desynced if it breaks.
        """
        if not self.synced:
            return False
        self.seq = seq or self.seq
        side = self.yes if msg.get("side") == "yes" else self.no
        price = _cents(msg.get("price_dollars", msg.get("price", 0)))
        delta = float(msg.get("delta_fp", msg.get("delta", 0)) or 0)
        new = side.get(price, 0.0) + delta
        if new > 0:
            side[price] = new
        else:
            side.pop(price, None)
        self.updated_at = datetime.now(timezone.utc).isoformat()
        return True

    @property
    def best_bid(self) -> int | None:
        return max(self.yes) if self.yes else None

    @property
    def best_ask(self) -> int | None:
        """Best yes offer, derived from the no side."""
        return 100 - max(self.no) if self.no else None

    @property
    def mid(self) -> float | None:
        bid, ask = self.best_bid, self.best_ask
        return (bid + ask) / 2 if bid is not None and ask is not None else None


class KalshiStream:
    """Subscribe to market channels and keep local books in sync."""

    def __init__(self, tickers: list[str], client: KalshiClient | None = None,
                 channels: tuple[str, ...] = CHANNELS) -> None:
        self.tickers = tickers
        self.client = client or KalshiClient()
        self.channels = channels
        self.books: dict[str, LiveBook] = defaultdict(lambda: LiveBook(""))
        self._cmd_id = 0
        self._sid_seq: dict[int, int] = {}   # subscription-level continuity

        # Callbacks — assign to consume. All are optional.
        self.on_book: Callable[[str, LiveBook], None] | None = None
        self.on_ticker: Callable[[str, dict], None] | None = None
        self.on_trade: Callable[[str, dict], None] | None = None
        self.on_desync: Callable[[str], None] | None = None

    def _next_id(self) -> int:
        self._cmd_id += 1
        return self._cmd_id

    async def run(self, reconnect: bool = True) -> None:
        """Connect and consume until cancelled, reconnecting with backoff."""
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("pip install websockets to use the stream") from exc

        if not self.client.is_authenticated:
            raise RuntimeError(
                "The WebSocket needs credentials even for public channels. "
                "Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH, or poll "
                "with recorder.py instead."
            )

        backoff = 1.0
        while True:
            try:
                headers = self.client.ws_auth_headers()
                async with websockets.connect(
                    WS_URL, additional_headers=headers, ping_interval=10
                ) as ws:
                    await self._subscribe(ws)
                    backoff = 1.0
                    async for raw in ws:
                        self._handle(json.loads(raw))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not reconnect:
                    raise
                print(f"[stream] {exc}; reconnecting in {backoff:.0f}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def _subscribe(self, ws) -> None:
        for channel in self.channels:
            await ws.send(json.dumps({
                "id": self._next_id(), "cmd": "subscribe",
                "params": {"channels": [channel], "market_tickers": self.tickers},
            }))

    def _check_sequence(self, sid: int, seq: int) -> bool:
        """Subscription-level continuity. False means a frame was missed."""
        if not seq:
            return True
        previous = self._sid_seq.get(sid)
        self._sid_seq[sid] = seq
        return previous is None or seq == previous + 1

    def _handle(self, msg: dict) -> None:
        kind, payload = msg.get("type"), msg.get("msg") or {}
        seq = int(msg.get("seq") or 0)          # top level, and per subscription
        sid = int(msg.get("sid") or 0)
        ticker = payload.get("market_ticker") or payload.get("ticker") or ""

        if kind in ("orderbook_snapshot", "orderbook_delta"):
            if not self._check_sequence(sid, seq):
                for book in self.books.values():
                    book.synced = False
                if self.on_desync:
                    self.on_desync(ticker)

        if kind == "orderbook_snapshot":
            book = self.books.setdefault(ticker, LiveBook(ticker))
            book.ticker = ticker
            book.apply_snapshot(payload, seq)
            if self.on_book:
                self.on_book(ticker, book)

        elif kind == "orderbook_delta":
            book = self.books.setdefault(ticker, LiveBook(ticker))
            book.ticker = ticker
            if book.apply_delta(payload, seq) and self.on_book:
                self.on_book(ticker, book)

        elif kind == "ticker" and self.on_ticker:
            self.on_ticker(ticker, payload)

        elif kind == "trade" and self.on_trade:
            self.on_trade(ticker, payload)

        elif kind == "error":
            print(f"[stream] error: {payload}")
