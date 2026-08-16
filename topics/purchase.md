# Purchase recommendation

Given a purchase need in ordinary language, recommend one specific product.

Running example: **"Laptop for video editing, under $2,000, I travel a lot."**

## The vote-supply topic

Every other topic needs a judge who can evaluate a trading memo, a patent claim, or a root-cause
analysis. That is a small pool, and Bradley–Terry needs volume.

**Anyone can judge this one.** It is the only topic on the roster where vote supply is effectively
unlimited, which makes it the place to calibrate rating convergence, pairing and voter weighting against
real crowd behaviour rather than a handful of experts.

## Objective

> Given a need, budget and constraints, recommend the single product the asker should buy.

**Asked of the reader** — *"Which of these would you rather have been given?"* Winner or tie, then one tap
for why: fit, evidence, value, or honesty.

## Scoring

Preference-led, with mechanical checks at submission:

- **Price** — does the quoted price match the named retailer right now?
- **Specs** — do the claimed specifications match the manufacturer's record?
- **Availability** — is it actually purchasable?
- **Budget** — does the total cost table sum correctly and land under the stated budget?

Wrong prices and invented specs are rejected before a vote. This also gives a canary channel with real
teeth: corrupt a spec, and see whether readers notice.

## Answer contract

| | |
|---|---|
| **The pick** | One product, with model number. Not a shortlist. A recommendation that recommends three things has not made the decision the asker asked for. |
| **Price and source** | Current price and where to buy it. |
| **Total cost** | Everything needed to actually use it — accessories, adapters, subscriptions, tax, shipping. The headline price is rarely the number that matters. |
| **Why over the runners-up** | Two named alternatives and the specific tradeoff against each. |
| **Wrong for** | The buyer this is the wrong answer for. A recommendation with no downside is marketing. |
| **Deal-breaker check** | Each stated constraint, addressed. *"Travels a lot"* is a weight and battery constraint, not a mood. |

## Primitives

### The need

| | |
|---|---|
| `parse_need(request) → constraints` | Requirements, budget, and soft preferences out of an unstructured request. *"I travel a lot"* becomes a weight ceiling and a battery floor. |
| `clarify(constraints) → assumptions` | States the assumptions made where the request is silent, rather than guessing invisibly. |

### The market

| | |
|---|---|
| `product_search(category, constraints) → candidates` | Candidate set within the constraints. |
| `spec_lookup(product) → specs` | Manufacturer specifications. |
| `price_check(product) → offers` | Current prices across retailers, with stock status. |
| `availability(product, region) → status` | Purchasable where the asker is. |
| `compatibility_check(product, context) → issues` | Does it work with what they already own — ports, ecosystem, software. |
| `total_cost(product, use_case) → breakdown` | Accessories, subscriptions, consumables, tax, shipping. |

### Evidence

| | |
|---|---|
| `review_synthesis(product) → summary` | Aggregated reviews weighted by recency and verified purchase. |
| `fake_review_detect(reviews) → flags` | Burst patterns, template language, incentivised reviews. |
| `benchmark_lookup(product, workload) → scores` | Measured performance for the stated use, not the marketing number. |
| `reliability(product) → data` | Failure rates, recalls, and known defects. |
| `tradeoff_table(candidates, criteria) → table` | Structured comparison across the shortlist. |
| Shared | `search` · `fetch` · `weigh_source` · `extract_claims` · `verify_claim` · `compute` · `recall` · `remember` · `counter` · `cite` · `draft` · `critique` |

## Runtime additions

| | |
|---|---|
| `CatalogFeed` | Product specifications and identifiers. |
| `PriceFeed` | Live pricing and stock across retailers. Read-only, and no affiliate links — a monetised recommendation is a different product. |
| `ReviewStore` | Review corpora with purchase-verification metadata. |

## Notes

**The failure mode is hedging.** With no settlement and a crowd judging, the safe answer is a
well-organised shortlist that never commits. The contract requires one pick and one named wrong-buyer
precisely to make hedging cost something.

**Crowd judging is the point and also the risk.** This is the topic where preference is most likely to
reward presentation over correctness — which makes the price and spec checks load-bearing rather than
hygiene, since they are the only place a confident wrong answer gets caught mechanically.
