# Texas Hold'em

Agents are given a hand state and produce the action plus the reasoning behind it.

Running example: **$2/$5, 200bb effective, hero BTN with A♠Q♠, flop K♠7♠2♦, villain leads 2/3 pot.**

## Verifiable, deliberately

A solver gives near-ground-truth on the action. By the usual rule this disqualifies a topic — if a
scorer exists, use it.

It is here for the opposite reason. Because EV loss is computable, **poker is the best instrument for
checking whether reader preference tracks correctness.** Run the arena, then ask how often the
preferred answer was the higher-EV one. If preference and solver EV diverge, that is the clearest
evidence available that the arena rewards persuasion. No other topic can measure this directly.

Keep it out of the public leaderboard. It is a test harness.

## Objective

> Given a hand state, produce the action and analysis a strong player would most want to have read
> before acting.

**Asked of the reader** — *"Which of these is the better analysis of the spot?"* Winner or tie, then one
tap for why: action, range work, EV, or exploitative read.

## Answer contract

| | |
|---|---|
| **Action** | One action with a size. *"Raise to $340."* |
| **Ranges** | Hero's range in this line and villain's range for the bet, as combos. |
| **EV** | EV of the chosen action and of the two nearest alternatives. |
| **Baseline** | The solver-approximate play, if it differs from the recommendation. |
| **Exploit** | Any deviation from baseline, and the read justifying it. |
| **Falsifier** | *"If villain never bluffs this texture, calling is wrong."* |

## Primitives

### State

| | |
|---|---|
| `parse_hand(history) → state` | Hand history to structured state: positions, stacks, board, action sequence, pot. |
| `board_texture(board) → features` | Connectedness, suitedness, pairing, high-card structure. |
| `pot_odds(pot, bet) → required_equity` | Trivial and worth not doing in-head. |
| `combo_count(range, filter) → n` | How many nutted combos are actually in the range. Most range errors are counting errors. |

### Ranges and equity

| | |
|---|---|
| `assign_range(action_sequence, profile) → range` | Infers a villain range from the line and player type. |
| `equity(hand, range, board) → pct` | Exhaustive or Monte Carlo. |
| `range_vs_range(r1, r2, board) → equity` | Distribution of equity, not just the mean. |
| `solver(spot, abstraction) → strategy` | Approximate GTO baseline for the spot. |
| `ev(action, ranges, board) → value` | EV in chips for a candidate action. |
| `bet_size_grid(spot) → candidates` | The sizes worth evaluating, so the agent does not evaluate one. |

### Opponents and format

| | |
|---|---|
| `opponent_model(player_id, history) → tendencies` | VPIP, PFR, aggression frequency, fold-to-cbet, showdown tendencies. |
| `exploit(baseline, tendencies) → deviation` | The adjustment and its EV gain against the modelled opponent. |
| `icm(stacks, payouts) → adjustment` | Tournament equity. Chip EV and $EV differ, sometimes by the whole decision. |
| `simulate_hand(strategy, draws) → distribution` | Outcome distribution over runouts. |

### Output

| | |
|---|---|
| `compute` · `run_code` · `recall` · `remember` · `counter` · `draft` · `critique` | Shared. No web research — everything needed is in the state. |

## Runtime additions

| | |
|---|---|
| `Solver` | Preflop and postflop approximations with a bounded abstraction and a time cap. |
| `HandDB` | Historical hands for opponent modelling. |
| `Verifier` | Scores the submitted action's EV loss against the solver. **Not a rating input** — it feeds the preference-vs-correctness comparison only. |

## Notes

The interesting failure is a memo with immaculate range notation that recommends a clearly -EV action.
Readers who play will catch it; readers who do not, will not. That gap is measurable here and is worth
measuring, because it is the same gap that exists silently in every other topic.
