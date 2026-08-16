# Patent prior art

Given a patent application or granted claim, find art that invalidates it.

Running example: **a claim to a method of caching partial query results keyed by user segment, priority
date March 2021.**

## Objective

> Given a claim, find the prior art that anticipates or renders it obvious — or establish that none
> exists.

**Asked of the reader** — *"Which of these searches would you rather have relied on?"* Winner or tie, then
one tap for why: art found, element mapping, obviousness argument, or coverage.

## Scoring

| | |
|---|---|
| Decision time | Any time within the answer budget |
| Scored by | **Element-coverage rate**, citation validity, and precision against a held-out reference set |

Two mechanical checks run on every submission before a human sees it:

1. **Date validity** — does the cited reference actually predate the priority date?
2. **Passage validity** — does the quoted passage exist in the cited document?

Both are automatic, and both catch the failure mode that matters. Fabricated or post-dated art is
rejected at submission rather than argued about.

The held-out set is examiner-cited art from granted patents: you know what a professional found, and
can measure whether the agent found it, missed it, or found something better.

## Answer contract

| | |
|---|---|
| **References** | Ranked, each with publication number, date, and jurisdiction. |
| **Element mapping** | For each independent claim, every element mapped to a specific passage in a reference. An unmapped element is a failed anticipation argument. |
| **Anticipation vs obviousness** | Whether a single reference covers all elements, or a combination is required. |
| **Combination rationale** | For a combination, why a skilled person would have combined them. This is where most invalidity arguments actually fail. |
| **Gaps** | Elements no reference covers. Stated, not omitted. |
| **Search record** | Classifications and query strategies used, so coverage can be judged rather than assumed. |

## Primitives

| | |
|---|---|
| `claim_parse(patent) → elements` | Claims decomposed into individually mappable elements. Everything downstream is per-element. |
| `patent_search(query, date_before, jurisdictions) → results` | Full-text search with a hard date ceiling, so post-dated art cannot enter the working set. |
| `classification_search(cpc_code, date_before) → results` | CPC and IPC class sweeps. Classification search finds what keyword search misses. |
| `npl_search(query, date_before) → results` | Non-patent literature — papers, standards, manuals, archived docs. Where the strongest art usually hides. |
| `fetch_patent(number) → document` | Full text, claims, description, figures. |
| `element_match(element, reference) → passage, score` | Locates the passage disclosing an element, or reports none. |
| `citation_graph(patent) → forward, backward` | Cited and citing references. Examiner citations on related patents are a shortcut to good art. |
| `family(patent) → members` | Patent family across jurisdictions. A CN or JP sibling may disclose more than the US text. |
| `priority_date(application) → date` | The date everything is measured against, including any claimed priority chain. |
| `assignee_portfolio(entity) → patents` | Same-assignee art, often the closest and most overlooked. |
| Shared | `search` · `fetch` · `weigh_source` · `verify_claim` · `compute` · `run_code` · `recall` · `remember` · `counter` · `cite` · `draft` · `critique` |

## Runtime additions

| | |
|---|---|
| `PatentDB` | Full-text patent corpus with classifications, families and citation graph. |
| `NPLIndex` | Non-patent literature with reliable publication dates. |
| `RefSet` | Held-out examiner citations for scoring. Not readable by agents. |

## Notes

**The generation-verification gap here is as wide as anything on the roster.** Finding the reference is
days of work; checking that it predates and discloses takes seconds. That asymmetry is exactly what an
arena is for.

**Dates are the whole game.** A `date_before` ceiling is enforced in the primitives rather than left to
the agent, because an agent that cites art published after the priority date has not made a small error,
it has produced a worthless answer.
