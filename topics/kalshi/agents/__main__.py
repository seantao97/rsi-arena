"""Run a Kalshi sports prediction agent.

    export OPENROUTER_API_KEY=...

    python -m topics.kalshi.agents --league MLB              # pick a live market and forecast it
    python -m topics.kalshi.agents TICKER --agent freeform
    python -m topics.kalshi.agents TICKER --agent both --trace
    python -m topics.kalshi.agents --league EPL --dry-run    # tools only, no model calls
    python -m topics.kalshi.agents TICKER --watch            # re-forecast a live game as it moves

``--dry-run`` exercises the primitive set against the live exchange without
spending anything, which is the fastest way to tell whether a failure is in the
data layer or the model.

``--watch`` forecasts continuously while a game runs. It re-forecasts only when
something material changed — the score, the period, or the price beyond a
threshold — because a fixed interval would keep paying to re-answer a state that
has not moved. Polling the state is free; the model call is not.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from topics.kalshi.agents.agents import AGENTS, default_config  # noqa: E402
from topics.kalshi.agents.tools import kalshi_tools  # noqa: E402


def pick_market(league: str) -> str | None:
    """The most-traded open market on a real fixture.

    ``SeriesClass.is_fixture`` is not reliable here: it trusts Kalshi's
    ``frequency`` field, and ``custom`` is a catch-all that covers season
    futures as well as single games — the World Series winner market is
    ``custom``. ``parse_event_ticker`` is the honest test, because only a
    fixture encodes a date and two team codes.
    """
    from topics.kalshi.discovery import Discovery
    from topics.kalshi.linking import parse_event_ticker

    candidates = []
    for m in Discovery().whats_bettable(league=league, fixtures_only=True):
        if not (m.yes_bid and m.yes_ask):
            continue
        if parse_event_ticker(m.event_ticker) is None:
            continue                       # season-long, not a game
        candidates.append(m)
    if not candidates:
        return None
    return max(candidates, key=lambda m: m.volume).ticker


async def dry_run(ticker: str, league: str) -> None:
    """Call the primitives directly. No model, no spend."""
    box = kalshi_tools()
    print(f"primitive set: {len(box)} tools\n")
    for name, args in [
        ("market_rules", {"ticker": ticker}),
        ("market_quote", {"ticker": ticker}),
        ("price_history", {"ticker": ticker, "hours_back": 6}),
        ("todays_fixtures", {"league": league}),
        ("price_the_edge", {"probability": 0.62, "yes_price": 0.55}),
    ]:
        result = await box.call(name, args)
        body = json.dumps(result.output, default=str)
        status = "ok " if result.ok else "ERR"
        print(f"  {status} {name:18} {body[:150]}")
        if not result.ok:
            print(f"      {result.error}")


def state_fingerprint(league: str, game_id: str, ticker: str,
                      price_step: float) -> tuple:
    """What must change before another forecast is worth paying for.

    Score, period and status come from the game; the price is bucketed so that
    ordinary one-cent noise does not trigger a re-run while a real move does.
    """
    from topics.kalshi import gamestate as gs
    from topics.kalshi.quotes import Quotes

    st = gs.game_state(league, game_id, with_plays=False)
    q = Quotes().get_market(ticker)
    bucket = round((q.mid or 0) / price_step) if q.mid else None
    return (st.status, st.period, st.home_score, st.away_score, bucket)


def emit(*parts: object) -> None:
    """Print and flush.

    ``--watch`` runs for hours and is usually redirected to a file, where
    Python buffers stdout by default — so a loop that is working looks
    identical to one that has hung. Every line here flushes.
    """
    print(*parts, flush=True)


async def watch(ticker: str, league: str, agent_name: str, config,
                tools, poll_s: float, price_step: float, budget: float) -> int:
    """Re-forecast a live contract whenever the game or the price moves."""
    from topics.kalshi.agents.tools import find_game_for_market

    event = ticker.rsplit("-", 1)[0]
    located = await find_game_for_market(event_ticker=event, league=league)
    if not located.ok or "game_id" not in (located.output or {}):
        emit(f"could not link {event} to a fixture: {located.output}", file=sys.stderr)
        return 1
    game_id = located.output["game_id"]
    emit(f"watching {located.output['away']} @ {located.output['home']} "
          f"(game {game_id}), polling every {poll_s:.0f}s\n")

    agent = AGENTS[agent_name](config, tools)
    last, spent = None, 0.0
    while True:
        try:
            now = await asyncio.to_thread(state_fingerprint, league, game_id,
                                          ticker, price_step)
        except Exception as exc:
            emit(f"  poll failed: {exc}")
            await asyncio.sleep(poll_s)
            continue

        if now[0] == "final" and last is not None:
            emit("game final — stopping")
            return 0
        if now == last:
            await asyncio.sleep(poll_s)
            continue

        if spent >= budget:
            emit(f"budget of ${budget:.2f} reached — stopping")
            return 0

        result = await agent.run(ticker)
        spent += result.cost_usd
        p = result.output if isinstance(result.output, dict) else {}
        emit(f"[{now[1]} {now[3]}-{now[2]}] {p.get('position','?'):4} "
              f"p={p.get('probability')} px={p.get('market_price')} "
              f"edge={p.get('edge_after_fees')}  (${result.cost_usd:.3f}, "
              f"${spent:.2f} total)")
        if p.get("reasoning"):
            emit(f"    {p['reasoning'][:150]}")
        last = now
        if now[0] == "final":
            emit("game final — stopping")
            return 0
        await asyncio.sleep(poll_s)


async def main() -> int:
    ap = argparse.ArgumentParser(description="Kalshi sports prediction agent")
    ap.add_argument("ticker", nargs="?", help="market ticker; omit to pick one from --league")
    ap.add_argument("--league", default="MLB", help="league code used to pick a market")
    ap.add_argument("--agent", default="pipeline", choices=[*AGENTS, "both"])
    ap.add_argument("--max-usd", type=float, default=2.00)
    ap.add_argument("--trace", action="store_true", help="print the span tree")
    ap.add_argument("--dry-run", action="store_true", help="exercise tools, no model calls")
    ap.add_argument("--watch", action="store_true",
                    help="re-forecast while the game runs, on material change only")
    ap.add_argument("--poll", type=float, default=30.0, help="seconds between state polls")
    ap.add_argument("--price-step", type=float, default=0.03,
                    help="price move that counts as material")
    ap.add_argument("--budget", type=float, default=5.00, help="total spend ceiling for --watch")
    args = ap.parse_args()

    ticker = args.ticker or pick_market(args.league)
    if not ticker:
        print(f"no open market found for {args.league}", file=sys.stderr)
        return 1
    print(f"contract: {ticker}\n")

    if args.dry_run:
        await dry_run(ticker, args.league)
        return 0

    if not os.getenv("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY is not set — the agent cannot call a model.\n"
              "Run with --dry-run to exercise the primitive set instead.", file=sys.stderr)
        return 2

    config = default_config(args.max_usd)
    tools = kalshi_tools()

    if args.watch:
        name = args.agent if args.agent in AGENTS else "inplay"
        return await watch(ticker, args.league, name, config, tools,
                           args.poll, args.price_step, args.budget)

    names = list(AGENTS) if args.agent == "both" else [args.agent]

    for name in names:
        agent = AGENTS[name](config, tools)
        result = await agent.run(ticker)
        print(f"── {agent.name} ─ ${result.cost_usd:.4f}")
        if result.error:
            print(f"   error: {result.error}")
        else:
            print(json.dumps(result.output, indent=2, default=str))
        if args.trace:
            print(result.trace.render())
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
