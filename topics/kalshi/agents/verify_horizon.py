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

from ..fees import taker_fee
from ..history import History


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
    def pnl(self) -> float:
        """Enter now at the quoted price, mark out at the realised mid."""
        if self.action == "PASS" or not self.entry_price or not self.stake_usd:
            return 0.0
        contracts = self.stake_usd / self.entry_price
        exit_value = (self.realised if self.action == "BUY_YES"
                      else 1 - self.realised)
        return contracts * (exit_value - self.entry_price
                            - taker_fee(self.entry_price))


@dataclass
class HorizonReport:
    windows: list[Window] = field(default_factory=list)
    unresolved: int = 0
    skipped: int = 0

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
    def direction_accuracy(self) -> float:
        calls = [w for w in self.moves if w.direction != "FLAT"]
        if not calls:
            return 0.0
        return sum(1 for w in calls if w.direction_right) / len(calls)

    @property
    def echoed(self) -> int:
        return sum(1 for w in self.windows if w.echoes_market)

    @property
    def coverage(self) -> float:
        seen = [w for w in self.windows if w.covered is not None]
        return sum(1 for w in seen if w.covered) / len(seen) if seen else 0.0

    @property
    def taken(self) -> list[Window]:
        return [w for w in self.windows if w.action != "PASS"]

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

    def summary(self) -> str:
        if not self.n:
            return (f"no scored windows ({self.unresolved} still open, "
                    f"{self.skipped} unscoreable)")
        lines = [
            f"HORIZON  {self.n} windows across {self.contracts} contracts, "
            f"{self.games} games",
            "",
            f"  price error       {self.mae:.4f}   (no-change {self.naive_mae:.4f})",
            f"  skill vs no-change {self.skill:+.1%}"
            + ("   — worse than saying nothing" if self.skill < 0 else ""),
            f"  direction         {self.direction_accuracy:.1%} of "
            f"{len([w for w in self.moves if w.direction != 'FLAT'])} calls "
            f"on real moves ({len(self.moves)}/{self.n} windows moved)",
            f"  interval coverage {self.coverage:.1%}",
            f"  echoed the market  {self.echoed}/{self.n} windows predicted the "
            f"current mid exactly",
        ]
        if self.taken:
            lines += [
                "",
                f"  traded            {len(self.taken)} of {self.n} windows",
                f"  pnl               ${self.pnl:+,.2f} on ${self.staked:,.0f} "
                f"({self.roi:+.2%})",
                f"  win rate          {self.win_rate:.1%}",
            ]
        else:
            lines += ["", "  no window cleared the fee threshold"]
        if self.unresolved:
            lines.append(f"\n  {self.unresolved} windows not yet due")
        if self.skipped:
            lines.append(f"  {self.skipped} skipped — no two-sided quote at target")
        if self.contracts < 5:
            plural = "contract" if self.contracts == 1 else "contracts"
            lines.append(f"\n  only {self.contracts} independent {plural} — "
                         "windows on one market are highly correlated, so "
                         "treat the pnl as an illustration, not a result")
        return "\n".join(lines)


SETTLE_MARGIN_S = 90
"""How long past the target to wait before scoring.

Kalshi's minute candle for a given minute is not queryable the instant that
minute ends. Scoring a window the moment it comes due therefore reads the
*previous* candle and marks the prediction against a price from before the
horizon closed — which flatters any forecast that said FLAT.
"""


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

        report.windows.append(Window(
            ticker=row["ticker"], ts=row.get("ts", ""), target_ts=target,
            mid_now=float(mid_now), predicted=float(predicted),
            realised=float(candle.mid),
            stated_direction=row.get("direction") or "FLAT",
            confidence=row.get("confidence") or 0.0,
            action=row.get("action") or "PASS",
            entry_price=row.get("entry_price") or 0.0,
            stake_usd=row.get("stake_usd") or 0.0,
            interval=row.get("interval"),
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
