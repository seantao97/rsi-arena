"""Kalshi sports data access.

Three questions, three modules:

    from topics.kalshi import Discovery, Quotes, gamestate, linking

    Discovery().whats_bettable(league="MLB")   # what can I bet on
    Quotes().get_market(ticker)                # what is it worth now
    gamestate.game_state("MLB", game_id)       # what is happening in the game
    linking.link_series(...)                   # which game is this market about

Reads need no credentials. Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH
only for the WebSocket or portfolio endpoints; ``credentials.status()`` reports
what is configured without printing key material.
"""

from .client import KalshiClient
from .credentials import Credentials, load as load_credentials, status as credential_status
from .discovery import Discovery, MarketRef
from .linking import Fixture, Link, parse_event_ticker, harvest_team_codes
from .quotes import OrderBook, Quote, Quotes
from .taxonomy import MarketType, SeriesClass, Sport, classify_series

__all__ = [
    "KalshiClient", "Credentials", "load_credentials", "credential_status",
    "Discovery", "MarketRef", "Quotes", "Quote", "OrderBook",
    "Fixture", "Link", "parse_event_ticker", "harvest_team_codes",
    "Sport", "MarketType", "SeriesClass", "classify_series",
]
