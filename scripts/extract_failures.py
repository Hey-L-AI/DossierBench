"""Reconstruct every per-cell failure instance from the cached SUT emissions.

Position-replays the emission cache (free, no model calls) against the one canonical
answer key, re-runs the scorer, and dumps one record per committed-and-wrong cell,
per fabrication, and per constraint failure — with the emitted value, the gold value,
the evidence grade, the agreement and the scorer's error_kind. This is the raw material
for the failure-mode categorization; nothing here decides a taxonomy, it only surfaces
instances a reader (or a sub-agent) can adjudicate against the served transcript.

    python scripts/extract_failures.py                       # every run in the cache
    python scripts/extract_failures.py --run prompted-v2:openai/gpt-5.6-luna

Reads the emission cache `scripts/score.py` writes (`work/sut-emissions/`) and writes to
`work/failures/` (override with `FAILURES_OUT`), where `episodes_and_slices.py`,
`tag_failure_modes.py` and `aggregate_verdicts.py` pick it up.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Reproducibility: some cached emissions (notably glm-5.3-flash) are malformed JSON whose recovery
# iterates hash-ordered sets/dicts, so error counts drift a few cells per process. Pin the hash seed
# by re-exec'ing once with PYTHONHASHSEED=0 so a re-run is byte-identical.
if os.environ.get("PYTHONHASHSEED") != "0":
    os.environ["PYTHONHASHSEED"] = "0"
    os.execv(sys.executable, [sys.executable, *sys.argv])

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persona_twin import data  # noqa: E402

ROOT = data.work()  # every artifact a pass writes lands here

from persona_twin import sut, scorer, baselines, fields  # noqa: E402
from persona_twin.evidence import Grade  # noqa: E402

# The published answer key; `data.corpus("map")` for the model-free gold.
CORPUS = data.corpus("key")
STORE = data.transcripts()
EMISSIONS = ROOT / "sut-emissions"
# Shared with episodes_and_slices.py / tag_failure_modes.py / aggregate_verdicts.py.
OUT = Path(os.environ.get("FAILURES_OUT", ROOT / "failures"))


def _hidden(name: str) -> bool:
    # `._*` are AppleDouble sidecars some filesystems (e.g. exFAT) write beside every file.
    return name.startswith(".")


def cached_runs(emissions: Path) -> list[tuple[str, str]]:
    """Every `(kind, model)` pair present in the cache, in a stable order. The cache layout is
    `<emissions>/<kind>/<model slug>/`, and a slug is the model id with `/` written as `__`."""
    if not emissions.is_dir():
        raise SystemExit(
            f"no emission cache at {emissions}\n"
            f"This replays a scoring run rather than making one. Score a submission first:\n"
            f"    python scripts/score.py --sut prompted:<model>")
    runs = []
    for kind_dir in sorted(p for p in emissions.iterdir() if p.is_dir() and not _hidden(p.name)):
        for slug_dir in sorted(p for p in kind_dir.iterdir() if p.is_dir() and not _hidden(p.name)):
            runs.append((kind_dir.name, slug_dir.name.replace("__", "/")))
    return runs


PREF = frozenset(fields.PREFERENCE_IDS)
CONSTRAINT = frozenset(fields.CONSTRAINT_IDS)


def emitted_subjects(kind: str, model: str) -> list[str]:
    slug = sut.store_slug(model)
    d = EMISSIONS / kind / slug
    if not d.exists():
        return []
    ids = set()
    # os.listdir with an explicit shape check rather than a glob pattern: it skips `._*`
    # sidecars and the memory submission's `-extract` phase files in one place.
    for name in os.listdir(d):
        if _hidden(name) or "-extract" in name or not name.startswith("gen-"):
            continue
        if "-c" in name and name.endswith(".json"):
            ids.add(name.split("-c")[0])
    return sorted(ids)


def jsonify(v):
    if isinstance(v, (list, tuple)):
        return list(v)
    if isinstance(v, dict):
        return {k: jsonify(x) for k, x in v.items()}
    return v


def main(argv: list[str] | None = None) -> int:
    global EMISSIONS
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--emissions", type=Path, default=EMISSIONS,
                    help="emission cache to replay (default: work/sut-emissions)")
    ap.add_argument("--run", action="append", default=None, metavar="KIND:MODEL",
                    help="replay only this run; repeatable. Default: every run in the cache")
    args = ap.parse_args(argv)
    EMISSIONS = args.emissions
    if args.run:
        KIND_MODELS = [tuple(r.split(":", 1)) for r in args.run]
    else:
        KIND_MODELS = cached_runs(EMISSIONS)
    OUT.mkdir(parents=True, exist_ok=True)
    store = sut.EmissionStore(EMISSIONS, replay=True)
    all_records: list[dict] = []
    summary: list[dict] = []

    for kind, model in KIND_MODELS:
        subjects = emitted_subjects(kind, model)
        if not subjects:
            continue
        served, problems = sut.load_served(CORPUS, STORE, subjects=subjects)
        by_id = {one.subject_id: one for one in served}
        n_err = 0
        n_cells = 0
        for sid in subjects:
            one = by_id.get(sid)
            if one is None:
                continue
            subject = one.subject
            factory = sut.factories(
                [f"{kind}:{model}"], served, model=model, invoke=None, store=store,
            )[f"{kind}:{model}"]
            sut_inst = factory()
            try:
                cards, _bm, _tm = baselines.run_trajectory(
                    sut_inst, subject, [list(b) for b in one.batches]
                )
            except Exception as exc:  # a subject the cache does not fully cover
                problems.append(f"{kind}:{model}:{sid}: {type(exc).__name__}: {exc}")
                continue
            score = scorer.score_subject(subject, cards)
            for c, ck in enumerate(score.checkpoints):
                emitted_card = cards[c]
                gold = subject.gold_at(c)
                for o in ck.outcomes:
                    fid = o.field_id
                    is_pref = fid in PREF
                    is_con = fid in CONSTRAINT
                    n_cells += 1
                    if not o.error_kind and not (is_con and o.fabricated):
                        continue
                    # A committed-and-wrong cell, a fabrication, or a constraint fab.
                    n_err += 1
                    correction = subject.correction_for(fid, c)
                    rec = {
                        "kind": kind, "model": model, "subject": sid, "checkpoint": c,
                        "field": fid,
                        "field_class": "constraint" if is_con else "preference" if is_pref else "other",
                        "error_kind": o.error_kind,
                        "grade": str(o.grade).replace("Grade.", ""),
                        "committed": o.committed,
                        "fabricated": o.fabricated,
                        "agreement": o.agreement,
                        "emitted": jsonify(emitted_card.get(fid)),
                        "gold": jsonify(gold.get(fid)),
                        "prev_gold_value": jsonify(correction.old_value) if correction else None,
                        "was_corrected_by_here": correction is not None,
                        "n_checkpoints": len(score.checkpoints),
                    }
                    all_records.append(rec)
            # Constraint gating failures at the member level, mirroring scorer._score_constraints:
            # a *missed* atom is an active gold member the SUT did not predict (recall<1); an
            # *invented* atom is a predicted member with no gold backing (precision<1). Emitting
            # "unknown" against a gold of False/[] is NOT a failure and is not recorded.
            for c, ck in enumerate(score.checkpoints):
                if not ck.constraint_failure:
                    continue
                gold = subject.gold_at(c)
                emitted_card = cards[c]
                for fid in fields.CONSTRAINT_IDS:
                    g = subject.evidence.grade(c, fid)
                    ev = emitted_card.get(fid)
                    committed = emitted_card.committed(fid)
                    # Enum comparison, NOT str(g) == "Grade.UNEVIDENCED": on Python 3.14 str(IntEnum)
                    # returns "0", so the string form never matched and this branch was skipped,
                    # recording spurious missed_constraint atoms on UNEVIDENCED cells (where the SUT
                    # correctly withheld). The scorer itself is correct (scorer._score_constraints).
                    if g == Grade.UNEVIDENCED:
                        gold_members = set()
                    else:
                        gold_members = set(scorer._constraint_members(fid, gold.get(fid)))
                    pred_members = (
                        set(scorer._constraint_members(fid, ev))
                        if committed and scorer._constraint_is_active(fid, ev) else set()
                    )
                    missed = gold_members - pred_members
                    invented = pred_members - gold_members
                    for atom in sorted(missed):
                        all_records.append({
                            "kind": kind, "model": model, "subject": sid, "checkpoint": c,
                            "field": fid, "field_class": "constraint",
                            "error_kind": "missed_constraint", "constraint_atom": atom,
                            "grade": str(g).replace("Grade.", ""), "committed": committed,
                            "fabricated": False, "agreement": None,
                            "emitted": jsonify(ev), "gold": jsonify(gold.get(fid)),
                            "prev_gold_value": None, "was_corrected_by_here": False,
                            "n_checkpoints": len(score.checkpoints),
                        })
                    for atom in sorted(invented):
                        all_records.append({
                            "kind": kind, "model": model, "subject": sid, "checkpoint": c,
                            "field": fid, "field_class": "constraint",
                            "error_kind": "invented_constraint", "constraint_atom": atom,
                            "grade": str(g).replace("Grade.", ""), "committed": committed,
                            "fabricated": False, "agreement": None,
                            "emitted": jsonify(ev), "gold": jsonify(gold.get(fid)),
                            "prev_gold_value": None, "was_corrected_by_here": False,
                            "n_checkpoints": len(score.checkpoints),
                        })
        summary.append({
            "kind": kind, "model": model, "subjects": len(subjects),
            "error_instances": n_err, "cells_scored": n_cells,
        })
        print(f"{kind:9s} {model:34s} subjects={len(subjects):2d} err_cells={n_err}")

    (OUT / "instances.json").write_text(json.dumps(all_records, indent=1, default=str))
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    if problems:
        (OUT / "problems.txt").write_text("\n".join(problems))
    print(f"\nwrote {len(all_records)} error instances -> {OUT/'instances.json'}")
    print(f"problems: {len(problems)} (see problems.txt)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
