#!/usr/bin/env python3
"""Generate the paper's figures from the repo's real result/corpus files.

Outputs (vector PDF, into this directory):
  fig_evidence_map.pdf  -- the real derived map for one subject (gen-002),
                           24 fields x 16 checkpoints, banded strong/weak/unevidenced.
  fig_results.pdf       -- skill (AUFC normalized: abstain=0, oracle=1) by
                           system, grouped by model, from the SUT result JSONs.

Inputs:
  answer key   -- the published credited key, via `persona_twin.data.corpus("key")`
                  (downloaded from HuggingFace, or read from DOSSIERBENCH_DATA).
  results JSON -- the scoring-run results for the eight paper cells, written by
                  `python scripts/score.py ... --json <path>`. Pass it as the first
                  argument or set DOSSIERBENCH_RESULTS.

    python3 paper/make_figures.py work/result-paper.json
"""
import json, os, sys
import numpy as np
import matplotlib
matplotlib.use("pdf")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.colors import ListedColormap

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(REPO, "src"))

from persona_twin import data  # noqa: E402

#: Environment variable naming the results JSON, when it is not given on the command line.
RESULTS_ENV = "DOSSIERBENCH_RESULTS"


def results_path():
    raw = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(RESULTS_ENV)
    if not raw:
        raise SystemExit(
            "no results JSON given. Score the paper cells first and pass the file:\n"
            "    python scripts/score.py --sut prompted-v2:<model> --sut notebook-v2:<model> "
            "... --json work/result-paper.json\n"
            f"    python3 paper/make_figures.py work/result-paper.json   (or set {RESULTS_ENV})")
    path = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isfile(path):
        raise SystemExit(f"no results JSON at {path}\n"
                         "It is written by `python scripts/score.py ... --json <path>`.")
    return path

plt.rcParams.update({
    "font.family": "serif",
    "font.size": 8,
    "axes.linewidth": 0.6,
    "pdf.fonttype": 42,  # embed as TrueType (editable, Overleaf-safe)
})

# ---------------------------------------------------------------------------
# Figure 1 -- the real derived evidence map for gen-002
# ---------------------------------------------------------------------------
def evidence_map():
    # The published credited answer key (byte-identical to the fullpassfix key the paper used).
    d = json.load(open(data.corpus("key")))
    it = {x["subject_id"]: x for x in d["items"]}["gen-002"]
    prov = it["provisional"]
    ckpts = sorted(prov.keys(), key=int)
    fields = list(prov[ckpts[0]].keys())  # 24 fields, schema order
    band = {"UNEVIDENCED": 0, "WEAK": 1, "STRONG": 2}
    M = np.array([[band[prov[c][f]] for c in ckpts] for f in fields])

    # print-safe, colorblind-friendly 3-step ramp (light -> dark)
    cmap = ListedColormap(["#eef1f5", "#f3b76b", "#2f5c8a"])

    fig, ax = plt.subplots(figsize=(5.4, 4.6))
    ax.imshow(M, aspect="auto", cmap=cmap, vmin=0, vmax=2,
              interpolation="nearest")

    ax.set_xticks(range(len(ckpts)))
    ax.set_xticklabels([str(int(c)) for c in ckpts], fontsize=6.5)
    ax.set_yticks(range(len(fields)))
    ax.set_yticklabels([f.replace("_", " ") for f in fields], fontsize=6)
    ax.set_xlabel("checkpoint (session index)")
    ax.tick_params(length=0)

    # gridlines between cells
    ax.set_xticks(np.arange(-.5, len(ckpts), 1), minor=True)
    ax.set_yticks(np.arange(-.5, len(fields), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=0.8)
    for s in ax.spines.values():
        s.set_visible(False)

    legend = [
        Patch(facecolor="#2f5c8a", label="strong: commit is scored; abstain is a miss"),
        Patch(facecolor="#f3b76b", label="weak: commit scored; abstain gets partial credit"),
        Patch(facecolor="#eef1f5", edgecolor="#bbb",
              label="unevidenced: commit is a fabrication; abstain is free"),
    ]
    ax.legend(handles=legend, loc="lower center", bbox_to_anchor=(0.5, 1.02),
              ncol=1, frameon=False, fontsize=6.6, handlelength=1.1,
              handleheight=1.1, labelspacing=0.35)

    fig.tight_layout()
    out = os.path.join(HERE, "fig_evidence_map.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print("wrote", out, "| shape", M.shape,
          "| bands", {k: int((M == v).sum()) for k, v in band.items()})


# ---------------------------------------------------------------------------
# Figure 2 -- reference skill measurements, grouped bars, from real result JSONs
# ---------------------------------------------------------------------------
def results_figure():
    # One group per backbone, over the full 50 subjects. n=50 is the basis of
    # comparison. The figure shows the single-pass systems only (prompted,
    # notebook): they are the arm scored on all four backbones. The one scored
    # stateful cell (memory on GPT-5.6 Luna) is reported in the text instead, so
    # the figure carries no pending placeholders.
    # All eight cells come from one free cache replay of the reported prompt on the
    # canonical fullpassfix key (docs/provenance/CANONICAL.md, 2026-10-14), after the 2026-10-15 tagfix
    # re-run of five subjects (docs/provenance/n50-tagfix-2026-10-15.md); the `-v2`
    # suffix is the internal cache path for that prompt, not a paper-facing label.
    RES = results_path()
    cells = {
        ("Gemini 3.7 Flash", "prompted"): (RES, "prompted-v2:google/gemini-3.7-flash"),
        ("Gemini 3.7 Flash", "notebook"): (RES, "notebook-v2:google/gemini-3.7-flash"),
        ("GPT-5.6 Luna", "prompted"): (RES, "prompted-v2:openai/gpt-5.6-luna"),
        ("GPT-5.6 Luna", "notebook"): (RES, "notebook-v2:openai/gpt-5.6-luna"),
        ("DeepSeek V4 Pro", "prompted"): (RES, "prompted-v2:deepseek/deepseek-v4-pro-0813"),
        ("DeepSeek V4 Pro", "notebook"): (RES, "notebook-v2:deepseek/deepseek-v4-pro-0813"),
        ("GLM-5.3 Flash", "prompted"): (RES, "prompted-v2:z-ai/glm-5.3-flash"),
        ("GLM-5.3 Flash", "notebook"): (RES, "notebook-v2:z-ai/glm-5.3-flash"),
    }
    systems = ["prompted", "notebook"]
    skill = {}
    for (m, s), (f, key) in cells.items():
        d = json.load(open(f))
        d = d.get("results", d)
        skill[(m, s)] = d[key]["aggregate"][0]["mean_aufc_skill"]

    models = ["Gemini 3.7 Flash", "GPT-5.6 Luna", "DeepSeek V4 Pro", "GLM-5.3 Flash"]
    x = np.arange(len(models))
    w = 0.3
    # the single-pass pair: two blues
    colors = {"prompted": "#2f5c8a", "notebook": "#6b9bd1"}

    fig, ax = plt.subplots(figsize=(5.4, 1.9))
    for i, s in enumerate(systems):
        vals = [skill[(m, s)] for m in models]
        bars = ax.bar(x + (i - 0.5) * w, vals, w, label=s,
                      color=colors[s], edgecolor="white", linewidth=0.4)
        for b, val in zip(bars, vals):
            ax.annotate(f"{val:+.2f}",
                        (b.get_x() + b.get_width() / 2,
                         val + (0.012 if val >= 0 else -0.012)),
                        ha="center", va="bottom" if val >= 0 else "top",
                        fontsize=5.3)

    ax.axhline(0, color="black", linewidth=1.0)
    ax.set_xticks(x)
    ax.set_xticklabels(models)
    ax.set_ylabel("skill", fontsize=7)
    ax.set_ylim(0, 0.95)
    ax.set_yticks([0, 0.25, 0.5, 0.75])
    ax.tick_params(axis="both", labelsize=6.5)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.legend(ncol=2, frameon=False, fontsize=6.6, loc="upper center",
              bbox_to_anchor=(0.5, 1.14), columnspacing=1.2, handlelength=1.1)
    fig.tight_layout()
    out = os.path.join(HERE, "fig_results.pdf")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print("wrote", out, "| n=50 single-pass skill",
          {k: round(v, 3) for k, v in skill.items()})


if __name__ == "__main__":
    results_path()  # fail before drawing anything if the results JSON is missing
    evidence_map()
    results_figure()
