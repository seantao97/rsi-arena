# Crypto hourly contracts

Hourly BTC and ETH strike contracts — *"BTC above $94,000 at 15:00 ET"* — plus daily range brackets.
Contracts settle every hour against the reference index. Nothing else on the roster settles this fast.

Running example: **BTC strike ladder expiring 15:00 ET, spot 93,780 at 14:20.**

## Objective

> For a given hourly ladder, take the position with positive expected value at the available price, or
> pass.

**Asked of the reader** — *"Which of these would you rather have traded?"* Winner or tie, then one tap
for why: volatility estimate, ladder consistency, price, or risk.

## What the edge actually is

For an hourly contract, fair value is close to a closed-form function of spot, time remaining and
volatility. There is very little to forecast — nobody knows where BTC will be in forty minutes.

The edge is in three places, and the topic is scoped to them:

1. **A better volatility estimate** than the one implied by the ladder.
2. **Ladder inconsistency** — P(above $X) must fall as X rises, and butterflies must be non-negative.
   Violations are arbitrage and need no view at all.
3. **Staleness** — the ladder lagging a spot move.

An agent that writes a directional thesis about Bitcoin has misunderstood the topic.

## Budget and scoring

| | |
|---|---|
| Bank | $25,000 |
| Max stake | 3% of bank per ladder, 10% per hour |
| Markets | Hourly strike ladders on BTC and ETH; daily range brackets |
| Decision time | Any time while open. Timestamp recorded. |
| Scored by | Settled P&L, ROI, **Brier score**, and calibration curve across strikes |

Volume makes this the best instrument on the roster: roughly 24 settlements per asset per day per
ladder. A week here produces more settled decisions than earnings produces in a year. If you want to
know whether preference tracks profit, measure it here first.

## Answer contract

| | |
|---|---|
| **Position** | Strike, side, contracts, limit price. Or `PASS`. |
| **Fair value** | The model probability for that strike, with the volatility input named. |
| **Volatility** | The estimate used, its method and window, and why it differs from implied. |
| **Ladder check** | Whether the ladder is monotone and convex, and any violation found. |
| **Edge** | Model probability minus market price, in points, after fees. |
| **Risk** | Worst case, and exposure if spot gaps. |
| **Falsifier** | *"If realised vol over the next 20 minutes exceeds 40 annualised, this is wrong."* |

## Primitives

### Price and microstructure

| | |
|---|---|
| `index_price(asset) → price` | The reference index the contract actually settles against — not whatever a random exchange prints. |
| `orderbook(venue, pair) → book` | Depth on the major spot venues. |
| `trades(asset, window) → ticks` | Recent prints, for realised vol and flow. |
| `microstructure(asset, window) → features` | Order-flow imbalance, trade intensity, spread. |

### Volatility

| | |
|---|---|
| `realized_vol(asset, window, method) → σ` | High-frequency realised vol. Method and window named — a 5-minute and a 1-hour estimate disagree, and which one is right is the whole question. |
| `implied_vol(asset, tenor) → σ` | Deribit implied vol at the nearest tenor. |
| `option_chain(asset, expiry) → strikes` | Deribit chain. |
| `implied_distribution(chain) → pdf` | Risk-neutral density from option strikes. The market's own answer, in the same shape as the ladder. |
| `price_touch(spot, strike, σ, t) → P` | Closed-form probability of finishing above a strike. The fair-value workhorse. |

### Positioning

| | |
|---|---|
| `funding_rate(asset) → rate` | Perp funding. Extreme funding precedes squeezes. |
| `open_interest(asset, venue) → oi` | Leverage in the system. |
| `liquidations(asset, window) → events` | Recent cascades, which is what makes hourly tails fat. |
| `macro_calendar(window) → events` | CPI and FOMC land inside single hourly contracts and break the diffusion assumption entirely. |

### Ladder

| | |
|---|---|
| `strike_ladder(event) → strikes` | Every strike in the expiry with its price. |
| `check_coherence(ladder) → violations` | Monotonicity across strikes and butterfly convexity. Pure arbitrage when it fails. |
| `read_market(ticker) → book` | Bid, ask, depth per strike. |
| `closing_line(ticker) → price` | Final price before settlement. |

### Decision and shared

| | |
|---|---|
| `cost_model(ticker, side, size) → costs` | Fee is `ceil(0.07·P·(1−P))` capped at $0.035 — largest at 50¢, near zero in the tails, which is where these ladders live. |
| `breakeven(price, costs) → threshold` | |
| `kelly(p, price, fraction) → stake` | Fractional Kelly. |
| Shared | `compute` · `run_code` · `estimate` · `calibrate` · `sensitivity` · `recall` · `remember` · `counter` · `cite` · `draft` · `critique` |

## Runtime additions

| | |
|---|---|
| `CryptoFeed` | Index price, spot books, trades, funding, open interest. Read-only. |
| `DeribitFeed` | Option chains and implied vol. Read-only. |
| `Bank` | Simulated bankroll and open positions. |
| `Settlement` | Hourly outcomes, readable by `recall`. Calibration data accumulates fast here. |

## Notes

**Fee shape decides where to trade.** The fee peaks at 50¢ and approaches zero in the tails, so
near-the-money contracts need a large edge to clear costs while far strikes need very little. Expect
good agents to live in the tails.

**Research primitives are nearly useless here** and are deliberately absent — no `search`, no `fetch`.
Forty minutes of news does not move a settled probability as much as a better vol estimate does. This is
the one topic that is pure modelling.
