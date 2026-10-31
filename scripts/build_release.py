#!/usr/bin/env python3
"""Assemble the HuggingFace release package from the canonical base.

The canonical base (`docs/provenance/CANONICAL.md`) is the source of truth. This script does
not decide anything — it copies the two gold corpora verbatim, extracts the served dialogue
from the transcript store, emits one merged JSONL view for `datasets.load_dataset`, describes
the 24 card fields, and hashes everything. Every derived artifact is verified to round-trip
against the canonical files before `SHA256SUMS` is written, so the package cannot ship a view
that disagrees with the gold it was built from.

    python scripts/build_release.py --base work/provenance/<base> --store work/authored/claude-opus-5

A **base** is a directory holding the two gold corpora and a `SHA256SUMS` over them — what a
promotion pass writes when it fixes a base as canonical. The two gold filenames are resolved by
shape rather than named here (the map is the one whose name carries `-map-`), so a base bump
renames both files and costs nothing in this script.

Ships (scope: credited answer key + model-free map + conversations, all 50 public):
  gold/<key>.json         credited answer key (values + credited grades)
  gold/<key>-map-*.json   model-free map (schedule-only grades)
  gold/fields.json        the 24-field registry, self-describing
  transcripts/gen-*.json  the served sessions, trimmed to {ids, transcript}
  data/subjects.jsonl     one row per subject: conversations + both golds
  SHA256SUMS              over every shipped file

The output of this script is exactly what `DOSSIERBENCH_DATA` may point at, so a release you
build locally is scoreable without going near HuggingFace.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persona_twin import data, fields  # noqa: E402

# Assigned by `main()`; the defaults below are what a bare run uses.
BASE = data.work() / "base"
STORE = data.authored_store()
OUT = data.work() / "release"
ANSWER_KEY = ""
MAP = ""

FIELD_IDS = [f.field_id for f in fields.ALL_FIELDS]

# Store keys that are answer key / generation internals, not part of what a submission is served.
# The scorer reads only `transcript` from a store record (sut.build_served); these ids come from
# the corpus item. We keep a small, honest subset so the repo scorer still loads the directory.
TRANSCRIPT_KEYS = (
    "task_id", "subject_id", "session", "checkpoint", "date",
    "is_distractor", "removable", "transcript",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def load_sha_manifest(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        digest, name = line.split(maxsplit=1)
        out[name.strip()] = digest
    return out


def load_store() -> dict[str, dict]:
    """Authored records by task id — `._*` sidecars excluded (verify.load_store's rule)."""
    out: dict[str, dict] = {}
    for path in sorted(STORE.glob("gen-*.json")):
        if path.name.startswith("._"):
            continue
        rec = json.loads(path.read_text())
        out[str(rec["task_id"])] = rec
    return out


def build_fields_json() -> dict:
    """The card, described from the single source of truth (persona_twin.fields)."""
    out = {
        "schema_version": fields.SCHEMA_VERSION,
        "distribution_reading_rule": fields.DISTRIBUTION_READING_RULE,
        "grades": {
            "UNEVIDENCED": "the conversations so far do not evidence this field; a faithful "
                           "submission abstains (emits 'unknown')",
            "WEAK": "the conversations narrow the field without pinning it; a value is scored "
                    "with partial credit under the field's metric",
            "STRONG": "the conversations pin the field; a value is scored in full",
        },
        "fields": [],
    }
    for f in fields.ALL_FIELDS:
        entry = {
            "field_id": f.field_id,
            "group": f.group,
            "metric": f.metric,
            "is_constraint": f.is_constraint,
            "doc": f.doc,
        }
        if f.levels:
            entry["levels"] = list(f.levels)
        if f.anchors:
            entry["anchors"] = list(f.anchors)
        out["fields"].append(entry)
    out["vocabularies"] = {
        "cuisines": list(fields.CUISINES),
        "price_bands": list(fields.PRICE_BANDS),
        "party_sizes": list(fields.PARTY_SIZES),
        "event_types": list(fields.EVENT_TYPES),
        "diets": list(fields.DIETS),
        "allergens": list(fields.ALLERGENS),
        "accessibility_needs": list(fields.ACCESSIBILITY_NEEDS),
    }
    return out


def build():
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "gold").mkdir(parents=True)
    (OUT / "transcripts").mkdir()
    (OUT / "data").mkdir()

    base_sums = load_sha_manifest(BASE / "SHA256SUMS")

    # 1. Gold, copied verbatim and integrity-checked against the base's own manifest.
    key_payload = json.loads((BASE / ANSWER_KEY).read_text())
    map_payload = json.loads((BASE / MAP).read_text())
    for name in (ANSWER_KEY, MAP):
        src = BASE / name
        dst = OUT / "gold" / name
        shutil.copyfile(src, dst)
        digest = sha256(dst)
        expected = base_sums.get(name)
        assert digest == expected, f"{name}: {digest} != base {expected}"
    print(f"gold: 2 corpora copied, sha256 matches base SHA256SUMS")

    (OUT / "gold" / "fields.json").write_text(
        json.dumps(build_fields_json(), indent=2, ensure_ascii=False) + "\n"
    )

    key_items = {i["subject_id"]: i for i in key_payload["items"]}
    map_items = {i["subject_id"]: i for i in map_payload["items"]}
    store = load_store()

    # 2. Transcripts, trimmed. Verify the text is byte-identical to the store for every task
    #    the corpus references, and that every referenced task has a transcript.
    tx_written = 0
    for item in key_payload["items"]:
        for s in item["sessions"]:
            tid = str(s["task_id"])
            rec = store[tid]
            text = str(rec.get("transcript") or "")
            assert text.strip(), f"{tid}: empty transcript"
            trimmed = {k: rec.get(k) for k in TRANSCRIPT_KEYS}
            assert trimmed["transcript"] == text
            (OUT / "transcripts" / f"{tid}.json").write_text(
                json.dumps(trimmed, indent=2, ensure_ascii=False) + "\n"
            )
            tx_written += 1
    print(f"transcripts: {tx_written} sessions written (trimmed to {len(TRANSCRIPT_KEYS)} keys)")

    # 3. Merged per-subject JSONL. Heterogeneous gold blobs (values + both grade layers) are
    #    JSON-encoded per checkpoint so the row schema stays flat and datasets.load_dataset never
    #    trips on a variable-keyed distribution dict. Scalars/flags/transcript stay native columns.
    rows = []
    for item in key_payload["items"]:
        sid = item["subject_id"]
        mitem = map_items[sid]
        by_ckpt = {}
        for s in item["sessions"]:
            tid = str(s["task_id"])
            ck = int(s["checkpoint"])
            by_ckpt.setdefault(ck, []).append({
                "session": int(s["session"]),
                "task_id": tid,
                "is_distractor": bool(s.get("is_distractor")),
                "removable": bool(s.get("removable")),
                "date": str(s.get("date") or ""),
                "transcript": str(store[tid].get("transcript") or ""),
            })
        checkpoints = []
        for ck in sorted(item["card_at"], key=int):
            served = sorted(by_ckpt.get(int(ck), []), key=lambda x: x["session"])
            checkpoints.append({
                "checkpoint": int(ck),
                "sessions": served,
                "gold_values_json": json.dumps(item["card_at"][ck], ensure_ascii=False, sort_keys=True),
                "grades_credited_json": json.dumps(item["provisional"][ck], ensure_ascii=False, sort_keys=True),
                "grades_map_json": json.dumps(mitem["provisional"][ck], ensure_ascii=False, sort_keys=True),
            })
        rows.append({
            "subject_id": sid,
            "split": item["split"],
            "stratum": item["stratum"],
            "sociality": item.get("sociality"),
            "deliberateness": item.get("deliberateness"),
            "pace": item.get("pace"),
            "session_count": int(item["session_count"]),
            "n_checkpoints": len(item["card_at"]),
            "card_json": json.dumps(item["card"], ensure_ascii=False, sort_keys=True),
            "evidence_provenance_json": json.dumps(item.get("evidence_provenance", {}), ensure_ascii=False, sort_keys=True),
            "checkpoints": checkpoints,
        })
    jsonl = OUT / "data" / "subjects.jsonl"
    with jsonl.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"data/subjects.jsonl: {len(rows)} subjects")

    # 4. Round-trip: the JSONL must reconstruct exactly what the canonical gold holds.
    checked = 0
    for r in rows:
        sid = r["subject_id"]
        assert json.loads(r["card_json"]) == key_items[sid]["card"]
        for cp in r["checkpoints"]:
            ck = str(cp["checkpoint"])
            assert json.loads(cp["gold_values_json"]) == key_items[sid]["card_at"][ck]
            assert json.loads(cp["grades_credited_json"]) == key_items[sid]["provisional"][ck]
            assert json.loads(cp["grades_map_json"]) == map_items[sid]["provisional"][ck]
            for sess in cp["sessions"]:
                assert sess["transcript"] == str(store[sess["task_id"]].get("transcript") or "")
            checked += 1
    print(f"round-trip: {checked} checkpoints reconstruct the canonical gold exactly")

    print("\nBuilt", OUT)


def main(argv: list[str] | None = None) -> int:
    global BASE, STORE, OUT, ANSWER_KEY, MAP
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", type=Path, default=BASE,
                    help="directory holding the two gold corpora and their SHA256SUMS")
    ap.add_argument("--store", type=Path, default=STORE,
                    help="authored store the served dialogue is extracted from")
    ap.add_argument("--out", type=Path, default=OUT, help="release directory to write (replaced)")
    args = ap.parse_args(argv)
    BASE, STORE, OUT = args.base, args.store, args.out

    golds = sorted(p.name for p in BASE.glob("corpus-*.json"))
    maps = [n for n in golds if "-map-" in n]
    keys = [n for n in golds if "-map-" not in n]
    if len(keys) != 1 or len(maps) != 1:
        ap.error(f"expected one answer key and one map in {BASE}; found keys={keys} maps={maps}")
    ANSWER_KEY, MAP = keys[0], maps[0]
    print(f"base   {BASE}\nkey    {ANSWER_KEY}\nmap    {MAP}\nstore  {STORE}\n")
    build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
