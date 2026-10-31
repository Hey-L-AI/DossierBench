"""Distribution *shape* diagnostic — persona vs twin, apart from the JSD score.

Runnable as `python3 -m persona_twin.shape` (stdlib only, no model calls). It reads the
cached SUT emissions in `work/sut-emissions/` and, for the three distribution fields,
compares each twin's produced distribution to the persona's on shape rather than on
agreement.

Why this exists separately from the scorer. 1 − JSD is scale-compressed (see
`metrics.jensen_shannon_divergence` and `metrics.weak_abstention_credit`): a spike on the
right mode scores almost as well as the true spread, so the headline score is nearly blind
to whether a twin produced a distribution at all. Measured on the first scored emissions,
the SUTs post a mean 1 − JSD on `price_band_distribution` at parity with a perfect
occasion-counter (~0.87) while reaching it with a single-bin spike **48%** of the time and
a mean support of 1.66 bins against the persona's ~3. The mean hid a categorical
difference in kind. This diagnostic restores the shape axis. See
`docs/distribution-shape.md`.

Nothing here feeds `scorer.py`; it is read-only reporting over runs that already happened.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import data, fields, metrics, sut

#: Defaults, resolved in `main` rather than at import so importing this module never touches the
#: disk or the network: the emission cache `scripts/score.py` writes (`work/sut-emissions`), and
#: the published credited answer key (`data.corpus("key")`).
DEFAULT_EMISSIONS = None
DEFAULT_CORPUS = None

#: The card-bearing cache file per checkpoint. `memory` commits its card in the integrate
#: phase; the prompted family emits it whole. Anything else (extract phases) is skipped.
_EMIT_RE = re.compile(r"^(gen-\d+)-c(\d+)\.json$")
_INTEGRATE_RE = re.compile(r"^(gen-\d+)-c(\d+)-integrate\.json$")

DIST_FIELDS: tuple[str, ...] = tuple(
    f.field_id for f in fields.ALL_FIELDS if f.metric == fields.METRIC_JSD
)


# --------------------------------------------------------------------------- loading


@dataclass(frozen=True)
class TwinDist:
    """One twin's best committed distribution for one subject/field, beside the gold."""

    subject_id: str
    field_id: str
    twin: Mapping[str, float]
    gold: Mapping[str, float]


def _card_from_raw(submission: str, raw: str, subject_id: str):
    parse = sut.parse_integration if submission == "memory" else sut.parse_emission
    return parse(raw, subject_id, None).card


def _checkpoint_files(model_dir: Path, submission: str) -> dict[tuple[str, int], Path]:
    pat = _INTEGRATE_RE if submission == "memory" else _EMIT_RE
    out: dict[tuple[str, int], Path] = {}
    for fp in model_dir.glob("*.json"):
        if fp.name.startswith("._"):
            continue
        m = pat.match(fp.name)
        if m:
            out[(m.group(1), int(m.group(2)))] = fp
    return out


def best_committed(
    model_dir: Path, submission: str, gold_by_subject: Mapping[str, Mapping]
) -> tuple[list[TwinDist], dict[str, tuple[int, int]]]:
    """The highest-checkpoint committed distribution per subject/field, plus abstain tally.

    Returns the committed distributions and, per field, ``(committed, total)`` subject
    counts — a twin that never commits a distribution for a subject is abstaining, which
    the shape view reports rather than dropping.
    """
    by_subject: dict[str, dict[int, Path]] = {}
    for (subject_id, cp), fp in _checkpoint_files(model_dir, submission).items():
        by_subject.setdefault(subject_id, {})[cp] = fp

    dists: list[TwinDist] = []
    commit = {f: [0, 0] for f in DIST_FIELDS}
    for subject_id, cps in by_subject.items():
        gold = gold_by_subject.get(subject_id)
        if gold is None:
            continue
        cards = {cp: _safe_card(submission, fp, subject_id) for cp, fp in cps.items()}
        for field_id in DIST_FIELDS:
            commit[field_id][1] += 1
            for cp in sorted(cps, reverse=True):
                card = cards[cp]
                if card is not None and card.committed(field_id):
                    dists.append(
                        TwinDist(subject_id, field_id, card.get(field_id), gold[field_id])
                    )
                    commit[field_id][0] += 1
                    break
    return dists, {f: (c[0], c[1]) for f, c in commit.items()}


def _safe_card(submission: str, fp: Path, subject_id: str):
    try:
        raw = json.loads(fp.read_text())["raw"]
        return _card_from_raw(submission, raw, subject_id)
    except Exception:
        return None


def discover_runs(emissions: Path) -> list[tuple[str, str, Path]]:
    """`(submission, model, model_dir)` for every cached run, in a stable order."""
    runs: list[tuple[str, str, Path]] = []
    for submission in sorted(p.name for p in emissions.iterdir() if p.is_dir()):
        if submission.startswith("."):
            continue
        for model_dir in sorted((emissions / submission).iterdir()):
            if not model_dir.is_dir() or model_dir.name.startswith("."):
                continue
            runs.append((submission, model_dir.name.replace("__", "/"), model_dir))
    return runs


# --------------------------------------------------------------------------- rendering

_BAR_WIDTH = 14


def _bar(mass: float) -> str:
    filled = round(mass * _BAR_WIDTH)
    return ("█" * filled if filled else "·").ljust(_BAR_WIDTH)


def render_pair(field_id: str, gold: Mapping[str, float], twin: Mapping[str, float],
                twin_label: str) -> str:
    """A side-by-side ASCII histogram of the persona and the twin over a field's bins."""
    bins = fields.BY_ID[field_id].levels
    gs = metrics.distribution_shape(field_id, gold)
    ts = metrics.distribution_shape(field_id, twin)
    label_w = max(len(b) for b in bins)
    lines = [f"{field_id}", f"{'':<{label_w}}   {'persona':<{_BAR_WIDTH + 5}}  {twin_label}"]
    for b in bins:
        g, t = float(gold.get(b, 0.0)), float(twin.get(b, 0.0))
        spike = "  <- spike" if t >= metrics.SPIKE_THRESHOLD else ""
        lines.append(f"{b:<{label_w}}   {_bar(g)} {g:.2f}   {_bar(t)} {t:.2f}{spike}")
    jsd = metrics.distribution_agreement(field_id, twin, gold)
    lines.append(
        f"{'':<{label_w}}   support {gs.support} vs {ts.support}  |  "
        f"spike: {'yes' if ts.is_spike else 'no '}  |  "
        f"zero-bins {gs.zero_bins} vs {ts.zero_bins}  |  1-JSD {jsd:.2f}"
    )
    return "\n".join(lines)


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def render_summary(rows: list[tuple[str, str, list[TwinDist], dict[str, tuple[int, int]]]]
                   ) -> str:
    """Aggregate shape table: persona vs twin support/entropy, spike & abstain rates."""
    out: list[str] = []
    # One persona reference block, computed once over whatever subjects appear.
    out.append("Shape is measured apart from 1-JSD (the score is near-blind to it).")
    out.append(f"support = bins over {metrics.SUPPORT_THRESHOLD:.0%} mass; "
               f"spike = one bin >= {metrics.SPIKE_THRESHOLD:.0%}.\n")
    header = (f"{'submission':10s} {'model':11s} {'field':10s} "
              f"{'supp g/t':>9s} {'entropy g/t':>13s} {'spike%':>7s} {'abstain%':>8s} {'n':>4s}")
    out.append(header)
    out.append("-" * len(header))
    for submission, model, dists, commit in rows:
        by_field: dict[str, list[TwinDist]] = {f: [] for f in DIST_FIELDS}
        for d in dists:
            by_field[d.field_id].append(d)
        mshort = model.split("/")[-1].split("-")[0]
        for field_id in DIST_FIELDS:
            ds = by_field[field_id]
            gshapes = [metrics.distribution_shape(field_id, d.gold) for d in ds]
            tshapes = [metrics.distribution_shape(field_id, d.twin) for d in ds]
            committed, total = commit[field_id]
            abstain = 100.0 * (total - committed) / total if total else float("nan")
            spike = 100.0 * _mean([1.0 if s.is_spike else 0.0 for s in tshapes]) if tshapes else float("nan")
            out.append(
                f"{submission:10s} {mshort:11s} {_short(field_id):10s} "
                f"{_mean([s.support for s in gshapes]):.1f}/{_mean([s.support for s in tshapes]):<4.1f} "
                f"{_mean([s.entropy for s in gshapes]):.2f}/{_mean([s.entropy for s in tshapes]):<6.2f} "
                f"{spike:6.0f}% {abstain:7.0f}% {len(ds):4d}"
            )
        out.append("")
    return "\n".join(out)


def _short(field_id: str) -> str:
    return field_id.replace("_distribution", "")


# --------------------------------------------------------------------------- cli


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--emissions", type=Path, default=DEFAULT_EMISSIONS)
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--subject", help="print per-bin histograms for this subject id")
    ap.add_argument("--run", help="only this 'submission/model' substring")
    args = ap.parse_args(argv)
    if args.emissions is None:
        args.emissions = data.work() / "sut-emissions"
    if not args.emissions.is_dir():
        raise SystemExit(f"no emission cache at {args.emissions}\n"
                         f"Score a submission first: python scripts/score.py --sut prompted:<model>")
    if args.corpus is None:
        args.corpus = data.corpus("key")

    corpus = json.loads(args.corpus.read_text())
    gold_by_subject = {it["subject_id"]: it["card"] for it in corpus["items"]}

    runs = discover_runs(args.emissions)
    if args.run:
        runs = [r for r in runs if args.run in f"{r[0]}/{r[1]}"]

    rows = []
    for submission, model, model_dir in runs:
        dists, commit = best_committed(model_dir, submission, gold_by_subject)
        rows.append((submission, model, dists, commit))

    print(render_summary(rows))

    if args.subject:
        print("=" * 64)
        print(f"per-bin histograms for {args.subject}\n")
        for submission, model, dists, _ in rows:
            for d in dists:
                if d.subject_id == args.subject:
                    print(render_pair(d.field_id, d.gold, d.twin, f"{submission}/{model}"))
                    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
