"""Verify an authored corpus against its answer key. Free by default; `--audit` spends calls.

    python scripts/audit.py
    python scripts/audit.py --audit --sample 24

The first form is the deterministic arm — structure, drift against the corpus, today's rules
replayed over stored text, format, the contradiction probe, and the countable distribution
instances. No model calls, so run it after every authoring pass.

The second adds the reader: one call per sampled session, answering the three questions no
rule can — whose a statement is, whether a scheduled beat actually landed, and whether a
field nobody scheduled is readable anyway. Sampled rather than exhaustive, and stratified
toward the sessions that can falsify something (traps, within-session repeats, distractors,
anything a rule already flagged). Verdicts are cached and fingerprinted on what the reader was
shown, so a re-run after a re-authoring reads the new text and everything else is free.

Defaults are a corpus you drew and a store you authored (`persona_twin.data`); point `--corpus`
and `--store` anywhere else. Nothing here is corpus-specific. This stage needs an **authored**
store, not the published transcripts — see `docs/PIPELINE.md`.

What each finding kind means, and which ones block, is defined in `persona_twin/verify.py`
(decision record ADR-0036); `docs/PIPELINE.md` §3 shows the audit in the full recipe.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persona_twin import data  # noqa: E402

ROOT = data.work()  # every artifact a pass writes lands here

from persona_twin import verify  # noqa: E402

DEFAULT_CORPUS = data.local_corpus()
DEFAULT_STORE = data.authored_store()
#: The verdict cache. One store, one reading: a cache paired with a different store than the
#: one being read is how a page ends up half one run and half another, so give each reading
#: pass its own directory and never share one across stores.
DEFAULT_VERDICTS = ROOT / "verify"


def main() -> int:
    argv = sys.argv[1:]
    if not any(a.startswith("--corpus") for a in argv):
        argv += ["--corpus", str(DEFAULT_CORPUS)]
    if not any(a.startswith("--store") for a in argv):
        argv += ["--store", str(DEFAULT_STORE)]
    if not any(a.startswith("--verdicts") for a in argv):
        argv += ["--verdicts", str(DEFAULT_VERDICTS)]
    return verify.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
