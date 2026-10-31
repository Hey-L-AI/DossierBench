"""Ground-truth card generator (spec-v0 §4, build step 2).

Produces the stage-1 artifact: N persona cards with machine-readable stratification
records, the §4.2 hard-case quota, the §4.3 anti-correlation quota with tags wired
through, the §4.5 texture rule, and the §4.6 regeneration accounting.

**The card is sampled deterministically; the prose is not.** §4.4 says "an independent
model verifies each card", and the model half is real work — performability is a prose
judgment. What is *not* prose work is hitting the quotas: §4.1 wants auditable per-stratum
coverage, §4.2 wants ≥25% hard cases of four named kinds, and §4.3 wants ~15% of subjects
violating a declared cluster by a measurable amount. Asking a model for those and checking
afterwards inverts the control: the quota becomes something to verify rather than something
to construct. So this module draws the structured card from a seeded RNG over a declared
grid, and leaves two things to the model half — the `idiosyncrasies` text (§8.5, step 3)
and the performability judgment (§4.4, gated on `probe_coherence/`).

Consequences of that split, both worth holding against it:

* the cards are combinatorial, so they can only be as varied as the grid. `visit_time`
  shapes, level choice within a pole, set membership and distribution mass are jittered
  per subject, but two subjects in one stratum are more alike than two people are. §6.0's
  uniformity risk is a transcript-level risk and this does not remove it.
* with `RuleAuditor` the realized regeneration rate is near zero, so §4.6's accounting is
  exercised by tests rather than by the shipped run. It is built now because it has to be
  in place *before* a model auditor is attached, not because it earns its keep today.

Every field is `provenance: synthesized` (§12, and `fields.py` on why the tag stays).

Usage:
    python -m persona_twin.generator --n 36 --seed 7 --json cards.json
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field as dc_field
from typing import Any

from . import coherence, fields
from .card import PersonaCard
from .coherence import Auditor, RuleAuditor, Verdict

#: Default subject count. Spec §9.1 wants 30–100; 36 is two per stratum, the smallest
#: number at which every cell of the §4.1 grid is covered more than once.
DEFAULT_N = 36

#: §4.6: attempts per card before it is quarantined rather than silently dropped.
MAX_ATTEMPTS = 3

#: §4.2 and §4.3 quota targets, as fractions of N.
HARD_CASE_QUOTA = 0.25
ANTI_CORRELATION_QUOTA = 0.15

#: The same two quotas as *strides over the index space*, which is what makes them
#: per-subject rather than per-run. A quota expressed as a share of `n` cannot be
#: decided for subject `i` without knowing how many subjects come after it, and that is
#: exactly the instability ADR-0043 closes: `plan_subjects(50)[:5]` disagreed with
#: `plan_subjects(5)` on three of five subjects, so appending personas rewrote the cards
#: of subjects that had already been authored against.
#:
#: `HARD_CASE_STRIDE` is 1/0.25. The anti-correlation quota is kept as an exact integer
#: ratio rather than a stride because 0.15 has no clean reciprocal — and because float
#: arithmetic lies here: `20 * 0.15` is 3.0000000000000004, so a `ceil` on it would
#: silently over-assign at every twentieth subject.
HARD_CASE_STRIDE = 4
_ANTI_NUMERATOR, _ANTI_DENOMINATOR = 3, 20

#: §4.6: a stratum whose rejection rate exceeds this multiple of the median is a
#: generator bug to fix, not a bias to absorb.
REGENERATION_OUTLIER_FACTOR = 2.0

#: Shortest text that counts as an authored idiosyncrasy (§8.5). A structural floor, not a
#: quality bar — whether an idiosyncrasy is *strong*, which is what §4.2's mid-scale hard
#: case is exempted against, is a rubric judgment and is deferred with the rest of §8.5.
#: The floor exists so that discharging the texture debt cannot be done with a word.
MIN_IDIOSYNCRASY_CHARS = 40


# --- the §4.1 grid ----------------------------------------------------------
# Primary axes are crossed exhaustively and define the stratum. Secondary axes are
# rotated globally so their marginals are balanced without multiplying the cell count —
# 18 strata is already two subjects per cell at N = 36, and 72 would leave most cells
# empty at any N the spec permits.

EXPLORATION: tuple[str, ...] = ("low", "high")            # cluster C1
SPEND: tuple[str, ...] = ("low", "high", "bimodal")       # cluster C2
CONSTRAINT_LOAD: tuple[str, ...] = ("none", "single", "multiple")   # §2.3
SOCIALITY: tuple[str, ...] = ("solo", "group")            # cluster C3
DELIBERATENESS: tuple[str, ...] = ("spontaneous", "planned")  # cluster C4


@dataclass(frozen=True)
class Stratum:
    """One cell of the §4.1 sampling grid."""

    exploration: str
    spend: str
    constraints: str

    @property
    def id(self) -> str:
        return f"exp:{self.exploration}|spend:{self.spend}|con:{self.constraints}"


STRATA: tuple[Stratum, ...] = tuple(
    Stratum(exploration, spend, constraints)
    for exploration in EXPLORATION
    for spend in SPEND
    for constraints in CONSTRAINT_LOAD
)


# --- hard cases (§4.2) ------------------------------------------------------

HARD_CASE_KINDS: tuple[str, ...] = (
    "bimodal_distribution",      # bimodal price or visit-time spend
    "stated_vs_revealed",        # card asserts one thing, scheduled behavior shows another
    "single_mention",            # a hard constraint mentioned exactly once in the timeline
    coherence.MID_SCALE_HARD_CASE,   # strong idiosyncrasies, mid-scale ordinals elsewhere
)

#: Fields a stated-vs-revealed tension can be built on. The tension must be *performable*
#: in dialogue (§4.2), so these are the fields a person can plausibly describe themselves
#: as having while behaving otherwise — self-image fields, not logistics.
_TENSION_FIELDS: tuple[str, ...] = (
    "adventurousness", "spice_tolerance", "novelty_vs_loyalty", "deal_seeking",
)


# --- anti-correlation (§4.3) ------------------------------------------------
# Per cluster, the field whose value is flipped to the opposite extreme of the cluster's
# anchor. Choice of flip field is not free: on cluster C3 the obvious flip
# (`solo_dining_comfort`) collides with the `solo_comfort_conflicts_party_sizes`
# contradiction rule on solo-pole cards, which would make the anti-correlated subject
# genuinely incoherent rather than merely atypical. `noise_preference` violates C3 from
# either pole without tripping any rule — the distinction this whole quota depends on.

_ANTI_FLIP: dict[str, tuple[str, str]] = {
    "C1_exploration": ("adventurousness", "novelty_vs_loyalty"),
    "C2_spend": ("price_band_distribution", "deal_seeking"),
    "C3_sociality": ("party_size_distribution", "noise_preference"),
    "C4_deliberateness": ("planning_style", "wait_tolerance"),
}


# --- planning ---------------------------------------------------------------


@dataclass(frozen=True)
class SubjectPlan:
    """What a subject is *for*, decided before any value is drawn.

    Separating the plan from the draw is what makes the quotas constructive rather than
    emergent: quota membership is assigned over the index space, so it holds exactly, and
    a regeneration redraws values without changing what the subject was for.
    """

    subject_id: str
    stratum: Stratum
    sociality: str
    deliberateness: str
    anti_correlated: tuple[str, ...] = ()
    hard_case_tags: tuple[str, ...] = ()
    directives: tuple[str, ...] = ()


@dataclass
class GeneratedSubject:
    """One accepted card plus everything the later steps need to honour it."""

    subject_id: str
    card: PersonaCard
    stratum_id: str
    sociality: str
    deliberateness: str
    #: Every cluster this card's values violate, measured by `coherence.violated_clusters`
    #: — which is what §4.3's consumers need, since the protection an SUT is owed does not
    #: depend on whether the violation was intended.
    anti_correlated: tuple[str, ...] = ()
    #: The subset the §4.3 quota asked for. The difference between the two is *incidental*
    #: violation: a hard constraint can force one (`quiet_room_needed` on a group-pole
    #: subject pushes `noise_preference` down and splits C3), and the accounting has to
    #: distinguish "the quota was met" from "the grid leaks".
    deliberate_anti_correlated: tuple[str, ...] = ()
    hard_case_tags: tuple[str, ...] = ()
    #: Instructions for §5 schedule authoring and §6 transcript authoring. Step 2 cannot
    #: realize a stated-vs-revealed tension or a once-mentioned constraint — both are
    #: properties of the timeline — so it names them here and step 4 consumes them.
    directives: tuple[str, ...] = ()
    attempts: int = 1

    def to_json(self) -> dict[str, Any]:
        return {
            "card": self.card.to_json(),
            "stratum": self.stratum_id,
            "sociality": self.sociality,
            "deliberateness": self.deliberateness,
            "anti_correlated": list(self.anti_correlated),
            "deliberate_anti_correlated": list(self.deliberate_anti_correlated),
            "hard_case_tags": list(self.hard_case_tags),
            "directives": list(self.directives),
            "attempts": self.attempts,
        }


@dataclass
class StratumRecord:
    """§4.1 stratification record and §4.6 regeneration accounting, per cell."""

    stratum_id: str
    accepted: int = 0
    attempts: int = 0
    rejections: int = 0
    quarantined: int = 0
    reasons: dict[str, int] = dc_field(default_factory=dict)

    @property
    def regeneration_rate(self) -> float:
        """Rejections per attempt. §4.6 wants this per stratum, not in aggregate: the
        corners of the schema space are where coherence audits are marginal, and an
        aggregate rate hides a corner being quietly emptied."""
        return self.rejections / self.attempts if self.attempts else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "stratum": self.stratum_id, "accepted": self.accepted,
            "attempts": self.attempts, "rejections": self.rejections,
            "quarantined": self.quarantined,
            "regeneration_rate": round(self.regeneration_rate, 4),
            "reasons": dict(sorted(self.reasons.items())),
        }


@dataclass
class GenerationRun:
    """The stage-1 deliverable: cards, coverage, and what it cost to get them."""

    subjects: list[GeneratedSubject]
    records: dict[str, StratumRecord]
    quarantine: list[tuple[str, tuple[str, ...]]]
    seed: int
    auditor: str
    requested: int

    @property
    def anti_correlated_subjects(self) -> list[GeneratedSubject]:
        """Subjects the §4.3 quota asked for. Incidental violators are not quota."""
        return [s for s in self.subjects if s.deliberate_anti_correlated]

    @property
    def incidental_violations(self) -> list[tuple[str, tuple[str, ...]]]:
        """Cluster violations the plan did not ask for, per subject.

        Not a defect in itself — the subject is tagged for what it does, so the §4.3
        protection holds — but a rising count means the grid is producing violations by
        accident, and the quota stops being the ~15% the fabrication rule was calibrated
        against.
        """
        return [
            (s.subject_id,
             tuple(c for c in s.anti_correlated if c not in s.deliberate_anti_correlated))
            for s in self.subjects
            if set(s.anti_correlated) - set(s.deliberate_anti_correlated)
        ]

    @property
    def hard_case_subjects(self) -> list[GeneratedSubject]:
        return [s for s in self.subjects if s.hard_case_tags]

    @property
    def texture_debt(self) -> list[str]:
        """Subjects whose texture rests on an idiosyncrasy that is not authored yet.

        §4.2's mid-scale hard case is exempted from §4.5's extreme-ordinal rule on the
        promise of a strong idiosyncrasy (see `coherence.MID_SCALE_HARD_CASE`). Step 3
        must author one for each of these and re-run the texture check; until then the
        exemption is unbacked, and publishing the list is what stops it being forgotten.
        """
        return [
            s.subject_id for s in self.subjects
            if coherence.MID_SCALE_HARD_CASE in s.hard_case_tags
            and not s.card.idiosyncrasies
        ]

    def outlier_strata(self) -> list[str]:
        """§4.6: strata regenerating more than twice the median rate — a generator bug."""
        rates = [r.regeneration_rate for r in self.records.values()]
        median = statistics.median(rates) if rates else 0.0
        if median == 0.0:
            # With a near-zero median any nonzero cell is an outlier by ratio, which
            # would fire on a single rejection. Fall back to naming cells that reject at
            # all — the honest reading of "unusual" when nothing usually rejects.
            return sorted(r.stratum_id for r in self.records.values() if r.rejections)
        threshold = REGENERATION_OUTLIER_FACTOR * median
        return sorted(
            r.stratum_id for r in self.records.values()
            if r.regeneration_rate > threshold
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "auditor": self.auditor,
            "requested": self.requested,
            "accepted": len(self.subjects),
            "quotas": {
                "hard_case": {
                    "target": -(-self.requested * 25 // 100),
                    "realized": len(self.hard_case_subjects),
                },
                "anti_correlated": {
                    "target": anti_correlation_target(len([
                        s for s in self.subjects
                        if coherence.MID_SCALE_HARD_CASE not in s.hard_case_tags
                    ])),
                    "realized": len(self.anti_correlated_subjects),
                },
            },
            "incidental_violations": [
                {"subject_id": sid, "clusters": list(clusters)}
                for sid, clusters in self.incidental_violations
            ],
            "strata": [r.to_json() for r in self.records.values()],
            "outlier_strata": self.outlier_strata(),
            "quarantine": [
                {"subject_id": sid, "reasons": list(reasons)}
                for sid, reasons in self.quarantine
            ],
            "texture_debt": self.texture_debt,
            "subjects": [s.to_json() for s in self.subjects],
        }


# --- planning ---------------------------------------------------------------


def plan_subjects(n: int = DEFAULT_N) -> list[SubjectPlan]:
    """Assign strata, secondary axes and quota membership over the index space.

    Deterministic and RNG-free: what a subject is for must not change when its values are
    redrawn, or a regeneration could quietly retire a quota member (§4.4's failure in a
    different costume).
    """
    plans: list[SubjectPlan] = []
    for index in range(n):
        stratum = STRATA[index % len(STRATA)]
        plans.append(SubjectPlan(
            subject_id=f"gen-{index:03d}",
            stratum=stratum,
            sociality=SOCIALITY[index % len(SOCIALITY)],
            # Offset so sociality and deliberateness do not lock in step with each other
            # and leave two of their four combinations unsampled.
            deliberateness=DELIBERATENESS[(index // 2) % len(DELIBERATENESS)],
        ))

    plans = _assign_hard_cases(plans)
    plans = _assign_anti_correlation(plans)
    return plans


def _hard_case_slot(index: int) -> bool:
    """Whether subject `index` carries a §4.2 hard case, read off the index alone.

    Subject 0 always carries one, so a corpus of any length meets the floor, and from
    index 1 every fourth subject does. Over a prefix of length `m` that is
    `1 + ceil((m - 1) / 4)` members, which clears §4.2's `ceil(m / 4)` at every `m` —
    the quota now holds at every prefix instead of only at the `n` the run was planned
    for.

    The stride is anchored at index 1 rather than index 0, and that choice is worth its
    line: `{0, 4, 8, ...}` meets the floor just as well, but `gen-001` carries
    `single_mention` in the authored pilot and `gen-004` carries nothing, so anchoring
    at 0 would have moved both and cost 33 re-authored sessions to buy nothing. The
    price of the anchor is one extra hard case per 50 subjects — 14 rather than 13,
    28% against a floor of 25%.
    """
    return index == 0 or index % HARD_CASE_STRIDE == 1


def _kind_is_eligible(plan: SubjectPlan, kind: str) -> bool:
    """§4.2 kinds are not freely assignable: `bimodal_distribution` needs a bimodal-spend
    stratum and `single_mention` needs a subject with a constraint to mention. The other
    two fit any subject, which is what guarantees every slot can be filled."""
    if kind == "bimodal_distribution":
        return plan.stratum.spend == "bimodal"
    if kind == "single_mention":
        return plan.stratum.constraints != "none"
    return True


def _assign_hard_cases(plans: list[SubjectPlan]) -> list[SubjectPlan]:
    """§4.2: a hard case on every stride slot, cycling the four named kinds.

    The kind is the **least-assigned kind this subject is eligible for**, ties broken by
    `HARD_CASE_KINDS` order. A plain rotation was the obvious alternative and it is worse
    here: `bimodal_distribution` fits only one spend stratum, so a rotation that offers it
    to an ineligible subject burns its turn, and across a 50-subject run the kind can go
    unrealized altogether. Least-assigned holds a scarce kind at zero until a subject that
    can carry it appears.

    Prefix-stable by construction: the slot reads only `index`, and the counts read only
    the subjects before it, so `_assign_hard_cases(plans[:m])` is `_assign_hard_cases(plans)[:m]`.
    """
    updated = list(plans)
    assigned = {kind: 0 for kind in HARD_CASE_KINDS}
    for index, plan in enumerate(plans):
        if not _hard_case_slot(index):
            continue
        eligible = [k for k in HARD_CASE_KINDS if _kind_is_eligible(plan, k)]
        if not eligible:  # pragma: no cover - two kinds fit every subject
            raise RuntimeError(f"{plan.subject_id}: no §4.2 hard case is assignable")
        kind = min(eligible, key=lambda k: (assigned[k], HARD_CASE_KINDS.index(k)))
        assigned[kind] += 1
        directives = plan.directives
        if kind == "stated_vs_revealed":
            target_field = _TENSION_FIELDS[index % len(_TENSION_FIELDS)]
            directives += (f"stated_vs_revealed:{target_field}",)
        elif kind == "single_mention":
            directives += (f"single_mention:{_constraint_to_mention(plan)}",)
        updated[index] = SubjectPlan(
            plan.subject_id, plan.stratum, plan.sociality, plan.deliberateness,
            plan.anti_correlated, plan.hard_case_tags + (kind,), directives,
        )
    return updated


def _constraint_to_mention(plan: SubjectPlan) -> str:
    """Which constraint the §4.2 single-mention case hangs on.

    Must be one the subject actually has, which is a property of the constraint-load
    axis; `_constraint_fields` is the single source of that mapping so the directive and
    the drawn card cannot disagree.
    """
    return _constraint_fields(plan)[0]


def _constraint_fields(plan: SubjectPlan) -> tuple[str, ...]:
    """The constraint fields this plan's subject carries, by load and index rotation."""
    if plan.stratum.constraints == "none":
        return ()
    index = int(plan.subject_id.rsplit("-", 1)[1])
    rotations = (
        ("dietary",), ("allergies",), ("accessibility_needs",),
        ("alcohol_free_required",),
    )
    single = rotations[index % len(rotations)]
    if plan.stratum.constraints == "single":
        return single
    pairs = (
        ("dietary", "allergies"),
        ("allergies", "accessibility_needs"),
        ("dietary", "alcohol_free_required"),
        ("accessibility_needs", "alcohol_free_required"),
        ("dietary", "allergies", "accessibility_needs"),
    )
    return pairs[index % len(pairs)]


def anti_correlation_target(eligible_count: int) -> int:
    """How many §4.3 members a population of `eligible_count` eligible subjects carries.

    Exact integer ceiling of 15%, and the denominator is the *eligible* count rather than
    `n`: mid-scale hard cases are skipped, so measuring the quota against `n` was always
    measuring it against a population the assigner could not draw from.
    """
    return -(-eligible_count * _ANTI_NUMERATOR // _ANTI_DENOMINATOR)


def _assign_anti_correlation(plans: list[SubjectPlan]) -> list[SubjectPlan]:
    """§4.3: ~15% of subjects violate one declared cluster, tagged.

    Mid-scale hard cases are skipped: the violation is built by pushing two cluster fields
    to opposite extremes, which is the opposite of what that case is for. Every other
    subject is eligible, and the four clusters are rotated so no single cluster carries
    the whole quota — a quota concentrated on C1 would leave C2–C4 undefended against the
    same fabrication artifact.

    Membership is decided by a subject's position among the eligible subjects *before* it,
    never by `n`: after `m` eligible subjects exactly `anti_correlation_target(m)` are
    members. The old spread-by-`len(eligible)/target` step needed the total to place even
    the first member, which is the §4.4 failure in a different costume — a regeneration at
    a different `n` quietly retiring a quota member that had already been authored against.
    """
    updated = list(plans)
    cluster_ids = tuple(fields.CLUSTERS)
    eligible_seen = 0
    taken = 0
    for index, plan in enumerate(plans):
        if coherence.MID_SCALE_HARD_CASE in plan.hard_case_tags:
            continue
        eligible_seen += 1
        if anti_correlation_target(eligible_seen) <= taken:
            continue
        cluster = cluster_ids[taken % len(cluster_ids)]
        taken += 1
        updated[index] = SubjectPlan(
            plan.subject_id, plan.stratum, plan.sociality, plan.deliberateness,
            (cluster,), plan.hard_case_tags, plan.directives,
        )
    return updated


# --- drawing values ---------------------------------------------------------

_LOW, _HIGH, _MID = "low", "high", "mid"

#: Probability that a pole picks the extreme level rather than the inner one. Above 0.5
#: because §4.5 needs extremes and a person who is "somewhat" everything is unperformable;
#: below 1.0 because a grid that always picks the extreme produces two distinct cards per
#: stratum and the population-mode baseline turns into an oracle (the `toy.py` lesson).
_EXTREME_BIAS = 0.65

#: Cuisine pools. The `low` exploration pole draws from cuisines a cautious eater is
#: plausibly loyal to; `high` draws from the long tail. Overlapping deliberately — the
#: pole difference is breadth first and membership second.
_FAMILIAR = (
    "american", "burgers", "pizza", "italian", "deli", "brunch", "chinese", "mexican",
    "sushi", "greek", "steakhouse", "bbq", "seafood", "french",
)
_ADVENTUROUS = (
    "ethiopian", "peruvian", "malaysian", "filipino", "taiwanese", "moroccan",
    "lebanese", "korean", "vietnamese", "cajun", "caribbean", "portuguese",
    "dim_sum", "ramen", "soul_food", "turkish", "middle_eastern", "thai",
    "indian", "japanese", "spanish", "german", "latin_american", "mediterranean",
    "vegan_restaurant",
)


def _pick_level(
    rng: random.Random, field_id: str, pole: str, *, inner_only: bool = False
) -> str:
    """A level for an ordinal, from its pole's half of the scale.

    ``inner_only`` is the §4.2 mid-scale hard case: the level stays on the pole's side —
    a frugal subject is still frugal — but takes the inner of the two levels, so the card
    reads mid-scale everywhere and needs its idiosyncrasy to carry the texture. Collapsing
    such a card to the *middle* of the scale instead would break cluster consistency: its
    distributions are still poled by the stratum, so a mid ordinal beside a low-spend
    distribution measures as a §4.3 violation the subject was never tagged for.
    """
    levels = fields.BY_ID[field_id].levels
    if pole == _MID:
        return rng.choice(levels[1:3])
    if inner_only:
        return levels[1] if pole == _LOW else levels[-2]
    if pole == _LOW:
        pair = (levels[0], levels[1])
    else:
        pair = (levels[-1], levels[-2])
    return pair[0] if rng.random() < _EXTREME_BIAS else pair[1]


def _extreme(field_id: str, pole: str) -> str:
    """The extreme level of a pole — used where a value must be unambiguous."""
    levels = fields.BY_ID[field_id].levels
    return levels[0] if pole == _LOW else levels[-1]


def _opposite(pole: str) -> str:
    return _LOW if pole == _HIGH else _HIGH


def _distribution(bins: tuple[str, ...], weights: list[float]) -> dict[str, float]:
    """Normalize to masses that sum to exactly 1.0 at 2dp (the card validator's tolerance
    is 1e-6, so the residue is folded into the largest bin rather than left to float)."""
    total = sum(weights)
    masses = [round(w / total, 2) for w in weights]
    residue = round(1.0 - sum(masses), 2)
    masses[masses.index(max(masses))] += residue
    return {b: round(m, 2) for b, m in zip(bins, masses)}


def _price_distribution(rng: random.Random, spend: str) -> dict[str, float]:
    if spend == "bimodal":
        # Cheap weeknights and occasional blowouts — §2.4's stated reason for the field
        # being a distribution at all. Mean bin lands mid-scale, which is why the other
        # C2 fields go mid in this stratum (see `_draw_values`).
        heavy = rng.uniform(0.40, 0.48)
        return _distribution(
            fields.PRICE_BANDS, [heavy, 0.5 - heavy, 0.5 - heavy, heavy]
        )
    if spend == _LOW:
        return _distribution(
            fields.PRICE_BANDS,
            [rng.uniform(0.45, 0.65), rng.uniform(0.25, 0.45), rng.uniform(0.0, 0.08), 0.0],
        )
    return _distribution(
        fields.PRICE_BANDS,
        [0.0, rng.uniform(0.0, 0.08), rng.uniform(0.30, 0.50), rng.uniform(0.40, 0.60)],
    )


def _party_distribution(rng: random.Random, sociality: str) -> dict[str, float]:
    if sociality == "solo":
        return _distribution(
            fields.PARTY_SIZES,
            [rng.uniform(0.30, 0.55), rng.uniform(0.35, 0.55), rng.uniform(0.0, 0.10), 0.0],
        )
    return _distribution(
        fields.PARTY_SIZES,
        [0.0, rng.uniform(0.0, 0.12), rng.uniform(0.40, 0.60), rng.uniform(0.30, 0.50)],
    )


#: Visit-time shapes, rotated per subject. Free of every cluster, so this is where
#: cheap variance goes.
_VISIT_SHAPES: tuple[tuple[float, ...], ...] = (
    (0.10, 0.45, 0.10, 0.30, 0.05),   # weekday-evening regular
    (0.35, 0.15, 0.25, 0.20, 0.05),   # lunch-led
    (0.05, 0.10, 0.30, 0.45, 0.10),   # weekend-led
    (0.05, 0.20, 0.05, 0.25, 0.45),   # late-night
    (0.40, 0.05, 0.10, 0.05, 0.40),   # bimodal: lunches and late nights, nothing between
)


def _draw_values(plan: SubjectPlan, rng: random.Random) -> dict[str, Any]:
    """Draw one complete card from the plan's grid position."""
    exploration = plan.stratum.exploration
    spend = plan.stratum.spend
    sociality = plan.sociality
    deliberateness = plan.deliberateness
    mid_scale = coherence.MID_SCALE_HARD_CASE in plan.hard_case_tags

    def ordinal(field_id: str, pole: str) -> str:
        return _pick_level(rng, field_id, pole, inner_only=mid_scale)

    # C2 fields go mid-scale in the bimodal stratum: bimodal price measures mid on the
    # cluster's position scale (`coherence.position`), so poling the rest high or low
    # would read as an untagged cluster violation.
    spend_pole = _MID if spend == "bimodal" else spend
    sociality_pole = _LOW if sociality == "solo" else _HIGH
    deliberate_pole = _LOW if deliberateness == "spontaneous" else _HIGH

    values: dict[str, Any] = {}

    # --- hard constraints (§2.3) --------------------------------------------
    carried = _constraint_fields(plan)
    values["dietary"] = (
        [rng.choice(fields.DIETS)] if "dietary" in carried else []
    )
    values["allergies"] = (
        [rng.choice(fields.ALLERGENS)] if "allergies" in carried else []
    )
    values["accessibility_needs"] = (
        [rng.choice(fields.ACCESSIBILITY_NEEDS)] if "accessibility_needs" in carried else []
    )
    values["alcohol_free_required"] = "alcohol_free_required" in carried

    # --- taste ---------------------------------------------------------------
    breadth = rng.randint(5, 8) if exploration == _HIGH else rng.randint(2, 3)
    pool = _ADVENTUROUS if exploration == _HIGH else _FAMILIAR
    liked = rng.sample(pool, breadth)
    values["cuisines_liked"] = _respect_constraints(liked, values, pool=pool)
    values["cuisines_avoided"] = rng.sample(
        _avoidable_cuisines(values["cuisines_liked"]), rng.randint(1, 3)
    )
    values["spice_tolerance"] = _pick_level(
        rng, "spice_tolerance", _MID if mid_scale else rng.choice((_LOW, _HIGH))
    )
    values["adventurousness"] = ordinal("adventurousness", exploration)

    # --- economics -----------------------------------------------------------
    values["price_band_distribution"] = _price_distribution(rng, spend)
    values["deal_seeking"] = ordinal(
        "deal_seeking", _MID if spend_pole == _MID else _opposite(spend_pole)
    )

    # --- ambiance ------------------------------------------------------------
    values["noise_preference"] = ordinal("noise_preference", sociality_pole)
    values["formality_ceiling"] = ordinal("formality_ceiling", spend_pole)
    values["design_sensitivity"] = ordinal("design_sensitivity", spend_pole)

    # --- social --------------------------------------------------------------
    values["party_size_distribution"] = _party_distribution(rng, sociality)
    values["solo_dining_comfort"] = ordinal(
        "solo_dining_comfort", _opposite(sociality_pole)
    )

    # --- logistics -----------------------------------------------------------
    values["travel_radius"] = _pick_level(
        rng, "travel_radius", _MID if mid_scale else rng.choice((_LOW, _HIGH))
    )
    values["visit_time_distribution"] = _distribution(
        fields.VISIT_TIMES,
        [w * rng.uniform(0.85, 1.15) for w in rng.choice(_VISIT_SHAPES)],
    )
    values["planning_style"] = ordinal("planning_style", deliberate_pole)
    values["wait_tolerance"] = ordinal("wait_tolerance", deliberate_pole)

    # --- events --------------------------------------------------------------
    event_pool = [
        e for e in fields.EVENT_TYPES
        if not (values["alcohol_free_required"] and e in ("wine_tasting",))
    ]
    values["event_types_liked"] = rng.sample(event_pool, rng.randint(2, 4))
    values["event_types_avoided"] = rng.sample(
        [e for e in fields.EVENT_TYPES if e not in values["event_types_liked"]],
        rng.randint(1, 2),
    )
    values["participation_vs_spectating"] = _pick_level(
        rng, "participation_vs_spectating",
        _MID if mid_scale else rng.choice((_LOW, _HIGH)),
    )

    # --- quality signals ------------------------------------------------------
    values["rating_reliance"] = ordinal("rating_reliance", deliberate_pole)
    values["novelty_vs_loyalty"] = ordinal("novelty_vs_loyalty", exploration)

    # --- accessibility/noise interaction --------------------------------------
    # `quiet_room_needed` and a taste for loud rooms is a contradiction rule, not a
    # typicality judgment, and it is one the draw can avoid rather than regenerate into.
    if "quiet_room_needed" in values["accessibility_needs"]:
        values["noise_preference"] = _pick_level(rng, "noise_preference", _LOW)

    if plan.anti_correlated:
        _apply_anti_correlation(values, plan, rng)
    _resolve_cross_field_tensions(values, rng)
    return values


def _resolve_cross_field_tensions(values: dict[str, Any], rng: random.Random) -> None:
    """Repair the two tensions the grid can produce across independent axes.

    Both were found by `probe_coherence/` round 1 rather than by the rule set: the model
    judges rejected generated cards for them, and they were right — 17 of 60 first-run
    cards wanted a room quiet enough to talk in while seeking out karaoke. They are
    repaired at the draw for the same reason `_respect_constraints` is: a defect with a
    known fix should not be routed through §4.6's attempt budget, where it would inflate
    the regeneration rate that §4.6 reads as a signal about the *stratum*.

    Runs after `_apply_anti_correlation`, which can move both fields it reads. The
    repairs are chosen to be quota-safe: dropping loud events touches no cluster field,
    and the planning repair moves `planning_style` by one level *within its own pole*
    rather than touching `deal_seeking`, which may be a §4.3 flip field.
    """
    if values["noise_preference"] == "needs_quiet":
        quiet_pool = [
            e for e in fields.EVENT_TYPES
            if e not in coherence._LOUD_EVENTS
            and e not in values["event_types_avoided"]
            and not (values["alcohol_free_required"] and e == "wine_tasting")
        ]
        kept = [e for e in values["event_types_liked"] if e not in coherence._LOUD_EVENTS]
        while len(kept) < 2:
            candidate = rng.choice(quiet_pool)
            if candidate not in kept:
                kept.append(candidate)
        values["event_types_liked"] = kept

    if (values["planning_style"] == "fully_spontaneous"
            and values["deal_seeking"] == "plans_around_specials"):
        values["planning_style"] = "decides_same_day"

    wanted = {
        "seeks_hands_on": coherence._PARTICIPATORY_EVENTS,
        "watches_only": coherence._SPECTATOR_EVENTS,
    }.get(values["participation_vs_spectating"])
    if wanted and not set(values["event_types_liked"]) & wanted:
        candidates = sorted(
            e for e in wanted
            if e not in values["event_types_avoided"]
            and not (values["alcohol_free_required"] and e == "wine_tasting")
            and not (values["noise_preference"] == "needs_quiet"
                     and e in coherence._LOUD_EVENTS)
        )
        if candidates:
            values["event_types_liked"] = (
                [rng.choice(candidates)] + values["event_types_liked"][1:]
            )


def _avoidable_cuisines(liked: Sequence[str]) -> list[str]:
    """Cuisines this card can avoid without contradicting what it already seeks out.

    Excluding by name alone was not enough. `cuisines_avoided` may hold a token that names
    an *ingredient class* rather than a kitchen — `seafood` is the only one — and avoiding
    it while seeking out `sushi` is a contradiction the name check cannot see, because the
    two tokens differ. `gen-002` was drawn that way and every session of it that reached
    for the sushi beat wrote the user eating fish.

    The reverse direction needs no rule: avoiding `steakhouse` while liking `bbq` is an
    ordinary person, which is why `AVOIDING_FORBIDS` is one entry and not eleven.
    """
    entailed = set().union(
        *(coherence.CUISINE_INGREDIENTS.get(c, frozenset()) for c in liked)
    ) if liked else set()
    return [
        c for c in fields.CUISINES
        if c not in liked
        and not (coherence.AVOIDING_FORBIDS.get(c, frozenset()) & entailed)
    ]


def _respect_constraints(
    liked: list[str], values: dict[str, Any], *, pool: Sequence[str] = ()
) -> list[str]:
    """Drop cuisines the drawn constraints contradict (`coherence.check_contradictions`).

    Filtering at the draw rather than rejecting afterwards is deliberate: a vegan card
    with `steakhouse` liked is a *generator* error with a known fix, and routing it
    through the regeneration loop would spend the §4.6 attempt budget on a bug and
    inflate the very rate §4.6 reads as a signal.

    The ban list is no longer written out here. It was, and it had drifted from the one
    `coherence` checks against — this copy knew nothing about dairy, about `sushi` being
    fish, or about `american` being meat, so the draw emitted cards the checker would
    have caught if the checker had been complete either. Both now read
    `coherence.forbidden_ingredients`, which is the only place the collision is defined.

    The backfill preserves the requested breadth rather than taking two. Dropping to two
    on a constrained card would make `cuisines_liked` breadth a function of whether the
    subject happens to be vegan, and that breadth is the C1 cluster position scalar — a
    silent narrowing of exactly the axis §4.3 measures.
    """
    forbidden = coherence.forbidden_ingredients(values)
    kept = [c for c in liked if c not in coherence.conflicting_cuisines(liked, forbidden)]
    # Backfill from the pool this card actually drew from, falling back to `_FAMILIAR`
    # only if that one is exhausted. Refilling an `_ADVENTUROUS` card from `_FAMILIAR`
    # would make an adventurous vegan measurably less adventurous than an adventurous
    # omnivore, which is the same C1 distortion as truncating the list.
    for source in (pool or _FAMILIAR, _FAMILIAR):
        allowed = [
            c for c in source
            if c not in coherence.conflicting_cuisines(source, forbidden)
        ]
        for candidate in allowed:
            if len(kept) >= len(liked):
                return kept
            if candidate not in kept:
                kept.append(candidate)
    return kept


def _apply_anti_correlation(
    values: dict[str, Any], plan: SubjectPlan, rng: random.Random
) -> None:
    """Push one cluster's flip field to the opposite extreme of its anchor (§4.3).

    Both fields are set to unambiguous extremes rather than sampled, because the quota is
    only worth having if the violation is large enough to be *measurable*: the tag and
    `coherence.violated_clusters` must agree, and a violation that depends on which side
    of `_EXTREME_BIAS` a coin landed is not a control.
    """
    for cluster in plan.anti_correlated:
        anchor, flip = _ANTI_FLIP[cluster]
        anchor_position = coherence.position(anchor, values[anchor])
        if 0.35 < anchor_position < 0.65:
            # A mid anchor cannot carry a violation: the flip field can be at most half a
            # scale away from it, which lands under `ANTI_CORRELATION_SPREAD` and would
            # produce a subject tagged for a violation its values do not show. This is
            # the bimodal-spend stratum, where the C2 anchor measures mid by construction
            # (`coherence.position` flattens bimodality). Pole the anchor first.
            values[anchor] = _rebuild_anchor_high(anchor, rng)
            anchor_position = coherence.position(anchor, values[anchor])
        anchor_pole = _HIGH if anchor_position >= 0.5 else _LOW
        flip_spec = fields.BY_ID[flip]
        orientation = coherence.ORIENTATION[flip]
        # The flip field must land at the opposite *oriented* position, so a field that
        # runs backwards against its cluster (deal_seeking, solo_dining_comfort) takes
        # the level whose raw index is the same end, not the opposite one.
        want_high = anchor_pole == _LOW
        raw_pole = _HIGH if (want_high == (orientation > 0)) else _LOW
        values[flip] = _extreme(flip, raw_pole)
        if flip_spec.field_id == "noise_preference" and \
                "quiet_room_needed" in (values.get("accessibility_needs") or ()) and \
                raw_pole == _HIGH:
            # The access requirement wins: it is a hard constraint (§2.3) and the
            # contradiction rule is not negotiable. Violate the cluster from the anchor
            # side instead, by moving the anchor rather than the flip field.
            values[flip] = _extreme(flip, _LOW)
            values[anchor] = _rebuild_anchor_high(anchor, rng)


def _rebuild_anchor_high(anchor: str, rng: random.Random) -> Any:
    """The anchor at its top extreme, for the one case where the flip field is blocked."""
    spec = fields.BY_ID[anchor]
    if spec.metric == fields.METRIC_JSD:
        weights = [0.0] * len(spec.levels)
        weights[-1] = rng.uniform(0.55, 0.7)
        weights[-2] = 1.0 - weights[-1]
        return _distribution(spec.levels, weights)
    return _extreme(anchor, _HIGH)


# --- the generation loop ----------------------------------------------------


def generate(
    n: int = DEFAULT_N,
    seed: int = 0,
    auditor: Auditor | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    idiosyncrasies: dict[str, str] | None = None,
) -> GenerationRun:
    """Draw, audit and account for ``n`` cards (§4.1–§4.6).

    Deterministic in ``(n, seed)``: the per-attempt RNG is seeded from the subject index
    and attempt number, so a rejected draw is genuinely redrawn rather than repeated, and
    the whole run reproduces exactly.

    ``idiosyncrasies`` maps subject id to the §8.5 free text, which is authored by a model
    in §15 step 3 and therefore cannot be drawn here — this module is stdlib and never
    calls one (`../../authoring/idiosyncrasies.py` is the tool). Passing it discharges the
    §4.2 texture debt for the mid-scale hard cases; the text is attached after the card is
    audited, so it can never affect whether a card is accepted.
    """
    audit = auditor or RuleAuditor()
    plans = plan_subjects(n)
    records = {s.id: StratumRecord(s.id) for s in STRATA}
    subjects: list[GeneratedSubject] = []
    quarantine: list[tuple[str, tuple[str, ...]]] = []

    for index, plan in enumerate(plans):
        record = records[plan.stratum.id]
        last_reasons: tuple[str, ...] = ()
        for attempt in range(1, max_attempts + 1):
            # Seeded from a string rather than a tuple hash: `hash(str)` is salted per
            # process (PYTHONHASHSEED), which would make the run reproducible within a
            # session and different across sessions — the worst of both.
            rng = random.Random(f"{seed}|{plan.subject_id}|attempt{attempt}")
            card = PersonaCard(plan.subject_id, _draw_values(plan, rng))
            card.validate(allow_unknown=False)
            record.attempts += 1
            verdict = audit(
                card, anti_correlated=plan.anti_correlated,
                hard_case_tags=plan.hard_case_tags,
            )
            if verdict.ok:
                record.accepted += 1
                card.idiosyncrasies = (idiosyncrasies or {}).get(plan.subject_id, "")
                measured = coherence.violated_clusters(card)
                subjects.append(GeneratedSubject(
                    subject_id=plan.subject_id, card=card, stratum_id=plan.stratum.id,
                    sociality=plan.sociality, deliberateness=plan.deliberateness,
                    # The tag records what the card *does*, not what was intended: an
                    # SUT is owed the §4.3 protection on any cluster this subject
                    # violates, and a judge (§7.3) needs to know not to infer across it.
                    anti_correlated=tuple(sorted(
                        set(plan.anti_correlated) | set(measured)
                    )),
                    deliberate_anti_correlated=plan.anti_correlated,
                    hard_case_tags=plan.hard_case_tags, directives=plan.directives,
                    attempts=attempt,
                ))
                break
            record.rejections += 1
            last_reasons = verdict.reasons
            for reason in verdict.reasons:
                key = reason.split(":", 1)[0]
                record.reasons[key] = record.reasons.get(key, 0) + 1
        else:
            # §4.6: published, never silently dropped. A quarantined subject is a fact
            # about the generator that the dataset has to carry.
            record.quarantined += 1
            quarantine.append((plan.subject_id, last_reasons))

    return GenerationRun(
        subjects=subjects, records=records, quarantine=quarantine,
        seed=seed, auditor=getattr(audit, "name", type(audit).__name__), requested=n,
    )


# --- verification -----------------------------------------------------------


def verify(run: GenerationRun) -> list[str]:
    """Invariants a run must satisfy before it can be handed to step 3. Empty is a pass.

    This is not the §4.4 audit — that ran per card. These are properties of the *set*,
    and the one that matters most is the last: a tag that does not match a measured
    violation means the §4.3 control is not doing what the fabrication rule assumes.
    """
    problems: list[str] = []

    hard_target = -(-run.requested * 25 // 100)
    if len(run.hard_case_subjects) < hard_target:
        problems.append(
            f"§4.2 hard-case quota: {len(run.hard_case_subjects)} < {hard_target}"
        )
    # Measured against the *eligible* population and allowing one subject of slack per
    # quarantined card: a quarantine can drop a member (realized falls) or a non-member
    # (the eligible denominator falls and the target with it), and neither is a quota
    # failure. With an empty quarantine — every run so far — this is exact equality.
    anti_eligible = len([
        s for s in run.subjects if coherence.MID_SCALE_HARD_CASE not in s.hard_case_tags
    ])
    anti_target = anti_correlation_target(anti_eligible)
    if abs(len(run.anti_correlated_subjects) - anti_target) > len(run.quarantine):
        problems.append(
            f"§4.3 anti-correlation quota: {len(run.anti_correlated_subjects)} != "
            f"{anti_target} of {anti_eligible} eligible"
        )

    for kind in HARD_CASE_KINDS:
        if not any(kind in s.hard_case_tags for s in run.subjects):
            problems.append(f"§4.2 kind never realized: {kind}")

    empty = [r.stratum_id for r in run.records.values() if r.accepted == 0]
    if empty:
        problems.append(f"§4.1 strata with no accepted subject: {empty}")

    for subject in run.subjects:
        text = subject.card.idiosyncrasies
        if text and len(text.strip()) < MIN_IDIOSYNCRASY_CHARS:
            problems.append(
                f"{subject.subject_id}: idiosyncrasy is {len(text.strip())} chars, "
                f"under the {MIN_IDIOSYNCRASY_CHARS}-char floor"
            )
        measured = set(coherence.violated_clusters(subject.card))
        if missing := set(subject.deliberate_anti_correlated) - measured:
            problems.append(
                f"{subject.subject_id}: planned to violate {sorted(missing)} but the "
                f"values do not — the quota is nominal there"
            )
        if set(subject.anti_correlated) != measured:
            problems.append(
                f"{subject.subject_id}: tag {sorted(subject.anti_correlated)} does not "
                f"match measured {sorted(measured)}"
            )
    return problems


# --- CLI --------------------------------------------------------------------


def _summary(run: GenerationRun) -> str:
    lines = [
        f"seed {run.seed} · auditor {run.auditor} · "
        f"{len(run.subjects)}/{run.requested} accepted",
        "",
        f"{'stratum':38} {'n':>3} {'att':>4} {'rej':>4} {'rate':>6}",
        "-" * 60,
    ]
    for record in run.records.values():
        lines.append(
            f"{record.stratum_id:38} {record.accepted:>3} {record.attempts:>4} "
            f"{record.rejections:>4} {record.regeneration_rate:>6.2f}"
        )
    hard = len(run.hard_case_subjects)
    anti = len(run.anti_correlated_subjects)
    lines += [
        "",
        f"hard cases       {hard}/{run.requested} ({hard / run.requested:.0%}, need ≥25%)",
        f"anti-correlated  {anti}/{run.requested} ({anti / run.requested:.0%}, target 15%)"
        f" + {len(run.incidental_violations)} incidental",
        f"quarantined      {len(run.quarantine)}",
        f"texture debt     {len(run.texture_debt)} card(s) owed an idiosyncrasy (§4.2)",
        f"outlier strata   {run.outlier_strata() or 'none'}",
    ]
    problems = verify(run)
    lines += ["", "verify: " + ("ok" if not problems else "")]
    lines += [f"  - {p}" for p in problems]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--json", metavar="PATH", help="write the full run as JSON")
    parser.add_argument(
        "--idiosyncrasies", metavar="PATH",
        help="subject_id -> §8.5 text, from authoring/idiosyncrasies.py (step 3)",
    )
    args = parser.parse_args()

    authored = None
    if args.idiosyncrasies:
        with open(args.idiosyncrasies) as handle:
            authored = json.load(handle)

    run = generate(n=args.n, seed=args.seed, idiosyncrasies=authored)
    print(_summary(run))
    if args.json:
        with open(args.json, "w") as handle:
            json.dump(run.to_json(), handle, indent=2)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
