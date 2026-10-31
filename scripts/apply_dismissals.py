"""Demote every blocking finding a person refused, and report the ones whose page has moved.

Step 2 of the base pipeline. Step 1 (`measure_credit.py`) settles the *leaks* — a
credit says the map was wrong, an owner REJECT says the reader was — and this settles every
other blocking kind, which credit cannot touch (`credit.ABSOLVABLE_KINDS` is `{"leak"}`).
The previous two bases ran this step by hand and their MANIFESTs could only describe it; a
step that cannot be re-run is a step nobody can check.

Staleness is `adjudicate.dismiss`'s own, pinned on the instrument of the reader's verdict:
edit a page and the dismissals granted against it stop applying and are printed here.

    python scripts/apply_dismissals.py \
        --report work/audit-absolved.json \
        --dismissals work/dismissals.json \
        --verdicts work/verify/claude-opus-5 \
        --out work/audit-absolved-adjudicated.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persona_twin import data  # noqa: E402

ROOT = data.work()  # every artifact a pass writes lands here

from persona_twin import adjudicate, verify  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--dismissals", type=Path, required=True)
    ap.add_argument("--store", type=Path, default=data.authored_store())
    ap.add_argument("--verdicts", type=Path, required=True,
                    help="the first reader's verdict directory — the instrument a dismissal "
                         "is pinned to is the one the reader recorded")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    payload = json.loads(args.report.read_text())
    fields_of = verify.Finding.__dataclass_fields__
    findings = [
        verify.Finding(**{
            k: (tuple(v) if isinstance(v, list) else v)
            for k, v in f.items() if k in fields_of
        })
        for f in payload["findings"]
    ]
    records = verify.load_store(args.store)
    # `gen-*.json`, like `verify.load_store` and `measure_credit` — some filesystems (e.g.
    # exFAT) write AppleDouble `._gen-*.json` siblings that are not UTF-8.
    verdicts = {}
    for path in sorted(args.verdicts.glob("gen-*.json")):
        record = json.loads(path.read_text())
        verdicts[str(record["task_id"])] = record
    entries, malformed = adjudicate.load_dismissals(args.dismissals)
    for bad in malformed:
        print(f"MALFORMED, ignored: {bad}")

    out, demoted, stale = adjudicate.dismiss(
        findings, entries, records=records, verdicts=verdicts,
    )
    blocking = [f for f in out if f.severity == verify.BLOCKING]
    args.out.write_text(json.dumps({
        "findings": [f.to_json() for f in out],
        "counts": dict(Counter(f.kind for f in out)),
        "blocking": len(blocking),
    }, indent=2))

    print(f"dismissals on file: {len(entries)}   demoted: {demoted}   stale: {len(stale)}")
    for entry in stale:
        print(f"  STALE (page moved since it was decided): {entry.task_id} {entry.field_id} "
              f"{entry.kind}")
    print(f"BLOCKING: {len(blocking)} "
          f"{dict(Counter(f.kind for f in blocking))}")
    for f in sorted(blocking, key=lambda f: (f.task_id, f.field_id)):
        print(f"  {f.task_id:<14} {f.field_id:<26} {f.kind}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
