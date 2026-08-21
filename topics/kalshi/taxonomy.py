"""Classify Kalshi series into sport, league and market type.

Kalshi encodes almost everything in the series ticker: ``KXNFLGAME`` is a pro
football game winner, ``KXNBA3PT`` is a player threes prop. There is no API
field for market type, so it is derived from the ticker and title. **Sport is
an API field** — ``series.tags`` carries it for 96% of sports series, so tags
are used first and the ticker regex is only a fallback. ``series.frequency``
separates fixtures (``custom``) from season futures (``annual``, ``one_off``).

Coverage on the 2026-08-17 sweep: 73% of sports series resolve to a named
league, 75% to a market type. The residue is the long tail of world leagues
and one-off event series; ``league == "UNKNOWN"`` is honest rather than wrong.

Derived from a sweep of all 13,029 series on 2026-08-17, of which 3,403 are
category ``Sports``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Sport(str, Enum):
    FOOTBALL = "football"          # American
    BASKETBALL = "basketball"
    BASEBALL = "baseball"
    HOCKEY = "hockey"
    SOCCER = "soccer"
    TENNIS = "tennis"
    GOLF = "golf"
    COMBAT = "combat"              # boxing, MMA
    MOTORSPORT = "motorsport"
    ESPORTS = "esports"
    CRICKET = "cricket"
    DARTS = "darts"
    CHESS = "chess"
    OLYMPICS = "olympics"
    OTHER = "other"


class MarketType(str, Enum):
    GAME_WINNER = "game_winner"
    SPREAD = "spread"
    TOTAL = "total"
    TEAM_TOTAL = "team_total"
    BTTS = "btts"                  # both teams to score
    PERIOD = "period"              # 1st half / quarter / period markets
    EXACT_SCORE = "exact_score"
    PLAYER_PROP = "player_prop"
    SEASON_WINS = "season_wins"
    CHAMPIONSHIP = "championship"
    QUALIFY = "qualify"            # advance / make playoffs
    AWARD = "award"
    DRAFT = "draft"
    TRANSFER = "transfer"          # players, managers, coaches
    OTHER = "other"


# Kalshi's own ``tags`` field, which covers 96% of sports series. Far more
# reliable than pattern-matching a ticker, so it is consulted first.
TAG_TO_SPORT = {
    "soccer": Sport.SOCCER, "basketball": Sport.BASKETBALL,
    "football": Sport.FOOTBALL, "cfb": Sport.FOOTBALL,
    "baseball": Sport.BASEBALL, "hockey": Sport.HOCKEY,
    "tennis": Sport.TENNIS, "table tennis": Sport.TENNIS,
    "golf": Sport.GOLF, "esports": Sport.ESPORTS, "video games": Sport.ESPORTS,
    "mma": Sport.COMBAT, "ufc": Sport.COMBAT, "boxing": Sport.COMBAT,
    "motorsport": Sport.MOTORSPORT, "cycling": Sport.MOTORSPORT,
    "cricket": Sport.CRICKET, "darts": Sport.DARTS, "chess": Sport.CHESS,
    "olympics": Sport.OLYMPICS,
    "rugby": Sport.OTHER, "lacrosse": Sport.OTHER, "squash": Sport.OTHER,
    "aussie rules": Sport.OTHER,
}

# Kalshi ``frequency`` values that mean "one fixture" rather than "a season".
FIXTURE_FREQUENCIES = {"custom", "daily", "weekly"}



# Soccer competitions keyed by ticker stem, each with the ESPN slug that serves
# it. Every slug here was validated against the live ESPN scoreboard endpoint on
# 2026-08-17; four candidates (Korean, Egyptian, Polish and Canadian top flights)
# were dropped because ESPN genuinely does not carry them.
#
# Matched by prefix, not regex. Ticker stems are concatenated words, so a
# pattern like ``\bMLS\b`` never fires on ``MLSGAME`` — the trailing word
# boundary has nothing to match against.
SOCCER_STEMS: dict[str, tuple[str, str]] = {
    # England
    "EPL": ("EPL", "eng.1"), "FACUP": ("FA_CUP", "eng.fa"),
    "EFLCUP": ("EFL_CUP", "eng.league_cup"), "ENGCS": ("ENG_SHIELD", "eng.charity"),
    "EFL": ("EFL", "eng.2"),
    # Spain, Italy, Germany, France
    "LALIGA": ("LALIGA", "esp.1"), "COPADELREY": ("COPA_DEL_REY", "esp.copa_del_rey"),
    "SERIEA": ("SERIEA", "ita.1"), "SERIEB": ("SERIEB", "ita.2"),
    "COPPAITALIA": ("COPPA_ITALIA", "ita.coppa_italia"),
    "BUNDESLIGA": ("BUNDESLIGA", "ger.1"), "DFBPOKAL": ("DFB_POKAL", "ger.dfb_pokal"),
    "LIGUE1": ("LIGUE1", "fra.1"), "COUPEDEFRANCE": ("COUPE_DE_FRANCE", "fra.coupe_de_france"),
    # UEFA
    "UCLW": ("UCL_W", "uefa.wchampions"), "UCL": ("UCL", "uefa.champions"),
    "UEL": ("UEL", "uefa.europa"), "UECL": ("UECL", "uefa.europa.conf"),
    "UEFASC": ("UEFA_SUPERCUP", "uefa.super_cup"), "UEFANL": ("UEFA_NATIONS", "uefa.nations"),
    # Americas
    "MLS": ("MLS", "usa.1"), "NWSL": ("NWSL", "usa.nwsl"),
    "USLCUP": ("US_OPEN_CUP", "usa.open"), "USL": ("USL", "usa.usl.1"),
    "LIGAEXP": ("LIGA_EXPANSION", "mex.2"), "LIGAMX": ("LIGAMX", "mex.1"),
    "BRASILEIRAOB": ("BRASILEIRAO_B", "bra.2"), "BRASILEIRO": ("BRASILEIRAO", "bra.1"),
    "ARGPREMDIV": ("ARGENTINA", "arg.1"), "URYPD": ("URUGUAY", "uru.1"),
    "CHILEAN": ("CHILE", "chi.1"), "COLOMBIAN": ("COLOMBIA", "col.1"),
    "DIMAYOR": ("COLOMBIA", "col.1"), "PERLIGA1": ("PERU", "per.1"),
    "ECULP": ("ECUADOR", "ecu.1"), "VENFUTVE": ("VENEZUELA", "ven.1"),
    "CONMEBOLLIB": ("LIBERTADORES", "conmebol.libertadores"),
    "CONMEBOLSUD": ("SUDAMERICANA", "conmebol.sudamericana"),
    "CONCACAFCL": ("CONCACAF_CL", "concacaf.champions"),
    "LEAGUESCUP": ("LEAGUES_CUP", "concacaf.leagues.cup"),
    # Rest of Europe
    "EREDIVISIE": ("EREDIVISIE", "ned.1"), "KNVBCUP": ("KNVB_CUP", "ned.cup"),
    "LIGAPORTUGAL": ("PRIMEIRA", "por.1"), "SCOTTISHPREM": ("SCOTTISH", "sco.1"),
    "BELGIANPL": ("BELGIUM", "bel.1"), "SUPERLIG": ("TURKEY", "tur.1"),
    "SLGREECE": ("GREECE", "gre.1"), "SWISSLEAGUE": ("SWITZERLAND", "sui.1"),
    "AUSTRIANBL": ("AUSTRIA", "aut.1"), "DENSUPERLIGA": ("DENMARK", "den.1"),
    "ALLSVENSKAN": ("SWEDEN", "swe.1"), "ELITESERIEN": ("NORWAY", "nor.1"),
    # Asia, Africa, Oceania
    "JLEAGUE": ("JLEAGUE", "jpn.1"), "CHNSL": ("CHINA", "chn.1"),
    "SAUDIPL": ("SAUDI", "ksa.1"), "THAIL1": ("THAILAND", "tha.1"),
    "INDIANSL": ("INDIA", "ind.1"), "ALEAGUE": ("A_LEAGUE", "aus.1"),
    "AFCCL": ("AFC_CL", "afc.champions"), "AFCAC": ("AFC_CL", "afc.champions"),
    "AFCON": ("AFCON", "caf.nations"), "FIFAW": ("FIFA_WWC", "fifa.wwc"),
}

# Longest stem first, so LIGAMX is not shadowed by LIGA-prefixed neighbours.
_COMPETITION_ORDER = sorted(SOCCER_STEMS, key=len, reverse=True)


def match_competition(stem: str) -> tuple[str, str] | None:
    """Resolve a ticker stem to ``(league, espn_slug)`` by longest prefix."""
    for key in _COMPETITION_ORDER:
        if stem.startswith(key):
            return SOCCER_STEMS[key]
    return None


# Non-soccer leagues, each one ESPN endpoint. ESPN keys most sports by league —
# ``basketball/nba`` is the whole NBA — but keys soccer by competition, which is
# why soccer needs one entry per tournament above and everything else needs one
# entry here.
NON_SOCCER_LEAGUES: dict[str, tuple[Sport, str]] = {
    "NFL": (Sport.FOOTBALL, "football/nfl"),
    "NCAAF": (Sport.FOOTBALL, "football/college-football"),
    "NBA": (Sport.BASKETBALL, "basketball/nba"),
    "WNBA": (Sport.BASKETBALL, "basketball/wnba"),
    "NCAAB": (Sport.BASKETBALL, "basketball/mens-college-basketball"),
    "INTLBASKET": (Sport.BASKETBALL, "basketball/nba"),
    "MLB": (Sport.BASEBALL, "baseball/mlb"),
    "NHL": (Sport.HOCKEY, "hockey/nhl"),
    "TENNIS": (Sport.TENNIS, "tennis/atp"),
    "GOLF": (Sport.GOLF, "golf/pga"),
    "COMBAT": (Sport.COMBAT, "mma/ufc"),
    "MOTORSPORT": (Sport.MOTORSPORT, "racing/f1"),
    "CRICKET": (Sport.CRICKET, "cricket/league"),
}


def _build_registry() -> dict[str, tuple[Sport, str]]:
    """One league -> (sport, ESPN path) map, soccer and everything else.

    Previously soccer routing lived in two places — a competition table here and
    a league table in gamestate — which agreed by luck and nothing else. This is
    the single source of truth; gamestate reads it rather than keeping a copy.
    """
    registry: dict[str, tuple[Sport, str]] = dict(NON_SOCCER_LEAGUES)
    for league, slug in SOCCER_STEMS.values():
        registry.setdefault(league, (Sport.SOCCER, f"soccer/{slug}"))
    return registry


COMPETITIONS: dict[str, tuple[Sport, str]] = _build_registry()


def resolve_league(value: str) -> str | None:
    """Map anything league-shaped onto a canonical league code.

    Callers guess, and reasonably: a Kalshi ticker reads ``KXSAUDIPLGAME``, so
    ``SAUDIPL`` is the obvious code to try — but the canonical one is ``SAUDI``.
    Rejecting that is a usability bug, not a correct validation, so this accepts
    the ticker stem, the ESPN slug and ordinary case variants.
    """
    if not value:
        return None
    probe = value.strip().upper().replace("-", "").replace("_", "")
    if probe in COMPETITIONS:
        return probe

    # A Kalshi series stem, e.g. SAUDIPL -> SAUDI, LIGAPORTUGAL -> PRIMEIRA.
    for stem, (league, _slug) in SOCCER_STEMS.items():
        if probe.startswith(stem) or stem.startswith(probe):
            return league

    # An ESPN slug, e.g. "eng.1" or "soccer/eng.1".
    tail = value.strip().lower().split("/")[-1]
    for league, (_sport, path) in COMPETITIONS.items():
        if path.split("/")[-1] == tail:
            return league

    for league in COMPETITIONS:
        if probe.startswith(league) or league.startswith(probe):
            return league
    return None


def espn_path(league: str) -> str | None:
    """ESPN ``sport/league`` path for a league code, or None if unwired."""
    hit = COMPETITIONS.get(league)
    return hit[1] if hit else None


# League detection. Order matters — longer, more specific stems first.
_LEAGUE_PATTERNS: list[tuple[str, str, Sport]] = [
    # American football
    (r"\bNCAAF|CFB|COLLEGEFOOT", "NCAAF", Sport.FOOTBALL),
    (r"\bNFL", "NFL", Sport.FOOTBALL),
    # Basketball
    (r"\bWNBA", "WNBA", Sport.BASKETBALL),
    (r"\bNCAAM?B|NCAAW?B|MARMAD|CBB", "NCAAB", Sport.BASKETBALL),
    (r"\bNBA", "NBA", Sport.BASKETBALL),
    (r"\bEUROLEAGUE|\bDBB|\bVBA", "INTLBASKET", Sport.BASKETBALL),
    # Baseball
    (r"\bMLB|\bNLGAME|\bALGAME|\bNL[A-Z]*WEST|\bAL[A-Z]*WEST|WORLDSERIES", "MLB", Sport.BASEBALL),
    (r"\bKBO|\bNPB", "INTLBASEBALL", Sport.BASEBALL),
    # Hockey
    (r"\bNHL|STANLEYCUP", "NHL", Sport.HOCKEY),
    # Soccer — domestic
    (r"\bEPL|PREMIERLEAGUE", "EPL", Sport.SOCCER),
    (r"\bLALIGA", "LALIGA", Sport.SOCCER),
    (r"\bSERIEA", "SERIEA", Sport.SOCCER),
    (r"\bBUNDESLIGA", "BUNDESLIGA", Sport.SOCCER),
    (r"\bLIGUE1|FRALIGUE", "LIGUE1", Sport.SOCCER),
    (r"\bMLS", "MLS", Sport.SOCCER),
    (r"\bNWSL", "NWSL", Sport.SOCCER),
    (r"\bEFL|CHAMPIONSHIP1H", "EFL", Sport.SOCCER),
    (r"\bBRASILEIRO|COPADOBRAS", "BRASILEIRAO", Sport.SOCCER),
    (r"\bEREDIVISIE|LIGAPORTUG|SCOTTISHPR|EKSTRAKLAS|ALLSVENSKA|ELITESERIE"
     r"|DENSUPERLI|SWISSLEAGU|ARGPREMDIV|DIMAYOR|USL\b|EGYPL|URYPD|CANPL"
     r"|EERSTEDIV|ASEAN|VENFUTVE|\bJ1LEAGUE|\bJ2LEAGUE|KLEAGUE|ALEAGUE"
     r"|SUPERLIG|BELPRO|AUTBUND|GREEKSL|TURKSL|CHISUPER|MEXLIGA|LIGAMX",
     "OTHERLEAGUE", Sport.SOCCER),
    # Soccer — cups and international
    (r"\bUEFACL|CHAMPIONSLEAGUE", "UCL", Sport.SOCCER),
    (r"\bUEL\d*|EUROPALEAGUE|\bUECL|CONFERENCELEAGUE|UEFANL|NATIONSLEAGUE",
     "UEFA_OTHER", Sport.SOCCER),
    (r"\bAFCON|ASIANCUP|GOLDCUP|COPAAMERICA|\bWC[A-Z]*|WORLDCUP|EURO20|EURO24",
     "INTERNATIONAL", Sport.SOCCER),
    (r"\bCONMEBOL|CONCACAF|LEAGUESCUP|CLUBWC|FINALISSIM|INTLFRIEND"
     r"|COPPAITALI|COUPEDEFRA|COPADELREY|[A-Z]{3}SUPERCU|FINCUP|TACAPORT"
     r"|CLUBF|SOCCERTRANSFER", "CUP", Sport.SOCCER),
    # Individual
    (r"\bATP|\bWTA|TENNIS|EXHIBITIONMEN|\bFO(MEN|WOMEN)|USOPEN|WIMBLEDON"
     r"|AUSOPEN|ROLANDGARROS", "TENNIS", Sport.TENNIS),
    (r"\bPGA|LIVGOLF|DPWORLDTOU|RYDERCUP|MASTERS", "GOLF", Sport.GOLF),
    (r"\bUFC|\bBOXING|\bMMA", "COMBAT", Sport.COMBAT),
    (r"\bF1|FORMULA|NASCAR|MOTOGP|INDYCAR|LEMANS|WRC\b", "MOTORSPORT", Sport.MOTORSPORT),
    (r"VALORANT|\bLOL\b|\bCSGO|\bCS2\b|\bDOTA|ESPORT|\bESL[A-Z]*|OVERWATCH"
     r"|ROCKETLEAGUE|APEX", "ESPORTS", Sport.ESPORTS),
    (r"\bIPL|CRICKET|\bT20|\bBBL|TESTMATCH", "CRICKET", Sport.CRICKET),
    (r"\bPDC|DARTS", "DARTS", Sport.DARTS),
    (r"\bCHESS", "CHESS", Sport.CHESS),
    (r"OLYMPIC|WINTERGAMES|SUMMERGAMES|PARALYMP", "OLYMPICS", Sport.OLYMPICS),
    (r"SQUASH|WRESTL|VOLLEY|RUGBY|\bNRL\b|\bAFL\b|HANDBALL|CYCLING|SNOOKER",
     "OTHER", Sport.OTHER),
]

# Market type detection, matched against ticker and title together.
_TYPE_PATTERNS: list[tuple[str, MarketType]] = [
    (r"1H|2H|FIRSTHALF|QUARTER|PERIOD|1STQ|1ST HALF|2ND HALF|FIRST HALF"
     r"|1ST QUARTER|FIRST PERIOD", MarketType.PERIOD),
    (r"EXACT(WINS|SCORE)?|FINALSEXACT|CORRECT SCORE|EXACT SCORE", MarketType.EXACT_SCORE),
    (r"TEAMTOTAL|TEAM TOTAL", MarketType.TEAM_TOTAL),
    (r"TOTAL|OVERUNDER|ROUNDS|MAPS|POINT TOTAL|OVER/UNDER", MarketType.TOTAL),
    (r"SPREAD|HANDICAP|MARGIN", MarketType.SPREAD),
    (r"BTTS|BOTH TEAMS", MarketType.BTTS),
    (r"WINS$|SEASONR|REGSEASON|WINTOTAL", MarketType.SEASON_WINS),
    (r"ADVANCE|QUALIF|MAKEPLAYOFF|SEED|TO REACH|KNOCKOUT", MarketType.QUALIFY),
    (r"CHAMP|TITLE|CUP|TOP ?\d*|LEADER|FINALS|DIVISION|CONFERENCE"
     r"|\b(AL|NL)(EAST|WEST|CENTRAL)\b|SEASON WINNER|WIN THE", MarketType.CHAMPIONSHIP),
    (r"MVP|MOTY|COMEBACK|ALLSTAR|ROTY|AWARD|GOLDGLOVE", MarketType.AWARD),
    (r"DRAFT", MarketType.DRAFT),
    (r"TRANSFER|NEXTMANAGE|NEXTTEAM|COACHON|NEXT TEAM|NEXT MANAGER", MarketType.TRANSFER),
    (r"3PT|GOAL|FIRSTTD|RSHYDS|PASSYDS|RECYDS|STRIKEOUT|\bHR\b|\bSB\b"
     r"|POINTS|ASSISTS|REBOUND|SAVES|PLAYER|SCORER|HRDERBY", MarketType.PLAYER_PROP),
    (r"GAME|MONEYLINE|MATCH|WINNER", MarketType.GAME_WINNER),
]


@dataclass(frozen=True)
class SeriesClass:
    """Classification of one Kalshi series."""

    ticker: str
    sport: Sport
    league: str
    market_type: MarketType
    title: str = ""
    frequency: str = ""
    sport_source: str = "regex"   # "tag" when it came from Kalshi's own field
    espn_slug: str = ""           # set when a game-state feed is known

    @property
    def is_fixture(self) -> bool:
        """A *hint* that the series covers single fixtures rather than a season.

        **Not authoritative, and must not be used as a filter on its own.**
        Kalshi's ``frequency`` is the only signal available on a series object,
        and ``custom`` is a catch-all: it covers per-game series and season
        futures alike, so the World Series winner market reads as a fixture
        here. It is right far more often than not, which is exactly what makes
        it dangerous alone.

        The definitive test needs an event ticker, since only a fixture encodes
        a date and two team codes — see :func:`linking.is_fixture_event`, which
        :meth:`discovery.Discovery.whats_bettable` applies for free on markets
        it has already fetched.
        """
        if self.frequency:
            return self.frequency in FIXTURE_FREQUENCIES
        return self.is_game_level

    @property
    def is_game_level(self) -> bool:
        """True for market types tied to a single fixture."""
        return self.market_type in {
            MarketType.GAME_WINNER,
            MarketType.SPREAD,
            MarketType.TOTAL,
            MarketType.TEAM_TOTAL,
            MarketType.BTTS,
            MarketType.PERIOD,
            MarketType.EXACT_SCORE,
            MarketType.PLAYER_PROP,
        }


def _strip_prefix(ticker: str) -> str:
    return ticker[2:] if ticker.startswith("KX") else ticker


def classify_series(ticker: str, title: str = "", category: str = "",
                    tags: list[str] | None = None,
                    frequency: str = "") -> SeriesClass:
    """Classify a series ticker into sport, league and market type.

    ``category`` is Kalshi's own field; when it is present and not ``Sports``
    the series is returned as OTHER/OTHER so non-sports series can be filtered
    cheaply without a second pass.
    """
    if category and category != "Sports":
        return SeriesClass(ticker, Sport.OTHER, "NONSPORT", MarketType.OTHER,
                           title, frequency, "category")

    stem = _strip_prefix(ticker).upper()
    hay = f"{stem} {title.upper()}".strip()

    # Kalshi's tag is authoritative for sport when present.
    tag_sport = None
    for tag in tags or []:
        hit = TAG_TO_SPORT.get(tag.strip().lower())
        if hit:
            tag_sport = hit
            break

    sport, league, slug = Sport.OTHER, "UNKNOWN", ""

    competition = match_competition(stem)
    if competition:
        league, slug = competition
        sport = Sport.SOCCER

    for pattern, lg, sp in ([] if competition else _LEAGUE_PATTERNS):
        if re.search(pattern, hay):
            sport, league = sp, lg
            break

    sport_source = "regex"
    if tag_sport:
        sport_source = "tag"
        if sport is Sport.OTHER or sport is not tag_sport:
            # The tag wins on sport. Keep a league only if it agrees.
            if sport is not tag_sport:
                league = league if sport is tag_sport else _generic_league(tag_sport, league)
            sport = tag_sport

    if sport is Sport.OTHER and re.search(
            r"\bBTTS\b|GOALSCORER|CLEAN ?SHEET|\bCORNERS?\b|\bGOALS?\b|BOTH TEAMS", hay):
        sport, league = Sport.SOCCER, "OTHERLEAGUE"

    market_type = MarketType.OTHER
    for pattern, mt in _TYPE_PATTERNS:
        # Match the ticker stem and the title separately: the stem is
        # concatenated words, so word-boundary anchors only work on the title.
        if re.search(pattern, stem) or re.search(pattern, title.upper()):
            market_type = mt
            break

    return SeriesClass(ticker, sport, league, market_type, title,
                       frequency, sport_source, slug)


def _generic_league(sport: Sport, current: str) -> str:
    """A per-sport catch-all so an unmatched league is still usable.

    Better than UNKNOWN: an agent filtering on ``sport`` still gets everything,
    and the label says which sport rather than nothing.
    """
    if current not in ("UNKNOWN", "NONSPORT"):
        return current
    return {
        Sport.SOCCER: "SOCCER_OTHER", Sport.BASKETBALL: "BASKET_OTHER",
        Sport.FOOTBALL: "FOOTBALL_OTHER", Sport.BASEBALL: "BASEBALL_OTHER",
        Sport.HOCKEY: "HOCKEY_OTHER", Sport.TENNIS: "TENNIS",
        Sport.GOLF: "GOLF", Sport.COMBAT: "COMBAT", Sport.ESPORTS: "ESPORTS",
        Sport.MOTORSPORT: "MOTORSPORT", Sport.CRICKET: "CRICKET",
        Sport.DARTS: "DARTS", Sport.CHESS: "CHESS", Sport.OLYMPICS: "OLYMPICS",
    }.get(sport, "UNKNOWN")


def classify_many(series: list[dict]) -> list[SeriesClass]:
    """Classify a list of series objects as returned by ``/series``."""
    return [
        classify_series(s.get("ticker", ""), s.get("title", ""),
                        s.get("category", ""), s.get("tags"), s.get("frequency", ""))
        for s in series
    ]


# Leagues for which a live game-state feed exists in gamestate.py.
LIVE_STATE_SUPPORTED = {"MLB", "NHL", "NFL", "NBA", "WNBA", "NCAAF", "NCAAB",
                        "EPL", "LALIGA", "SERIEA", "BUNDESLIGA", "LIGUE1", "MLS", "UCL"}
