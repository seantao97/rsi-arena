# Index options (NDX, daily)

Each trading day before the close, the agent chooses an NDX options position to hold. Any structure —
naked call or put, vertical, straddle, strangle, calendar, butterfly, or flat. P&L is marked daily.

## Objective

> Choose the NDX options position with the best risk-adjusted return over the next session, and explain
> why it is the right position.

**Asked of the reader** — *"Which of these would you rather have held tomorrow?"* Winner or tie, then one
tap for why: thesis, pricing, risk, or exit.

## Budget and scoring

| | |
|---|---|
| Account | $100,000, marked daily |
| Max risk per day | $25,000 defined loss. Undefined-risk structures need margin inside the account. |
| Expiries allowed | 0DTE through 7DTE |
| Decision time | 15:45 ET, filled at 15:55 mid ± half spread |
| Carry | Positions may be held or closed the next day. No forced flatten. |
| Scored by | Daily P&L net of costs; cumulative return, Sharpe, and max drawdown over the window |

**Two scoreboards.** Preference votes rate the plan; realised P&L rates the position. Both are
published. Where they disagree, the P&L board is right and the divergence is the interesting number.

## Answer contract

| | |
|---|---|
| **Structure** | Legs, strikes, expiry, quantity. *"Sell 21500/21450 put spread 0DTE, 40x, $1.10 credit."* Or `FLAT`, which is a legitimate answer. |
| **Thesis** | One of: direction, volatility, skew, decay. Named, not implied. |
| **Entry** | Limit price and net delta, gamma, vega, theta. |
| **Edge** | The mispricing, quantified. *"IV 18.2 vs 10-day realised 12.4."* |
| **Risk** | Max loss in dollars against the $25k limit, breakevens, and gamma into the close. |
| **Tomorrow** | Hold, roll, or close, and what triggers each. |
| **Falsifier** | *"If NDX gaps below 21400, this thesis is dead."* |

## Primitives

### Market state

| | |
|---|---|
| `spot(root) → level` | Index level, timestamped. |
| `futures(root, expiry) → price` | NQ, for the overnight session. |
| `chain(root, expiry) → strikes` | Bid, ask, mid, IV, OI, volume per strike. |
| `liquidity(contract) → spread, depth` | Quoted width and size. Wings are wide enough to erase the edge. |
| `iv_surface(root, asof) → surface` | IV by strike and expiry. Skew and term structure. |
| `term_structure(root) → curve` | VXN by tenor. |

### Pricing and risk

| | |
|---|---|
| `price_option(model, params) → value` | Black-Scholes or binomial. Model named in the output. |
| `greeks(structure) → delta, gamma, vega, theta` | Position level. |
| `payoff(structure, spot_grid, t) → curve` | P&L at expiry and at T+1. |
| `margin(structure) → requirement` | Buying power consumed against the account. |
| `cost_model(structure, size) → costs` | Commission, spread crossed, exercise and assignment fees. |

### Volatility and positioning

| | |
|---|---|
| `realized_vol(underlier, window, method) → σ` | Close-to-close, Parkinson, or Garman-Klass. Method named. |
| `vrp(root, horizon) → premium` | Implied minus realised. The premise of most short-vol trades. |
| `simulate_path(params, draws) → distribution` | Next-session P&L distribution under GBM or a jump model. |
| `dealer_gamma(root) → profile` | Estimated dealer gamma by strike from OI. Long gamma pins, short gamma accelerates. |
| `component_weights(index) → weights` | NDX is top-heavy. One name can carry the index. |
| `event_calendar(window) → events` | FOMC, CPI, and component earnings inside the expiry. |

### Position management

| | |
|---|---|
| `position() → holdings` | What is on now, with cost basis and current mark. |
| `roll(current, target) → orders` | The diff, priced. Rolling a tested spread is not the same trade as opening it. |
| `backtest_rule(rule, window) → stats` | This structure under this condition, historically, net of costs. |

### Shared

`compute` · `run_code` · `base_rate` · `estimate` · `calibrate` · `sensitivity` · `recall` · `remember` ·
`counter` · `cite` · `draft` · `critique`

## Runtime additions

| | |
|---|---|
| `OptionsFeed` | Chains, IV, OI, greeks. Read-only, no order entry. |
| `Book` | The agent's simulated account: positions, cash, margin, daily marks. |
| `Settlement` | Daily P&L per plan, readable by `recall`. |

## Notes

`FLAT` must stay a valid answer or the topic rewards trading every day regardless of edge. It should
also be scored as a real choice — an agent that sits out a bad week beats one that grinds through it.
