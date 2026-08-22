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

    python -m topics.kalshi.agents.supervisor --league EPL --contracts TICKER
    python -m topics.kalshi.agents.supervisor --league EPL --discover --mode horizon

``--discover`` is the autonomous form: it rescans the league for live markets,
takes on new ones, releases settled ones, and keeps going. Nothing needs a
ticker chosen by hand.

``--mode horizon`` predicts the price five minutes ahead and lets arithmetic
decide the trade, rather than asking the model for a probability and a position.
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
    last_record: dict | None = None
    last_error: str | None = None


class Supervisor:
    """Runs forecasting agents until their contracts settle."""

    def __init__(self, league: str, agent: str = "inplay",
                 poll_s: float = 45.0, price_step: float = 0.03,
                 budget_usd: float = 10.0, per_run_usd: float = 0.30,
                 state_dir: str = "~/.kalshi-agent", mode: str = "inplay",
                 discover: bool = False, max_contracts: int = 4,
                 rescan_s: float = 300.0, max_failures: int = 6) -> None:
        self.league = league
        self.agent_name = agent
        self.mode = mode
        self.discover = discover
        self.max_contracts = max_contracts
        self.rescan_s = rescan_s
        self.max_failures = max_failures
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
            data.pop("last_record", None)
            self.positions[ticker] = Position(**data)

    def _save(self) -> None:
        payload = {"updated": _now(),
                   "positions": {t: {k: v for k, v in asdict(p).items()
                                     if k not in ("last_fingerprint", "last_record")}
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
        self._workers = {t: asyncio.create_task(self._own(t)) for t in self.positions}
        background = [asyncio.create_task(self._heartbeat())]
        if self.discover:
            background.append(asyncio.create_task(self._discovery_loop()))
        try:
            while not self._stop.is_set():
                if self._workers:
                    await asyncio.wait(self._workers.values(),
                                       return_when=asyncio.FIRST_COMPLETED)
                    self._workers = {t: w for t, w in self._workers.items()
                                     if not w.done()}
                if not self._workers and not self.discover:
                    break                      # fixed contract list, all settled
                if not self._workers:
                    await self._sleep(15)      # discovering: idle until one appears
        finally:
            for task in list(self._workers.values()) + background:
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*self._workers.values(), *background,
                                     return_exceptions=True)
            self._save()
            self._log("stop", spent=round(self.spent, 4),
                      settled=sum(1 for p in self.positions.values() if p.settled))

    async def _discovery_loop(self) -> None:
        """Take on new live markets, without being told which.

        Runs alongside the workers: settled contracts free a slot, and the next
        rescan fills it. That is what makes the service autonomous rather than a
        list of tickers someone typed.
        """
        from .tools import live_markets

        while not self._stop.is_set():
            try:
                active = sum(1 for p in self.positions.values() if not p.settled)
                room = self.max_contracts - active
                if room > 0:
                    found = await live_markets(league=self.league, limit=30)
                    added = 0
                    for game in (found.output or {}).get("markets", []):
                        ranked = sorted(game["markets"],
                                        key=lambda m: -(m.get("volume") or 0))
                        for market in ranked:
                            ticker = market["ticker"]
                            if added >= room:
                                break
                            if ticker in self.positions:
                                continue
                            # A market with no two-sided quote cannot be traded
                            # or scored, so it is not worth a slot.
                            if not market.get("yes_bid") or not market.get("yes_ask"):
                                continue
                            self.add(ticker)
                            self._workers[ticker] = asyncio.create_task(
                                self._own(ticker))
                            self._log("discovered", ticker=ticker,
                                      volume=market.get("volume"))
                            added += 1
                    if added:
                        self._save()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log("discovery_error", error=f"{type(exc).__name__}: {exc}")
            await self._sleep(self.rescan_s)

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
                if pos.failures >= self.max_failures:
                    # Give the slot back. An unlinkable contract that retries
                    # forever is indistinguishable from a working one to the
                    # discovery loop, which then never refills.
                    self._log("abandoned", ticker=ticker, failures=pos.failures,
                              error=pos.last_error)
                    pos.settled = True
                    self._save()
                    return
                await self._sleep(backoff)
                backoff = min(backoff * 2, 300.0)

    async def _tick(self, pos: Position) -> None:
        from . import __main__ as cli
        from ..history import History

        # Settlement is checked before linking, and deliberately so. A settled
        # market needs no fixture, and its game has usually rolled off the feed
        # — linking first meant such a contract could never be released, so it
        # retried forever and held a slot that discovery could not reuse.
        result = await asyncio.to_thread(History().settlement, pos.ticker)
        if result is not None:
            pos.settled, pos.result = True, result
            self._log("settled", ticker=pos.ticker, result=result,
                      forecasts=pos.forecasts, spent=round(pos.spent_usd, 4))
            self._save()
            return

        if pos.game_id is None:
            event = pos.ticker.rsplit("-", 1)[0]
            from .tools import find_game_for_market
            found = await find_game_for_market(event_ticker=event, league=pos.league)
            data = found.output if found.ok else {}
            pos.game_id = data.get("game_id")
            if not pos.game_id:
                raise RuntimeError(f"cannot link {event} to a fixture: {data}")
            self._log("linked", ticker=pos.ticker, game_id=pos.game_id)

        fingerprint = await asyncio.to_thread(
            cli.state_fingerprint, pos.league, pos.game_id, pos.ticker, self.price_step)
        # In probability mode a forecast only earns its cost when something
        # changed. In horizon mode the cadence *is* the product: each run opens
        # a window that gets scored five minutes later, and skipping quiet
        # stretches would drop exactly the windows where predicting FLAT is the
        # skill being measured.
        if self.mode != "horizon" and fingerprint == pos.last_fingerprint:
            return

        if self.mode == "horizon":
            entry = await self._horizon_tick(pos, fingerprint)
        else:
            entry = await self._probability_tick(pos, fingerprint)
        pos.last_fingerprint = fingerprint
        self._record(entry)
        self._log("forecast", **{k: v for k, v in entry.items()
                                 if k in ("ticker", "score", "position", "action",
                                          "probability", "predicted_mid",
                                          "market_price", "edge", "cost_usd")})
        self._save()

    async def _probability_tick(self, pos: Position, fingerprint: tuple) -> dict:
        """Ask for a probability, then check the arithmetic before recording."""
        from .validation import validate

        agent = AGENTS[self.agent_name](self.config, self.tools)
        run = await agent.run(pos.ticker)
        pos.spent_usd += run.cost_usd
        pos.forecasts += 1

        out = dict(run.output) if isinstance(run.output, dict) else {}
        entry = {"ts": _now(), "mode": "probability", "ticker": pos.ticker,
                 "game_id": pos.game_id, "status": fingerprint[0],
                 "period": fingerprint[1], "score": f"{fingerprint[3]}-{fingerprint[2]}",
                 "position": out.get("position"), "probability": out.get("probability"),
                 "market_price": out.get("market_price"),
                 "edge_after_fees": out.get("edge_after_fees"),
                 "stake_usd": out.get("stake_usd"),
                 "cost_usd": round(run.cost_usd, 4), "error": run.error}

        check = validate(entry, pos.last_record)
        entry = check.corrected
        entry["valid"] = check.ok
        if check.errors or check.warnings:
            entry["validation"] = check.errors + check.warnings
            self._log("validation", ticker=pos.ticker, ok=check.ok,
                      issues=check.errors + check.warnings)
        pos.last_record = {k: entry.get(k) for k in
                           ("probability", "score", "period")}
        return entry

    async def _horizon_tick(self, pos: Position, fingerprint: tuple) -> dict:
        """Predict the price five minutes out; let arithmetic decide the trade."""
        from .horizon import HORIZON_MINUTES, decide, horizon_agent, target_time
        from ..quotes import Quotes

        # The supervisor already knows the fixture, so the game state is passed
        # in rather than rediscovered by a tool-calling loop on every tick.
        from .tools import game_state
        state = await game_state(league=pos.league, game_id=pos.game_id)
        agent = horizon_agent(self.config, self.tools)
        run = await agent.run(pos.ticker, game=json.dumps(state.output)[:1200]
                              if state.ok else "unavailable")
        pos.spent_usd += run.cost_usd
        pos.forecasts += 1

        out = run.output if isinstance(run.output, dict) else {}
        quote = await asyncio.to_thread(Quotes().get_market, pos.ticker)
        predicted = out.get("predicted_mid")
        decision = (decide(predicted, quote.yes_bid, quote.yes_ask,
                           out.get("confidence") or 0.5,
                           interval=out.get("interval"))
                    if isinstance(predicted, (int, float))
                    else None)

        return {"ts": _now(), "mode": "horizon", "ticker": pos.ticker,
                "game_id": pos.game_id, "status": fingerprint[0],
                "period": fingerprint[1], "score": f"{fingerprint[3]}-{fingerprint[2]}",
                "horizon_minutes": HORIZON_MINUTES,
                "target_ts": target_time(HORIZON_MINUTES),
                "mid_now": quote.mid, "bid": quote.yes_bid, "ask": quote.yes_ask,
                "predicted_mid": predicted, "interval": out.get("interval"),
                "direction": out.get("direction"), "confidence": out.get("confidence"),
                "driver": out.get("driver"),
                "action": decision.action if decision else "PASS",
                "edge": decision.edge if decision else 0.0,
                "entry_price": decision.entry_price if decision else 0.0,
                "stake_usd": decision.size_usd if decision else 0.0,
                "cost_usd": round(run.cost_usd, 4), "error": run.error}

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
    ap.add_argument("--discover", action="store_true",
                    help="find live markets continuously and keep going")
    ap.add_argument("--mode", default="inplay", choices=["inplay", "horizon"])
    ap.add_argument("--rescan", type=float, default=300.0)
    ap.add_argument("--max-contracts", type=int, default=4)
    ap.add_argument("--agent", default="inplay", choices=list(AGENTS))
    ap.add_argument("--poll", type=float, default=45.0)
    ap.add_argument("--price-step", type=float, default=0.03)
    ap.add_argument("--budget", type=float, default=10.0, help="total USD across restarts")
    ap.add_argument("--per-run", type=float, default=0.30)
    ap.add_argument("--state-dir", default="~/.kalshi-agent")
    args = ap.parse_args()

    sup = Supervisor(args.league, args.agent, args.poll, args.price_step,
                     args.budget, args.per_run, args.state_dir,
                     mode=args.mode, discover=args.discover,
                     max_contracts=args.max_contracts, rescan_s=args.rescan)

    for ticker in [t.strip() for t in args.contracts.split(",") if t.strip()]:
        sup.add(ticker)

    if not sup.positions and not args.discover:
        print(json.dumps({"ts": _now(), "event": "nothing_to_watch",
                          "league": args.league}), flush=True)
        return 1
    await sup.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
