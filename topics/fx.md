# FX (G10, daily)

Each day at the New York close, the agent sets positions across ten G10 pairs. Positions carry over.
Marked daily.

## Objective

> Set the FX book with the best risk-adjusted return over the next session, and explain the view.

**Asked of the reader** — *"Which of these would you rather have held tomorrow?"* Winner or tie, then one
tap for why: thesis, carry, risk, or sizing.

## Universe, budget and scoring

```
EURUSD  USDJPY  GBPUSD  USDCHF  AUDUSD
NZDUSD  USDCAD  EURJPY  EURGBP  AUDJPY
```

| | |
|---|---|
| Account | $250,000 |
| Gross notional | ≤ 5× equity |
| Per pair | ≤ 1.5× equity |
| Decision time | 16:00 ET, marked at the next NY close |
| Carry | Positions roll with swap applied daily. No forced flatten. |
| Risk stop | Flat at −20% peak-to-trough |
| Scored by | **Sharpe first**, then cumulative return, max drawdown, and carry-versus-spot attribution |

Carry attribution is separate on purpose. A book that earns its return entirely from interest
differential is running a known trade with a known tail, and a reader should be able to see that rather
than infer it.

## Answer contract

| | |
|---|---|
| **Positions** | Signed notional per pair. `NO CHANGE` is valid. |
| **Exposure** | Gross notional, and **net USD exposure** — ten pairs are usually one dollar bet. |
| **Thesis** | One sentence per position that changed, naming the driver: carry, momentum, valuation, or event. |
| **Carry** | Annualised carry on the book, and what fraction of expected return it is. |
| **Risk** | Estimated daily vol, worst-case day, and correlation-adjusted position count. |
| **Falsifier** | *"If the BoJ moves the YCC band, the short JPY leg is wrong."* |

## Primitives

### Price and carry

| | |
|---|---|
| `spot_fx(pair) → rate` | Mid, bid, ask. |
| `forward_points(pair, tenor) → points` | The tradeable carry, not the theoretical one. |
| `interest_rates(currency, tenor) → rate` | Policy rate and OIS curve. |
| `carry(pair) → annualised` | Interest differential net of forward points and swap cost. |
| `real_rate(currency) → rate` | Inflation-adjusted. Nominal differentials mislead across inflation regimes. |

### Positioning and valuation

| | |
|---|---|
| `cot_positioning(currency) → data` | CFTC Commitments of Traders. Crowded carry unwinds violently. |
| `vol_surface_fx(pair, tenor) → surface` | Implied vol and **risk reversals** — the skew is a direct read on positioning. |
| `realized_vol(pair, window, method) → σ` | Method and window named. |
| `ppp_deviation(pair) → pct` | Long-run valuation anchor. Useless for a day, useful for knowing which way the tail leans. |
| `terms_of_trade(currency) → index` | Commodity terms of trade for AUD, CAD, NZD, NOK. |

### Risk and events

| | |
|---|---|
| `correlation(pairs, window) → matrix` | **The core risk primitive here.** G10 is dominated by one USD factor; ten positions are frequently one bet. |
| `exposure(positions) → gross, net_usd, factor` | Against the limits. |
| `risk_sentiment() → indicator` | Equity vol and risk-proxy beta. AUDJPY is a risk trade wearing a currency costume. |
| `central_bank_calendar(window) → events` | Meetings, minutes, and scheduled speeches. |
| `intervention_risk(pair) → assessment` | Historical intervention zones and official rhetoric. USDJPY and CHF have policy ceilings that are not in any model. |

### Execution and shared

| | |
|---|---|
| `position() → holdings` | Current notionals and cost basis. |
| `rebalance_diff(current, target, band) → trades` | With a no-trade band. |
| `cost_model(trades) → bps` | Spread plus daily swap. Swap is the dominant cost on a carried book. |
| Shared | `search` · `fetch` · `weigh_source` · `extract_claims` · `verify_claim` · `timeseries` · `compute` · `run_code` · `estimate` · `calibrate` · `sensitivity` · `recall` · `remember` · `counter` · `cite` · `draft` · `critique` |

## Runtime additions

| | |
|---|---|
| `FXFeed` | Spot, forwards, swap rates, vol surfaces. Read-only. |
| `Book` | Simulated account: notionals, cash, swap accrual, daily marks. |
| `Settlement` | Daily P&L and carry attribution, readable by `recall`. |

## Notes

The failure mode to watch is an agent that reports ten positions and one risk. `exposure` and
`correlation` are in the contract so a book that is really just short dollars reads as short dollars.
