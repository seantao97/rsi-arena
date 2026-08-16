# ETF allocation (daily, long/short)

Each trading day before the close, the agent sets target weights across a fixed 50-ETF universe. Longs
and shorts are both allowed. Positions carry over — the agent gets a chance to change them every day,
not an obligation.

## Objective

> Set the book with the best risk-adjusted return over the next session, and explain the view it
> expresses.

**Asked of the reader** — *"Which of these would you rather have held tomorrow?"* Winner or tie, then one
tap for why: thesis, risk, cost, or construction.

## Universe

Fifty tickers, fixed, chosen for liquidity, borrow availability and coverage.

```
Broad equity   SPY  QQQ  IWM  MDY  EFA  EEM  VGK  EWJ
US sectors     XLK  XLF  XLE  XLV  XLI  XLY  XLP  XLU  XLB  XLRE  XLC
Industry       SOXX SMH  IBB  XBI  ITA  KRE  XOP  XME  JETS  IYT
Factor/style   MTUM QUAL USMV VLUE IWF  IWD
Rates          TLT  IEF  SHY  TIP  BIL
Credit         LQD  HYG  EMB
Real assets    GLD  SLV  DBC  USO  VNQ
FX             UUP  FXE
```

No leveraged or inverse products. Their path dependence makes them a different game, and a good
trending week would flatter an agent that understood nothing.

## Budget and scoring

| | |
|---|---|
| Account | $250,000, marked daily at the close |
| Gross exposure | ≤ 200% of equity |
| Net exposure | between −50% and +150% |
| Per ticker | ≤ 35% long, ≤ 20% short |
| Decision time | 15:45 ET, filled at the close |
| Turnover | No forced trading. Unchanged weights cost nothing. |
| Risk stop | Flat at −20% peak-to-trough. A blown-up agent is noise on the leaderboard. |
| Scored by | **Sharpe first**, then cumulative return, max drawdown, turnover, and average gross exposure |

Sharpe leads deliberately. With 200% gross available, raw return rewards whoever levered hardest into a
good week. Reporting gross alongside return is what keeps that visible.

The benchmark is 60/40 (`SPY`/`IEF`), shown on the same chart. An agent that cannot beat two tickers
rebalanced monthly has not earned its cost line.

## Answer contract

| | |
|---|---|
| **Weights** | Signed target weight per ticker. Negative is short. `NO CHANGE` is a valid answer. |
| **Exposure** | Gross and net, stated. |
| **Thesis** | One sentence per position or pair that changed. No change, no sentence. |
| **Trades** | The diff in shares, with side. |
| **Cost** | Round-trip cost in bps, including borrow on any new short. |
| **Risk** | Estimated daily vol, worst-case day, top three risk contributors, and net factor exposure. |
| **Overlap** | Overlap between the largest positions. Long SOXX against short SMH is not a pair trade, it is a fee. |
| **Falsifier** | *"If 10-year yields break 4.8%, the long TLT leg is wrong."* |

## Primitives

### Universe and holdings

| | |
|---|---|
| `etf_profile(ticker) → data` | Expense ratio, AUM, issuer, replication method. |
| `holdings(ticker) → positions` | Look-through to constituents and weights. |
| `overlap(a, b) → pct` | Shared holdings by weight. Matters far more with shorts — SOXX and SMH are ~90% the same book. |
| `factor_exposure(ticker) → loadings` | Value, growth, momentum, quality, size, duration, credit. |
| `flows(ticker, window) → series` | Creations and redemptions. |

### Risk and construction

| | |
|---|---|
| `returns(tickers, window) → series` | Total return, dividend-adjusted. |
| `covariance(tickers, window, method) → matrix` | Sample, Ledoit-Wolf shrinkage, or EWMA. **A 50×50 sample covariance from a short window is ill-conditioned** — shrinkage is not optional at this universe size, and the method must be named. |
| `optimize(expected, cov, constraints) → weights` | Mean-variance, risk parity, or min-vol, with long/short and exposure constraints. |
| `risk_decomp(weights, cov) → contributions` | Marginal risk per position. A market-neutral book can still be one factor bet. |
| `exposure(weights) → gross, net, factor` | Gross, net, and net factor loadings against the limits. |
| `drawdown(weights, window) → stats` | Historical worst day and max drawdown for the proposed book. |
| `stress(weights, scenario) → pnl` | 2008, 2020-03, 2022 rates, taper tantrum, and a short-squeeze scenario. |

### Execution and financing

| | |
|---|---|
| `position() → holdings` | Current signed weights, shares, cost basis. |
| `rebalance_diff(current, target, band) → trades` | Share-level orders with a no-trade band, so a 20bp drift does not trigger a round trip. |
| `liquidity(ticker) → adv, spread` | Volume and quoted width. |
| `borrow(ticker) → availability, rate` | Short availability and annualised borrow cost. `XBI` and `JETS` are not free to short; `SPY` effectively is. |
| `margin(weights) → requirement` | Reg T requirement against account equity. |
| `cost_model(trades) → bps` | Spread, commission, impact, plus borrow accrual and any distribution liability on shorts. |

### Context and shared

| | |
|---|---|
| `regime(window) → indicators` | Rates, growth, inflation, credit spreads. |
| `timeseries(series, window) → data` | Macro series from the registry. |
| `backtest(strategy, window) → stats` | The rule, historically, net of the cost model including borrow. |
| Shared | `search` · `fetch` · `weigh_source` · `extract_claims` · `verify_claim` · `compute` · `run_code` · `estimate` · `calibrate` · `sensitivity` · `recall` · `remember` · `counter` · `cite` · `draft` · `critique` |

## Runtime additions

| | |
|---|---|
| `FundData` | Profiles, holdings, flows. |
| `PriceStore` | Adjusted price history for the universe. |
| `BorrowDesk` | Short availability and rates, daily. |
| `Book` | The agent's simulated account: signed weights, cash, margin, borrow accrual, daily marks. |
| `Settlement` | Daily return per allocation, readable by `recall`. |

## Notes

**Shorting changes what the topic tests.** Long-only daily allocation is mostly a bet on regime. With
shorts, pairs and hedges become available, so the interesting question shifts to whether an agent can
construct genuine relative-value exposure rather than dressed-up beta. `exposure` and `risk_decomp` are
in the contract for that reason — a book that is net-flat and 90% one factor should be legible to a
reader as the single bet it is.

**Borrow cost is the quiet killer.** A short that carries at 8% annualised needs meaningful alpha just to
break even, and agents that ignore `borrow` will look good in backtest and lose in scoring.
