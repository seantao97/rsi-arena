"""SQLite store for battles and votes.

Append-only, and the same choice ``topics/kalshi/storage.py`` makes for the
same reason: no server to run, and a replay of any past instant is exactly
what was recorded.

This is not the arena's rating system — there is no Elo here yet. It records
what happened so that when the rating system is built it has something to
compute over.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS battles (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    question TEXT NOT NULL,
    agent_a TEXT NOT NULL,
    agent_b TEXT NOT NULL,
    model TEXT,
    blind INTEGER NOT NULL DEFAULT 1,
    result_json TEXT
);

CREATE TABLE IF NOT EXISTS votes (
    id TEXT PRIMARY KEY,
    battle_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    winner TEXT NOT NULL,
    reason TEXT,
    FOREIGN KEY (battle_id) REFERENCES battles(id)
);
CREATE INDEX IF NOT EXISTS votes_battle ON votes(battle_id);
"""

DB_PATH = os.environ.get("RSI_ARENA_DB", "arena.db")


class Store:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or DB_PATH
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def open_battle(
        self, question: str, agent_a: str, agent_b: str, model: str, blind: bool
    ) -> str:
        battle_id = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO battles (id, created_at, question, agent_a, agent_b, model, blind) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (battle_id, time.time(), question, agent_a, agent_b, model, int(blind)),
            )
        return battle_id

    def close_battle(self, battle_id: str, result: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE battles SET result_json = ? WHERE id = ?",
                (json.dumps(result, default=str), battle_id),
            )

    def battle(self, battle_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM battles WHERE id = ?", (battle_id,)).fetchone()
        return dict(row) if row else None

    def record_vote(self, battle_id: str, winner: str, reason: str = "") -> str:
        vote_id = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO votes (id, battle_id, created_at, winner, reason) VALUES (?,?,?,?,?)",
                (vote_id, battle_id, time.time(), winner, reason),
            )
        return vote_id

    def tally(self) -> list[dict[str, Any]]:
        """Wins, losses and ties per agent. A scoreboard, not a rating.

        Deliberately not Elo: with a handful of votes an Elo number looks
        authoritative and means nothing. Counts do not pretend otherwise.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT b.agent_a, b.agent_b, v.winner FROM votes v "
                "JOIN battles b ON b.id = v.battle_id"
            ).fetchall()
        table: dict[str, dict[str, Any]] = {}

        def slot(agent: str) -> dict[str, Any]:
            return table.setdefault(agent, {"agent": agent, "wins": 0, "losses": 0,
                                            "ties": 0, "battles": 0})

        for row in rows:
            a, b, winner = slot(row["agent_a"]), slot(row["agent_b"]), row["winner"]
            a["battles"] += 1
            b["battles"] += 1
            if winner == "a":
                a["wins"] += 1
                b["losses"] += 1
            elif winner == "b":
                b["wins"] += 1
                a["losses"] += 1
            elif winner == "tie":
                a["ties"] += 1
                b["ties"] += 1
        return sorted(table.values(), key=lambda r: (-r["wins"], r["agent"]))
