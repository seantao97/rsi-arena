"""Prove both halves of the pipeline work before collecting for hours.

Forecasting needs two feeds, and losing either one produces the same silence: an
empty forecasts file that looks exactly like a night with no football.

* **Kalshi** for the markets and the model behind them — checked by making one
  real forecast on one open market.
* **The fixture feed** for which games are being played — checked by asking for
  today's fixtures and for anything live. A run that can price markets but
  cannot see a kickoff will adopt nothing, all night, without an error.

The second check does not fail the run when nothing is live, because that is a
fact about the schedule. It fails when the feed itself is unreachable.
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, ".")

from rsi_arena import AgentConfig                       # noqa: E402
from topics.kalshi.eval.load import load_agent          # noqa: E402
from topics.kalshi.eval.trading import decide, quote_from  # noqa: E402
from topics.kalshi import gamestate as gs               # noqa: E402
from topics.kalshi.tools import TOOLS                   # noqa: E402


async def check_fixture_feed(leagues: list[str]) -> bool:
    """Can we see fixtures at all, and does anything link to a market?

    Two different failures, and only one of them is ours.

    The feed being unreachable is fatal — it is what silently emptied a
    165-minute run, and no amount of live football helps if we cannot see it.

    A league with live fixtures and no markets is **not** a failure. Kalshi does
    not list every competition or every fixture, markets close before the feed
    marks a game final, and the feed counts a just-finished match as live. On a
    busy evening most leagues look like this and the run is still worth making.
    An earlier version failed the whole run on any single league with a live
    fixture and no market, and killed a scheduled run that already had five
    linkable games across four other competitions.

    Broken linking looks different: fixtures live across the board and not one
    market attached to any of them, anywhere.
    """
    reachable = False
    live_total = linked_total = 0
    for league in leagues:
        try:
            fixtures = gs.todays_games(league)
        except Exception as exc:
            print(f"  fixtures  {league}: {type(exc).__name__}: {exc}")
            continue
        reachable = True
        live = gs.live_games(league)
        print(f"  fixtures  {league}: {len(fixtures)} today, {len(live)} live")
        if not live:
            continue
        live_total += len(live)
        found = await TOOLS["live_markets"].aget_tool_output(league=league, limit=3)
        linked = len(found.raw_output.get("markets", []))
        linked_total += linked
        print(f"  linked    {league}: "
              f"{found.raw_output.get('live_games', 0)} live -> "
              f"{linked} with markets")

    if not reachable:
        print("PREFLIGHT FAIL: the fixture feed is unreachable from here")
        return False
    if live_total and not linked_total:
        print(f"PREFLIGHT FAIL: {live_total} live fixtures across "
              f"{len(leagues)} leagues and not one links to a market — "
              "linking is broken")
        return False
    print(f"  summary   {linked_total} linkable game(s) from {live_total} live")
    # Hand the count to the steps that follow. Collecting nothing is a failure
    # only when there was something to collect.
    out = os.environ.get("GITHUB_ENV")
    if out:
        with open(out, "a") as fh:
            fh.write(f"LINKABLE={linked_total}\n")
    return True


async def main() -> int:
    # The caller passes the same league list the run will sweep. Any one of
    # them with an open two-sided market will do — the point is to prove the
    # model answers, not to survey the schedule.
    leagues = [x.strip() for x in
               (sys.argv[1] if len(sys.argv) > 1 else "EPL").split(",") if x.strip()]
    quoted: list[dict] = []
    for league in leagues:
        found = await TOOLS["list_markets"].aget_tool_output(league=league, limit=10)
        markets = found.raw_output.get("markets", [])
        quoted = [m for m in markets if m.get("yes_bid") and m.get("yes_ask")]
        if quoted:
            print(f"  league    {league}")
            break
    if not quoted:
        print(f"PREFLIGHT FAIL: no open two-sided market in {', '.join(leagues)}")
        return 1

    market = quoted[0]
    ticker = market["ticker"]
    bid, ask = market["yes_bid"], market["yes_ask"]
    mid = (bid + ask) / 2

    agent = load_agent("horizon", config=AgentConfig(default_model="anthropic/claude-sonnet-4.5",
                                      max_usd=0.10))
    run = await agent.run(ticker, game="preflight — no live fixture")
    out = run.output if isinstance(run.output, dict) else {}
    delta, width = out.get("delta_cents"), out.get("half_width_cents")

    print(f"  ticker    {ticker}")
    print(f"  cost      ${run.cost_usd:.4f}")
    print(f"  market    {bid:.2f}/{ask:.2f}")
    print(f"  delta     {delta}c   half width {width}c")
    if run.error:
        print(f"  error     {run.error}")

    if not isinstance(delta, (int, float)):
        print("PREFLIGHT FAIL: the model returned no usable prediction")
        return 1

    predicted, low, high = quote_from(
        mid, delta, width if isinstance(width, (int, float)) else 3.0)
    decision = decide(predicted, bid, ask, out.get("confidence") or 0.5,
                      interval=[low, high])
    print(f"  quote     {low:.3f}/{high:.3f} around {predicted:.3f}")
    print(f"  decision  {decision.action} edge={decision.edge:+.4f}")

    if not await check_fixture_feed(leagues):
        return 1

    print("PREFLIGHT OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
