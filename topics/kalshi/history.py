"""Historical quotes, pulled from the API rather than a local store.

Kalshi keeps the whole life of every market in its candlestick endpoint, open or
settled, so there is nothing to record. Ask for the window you want and it is
served — which means no collector to run, no database to keep consistent, and no
possibility of a replay disagreeing with the exchange.

Three constraints, all found by hitting the endpoint on 2026-08-18:

* ``period_interval`` accepts **1, 60 or 1440 only** — minute, hour, day.
  5, 15 and 240 are rejected with a 400.
* A request may span at most **5000 periods**. That is 5000 minutes (3.47 days)
  at minute resolution. ``candles`` chunks automatically.
* The path needs the *series* ticker, which is not in the market object.
  ``resolve_series`` gets it from ``/events`` and caches it.

Settled markets answer exactly like open ones, so a market's full history is
available for as long as Kalshi keeps it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from .client import KalshiClient

MINUTE, HOUR, DAY = 1, 60, 1440
VALID_INTERVALS = (MINUTE, HOUR, DAY)
MAX_PERIODS = 5000


def _f(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Candle:
    """One period of a market's history.

    Bid and ask are always present; ``price`` fields are None in a period with
    no trades, which is most of them on a thin market. ``previous`` carries the
    last trade before the period, so a price is available even when volume is
    zero — use ``last`` rather than ``close`` when you want "what was it worth".
    """

    ticker: str
    ts: datetime                  # end of the period, UTC
    yes_bid_open: float | None
    yes_bid_close: float | None
    yes_bid_high: float | None
    yes_bid_low: float | None
    yes_ask_open: float | None
    yes_ask_close: float | None
    yes_ask_high: float | None
    yes_ask_low: float | None
    price_open: float | None
    price_close: float | None
    price_high: float | None
    price_low: float | None
    price_mean: float | None
    price_previous: float | None
    volume: float
    open_interest: float

    @property
    def two_sided(self) -> bool:
        """Whether a real book existed in this period.

        Once a market closes the book empties and quotes read bid 0.00 /
        ask 1.00. That is the absence of a market, not a 50/50 one, and taking
        a midpoint of it makes every contract appear to crash to 0.50 at
        settlement.
        """
        bid, ask = self.yes_bid_close, self.yes_ask_close
        if bid is None or ask is None:
            return False
        return not (bid <= 0.0 and ask >= 1.0)

    @property
    def mid(self) -> float | None:
        if not self.two_sided:
            return None
        return (self.yes_bid_close + self.yes_ask_close) / 2

    @property
    def spread(self) -> float | None:
        if self.yes_bid_close is None or self.yes_ask_close is None:
            return None
        return self.yes_ask_close - self.yes_bid_close

    @property
    def last(self) -> float | None:
        """Last traded price known at the end of this period."""
        return self.price_close if self.price_close is not None else self.price_previous

    @property
    def traded(self) -> bool:
        return self.volume > 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        return d


def _candle(ticker: str, raw: dict) -> Candle:
    bid, ask, price = raw.get("yes_bid") or {}, raw.get("yes_ask") or {}, raw.get("price") or {}
    return Candle(
        ticker=ticker,
        ts=datetime.fromtimestamp(int(raw.get("end_period_ts", 0)), tz=timezone.utc),
        yes_bid_open=_f(bid.get("open_dollars")), yes_bid_close=_f(bid.get("close_dollars")),
        yes_bid_high=_f(bid.get("high_dollars")), yes_bid_low=_f(bid.get("low_dollars")),
        yes_ask_open=_f(ask.get("open_dollars")), yes_ask_close=_f(ask.get("close_dollars")),
        yes_ask_high=_f(ask.get("high_dollars")), yes_ask_low=_f(ask.get("low_dollars")),
        price_open=_f(price.get("open_dollars")), price_close=_f(price.get("close_dollars")),
        price_high=_f(price.get("high_dollars")), price_low=_f(price.get("low_dollars")),
        price_mean=_f(price.get("mean_dollars")), price_previous=_f(price.get("previous_dollars")),
        volume=_f(raw.get("volume_fp")) or 0.0,
        open_interest=_f(raw.get("open_interest_fp")) or 0.0,
    )


class History:
    """Historical quotes for any market, open or settled, straight from the API."""

    def __init__(self, client: KalshiClient | None = None) -> None:
        self.client = client or KalshiClient()
        self._series: dict[str, str] = {}
        self._window: dict[str, tuple[datetime, datetime, str]] = {}

    # ---------- resolution ----------

    def resolve_series(self, ticker: str) -> str:
        """Series ticker for a market. Cached; costs one ``/events`` call."""
        event = ticker.rsplit("-", 1)[0]
        if event not in self._series:
            payload = self.client.get(f"/events/{event}")
            self._series[event] = (payload.get("event") or payload).get("series_ticker", "")
        return self._series[event]

    def market_window(self, ticker: str) -> tuple[datetime, datetime, str]:
        """``(open_time, close_time, status)`` for a market. Cached."""
        if ticker not in self._window:
            m = self.client.get(f"/markets/{ticker}")["market"]
            self._window[ticker] = (
                _iso(m.get("open_time")), _iso(m.get("close_time")), m.get("status", ""),
            )
        return self._window[ticker]

    # ---------- the core call ----------

    def candles(
        self, ticker: str, start: datetime, end: datetime,
        interval: int = MINUTE, series_ticker: str | None = None,
    ) -> list[Candle]:
        """Every candle between ``start`` and ``end``, oldest first.

        Chunks the request so the 5000-period cap is never hit, and works
        identically on settled markets.
        """
        if interval not in VALID_INTERVALS:
            raise ValueError(
                f"period_interval must be one of {VALID_INTERVALS} "
                f"(minute, hour, day); Kalshi rejects anything else"
            )
        series = series_ticker or self.resolve_series(ticker)
        if not series:
            raise ValueError(f"could not resolve a series for {ticker}")

        span = timedelta(minutes=interval * MAX_PERIODS)
        path = f"/series/{series}/markets/{ticker}/candlesticks"
        out: list[Candle] = []
        cursor = start
        while cursor < end:
            stop = min(cursor + span, end)
            payload = self.client.get(path, {
                "start_ts": int(cursor.timestamp()),
                "end_ts": int(stop.timestamp()),
                "period_interval": interval,
            })
            out.extend(_candle(ticker, c) for c in payload.get("candlesticks") or [])
            cursor = stop
        out.sort(key=lambda c: c.ts)
        return out

    # ---------- convenience ----------

    def full_history(self, ticker: str, interval: int = MINUTE) -> list[Candle]:
        """The market's entire life, from listing to close."""
        opened, closed, _ = self.market_window(ticker)
        end = min(closed, datetime.now(timezone.utc)) if closed else datetime.now(timezone.utc)
        return self.candles(ticker, opened, end, interval)

    def quote_at(self, ticker: str, when: datetime,
                 interval: int = MINUTE) -> Candle | None:
        """The market's state at an instant — the last candle at or before it.

        Point-in-time by construction: it cannot return a period ending after
        the moment asked for.
        """
        when = when.astimezone(timezone.utc)
        lookback = timedelta(minutes=interval * 240)
        got = self.candles(ticker, when - lookback, when, interval)
        usable = [c for c in got if c.ts <= when]
        return usable[-1] if usable else None

    def price_path(self, ticker: str, start: datetime | None = None,
                   end: datetime | None = None, interval: int = MINUTE,
                   clip_to_close: bool = True) -> list[Candle]:
        """Candles over a window, defaulting to the market's whole life.

        ``clip_to_close`` drops periods after the market closed. They contain
        an empty book rather than a market, and including them is how a study
        ends up concluding that every contract reverts to 0.50 at settlement.
        """
        opened, closed, _ = self.market_window(ticker)
        start = start or opened
        end = end or min(closed, datetime.now(timezone.utc))
        if clip_to_close and closed:
            end = min(end, closed)
        return self.candles(ticker, start, end, interval)

    def closing_quote(self, ticker: str, interval: int = MINUTE) -> Candle | None:
        """The last two-sided quote before the market closed — the CLV benchmark.

        Skips trailing periods with no bid or ask, which is what a market looks
        like once it stops quoting.
        """
        opened, closed, _ = self.market_window(ticker)
        end = min(closed, datetime.now(timezone.utc))
        window = self.candles(ticker, max(opened, end - timedelta(hours=12)), end, interval)
        for candle in reversed(window):
            if candle.two_sided:
                return candle
        return window[-1] if window else None

    def settlement(self, ticker: str) -> str | None:
        """``"yes"``, ``"no"``, or None if the market has not settled."""
        m = self.client.get(f"/markets/{ticker}")["market"]
        return m.get("result") or None

    def trades(self, ticker: str, start: datetime | None = None,
               end: datetime | None = None, max_trades: int | None = None) -> list[dict]:
        """Every print on a market in a window, newest first from the API.

        Finer than candles: each trade carries its price, size and which side
        took liquidity, which candles do not. Use it for anything about flow
        rather than level. Works on settled markets.
        """
        params: dict = {"ticker": ticker}
        if start:
            params["min_ts"] = int(start.timestamp())
        if end:
            params["max_ts"] = int(end.timestamp())
        return list(self.client.paginate("/markets/trades", "trades", params,
                                         max_items=max_trades))

    def volume_profile(self, ticker: str, start: datetime | None = None,
                       end: datetime | None = None) -> dict[float, float]:
        """Contracts traded at each price over a window.

        Where the volume actually sat, as opposed to where the market closed —
        a market that printed 90% of its size at 0.30 and drifted to 0.55 tells
        a different story from one that traded evenly.
        """
        profile: dict[float, float] = {}
        for t in self.trades(ticker, start, end):
            price = _f(t.get("yes_price_dollars"))
            size = _f(t.get("count_fp")) or 0.0
            if price is not None:
                profile[price] = profile.get(price, 0.0) + size
        return dict(sorted(profile.items()))

    def rules(self, ticker: str) -> dict:
        """Settlement terms for a market, as written by the exchange.

        ``rules_primary`` is the sentence that decides the contract. Reading it
        is the cheapest way to avoid answering a slightly different question
        than the one that settles.
        """
        m = self.client.get(f"/markets/{ticker}")["market"]
        return {
            "ticker": ticker,
            "title": m.get("title", ""),
            "subtitle": m.get("yes_sub_title", ""),
            "rules_primary": m.get("rules_primary", ""),
            "rules_secondary": m.get("rules_secondary", ""),
            "strike_type": m.get("strike_type", ""),
            "floor_strike": m.get("floor_strike"),
            "cap_strike": m.get("cap_strike"),
            "open_time": m.get("open_time", ""),
            "close_time": m.get("close_time", ""),
            "status": m.get("status", ""),
            "result": m.get("result") or None,
        }

    # ---------- many markets ----------

    def event_history(self, event_ticker: str, start: datetime | None = None,
                      end: datetime | None = None, interval: int = MINUTE,
                      status: str | None = None) -> dict[str, list[Candle]]:
        """History for every market under one event — the whole card for a fixture."""
        series = None
        out: dict[str, list[Candle]] = {}
        for m in self.client.paginate("/markets", "markets",
                                      {"event_ticker": event_ticker, "status": status}):
            ticker = m["ticker"]
            series = series or self.resolve_series(ticker)
            s = start or _iso(m.get("open_time"))
            e = end or min(_iso(m.get("close_time")), datetime.now(timezone.utc))
            out[ticker] = self.candles(ticker, s, e, interval, series)
        return out

    def series_history(self, series_ticker: str, start: datetime, end: datetime,
                       interval: int = HOUR, status: str | None = None,
                       max_markets: int | None = None) -> dict[str, list[Candle]]:
        """History for every market in a series — a whole competition's book.

        Defaults to hourly, because a series can hold thousands of markets and
        minute resolution over all of them is a very large number of requests.
        Use ``max_markets`` to bound an exploratory sweep.
        """
        out: dict[str, list[Candle]] = {}
        for m in self.client.paginate("/markets", "markets",
                                      {"series_ticker": series_ticker, "status": status},
                                      max_items=max_markets):
            out[m["ticker"]] = self.candles(m["ticker"], start, end, interval, series_ticker)
        return out

    def request_count(self, start: datetime, end: datetime, interval: int,
                      markets: int = 1) -> int:
        """How many API calls a sweep will cost, before making it.

        Worth checking: a season of one series at minute resolution runs to
        thousands of requests, and the read budget is 200/s shared with
        everything else.
        """
        periods = (end - start).total_seconds() / 60 / interval
        chunks = max(1, int(-(-periods // MAX_PERIODS)))
        return chunks * markets

    def slate_history(self, tickers: list[str], start: datetime, end: datetime,
                      interval: int = MINUTE) -> dict[str, list[Candle]]:
        """History for an arbitrary set of markets. Sequential by design — the
        read budget is shared, and fanning out only buys 429s."""
        return {t: self.candles(t, start, end, interval) for t in tickers}


def _iso(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
