# Macro nowcasting

Predict the next scheduled US data release before it prints — the headline number and the components
that matter — against consensus.

**Releases.** CPI, PCE, non-farm payrolls, GDP, retail sales, ISM manufacturing and services, JOLTS.

Running example: **CPI for last month, released Thursday 08:30 ET.**

## Objective

> For a scheduled release, produce the forecast a competent macro reader would most want to have had
> before the print.

**Asked of the reader** — *"Which of these would you rather have read before the print?"* Winner or tie,
then one tap for why: components, evidence, model, or risk framing.

## Budget and scoring

| | |
|---|---|
| Decision time | Any time up to one hour before release. Timestamp recorded. |
| Optional position | $50,000 bank, 10% max per release, on the event contract where one is listed |
| Scored by | **MAE against the actual print**, direction-versus-consensus hit rate, CRPS on the stated distribution, and P&L where a position was taken |

Direction-versus-consensus is the important one. Matching consensus is free and worthless; the question
is whether the agent knows *which side* consensus is wrong on.

## Answer contract

| | |
|---|---|
| **Headline** | Point forecast for the headline number, to the published precision. |
| **Distribution** | A distribution, not just a point. Macro prints have fat tails and consensus does not. |
| **Components** | Forecasts for the components carrying the variance — for CPI, shelter, used vehicles, medical and core services ex-shelter. |
| **Versus consensus** | Consensus median and range, and where this forecast sits in it. |
| **Drivers** | The two or three high-frequency inputs doing the work, with values. |
| **Revision risk** | Whether the prior print is likely to be revised, and which way. |
| **Falsifier** | *"If the ApartmentList index has stopped decelerating, the shelter call is wrong."* |

## Primitives

### The release

| | |
|---|---|
| `release_calendar(window) → releases` | Dates, times, reference periods, and what is published. |
| `consensus(release) → median, range, n` | Survey median, dispersion, and how many forecasters. Dispersion is the honest prior on difficulty. |
| `component_history(series) → data` | Full component tree with weights. CPI is not one number; it is a weighted sum with three components driving most of the surprise. |
| `seasonal_factors(series) → factors` | Published adjustment factors. January and back-to-school prints are seasonal-factor events as much as economic ones. |
| `revision_history(series) → data` | How this series revises. First prints of payrolls are noisy and systematically revised. |

### Leading data

| | |
|---|---|
| `high_frequency(indicator) → data` | Weekly claims, card spend, gasoline prices, container rates, mortgage applications. |
| `private_index(name) → data` | Named private series that lead official components — **ApartmentList and Zillow lead shelter CPI by six to twelve months; Manheim leads used-vehicle CPI by about two.** These are the topic's core inputs. |
| `regional_surveys(window) → data` | Empire, Philly Fed, Chicago PMI, Dallas Fed. They lead ISM. |
| `nowcast_model(series, inputs) → forecast` | Bridge or dynamic factor model in the GDPNow style. |

### Market and policy

| | |
|---|---|
| `market_implied(release) → distribution` | The distribution implied by event contracts and options on the print. |
| `read_market(ticker) → book` | The event contract, where listed. |
| `fed_speak(window) → statements` | Official commentary since the last print. Matters more for the reaction than the number. |
| `timeseries(series, window) → data` | Official series from the registry. |

### Shared

`search` · `fetch` · `weigh_source` · `extract_claims` · `verify_claim` · `compute` · `run_code` ·
`decompose` · `base_rate` · `estimate` · `calibrate` · `sensitivity` · `recall` · `remember` ·
`counter` · `cite` · `draft` · `critique`

## Runtime additions

| | |
|---|---|
| `StatsFeed` | BLS, BEA, Census and Fed releases with vintages — the number as first published, not as later revised. |
| `PrivateData` | Registry-listed private indices behind `private_index`. |
| `ConsensusFeed` | Survey medians and dispersion. |
| `Settlement` | Actual prints and revisions, readable by `recall`. |

## Notes

**Vintages are essential.** Scoring a forecast against a twice-revised number is scoring the wrong
thing, so `StatsFeed` must serve the first print. This is the one topic where getting the data layer
wrong silently invalidates the leaderboard.

**Volume is low** — roughly eight releases a month. This topic is not where arena mechanics get
calibrated. It is here because it is research-heavy, because the leading indicators are specific and
learnable, and because it feeds [event markets](event-markets.md) directly: Fed contracts price off
exactly these prints.
