"""Read a corpus off disk: what was authored, and what the twin looks like at each prefix.

Lengths, trajectories and beat coverage. No model calls — everything here is computed from the
corpus and the authored store, so run it after every authoring pass.

    python scripts/read_corpus.py
    python scripts/read_corpus.py --corpus work/corpus.json --store work/authored/claude-opus-5

The twin shown is the **designed** map (`provisional`), not a graded one. These are the grades
the beat placement predicts a competent reader could reach; they are a *ceiling* on what a reader
may later report, never a measurement of what it will.
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

from persona_twin import fields, transcript                    # noqa: E402

# Rebound by `main()`; these are what a bare run uses.
CORPUS = data.local_corpus()
STORE = data.authored_store()


def records() -> dict[str, dict]:
    out = {}
    for path in sorted(STORE.glob("gen-*.json")):
        if path.name.startswith("._"):     # exFAT sidecars; they double every count
            continue
        record = json.loads(path.read_text())
        out[record["task_id"]] = record
    return out


def main(argv: list[str] | None = None) -> None:
    global CORPUS, STORE
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, default=CORPUS)
    ap.add_argument("--store", type=Path, default=STORE)
    args = ap.parse_args(argv)
    CORPUS, STORE = args.corpus, args.store

    corpus = json.loads(CORPUS.read_text())
    store = records()
    items = corpus["items"]

    print("=== the pilot ===\n")
    print(f"subjects   {len(items)}")
    print(f"sessions   {sum(len(i['sessions']) for i in items)}")
    print(f"authored   {len(store)}  ({sum(1 for r in store.values() if r['transcript'])} non-empty)")
    exhausted = [t for t, r in store.items() if r.get("exhausted")]
    print(f"exhausted  {len(exhausted)}{' — ' + ', '.join(exhausted) if exhausted else ''}")

    print("\n=== conversation length: briefed against realised ===\n")
    #: Computed from this corpus, not quoted from a previous one. ADR-0038 set the budget to
    #: 12 turns at turns/3.0 beats, and the numbers below are how honest that brief turned
    #: out to be *here* — a hardcoded sentence about an earlier run would be a false
    #: statement the moment the pilot is regenerated, which is what these lines used to be.
    by_brief: dict[int, list[int]] = {}
    realised: list[int] = []
    overshoot: list[int] = []
    for record in store.values():
        if record["transcript"]:
            got = len(_user_turns(record["transcript"]))
            by_brief.setdefault(record["turns"], []).append(got)
            realised.append(got)
            overshoot.append(record["turns"] - got)
    if realised:
        print(f"The brief overshoots by {_mean(overshoot):.2f} turns on average. Realised "
              f"{min(realised)}-{max(realised)} user turns,")
        print(f"mean {_mean(realised):.2f}, sd {_sd(realised):.2f}, over "
              f"{len(realised)} sessions. A brief the author")
        print("tracks is the point; a brief it saturates against means the ceiling, not the "
              "draw,\n"
              "is setting the length.\n")
    print("     briefed   n   mean user turns")
    for briefed in sorted(by_brief):
        got = by_brief[briefed]
        print(f"     {briefed:7d} {len(got):3d}   {sum(got)/len(got):.1f}")

    print("\n=== the twin at every prefix ===\n")
    print("Designed grades over all 24 fields. Read down a column to see one subject's")
    print("timeline move from abstention to commitment — this is what replaced the tiers.\n")
    for item in items:
        prov = item["provisional"]
        print(f"--- {item['subject_id']}  {item['session_count']} sessions, "
              f"pace {item['pace']}, {item['stratum']}")
        header = "     session  " + "".join(f"{int(k):>4d}" for k in sorted(prov, key=int))
        print(header)
        for grade in ("STRONG", "WEAK", "UNEVIDENCED"):
            row = "".join(
                f"{sum(1 for v in prov[k].values() if v == grade):>4d}"
                for k in sorted(prov, key=int)
            )
            print(f"     {grade:11s}{row}")
        events = []
        for correction in item["corrections"]:
            events.append(f"s{correction['checkpoint']} correction on "
                          f"{correction['field_id']} "
                          f"({correction['old_value']} -> {correction['new_value']})")
        for session in item["distractor_checkpoints"]:
            events.append(f"s{session} distractor")
        removable = set(item["removable_sessions"]) - set(item["distractor_checkpoints"])
        for session in sorted(removable):
            events.append(f"s{session} interference")
        if events:
            print("     events: " + "; ".join(events))
        print(f"     beats placed {item['beats_placed']} of "
              f"{item['beats_to_pin_all']} needed for full reveal")
        print()

    print("=== beat coverage ===\n")
    moves = Counter()
    per_field = Counter()
    for item in items:
        for session in item["sessions"]:
            for beat in session["beats"]:
                moves[beat["move"]] += 1
                per_field[beat["field_id"]] += 1
    print("moves:  " + ", ".join(f"{m} {c}" for m, c in moves.most_common()))
    never = [f for f in _all_fields() if f not in per_field]
    print(f"fields touched: {len(per_field)}/24")
    if never:
        print(f"never touched in the whole pilot: {', '.join(never)}")

    print("\n=== difficulty lanes ===\n")
    for item in items:
        removable = item["removable_sessions"]
        corrected = [c["checkpoint"] for c in item["corrections"]]
        print(f"{item['subject_id']}: {len(item['sessions'])} sessions, "
              f"{len(removable)} droppable for an easy lane {removable}, "
              f"{len(corrected)} correction(s) at {corrected} that cannot be dropped")


def _mean(values) -> float:
    return sum(values) / len(values)


def _sd(values) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return (sum((v - mean) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def _user_turns(text: str) -> list[str]:
    """The user's turns, read with the library's own parser.

    This used to match `line.startswith("USER:")`, which silently returned zero for every
    v0.4 transcript: the author now emits a message number, so the line begins `[0] USER:`.
    A count that reads 0 for all 57 sessions is not a length measurement, and every number
    derived from it — the overshoot, the sd, the per-brief means — was 0 too.
    """
    return [body for speaker, body in transcript.speaker_turns(text) if speaker == "USER"]


def _all_fields() -> list[str]:
    return list(fields.ALL_FIELDS_IDS)


if __name__ == "__main__":
    main()
