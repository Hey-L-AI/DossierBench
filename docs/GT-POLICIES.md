# Judgement-based ground-truth policies

The gold in this benchmark is not purely mechanical. A set of **owner adjudications** decide, by
reasoning rather than by rule, what a transcript evidences and how a cell is graded. This file is the
single record of those calls, so the same question is not re-argued once per credit pass and so the
reasoning survives the person who made it.

**Why this exists separately.** Each policy also lives at its point of use (an ADR in the unreleased development tree, a docstring, a
dismissal note, a MANIFEST). Those are the authorities; this is the index. When they disagree, the
cited source wins and this file is stale — fix it.

**Twin-facing use.** Several of these are *twin-facing* — they describe what a faithful reader should
and should not commit — and the `[TWIN-FACING]` tag marks them; `[GRADER]` marks rules that govern gold
construction or scoring and would not go to a SUT. Where they stand in the SUT prompts
(`src/persona_twin/sut.py`), as of 2026-08-30:

- **v1** (`CARD_SYSTEM`, `MEMORY_EXTRACTION_SYSTEM`; the two read-outs `MEMORY_INTEGRATION_SYSTEM`
  and `BANK_EMISSION_SYSTEM` carry only the `[]` rule): 1.3 (assistant ≠ evidence), 1.4 (companion ≠
  user), 2.1/2.3 (`[]` vs `"unknown"`, split by constraint/taste field). v1 states 1.2 *backwards* —
  "chooses, accepts" counts as evidence.
- **v2** (submission kinds `prompted-v2`, `notebook-v2`, `memory-v2`, `mem0-v2`; constants
  `*_SYSTEM_V2`, selected by `prompt_version`): each v1 prompt with the conventions 1.2 (booking ≠
  liking), 1.3, 1.4, 1.5 (ceiling), 6.4 (literal level) — swapped into the evidence block of the two
  prompts that read the transcript (card, memory extraction), inserted as "what a memory on file
  licenses" into the two read-outs. For `mem0-v2` the read-out is the only lever: mem0 writes its
  own bank. Conventions only — the traps (6.2 latent values, 6.3 abstraction words) are
  deliberately not disclosed. **The nine paper runs use v2** (`prompted-v2` and `notebook-v2` on four
  backbones, `memory-v2` on GPT-5.6 Luna; `mem0-v2` was not run at n=50). Compare with
  `scripts/compare_suts.py` (paired ΔAUFC + cell transition table).

Status legend: **MECHANIZED** (a gate/rule in code) · **MANUAL** (a written reviewer judgement, not
enforced by code) · **HYBRID** (a code gate exists but cannot see the real defect, so a manual check
is mandated alongside it).

---

## 1. What counts as the user's own preference  `[TWIN-FACING]`

These govern whether a turn licenses a value in the user's gold. They are the most likely to become
SUT guidance, and the most contested among themselves — read them as one family.

### 1.1 Acceptance evidences the attribute, at WEAK, never a frequency
**Rule.** Accepting a venue evidences the preference that venue instantiates — *at WEAK and no higher*
— **when the page names the attribute and the user assents to it in their own words**. It never
evidences a frequency (accepting one recalled venue does not pin `novelty_vs_loyalty` or any
distribution).
**Why.** Without a written rule the same split cells get re-argued every credit pass; STRONG stays
with the schedule, which alone knows a beat was *placed*.
**Source.** ADR-0058 Decision 1; `docs/SCORING.md:294`. **MECHANIZED** as
the WEAK boundary in `credit`/`confirm`, applied by hand in the adjudication JSONs.

### 1.2 A booking accepted on other grounds does not license the attribute (booking ≠ liking)
**Rule (STRICT, owner, 2026-08-29).** A bot-offered venue booking the user accepts **for an unrelated
reason** — distance, a quiet room, table service, price, availability — does **not** license the
cuisine (or other attribute) the venue instantiates. Only the user voicing the attribute *itself* in
their own words does. The bot's word for a venue ("a Mediterranean spot") is never the user's word.
**Relation to 1.1.** This is not a reversal of 1.1 — it is its "in their own words, to the attribute
itself" clause read strictly. In 1.1 the user assents to *the attribute*; in a visit-to-like case the
user assents to *the venue* while their stated reason is something else. Assent to the venue is not
assent to the attribute.
**Why.** This is the same `set_inflation` the failure-mode catalog charges single-pass SUTs for; gold
must not commit what it penalizes a twin for committing.
**Source.** `docs/GT-REVIEW.md` (Flavor B); base `n50-flavorB-2026-08-29`
(`runs/provenance/n50-flavorB-2026-08-29/MANIFEST.md`). **MANUAL** policy, applied by
`runs/apply_flavorB_prune.py`. The general generation-side gate (route-1 `surfaced` record) LANDED
2026-08-30 (`n50-route1-surfaced-2026-08-30`, §3.2) — `card_at` now carries the surfaced-through-*t*
subset, so scored list cells no longer need a per-cell ≥2-judge check for the visit-to-like subset.

### 1.3 Bot scenery is not the user's act ("gen-011-s7 rule")
**Rule.** When the user books a venue the bot *describes* as doing dish/cuisine X, and the user orders
no dish and states nothing themselves, the bot's description neither evidences nor contradicts a user
constraint or preference. Bot prose is scenery.
**Why.** A dietary "contradiction" read off a venue the bot described is a misread of who spoke.
**Source.** `runs/dismissals-n50-union-2026-08-25.json` (gen-004-s11 "under the gen-011-s7 rule",
gen-005-s12). **MANUAL**, applied in dismissals. Same family as 1.2.

### 1.4 A companion's constraint stays out of the user's gold (third-party)
**Rule.** A constraint or preference stated as a companion's is THIRD_PARTY and stays out of the
user's gold, even when the user relays it and accommodates it. A reader *declining to say* whose a
value is (AMBIGUOUS) is not accusing anyone — a hedge is never an over-attribution.
**Why.** The reader's job is to attribute, not to guess; a hedge must not be scored as a positive
attribution to a third party.
**Source.** `verify.py` `AUDIT_SYSTEM` (`whose`); ADR-0036; the `misattributed` carve-out
(`RESUME.md:84-92`). **MECHANIZED** (reader `whose`) + **MANUAL** adjudication. See 6.1.

### 1.5 A level below an ordinal ceiling neither evidences nor contradicts the ceiling
**Rule.** For an ordinal *ceiling* field, a request for a level **below** the card's value introduces
no credit and the cell stays UNEVIDENCED; accepting a venue below the ceiling cannot contradict it
either.
**Why.** A ceiling is an upper bound; observing something under it says nothing about where the bound
sits.
**Date/Source.** 2026-08-24; `RESUME.md:152`; `runs/finding-dismissals.json` (gen-013-s24),
`runs/dismissals-n50-union-2026-08-25.json` (gen-003-s7). **MANUAL**, applied in dismissals.

### 1.6 One instance is not a rate
**Rule.** A single observed occasion *narrows* a distribution at WEAK but never *pins* it; a single
visit in a low-but-nonzero bin does not contradict a "no particular frequency" claim.
**Why.** A distribution is not pinned by one line; a distribution judged at checkpoint 0–1 where one
observation forces a point-mass is not a model failure (the multi-visit shape is not yet inferable).
**Date/Source.** 2026-08-23; `RESUME.md:126-135`; `runs/finding-dismissals.json` (gen-006-s2);
`docs/FAILURE-MODES.md:30-35` (low-N collapse). **MECHANIZED** in `verify._on_page`/`reconcile`
(the distribution beat reconciles on the occasion it narrows).

---

## 2. Empty set vs unknown (abstention)  `[TWIN-FACING]`

### 2.1 Unmentioned means unknown; mentioned-and-none means none
**Rule.** Never raised → the cell is UNEVIDENCED and the faithful value is `"unknown"`; committing `[]`
is a fabrication. Asked and the user said none → the cell is STRONG and the faithful value is `[]`;
`"unknown"` is now a miss. `[]` is a *commit* ("has none"), distinct from the `"unknown"` abstention
sentinel.
**Source.** ADR-0035; `docs/SCORING.md:423-426`; `sut.coerce_value`. **MECHANIZED**.

### 2.2 A commit on an UNEVIDENCED cell is a fabrication even if it matches gold; `[]` is not exempt
**Rule.** Any positive commit on a cell the timeline has not evidenced is charged a fabrication, even
when the committed value equals the hidden card value. `[]` gets no exemption.
**Why.** Exempting `[]` would let a SUT assert the safe empty set on every unevidenced list field for
free (§8.1). This is the whole point of the calibration penalty.
**Source.** `docs/GT-REVIEW.md` (S1 WONTFIX, 2026-08-27); `scorer.py`; `docs/SCORING.md:129`.
**MECHANIZED**. WONTFIX.

### 2.3 The `[]`-vs-`unknown` fix belongs in the SUT prompt, not the scorer
**Rule (pending).** Constraint fields → emit `[]` only after the person declares none, else
`"unknown"`; taste fields → never `[]`, emit `"unknown"` when nothing was shown. This is a prompt
change, deliberately not a scoring change (see 2.2).
**Source.** `docs/GT-REVIEW.md` (2026-08-27). **MANUAL/PROMPT**, re-run pending.

---

## 3. Gold construction — what the answer key may hold  `[GRADER]`

### 3.1 Every scored list element must have a USER-side surface (phantom prune)
**Rule.** On a scored (WEAK/STRONG) taste-list cell, every gold element must be surfaced by a USER
turn in the served transcript; an element survives only on ≥2-judge agreement. Prune the rest from
`card`/`card_at`.
**Why.** A beat records the *field* it touched, not the *element*, so a field flips WEAK/STRONG on
aggregate beat weight while carrying elements no turn voiced — and STRONG has no Jaccard floor
(see 7), so a faithful reader scores below abstaining.
**Source.** route 2 (2026-08-27, never-STRONG cells); Flavor A (2026-08-29, STRONG cells,
`n50-flavorA`); Flavor B (2026-08-29, the visit-to-like subset, 1.2 above); the **STRONG-cell audit**
(2026-08-29, `n50-strongaudit`) closed the full 39-cell worklist — 5 more USER-never-voiced elements
pruned (gen-005 `supper_club`, gen-013 `taiwanese`, gen-031 `vegan_restaurant`/`german`/`cajun`).
**MECHANIZED** for the audited cells (`sut._evidenced_gold_at` / `_PRUNED_LIST_FIELDS`); the 39-cell
STRONG worklist is now **DONE**. The generation-side route-1 gate also **landed 2026-08-30** (3.2).
**Method rule (2026-08-29):** the ≥2-judge instrument is a surface/token test with a measured 1/6
false-positive rate (it missed gen-011 `filipino`, surfaced indirectly as the dish *sisig*) — run an
**indirect-reference adversarial pass** (a signature-dish / paraphrase hunt) before removing any
element, and record any rescue as an owner keep.

### 3.2 A list's scored value at t is the subset surfaced *through t* (route-1 timing)
**Rule.** A list field scored at checkpoint *t* holds the union of elements surfaced through *t*,
not the full ever-evidenced union carried from the first WEAK checkpoint.
**Why.** gen-014's 8 genuine cuisines surface c2→c15; a reader holding the c2 subset scores Jaccard
1/8 and loses to abstention.
**Source.** `docs/GT-REVIEW.md`; `RESUME.md` (v1 blocker, "THE GATE"). **DONE / MECHANIZED
(2026-08-30)** — the canonical base `n50-route1-surfaced-2026-08-30` rewrote `card_at` on the four
taste-list fields to the surfaced-through-*t* subset (~701 cell values across 79 subject×field cells
on 33 subjects; final `card` and grades byte-identical, no reader re-key), and `sut._evidenced_gold_at`
/ `_PRUNED_LIST_FIELDS` read it. Verified in-data (gen-014 `cuisines_liked` grows
`[]`→`[peruvian]`→…→8 across c0–15). `docs/provenance/CANONICAL.md`.

### 3.3 The scalar analog: `card_at[t]` is the point-in-time level, not the end state
**Rule.** For a per-checkpoint scalar ordinal, `card_at[t]` must be the highest level readable from
USER turns *through t*, not the final card value written early.
**Why.** Writing the end state at every checkpoint charges a faithful reader who reads the
checkpoint-local level (gen-020/024 `solo_dining_comfort`, gen-022 `formality_ceiling`).
**Source.** scalarfix (2026-08-28); `sut._CARD_AT_GOLD_FIELDS`; Gate 1 `_verify_scalar_card_at`.
**HYBRID** — Gate 1 catches malformed edits; the "readable by *t*?" check (Gate 2) is **MANUAL**,
pending.

### 3.5 A scored grade needs a USER-side line; the onset is the checkpoint of that line
**Rule.** A WEAK/STRONG grade on a scalar or list field needs a USER turn at or before *t* that
voices the value (or, for a list element, the element in the user's own words or a signature of it,
e.g. "not tapas" → `spanish` avoided). If no user turn ever voices it, the field stays UNEVIDENCED
and the card value stays latent (6.2). The grade turns on at the checkpoint of the voicing line, not
at the checkpoint where the schedule placed its beat.
**Why.** A beat records the field it touched, not whether the user voiced it. Bot prose about prices
(1.3) or a schedule beat in a later session moved the grade, so a system that read the user's own
line was charged a fabrication or a misread.
**Source.** ADR-0066 (gen-037 `deal_seeking` WWW→UUU); ADR-0067, from the 2026-10-14 full pass
(`docs/failure-modes/FULLPASS-2026-10-14.md`): gen-006 and gen-017 `deal_seeking` → UNEVIDENCED
throughout (no user-side deal line at any checkpoint), gen-007 `deal_seeking` WEAK from c7 not c8,
gen-002 `cuisines_avoided` `spanish` from c0 not c8. **MANUAL** — found by reading failure cells,
so only cells some system got wrong have been checked. A whole-key sweep for `deal_seeking`
(3 of the 6 `gold_wrong` cells) is proposed, not run.

### 3.4 v0.5 ships zero scripted corrections
**Rule.** `schedule.MAX_CORRECTIONS = 0`; no gold value is replaced mid-timeline.
**Why.** All 44 corrections in the union base were phantom — the pre-correction value was never
authored into the transcript, so a correct reader was charged a misread before the flip.
**Source.** `n50-nocorr` MANIFEST; `CANONICAL.md`. **MECHANIZED**.

---

## 4. Crediting and monotonicity  `[GRADER]`

### 4.1 Judges and credit may only demote, never promote (§7.3)
**Rule.** A reader finding a cell *stronger* than the schedule placed it produces a `disagreement`
note and changes nothing. Only demotion (take-away, move-to-another-person, find-one-nobody-scheduled)
is taken at face value.
**Source.** ADR-0009/0010; `docs/SCORING.md:213-217`; `verify.py`. **MECHANIZED**.

### 4.2 Credit's three keys, all required; key-3 fails closed
**Rule.** Credit a UNEVIDENCED cell at WEAK only if (1) the card holds a non-empty value, (2) the page
reads as carrying it with no contradiction, and (3) a second reader on a different model independently
reads it *and agrees with the card's value*. Credit never exceeds WEAK, never touches STRONG, never
introduces a value the card lacks. No second reading = refusal, not acceptance.
**Source.** ADR-0046/0047; `docs/SCORING.md:260-291`; `scripts/measure_credit.py`. **MECHANIZED**.

### 4.3 A refusal settles a finding as surely as a credit
**Rule.** Both verdicts dismiss the leak: approval credits the cell (the map was wrong), rejection
credits nothing but the finding stops blocking (the reader was wrong). Only **fresh** decisions
dismiss — a stale approval is fabrication in the key; a stale rejection is a silenced blocking finding.
**Source.** ADR-0058 Decision 2; `docs/SCORING.md:300-306`. **MECHANIZED** (`credit.absolve`).

### 4.4 A credit carries forward; no WEAK islands
**Rule.** A credit must carry forward to all later checkpoints (`carried_from`) and never raise a
single checkpoint in isolation. The key may not expect a twin to know a field at *t* and to have
forgotten it at *t+1*.
**Why.** 37 of 48 credits once wrote islands, failing `EvidenceMap.validate()` on 22/50 subjects; the
oracle can't catch it (it reads the same map).
**Source.** 2026-08-25; `RESUME.md:106-113`; `sut._verify_grade_leads_value`; `sut.evidence_map`
validates on load. **MECHANIZED**.

### 4.5 Free recall may add a reading, not override a direction check
**Rule.** A targeted card-withheld ask that reads *against* the card removes the cell, even if free
recall named the field. Naming a field is recall, not direction.
**Source.** ADR-0051; `docs/SCORING.md:278-279`. **MECHANIZED** (`confirm.veto`).

---

## 5. Field definitions with grading consequences  `[GRADER]`

### 5.1 A tolerance ladder measures deterrence, not desire
**Rule.** `wait_tolerance`'s top anchor was reworded to drop "and refuses a table that skips it" — a
second trait (an anti-reservation disposition) riding on the ladder. Every ordinal's top rung must
refuse the low end of *its own* scale, so the ladder measures one quantity end to end.
**Why.** Every `wait_tolerance` contradiction ever filed quoted that clause; the fix answers a
definition, not a defect.
**Source.** ADR-0061 (2026-08-23); `RESUME.md:1181-1202`. **MANUAL/definition** (no gold value moved).

### 5.2 Question budget: floor 0, ceiling 3, ceiling applies even to distractors
**Rule.** The permitted bot questions are the beats alone; a one-question or silent session is not a
defect (floor dropped to 0 in v0.5). The ceiling of 3 is kept universal, distractors included —
exempting them silenced a genuine 4-question catch (gen-003-s11).
**Source.** `verify.py` `QUESTION_BUDGET`. **MECHANIZED** (report-only).

### 5.3 An unbriefed intake ask that forces denial of a real constraint is blocking
**Rule.** An unbriefed *intake* ask that makes the user *deny* a hard constraint they actually carry
puts the negation of ground truth on the page and is BLOCKING (`denied_constraint`). A scheduled
`disclosure` beat (field in `DISCLOSURE_PROBES`) is the carve-out, per-field. Unbriefed *preference*
asks are advisory only.
**Source.** ADR-0035; `verify.py:419-504`. **MECHANIZED**.

---

## 6. Failure-adjudication line-drawing (from the 55%-artifact cull)  `[GRADER]`

These decide whether a SUT error is a GENUINE model failure or a KEY_ARTIFACT (the gold's fault).

### 6.1 The `misattributed`/`reconcile` carve-outs
**Rule.** `misattributed` fires on a *positive* attribution only, never on a hedge (AMBIGUOUS =
reader declining to say whose it is). The distribution beat reconciles on the occasion it narrows, and
the first-person-plural voice of a shared occasion (a table for a party) is not `misattributed`.
**Note.** `RESUME.md:90-92` flags both carve-outs (2026-08-24 distfix, 2026-08-29 hedge) as "drawn
around the case that had been noticed rather than around the reason" — the owner considers the
`reconcile` family under-generalized and open to re-examination.
**Source.** `verify.py`; `RESUME.md:84-92, 126-135`. **MECHANIZED**.

### 6.2 A latent (hidden-prior) card value is intended; committing it is still a fabrication
**Rule.** A field UNEVIDENCED at every checkpoint that still carries a non-empty card value is the
intended card/timeline gap (the card is the whole person; the timeline surfaces only part). Committing
one is a genuine fabrication *even when it matches the hidden card* (249 such values, 39/50 subjects).
**Source.** `docs/GT-REVIEW.md` (B1, 2026-08-27); `RESUME.md:25-26`. **MANUAL/by-design**.

### 6.3 An abstraction-trap turn evidences its tagged trait, not concrete sibling fields
**Rule.** A turn authored to evidence an abstraction (e.g. `participation_vs_spectating` from
"pasta-making evening / climbing the walls at a wine thing") does not license concrete event commits
(`cooking_class`, `wine_tasting`). A SUT double-dipping those words into event fields is a genuine
fabrication, even on a lucky gold match.
**Source.** `docs/GT-REVIEW.md` (Tiers A&B, 2026-08-27). **MANUAL**.

### 6.4 A sibling-vocab mismatch is a genuine misread, not an artifact
**Rule.** Where two near-sibling levels are both actively used and the user used the distinct words
(`market` vs `food_festival`; `japanese` ⊃ `sushi`), a SUT picking the wrong one is a genuine misread —
not a taxonomy defect.
**Source.** `docs/GT-REVIEW.md` (Tier C1 WONTFIX); `docs/FAILURE-MODES.md`. **MANUAL**.

### 6.5 Scope calls on the scalar-ordinal fix
**Rule.** Only clear distance-2 grade-leads-value cells were fixed. Distance-1 cells flagged by
cross-model consensus were left unfixed (consensus is shared-bias risk, not proof — cf. gen-036
`deal_seeking`, a confirmed false positive; distance-1 is partial-credit-softened). gen-022 was left
WEAK, not raised to STRONG (un-raisable by 4.1, and raising it is a difficulty knob, not a correctness
fix).
**Source.** `docs/GT-REVIEW.md` (2026-08-28). **MANUAL**.

---

## 7. The mechanical rule these all lean on

**Jaccard softening on WEAK, none on STRONG.** WEAK list cells are scored by Jaccard overlap with a
`WEAK_CREDIT` abstention floor; STRONG cells hold the twin to the *exact* gold set with no floor. This
is why STRONG is "the worst place for a phantom" and is the mechanical basis for 1.2, 3.1, and 6.5.
**Source.** `scorer.py`; `docs/SCORING.md:126-135`. **MECHANIZED**.

---

## 8. Open tensions to revisit

- **The `reconcile` family is under-generalized** (6.1) — two carve-outs drawn around noticed cases,
  not their reason. Worth a pass on the rest of `reconcile`.
- ~~**Route-1 list-timing (3.2) is unbuilt**~~ **DONE 2026-08-30** — `n50-route1-surfaced-2026-08-30`
  rewrote `card_at` on the four taste-list fields to the surfaced-through-*t* subset; this made 3.1
  structural at generation time and closed the timing dimension on genuine multi-element cells.
- **Low-N distributions (scoring side) — DONE 2026-08-30.** A WEAK distribution cell is now excluded
  from both the agreement and the fabrication term by the §8.1a scorer carve-out (ADR-0065): score
  distributions only at STRONG (`STRONG ⟺ N≥5`). The answer key is unchanged; the carve-out lives in
  `scorer.score_checkpoint`, so any SUT score from before this date is pre-carve-out and must be
  re-run before comparison. The `route1c` grade-flip corpus that would have done this in-key is
  **abandoned/unadopted** — the carve-out replaced it. Generation-side, §1.6 already withholds a pin
  from a single occasion.
- ~~**The 35 remaining STRONG worklist cells** (3.1) await the ≥2-judge full-transcript pass.~~
  **DONE 2026-08-29** — the full 39-cell STRONG worklist was audited (`n50-strongaudit`); 5 elements
  pruned, gen-011 `filipino` kept on the indirect-reference rescue.
- **Distribution gold is the latent histogram, not the observed one (open, 2026-10-14).** A STRONG
  distribution is scored against the persona's full histogram (ADR-0065). On gen-035 and gen-047
  `visit_time_distribution` the readers found that shape contradicts every occasion served by c5
  (gen-035: five of five at the weekend against 51% weekday gold). ADR-0067 left them alone because a
  per-cell edit would make them the only empirically keyed distributions. Whether to score against
  the observed shape is a corpus-wide decision.
- **Wiring the `[TWIN-FACING]` subset into SUT prompts** — done for all four paid kinds as prompt
  version v2 (see the header); the nine paper runs use it.
