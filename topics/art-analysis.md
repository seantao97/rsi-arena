# Art analysis

Given a work and an audience, produce an interpretive analysis of it.

Running example: **Manet, *A Bar at the Folies-Bergère*, 1882, for a catalogue essay.**

## The second non-verifiable topic

Like [essays](essays.md), nothing here resolves. There is no outcome and no scorer for an
interpretation — only readers who find one analysis better than another.

It earns its place beside essays because it fails differently. Essays reward argument; art writing
rewards *looking*, and its characteristic failure is fluent prose that could have been written without
seeing the work. If the arena can tell those apart, that is a stronger result than essays alone can
give.

## Objective

> Given a work and a stated audience, produce the analysis that reader would rather have read.

**Asked of the reader** — *"Which of these tells you more about the work?"* Winner or tie, then one tap
for why: looking, argument, context, or writing.

## Scoring

Preference only. But two mechanical checks run first, and they matter more here than in essays:

- **Catalogue facts** — date, medium, dimensions, collection and provenance are checked against the
  museum record. Wrong facts are rejected at submission.
- **Visual claims** — any claim about a specific region must cite a region identifier from `view`. An
  analysis that describes a gesture in the wrong corner is making it up.

Together these give a canary channel with real teeth: corrupt a catalogue fact or a described detail and
measure whether readers notice.

## Answer contract

| | |
|---|---|
| **Claim** | An interpretive thesis. A description of what is depicted is not a claim. |
| **Visual evidence** | At least three specific passages, cited by region, that the claim rests on. Composition, handling, palette, scale, or facture. |
| **Context** | Period, movement, the artist's adjacent work, and conditions of making — commission, patron, exhibition. Only what bears on the claim. |
| **Counter-reading** | The strongest alternative interpretation, engaged rather than dismissed. |
| **What would settle it** | A technical or archival finding that would decide between the readings — an underdrawing, a pigment, a letter. |
| **Register** | Matched to the stated audience: wall label, catalogue essay, or seminar paper. These are different documents. |

## Primitives

### Looking

| | |
|---|---|
| `view(work, region) → detail` | Deep zoom on a named region via IIIF. **The core primitive** — an analysis that never calls it is writing from memory of a thumbnail. |
| `composition_analysis(image) → geometry` | Lines, symmetry, focal structure, ratios. Measurable, and often contradicts received readings. |
| `colour_analysis(image, region) → palette` | Measured palette and value structure for a region. |
| `compare_works(a, b, aspect) → notes` | Side-by-side on a named aspect — handling, palette, motif, scale. |

### Record

| | |
|---|---|
| `catalogue_entry(work) → record` | Date, medium, dimensions, accession, current collection. |
| `provenance(work) → chain` | Ownership history, with gaps marked. |
| `exhibition_history(work) → shows` | Where it has hung and in what company. |
| `technical_report(work) → findings` | Published X-ray, infrared reflectography, pigment analysis. Where an interpretation meets physical evidence. |

### Context

| | |
|---|---|
| `artist_corpus(artist, filter) → works` | Adjacent works for comparison. |
| `iconography_lookup(motif) → references` | Iconclass and comparable conventions. What a motif meant to its first audience. |
| `period_context(date, region) → material` | Patronage, exhibition conditions, critical reception at the time. |
| `criticism_search(work) → texts` | Existing scholarship, so the analysis argues with it rather than reinventing it. |

### Composition and shared

`quote` · `search` · `fetch` · `weigh_source` · `verify_claim` · `outline` · `draft` · `revise` ·
`tighten` · `vary_register` · `critique` · `cite` · `recall` · `remember`

## Runtime additions

| | |
|---|---|
| `ImageStore` | IIIF-tiled images with stable region identifiers, backing `view`. |
| `MuseumData` | Catalogue records, provenance, exhibition history from participating collections. |
| `IconIndex` | Iconographic reference corpus. |

No `Settlement`. Nothing resolves.

## Notes

**Region identifiers are what make this topic possible.** Without them a visual claim is unfalsifiable
and the whole thing collapses into prose judging. With them, "the barmaid's reflection sits too far
right to be optically possible" is a checkable statement about a specific rectangle.

**Judge agreement is the open question**, as with essays. Art historians agree readily on which analysis
is careless and diverge sharply on which of two good readings is better. Measure agreement at the top of
the distribution before treating the leaderboard as meaningful.
