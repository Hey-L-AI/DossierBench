"""Per-field agreement functions (spec-v0 §8.1).

Each returns ``a_f`` in [0, 1], where 1 is exact agreement. Stdlib only — the scorer must
be runnable with `python3` and must never depend on a model.

The three metric families exist because the three field types fail differently:

* **sets** — Jaccard, because both a missed member and an invented member should cost, and
  neither should dominate.
* **4-level ordinals** — normalized distance, so an adjacent-level miss keeps 2/3 credit.
  Exact match on a 4-level scale would make the headline metric mostly measure whether the
  SUT and the anchor author round the same way (spec §2.2).
* **distributions** — 1 − Jensen-Shannon divergence, which is bounded, symmetric, and
  finite even when the supports differ. KL is none of those and would blow up the first
  time an SUT put zero mass on a bin ground truth uses.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from . import fields

#: Distributions must sum to 1 within this tolerance to be considered well-formed.
DIST_TOLERANCE = 1e-6


def jaccard(predicted: Iterable[str], gold: Iterable[str]) -> float:
    """|A ∩ B| / |A ∪ B|, with two empty sets counting as perfect agreement.

    The empty/empty case is deliberate and load-bearing. "This person has no dietary
    restrictions" is a real, correct claim about a person, and an SUT that says so should
    not be scored the same as one that invents a restriction.
    """
    a, b = set(predicted), set(gold)
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union)


def ordinal_agreement(field_id: str, predicted: str, gold: str) -> float:
    """1 − normalized level distance over a 4-level anchored ordinal.

    With 4 levels the distances are 0, 1/3, 2/3, 1 — so an adjacent miss scores 0.667 and
    an opposite-end miss scores 0. This is the "adjacent level scored as partial credit"
    rule of spec §8.1.
    """
    field = fields.BY_ID[field_id]
    span = len(field.levels) - 1
    if span <= 0:
        raise ValueError(f"{field_id} has no ordinal span")
    distance = abs(
        fields.ordinal_index(field_id, predicted) - fields.ordinal_index(field_id, gold)
    )
    return 1.0 - distance / span


def _normalize(dist: Mapping[str, float], bins: tuple[str, ...]) -> list[float]:
    """Project a bin->mass mapping onto the field's ordered bins and validate it.

    Missing bins are zero. Unknown bins, negative mass, and a total that isn't 1 are all
    errors rather than silently-repaired input: a malformed distribution from an SUT is a
    protocol violation, and repairing it would hide the violation from the scorer.
    """
    unknown = set(dist) - set(bins)
    if unknown:
        raise ValueError(f"unknown distribution bins {sorted(unknown)}; expected {bins}")
    values = [float(dist.get(b, 0.0)) for b in bins]
    if any(v < 0 for v in values):
        raise ValueError(f"negative mass in distribution: {dict(dist)}")
    total = sum(values)
    if abs(total - 1.0) > DIST_TOLERANCE:
        raise ValueError(f"distribution sums to {total}, expected 1.0: {dict(dist)}")
    return values


def jensen_shannon_divergence(p: list[float], q: list[float]) -> float:
    """JSD with log base 2, so the result is bounded in [0, 1].

    Base 2 matters: base e would top out at ln 2 ≈ 0.693 and ``1 − JSD`` would never reach
    0 for maximally disjoint distributions, quietly compressing the bottom of the scale.
    """
    if len(p) != len(q):
        raise ValueError("distributions must share a support")

    def kl(a: list[float], b: list[float]) -> float:
        total = 0.0
        for ai, bi in zip(a, b):
            if ai > 0.0:
                # bi is a midpoint mass here, so bi == 0 implies ai == 0; the guard above
                # already skipped that case.
                total += ai * math.log2(ai / bi)
        return total

    m = [(pi + qi) / 2.0 for pi, qi in zip(p, q)]
    return 0.5 * kl(p, m) + 0.5 * kl(q, m)


def distribution_agreement(
    field_id: str, predicted: Mapping[str, float], gold: Mapping[str, float]
) -> float:
    """1 − JSD over the field's declared bins."""
    bins = fields.BY_ID[field_id].levels
    p = _normalize(predicted, bins)
    q = _normalize(gold, bins)
    return 1.0 - jensen_shannon_divergence(p, q)


#: A bin holds "real" mass, for support-counting, above this share. Below it the mass is
#: rounding or a token hedge, not a bin the twin is claiming the person visits.
SUPPORT_THRESHOLD = 0.02
#: At or above this on a single bin, the "distribution" is a point value wearing a
#: distribution's clothes — a mode guess, not a spread. See `docs/distribution-shape.md`.
SPIKE_THRESHOLD = 0.90


@dataclass(frozen=True)
class DistributionShape:
    """The *shape* of one distribution, apart from its agreement with any gold.

    This is a diagnostic, deliberately NOT consumed by `agreement`. 1 − JSD is scale-
    compressed (see the module docstring and `weak_abstention_credit`), so a spike on the
    right mode scores almost as well as the true spread — the score is nearly blind to the
    thing the distribution fields exist to measure. These numbers restore that axis so a
    twin's distribution can be compared to the persona's on shape, separately from JSD.
    """

    field_id: str
    masses: tuple[float, ...]          # over the field's ordered bins, summing to 1
    support: int                       # bins carrying more than SUPPORT_THRESHOLD
    zero_bins: int                     # bins with exactly no mass — each an implicit "never"
    peak_mass: float                   # largest single-bin share
    entropy: float                     # base-2 Shannon entropy, in bits
    is_spike: bool                     # peak_mass >= SPIKE_THRESHOLD


def distribution_shape(field_id: str, dist: Mapping[str, float]) -> DistributionShape:
    """Shape statistics for one well-formed distribution over ``field_id``'s bins.

    ``dist`` is validated by `_normalize`, so a malformed one raises here exactly as it
    would in the scorer — the diagnostic never quietly repairs what the score would reject.
    ``zero_bins`` is called out because a 0.00 bin is not "rare" but "never" under
    `fields.DISTRIBUTION_READING_RULE`: every zero a spike invents is an implicit claim the
    person never does that, which a reader can contradict from a single session.
    """
    bins = fields.BY_ID[field_id].levels
    masses = _normalize(dist, bins)
    entropy = sum(-m * math.log2(m) for m in masses if m > 0.0)
    return DistributionShape(
        field_id=field_id,
        masses=tuple(masses),
        support=sum(1 for m in masses if m > SUPPORT_THRESHOLD),
        zero_bins=sum(1 for m in masses if m == 0.0),
        peak_mass=max(masses),
        entropy=entropy,
        is_spike=max(masses) >= SPIKE_THRESHOLD,
    )


def exact_agreement(predicted: object, gold: object) -> float:
    """1.0 iff equal. Used for the boolean gating constraint."""
    return 1.0 if predicted == gold else 0.0


# --- weak-band abstention credit (spec §8.1) --------------------------------
# What abstaining on a WEAK field is worth. Derived rather than declared: a magic constant
# here would be the single most contestable number in the benchmark, so it is computed from
# the field's own metric under an explicitly stated model of what "weak" means.
#
# **The model.** A WEAK field is one the transcript narrows but does not pin (§7.3). v0 takes
# the minimal such case — the evidence leaves exactly **one remaining binary ambiguity**, so
# the licensed set has two members and nothing in the transcript favours either. The credit
# is the expected agreement of a uniform choice between them:
#
#     credit = 0.5 * 1.0 + 0.5 * a_f(the other licensed value, gold)
#
# **Why that anchor and not a round number.** It makes a *calibrated* system indifferent
# between committing and abstaining, which is the property that stops the scorer from paying
# for either behaviour as such. A system whose weak-band commits score *below* what the
# evidence licenses gains by abstaining; one that scores above it gains by committing. So the
# weak band measures calibration against the evidence rather than rewarding a fixed posture —
# and the §11 criterion becomes falsifiable there, because a system can fail it.
#
# **What is approximate.** The one-binary-ambiguity shape. When §7.3 begins emitting real
# licensed sets per weak field (spec §13, the partial-commitment path), the same definition
# applies unchanged to the real set and these functions take it as an argument instead of
# assuming it. Nothing about the definition changes then — only its input gets sharper.

def _adjacent_level_expectation(span: int) -> float:
    """The credit, derived for a `span+1`-level ordinal whose licensed set is two adjacent
    levels: half the time the choice is right, half the time it is one level off."""
    return 0.5 * (1.0 + (1.0 - 1.0 / span))


#: Credit for abstaining on a WEAK field. Derived, not declared: it is
#: `_adjacent_level_expectation(3)` = 5/6, the exact calibrated expectation for a 4-level
#: ordinal — the field type that dominates the card (13 of the 20 scored preference fields).
WEAK_CREDIT: float = _adjacent_level_expectation(3)


# --- weak-band discernment (diagnostic, not scored) -------------------------
# The scorer neither rewards nor punishes a *posture* on the weak band: committing scores the
# committed value's agreement, abstaining scores `WEAK_CREDIT`, and the two are calibrated to
# be equal for a system reading exactly what the evidence licenses (see the derivation above).
# That is the right scoring rule and it stays. But it means the headline score is deliberately
# blind to a real question about a system: *when the evidence only hints, how well does the SUT
# read the hint?* Two systems can post the same `A(t)` on the weak band — one by abstaining
# everywhere and banking `WEAK_CREDIT`, the other by committing and reading the soft signal
# correctly — and the score cannot tell them apart, because it was designed not to pay for
# either posture. This diagnostic restores that axis. Nothing here is consumed by `agreement`,
# `score_checkpoint`, or `F(t)`; it is read off the same `FieldOutcome`s the scorer already
# emits (see `scorer.FieldOutcome`) and reported beside the score, like `distribution_shape`.
#
# **The confound this is built around.** A WEAK field is one "a careful reader could abstain"
# on — the value is under-determined by construction. So raw weak-commit accuracy blends two
# things: genuine reading of the soft signal, and base-rate luck (committing the population's
# modal value, which happens to be right often and which the U-band fabrication rule exists to
# kill but the W band does not). A single accuracy number cannot separate them, so this reports
# accuracy *against two reference points*, both computed over the exact cells the SUT chose to
# commit on (paired, so the SUT's own selectivity is controlled for):
#
#   * `discernment` = commit_accuracy − chance_accuracy, where chance is a base-rate guesser
#     (`baselines.PopulationMode`) scored on those same cells. Positive means the SUT read more
#     than the population prior alone would give. This is the headline.
#   * `calibration` = commit_accuracy − `WEAK_CREDIT`, the scorer's own indifference point.
#     Positive means committing beat what the evidence licenses on average — the SUT *should*
#     commit here; negative means it would have scored better abstaining. This ties the
#     diagnostic back to the exact quantity `score_checkpoint` trades against.
#
# Read `commit_rate` alongside both: accuracy-given-commit rewards a system that only commits
# when sure, so a high accuracy at a low commit rate is discernment *of when to bet*, not of
# reading in general. The triple (commit_rate, accuracy, lift) is the interpretable unit, not
# any one of them alone.


@dataclass(frozen=True)
class WeakCell:
    """One weak preference field-checkpoint, from the SUT's and a base-rate guesser's view.

    ``agreement`` is the SUT's committed value's ``a_f`` against gold, or ``None`` when the SUT
    abstained. ``chance_agreement`` is a base-rate guesser's ``a_f`` on the *same* cell (the
    population-modal value against the same gold), or ``None`` when the guesser has no value to
    offer there. Kept per-cell rather than pre-aggregated so a caller can slice by metric family
    or checkpoint depth before reducing.
    """

    committed: bool
    agreement: float | None
    chance_agreement: float | None


@dataclass(frozen=True)
class WeakDiscernment:
    """How well a system reads the weak band, apart from what the score pays for it.

    Every field is ``None`` where its denominator is empty (no weak cells, or none committed),
    never 0.0 — an unmeasured axis is not a failing one. See the section comment above for how
    to read `discernment` (vs base-rate) against `calibration` (vs the licensed indifference
    point), and why neither is interpretable without `commit_rate`.
    """

    n: int                             # weak preference field-checkpoints observed
    committed: int                     # of those, how many the SUT committed a value on
    commit_rate: float | None          # committed / n
    commit_accuracy: float | None      # mean a_f over committed cells — the raw number
    chance_accuracy: float | None      # base-rate guesser's mean a_f over those same cells
    discernment: float | None          # commit_accuracy − chance_accuracy (headline lift)
    licensed_credit: float             # WEAK_CREDIT, the scorer's indifference point
    calibration: float | None          # commit_accuracy − licensed_credit
    wrong_rate: float | None           # of committed, share scored below error_threshold
    error_threshold: float


def weak_discernment(
    cells: Sequence[WeakCell], *, error_threshold: float = 0.5
) -> WeakDiscernment:
    """Aggregate weak-band cells into the discernment diagnostic.

    ``error_threshold`` mirrors `scorer.ERROR_THRESHOLD` (the below-half-agreement cut the
    error decomposition uses); it is a parameter rather than an import so this module keeps its
    stdlib-only, scorer-independent contract — pass `scorer.ERROR_THRESHOLD` at the call site.

    Both reference points are computed over the *committed* cells only, so the comparison is
    against what a guesser (or the licensed floor) scores on exactly the cells the SUT chose to
    bet on — the SUT's selectivity is held fixed rather than being able to flatter the number.
    """
    n = len(cells)
    committed = [c for c in cells if c.committed]
    n_committed = len(committed)

    def _mean(xs: list[float]) -> float | None:
        return sum(xs) / len(xs) if xs else None

    commit_accuracy = _mean([c.agreement for c in committed if c.agreement is not None])
    chance_accuracy = _mean(
        [c.chance_agreement for c in committed if c.chance_agreement is not None]
    )
    discernment = (
        commit_accuracy - chance_accuracy
        if commit_accuracy is not None and chance_accuracy is not None
        else None
    )
    calibration = (
        commit_accuracy - WEAK_CREDIT if commit_accuracy is not None else None
    )
    scored = [c.agreement for c in committed if c.agreement is not None]
    wrong_rate = (
        sum(1 for a in scored if a < error_threshold) / len(scored) if scored else None
    )
    return WeakDiscernment(
        n=n,
        committed=n_committed,
        commit_rate=n_committed / n if n else None,
        commit_accuracy=commit_accuracy,
        chance_accuracy=chance_accuracy,
        discernment=discernment,
        licensed_credit=WEAK_CREDIT,
        calibration=calibration,
        wrong_rate=wrong_rate,
        error_threshold=error_threshold,
    )


def weak_abstention_credit(field_id: str, gold: object) -> float:
    """Credit for abstaining on a WEAK field (spec §8.1). One constant for every field type.

    **A per-metric credit was computed and rejected.** Applying the licensed-set definition
    separately to each metric family gives ordinals 0.833, sets 0.625–0.854 (with gold set
    size) and **distributions 0.95–0.97**. That last group is not a real difference in how
    much the evidence narrows the field — it is 1−JSD's scale compression, which the module
    docstring already notes keeps agreement high even for quite different distributions. A
    0.97 credit would make abstaining on the three distribution fields near-free money, so
    an SUT would blanket-abstain there and those fields would stop being measured. Paying
    postures differently because of a metric's scale, rather than because of the evidence, is
    exactly the artifact the weak band exists to avoid.

    So v0 pays one derived constant everywhere. It is right for 13 of 20 fields by
    construction, generous for sets and stingy for distributions relative to their own
    metrics, and — being a single number — it can be swept for sensitivity alongside λ,
    which three interacting numbers could not be.

    ``field_id`` and ``gold`` are taken but unused. They are the signature the real-licensed-
    set version needs (spec §13): once §7.3 emits the licensed set per weak field, this
    returns the expectation over that set and nothing else in the scorer changes.
    """
    return WEAK_CREDIT


def agreement(field_id: str, predicted: object, gold: object) -> float:
    """Dispatch to the metric declared for ``field_id`` in the field registry.

    ``predicted`` must already be a committed value — callers strip the ``"unknown"``
    sentinel before getting here, because abstention is a *band* decision in `scorer.py`,
    not a value with an agreement score.
    """
    field = fields.BY_ID[field_id]
    if field.metric == fields.METRIC_JACCARD:
        if isinstance(predicted, str) or isinstance(gold, str):
            raise TypeError(f"{field_id} is set-valued; got a bare string")
        return jaccard(predicted, gold)          # type: ignore[arg-type]
    if field.metric == fields.METRIC_ORDINAL:
        return ordinal_agreement(field_id, str(predicted), str(gold))
    if field.metric == fields.METRIC_JSD:
        return distribution_agreement(field_id, predicted, gold)  # type: ignore[arg-type]
    if field.metric == fields.METRIC_EXACT:
        return exact_agreement(predicted, gold)
    raise ValueError(f"unhandled metric {field.metric!r} for {field_id}")
