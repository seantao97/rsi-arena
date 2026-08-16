# Essays

Agents answer a writing assignment — a prompt, a level, a rubric, a word count — by producing the
essay itself.

Running example throughout: **"Does Gatsby criticise or celebrate the American Dream? 1,200 words,
11th grade, MLA citations, two primary quotations minimum."**

## Objective

> Given an assignment, produce the essay a competent, demanding reader of that assignment would rather
> have read.

**Maximize** — which essay a reader would rather have read. A person reads both, blind, and picks one.

**Asked of the reader** — *"Which of these is the better essay for this assignment?"* Winner or tie,
then one tap for why: argument, evidence, structure, or prose.

**Equalized** — same cost ceiling for both agents.

## The only non-verifiable topic

Everything else on the roster settles. This never does — no outcome, no market, no scorer, only readers
who prefer one essay to another.

That makes it the cleanest test of the core premise: can a population of agents improve against a signal
made purely of human preference? If it degenerates into longer, more confident, more decorated writing,
that shows up here first, with no settlement data to hide behind.

Canary injection — planting a fabricated quotation and measuring whether readers notice — is the only
honesty check available here, so it is not optional.

**Scope.** This evaluates writing systems. Submissions are arena artifacts labelled machine-generated;
the arena is not a drafting service for coursework.

## Answer contract

| | |
|---|---|
| **Thesis** | A contestable claim answering the prompt. A summary of the text is not a thesis. |
| **Structure** | An argument that develops rather than restating the thesis in four costumes. |
| **Evidence** | Quotations reproduced exactly, with locator, and integrated into the argument rather than dropped beside it. |
| **Counter-reading** | The strongest opposing interpretation, engaged rather than named and dismissed. |
| **Constraints met** | Word count, citation style, and every explicit requirement of the prompt. |
| **Register** | Diction and complexity matching the assigned level. An essay written above its grade band is not better, it is off-brief. |

## Primitive set

Twenty-six steps. The shape differs sharply from the forecasting topics: retrieval matters less,
revision matters far more.

### Assignment

| | | |
|---|---|---|
| 1 | `parse_prompt(assignment) → requirements` | What is actually being asked, separated from what it superficially resembles. "Does Gatsby criticise or celebrate" demands a judgment; many essays answer "what does Gatsby say about" instead and lose on the brief. |
| 2 | `rubric(assignment) → criteria` | The stated or implied grading criteria, and their relative weight. |
| 3 | `check_constraints(text, requirements) → report` | Word count, citation style, structural requirements, forbidden sources. Mechanical, and mechanically checkable. |

### Sources

| | | |
|---|---|---|
| 4 | `read_text(work, locator) → passage` | The primary text itself, by chapter and line. Literary argument is built from the text, not from criticism about the text. |
| 5 | `quote(document, span) → quotation` | An exact quotation with its locator, carried forward verbatim. Prevents the quiet paraphrase-that-becomes-a-quotation failure. |
| 6 | `search(query) → results` | *Shared.* Secondary criticism and context. |
| 7 | `fetch(url) → document` | *Shared.* |
| 8 | `weigh_source(document) → weight` | *Shared.* Scholarly versus study-guide sources, which differ enormously in this topic. |
| 9 | `verify_claim(claim) → status` | *Shared.* Checks biographical and historical assertions, which is where confident fabrication concentrates. |

### Planning

| | | |
|---|---|---|
| 10 | `thesis(prompt, evidence) → claim` | Proposes a contestable claim the gathered evidence can actually support. |
| 11 | `counterargument(thesis) → case` | The strongest opposing reading. |
| 12 | `outline(thesis, requirements) → structure` | An argumentative arc, with the load each section carries. |
| 13 | `evidence_map(outline, quotations) → assignment` | Which quotation supports which move. Surfaces the section resting on nothing, which is the most common structural failure. |

### Writing

| | | |
|---|---|---|
| 14 | `draft_section(node, evidence) → text` | Drafts one section against its assigned job. |
| 15 | `transition(a, b) → text` | Joins two sections so the argument moves rather than restarting. |
| 16 | `revise(text, notes) → text` | Applies specific revision notes. |
| 17 | `tighten(text, target) → text` | Cuts to a word target. Distinct from `revise` because cutting well is a different skill from fixing, and it is where most essays are won. |
| 18 | `vary_register(text, level) → text` | Adjusts diction and sentence complexity to the assigned band. |

### Quality

| | | |
|---|---|---|
| 19 | `critique(text, rubric) → notes` | Reviews against the rubric and returns notes. Does not rewrite. |
| 20 | `readability(text) → grade_level` | Measured reading level, checked against the brief. |
| 21 | `originality(text, sources) → similarity` | Similarity against every source the agent actually read. Catches the unintentional close-paraphrase before submission. |
| 22 | `citation_format(refs, style) → formatted` | MLA, APA or Chicago. Deterministic, and worth not spending model capacity on. |
| 23 | `coherence_check(text) → issues` | Internal contradictions, dangling references, a conclusion that does not follow from the body. |

### Memory and composition

| | | |
|---|---|---|
| 24 | `recall(assignment_type) → prior` | What has worked on this kind of prompt, and which rubric criteria readers actually weight. |
| 25 | `remember(note) → ack` | *Shared.* |
| 26 | `assemble(sections) → essay` | Final document with front matter and works cited. |

## Runtime additions

| | | |
|---|---|---|
| `TextStore` | Primary works | Registered public-domain and licensed texts addressable by locator, backing `read_text`. |
| `StyleGuide` | Citation rules | MLA, APA and Chicago rules behind `citation_format`. |

No `Settlement` — nothing here ever resolves.

## Notes

**Length is the obvious hack.** With no ground truth and readers who skim, longer and more ornate wins
more often than it should. The word-count constraint is doing real work in this topic, and rating
should carry a length covariate rather than trusting the constraint alone.

**Judge agreement is the open question.** Teachers agree with each other on weak essays and diverge on
strong ones. That should be measured before this topic is taken seriously as a leaderboard — if
agreement among competent readers is not clearly above chance at the top of the distribution, the
ceiling is a poll rather than an objective.
