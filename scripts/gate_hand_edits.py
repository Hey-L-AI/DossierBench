"""Put the hand-authored contradiction candidates through the editor's own model gate.

    python scripts/gate_hand_edits.py --candidates work/hand-edits.json \
        --report work/audit-opus.json --dry-run          # offline, no calls
    python scripts/gate_hand_edits.py --candidates work/hand-edits.json \
        --report work/audit-opus.json --out work/hand-edit-gate.json --apply

`--candidates` is a JSON file `{"candidates": [...]}` holding one hand-written candidate per
unresolved finding (for example, each `contradiction` an `scripts/edit.py` run left behind).
Each is expected to have passed `transcript.check_session` and `editor.beats_survive` offline;
none has yet faced the re-audit gate — the audit call plus
`_regressions` / `_downgrades` / `_collateral` / `_no_progress` — which is what decides whether
a candidate is a repair.

Nothing here authors a candidate. `editor.edit_session` is driven with a stub invoker that
returns the hand-written replacements as the editor model's reply, so the gate that runs is
the shipped one, verbatim, including ADR-0059's transition rule and `MAX_COLLATERAL`. The
only model calls are the gate's own `verify.audit_session`, one per candidate
(`--max-attempts 1`).

A candidate that rewrites fewer messages than the finding names is padded with the store's
own text for the rest: `editor._parse_edit_reply` is strict about rewriting exactly the
messages the finding cites, and splicing a message back over itself is a no-op.

The store is written ONLY with `--apply`. Without it this is a measurement and the page is
left alone.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persona_twin import data  # noqa: E402

ROOT = data.work()  # every artifact a pass writes lands here

from persona_twin import author, editor, transcript, verify  # noqa: E402
from persona_twin.judge import ClaudeCli  # noqa: E402


def _load_findings(path: Path) -> list[verify.Finding]:
    payload = json.loads(path.read_text())
    return [
        verify.Finding(
            kind=f["kind"], severity=f["severity"], task_id=f["task_id"],
            detail=f["detail"], field_id=f.get("field_id", ""), quote=f.get("quote", ""),
            turn_index=f.get("turn_index"), speaker=f.get("speaker", ""),
            messages=tuple(f.get("messages") or ()),
            removable_messages=tuple(f.get("removable") or ()),
        )
        for f in payload["findings"]
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", type=Path, default=data.local_corpus())
    ap.add_argument("--store", type=Path, default=data.authored_store())
    ap.add_argument("--candidates", type=Path, required=True,
                    help='hand-written candidates, as {"candidates": [...]}')
    ap.add_argument("--report", type=Path, required=True,
                    help="the reader report the candidates were written against; also the "
                         "gate baseline")
    ap.add_argument("--verify-model", default=verify.DEFAULT_MODEL)
    ap.add_argument("--max-attempts", type=int, default=1)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--apply", action="store_true",
                    help="write an accepted candidate back to the store")
    ap.add_argument("--apply-suggested", action="store_true",
                    help="ALSO write a candidate the gate refused ONLY on price — every "
                         "rejection at `collateral`, i.e. the target defect gone and nothing "
                         "introduced that `credit` cannot settle, but more than "
                         "editor.MAX_COLLATERAL leaks on other fields. This is the one "
                         "refusal the cap defers to a person by design (see "
                         "editor.MAX_COLLATERAL and scripts/edit.py --suggestions-out). The "
                         "price is written to the edit log as `collateral` and the entry is "
                         "marked `owner_override`, because the independent audit cannot tell "
                         "an editor-minted leak from an author-written one and this log is "
                         "the only place that distinction lives")
    ap.add_argument("--dry-run", action="store_true",
                    help="offline only: splice, check_session, beats_survive, and whether "
                         "the spliced page matches the recorded candidate_transcript")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:                                    # pragma: no cover - defensive
        pass

    candidates = json.loads(args.candidates.read_text())["candidates"]
    findings = [f for f in _load_findings(args.report) if f.severity == verify.BLOCKING]
    by_task: dict[str, list[verify.Finding]] = {}
    for f in findings:
        by_task.setdefault(f.task_id, []).append(f)
    corpus = json.loads(args.corpus.read_text())
    items = {str(i["subject_id"]): i
             for i in (corpus["items"] if isinstance(corpus, dict) else corpus)}
    records = verify.load_store(args.store)
    store = author.TranscriptStore(args.store.parent)

    results = []
    for cand in candidates:
        task_id, field_id = cand["task_id"], cand["field_id"]
        record = records.get(task_id)
        if record is None:
            print(f"{task_id}: NOT IN STORE")
            continue
        item = items[task_id.rsplit("-s", 1)[0]]
        matches = [f for f in by_task.get(task_id, [])
                   if f.field_id == field_id and f.kind == cand["kind"]]
        if len(matches) != 1:
            print(f"{task_id} {field_id}: {len(matches)} matching finding(s) in the report")
            continue
        finding = matches[0]
        text = str(record.get("transcript") or "")

        # Pad the reply to exactly the messages the finding names — see the module docstring.
        replacements = {int(k): v for k, v in cand["replacements"].items()}
        reply: dict[int, str] = {}
        missing = []
        for number in finding.targets:
            if number in replacements:
                reply[number] = replacements[number]
                continue
            loc = transcript.locate_message(text, number)
            if loc is None:
                missing.append(number)
                continue
            reply[number] = text[loc.start:loc.end].split(":", 1)[-1].strip()
        if missing:
            print(f"{task_id} {field_id}: message(s) {missing} not in the store text")
            continue
        extra = sorted(set(replacements) - set(finding.targets))
        if extra:
            print(f"{task_id} {field_id}: candidate rewrites {extra}, which the finding "
                  f"does not name — the gate would refuse the reply")
            continue

        locators = [transcript.locate_message(text, n) for n in finding.targets]
        locators = [loc for loc in locators if loc is not None]
        locators.sort(key=lambda loc: loc.start)
        spliced, _remap = transcript.apply_message_edits(
            text, [(loc, reply[loc.turn_index]) for loc in locators]
        )
        recorded = str(cand.get("candidate_transcript") or "").strip()
        same = recorded == spliced.strip()
        problems = transcript.check_session(
            spliced, self_report=record.get("self_report"),
            beats=record.get("beats") or (), require_numbering=True,
            free_offers=editor._remapped_escapes(record),
        )
        lost = editor.beats_survive(text, spliced, record.get("beats") or ())
        padded = sorted(set(finding.targets) - set(replacements))
        print(f"{task_id} {field_id}: names {list(finding.targets)}, rewrites "
              f"{sorted(replacements)}{f', pads {padded}' if padded else ''}; "
              f"matches recorded candidate: {same}; check_session: "
              f"{'pass' if not problems else problems}; beats_survive: "
              f"{'pass' if not lost else lost}; deletable: {finding.deletable}")
        if args.dry_run:
            results.append({"task_id": task_id, "field_id": field_id,
                            "matches_recorded_candidate": same,
                            "check_session": [str(p) for p in problems],
                            "beats_lost": lost, "padded": padded})
            continue

        def stub(model: str, system: str, prompt: str, _reply=reply) -> str:
            return json.dumps({"messages": {str(k): v for k, v in _reply.items()}})

        baseline = by_task.get(task_id, [])
        result, patched = editor.edit_session(
            stub, "hand-authored", record, item, finding,
            verify_invoke=ClaudeCli(), verify_model=args.verify_model,
            max_attempts=args.max_attempts, baseline=baseline,
        )
        print(f"  -> {result.outcome}"
              + (f", collateral={[c['field_id'] for c in result.collateral]}"
                 if result.collateral else "")
              + (f", traded={[c['field_id'] for c in result.traded]}"
                 if result.traded else "")
              + (f", suggested" if result.suggested else "")
              + (f"\n     rejected: "
                 + "; ".join(f"{r['gate']}: {r['why']}" for r in result.rejected)
                 if result.rejected else ""))
        entry = result.to_json()
        entry["strategy"] = cand.get("strategy")
        entry["confidence"] = cand.get("confidence")
        # Who asked for this change, and why. A hand candidate is somebody's decision and the
        # edit log is the only place that survives the conversation it was made in.
        entry["decided_by"] = cand.get("decided_by")
        entry["decided_at"] = cand.get("decided_at")
        entry["owner_note"] = cand.get("note")
        entry["applied"] = bool(args.apply and patched is not None)

        # The one refusal a person is entitled to overturn: refused ONLY for costing more
        # than the cap. Spliced here from the recorded suggestion rather than re-gated, so
        # what lands is byte-for-byte the candidate the gate judged.
        priced_out = (
            patched is None and result.suggested
            and all(r["gate"] == "collateral" for r in result.rejected)
        )
        if priced_out and args.apply_suggested:
            pick = result.suggested[0]
            reply2 = {int(k): v for k, v in pick["proposed"].items()}
            locs2 = [transcript.locate_message(text, n) for n in sorted(reply2)]
            locs2 = [loc for loc in locs2 if loc is not None]
            spliced2, _r2 = transcript.apply_message_edits(
                text, [(loc, reply2[loc.turn_index]) for loc in locs2]
            )
            patched = {**record, "transcript": spliced2}
            entry["applied"] = True
            entry["outcome"] = "fixed"
            entry["owner_override"] = (
                f"refused only at `collateral` ({len(pick['collateral'])} leaks against "
                f"MAX_COLLATERAL={editor.MAX_COLLATERAL}); applied by "
                f"{cand.get('decided_by') or 'owner'} on {cand.get('decided_at') or datetime.date.today().isoformat()}"
            )
            entry["collateral"] = pick["collateral"]
            entry["proposed"] = " / ".join(pick["proposed"][k] for k in sorted(pick["proposed"]))
            cost = ", ".join(f"{c['kind']}/{c['field_id']}" for c in pick["collateral"])
            print(f"     OWNER OVERRIDE: applied over the cap, cost {cost}")

        results.append(entry)
        if patched is not None:
            records[task_id] = patched
            if args.apply or entry.get("owner_override"):
                store.write(patched)
                print(f"     written to {args.store}")
        if args.out:
            args.out.write_text(json.dumps({"edits": results}, indent=2))

    if args.out:
        args.out.write_text(json.dumps({"edits": results}, indent=2))
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":                                    # pragma: no cover
    raise SystemExit(main())
