"""Weak-band discernment diagnostic — how well a SUT reads soft evidence, apart from score.

Runnable as `python3 -m persona_twin.discern` (stdlib only, no model calls). It reads the
cached SUT emissions in `work/sut-emissions/` and, for every WEAK preference field-checkpoint,
asks the question the score is deliberately blind to: when the evidence only *hints* at a
value, does the SUT commit and read it correctly?

Why this exists separately from the scorer. On the weak band the scorer neither rewards nor
punishes a posture — committing scores the value's agreement, abstaining scores `WEAK_CREDIT`,
and the two are calibrated to be equal for a system reading exactly what the evidence licenses
(see `metrics.weak_abstention_credit`). That is the correct scoring rule, and it stays. But it
means two systems can post the same `A(t)` on the weak band — one by abstaining everywhere and
banking the credit, the other by committing and reading the soft signal right — and the score
cannot tell them apart. This restores that axis. See `docs/weak-discernment.md`.

The confound the metric is built around, and the two reference points it reports
(`discernment` vs a base-rate guesser, `calibration` vs the licensed indifference point), are
documented on `metrics.weak_discernment`. The base-rate line is `ChanceModel`: population-mode
for ordinal and distribution fields, and the best constant set (`best_population_set`) for the
Jaccard fields where the mode collapses to the empty set. Both are fit on the **public** split
only — fitting on the split being scored would leak ground truth.

Nothing here feeds `scorer.py`; it is read-only reporting over runs that already happened, and
it reuses `scorer.score_checkpoint` for the grade/agreement of every cell so it cannot drift
from what the scorer actually computed.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import baselines, data, fields, metrics, scorer, shape, sut
from .card import PersonaCard
from .evidence import Grade
from .fields import UNKNOWN

#: Defaults, resolved in `main` rather than at import so importing this module never touches the
#: disk or the network: the emission cache `scripts/score.py` writes (`work/sut-emissions`), and
#: the published credited answer key (`data.corpus("key")`).
DEFAULT_EMISSIONS = None
DEFAULT_CORPUS = None

_PREF_SET = frozenset(fields.PREFERENCE_IDS)
#: The preference fields scored by Jaccard — where the population-mode base rate collapses to
#: the empty set and a stronger set guess is used instead (see `best_population_set`).
_SET_PREF_IDS = tuple(
    fid for fid in fields.PREFERENCE_IDS if fields.BY_ID[fid].metric == fields.METRIC_JACCARD
)

#: Preference metric families, in a stable print order. Constraints are excluded — they never
#: enter the weak band's agreement track (they gate separately, `scorer._score_constraints`).
_FAMILIES: tuple[tuple[str, str], ...] = (
    ("ordinal", fields.METRIC_ORDINAL),
    ("set", fields.METRIC_JACCARD),
    ("dist", fields.METRIC_JSD),
)


# --------------------------------------------------------------------------- collection


@dataclass
class RunCells:
    """Every weak cell a run produced, plus the strong-band accuracy for context."""

    submission: str
    model: str
    weak: list[metrics.WeakCell]
    weak_by_family: dict[str, list[metrics.WeakCell]]
    strong_committed_agreements: list[float]


def best_population_set(gold_sets: Sequence[frozenset[str]]) -> list[str]:
    """The constant set that maximises mean Jaccard against a population of gold sets.

    This is the honest base rate for a Jaccard field: the best a guesser can do knowing only
    the population, not the individual. `baselines.PopulationMode` takes the per-member majority
    (members in >50% of cards), which over a large vocabulary is almost always the empty set —
    a base rate that scores 0.000 against every non-empty gold and so makes the discernment
    lift on sets meaningless (`baselines._modal_value` says as much). A member that appears in,
    say, 40% of people still *raises* mean Jaccard by being guessed, and this finds exactly the
    set of such members by greedy hill-climb over single-member toggles. Deterministic: the
    vocabulary is sorted and a toggle is taken only on a strict improvement, so ties never flip
    the result. Fit on the public split only, like any base rate (spec §8.8).
    """
    if not gold_sets:
        return []
    vocab = sorted(set().union(*gold_sets))

    def mean_jaccard(candidate: set[str]) -> float:
        return sum(metrics.jaccard(candidate, g) for g in gold_sets) / len(gold_sets)

    current: set[str] = set()
    best = mean_jaccard(current)
    improved = True
    while improved:
        improved = False
        best_member, best_value = None, best
        for member in vocab:
            value = mean_jaccard(current ^ {member})
            if value > best_value + 1e-12:
                best_member, best_value = member, value
        if best_member is not None:
            current ^= {best_member}
            best, improved = best_value, True
    return sorted(current)


@dataclass(frozen=True)
class ChanceModel:
    """The strongest population-only guess per field, used as the base-rate line.

    ``mode`` (population-mode) guesses ordinal and distribution fields; ``set_guess`` overrides
    the Jaccard preference fields with `best_population_set`, because the mode's per-member
    majority collapses to the empty set there and scores a spurious 0.000 (see caveat 1 in
    `docs/weak-discernment.md`). Both are fit on the public split only.
    """

    mode: baselines.PopulationMode
    set_guess: Mapping[str, list[str]]

    def agreement(self, field_id: str, gold: PersonaCard) -> float | None:
        """The base-rate guesser's ``a_f`` on one cell, or ``None`` if it cannot score it."""
        truth = gold.get(field_id)
        if truth == UNKNOWN:
            return None
        if field_id in self.set_guess:
            return metrics.agreement(field_id, self.set_guess[field_id], truth)
        guess = self.mode.card_values.get(field_id, UNKNOWN)
        if guess == UNKNOWN:
            return None
        return metrics.agreement(field_id, guess, truth)


#: metric constant -> family label, for the per-family slice.
_LABEL_OF_METRIC = {metric: name for name, metric in _FAMILIES}


def cells_from_checkpoint(
    subject: "sut.Subject",
    checkpoint: int,
    card: PersonaCard,
    chance: ChanceModel,
) -> tuple[list[tuple[str, metrics.WeakCell]], list[float]]:
    """One emitted card's ``(family, weak cell)`` list and its committed-STRONG agreements.

    Grade and ``a_f`` come from `scorer.score_checkpoint`, so this diagnostic reads exactly what
    the scorer computed off its `FieldOutcome`s rather than recomputing agreement — it cannot
    disagree with the published score. Pure and disk-free, so the whole reduction is testable
    without a run on disk.
    """
    scored = scorer.score_checkpoint(subject, checkpoint, card)
    gold = subject.gold_at(checkpoint)
    weak: list[tuple[str, metrics.WeakCell]] = []
    strong: list[float] = []
    for outcome in scored.outcomes:
        if outcome.field_id not in _PREF_SET:
            continue
        if outcome.grade == Grade.STRONG and outcome.committed:
            if outcome.agreement is not None:
                strong.append(outcome.agreement)
            continue
        if outcome.grade != Grade.WEAK:
            continue
        family = _LABEL_OF_METRIC.get(fields.BY_ID[outcome.field_id].metric)
        if family is None:
            continue
        weak.append((family, metrics.WeakCell(
            committed=outcome.committed,
            agreement=outcome.agreement if outcome.committed else None,
            chance_agreement=chance.agreement(outcome.field_id, gold),
        )))
    return weak, strong


def collect_run(
    model_dir: Path,
    submission: str,
    subjects: Mapping[str, "sut.Subject"],
    chance: ChanceModel,
) -> tuple[list[metrics.WeakCell], dict[str, list[metrics.WeakCell]], list[float]]:
    """Weak cells (overall and by family) and strong-committed agreements for one run."""
    files = shape._checkpoint_files(model_dir, submission)
    by_subject: dict[str, dict[int, Path]] = {}
    for (subject_id, cp), fp in files.items():
        by_subject.setdefault(subject_id, {})[cp] = fp

    weak: list[metrics.WeakCell] = []
    by_family: dict[str, list[metrics.WeakCell]] = {name: [] for name, _ in _FAMILIES}
    strong: list[float] = []
    for subject_id, cps in by_subject.items():
        subject = subjects.get(subject_id)
        if subject is None:
            continue
        count = subject.evidence.checkpoint_count
        for cp, fp in cps.items():
            if not 0 <= cp < count:
                continue
            card = shape._safe_card(submission, fp, subject_id)
            if card is None:
                continue
            cells, strong_cells = cells_from_checkpoint(subject, cp, card, chance)
            strong.extend(strong_cells)
            for family, cell in cells:
                weak.append(cell)
                by_family[family].append(cell)
    return weak, by_family, strong


def load_subjects(corpus: Path | str) -> dict[str, "sut.Subject"]:
    """Every corpus item as a scorer `Subject` (gold + evidence map + scripted events)."""
    return {sid: sut.scorer_subject(item) for sid, item in sut.load_items(corpus).items()}


def public_golds(corpus: Path | str) -> list[PersonaCard]:
    """Gold cards from the public split — the only split a base rate may be fit on (§8.8)."""
    return [
        PersonaCard(sid, dict(item["card"]))
        for sid, item in sut.load_items(corpus).items()
        if (item.get("split") or "public") == "public"
    ]


def build_chance_model(golds: Sequence[PersonaCard]) -> ChanceModel:
    """The base-rate line: population-mode everywhere, best-population-set on Jaccard fields."""
    mode = baselines.PopulationMode.fit(list(golds))
    set_guess = {
        fid: best_population_set(
            [frozenset(g.get(fid)) for g in golds if g.committed(fid)]
        )
        for fid in _SET_PREF_IDS
    }
    return ChanceModel(mode, set_guess)


# --------------------------------------------------------------------------- rendering


def _fmt(value: float | None, places: int = 3, width: int = 7) -> str:
    if value is None:
        return f"{'—':>{width}}"
    return f"{value:>{width}.{places}f}"


def _pct(value: float | None, width: int = 6) -> str:
    if value is None:
        return f"{'—':>{width}}"
    return f"{100.0 * value:>{width - 1}.0f}%"


def _signed(value: float | None, width: int = 7) -> str:
    if value is None:
        return f"{'—':>{width}}"
    return f"{value:>+{width}.3f}"


def render(rows: Sequence[RunCells]) -> str:
    """The discernment table: one block per run, an overall line then per-family lines."""
    out: list[str] = []
    out.append(
        "Weak-band discernment: how well the SUT reads soft evidence, apart from what the "
        "score pays."
    )
    out.append(
        f"acc = mean a_f | committed.  chance = strongest population-only guess on the same "
        f"cells (mode; best population set for sets).  credit = {metrics.WEAK_CREDIT:.3f} (indifference)."
    )
    out.append(
        "discern = acc − chance (>0: reads more than the prior).  calib = acc − credit "
        "(>0: should commit).  strong = mean a_f on committed STRONG cells, for scale.\n"
    )
    header = (
        f"{'submission':10s} {'model':11s} {'band':7s} "
        f"{'n':>5s} {'commit':>7s} {'acc':>7s} {'chance':>7s} "
        f"{'discern':>8s} {'calib':>8s} {'wrong':>6s} {'strong':>7s}"
    )
    out.append(header)
    out.append("-" * len(header))
    for row in rows:
        mshort = row.model.split("/")[-1].split("-")[0]
        strong_acc = (
            sum(row.strong_committed_agreements) / len(row.strong_committed_agreements)
            if row.strong_committed_agreements
            else None
        )
        blocks = [("all", row.weak)] + [
            (name, row.weak_by_family[name]) for name, _ in _FAMILIES
        ]
        for i, (band, cells) in enumerate(blocks):
            wd = metrics.weak_discernment(
                cells, error_threshold=scorer.ERROR_THRESHOLD
            )
            head = (
                f"{row.submission:10s} {mshort:11s}"
                if i == 0
                else f"{'':10s} {'':11s}"
            )
            strong_col = _fmt(strong_acc) if i == 0 else f"{'':>7s}"
            out.append(
                f"{head} {band:7s} "
                f"{wd.n:>5d} {_pct(wd.commit_rate):>7s} {_fmt(wd.commit_accuracy):>7s} "
                f"{_fmt(wd.chance_accuracy):>7s} {_signed(wd.discernment):>8s} "
                f"{_signed(wd.calibration):>8s} {_pct(wd.wrong_rate):>6s} {strong_col:>7s}"
            )
        out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------- cli


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--emissions", type=Path, default=DEFAULT_EMISSIONS)
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--run", help="only this 'submission/model' substring")
    args = ap.parse_args(argv)
    if args.emissions is None:
        args.emissions = data.work() / "sut-emissions"
    if not args.emissions.is_dir():
        raise SystemExit(f"no emission cache at {args.emissions}\n"
                         f"Score a submission first: python scripts/score.py --sut prompted:<model>")
    if args.corpus is None:
        args.corpus = data.corpus("key")

    subjects = load_subjects(args.corpus)
    chance = build_chance_model(public_golds(args.corpus))

    runs = shape.discover_runs(args.emissions)
    if args.run:
        runs = [r for r in runs if args.run in f"{r[0]}/{r[1]}"]

    rows: list[RunCells] = []
    for submission, model, model_dir in runs:
        weak, by_family, strong = collect_run(model_dir, submission, subjects, chance)
        rows.append(RunCells(submission, model, weak, by_family, strong))

    print(render(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
