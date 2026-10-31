"""Pre-freeze editor pass: turn-level repair driven by a `verify.py` report's own findings.

    python scripts/edit.py --report work/audit-absolved.json --dry-run
    python scripts/edit.py --report work/audit-absolved.json \\
        --baseline-report work/audit-opus.json --edits-out work/edits.json --workers 4

Reads a `Report` written by `scripts/audit.py --json` (or any `verify.inspect`/`verify.audit`
output). For every `BLOCKING` finding whose turn `verify._located` could resolve
(`finding.locatable`), it asks a model to rewrite just that turn, gates the candidate through
`transcript.check_session` and a scoped re-audit, and writes the session back to `--store` only
if both pass — see `persona_twin/editor.py` for what the gate does and why. A finding with no
resolvable turn (an unmatched quote, or a beat that was never delivered at all — nothing to
splice, an insertion would be needed instead) is left alone and printed for a human.

Sessions are repaired in parallel (`--workers`, default 4). The unit is the page, never
the finding: a repair's two order-dependent inputs — the pre-edit `baseline` the gate needs,
and the `remap` a deletion forces onto findings queued behind it — are both scoped to one
session, so findings on different pages were already independent in the serial version.

**This script does not produce the report a score is based on.** Run `scripts/audit.py`
again over the full corpus afterward — that independent run, not this one's internal re-audit
calls, is what ADR-0009/0010's boundary asks for.

`--corpus` and `--store` default to `work/corpus.json` and `work/authored/claude-opus-5`, the
same as `scripts/audit.py`. See `docs/PIPELINE.md` §5.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persona_twin import data  # noqa: E402

ROOT = data.work()  # every artifact a pass writes lands here

from persona_twin import author, editor, verify  # noqa: E402
from persona_twin.judge import ClaudeCli  # noqa: E402

DEFAULT_CORPUS = data.local_corpus()
DEFAULT_STORE = data.authored_store()


def _load_findings(report_path: Path) -> list[verify.Finding]:
    payload = json.loads(report_path.read_text())
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


def _load_items(corpus: Path) -> dict[str, dict]:
    payload = json.loads(corpus.read_text())
    items = payload["items"] if isinstance(payload, dict) else payload
    return {str(item["subject_id"]): item for item in items}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--store", type=Path, default=DEFAULT_STORE,
                     help="the per-author transcript directory, e.g. work/authored/claude-opus-5")
    ap.add_argument("--report", type=Path, required=True,
                     help="a verify.Report JSON — e.g. scripts/audit.py --json. This is the "
                          "WORK LIST: what gets attempted.")
    ap.add_argument("--baseline-report", type=Path, default=None,
                     help="the UNCREDITED report, when --report has been through "
                          "credit.absolve. The accept/reject gate re-audits candidates "
                          "without credit applied, so it has to be told what the page "
                          "already carried in those same terms or it scores every "
                          "pre-existing leak as one the rewrite introduced. Defaults to "
                          "--report.")
    ap.add_argument("--editor", default="claude-opus-5", help="model that rewrites a turn")
    ap.add_argument("--verify-model", default=verify.DEFAULT_MODEL,
                     help="model that re-audits a candidate before it is accepted")
    ap.add_argument("--edits-out", type=Path, default=None)
    ap.add_argument("--suggestions-out", type=Path, default=None,
                     help="candidates that cleared every gate except editor.MAX_COLLATERAL "
                          "— the target defect gone, nothing introduced that `credit` "
                          "cannot settle, but more than the cap's worth of new leaks on "
                          "other fields. The page is left alone and the candidate is "
                          "written here for a person to apply or refuse. Also present "
                          "under `suggested` in --edits-out.")
    ap.add_argument("--workers", type=int, default=editor.DEFAULT_WORKERS,
                     help="sessions repaired at the same time. A page's own findings "
                          "always run in order; only different pages overlap.")
    ap.add_argument("--dry-run", action="store_true",
                     help="print what would be attempted; no model calls, no writes")
    args = ap.parse_args(argv)
    # Line-buffered: redirected to a file, stdout is block-buffered by default and a killed
    # run loses its entire log. This one did.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:                                    # pragma: no cover - defensive
        pass

    findings = _load_findings(args.report)
    blocking = [f for f in findings if f.severity == verify.BLOCKING]
    locatable = [f for f in blocking if f.locatable]
    skipped = [f for f in blocking if not f.locatable]

    print(f"{len(blocking)} blocking finding(s) in the report: "
          f"{len(locatable)} locatable, {len(skipped)} need a human (no turn to patch).")
    for f in skipped:
        print(f"  skip: {f}")
    for f in locatable:
        print(f"  {'would attempt' if args.dry_run else 'attempting'}: {f}")

    if args.dry_run or not locatable:
        return 0

    items = _load_items(args.corpus)
    records = verify.load_store(args.store)
    store = author.TranscriptStore(args.store.parent)

    results: list[editor.EditResult] = []

    def on_session(task_id: str, out, log) -> None:
        """Print one page's log contiguously, and re-write the edit log after every page.

        A run that is interrupted — killed, a transport wedge, a laptop lid — otherwise
        leaves a store with edits in it and no artifact saying what they were. That happened:
        a killed run wrote 6 sessions and produced a zero-byte log and no `--edits-out`, and
        the only way to find out what had changed was to diff the store against a snapshot
        taken beforehand. A dataset that is about to be frozen must never depend on that.

        Called under `repair_corpus`'s lock, so the lines of one session never interleave
        with another's — worth having when four pages are being repaired at once.
        """
        results.extend(out)
        if log:
            print("\n".join(log), flush=True)
        if args.edits_out:
            args.edits_out.write_text(
                json.dumps({"edits": [r.to_json() for r in results]}, indent=2)
            )

    sessions = len({f.task_id for f in locatable})
    print(f"\n{sessions} session(s) to repair, {args.workers} worker(s). Findings on one "
          "page run in order; different pages run at the same time.\n")

    baseline_findings = None
    if args.baseline_report:
        baseline_findings = [
            f for f in _load_findings(args.baseline_report) if f.severity == verify.BLOCKING
        ]
        print(f"gate baseline: {len(baseline_findings)} blocking finding(s) from "
              f"{args.baseline_report.name}, uncredited.\n")

    ordered = editor.repair_corpus(
        ClaudeCli(), args.editor, blocking,
        records=records, items=items, write=store.write,
        baseline_findings=baseline_findings,
        verify_model=args.verify_model, workers=args.workers,
        on_session=on_session,
    )

    if args.edits_out:
        args.edits_out.write_text(
            json.dumps({"edits": [r.to_json() for r in ordered]}, indent=2)
        )
        print(f"\nwrote {args.edits_out}")

    suggestions = [
        {"task_id": r.task_id, "field_id": r.field_id, "kind": r.kind,
         "messages": list(r.messages), "original": r.original, **entry}
        for r in ordered for entry in r.suggested
    ]
    if args.suggestions_out:
        args.suggestions_out.write_text(
            json.dumps({"suggestions": suggestions}, indent=2)
        )
    if suggestions:
        # Printed whether or not --suggestions-out was passed. A candidate that was refused
        # on price rather than on correctness is the one refusal a person can overturn, and
        # a run that does not say so out loud is a run where nobody finds out.
        print(f"\n{len(suggestions)} candidate(s) refused ONLY for costing more than "
              f"MAX_COLLATERAL={editor.MAX_COLLATERAL} — a person decides these:")
        for entry in suggestions:
            cost = ", ".join(sorted(
                f"{c['kind']}/{c['field_id']}" for c in entry["collateral"]
            ))
            print(f"  {entry['task_id']} {entry['field_id']} ({entry['kind']}) "
                  f"attempt {entry['attempt']} would cost {len(entry['collateral'])}: {cost}")
        if args.suggestions_out:
            print(f"  wrote {args.suggestions_out}")

    traded = sum(1 for r in ordered if r.traded)
    collateral = sum(len(r.collateral) for r in ordered)
    if traded or collateral:
        print(f"\n{traded} accepted repair(s) traded the defect for another kind on the "
              f"same field; {collateral} new absolvable finding(s) landed on other fields "
              "and are recorded as `collateral`. Both are for the credit pass to settle, "
              "and the independent audit cannot tell either from something the author "
              "wrote — this log is where that distinction lives.")

    fixed = sum(1 for r in ordered if r.outcome == "fixed")
    deleted = sum(1 for r in ordered if r.outcome == "deleted")
    neutral = [r for r in ordered if r.outcome == "neutral"]
    if neutral:
        # Reported apart from `unresolved` because they are different work orders: an
        # unresolved repair is one to try again, a neutral one is a finding no rewrite can
        # win. Counting them together would put the second back in a queue forever.
        print(f"\n{len(neutral)} NEUTRAL — every candidate swapped a leak for a leak "
              f"({editor.TRANSITIONS} are the moves that count). The page is unchanged; "
              "these are for the credit pass or the map, not for another editor run:")
        for r in neutral:
            print(f"  {r.task_id} {r.field_id} ({r.kind}), {r.attempts} attempt(s)")
    if deleted:
        # Reported apart from rewrites on purpose. A deletion says the messages should not
        # have been on the page; a rewrite says they were written badly. Which defects are
        # only ever solved by removal is the question the marking was added to answer, and
        # one combined count cannot answer it.
        print(f"\n{deleted} resolved by DELETION (no rewrite call spent proposing them):")
        for r in ordered:
            if r.outcome == "deleted":
                print(f"  {r.task_id} {r.field_id} ({r.kind}) removed messages "
                      f"{list(r.deleted)}")
    print(f"\n{fixed + deleted}/{len(locatable)} resolved "
          f"({fixed} rewritten, {deleted} deleted). "
          f"Re-run scripts/audit.py over the full corpus "
          "before freezing — this script's own re-audit is a gate, not the record.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
