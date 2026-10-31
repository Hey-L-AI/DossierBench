"""Measure the reader-derived evidence credit. No model calls; reads what is already on disk.

    python scripts/measure_credit.py

Its prediction was written before the first run (in the unreleased development tree).
See `docs/PIPELINE.md` §4 for the full credit recipe.
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

from persona_twin import adjudicate, confirm, credit, verify    # noqa: E402

#: Defaults follow the file names in `docs/PIPELINE.md` §4 (the opus arm). Every one is
#: overridable, so the other arm runs without editing this file.
CORPUS = data.local_corpus()
#: The store as the reader saw it. Credit is a claim about what the *audited* page carried,
#: so it must be measured against the text that was audited, never against a store the
#: editor has since rewritten.
STORE = data.authored_store()
REPORT = ROOT / "audit-opus.json"
VERDICTS = ROOT / "verify" / "claude-opus-5"
SECOND = ROOT / "verify" / "claude-sonnet-5"      # key 3
RESULT = ROOT / "result-credit-opus.json"


def _bands(items) -> Counter:
    out: Counter = Counter()
    for item in items.values():
        for grades in (item.get("provisional") or {}).values():
            out.update(grades.values())
    return out


def _kinds(findings) -> Counter:
    return Counter(f.kind for f in findings)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", type=Path, default=CORPUS)
    ap.add_argument("--store", type=Path, default=STORE,
                    help="the store as the reader saw it, not a post-editor one")
    ap.add_argument("--report", type=Path, default=REPORT)
    ap.add_argument("--verdicts", type=Path, default=VERDICTS)
    ap.add_argument("--second", type=Path, default=SECOND, help="key 3's reader")
    ap.add_argument("--reader-model", default=verify.DEFAULT_MODEL)
    ap.add_argument("--out", type=Path, default=RESULT)
    ap.add_argument("--absolved-out", type=Path, default=None,
                    help="the report with every credited leak demoted to a note — the "
                         "editor's work list (`scripts/edit.py --report`). The raw report "
                         "stays the gate's baseline (`--baseline-report`)")
    ap.add_argument("--confirm-answers", type=Path, nargs="*", default=(),
                    help="targeted key-3 answer files from scripts/confirm_key3.py, one per "
                         "repetition. A cell credits on a majority that AGREES WITH THE "
                         "CARD; a single file is accepted and is a single sample")
    ap.add_argument("--adjudications", type=Path, default=None,
                    help="the owner's resolutions of cells the readers split on. Settles "
                         "key 3 only — never key 1, and never above WEAK")
    ap.add_argument("--pending-out", type=Path, default=None,
                    help="what neither reader agreement nor a fresh decision resolved: the "
                         "queue the UI renders")
    ap.add_argument("--corpus-out", type=Path, default=None,
                    help="the credited map: the corpus with every credited cell at WEAK "
                         "and its provenance recorded. A new artifact; the model-free "
                         "map in --corpus is left exactly as schedule.py produced it")
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
    verdicts = {}
    for path in sorted(args.verdicts.glob("gen-*.json")):
        if path.name.startswith("._"):
            continue
        v = json.loads(path.read_text())
        verdicts[str(v.get("task_id"))] = v

    confirmations = {}
    for path in sorted(args.second.glob("gen-*.json")):
        if path.name.startswith("._"):
            continue
        v = json.loads(path.read_text())
        confirmations[str(v.get("task_id"))] = v

    # The targeted ask (`confirm.py`) and the free-recall read are the same key, gathered two
    # ways, so they are unioned rather than ranked. A field the second reader volunteered needs
    # no second asking; a field it never mentioned needs one before its silence means anything.
    cards = {
        task_id: (items.get(str(record["subject_id"])) or {}).get("card") or {}
        for task_id, record in records.items()
    }
    runs = [confirm.load_answers(path) for path in (args.confirm_answers or ())]
    tallies = confirm.tally(runs, cards=cards) if runs else {}
    if tallies:
        confirmations = confirm.merge(confirmations, confirm.consensus(tallies))
        # ...then strip what the targeted ask read AGAINST the card. `merge` unions, which is
        # right for adding a reading and wrong for removing one: free recall only shows the
        # field was mentioned, while the targeted ask checked its level against the card.
        confirmations = confirm.veto(confirmations, tallies)

    decisions, malformed = ({}, [])
    if args.adjudications:
        decisions, malformed = adjudicate.load(args.adjudications)
    approved, stale = adjudicate.fresh(decisions, records=records, verdicts=verdicts)

    before = _bands(items)
    credits, refused = credit.eligible(
        findings, records=records, items=items, verdicts=verdicts,
        reader_model=args.reader_model, confirmations=confirmations,
        adjudications=approved,
    )
    after_items, moved = credit.apply(items, credits)
    after = _bands(after_items)
    total = sum(before.values())

    leaks = sum(1 for f in findings if f.kind == "leak")
    blocking = sum(1 for f in findings if f.severity == verify.BLOCKING)
    # A rejection settles a cell as surely as a credit does — it says the reader's reading did
    # not survive a person looking at the page, so the cell stays UNEVIDENCED and there is
    # nothing to repair. Leaving it blocking would send the editor at a page the owner has
    # already cleared, and would keep a question they have answered in the outstanding count.
    dismissed = {key for key, decision in approved.items()
                 if str(decision.get("verdict")) == credit.REJECT}
    dismissable = sum(1 for f in findings
                      if f.kind in credit.ABSOLVABLE_KINDS
                      and f.severity == verify.BLOCKING
                      and (f.task_id, f.field_id) in dismissed)

    print("=== RESULT — reader-derived evidence credit, three keys ===\n")
    print(f"leak findings offered:            {leaks}")
    print(f"CELLS CREDITED UNEVIDENCED->WEAK: {len(credits)}")
    print(f"leaks that become notes:          {len(credits) + dismissable}"
          + (f"  ({len(credits)} credited, {dismissable} refused by a person)"
             if dismissable else ""))
    print(f"blocking findings: {blocking} -> {blocking - len(credits) - dismissable}\n")
    print("refused, by reason:")
    for reason, n in refused.most_common():
        print(f"   {reason:24} {n}")
    print("\nbands:")
    for band in ("STRONG", "WEAK", "UNEVIDENCED"):
        b, a = before[band], after[band]
        print(f"   {band:12} {b:5} ({100*b/total:.1f}%)  ->  {a:5} ({100*a/total:.1f}%)"
              f"   {a-b:+d}")
    print("\ncredited by field:")
    for field_id, n in moved.most_common():
        print(f"   {field_id:26} {n}")

    by_hand = [c for c in credits if c.adjudicated_by]
    if tallies or approved or stale or malformed:
        print("\nkey 3, in detail:")
        print(f"   repetitions of the targeted ask:  {len(runs)}")
        if tallies:
            verdicts_seen = Counter(t.verdict for t in tallies.values())
            for name in ("credit", "opposes", "split", "refused"):
                if verdicts_seen.get(name):
                    print(f"   asked cells reading {name:10}   {verdicts_seen[name]}")
        print(f"   credited by owner decision:       {len(by_hand)}")
        if stale:
            print(f"   DECISIONS GONE STALE:             {len(stale)}  "
                  f"(page edited since; back in the queue)")
            for decision in stale:
                print(f"      {decision.task_id} {decision.field_id} "
                      f"({decision.verdict} by {decision.decided_by})")
        if malformed:
            print(f"   MALFORMED DECISIONS:              {len(malformed)}  (ignored)")

    print("\n--- falsifiers ---")
    print(f"   UNEVIDENCED >= 30% of cells:   "
          f"{'PASS' if 100*after['UNEVIDENCED']/total >= 30 else 'FAIL'} "
          f"({100*after['UNEVIDENCED']/total:.1f}%)")
    print(f"   STRONG unchanged:              "
          f"{'PASS' if after['STRONG'] == before['STRONG'] else 'FAIL'}")
    unbacked = [c for c in credits if not c.confirmed_by and not c.adjudicated_by]
    print(f"   every credit has a 2nd reader or a recorded decision: "
          f"{'PASS' if not unbacked else 'FAIL'}")
    opposed = [
        c for c in credits
        if (tallies.get((c.task_id, c.field_id)) or confirm.Tally("", "")).oppose
        > (tallies.get((c.task_id, c.field_id)) or confirm.Tally("", "")).agree
    ]
    print(f"   no credit read against its card:  {'PASS' if not opposed else 'FAIL'}")
    above = [c for c in credits if credit.CREDIT_GRADE != "WEAK"]
    print(f"   adjudication cannot exceed WEAK:  {'PASS' if not above else 'FAIL'}")
    empty = [c for c in credits
             if not credit._has_value(items[c.subject_id]['card'], c.field_id)]
    print(f"   no credit on an empty card value: {'PASS' if not empty else 'FAIL'}")

    out = {
        "credits": [c.to_json() for c in credits],
        "refused": dict(refused),
        "bands_before": dict(before), "bands_after": dict(after),
        "adjudicated": [c.to_json() for c in by_hand],
        "stale_decisions": [d.to_json() for d in stale],
        "tallies": {
            f"{k[0]}::{k[1]}": {
                "verdict": t.verdict, "agree": t.agree, "oppose": t.oppose,
                "silent": t.silent, "levels": list(t.levels),
            }
            for k, t in sorted(tallies.items())
        },
    }
    args.out.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {args.out}")

    if args.absolved_out:
        absolved, demoted = credit.absolve(findings, credits, dismissed=dismissed)
        args.absolved_out.write_text(json.dumps({
            "findings": [f.to_json() for f in absolved],
            "counts": dict(_kinds(absolved)),
            "blocking": sum(1 for f in absolved if f.severity == verify.BLOCKING),
        }, indent=2))
        print(f"wrote {args.absolved_out}  ({demoted} leaks demoted to notes"
              + (f", {dismissable} of them by an owner rejection" if dismissable else "")
              + ")")

    if args.pending_out:
        candidates, _ = credit.screen(findings, records=records, items=items)
        answers = [a for run in runs for a in run]
        questions = adjudicate.pending(
            candidates, records=records, items=items, confirmations=confirmations,
            answers=answers, verdicts=verdicts, decisions=decisions,
            reader_model=args.reader_model, second_model=confirm.DEFAULT_MODEL,
        )
        args.pending_out.write_text(json.dumps({
            "questions": [q.to_json() for q in questions],
            "tallies": {
                f"{k[0]}::{k[1]}": {
                    "verdict": t.verdict, "agree": t.agree, "oppose": t.oppose,
                    "silent": t.silent, "levels": list(t.levels),
                    "reads_as": t.reads_as,
                }
                for k, t in sorted(tallies.items())
            },
        }, indent=2))
        print(f"wrote {args.pending_out}  ({len(questions)} awaiting a decision)")

    if args.corpus_out:
        payload_corpus = json.loads(args.corpus.read_text())
        payload_corpus["items"] = [
            after_items[str(i["subject_id"])] for i in payload_corpus["items"]
        ]
        args.corpus_out.write_text(json.dumps(payload_corpus, indent=2))
        print(f"wrote {args.corpus_out}  ({sum(moved.values())} cells at WEAK by credit)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
