"""What is actually tradeable today, as opposed to what is being played.

Two questions get confused, and both of them bite.

``todays_games`` does not always mean today. When a league has nothing on, the
feed hands back the next round instead — so a Tuesday with one Spanish fixture
reads as seven matches across five leagues, four of them three days away. A run
planned from that list waits all afternoon for kick-offs that are not coming.

And a fixture being played is not a fixture being traded. The exchange lists
what it chooses to list, and discovery can only adopt a match some market links
to.

This asks both questions at once and says plainly which answer is which, so an
empty afternoon can be told apart from a broken one before three hours are spent
finding out.

    python -m topics.kalshi.agents.slate --league EPL,LALIGA,SERIEA,MLS
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..tools import _gamestate as gs
from .. import fixture_key
from ..tools import TOOLS


@dataclass
class LeagueSlate:
    league: str
    fixtures: int = 0
    live: int = 0
    tradeable: int = 0                 # live fixtures a market links to
    kickoffs: list[str] = field(default_factory=list)
    error: str = ""


async def survey(leagues: list[str]) -> list[LeagueSlate]:
    """Ask both sides and report where they disagree."""
    async def one(league: str) -> LeagueSlate:
        slate = LeagueSlate(league=league)
        try:
            games = await asyncio.to_thread(gs.todays_games, league)
            slate.fixtures = len(games)
            slate.kickoffs = sorted({g.get("start", "")[11:16]
                                     for g in games if g.get("start")})
            slate.live = len(await asyncio.to_thread(gs.live_games, league))
        except Exception as exc:
            slate.error = f"{type(exc).__name__}: {exc}"
            return slate
        try:
            found = await TOOLS["live_markets"].aget_tool_output(league=league, limit=4)
            slate.tradeable = len(found.raw_output.get("markets", []))
        except Exception as exc:
            slate.error = f"{type(exc).__name__}: {exc}"
        return slate

    return list(await asyncio.gather(*(one(lg) for lg in leagues)))


def verdict(slates: list[LeagueSlate], local_offset_hours: int = -4) -> str:
    fixtures = sum(s.fixtures for s in slates)
    live = sum(s.live for s in slates)
    tradeable = sum(s.tradeable for s in slates)

    lines = [f"{'league':<12}{'fixtures':>9}{'live':>6}{'tradeable':>11}   kick-offs"]
    for s in sorted(slates, key=lambda x: -x.tradeable):
        if not (s.fixtures or s.live or s.error):
            continue
        times = ", ".join(s.kickoffs[:5]) if s.kickoffs else "—"
        note = f"   {s.error}" if s.error else ""
        lines.append(f"{s.league:<12}{s.fixtures:>9}{s.live:>6}"
                     f"{s.tradeable:>11}   {times}{note}")

    lines.append("")
    lines.append(f"{fixtures} fixtures on the feed, {live} of them under way, "
                 f"{tradeable} with a market to trade.")

    if tradeable:
        lines.append("Worth collecting now.")
    elif live:
        lines.append("Matches are being played and none of them is listed. "
                     "Collecting now would watch nothing — which is a fact "
                     "about the exchange, not a fault in the agent.")
    elif fixtures:
        lines.append("Nothing has kicked off yet. Check the kick-off times "
                     "above before starting: when a league has nothing on "
                     "today, the feed answers with the next round instead, "
                     "and those may be days away.")
    else:
        lines.append("No fixtures at all. Nothing to do today.")
    return "\n".join(lines)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league",
                    default="EPL,LALIGA,SERIEA,BUNDESLIGA,LIGUE1,MLS,USL,"
                            "LIGAMX,ARGENTINA,BRASIL")
    args = ap.parse_args()
    leagues = [x.strip().upper() for x in args.league.split(",") if x.strip()]
    print(verdict(await survey(leagues)))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
