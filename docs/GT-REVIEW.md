# Ground-truth review candidates — where a SUT may be right and the answer key wrong

> **Closed record (as of 2026-08-30).** This is the working ledger of the gold review as it stood
> on 2026-08-30. Every item below is closed — fixed and promoted into a later base, or resolved as
> WONTFIX — even where an entry still reads "OPEN" or "pending" in its own dated text. The one
> forward-looking proposal, an automated "scalar Gate 2" for new authoring, was not built: that
> check stays manual ([`GT-POLICIES.md`](GT-POLICIES.md) §3.3). The fixes it
> led to are in the current key; see [`provenance/CANONICAL.md`](provenance/CANONICAL.md) for the
> lineage and [`GT-POLICIES.md`](GT-POLICIES.md) for the rules that came out of it. References to
> `RESUME.md`, `runs/` and ADR numbers point to the unreleased development tree.

Working ledger of cases where a submission was charged an error but the transcript backs its read,
so the **corpus / evidence map / gold** is the thing that may need fixing — not the SUT.

- Base under review: `runs/corpus-n50-credited-nocorr-2026-08-26.json` (canonical nocorr base;
  see `docs/provenance/CANONICAL.md`).
- Store the SUT actually read: `work/authored/claude-opus-5` (transcripts keyed by task id).
- Source audit: **luna `prompted` n=10 fabrication cases** (all 47 charges walked against the
  transcript, 2026-08-26). The 47 charges collapse to ~8 distinct beliefs; the ones below are the
  subset where Luna's commit has a real basis or gold is internally inconsistent. The remaining
  charges were genuine hallucinations (misread BOT venue prose, invented values) and are NOT
  listed here.

Status legend: **OPEN** (needs adjudication) · **FIX** (agreed defect) · **WONTFIX** (intended) · **DONE**.

---

## DEFECT — phantoms + visit-to-like inflation on STRONG-graded taste cells (route-2 skipped them) · **FLAVOR A + B + FULL STRONG AUDIT PROMOTED 2026-08-29 · worklist CLOSED**

> **STATUS 2026-08-29 (STRONG-cell audit — the 39-cell worklist, CLOSED).** The systematic ≥2-judge
> full-transcript pass ran over **all 39** STRONG multi-element taste cells (`runs/route2-strong-audit/`,
> instrument = the shipped route-2 opus reader + sonnet confirmer, opus tiebreak, prompt tightened to
> STRICT booking≠liking). 6 candidate phantoms across 4 cells, all unanimous. A **Fable-5
> indirect-reference adversarial pass** then RESCUED **1**: gen-011 `cuisines_liked` `filipino` — the
> user names the dish *sisig* at cp8 (a false negative from BOTH the token scan and the opus+sonnet
> judges; **kept**, owner call). The other **5 confirmed USER-never-voiced elements** were pruned:
> gen-005 `event_types_liked` −supper_club, gen-013 `cuisines_avoided` −taiwanese, gen-031
> `cuisines_liked` −vegan_restaurant/german/cajun. No cell prunes below 2; all stay STRONG.
> `runs/apply_strongaudit_prune.py` → **`runs/corpus-n50-strongaudit-2026-08-29.json`**. Gates:
> validate 50/50 · verify_gold 0 · oracle 1.000 every cp · coherence 14,688/0 · blocking 0 · suite
> 934/3-skip. **PROMOTED TO CANONICAL 2026-08-29** → `runs/provenance/n50-strongaudit-2026-08-29/`
> (`runs/result-strongaudit-2026-08-29.txt`). Map pruned + 138 verdicts + 4 dismissals re-keyed (0
> refused, 0 skipped). `DEFAULT_CORPUS`/CANONICAL.md/`build_ui.py` repointed.
> **Method finding:** the ≥2-judge instrument had a **1/6 false-positive rate** (surface/token test,
> misses indirect references) — run the indirect-reference pass before any future STRONG/WEAK prune.
> **Still OPEN:** only route-1 list-timing (the 39-cell surface worklist is now closed).
>
> **STATUS 2026-08-29 (Flavor B).** **Flavor B pruned** (owner policy: STRICT, booking ≠ liking —
> see `docs/GT-POLICIES.md`): the 4 verified visit-to-like / bot-word elements across 3 STRONG
> `cuisines_liked` cells (gen-029 indian/filipino, gen-013 filipino, gen-010 mediterranean) removed
> from `card`+`card_at` by `runs/apply_flavorB_prune.py` → **`runs/corpus-n50-flavorB-2026-08-29.json`**.
> Each verified by a full-transcript walk against the served store. The walk refined the earlier read:
> gen-029 `filipino` and gen-010 cp17 `mediterranean` are bot venue prose the user never booked
> (unsurfaced regardless of policy); the rest are bookings accepted on non-cuisine grounds. Gates:
> validate 50/50 · verify_gold 0 · oracle 1.000 every cp · coherence 14,688/0 · blocking 0 · suite
> 934/3-skip. **PROMOTED TO CANONICAL 2026-08-29** → `runs/provenance/n50-flavorB-2026-08-29/`
> (`runs/result-flavorB-2026-08-29.txt`). Map pruned + 160 verdicts + 4 dismissals + 2 adjudications
> re-keyed (0 refused). Effect (Luna prompted, 3 subjects, replay-cached, no SUT calls): misreads
> 51→54 (gen-013 rises — it committed filipino from the booking, so the prune correctly charges the
> inflation), skill 0.619→0.627. `DEFAULT_CORPUS`/CANONICAL.md/`build_ui.py` repointed.
> **Still OPEN:** the remaining **35 worklist cells** (≥2-judge pass) and route-1 list-timing.
>
> **STATUS 2026-08-29 (Flavor A).** **Flavor A pruned** (owner-authorized): the 8 verified never-surfaced
> elements across 4 `cuisines_liked` cells (gen-011 german/cajun, gen-046 moroccan/cajun/
> vegan_restaurant, gen-013 soul_food/thai, gen-010 soul_food) removed from `card`+`card_at` by
> `runs/apply_flavorA_prune.py` → **`runs/corpus-n50-flavorA-2026-08-29.json`**. Absence
> re-confirmed by three scans (exact token, signature-dish basket, full cuisine landscape — the
> landscape control surfaces genuine + avoided elements, nothing for the 8 phantoms). Gates:
> validate 50/50 · verify_gold 0 · oracle A=1.000 every checkpoint. Effect (Luna prompted, 4
> affected subjects, replay-cached, **no SUT calls**): misreads 96→83, skill 0.620→0.637.
> **PROMOTED TO CANONICAL 2026-08-29** → `runs/provenance/n50-flavorA-2026-08-29/`
> (`runs/result-flavorA-2026-08-29.txt`). Map pruned + 220 verdicts and 7 dismissals + 2 adjudications
> re-keyed (0 refused, 0 skipped; every diff confined to the cuisines_liked line); coherence 14,688/0,
> blocking 0, oracle 1.000, suite 949. `DEFAULT_CORPUS`/CANONICAL.md/`build_ui.py` repointed.
>
> **Still OPEN:** **Flavor B** (visit-to-like, below) awaits the policy call — does a bot-offered
> booking the user accepts license a "liked cuisine"? — and the **remaining 35 worklist cells** await
> the ≥2-judge full-transcript pass (those need judge LLM calls; Flavor A did not).

**The bug.** The 2026-08-27 phantom-prune (route 2) audited only cells whose exposure filter was
*"multi-element, scored, **never STRONG**"* — a structural scan confirms **0 of its 76 worklist items
carried a STRONG band** (`runs/route2-phantom-prune/worklist.json`). So every taste cell that reaches
STRONG was excluded from the prune, and STRONG is the worst place for a phantom: the scorer holds the
twin to the **exact** gold set with no ~0.83 weak-credit floor (WEAK cells get Jaccard softening;
STRONG cells do not). **39 STRONG multi-element taste cells were never audited.**

**Found via** the failure-mode catalog (`docs/FAILURE-MODES.md` → "Inside the artifacts"): the four
taste fields are 292 of 789 answer-key-artifact episodes (37%). A transcript walk of the flagged
STRONG cells finds two defect flavors.

**Flavor A — phantoms absent from the entire transcript** (only in the gold-card header; full-file
token scan, all speakers):

| cell (STRONG `cuisines_liked`) | phantom gold element(s) | in served transcript? |
|---|---|---|
| gen-011 | `german`, `cajun` | **absent everywhere** |
| gen-046 | `moroccan`, `cajun`, `vegan_restaurant` | **absent everywhere** |
| gen-013 | `soul_food`, `thai` | **absent everywhere** |
| gen-010 | `soul_food` | **absent everywhere** |

**Flavor B — visit-to-like inflation (policy call).** Gold credits a cuisine from a *booking*, not a
stated liking — the same `set_inflation` the catalog charges single-pass SUTs for:
- **gen-029** `cuisines_liked` ⊇ `indian`,`filipino`: user books Kalyani (bot's Indian pick) explicitly
  for *a quiet room where four can hear each other*, never mentions Indian; `filipino` is a bot-offered
  food-yard stall.
- **gen-013** `cuisines_liked` ⊇ `filipino`: "that'll do nicely, book it" for the bot's Ilaya — while
  the cuisine the user actually raves about (Portuguese) *is* correctly in gold. Key mixes the two.
- **gen-010** `cuisines_liked` ⊇ `mediterranean`: only ever the bot's word for a venue ("a Mediterranean
  spot doing mezze"), never the user's.

**Related, still open — timing (route 1).** Even where every element is genuine, the list is scored
against the full ever-evidenced union from the first WEAK checkpoint. gen-014 `cuisines_liked` (8
cuisines, all genuinely user-stated) is WEAK from c2 but surfaces `peruvian` c2 → `vietnamese` c3 →
`japanese`/`taiwanese` c12 → `thai`/`indian`/`mediterranean`/`ethiopian` c15; a reader holding the
c2 subset scores Jaccard 1/8 and loses to abstention. This is the per-checkpoint `surfaced` record
(route 1, THE GATE) — still unbuilt, now visible on STRONG cells too, not only WEAK.

**Worklist — 39 STRONG multi-element taste cells to audit (element count in parens):**
- `cuisines_liked` [13]: gen-002(2), gen-004(3), gen-005(2), gen-010(6), gen-011(8), gen-013(7),
  gen-014(8), gen-019(2), gen-024(3), gen-029(7), gen-031(8), gen-032(7), gen-046(7)
- `cuisines_avoided` [9]: gen-010(3), gen-013(3), gen-014(3), gen-018(2), gen-019(3), gen-029(2),
  gen-032(3), gen-045(2), gen-046(2)
- `event_types_liked` [12]: gen-005(3), gen-007(3), gen-010(3), gen-011(4), gen-013(4), gen-018(3),
  gen-019(3), gen-029(2), gen-031(2), gen-032(3), gen-034(3), gen-046(2)
- `event_types_avoided` [5]: gen-013(2), gen-016(2), gen-022(2), gen-031(2), gen-046(2)

**Proposed fix (mirrors route 2, pending owner review).** A ≥2-judge full-transcript reading pass over
the 39 cells: an element survives only on ≥2-judge agreement that a USER turn expresses the
liking/avoidance (a bot-offered booking the user accepts for other reasons does **not** license it,
if the owner adopts that policy). Prune the rest from `card`/`card_at` for the four taste fields, as
route 2 did. Separately, route-1 `surfaced` growth closes the timing dimension. **Consequence for the
paper:** part of the `set_underlist` genuine count is a twin correctly declining to inflate; treat that
mode's tally as an upper bound until the audit runs. Aggregate 623/55% unaffected (these are already
KEY_ARTIFACT).

---

## DEFECT — gen-002 `solo_dining_comfort` = `enjoys_solo` is over-high · **FIX, PROMOTED 2026-08-29**

**Same family as the scalarfix defects below** (gen-020/024/022): a scalar ordinal gold set above the
highest rung the USER turns license. Found by the full-conversation GT double-check over the failure
catalog (`docs/FAILURE-MODES.md`), auditing the 34 highest-convergence "models beat gold" cells
(≥3 distinct single-pass models, n50, canonical `strongaudit`). This cell was **missed by the scalarfix
pass** because that pass only walked its own cross-model shortlist, not the full genuine-episode set.

- **Cell.** gen-002 `solo_dining_comfort`, gold `enjoys_solo` (top rung). **WEAK-graded all 16
  checkpoints → scored every cp** (not a latent/UNEVIDENCED gap). `solo_dining_comfort` is already a
  per-checkpoint `card_at` field (scalarfix `_CARD_AT_GOLD_FIELDS`), so the fix mechanism is identical.
- **Convergence.** 5 distinct single-pass models (deepseek-v4-flash, deepseek-v4-pro, gemini-3.7-flash,
  glm-5.3-flash, gpt-5.6-luna) all emit `takeaway_or_counter` (rung 2); tagged `ordinal_far`, GENUINE.
- **Evidence.** No USER turn expresses *enjoying* solitude (the rung-4 requirement). Every solo meal is
  counter-order-and-carry: c0 "order at the till and carry it over", "pay at the counter and take the
  tray myself", "not sitting down to be waited on"; the one lingering turn is c1[2] "sat at the counter
  with a salmon set and my book for about an hour" — still at the counter. Ladder: `wont_eat_alone <
  takeaway_or_counter < comfortable_at_table < enjoys_solo`. Highest defensible rung is
  `takeaway_or_counter` (rung 2, = the models' read) or at most `comfortable_at_table` (rung 3).
  Contrast gen-000, where `enjoys_solo` DID survive the same audit ("best part of the day… almost
  annoyed when it was time to go") — so this is not a blanket downgrade of the field.
- **Status.** Adversarial "defend the gold" pass (Opus, owner-authorized, mirrors scalarfix) ran
  2026-08-29: **VERDICT = FALLS.** `enjoys_solo` (rung 4) is unsupported — no USER turn expresses
  enjoyment or preference for solitude, and he defaults to bringing company whenever it is available
  (brother s2, friend s3/s13, sister s9); he goes solo only when it is circumstantially "just me".
  The defender's proposed rung is **`comfortable_at_table` (rung 3)** — above the 5 models'
  `takeaway_or_counter` (rung 2) because he routinely sits alone at a table (s5[0] "something I can
  carry to a table myself") and lingers unhurried (s1[2] "sat at the counter with a salmon set and my
  book for about an hour"), his only rushed exits blamed on the room, never on being alone. A fresh
  **Fable-5** read (neutral framing) instead KEPT `enjoys_solo` (medium-high), reading the s1 Leeds
  "hour with my book" as fond savouring — so the three reads split **2–1**, the disagreement resting
  entirely on the tone of that one turn.
- **Resolution (owner, 2026-08-29): downgraded to `comfortable_at_table`, PROMOTED TO CANONICAL** →
  `runs/provenance/n50-gen002fix-2026-08-29/`. Per-checkpoint `card_at`: `takeaway_or_counter` c0–4,
  `comfortable_at_table` c5–15 (solo table-sitting first surfaces c5[0] "just me… something I can carry
  to a table myself"; c1's book-and-an-hour is explicitly *at a counter*). Final card
  `comfortable_at_table`. Gates: validate 50/50 · verify_gold 0 · oracle 1.000 every cp · coherence
  14,688/0 · 0 stale · blocking_after 0 · suite 949 passed, 2 skipped. Map + 32 verdicts re-keyed (0
  refused/skipped); 0 decisions needed re-keying. `runs/apply_gen002fix.py`, `runs/promote_gen002fix.py`,
  `runs/verify_coherence_gen002fix.py`. `DEFAULT_CORPUS` / `build_ui.py` defaults / `CANONICAL.md`
  repointed. **HF release now stale — re-run `scripts/build_release.py` before next upload.**

---

## DEFECT — scalar ordinal grade-leads-value (end-state written at every checkpoint) · **FIX, 2026-08-28**

**The bug (scalar analog of the phantom-list prune).** `gold_at(t)` returns the *final* card value
at every checkpoint for all 13 scalar ordinal fields — a structural scan found **0 of 650 scalar
cells vary across the timeline** (`card_at[t][field]` = final value everywhere; v0.5 ships 0
corrections). So a progressively-revealed trait is scored against its **end state** from its first
evidenced checkpoint, and a faithful reader who reads the checkpoint-local level is charged a
misread. Same family as the pruned taste-list fields (route-2), which got a per-checkpoint `card_at`
subset; scalar ordinals never did.

**Found via** Luna n50 (`prompted`+`notebook`), convergent both-arm misreads of ordinal distance ≥2
on the canonical gradefix base; five sub-agents read the full conversations, then one adversarial
Opus agent per case tried to *defend* gold. Two survived adversarially; one was downgraded by the
owner.

| subject · field | old gold (all cps) | fix | evidence |
|---|---|---|---|
| gen-020 `solo_dining_comfort` | `enjoys_solo` | **→ `takeaway_or_counter`** (flat, final card too) | no enjoyment-of-solitude turn exists; every solo meal is counter-order-and-carry; counter is a *general* pref (wanted in groups too). Owner decision. |
| gen-024 `solo_dining_comfort` | `enjoys_solo` | cp0–6 `takeaway_or_counter` · cp7–9 `comfortable_at_table` · cp10–13 `enjoys_solo` | enjoyment first surfaces c10 ("sat on my own with my book … Perfect"), STRONG c12 ("the whole pleasure of it"). Final unchanged. |
| gen-022 `formality_ceiling` | `white_tablecloth` | cp6–7 `smart_casual` · cp8–15 `white_tablecloth` | linen/silver register first surfaces c8; cp6–7 tops out at "proper dinner / £70". cp0–5 stay UNEVIDENCED (unscored). Final unchanged. |

**Fix (mirrors route-2).** `sut._CARD_AT_GOLD_FIELDS` = `PRUNED_LIST_FIELDS` ∪
`{solo_dining_comfort, formality_ceiling}`; `_evidenced_gold_at` overlays `card_at` for that set, so
`gold_at` reads the point-in-time level. The list-only grade-leads-value gate
(`_verify_grade_leads_value`, `credit` Key 1b) still keys on `PRUNED_LIST_FIELDS` alone (its
empty-value surface test is meaningless for an always-present scalar). Corpus edits by
`runs/apply_scalarfix.py` → **`runs/corpus-n50-scalarfix-2026-08-28.json`**.

**Gates (new base):** `verify_gold` 0 problems · `EvidenceMap.validate` 50/50 · oracle f_raw=1.000
every checkpoint · suite **942 passed, 2 skipped**. **Effect (Luna, replay-cached):** the 3 affected
cells' spurious charges 36→7 — gen-020 12→0, gen-024 14→0, gen-022 10→7 (the 7 that remain are
genuine: prompted cp14 regression + notebook cp0–5 commits on UNEVIDENCED cps). **Promoted to
canonical 2026-08-28** (`runs/provenance/n50-scalarfix-2026-08-28/`): map built + 12 gen-020 reader
verdicts re-keyed (0 refused) by `runs/promote_scalarfix.py`; `run_sut.DEFAULT_CORPUS` and
`build_ui.py --corpus` repointed; band coherence 14,688/0, blocking 0, oracle 1.000, suite 942.
Credit arms/reading/store byte-identical to gradefix.

**Scope decisions (owner, 2026-08-28):**
- **This introduces the first value *replacements* in the corpus.** A structural scan confirmed the
  shipped base has **zero** non-additive gold changes: list fields only *add* elements (0 of 200
  cells ever drop/swap), and scalars never change (0 corrections). gen-024/gen-022 climb one level
  to another (`takeaway_or_counter→comfortable_at_table→enjoys_solo`; `smart_casual→white_tablecloth`),
  which is new — but it is the faithful scalar analog of the list `card_at` growing, and it keeps the
  oracle target readable at every checkpoint. gen-020 is a flat value change, not a replacement.
- **Distance-1 candidates left UNFIXED (decision #1).** A cross-model scan (3 models × prompted+notebook)
  for the same signature — early consensus ≠ gold, ≥4/6 agree, later converges to gold — flagged
  **gen-000 `solo_dining_comfort`**, **gen-005 `deal_seeking`**, **gen-007 `adventurousness`** (all
  ordinal distance 1; at ≥3/6 also gen-000 `deal_seeking` d2 and gen-004 `planning_style` d1). All are
  distance-1 (partial-credit-softened, small impact) and unverified — cross-model consensus is a
  shared-bias risk, not proof (cf. gen-036 `deal_seeking`, a confirmed false positive). Owner chose to
  stop at the two clear distance-2 cells; these were **not** adjudicated or edited.
- **gen-022 grade left WEAK (not raised to STRONG).** cp8–15 read as STRONG-eligible on the transcript
  (explicit linen/tablecloth), but the schedule's `provisional` ceiling is WEAK there and ADR-0009
  makes that ceiling un-raisable by judges/credit. Raising it would require editing the authored
  provisional map, fixes no SUT unfairness (Luna already commits `white_tablecloth` and scores 1.0
  there — only the abstain floor would move), and is a difficulty knob, not a correctness fix. Left WEAK.

**Gate 1 landed (2026-08-29): `sut._verify_scalar_card_at`**, called from `evidence_map` beside the
list gate, tested in `tests/test_scalar_card_at.py` (7 cases; suite 949). Over a per-checkpoint scalar
field's **evidenced** checkpoints it refuses: an off-vocab level; a value whose ordinal distance to the
final card value *increases* (zig-zag / overshoot-then-return); and a last-evidenced value ≠ the final
card value. This is a **malformed-edit guard, not a defect detector** — the original bug (end-state at
every checkpoint) is distance-0 throughout and passes it. A model-free gate cannot do better, because
"is this level readable by *t*?" is a transcript fact.

**Reviewer check the gate cannot replace (do this for every per-checkpoint scalar edit, LLM or human):**
for each evidenced checkpoint *t*, confirm `card_at[t]` is the **highest level a competent reader can
commit to from the USER turns through *t*** — and specifically is **not** the end-state level before the
transcript surfaces it. Quote the USER turn that first surfaces each level; if the only cue for the
final level is at *t+k*, then *t*..*t+k−1* must hold the lower level, not the end state. (This is what
caught all three cells.)

**Scalar Gate 2 — PENDING · GENERATION GATE (sits beside route 1; both block new authoring).**
Mechanizing the reviewer check needs the generator to author a
per-checkpoint *surfaced level* per scalar cell (the scalar analog of the list "route 1" `surfaced`
record). Then the gate becomes exact and model-free: `card_at[t]` must equal the authored surfaced
level, and a WEAK/STRONG grade at *t* requires that surfaced level to be defined. That is a generation
change (and would let `author.py` produce scalar `card_at` automatically instead of hand-editing). Not
started — see RESUME.md "scalar Gate 2 pending".

---

## DEFECT (CONFIRMED, class-wide) — phantom list-elements scored on WEAK list-fields · **FIX, 2026-08-27**

> **GENERATION GATE: no new subject may be generated until BOTH generator-side fixes land —
> (1) route 1 below (per-element list `surfaced`) and (2) scalar Gate 2 (per-checkpoint scalar
> `surfaced` level, "Scalar ordinal grade-leads-value" section above).** Every subject authored
> under the current pipeline reintroduces the list phantom defect, and without scalar Gate 2 the
> grade-leads-value check on scalar ordinals stays a manual reader pass that new subjects can
> silently violate. This blocks `author.py` runs on NEW ids (existing pilot subjects are never
> re-authored).

**The bug.** A list/set field's gold value is the full multi-element card list (e.g.
`cuisines_liked=[8 cuisines]`). The transcript author surfaces only a *subset* of those elements
in prose. But a beat carries **no record of which element it surfaced** (`Beat` has
`field_id, move, weight, bin` — `bin` is distribution-only; list fields carry nothing;
schedule.py:889). So `provisional_map` grades the field per-field on total *weight*: one landed
beat flips the whole field **WEAK**, and the scorer then holds the twin to the **entire** card
set — including elements no turn ever evidenced. Grade is per-field; the scored value is
multi-element; the two disagree.

**Consequence.** A faithful transcript-reader cannot reach 1.0 on these cells, and reading the
evidenced subset scores *below abstaining*:
- On a WEAK list field, abstain (`unknown`) earns ~0.83 weak-credit; committing the correctly-read
  subset earns `Jaccard(subset, full_gold)` — 0.25–0.67 in the sampled cases. **The incentive is
  inverted: the benchmark rewards abstention over correct extraction on taste fields.**
- The oracle scores 1.0 (it reads the same card), so nothing downstream catches it — only a
  transcript-vs-gold walk does. Same family as the credit carry-forward monotonicity bug (WEAK islands),
  but on the *value* dimension, not the time dimension.

**Scope (structural scan, canonical nocorr base).** 245 scored list-field cells; 115 multi-element;
**76 exposed** (multi-element, scored, never STRONG) — all four taste-list fields only
(`cuisines_liked` 26, `event_types_liked` 21, `cuisines_avoided` 16, `event_types_avoided` 13; no
constraint field). 10 cells carry ≥5 gold elements, all `cuisines_liked`.

**Semantic confirmation (4/4 full-transcript reads, 2026-08-27):**
| subject · field | gold(n) | evidenced | phantom | reader ceiling |
|---|---|---|---|---|
| gen-001 `cuisines_avoided` | 2 | latin_american | middle_eastern | 0.50 |
| gen-004 `cuisines_avoided` | 3 | vietnamese, steakhouse | portuguese | 0.67 |
| gen-047 `cuisines_liked` | 8 | ramen, malaysian, taiwanese, ethiopian | cajun, soul_food, dim_sum, mediterranean | 0.50 |
| gen-015 `cuisines_liked` | 7 | vietnamese (weak) | malaysian, lebanese, peruvian, japanese, vegan_restaurant, moroccan | 0.14 |

gen-015's real liked cuisine is **Georgian — not in vocab**: the card's 7 were assigned
independently of what the transcript shows. (Third-party rule still works: gen-004's in-text
`latin_american` avoidance is the companion's and correctly stays out of gold.)

**Contamination of live numbers.** Of GLM-flash's 324 misreadings, **137 (42%) are on the four
taste-list fields; 125 (38%) are on WEAK-graded (phantom-exposed) cells.** The cuisines/events
fidelity axis and cross-SUT comparison are partly measuring failure to name absent cuisines.

**Fix — the evidenced subset does not exist in structured form, so it must be supplied first:**
1. **Generation-side (route 1, forward, THE GATE):** add a structured `surfaced` field to the
   beat/turn that the author must populate with the exact card elements it wrote in; the map's
   scored value at checkpoint t = union of surfaced elements so far. Correct going forward; must
   land before any new authoring.
2. **Post-hoc credit pass (route 2, this base):** transcripts are frozen — derive the evidenced
   subset per (subject, list-field, checkpoint) with a ≥2-judge verified reading pass (the map's
   own grading standard) and prune phantom elements from the credited gold. One-time, ~76 cells.
   **DONE 2026-08-27 → `runs/provenance/n50-phantomprune-2026-08-27/`.**

The scorer change ("score WEAK list fields against the evidenced subset only") is small once the
subset exists; the work is producing the subset.

### Route 2 — DONE 2026-08-27

Pipeline in `runs/route2-phantom-prune/` (worklist → judge.py → apply_prune.py). Per exposed
`(subject, field)` — exposure is **per-cell, not beat-anchored** (gen-015 `cuisines_liked` reached
WEAK via the reader/credit layer with zero beats) — two judges (opus reader + sonnet confirmer, the
shipped `ClaudeCli`) read the whole checkpoint-labelled transcript; an element survives only on
≥2-judge agreement (18 tiebreaks). **89 of 224 gold elements pruned (40%)**: 13 cells clean, 61
partial, **2 prune-to-empty** (gen-000 / gen-015 `cuisines_liked` — WEAK credit rested on assistant
scenery / an off-vocab liking; regraded WEAK→UNEVIDENCED per owner, 2026-08-27).

Instrument validated 3/4 exact vs the manual table above; gen-015's hedged "vietnamese (weak)" also
prunes (both judges agree it's absent — the transcript's only cuisine is Georgian, off-vocab).

Scorer: `Subject.gold_at` overlays `card_at` for the four taste-list fields (`sut._evidenced_gold_at`),
a no-op on any unpruned base. Gates: `verify_gold` 0 problems, `EvidenceMap.validate` all 50, oracle
A=1.000 every checkpoint, suite 916 passed (6 new tests `tests/test_phantom_prune.py`). Effect on
GLM-flash prompted (gen-000..003, replay-cached): skill −0.133→−0.044, gen-003 0.127→0.514; gen-002
misread 39→47 (correctly penalizing over-committed phantom cuisines). **Promotion to canonical is
pending** — CANONICAL.md and `build_ui.py` defaults still point at the nocorr base.

---

## Tier A — RESOLVED WONTFIX: gold correct, Luna over-committed (2026-08-27)

Both items trace to the **same gen-004 session 0 turn**. Resolution: **the ground truth is right, no
fix.** The turn was authored — correctly — as evidence for `participation_vs_spectating` (an
abstraction trap), not for the event fields. Luna extracted the trait AND double-dipped the same
words into concrete event commits; those are genuine fabrications.

> s0[6] USER: "…we did a **pasta-making evening** at their sister place in the winter, rolling it
> out yourself, which is why I'd not just book a normal dinner somewhere — we went to a **wine
> thing** before that where you sat and listened to a man talk for an hour and **I was climbing
> the walls**"

**The s0 beats (what the author actually tagged this turn as), attributed=user, "narrows without pinning":**
- `cuisines_liked -> [italian, brunch, chinese]`
- `design_sensitivity -> room_over_food`
- **`participation_vs_spectating -> seeks_hands_on`**  ← the pasta/wine contrast lives here

Consequence: `participation_vs_spectating` is graded **WEAK from cp0**; `event_types_liked` /
`event_types_avoided` stay **UNEVIDENCED** at s0 by design.

**Luna's emitted cp0 card** confirms it understood the trait and then over-read:
`participation_vs_spectating="seeks_hands_on"` (✓ correct) **plus** `event_types_liked=["cooking_class"]`
and `event_types_avoided=["wine_tasting"]` (✗ the fabrications).

**The real gold event values come from other, repeated user reactions — never from s0:**
- `event_types_avoided=[live_rock]` — s9[4] "No. I went to one of those [band gig]… spent most of it
  checking the time"; s15[8] "Not that [band]… I'd be sat there waiting for it to be over."
- `event_types_liked=[tasting_menu, museum_late, dance_night, art_opening]` — pinned s3/s9 (nine-course
  linen lunch; Hansen late floor; Vessel floor night).

### A1 — gen-004 · event_types_avoided=wine_tasting · **WONTFIX**
The "wine thing / climbing the walls" is the spectating pole of the participation contrast, not a
standing dislike of wine-tasting events. Correctly UNEVIDENCED. Luna's commit is a real fabrication.

### A2 — gen-004 · event_types_liked=cooking_class · **WONTFIX**
The pasta-making evening illustrates hands-on engagement (→ seeks_hands_on), not a recurring liked
event. Correctly UNEVIDENCED. Luna's commit is a real fabrication. (The gen-004↔gen-008 cooking_class
asymmetry noted in B1 is unaffected — B1 stays open.)

**Spin-off (logged, not a defect):** s0 is a reliable fabrication-inducer (surface tokens look like
event types), and `formality_ceiling=casual_sit_down` at cp0–6 is the same over-read of s0 scenery
(gold flips white_tablecloth once the user asks for linen at cp11).

---

## Tier B — RESOLVED WONTFIX (2026-08-27): both premises were wrong; gold is correct

Adjudicated by reading the full conversations. Neither is a defect.

### B1 — gen-008 · event_types_liked=[live_jazz, museum_late, cooking_class] · **WONTFIX**
- No beat pins the field; UNEVIDENCED all 10 cps. In-text: `live_jazz` was OFFERED and DECLINED
  (s7, grey room), `museum_late`/`cooking_class` never appear. Luna's `cooking_class` (cp0–9) is a
  lucky-match fabrication.
- **Not a defect — it's the intended card/timeline gap.** A general scan found **249 such gold
  values across 39/50 subjects** (fields UNEVIDENCED at every checkpoint, no beat, card asserts a
  value). The persona card is the full person; the timeline surfaces only part. Scoring never
  touches UNEVIDENCED fields, and committing one is charged a fabrication *even when it matches the
  hidden card* — exactly what the penalty is for. The A2↔B1 "cooking_class asymmetry" dissolves:
  gen-008's is orphaned latent data, not a competing valid read.
- **Logged, not fixed:** `live_jazz` is *softly contradicted* (declined the one jazz offer); s7 is
  a removable session. See the follow-up check below.

### B2 — gen-002 · event_types_liked=[pub_quiz, open_mic] · **WONTFIX (premise was a misread)**
- open_mic IS evidenced — the s14 "storytelling night, name in a hat, read something short, host
  calls names" is an open-mic event (grep missed it; the words "open mic" never appear). pub_quiz ←
  s15 quiz, user "that I'd come back for." Beat at cp15 `event_types_liked -> [pub_quiz, open_mic]`.
- The s14 reaction ("if the host pulled me up I'd probably do it") was correctly routed to
  `participation_vs_spectating -> joins_if_pushed`, not to the event field. Luna's cp14 `open_mic`
  is the same participation→event over-read as gen-004, lucky-matching gold. Gold is right.

## SUT construction issue — `[]` vs `"unknown"` never disclosed to the model · **PROMPT FIXED 2026-08-27, re-run pending**
Two-way list-field clause added to `CARD_SYSTEM`, `MEMORY_INTEGRATION_SYSTEM`, `BANK_EMISSION_SYSTEM`
(src/persona_twin/sut.py); `MEMORY_EXTRACTION_SYSTEM` left alone (writes memory items, not a card).
53 SUT tests pass. Fingerprints changed → cached emissions stale → re-score needs fresh calls. Not
yet re-run (measure formatting-vs-real split after).

Surfaced while resolving S1 (2026-08-27). ADR-0035's rule that a list-field `[]` is a *commit*
("has none"), distinct from the `"unknown"` abstain token, is enforced in `coerce_value`/scorer but
**never stated in the SUT prompts** (`CARD_SYSTEM`, `BANK_EMISSION_SYSTEM`, notebook variant), which
present a scalar "give the value or say unknown" model and an output schema showing only `"unknown"`.
The datapoints/memory prompts even say "an empty list is fine," training the opposite habit.

Consequence — model emits `[]` for "none found" on list fields and is charged a fabrication. It is
model-emitted, not a harness default (`all_unknown` seeds `"unknown"`; omitted fields → UNKNOWN), and
it lands unevenly: empty-list share of each SUT's fabrications — **mem0 51/87 (59%)**, memory 11/33
(33%), prompted 1/47, notebook 0. So cross-SUT fabrication comparison is partly measuring an `[]`-vs-
`"unknown"` formatting habit, not calibration; mem0's fabrication "lead" is a majority artifact.

Fix belongs in the **prompt**, not scoring (ADR-0035 is sound; exempting `[]` on unevidenced fields
reopens the "assert modal empty set for free" gaming path). Re-scoring needs fresh calls (prompt
change invalidates `--replay-cached`).

**Re-run scope — DIRECT FINGERPRINT CHECK, 2026-08-29 (supersedes the assumption that all cached
runs are stale).** Replayed every emission store through the fingerprint gate (`EmissionStore` with
`replay=False`; a hit means the stored `instrument` == `fingerprint(current_system, reconstructed_
prompt)`, exactly certifying the prompt version). **Result: ZERO old-prompt files exist anywhere.**
Every emission file on disk was written under the NEW prompt — the `[]`/unknown re-score is already
effectively in place for all existing data; **no re-run is required for the prompt fix.** The fix
landed on 2026-08-27 *before* the earliest surviving emission (12:51 that day); everything postdates
it. What varies is **coverage, not prompt version** — the stateful arms were only ever run n≈10:

| kind | model | #subj | kind | model | #subj |
|---|---|--:|---|---|--:|
| prompted | gemini-3.7-flash | 50 | notebook | gemini-3.7-flash | 50 |
| prompted | gpt-5.6-luna | 50 | notebook | gpt-5.6-luna | 50 |
| prompted | glm-5.3 | 50 | notebook | deepseek-pro | 31 |
| prompted | deepseek-pro | 35 | notebook | deepseek-flash | 10 |
| prompted | deepseek-flash | 10 | notebook | glm-5.3-flash | 10 |
| prompted | glm-5.3-flash | 10 | memory | deepseek-pro | 12 |
| prompted | claude-opus-5 | 6 (gaps) | memory | flash/luna/glm-flash | 10 ea |
| mem0 | deepseek-pro | 12 | mem0 | flash/luna/glm-flash | 10 ea |

prompted/notebook/memory (13 stores) are **fingerprint-certified new**. mem0 (4 stores) can't be
certified offline (`mem0ai` not installed → its bank can't be rebuilt to reconstruct the emit
prompt), but every mem0 file is dated within the post-fix window and matches the uniform pattern, so
it is almost certainly new too; certify by `pip install mem0ai==2.0.18` + a cached replay of the emit
phase, or simply re-run its 4×~10 subjects (cheap, and it's the highest-`[]`-signal store). **Bottom
line: the only open question is whether to extend the stateful arms (memory/mem0, n≈10) to full n50
for coverage — a scope decision, not a prompt-fix re-run.**

**How `[]`/`"unknown"` reconcile (it's per-checkpoint, no cross-checkpoint merge).** The scorer scores
each emitted card independently against `gold_at(t)` × grade(t):

| emit | UNEVIDENCED | WEAK | STRONG |
|------|-------------|------|--------|
| `"unknown"` | free, unscored | ~0.83 weak credit | 0.0 miss |
| `[]`        | FABRICATION | Jaccard vs gold | 1.0 if gold `[]` else 0 |
| `[x,y]`     | FABRICATION | Jaccard vs gold | Jaccard vs gold |

The real reconciliation is **temporal** and the map already supports it: of 109 list-fields with gold
`=[]`, **74 flip UNEVIDENCED→STRONG** when the person declares the absence — and they are exactly the
three constraint fields (dietary 22, allergies 27, accessibility 25 evidenced). The four taste fields
**never** have gold `[]` (0 each). So the correct prompt clause is a two-way split, not one line:
- **Constraint fields** (dietary, allergies, accessibility_needs): `[]` ONLY after the person says
  they have none; `"unknown"` until then.
- **Taste fields** (cuisines_liked/avoided, event_types_liked/avoided): never `[]` — `"unknown"` when
  nothing shown, else list what they've shown.

Maps the inflation onto real errors: mem0's 51 `[]`-fabs ≈ 36 taste-field `[]` (always wrong) + 15
constraint-field `[]` emitted before the declaration (premature) — both genuine "should have abstained"
the model was never told about. Post-fix, residual `[]` commits are real calibration signal.

## Contradicted-latent-value scan — RESOLVED, no defects (2026-08-27)
Checked whether any latent card value (UNEVIDENCED-all, present in gold) is actively **contradicted**
by shown behavior. Three angles covering the behavior-legible classes:
- Latent *liked* list-values whose token appears in-transcript: **14 cases, 0 contradictions** — every
  one was a decline for an unrelated field (noise, room, seating, dress code, logistics), a false
  keyword match (Broadway Market, "French cooking"), or the user being positive.
- Latent *avoided* list-values appearing in-transcript: **0 cases**.
- Latent `solo_dining_comfort` vs party-size behavior: **8 subjects, all consistent** (group-dining
  matches `wont_eat_alone`).

The gen-008 `live_jazz` example that motivated this is **NOT a contradiction** — the user declined
Marram Hall for "all that grey, and those long benches jammed together" (the room), never the jazz.
Structural reason it's clean: behavior is generated FROM the card and declines are authored to be
attributable to a specific field (anti-correlation design), so latent values are never contradicted.
Residual: a full scalar sweep (spice_tolerance, design_sensitivity, adventurousness, …) needs
per-subject semantic reading; the token-searchable classes came back clean, so likely clean overall.

---

## Tier C — vocab granularity (adjudication, not a clear defect)

### C1 — gen-007 · event_types_liked · market vs food_festival · **RESOLVED WONTFIX (2026-08-27)**
- Gold `food_festival` is **directly evidenced**: s19[8] USER "If there's a **food festival** on, or
  an art opening, or somewhere doing an open mic night, send them my way" — the user says the words
  ("food markets" at s23 is a looser re-request). `market` and `food_festival` are distinct,
  actively-used levels (17 vs 11 subjects) and the user distinguished them — "food festival" for the
  event, "market" for the venues they eat at. Luna's `market` is a genuine misread, not a vocab
  defect. No metric change.

---

## Not ground truth — scorer behaviour

### S1 — empty-list commit charged as fabrication · **RESOLVED WONTFIX (2026-08-27)**
- gen-001 `cuisines_avoided = []` at cp6 charged a fabrication. **Working as designed.**
- `coerce_value` (src/persona_twin/sut.py) per **ADR-0035** deliberately treats `[]` as a *commit*
  ("this person avoids nothing"), distinct from the `"unknown"` abstention sentinel — so that a
  correct "no allergies / no dietary rules" is creditable on the gating fields rather than read as an
  abstention. Luna used both on the same cp6 card (`spice_tolerance:"unknown"` vs `cuisines_avoided:[]`),
  so it chose to assert the empty set with the free abstention channel available.
- `cuisines_avoided` is UNEVIDENCED at cp6, and §8.1 fabrication is band-determined: every commit on
  U(t) is charged regardless of value. Exempting `[]` would break that invariant and let a SUT assert
  the safe empty set on every unevidenced list field for free. Correct charge, no fix.
