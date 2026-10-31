"""Union two reader arms into one answer key and one reading.

`measure_credit.py` runs ONE arm: a report from reader A, key 3 from reader B. That
asymmetry is invisible while only one arm is ever run, and it is the whole content of the
question "is the base opus-primary, or the union of what any competent reader sees" — because a
leak reader A never files is a cell reader A's pass can never even offer.

This runs the other direction as a first-class artifact. Each arm keeps its own three keys and
its own provenance — the credited cell records which model READ it and which confirmed it, and
merging must not blur that — so the merge is over `Credit` records, not over maps:

    union credits  = arm A's credits + arm B's credits, deduped by (subject, checkpoint, field)
    union map      = `credit.apply` over the raw map with that list
    union reading  = arm A's findings + the findings ONLY arm B filed, absolved against the
                     union credits and the dismissals

A cell both arms credit is kept once, from whichever arm is named first — the two records agree
on everything but which model was reader 1, and the incumbent arm is the one the base already
published.

    python scripts/merge_credits.py \
        --corpus work/corpus.json \
        --arm work/result-credit-opus.json:work/audit-opus.json \
        --arm work/result-credit-sonnet.json:work/audit-sonnet.json \
        --corpus-out work/corpus-credited.json \
        --report-out work/audit-absolved.json
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

from persona_twin import credit, verify  # noqa: E402


def _findings(path: Path) -> list[verify.Finding]:
    known = verify.Finding.__dataclass_fields__
    return [
        verify.Finding(**{
            k: (tuple(v) if isinstance(v, list) else v)
            for k, v in f.items() if k in known
        })
        for f in json.loads(path.read_text())["findings"]
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--arm", action="append", required=True,
                    help="RESULT.json:REPORT.json, repeatable. The first arm named is the "
                         "incumbent: it wins a tie on a cell both credit, and its report is "
                         "the spine of the union reading")
    ap.add_argument("--adjudications", type=Path, action="append", default=[],
                    help="decision files, repeatable. A REJECT settles a cell as surely as a "
                         "credit does — the reader's reading did not survive a person looking "
                         "at the page — so its leak stops blocking too. `measure_credit` "
                         "does this inside one arm; the union has to do it across both or an "
                         "already-answered question comes back as outstanding work")
    ap.add_argument("--corpus-out", type=Path, required=True)
    ap.add_argument("--report-out", type=Path, required=True)
    args = ap.parse_args(argv)

    arms = [(Path(a.split(":")[0]), Path(a.split(":")[1])) for a in args.arm]
    seen: set[tuple[str, int, str]] = set()
    credits: list[credit.Credit] = []
    per_arm: Counter = Counter()
    for result_path, _ in arms:
        for raw in json.loads(result_path.read_text())["credits"]:
            prov = raw.get("provenance") or {}
            entry = credit.Credit(
                subject_id=str(raw["subject_id"]), checkpoint=int(raw["checkpoint"]),
                field_id=str(raw["field_id"]), task_id=str(raw["task_id"]),
                messages=tuple(raw.get("messages") or ()), quote=str(raw.get("quote") or ""),
                reader_model=str(prov.get("model") or ""),
                instrument=str(prov.get("instrument") or ""),
                confirmed_by=tuple(prov.get("confirmed_by") or ()),
                adjudicated_by=str(prov.get("adjudicated_by") or ""),
            )
            if entry.cell in seen:
                continue
            seen.add(entry.cell)
            credits.append(entry)
            per_arm[result_path.name] += 1

    payload = json.loads(args.corpus.read_text())
    items = {str(i["subject_id"]): i for i in payload["items"]}
    after, moved = credit.apply(items, credits)
    payload["items"] = [after[str(i["subject_id"])] for i in payload["items"]]
    args.corpus_out.write_text(json.dumps(payload, indent=2))

    spine = _findings(arms[0][1])
    have = {(f.task_id, f.field_id, f.kind) for f in spine}
    extra = [f for _, r in arms[1:] for f in _findings(r)
             if (f.task_id, f.field_id, f.kind) not in have]
    rejected = {
        (str(d["task_id"]), str(d["field_id"]))
        for path in args.adjudications
        for d in json.loads(path.read_text())["decisions"]
        if str(d.get("verdict")) == credit.REJECT
    }
    absolved, demoted = credit.absolve(spine + extra, credits, dismissed=rejected)
    args.report_out.write_text(json.dumps({
        "findings": [f.to_json() for f in absolved],
        "counts": dict(Counter(f.kind for f in absolved)),
        "blocking": sum(1 for f in absolved if f.severity == verify.BLOCKING),
    }, indent=2))

    carried = sum(
        1 for i in payload["items"]
        for fields in (i.get("evidence_provenance") or {}).values()
        for p in fields.values() if "carried_from" in p
    )
    print(f"credits merged:  {len(credits)}  {dict(per_arm)}")
    print(f"cells at WEAK:   {sum(moved.values())} read directly + {carried} carried forward")
    print(f"union findings:  {len(spine)} from the incumbent arm + {len(extra)} only the "
          f"other arm filed")
    print(f"leaks demoted:   {demoted} ({len(credits)} credited, the rest owner-rejected)")
    print(f"BLOCKING:        {sum(1 for f in absolved if f.severity == verify.BLOCKING)}")
    print(f"wrote {args.corpus_out}\nwrote {args.report_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
