"""Run a Kalshi sports prediction agent.

    export OPENROUTER_API_KEY=...

    python -m topics.kalshi.agents --league MLB              # pick a live market and forecast it
    python -m topics.kalshi.agents TICKER --agent freeform
    python -m topics.kalshi.agents TICKER --agent both --trace
    python -m topics.kalshi.agents --league EPL --dry-run    # tools only, no model calls

``--dry-run`` exercises the primitive set against the live exchange without
spending anything, which is the fastest way to tell whether a failure is in the
data layer or the model.
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


async def main() -> int:
    ap = argparse.ArgumentParser(description="Kalshi sports prediction agent")
    ap.add_argument("ticker", nargs="?", help="market ticker; omit to pick one from --league")
    ap.add_argument("--league", default="MLB", help="league code used to pick a market")
    ap.add_argument("--agent", default="pipeline", choices=[*AGENTS, "both"])
    ap.add_argument("--max-usd", type=float, default=2.00)
    ap.add_argument("--trace", action="store_true", help="print the span tree")
    ap.add_argument("--dry-run", action="store_true", help="exercise tools, no model calls")
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
