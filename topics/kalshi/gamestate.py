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

**ESPN throttles concurrency.** Twelve parallel requests returned errors for
every slug, including ones that work fine serially. Requests are paced through
a shared limiter here; do not fan out around it.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .taxonomy import COMPETITIONS

MLB_API = "https://statsapi.mlb.com/api/v1"
NHL_API = "https://api-web.nhle.com/v1"
ESPN_API = "https://site.api.espn.com/apis/site/v2/sports"

# Routing comes from taxonomy.COMPETITIONS — the single source of truth for
# which league maps to which ESPN endpoint. This module used to keep its own
# copy, which duplicated ten soccer leagues and kept them in step by luck.
ESPN_PATHS = {lg: tuple(path.split("/", 1))
              for lg, (_sport, path) in COMPETITIONS.items()}

# Kept for callers that want to reach a competition by name rather than by
# Kalshi league code. Superset entries live in taxonomy.SOCCER_STEMS.
SOCCER_SLUGS = {
    "eredivisie": "ned.1", "primeira": "por.1", "scottish": "sco.1",
    "belgian": "bel.1", "turkish": "tur.1", "greek": "gre.1",
    "austrian": "aut.1", "swiss": "sui.1", "danish": "den.1",
    "norwegian": "nor.1", "swedish": "swe.1", "polish": "pol.1",
    "argentine": "arg.1", "mexican": "mex.1", "colombian": "col.1",
    "japanese": "jpn.1", "korean": "kor.1", "australian": "aus.1",
    "egyptian": "egy.1", "usl": "usa.usl.1", "conference": "uefa.europa.conf",
    "libertadores": "conmebol.libertadores", "concacaf": "concacaf.champions",
}


@dataclass
class Play:
    """One discrete event: a pitch, a shot, a goal, a touchdown.

    ``ts`` is a real UTC timestamp wherever the feed provides one, which is what
    makes a play joinable to a market candle. ``sub_events`` carries the level
    below the play — every pitch of an at-bat, with velocity, movement and plate
    location — kept as the feed returned it rather than flattened into a schema
    that would differ per sport.
    """

    ts: str                        # ISO8601 UTC when available
    period: str                    # inning, quarter, half, period
    clock: str
    team: str
    description: str
    scoring: bool = False
    players: list[str] = field(default_factory=list)
    home_score: int | None = None  # after this play
    away_score: int | None = None
    play_type: str = ""
    coordinates: dict[str, Any] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)   # count, runners, matchup
    sub_events: list[dict] = field(default_factory=list)   # pitches, shots
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def utc(self) -> datetime | None:
        """Parsed timestamp, or None when the feed gave only a game clock."""
        if not self.ts:
            return None
        try:
            return datetime.fromisoformat(self.ts.replace("Z", "+00:00"))
        except ValueError:
            return None


@dataclass
class GameDetail:
    """Everything a feed will give about one fixture.

    ``GameState`` is the normalised core — score, clock, plays. This is the rest
    of it, and the sections are kept as the feed returned them rather than
    flattened, because what is present varies by sport and by how far the game
    has progressed. A pre-match fixture has odds and rosters but an empty
    boxscore; a finished one has the reverse.
    """

    state: GameState
    boxscore: dict[str, Any] = field(default_factory=dict)
    odds: list[dict] = field(default_factory=list)
    predictions: list[dict] = field(default_factory=list)   # ESPN pickcenter
    injuries: list[dict] = field(default_factory=list)
    rosters: list[dict] = field(default_factory=list)
    leaders: list[dict] = field(default_factory=list)
    win_probability: list[dict] = field(default_factory=list)
    standings: dict[str, Any] = field(default_factory=dict)
    last_five: list[dict] = field(default_factory=list)
    season_series: list[dict] = field(default_factory=list)
    venue: dict[str, Any] = field(default_factory=dict)
    officials: list[dict] = field(default_factory=list)
    attendance: int | None = None
    weather: dict[str, Any] = field(default_factory=dict)

    @property
    def book_lines(self) -> list[dict]:
        """Odds normalised to ``{provider, moneyline_home, moneyline_away,
        spread, total}``.

        ESPN carries live sportsbook prices, which is the only cross-venue
        reference in the package — everything else here is Kalshi's own view.
        """
        out = []
        for o in self.odds:
            home = (o.get("homeTeamOdds") or {}).get("moneyLine")
            away = (o.get("awayTeamOdds") or {}).get("moneyLine")
            out.append({
                "provider": (o.get("provider") or {}).get("name", ""),
                "moneyline_home": home, "moneyline_away": away,
                "spread": o.get("spread"), "total": o.get("overUnder"),
                "over_odds": o.get("overOdds"), "under_odds": o.get("underOdds"),
                "details": o.get("details", ""),
            })
        return out

    def fair_probabilities(self, method: str = "proportional") -> dict | None:
        """De-vigged outcome probabilities from the best book line available.

        The only cross-venue reference in the package. Comparing a Kalshi price
        to a raw moneyline overstates edge by roughly half the vig, so the
        margin is removed first.

        **Soccer is three-way.** Home and away alone do not partition the
        outcome space — the draw does the rest — so de-vigging them as a pair
        produces nonsense. The draw is included when the book quotes it, and if
        the raw probabilities still sum below 1 the result is returned with
        ``complete=False`` rather than silently normalised, because a negative
        overround means an outcome is missing rather than that the book is
        generous.
        """
        from .implied import american_to_prob, devig, overround

        for line, raw_odds in zip(self.book_lines, self.odds):
            home, away = line["moneyline_home"], line["moneyline_away"]
            if home is None or away is None:
                continue
            draw = (raw_odds.get("drawOdds") or {}).get("moneyLine")

            labels = ["home", "away"] + (["draw"] if draw is not None else [])
            odds = [float(home), float(away)] + ([float(draw)] if draw is not None else [])
            raw = [american_to_prob(o) for o in odds]
            margin = overround(raw)
            complete = margin >= 0

            fair = devig(raw, method) if complete else raw
            out = {"provider": line["provider"], "overround": margin,
                   "complete": complete, "outcomes": labels,
                   "spread": line["spread"], "total": line["total"]}
            for label, f, r in zip(labels, fair, raw):
                out[f"{label}_fair"] = f
                out[f"{label}_raw"] = r
            return out
        return None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.to_dict()
        return d


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


_ESPN_LOCK = threading.Lock()
_ESPN_LAST = [0.0]
_ESPN_MIN_INTERVAL = 0.25       # 4/s; higher rates get refused


def _get(url: str, timeout: int = 20, throttle: bool = False) -> dict:
    if throttle:
        with _ESPN_LOCK:
            wait = _ESPN_MIN_INTERVAL - (time.monotonic() - _ESPN_LAST[0])
            if wait > 0:
                time.sleep(wait)
            _ESPN_LAST[0] = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
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
            matchup = p.get("matchup") or {}
            plays.append(Play(
                ts=about.get("endTime") or about.get("startTime") or "",
                period=f"{about.get('halfInning','')} {about.get('inning','')}".strip(),
                clock="", team=(matchup.get("batter") or {}).get("fullName", ""),
                description=result.get("description", ""),
                scoring=bool(about.get("isScoringPlay")),
                players=[v.get("fullName", "") for v in matchup.values()
                         if isinstance(v, dict) and v.get("fullName")],
                home_score=result.get("homeScore"), away_score=result.get("awayScore"),
                play_type=result.get("eventType", ""),
                detail={
                    "count": p.get("count") or {},
                    "runners": [
                        {"movement": r.get("movement") or {},
                         "details": r.get("details") or {}}
                        for r in (p.get("runners") or [])
                    ],
                    "rbi": result.get("rbi"), "is_out": result.get("isOut"),
                    "bat_side": (matchup.get("batSide") or {}).get("code"),
                    "pitch_hand": (matchup.get("pitchHand") or {}).get("code"),
                },
                # every pitch: velocity, break, plate location, timing, contact
                sub_events=[{
                    "index": e.get("index"), "is_pitch": e.get("isPitch"),
                    "pitch_number": e.get("pitchNumber"),
                    "start_time": e.get("startTime"), "end_time": e.get("endTime"),
                    "call": ((e.get("details") or {}).get("call") or {}).get("description"),
                    "pitch_type": ((e.get("details") or {}).get("type") or {}).get("description"),
                    "count": e.get("count") or {},
                    "pitch": e.get("pitchData") or {},
                    "hit": e.get("hitData") or {},
                } for e in (p.get("playEvents") or [])],
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

def espn_scoreboard(league: str, date: str | None = None,
                    espn_slug: str | None = None) -> list[dict]:
    """Today's fixtures for a league, with ESPN event ids."""
    if espn_slug:
        sport, lg = "soccer", espn_slug
    elif league in ESPN_PATHS:
        sport, lg = ESPN_PATHS[league]
    else:
        raise ValueError(f"no ESPN path for {league}; add one or pass espn_slug")
    qs = f"?dates={date.replace('-', '')}" if date else ""
    return _get(f"{ESPN_API}/{sport}/{lg}/scoreboard{qs}", throttle=True).get("events", [])


def espn_game_state(league: str, event_id: str, with_plays: bool = True) -> GameState:
    if league not in ESPN_PATHS:
        raise ValueError(f"no ESPN path for {league}; add one or pass espn_slug")
    sport, lg = ESPN_PATHS[league]
    return _espn_state_by_slug(sport, lg, event_id, with_plays, league)


def _espn_state_by_slug(sport: str, lg: str, event_id: str,
                        with_plays: bool, league_label: str) -> GameState:
    data = _get(f"{ESPN_API}/{sport}/{lg}/summary?event={event_id}", throttle=True)
    return _state_from_summary(data, league_label, event_id, with_plays)


def _state_from_summary(data: dict, league: str, event_id: str,
                        with_plays: bool = True) -> GameState:
    """Normalise an ESPN summary payload into a GameState.

    Split out so ``game_detail`` can parse the state from the payload it already
    has rather than paying for a second request.
    """
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
                period=str((p.get("period") or {}).get("number", "")),
                clock=(p.get("clock") or {}).get("displayValue", ""),
                team=(p.get("team") or {}).get("id", ""),
                description=p.get("text") or p.get("shortText", ""),
                scoring=bool(p.get("scoringPlay")),
                players=[(a.get("athlete") or {}).get("displayName", "")
                         for a in (p.get("participants") or [])],
                home_score=p.get("homeScore"), away_score=p.get("awayScore"),
                play_type=((p.get("type") or {}).get("text")
                           or (p.get("type") or {}).get("id") or ""),
                coordinates=p.get("coordinate") or p.get("coordinates") or {},
                detail={k: p[k] for k in
                        ("start", "end", "statYardage", "scoreValue", "shootout")
                        if k in p},
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

def for_series(series_class, game_id: str, with_plays: bool = True) -> GameState:
    """Fetch state for a market's fixture, routing on the series classification.

    Uses ``SeriesClass.espn_slug`` when the taxonomy resolved one — that is what
    covers the world-soccer tail that has no entry in ``ESPN_PATHS``.
    """
    slug = getattr(series_class, "espn_slug", "") or None
    return game_state(series_class.league, game_id, with_plays, espn_slug=slug)


def fixtures_for_series(series_class, date: str | None = None) -> list[dict]:
    """Today's fixtures for whatever competition a series covers."""
    slug = getattr(series_class, "espn_slug", "") or None
    return todays_games(series_class.league, date, espn_slug=slug)


def supported_leagues() -> list[str]:
    """Every league with a live game-state feed."""
    return sorted(set(ESPN_PATHS) | {"MLB", "NHL"})


def game_detail(league: str, game_id: str, espn_slug: str | None = None) -> GameDetail:
    """Comprehensive capture for one fixture — every section the feed offers.

    Costs one request. Sections absent for the sport, or not yet populated at
    this stage of the game, come back empty rather than missing.
    """
    path = espn_slug and f"soccer/{espn_slug}"
    if not path:
        pair = ESPN_PATHS.get(league)
        if not pair:
            raise ValueError(f"no ESPN path for {league}")
        path = "/".join(pair)

    data = _get(f"{ESPN_API}/{path}/summary?event={game_id}", throttle=True)
    state = _state_from_summary(data, league, game_id)
    info = data.get("gameInfo") or {}

    return GameDetail(
        state=state,
        boxscore=data.get("boxscore") or {},
        odds=data.get("odds") or [],
        predictions=data.get("pickcenter") or [],
        injuries=data.get("injuries") or [],
        rosters=data.get("rosters") or [],
        leaders=data.get("leaders") or [],
        win_probability=data.get("winprobability") or [],
        standings=data.get("standings") or {},
        last_five=data.get("lastFiveGames") or [],
        season_series=data.get("seasonseries") or [],
        venue=info.get("venue") or {},
        officials=info.get("officials") or [],
        attendance=info.get("attendance"),
        weather=info.get("weather") or {},
    )


def game_state(league: str, game_id: str, with_plays: bool = True,
               espn_slug: str | None = None) -> GameState:
    """Fetch state for any supported league through one call.

    MLB and NHL use their official feeds, which are richer than ESPN's. Pass
    ``espn_slug`` to reach a competition not in ``ESPN_PATHS`` — see
    ``SOCCER_SLUGS`` for the world-league tail.
    """
    if espn_slug:
        return _espn_state_by_slug("soccer", espn_slug, game_id, with_plays, league)
    if league == "MLB":
        return mlb_game_state(game_id, with_plays)
    if league == "NHL":
        return nhl_game_state(game_id, with_plays)
    return espn_game_state(league, game_id, with_plays)


def todays_games(league: str, date: str | None = None,
                 espn_slug: str | None = None) -> list[dict]:
    """Fixtures for a league today, normalised to ``{id, home, away, start}``."""
    if league == "MLB" and not espn_slug:
        return [{"id": str(g["gamePk"]),
                 "home": g["teams"]["home"]["team"]["name"],
                 "away": g["teams"]["away"]["team"]["name"],
                 "start": g.get("gameDate", "")} for g in mlb_schedule(date)]
    if league == "NHL" and not espn_slug:
        return [{"id": str(g["id"]),
                 "home": g.get("homeTeam", {}).get("abbrev", ""),
                 "away": g.get("awayTeam", {}).get("abbrev", ""),
                 "start": g.get("startTimeUTC", "")} for g in nhl_schedule(date)]
    out = []
    for e in espn_scoreboard(league, date, espn_slug):
        comp = (e.get("competitions") or [{}])[0]
        cs = comp.get("competitors", [])
        h = next((c for c in cs if c.get("homeAway") == "home"), {})
        a = next((c for c in cs if c.get("homeAway") == "away"), {})
        out.append({"id": str(e.get("id")),
                    "home": (h.get("team") or {}).get("displayName", ""),
                    "away": (a.get("team") or {}).get("displayName", ""),
                    "start": e.get("date", "")})
    return out
