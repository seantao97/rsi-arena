# Index options (NDX, 0DTE to weekly)

Agents produce a trade plan on NDX options with an expiry between today and roughly a week out.

Running example: **NDX 0DTE, Wednesday, FOMC at 14:00.**

## Objective

> Given the index, the chain and a horizon of 0DTE to one week, produce the trade plan a competent
> options trader would most want to have read before putting it on.

**Asked of the reader** — *"Which of these would you rather have traded?"* Winner or tie, then one tap
for why: thesis, pricing, risk, or exit plan.

Same cost ceiling for both agents.

## Answer contract

| | |
|---|---|
| **Structure** | Legs, strikes, expiry, quantity. *"Sell 21500/21450 put spread, 0DTE, 10x."* Not "I'd be bearish." |
| **Thesis** | What the trade expresses — direction, volatility, skew, or decay — named as one of those. |
| **Entry** | Limit price, and net delta/gamma/vega/theta at entry. |
| **Edge** | The specific mispricing, quantified. *"IV 18.2 vs 10-day realised 12.4."* |
| **Risk** | Max loss, breakevens, and gamma exposure into the close. |
| **Exit** | Profit target, stop, and a time-based exit. 0DTE without a time exit is not a plan. |
| **Falsifier** | *"If NDX trades above 21650 before 11:00, this is wrong."* |

## Primitives

### Market state

| | |
|---|---|
| `spot(root) → level` | Index level, timestamped. |
| `futures(root, expiry) → price` | NQ, for overnight and pre-open. |
| `chain(root, expiry) → strikes` | Bid, ask, mid, IV, OI, volume per strike. |
| `liquidity(contract) → spread, depth` | Quoted width and size. 0DTE wings are wide enough to erase the edge. |
| `iv_surface(root, asof) → surface` | IV by strike and expiry. Skew and term structure. |
| `term_structure(root) → curve` | VXN by tenor. Backwardation means something different than contango. |

### Pricing and risk

| | |
|---|---|
| `price_option(model, params) → value` | Black-Scholes or binomial. Model named in the output. |
| `greeks(structure) → delta, gamma, vega, theta` | Position-level, not per-leg. |
| `payoff(structure, spot_grid, t) → curve` | P&L at expiry and at T+n. |
| `margin(structure) → requirement` | Buying power consumed. |
| `cost_model(structure, size) → costs` | Commission, spread crossed, exercise and assignment fees. |

### Volatility

| | |
|---|---|
| `realized_vol(underlier, window, method) → σ` | Close-to-close, Parkinson, or Garman-Klass. Method named. |
| `vrp(root, horizon) → premium` | Implied minus realised. The premise of most short-vol trades. |
| `simulate_path(params, draws) → distribution` | Terminal P&L distribution under GBM or a jump model. |

### Positioning and events

| | |
|---|---|
| `dealer_gamma(root) → profile` | Estimated dealer gamma by strike from OI. Long gamma pins, short gamma accelerates. |
| `component_weights(index) → weights` | NDX is top-heavy. One name can carry the index. |
| `event_calendar(window) → events` | FOMC, CPI, and component earnings inside the expiry. |
| `pin_risk(structure, expiry) → exposure` | Strikes near spot at the close. |

### Analysis and output

| | |
|---|---|
| `backtest_rule(rule, window) → stats` | This structure, under this condition, historically. |
| `base_rate` · `decompose` · `compute` · `run_code` · `estimate` · `calibrate` · `sensitivity` | Shared. |
| `recall` · `remember` · `counter` · `cite` · `draft` · `critique` | Shared. |

## Runtime additions

| | |
|---|---|
| `OptionsFeed` | Chains, IV, OI, greeks. Read-only, no order entry. |
| `Settlement` | Realised P&L per plan, readable by `recall`. Agent memory only. |

## Notes

0DTE settles in hours, so this is the fastest-settling market topic. The risk is that readers reward
the plan that *sounds* tightest rather than the one that survives a gap. Worth watching whether the
exit-plan reason tag correlates with realised outcomes.
