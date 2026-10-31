"""The abstention-incentive results of `ablation.py`, held to the real scorer (spec §11).

Three jobs.

**Verify the closed forms.** `expected()`, `break_even_detection`, `exchange_rate` and
`weak_band_commit_pays` must reproduce what `scorer.score_checkpoint` actually computes, or
the numbers in the ADRs describe a metric nobody is shipping.

**Pin the properties the 2026-08-10 rescoring was chosen for.** The exchange rate is
mix-independent (ADR-0002); the weak-band decision depends only on `a_W` vs the credit
(ADR-0001). Both were *false* under the previous rule, so a regression here is a regression to
a scoring rule with known defects — the assertions carry that in their docstrings.

**Pin the defects that remain.** §11's `U`-band ablation is still mechanical, and the clamp
still flattens the gradient for gross fabricators. Those tests are not testing features; they
hold the known-bad behaviour in place so it cannot be quietly "fixed" without the reasoning
being revisited.
"""

from __future__ import annotations

import pytest

from persona_twin import ablation, fields, metrics, scorer
from persona_twin.ablation import BandMix, Policy
from persona_twin.card import PersonaCard
from persona_twin.evidence import Grade

REPLICATES = 300

#: Mixes chosen to span the corners the closed forms have to survive: no weak band, no strong
#: band, no unevidenced band, and a fully-mixed case.
MIXES = (
    BandMix(2, 10, "sparse"),
    BandMix(5, 8, "medium-early"),
    BandMix(11, 5, "medium-late"),
    BandMix(15, 3, "rich"),
    BandMix(10, 0, "no-weak-band"),
    BandMix(0, 10, "no-strong-band"),
    BandMix(12, 8, "no-unevidenced-band"),
)
TIERS = MIXES[:4]

#: A mix whose unevidenced band dwarfs its evidenced one, so the count-based penalty drives
#: `F` onto the clamp floor. Used for the clamp tests.
FLOORED = BandMix(2, 2, "tiny-evidenced")


# --- the instrument itself --------------------------------------------------


@pytest.mark.parametrize("mix", MIXES, ids=lambda m: m.name)
def test_synthetic_subject_realizes_the_requested_band_mix(mix: BandMix) -> None:
    """If the fixture's bands drift from the requested mix, every number here is wrong."""
    subject = ablation.synthetic_subject(mix, seed=1)
    bands = tuple(
        len(subject.evidence.preference_band(0, grade))
        for grade in (Grade.STRONG, Grade.WEAK, Grade.UNEVIDENCED)
    )
    assert bands == (mix.strong, mix.weak, mix.unevidenced)


def test_band_membership_is_shuffled_across_seeds() -> None:
    """Fixed band membership would make `a_strong` an artifact of which metrics landed in
    the strong band, since Jaccard over a 39-member vocabulary and a 4-level ordinal behave
    nothing alike under corruption."""
    mix = BandMix(5, 5)
    bands = {
        ablation.synthetic_subject(mix, seed=seed).evidence.preference_band(0, Grade.STRONG)
        for seed in range(10)
    }
    assert len(bands) > 1


def test_the_instrument_reproduces_the_oracle_ceiling() -> None:
    """A perfect detector with perfect values must score 1.0 — the same ceiling
    `EvidenceOracle` establishes. This is what certifies the instrument is measuring the
    scorer rather than its own noise."""
    perfect = Policy(
        commit_strong=1.0, commit_weak=1.0, commit_unevidenced=0.0,
        exact_rate_strong=1.0, exact_rate_weak=1.0,
    )
    for mix in MIXES:
        if not mix.evidenced:
            continue
        measured = ablation.simulate(mix, perfect, lam=scorer.DEFAULT_LAMBDA, replicates=50)
        assert measured.a == pytest.approx(1.0)
        assert measured.f == pytest.approx(1.0)
        assert measured.f_skill == pytest.approx(1.0)


def test_the_instrument_abstains_on_the_gating_track() -> None:
    """Constraints are scored separately (§8.2) and must stay out of the measurement."""
    subject = ablation.synthetic_subject(BandMix(10, 5), seed=3)
    emitted = ablation.NoisyDetector(subject, Policy(), seed=3).emit(0)
    assert not any(emitted.committed(field_id) for field_id in fields.CONSTRAINT_IDS)


# --- the closed form vs the real scorer ------------------------------------


@pytest.mark.parametrize("mix", MIXES, ids=lambda m: m.name)
@pytest.mark.parametrize("commit_weak", (0.0, 0.5, 1.0))
def test_closed_form_matches_the_scorer(mix: BandMix, commit_weak: float) -> None:
    """`expected()` must reproduce `score_checkpoint`'s aggregation, fed the agreements the
    scorer itself realized.

    A **fractional** weak-commit rate is included, which the pre-rescoring version could not
    handle: abstaining used to remove the field from `A`'s denominator, making `A` a ratio of
    two random variables. Since ADR-0001 the denominator is `mix.evidenced` and the closed
    form is exact everywhere.
    """
    policy = Policy(
        commit_strong=0.8, commit_weak=commit_weak, commit_unevidenced=0.3,
        exact_rate_strong=0.85, exact_rate_weak=0.55,
    )
    lam = scorer.DEFAULT_LAMBDA
    measured = ablation.simulate(mix, policy, lam=lam, replicates=REPLICATES)
    predicted = ablation.expected(
        mix,
        commit_strong=policy.commit_strong,
        commit_weak=commit_weak,
        commit_unevidenced=policy.commit_unevidenced,
        a_strong=measured.a_strong,
        a_weak=measured.a_weak,
        lam=lam,
    )

    if measured.a is None:
        assert predicted.a is None
        return
    assert predicted.a == pytest.approx(measured.a, abs=0.02)
    assert predicted.penalty == pytest.approx(measured.penalty, abs=0.03)
    assert predicted.f_raw == pytest.approx(measured.f_raw, abs=0.04)


# --- ADR-0002: the exchange rate is mix-independent ------------------------


@pytest.mark.parametrize("lam", scorer.REPORTED_LAMBDAS)
def test_the_exchange_rate_is_the_same_in_every_tier(lam: float) -> None:
    """The property ADR-0002 exists to deliver.

    Under the previous rate-based penalty this ran 1.24 (sparse) → 0.21 (rich) and crossed 1,
    meaning the scorer paid an unsure system to guess in the sparse tier and to abstain in
    rich. Pricing both errors on the same denominator collapses it to `a_S/λ`.
    """
    rates = {
        ablation.exchange_rate(mix, lam, a_strong=0.92) for mix in TIERS
    }
    assert len(rates) == 1
    assert rates.pop() == pytest.approx(0.92 / lam)


def test_the_exchange_rate_agrees_with_the_marginal_costs() -> None:
    for mix in TIERS:
        for lam in scorer.REPORTED_LAMBDAS:
            missed, fabricated = ablation.marginal_costs(mix, lam, a_strong=0.92)
            rate = ablation.exchange_rate(mix, lam, a_strong=0.92)
            assert missed is not None and fabricated is not None and rate is not None
            assert rate == pytest.approx(missed / fabricated)


@pytest.mark.parametrize("mix", TIERS, ids=lambda m: m.name)
def test_marginal_costs_match_the_scorer(mix: BandMix) -> None:
    """The per-field costs must be what the scorer charges, not what the algebra hopes."""
    lam = scorer.DEFAULT_LAMBDA
    subject = ablation.synthetic_subject(mix, seed=5)
    gold = subject.gold_at(0)
    evidenced = {
        field_id: gold.get(field_id)
        for field_id in fields.PREFERENCE_IDS
        if subject.evidence.grade(0, field_id) != Grade.UNEVIDENCED
    }

    def card(values: dict[str, object]) -> PersonaCard:
        full: dict[str, object] = {f: fields.UNKNOWN for f in fields.ALL_FIELDS_IDS}
        full.update(values)
        return PersonaCard(subject.subject_id, full)

    baseline = scorer.score_checkpoint(subject, 0, card(evidenced), lam=lam)
    assert baseline.a == pytest.approx(1.0)
    missed, fabricated = ablation.marginal_costs(mix, lam, a_strong=1.0)

    if mix.strong:
        one_strong = subject.evidence.preference_band(0, Grade.STRONG)[0]
        withheld = scorer.score_checkpoint(
            subject, 0,
            card({k: v for k, v in evidenced.items() if k != one_strong}),
            lam=lam,
        )
        assert missed is not None
        assert (baseline.f_raw - withheld.f_raw) == pytest.approx(missed, abs=1e-9)

    if mix.unevidenced:
        one_unevidenced = subject.evidence.preference_band(0, Grade.UNEVIDENCED)[0]
        added = dict(evidenced)
        added[one_unevidenced] = gold.get(one_unevidenced)
        fabricating = scorer.score_checkpoint(subject, 0, card(added), lam=lam)
        assert fabricated is not None
        assert (baseline.f_raw - fabricating.f_raw) == pytest.approx(fabricated, abs=1e-9)


def test_the_cost_of_a_fabrication_no_longer_depends_on_the_unevidenced_band_size() -> None:
    """Same evidenced band, different amounts left unevidenced: one fabrication costs the
    same. Under the rate-based penalty it cost `λ/n_U`, so it got cheaper the sparser the
    transcript — the defect that made guessing pay in the sparse tier."""
    lam = scorer.DEFAULT_LAMBDA
    # At a fixed card size n_U cannot vary independently of the evidenced band, so the claim
    # is tested as the formula's shape: the cost is λ/n_evidenced with no n_U term at all.
    for mix in MIXES:
        _, cost = ablation.marginal_costs(mix, lam, a_strong=0.9)
        if not mix.evidenced:
            assert cost is None
            continue
        assert cost == pytest.approx(lam / mix.evidenced)

    # Two mixes with the same evidenced band size and different n_U therefore agree, which is
    # what fails under a `λ/n_U` penalty.
    same_evidenced = [m for m in MIXES if m.evidenced == 12]
    assert len({m.unevidenced for m in same_evidenced}) == 1 or all(
        ablation.marginal_costs(m, lam, 0.9)[1] == pytest.approx(lam / 12)
        for m in same_evidenced
    )


# --- ADR-0002: break-even detection ---------------------------------------


def _always_commit(mix: BandMix, lam: float) -> ablation.Measured:
    return ablation.simulate(
        mix, Policy(commit_strong=1.0, commit_weak=1.0, commit_unevidenced=1.0,
                    exact_rate_strong=0.85, exact_rate_weak=0.55),
        lam=lam, replicates=REPLICATES,
    )


def _detector(mix: BandMix, lam: float, p: float) -> ablation.Measured:
    return ablation.simulate(
        mix, Policy(commit_strong=p, commit_weak=1.0, commit_unevidenced=1.0 - p,
                    exact_rate_strong=0.85, exact_rate_weak=0.55),
        lam=lam, replicates=REPLICATES,
    )


@pytest.mark.parametrize("mix", TIERS, ids=lambda m: m.name)
@pytest.mark.parametrize("lam", scorer.REPORTED_LAMBDAS)
def test_break_even_detection_predicts_the_crossover(mix: BandMix, lam: float) -> None:
    """`p* = n_S·a_S/(λ·n_U + n_S·a_S)` must be where detect-and-abstain actually crosses
    always-commit under the real scorer.

    Compared on `f_raw`, since `p*` is derived from the unclamped `A − penalty` and the clamp
    truncates one side of the comparison wherever a system sits on the floor.
    """
    always = _always_commit(mix, lam)
    assert always.f_raw is not None
    p_star = ablation.break_even_detection(mix, lam, always.a_strong)
    assert p_star is not None

    margin = 0.12
    if not (margin < p_star < 1.0 - margin):
        pytest.skip(f"p* = {p_star:.3f} is too close to a bound to probe either side")

    below = _detector(mix, lam, p_star - margin)
    above = _detector(mix, lam, p_star + margin)
    assert below.f_raw is not None and above.f_raw is not None
    assert below.f_raw < always.f_raw
    assert above.f_raw > always.f_raw


def test_break_even_now_depends_on_the_unevidenced_band_size() -> None:
    """And in the right direction. Under the previous rate-based penalty `n_U` cancelled out
    of `p*` entirely, which was the tell that the penalty was normalized wrongly: the amount
    a detector *protects* cannot be irrelevant to whether running one is worth it."""
    lam = scorer.DEFAULT_LAMBDA
    much_unevidenced = ablation.break_even_detection(BandMix(4, 4), lam, 0.9)   # n_U = 12
    little_unevidenced = ablation.break_even_detection(BandMix(4, 14), lam, 0.9)  # n_U = 2
    assert much_unevidenced is not None and little_unevidenced is not None
    assert much_unevidenced < little_unevidenced


# --- ADR-0001: the weak band ----------------------------------------------


@pytest.mark.parametrize("mix", TIERS, ids=lambda m: m.name)
def test_weak_band_commit_pays_iff_it_beats_the_credit(mix: BandMix) -> None:
    """Verified against the scorer: committing across `W(t)` raises `A` iff
    `a_W > WEAK_CREDIT`."""
    lam = scorer.DEFAULT_LAMBDA
    for exact_weak in (0.15, 0.95):
        shared = dict(
            commit_strong=1.0, commit_unevidenced=0.0,
            exact_rate_strong=0.9, exact_rate_weak=exact_weak,
        )
        committing = ablation.simulate(
            mix, Policy(commit_weak=1.0, **shared), lam=lam, replicates=REPLICATES
        )
        abstaining = ablation.simulate(
            mix, Policy(commit_weak=0.0, **shared), lam=lam, replicates=REPLICATES
        )
        assert committing.a is not None and abstaining.a is not None

        predicted = ablation.weak_band_commit_pays(mix, a_weak=committing.a_weak)
        assert predicted == pytest.approx(committing.a - abstaining.a, abs=0.02)
        assert (predicted > 0) == (committing.a_weak > metrics.WEAK_CREDIT)


def test_the_weak_band_incentive_no_longer_flips_with_system_quality() -> None:
    """The defect ADR-0001 fixes, pinned as its absence.

    Two systems with **identical** weak-band accuracy and very different strong-band accuracy
    must now get the same weak-band verdict. Under the previous rule the comparison was
    `a_W > c_S·a_S`, so the strong system was paid to abstain and the weak one paid to guess
    for identical behaviour on identical evidence.
    """
    mix = BandMix(8, 6)
    strong_system = ablation.weak_band_commit_pays(mix, a_weak=0.70)
    weak_system = ablation.weak_band_commit_pays(mix, a_weak=0.70)
    assert strong_system == weak_system

    # And end-to-end through the scorer, where a_S genuinely differs.
    lam = scorer.DEFAULT_LAMBDA
    deltas = []
    for exact_strong in (0.95, 0.25):
        shared = dict(
            commit_strong=1.0, commit_unevidenced=0.0,
            exact_rate_strong=exact_strong, exact_rate_weak=0.4,
        )
        committing = ablation.simulate(
            mix, Policy(commit_weak=1.0, **shared), lam=lam, replicates=REPLICATES
        )
        abstaining = ablation.simulate(
            mix, Policy(commit_weak=0.0, **shared), lam=lam, replicates=REPLICATES
        )
        assert committing.a is not None and abstaining.a is not None
        deltas.append(committing.a - abstaining.a)
    assert deltas[0] == pytest.approx(deltas[1], abs=0.02)
    assert all(d < 0 for d in deltas), "a_W of 0.4 should lose to the credit in both"


def test_the_credit_is_derived_from_the_ordinal_case_not_declared() -> None:
    """`WEAK_CREDIT` must stay tied to its derivation, so a future edit to the ordinal metric
    cannot silently leave the credit behind."""
    assert metrics.WEAK_CREDIT == pytest.approx(5.0 / 6.0)
    ordinal = next(
        f for f in fields.PREFERENCE_FIELDS if f.metric == fields.METRIC_ORDINAL
    )
    exact = 1.0
    adjacent = metrics.ordinal_agreement(
        ordinal.field_id, ordinal.levels[0], ordinal.levels[1]
    )
    assert metrics.WEAK_CREDIT == pytest.approx(0.5 * (exact + adjacent))


def test_the_credit_is_uniform_across_metric_families() -> None:
    """A per-metric credit was computed and rejected — 1−JSD's compression put distribution
    fields at 0.95–0.97, which would have made abstaining there near-free money. See
    `metrics.weak_abstention_credit`."""
    values = {
        metrics.weak_abstention_credit(f.field_id, None)
        for f in fields.PREFERENCE_FIELDS
    }
    assert values == {metrics.WEAK_CREDIT}


# --- ADR-0003: the do-nothing floor --------------------------------------


@pytest.mark.parametrize("mix", MIXES, ids=lambda m: m.name)
def test_the_predicted_floor_matches_the_scorer(mix: BandMix) -> None:
    do_nothing = ablation.simulate(
        mix, Policy(commit_strong=0.0, commit_weak=0.0, commit_unevidenced=0.0),
        lam=scorer.DEFAULT_LAMBDA, replicates=50,
    )
    predicted = ablation.abstain_everything_floor(mix)
    if predicted is None:
        assert do_nothing.a is None
        return
    assert do_nothing.a == pytest.approx(predicted)
    assert do_nothing.f_raw == pytest.approx(predicted)
    # Zero skill by construction — the property that keeps §11's abstain-everything sanity
    # check meaningful now that the raw floor is non-zero.
    assert do_nothing.f_skill == pytest.approx(0.0)


def test_the_floor_is_high_in_a_weak_heavy_tier() -> None:
    """The cost of ADR-0001, quantified: this is why the floor must be published beside `F`.
    A reader who does not know it cannot tell a real 0.69 from a do-nothing 0.69."""
    sparse_floor = ablation.abstain_everything_floor(BandMix(2, 10))
    rich_floor = ablation.abstain_everything_floor(BandMix(15, 3))
    assert sparse_floor is not None and rich_floor is not None
    assert sparse_floor > 0.65
    assert rich_floor < 0.20


# --- what the rescoring did NOT fix --------------------------------------


@pytest.mark.parametrize("mix", TIERS, ids=lambda m: m.name)
@pytest.mark.parametrize("lam", scorer.REPORTED_LAMBDAS)
def test_the_spec_11_u_band_ablation_is_still_mechanical(mix: BandMix, lam: float) -> None:
    """Unchanged by the rescoring, and unchangeable while the `U`-band rule stands.

    A commit in `U(t)` earns nothing in `A(t)`, so the guess-instead-of-abstain delta is
    exactly `λ·Δn_fab/n_evidenced` for every system on every dataset. §11's criterion cannot
    fail, which is why the falsifiable criterion is the weak-band one. This test pins the
    defect: if a scorer change ever routes a `U`-band commit into `A`, it fails here with the
    reasoning attached.
    """
    abstaining, guessing = ablation.guess_instead_of_abstain(
        mix, Policy(exact_rate_strong=0.85, exact_rate_weak=0.55), lam=lam, replicates=100
    )
    assert abstaining.f_raw is not None and guessing.f_raw is not None
    # Exactly paired: A is untouched by the U-band policy.
    assert abstaining.a == pytest.approx(guessing.a, abs=1e-9)
    expected_delta = (
        lam * (guessing.fabrication_count - abstaining.fabrication_count) / mix.evidenced
    )
    assert (abstaining.f_raw - guessing.f_raw) == pytest.approx(expected_delta, abs=1e-9)


def test_the_clamp_still_flattens_the_gradient_for_gross_fabricators() -> None:
    """`F = clamp(A − penalty, 0, 1)`. The count-based penalty is unbounded, so a system that
    invents most of a thinly-evidenced card is pinned at 0 and fabricating more is free.

    This is the known cost of ADR-0002 and the reason `f_raw` is a first-class reported
    number rather than a debugging aid. Removing the clamp is a v1 candidate.
    """
    assert ablation.clamp_kills_gradient(
        FLOORED, scorer.DEFAULT_LAMBDA, a_strong=0.9, a_weak=0.8
    )
    # A well-evidenced card at the same λ keeps a live gradient — it is a floor effect, not a
    # property of λ.
    assert not ablation.clamp_kills_gradient(
        BandMix(15, 3), scorer.DEFAULT_LAMBDA, a_strong=0.9, a_weak=0.8
    )


def test_f_raw_discriminates_where_the_clamped_f_cannot() -> None:
    lam = scorer.DEFAULT_LAMBDA
    partial = ablation.simulate(
        FLOORED, Policy(commit_strong=1.0, commit_weak=1.0, commit_unevidenced=0.5,
                        exact_rate_strong=0.9, exact_rate_weak=0.6),
        lam=lam, replicates=REPLICATES,
    )
    total = ablation.simulate(
        FLOORED, Policy(commit_strong=1.0, commit_weak=1.0, commit_unevidenced=1.0,
                        exact_rate_strong=0.9, exact_rate_weak=0.6),
        lam=lam, replicates=REPLICATES,
    )
    # Both policies sit on the clamp floor: F collapses to ~0 for both, hiding a separation
    # F_raw keeps. F is a mean of per-replicate *clamped* scores, so a rare replicate whose
    # partition clears the floor leaves partial.f a hair above 0 rather than exactly 0 — the
    # point is that the clamp compresses the ~2.0 F_raw gap to a few ten-thousandths.
    assert partial.f < 1e-2
    assert total.f == pytest.approx(0.0)          # clamped: indistinguishable
    assert partial.f_raw is not None and total.f_raw is not None
    assert partial.f_raw - total.f_raw > 1.0      # raw: clearly separated


# --- evidence density: ADR-0004 ------------------------------------------


@pytest.mark.parametrize("mix", TIERS, ids=lambda m: m.name)
def test_worst_raw_is_reached_by_a_total_fabricator(mix: BandMix) -> None:
    """`worst_raw` claims to be the floor of the reported number, so it has to agree with
    what the closed form gives for the policy that reaches it: commit on every unevidenced
    field, earn nothing on the evidenced ones."""
    predicted = ablation.worst_raw(mix, scorer.DEFAULT_LAMBDA)
    actual = ablation.expected(
        mix,
        commit_strong=1.0, commit_weak=1.0, commit_unevidenced=1.0,
        a_strong=0.0, a_weak=0.0, lam=scorer.DEFAULT_LAMBDA,
    )
    assert predicted is not None and actual.f_raw is not None
    assert predicted == pytest.approx(actual.f_raw)


def test_the_unbounded_magnitude_is_a_toy_artifact() -> None:
    """ADR-0004's central claim. The −8.7 in ADR-0002 is a property of a 3-of-20 evidence
    mix; on every tier profile §7.4 would accept, `F_raw` stays inside a narrow band."""
    toy = ablation.worst_raw(ablation.TOY_MIX, scorer.DEFAULT_LAMBDA)
    assert toy is not None and toy < -5.0

    for mix in TIERS:
        worst = ablation.worst_raw(mix, scorer.DEFAULT_LAMBDA)
        assert worst is not None
        assert worst > -1.0, f"{mix.name} reaches {worst}"


def test_skill_is_less_bounded_than_f_raw_and_worst_where_the_floor_is_highest() -> None:
    """The fact that reopens ADR-0003 rather than settling it: `f_skill` amplifies by
    1/(1 − floor), and the floor peaks in the weak-heavy sparse tier — so the number
    proposed as the more legible headline is also the one with the wider range."""
    sparse, rich = BandMix(2, 10, "sparse"), BandMix(15, 3, "rich")
    amp_sparse = ablation.skill_amplification(sparse)
    amp_rich = ablation.skill_amplification(rich)
    assert amp_sparse is not None and amp_rich is not None
    assert amp_sparse > amp_rich > 1.0

    raw = ablation.worst_raw(sparse, scorer.DEFAULT_LAMBDA)
    floor = ablation.abstain_everything_floor(sparse)
    assert raw is not None and floor is not None
    worst_skill = (raw - floor) * amp_sparse
    assert worst_skill < raw, "skill should reach further below zero than F_raw"


@pytest.mark.parametrize("bound", [0.5, 1.0, 2.0, 4.0])
@pytest.mark.parametrize("lam", scorer.REPORTED_LAMBDAS)
def test_min_evidenced_for_is_the_smallest_band_meeting_the_bound(
    bound: float, lam: float
) -> None:
    """The gate ADR-0004 proposes is only useful if the threshold it returns is tight:
    the band it names must meet the bound, and one field fewer must miss it."""
    n_e = ablation.min_evidenced_for(bound, lam)
    assert n_e is not None
    at = ablation.penalty_ceiling(BandMix(n_e, 0), lam)
    assert at is not None and at <= bound + 1e-9

    if n_e > 1:
        below = ablation.penalty_ceiling(BandMix(n_e - 1, 0), lam)
        assert below is not None and below > bound


def test_every_tier_profile_already_clears_the_proposed_gate() -> None:
    """The gate is meant to bite only on pathological checkpoints. If a tier mix failed it,
    it would be a change to the benchmark rather than a precondition on it."""
    threshold = ablation.min_evidenced_for(2.0, scorer.DEFAULT_LAMBDA)
    assert threshold is not None
    for mix in TIERS:
        assert mix.evidenced >= threshold, f"{mix.name} would be excluded"
    assert ablation.TOY_MIX.evidenced < threshold
