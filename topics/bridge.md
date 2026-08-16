# Contract Bridge

Agents are given a deal in progress — mid-auction or mid-play — and produce the call or the card, with
the inference behind it.

Running example: **Teams, both vul, hero holds ♠AK32 ♥K7 ♦QJ96 ♣A54, partner opens 1♥, RHO overcalls 2♣.**

## Partly verifiable

Double-dummy analysis is exact, and par contract is computable, so the *outcome* of a line can be
checked. Bidding judgment and single-dummy play cannot — declarer plays without seeing the hands, and
the best single-dummy line often differs from the double-dummy one.

That split is what makes bridge useful: the play half can be scored, the bidding half cannot, and both
sit in one topic. It is the cleanest available test of whether readers judge the unverifiable half
consistently with how they judge the verifiable half.

## Objective

> Given a deal in progress, produce the call or card and the reasoning a strong partner would most want
> to have seen.

**Asked of the reader** — *"Which of these is the better analysis of the deal?"* Winner or tie, then one
tap for why: action, inference, plan, or alternatives.

## Answer contract

| | |
|---|---|
| **Action** | One call or one card. |
| **System** | The agreed system and the meaning of the auction so far under it. A bid means nothing without this. |
| **Inference** | What each call or card has shown, as constraints on shape and strength. |
| **Plan** | For play: the line, entries, timing, and what happens if the key suit breaks badly. |
| **Odds** | Probability of making, or expected tricks. |
| **Alternative** | The next-best action and why it was rejected. |
| **Falsifier** | *"If trumps are 4-1, this line fails and the safety play is better."* |

## Primitives

### Deal state

| | |
|---|---|
| `parse_deal(pbn \| lin) → state` | Standard formats to structured state: hands seen, auction, played tricks, vulnerability, form of scoring. |
| `hand_eval(hand) → metrics` | HCP, shape, losing-trick count, quick tricks, controls. |
| `legal_actions(state) → [action]` | Legal bids or legal cards. |

### System and inference

| | |
|---|---|
| `system(convention_card) → rules` | SAYC, 2/1, Precision, plus agreed conventions. Everything downstream depends on this. |
| `bid_meaning(auction, system) → constraints` | What the auction shows so far, per seat. |
| `infer_hands(state, system) → distributions` | Constraints on the three unseen hands from bidding and cards played. |
| `deal_sample(constraints, n) → deals` | Monte Carlo deals consistent with every inference. The core primitive — single-dummy play is sampling. |

### Evaluation

| | |
|---|---|
| `double_dummy(deal, contract) → tricks` | Exact tricks with all hands visible. |
| `par(deal) → contract, score` | Par contract and score for the deal. |
| `simulate_play(line, samples) → make_rate` | Runs a line across sampled deals. Single-dummy, which is the real question. |
| `suit_combination(holding, missing) → best_play` | Standard percentages for playing a suit in isolation. |
| `squeeze_check(state) → available` | Squeezes, endplays and throw-ins present in the position. |

### Scoring

| | |
|---|---|
| `score(contract, tricks, vul, form) → points` | Duplicate scoring, vulnerability-aware. |
| `imp_ev(action, alternatives, field) → imps` | IMP and matchpoint strategy differ sharply — an overtrick is worth little at IMPs and everything at pairs. |

### Output

| | |
|---|---|
| `opponent_model(pair, history) → tendencies` | Style, aggression, conventions in practice. |
| `compute` · `run_code` · `recall` · `remember` · `counter` · `draft` · `critique` | Shared. No web research. |

## Runtime additions

| | |
|---|---|
| `DDSolver` | Double-dummy engine with a per-call time cap. |
| `DealDB` | Archived deals and results for opponent modelling and base rates. |
| `Verifier` | Scores play decisions against double-dummy and par. **Not a rating input** — it feeds the preference-vs-correctness comparison only, and only on the play half. |

## Notes

Bidding is a communication protocol under a partnership agreement, not a solo optimisation. An agent
that finds a technically superior call outside the agreed system has made an error, and the `system`
primitive exists so that this is checkable rather than a matter of taste.
