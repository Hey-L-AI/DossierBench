# The weak band: the score is deliberately blind to how well soft evidence is read

*Measured 2026-08-26 on the scored SUT emissions (`work/sut-emissions/`, the four
submissions × three models over the pilot subjects, `runs/corpus-n50-credited-union.json`).
Diagnostic: `python3 -m persona_twin.discern`. No model calls — this reads runs that already
happened, and takes every cell's grade and agreement from `scorer.score_checkpoint`, so it
cannot drift from the published score.*

## The claim

On the weak band the scorer neither rewards nor punishes a *posture*. Committing scores the
value's agreement `a_f`; abstaining scores `WEAK_CREDIT`; and the two are calibrated to be
equal for a system reading exactly what the evidence licenses (`metrics.weak_abstention_credit`
derives the constant as that indifference point). That is the correct scoring rule and it
stays — it is what stops the benchmark from paying for a fixed posture on under-determined
fields.

But it has a consequence: **two systems can post the same `A(t)` on the weak band — one by
abstaining everywhere and banking the credit, the other by committing and reading the soft
signal correctly — and the headline score cannot tell them apart.** "How well does the SUT
read a hint?" is a real difference between systems that the score is designed not to see. This
diagnostic restores that axis. It changes no score.

## What it measures

For every WEAK preference field-checkpoint, over the cells a SUT chose to commit on:

- **commit_rate** — how often the SUT committed rather than abstained on the weak band.
- **acc** (`commit_accuracy`) — mean `a_f` given committed. The raw number.
- **discern** (`discernment`) — `acc − chance`, where **chance** is the strongest population-only
  guess (`discern.ChanceModel`, fit on the public split only) scored on the *same* committed
  cells: population-mode for ordinal and distribution fields, and the best constant set
  (`best_population_set`) for the Jaccard fields, where the mode collapses to the empty set.
  Positive means the SUT read more than the population prior alone would give. This is the
  headline: it is the part of accuracy that is not base-rate luck.
- **calib** (`calibration`) — `acc − WEAK_CREDIT`, against the scorer's own indifference point.
  Positive means committing beat what the evidence licenses on average, so the SUT *should*
  commit there; negative means it would have scored better abstaining. This ties the diagnostic
  back to the exact quantity `score_checkpoint` trades against.
- **wrong** (`wrong_rate`) — of committed cells, the share scored below `ERROR_THRESHOLD`.

Both reference points are paired over the committed cells, so a SUT's own selectivity is held
fixed rather than flattering the number. Read the triple `(commit_rate, acc, discern)` as one
unit: a high accuracy at a low commit rate is discernment *of when to bet*, not of reading in
general.

## Why two reference points, and the confound they are built around

A WEAK field is one "a careful reader could abstain" on — the value is under-determined by
construction. So raw weak-commit accuracy blends genuine reading with base-rate luck: committing
the population's modal value is often right, and the U-band fabrication rule that kills such
guessing does not apply on the weak band. A single accuracy number cannot separate the two,
which is why `discern` subtracts the base-rate line and `calib` subtracts the licensed floor.
Neither the raw number nor either lift is interpretable alone.

## What it shows (first scored runs)

Pooled over the weak preference cells (n ≈ 574 per run; ordinal 373, set 112, dist 89):

| band | typical acc | typical discern | typical calib | reading |
|---|---|---|---|---|
| ordinal | 0.84–0.90 | **+0.28 to +0.34** | ≈ 0 (slightly +) | SUTs read weak ordinals well above the prior and are roughly calibrated |
| set | 0.53–0.63 | +0.41 to +0.53 | **−0.20 to −0.31** | real lift over the best population set (chance ≈ 0.10–0.13); but see caveat 2 on `calib` |
| dist | 0.71–0.83 | **≈ 0 or negative** | negative | SUTs barely beat base-rate on weak distributions |

Two cross-cutting patterns: the single-pass submissions (`prompted`, `notebook`) commit more
on the weak band and post higher discernment than the memory layers (`memory`, `mem0`) — they
read soft signal better, consistent with the score-side finding that single-pass beats the
memory layers. And the near-zero discernment on distributions corroborates
`docs/distribution-shape.md`: weak distribution commits are mostly mode-spikes that a base-rate
guesser matches.

## Caveats — where a column is not clean

- **Sets — `discern` fixed, `calib` still loose.** `baselines.PopulationMode`'s modal set is
  empty (per-member majority, and no member clears 50% over the vocabulary), which scored a
  spurious `chance = 0.000` on every set cell. `discern.ChanceModel` replaces that with
  `best_population_set` — the constant set maximising mean Jaccard over the public split — so
  the set base rate is now a real ≈ 0.10–0.13 and `discern` on sets is an honest lift over the
  best a population-only guesser could do (it dropped from an inflated ≈ +0.59 to ≈ +0.45). But
  `calib` on sets is still confounded the other way: the flat `WEAK_CREDIT` (5/6) is generous
  for sets, as `weak_abstention_credit` records, so the strongly negative `calib` overstates
  over-commitment. So on sets, **trust `discern`, treat `calib` as loose** — the latter waits on
  caveat 2. The ordinal family — 13 of the 20 scored fields — is clean on both.
- **`calib` uses a flat constant.** `WEAK_CREDIT` is exact for a 4-level ordinal and approximate
  elsewhere. When §7.3 emits real licensed sets per weak field (spec §13), `calib` sharpens
  automatically — `weak_abstention_credit` already takes the field and gold for that reason.
- The diagnostic is only exercised where a run actually produced weak cells; an empty axis is
  reported as `—` (`None`), never as 0.0.

## The n50 read (luna, 2026-08-28)

Re-run over all 50 subjects on the gradefix base (canonical on 2026-08-28 when this read was run;
since superseded by scalarfix → `n50-strongaudit-2026-08-29`), `prompted` and `notebook` only
(both **5194 weak cells**; `mem0`/`memory` are still the 10-subject cache and are not part of
this read):

    python -m persona_twin.discern --run luna
    # defaults: emissions under data.work()/"sut-emissions", key = data.corpus("key");
    # this read was taken with --corpus pointing at the 2026-08-28 gradefix key (not shipped)

| band | commit | acc | discern | calib | wrong | reading |
|---|---|---|---|---|---|---|
| ordinal | 91% | 0.956 | **+0.39** | **+0.12** | 1% | reads well above the prior, committing is licensed, and it commits — clean |
| set | 96–97% | 0.76 | **+0.64** | **−0.07** | 15–16% | **over-commits**: reads far above chance yet `acc 0.76 < WEAK_CREDIT 0.833`, so committing scores below abstaining, and it commits anyway |
| dist | 0% | — | — | — | — | abstains entirely once distributions are counted from occasions; nothing to read |

The set row is the actionable finding, and it is bigger at n50 than the pilot suggested: the SUT
extracts genuine signal from weak set hints (discern +0.64) but the licensed move is still to
abstain (calib < 0), and it commits at 96–97%, leaving points on the table the scorer already
docks. The negative-`calib` **sign** is robust; its magnitude is softened by caveat 1 (the flat
5/6 credit is generous for sets) and will sharpen once §7.3 emits real licensed sets. The
distribution row is now a clean 0% commit rather than the pilot's noisy near-zero discern —
building distributions from a counted occasion histogram moved weak distributions to abstention,
exactly as the shape note argued.

`prompted` and `notebook` are indistinguishable on every weak axis (discern +0.453 both; calib
+0.074 vs +0.071), which is the same verdict the paired-bootstrap block of the report returns on
their headline AUFC. `discern` now defaults to the published answer key (`data.corpus("key")`), so a
fresh run reads the current base rather than the one this note was measured on.
