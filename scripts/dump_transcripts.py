"""Dump served transcripts + gold + per-checkpoint evidence grades for every subject, against
the canonical answer key, into `work/transcripts-dump/` for transcript-grounded failure-mode
adjudication."""
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persona_twin import data  # noqa: E402

ROOT = data.work()  # every artifact a pass writes lands here
from persona_twin import sut, fields  # noqa
from persona_twin.evidence import Grade  # noqa

# The published answer key; `data.corpus("map")` for the model-free gold.
CORPUS = data.corpus("key")
STORE = data.transcripts()
# Written to work/transcripts-dump/; override with TRANSCRIPTS_OUT.
OUT = Path(os.environ.get("TRANSCRIPTS_OUT", ROOT / "transcripts-dump"))
OUT.mkdir(parents=True, exist_ok=True)

# Enum -> letter. NB: on Python 3.14 str(Grade.WEAK) == "1" (IntEnum), so the old
# str(grade).replace("Grade.","")[0] rendered digits and the "!= 'Grade.UNEVIDENCED'" filter never
# fired (every field printed as evidenced). Map through the enum instead.
LETTER = {Grade.UNEVIDENCED: "U", Grade.WEAK: "W", Grade.STRONG: "S"}

subjects = sys.argv[1:] or [f"gen-{i:03d}" for i in range(50)]
served, problems = sut.load_served(CORPUS, STORE, subjects=subjects)
for p in problems:
    print("!", p, file=sys.stderr)

for one in served:
    subj = one.subject
    sid = subj.subject_id
    lines = [f"# {sid}", ""]
    lines.append("## FINAL GOLD CARD")
    lines.append(json.dumps(subj.gold.values, indent=1, default=str))
    lines.append("")
    lines.append("## EVIDENCE GRADE PER CHECKPOINT (field: grade at each ckpt; S/W/U)")
    n = subj.evidence.checkpoint_count
    for fid in fields.ALL_FIELDS_IDS:
        grades = [LETTER.get(subj.evidence.grade(c, fid), "?") for c in range(n)]
        if set(grades) != {"U"}:
            lines.append(f"  {fid:26s} {''.join(grades)}")
    lines.append("")
    lines.append("## GOLD VALUE AT EACH CHECKPOINT (evidenced fields only)")
    for c in range(n):
        g = subj.gold_at(c)
        evd = {fid: g.get(fid) for fid in fields.ALL_FIELDS_IDS
               if subj.evidence.grade(c, fid) != Grade.UNEVIDENCED}
        lines.append(f"  c{c}: {json.dumps(evd, default=str)}")
    lines.append("")
    lines.append("## SERVED TRANSCRIPTS (what the SUT sees, in checkpoint/session order)")
    for ck, batch in enumerate(one.batches):
        for ss in batch:
            lines.append(f"\n===== checkpoint {ck} · session {ss.session} · date {ss.date} =====")
            lines.append(ss.transcript)
    (OUT / f"{sid}.md").write_text("\n".join(lines))
print(f"wrote {len(served)} transcripts to {OUT}")
