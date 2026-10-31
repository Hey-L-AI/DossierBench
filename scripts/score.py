"""Serve the published conversations to a submission and score what it emits. Free by default.

    python scripts/score.py
    python scripts/score.py --sut prompted --sut abstain

The first form runs the three submissions that cost nothing — `abstain`, `population` and the
`oracle` ceiling — which is the check worth having after any change to the corpus or the map:
the oracle must come out at exactly 1.000 on every subject, `abstain` at exactly 0.000 skill,
and `population` below it. All three are properties of the *scorer*, so a deviation is a defect
in the answer key or in this harness and never in a submission. **Run it first on a fresh
clone**: it is the whole installation check, and it makes no model calls.

The second spends one model call per checkpoint per submission — 612 for the published store —
and is the thing the benchmark exists to do. Replies are cached under `--emissions`,
fingerprinted on what the model was shown, so a re-run pays only for the checkpoints whose
prompt moved. `--dry-run` prints the bill without spending it.

Defaults come from `persona_twin.data`: the **credited answer key** and the published
transcripts, downloaded from HuggingFace on first use or read from `DOSSIERBENCH_DATA`. To score
against the model-free map instead — the schedule-only gold, no reader model in it:

    python scripts/score.py --corpus "$(python -c 'from persona_twin import data
    print(data.corpus("map"))')"

The two are not comparable cell-for-cell on the 28 of 50 subjects that carry a credited cell, so
a read-out taken over either must say which it used.

**Nothing is scored until it is predicted.** The rule the corpus was built under: a run is
predicted before its first call in a `prediction-*.txt` and scored against it afterwards in a
`result-*.txt`. The free arm above is exempt only because it makes no draw — it is a gate on the
scorer, and its three numbers are fixed by construction rather than measured.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persona_twin import data, sut  # noqa: E402


def main() -> int:
    argv = sys.argv[1:]
    if not any(a.startswith("--corpus") for a in argv):
        argv += ["--corpus", str(data.corpus("key"))]
    if not any(a.startswith("--store") for a in argv):
        argv += ["--store", str(data.transcripts())]
    if not any(a.startswith("--emissions") for a in argv):
        argv += ["--emissions", str(data.work() / "sut-emissions")]
    return sut.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
