"""The spec §11 acceptance criteria that this build step can already check.

These are the sanity checks that decide whether the metric is coherent at all. Per the
literature review (§5.1), they are the kill gate: if `F(t)` does not behave this way on
hand-written toys, no amount of dataset generation will fix it.

Deferred to later build steps because they need a generation pipeline or a model:
audit pass rates per stratum, the reference SUT beating all three naive baselines,
paraphrase-stability, and run-to-run self-agreement.
"""

from __future__ import annotations

import pytest

from persona_twin import scorer, toy
from persona_twin.evidence import Grade
from persona_twin.baselines import (
    SUT, AbstainEverything, EvidenceOracle, PopulationMode, PromptedExtractor,
    RandomValid, run_trajectory,
)
from persona_twin.card import PersonaCard


def _population() -> PopulationMode:
    """Fit the modal card on a varied sample standing in for the public split.

    Deliberately *not* fit on the toys themselves: that is circular (the baseline would be
    fit on the very cards it is scored against) and it made population-mode a perfect
    oracle, which turned every check below vacuously true.
    """
    return PopulationMode.fit(toy.population_sample())


def test_population_mode_is_not_a_perfect_oracle_on_the_toys():
    """Guards the fixtures themselves. If the toys ever converge on the modal card, the
    §11 checks in this file keep passing while testing nothing."""
    population = _population()
    perfect = 0
    for build in toy.ALL_TOYS:
        subject = build()
        result = _score(population, subject)
        scorable = [c for c in result.checkpoints if c.scorable]
        if scorable and all(c.a == pytest.approx(1.0) for c in scorable):
            perfect += 1
    assert perfect < len(toy.ALL_TOYS), (
        "population-mode reconstructs every toy exactly; the fixtures are degenerate"
    )


def _score(sut, subject):
    """Score a baseline over a toy subject.

    `min_evidenced` opts out of ADR-0004's AUFC density gate because every toy checkpoint
    falls below the production threshold — see `toy.TOY_MIN_EVIDENCED`. The §11 criteria
    below are about the *ordering* of baselines, which needs a defined AUFC to compare.
    """
    emissions, byte_meter, token_meter = run_trajectory(sut, subject)
    return scorer.score_subject(
        subject, emissions, min_evidenced=toy.TOY_MIN_EVIDENCED,
        carried_state_bytes=byte_meter, carried_state_tokens=token_meter,
    )


# --- §11: abstain-everything ------------------------------------------------

def test_abstain_everything_has_perfect_abstention_calibration():
    for build in toy.ALL_TOYS:
        subject = build()
        result = _score(AbstainEverything(), subject)
        assert all(c.fab == 0.0 for c in result.checkpoints), subject.subject_id


def test_abstain_everything_has_zero_skill_but_a_non_zero_raw_score():
    """The other half of the §11 sanity check, restated for the post-ADR-0001 rule.

    Crediting weak abstention means a do-nothing card no longer scores 0 on `F` — it collects
    `WEAK_CREDIT` on every weak field, and in a weak-heavy tier that floor is high. The
    tension between calibration and fidelity is therefore checked on `f_skill`, which divides
    the floor out and is 0 for this baseline **by construction** (ADR-0003). Asserting
    `aufc == 0` would now be asserting that the weak band is empty.
    """
    subject = toy.evidence_ramp()
    result = _score(AbstainEverything(), subject)
    assert result.aufc_skill == pytest.approx(0.0)
    # And the raw number is the floor, not zero — the fact that makes publishing it mandatory.
    assert result.aufc > 0.0


def test_the_do_nothing_floor_is_published_on_every_checkpoint():
    """A reader cannot interpret F without it: 0.69 in a weak-heavy tier may be the floor."""
    for build in toy.ALL_TOYS:
        subject = build()
        result = _score(AbstainEverything(), subject)
        for checkpoint in result.checkpoints:
            if not checkpoint.scorable:
                continue
            assert checkpoint.a_floor is not None
            assert checkpoint.f_raw == pytest.approx(checkpoint.a_floor), subject.subject_id


# --- §11: population-mode ---------------------------------------------------

def test_population_mode_earns_non_trivial_agreement():
    """It must be an honest bar, not a straw man. With 4-level ordinals the modal card is
    meaningfully strong — that is intended (§8.8)."""
    subject = toy.evidence_ramp()
    result = _score(_population(), subject)
    mean_a = sum(c.a for c in result.checkpoints) / len(result.checkpoints)
    assert mean_a > 0.3, f"population-mode A(t) = {mean_a}, too weak to be an honest bar"


def test_population_mode_is_beaten_on_f_by_the_fabrication_penalty():
    """The check that the penalty bites: a baseline that never abstains must lose its
    agreement to λ·fab."""
    subject = toy.evidence_ramp()
    result = _score(_population(), subject)
    mean_a = sum(c.a for c in result.checkpoints) / len(result.checkpoints)
    assert result.aufc < mean_a, "fab penalty did not reduce F below A"


def test_population_mode_loses_to_the_oracle_on_f_but_can_rival_it_on_a():
    """Isolates what F is actually buying over A."""
    subject = toy.evidence_ramp()
    population = _score(_population(), subject)
    oracle_sut = EvidenceOracle(subject)
    oracle = _score(oracle_sut, subject)
    assert oracle.aufc > population.aufc


def test_population_mode_fabricates_on_every_unevidenced_field():
    subject = toy.never_evidenced()
    result = _score(_population(), subject)
    assert all(c.fab == pytest.approx(1.0) for c in result.checkpoints)


# --- §11: the penalty is what separates them --------------------------------

def test_raising_lambda_monotonically_punishes_fabrication():
    """Direct demonstration that λ is doing the work rather than some other asymmetry.

    Measured on a card with exactly **one** fabrication. Under the count-based penalty
    (ADR-0002) a baseline that commits on the whole card incurs a penalty far above 1 and
    clamps to 0 at every λ, so it can no longer show the gradient — the one-fabrication case
    can, and isolates the price of a single fabrication: λ/|S∪W|.
    """
    subject = toy.evidence_ramp()
    gold = subject.gold_at(0)
    evidenced = (
        subject.evidence.preference_band(0, Grade.STRONG)
        + subject.evidence.preference_band(0, Grade.WEAK)
    )
    unevidenced = subject.evidence.preference_band(0, Grade.UNEVIDENCED)

    card = PersonaCard.all_unknown(subject.subject_id)
    for field_id in evidenced:
        card.values[field_id] = gold.get(field_id)
    card.values[unevidenced[0]] = gold.get(unevidenced[0])   # one fabrication

    score = scorer.score_checkpoint(subject, 0, card)
    reported = sorted(scorer.REPORTED_LAMBDAS)
    scores = [score.f_by_lambda[candidate] for candidate in reported]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] > scores[-1], "lambda is not separating anything"
    for candidate in reported:
        assert score.f_by_lambda[candidate] == pytest.approx(
            1.0 - candidate / score.a_denominator
        )


def test_random_valid_is_beaten_by_population_mode():
    subject = toy.evidence_ramp()
    random_result = _score(RandomValid(seed=7), subject)
    population_result = _score(_population(), subject)
    assert population_result.aufc >= random_result.aufc


def test_the_ordering_of_all_baselines_is_the_expected_one():
    """oracle > abstain-everything > population-mode on **skill**, on every toy.

    Two things changed here on 2026-08-10, and both are the new rule working rather than a
    weakened assertion.

    *Skill, not raw AUFC.* Crediting weak abstention gives every card a non-zero floor that
    varies with the tier's band mix, so raw `F` is not comparable across subjects. `f_skill`
    divides the floor out (ADR-0003).

    *Abstain-everything now outranks population-mode.* Under the count-based penalty
    (ADR-0002) population-mode's 17 fabrications at checkpoint 0 cost 17·λ/|S∪W|, far more
    than the agreement they buy. Saying nothing beating inventing the whole card **is** this
    benchmark's thesis; the previous rate-based penalty capped the cost at λ and so ranked
    the inventor higher. §11's "population-mode is beaten on F" is still checked directly, in
    `test_population_mode_is_beaten_on_f_by_the_fabrication_penalty`.

    Random-valid is excluded from the ordering assertion: uniform draws over a closed
    vocabulary make it noisy by construction, and pinning its rank would be testing the
    seed rather than the metric.

    Subjects with no scorable checkpoint are skipped — AUFC is None there for every SUT,
    so there is no ordering to assert.
    """
    compared = 0
    for build in toy.ALL_TOYS:
        subject = build()
        oracle = _score(EvidenceOracle(subject), subject).aufc_skill
        population = _score(_population(), subject).aufc_skill
        abstain = _score(AbstainEverything(), subject).aufc_skill
        if oracle is None:
            assert population is None and abstain is None, subject.subject_id
            continue
        # On skill, not raw F, and abstain-everything now sits *above* population-mode
        # rather than below it — see the docstring.
        assert oracle >= abstain >= population, subject.subject_id
        compared += 1
    assert compared >= 3, "ordering was not actually exercised"


# --- §4.3: the anti-correlation quota does its job --------------------------

def test_cluster_inference_is_penalized_on_an_anti_correlated_subject():
    """The §4.3 mechanism, made concrete.

    On a subject who is adventurous *and* fiercely loyal, an SUT that infers
    `novelty_vs_loyalty` from `adventurousness` gets it wrong. That is the intended signal.
    The quota exists so this failure is detectable as inference-from-correlation rather
    than being indistinguishable from noise.
    """
    subject = toy.anti_correlated()
    gold = subject.gold_at(1)
    assert gold.get("adventurousness") == "seeks_unidentifiable"
    assert gold.get("novelty_vs_loyalty") == "same_few_places"

    card = PersonaCard.all_unknown(subject.subject_id)
    card.values.update({
        "adventurousness": gold.get("adventurousness"),
        "cuisines_liked": gold.get("cuisines_liked"),
        # The cluster-following guess: adventurous therefore novelty-seeking.
        "novelty_vs_loyalty": "almost_never_repeats",
    })
    score = scorer.score_checkpoint(subject, 1, card)
    outcome = next(o for o in score.outcomes if o.field_id == "novelty_vs_loyalty")
    assert outcome.agreement == 0.0
    assert outcome.error_kind == scorer.MISREADING


# --- state metering (§1.3) --------------------------------------------------

def test_stateless_baselines_report_zero_carried_state():
    subject = toy.evidence_ramp()
    for sut in (AbstainEverything(), RandomValid(), _population()):
        result = _score(sut, subject)
        assert result.peak_carried_state_tokens == 0


# --- aggregation (§8.9) -----------------------------------------------------

def test_aggregation_never_pools_across_tiers():
    """spec §8.9: AUFC is computed at different checkpoint densities per tier, so tier
    numbers are never pooled in headline figures."""
    subjects = [toy.evidence_ramp(), toy.never_evidenced(), toy.interference_case()]
    scores = [_score(AbstainEverything(), s) for s in subjects]
    cells = scorer.aggregate(scores)
    tiers = {cell.tier for cell in cells}
    assert tiers == {"medium", "sparse", "rich"}
    assert all(cell.n_subjects == 1 for cell in cells)


def test_aggregation_splits_by_simulator_model():
    """spec §7.1/§8.9: results are broken out by simulator, because Opus and Sonnet share
    training lineage and the split is a disclosed confound rather than a bias control."""
    a = toy.evidence_ramp()
    a.simulator_model = "opus"
    b = toy.evidence_ramp()
    b.simulator_model = "sonnet"
    cells = scorer.aggregate([_score(AbstainEverything(), s) for s in (a, b)])
    assert {cell.simulator_model for cell in cells} == {"opus", "sonnet"}


# --- the reference SUT ------------------------------------------------------

def test_prompted_extractor_is_the_sut_modules_extractor():
    """Baseline 4 is built (`sut.PromptedExtractor`) and this name still reaches it: §8.8 names
    it here, and the implementation sits next to the harness, the prompt and the parser because
    those four are one artifact.

    Constructed with an invoker rather than without one, so this test needs no key and makes no
    call — the model boundary is a single callable for exactly that reason."""
    from persona_twin import sut

    built = PromptedExtractor(model="fake", invoke=lambda model, system, prompt: "{}")
    assert isinstance(built, sut.PromptedExtractor)
    assert isinstance(built, SUT)
