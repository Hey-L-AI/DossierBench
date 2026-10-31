"""Collapse per-checkpoint failure cells into episodes and write per-subject slices.

An episode = the onset checkpoint of a maximal run of the same (kind, model, subject, field)
being wrong, keyed also by mode so a mode change opens a new episode. This matches the paper's
episode-based error counting and removes the checkpoint-repeat inflation, so a sub-agent
verifies each distinct failure once against the transcript instead of five times.
"""
from __future__ import annotations
import json, collections, os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from persona_twin import data  # noqa: E402

# Shared with extract_failures.py / tag_failure_modes.py / aggregate_verdicts.py: work/failures/, or FAILURES_OUT.
D = Path(os.environ.get("FAILURES_OUT", data.work() / "failures"))

recs = json.loads((D / "instances.json").read_text())

# group by identity, ordered by checkpoint; open a new episode when checkpoints are
# non-consecutive or the (mode, emitted, gold) signature changes.
groups = collections.defaultdict(list)
for r in recs:
    key = (r["kind"], r["model"], r["subject"], r["field"], r.get("constraint_atom"))
    groups[key].append(r)

episodes = []
for key, rs in groups.items():
    rs.sort(key=lambda r: r["checkpoint"])
    prev_ck = None
    prev_sig = None
    cur = None
    for r in rs:
        sig = (r["mode"], json.dumps(r["emitted"], sort_keys=True, default=str))
        if cur is None or r["checkpoint"] != prev_ck + 1 or sig != prev_sig:
            if cur:
                episodes.append(cur)
            cur = dict(r)
            cur["onset_checkpoint"] = r["checkpoint"]
            cur["last_checkpoint"] = r["checkpoint"]
            cur["span"] = 1
        else:
            cur["last_checkpoint"] = r["checkpoint"]
            cur["span"] += 1
        prev_ck = r["checkpoint"]
        prev_sig = sig
    if cur:
        episodes.append(cur)

(D / "episodes.json").write_text(json.dumps(episodes, indent=1, default=str))

# counts
by_mode = collections.Counter(e["mode"] for e in episodes)
by_mode_kind = collections.Counter((e["mode"], e["kind"]) for e in episodes)
lines = [f"episodes: {len(episodes)} (from {len(recs)} cells)\n"]
for m, n in by_mode.most_common():
    row = "  ".join(f"{k}={by_mode_kind.get((m,k),0)}" for k in ("prompted","notebook","memory","mem0"))
    lines.append(f"{m:24s} {n:5d}   [{row}]")
(D / "episode_counts.txt").write_text("\n".join(lines))
print("\n".join(lines))

# per-subject slices for the shared n10 set, compact fields only
SLICE = D / "by_subject"
SLICE.mkdir(exist_ok=True)
keep = ["kind","model","field","field_class","mode","error_kind","constraint_atom",
        "onset_checkpoint","last_checkpoint","span","grade","emitted","gold",
        "prev_gold_value","agreement"]
for sid in sorted({e["subject"] for e in episodes}):
    sub = [{k: e.get(k) for k in keep} for e in episodes if e["subject"] == sid]
    sub.sort(key=lambda e: (e["onset_checkpoint"], e["field"], e["kind"], e["model"]))
    (SLICE / f"{sid}.json").write_text(json.dumps(sub, indent=1, default=str))
print(f"\nwrote per-subject slices to {SLICE}")
