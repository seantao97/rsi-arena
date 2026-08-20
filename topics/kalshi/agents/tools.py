"""The primitive set for the Kalshi sports topic.

Every tool here is a thin adapter over a function that already exists in
``topics/kalshi``. Nothing new is implemented — the point of the exercise is
that the infrastructure is enough, and if a tool needed a helper that did not
exist, that would be a finding about the infrastructure rather than something to
paper over here.

Human-supplied and fixed, per the arena's rule: an agent composes these, it does
not extend them.

**Every tool is async and offloads to a thread.** The Kalshi data layer is
synchronous, and the runtime runs a sync tool inline on the event loop — so a
sync tool silently defeats ``Toolbox.call_many``, which gathers calls expecting
them to overlap. Four Kalshi calls that look concurrent would run one after
another with the loop blocked throughout. ``asyncio.to_thread`` restores the
concurrency the runtime is promising, without the data layer having to be
rewritten around an async HTTP client.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from rsi_arena import Tool, Toolbox, tool

from .. import gamestate as gs
from ..client import KalshiClient
from ..coherence import Coherence
from ..discovery import Discovery
from ..fees import breakeven, edge, kelly, taker_fee
from ..history import HOUR, MINUTE, History
from ..implied import american_to_prob, devig
from ..linking import link_event
from ..quotes import Quotes
from ..timeline import Timeline

_client = KalshiClient()
_discovery = Discovery(_client)
_quotes = Quotes(_client)
_history = History(_client)
_coherence = Coherence(_client)
_timeline = Timeline(_client)


def _utc(hours_back: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours_back)


# --- what can I bet on -----------------------------------------------------

@tool
async def list_markets(league: str, limit: int = 25) -> list[dict]:
    """List open Kalshi markets for a league, newest first.

    league: a league code such as MLB, NFL, NBA, EPL, LIGAMX.
    Returns ticker, title, market type, current bid and ask, volume.
    """
    found = await asyncio.to_thread(_discovery.whats_bettable, league=league,
                                    fixtures_only=True)
    out = []
    for m in found:
        out.append({"ticker": m.ticker, "event": m.event_ticker, "title": m.title,
                    "subtitle": m.subtitle, "type": m.market_type,
                    "yes_bid": m.yes_bid, "yes_ask": m.yes_ask,
                    "volume": m.volume, "close_time": m.close_time})
        if len(out) >= limit:
            break
    return out


@tool
async def event_markets(event_ticker: str) -> list[dict]:
    """Every market on one fixture — both sides, every spread and total line."""
    return [{"ticker": m.ticker, "subtitle": m.subtitle, "type": m.market_type,
             "yes_bid": m.yes_bid, "yes_ask": m.yes_ask, "volume": m.volume}
            for m in await asyncio.to_thread(
                lambda: list(_discovery.markets(event_ticker=event_ticker)))]


@tool
async def market_rules(ticker: str) -> dict:
    """The settlement terms for a market — what actually decides it, and when.

    Read this before forming a view. Most avoidable losses come from answering a
    slightly different question than the one that settles.
    """
    return await asyncio.to_thread(_history.rules, ticker)


# --- what is it worth ------------------------------------------------------

@tool
async def market_quote(ticker: str) -> dict:
    """Current bid, ask, last price, volume and open interest for one market."""
    q = await asyncio.to_thread(_quotes.get_market, ticker)
    return {"ticker": q.ticker, "yes_bid": q.yes_bid, "yes_ask": q.yes_ask,
            "mid": q.mid, "spread": q.spread, "last": q.last,
            "volume": q.volume, "open_interest": q.open_interest, "status": q.status}


@tool
async def order_book(ticker: str, depth: int = 5) -> dict:
    """Resting depth on both sides. Tells you what size is actually available."""
    b = await asyncio.to_thread(_quotes.get_orderbook, ticker, depth)
    return {"ticker": b.ticker, "yes": b.yes[:depth], "no": b.no[:depth]}


@tool
async def price_history(ticker: str, hours_back: float = 24.0, hourly: bool = True) -> list[dict]:
    """How the price has moved. Use it to see whether a level is new or settled.

    hourly: hourly candles when True, minute candles when False.
    """
    interval = HOUR if hourly else MINUTE
    candles = await asyncio.to_thread(_history.price_path, ticker, _utc(hours_back),
                                      datetime.now(timezone.utc), interval)
    return [{"ts": c.ts.isoformat(), "mid": c.mid, "last": c.last,
             "volume": c.volume, "open_interest": c.open_interest} for c in candles]


@tool
async def recent_trades(ticker: str, limit: int = 30) -> list[dict]:
    """The print tape — what actually traded, at what price, and who took liquidity."""
    return [{"ts": t.get("created_time"), "yes_price": t.get("yes_price_dollars"),
             "count": t.get("count_fp"), "taker_side": t.get("taker_side")}
            for t in await asyncio.to_thread(_history.trades, ticker, None, None, limit)]


# --- what is happening in the game -----------------------------------------

@tool
async def todays_fixtures(league: str) -> list[dict]:
    """Today's fixtures for a league, with the game id the other game tools need."""
    return await asyncio.to_thread(gs.todays_games, league)


@tool
async def game_state(league: str, game_id: str) -> dict:
    """Live score, period, clock and situation for a fixture."""
    st = await asyncio.to_thread(gs.game_state, league, game_id, False)
    return {"status": st.status, "home": st.home, "away": st.away,
            "home_score": st.home_score, "away_score": st.away_score,
            "period": st.period, "clock": st.clock, "detail": st.detail}


@tool
async def recent_plays(league: str, game_id: str, limit: int = 12) -> list[dict]:
    """The last plays in a game, most recent last. Scoring plays are flagged."""
    st = await asyncio.to_thread(gs.game_state, league, game_id, True)
    return [{"ts": p.ts, "period": p.period, "clock": p.clock,
             "description": p.description, "scoring": p.scoring,
             "score": f"{p.away_score}-{p.home_score}"} for p in st.plays[-limit:]]


@tool
async def game_context(league: str, game_id: str) -> dict:
    """Everything around a fixture: venue, injuries, form, head-to-head, leaders.

    One request. Sections a sport does not have come back empty.
    """
    d = await asyncio.to_thread(gs.game_detail, league, game_id)
    return {"venue": d.venue.get("fullName"), "attendance": d.attendance,
            "weather": d.weather, "officials": d.officials,
            "injuries": d.injuries[:2], "leaders": d.leaders[:2],
            "recent_form": d.last_five, "head_to_head": d.season_series,
            "has_boxscore": bool(d.boxscore.get("teams"))}


@tool
async def sportsbook_line(league: str, game_id: str) -> dict:
    """The sportsbook's price on this fixture, with the bookmaker's margin removed.

    The only outside reference available. A Kalshi price far from a de-vigged
    book line is either an edge or a misreading of the contract — check the
    rules before assuming the former.

    Coverage is uneven and that is not a failure: ESPN publishes odds for
    soccer competitions but not, as of August 2026, for MLB, NFL or WNBA. When
    none is published, price off the market and the game state instead of
    treating it as a broken call.
    """
    d = await asyncio.to_thread(gs.game_detail, league, game_id)
    fair = d.fair_probabilities()
    if fair:
        return fair
    return {"available": False,
            "reason": f"ESPN publishes no sportsbook odds for {league}. "
                      "Soccer competitions carry them; the US leagues do not."}


# --- how this market responds to events ------------------------------------

@tool
async def market_reaction(league: str, game_id: str, ticker: str) -> dict:
    """How much this market has moved on each scoring play so far.

    The market's sensitivity to events, measured on this exact contract rather
    than assumed. If a home run moved it 12c earlier, the next one probably
    moves it about as much — which is what tells you whether the current price
    already reflects what just happened.

    Only meaningful once a game is under way; a scheduled fixture has no plays.
    """
    def compute() -> dict:
        state = gs.game_state(league, game_id, with_plays=True)
        if state.status == "scheduled":
            return {"available": False,
                    "reason": "game has not started, so there are no plays to react to"}
        entries = _timeline.build(league, game_id, [ticker], state=state)
        moves = _timeline.reactions(entries, ticker)
        return {
            "available": True,
            "status": state.status,
            "score": f"{state.away} {state.away_score} - {state.home_score} {state.home}",
            "reactions": [{"play": r.play.description[:120],
                           "price_before": r.before, "price_after": r.after,
                           "move": round(r.move, 4) if r.move is not None else None,
                           "max_swing": round(r.swing, 4) if r.swing is not None else None}
                          for r in moves],
            "coverage": _timeline.coverage(state),
        }
    return await asyncio.to_thread(compute)


@tool
async def unexplained_moves(league: str, game_id: str, ticker: str,
                            threshold: float = 0.05) -> dict:
    """Price moves with no play behind them.

    Either the market knows something the feed has not reported, or the feed
    lags the market. Both matter: the first is information, the second means
    game state is stale and should be weighted down.
    """
    def compute() -> dict:
        state = gs.game_state(league, game_id, with_plays=True)
        if state.status == "scheduled":
            return {"available": False, "reason": "game has not started"}
        entries = _timeline.build(league, game_id, [ticker], state=state)
        found = _timeline.leading_moves(entries, ticker, threshold=threshold)
        return {"available": True, "count": len(found),
                "moves": [{"ts": m["ts"].isoformat(), "from": m["from"],
                           "to": m["to"], "move": round(m["move"], 4)}
                          for m in found[:8]]}
    return await asyncio.to_thread(compute)


# --- structure and pricing -------------------------------------------------

@tool
async def coherence_check(event_ticker: str) -> list[dict]:
    """Look for inconsistent pricing across the markets on one fixture.

    A violation is money available without a forecast. Returns only findings
    that survive fees at the quoted size.
    """
    return [{"kind": v.kind, "tickers": v.tickers, "detail": v.detail,
             "net_per_contract": round(v.net, 4), "size": v.size,
             "value_usd": round(v.value, 2)}
            for v in await asyncio.to_thread(_coherence.check_event, event_ticker)]


@tool
def price_the_edge(probability: float, yes_price: float, bankroll: float = 50000.0) -> dict:
    """Turn a probability and a price into an edge after fees, and a stake.

    Kalshi's fee peaks near 50c, so a 2c edge at midprice is nothing while the
    same edge at 90c is real. Always run this before claiming an edge.
    """
    ev = edge(probability, yes_price)
    frac = kelly(probability, yes_price)
    return {"breakeven_probability": round(breakeven(yes_price), 4),
            "fee_per_contract": round(taker_fee(yes_price), 4),
            "edge_per_contract": round(ev, 4),
            "kelly_fraction": round(frac, 4),
            "suggested_stake_usd": round(frac * bankroll, 2),
            "worth_taking": ev > 0}


@tool
def devig_odds(american_odds: list[float]) -> dict:
    """Remove a bookmaker's margin from a set of American odds.

    Pass every outcome — for soccer that means home, away AND draw, or the
    result is meaningless.
    """
    raw = [american_to_prob(o) for o in american_odds]
    return {"raw": [round(r, 4) for r in raw],
            "overround": round(sum(raw) - 1, 4),
            "fair": [round(f, 4) for f in devig(raw)]}


# --- joining a market to a game --------------------------------------------

@tool
async def find_game_for_market(event_ticker: str, league: str) -> dict:
    """Work out which real fixture a Kalshi event refers to.

    Returns the game id the game-state tools need.
    """
    link = await asyncio.to_thread(
        link_event, _client, event_ticker, league,
        lambda lg, day: gs.todays_games(lg, day))
    if link:
        return {"game_id": link.game_id, "home": link.home, "away": link.away,
                "confidence": link.confidence}
    return {"error": f"no fixture matched {event_ticker}"}


def kalshi_tools() -> Toolbox:
    """The whole primitive set. Fixed — agents compose it, they do not extend it."""
    return Toolbox([
        list_markets, event_markets, market_rules,
        market_quote, order_book, price_history, recent_trades,
        todays_fixtures, game_state, recent_plays, game_context, sportsbook_line,
        market_reaction, unexplained_moves,
        coherence_check, price_the_edge, devig_odds, find_game_for_market,
    ])
