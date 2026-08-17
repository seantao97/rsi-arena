"""SQLite storage for recorded quotes, books, trades and game state.

SQLite is the default because it needs no server and handles this write rate
comfortably — a busy slate is a few hundred rows a second, well inside what
WAL mode absorbs. Swap in Postgres by reimplementing ``Store`` against the same
method signatures; the schema is portable apart from the pragmas.

Everything is append-only. Nothing is ever updated in place, so a replay of any
past instant is exactly what was observed at that instant.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Iterable, Iterator

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS quotes (
    ticker TEXT NOT NULL, ts TEXT NOT NULL,
    yes_bid REAL, yes_ask REAL, yes_bid_size REAL, yes_ask_size REAL,
    last REAL, volume REAL, open_interest REAL, status TEXT,
    PRIMARY KEY (ticker, ts)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS quotes_ts ON quotes(ts);

CREATE TABLE IF NOT EXISTS books (
    ticker TEXT NOT NULL, ts TEXT NOT NULL,
    yes_levels TEXT NOT NULL, no_levels TEXT NOT NULL,
    PRIMARY KEY (ticker, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, ts TEXT NOT NULL,
    price REAL, count REAL, taker_side TEXT
);
CREATE INDEX IF NOT EXISTS trades_ticker_ts ON trades(ticker, ts);

CREATE TABLE IF NOT EXISTS markets (
    ticker TEXT PRIMARY KEY, event_ticker TEXT, series_ticker TEXT,
    title TEXT, subtitle TEXT, sport TEXT, league TEXT, market_type TEXT,
    close_time TEXT, first_seen TEXT, last_seen TEXT
);
CREATE INDEX IF NOT EXISTS markets_league ON markets(league, market_type);

CREATE TABLE IF NOT EXISTS game_states (
    league TEXT NOT NULL, game_id TEXT NOT NULL, ts TEXT NOT NULL,
    status TEXT, home TEXT, away TEXT, home_score INTEGER, away_score INTEGER,
    period TEXT, clock TEXT, detail TEXT,
    PRIMARY KEY (league, game_id, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS plays (
    league TEXT NOT NULL, game_id TEXT NOT NULL, seq INTEGER NOT NULL,
    ts TEXT, period TEXT, clock TEXT, team TEXT, description TEXT,
    scoring INTEGER, players TEXT,
    PRIMARY KEY (league, game_id, seq)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS links (
    league TEXT NOT NULL, game_id TEXT NOT NULL,
    event_ticker TEXT NOT NULL, confidence REAL, method TEXT,
    PRIMARY KEY (league, game_id, event_ticker)
) WITHOUT ROWID;
"""


class Store:
    """Append-only writer and reader over the recorder database."""

    def __init__(self, path: str = "kalshi.db") -> None:
        self.path = path
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------- writes ----------

    def write_quotes(self, quotes: Iterable) -> int:
        rows = [(q.ticker, q.ts, q.yes_bid, q.yes_ask, q.yes_bid_size,
                 q.yes_ask_size, q.last, q.volume, q.open_interest, q.status)
                for q in quotes]
        if not rows:
            return 0
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        return len(rows)

    def write_book(self, book) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO books VALUES (?,?,?,?)",
                (book.ticker, book.ts, json.dumps(book.yes), json.dumps(book.no)))

    def write_trades(self, ticker: str, trades: Iterable[dict]) -> int:
        rows = [(t.get("trade_id") or f"{ticker}:{t.get('created_time')}",
                 ticker, t.get("created_time", ""), t.get("yes_price"),
                 t.get("count"), t.get("taker_side")) for t in trades]
        if not rows:
            return 0
        with self._conn() as conn:
            conn.executemany("INSERT OR IGNORE INTO trades VALUES (?,?,?,?,?,?)", rows)
        return len(rows)

    def upsert_markets(self, refs: Iterable, seen_at: str) -> int:
        rows = [(r.ticker, r.event_ticker, r.series_ticker, r.title, r.subtitle,
                 r.sport, r.league, r.market_type, r.close_time, seen_at, seen_at)
                for r in refs]
        if not rows:
            return 0
        with self._conn() as conn:
            conn.executemany(
                """INSERT INTO markets VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(ticker) DO UPDATE SET last_seen=excluded.last_seen,
                       close_time=excluded.close_time""", rows)
        return len(rows)

    def write_game_state(self, state) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO game_states VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (state.league, state.game_id, state.fetched_at, state.status,
                 state.home, state.away, state.home_score, state.away_score,
                 state.period, state.clock, json.dumps(state.detail)))
            if state.plays:
                conn.executemany(
                    "INSERT OR IGNORE INTO plays VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [(state.league, state.game_id, i, p.ts, p.period, p.clock,
                      p.team, p.description, int(p.scoring), json.dumps(p.players))
                     for i, p in enumerate(state.plays)])

    def link(self, league: str, game_id: str, event_ticker: str,
             confidence: float, method: str) -> None:
        """Record a fixture <-> Kalshi event correspondence."""
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO links VALUES (?,?,?,?,?)",
                         (league, game_id, event_ticker, confidence, method))

    # ---------- reads ----------

    def stats(self) -> dict[str, int]:
        with self._conn() as conn:
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("quotes", "books", "trades", "markets",
                              "game_states", "plays", "links")}

    def tickers_for_event(self, event_ticker: str) -> list[str]:
        with self._conn() as conn:
            return [r[0] for r in conn.execute(
                "SELECT ticker FROM markets WHERE event_ticker = ?", (event_ticker,))]
