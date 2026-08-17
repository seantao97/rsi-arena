"""Join a Kalshi event to the fixture it is about.

Kalshi encodes fixtures in the event ticker::

    KXMLBSPREAD-26AUG171910AZBOS      26 Aug 2026, 19:10, AZ at BOS
    KXWNBAGAME-26AUG19MINGS           19 Aug 2026, MIN at GS
    KXVALORANTMAP-26AUG191700C9BST-2  19 Aug 2026, 17:00, C9 vs BST, map 2

Season-long events have no fixture and are skipped::

    KXNFLTEAMPTS-LEAST27  KXEPLTEAMPOINTS-27

Two problems, both solved here:

1. **Team codes are concatenated with no separator.** ``AZBOS`` is AZ at BOS,
   but ``MINGS`` is MIN at GS. Splitting needs to know which codes exist, so
   ``harvest_team_codes`` reads them out of the market tickers under a series
   rather than relying on a hardcoded table that would rot.

2. **Kalshi's codes are its own.** They are not ESPN's or MLB's, so matching to
   a game feed is done on date plus a scored name comparison.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from .client import KalshiClient

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}

# YYMONDD, optional HHMM, then the concatenated team blob.
_FIXTURE_RE = re.compile(
    r"^(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})(?P<hhmm>\d{4})?(?P<teams>[A-Z0-9]+)$"
)


@dataclass(frozen=True)
class Fixture:
    """A fixture parsed out of a Kalshi event ticker."""

    event_ticker: str
    series_ticker: str
    date: date
    start_utc: datetime | None
    away_code: str | None
    home_code: str | None
    team_blob: str
    suffix: str = ""          # trailing segment such as a map or game number

    @property
    def is_split(self) -> bool:
        return bool(self.away_code and self.home_code)


@dataclass(frozen=True)
class Link:
    """A correspondence between a Kalshi event and a game-feed fixture."""

    event_ticker: str
    league: str
    game_id: str
    home: str
    away: str
    confidence: float
    method: str


def parse_event_ticker(event_ticker: str, series_ticker: str | None = None,
                       team_codes: set[str] | None = None) -> Fixture | None:
    """Parse an event ticker into a fixture, or ``None`` if it is not one.

    Pass ``team_codes`` to split the concatenated team blob; without it the
    blob is returned whole and ``is_split`` is False.
    """
    if "-" not in event_ticker:
        return None
    series, _, rest = event_ticker.partition("-")
    if series_ticker and series != series_ticker:
        series = series_ticker

    suffix = ""
    if "-" in rest:                       # trailing map/game number
        rest, _, suffix = rest.partition("-")

    m = _FIXTURE_RE.match(rest)
    if not m:
        return None

    try:
        month = _MONTHS[m.group("mon")]
        day = int(m.group("dd"))
        year = 2000 + int(m.group("yy"))
        when = date(year, month, day)
    except (KeyError, ValueError):
        return None

    start = None
    if m.group("hhmm"):
        hh, mm = int(m.group("hhmm")[:2]), int(m.group("hhmm")[2:])
        if hh < 24 and mm < 60:
            start = datetime(year, month, day, hh, mm, tzinfo=timezone.utc)

    blob = m.group("teams")
    away = home = None
    if team_codes:
        away, home = split_team_blob(blob, team_codes)

    return Fixture(event_ticker, series, when, start, away, home, blob, suffix)


def split_team_blob(blob: str, codes: set[str]) -> tuple[str | None, str | None]:
    """Split ``AZBOS`` into ``("AZ", "BOS")`` using a set of known codes.

    Kalshi writes away then home. Where more than one split is valid, the one
    with the most balanced code lengths wins, which resolves the common
    two-versus-three character ambiguity correctly in practice.
    """
    candidates: list[tuple[int, str, str]] = []
    for i in range(2, len(blob) - 1):
        left, right = blob[:i], blob[i:]
        if left in codes and right in codes:
            candidates.append((abs(len(left) - len(right)), left, right))
    if not candidates:
        return None, None
    candidates.sort()
    return candidates[0][1], candidates[0][2]


def harvest_team_codes(client: KalshiClient, series_ticker: str,
                       status: str | None = None) -> set[str]:
    """Collect the team codes a series actually uses.

    Market tickers end in the code they refer to — ``...-BOS4`` is a Boston
    spread line, ``...-MIN`` a Minnesota moneyline. Stripping trailing digits
    recovers the code. Self-bootstrapping, so no table to maintain.

    ``status=None`` sweeps every market ever listed under the series, which
    gives the full code set. Pass ``"open"`` to limit it to what is live now.
    """
    ignore = {"TIE", "YES", "NO", "AL", "NL"}   # AL/NL are all-star, not teams
    codes: set[str] = set()
    for m in client.paginate("/markets", "markets",
                             {"series_ticker": series_ticker, "status": status}):
        ticker = m.get("ticker", "")
        tail = ticker.rsplit("-", 1)[-1] if "-" in ticker else ""
        tail = re.sub(r"\d+$", "", tail)          # drop a spread or total number
        if tail and tail.isalnum() and tail not in ignore and len(tail) <= 4:
            codes.add(tail)
    return codes


def _score_match(kalshi_code: str, feed_name: str) -> float:
    """How well a Kalshi team code matches a feed team name. 0..1."""
    if not kalshi_code or not feed_name:
        return 0.0
    code = kalshi_code.upper()
    name = re.sub(r"[^A-Z ]", "", feed_name.upper())
    words = name.split()
    if not words:
        return 0.0
    initials = "".join(w[0] for w in words)
    if code == initials:
        return 0.95
    for w in words:
        if w.startswith(code):
            return 0.9
    if name.replace(" ", "").startswith(code):
        return 0.85
    # every character of the code appears in order somewhere in the name
    pos, hits = 0, 0
    flat = name.replace(" ", "")
    for ch in code:
        idx = flat.find(ch, pos)
        if idx >= 0:
            pos, hits = idx + 1, hits + 1
    return 0.5 * hits / len(code)


def match_event_to_game(fixture: Fixture, games: list[dict],
                        min_confidence: float = 0.6) -> Link | None:
    """Match one parsed fixture against a league's fixtures for that date.

    ``games`` is the output of ``gamestate.todays_games`` — dicts with ``id``,
    ``home``, ``away`` and ``start``.
    """
    if not fixture.is_split:
        return None
    best, best_score = None, 0.0
    for g in games:
        direct = (_score_match(fixture.away_code or "", g.get("away", "")) +
                  _score_match(fixture.home_code or "", g.get("home", ""))) / 2
        # Kalshi is away-then-home, but tolerate a feed that disagrees.
        swapped = (_score_match(fixture.away_code or "", g.get("home", "")) +
                   _score_match(fixture.home_code or "", g.get("away", ""))) / 2
        score = max(direct, swapped)
        if score > best_score:
            best, best_score = g, score
    if not best or best_score < min_confidence:
        return None
    return Link(
        event_ticker=fixture.event_ticker, league="", game_id=str(best["id"]),
        home=best.get("home", ""), away=best.get("away", ""),
        confidence=round(best_score, 3), method="date+name",
    )


def link_series(client: KalshiClient, series_ticker: str, league: str,
                games_by_date, min_confidence: float = 0.6) -> list[Link]:
    """Link every open event under a series to a fixture in the game feed.

    ``games_by_date`` is a callable ``(league, iso_date) -> list[dict]``; pass
    ``gamestate.todays_games`` wrapped to accept a date.
    """
    codes = harvest_team_codes(client, series_ticker)
    links: list[Link] = []
    cache: dict[str, list[dict]] = {}

    for ev in client.paginate("/events", "events",
                              {"series_ticker": series_ticker, "status": "open"}):
        fixture = parse_event_ticker(ev.get("event_ticker", ""), series_ticker, codes)
        if not fixture or not fixture.is_split:
            continue
        iso = fixture.date.isoformat()
        if iso not in cache:
            try:
                cache[iso] = games_by_date(league, iso)
            except Exception:
                cache[iso] = []
        link = match_event_to_game(fixture, cache[iso], min_confidence)
        if link:
            links.append(Link(link.event_ticker, league, link.game_id,
                              link.home, link.away, link.confidence, link.method))
    return links


def link_league(client: KalshiClient, league: str, series_tickers: list[str],
                games_by_date, min_confidence: float = 0.6) -> list[Link]:
    """Link every game-level series in a league."""
    out: list[Link] = []
    for st in series_tickers:
        try:
            out += link_series(client, st, league, games_by_date, min_confidence)
        except Exception as exc:
            print(f"[link] {st}: {exc}")
    return out
