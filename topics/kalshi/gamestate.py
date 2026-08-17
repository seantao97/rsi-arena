"""Live game state — who scored, who did what, what the clock says.

Kalshi tells you what a market costs. It does not tell you that the starting
pitcher just walked three batters. This module is the other half.

Sources, all free and keyless as of 2026-08-17:

* **MLB** — ``statsapi.mlb.com``. Official, full play-by-play, pitch level.
  The best free sports feed that exists.
* **NHL** — ``api-web.nhle.com``. Official, play-by-play and shifts.
* **Everything else** — ESPN's public endpoints. Unofficial but stable for
  years, and they cover NFL, NBA, WNBA, NCAA and the major soccer leagues.

ESPN is undocumented, so treat a schema change as expected rather than
exceptional. Every adapter returns the same ``GameState`` so a caller does not
care which one answered.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

MLB_API = "https://statsapi.mlb.com/api/v1"
NHL_API = "https://api-web.nhle.com/v1"
ESPN_API = "https://site.api.espn.com/apis/site/v2/sports"

# league -> (espn_sport, espn_league)
ESPN_PATHS = {
    "NFL": ("football", "nfl"),
    "NCAAF": ("football", "college-football"),
    "NBA": ("basketball", "nba"),
    "WNBA": ("basketball", "wnba"),
    "NCAAB": ("basketball", "mens-college-basketball"),
    "EPL": ("soccer", "eng.1"),
    "LALIGA": ("soccer", "esp.1"),
    "SERIEA": ("soccer", "ita.1"),
    "BUNDESLIGA": ("soccer", "ger.1"),
    "LIGUE1": ("soccer", "fra.1"),
    "MLS": ("soccer", "usa.1"),
    "UCL": ("soccer", "uefa.champions"),
    "NWSL": ("soccer", "usa.nwsl"),
}


@dataclass
class Play:
    """One discrete event: a pitch, a shot, a goal, a touchdown."""

    ts: str
    period: str                    # inning, quarter, half, period
    clock: str
    team: str
    description: str
    scoring: bool = False
    players: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class GameState:
    """Normalised live state for one fixture, whatever the sport."""

    game_id: str
    league: str
    status: str                    # "scheduled" | "in_progress" | "final"
    home: str
    away: str
    home_score: int
    away_score: int
    period: str
    clock: str
    fetched_at: str
    detail: dict[str, Any] = field(default_factory=dict)
    plays: list[Play] = field(default_factory=list)

    @property
    def is_live(self) -> bool:
        return self.status == "in_progress"

    def to_dict(self) -> dict:
        return asdict(self)


def _get(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "rsi-arena/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- MLB

def mlb_schedule(date: str | None = None) -> list[dict]:
    """Games on a date (YYYY-MM-DD, default today), with gamePks."""
    day = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = _get(f"{MLB_API}/schedule?sportId=1&date={day}")
    return [g for d in data.get("dates", []) for g in d.get("games", [])]


def mlb_game_state(game_pk: str | int, with_plays: bool = True) -> GameState:
    """Full live state for one MLB game.

    ``detail`` carries the situation that actually drives in-game markets:
    count, runners, outs, current pitcher and batter, and pitch count.
    """
    feed = _get(f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live")
    gd, ld = feed.get("gameData", {}), feed.get("liveData", {})
    linescore = ld.get("linescore", {})
    teams = gd.get("teams", {})
    abstract = gd.get("status", {}).get("abstractGameState", "")
    status = {"Preview": "scheduled", "Live": "in_progress",
              "Final": "final"}.get(abstract, abstract.lower())

    offense = linescore.get("offense", {})
    defense = linescore.get("defense", {})
    detail = {
        "balls": linescore.get("balls"), "strikes": linescore.get("strikes"),
        "outs": linescore.get("outs"),
        "runners": {b: bool(offense.get(b)) for b in ("first", "second", "third")},
        "pitcher": (defense.get("pitcher") or {}).get("fullName"),
        "batter": (offense.get("batter") or {}).get("fullName"),
        "inning_half": linescore.get("inningHalf"),
        "pitch_count": (ld.get("boxscore", {}).get("teams", {}) or {}),
    }

    plays: list[Play] = []
    if with_plays:
        for p in ld.get("plays", {}).get("allPlays", []):
            about, result = p.get("about", {}), p.get("result", {})
            plays.append(Play(
                ts=about.get("endTime") or about.get("startTime") or "",
                period=f"{about.get('halfInning','')} {about.get('inning','')}".strip(),
                clock="", team=(p.get("matchup", {}).get("batter", {}) or {}).get("fullName", ""),
                description=result.get("description", ""),
                scoring=bool(about.get("isScoringPlay")),
                players=[v.get("fullName", "") for k, v in (p.get("matchup") or {}).items()
                         if isinstance(v, dict) and v.get("fullName")],
            ))

    return GameState(
        game_id=str(game_pk), league="MLB", status=status,
        home=teams.get("home", {}).get("name", ""),
        away=teams.get("away", {}).get("name", ""),
        home_score=linescore.get("teams", {}).get("home", {}).get("runs", 0) or 0,
        away_score=linescore.get("teams", {}).get("away", {}).get("runs", 0) or 0,
        period=str(linescore.get("currentInning", "")),
        clock=linescore.get("inningState", ""),
        fetched_at=_now(), detail=detail, plays=plays,
    )


# ---------------------------------------------------------------- NHL

def nhl_schedule(date: str | None = None) -> list[dict]:
    day = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = _get(f"{NHL_API}/schedule/{day}")
    return [g for wk in data.get("gameWeek", []) for g in wk.get("games", [])]


def nhl_game_state(game_id: str | int, with_plays: bool = True) -> GameState:
    pbp = _get(f"{NHL_API}/gamecenter/{game_id}/play-by-play")
    state = {"FUT": "scheduled", "PRE": "scheduled", "LIVE": "in_progress",
             "CRIT": "in_progress", "OFF": "final",
             "FINAL": "final"}.get(pbp.get("gameState", ""), "unknown")
    plays: list[Play] = []
    if with_plays:
        for p in pbp.get("plays", []):
            plays.append(Play(
                ts=p.get("timeInPeriod", ""),
                period=str(p.get("periodDescriptor", {}).get("number", "")),
                clock=p.get("timeRemaining", ""), team="",
                description=p.get("typeDescKey", ""),
                scoring=p.get("typeDescKey") == "goal",
            ))
    return GameState(
        game_id=str(game_id), league="NHL", status=state,
        home=pbp.get("homeTeam", {}).get("commonName", {}).get("default", ""),
        away=pbp.get("awayTeam", {}).get("commonName", {}).get("default", ""),
        home_score=pbp.get("homeTeam", {}).get("score", 0) or 0,
        away_score=pbp.get("awayTeam", {}).get("score", 0) or 0,
        period=str(pbp.get("periodDescriptor", {}).get("number", "")),
        clock=pbp.get("clock", {}).get("timeRemaining", ""),
        fetched_at=_now(), plays=plays,
    )


# ---------------------------------------------------------------- ESPN

def espn_scoreboard(league: str, date: str | None = None) -> list[dict]:
    """Today's fixtures for a league, with ESPN event ids."""
    if league not in ESPN_PATHS:
        raise ValueError(f"no ESPN path for {league}; add one to ESPN_PATHS")
    sport, lg = ESPN_PATHS[league]
    qs = f"?dates={date.replace('-', '')}" if date else ""
    return _get(f"{ESPN_API}/{sport}/{lg}/scoreboard{qs}").get("events", [])


def espn_game_state(league: str, event_id: str, with_plays: bool = True) -> GameState:
    sport, lg = ESPN_PATHS[league]
    data = _get(f"{ESPN_API}/{sport}/{lg}/summary?event={event_id}")
    comp = (data.get("header", {}).get("competitions") or [{}])[0]
    competitors = comp.get("competitors", [])
    home = next((c for c in competitors if c.get("homeAway") == "home"), {})
    away = next((c for c in competitors if c.get("homeAway") == "away"), {})
    st = comp.get("status", {}).get("type", {})
    status = ("final" if st.get("completed") else
              "in_progress" if st.get("state") == "in" else "scheduled")

    plays: list[Play] = []
    if with_plays:
        for p in (data.get("plays") or []):
            plays.append(Play(
                ts=p.get("wallclock", ""),
                period=str(p.get("period", {}).get("number", "")),
                clock=p.get("clock", {}).get("displayValue", ""),
                team=(p.get("team") or {}).get("id", ""),
                description=p.get("text", ""),
                scoring=bool(p.get("scoringPlay")),
            ))

    return GameState(
        game_id=str(event_id), league=league, status=status,
        home=(home.get("team") or {}).get("displayName", ""),
        away=(away.get("team") or {}).get("displayName", ""),
        home_score=int(home.get("score") or 0),
        away_score=int(away.get("score") or 0),
        period=str(comp.get("status", {}).get("period", "")),
        clock=comp.get("status", {}).get("displayClock", ""),
        fetched_at=_now(),
        detail={"situation": data.get("situation", {}),
                "leaders": data.get("leaders", [])},
        plays=plays,
    )


# ---------------------------------------------------------------- routing

def game_state(league: str, game_id: str, with_plays: bool = True) -> GameState:
    """Fetch state for any supported league through one call."""
    if league == "MLB":
        return mlb_game_state(game_id, with_plays)
    if league == "NHL":
        return nhl_game_state(game_id, with_plays)
    return espn_game_state(league, game_id, with_plays)


def todays_games(league: str, date: str | None = None) -> list[dict]:
    """Fixtures for a league today, normalised to ``{id, home, away, start}``."""
    if league == "MLB":
        return [{"id": str(g["gamePk"]),
                 "home": g["teams"]["home"]["team"]["name"],
                 "away": g["teams"]["away"]["team"]["name"],
                 "start": g.get("gameDate", "")} for g in mlb_schedule(date)]
    if league == "NHL":
        return [{"id": str(g["id"]),
                 "home": g.get("homeTeam", {}).get("abbrev", ""),
                 "away": g.get("awayTeam", {}).get("abbrev", ""),
                 "start": g.get("startTimeUTC", "")} for g in nhl_schedule(date)]
    out = []
    for e in espn_scoreboard(league, date):
        comp = (e.get("competitions") or [{}])[0]
        cs = comp.get("competitors", [])
        h = next((c for c in cs if c.get("homeAway") == "home"), {})
        a = next((c for c in cs if c.get("homeAway") == "away"), {})
        out.append({"id": str(e.get("id")),
                    "home": (h.get("team") or {}).get("displayName", ""),
                    "away": (a.get("team") or {}).get("displayName", ""),
                    "start": e.get("date", "")})
    return out
