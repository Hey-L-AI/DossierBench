"""Ask the second reader directly about the cells its free-recall list did not mention.

    python scripts/confirm_key3.py

One call per session, only for sessions that have an open question, and cached on the page +
question list. Writes the answers; `measure_credit.py --confirm-answers` folds them into
key 3 and `--pending-out` writes what is left for a person to decide.

Its prediction was written before the first run (in the unreleased development tree).
See `docs/PIPELINE.md` §4 for the full credit recipe.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persona_twin import data  # noqa: E402

ROOT = data.work()  # every artifact a pass writes lands here

from persona_twin import confirm, credit, verify                # noqa: E402
from persona_twin.judge import ClaudeCli                        # noqa: E402

CORPUS = data.local_corpus()
STORE = data.authored_store()
#: Defaults follow the file names in `docs/PIPELINE.md` §4.
REPORT = ROOT / "audit-opus.json"
#: The second reader's free-recall pass. Its answers still count — a field it volunteered needs
#: no second asking — so the targeted call is made only for what it did not mention.
SECOND = ROOT / "verify" / "claude-sonnet-5"
CONFIRMS = ROOT / "confirm"
OUT = ROOT / "confirm-key3.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", type=Path, default=CORPUS)
    ap.add_argument("--store", type=Path, default=STORE)
    ap.add_argument("--report", type=Path, default=REPORT)
    ap.add_argument("--second", type=Path, default=SECOND)
    ap.add_argument("--confirms", type=Path, default=CONFIRMS, help="verdict cache")
    ap.add_argument("--model", default=confirm.DEFAULT_MODEL)
    ap.add_argument("--system", default="neutral", choices=sorted(confirm.SYSTEMS),
                    help="which framing to ask in. 'leading-v1' reproduces the first, "
                         "prediction-contaminated prompt; the pair is the sensitivity check")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be asked and make no calls")
    args = ap.parse_args(argv)

    payload = json.loads(args.report.read_text())
    findings = [
        verify.Finding(
            kind=f["kind"], severity=f["severity"], task_id=f["task_id"],
            detail=f["detail"], field_id=f.get("field_id", ""), quote=f.get("quote", ""),
            turn_index=f.get("turn_index"), speaker=f.get("speaker", ""),
            messages=tuple(f.get("messages") or ()),
        )
        for f in payload["findings"]
    ]
    items = {str(i["subject_id"]): i
             for i in json.loads(args.corpus.read_text())["items"]}
    records = verify.load_store(args.store)

    candidates, refused = credit.screen(findings, records=records, items=items)
    free = {}
    for path in sorted(args.second.glob("gen-*.json")):
        if path.name.startswith("._"):
            continue
        verdict = json.loads(path.read_text())
        free[str(verdict["task_id"])] = verdict

    # Only what the free-recall pass did not already confirm. Asking about a field the second
    # reader volunteered would spend a call to be told what is on disk.
    open_by_task: dict[str, list[str]] = defaultdict(list)
    for candidate in candidates:
        second = free.get(candidate.task_id)
        if second and candidate.field_id in credit.read_fields(second):
            continue
        open_by_task[candidate.task_id].append(candidate.field_id)

    print(f"candidates through keys 1+2: {len(candidates)}")
    print(f"refused before key 3:        {dict(refused)}")
    print(f"open questions:              "
          f"{sum(len(v) for v in open_by_task.values())} "
          f"across {len(open_by_task)} sessions")
    for task_id, field_ids in sorted(open_by_task.items()):
        print(f"   {task_id}: {', '.join(field_ids)}")
    if args.dry_run:
        return 0

    store = verify.VerdictStore(args.confirms)
    invoke = ClaudeCli()
    answers, failures = [], []
    for task_id, field_ids in sorted(open_by_task.items()):
        record = records.get(task_id)
        if record is None:                                # pragma: no cover - defensive
            continue
        try:
            got = confirm.confirm_session(
                invoke, args.model, record, field_ids, store=store,
                system=confirm.SYSTEMS[args.system],
            )
        except Exception as exc:                          # noqa: BLE001 - reported, not raised
            # A reader that cannot be reached is not a reader that said no. The cell stays a
            # question rather than becoming a silent refusal.
            failures.append({"task_id": task_id, "error": f"{type(exc).__name__}: {exc}"})
            print(f"   ! {task_id}: {type(exc).__name__}: {exc}")
            continue
        answers.extend(got)
        card = (items.get(str(record["subject_id"])) or {}).get("card") or {}
        for answer in got:
            way = confirm.direction(answer, card.get(answer.field_id))
            mark = "YES" if answer.confirms else " no"
            if answer.readable and not answer.quote_found:
                mark = "!!!"                              # said yes, quoted text not on the page
            if answer.confirms and way == confirm.OPPOSES:
                mark = "OPP"                              # readable, and reads against the card
            print(f"   {mark}  {task_id}  {answer.field_id}  "
                  f"card={card.get(answer.field_id)!r} read={list(answer.levels)}")
            print(f"        {answer.reads_as[:100]}")

    args.out.write_text(json.dumps({
        "model": args.model, "system": args.system, "report": str(args.report),
        "store": str(args.store),
        "answers": [a.__dict__ for a in answers], "failures": failures,
    }, indent=2, sort_keys=True))
    cards = {
        task_id: (items.get(str(record["subject_id"])) or {}).get("card") or {}
        for task_id, record in records.items()
    }
    agreeing = confirm.as_confirmations(answers, cards=cards)
    confirmed = sum(len(v["unscheduled"]) for v in agreeing.values())
    readable = sum(1 for a in answers if a.confirms)
    opposed = readable - confirmed
    print(f"\nreadable on the targeted ask:   {readable} of {len(answers)}")
    print(f"  ... and agreeing with the card: {confirmed}")
    print(f"  ... reading AGAINST the card:   {opposed}   (never credited)")
    print(f"still split, for adjudication:  {len(answers) - confirmed}")
    if failures:
        print(f"unreached sessions:            {len(failures)}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":                                # pragma: no cover - CLI
    raise SystemExit(main())
