"""Replay every cached SUT run and dump every charged error, with emitted+gold values.

Goal: find ground-truth issues. An error that ALL/most (model x config) combos make on the
same (subject, checkpoint, field) is a strong signal the gold/map is wrong, not the SUT.

This is the replay used for the 2026-08 gold review; its defaults are that review's grid
(3 models x 4 configs over the first 10 subjects). Every run it names must already be in the
emission cache (`python scripts/score.py` writes it to `work/sut-emissions/`).

    python scripts/audit_gt_across_models.py
    python scripts/audit_gt_across_models.py --models openai/gpt-5.6-luna \
        --configs prompted notebook --subjects all
"""
from __future__ import annotations
import argparse, json, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persona_twin import data  # noqa: E402

ROOT = data.work()  # every artifact a pass writes lands here

from persona_twin import sut, scorer, baselines, fields  # noqa: E402

CORPUS = data.corpus("key")
STORE = data.transcripts()
EMISSIONS = ROOT / "sut-emissions"

#: Defaults: the 2026-08 gold-review grid.
MODELS = ["openai/gpt-5.6-luna", "deepseek/deepseek-v4-flash-0731", "z-ai/glm-5.3-flash"]
CONFIGS = ["prompted", "notebook", "memory", "mem0"]
SUBJECTS = [f"gen-{i:03d}" for i in range(10)]  # n10

def val(card, fid):
    try:
        return card.get(fid)
    except Exception:
        return None

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", nargs="+", default=MODELS)
    ap.add_argument("--configs", nargs="+", default=CONFIGS, help="submission kinds")
    ap.add_argument("--subjects", nargs="+", default=SUBJECTS,
                    help="subject ids, or `all` for every subject in the key")
    args = ap.parse_args(argv)
    subjects = None if args.subjects == ["all"] else args.subjects
    n_models, n_configs = len(args.models), len(args.configs)
    n_combos = n_models * n_configs

    served, problems = sut.load_served(CORPUS, STORE, subjects=subjects)
    for p in problems:
        print("!", p, file=sys.stderr)
    by_id = {s.subject_id: s for s in served}
    store = sut.EmissionStore(EMISSIONS, replay=True)

    # errors[(sid, ckpt, fid)] -> list of dict(model, config, kind, emitted, gold, grade)
    errors = defaultdict(list)
    combo_count = 0

    for model in args.models:
        specs = [f"{c}:{model}" for c in args.configs]
        built = sut.factories(specs, served, model=model, store=store)
        for label, factory in built.items():
            kind = label.split(":")[0]
            combo_count += 1
            for one in served:
                subj = one.subject
                sid = subj.subject_id
                sut_obj = factory()
                cards, _, _ = baselines.run_trajectory(
                    sut_obj, subj, [list(b) for b in one.batches])
                for ckpt, card in enumerate(cards):
                    cs = scorer.score_checkpoint(subj, ckpt, card)
                    gold = subj.gold_at(ckpt)
                    for oc in cs.outcomes:
                        if oc.error_kind:
                            errors[(sid, ckpt, oc.field_id)].append(dict(
                                model=model.split("/")[-1], config=kind,
                                kind=oc.error_kind,
                                emitted=val(card, oc.field_id),
                                gold=val(gold, oc.field_id),
                                grade=str(oc.grade),
                                agreement=oc.agreement,
                            ))
    print(f"# {combo_count} (model x config) combos replayed")
    out = {f"{sid}|c{ckpt}|{fid}": recs for (sid, ckpt, fid), recs in errors.items()}
    (ROOT / "audit-gt-across-models.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"# {len(out)} distinct (subject,checkpoint,field) error cells")

    # Rank by how many combos hit the same cell (at most n_combos).
    ranked = sorted(errors.items(), key=lambda kv: -len(kv[1]))
    floor = max(1, (n_combos + 1) // 2)
    print(f"\n# cells charged across the MOST (model x config) combos (>={floor} of {n_combos}):")
    for (sid, ckpt, fid), recs in ranked:
        n = len(recs)
        if n < floor:
            break
        kinds = {r["kind"] for r in recs}
        models = {r["model"] for r in recs}
        configs = {r["config"] for r in recs}
        gold = recs[0]["gold"]
        emitted_vals = {json.dumps(r["emitted"], default=str) for r in recs}
        print(f"{sid} c{ckpt} {fid}: n={n} kinds={kinds} "
              f"models={len(models)}/{n_models} configs={len(configs)}/{n_configs} gold={gold!r}")
        for ev in sorted(emitted_vals):
            print(f"      emitted={ev}")

if __name__ == "__main__":
    main()
