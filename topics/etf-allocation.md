# ETF allocation (daily)

Each trading day before the close, the agent sets target weights across a fixed ETF universe. Positions
carry over — the agent gets a chance to change them every day, not an obligation.

## Objective

> Set the allocation with the best risk-adjusted return over the next session, and explain the view it
> expresses.

**Asked of the reader** — *"Which of these would you rather have held tomorrow?"* Winner or tie, then one
tap for why: thesis, risk, cost, or construction.

## Universe, budget and scoring

Twenty tickers, fixed, chosen for liquidity and coverage:

```
Equity      SPY  QQQ  IWM  EFA  EEM
Sector      XLE  XLF  XLK  XLV  XLU
Rates       TLT  IEF  SHY
Credit      LQD  HYG
Real assets GLD  DBC  VNQ
FX / cash   UUP  BIL
```

| | |
|---|---|
| Account | $250,000, marked daily at the close |
| Constraints | Long only, weights sum to 1 including `BIL` as cash, max 35% per ticker |
| Decision time | 15:45 ET, filled at the close |
| Turnover | No forced trading. Unchanged weights cost nothing. |
| Scored by | Daily return net of costs; cumulative return, Sharpe, max drawdown, and turnover |

**Two scoreboards.** Preference votes rate the reasoning; realised return rates the portfolio. Publish
both, and treat disagreement between them as the finding.

## Answer contract

| | |
|---|---|
| **Weights** | Target weight per ticker, summing to 1. Unchanged from yesterday is a valid answer. |
| **Thesis** | One sentence per tilt away from the prior allocation. No tilt, no sentence. |
| **Trades** | The diff in shares, or `NO CHANGE`. |
| **Cost** | Round-trip cost of the diff in bps. |
| **Risk** | Estimated daily vol, worst-case day, and the top three risk contributors. |
| **Overlap** | Holdings overlap between the largest positions. SPY and QQQ are not two bets. |
| **Falsifier** | *"If 10-year yields break 4.8%, the TLT weight is wrong."* |

## Primitives

### Universe and holdings

| | |
|---|---|
| `etf_profile(ticker) → data` | Expense ratio, AUM, issuer, replication method. |
| `holdings(ticker) → positions` | Look-through to constituents and weights. |
| `overlap(a, b) → pct` | Shared holdings by weight. |
| `factor_exposure(ticker) → loadings` | Value, growth, momentum, quality, size, duration, credit. |
| `flows(ticker, window) → series` | Creations and redemptions. |

### Risk and construction

| | |
|---|---|
| `returns(tickers, window) → series` | Total return, dividend-adjusted. |
| `covariance(tickers, window, method) → matrix` | Sample, shrinkage, or EWMA. Method named. |
| `optimize(expected, cov, constraints) → weights` | Mean-variance, risk parity, or min-vol. |
| `risk_decomp(weights, cov) → contributions` | Marginal risk per position. Equal weights are not equal risk. |
| `drawdown(weights, window) → stats` | Historical worst day and max drawdown for the proposed mix. |
| `stress(weights, scenario) → pnl` | 2008, 2020-03, 2022 rates, taper tantrum. |

### Execution

| | |
|---|---|
| `position() → holdings` | Current weights, shares and cost basis. |
| `rebalance_diff(current, target, band) → trades` | Share-level orders with a no-trade band, so a 20bp drift does not trigger a round trip. |
| `liquidity(ticker) → adv, spread` | Volume and quoted width. |
| `cost_model(trades) → bps` | Spread, commission, impact. |

### Context and shared

| | |
|---|---|
| `regime(window) → indicators` | Rates, growth, inflation, credit spreads. |
| `timeseries(series, window) → data` | Macro series from the registry. |
| `backtest(strategy, window) → stats` | The rule, historically, net of the cost model. |
| Shared | `search` · `fetch` · `weigh_source` · `extract_claims` · `verify_claim` · `compute` · `run_code` · `estimate` · `calibrate` · `sensitivity` · `recall` · `remember` · `counter` · `cite` · `draft` · `critique` |

## Runtime additions

| | |
|---|---|
| `FundData` | Profiles, holdings, flows. |
| `PriceStore` | Adjusted price history for the universe. |
| `Book` | The agent's simulated account: weights, cash, cost basis, daily marks. |
| `Settlement` | Daily return per allocation, readable by `recall`. |

## Notes

Daily rebalancing on a 20-ticker universe will lose to buy-and-hold on costs alone unless the no-trade
band does real work. That is the point of scoring turnover alongside return — an agent that churns to
look active should be visibly worse.
