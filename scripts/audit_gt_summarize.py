"""Collapse the per-cell error dump into (subject, field) candidates for GT review."""
import json, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from persona_twin import data as _data  # noqa: E402

ROOT = _data.work()
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "audit-gt-across-models.json"
if not SRC.exists():
    raise SystemExit(f"no dump at {SRC}\nRun scripts/audit_gt_across_models.py first, "
                     f"or pass the dump as the first argument.")
data = json.loads(SRC.read_text())

# group by (subject, field)
groups = defaultdict(lambda: {"ckpts": set(), "n_recs": 0, "kinds": set(),
                              "models": set(), "configs": set(), "gold": None,
                              "emitted": set(), "max_combos": 0, "ck_combo": {}})
for key, recs in data.items():
    sid, ck, fid = key.split("|")
    ck = int(ck[1:])
    g = groups[(sid, fid)]
    g["ckpts"].add(ck)
    g["n_recs"] += len(recs)
    g["ck_combo"][ck] = len(recs)
    g["max_combos"] = max(g["max_combos"], len(recs))
    for r in recs:
        g["kinds"].add(r["kind"])
        g["models"].add(r["model"])
        g["configs"].add(r["config"])
        g["gold"] = r["gold"]
        g["emitted"].add(json.dumps(r["emitted"]))

# rank: unanimous cells (max_combos high, all 3 models, all 4 configs) first
rows = []
for (sid, fid), g in groups.items():
    unanimous_ckpts = sum(1 for ck, n in g["ck_combo"].items() if n >= 11)
    rows.append((sid, fid, g, unanimous_ckpts))
rows.sort(key=lambda r: (-r[3], -r[2]["max_combos"], -len(r[2]["ckpts"])))

print("SUBJECT   FIELD                     unanCk maxN nCk models configs kinds  gold")
for sid, fid, g, unan in rows:
    if g["max_combos"] < 6:
        continue
    print(f"{sid} {fid:24s} {unan:5d} {g['max_combos']:4d} {len(g['ckpts']):3d} "
          f"{len(g['models'])}/3   {len(g['configs'])}/4    {','.join(sorted(g['kinds']))[:11]:11s} "
          f"{json.dumps(g['gold'])}")
    for ev in sorted(g["emitted"])[:6]:
        print(f"        emit: {ev}")
