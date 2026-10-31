# n50-fullpassfix-2026-10-14

**The answer key the published data ships with** (canonical 2026-10-14; since 2026-10-15 carried
unchanged, same files and hashes, by `n50-tagfix-2026-10-15`, which fixed the store). Supersedes `n50-gtfix-2026-08-30`. Applies the **4 gold fixes** from the 2026-10-14
full failure-mode pass ([`../failure-modes/FULLPASS-2026-10-14.md`](../failure-modes/FULLPASS-2026-10-14.md)). Decision record: **ADR-0067**.
Applied by a one-off apply script in the development tree.

## What changed

| subject · field | edit |
|---|---|
| gen-007 `deal_seeking` | WEAK `notices_value` from c7 (was c8) |
| gen-002 `cuisines_avoided` | `[spanish]` WEAK from c0 (was c8) |
| gen-006 `deal_seeking` | UNEVIDENCED at every checkpoint (was WEAK `looks_for_deals` c0–c4); joins `must_unevidenced` |
| gen-017 `deal_seeking` | UNEVIDENCED at every checkpoint (was WEAK `looks_for_deals` c1–c7); joins `must_unevidenced` |

Reasons and quotes are in [`../failure-modes/FULLPASS-2026-10-14.md`](../failure-modes/FULLPASS-2026-10-14.md) ("Gold fixes applied").
The two cells deliberately left alone are gen-035/gen-047 `visit_time_distribution`: every distribution
is scored against the final card's histogram, and re-keying just these two to the visits shown would
make them the only empirically keyed distributions.

## Scope of the diff

Against gtfix: **4 subjects**, confined to the named fields: 21 `provisional` cells, 9 `card_at`
values, 2 `must_unevidenced` lists. Final `card` unchanged on every subject. The same edit is applied
to the model-free map. Bands: S 4,464 · W 5,244 · U 4,980 (gtfix: 4,464 · 5,247 · 4,977).

## Credit layer and reading

Unchanged. The final card is untouched, so no reader prompt moves (checked by the apply script:
0 moved), and the credit arms, dismissals and adjudications in this folder are byte-identical copies of
the gtfix ones (kept under their `-gtfix` names). Reading: `runs/verify-n50-both`, unchanged.

## Invariants (verified 2026-10-14)

- `EvidenceMap.validate` **50/50**; `verify_gold` **0**; both `card_at` gates pass on all 50.
- `EvidenceOracle` scores **A = 1.000** at every checkpoint of every subject.
- build_ui union derivation (`runs/verify_coherence_fullpassfix.py`) reproduces the key on **all
  14,688 cells, 0 mismatches**; **blocking 0**.
- Free arms: oracle 1.000, abstain 0.000 skill.
- Test suite **956 passed, 2 skipped**.

## Re-score

`runs/result-v2only-all-n50-fullpassfix-2026-10-14.json` / `runs/_v2only-all-n50-fullpassfix.log`:
the nine paper runs, free cache replay (`--replay-cached`, 0 model calls), same `--sut` order as the
gtfix replay. The gtfix replay was first reproduced byte-for-byte from cache before this one was run.

    PYTHONPATH=src python scripts/score.py --corpus runs/corpus-n50-fullpassfix-2026-10-14.json \
      --replay-cached --sut prompted-v2:google/gemini-3.7-flash --sut notebook-v2:google/gemini-3.7-flash \
      --sut prompted-v2:openai/gpt-5.6-luna --sut notebook-v2:openai/gpt-5.6-luna \
      --sut prompted-v2:deepseek/deepseek-v4-pro-0813 --sut notebook-v2:deepseek/deepseek-v4-pro-0813 \
      --sut prompted-v2:z-ai/glm-5.3-flash --sut notebook-v2:z-ai/glm-5.3-flash \
      --sut memory-v2:openai/gpt-5.6-luna --json runs/result-v2only-all-n50-fullpassfix-2026-10-14.json

**2026-10-15 — one missing call filled.** The GLM-5.3 Flash `prompted-v2` run had no reply at gen-007 c29
(the original replay ran without `OPENROUTER_API_KEY`, so the harness carried the c28 card forward). The
call was made on 2026-10-15 (`work/sut-emissions/prompted-v2/z-ai__glm-5.3-flash/gen-007-c29.json`; same
route and provider pin, ~6 weeks after the rest of the run), and the nine runs were re-scored by the same
free replay command into the same two files. The emitted card fields are identical to c28; only the
distribution data points (+2 kept) differ. Every printed number is unchanged (GLM `prompted` skill
0.5036113 → 0.5036126, mean AUFC 0.86837 → 0.86837; error counts, fabrications, hard-constraint failure
rate and bootstrap tables identical; mean final fidelity 0.884 → 0.885). All nine runs now have a reply
at every checkpoint. Previous files kept as `*.pre-c29fill.json` / `*.pre-c29fill.log`.

## Still open

- The HuggingFace release (`runs/hf-release-2026-08-30/`, uploaded) is still the gtfix base.
  Rebuilding it (`scripts/build_release.py`, which still points at gtfix) and re-uploading is a
  separate, owner-approved step.
- ~~The paper still reads the gtfix result file~~ — DONE 2026-10-14: dossierbench `paper/main.tex`
  numbers and both figures (`make_figures.py`) are on this base.
