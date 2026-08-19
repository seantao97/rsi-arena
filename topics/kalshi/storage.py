"""SQLite storage for game state. **Quotes are not stored.**

Market quotes and order books used to live here. They no longer do: Kalshi
serves the full life of every market from its candlestick endpoint, open or
settled, so :mod:`.history` reads history from the API instead. That removes a
collector to run and a database that could drift from the exchange.

What remains is the data Kalshi does not hold — live game state, plays, and the
fixture links that join a market to a real game.

Everything is append-only, so a replay of any past instant is exactly what was
observed at that instant.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Iterable, Iterator

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

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
                    for t in ("markets", "game_states", "plays", "links")}

    def tickers_for_event(self, event_ticker: str) -> list[str]:
        with self._conn() as conn:
            return [r[0] for r in conn.execute(
                "SELECT ticker FROM markets WHERE event_ticker = ?", (event_ticker,))]
