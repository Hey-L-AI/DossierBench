# SUT results — what the submissions have scored

This is the record of what the submission side has **measured**, as opposed to what it is (that is
the [reference-submissions section of the README](../README.md#reference-submissions)). It opens
with the nine runs the paper reports, on the current base; everything after that is the dated
record of earlier runs on earlier bases, kept as measured and not re-scored.

Every paid run follows one rule: a `prediction-*.txt` is written **before** the first paid call
and scored line by line in a `result-*.txt` afterward (both under `runs/` in the unreleased
development tree).

## Current: the nine paper runs on `n50-tagfix-2026-10-15`

`prompted-v2` and `notebook-v2` on four backbones, plus `memory-v2` on GPT-5.6 Luna, each on every
checkpoint of all 50 subjects, scored against the released key. Skill (abstain 0.000, oracle 1.000),
mean over the 47 of 50 subjects that reach the AUFC gate:

| backbone | `prompted` | `notebook` | `memory` |
|---|---|---|---|
| `google/gemini-3.7-flash` | **+0.813** | +0.789 | not run |
| `openai/gpt-5.6-luna` | +0.669 | **+0.700** | +0.493 |
| `deepseek/deepseek-v4-pro-0813` | +0.590 | **+0.601** | not run |
| `z-ai/glm-5.3-flash` | **+0.497** | +0.434 | not run |

`mem0` was not run at n=50. How these numbers moved on the way to this base:

### 2026-10-15 — five subjects re-run after the store tagfix

Ten doubled speaker labels removed from five sessions (gen-005-s5, gen-008-s8, gen-017-s1, gen-027-s6,
gen-029-s1); the nine paper runs re-run on those subjects from the edited checkpoint on (390 calls) and
re-scored free on all 50: `runs/result-v2only-all-n50-tagfix-2026-10-15.json` /
`runs/_v2only-all-n50-tagfix.log`. Skill moves ≤0.007 (Luna prompted 0.673→0.669, DeepSeek prompted
0.584→0.590, GLM prompted 0.504→0.497; others ≤0.001); order, constraint rates and all 27-setting
verdicts unchanged. Details: [`provenance/n50-tagfix-2026-10-15.md`](provenance/n50-tagfix-2026-10-15.md).

### 2026-10-14 — the nine paper runs re-scored on the fullpassfix key (pointer)

Free cache replay of the same nine runs on `n50-fullpassfix-2026-10-14` (the 4 gold fixes from the full failure-mode pass):
`runs/result-v2only-all-n50-fullpassfix-2026-10-14.json` / `runs/_v2only-all-n50-fullpassfix.log`.
The gtfix replay was reproduced byte-for-byte first. Skill (prompted / notebook): Gemini 3.7 Flash
0.813 / 0.788, GPT-5.6 Luna 0.673 / 0.700, DeepSeek V4 Pro 0.584 / 0.602, GLM-5.3 Flash 0.504 / 0.433;
memory on Luna 0.492. Mean AUFC moves by at most 0.003; the backbone order and every knob-stability
verdict are unchanged; prompted- and notebook-Luna (a tie) swap rank. Free arms: population 0.117 /
−3.969, random 0.114 / −3.943, both 3,908 preference-field fabrications (gtfix: 3,905).

**2026-10-15 — one missing call filled.** The GLM-5.3 Flash `prompted-v2` run had no reply at gen-007 c29
(the original replay ran without `OPENROUTER_API_KEY`, so the harness carried the c28 card forward). The
call was made on 2026-10-15 (`work/sut-emissions/prompted-v2/z-ai__glm-5.3-flash/gen-007-c29.json`; same
route and provider pin, ~6 weeks after the rest of the run), and the nine runs were re-scored by the same
free replay command into the same two files. The emitted card fields are identical to c28; only the
distribution data points (+2 kept) differ. Every printed number is unchanged (GLM `prompted` skill
0.5036113 → 0.5036126, mean AUFC 0.86837 → 0.86837; error counts, fabrications, hard-constraint failure
rate and bootstrap tables identical; mean final fidelity 0.884 → 0.885). All nine runs now have a reply
at every checkpoint. Previous files kept as `*.pre-c29fill.json` / `*.pre-c29fill.log`.

### 2026-08-30 — v2 prompt on all four backbones (gtfix key; pointer)

Every prompted/notebook v1-vs-v2 pair is now scored at n=50 on the gtfix canonical (full 612/612
caches, paired bootstrap; files `runs/result-{promptv2,notebookv2}-<model>-n50-gtfix-2026-08-30.json`).
Skill v1→v2: Gemini 3.7 Flash +0.826→+0.806 / +0.808→+0.782 (borderline worse); GPT-5.6 Luna
+0.642→+0.684 (sig) / +0.674→+0.698 (tie); DeepSeek V4 Pro +0.617→+0.589 (borderline worse, v1
ahead 27/27, hard-constraint rate 0.26→0.40) / +0.539→+0.607 (n.s., flips); GLM-5.3 Flash
+0.405→+0.517 / +0.309→+0.440 (both sig). The behavioural read — the one clause that fixes
(booking≠liking), and why v2 hurts where it hurts (whole-card wipes of the carried card, soft
requests promoted to accessibility constraints, the ceiling clause as a commit rule) — was
written up in a cross-cut failure analysis on the gtfix key that is not shipped; its verdict counts
are superseded by [`failure-modes/FULLPASS-2026-10-14.md`](failure-modes/FULLPASS-2026-10-14.md).

---

## Historical: the first submissions scored (n=5 pilot, 2026-08-25)

Everything below predates the v2 prompts and the 18 + 4 gold fixes, and was not re-scored. Until
2026-08-25 no model had been served this dataset — the free arm was a gate on the scorer, never a
measurement of a system.

> **The distribution cells here predate counted-occasion distributions (ADR-0063,
> 2026-08-26).** These runs emitted the three distribution fields the old way — the model
> estimating the whole share object in one shot. Every submission now builds them from a counted
> histogram instead, so any number below that turns on `price_band_distribution`,
> `party_size_distribution` or `visit_time_distribution` — and the `distribution-shape.md`
> persona-vs-twin comparison — is stale for those fields and wants a re-run. The AUFC/skill
> headline is dominated by the other 21 fields, but it is not untouched. ADR-0063 is a shape lever,
> not a score one, so a re-run is not expected to move the headline much; it is expected to move the
> shape table.

### The test

| | |
|---|---|
| corpus / answer key | `runs/corpus-n50-credited-union.json`, the n50-union base — canonical when this n=5 test was run 2026-08-25; since superseded, current canonical is `n50-strongaudit-2026-08-29` (blocking 0 on both reader arms) |
| subjects | the **first five**: `gen-000..004`, all public split |
| store | `work/authored/claude-opus-5` (pinned by `store-n50-handedit-2026-08-25.tar.gz`) |
| submissions | all four paid: `prompted`, `notebook`, `memory`, `mem0` — none tuned |
| per run | 57 checkpoints, up to 342 model calls (57 + 57 + 114 + 114); cache is per-model |

**Why the n50-union subset and not the 5-subject credited pilot.** `corpus-pilot-credited-adjudicated.json`
fails `EvidenceMap.validate()` — `cuisines_liked` regresses WEAK→UNEVIDENCED at checkpoint 1, the
WEAK-island bug every credited base before 2026-08-25 carries (see `docs/provenance/CANONICAL.md`).
`sut.evidence_map` refuses it on load. The n50-union base was the only validated credited answer key
at the time (2026-08-25), so the honest 5-subject test is its first five subjects, not the broken
pilot key. (These n=5 scores were not re-run on the later bases; the n=50 section below is the paper's
basis of comparison.)

**The scorer gate, on these exact five subjects** (free, model-independent, run once before any
paid call): `oracle` **1.000** on every subject, `abstain` **0.000**, `population` **−4.175**. The
answer key and scorer are sound on `gen-000..004`; anything a submission gets wrong here is the
submission, not the key.

### The leaderboards

Skill is AUFC normalized so `abstain` = 0.000 and `oracle` = 1.000 — negative means *worse than
emitting nothing*. `fab` / `misread` are counts summed over the five subjects; `constraint` is the
mean per-checkpoint hard-constraint failure rate.

#### `openai/gpt-5.6-luna`

| submission | AUFC | skill | fab | misread | constraint |
|---|---|---|---|---|---|
| `prompted` | 0.796 | **+0.099** | 28 | 81 | 0.00 |
| `notebook` | 0.772 | **+0.091** | 19 | 75 | 0.00 |
| `memory` | 0.719 | **−0.123** | 25 | 84 | 0.20 |
| `mem0` | 0.722 | **−0.134** | 33 | 79 | 0.40 |

#### `google/gemini-3.7-flash`

| submission | AUFC | skill | fab | misread | constraint |
|---|---|---|---|---|---|
| `prompted` | 0.803 | **+0.265** | 7 | 63 | 0.00 |
| `notebook` | 0.810 | **+0.243** | 22 | 55 | 0.00 |
| `memory` | 0.718 | **−0.091** | 70 | 64 | 0.20 |
| `mem0` | 0.667 | **−0.359** | 121 | 75 | 0.20 |

#### `deepseek/deepseek-v4-pro`

| submission | AUFC | skill | fab | misread | constraint |
|---|---|---|---|---|---|
| `notebook` | 0.815 | **+0.180** | 20 | 74 | 0.00 |
| `prompted` | 0.791 | **+0.053** | 27 | 69 | 0.00 |
| `memory` | 0.763 | **+0.038** | 24 | 76 | 0.40 |
| `mem0` | 0.726 | **−0.193** | 58 | 85 | 0.40 |

### The finding: single-pass beats the memory layers — real, not absolute

Skill by submission across the three models:

| skill | luna | gemini-3.7-flash | deepseek-v4-pro |
|---|---|---|---|
| `prompted` | +0.099 | +0.265 | +0.053 |
| `notebook` | +0.091 | +0.243 | **+0.180** |
| `memory` | −0.123 | −0.091 | **+0.038** |
| `mem0` | −0.134 | −0.359 | −0.193 |

**What holds on all three models.** The single-pass extractors (`prompted`, `notebook`) beat the
memory layers (`memory`, `mem0`) on ordering, every time. **`mem0` loses to single-pass *and* falls
below the abstain floor on all three** — its schema-blind, ADD-only bank (ADR-0062)
is a consistent liability. A memory layer buys nothing here and costs 2× the calls.

**What is not absolute.** The strong form — "both stateful SUTs score below doing-nothing" — is a
luna/gemini fact only. On the stronger deepseek-v4-pro, `memory` **recovered to +0.038**, above the
floor and in a statistical tie with `prompted` (delta 0.015, well inside n=5 noise). So a stronger
model's extract→integrate loop can hold enough to beat silence, though still not clearly beat
single-pass. Which of the two single-pass SUTs *wins* also flips by model (`prompted` on luna and
gemini, `notebook` on deepseek).

**The durable claim, narrowed:** a memory layer buys nothing at 2× the calls, and the schema-blind
one (`mem0`) actively hurts on every model — but "stateful is always worse than silence" is a
two-model observation, not a law.

### Why the memory layers lose — the mechanism

A single-pass extractor re-derives the card from the transcript at every checkpoint, so an error is
transient and self-corrects on the next read. A memory SUT writes its error into state, where a
dropped fact stays dropped and a wrong value re-scores on every later checkpoint. The damage
concentrates on the long multi-session subjects (`gen-002` at 16 checkpoints, `gen-004` at 15),
exactly where carried state was supposed to help.

Concrete, `gen-002`, hard constraint `alcohol_free_required` (gold `True`) on luna:

- `prompted` at c9 and c15 emits `True` — it re-reads the transcript cold and re-finds it.
- `memory` at c9 and c15 emits `unknown` — its extract→integrate pipeline **dropped the
  constraint** and never recovered it. That recall miss is the constraint-failure rate firing on
  every later checkpoint.

Constraint failures land **only** on the stateful SUTs, always on the `gen-002` late-checkpoint
cluster (rate 0.20 on luna/gemini, 0.40 on deepseek); `prompted`/`notebook` never break one.

### The failure *route* is model-dependent

The models fail the same architecture differently:

- **luna and deepseek: misread-dominated.** Every submission has more misreads than fabrications
  (e.g. deepseek `memory` 76 misread vs 24 fab). The memory layers accumulate wrong evidence.
- **gemini: fabrication blowup.** Its memory SUTs *over-commit* — `mem0` fabricated **121** times
  across 57 checkpoints (vs deepseek's 58, luna's 33), more than its own misread count. This is
  ADR-0062's reading of mem0 borne out: mem0's bank is written by
  mem0's own prompts — "when in doubt, extract", a 5–15 memory quota — and a flash model that
  over-commits fills that bank with assertions the transcript never supported, which the
  schema-aware readout then faithfully reports as fabrications. The prediction in Pending 0g named
  this before the first call.

### The error is field difficulty first, architecture second

Across all four SUTs on all three models the errors concentrate on the same handful of subjective
preference fields — `cuisines_liked`, `design_sensitivity`, `solo_dining_comfort`,
`formality_ceiling`, `event_types_liked`/`_avoided`, `rating_reliance`, `spice_tolerance` — in
nearly the same order. This is why all four submissions cluster near each other on AUFC (~0.72–0.82).
Example, `gen-002` `cuisines_liked`: gold `['sushi','american']`, and *every* SUT emits
`['burgers','sushi']` — `american` missed, `burgers` invented. A wrong value on an evidenced set is
a misread, and the answer key reads that cell the same way for all of them. The architecture
difference (state persistence) rides *on top of* this shared field difficulty.

### Schema is clean of confound

Checked because a SUT shown a different schema than the scorer grades against would show mismatch
artifacts, not extraction failure. It is clean:

- **One source of truth, wired into every readout.** The schema block each SUT sees is
  `sut.field_menu()`, built at runtime from the `fields.py` registry (field set, value spaces,
  metric, and the same `gloss()` the auditor and editor read). It is injected into every scored
  emission: `prompted`/`notebook` (`sut.py:188`), `memory` (`sut.py:450`), `mem0` (`sut.py:500`).
  No prompt can drift from the scorer by construction.
- **Gold matches the registry exactly.** Corpus schema is `v0.1`; each gold card's fields
  `== ALL_FIELDS_IDS` (no extra, none missing), validated `allow_unknown=False` on load. The free
  arm's `oracle` = 1.000 is the empirical proof — the gold is exactly expressible in the scored
  schema.
- **The "misread" is not a vocab artifact.** `cuisines_liked` is Jaccard over a closed 39-item
  vocab; both `american` (gold) and `burgers` (emitted) are valid members, so `gen-002`'s miss is a
  genuine scored error.

One nuance, by design not defect: the schema is identical at *readout*, but the memory SUTs' *write*
side is not schema-guided — `mem0` populates its bank with mem0's own schema-blind prompts. That is
the intended "untuned memory layer" comparison, and it reinforces the finding: the stateful SUTs
aren't losing because they were shown a wrong schema — they get the right one at readout — they lose
on what their memory layer stored before ever reaching it.

### Prediction scorecards

| claim | luna | gemini | deepseek |
|---|---|---|---|
| structure: single-pass > stateful | HIT | HIT | HIT (weakened — `memory` cleared floor) |
| ordering `memory ≥ notebook ≥ prompted` | MISS (inverted) | — | — |
| both stateful below abstain | (n/a) | HIT | MISS (`memory` +0.038) |
| skill band | MISS (too high) | MISS (flash beat luna) | MISS (prompted below band) |
| misread > fab | HIT | MISS (stateful fabricate) | HIT |
| constraint only on stateful | partial | HIT | HIT |

The prediction the runs kept teaching: **the architectural direction is robust; the level, the
winner-among-single-pass, and the failure route are all model-specific.**

### Caveats

n = 5 subjects, single draw, all public split, and `gen-004`/`gen-002` (worst for everyone) carry
outsized weight in a five-subject mean. These are **smoke tests across three models that now agree
on direction** — not a leaderboard verdict. The `memory`-vs-`prompted` near-tie on deepseek (0.015)
is inside n=5 noise. Nothing here is magnitude, only direction.

### n=50 scale-up — single-pass, v1 prompts, scalarfix base (2026-08-28/29)

The two single-pass systems (`prompted`, `notebook`)
were scored on the **full 50-subject** scalarfix base for three backbones (canonical when
scored; the canonical base has since advanced to `n50-strongaudit-2026-08-29`, whose prune of 5 taste
elements on 3 subjects does not move a 50-subject mean — see the Bases note below). These
v1 numbers were later replaced by the v2 paper runs at the top of this file. At n=50 the stateful
arm was run once, `memory-v2` on GPT-5.6 Luna; `mem0` was not run at n=50.

Skill (AUFC normalized: abstain 0.000, oracle 1.000), mean over all 50 subjects:

| backbone | route | reasoning | prompted | notebook | single-pass winner |
|---|---|---|---|---|---|
| `google/gemini-3.7-flash` | OpenRouter default | default, `medium` (mandatory) | **+0.531** | +0.521 | prompted (ΔAUFC +0.004, CI [−0.003,+0.013] — tie) |
| `openai/gpt-5.6-luna` | OpenRouter default | default-on, `medium` | +0.358 | **+0.385** | notebook (within noise) |
| `deepseek/deepseek-v4-pro-0813` | Alibaba-pinned | default, `high` (default-on: probe pending) | **+0.226** | +0.176 | prompted (ΔAUFC +0.021, CI [+0.003,+0.043], P=0.99) |
| `z-ai/glm-5.3` | Together-pinned | mandatory (unverified — Together route, not the OpenRouter model API) | **+0.103** | — (not run) | n/a — prompted only |

The glm-5.3 row was added 2026-08-29 (`runs/result-sut-glm53-n50-prompted-together-2026-08-29.json`);
it is the weakest single-pass backbone but still clears the abstain floor. Its weakness is almost
entirely *over-commission*: 604 fabrications across 50 subjects (vs deepseek-pro 237, luna 163,
gemini 51) against a normal misread load (269) — the single-pass "when unevidenced, commit" pathology
in its purest form. AUFC/skill for the other three are unchanged from the earlier draws.

Reasoning effort is never overridden in the harness (`judge.OpenRouter` sends no `reasoning` field);
each backbone runs at its OpenRouter-published default `default_effort`. `mandatory` = reasoning cannot
be turned off; `default-on` = on when unset. Values from `/api/v1/models` (2026-08-29). glm-5.3-flash,
used elsewhere, is mandatory at `max`.

**What holds at n=50.** Both single-pass systems clear the abstain floor by a wide margin on every
backbone — the paper's load-bearing "re-reading the transcript is a strong calibrator" survives the
scale-up. The single-pass pair is a **statistical near-tie whose winner flips by backbone** (prompted
on gemini/deepseek-pro, notebook on luna) — so the earlier "notebook is the top single-pass system"
line was an n=5/n=10 artifact and has been removed from the paper. Scaling softens magnitudes:
luna `notebook` moved +0.513 (n=10) → +0.385 (n=50) as the harder subjects entered the mean.

**What does NOT survive the scale-up: "constraint failures land only on the stateful SUTs."** That
was an n=5 observation. At n=50 the single-pass constraint-failure rate is non-zero on every backbone
— gemini 0.04–0.06, glm-5.3 0.12, luna 0.08–0.16, and **deepseek-v4-pro 0.42/0.34**. The direction is
still architectural, though: deepseek-v4-pro's are 78% *invented* atoms (241 cells vs 67 missed),
almost all the phantom `accessibility_needs=['seating_required']` escalation — single-pass breaks
constraints by over-asserting them, not by dropping them (the stateful failure). The transcript
adjudication in [`FAILURE-MODES.md`](FAILURE-MODES.md) (Model cross-cuts, 2026-08-29 UPDATE) confirms
these invented constraints as genuine, not answer-key artifacts.

**Bases — all three on the scalarfix base (parity).** luna was re-run on scalarfix
2026-08-29; the shift off its gradefix predecessor is +0.010 on both single-pass kinds (notebook
+0.375 → +0.385, prompted +0.348 → +0.358), same ordinal — empirically confirming the
gradefix↔scalarfix delta is immaterial to a 50-subject skill mean. The subsequent
scalarfix→`n50-strongaudit-2026-08-29` prune is smaller still (5 taste elements on gen-005/013/031,
all phantom-removals that can only *raise* an over-committed twin), so these single-pass means carry
over unchanged; a replay-cached re-score on strongaudit is free if exact parity is wanted. Free-arm
gate on the scalarfix n=50 base: oracle 1.000, abstain 0.670/0.000, population 0.107/−4.780
(`runs/result-sut-deepseek-n50-freearm-2026-08-28.json`; deepseek and gemini share byte-identical
anchors).

Artifacts: `runs/result-sut-gemini-n50-notebook-prompted-scalarfix-2026-08-28.{json,txt}`,
`runs/result-sut-deepseek-pro-n50-notebook-prompted-2026-08-28.json` +
`runs/result-sut-deepseek-pro-n50-2026-08-29.txt`,
`runs/result-sut-luna-n50-notebook-prompted-scalarfix-2026-08-29.json` (supersedes the gradefix
`...-gradefix-2026-08-28.json`).

### Reproduce (the n=5 pilot)

```sh
# needs OPENROUTER_API_KEY in the environment; the n=5 pilot key below is not shipped
# parallelize: one process per SUT, shared cache keyed by kind/model (no collision)
for KIND in prompted notebook memory mem0; do
  python scripts/score.py \
    --corpus runs/corpus-n50-credited-union.json --store work/authored/claude-opus-5 \
    --subjects gen-000 gen-001 gen-002 gen-003 gen-004 --workers 5 \
    --sut ${KIND}:<provider/model> --json runs/result-sut-<model>-pilot5-${KIND}.json &
done
wait
```

Then read the four per-SUT JSONs directly for the leaderboard — do **not** re-run a combined
scoring pass, `mem0` rebuilds its store even on cache hits and it is slow. The `aggregate[0]` block
of each JSON carries `mean_aufc`, `mean_aufc_skill`, `total_errors`, `constraint_failure_rate`.

### Artifacts

| model | prediction | result | per-SUT json |
|---|---|---|---|
| luna | `runs/prediction-sut-luna-pilot5-2026-08-25.txt` | `runs/result-sut-luna-pilot5-2026-08-25.txt` | `runs/result-sut-luna-pilot5-2026-08-25.json` |
| gemini | `runs/prediction-sut-gemini-pilot5-2026-08-25.txt` | `runs/result-sut-gemini-pilot5-2026-08-25.txt` | `runs/result-sut-gemini-pilot5-2026-08-25.json` |
| deepseek | `runs/prediction-sut-deepseek-pilot5-2026-08-25.txt` | `runs/result-sut-deepseek-pilot5-2026-08-25.txt` | `runs/result-sut-deepseek-pilot5-{prompted,notebook,memory,mem0}.json` |

All three share the emission cache at `work/sut-emissions/`, keyed by kind and model, so a re-run
pays only for prompts that moved.
