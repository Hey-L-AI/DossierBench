"""Join transcript-adjudicated verdicts back onto the episode dataset and produce the
verified failure-mode tables + best per-mode examples.

Final mode = corrected_mode when the verdict changed it, else the auto mode. Rates are
reported GENUINE-only (transcript-confirmed) so the catalog does not inherit answer-key
artifacts. Writes verified_episodes.json, the mode/SUT/model tables, and per-mode example
shortlists (GENUINE, ranked by example_quality).
"""
from __future__ import annotations
import json, collections, os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from persona_twin import data  # noqa: E402

# Shared with extract_failures.py / episodes_and_slices.py / tag_failure_modes.py: work/failures/, or FAILURES_OUT.
D = Path(os.environ.get("FAILURES_OUT", data.work() / "failures"))
V = D / "verdicts"

episodes = json.loads((D / "episodes.json").read_text())

# index verdicts by (subject,kind,model,field,onset_checkpoint); fall back to
# (subject,kind,model,field) if the agent didn't preserve the checkpoint exactly.
def sig(subject, field, emitted, gold):
    return (subject, field,
            json.dumps(emitted, sort_keys=True, default=str),
            json.dumps(gold, sort_keys=True, default=str))

vmap, vmap_loose, vmap_sig = {}, {}, {}
loaded_subjects = set()
for f in sorted(V.glob("gen-*.json")):
    try:
        rows = json.loads(f.read_text())
    except Exception as e:
        print("!! could not parse", f.name, e); continue
    sid = f.stem
    loaded_subjects.add(sid)
    for r in rows:
        k = (sid, r.get("kind"), r.get("model"), r.get("field"), r.get("onset_checkpoint"))
        vmap[k] = r
        vmap_loose[(sid, r.get("kind"), r.get("model"), r.get("field"))] = r

# Build a value-signature map from episodes that DO match a verdict exactly: transcript truth is
# model-independent, so a deduped verdict propagates to sibling episodes with the same
# (subject, field, emitted, gold).
for e in episodes:
    k = (e["subject"], e["kind"], e["model"], e["field"], e["onset_checkpoint"])
    v = vmap.get(k) or vmap_loose.get((e["subject"], e["kind"], e["model"], e["field"]))
    if v is not None:
        vmap_sig.setdefault(sig(e["subject"], e["field"], e["emitted"], e["gold"]), v)

merged = []
missing = 0
for e in episodes:
    if e["subject"] not in loaded_subjects:
        continue  # not yet verified
    k = (e["subject"], e["kind"], e["model"], e["field"], e["onset_checkpoint"])
    v = (vmap.get(k)
         or vmap_loose.get((e["subject"], e["kind"], e["model"], e["field"]))
         or vmap_sig.get(sig(e["subject"], e["field"], e["emitted"], e["gold"])))
    if v is None:
        missing += 1
        continue
    final_mode = v.get("corrected_mode") or e["mode"]
    if not v.get("mode_correct", True) and not v.get("corrected_mode"):
        final_mode = e["mode"]
    merged.append({**e,
                   "verdict": v.get("verdict"),
                   "final_mode": final_mode,
                   "auto_mode": e["mode"],
                   "example_quality": v.get("example_quality"),
                   "evidence_quote": v.get("evidence_quote", ""),
                   "note": v.get("note", "")})

(D / "verified_episodes.json").write_text(json.dumps(merged, indent=1, default=str))

genuine = [m for m in merged if m["verdict"] == "GENUINE"]
artifact = [m for m in merged if m["verdict"] == "KEY_ARTIFACT"]
amb = [m for m in merged if m["verdict"] == "AMBIGUOUS"]

lines = []
lines.append(f"verified episodes: {len(merged)}  (missing join: {missing})")
lines.append(f"  GENUINE {len(genuine)}   KEY_ARTIFACT {len(artifact)}   AMBIGUOUS {len(amb)}")
lines.append(f"  artifact rate: {len(artifact)/len(merged):.1%}" if merged else "")
lines.append("")

# verdict rate by auto_mode — which auto modes are mostly real vs mostly noise
lines.append("== verdict by AUTO mode ==")
am = sorted({m["auto_mode"] for m in merged})
for mode in am:
    sub = [m for m in merged if m["auto_mode"] == mode]
    g = sum(1 for m in sub if m["verdict"] == "GENUINE")
    lines.append(f"  {mode:22s} n={len(sub):4d}  genuine={g:4d} ({g/len(sub):.0%})")
lines.append("")

# GENUINE final-mode counts by SUT
kinds = ["prompted", "notebook", "memory", "mem0"]
fm = collections.Counter(m["final_mode"] for m in genuine)
fmk = collections.Counter((m["final_mode"], m["kind"]) for m in genuine)
lines.append("== GENUINE episodes by FINAL mode x SUT ==")
for mode, n in fm.most_common():
    row = "  ".join(f"{k}={fmk.get((mode,k),0)}" for k in kinds)
    lines.append(f"  {mode:22s} {n:4d}   [{row}]")
lines.append("")

# GENUINE by model
fmm = collections.Counter((m["final_mode"], m["model"]) for m in genuine)
models = sorted({m["model"] for m in genuine})
lines.append("== GENUINE totals by model ==")
bymodel = collections.Counter(m["model"] for m in genuine)
for mod, n in bymodel.most_common():
    lines.append(f"  {mod:34s} {n}")
report = "\n".join(lines)
(D / "verified_report.txt").write_text(report)
print(report)

# per-mode example shortlists (GENUINE, quality desc, dedup by field+emitted+gold)
EX = D / "examples"
EX.mkdir(exist_ok=True)
for mode in fm:
    sub = [m for m in genuine if m["final_mode"] == mode]
    seen, uniq = set(), []
    for m in sorted(sub, key=lambda r: -(r.get("example_quality") or 0)):
        sig = (m["field"], json.dumps(m["emitted"], sort_keys=True, default=str),
               json.dumps(m["gold"], sort_keys=True, default=str), m["subject"])
        if sig in seen:
            continue
        seen.add(sig); uniq.append(m)
    (EX / f"{mode}.json").write_text(json.dumps(uniq, indent=1, default=str))
print(f"\nwrote per-mode GENUINE example shortlists to {EX}")
