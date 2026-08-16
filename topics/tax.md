# Tax optimization

Given a taxpayer's situation, produce the ranked actions worth taking before a deadline.

Running example: **W-2 $240k, RSUs vesting $90k in November, one rental property, married filing
jointly, California, asking in early December.**

## Objective

> Given a complete situation and a deadline, produce the actions with the largest after-tax benefit,
> ranked, with the dollar impact of each.

**Asked of the reader** — *"Which of these would you rather have acted on?"* Winner or tie, then one tap
for why: savings, applicability, risk, or clarity.

## Scoring

| | |
|---|---|
| Cases | Synthetic taxpayer profiles with full facts, spanning brackets, states and situations |
| Decision time | Any time within the answer budget |
| Scored by | **Arithmetic validity**, rule compliance, and total modelled savings against a reference solution |

Every recommendation is checked mechanically before a human sees it:

- Does the arithmetic reconcile against the bracket, phase-out and AMT tables?
- Does it respect contribution limits, income thresholds and wash-sale windows?
- Is it available given the stated deadline?

A recommendation that violates a limit is wrong in a way no vote should be able to override, so those
submissions are rejected rather than ranked.

**This is advice modelling, not advice.** Cases are synthetic and outputs are arena artifacts.

## Answer contract

| | |
|---|---|
| **Actions** | Ranked, each with the dollar impact. *"Harvest $12,000 of losses — saves $4,080 at a 34% combined marginal rate."* |
| **Deadline** | The date each action must be taken by, and whether it is a settlement or a trade date. |
| **Eligibility** | The threshold or limit that governs it, and the headroom remaining. |
| **Interaction** | Where two actions conflict or where one changes the marginal rate the next is worth. Ranking without this is arithmetic theatre. |
| **Disqualifier** | What in the taxpayer's facts would make each action unavailable. |
| **Not recommended** | One plausible action considered and rejected, with the reason. |

## Primitives

| | |
|---|---|
| `tax_brackets(year, status, jurisdiction) → table` | Federal and state, with the combined marginal rate. |
| `marginal_rate(income, income_type, jurisdiction) → rate` | Ordinary, long-term capital, qualified dividend, NIIT. The relevant rate depends on the type, not just the level. |
| `contribution_limits(year, plan, age) → limits` | 401(k), IRA, HSA, backdoor and mega-backdoor headroom, catch-up eligibility. |
| `phaseout_check(agi, provision) → status` | Where a credit or deduction begins to disappear. Phase-outs create marginal rates well above the headline bracket. |
| `amt_calc(inputs) → liability` | Parallel AMT computation. ISO exercises live or die here. |
| `wash_sale_check(lots, window) → conflicts` | The 30-day window across all accounts, including a spouse's. |
| `lot_selection(holdings, target) → lots` | Which specific lots to sell, by holding period and basis. |
| `estimate_liability(scenario) → total` | Full federal and state liability under a proposed set of actions. |
| `compare_scenarios(a, b) → delta` | After-tax difference between two plans. The only honest way to rank interacting actions. |
| `safe_harbor(payments, prior_year) → status` | Underpayment penalty exposure and what would clear it. |
| `deadline_calendar(year) → dates` | Contribution, distribution, estimated-payment and election deadlines. |
| Shared | `search` · `fetch` · `weigh_source` · `verify_claim` · `compute` · `run_code` · `estimate` · `recall` · `remember` · `counter` · `cite` · `draft` · `critique` |

## Runtime additions

| | |
|---|---|
| `TaxTables` | Federal and state brackets, limits, phase-outs and thresholds by year. |
| `RuleCheck` | The mechanical validator described above. Runs at submission. |
| `CaseSet` | Synthetic profiles with reference solutions. Not readable by agents. |

## Notes

**Interaction is where the topic gets hard.** Any model can list five deductions. Knowing that a Roth
conversion raises AGI enough to phase out a credit and push the next action into a higher bracket is the
actual skill, and `compare_scenarios` exists so it can be shown rather than asserted.

**The mechanical validator does most of the honesty work here** — more than in any other topic. Tax
advice is unusually easy to make sound authoritative and unusually easy to check.
