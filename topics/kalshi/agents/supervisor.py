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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..linking import fixture_key
from .agents import AGENTS, default_config
from ..tools import TOOLS, kalshi_tools


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
    resting: dict | None = None            # an order posted, not yet filled
    holding: dict | None = None            # an open position, or nothing
    realised_pnl: float = 0.0              # closed round trips only
    trades: int = 0
    last_error: str | None = None


class Supervisor:
    """Runs forecasting agents until their contracts settle."""

    def __init__(self, league: str | list[str], agent: str = "inplay",
                 poll_s: float = 45.0, price_step: float = 0.03,
                 budget_usd: float = 10.0, per_run_usd: float = 0.30,
                 state_dir: str = "~/.kalshi-agent", mode: str = "inplay",
                 discover: bool = False, max_contracts: int = 4,
                 rescan_s: float = 300.0, max_failures: int = 6,
                 max_per_game: int = 3) -> None:
        self.leagues = ([league] if isinstance(league, str)
                        else [x for x in league if x])
        self.league = self.leagues[0]      # positions without one fall back here
        self.agent_name = agent
        self.mode = mode
        self.discover = discover
        self.max_contracts = max_contracts
        self.rescan_s = rescan_s
        self.max_failures = max_failures
        self.max_per_game = max(1, max_per_game)
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

    def add(self, ticker: str, league: str | None = None) -> None:
        """Take on a contract. ``league`` is per-position, because one
        supervisor may be sweeping several at once."""
        self.positions.setdefault(
            ticker, Position(ticker=ticker, league=league or self.league))

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

        while not self._stop.is_set():
            try:
                active = sum(1 for p in self.positions.values() if not p.settled)
                room = self.max_contracts - active
                if room > 0:
                    # Leagues are swept concurrently. Measured against the live
                    # API, twenty of them together take about as long as two in
                    # sequence, and none of them come back short — the counts
                    # match what each returns on its own.
                    swept = await asyncio.gather(
                        *(TOOLS["live_markets"].acall(league=lg, limit=30)
                          for lg in self.leagues),
                        return_exceptions=True)

                    games: list[tuple[str, list]] = []
                    for lg, found in zip(self.leagues, swept):
                        if isinstance(found, BaseException):
                            self._log("discovery_error", league=lg,
                                      error=f"{type(found).__name__}: {found}")
                            continue
                        for game in found.raw_output.get("markets", []):
                            tradeable = [m for m in game["markets"]
                                         # No two-sided quote means it can be
                                         # neither traded nor scored.
                                         if m.get("yes_bid") and m.get("yes_ask")
                                         and m["ticker"] not in self.positions]
                            if tradeable:
                                games.append((lg, sorted(
                                    tradeable,
                                    key=lambda m: -(m.get("volume") or 0))))

                    # Round-robin twice: across leagues, then across the games
                    # within each. One contract per game before any game gets a
                    # second, and one game per league before any league gets a
                    # second.
                    #
                    # Both matter. Four contracts on one match are four
                    # correlated observations where four matches are four
                    # independent ones, and the scoring needs the latter. And
                    # without the league pass a busy competition takes every
                    # slot — MLS alone had seven live fixtures here, enough to
                    # shut out four other leagues that were also playing.
                    by_league: dict[str, list] = {}
                    for lg, ranked in games:
                        by_league.setdefault(lg, []).append(ranked)

                    order: list[tuple[str, list]] = []
                    for depth in range(max((len(v) for v in by_league.values()),
                                           default=0)):
                        for lg in self.leagues:
                            bucket = by_league.get(lg) or []
                            if depth < len(bucket):
                                order.append((lg, bucket[depth]))

                    # How many slots each fixture already holds, so a match
                    # that is under way cannot keep the ones a later kickoff
                    # will need. Twelve contracts on one game are twelve
                    # correlated observations; the scoring counts distinct
                    # contracts for exactly this reason.
                    per_game: dict[str, int] = {}
                    for ticker, position in self.positions.items():
                        if not position.settled:
                            key = fixture_key(ticker)
                            per_game[key] = per_game.get(key, 0) + 1

                    added = 0
                    for depth in range(max((len(g) for _, g in order), default=0)):
                        for lg, ranked in order:
                            if added >= room:
                                break
                            if depth >= len(ranked):
                                continue
                            market = ranked[depth]
                            match = fixture_key(market["ticker"])
                            if per_game.get(match, 0) >= self.max_per_game:
                                continue
                            per_game[match] = per_game.get(match, 0) + 1
                            self.add(market["ticker"], league=lg)
                            self._workers[market["ticker"]] = asyncio.create_task(
                                self._own(market["ticker"]))
                            self._log("discovered", ticker=market["ticker"],
                                      league=lg, volume=market.get("volume"))
                            added += 1
                        if added >= room:
                            break
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
            if pos.holding:
                # Never closed, so it pays what the contract pays — a dollar or
                # nothing. This is the cost of not deciding to get out.
                from .horizon import Holding as _H
                held = _H.from_dict(pos.holding)
                payout = 1.0 if ((result == "yes") == (held.side == "YES")) else 0.0
                settled_pnl = round(held.contracts * (payout - held.price)
                                    - held.fee_paid, 2)
                pos.realised_pnl = round(pos.realised_pnl + settled_pnl, 2)
                self._log("settled_open_position", ticker=pos.ticker,
                          side=held.side, entry=held.price, payout=payout,
                          pnl=settled_pnl)
                self._record({"ts": _now(), "mode": "horizon",
                              "ticker": pos.ticker, "action": "SETTLE",
                              "settle_result": result, "side": held.side,
                              "entry_price": held.price,
                              "contracts": held.contracts,
                              "realised_pnl": settled_pnl})
                pos.holding = None
            self._log("settled", ticker=pos.ticker, result=result,
                      forecasts=pos.forecasts, pnl=pos.realised_pnl,
                      trades=pos.trades, spent=round(pos.spent_usd, 4))
            self._save()
            return

        if pos.game_id is None:
            event = pos.ticker.rsplit("-", 1)[0]
            found = await TOOLS["find_game_for_market"].acall(
                event_ticker=event, league=pos.league)
            data = found.raw_output
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
        from .horizon import (HORIZON_MINUTES, Holding, decide, horizon_agent,
                              quote_from, target_time)
        from ..fees import maker_fee, taker_fee
        from .validation import validate_horizon
        from ..quotes import Quotes

        # The supervisor already knows the fixture, so the game state is passed
        # in rather than rediscovered by a tool-calling loop on every tick.
        state = await TOOLS["game_state"].acall(
            league=pos.league, game_id=pos.game_id)
        agent = horizon_agent(self.config, self.tools)
        run = await agent.run(pos.ticker,
                              game=json.dumps(state.raw_output)[:1200]
                              if state.ok else "unavailable")
        pos.spent_usd += run.cost_usd
        pos.forecasts += 1

        out = run.output if isinstance(run.output, dict) else {}
        quote = await asyncio.to_thread(Quotes().get_market, pos.ticker)

        # The anchor is the exchange's mid, never the model's reading of it.
        delta = out.get("delta_cents")
        width = out.get("half_width_cents")
        # Settle any order posted last tick before deciding anything new: it
        # either got hit, or it did not and is pulled. A five-minute view does
        # not justify leaving a quote out indefinitely.
        if pos.resting:
            order = pos.resting
            filled = await asyncio.to_thread(
                self._resting_filled, pos.ticker, order)
            try:
                placed_at = datetime.fromisoformat(order["placed_at"])
            except (KeyError, ValueError):
                placed_at = datetime.now(timezone.utc)
            from .horizon import HORIZON_MINUTES
            expired = (datetime.now(timezone.utc) - placed_at
                       >= timedelta(minutes=HORIZON_MINUTES))
            if not filled and not expired:
                # Still standing. The view behind it has not run out yet.
                return {"ts": _now(), "mode": "horizon", "ticker": pos.ticker,
                        "action": "RESTING", "resting": order,
                        "realised_pnl": 0.0, "cost_usd": 0.0, "error": None}
            pos.resting = None
            if filled:
                fee = maker_fee(order["price"]) * order["contracts"]
                pos.holding = Holding(side=order["side"], price=order["price"],
                                      contracts=order["contracts"],
                                      fee_paid=round(fee, 4),
                                      opened_at=order["placed_at"]).to_dict()
                pos.trades += 1
                self._log("filled", ticker=pos.ticker, side=order["side"],
                          price=round(order["price"], 3))
            else:
                self._log("unfilled", ticker=pos.ticker, side=order["side"],
                          price=round(order["price"], 3))

        held = Holding.from_dict(pos.holding)
        predicted = interval = decision = None
        if isinstance(delta, (int, float)) and quote.mid is not None:
            predicted, low, high = quote_from(
                quote.mid, delta, width if isinstance(width, (int, float)) else 3.0)
            interval = [round(low, 4), round(high, 4)]
            decision = decide(predicted, quote.yes_bid, quote.yes_ask,
                              out.get("confidence") or 0.5, interval=interval,
                              holding=held)

        # Book-keeping lives here, not in the scorer. A position opens when the
        # agent says so and closes when the agent says so; nothing is marked out
        # on a timer. Whatever is still open when the contract settles is paid
        # at settlement, which is the expensive and honest treatment.
        realised = 0.0
        if decision and decision.action.startswith("OPEN") and held is None:
            side = "YES" if decision.action == "OPEN_YES" else "NO"
            contracts = (decision.size_usd / decision.entry_price
                         if decision.entry_price else 0.0)
            if decision.resting:
                # A posted order is not a position. It becomes one only if the
                # market comes to it, which the next tick checks against the
                # prints. Booking it on placement would credit the agent with
                # every quote it ever wrote, most of which nobody takes.
                pos.resting = {"side": side, "price": decision.entry_price,
                               "contracts": round(contracts, 2),
                               "placed_at": _now()}
                self._log("posted", ticker=pos.ticker, side=side,
                          price=round(decision.entry_price, 3),
                          contracts=round(contracts, 2))
            else:
                fee = taker_fee(decision.entry_price) * contracts
                pos.holding = Holding(side=side, price=decision.entry_price,
                                      contracts=round(contracts, 2),
                                      fee_paid=round(fee, 4),
                                      opened_at=_now()).to_dict()
                pos.trades += 1
        elif decision and decision.action == "CLOSE" and held is not None:
            proceeds = decision.entry_price          # already net of exit fee
            realised = round(held.contracts * proceeds
                             - held.contracts * held.price - held.fee_paid, 2)
            pos.realised_pnl = round(pos.realised_pnl + realised, 2)
            pos.holding = None

        entry = {"ts": _now(), "mode": "horizon", "ticker": pos.ticker,
                "game_id": pos.game_id, "status": fingerprint[0],
                "period": fingerprint[1], "score": f"{fingerprint[3]}-{fingerprint[2]}",
                "horizon_minutes": HORIZON_MINUTES,
                "target_ts": target_time(HORIZON_MINUTES),
                "mid_now": quote.mid, "bid": quote.yes_bid, "ask": quote.yes_ask,
                "predicted_mid": predicted, "interval": interval,
                "delta_cents": delta, "half_width_cents": width,
                "confidence": out.get("confidence"),
                "driver": out.get("driver"),
                "action": decision.action if decision else "PASS",
                "edge": decision.edge if decision else 0.0,
                "entry_price": decision.entry_price if decision else 0.0,
                "stake_usd": decision.size_usd if decision else 0.0,
                "resting": bool(decision.resting) if decision else False,
                "resting": pos.resting,
                "holding": pos.holding,
                "realised_pnl": realised,
                "reason": decision.reason if decision else "",
                "cost_usd": round(run.cost_usd, 4), "error": run.error}

        # A forecast whose stated direction contradicts its own number has
        # misread the book, and decide() will have built an edge out of the
        # misreading. Refuse the trade, but keep the prediction: it is still
        # scoreable, and still evidence about the harness.
        check = validate_horizon(entry)
        entry = check.corrected
        entry["valid"] = check.ok
        if check.errors or check.warnings:
            entry["validation"] = check.errors + check.warnings
            self._log("validation", ticker=pos.ticker, ok=check.ok,
                      issues=check.errors + check.warnings)
        return entry

    def _resting_filled(self, ticker: str, order: dict) -> bool:
        """Did anyone trade through a posted order while it was out there?

        A fill needs a counterparty. Someone has to sell into a resting bid, and
        that leaves a print at or below its price — so the traded range settles
        it, not the quoted one. On a thin book the best bid collapses whenever
        the makers pull, with nothing traded at all, and an order sitting at
        that collapsed bid *is* the best bid: alone at the top of the book is
        the opposite of filled.
        """
        from datetime import datetime

        from ..history import MINUTE, History
        try:
            placed = datetime.fromisoformat(order["placed_at"])
        except (KeyError, ValueError):
            return False
        from .horizon import HORIZON_MINUTES
        expired = min(placed + timedelta(minutes=HORIZON_MINUTES),
                      datetime.now(timezone.utc))
        if expired <= placed:
            return False
        candles = History().candles(ticker, placed, expired, MINUTE)
        traded = [c for c in candles if c.volume]
        if not traded:
            return False
        if order["side"] == "YES":
            lows = [c.price_low for c in traded if c.price_low is not None]
            return bool(lows) and min(lows) <= order["price"] + 1e-9
        target = 1 - order["price"]
        highs = [c.price_high for c in traded if c.price_high is not None]
        return bool(highs) and max(highs) >= target - 1e-9

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)

    def _log(self, event: str, **fields) -> None:
        print(json.dumps({"ts": _now(), "event": event, **fields}), flush=True)


async def _auto_contracts(league: str, limit: int) -> list[str]:
    """Most-traded live markets in a league."""
    found = await live_markets(league=league, limit=40)
    out: list[str] = []
    for game in found.raw_output.get("markets", []):
        ranked = sorted(game["markets"], key=lambda m: -(m.get("volume") or 0))
        out += [m["ticker"] for m in ranked[:2]]
    return out[:limit]


async def main() -> int:
    ap = argparse.ArgumentParser(description="Run Kalshi forecasting agents as a service")
    ap.add_argument("--league", required=True,
                    help="one league, or a comma-separated list to sweep together")
    ap.add_argument("--contracts", default="", help="comma separated tickers")
    ap.add_argument("--discover", action="store_true",
                    help="find live markets continuously and keep going")
    ap.add_argument("--mode", default="inplay", choices=["inplay", "horizon"])
    ap.add_argument("--rescan", type=float, default=300.0)
    ap.add_argument("--max-contracts", type=int, default=4)
    ap.add_argument("--max-per-game", type=int, default=3,
                    help="cap slots one fixture may hold, so a match already "
                         "under way leaves room for later kickoffs")
    ap.add_argument("--agent", default="inplay", choices=list(AGENTS))
    ap.add_argument("--poll", type=float, default=45.0)
    ap.add_argument("--price-step", type=float, default=0.03)
    ap.add_argument("--budget", type=float, default=10.0, help="total USD across restarts")
    ap.add_argument("--per-run", type=float, default=0.30)
    ap.add_argument("--state-dir", default="~/.kalshi-agent")
    args = ap.parse_args()

    leagues = [x.strip().upper() for x in args.league.split(",") if x.strip()]
    sup = Supervisor(leagues, args.agent, args.poll, args.price_step,
                     args.budget, args.per_run, args.state_dir,
                     mode=args.mode, discover=args.discover,
                     max_contracts=args.max_contracts, rescan_s=args.rescan,
                     max_per_game=args.max_per_game)

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
