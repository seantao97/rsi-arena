"""The Kalshi primitive set, one class per tool.

Each tool is a :class:`~rsi_arena.agent.tool.Tool` subclass in its own module:
a version, a description written for a model that has to choose between twenty
of them, schemas for what goes in and what comes out, and one method that
answers. Nothing in here decides anything — a tool reports, and the harness
decides.

Two calls, depending on which end you are:

``kalshi_tools()`` gives the whole set as a :class:`Toolbox`, ready for an
agent. ``TOOLS`` maps name to instance, for anything that wants to reach one
directly or read its schema.

Adding a primitive is a file and one line in :data:`REGISTRY`.
"""

from __future__ import annotations

from rsi_arena import Tool, Toolbox

from .active_leagues import ActiveLeaguesTool
from .candlestick import CandlestickTool
from .candlestick_prob import CandlestickProbTool
from .coherence import CoherenceCheckTool
from .devig import DevigOddsTool
from .event_markets import EventMarketsTool
from .find_game import FindGameForMarketTool
from .game_context import GameContextTool
from .game_state import GameStateTool
from .kalshi_vs_book import KalshiVsBookTool
from .list_markets import ListMarketsTool
from .live_markets import LiveMarketsTool
from .market_quote import MarketQuoteTool
from .market_reaction import MarketReactionTool
from .market_at_time import MarketAtTimeTool
from .market_rules import MarketRulesTool
from .order_book import OrderBookTool
from .previous_trades import PreviousTradesTool
from .price_the_edge import PriceTheEdgeTool
from .recent_plays import RecentPlaysTool
from .settlement import SettlementTool
from .sportsbook_line import SportsbookLineTool
from .todays_fixtures import TodaysFixturesTool
from .trading_fees import TradingFeesTool
from .unexplained_moves import UnexplainedMovesTool
from .volume_profile import VolumeProfileTool

REGISTRY: list[type[Tool]] = [
    # what can I bet on
    ActiveLeaguesTool, ListMarketsTool, EventMarketsTool, LiveMarketsTool,
    MarketRulesTool,
    # what is it worth
    MarketQuoteTool, OrderBookTool, CandlestickTool, CandlestickProbTool,
    PreviousTradesTool, VolumeProfileTool, MarketAtTimeTool, SettlementTool,
    # what is happening in the game
    TodaysFixturesTool, GameStateTool, RecentPlaysTool, GameContextTool,
    SportsbookLineTool, FindGameForMarketTool,
    # how the market responds
    MarketReactionTool, UnexplainedMovesTool,
    # what a trade costs, and whether it is worth making
    TradingFeesTool, PriceTheEdgeTool, CoherenceCheckTool, DevigOddsTool,
    KalshiVsBookTool,
]

TOOLS: dict[str, Tool] = {cls.name: cls() for cls in REGISTRY}


def kalshi_tools(only: list[str] | None = None) -> Toolbox:
    """The primitive set as a toolbox.

    ``only`` narrows it by name. A harness that cannot reach a tool cannot
    surprise you by reaching for it, which is worth more than the tokens saved
    once a model is rewriting the harness.
    """
    chosen = TOOLS.values() if only is None else [TOOLS[n] for n in only]
    return Toolbox(list(chosen))


def describe() -> str:
    """Every tool, name and description — what a model is choosing between."""
    return "\n\n".join(f"{t.name} (v{t.version})\n{t.description}"
                       for t in TOOLS.values())


__all__ = ["REGISTRY", "TOOLS", "kalshi_tools", "describe",
           *(cls.__name__ for cls in REGISTRY)]
