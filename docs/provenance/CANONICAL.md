# The canonical base

**`n50-tagfix-2026-10-15`** — the full-pass fix key over the fixed store (2026-10-15).

Answer key `runs/corpus-n50-fullpassfix-2026-10-14.json` and model-free map
`runs/corpus-n50-fullpassfix-map-2026-10-14.json`, both byte-identical to `n50-fullpassfix-2026-10-14`
(described below). What moved is the store: 10 doubled speaker labels (`[5] BOT: [5] BOT: ...`) left by the
2026-08-25 hand edits were removed from 5 sessions, and the nine paper runs were re-run on those 5 subjects
from the edited checkpoint on. Decision record: **ADR-0068**. Record: [`n50-tagfix-2026-10-15.md`](n50-tagfix-2026-10-15.md)
(hashes: `n50-tagfix-2026-10-15.sha256`). Store pinned by `runs/store-n50-tagfix-2026-10-15.tar.gz`. Reader cache: the 10
verdicts on the fixed sessions were re-stamped without model calls (`runs/rekey_cache_tagfix.py`), so
the dataset browser (`ui/build_ui.py`, not shipped) reads 612/612 sessions, 0 stale, blocking 0, and
reproduces the key on all 14,688 cells.

## The key it carries: n50-fullpassfix-2026-10-14

**`runs/corpus-n50-fullpassfix-2026-10-14.json`** — the full-pass fix base (2026-10-14).

Supersedes `n50-gtfix-2026-08-30`. Applies the **4 gold fixes** from the 2026-10-14 full failure-mode
pass (`docs/failure-modes/FULLPASS-2026-10-14.md`), owner-approved 2026-10-14. Decision record:
**ADR-0067**. Applied by a one-off apply script in the development tree:
- gen-007 `deal_seeking` WEAK `notices_value` from c7 (was c8; the c7 user line is the evidence);
- gen-002 `cuisines_avoided` `[spanish]` WEAK from c0 (was c8; "though not tapas" at c0);
- gen-006 and gen-017 `deal_seeking` → UNEVIDENCED everywhere (latent leaked; precedent gen-037).

Final cards unchanged, so the reading, credit arms, dismissals and adjudications are the gtfix ones,
byte-identical. Not fixed: gen-035/gen-047 `visit_time_distribution` (a design question: re-keying them to the visits shown would make them the only empirically keyed distributions).
Gates: validate 50/50 · verify_gold 0 · oracle A=1.0 all cps · **coherence 14,688/0 · blocking 0** ·
prompts moved 0 · pytest 956 · free arms oracle 1.000 / abstain 0.000.

| use | path |
|---|---|
| answer key | `runs/corpus-n50-fullpassfix-2026-10-14.json` |
| base | [`n50-fullpassfix-2026-10-14.md`](n50-fullpassfix-2026-10-14.md) (the manifest; hashes in `n50-fullpassfix-2026-10-14.sha256`, 7/7 OK) |
| model-free map | `runs/corpus-n50-fullpassfix-map-2026-10-14.json` — gtfix map + the same 4 fixes |
| reading | `runs/verify-n50-both` (unchanged; no prompt moved) |
| credit arms | `runs/result-credit-union-{opus,sonnet}arm-gtfix.json` (unchanged) |
| dismissals / adjudications | `runs/dismissals-n50-union-2026-08-25-gtfix.json` · `runs/adjudications-n50-handedit-2026-08-25-gtfix.json` (unchanged) |
| store | `work/authored/claude-opus-5`, pinned by `runs/store-n50-tagfix-2026-10-15.tar.gz` (2026-10-15: 10 doubled speaker labels removed from 5 sessions; [`n50-tagfix-2026-10-15.md`](n50-tagfix-2026-10-15.md)). Was `store-n50-handedit-2026-08-25.tar.gz`. |
| apply / check scripts | one-off apply script, `runs/verify_coherence_fullpassfix.py` |
| judgement-based GT policies | `docs/GT-POLICIES.md` |
| scoring against it | `python scripts/score.py` (its default corpus is the released answer key) |
| paper runs, re-scored | `runs/result-v2only-all-n50-tagfix-2026-10-15.json` / `runs/_v2only-all-n50-tagfix.log` (five subjects re-run after the store fix; see [`n50-tagfix-2026-10-15.md`](n50-tagfix-2026-10-15.md)). Before it: `runs/result-v2only-all-n50-fullpassfix-2026-10-14.json`. |
| published release | `runs/hf-release-2026-10-15/` = this base + the store fix, built and uploaded 2026-10-15 to `tangents/dossierbench` (commit fe2c459; 8 changed files sent, hub verified: 620 files, no `._*` sidecars, gold and the five transcripts hash-equal to the local release). Previous: `runs/hf-release-2026-10-14/`, uploaded 2026-10-15 to `tangents/dossierbench` (commit 6acc1c8; old gtfix gold removed from the tip, kept in history). Built by `scripts/build_release.py`. |

> **Re-score before comparing.** Any SUT score computed against gtfix is stale on 4 subjects. Re-run
> `scripts/score.py` (free cache replay) before comparing SUTs.

---

## Superseded: n50-gtfix-2026-08-30

**`runs/corpus-n50-gtfix-2026-08-30.json`** — the GT-fix base (2026-08-30).

Supersedes `n50-route1-surfaced-2026-08-30`. Applies **all 18 verified gold-defect fixes** from
the 2026-08-30 failure-mode re-analysis (a write-up and worklist in the development tree, not
shipped). Each was adjudicated on the full transcript,
adversarially verified, and pre-commit-confirmed by independent Fable-5 sub-agents. Applied by
a one-off apply script (`APPLY_CONTESTED=True`; element-level ops; 15 subjects; no collateral).
The edits, by kind:
- **booking/bot ≠ liking** (onset move / prune, rule 1.2/1.3): gen-014 `japanese` c6→c15, gen-010
  `german` c0→c1 + `ramen` c11→c0, gen-031 `caribbean` c3→c12, gen-033 prune `taiwanese` + `portuguese`
  c2→c0, gen-027 prune `japanese`(zero-support)+`portuguese`, gen-047 (below).
- **sibling-vocab** (rule 6.4): gen-040 `japanese`→`sushi`, gen-019 `japanese`→`ramen` (c5–6),
  gen-020 `food_festival`→`market`.
- **user-voiced element gold omits** (add + WEAK, rule 7a): gen-017 `event_types_avoided`+=`tasting_menu`
  & `cuisines_liked`+=`vietnamese`, gen-024 `event_types_avoided`+=`tasting_menu`, gen-027
  `event_types_liked` `dance_night` WEAK, gen-030 `cuisines_liked`+=`spanish`, gen-039
  `event_types_liked`+=`dance_night`.
- **latent leaked / contradicted** (regrade): gen-037 `deal_seeking` WWW→UUU, gen-047
  `participation_vs_spectating` `watches_only`→`joins_readily` WEAK from c6.
- **contested reversals — owner adopted A, 2026-08-30** (each reversed an established credit-layer
  decision, so re-checked before applying): **gen-013 `allergies` c11–14 → UNEVIDENCED** (bot
  "no-wheat note", rule 1.3 — a full sweep confirmed the user says nothing wheat-adjacent before c11;
  reverses a two-reader key-3 credit) and **gen-024 `cuisines_liked` c2 burgers → c3** (bot-named on a
  dairy-driven venue pick, STRICT 1.2 — reverses a 2026-08-25 owner adjudication that predated 1.2).

**Reconciliation (full ceremony, all green).** The 9 grade edits are map corrections, so the same 18
edits were applied to the model-free map (`corpus-n50-gtfix-map-2026-08-30.json`). The card changes to
7 subjects re-fingerprinted their reader findings, so the verdict cache (110 verdicts,
`runs/rekey_cache_gtfix.py`) and the dismissals + adjudications (`runs/rekey_decisions_gtfix.py`) were
re-stamped — inert (card-line diffs only; 0 refused). The two contested reversals had their credits
removed from the arms and their leaks dismissed by `runs/reconcile_contested_gtfix.py` (gen-013-s11
allergies ×2 arms, gen-024-s2 cuisines_liked ×1 arm + its owner adjudication dropped; 2 owner
dismissals filed). Gates: EvidenceMap.validate 50/50 · verify_gold 0 · oracle A=1.0 all cps ·
**build_ui coherence 14,688/0 · blocking 0 · stale verdicts 0** · pytest 956. Carries the §8.1a low-N
distribution carve-out (ADR-0065) as before.

This file exists so "which one is canonical" is a fact in the tree rather than a claim in a
paragraph somewhere. Bases were superseded in a week, and more than one of them was
described as canonical in a file that was not updated when it stopped being true.

| use | path |
|---|---|
| answer key | `runs/corpus-n50-gtfix-2026-08-30.json` |
| base | `runs/provenance/n50-gtfix-2026-08-30/` — read its `MANIFEST.md`; `SHA256SUMS` 7/7 OK |
| model-free map | `runs/corpus-n50-gtfix-map-2026-08-30.json` — the schedule's own + the 9 grade corrections (ADR-0034/0066) |
| reading | `runs/verify-n50-both` (verdict cache re-keyed to the gtfix cards; 0 stale) |
| credit arms | `runs/result-credit-union-{opus,sonnet}arm-gtfix.json` (gradefix arms minus the 3 reversed credits — gen-013-s11 allergies ×2, gen-024-s2 cuisines_liked; build_ui reproduces the key on all 14,688 cells, 0 mismatches) |
| dismissals / adjudications | `runs/dismissals-n50-union-2026-08-25-gtfix.json` · `runs/adjudications-n50-handedit-2026-08-25-gtfix.json` (re-keyed) |
| store | `work/authored/claude-opus-5`, pinned by `runs/store-n50-handedit-2026-08-25.tar.gz` |
| apply / re-key scripts | one-off apply script, `runs/rekey_cache_gtfix.py`, `runs/rekey_decisions_gtfix.py` |
| judgement-based GT policies | `docs/GT-POLICIES.md` — the owner adjudication rules the gold rests on |
| browsing it | `ui/dataset.html` — `python ui/build_ui.py`, no arguments |
| scoring against it | `python scripts/score.py` (its `DEFAULT_CORPUS` is the answer key) |
| published release | `runs/hf-release-2026-08-30/` — REBUILT 2026-08-30 from THIS base (`scripts/build_release.py`; gold sha256 == this `SHA256SUMS`, 612-cp round-trip, `shasum -c` 618/618, `datasets` parses 50 rows). **UPLOADED 2026-08-30 → https://huggingface.co/datasets/tangents/dossierbench** (public; gold sha256 verified on the hub) |

> **Re-score before comparing.** Any SUT score computed before 2026-08-30 (or against route1-surfaced)
> is stale — 16 gold cells moved. Re-run `scripts/score.py` (free cache replay) before comparing SUTs.

---

## Superseded: n50-route1-surfaced-2026-08-30

**`runs/provenance/n50-route1-surfaced-2026-08-30/`** — read its `MANIFEST.md`.

This supersedes `n50-gen002fix-2026-08-29` and adopts the **route-1 surfaced gate**: `card_at` on nine
preference fields rewritten to the value the transcript has surfaced by each checkpoint (route-1 lists +
`formality_ceiling`/`solo_dining_comfort`; route-1b ordinals `deal_seeking`/`novelty_vs_loyalty`/
`noise_preference`). Grades, credit, reading, store and the final `card` are byte-identical to gen002fix,
so NO reader re-key was needed. **route1c (the low-N distribution guard) is NOT adopted** — held for
owner review; its `runs/corpus-n50-route1c-*` files are left untouched and unreferenced here.

**Scoring against this key carries the §8.1a low-N distribution carve-out (ADR-0065), as of
2026-08-30.** The answer key is unchanged by it — the carve-out lives in `scorer.score_checkpoint`,
which excludes a WEAK *distribution* cell from both the agreement and the fabrication term (it reads
the STRONG grade already in this map; distribution `STRONG ⟺ N≥5` here).

| use | path |
|---|---|
| answer key | `runs/corpus-n50-route1-surfaced-2026-08-30.json` |
| base | `runs/provenance/n50-route1-surfaced-2026-08-30/` |
| reading | `runs/audit-n50-union-absolved-adjudicated.json` (blocking 0; unchanged from the union base) |
| credit arms | `runs/result-credit-union-{opus,sonnet}arm-gradefix.json` (byte-identical to gradefix; `runs/verify_coherence_route1_surfaced.py`) |
| store | `work/authored/claude-opus-5`, pinned by `runs/store-n50-handedit-2026-08-25.tar.gz` |
| model-free map | `runs/corpus-n50-route1-surfaced-map-2026-08-30.json` — the schedule's own, no reader in it (ADR-0034) |
| judgement-based GT policies | `docs/GT-POLICIES.md` |

**gen-002 solo_dining_comfort correction (2026-08-29).** Supersedes strongaudit. The full-conversation
GT double-check over `docs/FAILURE-MODES.md` (34 highest-convergence "models beat gold" cells) found
gen-002 `solo_dining_comfort` gold `enjoys_solo` — WEAK-graded and scored at all 16 cps — unsupported:
no USER turn expresses *enjoying* solitude (the rung-4 requirement); every solo meal is
counter-order-and-carry and he brings company whenever available. 5 distinct single-pass models all read
`takeaway_or_counter`. Three full-transcript reads split 2–1 (reader + adversarial Opus defender FALLS →
`comfortable_at_table`; a fresh Fable-5 read kept `enjoys_solo`); **owner chose the downgrade to
`comfortable_at_table`**, per-checkpoint `takeaway_or_counter` c0–4 / `comfortable_at_table` c5–15 (solo
table-sitting first surfaces c5). Same family as the scalarfix cells (gen-020/024/022). Gates: validate
50/50 · verify_gold 0 · oracle 1.000 every cp · coherence 14,688/0 · 0 stale · blocking_after 0 · suite
949 passed, 2 skipped. Map + 32 verdicts re-keyed (0 refused, 0 skipped); 0 decisions needed re-keying.
`runs/apply_gen002fix.py` (key), `runs/promote_gen002fix.py` (map + re-key),
`runs/verify_coherence_gen002fix.py`. Grades, credit arms, reading, store, and all owner decisions
inherited unchanged from strongaudit. Pre-re-key cache: `runs/verify-n50-both-pre-gen002fix-2026-08-29.tar.gz`.

**Published release (rebuilt 2026-08-30).** `scripts/build_release.py` packages this base for HuggingFace as
**DossierBench** into `runs/hf-release-2026-08-30/`. It copies the answer key and the model-free map
**byte-identical** (sha256 asserted against this base's `SHA256SUMS`), ships the 612 served transcripts
trimmed to what a submission is served, and emits a `data/subjects.jsonl` view (verified to round-trip
to the canonical gold on all 612 checkpoints) for `datasets.load_dataset`. Decisions: scope = answer key
**+** model-free map; license **CC-BY-4.0**; all 50 subjects public (no holdout). The builder decides
nothing — it derives from whatever base this file names, so re-point it here if the canonical base moves.
The release is uploaded to https://huggingface.co/datasets/tangents/dossierbench (see the table above).

**STRONG-cell audit prune (2026-08-29).** Supersedes flavorB. The Flavor A/B passes hand-pruned a few
STRONG cells by inspection; this is the **systematic ≥2-judge full-transcript pass over ALL 39 STRONG
multi-element taste cells** (`runs/route2-strong-audit/`), closing the "35 remaining worklist cells"
item (`docs/GT-POLICIES.md` 3.1). It flagged 6 candidate phantoms across 4 cells; a **Fable-5
indirect-reference adversarial pass** then RESCUED one — gen-011 `cuisines_liked` `filipino`, which the
user surfaces indirectly by naming the dish *sisig* at cp8 (a false negative from both the token scan
and the opus+sonnet judges; **kept**, owner call, burden of removal not met). The remaining **5
confirmed USER-never-voiced elements on 3 STRONG cells** were removed from `card`+`card_at`: gen-005
`event_types_liked` −`supper_club`, gen-013 `cuisines_avoided` −`taiwanese`, gen-031 `cuisines_liked`
−`vegan_restaurant`/`german`/`cajun`. No cell prunes below 2 elements (all stay STRONG). Grades, credit
arms, reading and store inherited unchanged from flavorB; each of the 3 subjects' final card moved a
reader fingerprint (138 verdicts re-keyed across both arms, 0 refused, 0 skipped — every diff confined
to the touched taste-list card line; the one verdict naming a pruned token, gen-013-s4 opus, is a reader
note *corroborating* the prune). Oracle 1.000 · verify_gold 0 · validate 50/50 · coherence 14,688/0 · 0
stale · blocking 0 · suite 934/3-skip. `runs/apply_strongaudit_prune.py` (key), `runs/promote_strongaudit.py`
(map + re-key), `runs/strongaudit/rekey_decisions_strongaudit.py` (4 dismissals re-keyed, 0 refused),
`runs/strongaudit/verify_coherence_strongaudit.py`. **Scope: the 5 confirmed elements.** Route-1
list-timing stays OPEN in `docs/GT-REVIEW.md`. Pre-re-key cache:
`runs/verify-n50-both-pre-strongaudit-2026-08-29.tar.gz`.

**Flavor-B visit-to-like prune (2026-08-29).** Superseded by the STRONG-cell audit above. The 39 STRONG taste cells the
route-2 filter skipped also included **Flavor B**: `cuisines_liked` elements gold credited from a
bot-offered venue booking the user accepted on other grounds, or from the bot's word for a venue —
never a USER-voiced liking (the `set_inflation` the catalog charges SUTs for). **Owner policy: STRICT
— booking ≠ liking** (`docs/GT-POLICIES.md`). 4 elements on 3 cells removed from `card`+`card_at`
(gen-029 indian/filipino, gen-013 filipino, gen-010 mediterranean), each verified by a full-transcript
walk. Grades, credit arms, reading and store inherited unchanged from flavorA; 160 verdicts re-keyed
across both arms (0 refused, 0 skipped — every diff confined to the `cuisines_liked` line), 4
dismissals + 2 adjudications re-keyed (0 refused). Oracle 1.000 · verify_gold 0 · validate 50/50 ·
coherence 14,688/0 · 0 stale · suite 934/3-skip. `runs/apply_flavorB_prune.py` (key),
`runs/promote_flavorB.py` (map + re-key). **Scope: Flavor B only** — the remaining 35 worklist cells
(≥2-judge pass) and route-1 list-timing stay OPEN in `docs/GT-REVIEW.md`. Pre-re-key cache:
`runs/verify-n50-both-pre-flavorB-2026-08-29.tar.gz`.

**Flavor-A phantom prune (2026-08-29).** Supersedes scalarfix. Route-2's phantom prune (below) audited
only cells graded "never STRONG", so 39 STRONG multi-element taste cells were never checked — and STRONG
is the worst place for a phantom (the scorer holds the twin to the exact set, no Jaccard floor). Eight
never-surfaced `cuisines_liked` elements on 4 STRONG cells (gen-011 german/cajun, gen-046 moroccan/
cajun/vegan_restaurant, gen-013 soul_food/thai, gen-010 soul_food), verified absent by three scans
(exact token, signature dish, full cuisine landscape), were removed from `card`+`card_at`. Grades,
credit arms, reading and store inherited unchanged from scalarfix; each of the 4 subjects' final card
moved a reader fingerprint (220 verdicts re-keyed across both arms, 0 refused, 0 skipped — every prompt
diff confined to the `cuisines_liked` line, no verdict references a token pruned from its own subject).
Oracle 1.000 · verify_gold 0 · validate 50/50 · coherence 14,688/0 · 0 stale. `runs/apply_flavorA_prune.py`
(key), `runs/promote_flavorA.py` (map + re-key). **Scope: Flavor A only** — Flavor B (visit-to-like
inflation) and the remaining 35 worklist cells are still OPEN in `docs/GT-REVIEW.md` (Flavor B
awaits a policy call; the 35 need the ≥2-judge pass). Pre-re-key cache:
`runs/verify-n50-both-pre-flavorA-2026-08-29.tar.gz`.

**Scalar-ordinal grade-leads-value fix (2026-08-28).** Supersedes gradefix. Three scalar cells held
their end-state value in `card_at` at every checkpoint, so a faithful reader was charged at the
early checkpoints where the trait had surfaced only at a lower level (gen-020 `solo_dining_comfort`
flat `enjoys_solo`→`takeaway_or_counter`; gen-024 `solo_dining_comfort` and gen-022 `formality_ceiling`
per-checkpoint climbs — the first value *replacements* in the corpus, the scalar analog of the
phantom-list prune). `sut._CARD_AT_GOLD_FIELDS` now overlays `card_at` for these two scalar fields.
Grades, credit arms, reading and store are inherited unchanged from gradefix; only gen-020's final
card moved a reader fingerprint (12 verdicts re-keyed, 0 refused). Oracle 1.000 · verify_gold 0 ·
validate 50/50 · coherence 14,688/0 · blocking 0 · suite 942 passed.

**The frozen union reading was re-keyed to the pruned cards, not re-read.** The audit reader is
shown the card (`verify.audit_prompt` prints `card_block`), so pruning the taste-list cards changed
the reader-prompt fingerprint and staled the cached reading + owner decisions pinned to it — a
page-only effect (scoring never touches the verdict cache). The prune is provably inert to every
finding (it removes only card elements no transcript turn mentions), so the exact frozen reading was
preserved and only its `instrument` fingerprints re-stamped (`runs/route2-phantom-prune/rekey_*.py`,
guarded to refuse any prompt diff beyond taste-list card lines). Bare `build_ui.py`: 612 checked, 0
stale, blocking 0. Pre-re-key cache: `runs/verify-n50-both-pre-phantomprune-2026-08-27.tar.gz`.

**Phantom taste-list elements pruned from gold.** A list field's card held the full multi-element
set (e.g. `cuisines_liked=[8]`) but the transcript surfaced only a subset, and a WEAK grade held the
twin to the whole set — scoring a faithful reader *below abstaining*. A ≥2-judge read of all 76
exposed cells pruned **89 of 224 gold elements (40%)** that no user turn ever surfaced; the scorer
(`Subject.gold_at`) now reads the evidenced-so-far subset from `card_at` for the four taste-list
fields. Two prune-to-empty cells (gen-000 / gen-015 `cuisines_liked`, WEAK credit resting on
assistant scenery / an off-vocab liking) were regraded WEAK→UNEVIDENCED, and their two credits
withdrawn from the arms. Bands: WEAK 5260→5254, UNEVIDENCED 4964→4970, STRONG 4464 unchanged;
credits 58→56. Everything else — store, reading, evidence bands elsewhere — is byte-identical to the
nocorr base. Full record: that base's MANIFEST (development tree).

**No scripted corrections.** All 44 corrections in the union base were phantom — the pre-correction
gold value was never authored into the transcript, so a correct reader was charged a misreading
before the flip (details in the stage MANIFEST, development tree).
v0.5 ships without corrections; `schedule.MAX_CORRECTIONS = 0` keeps generation from producing any,
with the machinery preserved for v1/v2. The evidence map, credit layer and store are byte-identical
to `n50-union-2026-08-25`; only the phantom gold values changed.

**The page is built from these files and not from a re-derivation of them.** `build_ui.py`'s
defaults name this base and nothing else, and `--arm` puts it in union mode: both arms' credits
merged by cell, both arms' findings merged by (task, field, kind), each blocking finding chipped
with whether one reader filed it or both. Verified for the phantom-prune base on 2026-08-27 —
`build_ui`'s derived credited bands match the pinned answer key on **all 14,688 cells** (0
mismatches, `runs/route2-phantom-prune/verify_coherence.py`), **56 credits** (58 minus the 2
withdrawn phantom credits), bands STRONG 4464 · WEAK 5254 · UNEVIDENCED 4970. `ui/superseded/` holds
the older pages; each one was current once.

**Before scoring against any other credited corpus in this tree, check it validates.** Every
credited map built before 2026-08-25 fails `EvidenceMap.validate()` — 21 or 22 of 50 subjects
carry WEAK islands, which score a correct memory as fabrication. `sut.evidence_map` now refuses
them on load; the ones on disk are kept as history, not as usable answer keys.

## The lineage, newest first

| base | blocking | why it was superseded |
|---|---|---|
| **`n50-tagfix-2026-10-15`** | **0, on BOTH arms** | current (key and map = fullpassfix; store fixed) |
| `n50-fullpassfix-2026-10-14` | 0, on BOTH arms | its store served 10 doubled speaker labels in 5 hand-edited sessions; removed and the 5 subjects re-run, key unchanged; ADR-0068 |
| `n50-gtfix-2026-08-30` | 0, on BOTH arms | the 2026-10-14 full failure-mode pass found 4 gold cells wrong in one place each (gen-007 `deal_seeking` one checkpoint late, gen-002 `spanish` avoid missed at c0, gen-006/017 `deal_seeking` latent leaked into WEAK); ADR-0067 |
| `n50-route1-surfaced-2026-08-30` | 0, on BOTH arms | the 2026-08-30 failure-mode re-analysis confirmed 18 gold defects (booking≠liking, sibling vocab, voiced-element omits, leaked latents); ADR-0066 |
| `n50-gen002fix-2026-08-29` | 0, on BOTH arms | its `card_at` still held the end-state value at every checkpoint for the nine surfaced-gate fields; the route-1 (lists + `formality_ceiling`/`solo_dining_comfort`) and route-1b (ordinals `deal_seeking`/`novelty_vs_loyalty`/`noise_preference`) passes rewrote `card_at` to the value surfaced by each checkpoint, so a faithful reader is no longer charged before the trait/level surfaces |
| `n50-strongaudit-2026-08-29` | 0, on BOTH arms | gen-002 `solo_dining_comfort` gold `enjoys_solo` was WEAK-graded at all 16 cps but unsupported (no USER turn expresses enjoying solitude); owner downgraded to `comfortable_at_table`, per-checkpoint |
| `n50-flavorB-2026-08-29` | 0, on BOTH arms | Flavor A/B hand-pruned STRONG cells by inspection; the systematic ≥2-judge pass over all 39 STRONG taste cells (+ a Fable-5 indirect-reference pass that rescued gen-011 `filipino` via *sisig*) found 5 more USER-never-voiced elements to prune (gen-005 `supper_club`, gen-013 `taiwanese`, gen-031 `vegan_restaurant`/`german`/`cajun`) |
| `n50-flavorA-2026-08-29` | 0, on BOTH arms | the same 39 STRONG cells also held Flavor B — `cuisines_liked` elements gold credited from a bot-offered booking or the bot's word for a venue, never a USER-voiced liking (gen-029 indian/filipino, gen-013 filipino, gen-010 mediterranean); owner policy strict, booking ≠ liking |
| `n50-scalarfix-2026-08-28` | 0, on BOTH arms | route-2's phantom prune had a "never STRONG" worklist filter, so 39 STRONG multi-element taste cells were never audited; 8 never-surfaced `cuisines_liked` elements on 4 of them (gen-010/011/013/046) were scored against a faithful reader with no Jaccard floor (Flavor A) |
| `n50-gradefix-2026-08-28` | 0, on BOTH arms | three scalar-ordinal cells held their end-state value in `card_at` at every checkpoint (the scalar analog of the taste-list phantom prune), charging a faithful reader at the early checkpoints where the trait had surfaced only at a lower level (gen-020/024 `solo_dining_comfort`, gen-022 `formality_ceiling`) |
| `n50-phantomprune-2026-08-27` | 0, on BOTH arms | four taste-list cells graded WEAK before their value surfaced in `card_at` — the grade led the value, so a twin correctly holding `[]` was charged a misreading (gen-002 `cuisines_avoided`, gen-007/022/041 `cuisines_liked`); gen-022 `french` was also a formality→cuisine misattribution with no beat |
| `n50-nocorr-2026-08-26` | 0, on BOTH arms | its taste-list gold held never-surfaced (phantom) elements — a WEAK grade held the twin to the whole card set, scoring a faithful reader below abstaining |
| `n50-union-2026-08-25` | 0, on BOTH arms | its 44 scripted corrections were phantom — pre-correction gold never authored into the transcript, so a correct reader was charged a misreading before the flip |
| `n50-handedit-2026-08-25` | 0 on the opus arm | its 48 sonnet-only findings had never been adjudicated, so "blocking 0" described one reader |
| `n50-bothdraw-2026-08-25` | 8 | the 8 were closed: 5 pages repaired, 3 readings refused |
| `n50-gen048edit-2026-08-25` | 0 | its key-3 second reader covered 48 of 612 sessions; the two-model pass replaced it |
| `n50-distfix-2026-08-24` | 0 | single draw, single arm |
| `n50-adjudicated-2026-08-24` | 7 | the 7 were a `reconcile` bug |
