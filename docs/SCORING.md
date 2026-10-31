# Grading and scoring

> **A dated record, kept current where it matters.** First written for the v0.1 scorer and carried
> down the development lineage; where it names a run, the run is dated. Added since v0.1: the
> reader-derived credit layer (ADR-0046/0047), the direction check and its two repairs (0050,
> 0051), and the owner adjudication and its settlement rule (0058) — all in "A second layer on the
> map" below. Where a magnitude is quoted, the store it was measured on is named, because two of
> them have failed to transfer between stores. ADR numbers name unreleased design records.

The original spec (`spec-v0.md`, unreleased; the § numbers below refer to it) is the authority on
*how a twin is scored*, and almost all of it carries over. What does not is everything stated **per
tier** — the released corpus has no tiers (ADR-0033) — and the **position of the LLM judge**.

This document is the reading for the generator that produced the released corpus. Where it
disagrees with `spec-v0.md`, this document wins.

The short version of the change: the schedule writes the evidence map, and judges moved from
labelling cells to a sampled audit of the transcripts. This is the resulting pipeline.

---

## The one-paragraph version

A subject's answer key has two halves and both are produced without model calls. The **persona
card** says what the twin should say. The **derived evidence map**, computed from the schedule,
says when each field becomes knowable — `strong` (pinnable), `weak` (hinted), `unevidenced`
(nothing there), per field per checkpoint. The scorer reads both. LLM judges are **not** in that
path: they run afterwards, over a sample, to measure whether the authored transcript actually
delivered what the schedule scheduled.

## Why the map is the interesting artifact

The benchmark scores **calibration**, not accuracy. To say "abstaining here was correct" or
"committing here was a fabrication even though the value happens to be right", you need to know
what the transcript supported at that moment. Without an evidence map you can only measure whether
the twin got the value right, which is a much less interesting benchmark and one other people have
already built (§0.1, and FEVER 2018 is the ancestor of the evidence-conditioned rule).

The map is what makes "correct abstention" a checkable claim rather than an assertion.

---

## Three stages

### Stage 1 — the answer key (no model calls)

| artifact | produced by | what it answers |
|---|---|---|
| persona card | `generator.generate` | what is true of this person |
| derived evidence map | `EvidenceSchedule.provisional_map()` | what a reader could know by checkpoint *t* |

The map is **computed from what the schedule placed**, never written down beside it — a schedule
carrying both a beat list and a hand-written grade table would hold two records of one fact with
nothing stopping them drifting (§5.1, ADR-0033 decision 2). Evidence accumulates against a per-field
threshold; reinforcement is the mechanism by which a weak field becomes strong.

Three kinds of placement count, and the second and third were added by measurement rather than by
design:

| what | weight | why it counts |
|---|---|---|
| a **beat** that can raise a grade (`counts_toward_gold`) | `evidence_weight(beat)` | the field's own evidence, in the subject's own voice |
| an **occasion** — a distribution bin the session instantiates as scenery | `INCIDENTAL_WEIGHT` (1) | you cannot book a table without saying who is coming and when, so it is on the page whether or not a beat asked for it (ADR-0037) |
| an **entailed correlate** of an occasion | reaches `weak`, never more | the draw is made from the card, and cluster coherence ties the pair, so the occasion *is* evidence about the ordinal (ADR-0044) |
| the **lead time** an outing is arranged at | reaches `weak`, never more | a page that says *when* the outing is, in a conversation happening now, has said how far ahead this person decides. Drawn from the card by the brief, so the page was never wrong — only uncounted (ADR-0044) |
| the **formality** of the outing | reaches `weak`, never more | the outing's format is drawn from the card at or below the ceiling, and an outing *at* the ceiling says where the ceiling sits. One below it says nothing — every higher level also permits a casual meal (ADR-0053) |

The last three are the ones to understand before designing against the map, and they are three
mechanisms rather than one. `schedule.ENTAILED_BY_BIN` carries three entries and no more: a
party-of-one occasion credits `solo_dining_comfort`, and a `£30-50` or `£50+` occasion credits
`deal_seeking`. Both pairs are same-cluster (`fields.CLUSTERS` C3,
C2), the C2 pair is that cluster's declared `generator._ANTI_FLIP` pair, and C3 carries the hard
`solo_comfort_conflicts_party_sizes` coherence rule — so across the pilot's five cards the
correlation is exceptionless. The credit stops at `weak` at any count, and applies only where the
field has no evidence of its own: this is inference from a correlate, and pinning is for the
field's own evidence. `turn_refs` records `s{n}:entailed:{field}={bin}` so a credited cell can say
where its credit came from.

The lead time (`schedule.horizon_lands`) and the formality (`schedule.formality_lands`) are
separate predicates on the same principle, and each is stated **once** and called by both the map
and the brief. Two copies of such a rule would let the map credit a cell the brief never asked
for, and the divergence would be silent — the page would simply be missing the sentence the grade
rests on.

**Why these are entailments and not occasions.** `_map_of` reads occasions and feeds
`emap_so_far` into placement decisions, so adding one moves *other* fields' beats across the whole
corpus. It does not read entailments. That is what let ADR-0044 and ADR-0053 correct the map while
leaving every beat byte-identical — re-deriving the pilot under ADR-0053 moved 27 cells, all of
them `formality_ceiling`'s own, with STRONG unchanged and no beat displaced.

**Why this is in the map and not in the judges' hands.** ADR-0034 put the schedule in charge
precisely so that no model call decides a grade. An occasion draw *is* schedule state, so crediting
what it entails is derivable for free and stays in charge. Author invention is not schedule state,
and crediting that would hand the map back to the judges — which is why the answer to the two
sources is different, and why the retry that guards against invention names a field and forbids it
rather than showing the author a value.

Measured on the **67-session ladder pilot** (not the 57-session draw this tree ships): 63 of 1608
field-checkpoints (3.9%) move `unevidenced` → `weak`, and 34 of the reader arm's 83 leak findings
were the map failing to count evidence the schedule itself placed. On the 2026-08-21 rebuild the
same mechanism shows up as its own absence — `planning_style` raises **zero** raw leaks on either
reader, where it previously carried 20 of 42. Two properties fall out for free:

- **monotone by construction** — a field's grade never decreases in the checkpoint index, so §7.3's
  monotonicity requirement is met rather than checked;
- **prefix-stable by construction** — extending a subject cannot move an earlier checkpoint's map
  (`test_extension_preserves_the_provisional_map_prefix`).

A corpus item carries two derived lists beside the map — `must_strong` and `must_unevidenced` —
and **both are read off the map**, not recomputed from the beats. They were beat-derived until
ADR-0044, which is how 8 field-subject cells came to be listed as never-evidenced while the same
file's `provisional` graded them `weak` or `strong`. One fact, one record.

`profile(checkpoint)` reports the realized strong/weak/unevidenced shares. This is a **description
of the corpus, published per subject and per checkpoint. It is not a gate** — see "What was
retired" below.

The name `provisional_map` is kept for continuity with v0's vocabulary. It is not provisional in
this tree; call it the derived map where the distinction matters.

### Stage 2 — scoring a twin (no judges)

Unchanged from `spec-v0.md` §8, which is stated over field counts and bands and never over tiers:

| band | SUT commits a value | SUT emits `"unknown"` |
|---|---|---|
| `S(t)` strong | scored `a_f` | scored **0** — a miss, not an exclusion |
| `W(t)` weak | scored `a_f` | scored `WEAK_CREDIT`, counted in the denominator |
| `U(t)` unevidenced | **fabrication**, even if the value matches ground truth | correct abstention, costs nothing |

`WEAK_CREDIT = 5/6` (derived, §8.1). `A(t)`'s denominator is `|S(t)| + |W(t)|` — fixed by the map,
not by SUT behaviour. Fabrications are priced on that same denominator at λ = 1.0, also reported at
λ ∈ {0.5, 2.0}; `F_raw`, `a_floor` and `f_skill` are reported alongside, and a checkpoint enters
AUFC only if `|S(t)| + |W(t)| ≥ 7` (§8.3a). All four §8.8 baselines still apply.

**Nothing here needed a decision.** The scoring side was never the part that broke.

#### What is on the other end of it (ADR-0060)

Until 2026-08-23 nothing had ever *consumed* the key this way: §8.8's reference SUT raised
`NotImplementedError`, and §1.3's protocol was a docstring on `baselines.SUT` that
`run_trajectory` enforced by defaulting every batch to `[]`. `src/persona_twin/sut.py` is that
side, and `scripts/score.py` drives it.

A submission is one object with four methods:

```python
class SUT(Protocol):
    def reset(self, subject_id: str) -> None: ...        # between subjects; no carryover
    def observe(self, sessions) -> PersonaCard: ...      # the NEW sessions -> the whole card
    def carried_state_bytes(self) -> int: ...
    def carried_state_tokens(self) -> int: ...
```

Three serving rules, structural rather than trusted, because each is the kind of thing a
submission would break by accident:

| rule | how the harness holds it |
|---|---|
| the prefix is never re-served | `serve` returns one batch per checkpoint, containing only that checkpoint's sessions. Carrying the text forward is the submission's decision and shows up on its state meter |
| the key is never served | `ServedSession` is `(subject_id, session, checkpoint, date, transcript)`. The record beside it holds `beats`, `self_report` and `is_distractor`; all three are key |
| a hole is not a shorter timeline | an empty session makes the whole subject unservable and printed, never sessions 1 and 3 served as 1 and 2 (`judge.Bundle.prefix`'s rule) |

**Which corpus you point at *is* the answer key.** The one to point at is the released key on
HuggingFace (`tangents/dossierbench`, base `n50-tagfix-2026-10-15`), which `data.corpus("key")`
fetches — 50 subjects, 612 sessions, two-reader credit and the owner's decisions folded in — with
`data.corpus("map")` beside it as the model-free map the schedule itself wrote.
`docs/provenance/CANONICAL.md` is the lineage; the keys named next are earlier development keys (not shipped). `--corpus
runs/corpus-n15.json` scores against the model-free map over 15 subjects; `--corpus
runs/corpus-pilot-credited-adjudicated.json` scores against the 5 pilot subjects. None of these
are comparable cell-for-cell — they are different subject sets, and the smaller ones' credit
passes are one-armed — so a read-out says which it used.

**Check that a credited key validates before scoring against it.** Every credited map built
before 2026-08-25 fails `EvidenceMap.validate()`: `credit.apply` raised a cell's own checkpoint
and no later one, so the key expected a twin to know a field at *t* and to have forgotten it at
*t+1*, which scores a correct memory as fabrication. `sut.evidence_map` refuses those on load
now. The union key passes on all 50 subjects. `sut.verify_gold` checks the rewound card
against the corpus's own `card_at` on every load, because two records of one fact is the shape
ADR-0033 decision 2 refuses in the schedule.

Two reference SUTs ship, neither tuned. `prompted` is baseline 4: one call per checkpoint, and the
card it last emitted is its whole state. `notebook` is the same loop with a free-text scratchpad,
which exists to put a second point on the state axis rather than to be a second system. The model
rides on the submission — `--sut prompted:claude-opus-5 --sut prompted:claude-sonnet-5` puts both
on one table — because a leaderboard row is a system *and* the model it was built on.

**The free arm is a gate on the scorer, not a measurement.** Over 230 sessions the oracle must
come out at 1.000 on every subject, `abstain` at exactly 0.000 skill, and `population` below it;
on `work/authored/claude-opus-5` it does (1.000 / 0.000 / 0.176, and `random` 0.143). A
deviation is a defect in the answer key or in the harness and never in a submission. (Those numbers
are from the 230-session pilot store, before any model was served; the served runs since are in `SUT-RESULTS.md`, each
predicted in writing before its first call.)

### Stage 3 — the audit (judges, sampled)

Judges answer one question: **does the authored transcript deliver what the schedule scheduled?**
The output is a disagreement rate published as a dataset-quality number — not a per-cell label the
scorer consumes.

Two arms, and dropping either one leaves a specific hole:

| arm | sample from | the failure it catches | what it costs the scorer |
|---|---|---|---|
| **under-delivery** | cells graded `strong` / `weak` | the author buried the signal | demands a value the transcript does not support, so a *correct abstention* scores as a miss |
| **over-delivery (leakage)** | cells graded `unevidenced` | the transcript revealed it anyway | calls a *correct commit* a fabrication — the §0.1 claim inverts on that cell |

**The leakage arm is the one that matters more**, and it is the one that looks skippable.
`transcript.py`'s leakage checks are rule-based and catch explicit statement, not semantic bleed.
This arm is what closes that gap, and on the pilot it is where nearly all the blocking findings
are: 49 of 61, against 12 contradictions and nothing else.

Judges **may only demote** a cell, never promote it (§7.3, ADR-0009/0010). That rule survives as
the audit's grading instruction and in `verify.reconcile`: a reader that finds a cell *stronger*
than the schedule placed it produces a `disagreement` note and changes nothing, while a reader
taking a cell away, moving it to another person, or finding one nobody scheduled is taken at face
value — those are the three failures the schedule cannot see at all.

### The instrument, as built (ADR-0036)

`src/persona_twin/verify.py`, run by `scripts/audit.py`. Both arms are the same reconciliation
against the same corpus; what differs is who reads the page.

| arm | cost | what it reads |
|---|---|---|
| **deterministic** | free — run it after every authoring pass | structure, drift against the corpus, today's §6.1 rules replayed over stored text, format, the contradiction probe, the occasion surface |
| **reader** | one call per sampled session | whose a statement is, whether a scheduled beat landed, and whether a field nobody scheduled is readable anyway |

`stratified_sample` spends the reader's budget on sessions that can falsify something — traps,
within-session repeats, distractors, anything a rule already flagged — rather than uniformly.
Verdicts are cached and fingerprinted on **what the reader was shown**, so a re-run after a
re-authoring reads only the changed sessions and everything else is free. That is what makes a
map change measurable against the paid arm at zero cost, and it is how ADR-0044's 34 reclassified
leaks were measured.

Two rules about reading its output, both bought expensively:

* **read the excerpt before believing the count.** Three separate headline findings in this tree
  were the instrument and not the data — a bare `dry\b` matching "The Foundry", an occasion probe
  48 cells behind its own phrase list, and `repeat_not_delivered` reading a dict that had dropped
  half the reader's answer (ADR-0039, 0042, 0044).
* **`occasion_missing` and `occasion_mismatch` are questions for the reader**, not findings about
  the dataset. Their own message text says so. A 67-call re-author was once recommended on
  exactly that mistake, and retracted.

**Not decided yet:** the sample size and the acceptable disagreement rate for a release, against a
real 50-subject corpus. What *is* decided is that the audit does not block scoring — a run without
it ships with the error bar stated as unmeasured, rather than implied to be zero.

---

## A second layer on the map: reader-derived credit

Everything above is model-free by construction, and ADR-0034's guarantee is that **an answer key
with no model in it always exists**. What follows sits *on top* of that key as a separate
artifact — `credit.apply` returns new items rather than mutating, so the derived map stays exactly
as `schedule.py` produced it.

The problem it solves is the one the entailments solve, in the general case. A cell the schedule
calls UNEVIDENCED that the page nonetheless carries is not a page defect; it is a **map** defect,
and a twin reading it correctly gets scored as fabricating. ADR-0046 credits such a cell at WEAK
under three keys, **all three required**:

1. the **card** holds a non-empty value for the field;
2. the **page** reads as carrying it, with no contradiction raised;
3. a **second reader, on a different model**, independently reads it there.

**Key 3 fails closed.** No second reading is a refusal, not an acceptance. It is asked directly
with the card withheld (ADR-0047) rather than inferred from a ~30-field free-recall list, because
a silence in free recall is recall failing, not the reader refusing. The ask is repeated — three
times on the 2026-08-21 pilot — and a cell credits on a majority that **agrees with the card**.

That last clause is load-bearing and was added by measurement. A reader can find a field readable
*and read it as a different value than the card holds*: `gen-001-s7` reads as `casual_sit_down` in
2 of 3 runs against a card that says `counter_service` — readable, stable, one level off.
Crediting it would put a cell in the answer key whose page narrows away from the key's own value,
which is the exact inversion this benchmark exists to measure. `confirm.direction` checks it;
ADR-0050 fixed that check for the seven list-valued fields, where it had been comparing
`"['vegan']"` as a string; ADR-0051 stopped a free-recall *mention* from overriding it.

Three hard limits, all asserted as falsifiers in `scripts/measure_credit.py`:

* credit **never exceeds WEAK**, at any number of confirmations — a reader finding a field
  readable is not the page pinning it;
* credit **never touches STRONG**, which must come out of the run numerically unchanged;
* credit **may add evidence for a value the card already holds and may never introduce a value**.

Where the two readers split, the cell is not credited and not discarded: it goes to
`runs/adjudications-pending.json` and the UI's **Decide** view for a person. An approval settles
key 3 and nothing else — it cannot reach key 1, cannot exceed WEAK, and is pinned to the page's
fingerprint, so editing that transcript makes the decision stale and returns the cell to the queue.

**The rule those decisions are made under is written down** (ADR-0058, decided on the six cells the
2026-08-21 pilot produced): *accepting a venue evidences the preference that venue instantiates, at
WEAK and no higher, where the page names the attribute and the user assents to it in their own
words — and it never evidences a frequency*, so a field claiming a rate across outings
(`novelty_vs_loyalty`) is not settled by one recalled venue. Without a written rule the same
argument is had once per cell and the queue regenerates its own shape.

**Both verdicts settle the finding, for opposite reasons.** An approval credits the cell, so
`credit.absolve` demotes its leak: the map was wrong. A rejection credits nothing and the cell
stays UNEVIDENCED — but the leak stops blocking too, because the *reader* was wrong and a person
said so, which leaves nothing on the page to repair. The report appends a different sentence for
each so the two are never read as one. Only **fresh** decisions dismiss: a stale approval puts a
fabrication in the key, and a stale rejection silences a blocking finding about text nobody has
looked at.

**A credit that rests on a decision is not a credit that rests on a second reader**, and every
count that separates them has to keep them apart: `Credit.adjudicated_by` carries the decider onto
the cell, and the falsifier *no credit read against its card* is left free to fail on an owner
override rather than taught to exempt one. On the pilot it does fail, 3 of 12, and those three
cells are named in `runs/result-adjudication.txt`.

Every credit records **both reader models and the fingerprint of what each was shown**, because
two readers agreeing means nothing unless they were shown the same page.

## The editor, and why it is not in this path

`editor.py` repairs the *corpus*, not the map: it rewrites the messages a blocking finding names,
or — under ADR-0052 — deletes them where the reader marks the defect as one a deletion fixes.

**It does not move the boundary ADR-0009/0010 drew.** The editor's own gate calls are discarded
once they have accepted or rejected a candidate. The report a score rests on still comes from a
full, fresh `verify.audit` over the edited store, exactly as it would for a session nobody
touched. A gate is not a measurement.

One consequence matters for reading any before/after: **repairing a contradiction can convert it
into a leak.** A page that has stopped contradicting the card now *states* the field, and the map
calls that cell UNEVIDENCED — the defect does not leave the ledger, it changes column, into the one
credit absorbs. **A blocking count alone will understate the editor**, and the two stages have to be
read together.

The magnitude is store-dependent and should not be quoted from another run. v0.4 measured it at 12
of 20 new leak cells, with contradiction down 56% and leak up 40%. v0.5's pilot ran the same editor
over a portrait-authored store and traded **3 times in 29 accepted repairs**: contradiction 10 → 2 → 0 across
two passes, leak 21 → 13 → 14. Same mechanism, an order of magnitude apart. The editor logs each
trade as `EditResult.traded` (ADR-0049), so the count is read off the artifact rather than inferred
from a before/after diff.

**Since ADR-0059 the same is true one field over, and it matters more here than the trade does.**
A repair may leave a leak on a field it was not repairing — one, `editor.MAX_COLLATERAL` — and that
leak is recorded as `EditResult.collateral` for the same reason `traded` exists: the post-edit audit
reads a leak with **no memory of who wrote it**, so an editor-minted leak and an author-written one
are the same finding on the page. Any leak rate computed over an edited store — including
`runs/result-reader-n15.txt`'s per-session and per-UNEVIDENCED-cell rates by chain depth — is about
the author *and* the editor unless `collateral` is subtracted first.

The reverse move is refused: a leak repaired into a contradiction (ADR-0056), and a leak repaired
into another leak, which is written nowhere at all because the blocking count and credit's workload
are identical either side of it. That repair is logged `neutral` rather than `unresolved`, and the
distinction is a reporting one: `unresolved` counts as outstanding repair work, `neutral` counts as
a finding only the credit pass or a correction to the map can move. **Do not add them together.**

---

## Reporting units

| v0 | v0.1 |
|---|---|
| trajectory metrics aggregated per tier (§8.6) | **per subject**, primary |
| never pool across tiers (§8.9) | binned by **evidence density** `\|S(t)\| + \|W(t)\|` where binning is wanted |
| per-tier recoverability caveat table | dropped; recoverability per field, per subject |
| per-tier §7.4 profile gate | retired (below) |
| per-tier κ bands (§7.5) | κ over the audit sample; still a published diagnostic, not a blocker |

Density rather than depth, because depth bands would have to be invented and would not mean the
same thing for a 3-session and a 30-session subject; density is what actually drove the tier
profiles, and §8.3a already defends a threshold on exactly that quantity.

At 50 subjects, per-subject is a better-powered unit than v0's ~10–30 per tier — the §8.9 power
caveat was apologising for the tier split itself.

---

## What was retired, and why it is not coming back

**§7.4's evidence-grade distribution gate.** It compared a realized profile against a declared
target. The realized profile is now `EvidenceSchedule.profile()` — computed from the schedule that
was constructed to hit that target. It is the schedule checked against itself and it **cannot
fail**.

This is the same defect §7.4 already documents in medium's "a rising `strong` fraction across
checkpoints": monotonicity makes that true of every timeline whatsoever. Restating the gate over
depth or density bands reproduces it one level down. The profile is still published; it is a
description, not a check.

What can genuinely come out bad is the audit's disagreement rate, which is stage 3's job.

---

## The limitation this creates — publish it

The benchmark now rests entirely on **the schedule's weight model being right** about how many
observations pin a field. ADR-0010 measured this where the two records disagreed and the schedule
won: on cells every judge graded `strong` but the schedule called `weak`, unanimous judges named the
same value 60% of the time and hit gold 51%, against 89% / 72% on cells the schedule also called
strong. A judged `strong` there is above chance and well short of a pin.

That supports the decision. It does not make the assumption disappear, and the assumption is
load-bearing in a way it was not when a judge census sat downstream. **This belongs in §12's
published limitations, stated plainly**, with the audit's disagreement rate as the number that
bounds it.

Related: v0's map is judge-verified over a census, v0.1's is schedule-derived with a sampled error
rate. A consumer comparing map statistics across the two trees is comparing two instruments.

---

## The empty-set question — closed by ADR-0035, in the data

A field the persona does not carry — `allergies: []` — used to be permanently `unevidenced` at any
length, and §8.1 and §8.2 gave different answers about it. §8.1 called a twin that said `[]` a
fabrication even though it matched ground truth; §8.2 would not have flagged it, since `[]` is not
an *invented* constraint.

**No scoring rule changed.** ADR-0035 stopped the
data producing the ambiguous case. An absent hard constraint now gets one `disclosure` beat — the
bot asks, the user says there is nothing — so the cell reaches `strong` and the empty set becomes
the right answer for the ordinary reason. The rule, restated with nothing added:

| the transcript | the cell | correct twin answer |
|---|---|---|
| never raised it | `U` | `"unknown"`; committing to `[]` is a fabrication |
| asked, and the user said none | `S` | `[]`; `"unknown"` is now a miss |

That is exactly the owner's formulation: *unmentioned means unknown; mentioned-and-none means none.*

The onset is drawn normally rather than forced early, so short timelines still legitimately sit in
the first row — which is what keeps long-lived unevidenced constraint cells in the corpus.

Measured at `n=50, seed=7`: constraint cells evidenced by the final checkpoint went **39/200 → 138/200**,
and subjects with an entirely empty §8.2 gating track went **20/50 → 4/50**.
