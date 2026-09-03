"""Put an agent back at a past instant and let it forecast into a known future.

Scoring a five-minute forecast normally means waiting five minutes. That is
fine for collecting data and useless for comparing two harnesses: a hundred
windows is eight hours of football, the fixtures are never the same twice, and
the answer arrives long after the question stops being interesting.

A finished match has all of it already. Kalshi serves the full candlestick life
of every market, and the fixture feed publishes every goal and card of a
completed game with the clock it happened on. So the instant can be chosen, the
market state as of that instant reconstructed, and the realised price five
minutes later looked up — immediately, repeatably, and as many times as wanted.

The seam this hangs on is Sean's: an agent serialises to a spec that names its
tools, and ``Agent.from_dict(spec, toolbox)`` binds those names against
whatever toolbox it is handed. Replay supplies a toolbox with the same three
names reading history instead of the live book. The agent is not modified and
does not know.

**Point-in-time is the whole contract.** Every read here is bounded at the
instant asked for. A leak — a candle from a minute later, a goal that had not
happened yet — silently turns the benchmark into a measure of hindsight.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from rsi_arena import Tool, ToolOutput, Toolbox

from ..tools import _gamestate as gs
from .. import MINUTE, History
from .. import resolve_league

HORIZON_MINUTES = 5


# Every event type the feed uses that puts a goal on the board. A penalty is
# filed as "penalty---scored", not as a goal, and counting only "goal" quietly
# loses it: the match this was built against finished 2-2 and reconstructed as
# 2-1, because the equaliser was a penalty in stoppage time.
SCORING_KINDS = frozenset({"goal", "penalty---scored", "own-goal",
                           "penalty-goal", "goal-penalty"})


@dataclass(frozen=True)
class MatchEvent:
    """Something the feed timestamped: a goal, a card, a substitution."""

    seconds: float             # clock value from kickoff
    kind: str                  # goal | penalty---scored | yellow-card | ...
    team: str
    text: str

    @property
    def minute(self) -> int:
        return int(self.seconds // 60)

    @property
    def scored(self) -> bool:
        return self.kind in SCORING_KINDS

    @property
    def credited_to(self) -> str:
        """Whose score went up.

        An own goal is filed against the team that conceded it, so the side in
        the event is not the side that benefits.
        """
        return self.team


@dataclass
class Timeline:
    """A finished match, reduced to what changes a price."""

    game_id: str
    league: str
    home: str
    away: str
    kickoff: datetime
    events: list[MatchEvent] = field(default_factory=list)

    def score_at(self, when: datetime) -> tuple[int, int, int]:
        """Home, away and the match minute, as of ``when``.

        Built by replaying goals up to that clock rather than by reading a
        final score, because the final score is exactly the thing the agent
        must not be told.
        """
        elapsed = (when - self.kickoff).total_seconds()
        minute = max(0, int(elapsed // 60))
        home = away = 0
        for event in self.events:
            if not event.scored or event.seconds > elapsed:
                continue
            if event.team == self.home:
                home += 1
            else:
                away += 1
        return home, away, minute

    def final_score(self) -> tuple[int, int]:
        return self.score_at(self.kickoff + timedelta(days=1))[:2]

    def state_at(self, when: datetime) -> dict:
        """What ``game_state`` would have returned, had it been asked then."""
        home, away, minute = self.score_at(when)
        elapsed = (when - self.kickoff).total_seconds()
        recent = [e for e in self.events
                  if e.seconds <= elapsed and elapsed - e.seconds <= 600]
        return {
            "game_id": self.game_id, "league": self.league,
            "status": "in_progress" if minute <= 100 else "final",
            "home": self.home, "away": self.away,
            "home_score": home, "away_score": away,
            "period": "1" if minute < 45 else "2",
            "clock": f"{minute}'",
            "recent_events": [f"{e.minute}' {e.kind}: {e.text[:90]}"
                              for e in recent[-4:]],
        }

    def windows(self, every_minutes: int = 5, skip_first: int = 5,
                until_minute: int = 88) -> list[datetime]:
        """Instants worth forecasting from.

        Stops before full time: a window whose horizon runs past the whistle is
        scored against a market that has stopped trading, which measures the
        settlement rules rather than the forecast.
        """
        out, minute = [], skip_first
        while minute <= until_minute:
            out.append(self.kickoff + timedelta(minutes=minute))
            minute += every_minutes
        return out


def timeline(league: str, game_id: str) -> Timeline | None:
    """Reconstruct a finished match from the fixture feed's own record.

    Live, the feed publishes no play-by-play for soccer. Afterwards it
    publishes ``keyEvents``, with a clock value on each — which is what makes
    replay possible for a sport that cannot be followed ball by ball in the
    moment.
    """
    path = gs.ESPN_PATHS.get(resolve_league(league) or league.upper())
    if not path:
        return None
    sport, competition = path
    try:
        data = gs._get(f"{gs.ESPN_API}/{sport}/{competition}"
                       f"/summary?event={game_id}", throttle=True)
    except Exception:
        return None

    header = (data.get("header") or {})
    competitions = header.get("competitions") or []
    if not competitions:
        return None
    comp = competitions[0]
    teams = {c.get("homeAway"): (c.get("team") or {}).get("displayName", "")
             for c in comp.get("competitors", [])}
    try:
        kickoff = datetime.fromisoformat(
            comp["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
    except (KeyError, ValueError):
        return None

    events: list[MatchEvent] = []
    for raw in data.get("keyEvents") or []:
        clock = (raw.get("clock") or {}).get("value")
        kind = ((raw.get("type") or {}).get("type") or "").lower()
        if clock is None or not kind:
            continue
        events.append(MatchEvent(
            seconds=float(clock), kind=kind,
            team=((raw.get("team") or {}).get("displayName") or ""),
            text=(raw.get("text") or "")))

    return Timeline(game_id=str(game_id), league=league.upper(),
                    home=teams.get("home", ""), away=teams.get("away", ""),
                    kickoff=kickoff, events=sorted(events, key=lambda e: e.seconds))


def realised_mid(ticker: str, at: datetime, minutes: int = HORIZON_MINUTES,
                 history: History | None = None) -> float | None:
    """The mid the market actually printed ``minutes`` after ``at``.

    ``None`` when there is no two-sided quote to compare against, which is a
    fact about the book rather than a failure — the same guard the live scorer
    uses, so replay and live agree about what counts.
    """
    history = history or History()
    candle = history.quote_at(ticker, at + timedelta(minutes=minutes), MINUTE)
    if candle is None or not candle.two_sided:
        return candle.mid if candle and candle.mid is not None else None
    return candle.mid


def replay_tools(at: datetime, history: History | None = None) -> Toolbox:
    """The agent's three tools, bounded at an instant.

    Same names as the live toolbox, so an agent spec binds against either
    without knowing which it got. Every read stops at ``at``.
    """
    hist = history or History()

    class FrozenQuote(Tool):
        name = "market_quote"
        description = "The market's quoted state."
        parameters = {"type": "object",
                      "properties": {"ticker": {"type": "string"}},
                      "required": ["ticker"]}

        def get_tool_output(self, input: dict) -> ToolOutput:
            candle = hist.quote_at(input["ticker"], at, MINUTE)
            if candle is None:
                return ToolOutput.failed("no quote at that instant")
            out = {"ticker": input["ticker"], "yes_bid": candle.yes_bid_close,
                   "yes_ask": candle.yes_ask_close, "mid": candle.mid,
                   "spread": candle.spread, "last": candle.last,
                   "volume": candle.volume, "status": "active"}
            return ToolOutput(response=json.dumps(out, default=str), raw_output=out)

    class FrozenPath(Tool):
        name = "candlesticks"
        description = "Minute bars up to now."
        parameters = {"type": "object",
                      "properties": {"ticker": {"type": "string"},
                                     "hours_back": {"type": "number"},
                                     "hourly": {"type": "boolean"}},
                      "required": ["ticker"]}

        def get_tool_output(self, input: dict) -> ToolOutput:
            start = at - timedelta(hours=max(0.1, float(input.get("hours_back", 0.75))))
            bars = [{"ts": c.ts.isoformat(), "mid": c.mid, "last": c.last,
                     "volume": c.volume}
                    for c in hist.price_path(input["ticker"], start, at, MINUTE)]
            return ToolOutput(response=json.dumps(bars, default=str),
                              raw_output={"bars": bars})

    class FrozenTape(Tool):
        name = "previous_trades"
        description = "Prints from just before now."
        parameters = {"type": "object",
                      "properties": {"ticker": {"type": "string"},
                                     "limit": {"type": "integer"}},
                      "required": ["ticker"]}

        def get_tool_output(self, input: dict) -> ToolOutput:
            # Bounded by the API's own max_ts rather than by filtering what
            # comes back. Kalshi returns trades newest-first over the whole
            # life of a market, so asking for the newest N and trimming
            # afterwards would hand back prints from after the instant being
            # replayed — the exact leak this module exists to prevent.
            limit = int(input.get("limit", 12))
            recent = hist.trades(input["ticker"], start=at - timedelta(hours=1),
                                 end=at, max_trades=max(limit, 50))
            # Same field mapping as the live tool. Different names here would
            # hand the agent a differently-shaped tape in replay than in
            # production, which is a benchmark measuring its own plumbing.
            trades = [{"ts": t.get("created_time"),
                       "yes_price": t.get("yes_price_dollars"),
                       "count": t.get("count_fp"),
                       "taker_side": t.get("taker_side")}
                      for t in recent[:limit]]
            return ToolOutput(response=json.dumps(trades, default=str),
                              raw_output={"trades": trades})

    return Toolbox([FrozenQuote(), FrozenPath(), FrozenTape()])
