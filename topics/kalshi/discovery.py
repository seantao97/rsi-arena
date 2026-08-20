"""What is bettable — series, events and markets, classified by sport and league.

This is the "get all xyz markets" half. An agent calls it to find out what
exists before it decides what to look at.

Kalshi's hierarchy is series -> event -> market:

    KXNFLGAME                        series  — pro football game winner
    KXNFLGAME-25SEP07DALPHI          event   — one fixture
    KXNFLGAME-25SEP07DALPHI-DAL      market  — one binary contract
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable, Iterator

from .client import KalshiClient
from .linking import is_fixture_event
from .taxonomy import MarketType, SeriesClass, Sport, classify_series


@dataclass(frozen=True)
class MarketRef:
    """One bettable contract, with its classification attached."""

    ticker: str
    event_ticker: str
    series_ticker: str
    title: str
    subtitle: str
    status: str
    close_time: str
    sport: str
    league: str
    market_type: str
    yes_bid: float | None
    yes_ask: float | None
    volume: float
    open_interest: float
    liquidity: float

    def to_dict(self) -> dict:
        return asdict(self)


def _f(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Discovery:
    """Enumerate and classify everything tradeable."""

    def __init__(self, client: KalshiClient | None = None) -> None:
        self.client = client or KalshiClient()
        self._series_cache: dict[str, SeriesClass] = {}
        # Tools offload to threads, so the catalogue can be requested from
        # several at once. Without this, concurrent first-calls each fetch all
        # 13k series.
        self._cache_lock = threading.Lock()

    # ---------- series ----------

    def all_series(self, refresh: bool = False) -> dict[str, SeriesClass]:
        """Every series on the exchange, classified. ~13k objects, cached."""
        if self._series_cache and not refresh:
            return self._series_cache
        with self._cache_lock:
            if self._series_cache and not refresh:
                return self._series_cache
            return self._load_series()

    def _load_series(self) -> dict[str, SeriesClass]:
        out: dict[str, SeriesClass] = {}
        for s in self.client.paginate("/series", "series", page_size=200):
            ticker = s.get("ticker", "")
            if ticker:
                out[ticker] = classify_series(ticker, s.get("title", ""), s.get("category", ""),
                                              s.get("tags"), s.get("frequency", ""))
        self._series_cache = out
        return out

    def sports_series(
        self,
        sport: Sport | str | None = None,
        league: str | None = None,
        market_type: MarketType | str | None = None,
        game_level_only: bool = False,
        fixtures_only: bool = False,
    ) -> list[SeriesClass]:
        """Sports series, optionally filtered.

        ``fixtures_only`` filters on Kalshi's ``frequency`` hint. That hint is
        not decisive on its own — see :attr:`SeriesClass.is_fixture` — so
        :meth:`whats_bettable` re-checks each market's event ticker, which is
        definitive and free.
        """
        results = [c for c in self.all_series().values() if c.sport is not Sport.OTHER
                   or c.league not in ("NONSPORT",)]
        results = [c for c in results if c.league != "NONSPORT"]
        if sport:
            want = Sport(sport) if isinstance(sport, str) else sport
            results = [c for c in results if c.sport is want]
        if league:
            results = [c for c in results if c.league == league]
        if market_type:
            want_t = MarketType(market_type) if isinstance(market_type, str) else market_type
            results = [c for c in results if c.market_type is want_t]
        if game_level_only:
            results = [c for c in results if c.is_game_level]
        if fixtures_only:
            results = [c for c in results if c.is_fixture]
        return results

    def coverage(self) -> dict[str, dict[str, int]]:
        """League x market-type counts. Use it to see what the exchange offers."""
        table: dict[str, dict[str, int]] = {}
        for c in self.sports_series():
            table.setdefault(c.league, {}).setdefault(c.market_type.value, 0)
            table[c.league][c.market_type.value] += 1
        return table

    # ---------- events ----------

    def events(self, series_ticker: str, status: str = "open") -> list[dict]:
        """Events (fixtures) under a series."""
        return list(self.client.paginate(
            "/events", "events",
            {"series_ticker": series_ticker, "status": status}, page_size=200,
        ))

    # ---------- markets ----------

    def markets(
        self,
        series_ticker: str | None = None,
        event_ticker: str | None = None,
        status: str = "open",
        max_items: int | None = None,
    ) -> Iterator[MarketRef]:
        """Markets, classified. Filter server-side where possible."""
        params = {"status": status, "series_ticker": series_ticker,
                  "event_ticker": event_ticker}
        catalog = self.all_series()
        for m in self.client.paginate("/markets", "markets", params,
                                      max_items=max_items):
            yield self._to_ref(m, catalog)

    def _to_ref(self, m: dict, catalog: dict[str, SeriesClass]) -> MarketRef:
        event = m.get("event_ticker", "")
        cls = self._series_for(event, catalog)
        return MarketRef(
            ticker=m.get("ticker", ""),
            event_ticker=event,
            series_ticker=cls.ticker if cls else "",
            title=m.get("title", ""),
            subtitle=m.get("yes_sub_title") or "",
            status=m.get("status", ""),
            close_time=m.get("close_time", ""),
            sport=(cls.sport.value if cls else "other"),
            league=(cls.league if cls else "UNKNOWN"),
            market_type=(cls.market_type.value if cls else "other"),
            yes_bid=_f(m.get("yes_bid_dollars")),
            yes_ask=_f(m.get("yes_ask_dollars")),
            volume=_f(m.get("volume_fp")) or 0.0,
            open_interest=_f(m.get("open_interest_fp")) or 0.0,
            liquidity=_f(m.get("liquidity_dollars")) or 0.0,
        )

    @staticmethod
    def _series_for(event_ticker: str, catalog: dict[str, SeriesClass]) -> SeriesClass | None:
        """Resolve an event ticker to its series by longest matching prefix.

        Event tickers are ``SERIES-SUFFIX`` but the series stem itself can
        contain hyphens (``KXNFLWINS-KC``), so a plain split is wrong.
        """
        for i in range(len(event_ticker), 2, -1):
            hit = catalog.get(event_ticker[:i])
            if hit:
                return hit
        return None

    # ---------- the agent-facing call ----------

    def whats_bettable(
        self,
        league: str | None = None,
        sport: Sport | str | None = None,
        game_level_only: bool = False,
        fixtures_only: bool = True,
        min_liquidity: float = 0.0,
        closing_within_hours: float | None = None,
    ) -> list[MarketRef]:
        """Everything an agent could take a position on right now.

        This is the primary discovery entry point. It sweeps the open markets
        for the requested scope and returns them classified and filtered.
        """
        series = self.sports_series(sport=sport, league=league,
                                    game_level_only=game_level_only,
                                    fixtures_only=fixtures_only)
        wanted = {c.ticker for c in series}
        now = datetime.now(timezone.utc)

        out: list[MarketRef] = []
        for s in wanted:
            for ref in self.markets(series_ticker=s, status="open"):
                # The definitive fixture test, on a market already fetched.
                # frequency == "custom" covers season futures too, so without
                # this the World Series winner market passes fixtures_only.
                if fixtures_only and not is_fixture_event(ref.event_ticker):
                    continue
                if ref.liquidity < min_liquidity:
                    continue
                if closing_within_hours is not None and ref.close_time:
                    try:
                        close = datetime.fromisoformat(
                            ref.close_time.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if (close - now).total_seconds() > closing_within_hours * 3600:
                        continue
                out.append(ref)
        return out

    def active_leagues(self) -> list[str]:
        """Leagues with at least one open market. Cheap way to see what is in season."""
        seen: set[str] = set()
        catalog = self.all_series()
        for m in self.client.paginate("/markets", "markets", {"status": "open"}):
            cls = self._series_for(m.get("event_ticker", ""), catalog)
            if cls and cls.league not in ("NONSPORT", "UNKNOWN"):
                seen.add(cls.league)
        return sorted(seen)
