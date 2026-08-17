"""Classify Kalshi series into sport, league and market type.

Kalshi encodes almost everything in the series ticker: ``KXNFLGAME`` is a pro
football game winner, ``KXNBA3PT`` is a player threes prop. There is no API
field for sport or market type, so this module derives both from the ticker
and title together.

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


# League detection. Order matters — longer, more specific stems first.
_LEAGUE_PATTERNS: list[tuple[str, str, Sport]] = [
    # American football
    (r"\bNCAAF|CFB|COLLEGEFOOT", "NCAAF", Sport.FOOTBALL),
    (r"\bNFL", "NFL", Sport.FOOTBALL),
    # Basketball
    (r"\bWNBA", "WNBA", Sport.BASKETBALL),
    (r"\bNCAAM?B|NCAAW?B|MARMAD|CBB", "NCAAB", Sport.BASKETBALL),
    (r"\bNBA", "NBA", Sport.BASKETBALL),
    (r"\bEUROLEAGUE|\bDBB|\bVBA\b", "INTLBASKET", Sport.BASKETBALL),
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
    (r"\bMLS\b", "MLS", Sport.SOCCER),
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
    (r"\bF1\b|FORMULA|NASCAR|MOTOGP|INDYCAR|LEMANS|WRC\b", "MOTORSPORT", Sport.MOTORSPORT),
    (r"VALORANT|\bLOL\b|\bCSGO|\bCS2\b|\bDOTA|ESPORT|\bESL[A-Z]*|OVERWATCH"
     r"|ROCKETLEAGUE|APEX", "ESPORTS", Sport.ESPORTS),
    (r"\bIPL\b|CRICKET|\bT20\b|\bBBL\b|TESTMATCH", "CRICKET", Sport.CRICKET),
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
    (r"\bBTTS\b|BOTH TEAMS", MarketType.BTTS),
    (r"WINS$|SEASONR|REGSEASON|WINTOTAL", MarketType.SEASON_WINS),
    (r"ADVANCE|QUALIF|MAKEPLAYOFF|SEED|TO REACH|KNOCKOUT", MarketType.QUALIFY),
    (r"CHAMP|TITLE|\bCUP\b|TOP ?\d*|LEADER|FINALS|DIVISION|CONFERENCE"
     r"|\b(AL|NL)(EAST|WEST|CENTRAL)\b|SEASON WINNER|WIN THE", MarketType.CHAMPIONSHIP),
    (r"MVP|MOTY|COMEBACK|ALLSTAR|ROTY|AWARD|GOLDGLOVE", MarketType.AWARD),
    (r"DRAFT", MarketType.DRAFT),
    (r"TRANSFER|NEXTMANAGE|NEXTTEAM|COACHON|NEXT TEAM|NEXT MANAGER", MarketType.TRANSFER),
    (r"3PT|GOAL|FIRSTTD|RSHYDS|PASSYDS|RECYDS|STRIKEOUT|\bHR\b|\bSB\b"
     r"|POINTS|ASSISTS|REBOUND|SAVES|PLAYER|SCORER|HRDERBY", MarketType.PLAYER_PROP),
    (r"GAME$|GAME[A-Z]*$|MONEYLINE|GAME WINNER|\bGAME\b|\bMATCH\b|WINNER",
     MarketType.GAME_WINNER),
]


@dataclass(frozen=True)
class SeriesClass:
    """Classification of one Kalshi series."""

    ticker: str
    sport: Sport
    league: str
    market_type: MarketType
    title: str = ""

    @property
    def is_game_level(self) -> bool:
        """True for markets tied to a single fixture, as opposed to season futures.

        Game-level markets are the ones worth joining against live game state.
        """
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


def classify_series(ticker: str, title: str = "", category: str = "") -> SeriesClass:
    """Classify a series ticker into sport, league and market type.

    ``category`` is Kalshi's own field; when it is present and not ``Sports``
    the series is returned as OTHER/OTHER so non-sports series can be filtered
    cheaply without a second pass.
    """
    if category and category != "Sports":
        return SeriesClass(ticker, Sport.OTHER, "NONSPORT", MarketType.OTHER, title)

    stem = _strip_prefix(ticker).upper()
    hay = f"{stem} {title.upper()}"

    sport, league = Sport.OTHER, "UNKNOWN"
    for pattern, lg, sp in _LEAGUE_PATTERNS:
        if re.search(pattern, hay):
            sport, league = sp, lg
            break

    if sport is Sport.OTHER and re.search(
            r"\bBTTS\b|GOALSCORER|CLEAN ?SHEET|\bCORNERS?\b|\bGOALS?\b|BOTH TEAMS", hay):
        sport, league = Sport.SOCCER, "OTHERLEAGUE"

    market_type = MarketType.OTHER
    for pattern, mt in _TYPE_PATTERNS:
        if re.search(pattern, hay):
            market_type = mt
            break

    return SeriesClass(ticker, sport, league, market_type, title)


def classify_many(series: list[dict]) -> list[SeriesClass]:
    """Classify a list of series objects as returned by ``/series``."""
    return [
        classify_series(s.get("ticker", ""), s.get("title", ""), s.get("category", ""))
        for s in series
    ]


# Leagues for which a live game-state feed exists in gamestate.py.
LIVE_STATE_SUPPORTED = {"MLB", "NHL", "NFL", "NBA", "WNBA", "NCAAF", "NCAAB",
                        "EPL", "LALIGA", "SERIEA", "BUNDESLIGA", "LIGUE1", "MLS", "UCL"}
