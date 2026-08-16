# Merger arbitrage

Announced US public M&A deals above $500m. The agent takes the spread, or passes, and says whether the
deal closes and when.

Running example: **an announced all-cash acquisition trading at a 4.2% gross spread with a second
request outstanding.**

## Objective

> For an announced deal, decide whether the spread compensates for break risk and timing, or pass.

**Asked of the reader** — *"Which of these would you rather have owned?"* Winner or tie, then one tap for
why: regulatory read, terms, timing, or downside.

## The research topic

Every other trading topic on the roster rewards modelling. This one rewards reading.

The spread is public and the maths is arithmetic. What decides the trade is whether the FTC issues a
second request, whether the financing survives, whether the vote passes, and what the target is worth
if it all falls apart — questions answered by merger agreements, filings and precedent, not by a model.
It is the deliberate counterweight to [crypto-hourly](crypto-hourly.md).

## Budget and scoring

| | |
|---|---|
| Bank | $250,000 |
| Max position | 10% of bank per deal, 25% per regulator-correlated group |
| Structure | Long target outright for cash deals; long target / short acquirer at the exchange ratio for stock deals |
| Decision time | Any time while the deal is pending |
| Scored by | Settled P&L, **Brier on P(close)**, break-prediction hit rate, and timing error in days |

Brier on P(close) is scored separately because the trade and the forecast can come apart. Collecting
spread on twenty deals that close says little; calling the one that broke says a lot.

## Answer contract

| | |
|---|---|
| **Position** | Long/short legs, ratio, size. Or `PASS`. |
| **P(close)** | Probability the deal completes on current terms, with an interval. |
| **Timing** | Expected close date, and the annualised return that implies at the current spread. |
| **Regulatory read** | Where it sits — HSR, second request, DOJ/FTC, CFIUS, EU or CMA phase — and what that historically implies. |
| **Downside** | Unaffected price, and loss if the deal breaks tomorrow. |
| **Deal terms** | Termination fee, financing conditions, MAC language, outside date, and any collar. |
| **Falsifier** | *"If the FTC sues to block, this is wrong."* |

## Primitives

### The deal

| | |
|---|---|
| `deal_terms(deal) → structure` | Cash, stock or mixed; exchange ratio; collar; conditions precedent; termination fee; outside date; MAC clause. |
| `deal_docs(deal) → filings` | Merger agreement, DEFM14A, S-4, 8-K. The primary text, not coverage of it. |
| `spread(deal) → gross, annualised` | Current gross spread and annualised return to the expected close. |
| `timeline(deal) → milestones` | Announced date, filings made, vote date, outside date, and what has slipped. |

### Regulatory

| | |
|---|---|
| `regulatory_status(deal) → status` | HSR filed, early termination, second request, consent decree talks, CFIUS, EU Phase I/II, CMA. |
| `antitrust_precedent(sector, overlap) → cases` | Comparable transactions, the theory of harm raised, and how they ended. |
| `agency_posture(agency, window) → indicators` | Recent enforcement record and public statements. Regulatory appetite shifts by administration and is the largest single risk factor. |

### Financing, holders and downside

| | |
|---|---|
| `financing_status(deal) → status` | Committed facilities, bond issuance, and any financing condition still live. |
| `shareholder_vote(deal) → schedule, status` | Record date, vote date, ISS and Glass Lewis recommendations. |
| `holders(ticker) → positions` | Arb concentration and any activist or holdout position. Appraisal risk lives here. |
| `unaffected_price(target) → price` | Pre-announcement price adjusted for sector drift. The honest downside, not the pre-announcement close. |
| `borrow(ticker) → availability, rate` | For the short leg on stock deals. A hard-to-borrow acquirer changes the trade. |

### Base rates and shared

| | |
|---|---|
| `break_history(reference_class) → rate` | Historical break rate by deal type, size, regulator and structure. The outside view, which arbs routinely skip. |
| Shared | `search` · `fetch` · `weigh_source` · `extract_claims` · `verify_claim` · `timeseries` · `compute` · `run_code` · `base_rate` · `decompose` · `estimate` · `calibrate` · `sensitivity` · `recall` · `remember` · `counter` · `cite` · `draft` · `critique` |

## Runtime additions

| | |
|---|---|
| `DealDB` | Announced deals, terms, status, and a settled history for base rates. |
| `FilingsFeed` | EDGAR, with accepted timestamps. |
| `RegulatoryFeed` | HSR notices, agency press releases, EU and CMA case pages. |
| `Bank` | Simulated bankroll and open positions. |
| `Settlement` | Outcomes and dates, readable by `recall`. |

## Notes

**The payoff is short-optionality**: many small wins, occasional large loss. An agent that maximises
hit rate will look excellent for months and then give it all back on one break. Scoring Brier and
break-prediction separately from P&L is what makes that visible before it happens rather than after.

**Correlation is regulatory, not sectoral.** Five deals in front of the same agency in the same
administration are one bet, which is why the position limit is grouped by regulator.
