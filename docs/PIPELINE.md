# The pipeline — creating, extending and curating a corpus

Scoring a submission against the published corpus needs none of this; see the repository
[`README.md`](../README.md) for that. This document is the other direction: **how a corpus is
made**, which is what you need in order to extend DossierBench, rebuild it from scratch, or build
a differently-shaped benchmark on the same machinery.

Read [`SCORING.md`](SCORING.md) first for what an answer key *is*. Read
[`GT-POLICIES.md`](GT-POLICIES.md) before authoring gold, because most of the judgement calls that
look arbitrary in the data are recorded there with their reasons.

---

## The shape of it

Six stages. Three cost model calls; three do not.

| # | stage | what it produces | model calls |
|---|---|---|---|
| 1 | **draw** | a corpus: subjects, cards, and the beat schedule that will evidence them | none |
| 2 | **author** | a store: one transcript per session, written to the brief | one per session |
| 3 | **audit** | a reading: what two independent readers say the page actually carries | one per session per reader |
| 4 | **credit** | an answer key: the schedule's map plus the cells a reader proved were surfaced | none |
| 5 | **edit** | repairs to the sessions the audit found defective, behind a gate | one per repair attempt |
| 6 | **promote** | a base: gold + manifest + hashes, and a release package | none |

The invariant that makes any of it affordable: **everything caches on a fingerprint of what the
model was shown.** Re-running a stage after an unrelated change costs nothing. Re-running it after
a brief moves pays only for the sessions whose brief moved.

## Before you start

```bash
pip install -e '.[all]'
```

Model access, for stages 2, 3 and 5. Two invokers ship:

- **`ClaudeCli`** — shells out to `claude -p --model <model>`. Needs the Claude Code CLI on
  `PATH`. This is what the published corpus was authored and read with.
- **`OpenRouter`** — needs `OPENROUTER_API_KEY` in the environment and the `judge` extra
  installed. This is what the submission side was scored with.

Everything a run writes lands under `work/` (override with `DOSSIERBENCH_WORK`). Nothing in this
repository writes outside it.

---

## 1. Draw a corpus — free

```bash
# a new corpus: 50 subjects at seed 7
python -m persona_twin.dataset -n 50 --seed 7 --json work/corpus.json
```

**The generator is index-stable, so adding subjects is an extension rather than a rebuild.**
Raising `-n` at a fixed seed appends; it does not reshuffle. `plan_subjects(5)` is
`plan_subjects(50)[:5]`, and that property is what lets an existing store cache-hit when the
corpus grows.

It is also the property most worth *proving* rather than assuming, because everything downstream
is priced on it:

```bash
python -m persona_twin.dataset -n 15 --seed 7 --json work/corpus-15.json
python - <<'PY'
import json
a = json.load(open('work/corpus.json'))
b = json.load(open('work/corpus-15.json'))
new = {i['subject_id']: i for i in b['items']}
moved = [i['subject_id'] for i in a['items']
         if json.dumps(i, sort_keys=True) != json.dumps(new[i['subject_id']], sort_keys=True)]
print('MOVED:', moved or 'none')
PY
```

To extend one subject's timeline rather than add subjects — it refuses to write if the prefix
moved:

```bash
python -m persona_twin.dataset --json work/corpus.json --extend gen-001 20
```

A corpus carries the **model-free map**: the per-checkpoint STRONG/WEAK/UNEVIDENCED grade derived
from the schedule alone, with no model in the loop. That map is already a usable
answer key. Stages 3–4 only ever *add* to it, and every addition is provenanced.

## 2. Author the transcripts — one call per session

```bash
python -m persona_twin.author \
    --corpus work/corpus.json --out work/authored \
    --authors claude-opus-5 --workers 8 2>&1 | tee work/author.log
```

The unit is the session, so sessions are independent and parallel. A re-run with unchanged briefs
cache-hits and costs nothing.

**Snapshot the store before any pass that could rewrite it.** This is not ceremony — it is the
only way the cache-hit claim is falsifiable rather than merely expected, and this pipeline has
lost pre-edit text more than once:

```bash
find work/authored/claude-opus-5 -name '*.json' ! -name '._*' | sort \
  | xargs shasum -a 256 > work/store-before.sha
# ... run the pass ...
shasum -a 256 -c work/store-before.sha | grep -v ': OK$'   # must print nothing
```

The free read-out over what you just wrote — lengths, trajectories, beat coverage, no model calls:

```bash
python scripts/read_corpus.py --corpus work/corpus.json --store work/authored/claude-opus-5
```

## 3. Audit — the free arm, then two readers

The free arm is deterministic and costs nothing. Run it first; it catches the defects that do not
need a reader.

```bash
python scripts/audit.py --corpus work/corpus.json --store work/authored/claude-opus-5 \
    --json work/audit-free.json
```

Then the reader arm. `--all` rather than a stratified sample when the question is about every
cell, which it is for a release:

```bash
python scripts/audit.py --corpus work/corpus.json --store work/authored/claude-opus-5 \
    --audit --all --workers 6 --verdicts work/verify --json work/audit-opus.json

# the second reader, a different model, sharing one --verdicts root
python scripts/audit.py --corpus work/corpus.json --store work/authored/claude-opus-5 \
    --audit --all --workers 6 --model claude-sonnet-5 \
    --verdicts work/verify --json work/audit-sonnet.json
```

**A reader pass is not finished until `unreadable` is 0.** A reply that did not parse is a wrong
answer, not an abstention — this pipeline once read format variance as abstention across 19 of 23
cells from one model. Re-run into the same cache: everything that parsed is served from it and
only the unparsed replies are re-called.

```bash
python scripts/audit.py --corpus work/corpus.json --store work/authored/claude-opus-5 \
    --audit --all --workers 3 --verdicts work/verify --json work/audit-opus-retry.json
```

Concurrency note: **grading is memory-bound, not rate-limited.** Six workers is the ceiling on a
normal machine; 28 workers has OOM'd a box and failed 1,688 cells with empty stderr.

## 4. Credit — free

The audit says what a reader found on the page. Credit turns that into answer-key edits, under
three keys and six falsifiers.

The three keys a cell must pass to be promoted from UNEVIDENCED to WEAK: the **card** holds the
value, the **page** carries a leak of it, and a **second reader on a different model** confirms it
under a targeted ask with the card withheld.

```bash
# key 3 — the targeted ask, card withheld, verbatim quote checked, level required.
# Three repetitions is the requirement, not the ceiling; majority decides.
for rep in 1 2 3; do
  python scripts/confirm_key3.py \
      --store work/authored/claude-opus-5 --report work/audit-opus.json \
      --second work/verify/claude-sonnet-5 --confirms work/confirm-rep$rep \
      --out work/confirm-key3-rep$rep.json
done

# the credit pass itself: a credited report and a credited map, written beside the
# model-free map rather than over it
python scripts/measure_credit.py \
    --corpus work/corpus.json --store work/authored/claude-opus-5 \
    --report work/audit-opus.json \
    --verdicts work/verify/claude-opus-5 --second work/verify/claude-sonnet-5 \
    --confirm-answers work/confirm-key3-rep{1,2,3}.json \
    --out work/result-credit-opus.json \
    --absolved-out work/audit-opus-absolved.json \
    --corpus-out work/corpus-credited.json \
    --pending-out work/adjudications-pending.json | tee work/credit.log
```

`measure_credit.py` runs **one arm** — a report from reader A, key 3 from reader B. A leak reader
A never filed is a cell that pass can never offer, so run it both ways and merge:

```bash
python scripts/merge_credits.py --corpus work/corpus.json \
    --arm work/result-credit-opus.json:work/audit-opus.json \
    --arm work/result-credit-sonnet.json:work/audit-sonnet.json \
    --corpus-out work/corpus-credited.json \
    --report-out work/audit-absolved.json
```

Cells the two readers split on that no key settles go to an owner queue
(`--pending-out`). A person decides them; the decision is pinned to the page's fingerprint and
goes stale the moment that transcript is edited. Dismissals — findings a person refused outright —
are applied with `scripts/apply_dismissals.py`.

## 5. Edit — one call per repair attempt

Repairs are turn-level: the reader names the message numbers a defect spans, the editor rewrites
those messages, and the result is spliced back. A re-author is never needed to fix a defect.

**The work list comes from the credited report; the gate baseline comes from the raw one.** They
are different files on purpose — a repair is gated on the problems it *introduces*, not on the
ones the page already had.

```bash
python scripts/edit.py \
    --corpus work/corpus.json --store work/authored/claude-opus-5 \
    --report work/audit-absolved.json \
    --baseline-report work/audit-opus.json \
    --edits-out work/edits.json \
    --suggestions-out work/edit-suggestions.json --workers 6
```

`--suggestions-out` catches the candidates the collateral cap refuses on price: the
target defect gone, nothing introduced that credit cannot settle, but more than one new leak
elsewhere. **Those are lost if nobody asks for the file.** An owner accepts one by re-gating it with
`scripts/gate_hand_edits.py --apply-suggested`, which is the single refusal the cap defers to a
person by design.

Hand-authored repairs go through the editor's own gate rather than around it —
`scripts/gate_hand_edits.py` (`--dry-run` is offline and free, `--apply` writes the store), and
`scripts/apply_hand_edits.py` applies an owner edit set through the editor's persist path. Pure
message rewrites only: **an insertion remaps message numbers**, and every decision and beat span
is keyed by them.

Then re-audit independently, into its own cache:

```bash
python scripts/audit.py --corpus work/corpus.json --store work/authored/claude-opus-5 \
    --audit --all --workers 6 --verdicts work/verify-post-edit \
    --json work/audit-post-edit.json
```

An edit pass writes transcript text and leaves the brief alone, so an edited session cache-hits at
the author and its repairs survive. That is a property of the fingerprint, not a law — check the
snapshot before reading any number off the new store.

## 6. Promote and release — free

A **base** is a directory holding the two gold corpora (credited key and model-free map) plus a
`SHA256SUMS` over them, and a `MANIFEST.md` recording the pinned inputs, the derived artifacts,
what it answers that the previous base did not, what is **not** settled, and the exact commands to
rebuild it. See [`provenance/n50-tagfix-2026-10-15.md`](provenance/n50-tagfix-2026-10-15.md) for the
one the published data ships from, and
[`provenance/CANONICAL.md`](provenance/CANONICAL.md) for why "which base is canonical" is a file
rather than a claim in a paragraph.

The ceremony a base must pass before it is promoted, all free:

| gate | must be |
|---|---|
| `EvidenceMap.validate()` | passes on every subject |
| `sut.verify_gold` | 0 findings |
| oracle | 1.000 on every subject at every checkpoint |
| blocking findings | 0 on both reader arms |
| test suite | green |

`python scripts/score.py --corpus work/corpus-credited.json` runs the oracle/abstain/population
arm of that, which is the one that catches an answer key that has gone wrong.

Then package it:

```bash
python scripts/build_release.py \
    --base work/base --store work/authored/claude-opus-5 --out work/release
```

The builder decides nothing: it copies the two gold corpora verbatim, checks their hashes against
the base's own manifest, extracts the served dialogue, emits the merged JSONL view, and verifies
every checkpoint of that view reconstructs the canonical gold **before** `SHA256SUMS` is written.
Its output is exactly what `DOSSIERBENCH_DATA` may point at.

---

## What you cannot run against the published data

The release trims each session to what a submission is served — `{task_id, subject_id, session,
checkpoint, date, is_distractor, removable, transcript}`. **Stages 3, 4 and 5 read `beats`,
`turns`, `instrument` and `self_report`**, which are generation artifacts and are deliberately not
published: shipping them would hand a submission the answer key.

So the curation stages run against a store **you authored** (stage 2), not against the download.
`persona_twin.data.authored_store()` is their default for exactly this reason, and pointing them
at `data.transcripts()` fails on a missing key rather than silently reading nothing.

Stage 1 and stage 6 are free and need no store. The scoring side needs only the download.

## Analysis tools

Free, and all of them replay the emission cache rather than making calls:

| script | what |
|---|---|
| `scripts/extract_failures.py` | one record per committed-and-wrong cell, per fabrication, per constraint failure — with emitted value, gold value, evidence grade and the scorer's `error_kind` |
| `scripts/episodes_and_slices.py` | collapses per-checkpoint failure cells into **episodes**, so a defect is verified once rather than once per checkpoint it persists |
| `scripts/tag_failure_modes.py` | provisional mode labels off the emitted-vs-gold shape — hypotheses for a reader to confirm, not final labels |
| `scripts/aggregate_verdicts.py` | joins adjudicated verdicts back on and produces the verified mode tables, GENUINE-only |
| `scripts/audit_gt_across_models.py` | every charged error across every cached run — an error *all* models make on one cell is a signal the gold is wrong, not the submission |
| `scripts/compare_suts.py` | cell-level transition table between two submissions on one corpus |
| `scripts/dump_transcripts.py` | served transcripts + gold + grades per subject, for transcript-grounded adjudication |
| `scripts/show_checkpoint.py` | one checkpoint: served text, authored beats, continuity |

`audit_gt_across_models.py` into `audit_gt_summarize.py` is the loop that produced the 18 gold
fixes of the 2026-08-30 `n50-gtfix` base. It is the most useful thing here if you are extending the corpus: the cells
every system gets wrong are where your gold is wrong.

## The rule the corpus was built under

**Nothing is scored until it is predicted.** A run is predicted before its first call in a
`prediction-*.txt` and scored against it afterwards in a `result-*.txt`, line by line. It is not
bookkeeping — writing down what each outcome would mean *before* the run is what turned several
two-point differences into findings instead of vibes, and skipping it is what made two other runs
uninterpretable after the fact.

The free arms are exempt, because they make no draw: their numbers are fixed by construction
rather than measured.
