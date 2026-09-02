"""Run a harness over a fixed set of past windows and report how it did.

The point is comparison. A live evening gives one harness one set of fixtures
and never repeats them, so two harnesses tested on different nights cannot be
told apart from two harnesses of different quality. Here the questions are
fixed: the same instants of the same finished matches, asked of whatever
harness you hand it.

A harness is a **serialised agent** — Sean's ``Agent.to_dict()``. That is what
makes this useful for the arena rather than only for us: the thing under test
is JSON, so a model can author the next one without writing code, and the
benchmark scores it without knowing where it came from.

    python -m topics.kalshi.eval.run --league EPL --game 401879319
    python -m topics.kalshi.eval.run --spec v2.json --game 401879319 --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rsi_arena import Agent, AgentConfig

from .. import MINUTE, History
from .replay import HORIZON_MINUTES, Timeline, replay_tools, timeline
from .scorer import WindowScore, score_window


@dataclass
class BenchResult:
    """What a harness did over one fixed set of windows."""

    harness: str
    windows: list[WindowScore] = field(default_factory=list)
    unscoreable: int = 0
    cost_usd: float = 0.0

    @property
    def n(self) -> int:
        return len(self.windows)

    @property
    def skill(self) -> float:
        """Pooled, not averaged.

        A mean of per-window skills lets one quiet window — where the benchmark
        error is a tenth of a cent and the ratio explodes — outweigh fifty real
        ones. Summing the errors first weights each window by how much was
        actually at stake in it.
        """
        error = sum(w.error for w in self.windows)
        naive = sum(w.naive_error for w in self.windows)
        return 1 - error / naive if naive else 0.0

    @property
    def mae(self) -> float:
        return statistics.fmean(w.error for w in self.windows) if self.n else 0.0

    @property
    def naive_mae(self) -> float:
        return (statistics.fmean(w.naive_error for w in self.windows)
                if self.n else 0.0)

    @property
    def echoed(self) -> int:
        return sum(1 for w in self.windows if w.echoed)

    @property
    def coverage(self) -> float:
        return (sum(1 for w in self.windows if w.covered) / self.n
                if self.n else 0.0)

    @property
    def moved(self) -> list[WindowScore]:
        """Windows where the price actually went somewhere. A harness is only
        distinguishable from silence on these."""
        return [w for w in self.windows if w.naive_error >= 0.01]

    @property
    def skill_on_moves(self) -> float:
        error = sum(w.error for w in self.moved)
        naive = sum(w.naive_error for w in self.moved)
        return 1 - error / naive if naive else 0.0

    def summary(self) -> str:
        if not self.n:
            return f"{self.harness}: nothing scoreable ({self.unscoreable} skipped)"
        return "\n".join([
            f"{self.harness}",
            f"  windows           {self.n}"
            + (f"  ({self.unscoreable} unscoreable)" if self.unscoreable else ""),
            f"  price error       {self.mae:.4f}   (no-change {self.naive_mae:.4f})",
            f"  skill             {self.skill:+.1%}",
            f"  skill on moves    {self.skill_on_moves:+.1%}  "
            f"({len(self.moved)} of {self.n} windows moved a cent or more)",
            f"  echoed the market {self.echoed}/{self.n}",
            f"  quote coverage    {self.coverage:.1%}",
            f"  cost              ${self.cost_usd:.4f}"
            + (f"  (${self.cost_usd / self.n:.4f} a window)" if self.n else ""),
        ])


async def run_harness(spec: dict, line: Timeline, tickers: list[str],
                      every_minutes: int = 5,
                      minutes: int = HORIZON_MINUTES,
                      history: History | None = None,
                      concurrency: int = 4) -> BenchResult:
    """Ask one harness the same questions, at every window, on every contract."""
    hist = history or History()
    result = BenchResult(harness=spec.get("name", "unnamed"))
    gate = asyncio.Semaphore(concurrency)

    async def one(ticker: str, at: datetime) -> None:
        candle = await asyncio.to_thread(hist.quote_at, ticker, at, MINUTE)
        if candle is None or not candle.two_sided or candle.mid is None:
            result.unscoreable += 1
            return
        # Rebound against a toolbox that reads history, not the live book. The
        # agent is unchanged and cannot tell.
        agent = Agent.from_dict(spec, replay_tools(at, hist))
        async with gate:
            run = await agent.run(ticker,
                                  game=json.dumps(line.state_at(at))[:1200])
        result.cost_usd += run.cost_usd
        scored = score_window(run.output if isinstance(run.output, dict) else {},
                              ticker, at, candle.mid, minutes, hist)
        if scored is None:
            result.unscoreable += 1
        else:
            result.windows.append(scored)

    await asyncio.gather(*(one(t, at) for t in tickers
                           for at in line.windows(every_minutes=every_minutes)))
    return result


def default_spec() -> dict:
    """The harness this benchmark exists to be beaten."""
    from ..run.load import load_agent
    return load_agent("horizon", config=AgentConfig(default_model="anthropic/claude-sonnet-4.5",
                                     max_usd=0.10)).to_dict()


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", default="EPL")
    ap.add_argument("--game", required=True, help="the fixture feed's event id")
    ap.add_argument("--tickers", default="",
                    help="comma separated; defaults to the match-winner ladder")
    ap.add_argument("--spec", default="", help="a harness as JSON; omit for the base one")
    ap.add_argument("--every", type=int, default=5, help="minutes between windows")
    ap.add_argument("--horizon", type=int, default=HORIZON_MINUTES)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    line = timeline(args.league, args.game)
    if line is None:
        print(json.dumps({"error": f"no timeline for {args.league} {args.game}"}))
        return 1

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        print(json.dumps({"error": "pass --tickers; discovery cannot find a "
                                   "finished fixture's markets"}))
        return 1

    spec = (json.loads(Path(args.spec).expanduser().read_text())
            if args.spec else default_spec())
    result = await run_harness(spec, line, tickers, args.every, args.horizon)

    if args.json:
        print(json.dumps({
            "harness": result.harness, "windows": result.n,
            "skill": round(result.skill, 4),
            "skill_on_moves": round(result.skill_on_moves, 4),
            "mae": round(result.mae, 4), "naive_mae": round(result.naive_mae, 4),
            "echoed": result.echoed, "coverage": round(result.coverage, 4),
            "cost_usd": round(result.cost_usd, 4),
            "detail": [w.to_dict() for w in result.windows],
        }, indent=2))
    else:
        print(f"{line.away} at {line.home}, "
              f"{line.final_score()[0]}-{line.final_score()[1]}\n")
        print(result.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
