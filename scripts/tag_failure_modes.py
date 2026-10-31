"""Tag each extracted failure instance with a *provisional* human-readable mode.

Heuristics only, off the emitted-vs-gold value shape and the field's metric type. These are
hypotheses for a sub-agent to confirm against the served transcript, not final labels. Writes
the enriched instances back plus per-mode bucket files and a counts table.
"""
from __future__ import annotations

import json
import sys
import collections
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persona_twin import data  # noqa: E402

ROOT = data.work()  # every artifact a pass writes lands here
from persona_twin import fields  # noqa: E402

import os  # noqa: E402
# Shared with extract_failures.py / episodes_and_slices.py / aggregate_verdicts.py: work/failures/, or FAILURES_OUT.
D = Path(os.environ.get("FAILURES_OUT", ROOT / "failures"))

SET_METRICS = {"jaccard"}
ORD_METRIC = "ordinal_distance"
JSD_METRIC = "one_minus_jsd"


def as_set(v):
    if isinstance(v, (list, tuple)):
        return set(v)
    if v in (None, "unknown", ""):
        return set()
    return {v}


def ord_index(fid, v):
    lv = getattr(fields.BY_ID[fid], "levels", None)
    if not lv or v not in lv:
        return None
    return lv.index(v)


def tag(rec) -> str:
    fid = rec["field"]
    ek = rec["error_kind"]
    if ek in ("missed_constraint", "invented_constraint"):
        return ek  # already the right granularity
    metric = fields.BY_ID[fid].metric
    emitted, gold = rec["emitted"], rec["gold"]

    if ek == "fabrication":
        # committed on an UNEVIDENCED field. Did the guess happen to match final gold?
        return "fabrication_lucky" if as_set(emitted) and as_set(emitted) == as_set(gold) \
            else "fabrication_guess"

    # ek == misreading (evidenced but wrong)
    if metric in SET_METRICS:
        e, g = as_set(emitted), as_set(gold)
        if not e and g:
            return "set_collapse_to_empty"      # emitted unknown/empty on an evidenced set
        if e and g and e >= g and e != g:
            return "set_inflation"              # superset: invented extra members
        if e and g and e <= g and e != g:
            return "set_underlist"              # subset: dropped evidenced members
        if e and g and not (e & g):
            return "set_substitution"           # disjoint: swapped members wholesale
        return "set_partial_swap"               # partial overlap + extras
    if metric == ORD_METRIC:
        ei, gi = ord_index(fid, emitted), ord_index(fid, gold)
        if ei is None or gi is None:
            return "ordinal_other"
        dist = abs(ei - gi)
        return "ordinal_off_by_one" if dist == 1 else "ordinal_far"
    if metric == JSD_METRIC:
        return "distribution_shape"
    if metric == "exact":
        return "boolean_flip"
    return "other"


def main() -> int:
    recs = json.loads((D / "instances.json").read_text())
    for r in recs:
        r["mode"] = tag(r)

    # polarity pass: a liked/avoided pair wrong at the same cell where the emitted members of one
    # side appear in the *gold* of the other — a like/avoid confusion. Retag those.
    pairs = [("cuisines_liked", "cuisines_avoided"), ("event_types_liked", "event_types_avoided")]
    by_cell = collections.defaultdict(dict)
    for r in recs:
        by_cell[(r["kind"], r["model"], r["subject"], r["checkpoint"])][r["field"]] = r
    for cell in by_cell.values():
        for a, b in pairs:
            ra, rb = cell.get(a), cell.get(b)
            for r, other_gold in ((ra, rb), (rb, ra)):
                if r is None:
                    continue
                og = as_set(other_gold["gold"]) if other_gold else set()
                if og and as_set(r["emitted"]) & og:
                    r["mode"] = "polarity_flip"

    (D / "instances.json").write_text(json.dumps(recs, indent=1, default=str))

    # counts table
    counts = collections.Counter(r["mode"] for r in recs)
    by_mode_kind = collections.Counter((r["mode"], r["kind"]) for r in recs)
    lines = ["mode counts (raw cells, all cached runs)\n"]
    for m, n in counts.most_common():
        row = "  ".join(f"{k}={by_mode_kind.get((m, k), 0)}"
                        for k in ("prompted", "notebook", "memory", "mem0"))
        lines.append(f"{m:26s} {n:5d}   [{row}]")
    (D / "mode_counts.txt").write_text("\n".join(lines))
    print("\n".join(lines))

    # per-mode bucket files
    buckets = D / "by_mode"
    buckets.mkdir(exist_ok=True)
    for m in counts:
        sub = [r for r in recs if r["mode"] == m]
        (buckets / f"{m}.json").write_text(json.dumps(sub, indent=1, default=str))
    print(f"\nwrote {len(counts)} bucket files to {buckets}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
