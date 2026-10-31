# Paper — DossierBench (PALM @ NeurIPS 2026)

**DossierBench: Scoring a Recommender's Model of You on Calibration, Not Recall**
Muhammad Bilal, João V. Graça. Accepted at the PALM workshop (Personalized, Aligned, Long-Term
Memory for AI Systems) at NeurIPS 2026. Non-archival.

This directory holds the camera-ready version. `main.pdf` is the built paper (9-page main text,
then references, appendices and the NeurIPS checklist).

## Files
- `main.tex` — the manuscript. Uses `\usepackage[dblblindworkshop, final]{neurips_2026}`
  (camera-ready: authors shown, no line numbers). Drop `final` to get the anonymous review layout.
- `checklist.tex` — the NeurIPS paper checklist, included at the end of `main.tex`.
- `references.bib` — bibliography. `main.bbl` is the compiled copy, so Overleaf builds without BibTeX.
- `neurips_2026.sty` — official NeurIPS 2026 style file (do not edit).
- `fig_evidence_map.pdf`, `fig_results.pdf` — the two figures (vector PDF).
- `make_figures.py` — regenerates both figures. It takes the answer key from the published dataset
  (`persona_twin.data`) and the results figure from a scoring-run JSON, given as the first argument
  or `DOSSIERBENCH_RESULTS` (write one with `python scripts/score.py ... --json <file>`). Needs the
  `figures` extra (`pip install -e '.[data,figures]'`). The figure PDFs are committed, so you do not
  need it to build the paper.
- `poster/` — the conference poster (`poster.html` source, `poster.pdf`, wordmark).

## Build
```
pdflatex main && bibtex main && pdflatex main && pdflatex main
```
On Overleaf: upload this folder, set the compiler to pdfLaTeX, and use `main.tex` as the root.

## Where the numbers come from
Every score in the paper is on the released answer key
(https://huggingface.co/datasets/tangents/dossierbench). How that key was built and which base it
comes from is recorded in [`docs/provenance/CANONICAL.md`](../docs/provenance/CANONICAL.md); the
failure-mode catalogue behind §7.1 and Appendix F is described in
[`docs/FAILURE-MODES.md`](../docs/FAILURE-MODES.md).
