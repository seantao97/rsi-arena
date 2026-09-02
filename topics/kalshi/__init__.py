"""Kalshi sports data access.

The data layer lives in ``tools/`` beside the primitives that wrap it, under a
leading underscore — ``_history.py`` is machinery, ``candlestick.py`` is a tool.
Import through this package rather than reaching for those directly:

    from topics.kalshi import Discovery, Quotes, History, edge

    Discovery().whats_bettable(league="MLB")   # what can I bet on
    Quotes().get_market(ticker)                # what is it worth now
    History().full_history(ticker)             # what was it worth, whole life
    Coherence().check_event(event_ticker)      # what is priced inconsistently
    edge(probability, price)                   # is there anything left after fees
    Timeline().build(league, game_id, tickers) # game events and price on one clock
    fixture_key(ticker)                        # which game is this market about

Reads need no credentials. Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH
only for the portfolio endpoints; ``credentials.status()`` reports
what is configured without printing key material.
"""

from .tools._client import KalshiClient
from .tools._credentials import Credentials, load as load_credentials, status as credential_status
from .tools._discovery import Discovery, MarketRef
from .tools._linking import (Fixture, FieldEvent, Link, parse_event_ticker,
                      parse_field_event, field_entrants, harvest_team_codes,
                      fixture_key, names_from_markets)
from .tools._coherence import Coherence, Violation
from .tools._implied import (american_to_prob, devig, fair_probabilities,
                      kalshi_vs_book, overround)
from .tools._fees import (Trade, breakeven, clv, edge, fee, kelly, maker_fee, taker_fee)
from .tools._history import Candle, History, DAY, HOUR, MINUTE
from .tools._quotes import OrderBook, Quote, Quotes
from .tools._timeline import Entry, Reaction, Timeline
from .tools import TOOLS, kalshi_tools
# Module-style access, for callers that want gs.game_state(...) rather
# than a name-by-name import.
from .tools import _gamestate as gamestate, _linking as linking
from .tools._taxonomy import (MarketType, SeriesClass, Sport, classify_series,
                       resolve_league, match_competition)

__all__ = [
    "KalshiClient", "Credentials", "load_credentials", "credential_status",
    "Discovery", "MarketRef", "Quotes", "Quote", "OrderBook",
    "History", "Candle", "MINUTE", "HOUR", "DAY",
    "Coherence", "Violation", "Timeline", "Entry", "Reaction",
    "american_to_prob", "devig", "fair_probabilities", "kalshi_vs_book", "overround",
    "taker_fee", "maker_fee", "fee", "breakeven", "edge", "kelly", "clv", "Trade",
    "Fixture", "FieldEvent", "Link", "parse_event_ticker", "parse_field_event",
    "field_entrants", "harvest_team_codes", "fixture_key", "names_from_markets",
    "Sport", "MarketType", "SeriesClass", "classify_series",
    "resolve_league", "match_competition",
    "TOOLS", "kalshi_tools", "gamestate", "linking",
]
