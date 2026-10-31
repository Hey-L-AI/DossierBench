"""Apply a hand-written edit set to the store, through the editor's own persist path.

Takes a file of owner edits and sends each through the route an accepted model repair takes —
the same `transcript.locate_message` / `apply_message_edits` / `editor._repaired` route, so a hand
edit and a model repair leave the store in the same shape. Refuses anything that is not a pure
message rewrite — an insertion or a deletion would remap message numbers, and every decision
and beat span in the run is keyed by them.

    python scripts/apply_hand_edits.py work/edits-hand.json [--dry-run]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persona_twin import data  # noqa: E402

ROOT = data.work()  # every artifact a pass writes lands here

from persona_twin import author, editor, transcript  # noqa: E402

STORE = data.authored_store()


def main(argv: list[str]) -> int:
    positional = [a for a in argv if not a.startswith("-")]
    if not positional or "-h" in argv or "--help" in argv:
        raise SystemExit(__doc__)
    dry = "--dry-run" in argv
    path = Path(positional[0])
    payload = json.loads(path.read_text())
    store = author.TranscriptStore(STORE.parent)

    for edit in payload["edits"]:
        task_id = edit["task_id"]
        record = json.loads((STORE / f"{task_id}.json").read_text())
        text = record["transcript"]
        pairs = []
        for num, new in sorted(edit["proposed"].items(), key=lambda kv: int(kv[0])):
            # `apply_message_edits` puts the `[n] SPEAKER: ` label back, so a replacement that
            # already carries it is served doubled (`[5] BOT: [5] BOT: ...`). The 2026-08-25 edit
            # set did exactly that on 10 messages (ADR-0068). Strip the label here so
            # re-applying stays clean.
            m = re.match(r"^\s*\[(\d+)\]\s*(USER|BOT):\s*", new)
            if m:
                if int(m.group(1)) != int(num):
                    raise SystemExit(f"{task_id}: message {num} replacement is labelled [{m.group(1)}]")
                new = new[m.end():]
            loc = transcript.locate_message(text, int(num))
            if loc is None:
                raise SystemExit(f"{task_id}: could not locate message {num}")
            pairs.append((loc, new))
        new_text, remap = transcript.apply_message_edits(text, pairs)
        if any(k != v for k, v in remap.items()):
            raise SystemExit(f"{task_id}: not a pure rewrite — message numbers moved")
        # Before-and-after, not after alone. `gen-027-s6` carries two `offer_attribute`
        # problems on messages this edit never touches, and a gate that reads the new page
        # in isolation would refuse a repair for a defect that was already there. What must
        # not happen is the edit ADDING one.
        was = {(p.kind, p.turn) for p in transcript.check_session(text)}
        now = [p for p in transcript.check_session(new_text) if (p.kind, p.turn) not in was]
        print(f"{task_id}: {len(pairs)} message(s), new check_session problems -> "
              f"{[(p.kind, p.turn) for p in now] if now else 'none'}"
              + (f"  (pre-existing: {sorted(was)})" if was else ""))
        if now:
            raise SystemExit(f"{task_id}: the edit introduces {now}")
        if not dry:
            store.write(editor._repaired(record, transcript=new_text))
    print("dry run — nothing written" if dry else f"wrote {len(payload['edits'])} sessions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
