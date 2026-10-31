"""Show served transcript text + authored beats + continuity for given (subject, checkpoints).

    python scripts/show_checkpoint.py gen-014 3 4 5

Reads the published answer key and the published transcripts, so it needs no authored store.
"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persona_twin import data  # noqa: E402

ROOT = data.work()  # every artifact a pass writes lands here
from persona_twin import sut

CORPUS = data.corpus("key")
STORE = data.transcripts()

if len(sys.argv) < 3 or sys.argv[1] in ("-h", "--help"):
    raise SystemExit(__doc__)

sid = sys.argv[1]
ckpts = set(int(x) for x in sys.argv[2:])
items = sut.load_items(CORPUS)
records = sut.load_store(STORE)
it = items[sid]
for s in it["sessions"]:
    if s["checkpoint"] not in ckpts:
        continue
    print("=" * 70)
    print(f"checkpoint {s['checkpoint']} session {s['session']} "
          f"distractor={s.get('is_distractor')} removable={s.get('removable')} date={s.get('date')}")
    print("--- BEATS (authored intent) ---")
    for b in s["beats"]:
        print(f"   {b.get('field_id'):22s} {b.get('move'):12s} supports_gold={b.get('supports_gold')} "
              f"gold={b.get('gold_value')!r} attr={b.get('attributed')}  note={b.get('note')}")
    print("--- SERVED TRANSCRIPT (what the SUT sees) ---")
    rec = records.get(f"{sid}-s{s['session']}") or records.get(str(s.get("task_id")))
    print((rec or {}).get("transcript", "<<missing>>"))
