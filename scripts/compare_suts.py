"""Cell-level transition table between two submissions scored on one corpus.

Built for the prompt-version comparison (`prompted` v1 vs `prompted-v2`, the GT-POLICIES
`[TWIN-FACING]` conventions wired into the system prompt), but it takes any two `--sut` kinds.

    python scripts/compare_suts.py \
        --corpus work/corpus.json \
        --store work/authored/claude-opus-5 \
        --emissions work/sut-emissions --model openai/gpt-5.6-luna \
        --a prompted --b prompted-v2 --json work/result-promptv2-luna.json

Both arms are scored in one invocation, so `sut.format_report` prints the headline table and the
paired-bootstrap ΔAUFC for free. Below that, this script pairs every (subject, checkpoint, field)
cell across the two arms and prints the outcome transition matrix per field group:

  taste lists   cuisines_*/event_types_*      prediction: fabrication -> abstain·U
  ceiling       formality_ceiling             prediction: misread -> correct
  control       everything else               prediction: no movement

Movement in the control group is either sampling noise or the prompt leaking past the disclosed
conventions into trap content — either way it is the number that says the change did more than
it was meant to. The collateral line counts STRONG taste-list cells the v1 arm had right and the
v2 arm abstains on: the one way the wording can hurt.

Cell labels, from `scorer.FieldOutcome`:
  fabrication  UNEVIDENCED and committed        abstain·U  UNEVIDENCED and not committed
  abstain      evidenced and not committed       correct    committed, agreement 1.0
  partial      committed, agreement in [ERROR_THRESHOLD, 1)
  misread / stale / fab*  committed, agreement below ERROR_THRESHOLD, by `error_kind`
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persona_twin import scorer, sut  # noqa: E402
from persona_twin.evidence import Grade  # noqa: E402

TASTE = ("cuisines_liked", "cuisines_avoided", "event_types_liked", "event_types_avoided")
CEILING = ("formality_ceiling",)
GROUPS = {"taste lists": TASTE, "ceiling": CEILING}
LABELS = ("correct", "partial", "misread", "stale", "fab*", "abstain", "abstain·U", "fabrication")


def label(o: scorer.FieldOutcome) -> str:
    if o.grade == Grade.UNEVIDENCED:
        return "fabrication" if o.committed else "abstain·U"
    if not o.committed:
        return "abstain"
    if o.error_kind == scorer.MISREADING:
        return "misread"
    if o.error_kind == scorer.STALENESS:
        return "stale"
    if o.error_kind is not None:
        return "fab*"
    if o.agreement is not None and o.agreement >= 1.0:
        return "correct"
    return "partial"


def group_of(field_id: str) -> str:
    for name, members in GROUPS.items():
        if field_id in members:
            return name
    return "control"


def cells(scores):
    out = {}
    for s in scores:
        for cp in s.checkpoints:
            for o in cp.outcomes:
                out[(s.subject_id, cp.checkpoint, o.field_id)] = o
    return out


def matrix(pairs) -> str:
    counts = Counter((label(a), label(b)) for a, b in pairs)
    rows_used = [l for l in LABELS if any(counts[(l, c)] for c in LABELS)]
    cols_used = [l for l in LABELS if any(counts[(r, l)] for r in LABELS)]
    width = max(len(l) for l in LABELS) + 1
    lines = [" " * width + "".join(f"{c:>{width}}" for c in cols_used)]
    for r in rows_used:
        lines.append(f"{r:<{width}}" + "".join(
            f"{(counts[(r, c)] or ''):>{width}}" if r != c else f"{'·' + str(counts[(r, c)]):>{width}}"
            for c in cols_used))
    moved = sum(v for (r, c), v in counts.items() if r != c)
    lines.append(f"{'moved':<{width}}{moved} of {sum(counts.values())} cells (diagonal marked ·)")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--store", type=Path, required=True)
    ap.add_argument("--emissions", type=Path, required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--a", default="prompted", help="baseline kind (default prompted)")
    ap.add_argument("--b", default="prompted-v2", help="comparison kind (default prompted-v2)")
    ap.add_argument("--subjects", nargs="*", default=None)
    ap.add_argument("--replay-cached", action="store_true",
                    help="serve cached replies by position (see sut.py); an arm with no cache "
                         "directory still pays, so this is how a cached v1 sits beside a fresh v2")
    ap.add_argument("--workers", type=int, default=sut.DEFAULT_WORKERS)
    ap.add_argument("--lam", type=float, default=scorer.DEFAULT_LAMBDA)
    ap.add_argument("--min-evidenced", type=int, default=scorer.MIN_EVIDENCED_FOR_AUFC)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args(argv)

    served, problems = sut.load_served(args.corpus, args.store, subjects=args.subjects)
    for problem in problems:
        print(f"! {problem}", file=sys.stderr)
    store = sut.EmissionStore(args.emissions, replay=args.replay_cached)
    complaints: list[str] = []
    built = sut.factories([args.a, args.b], served, model=args.model, store=store,
                          on_problem=complaints.append)
    results = {name: sut.run(f, served, lam=args.lam, min_evidenced=args.min_evidenced,
                             workers=args.workers) for name, f in built.items()}
    print(sut.format_report(results))
    for c in complaints:
        print(f"! {c}", file=sys.stderr)

    (name_a, name_b) = list(results)
    ca, cb = cells(results[name_a]), cells(results[name_b])
    if set(ca) != set(cb):
        print(f"! cell sets differ: {len(set(ca) ^ set(cb))} unpaired", file=sys.stderr)
    keys = sorted(set(ca) & set(cb))
    by_group = defaultdict(list)
    for k in keys:
        by_group[group_of(k[2])].append((ca[k], cb[k]))

    print(f"\n=== cell transitions: rows {name_a} -> cols {name_b} ===")
    record = {"a": name_a, "b": name_b, "groups": {}, "per_field": {}, "collateral": {}}
    for g in ("taste lists", "ceiling", "control"):
        print(f"\n[{g}]")
        print(matrix(by_group[g]))
        record["groups"][g] = dict(Counter(f"{label(a)}->{label(b)}" for a, b in by_group[g]))

    print(f"\n[per field: net change {name_b} − {name_a}]")
    per_field = defaultdict(Counter)
    for k in keys:
        per_field[k[2]][label(cb[k])] += 1
        per_field[k[2]][label(ca[k])] -= 1
    head = ("fabrication", "misread", "correct", "partial", "abstain", "abstain·U")
    print(f"{'field':<28}" + "".join(f"{h:>12}" for h in head))
    for fid in sorted(per_field, key=lambda f: (group_of(f), f)):
        net = per_field[fid]
        if any(net[h] for h in head):
            print(f"{fid:<28}" + "".join(f"{net[h]:>+12d}" if net[h] else f"{'':>12}" for h in head))
            record["per_field"][fid] = {h: net[h] for h in head if net[h]}

    strong_lost = [k for k in keys if k[2] in TASTE and ca[k].grade == Grade.STRONG
                   and label(ca[k]) in ("correct", "partial") and label(cb[k]) == "abstain"]
    intended_fab = sum(1 for k in keys if k[2] in TASTE
                       and label(ca[k]) == "fabrication" and label(cb[k]) == "abstain·U")
    intended_ceiling = sum(1 for k in keys if k[2] in CEILING
                           and label(ca[k]) == "misread" and label(cb[k]) == "correct")
    print(f"\nintended: taste fabrication->abstain·U {intended_fab}; "
          f"ceiling misread->correct {intended_ceiling}")
    print(f"collateral: STRONG taste cells right in {name_a}, abstained in {name_b}: "
          f"{len(strong_lost)}" + (f"  e.g. {strong_lost[:5]}" if strong_lost else ""))
    record["collateral"] = {"strong_taste_right_to_abstain": len(strong_lost),
                            "cells": [list(k) for k in strong_lost]}
    record["intended"] = {"taste_fab_to_abstain_u": intended_fab,
                          "ceiling_misread_to_correct": intended_ceiling}
    if args.json:
        args.json.write_text(json.dumps(
            {"results": sut.to_json(results), "transitions": record}, indent=2, default=str))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
