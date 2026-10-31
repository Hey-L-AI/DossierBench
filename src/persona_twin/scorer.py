"""The scorer (spec-v0 §8). This module *is* the benchmark — everything upstream exists to
feed it, and it is built first for that reason (§15 step 1).

Dispatches on ``schema_version``. Stdlib only.

The load-bearing idea, and the one thing here with no counterpart in the surveyed prior art
(see the related-work section of `paper/main.tex`): **what an SUT should say depends on what the transcript so
far can support.** At checkpoint *t* every field sits in one of three evidence bands, and
committing is rewarded, neutral, or penalized depending on the band:

| band | SUT commits          | SUT says "unknown"           |
|------|----------------------|------------------------------|
| S(t) | scored a_f           | scored 0 — a miss            |
| W(t) | scored a_f           | scored `WEAK_CREDIT`         |
| U(t) | **fabrication**      | correct abstention, costs 0  |

One exception to the table (§8.1a, ADR-0065): a **distribution field in W(t)** is excluded
from both columns — neither scored for agreement nor fabrication-eligible. A histogram's shape
needs enough occasions to exist (STRONG onset is the fifth), so a WEAK distribution is scored
against a lifetime shape it cannot yet infer; and a commit there rests on a real (if thin)
observation, so it is not invention. The cell simply drops out of the checkpoint. STRONG and
U(t) distributions follow the table unchanged.

The U(t) row is the sharp one: a committed value scores as fabrication *even when it equals
ground truth*. That is what stops base-rate guessing and stereotype-filling from earning
anything, and it is why the dataset needs the §4.3 anti-correlation quota — without
subjects who violate the trait clusters, an SUT that infers `adventurousness` from
`cuisines_liked` breadth would be charged with fabrication for reasoning correctly, and the
artifact would be indistinguishable from genuine failure.

## The 2026-08-10 rescoring (ADR-0001, -0002, -0003)

Three coupled changes, made after `ablation.py` showed the previous rule rewarded guessing
in the sparse tier and left the weak band unscored. Read the ADRs for the reasoning; the
mechanics:

1. **`W(t)` abstention is credited and stays in the denominator.** It used to be dropped
   from both numerator and denominator, which is not a credit — it is removal from an
   average, so whether it helped depended on how well the SUT was doing on *other* fields.
   Committing on weak now pays iff `a_f > WEAK_CREDIT`, a comparison against the evidence
   and nothing else. Consequence worth knowing: `A(t)`'s denominator is now `|S(t)| + |W(t)|`,
   fixed by the evidence map rather than by SUT behaviour.
2. **The fabrication term is a count over the evidenced band, not a rate over `U(t)`.**
   `fab(t)` as a fraction of `U(t)` made each fabrication cheaper the less the transcript
   evidenced — so the sparse tier, built so abstention is the defensible move, was where
   guessing was cheapest. Pricing fabrications on the same denominator as agreement fixes it
   structurally: the exchange rate between "missed an evidenced field" and "fabricated" is
   `a_f/λ`, independent of the band mix.
3. **λ default 1.0**, reported at {0.5, 1.0, 2.0}. See `DEFAULT_LAMBDA`.

`fab(t)` — the rate — is still computed and reported, because it is the readable diagnostic
("what fraction of the unevidenced card did this system invent?"). It just no longer sets
the penalty.

## The 2026-08-10 density gate (ADR-0004)

`F_raw`'s floor at a checkpoint is `−λ·|U(t)|/|S(t) ∪ W(t)|`, which grows without bound as
the evidenced band thins. ADR-0002 read the −8.7 that produces on the toy subjects as a
defect in the composite and named a bounded-by-construction replacement as the v1 fix.
It is not a defect in the composite: it is a statement about the *item*, and the toys
evidence 2–3 of 20 fields by construction. At every §7.4 tier profile the same rule keeps
`F_raw` inside roughly [−0.67, 1].

So instead of changing the rule, `MIN_EVIDENCED_FOR_AUFC` refuses to *average* a checkpoint
whose evidenced band is too thin to price a fabrication against. Such a checkpoint is still
scored and still published in full — it just does not enter AUFC. See convention 5.

## Five conventions this module fixes, which spec-v0 leaves open

1. **`A(t)` when its denominator is empty** — now only when the evidence map grades *nothing*
   at this checkpoint, since weak abstention no longer removes fields. `A(t)` is then
   genuinely undefined and is reported as `None`, not 0.0, with the checkpoint excluded from
   AUFC. Scoring it 0.0 was the first thing tried and it is wrong: it means an SUT that
   behaves perfectly at a checkpoint where the timeline evidences nothing is scored as a
   total failure.

   The old residual incentive — an SUT emptying its own denominator by abstaining on weak
   fields — **is gone**, because the denominator is now a property of the item. That is a
   side benefit of change 1 above, not the reason for it.

   `F(t)` is also `None` there: with no evidenced field, there is no yardstick to price a
   fabrication against. The fabrication *count* is still published, so the checkpoint reports
   what it can.

2. **The fabrication term is computed over preference fields only.** Constraint fabrication
   is a hard gating failure under §8.2 and would otherwise be counted twice — once in the
   gating track and again in the fidelity penalty. It still appears in the error
   decomposition.

3. **What counts as "wrong" for error decomposition (§8.4).** Ordinals and distributions are
   graded, so "wrong" needs a cut. `ERROR_THRESHOLD = 0.5` — below half agreement — is used
   for the staleness/misreading split. Fabrication does *not* use it: any commit in U(t) is
   a fabrication regardless of its value, per §8.1.

4. **`f_skill` is reported next to `F(t)`.** Crediting weak abstention gives a do-nothing
   system a non-zero floor — it collects `WEAK_CREDIT` on every weak field — and in a
   weak-heavy tier that floor is high. `f_skill = (F_raw − F_floor)/(1 − F_floor)` divides it
   out, so abstain-everything scores 0 by construction and tiers become comparable. `F(t)`
   remains the headline; see ADR-0003 on why the floor is defensible but needs publishing.

5. **A thinly-evidenced checkpoint is scored but not averaged** (`MIN_EVIDENCED_FOR_AUFC`,
   ADR-0004). Convention 1 handles `|S ∪ W| = 0`; this handles `0 < |S ∪ W| < 7`, where the
   scores are well-defined but a single fabrication can move `F_raw` by more than the entire
   agreement range. Those checkpoints keep their `A`, `F`, `F_raw`, `f_skill` and fabrication
   count, and are excluded only from AUFC. `CheckpointScore.scorable` and `.averageable` are
   the two predicates, and `SubjectScore` publishes both counts, so the exclusions are
   visible rather than silent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field as dc_field
from typing import Any, Iterable, Sequence

from . import fields, metrics
from .card import PersonaCard
from .evidence import Grade
from .fields import UNKNOWN
from .subject import Subject

#: Default fabrication weight in F(t) = A(t) − λ·(n_fab / |S(t) ∪ W(t)|) (spec §8.3).
#:
#: λ = 1.0 is the only value with a non-arbitrary reading: **one fabrication costs exactly
#: what turning a perfectly-answered evidenced field into a zero costs.** Both errors are
#: priced on the same denominator, so the exchange rate between them is a_f/λ and λ = 1 puts
#: it just under 1 — fabrication is the marginally worse error, uniformly across tiers, with
#: no thumb on the scale. See ADR-0002.
DEFAULT_LAMBDA = 1.0
#: Smallest `|S(t) ∪ W(t)|` a checkpoint may have and still enter AUFC (spec §8.3a, ADR-0004).
#:
#: `F_raw`'s floor at a checkpoint is `−λ·|U(t)|/|S(t) ∪ W(t)|`. At 7 of 20 preference fields
#: evidenced that floor is −1.86 at λ = 1.0, so no single checkpoint can shift a
#: ten-checkpoint mean by more than about 0.29. Below 7 it grows fast — −3.0 at five fields,
#: −9.0 at two — and a mean over such a curve reports the thinnest checkpoint rather than the
#: system.
#:
#: **Deliberately independent of λ.** A λ-derived threshold would gate different checkpoints
#: into each column of the λ sweep, so the columns would no longer be the same measurement at
#: different prices. The set of averaged checkpoints is a property of the evidence map; only
#: the resulting ceiling moves with λ (−0.93 at λ = 0.5, −3.71 at λ = 2.0).
#:
#: Cost, stated plainly: in a sparse tier this excludes the earliest checkpoints, which are
#: where an over-committing system is punished hardest — so AUFC is measured on a slightly
#: easier stretch of the curve than the full trajectory. Per-checkpoint numbers and
#: fabrication counts are published for the excluded checkpoints precisely so that stretch
#: stays inspectable. Sweep this alongside λ and `WEAK_CREDIT`.
MIN_EVIDENCED_FOR_AUFC = 7
#: Also reported at these, so a reader can see how much the penalty is doing the work. λ = 0
#: is dropped: with no penalty at all `F` collapses to `A` and the composite is not being
#: reported, it is being switched off. λ = 2.0 replaces it as the "fabrication is clearly
#: worse than a wrong answer" end of the range.
REPORTED_LAMBDAS: tuple[float, ...] = (0.5, 1.0, 2.0)

#: Agreement below this counts as a wrong value for the §8.4 error decomposition.
ERROR_THRESHOLD = 0.5

#: `f_skill(t) >= this` with zero constraint failures defines time-to-threshold (spec §8.6).
#:
#: Measured on `f_skill` rather than `A(t)` since 2026-08-10. Crediting weak abstention gives
#: a do-nothing card a floor of `WEAK_CREDIT · |W|/(|S|+|W|)`, which in a weak-heavy tier can
#: sit at or above 0.8 — so an `A`-based threshold would be reached at checkpoint 0 by a
#: system that emitted nothing. On `f_skill` the threshold keeps its intended meaning: 80% of
#: the distance from doing nothing to being right.
FIDELITY_THRESHOLD = 0.8

FABRICATION = "fabrication"
STALENESS = "staleness"
MISREADING = "misreading"

_PREFERENCE_ID_SET = frozenset(fields.PREFERENCE_IDS)

#: The distribution (histogram) fields, identified the same way `sut.DISTRIBUTION_FIELD_IDS`
#: is — by their JSD metric — but derived here to avoid a scorer→sut import cycle. These are
#: the fields the low-N carve-out below applies to.
_DISTRIBUTION_ID_SET = frozenset(
    spec.field_id for spec in fields.ALL_FIELDS if spec.metric == fields.METRIC_JSD
)


@dataclass
class FieldOutcome:
    """The scored result for one field at one checkpoint. Published per §8.9 so failures
    are diagnosable rather than hidden behind an aggregate."""

    field_id: str
    grade: Grade
    committed: bool
    agreement: float | None          # None when not scored (abstained, or in U(t))
    counted_in_a: bool
    fabricated: bool
    error_kind: str | None = None    # one of FABRICATION / STALENESS / MISREADING


@dataclass
class CheckpointScore:
    """Everything the scorer knows about one checkpoint.

    ``a`` and ``f`` are ``None`` when the checkpoint has nothing scorable — see convention
    1 in the module docstring. ``fab`` is always defined.
    """

    checkpoint: int
    a: float | None                   # A(t): mean agreement over S(t) ∪ W(t)
    a_denominator: int                # |S(t)| + |W(t)|; 0 means A is undefined
    fab: float                        # fab(t): fraction of U(t) committed on — diagnostic
    fab_denominator: int              # |U(t)|
    fabrication_count: int            # what actually prices the penalty (§8.3)
    penalty: float                    # λ · fabrication_count / a_denominator
    f: float | None                   # F(t) at the default lambda, clamped to [0, 1]
    f_raw: float | None               # A − penalty, unclamped: the incentive-bearing number
    a_floor: float | None             # A of an all-abstain card here — the do-nothing floor
    f_skill: float | None             # (f_raw − a_floor) / (1 − a_floor)
    f_by_lambda: dict[float, float]   # empty when A is undefined
    constraint_recall: float | None
    constraint_precision: float | None
    constraint_failure: bool
    outcomes: list[FieldOutcome] = dc_field(default_factory=list)
    carried_state_bytes: int | None = None
    carried_state_tokens: int | None = None

    @property
    def scorable(self) -> bool:
        """False when the timeline evidences nothing this checkpoint can be scored on."""
        return self.a is not None

    def averageable(self, min_evidenced: int = MIN_EVIDENCED_FOR_AUFC) -> bool:
        """True when this checkpoint may enter AUFC (ADR-0004).

        Strictly stronger than `scorable`: a checkpoint with one or two evidenced fields is
        scored, reported, and diagnosable, but its `F_raw` swings too far per fabrication to
        belong in a mean. Read the two predicates together — a subject where they differ
        widely is one whose curve is mostly too thin to average, which is itself a finding
        about the tier rather than about the SUT.

        The threshold is an argument rather than a constant read from module scope because
        ADR-0004 makes it a published, swept parameter alongside λ and `WEAK_CREDIT`. The
        toy subjects sit deliberately below the production default and pass `1` — see
        `toy.py`.
        """
        return self.scorable and self.a_denominator >= min_evidenced

    def errors_by_kind(self) -> dict[str, int]:
        counts = {FABRICATION: 0, STALENESS: 0, MISREADING: 0}
        for outcome in self.outcomes:
            if outcome.error_kind:
                counts[outcome.error_kind] += 1
        return counts


@dataclass
class SubjectScore:
    """The fidelity trajectory for one subject, plus the §8.6 trajectory metrics."""

    subject_id: str
    tier: str
    simulator_model: str
    checkpoints: list[CheckpointScore]
    aufc: float | None                 # None when no checkpoint was scorable
    aufc_skill: float | None           # the same curve with the do-nothing floor divided out
    aufc_by_lambda: dict[float, float]
    scorable_checkpoints: int         # |S ∪ W| > 0 — has a defined A and F
    averaged_checkpoints: int         # ...and thick enough to enter AUFC (ADR-0004)
    time_to_threshold: int | None
    final_fidelity: float | None
    regression_count: int
    #: The §8.4 error decomposition counted **once per episode** rather than once per
    #: checkpoint-field. `errors_by_kind()` re-counts a wrong value at every later checkpoint
    #: it survives, so its totals scale with subject length, not with the number of mistakes.
    #: An episode is a maximal run of a field being wrong; it is opened once at onset and
    #: **re-opened only when new evidence lands on the still-wrong field and is ignored** —
    #: the grade rising (UNEVIDENCED→WEAK→STRONG) or a correction firing, both a fresh chance
    #: the SUT missed. A value that stays wrong with no new evidence is carried, not recounted.
    #: See `_error_episodes` and the `episode-error-count` decision.
    error_episodes: dict[str, int]
    post_correction_latency: dict[str, int | None]
    interference_collateral: float | None
    distractor_stability: float | None
    peak_carried_state_tokens: int | None
    #: Distribution occasions the submission emitted that landed in the closed bins, and those
    #: dropped as off-vocabulary (`sut.parse_datapoints`). A submission whose datapoints miss the
    #: bins — a raw "4" for the "3-5" bucket, "weekend-daytime" for "weekend-day" — is silently
    #: starved of the three JSD fields, which the fidelity numbers alone do not explain. Both
    #: default to 0, so a free baseline (which emits no datapoints) reports a clean 0/0.
    datapoint_kept: int = 0
    datapoint_dropped: int = 0

    def errors_by_kind(self) -> dict[str, int]:
        totals = {FABRICATION: 0, STALENESS: 0, MISREADING: 0}
        for checkpoint in self.checkpoints:
            for kind, count in checkpoint.errors_by_kind().items():
                totals[kind] += count
        return totals

    def datapoint_off_vocab_rate(self) -> float | None:
        """Fraction of emitted distribution occasions dropped as off-vocabulary, or None if the
        submission emitted no datapoints at all (every free baseline, and any paid one whose
        timeline never evidenced a distribution field)."""
        total = self.datapoint_kept + self.datapoint_dropped
        return self.datapoint_dropped / total if total else None


# --- per-checkpoint scoring -------------------------------------------------


def score_checkpoint(
    subject: Subject,
    checkpoint: int,
    emitted: PersonaCard,
    *,
    lam: float = DEFAULT_LAMBDA,
    carried_state_bytes: int | None = None,
    carried_state_tokens: int | None = None,
) -> CheckpointScore:
    """Score one emitted card against the gold card governing ``checkpoint`` (§8.1–§8.4)."""
    if emitted.schema_version != fields.SCHEMA_VERSION:
        raise ValueError(
            f"scorer handles schema_version {fields.SCHEMA_VERSION!r}, card declares "
            f"{emitted.schema_version!r}"
        )
    gold = subject.gold_at(checkpoint)
    outcomes: list[FieldOutcome] = []

    scored_agreements: list[float] = []
    floor_agreements: list[float] = []      # what an all-abstain card would have scored
    unevidenced_total = 0
    unevidenced_committed = 0

    # Constraint fields get outcomes too, so §8.4's "every wrong committed value is
    # classified" covers them — but they never enter A(t) or fab(t). §8.2 keeps them in a
    # separate gating track, and counting a fabricated constraint in both places would
    # penalize it twice for the same mistake.
    for field_id in fields.ALL_FIELDS_IDS:
        is_preference = field_id in _PREFERENCE_ID_SET
        grade = subject.evidence.grade(checkpoint, field_id)
        committed = emitted.committed(field_id)

        # Low-N distribution carve-out (§8.1a, ADR-0065). A distribution's *shape* is not
        # inferable from the handful of occasions behind a WEAK grade — on this corpus the
        # generator's STRONG onset is the fifth observation, so WEAK == 1–4 occasions (a
        # cell-for-cell equivalence; the only WEAK distribution cells with zero occasions are
        # qualitative price mentions carrying no bin, which belong here too). Such a cell is
        # excluded from BOTH terms: not scored for agreement (its one_minus_jsd against the
        # terminal card is low-N noise — 81% of `distribution_shape` auto-errors were this),
        # and not fabrication-eligible (the SUT saw a real visit, so a point-mass here is thin
        # evidence, not invention). STRONG distributions (>=5 occasions) score as normal below;
        # a genuinely UNEVIDENCED distribution (no occasion at all) still charges fabrication on
        # a commit, since a shape asserted with zero observed visits is invented. Reusing the
        # UNEVIDENCED grade for the WEAK band instead — the abandoned route1c grade-flip — made
        # every early distribution commit a fabrication and drove real SUTs below the abstain
        # floor (ADR-0065; `docs/GT-POLICIES.md` notes the route1c corpus); excluding the cell is what the fix requires.
        if field_id in _DISTRIBUTION_ID_SET and grade == Grade.WEAK:
            outcomes.append(FieldOutcome(field_id, grade, committed, None, False, False))
            continue

        if grade == Grade.UNEVIDENCED:
            if is_preference:
                unevidenced_total += 1
            if committed:
                if is_preference:
                    unevidenced_committed += 1
                # Fabrication is band-determined, not value-determined: a commit here is
                # an error even if it happens to match gold (spec §8.1).
                outcomes.append(FieldOutcome(
                    field_id, grade, True, None, False, True, FABRICATION,
                ))
            else:
                outcomes.append(FieldOutcome(field_id, grade, False, None, False, False))
            continue

        if not committed:
            if grade == Grade.STRONG and is_preference:
                # A miss, not an exclusion. Excluding it would make abstaining everywhere
                # either undefined or free (spec §8.1).
                scored_agreements.append(0.0)
                floor_agreements.append(0.0)
                outcomes.append(FieldOutcome(field_id, grade, False, 0.0, True, False))
            elif grade == Grade.WEAK and is_preference:
                # Credited, and *in* the denominator (ADR-0001). Dropping it from both — the
                # pre-2026-08-10 rule — was not a credit but removal from an average, which
                # made the value of abstaining depend on the SUT's accuracy elsewhere.
                credit = metrics.weak_abstention_credit(field_id, gold.get(field_id))
                scored_agreements.append(credit)
                floor_agreements.append(credit)
                outcomes.append(FieldOutcome(field_id, grade, False, credit, True, False))
            else:
                # A constraint, or a preference field the bands above already handled.
                outcomes.append(FieldOutcome(field_id, grade, False, None, False, False))
            continue

        agreement = metrics.agreement(field_id, emitted.get(field_id), gold.get(field_id))
        if is_preference:
            scored_agreements.append(agreement)
            # The floor is what this card would have scored had it abstained everywhere, so
            # it takes the abstention value for this band regardless of what was committed.
            floor_agreements.append(
                0.0 if grade == Grade.STRONG
                else metrics.weak_abstention_credit(field_id, gold.get(field_id))
            )
        error_kind = None
        if agreement < ERROR_THRESHOLD:
            error_kind = _classify_error(subject, checkpoint, field_id, emitted)
        outcomes.append(FieldOutcome(
            field_id, grade, True, agreement, is_preference, False, error_kind,
        ))

    # Undefined rather than 0.0 when nothing is scorable — see convention 1 above.
    denominator = len(scored_agreements)
    a = sum(scored_agreements) / denominator if denominator else None
    # Reported for readability, no longer used to price the penalty (ADR-0002).
    fab = unevidenced_committed / unevidenced_total if unevidenced_total else 0.0

    recall, precision, failure = _score_constraints(subject, checkpoint, emitted, gold)

    def _penalty(weight: float) -> float:
        # Fabrications are priced on the *same* denominator as agreement, which is what makes
        # the exchange rate between the two error kinds a_f/λ rather than a function of how
        # much of the card the transcript left unevidenced (ADR-0002).
        return weight * unevidenced_committed / denominator if denominator else 0.0

    a_floor = sum(floor_agreements) / denominator if denominator else None
    raw_by_lambda = (
        {}
        if a is None
        else {
            candidate: a - _penalty(candidate)
            for candidate in sorted({*REPORTED_LAMBDAS, lam})
        }
    )
    f_by_lambda = {k: _clamp(v) for k, v in raw_by_lambda.items()}
    f_raw = raw_by_lambda.get(lam)

    # A do-nothing card already collects WEAK_CREDIT on every weak field, so the floor is not
    # zero and in a weak-heavy tier it is high. Dividing it out is what keeps "abstain-
    # everything scores ~0" meaningful and tiers comparable (ADR-0003, convention 4).
    f_skill = (
        None
        if f_raw is None or a_floor is None or abs(1.0 - a_floor) < 1e-12
        else (f_raw - a_floor) / (1.0 - a_floor)
    )

    return CheckpointScore(
        checkpoint=checkpoint,
        a=a,
        a_denominator=denominator,
        fab=fab,
        fab_denominator=unevidenced_total,
        fabrication_count=unevidenced_committed,
        penalty=_penalty(lam),
        f=f_by_lambda.get(lam),
        f_raw=f_raw,
        a_floor=a_floor,
        f_skill=f_skill,
        f_by_lambda=f_by_lambda,
        constraint_recall=recall,
        constraint_precision=precision,
        constraint_failure=failure,
        outcomes=outcomes,
        carried_state_bytes=carried_state_bytes,
        carried_state_tokens=carried_state_tokens,
    )


def _classify_error(
    subject: Subject, checkpoint: int, field_id: str, emitted: PersonaCard
) -> str:
    """Split an evidenced-but-wrong value into staleness vs misreading (spec §8.4).

    Staleness is specifically *the previously-correct answer*: the field was corrected at or
    before this checkpoint and the SUT is still emitting what used to be true. Anything else
    evidenced-and-wrong is a misreading. Distinguishing them matters because they have
    different fixes — staleness is a memory-update failure, misreading is an inference
    failure — and lumping them would make the error profile useless for diagnosis.
    """
    correction = subject.correction_for(field_id, checkpoint)
    if correction is not None:
        stale_agreement = metrics.agreement(
            field_id, emitted.get(field_id), correction.old_value
        )
        if stale_agreement >= 1.0 - 1e-9:
            return STALENESS
    return MISREADING


def _score_constraints(
    subject: Subject, checkpoint: int, emitted: PersonaCard, gold: PersonaCard
) -> tuple[float | None, float | None, bool]:
    """Constraint gating over so-far-mentioned constraints (spec §8.2).

    Reported separately and never averaged into fidelity. Both directions are hard
    failures: a missed constraint can hurt someone, and an invented one makes the twin
    useless in the opposite direction.

    Only constraints whose field is evidenced (STRONG or WEAK) count — the SUT cannot be
    charged with missing an allergy nobody has mentioned yet.
    """
    gold_members: set[tuple[str, str]] = set()
    predicted_members: set[tuple[str, str]] = set()
    failure = False
    any_evidenced = False

    for field_id in fields.CONSTRAINT_IDS:
        grade = subject.evidence.grade(checkpoint, field_id)
        if grade == Grade.UNEVIDENCED:
            # An invented constraint on an unevidenced field is still a fabrication and a
            # gating failure — it is the precision half of §8.2.
            if emitted.committed(field_id):
                value = emitted.get(field_id)
                if _constraint_is_active(field_id, value):
                    failure = True
                    for member in _constraint_members(field_id, value):
                        predicted_members.add((field_id, member))
            continue

        any_evidenced = True
        for member in _constraint_members(field_id, gold.get(field_id)):
            gold_members.add((field_id, member))
        if emitted.committed(field_id):
            for member in _constraint_members(field_id, emitted.get(field_id)):
                predicted_members.add((field_id, member))

    if not any_evidenced and not predicted_members:
        return None, None, failure

    hits = gold_members & predicted_members
    recall = len(hits) / len(gold_members) if gold_members else 1.0
    precision = len(hits) / len(predicted_members) if predicted_members else 1.0
    if recall < 1.0 or precision < 1.0:
        failure = True
    return recall, precision, failure


def _constraint_is_active(field_id: str, value: Any) -> bool:
    """Whether a committed constraint value asserts anything at all.

    ``dietary: []`` and ``alcohol_free_required: False`` assert the *absence* of a
    constraint. Those are legitimate claims, not invented constraints, so they must not
    trip the precision failure.
    """
    if value == UNKNOWN:
        return False
    if fields.BY_ID[field_id].metric == fields.METRIC_EXACT:
        return bool(value)
    return bool(value)


def _constraint_members(field_id: str, value: Any) -> tuple[str, ...]:
    """Flatten a constraint field into comparable (field, member) atoms."""
    if value == UNKNOWN:
        return ()
    if fields.BY_ID[field_id].metric == fields.METRIC_EXACT:
        return ("required",) if value else ()
    return tuple(sorted(value))


# --- trajectory scoring -----------------------------------------------------


def score_subject(
    subject: Subject,
    emissions: Sequence[PersonaCard],
    *,
    lam: float = DEFAULT_LAMBDA,
    min_evidenced: int = MIN_EVIDENCED_FOR_AUFC,
    carried_state_bytes: Sequence[int] | None = None,
    carried_state_tokens: Sequence[int] | None = None,
    datapoint_kept: int = 0,
    datapoint_dropped: int = 0,
) -> SubjectScore:
    """Score a full trajectory: one emitted card per checkpoint (spec §8.6).

    ``emissions[t]`` is what the SUT emitted at checkpoint *t*, having seen only the
    sessions since *t−1* plus its own carried state (§1.3).
    """
    expected = subject.evidence.checkpoint_count
    if len(emissions) != expected:
        raise ValueError(
            f"subject {subject.subject_id} has {expected} checkpoints but got "
            f"{len(emissions)} emissions"
        )

    scores = [
        score_checkpoint(
            subject, index, card, lam=lam,
            carried_state_bytes=_at(carried_state_bytes, index),
            carried_state_tokens=_at(carried_state_tokens, index),
        )
        for index, card in enumerate(emissions)
    ]

    # AUFC: area under the F(t) curve normalized by checkpoint count — i.e. the mean over
    # *averageable* checkpoints. Normalizing is what makes it comparable within a tier; it
    # does not make it comparable across tiers, because tiers checkpoint at different
    # densities. Tier numbers are never pooled (§8.9).
    #
    # Since ADR-0004 the averaged set is narrower than the scorable set: a checkpoint with a
    # very thin evidenced band is scored and published but kept out of the mean, because one
    # fabrication there moves F_raw by more than the whole agreement range. Both counts ship
    # on SubjectScore so the exclusion is legible.
    scorable = [s for s in scores if s.scorable]
    averaged = [s for s in scores if s.averageable(min_evidenced)]
    if averaged:
        aufc_by_lambda = {
            candidate: sum(s.f_by_lambda[candidate] for s in averaged) / len(averaged)
            for candidate in averaged[0].f_by_lambda
        }
    else:
        # Either the timeline never evidences anything, or it never evidences enough at one
        # checkpoint to average. Both leave fabrication counts as the only reportable signal.
        aufc_by_lambda = {}

    # Final fidelity is one checkpoint's value, not a mean, so the averaging gate does not
    # apply to it: `F` is clamped to [0, 1] and cannot run away however thin the band is.
    final = next((s.f for s in reversed(scores) if s.scorable), None)

    return SubjectScore(
        subject_id=subject.subject_id,
        tier=subject.tier,
        simulator_model=subject.simulator_model,
        checkpoints=scores,
        aufc=aufc_by_lambda.get(lam),
        aufc_skill=_mean_or_none([s.f_skill for s in averaged if s.f_skill is not None]),
        aufc_by_lambda=aufc_by_lambda,
        scorable_checkpoints=len(scorable),
        averaged_checkpoints=len(averaged),
        time_to_threshold=_time_to_threshold(scores),
        final_fidelity=final,
        regression_count=_regression_count(subject, scores),
        error_episodes=_error_episodes(subject, scores),
        post_correction_latency=_post_correction_latency(subject, emissions),
        interference_collateral=_interference_collateral(subject, scores),
        distractor_stability=_distractor_stability(subject, emissions),
        peak_carried_state_tokens=_peak(carried_state_tokens),
        datapoint_kept=datapoint_kept,
        datapoint_dropped=datapoint_dropped,
    )


def _time_to_threshold(scores: Sequence[CheckpointScore]) -> int | None:
    """Checkpoints to reach `f_skill` >= 0.8 with zero constraint failures (spec §8.6).

    On skill rather than raw `A` — see `FIDELITY_THRESHOLD` for why the raw number stopped
    meaning what this metric needs it to mean.
    """
    for score in scores:
        if score.f_skill is None:
            continue
        if score.f_skill >= FIDELITY_THRESHOLD and not score.constraint_failure:
            return score.checkpoint
    return None


def _error_episodes(
    subject: Subject, scores: Sequence[CheckpointScore]
) -> dict[str, int]:
    """The §8.4 errors counted once per episode instead of once per checkpoint-field.

    `errors_by_kind` re-tallies one wrong value at every checkpoint it survives, so its
    counts scale with subject length rather than with the number of mistakes (luna reported
    109 tallied for ~16 real). This counts a maximal run of a field being wrong as one
    episode, re-opening it only when the SUT is given a *fresh chance and misses again*:

    * **onset** — the field was not wrong at its previous checkpoint (a first mistake, or a
      fix-then-rebreak after it had been right);
    * **failed update** — the field is still wrong and new evidence has arrived since the last
      wrong checkpoint: the grade rose (UNEVIDENCED→WEAK→STRONG) or a correction fired on it.

    A value that stays wrong with *no* new evidence is carried, not re-counted — "unless new
    evidence appears that the SUT ignores again, that is still a single error." The episode
    population is exactly `errors_by_kind`'s (every FieldOutcome carrying an `error_kind`); an
    episode's kind is the kind at the checkpoint that opened it. See the `episode-error-count`
    decision.
    """
    corrected_at = {(e.field_id, e.checkpoint) for e in subject.corrections}
    episodes = {FABRICATION: 0, STALENESS: 0, MISREADING: 0}
    prev_wrong: dict[str, bool] = {}
    prev_grade: dict[str, Grade] = {}

    for score in scores:
        for outcome in score.outcomes:
            field_id = outcome.field_id
            if outcome.error_kind is None:
                prev_wrong[field_id] = False
                prev_grade[field_id] = outcome.grade
                continue
            was_wrong = prev_wrong.get(field_id, False)
            if not was_wrong:
                opens = True                                   # onset / fix-then-rebreak
            else:
                grade_rose = outcome.grade > prev_grade.get(field_id, outcome.grade)
                corrected_here = (field_id, score.checkpoint) in corrected_at
                opens = grade_rose or corrected_here           # a fresh chance, missed
            if opens:
                episodes[outcome.error_kind] += 1
            prev_wrong[field_id] = True
            prev_grade[field_id] = outcome.grade
    return episodes


def _regression_count(subject: Subject, scores: Sequence[CheckpointScore]) -> int:
    """Previously-correct fields later wrong, excluding scripted corrections (§8.6).

    The exclusion matters: when the user says "actually I stopped eating meat", the SUT
    *should* change its answer, and counting that as a regression would penalize exactly
    the behaviour the correction event exists to elicit.
    """
    corrected_at = {
        (event.field_id, event.checkpoint) for event in subject.corrections
    }
    correct_before: dict[str, bool] = {}
    regressions = 0

    for score in scores:
        for outcome in score.outcomes:
            if outcome.agreement is None:
                continue
            was_correct = correct_before.get(outcome.field_id, False)
            now_correct = outcome.agreement >= ERROR_THRESHOLD
            if was_correct and not now_correct:
                # Give the SUT the checkpoint of the correction and the one after it to
                # absorb the change; latency is measured separately and shouldn't be
                # double-counted as a regression.
                excused = any(
                    outcome.field_id == field_id
                    and score.checkpoint in (cp, cp + 1)
                    for field_id, cp in corrected_at
                )
                if not excused:
                    regressions += 1
            correct_before[outcome.field_id] = now_correct
    return regressions


def _post_correction_latency(
    subject: Subject, emissions: Sequence[PersonaCard]
) -> dict[str, int | None]:
    """Checkpoints from each correction until the field flips to the new value (§8.6).

    ``0`` means the SUT absorbed it in the same checkpoint. ``None`` means it never did —
    which is the failure mode §11 requires the reference SUT *not* to exhibit.
    """
    latencies: dict[str, int | None] = {}
    for event in subject.corrections:
        field_id = event.field_id
        assert field_id is not None
        latency: int | None = None
        for index in range(event.checkpoint, len(emissions)):
            card = emissions[index]
            if not card.committed(field_id):
                continue
            if metrics.agreement(field_id, card.get(field_id), event.new_value) >= 1.0 - 1e-9:
                latency = index - event.checkpoint
                break
        latencies[f"{field_id}@{event.checkpoint}"] = latency
    return latencies


def _interference_collateral(
    subject: Subject, scores: Sequence[CheckpointScore]
) -> float | None:
    """Mean drop in agreement on *settled, unrelated* fields during an interference burst.

    "Unrelated" means outside the facet group the burst targets; "settled" means the field
    was already scored at the previous checkpoint. Positive = degradation.

    This is the metric that catches a twin which updates by rewriting the whole card
    instead of the part the new evidence touches.
    """
    bursts = [e for e in subject.events if e.kind == "interference"]
    if not bursts:
        return None

    drops: list[float] = []
    for burst in bursts:
        if burst.checkpoint == 0:
            continue
        # Committed outcomes only. Since 2026-08-10 an abstained weak field carries an
        # agreement (the credit), so keying on "has an agreement" would count the SUT
        # *choosing to abstain* as collateral damage — which is a different behaviour, and one
        # the weak-band credit exists to treat as legitimate.
        before = {
            o.field_id: o.agreement
            for o in scores[burst.checkpoint - 1].outcomes if o.committed
        }
        after = {
            o.field_id: o.agreement
            for o in scores[burst.checkpoint].outcomes if o.committed
        }
        for field_id in fields.PREFERENCE_IDS:
            if fields.BY_ID[field_id].group == burst.facet_group:
                continue
            prior, current = before.get(field_id), after.get(field_id)
            if prior is None or current is None:
                continue
            drops.append(prior - current)
    if not drops:
        return None
    return sum(drops) / len(drops)


def _distractor_stability(
    subject: Subject, emissions: Sequence[PersonaCard]
) -> float | None:
    """Fraction of fields left unchanged across a zero-signal session (spec §8.6).

    1.0 is perfect: a session with no persona signal should move nothing. Anything less is
    the card drifting under noise, which is the cheapest possible tell that a twin is
    re-imagining rather than updating.
    """
    distractors = [e for e in subject.events if e.kind == "distractor"]
    if not distractors:
        return None

    stabilities: list[float] = []
    for event in distractors:
        if event.checkpoint == 0:
            continue
        before = emissions[event.checkpoint - 1]
        after = emissions[event.checkpoint]
        unchanged = sum(
            1 for field_id in fields.PREFERENCE_IDS
            if before.get(field_id) == after.get(field_id)
        )
        stabilities.append(unchanged / len(fields.PREFERENCE_IDS))
    if not stabilities:
        return None
    return sum(stabilities) / len(stabilities)


# --- aggregation (spec §8.9) ------------------------------------------------


@dataclass
class Aggregate:
    """Metrics for one (tier, simulator) cell. Never pooled across cells in headline
    numbers — AUFC is computed at different checkpoint densities per tier (§8.9), and
    simulator lineage is a live confound (§7.1)."""

    tier: str
    simulator_model: str
    n_subjects: int
    n_scored_subjects: int             # subjects with at least one scorable checkpoint
    mean_aufc: float | None
    mean_aufc_skill: float | None       # published beside mean_aufc; see ADR-0003
    mean_aufc_by_lambda: dict[float, float]
    mean_final_fidelity: float | None
    median_time_to_threshold: float | None
    total_errors: dict[str, int]
    #: The same errors counted once per episode (`SubjectScore.error_episodes`), summed over
    #: the cell. Length-independent, so this is the number to read for "how many mistakes",
    #: while `total_errors` reads "how many session-checkpoints carried a mistake".
    total_episodes: dict[str, int]
    constraint_failure_rate: float
    mean_peak_carried_state_tokens: float | None
    #: Off-vocabulary distribution occasions pooled over the cell: kept, dropped, and the drop
    #: rate `dropped / (kept + dropped)`. The rate is the cross-submission-comparable number —
    #: `Mem0Extractor` re-lists its whole bank each checkpoint, so its absolute counts are per
    #: emission rather than once per occasion, but the fraction that misses the bins is not.
    datapoints_kept: int = 0
    datapoints_dropped: int = 0
    datapoint_off_vocab_rate: float | None = None


def aggregate(scores: Iterable[SubjectScore]) -> list[Aggregate]:
    """Group subject scores into (tier, simulator) cells.

    Subjects with no scorable checkpoint are counted in ``n_subjects`` but excluded from
    the fidelity means — averaging in a missing number as zero would misreport a timeline
    that evidences nothing as an SUT failure.
    """
    cells: dict[tuple[str, str], list[SubjectScore]] = {}
    for score in scores:
        cells.setdefault((score.tier, score.simulator_model), []).append(score)

    results: list[Aggregate] = []
    for (tier, simulator), group in sorted(cells.items()):
        scored = [s for s in group if s.aufc is not None]
        lambdas = scored[0].aufc_by_lambda.keys() if scored else ()
        times = [s.time_to_threshold for s in group if s.time_to_threshold is not None]
        tokens = [
            s.peak_carried_state_tokens for s in group
            if s.peak_carried_state_tokens is not None
        ]
        failures = sum(
            1 for s in group if any(c.constraint_failure for c in s.checkpoints)
        )
        totals = {FABRICATION: 0, STALENESS: 0, MISREADING: 0}
        episodes = {FABRICATION: 0, STALENESS: 0, MISREADING: 0}
        for score in group:
            for kind, count in score.errors_by_kind().items():
                totals[kind] += count
            for kind, count in score.error_episodes.items():
                episodes[kind] += count
        kept = sum(s.datapoint_kept for s in group)
        dropped = sum(s.datapoint_dropped for s in group)

        results.append(Aggregate(
            tier=tier,
            simulator_model=simulator,
            n_subjects=len(group),
            n_scored_subjects=len(scored),
            mean_aufc=_mean(s.aufc for s in scored) if scored else None,
            mean_aufc_skill=_mean_or_none(
                [s.aufc_skill for s in scored if s.aufc_skill is not None]
            ),
            mean_aufc_by_lambda={
                candidate: _mean(s.aufc_by_lambda[candidate] for s in scored)
                for candidate in lambdas
            },
            mean_final_fidelity=(
                _mean(s.final_fidelity for s in scored if s.final_fidelity is not None)
                if scored else None
            ),
            median_time_to_threshold=_median(times) if times else None,
            total_errors=totals,
            total_episodes=episodes,
            constraint_failure_rate=failures / len(group),
            mean_peak_carried_state_tokens=_mean(tokens) if tokens else None,
            datapoints_kept=kept,
            datapoints_dropped=dropped,
            datapoint_off_vocab_rate=(dropped / (kept + dropped) if kept + dropped else None),
        ))
    return results


# --- helpers ----------------------------------------------------------------


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _at(sequence: Sequence[Any] | None, index: int) -> Any:
    if sequence is None or index >= len(sequence):
        return None
    return sequence[index]


def _peak(sequence: Sequence[int] | None) -> int | None:
    return max(sequence) if sequence else None


def _mean_or_none(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else math.nan


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0
