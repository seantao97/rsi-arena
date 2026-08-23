"""Score five-minute-ahead price predictions.

Settlement scoring gives one label per contract and makes you wait for full
time. This gives one label every five minutes, from the same candlestick
history the agent could have read — so a night of soccer produces hundreds of
scored predictions instead of a handful.

The benchmark that matters is **no change**. A prediction that the price stays
where it is costs nothing and is right most of the time; a forecast is only
worth running if it beats that. Everything here is reported against it, and the
skill number is negative when it is not beaten. Absolute error alone would look
impressive on a quiet market and mean nothing.

    python -m topics.kalshi.agents.verify --mode horizon --plots
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..fees import maker_fee, taker_fee
from ..history import MINUTE, History


@dataclass
class Window:
    """One prediction and what the price actually did."""

    ticker: str
    ts: str
    target_ts: str
    mid_now: float
    predicted: float
    realised: float
    stated_direction: str          # what the model called it, for information
    confidence: float
    action: str
    entry_price: float
    stake_usd: float
    interval: list | None = None
    staleness_s: float = 0.0
    source: str = ""               # which feed this came from
    half_spread: float = 0.0       # cost of crossing to get out
    realised_pnl: float = 0.0      # booked by the supervisor on a close
    filled: bool = True            # resting orders only fill if price came to them       # how far before the target the price is from

    @property
    def error(self) -> float:
        return abs(self.predicted - self.realised)

    @property
    def naive_error(self) -> float:
        """What predicting no change would have cost."""
        return abs(self.mid_now - self.realised)

    @property
    def direction(self) -> str:
        """Derived from the numbers, not from the model's own label.

        Seen live: a forecast that put the price four cents above the current
        mid and labelled itself FLAT. The number is the prediction; the label
        is commentary, and scoring the commentary would score the wrong thing.
        """
        delta = self.predicted - self.mid_now
        if abs(delta) < 0.01:
            return "FLAT"
        return "UP" if delta > 0 else "DOWN"

    @property
    def echoes_market(self) -> bool:
        """Predicting exactly the current mid is the degenerate answer.

        It scores zero skill by construction and costs a model call to produce.
        Tracked because a base harness that mostly does this is the thing the
        next harness has to beat.
        """
        return self.predicted == self.mid_now

    @property
    def moved(self) -> bool:
        """Did the price move enough for direction to mean anything? One cent
        of drift on a two-cent spread is noise, not a move."""
        return abs(self.realised - self.mid_now) >= 0.01

    @property
    def direction_right(self) -> bool | None:
        if not self.moved:
            return None
        actual = "UP" if self.realised > self.mid_now else "DOWN"
        return self.direction == actual

    @property
    def covered(self) -> bool | None:
        """Did the realised price land inside the stated interval?"""
        if not (isinstance(self.interval, list) and len(self.interval) == 2):
            return None
        lo, hi = sorted(self.interval)
        return lo <= self.realised <= hi

    @property
    def resting(self) -> bool:
        return self.action.startswith("MAKE")

    @property
    def long_yes(self) -> bool:
        return self.action in ("BUY_YES", "MAKE_YES")

    @property
    def pnl(self) -> float:
        """What the agent actually booked, not what it could have booked.

        The supervisor keeps the position and books a number when the agent
        decides to close, or when the contract settles under an open position.
        Nothing here re-derives it.

        An earlier version marked every trade out at the five-minute mid, as
        though a position opened itself and closed itself for free. On these
        books that flattered the result badly: charging the exit turned one
        match from -4.05% to -22.18%, because a round trip costs half a spread
        plus two fees and the agent's predictions are two or three cents wide.
        Now a position that is never closed is simply carried, and pays a dollar
        or nothing.
        """
        return self.realised_pnl


@dataclass
class HorizonReport:
    windows: list[Window] = field(default_factory=list)
    unresolved: int = 0
    skipped: int = 0
    stale: int = 0

    @property
    def n(self) -> int:
        return len(self.windows)

    @property
    def mae(self) -> float:
        return (sum(w.error for w in self.windows) / self.n) if self.n else 0.0

    @property
    def naive_mae(self) -> float:
        return (sum(w.naive_error for w in self.windows) / self.n) if self.n else 0.0

    @property
    def skill(self) -> float:
        """Fraction of the no-change benchmark's error removed. Negative means
        the agent would have done better saying nothing."""
        return 1 - self.mae / self.naive_mae if self.naive_mae else 0.0

    @property
    def moves(self) -> list[Window]:
        return [w for w in self.windows if w.moved]

    @property
    def calls(self) -> list[Window]:
        """Windows where the agent committed to a direction and the price did
        move. Anything else has no directional call to be right or wrong about."""
        return [w for w in self.moves if w.direction != "FLAT"]

    @property
    def direction_accuracy(self) -> float:
        if not self.calls:
            return 0.0
        return sum(1 for w in self.calls if w.direction_right) / len(self.calls)

    @property
    def echoed(self) -> int:
        return sum(1 for w in self.windows if w.echoes_market)

    @property
    def coverage(self) -> float:
        seen = [w for w in self.windows if w.covered is not None]
        return sum(1 for w in seen if w.covered) / len(seen) if seen else 0.0

    @property
    def opened(self) -> list[Window]:
        return [w for w in self.windows if w.action.startswith("OPEN")]

    @property
    def closed(self) -> list[Window]:
        """Round trips the agent decided to end, plus any paid at settlement."""
        return [w for w in self.windows
                if w.action in ("CLOSE", "SETTLE") or w.realised_pnl]

    @property
    def taken(self) -> list[Window]:
        return self.closed

    @property
    def quoted(self) -> list[Window]:
        return [w for w in self.windows if w.action != "PASS"]

    @property
    def fill_rate(self) -> float:
        rest = [w for w in self.windows if w.resting]
        return sum(1 for w in rest if w.filled) / len(rest) if rest else 0.0

    @property
    def realised_pnl(self) -> float:
        return sum(w.realised_pnl for w in self.windows)

    @property
    def settled_open(self) -> list[Window]:
        """Positions carried all the way to settlement, having never been
        closed. The expensive ones, usually."""
        return [w for w in self.windows if w.action == "SETTLE"]

    @property
    def pnl(self) -> float:
        return sum(w.pnl for w in self.taken)

    @property
    def staked(self) -> float:
        return sum(w.stake_usd for w in self.taken)

    @property
    def roi(self) -> float:
        return self.pnl / self.staked if self.staked else 0.0

    @property
    def win_rate(self) -> float:
        return (sum(1 for w in self.taken if w.pnl > 0) / len(self.taken)
                if self.taken else 0.0)

    @property
    def contracts(self) -> int:
        return len({w.ticker for w in self.windows})

    @property
    def games(self) -> int:
        return len({w.ticker.rsplit("-", 1)[0] for w in self.windows})

    def by_run(self) -> list[dict]:
        """One row per source feed, for seeing whether the pooled number is
        made of agreeing runs or of one run dominating."""
        groups: dict[str, list[Window]] = {}
        for w in self.windows:
            groups.setdefault(w.source, []).append(w)
        rows = []
        for name, ws in groups.items():
            mae = sum(x.error for x in ws) / len(ws)
            naive = sum(x.naive_error for x in ws) / len(ws)
            taken = [x for x in ws if x.action != "PASS" and (x.filled
                                                             or not x.resting)]
            rows.append({
                "run": name, "windows": len(ws),
                "contracts": len({x.ticker for x in ws}),
                "skill": 1 - mae / naive if naive else 0.0,
                "echoed": sum(1 for x in ws if x.echoes_market),
                "quoted": sum(1 for x in ws if x.action != "PASS"),
                "filled": len(taken),
                "pnl": sum(x.pnl for x in taken),
                "staked": sum(x.stake_usd for x in taken),
            })
        return sorted(rows, key=lambda r: -r["windows"])

    def summary(self) -> str:
        if not self.n:
            return (f"no scored windows ({self.unresolved} still open, "
                    f"{self.skipped} unscoreable, {self.stale} stale)")
        lines = [
            f"HORIZON  {self.n} windows across {self.contracts} contracts, "
            f"{self.games} games",
            "",
            f"  price error       {self.mae:.4f}   (no-change {self.naive_mae:.4f})",
            f"  skill vs no-change {self.skill:+.1%}" + (
                "   — worse than saying nothing" if self.skill < -0.01
                else "   — indistinguishable from it" if self.skill < 0.01
                else ""),
            "  direction         " + (
                f"{self.direction_accuracy:.1%} of {len(self.calls)} calls"
                if self.calls else "no directional calls")
            + f" ({len(self.moves)}/{self.n} windows moved)",
            f"  interval coverage {self.coverage:.1%}",
            f"  echoed the market  {self.echoed}/{self.n} windows predicted the "
            f"current mid exactly",
        ]
        if self.quoted:
            lines += [
                "",
                f"  opened            {len(self.opened)} positions, "
                f"{len(self.closed)} closed "
                f"({len(self.settled_open)} of them at settlement)",
                f"  quoted            {len(self.quoted)} of {self.n} windows"
                + (f", {len([w for w in self.windows if w.resting])} resting "
                   f"({self.fill_rate:.0%} filled)"
                   if any(w.resting for w in self.windows) else ""),
                f"  traded            {len(self.taken)}",
                f"  pnl               ${self.pnl:+,.2f} realised on "
                f"${self.staked:,.0f} staked ({self.roi:+.2%})",
                f"  win rate          {self.win_rate:.1%}",
            ]
        else:
            lines += ["", "  no window cleared the fee threshold"]
        if self.unresolved:
            lines.append(f"\n  {self.unresolved} windows not yet due")
        if self.skipped:
            lines.append(f"  {self.skipped} skipped — no two-sided quote at target")
        if self.stale:
            lines.append(f"  {self.stale} dropped — nearest candle more than "
                         f"{MAX_STALENESS_S}s before the target")
        if self.contracts < 5:
            plural = "contract" if self.contracts == 1 else "contracts"
            lines.append(f"\n  only {self.contracts} independent {plural} — "
                         "windows on one market are highly correlated, so "
                         "treat the pnl as an illustration, not a result")
        return "\n".join(lines)


MAX_STALENESS_S = 180
"""How far before the target a resolved price may sit and still count.

Kalshi emits a candle only for minutes that saw activity, so on a thin market
the target minute may have none and ``quote_at`` returns an older one — which
would mark the prediction against a price from before the horizon opened. Seen
on a real contract: 75 minutes of an EPL match produced 29 candles, not 75.

Three minutes is generous for a five-minute horizon, and windows beyond it are
dropped rather than scored, because a wrong label is worse than a missing one.
"""

SETTLE_MARGIN_S = 90
"""How long past the target to wait before scoring.

Kalshi's minute candle for a given minute is not queryable the instant that
minute ends. Scoring a window the moment it comes due therefore reads the
*previous* candle and marks the prediction against a price from before the
horizon closed — which flatters any forecast that said FLAT.
"""


def _filled(history: History, ticker: str, placed: datetime, due: datetime,
            action: str, entry: float) -> bool:
    """Did anyone actually trade against a resting order during the window?

    A fill needs a counterparty. Someone has to sell into a resting bid, and
    that leaves a print at or below its price — so what settles the question is
    the traded range, not the quoted one.

    This started out reading the quoted range and was wrong in a way that
    flattered the agent badly. On a thin book the best bid collapses whenever
    the makers pull, with nothing traded at all: one live window showed
    ``bid[0.08..0.18]`` against ``volume 0`` while every actual print that
    period was 0.23 or higher. A resting buy at 0.095 was scored as filled
    there, and since the contract later recovered it booked +$339 — enough on
    its own to turn the run's pnl from -$156 to +$184. Nobody had sold to it.
    An order resting at the collapsed bid *is* the best bid; being alone at the
    top of the book is the opposite of being filled.

    Still optimistic in one respect, deliberately: any print through the price
    counts, when a real queue might have absorbed the whole trade ahead of us.
    That errs toward counting fills, which errs against the agent.
    """
    candles = history.candles(ticker, placed, due, MINUTE)
    if not candles:
        return False
    if action == "MAKE_YES":
        lows = [c.price_low for c in candles
                if c.price_low is not None and c.volume]
        return bool(lows) and min(lows) <= entry + 1e-9
    # A resting sell is quoted on the yes side at 1 - entry; it fills when
    # someone buys through that price.
    target = 1 - entry
    highs = [c.price_high for c in candles
             if c.price_high is not None and c.volume]
    return bool(highs) and max(highs) >= target - 1e-9


def load_many(paths, history: History | None = None) -> HorizonReport:
    """Score several runs as one body of evidence.

    A single run covers one evening and a handful of contracts, which is not
    enough to separate a real number from a lucky one — every run so far has
    swung tens of percent before settling. Pooling them is how the baseline
    stops being anecdote: same scoring, same guards, one report.

    Runs are kept distinct where it matters. Contract and game counts are taken
    over the union, so ten windows on one fixture in four separate runs are
    still one fixture.
    """
    history = history or History()
    pooled = HorizonReport()
    for path in paths:
        part = load(path, history)
        pooled.windows.extend(part.windows)
        pooled.unresolved += part.unresolved
        pooled.skipped += part.skipped
        pooled.stale += part.stale
    return pooled


def load(path: str | Path = "~/.kalshi-agent/forecasts.jsonl",
         history: History | None = None) -> HorizonReport:
    """Read horizon forecasts and look up what the price actually did."""
    history = history or History()
    report = HorizonReport()
    file = Path(path).expanduser()
    if not file.exists():
        return report

    now = datetime.now().astimezone()
    for line in file.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("mode") != "horizon" or row.get("error"):
            continue
        predicted, mid_now = row.get("predicted_mid"), row.get("mid_now")
        target = row.get("target_ts")
        if not all(isinstance(v, (int, float)) for v in (predicted, mid_now)) \
                or not target:
            report.skipped += 1
            continue

        due = datetime.fromisoformat(target)
        if (now - due).total_seconds() < SETTLE_MARGIN_S:
            report.unresolved += 1
            continue

        candle = history.quote_at(row["ticker"], due)
        # `two_sided` rejects the empty post-close book, which quotes 0.00/1.00
        # and would otherwise score every prediction against a fictional 0.50.
        if candle is None or not candle.two_sided or candle.mid is None:
            report.skipped += 1
            continue

        staleness = (due - candle.ts.astimezone(due.tzinfo)).total_seconds()
        if staleness > MAX_STALENESS_S:
            report.stale += 1
            continue

        action = row.get("action") or "PASS"
        entry = row.get("entry_price") or 0.0
        filled = True
        if action.startswith("MAKE") and entry:
            filled = _filled(history, row["ticker"],
                             datetime.fromisoformat(row["ts"]), due,
                             action, entry)

        report.windows.append(Window(
            ticker=row["ticker"], ts=row.get("ts", ""), target_ts=target,
            mid_now=float(mid_now), predicted=float(predicted),
            realised=float(candle.mid),
            stated_direction=row.get("direction") or "FLAT",
            confidence=row.get("confidence") or 0.0,
            action=action, entry_price=entry, filled=filled,
            stake_usd=row.get("stake_usd") or 0.0,
            interval=row.get("interval"),
            staleness_s=staleness,
            source=file.parent.name,
            realised_pnl=float(row.get("realised_pnl") or 0.0),
            # Closing crosses half the spread. The book at entry is the best
            # estimate available for what it will cost at exit.
            half_spread=(max(0.0, (row.get("ask") or 0) - (row.get("bid") or 0))
                         / 2),
        ))
    return report


def plot(report: HorizonReport, out_dir: str | Path = "~/.kalshi-agent/plots") -> list[Path]:
    """Predicted vs realised, error against the benchmark, and equity."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    if not report.n:
        return []
    w = report.windows
    written = []

    fig, ax = plt.subplots(figsize=(5, 5))
    ax.scatter([x.realised for x in w], [x.predicted for x in w],
               c=[x.confidence for x in w], cmap="viridis", s=28,
               alpha=0.8, edgecolors="none")
    ax.plot([0, 1], [0, 1], "k--", lw=0.8, alpha=0.5)
    ax.set(xlabel="realised mid, 5 min later", ylabel="predicted mid",
           title=f"Predicted vs realised (n={report.n})", xlim=(0, 1), ylim=(0, 1))
    fig.tight_layout(); path = out / "horizon_scatter.png"
    fig.savefig(path, dpi=130); plt.close(fig); written.append(path)

    fig, ax = plt.subplots(figsize=(6, 3.6))
    ax.bar(["agent", "no change"], [report.mae, report.naive_mae],
           color=["#2b6cb0", "#a0aec0"], width=0.5)
    ax.set(ylabel="mean absolute error",
           title=f"Skill vs benchmark: {report.skill:+.1%}")
    fig.tight_layout(); path = out / "horizon_skill.png"
    fig.savefig(path, dpi=130); plt.close(fig); written.append(path)

    if report.taken:
        equity, total = [], 0.0
        for x in report.taken:
            total += x.pnl
            equity.append(total)
        fig, ax = plt.subplots(figsize=(7, 3.6))
        ax.plot(range(1, len(equity) + 1), equity, lw=1.6, color="#2f855a")
        ax.axhline(0, color="k", lw=0.8, alpha=0.4)
        ax.set(xlabel="trade", ylabel="cumulative $",
               title=f"Paper pnl ${report.pnl:+,.0f} ({report.roi:+.2%})")
        fig.tight_layout(); path = out / "horizon_equity.png"
        fig.savefig(path, dpi=130); plt.close(fig); written.append(path)

    return written
