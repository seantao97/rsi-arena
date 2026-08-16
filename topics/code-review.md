# Code review

Given a diff against a repository, find what is wrong with it.

Running example: **a 340-line PR adding a retry wrapper around an HTTP client.**

## Objective

> Given a diff and the repository it applies to, produce the review a maintainer would rather have
> received.

**Asked of the reader** — *"Which of these reviews would you rather have gotten on your PR?"* Winner or
tie, then one tap for why: correctness, coverage, prioritisation, or clarity.

## The reproduce loop

A review finding is a claim about behaviour, and claims about behaviour are testable.

Every correctness finding carries a failure scenario. A verifier turns that scenario into a test and
runs it twice — against the diff, and against the diff plus the proposed fix.

| Test on diff | Test on fix | Verdict |
|---|---|---|
| fails | passes | **Confirmed** |
| passes | passes | **False positive** |
| fails | fails | Fix is wrong; finding may still be real |

That converts an unverifiable claim into a verified one automatically, which is the arena's own thesis
turned on itself. Style and design findings stay unverifiable and are judged by preference.

## Scoring

| | |
|---|---|
| Source | Real merged PRs with their follow-up bug fixes, plus synthetic diffs with planted defects |
| Decision time | Within the answer budget; wall-clock recorded |
| Scored by | **Confirmed findings**, false-positive rate, catch rate on planted defects, and preference on the unverifiable half |

False-positive rate is scored explicitly. A review that flags twenty things to catch one is worse than
one that flags two, and without penalising noise the loop converges on shotgun reviews.

## Answer contract

| | |
|---|---|
| **Findings** | Ranked by severity. Each with `file:line`, a one-line claim, and a category: correctness, security, performance, or design. |
| **Failure scenario** | For every correctness or security finding — concrete inputs or state, and the wrong output or crash that results. Vague findings are rejected at submission. |
| **Fix** | A minimal patch for each. |
| **Blast radius** | What else calls this, and what breaks if the claim is right. |
| **Verdict** | Approve, approve with comments, or request changes. Reviews without a verdict are not reviews. |
| **Not flagged** | One thing that looks wrong and is not, with the reason. |

## Primitives

### Reading

| | |
|---|---|
| `read_diff(pr) → hunks` | The change, with surrounding context. |
| `read_file(path, range) → text` | Anything in the repository at the PR's base commit. |
| `repo_search(pattern, scope) → hits` | Grep across the tree. |
| `call_graph(symbol) → callers, callees` | Who calls this and what it calls. The blast-radius primitive. |
| `blame(path, range) → history` | Why this line exists. A guard added in a bug fix three years ago is not dead code. |
| `similar_code(snippet) → matches` | Has this pattern been written, and fixed, elsewhere in the tree? |

### Checking

| | |
|---|---|
| `run_tests(selector) → results` | The existing suite, or a subset. |
| `write_test(scenario) → test` | Turns a failure scenario into an executable test. **The primitive that makes findings verifiable.** |
| `reproduce(test, revision) → result` | Runs a test at a given revision. |
| `type_check(paths) → errors` | Static types where the language has them. |
| `lint(paths) → findings` | So the agent does not spend model capacity on what a linter already knows. |
| `coverage(paths) → report` | Whether the changed lines are tested at all. |
| `dependency_check(manifest) → advisories` | Known CVEs and version drift in anything the diff adds. |

### Shared

`compute` · `run_code` · `recall` · `remember` · `counter` · `cite` · `draft` · `critique`

## Runtime additions

| | |
|---|---|
| `RepoStore` | Checkouts pinned at the PR base commit. |
| `TestRunner` | Sandboxed execution with a time and memory cap, backing `run_tests` and `reproduce`. |
| `DefectSet` | Planted defects and known follow-up fixes for scoring. Not readable by agents. |

## Notes

**`lint` and `type_check` are in the set deliberately.** An agent that spends its budget rediscovering
what a linter reports is misallocating, and giving it the cheap tool makes that a choice the optimizer
can learn rather than a trap.

**The interesting tension is depth against noise.** Confirmed findings reward digging; the
false-positive penalty punishes guessing. Where a harness lands between those is exactly the thing worth
measuring.
