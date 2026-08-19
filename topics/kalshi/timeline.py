"""Join game events to market moves on one clock.

This is the join the rest of the package exists to enable: *what did the price
do when the run scored*. The two halves arrive on different axes — plays carry
a UTC wallclock, Kalshi candles are UTC minutes — so aligning them is mostly a
matter of not being sloppy about timestamps.

Only plays with a real timestamp can be joined. Feeds that give a game clock and
no wallclock (some ESPN competitions) are reported as unjoinable rather than
guessed at, because inventing a timestamp would silently corrupt every
downstream measurement.

    tl = Timeline()
    entries = tl.build("MLB", game_id, ["KXMLBGAME-26AUG18NYYBAL-NYY"])
    tl.reactions(entries)          # what the market did around each scoring play
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .client import KalshiClient
from .gamestate import GameState, Play, game_state
from .history import MINUTE, Candle, History


@dataclass
class Entry:
    """One thing that happened, of either kind."""

    ts: datetime
    kind: str                       # "play" | "quote"
    label: str
    ticker: str | None = None
    play: Play | None = None
    candle: Candle | None = None

    @property
    def mid(self) -> float | None:
        return self.candle.mid if self.candle else None


@dataclass
class Reaction:
    """How the market moved around one play."""

    play: Play
    ticker: str
    before: float | None
    after: float | None
    peak: float | None
    trough: float | None

    @property
    def move(self) -> float | None:
        if self.before is None or self.after is None:
            return None
        return self.after - self.before

    @property
    def swing(self) -> float | None:
        """Largest excursion in the window, signed by direction of the move."""
        if self.peak is None or self.trough is None or self.before is None:
            return None
        up, down = self.peak - self.before, self.before - self.trough
        return up if up >= down else -down


class Timeline:
    """Build and analyse an aligned game/market timeline."""

    def __init__(self, client: KalshiClient | None = None) -> None:
        self.client = client or KalshiClient()
        self.history = History(self.client)

    # ---------- construction ----------

    def build(self, league: str, game_id: str, tickers: list[str],
              interval: int = MINUTE, pad: timedelta = timedelta(minutes=30),
              state: GameState | None = None) -> list[Entry]:
        """Interleave a game's plays with the candles of the given markets.

        The market window is taken from the plays themselves, padded either
        side, so it covers the game rather than the contract's whole life.
        """
        state = state or game_state(league, game_id)
        stamped = [p for p in state.plays if p.utc]
        if not stamped:
            raise ValueError(
                f"no play in this {league} feed carries a wallclock timestamp, "
                "so nothing can be aligned to the market"
            )

        start = min(p.utc for p in stamped) - pad
        end = max(p.utc for p in stamped) + pad

        entries = [Entry(p.utc, "play", p.description[:90], play=p) for p in stamped]
        for ticker in tickers:
            for candle in self.history.candles(ticker, start, end, interval):
                entries.append(Entry(candle.ts, "quote",
                                     f"{ticker} mid={candle.mid}",
                                     ticker=ticker, candle=candle))
        entries.sort(key=lambda e: e.ts)
        return entries

    def coverage(self, state: GameState) -> dict:
        """How much of a feed's play list can be joined at all."""
        total = len(state.plays)
        stamped = sum(1 for p in state.plays if p.utc)
        return {"plays": total, "timestamped": stamped,
                "joinable": stamped == total,
                "ratio": (stamped / total) if total else 0.0}

    # ---------- analysis ----------

    def reactions(self, entries: list[Entry], ticker: str | None = None,
                  before: timedelta = timedelta(minutes=2),
                  after: timedelta = timedelta(minutes=5),
                  scoring_only: bool = True) -> list[Reaction]:
        """What the market did around each play.

        ``before`` is the last quote at or before the play; ``after`` is the
        last quote inside the window that follows. Peak and trough are taken
        across the same window, so a move that reverses is visible.
        """
        quotes = [e for e in entries if e.kind == "quote"
                  and (ticker is None or e.ticker == ticker)]
        if not quotes:
            return []
        target = ticker or quotes[0].ticker or ""

        out: list[Reaction] = []
        for e in entries:
            if e.kind != "play" or (scoring_only and not e.play.scoring):
                continue
            prior = [q for q in quotes if e.ts - before <= q.ts <= e.ts and q.mid is not None]
            window = [q for q in quotes if e.ts < q.ts <= e.ts + after and q.mid is not None]
            if not prior or not window:
                continue
            mids = [q.mid for q in window]
            out.append(Reaction(
                play=e.play, ticker=target,
                before=prior[-1].mid, after=mids[-1],
                peak=max(mids), trough=min(mids),
            ))
        return out

    def leading_moves(self, entries: list[Entry], ticker: str,
                      threshold: float = 0.05,
                      window: timedelta = timedelta(minutes=3)) -> list[dict]:
        """Price moves that were not preceded by a play.

        A jump with no event behind it is either information the feed has not
        reported yet, or the feed lagging the market. Either way it is the
        thing to look at when deciding whether a feed is usable live.
        """
        quotes = [e for e in entries if e.kind == "quote"
                  and e.ticker == ticker and e.mid is not None]
        plays = [e for e in entries if e.kind == "play"]
        out: list[dict] = []
        for prev, cur in zip(quotes, quotes[1:]):
            move = cur.mid - prev.mid
            if abs(move) < threshold:
                continue
            preceding = [p for p in plays if cur.ts - window <= p.ts <= cur.ts]
            if not preceding:
                out.append({"ts": cur.ts, "from": prev.mid, "to": cur.mid,
                            "move": move, "explained_by": None})
        return out
