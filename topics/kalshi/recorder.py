"""Live collector — capture quotes, books and game state at high frequency.

Two loops run side by side:

* **Quote loop.** Batch-polls open markets. One REST call covers up to 100
  tickers, so a 400-market slate costs four calls per tick and comfortably
  sustains 1Hz inside the Basic read budget.
* **Game loop.** Polls live fixtures for score, situation and new plays.

The WebSocket path (``orderbook_delta``) gives true tick resolution but needs
credentials even for public channels, so polling is the default and the socket
is opt-in.

Run it:

    python -m topics.kalshi.recorder --leagues MLB,NFL --interval 1.0
"""

from __future__ import annotations

import argparse
import signal
import threading
import time
from datetime import datetime, timezone

from . import gamestate
from .client import KalshiClient
from .discovery import Discovery, MarketRef
from .quotes import Quotes
from .storage import Store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Recorder:
    """Collect market and game data until stopped."""

    def __init__(
        self,
        leagues: list[str],
        db_path: str = "kalshi.db",
        quote_interval: float = 1.0,
        game_interval: float = 5.0,
        refresh_interval: float = 300.0,
        book_depth: int = 0,
        client: KalshiClient | None = None,
    ) -> None:
        self.leagues = leagues
        self.quote_interval = quote_interval
        self.game_interval = game_interval
        self.refresh_interval = refresh_interval
        self.book_depth = book_depth

        self.client = client or KalshiClient()
        self.discovery = Discovery(self.client)
        self.quotes = Quotes(self.client, db_path)
        self.store = Store(db_path)

        self._watch: list[MarketRef] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.counters = {"quotes": 0, "books": 0, "states": 0, "refreshes": 0}

    # ---------- universe ----------

    def refresh_universe(self) -> int:
        """Re-discover open markets for the configured leagues."""
        found: list[MarketRef] = []
        for league in self.leagues:
            try:
                found += self.discovery.whats_bettable(
                    league=league, game_level_only=False)
            except Exception as exc:  # a dead league should not kill the loop
                print(f"[refresh] {league}: {exc}")
        with self._lock:
            self._watch = found
        self.store.upsert_markets(found, _now())
        self.counters["refreshes"] += 1
        print(f"[refresh] watching {len(found)} markets across {len(self.leagues)} leagues")
        return len(found)

    # ---------- loops ----------

    def _quote_loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            with self._lock:
                tickers = [m.ticker for m in self._watch]
            if tickers:
                try:
                    snap = self.quotes.get_markets(tickers)
                    self.counters["quotes"] += self.store.write_quotes(snap.values())
                    if self.book_depth:
                        for t in tickers[: self.book_depth]:
                            self.store.write_book(self.quotes.get_orderbook(t))
                            self.counters["books"] += 1
                except Exception as exc:
                    print(f"[quotes] {exc}")
            self._stop.wait(max(0.0, self.quote_interval - (time.monotonic() - started)))

    def _game_loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            for league in self.leagues:
                if league not in gamestate.ESPN_PATHS and league not in ("MLB", "NHL"):
                    continue
                try:
                    for game in gamestate.todays_games(league):
                        state = gamestate.game_state(league, game["id"])
                        if state.status == "scheduled":
                            continue
                        self.store.write_game_state(state)
                        self.counters["states"] += 1
                except Exception as exc:
                    print(f"[games] {league}: {exc}")
            self._stop.wait(max(0.0, self.game_interval - (time.monotonic() - started)))

    def _refresh_loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(self.refresh_interval)
            if not self._stop.is_set():
                self.refresh_universe()

    # ---------- control ----------

    def run(self) -> None:
        self.refresh_universe()
        threads = [threading.Thread(target=fn, daemon=True) for fn in
                   (self._quote_loop, self._game_loop, self._refresh_loop)]
        for t in threads:
            t.start()

        signal.signal(signal.SIGINT, lambda *_: self.stop())
        signal.signal(signal.SIGTERM, lambda *_: self.stop())
        print(f"[recorder] running — quotes {self.quote_interval}s, "
              f"games {self.game_interval}s. Ctrl-C to stop.")

        last = time.monotonic()
        while not self._stop.is_set():
            self._stop.wait(30)
            if time.monotonic() - last >= 30:
                print(f"[recorder] {self.counters} | rows {self.store.stats()}")
                last = time.monotonic()

    def stop(self) -> None:
        if not self._stop.is_set():
            print("\n[recorder] stopping")
            self._stop.set()


def main() -> None:
    ap = argparse.ArgumentParser(description="Record Kalshi markets and live game state")
    ap.add_argument("--leagues", default="MLB",
                    help="comma separated, e.g. MLB,NFL,NBA,EPL")
    ap.add_argument("--db", default="kalshi.db")
    ap.add_argument("--interval", type=float, default=1.0, help="quote poll seconds")
    ap.add_argument("--game-interval", type=float, default=5.0)
    ap.add_argument("--refresh", type=float, default=300.0,
                    help="universe re-discovery seconds")
    ap.add_argument("--book-depth", type=int, default=0,
                    help="also snapshot order books for the first N markets")
    args = ap.parse_args()

    Recorder(
        leagues=[x.strip().upper() for x in args.leagues.split(",") if x.strip()],
        db_path=args.db, quote_interval=args.interval,
        game_interval=args.game_interval, refresh_interval=args.refresh,
        book_depth=args.book_depth,
    ).run()


if __name__ == "__main__":
    main()
