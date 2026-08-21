"""Keep agents forecasting for as long as they are meant to be on.

``--watch`` is a loop; this is a service. The difference is what happens when
something goes wrong: a loop exits and stops forecasting, a service restarts and
keeps going. Everything here exists because the loop lost coverage of a live
contract at least once — a crash, a stop, an unnoticed hang.

What it guarantees while running:

* **A contract is owned until it settles.** Not until the process feels like
  stopping. Settlement is the only clean exit.
* **A failure is isolated.** One contract raising does not stop the others, and
  the failing one retries with backoff rather than dying.
* **Nothing is lost.** Every forecast appends to JSONL as it happens, so a
  restart resumes with its history rather than starting blind.
* **Spend is bounded across restarts**, not per process, because a crash loop
  that resets the budget is how a ceiling silently stops being one.

    python -m topics.kalshi.agents.supervisor --contracts TICKER,TICKER --league MLB
    python -m topics.kalshi.agents.supervisor --league MLB --auto --max-contracts 3
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import signal
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .agents import AGENTS, default_config
from .tools import kalshi_tools


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Position:
    """One contract being watched, and everything known about it."""

    ticker: str
    league: str
    game_id: str | None = None
    forecasts: int = 0
    spent_usd: float = 0.0
    failures: int = 0
    settled: bool = False
    result: str | None = None
    last_fingerprint: tuple | None = None
    last_error: str | None = None


class Supervisor:
    """Runs forecasting agents until their contracts settle."""

    def __init__(self, league: str, agent: str = "inplay",
                 poll_s: float = 45.0, price_step: float = 0.03,
                 budget_usd: float = 10.0, per_run_usd: float = 0.30,
                 state_dir: str = "~/.kalshi-agent") -> None:
        self.league = league
        self.agent_name = agent
        self.poll_s = poll_s
        self.price_step = price_step
        self.budget_usd = budget_usd
        self.per_run_usd = per_run_usd

        self.dir = Path(state_dir).expanduser()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.forecast_path = self.dir / "forecasts.jsonl"

        self.positions: dict[str, Position] = {}
        self.tools = kalshi_tools()
        self.config = default_config(per_run_usd)
        self._stop = asyncio.Event()
        self._load()

    # ---------- durable state ----------

    def _load(self) -> None:
        """Resume from disk. Spend and settlement survive a restart."""
        if not self.state_path.exists():
            return
        try:
            raw = json.loads(self.state_path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        for ticker, data in raw.get("positions", {}).items():
            data.pop("last_fingerprint", None)
            self.positions[ticker] = Position(**data)

    def _save(self) -> None:
        payload = {"updated": _now(),
                   "positions": {t: {k: v for k, v in asdict(p).items()
                                     if k != "last_fingerprint"}
                                 for t, p in self.positions.items()}}
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.state_path)          # atomic: a crash mid-write cannot corrupt it

    def _record(self, entry: dict) -> None:
        with self.forecast_path.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")

    @property
    def spent(self) -> float:
        return sum(p.spent_usd for p in self.positions.values())

    # ---------- lifecycle ----------

    def add(self, ticker: str) -> None:
        self.positions.setdefault(ticker, Position(ticker=ticker, league=self.league))

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Watch every contract until all settle, the budget runs out, or stopped."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, self.stop)

        self._log("start", contracts=len(self.positions), budget=self.budget_usd,
                  already_spent=round(self.spent, 4))
        workers = [asyncio.create_task(self._own(t)) for t in self.positions]
        heartbeat = asyncio.create_task(self._heartbeat())
        try:
            await asyncio.gather(*workers)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            self._save()
            self._log("stop", spent=round(self.spent, 4),
                      settled=sum(1 for p in self.positions.values() if p.settled))

    async def _heartbeat(self) -> None:
        """Prove liveness. A silent service and a hung one look identical."""
        while not self._stop.is_set():
            await asyncio.sleep(120)
            live = [t for t, p in self.positions.items() if not p.settled]
            self._log("heartbeat", watching=len(live), spent=round(self.spent, 4))

    # ---------- one contract, owned to settlement ----------

    async def _own(self, ticker: str) -> None:
        pos = self.positions[ticker]
        backoff = 5.0
        while not self._stop.is_set() and not pos.settled:
            if self.spent >= self.budget_usd:
                self._log("budget_exhausted", ticker=ticker, spent=round(self.spent, 4))
                return
            try:
                await self._tick(pos)
                backoff = 5.0                      # a good tick clears the penalty
                await self._sleep(self.poll_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:               # one contract must not stop the rest
                pos.failures += 1
                pos.last_error = f"{type(exc).__name__}: {exc}"
                self._log("error", ticker=ticker, error=pos.last_error,
                          failures=pos.failures, retry_in=backoff)
                self._save()
                await self._sleep(backoff)
                backoff = min(backoff * 2, 300.0)

    async def _tick(self, pos: Position) -> None:
        from . import __main__ as cli
        from ..history import History

        if pos.game_id is None:
            event = pos.ticker.rsplit("-", 1)[0]
            from .tools import find_game_for_market
            found = await find_game_for_market(event_ticker=event, league=pos.league)
            data = found.output if found.ok else {}
            pos.game_id = data.get("game_id")
            if not pos.game_id:
                raise RuntimeError(f"cannot link {event} to a fixture: {data}")
            self._log("linked", ticker=pos.ticker, game_id=pos.game_id)

        result = await asyncio.to_thread(History().settlement, pos.ticker)
        if result is not None:
            pos.settled, pos.result = True, result
            self._log("settled", ticker=pos.ticker, result=result,
                      forecasts=pos.forecasts, spent=round(pos.spent_usd, 4))
            self._save()
            return

        fingerprint = await asyncio.to_thread(
            cli.state_fingerprint, pos.league, pos.game_id, pos.ticker, self.price_step)
        if fingerprint == pos.last_fingerprint:
            return                                  # nothing material changed; spend nothing

        agent = AGENTS[self.agent_name](self.config, self.tools)
        run = await agent.run(pos.ticker)
        pos.spent_usd += run.cost_usd
        pos.forecasts += 1
        pos.last_fingerprint = fingerprint

        out = run.output if isinstance(run.output, dict) else {}
        entry = {"ts": _now(), "ticker": pos.ticker, "game_id": pos.game_id,
                 "status": fingerprint[0], "period": fingerprint[1],
                 "score": f"{fingerprint[3]}-{fingerprint[2]}",
                 "position": out.get("position"), "probability": out.get("probability"),
                 "market_price": out.get("market_price"),
                 "edge_reported": out.get("edge_after_fees"),
                 "cost_usd": round(run.cost_usd, 4), "error": run.error}
        self._record(entry)
        self._log("forecast", **{k: v for k, v in entry.items()
                                 if k in ("ticker", "score", "position",
                                          "probability", "market_price", "cost_usd")})
        self._save()

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)

    def _log(self, event: str, **fields) -> None:
        print(json.dumps({"ts": _now(), "event": event, **fields}), flush=True)


async def _auto_contracts(league: str, limit: int) -> list[str]:
    """Most-traded live markets in a league."""
    from .tools import live_markets
    found = await live_markets(league=league, limit=40)
    out: list[str] = []
    for game in (found.output or {}).get("markets", []):
        ranked = sorted(game["markets"], key=lambda m: -(m.get("volume") or 0))
        out += [m["ticker"] for m in ranked[:2]]
    return out[:limit]


async def main() -> int:
    ap = argparse.ArgumentParser(description="Run Kalshi forecasting agents as a service")
    ap.add_argument("--league", required=True)
    ap.add_argument("--contracts", default="", help="comma separated tickers")
    ap.add_argument("--auto", action="store_true", help="pick live markets automatically")
    ap.add_argument("--max-contracts", type=int, default=3)
    ap.add_argument("--agent", default="inplay", choices=list(AGENTS))
    ap.add_argument("--poll", type=float, default=45.0)
    ap.add_argument("--price-step", type=float, default=0.03)
    ap.add_argument("--budget", type=float, default=10.0, help="total USD across restarts")
    ap.add_argument("--per-run", type=float, default=0.30)
    ap.add_argument("--state-dir", default="~/.kalshi-agent")
    args = ap.parse_args()

    sup = Supervisor(args.league, args.agent, args.poll, args.price_step,
                     args.budget, args.per_run, args.state_dir)

    tickers = [t.strip() for t in args.contracts.split(",") if t.strip()]
    if args.auto and not tickers:
        tickers = await _auto_contracts(args.league, args.max_contracts)
    for ticker in tickers:
        sup.add(ticker)

    if not sup.positions:
        print(json.dumps({"ts": _now(), "event": "nothing_to_watch",
                          "league": args.league}), flush=True)
        return 1
    await sup.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
