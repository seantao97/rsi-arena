"""Kalshi sports data access.

Three questions, three modules:

    from topics.kalshi import Discovery, Quotes, gamestate, linking

    Discovery().whats_bettable(league="MLB")   # what can I bet on
    Quotes().get_market(ticker)                # what is it worth now
    History().full_history(ticker)             # what was it worth, whole life
    Coherence().check_event(event_ticker)      # what is priced inconsistently
    edge(probability, price)                   # is there anything left after fees
    gamestate.game_state("MLB", game_id)       # what is happening in the game
    Timeline().build(league, game_id, tickers) # game events and price on one clock
    linking.link_series(...)                   # which game is this market about

Reads need no credentials. Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH
only for the WebSocket or portfolio endpoints; ``credentials.status()`` reports
what is configured without printing key material.
"""

from .client import KalshiClient
from .credentials import Credentials, load as load_credentials, status as credential_status
from .discovery import Discovery, MarketRef
from .linking import (Fixture, FieldEvent, Link, parse_event_ticker,
                      parse_field_event, field_entrants, harvest_team_codes)
from .coherence import Coherence, Violation
from .implied import (american_to_prob, devig, fair_probabilities,
                      kalshi_vs_book, overround)
from .fees import (Trade, breakeven, clv, edge, fee, kelly, maker_fee, taker_fee)
from .history import Candle, History, DAY, HOUR, MINUTE
from .quotes import OrderBook, Quote, Quotes
from .timeline import Entry, Reaction, Timeline
from .stream import KalshiStream, LiveBook
from .taxonomy import MarketType, SeriesClass, Sport, classify_series

__all__ = [
    "KalshiClient", "Credentials", "load_credentials", "credential_status",
    "Discovery", "MarketRef", "Quotes", "Quote", "OrderBook",
    "History", "Candle", "MINUTE", "HOUR", "DAY",
    "Coherence", "Violation", "Timeline", "Entry", "Reaction",
    "american_to_prob", "devig", "fair_probabilities", "kalshi_vs_book", "overround",
    "taker_fee", "maker_fee", "fee", "breakeven", "edge", "kelly", "clv", "Trade",
    "Fixture", "FieldEvent", "Link", "parse_event_ticker", "parse_field_event",
    "field_entrants", "harvest_team_codes", "KalshiStream", "LiveBook",
    "Sport", "MarketType", "SeriesClass", "classify_series",
]
