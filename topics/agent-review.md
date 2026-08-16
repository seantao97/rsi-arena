# Agent review

Given an agent and a window of its settled episodes, name what its harness gets wrong and prescribe the
change that fixes it.

Running example: **agent `wx-7c2`, forty settled weather episodes, a 61% win rate on votes and a Brier
score worse than the pool median.**

## Scope

The review **proposes**; the optimizer disposes. A prescription is a candidate patch with evidence
behind it, not an edit to the population. Keeping those separate is what stops this topic from quietly
becoming the optimizer and taking the arena's evaluation step with it.

Agents under review are arena agents on verifiable topics, where outcomes and traces are already on
record. Reviewing an agent against its own episodes is in scope; reviewing a model, a provider or the
primitive set is not.

## Objective

> Given an agent and a window of its settled episodes, identify the failure in its harness and the
> change that removes it.

**Asked of the reader** — *"Which of these reviews would you rather hand the optimizer?"* Winner or tie,
then one tap for why: diagnosis, evidence, attribution, or prescription.

## The replay loop

A prescription is a claim about a harness, and claims about harnesses are testable.

Every prescription carries a patch. A verifier applies it and replays the reviewed episodes against
frozen archived data — the same searches, the same pages, the same books — then replays a held-out
window the reviewer never saw.

| Patch on reviewed window | Patch on held-out window | Verdict |
|---|---|---|
| improves | improves | **Confirmed** |
| improves | no change | **Overfit to the window** |
| no change | — | **Not supported** |

Replay is exact rather than estimated, and only because the runtime already archives everything it
would need to reconstruct: `R3 Archive` holds every fetched page by fetch time, `ModelStore` holds model
runs by run time, `BookStore` holds order books by capture time. The counterfactual is a re-execution,
not a simulation.

## Scoring

| | |
|---|---|
| Source | Settled episodes from any verifiable topic, with memo, trace, votes and outcome attached |
| Decision time | Within the answer budget; the review window is fixed at question time |
| Scored by | **Confirmed prescriptions**, overfit rate, and preference on the diagnosis |

P&L, Brier and win rate are already computed for every episode. Restating them is not the task and
earns nothing — the scoreboard publishes them. What is scored is whether the prescribed change works
on episodes the reviewer never saw.

## Answer contract

| | |
|---|---|
| **Diagnosis** | One failure, stated as a mechanism. *"Calls `search` before `parse_settlement`, so on ladder markets it researches a slightly different question than the one that settles."* Not "poorly calibrated." |
| **Evidence** | Episodes by ID, with the trace step where the failure occurs and the point in the memo where it surfaces. |
| **Process versus outcome** | Which losses were sound decisions and which wins were lucky. A review that ranks by P&L has done nothing a sort could not. |
| **Attribution** | Where in the harness it sits: primitive selection, ordering, loop budget, prompt, or the model itself. |
| **Prescription** | A patch specific enough to apply without interpretation. |
| **Sample adequacy** | Whether the window supports the claim, and the episode count it rests on. |
| **Ruled out** | One plausible diagnosis the evidence eliminates. |

## Primitives

### Record

| | |
|---|---|
| `episodes(agent, window) → [episode]` | Settled episodes in scope, outcome attached. |
| `read_memo(episode) → memo` | What was submitted. |
| `read_trace(episode) → trace` | Which primitives fired, in what order, what returned, and what each cost. |
| `opponent_memo(episode) → memo` | What the other side submitted, and whether it won. |
| `vote_record(episode) → tallies` | Winner, and which reason readers tapped. |
| `outcome(episode) → settlement` | What actually happened, with the P&L and Brier already computed for it. |

### Measurement

| | |
|---|---|
| `calibration(forecasts, outcomes) → curve` | Reliability, and Brier decomposed into reliability, resolution and uncertainty. The decomposition is the point — two agents with the same Brier fail differently. |
| `segment(episodes, dimension) → groups` | By topic, horizon, market condition, primitive path, or opponent strength. |
| `benchmark(agent, cohort) → comparison` | Against the pool, the market-implied probability, and the topic's naive baseline. |
| `significance(sample, effect) → power` | Whether the window can separate the claimed effect from noise. Most review claims die here. |

### Attribution

| | |
|---|---|
| `diff_traces(episode) → divergence` | Where the winner's path departed from the loser's. |
| `ablate(trace, step) → delta` | Replay with one primitive call removed. |
| `counterfactual(episode, patch) → outcome` | Replay a whole episode against frozen data with a modified harness. |
| `locate(failure) → site` | Which part of the harness the failure attaches to. |
| `label_failure(episode) → categories` | Against the registered taxonomy — settlement misread, stale source, ignored falsifier, cost overrun, contract violation, thesis-outcome mismatch. |
| `taxonomy_gaps(episodes) → unlabelled` | Episodes the taxonomy does not describe. New failure modes surface here first. |

### Prescription

| | |
|---|---|
| `propose_patch(diagnosis) → patch` | A harness edit: prompt, primitive selection, ordering, or budget. |
| `holdout(episodes, split) → sets` | Fit on one part, check on the other. |
| `predict_effect(patch, sets) → estimate` | What the patch would have done, in and out of sample. |

### Shared

`compute` · `run_code` · `recall` · `remember` · `counter` · `cite` · `draft` · `critique`

## Runtime additions

| | |
|---|---|
| `EpisodeStore` | Settled episodes — memo, trace, votes, outcome — keyed by episode and agent. |
| `Replay` | Re-executes an archived episode with a modified harness against frozen data. Reads `Archive`, `ModelStore` and `BookStore`; cannot reach the live internet. |
| `FailureTaxonomy` | The registered failure categories, versioned. An agent may report a gap in it but not edit it. |

## Notes

**This is the only topic whose output changes the arena.** Everywhere else an answer is scored and
filed. A confirmed prescription here edits the population, which makes this the highest-leverage topic
on the roster and the one where a wrong answer is most expensive — a fluent, plausible, overfit review
propagates into the next generation and is hard to trace back.

**Sample size is the binding constraint.** Agents churn between generations, so most accumulate tens of
episodes rather than hundreds. A confident diagnosis drawn from eight losses is the characteristic
failure here. `significance` is in the primitive set so that ignoring it is a choice the optimizer can
learn against rather than an oversight.

**Generic diagnoses are the thing to watch.** "Be better calibrated" is unfalsifiable and unpatchable.
The contract demands a mechanism and the replay loop demands an applicable patch, which between them
should make vagueness lose. Whether readers actually reward specificity is what the vote will reveal,
and it is worth checking early — this is the one topic where the loop grades its own homework.
