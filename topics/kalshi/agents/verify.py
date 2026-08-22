"""Score forecasts against what actually happened.

Reads the supervisor's JSONL, asks the exchange how each contract settled, and
answers the only question that matters: is this agent better than the price it
was reading?

Every statistic is computed against the market on the same forecasts, because an
agent's Brier score alone says nothing — a market at 0.99 on a settled favourite
scores beautifully and required no skill. The comparison is the measurement.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..fees import taker_fee
from ..history import History


@dataclass
class Scored:
    """One forecast joined to its outcome."""

    ticker: str
    ts: str
    probability: float
    market_price: float
    outcome: float                 # 1.0 settled yes, 0.0 settled no
    position: str
    stake_usd: float

    @property
    def brier(self) -> float:
        return (self.probability - self.outcome) ** 2

    @property
    def market_brier(self) -> float:
        return (self.market_price - self.outcome) ** 2

    @property
    def edge_side(self) -> str:
        """Which way the agent disagreed with the market."""
        if self.probability > self.market_price:
            return "over"
        return "under" if self.probability < self.market_price else "flat"

    @property
    def directionally_right(self) -> bool:
        """Did disagreeing with the market pay?

        Over the market and it settled yes, or under and it settled no.
        """
        if self.edge_side == "over":
            return self.outcome == 1.0
        if self.edge_side == "under":
            return self.outcome == 0.0
        return False

    @property
    def pnl(self) -> float:
        """Realised P&L on the position actually taken, net of the entry fee."""
        if self.position == "PASS" or not self.stake_usd:
            return 0.0
        price = self.market_price
        contracts = self.stake_usd / max(price, 1e-9)
        fee = taker_fee(price, contracts)
        won = self.outcome == 1.0 if self.position == "YES" else self.outcome == 0.0
        return (contracts * (1 - price) - fee) if won else -(self.stake_usd + fee)


@dataclass
class Report:
    scored: list[Scored] = field(default_factory=list)
    unsettled: int = 0

    # ---- headline ----

    @property
    def n(self) -> int:
        return len(self.scored)

    @property
    def brier(self) -> float:
        return sum(s.brier for s in self.scored) / self.n if self.n else float("nan")

    @property
    def market_brier(self) -> float:
        return (sum(s.market_brier for s in self.scored) / self.n
                if self.n else float("nan"))

    @property
    def skill(self) -> float:
        """Brier skill score against the market. Positive means the agent beat it."""
        return 1 - (self.brier / self.market_brier) if self.market_brier else 0.0

    @property
    def bias(self) -> float:
        """Mean signed gap to the market. Positive means systematically optimistic."""
        return (sum(s.probability - s.market_price for s in self.scored) / self.n
                if self.n else 0.0)

    # ---- trading ----

    @property
    def taken(self) -> list[Scored]:
        return [s for s in self.scored if s.position != "PASS" and s.stake_usd]

    @property
    def win_rate(self) -> float:
        taken = self.taken
        if not taken:
            return float("nan")
        return sum(1 for s in taken if s.pnl > 0) / len(taken)

    @property
    def pnl(self) -> float:
        return sum(s.pnl for s in self.taken)

    @property
    def staked(self) -> float:
        return sum(s.stake_usd for s in self.taken)

    @property
    def roi(self) -> float:
        return self.pnl / self.staked if self.staked else float("nan")

    @property
    def hit_rate(self) -> float:
        """How often disagreeing with the market was the right side."""
        disagreed = [s for s in self.scored if s.edge_side != "flat"]
        if not disagreed:
            return float("nan")
        return sum(1 for s in disagreed if s.directionally_right) / len(disagreed)

    def calibration(self, bins: int = 5) -> list[dict]:
        """Predicted probability against realised frequency, bucketed."""
        buckets: dict[int, list[Scored]] = defaultdict(list)
        for s in self.scored:
            buckets[min(int(s.probability * bins), bins - 1)].append(s)
        out = []
        for b in sorted(buckets):
            rows = buckets[b]
            out.append({"bin": f"{b/bins:.1f}-{(b+1)/bins:.1f}",
                        "n": len(rows),
                        "predicted": sum(r.probability for r in rows) / len(rows),
                        "actual": sum(r.outcome for r in rows) / len(rows)})
        return out

    def summary(self) -> str:
        if not self.n:
            return f"no settled forecasts yet ({self.unsettled} awaiting settlement)"
        lines = [
            f"forecasts scored   {self.n} on {len({s.ticker for s in self.scored})} "
            f"contract(s)   (unsettled {self.unsettled})",
            f"Brier              agent {self.brier:.4f}   market {self.market_brier:.4f}",
            f"skill vs market    {self.skill:+.4f}   "
            f"({'agent better' if self.skill > 0 else 'market better'})",
            f"bias               {self.bias:+.4f}   "
            f"({'optimistic' if self.bias > 0 else 'pessimistic'} against the market)",
            f"right side         {self.hit_rate:.1%} of disagreements",
        ]
        if self.taken:
            lines += [
                f"positions taken    {len(self.taken)}   staked ${self.staked:,.2f}",
                f"win rate           {self.win_rate:.1%}",
                f"P&L                ${self.pnl:+,.2f}   ROI {self.roi:+.2%}",
            ]
        else:
            lines.append("positions taken    0 — every forecast passed, so no P&L")
        lines.append("")
        lines.append("calibration (predicted vs actual):")
        for row in self.calibration():
            lines.append(f"  {row['bin']}  n={row['n']:3}  "
                         f"predicted {row['predicted']:.3f}  actual {row['actual']:.3f}")
        return "\n".join(lines)


@dataclass
class Paper:
    """What the agent's numbers would have made, had they been traded.

    The agent passes on almost everything, so realised P&L is zero and says
    nothing about whether its probabilities are worth money. This applies a
    mechanical rule to its own estimates instead — bet whenever the edge after
    fees clears a threshold, size by fractional Kelly — and asks whether *that*
    makes money. It separates two questions the agent conflates: is the
    estimate good, and is the decision good.
    """

    trades: list[dict] = field(default_factory=list)
    bankroll: float = 0.0
    min_edge: float = 0.02
    kelly_fraction: float = 0.25

    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def pnl(self) -> float:
        return sum(t["pnl"] for t in self.trades)

    @property
    def staked(self) -> float:
        return sum(t["stake"] for t in self.trades)

    @property
    def win_rate(self) -> float:
        return (sum(1 for t in self.trades if t["pnl"] > 0) / self.n
                if self.n else float("nan"))

    @property
    def roi(self) -> float:
        return self.pnl / self.staked if self.staked else float("nan")

    @property
    def contracts(self) -> int:
        """Distinct contracts traded — the real sample size.

        Seven trades on one contract that settles once are seven correlated
        bets on a single outcome, not seven independent results. A win rate
        computed over them is a statement about one event.
        """
        return len({t["ticker"] for t in self.trades})

    @property
    def max_drawdown(self) -> float:
        peak = run = worst = 0.0
        for t in self.trades:
            run += t["pnl"]
            peak = max(peak, run)
            worst = min(worst, run - peak)
        return worst

    def summary(self) -> str:
        if not self.n:
            return (f"paper trading   no forecast cleared a {self.min_edge:.0%} edge "
                    "after fees — nothing to trade")
        lines = [
            f"paper trades       {self.n} on {self.contracts} contract(s)   "
            f"(rule: edge > {self.min_edge:.0%}, {self.kelly_fraction:g} Kelly)",
            f"staked             ${self.staked:,.2f}",
            f"P&L                ${self.pnl:+,.2f}   ROI {self.roi:+.2%}",
            f"win rate           {self.win_rate:.1%}",
            f"max drawdown       ${self.max_drawdown:,.2f}",
        ]
        if self.contracts < 20:
            lines.append(
                f"  ⚠ {self.n} trades but only {self.contracts} independent outcome(s). "
                "Repeated bets on one contract settle together, so this win rate "
                "and ROI describe a handful of events, not a track record. "
                "Treat as unusable below ~20 contracts.")
        return "\n".join(lines)


def paper_trade(report: "Report", bankroll: float = 50_000.0,
                min_edge: float = 0.02, kelly_fraction: float = 0.25) -> Paper:
    """Trade the agent's probabilities mechanically and see what happens."""
    from ..fees import kelly

    paper = Paper(bankroll=bankroll, min_edge=min_edge, kelly_fraction=kelly_fraction)
    equity = bankroll
    for s in sorted(report.scored, key=lambda x: x.ts):
        # Buy yes when the estimate exceeds the price; buy no when it is below.
        # Both are priced against the same book, so the no side costs 1 - price.
        for side, prob, price in (("YES", s.probability, s.market_price),
                                  ("NO", 1 - s.probability, 1 - s.market_price)):
            if not 0 < price < 1:
                continue
            gain = prob - (price + taker_fee(price))
            if gain <= min_edge:
                continue
            frac = kelly(prob, price, kelly_fraction)
            stake = round(equity * frac, 2)
            if stake < 1:
                continue
            contracts = stake / price
            fee = taker_fee(price, contracts)
            won = (s.outcome == 1.0) if side == "YES" else (s.outcome == 0.0)
            pnl = (contracts * (1 - price) - fee) if won else -(stake + fee)
            equity += pnl
            paper.trades.append({"ts": s.ts, "ticker": s.ticker, "side": side,
                                 "price": price, "stake": stake,
                                 "edge": round(gain, 4), "won": won,
                                 "pnl": round(pnl, 2), "equity": round(equity, 2)})
            break        # one side per forecast
    return paper


def load(path: str | Path = "~/.kalshi-agent/forecasts.jsonl",
         history: History | None = None) -> Report:
    """Join every recorded forecast to its settlement."""
    feed = Path(str(path)).expanduser()
    if not feed.exists():
        return Report()
    hist = history or History()

    rows = []
    for line in feed.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    settlement: dict[str, str | None] = {}
    report = Report()
    for row in rows:
        ticker = row.get("ticker")
        p, px = row.get("probability"), row.get("market_price")
        if not ticker or not isinstance(p, (int, float)) or not isinstance(px, (int, float)):
            continue
        if ticker not in settlement:
            try:
                settlement[ticker] = hist.settlement(ticker)
            except Exception:
                settlement[ticker] = None
        result = settlement[ticker]
        if result is None:
            report.unsettled += 1
            continue
        report.scored.append(Scored(
            ticker=ticker, ts=row.get("ts", ""), probability=p, market_price=px,
            outcome=1.0 if result == "yes" else 0.0,
            position=(row.get("position") or "PASS").upper(),
            stake_usd=float(row.get("stake_usd") or 0)))
    return report


def plot(report: Report, out_dir: str | Path = "~/.kalshi-agent/plots",
         paper: "Paper | None" = None) -> list[Path]:
    """Four charts: cumulative P&L, calibration, Brier over time, bias."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    directory = Path(str(out_dir)).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    scored = sorted(report.scored, key=lambda s: s.ts)
    if not scored:
        return []
    written: list[Path] = []

    # cumulative P&L, or cumulative Brier advantage when nothing was traded
    fig, ax = plt.subplots(figsize=(8, 4))
    trades = paper.trades if paper and paper.trades else None
    if trades:
        series = [t["equity"] - paper.bankroll for t in trades]
        ax.plot(series, lw=1.6)
        ax.set_title(f"Paper P&L  (${paper.pnl:+,.2f}, ROI {paper.roi:+.1%}, "
                     f"{paper.n} trades)")
        ax.set_ylabel("USD")
    elif report.taken:
        run, series = 0.0, []
        for s in scored:
            run += s.pnl
            series.append(run)
        ax.plot(series, lw=1.6)
        ax.set_title(f"Cumulative P&L  (${report.pnl:+,.2f}, ROI {report.roi:+.1%})")
        ax.set_ylabel("USD")
    else:
        run, series = 0.0, []
        for s in scored:
            run += s.market_brier - s.brier      # positive = agent ahead
            series.append(run)
        ax.plot(series, lw=1.6)
        ax.set_title("Cumulative Brier advantage over the market  (no positions taken)")
        ax.set_ylabel("market Brier − agent Brier")
    ax.axhline(0, color="black", lw=0.8, ls="--")
    ax.set_xlabel("forecast")
    fig.tight_layout()
    path = directory / "pnl.png"
    fig.savefig(path, dpi=120); plt.close(fig); written.append(path)

    # calibration
    rows = report.calibration()
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, 1], [0, 1], ls="--", color="black", lw=0.8, label="perfect")
    ax.plot([r["predicted"] for r in rows], [r["actual"] for r in rows],
            marker="o", lw=1.6, label="agent")
    for r in rows:
        ax.annotate(f"n={r['n']}", (r["predicted"], r["actual"]),
                    textcoords="offset points", xytext=(6, -10), fontsize=8)
    ax.set_xlabel("predicted"); ax.set_ylabel("actual")
    ax.set_title("Calibration"); ax.legend()
    fig.tight_layout()
    path = directory / "calibration.png"
    fig.savefig(path, dpi=120); plt.close(fig); written.append(path)

    # agent vs market Brier, per forecast
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot([s.brier for s in scored], lw=1.4, label=f"agent ({report.brier:.4f})")
    ax.plot([s.market_brier for s in scored], lw=1.4,
            label=f"market ({report.market_brier:.4f})")
    ax.set_title("Brier per forecast"); ax.set_xlabel("forecast")
    ax.legend(); fig.tight_layout()
    path = directory / "brier.png"
    fig.savefig(path, dpi=120); plt.close(fig); written.append(path)

    # signed gap to the market
    fig, ax = plt.subplots(figsize=(8, 3.5))
    gaps = [s.probability - s.market_price for s in scored]
    ax.bar(range(len(gaps)), gaps, width=0.8)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_title(f"Gap to market  (mean {report.bias:+.4f})")
    ax.set_xlabel("forecast"); ax.set_ylabel("agent − market")
    fig.tight_layout()
    path = directory / "bias.png"
    fig.savefig(path, dpi=120); plt.close(fig); written.append(path)
    return written


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Score recorded forecasts against outcomes")
    ap.add_argument("--feed", default="~/.kalshi-agent/forecasts.jsonl")
    ap.add_argument("--plots", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--bankroll", type=float, default=50_000.0)
    ap.add_argument("--min-edge", type=float, default=0.02)
    ap.add_argument("--kelly", type=float, default=0.25)
    args = ap.parse_args()

    report = load(args.feed)
    paper = paper_trade(report, args.bankroll, args.min_edge, args.kelly)
    if args.json:
        print(json.dumps({
            "n": report.n, "unsettled": report.unsettled,
            "brier": report.brier, "market_brier": report.market_brier,
            "skill": report.skill, "bias": report.bias,
            "hit_rate": report.hit_rate, "win_rate": report.win_rate,
            "pnl": report.pnl, "roi": report.roi,
            "calibration": report.calibration(),
            "paper": {"trades": paper.n, "pnl": paper.pnl, "roi": paper.roi,
                      "win_rate": paper.win_rate,
                      "max_drawdown": paper.max_drawdown}}, indent=2, default=str))
    else:
        print(report.summary())
        print()
        print(paper.summary())
    if args.plots:
        for path in plot(report, paper=paper):
            print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
