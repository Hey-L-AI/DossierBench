# Failure modes of the SUTs — a transcript-grounded catalog

## The paper's catalogue: nine runs on the released key (2026-10-20)

This is the catalogue behind §7.1 ("What the systems get wrong") and Appendix F of the paper.

**Runs.** The nine runs of [`SUT-RESULTS.md`](SUT-RESULTS.md), each on every checkpoint of all 50
subjects: `prompted` and `notebook` on Gemini 3.7 Flash, GPT-5.6 Luna, DeepSeek V4 Pro and GLM-5.3
Flash, and `memory` on GPT-5.6 Luna (all with the v2 prompts).

**Key.** The released answer key (base `n50-tagfix-2026-10-15`; see
[`provenance/CANONICAL.md`](provenance/CANONICAL.md)).

**Procedure.**
1. *Recover the charged cells.* Every cell the scorer charged (a committed preference scoring below
   0.5, a fabrication, or a missed or invented constraint element) is recovered by replaying the
   cached outputs against the key. An `unknown` on a STRONG preference field is not included.
2. *Group them into episodes.* Cells are grouped by (system, backbone, subject, field, element); a new
   episode opens when checkpoints are not consecutive or the emitted value changes. **807 episodes.**
3. *Read each distinct case once.* Episodes collapse to distinct cases (subject, field, element,
   emitted value, gold value, first checkpoint). Failure readers (Claude Opus 5.5, shown neither the
   system nor any earlier verdict) read the full served transcript and label each case GENUINE,
   KEY_ARTIFACT or AMBIGUOUS under the key's written evidence policy
   ([`GT-POLICIES.md`](GT-POLICIES.md)), with a supporting quote, the failure mode, and a flag where
   they doubted the gold.
4. *Read every non-GENUINE case again.* The 76 cases not labelled GENUINE were read a second time by
   independent readers who did not see the first verdict. A first-read AMBIGUOUS that the second read
   called GENUINE is taken as GENUINE; the rest were settled from the run data or by the authors.
5. *Correct the key where it was wrong, and tally.* The reading was first done against the key before
   its last correction; the readers found it wrong on four fields there (three `deal_seeking` cells
   and one avoided cuisine). Those four are corrected in the released key, the episodes were
   re-extracted against it, and the three cases that changed were read afresh (all GENUINE).

**Result.**

| verdict | episodes | share |
|---|---:|---:|
| GENUINE (the "confirmed mistakes") | 742 | 92% |
| KEY_ARTIFACT | 60 | 7.4% |
| — the system recorded what the user said under a neighbouring label (a wheat allergy filed as `gluten_free`) | 37 | 4.6% |
| — a safe default on a field never raised (`alcohol_free_required` false, an empty list) | 21 | 2.6% |
| — the gold itself is wrong (two `visit_time_distribution` cells whose hidden histogram disagrees with the visits shown) | 2 | 0.2% |
| AMBIGUOUS | 5 | 0.6% |

None is open. The two wrong visit-time distributions are left as keyed: every distribution is scored
against the final card, and re-keying these two to the visits shown would make them the only
distributions keyed that way.

**Gold the readers doubted.** Readers flagged the gold as doubtful on 112 cases, whatever the
verdict; in 88 of them the system is still wrong. They come down to 57 questions about the key in 30
subjects. Only the two visit-time distributions were judged wrong by both reads; the rest are one
reader's doubt and are kept as keyed.

**What the confirmed mistakes show** (the paper's five patterns, in brief):
- **Committing too early is the main failure.** 49% (DeepSeek V4 Pro) to 74% (GLM-5.3 Flash) of each
  backbone's single-pass confirmed mistakes are commits on a field not yet evidenced, 45% of them at
  the first checkpoint; the guess is in the final gold card in at most 17% of episodes.
- **The guess is the middle of the scale.** On ordinal fields, 266 of 323 errors are commits on an
  unevidenced field, at the middle level whatever the persona.
- **Which cells fail varies by backbone; the trigger is shared.** 64% of failing `prompted` cells
  fail under one backbone only, 5% under all four. Shared triggers: a budget cap read as a
  deal-seeking level, a format chosen read as a liked event type, a soft preference hardened into a
  constraint (`seating_required`, 74 episodes).
- **A single-pass system can throw away what it holds.** A *wipe* rewrites five or more held fields
  to `unknown` at once: `prompted` wipes 16 times on DeepSeek V4 Pro and never elsewhere; `notebook`
  3–6 times on every backbone. 33 of DeepSeek V4 Pro `prompted`'s 42 constraint drops sit inside a wipe.
- **A memory layer changes the kind of failure.** `memory` on Luna has 3.14 confirmed mistakes per
  subject against 0.84–0.90 single-pass on the same backbone; 36% of its episodes are losses (vs
  11–14%), and its errors flicker (median length 1; 20% of wrong cells go wrong, right, wrong again)
  where single-pass errors persist (median 3–5 checkpoints).

**Caveats.** The readers are LLMs applying a written policy, not human annotators, and a case the
first read called GENUINE was not read again (440 of 472 GENUINE cases rest on one read). The
stateful mechanism is read from one run on one backbone.

**Where the record is.** The reading itself — reader protocol, both passes, how disagreements were
settled — is [`failure-modes/FULLPASS-2026-10-14.md`](failure-modes/FULLPASS-2026-10-14.md); its own
counts predate the final key correction. The episode and verdict files (`data-releasedkey-2026-10-20/`
and its predecessors) are **not shipped**; they stay in the unreleased development tree. The free
extraction steps are in [`../scripts/`](../scripts/): `extract_failures.py`, `episodes_and_slices.py`,
`tag_failure_modes.py`, `dump_transcripts.py`, `aggregate_verdicts.py`.

**Per-mode examples.** [`failure-modes/`](failure-modes/) holds one file per mode with
transcript-confirmed examples. The mode definitions and examples still apply; the prevalence counts in
those files come from the older catalogue below.

---

## Historical: the scalarfix catalogue (2026-08-28)

*Its counts predate the paper runs — four submissions including `mem0`, v1 prompts, up to six
backbones, the `n50-scalarfix` key — and must not be quoted. The taxonomy and the per-mode examples
are what carried forward.*

What the four submissions actually get *wrong*, categorized into behavioral failure modes, with
every mode's examples verified against the served transcript. This is the qualitative companion to
[`SUT-RESULTS.md`](SUT-RESULTS.md), which reports the scores; here we open the errors up and ask,
one cell at a time, **what kind** of mistake it is and **whether it is the model's fault at all.**

The per-mode example files live in [`failure-modes/`](failure-modes/) — one file per mode, each
holding its verified instances with transcript quotes.

> **Scope.** 4 SUTs (`prompted`, `notebook`, `memory`, `mem0`) × up to 6 backbones, scored against
> the `corpus-n50-scalarfix` answer key (canonical when this catalog was extracted; since superseded
> by `n50-strongaudit-2026-08-29`, which does not move the tallies — see STATUS below) by
> position-replay of the emission cache (free, no new calls). Single-pass systems now have **full n50
> coverage on four backbones** — gemini-3.7-flash, gpt-5.6-luna, deepseek-v4-pro (both arms) and
> glm-5.3 (prompted only); the stateful systems were only ever run at n≈10 (plus a partial
> deepseek-v4-pro draw), so every cross-*architecture* number (single-pass vs stateful) is taken on a
> **shared subject×model set** where all four ran. The deepseek-v4-pro completion (from a 34-subject
> draw) and the glm-5.3 addition post-date the first extraction and were adjudicated by the same
> transcript pass (284 new episodes, 54% artifact — the headline below holds); they change the
> **Model cross-cuts** reading, not the architecture finding — see the 2026-08-29 UPDATE there.

### The headline finding, before any mode: half of the "errors" are not failures

Every committed-and-wrong cell was collapsed into an **episode** (a field wrong across consecutive
checkpoints counts once), giving 1,458 candidate failure episodes. Twenty sub-agents then read the
full served transcript for each subject and adjudicated every episode: is the gold genuinely
supported by the transcript at that point, and is the emitted value genuinely wrong?

**Of 1,417 adjudicated episodes, 55% are answer-key artifacts, not model failures.** The raw error
counts — including those in `SUT-RESULTS.md` — overstate the failure load by roughly 2×. The
artifacts fall into a few repeatable buckets:

- **Hidden-prior gold** — a field graded UNEVIDENCED across the whole transcript that still carries
  a non-empty gold value (a persona prior never surfaced in conversation). The SUT is scored wrong
  on a value it had no way to derive.
- **Low-N distributions** — a distribution field judged at checkpoint 0–1, where one observation
  forces a point-mass and the multi-visit gold shape is simply not yet inferable. *Since 2026-08-30
  these no longer score at all:* the §8.1a scorer carve-out (ADR-0065) excludes a WEAK distribution
  cell from both the agreement and fabrication terms — distributions score only at STRONG (`N≥5`).
- **Benign defaults / lucky matches** — `alcohol_free_required=false` or an empty set on an
  unevidenced field (correct withholding), or a guess that happens to equal gold, both flagged as
  fabrication.
- **Taxonomy / sibling-field mismatch** — the concept is captured under the wrong label
  (`burgers`≈`american`, `japanese`⊃`sushi`) or the signal is coded into a neighbouring field
  (a wheat allergy filed as `dietary=gluten_free`).

**This is itself a result for the paper:** an untuned auto-scorer over a rich persona schema
manufactures a large minority of spurious errors, and a failure catalog that does not read the
transcript back will inherit them. It also gives a reliability score per raw error type — how often
an auto-flagged error of that kind survives transcript review:

| auto error type | episodes | survive review | read as |
|---|---|---|---|
| `boolean_flip` | 4 | 100% | trustworthy |
| `set_collapse_to_empty` | 11 | 100% | trustworthy |
| `ordinal_far` | 111 | 91% | trustworthy |
| `set_inflation` | 17 | 88% | trustworthy |
| `missed_constraint` | 91 | 75% | mostly real |
| `set_substitution` | 40 | 70% | mostly real |
| `set_underlist` | 133 | 60% | check the transcript |
| `set_partial_swap` | 111 | 40% | mostly taxonomy noise |
| `fabrication_guess` | 511 | 41% | half real |
| `invented_constraint` | 38 | 34% | mostly escalation noise |
| `distribution_shape` | 252 | 19% | mostly low-N noise |
| `fabrication_lucky` | 98 | 3% | noise |

Everything below counts **only the transcript-verified GENUINE episodes** (623 of them).

### Inside the artifacts: the taste-list gold carries two *correctable* defects

Most of the 789 verified artifacts are structural and un-fixable by editing gold — a hidden
prior the SUT could not derive, a distribution judged at N=1, a boolean default charged as a
fabrication. But the four taste-list fields (`cuisines_liked/avoided`, `event_types_liked/avoided`)
are different, and they **dominate the artifact load**: 292 of the 789 artifact episodes (37%),
51 of the 175 distinct artifact cells. Reading their transcripts back shows the gold itself is
wrong in two repeatable ways — the same family as the 2026-08-27 phantom-list prune (route 2) and
the still-unbuilt per-checkpoint `surfaced` gate (route 1). These do not move the 623/55% split
(the reviewers already scored these cells KEY_ARTIFACT), but the corpus should be corrected before
it is reused, and the second defect below quietly inflates one of the "real" modes.

> **STATUS (2026-08-29) — defects 1 and 2 are FIXED and the full STRONG-cell worklist is now CLOSED;
> the analysis below is kept as the finding that motivated the fix.** Defect 1 (Flavor A, never-surfaced
> phantoms) was pruned and promoted as `n50-flavorA-2026-08-29`; defect 2 (visit-to-like, booking ≠
> liking) as `n50-flavorB-2026-08-29` under a strict owner policy (`docs/GT-POLICIES.md` §1.2). Both
> removed the flagged elements from `card`+`card_at`. A **systematic ≥2-judge full-transcript pass over
> all 39 STRONG multi-element taste cells** then superseded those hand-pruned passes and was promoted as
> the current canonical base **`n50-strongaudit-2026-08-29`** (`docs/provenance/CANONICAL.md`,
> `docs/GT-POLICIES.md` §3.1): it flagged 6 candidate phantoms across 4 cells, a Fable-5
> indirect-reference adversarial pass **rescued 1** (gen-011 `cuisines_liked=filipino` — the user names
> the dish *sisig* at cp8, which both the token scan and the opus+sonnet judges missed), and the other
> **5** were pruned (gen-005 −`supper_club`, gen-013 −`taiwanese`, gen-031
> −`vegan_restaurant`/`german`/`cajun`); no cell drops below 2 elements, so all stay STRONG. Present-tense
> claims below ("the answer key does it too") describe the *pre-fix* key; the figures (623/55%, the mode
> tallies) were extracted on the `n50-scalarfix` base and are unchanged by these prunes, since the
> reviewers had already scored these cells KEY_ARTIFACT. **Defect 3 (route-1 list-timing) is now also
> CLOSED (2026-08-30)** — the canonical base `n50-route1-surfaced-2026-08-30` rewrote `card_at` on the
> four taste-list fields to the surfaced-through-*t* subset (verified in-data — gen-014 `cuisines_liked`
> grows `[]`→`[peruvian]`→…→8 across c0–15), so the whole taste-list worklist (defects 1–3) is closed.
> See `docs/provenance/CANONICAL.md`, `docs/GT-REVIEW.md` and `docs/GT-POLICIES.md` §3.2.

**1. Phantoms survive on STRONG-graded cells, because the earlier prune skipped them.** The route-2
phantom-prune audited only cells that were *never STRONG* — its exposure filter was "multi-element,
scored, never STRONG" (76 cells; **0** carried a STRONG band). That leaves **39 STRONG-graded
multi-element taste cells** (13 `cuisines_liked`, 12 `event_types_liked`, 9 `cuisines_avoided`,
5 `event_types_avoided`) never audited for phantoms — and STRONG is the *worst* place for one: the
scorer holds the twin to the exact gold set with no ~0.83 weak-credit floor, so a single absent
element is a hard miss. A hand-read of the flagged STRONG cells finds gold elements that appear
**nowhere in the served transcript — only in the card header**:

| cell (STRONG `cuisines_liked`) | phantom gold element(s) | in transcript? |
|---|---|---|
| gen-011 | `german`, `cajun` | absent entirely (only the gold-card line) |
| gen-046 | `moroccan`, `cajun`, `vegan_restaurant` | absent entirely |
| gen-013 | `soul_food`, `thai` | absent entirely |
| gen-010 | `soul_food` | absent entirely |

**2. The gold commits the SUTs' own `set_inflation` — visit-to-like.** The remaining STRONG phantoms
are credited from a *booking*, not a stated liking: the bot offers a venue of cuisine X, the user
books it for an unrelated reason, and gold lists X as liked.

- **gen-029** `cuisines_liked=[…, indian, filipino]`: the user books Kalyani (the bot's Indian
  suggestion) explicitly for *a quiet room where four people can hear each other* — never a word
  about Indian food; `filipino` is likewise a bot-offered food-yard stall.
- **gen-013** `cuisines_liked=[…, filipino]`: "that'll do nicely, book it" for the bot's Ilaya
  suggestion — while the cuisine the user actually raves about (Portuguese, "the best thing on the
  menu") *is* correctly in gold. The key mixes a genuine liking with a booking-acceptance.
- **gen-010** `cuisines_liked=[…, mediterranean]`: only ever the bot's word for a venue ("a
  Mediterranean spot doing mezze"), never the user's.

This is precisely the **visit-to-like conflation** the catalog charges single-pass SUTs for under
`set_inflation` (§"the architecture is the story") — the answer key does it too. Because these cells
are STRONG, a twin that *correctly declines to inflate* is scored as under-listing, so part of the
`set_underlist` load (60% survive review — the least trustworthy of the "real" modes, per the table
above) is the twin being penalized for not reproducing an inflated gold.

**3. The timing dimension — CLOSED 2026-08-30 (route 1).** It was, until this base, scored against the
full ever-evidenced union from the first WEAK checkpoint. gen-014
`cuisines_liked` (8 cuisines, all genuinely user-stated) is graded WEAK from c2, but the elements
surface `peruvian` c2 → `vietnamese` c3 → `japanese`/`taiwanese` c12 →
`thai`/`indian`/`mediterranean`/`ethiopian` c15. A reader holding the c2-licensed subset (`peruvian`)
scored Jaccard 1/8 and was beaten by abstention. The canonical base `n50-route1-surfaced-2026-08-30`
fixes this: `card_at` now grows `[]`→`[peruvian]`→…→8 across the checkpoints, so the per-checkpoint
`surfaced` record is authored and the reader is scored only against the subset licensed *through t*.

**Fix.** The route-2-style ≥2-judge reading pass over the 39 STRONG taste cells (prune absent and
visit-only elements) **ran and closed defects 1–2** (`n50-strongaudit-2026-08-29`, see STATUS above);
the route-1 `surfaced` growth for the timing dimension (defect 3) **then landed as
`n50-route1-surfaced-2026-08-30`, closing all three.** All are confined to the four taste fields; the
scalar and constraint artifacts are structural (hidden-prior, low-N, benign default) and are not
gold-value defects — the low-N distribution artifact is instead handled scoring-side by the §8.1a
carve-out (ADR-0065): a WEAK distribution cell is excluded from both the agreement and fabrication
terms (score distributions only at STRONG, `N≥5`).

### The taxonomy

Fourteen behavioral modes in five families. The scorer's three structural labels
(fabrication / misreading / staleness) sit underneath these — a "misreading" can be an ordinal jump,
a set swap, or a distribution collapse, and those fail for different reasons and have different
fixes, which is the point of splitting them.

Two modes were added by the transcript reviewers on top of the auto-tagger: **`state_drop`** (a
memory system losing a fact across sessions) and, noted but folded into `invented_constraint`,
**constraint escalation** (a soft preference hardened into an accessibility requirement, e.g.
"quiet enough to hear myself think" → `quiet_room_needed`). Staleness — still emitting a
*previously-correct* value after a correction — occurs **zero** times; and ordinal off-by-one never
appears because the scorer forgives it, so every `ordinal_far` is a jump of two levels or more.

| family | mode | genuine | dominant SUT(s) | file |
|---|---|---:|---|---|
| **Omission / loss** | [`state_drop`](failure-modes/state_drop.md) | 15 | mem0 (stateful only) | ← |
| | [`missed_constraint`](failure-modes/missed_constraint.md) | 53 | mem0, memory | ← |
| | [`set_underlist`](failure-modes/set_underlist.md) | 80 | single-pass | ← |
| | [`set_collapse_to_empty`](failure-modes/set_collapse_to_empty.md) | 8 | mem0 (stateful only) | ← |
| **Commission / over-assertion** | [`fabrication_guess`](failure-modes/fabrication_guess.md) | 206 | all; mem0 leads | ← |
| | [`set_inflation`](failure-modes/set_inflation.md) | 15 | single-pass only | ← |
| | [`invented_constraint`](failure-modes/invented_constraint.md) | 16 | spread | ← |
| **Value distortion** | [`ordinal_far`](failure-modes/ordinal_far.md) | 101 | memory, mem0 | ← |
| | [`set_substitution`](failure-modes/set_substitution.md) | 28 | memory, mem0 | ← |
| | [`set_partial_swap`](failure-modes/set_partial_swap.md) | 42 | memory | ← |
| | [`distribution_shape`](failure-modes/distribution_shape.md) | 48 | memory, mem0 | ← |
| | [`boolean_flip`](failure-modes/boolean_flip.md) | 7 | mem0 only | ← |

### The architecture is the story: stateful systems fail by *losing*, single-pass by *over-reaching*

On the identical subject×model set where all four SUTs ran (the n10 subjects × the three backbones
that ran every architecture: deepseek-v4-flash, gpt-5.6-luna, glm-5.3-flash), the verified genuine
failure counts are:

| | prompted | notebook | memory | mem0 | single-pass | stateful |
|---|---:|---:|---:|---:|---:|---:|
| genuine failures | 32 | 30 | 144 | 185 | **62** | **329** |

**A memory layer produces 5.3× the genuine failures of re-reading the transcript**, on the same
subjects and the same backbones. This is the AUFC gap from `SUT-RESULTS.md` re-derived from the
transcript up, and it is not a scoring artifact — it survived the 55% artifact cull. The *shape* of
the failure differs sharply by architecture:

- **Loss modes are almost entirely stateful.** `state_drop` (15 episodes, 14 mem0, **0 single-pass**),
  `set_collapse_to_empty` (8, all stateful), `boolean_flip` (7, all mem0) never happen when the
  system re-reads the window. `missed_constraint` and `distribution_shape` also concentrate on
  stateful (a constraint or a session that made it into the bank and then fell out or froze).
- **Over-assertion modes are single-pass.** `set_inflation` (15, **0 stateful**) is the mirror
  image: a fresh reader sees the current conversation and *adds* to the card — most often the
  **visit-to-like conflation**, listing every venue the user ate at as a cuisine they like.
- **Value distortion (`ordinal_far`, set swaps) skews stateful** — a compressed memory of a
  preference drifts to the wrong level or the wrong members, whereas a re-reader recovers it.

The mechanism is the one `SUT-RESULTS.md` names, now with instances: a single-pass extractor's error
is transient (it re-derives from the transcript next checkpoint), while a memory system writes its
error into state, where a dropped constraint stays dropped and a frozen distribution re-scores on
every later checkpoint. `state_drop` is the clean proof — the same fact the single-pass systems hold
across sessions is exactly what the memory systems lose.

### Model cross-cuts

> **UPDATE 2026-08-29 — the deepseek-v4-pro draw is now complete (50/50, both arms) and a new
> backbone, glm-5.3 (prompted), was added. Both were adjudicated by the same transcript pass (284 new
> episodes: 121 genuine, 154 artifact, 9 ambiguous → 54% artifact, matching the 55% headline; zero
> new modes). The completed draw overturns the "two flagship backbones make the fewest" reading — that
> rested on deepseek-v4-pro's partial 34-subject draw. The corrected table and bullet are below; the
> original partial-draw text is preserved beneath them as the finding that was superseded.**

Single-pass, full n50, transcript-verified genuine failures per backbone (both arms unless noted):

| backbone | genuine | dominant genuine modes | shape |
|---|---:|---|---|
| gemini-3.7-flash | **24** | `set_underlist` 17 | most conservative; its few errors are under-listing, not fabrication |
| glm-5.3 (prompted only) | 80 | `fabrication_guess` 47, `set_inflation` 11 | purest premature-committer |
| gpt-5.6-luna | 85 | `set_underlist` 20, `fabrication_guess` 19, `set_partial_swap` 16 | balanced omission/commission |
| deepseek-v4-pro | **134** | `fabrication_guess` 50, `invented_constraint` 21, `set_inflation` 20 | **most** errors; over-assertion |

- **Only gemini is a clean flagship; deepseek-v4-pro is not.** gemini-3.7-flash (24 genuine) sits far
  below every other backbone, but the completed deepseek-v4-pro draw carries the **most** genuine
  single-pass failures (134, up from the 73 its partial 34-subject draw showed), concentrated in the
  *commission* modes: `invented_constraint` (21) + `set_inflation` (20) = 41 genuine over-assertions,
  the highest of any backbone. Its `invented_constraint` is dominated by the phantom
  `accessibility_needs=['seating_required']` escalation — reading a table-service/counter dining wish
  as an access requirement — adjudicated genuine on every read. glm-5.3 (80 on the prompted arm alone,
  59% of them `fabrication_guess`) is the purest premature-committer. So a stronger backbone is not
  automatically a cleaner extractor along one axis: gemini withholds, deepseek-v4-pro over-reaches,
  and the single-pass "over-commission" signature (below) sharpens rather than softens at scale.
- **The safety-relevant surface claim from the n=5 pilot does not survive n50: single-pass systems DO
  break hard constraints.** Per-checkpoint constraint-failure rate at n50 is gemini 0.04–0.06, glm
  0.12, luna 0.08–0.16, **deepseek-v4-pro 0.42/0.34** — but the direction is commission, not the
  stateful omission: deepseek-v4-pro's failures are 78% *invented* atoms (241 cells) vs *missed* (67),
  overwhelmingly the `seating_required` escalation. Single-pass breaks constraints by inventing them,
  exactly the architectural signature this catalog predicts; the "constraint failures land only on
  stateful SUTs" line was a two-model n=5 artifact.

<details><summary>Superseded (partial-draw) reading, kept for the record</summary>

- **The two flagship backbones make the fewest single-pass errors.** On the full 50 subjects,
  gemini-3.7-flash (24 genuine) and — on its partial draw — deepseek-v4-pro (73) sit well below the
  weaker backbones; gpt-5.6-luna (85) carries more, mostly `ordinal_far` and constraint misses.

</details>

- **`fabrication_guess` (premature commitment) is the universal single-pass failure** — every
  backbone guesses on some unevidenced fields rather than abstaining; it is the largest genuine
  mode overall (206) and the one most worth a calibration fix.
- **The stateful pathologies are backbone-amplified but not backbone-caused.** `state_drop`,
  `boolean_flip` and `set_collapse` concentrate on `mem0`/`deepseek-v4-flash`, the weakest stateful
  pairing, but the *kind* of failure (lose-across-sessions) is set by the architecture; a stronger
  backbone loses less, it does not lose differently.

### Learnings for the paper

1. **Report verified failures, not raw error counts.** 55% of auto-flagged errors are answer-key
   artifacts; the reliability table above says which auto-labels can be trusted without a transcript
   (`ordinal_far`, `boolean_flip`, `set_collapse`) and which cannot (`distribution_shape`,
   `fabrication_lucky`, `set_partial_swap`).
2. **The single-pass advantage is a failure-*shape* result, not just a score.** Same subjects, same
   backbones: 62 vs 329 genuine failures. Memory buys a 5.3× failure multiplier at 2× the calls.
3. **The two error directions are architectural signatures.** Stateful = omission (`state_drop`,
   dropped constraints, frozen/collapsed distributions). Single-pass = commission (visit-to-like
   `set_inflation`, premature `fabrication_guess`). A reviewer can predict the failure from the
   architecture.
4. **The safety-relevant failures are the constraint ones, and both directions occur.** Stateful
   systems *drop* stated hard constraints (`missed_constraint`, e.g. a braille menu or a no-alcohol
   requirement carried for 18 checkpoints as `unknown`); all systems occasionally *invent* them, the
   cleanest cases being **third-party misattribution** — copying a companion's allergy onto the user
   ("no shellfish for me. And he can't have sesame" → the user is given a sesame allergy).
5. **Two of the scorer's own categories are effectively empty and worth stating as such.** Staleness
   never fires (no system re-emits a corrected value); ordinal off-by-one is forgiven by design.

### How this was produced (reproducibility)

The scripts below are in [`../scripts/`](../scripts/) unless noted; all intermediates are regenerable from the emission cache.

1. `extract_failures.py` — position-replays every cached emission against the scalarfix answer key,
   re-runs `scorer.score_checkpoint`, and dumps one record per committed-and-wrong cell / fabrication
   / member-level constraint failure (5,514 cells).
2. `episodes_and_slices.py` — collapses cells to 1,458 episodes and slices them per subject.
3. `tag_failure_modes.py` — assigns each episode a provisional behavioral mode from the value shape
   and the field's metric type.
4. `dump_transcripts.py` — writes the served transcript + gold + per-checkpoint grades per
   subject (what the SUT saw).
5. Twenty transcript-reading sub-agents adjudicated every episode (GENUINE / KEY_ARTIFACT /
   AMBIGUOUS), confirmed or corrected the mode, and pulled verbatim evidence quotes.
6. `aggregate_verdicts.py` joins verdicts back to episodes; `write_mode_files.py` (not shipped) emitted the
   per-mode files in [`failure-modes/`](failure-modes/).

### Limitations

- **Coverage is uneven.** The stateful arms exist only at n≈10, so the 5.3× multiplier rests on the
  shared n10×3-backbone cut, not on 50 subjects. Single-pass n50 is solid.
- **41 episodes (2.8%), all on gen-027 and gen-029, are unverified** — one sub-agent omitted rows
  rather than deduping them. The aggregate is unaffected; those two subjects are slightly
  under-counted.
- **The GENUINE/artifact line on `fabrication_guess` is a judgment call.** A positive commit on an
  unevidenced field is a genuine calibration failure by the benchmark's own §8.1, but where the gold
  itself is a hidden prior the penalty is unfair; reviewers marked the former GENUINE and the latter
  KEY_ARTIFACT, and a small band of borderline cases sits on that line.
- **Distribution fields predate ADR-0063** in some cached runs; `distribution_shape` counts should be
  read as directional (the collapse-to-point-mass pattern is robust; exact tallies are not). The low-N
  half of this mode no longer scores as of 2026-08-30 — the §8.1a carve-out (ADR-0065) excludes WEAK
  distribution cells; these tallies were extracted pre-carve-out, so re-score before reusing them.
- **`set_underlist` was scored against then-un-audited STRONG taste gold** (see "Inside the artifacts").
  The route-2 phantom-prune had excluded all 39 STRONG multi-element taste cells, so these tallies were
  extracted while those cells still carried absent-everywhere phantoms and visit-to-like inflation —
  charging a twin `set_underlist` for correctly listing only the user-stated cuisines. The **STRONG-cell
  audit has since closed this** (`n50-strongaudit-2026-08-29`): only 5 elements across 3 cells were
  pruned, so the effect on the `set_underlist` count is small — read the tally as a slight upper bound,
  not an open unknown. The other modes are unaffected.
