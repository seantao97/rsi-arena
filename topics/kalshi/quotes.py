"""Market state — now, and at a past instant.

This is the "get the state of xyz market at this time" half.

Three sources, in increasing resolution:

* ``get_market`` / ``get_orderbook`` — live, from the exchange
* ``get_candles`` — Kalshi's own OHLC history, minute resolution, no local
  storage needed but coarse
* ``state_at`` — local recorded snapshots, as fine as the recorder was run

For point-in-time work, ``state_at`` is the one that matters: it never returns
anything stamped after the requested instant, which is what keeps a replay
honest.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from .client import KalshiClient


@dataclass(frozen=True)
class Quote:
    """A market's quoted state at one instant."""

    ticker: str
    ts: str                       # ISO8601 UTC
    yes_bid: float | None
    yes_ask: float | None
    yes_bid_size: float | None
    yes_ask_size: float | None
    last: float | None
    volume: float
    open_interest: float
    status: str
    source: str                   # "rest" | "candle" | "snapshot"

    @property
    def mid(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return (self.yes_bid + self.yes_ask) / 2

    @property
    def spread(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return self.yes_ask - self.yes_bid

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class OrderBook:
    """Resting depth. Kalshi quotes both sides in cents, 1..99."""

    ticker: str
    ts: str
    yes: list[tuple[int, int]]    # [(price_cents, contracts)], best first
    no: list[tuple[int, int]]

    def depth_within(self, side: str, cents: int) -> int:
        """Contracts available within ``cents`` of the best price on a side."""
        levels = self.yes if side == "yes" else self.no
        if not levels:
            return 0
        best = levels[0][0]
        return sum(qty for px, qty in levels if abs(px - best) <= cents)


def _f(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Quotes:
    """Read market state from the exchange or from recorded snapshots."""

    def __init__(self, client: KalshiClient | None = None,
                 db_path: str | None = None) -> None:
        self.client = client or KalshiClient()
        self.db_path = db_path

    # ---------- live ----------

    def get_market(self, ticker: str) -> Quote:
        """Current quoted state for one market."""
        m = self.client.get(f"/markets/{ticker}").get("market", {})
        return Quote(
            ticker=ticker, ts=_now(),
            yes_bid=_f(m.get("yes_bid_dollars")),
            yes_ask=_f(m.get("yes_ask_dollars")),
            yes_bid_size=_f(m.get("yes_bid_size_fp")),
            yes_ask_size=_f(m.get("yes_ask_size_fp")),
            last=_f(m.get("last_price_dollars")),
            volume=_f(m.get("volume_fp")) or 0.0,
            open_interest=_f(m.get("open_interest_fp")) or 0.0,
            status=m.get("status", ""), source="rest",
        )

    def get_markets(self, tickers: list[str]) -> dict[str, Quote]:
        """Batch fetch. Kalshi accepts a comma-joined ``tickers`` filter, which
        is far cheaper than one call per market when watching a slate."""
        out: dict[str, Quote] = {}
        for i in range(0, len(tickers), 100):
            chunk = tickers[i:i + 100]
            page = self.client.get("/markets", {"tickers": ",".join(chunk),
                                                "limit": len(chunk)})
            ts = _now()
            for m in page.get("markets", []):
                out[m["ticker"]] = Quote(
                    ticker=m["ticker"], ts=ts,
                    yes_bid=_f(m.get("yes_bid_dollars")),
                    yes_ask=_f(m.get("yes_ask_dollars")),
                    yes_bid_size=_f(m.get("yes_bid_size_fp")),
                    yes_ask_size=_f(m.get("yes_ask_size_fp")),
                    last=_f(m.get("last_price_dollars")),
                    volume=_f(m.get("volume_fp")) or 0.0,
                    open_interest=_f(m.get("open_interest_fp")) or 0.0,
                    status=m.get("status", ""), source="rest",
                )
        return out

    def get_orderbook(self, ticker: str, depth: int = 10) -> OrderBook:
        """Resting depth on both sides."""
        book = self.client.get(f"/markets/{ticker}/orderbook",
                               {"depth": depth}).get("orderbook", {})
        return OrderBook(
            ticker=ticker, ts=_now(),
            yes=[(int(p), int(q)) for p, q in (book.get("yes") or [])],
            no=[(int(p), int(q)) for p, q in (book.get("no") or [])],
        )

    def get_trades(self, ticker: str, limit: int = 200) -> list[dict]:
        """Recent prints for a market."""
        return list(self.client.paginate(
            "/markets/trades", "trades", {"ticker": ticker}, max_items=limit))

    # ---------- history ----------

    def get_candles(
        self, series_ticker: str, ticker: str,
        start: datetime, end: datetime, interval_minutes: int = 1,
    ) -> list[dict]:
        """Kalshi's own OHLC history.

        Free and needs no recorder, but minute resolution at best and it does
        not carry depth. Use it for backfill; use ``state_at`` for anything
        where the book matters.
        """
        return self.client.get(
            f"/series/{series_ticker}/markets/{ticker}/candlesticks",
            {"start_ts": int(start.timestamp()), "end_ts": int(end.timestamp()),
             "period_interval": interval_minutes},
        ).get("candlesticks", [])

    def state_at(self, ticker: str, when: datetime) -> Quote | None:
        """The last recorded snapshot at or before ``when``.

        Point-in-time by construction: it cannot return a quote stamped after
        the requested instant, so a replay cannot see its own future.
        """
        if not self.db_path:
            raise RuntimeError("state_at needs db_path pointing at a recorder database")
        iso = when.astimezone(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT ts, yes_bid, yes_ask, yes_bid_size, yes_ask_size,
                          last, volume, open_interest, status
                   FROM quotes WHERE ticker = ? AND ts <= ?
                   ORDER BY ts DESC LIMIT 1""",
                (ticker, iso),
            ).fetchone()
        if not row:
            return None
        return Quote(ticker=ticker, ts=row[0], yes_bid=row[1], yes_ask=row[2],
                     yes_bid_size=row[3], yes_ask_size=row[4], last=row[5],
                     volume=row[6] or 0.0, open_interest=row[7] or 0.0,
                     status=row[8] or "", source="snapshot")

    def path(self, ticker: str, start: datetime, end: datetime) -> list[Quote]:
        """Every recorded snapshot in a window, oldest first."""
        if not self.db_path:
            raise RuntimeError("path needs db_path pointing at a recorder database")
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT ts, yes_bid, yes_ask, yes_bid_size, yes_ask_size,
                          last, volume, open_interest, status
                   FROM quotes WHERE ticker = ? AND ts BETWEEN ? AND ?
                   ORDER BY ts ASC""",
                (ticker, start.astimezone(timezone.utc).isoformat(),
                 end.astimezone(timezone.utc).isoformat()),
            ).fetchall()
        return [Quote(ticker, r[0], r[1], r[2], r[3], r[4], r[5],
                      r[6] or 0.0, r[7] or 0.0, r[8] or "", "snapshot") for r in rows]

    def closing_price(self, ticker: str) -> float | None:
        """Last recorded mid before the market closed — the CLV benchmark."""
        if not self.db_path:
            raise RuntimeError("closing_price needs db_path")
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT yes_bid, yes_ask FROM quotes
                   WHERE ticker = ? AND yes_bid IS NOT NULL AND yes_ask IS NOT NULL
                   ORDER BY ts DESC LIMIT 1""", (ticker,)).fetchone()
        return (row[0] + row[1]) / 2 if row else None
