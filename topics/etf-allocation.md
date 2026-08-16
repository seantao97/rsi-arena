# ETF allocation (weekly rebalance)

Agents produce a target portfolio across a liquid ETF universe, rebalanced weekly.

Running example: **$250k, weekly rebalance, benchmark 60/40.**

## Objective

> Given a universe, a current portfolio and a rebalance date, produce the allocation a competent,
> cost-aware allocator would most want to have held for the coming week.

**Asked of the reader** — *"Which of these would you rather have held this week?"* Winner or tie, then
one tap for why: thesis, risk, cost, or construction.

Same cost ceiling for both agents.

## Answer contract

| | |
|---|---|
| **Weights** | Target weight per ticker, summing to 1 including cash. Named ETFs, not asset classes. |
| **Thesis** | The view the allocation expresses, in one sentence per major tilt. |
| **Trades** | The diff from the current portfolio — what to buy and sell, in shares. |
| **Cost** | Round-trip cost in bps, including spread, and realised gains if any. |
| **Risk** | Estimated volatility, worst-case week, and top three risk contributors. |
| **Overlap** | Holdings overlap between the largest positions. Three S&P funds is one position. |
| **Counter-case** | The regime in which this allocation is wrong. |
| **Falsifier** | *"If 10-year yields break 4.8%, the duration tilt is wrong."* |

## Primitives

### Universe

| | |
|---|---|
| `universe(filter) → [ticker]` | Screen on AUM, expense ratio, average volume, inception date. |
| `etf_profile(ticker) → data` | Expense ratio, AUM, issuer, replication method, inception. |
| `holdings(ticker) → positions` | Look-through to underlying constituents and weights. |
| `overlap(a, b) → pct` | Shared holdings by weight. SPY and VOO are 99% the same trade. |
| `tracking_error(ticker, benchmark) → te` | How well it does what it claims. |
| `flows(ticker, window) → series` | Creations and redemptions. |

### Risk and construction

| | |
|---|---|
| `returns(tickers, window) → series` | Total return, dividend-adjusted. |
| `covariance(tickers, window, method) → matrix` | Sample, shrinkage, or EWMA. Method named. |
| `factor_exposure(ticker) → loadings` | Value, growth, momentum, quality, size, duration, credit. |
| `optimize(expected, cov, constraints) → weights` | Mean-variance, risk parity, or min-vol. |
| `risk_decomp(weights, cov) → contributions` | Marginal risk per position. Equal weights are not equal risk. |
| `drawdown(weights, window) → stats` | Historical max drawdown and worst week for the proposed mix. |
| `stress(weights, scenario) → pnl` | 2008, 2020-03, 2022 rates, taper tantrum. |

### Costs and execution

| | |
|---|---|
| `liquidity(ticker) → adv, spread` | Average daily volume and quoted width. |
| `cost_model(trades) → bps` | Spread, commission, and market impact for the trade size. |
| `tax_lots(current, target) → implications` | Realised short and long-term gains, wash-sale conflicts. |
| `rebalance_diff(current, target) → trades` | Share-level orders, with a no-trade band so weekly churn does not eat the thesis. |

### Context and output

| | |
|---|---|
| `regime(window) → indicators` | Rates, growth, inflation and credit-spread state. |
| `timeseries(series, window) → data` | Macro series from the registry. |
| `backtest(strategy, window) → stats` | The rule, historically, net of the cost model. |
| `search` · `fetch` · `weigh_source` · `extract_claims` · `verify_claim` | Shared. |
| `compute` · `run_code` · `estimate` · `calibrate` · `sensitivity` | Shared. |
| `recall` · `remember` · `counter` · `cite` · `draft` · `critique` | Shared. |

## Runtime additions

| | |
|---|---|
| `FundData` | Profiles, holdings, flows, expense ratios. |
| `PriceStore` | Adjusted price history across the universe. |
| `Portfolio` | Current holdings and cost basis, read-only to the agent. |
| `Settlement` | Realised weekly return per allocation, readable by `recall`. Agent memory only. |

## Notes

Weekly rebalancing costs real money. A no-trade band in `rebalance_diff` is what keeps the topic from
rewarding agents that churn to look active, and the cost line in the contract is what makes it visible
to the reader.
