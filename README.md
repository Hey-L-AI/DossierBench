# DossierBench

**Scoring persona memory on calibration, not recall.**

Memory systems for conversational assistants are usually evaluated on whether they **recall** a
stored fact. That target rewards a system for committing to a value and is silent on the failure
mode that matters most in personalization: **confidently asserting a preference the user never
expressed.** DossierBench scores a persona memory on calibration instead.

Each synthetic subject carries a ground-truth persona card and a derived **evidence map** that
records, for every one of 24 preference fields at every checkpoint, whether the conversation so
far makes that field STRONG (pinnable), WEAK (hinted), or UNEVIDENCED (unsupported). A system is
served one checkpoint of new conversation at a time and must emit the whole card, including
explicit abstentions. **Committing to an unevidenced field is a fabrication and is penalized even
when the value happens to match ground truth; abstaining on it is free.**

This repository is the code. The corpus — 50 subjects, 612 sessions — is published separately at
[**`tangents/dossierbench`**](https://huggingface.co/datasets/tangents/dossierbench) and is
downloaded on first use.

**Paper:** *DossierBench: Scoring a Recommender's Model of You on Calibration, Not Recall*
(PALM workshop at NeurIPS 2026). Source and PDF are in [`paper/`](paper/).

---

## Quickstart

```bash
pip install -e '.[data]'
python scripts/score.py
```

That runs the three submissions that cost nothing and is the whole installation check. It must
print:

| submission | AUFC | skill |
|---|---|---|
| `abstain` | 0.629 | **0.000** |
| `population` | 0.117 | −3.969 |
| `oracle` | 1.000 | **1.000** |

Those three numbers are properties of the *scorer*, not measurements of a system: a map-reading
oracle scores exactly 1 and an always-abstain system scores exactly 0, by construction. **A
deviation is a defect in the answer key or in the harness, never in a submission** — which is why
this is the first thing to run after any change, and the gate every answer key has to pass before
it ships.

To score a real system, one model call per checkpoint:

```bash
export OPENROUTER_API_KEY=...
python scripts/score.py --sut prompted --dry-run          # what it would cost
python scripts/score.py --sut prompted:google/gemini-3.7-flash \
                        --sut notebook:google/gemini-3.7-flash \
                        --json work/result.json
```

Replies cache under `work/sut-emissions/`, fingerprinted on what the model was shown, so a re-run
pays only for the checkpoints whose prompt moved.

## The scoring contract

- The system is served **one checkpoint of new conversation at a time** and emits the whole
  24-field card each checkpoint, with `"unknown"` for any field it abstains on. Prior conversation
  is **not re-served** — carrying state forward is the system's job.
- Each emitted field is scored against its grade at that checkpoint:
  - **UNEVIDENCED** — abstaining is free; committing any value is a **fabrication** and is
    penalized, even if it matches the latent card.
  - **WEAK** — the field is hinted; a value earns partial credit under the field's metric.
  - **STRONG** — the field is pinned; a value is scored in full.
- Per-field agreement uses the field's declared metric: Jaccard for set-valued fields, ordinal
  distance for 4-level ordinals, `1 − JSD` for distributions, exact for the one boolean.
- **List values are time-aware.** For the four taste-list fields, the gold at checkpoint *t* holds
  only the elements the conversation has surfaced *through* *t* — a system is never scored against
  an element the user has not yet voiced.
- The per-checkpoint fidelity trajectory is integrated (area under the curve) and normalized into
  a **skill score** anchored so always-abstain is 0 and the map-reading oracle is 1.

A checkpoint with fewer than `MIN_EVIDENCED_FOR_AUFC = 7` evidenced fields is scored but not
*averaged* — the mean of a two-field checkpoint is noise. That is why the report's subject count
reads 47 rather than 50: three subjects never reach an averageable checkpoint.

[`docs/SCORING.md`](docs/SCORING.md) is the full version, including what is still open.

## The two golds

Both describe the same subjects, conversations and values. They differ only in the **grade layer**
— which decides whether a field is expected to be committed at a given checkpoint.

| | what it is | when to use it |
|---|---|---|
| **answer key** (default) | schedule-derived grades **plus a credit pass**: two reader models promoted cells that were genuinely surfaced but that the schedule alone left UNEVIDENCED | the headline number |
| **model-free map** | grades from the generation schedule **alone**, no LLM in the loop | a model-independent, fully reproducible ground truth, or a sensitivity check |

They agree everywhere except **28 of 50 subjects**, which carry at least one reader-credited cell.
Against the key those cells expect a commitment; against the map they are UNEVIDENCED, so
abstaining is correct. Every promotion is logged in the subject's `evidence_provenance`.

```python
from persona_twin import data
data.corpus()        # the answer key
data.corpus("map")   # the model-free map
```

**A read-out must say which gold it used.** The two are not comparable cell-for-cell.

## Reference submissions

None are tuned. They exist to put points on the state axis, not to win.

| kind | state | calls/checkpoint |
|---|---|---|
| `abstain` | none — the 0 anchor | 0 |
| `population` | the modal card fitted on gold | 0 |
| `random` | a valid value per field | 0 |
| `oracle` | reads the evidence map — the 1 anchor | 0 |
| `prompted` | the card is the whole state; re-read each checkpoint | 1 |
| `notebook` | the same loop with a free-text scratchpad | 1 |
| `memory` | an extract phase and a read-out phase | 2 |
| `mem0` | [mem0](https://github.com/mem0ai/mem0) as the memory layer, its prompts untouched | 2 |

`mem0` is the only one nobody here designed — the other three were written by the same hands as
the scoring rule, so a leaderboard of just those is one author's prompts against that author's own
criterion. It needs the `mem0` extra.

Specs are `kind` or `kind:model`, so `--sut prompted:opus --sut prompted:sonnet` scores one system
on two backbones side by side.

## Creating and extending a corpus

The generator, the authoring runner, the two-reader audit, the credit pass and the turn-level
editor are all here. **[`docs/PIPELINE.md`](docs/PIPELINE.md)** is the recipe, end to end.

The short version — the generator is index-stable, so adding subjects appends rather than
reshuffles, and an existing store cache-hits:

```bash
python -m persona_twin.dataset -n 50 --seed 7 --json work/corpus.json
python -m persona_twin.author --corpus work/corpus.json --out work/authored \
    --authors claude-opus-5 --workers 8
```

**The curation stages need a store you authored, not the published one.** The release trims each
session to what a submission is served; `verify`, `credit` and `editor` read authoring-time facts
(`self_report`, `free_offers`, `instrument`) that are not published. `docs/PIPELINE.md` says which
stage needs which, and `persona_twin.data` defaults each script to the right side.

## What has been measured

Skill (abstain 0.000, oracle 1.000), mean over the 47 of 50 subjects that reach the AUFC gate, one run
per cell, on the canonical base `n50-tagfix-2026-10-15` (the base the published data ships from):

| backbone | `prompted` | `notebook` | `memory` |
|---|---|---|---|
| `google/gemini-3.7-flash` | **+0.813** | +0.789 | not run |
| `openai/gpt-5.6-luna` | +0.669 | **+0.700** | +0.493 |
| `deepseek/deepseek-v4-pro-0813` | +0.590 | **+0.601** | not run |
| `z-ai/glm-5.3-flash` | **+0.497** | +0.434 | not run |

Both single-pass systems clear the abstain floor by a wide margin on every backbone. The
`prompted`/`notebook` order flips by backbone and is within bootstrap noise except on Gemini 3.7
Flash (paired ΔAUFC +0.009, 95% CI [+0.001, +0.017]). The backbone moves skill by up to 0.36; the
choice between the two single-pass systems by at most 0.06. The one stateful run (`memory` on
GPT-5.6 Luna) falls below both single-pass systems on its backbone (ΔAUFC −0.065 and −0.067, both
significant) at twice the call budget; `mem0` has not been run at n=50.

[`docs/SUT-RESULTS.md`](docs/SUT-RESULTS.md) is the full record, and
[`docs/FAILURE-MODES.md`](docs/FAILURE-MODES.md) is the catalog of *how* systems fail here, with a
transcript-adjudicated example per mode.

## Layout

```
src/persona_twin/     the library — 29 modules
  data.py             where the corpus comes from; every path resolves through here
  fields.py           the 24-field registry: anchors, metrics, vocabularies
  generator.py        draws a subject: card, schedule, evidence map
  schedule.py         the prefix-stable scheduler — the invariant and its three rules
  dataset.py          the corpus CLI: draw, extend
  author.py           the authoring runner — per-session parallel, per-session cached
  transcript.py       authoring prompts, message numbering, locating and splicing
  verify.py           the audit: free deterministic arm, reader arm, and their reconciliation
  credit.py           the three keys, and the map they produce
  editor.py           turn-level repair driven by the reader's findings, behind a gate
  scorer.py           the scoring rule
  sut.py              the submission side: serving, emission, parsing, cache, report
scripts/              one per pipeline stage, plus the free analysis tools
docs/                 SCORING · PIPELINE · GT-POLICIES · FAILURE-MODES · SUT-RESULTS
docs/provenance/      which base is canonical, and its manifest
paper/                the PALM @ NeurIPS 2026 paper (LaTeX source, PDF, figures, poster)
tests/                916 tests
work/                 everything a run writes (gitignored; DOSSIERBENCH_WORK to move it)
```

## Tests

```bash
pytest                    # 914 hermetic tests, no network, no model calls
pytest -m data            # + 2 gates over the published answer key (~2.7 MB fetch)
```

The data-backed gates assert invariants of the shipped gold — the oracle scores 1.000 on every
subject, and every `card_at` passes the structural gate. They are opt-in because a fresh clone
running its test suite should not silently download a dataset.

## Caveats

- **All 50 subjects are public.** There is no held-out split; a public evaluation set can be tuned
  against. Treat leaderboard numbers accordingly.
- **Synthetic.** Subjects and conversations are generated, not drawn from real users. No gold
  value has external ground truth behind it.
- **Single-author transcripts.** The shipped dialogue was written by one model
  (`claude-opus-5`); grades were assigned by a separate two-model reader pass.
- **The gold has judgement in it.** Where a call could have gone either way — does accepting a
  bot-offered booking evidence a liking? — it was made by a person and recorded, with the
  reasoning, in [`docs/GT-POLICIES.md`](docs/GT-POLICIES.md). Read that before disputing a cell.

## A note on references in comments and docs

This repository was cut from a private development tree. Some code comments and dated docs cite
evidence that stayed there: files under `runs/` or `probe_*/`, a `RESUME.md` work log, sections of
an internal `spec-v0`, and design records numbered `ADR-00xx`. Read such a reference as "the
evidence existed and was checked on that date", not as a link. Everything needed to run, score and
rebuild the benchmark is in this repository and the published dataset.

## License and citation

Code is Apache-2.0. The dataset is CC-BY-4.0.

```bibtex
@inproceedings{bilal2026dossierbench,
  title     = {DossierBench: Scoring a Recommender's Model of You on Calibration, Not Recall},
  author    = {Bilal, Muhammad and Gra{\c{c}}a, Jo{\~a}o V.},
  booktitle = {Personalized, Aligned, Long-Term Memory for AI Systems (PALM) Workshop at NeurIPS 2026},
  year      = {2026},
  url       = {https://github.com/Hey-L-AI/DossierBench}
}
```
