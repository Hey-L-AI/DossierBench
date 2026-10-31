"""Band rules, composite fidelity, and trajectory metrics (spec-v0 §8)."""

from __future__ import annotations

import pytest

from persona_twin import fields, metrics, scorer, toy
from persona_twin.baselines import EvidenceOracle
from persona_twin.card import PersonaCard
from persona_twin.evidence import Grade
from persona_twin.fields import UNKNOWN


def _emit(subject, checkpoint, **overrides):
    """A card that abstains everywhere except the named fields."""
    card = PersonaCard.all_unknown(subject.subject_id)
    card.values.update(overrides)
    return card


# --- band rules (§8.1) ------------------------------------------------------

def test_strong_field_committed_correctly_scores_full_agreement():
    """Committing gold on every *scored* evidenced field — the strong pair plus the weak
    ordinal `deal_seeking` — scores 1.0. Since ADR-0001 an abstained weak field is credited
    rather than dropped, so leaving one unanswered no longer reaches 1.0. The WEAK
    `price_band_distribution` is excluded by the §8.1a low-N carve-out, so it is neither
    required nor scorable here."""
    subject = toy.evidence_ramp()
    gold = subject.gold_at(0)
    card = _emit(subject, 0, cuisines_liked=gold.get("cuisines_liked"),
                 spice_tolerance=gold.get("spice_tolerance"),
                 deal_seeking=gold.get("deal_seeking"))
    score = scorer.score_checkpoint(subject, 0, card)
    assert score.a == pytest.approx(1.0)


def test_strong_field_abstained_is_a_miss_not_an_exclusion():
    """spec §8.1: abstaining on a strongly-evidenced field scores 0 and stays in the
    denominator. Excluding it would make abstaining everywhere free."""
    subject = toy.evidence_ramp()
    card = PersonaCard.all_unknown(subject.subject_id)
    score = scorer.score_checkpoint(subject, 0, card)
    strong = subject.evidence.preference_band(0, Grade.STRONG)
    weak = subject.evidence.preference_band(0, Grade.WEAK)
    # The denominator is the evidenced band fixed by the evidence map (ADR-0001), minus any
    # WEAK distribution field the §8.1a carve-out excludes from scoring altogether.
    weak_scored = [w for w in weak if w not in scorer._DISTRIBUTION_ID_SET]
    assert score.a_denominator == len(strong) + len(weak_scored)
    strong_outcomes = [o for o in score.outcomes if o.field_id in strong]
    assert all(o.agreement == 0.0 for o in strong_outcomes)


def test_the_a_denominator_does_not_depend_on_what_the_sut_does():
    """ADR-0001's structural consequence: |S| + |W| is a property of the item, so an SUT can
    no longer shrink its own denominator by abstaining on weak fields."""
    subject = toy.evidence_ramp()
    gold = subject.gold_at(0)
    abstaining = scorer.score_checkpoint(
        subject, 0, PersonaCard.all_unknown(subject.subject_id)
    )
    committing = scorer.score_checkpoint(
        subject, 0,
        _emit(subject, 0, **{f: gold.get(f) for f in fields.PREFERENCE_IDS}),
    )
    assert abstaining.a_denominator == committing.a_denominator


def test_a_is_undefined_rather_than_zero_when_nothing_is_scorable():
    """Scoring an empty denominator as 0.0 would mark an SUT that behaves perfectly at a
    checkpoint evidencing nothing as a total failure. fab is still defined there, so such
    a checkpoint measures fabrication and only fabrication."""
    subject = toy.never_evidenced()
    card = PersonaCard.all_unknown(subject.subject_id)
    score = scorer.score_checkpoint(subject, 0, card)
    assert score.a is None
    assert score.f is None
    assert not score.scorable
    assert score.fab == 0.0


def test_weak_field_abstained_is_credited_and_stays_in_the_denominator():
    """ADR-0001. It used to be dropped from numerator *and* denominator, which is not a
    credit — it is removal from an average, so the value of abstaining depended on how well
    the SUT was doing on other fields. Now it is paid `WEAK_CREDIT` and counted."""
    subject = toy.evidence_ramp()
    gold = subject.gold_at(0)
    card = _emit(subject, 0, cuisines_liked=gold.get("cuisines_liked"),
                 spice_tolerance=gold.get("spice_tolerance"))
    # deal_seeking is WEAK at checkpoint 0 and left as "unknown" (price_band_distribution is
    # also WEAK but is excluded by the §8.1a carve-out, so it cannot carry this test).
    assert subject.evidence.grade(0, "deal_seeking") == Grade.WEAK
    score = scorer.score_checkpoint(subject, 0, card)
    assert score.a_denominator == 3
    weak_outcome = next(
        o for o in score.outcomes if o.field_id == "deal_seeking"
    )
    assert weak_outcome.counted_in_a
    assert not weak_outcome.committed
    assert weak_outcome.agreement == pytest.approx(metrics.WEAK_CREDIT)


def test_committing_on_weak_pays_exactly_when_it_beats_the_credit():
    """The falsifiable weak-band criterion (ADR-0001): the comparison is against what the
    evidence licenses, not against the system's accuracy elsewhere."""
    subject = toy.evidence_ramp()
    gold = subject.gold_at(0)
    base = {"cuisines_liked": gold.get("cuisines_liked"),
            "spice_tolerance": gold.get("spice_tolerance")}

    # deal_seeking carries the weak band (price_band_distribution is WEAK too but excluded by
    # the §8.1a carve-out, so committing it changes nothing — see the dedicated test below).
    abstaining = scorer.score_checkpoint(subject, 0, _emit(subject, 0, **base))
    exact = scorer.score_checkpoint(subject, 0, _emit(
        subject, 0, **base, deal_seeking=gold.get("deal_seeking"),
    ))
    # A commit at full agreement beats the credit; one at the far ordinal pole loses to it.
    wrong = scorer.score_checkpoint(subject, 0, _emit(
        subject, 0, **base, deal_seeking="ignores_price",
    ))
    assert exact.a > abstaining.a > wrong.a


def test_weak_field_committed_is_scored():
    subject = toy.evidence_ramp()
    gold = subject.gold_at(0)
    card = _emit(
        subject, 0,
        cuisines_liked=gold.get("cuisines_liked"),
        spice_tolerance=gold.get("spice_tolerance"),
        deal_seeking=gold.get("deal_seeking"),
    )
    score = scorer.score_checkpoint(subject, 0, card)
    assert score.a_denominator == 3
    assert score.a == pytest.approx(1.0)


def test_weak_distribution_is_excluded_from_both_terms():
    """§8.1a low-N carve-out. A WEAK distribution field is dropped from the checkpoint: not
    scored for agreement (its shape is not inferable from the handful of occasions behind a
    WEAK grade) and not fabrication-eligible (the SUT saw a real occasion, so a point-mass is
    thin evidence, not invention). `price_band_distribution` is WEAK at ramp checkpoint 0."""
    subject = toy.evidence_ramp()
    gold = subject.gold_at(0)
    assert subject.evidence.grade(0, "price_band_distribution") == Grade.WEAK

    committed = scorer.score_checkpoint(subject, 0, _emit(
        subject, 0, price_band_distribution=gold.get("price_band_distribution")))
    abstained = scorer.score_checkpoint(subject, 0, _emit(subject, 0))

    # Committing the exact gold distribution neither enters the agreement denominator nor is
    # charged as fabrication — the checkpoint scores identically whether it is committed.
    assert committed.a_denominator == abstained.a_denominator == 3   # 2 STRONG + deal_seeking
    assert committed.fabrication_count == 0
    assert committed.a == pytest.approx(abstained.a)
    pb = next(o for o in committed.outcomes if o.field_id == "price_band_distribution")
    assert not pb.counted_in_a and not pb.fabricated and pb.agreement is None


def test_committing_a_correct_value_on_an_unevidenced_field_is_still_fabrication():
    """The sharpest rule in the spec (§8.1) and the one with no counterpart in prior art:
    fabrication is band-determined, not value-determined."""
    subject = toy.never_evidenced()
    gold = subject.gold_at(0)
    perfect_but_unjustified = PersonaCard(subject.subject_id, dict(gold.values))
    score = scorer.score_checkpoint(subject, 0, perfect_but_unjustified)

    assert score.fab == pytest.approx(1.0)
    assert score.a_denominator == 0
    assert all(
        o.error_kind == scorer.FABRICATION
        for o in score.outcomes if o.field_id in fields.PREFERENCE_IDS
    )


def test_abstaining_on_unevidenced_fields_is_credited():
    subject = toy.never_evidenced()
    card = PersonaCard.all_unknown(subject.subject_id)
    score = scorer.score_checkpoint(subject, 0, card)
    assert score.fab == 0.0
    assert not any(o.fabricated for o in score.outcomes)


# --- composite fidelity (§8.3) ----------------------------------------------

def test_f_applies_the_lambda_penalty():
    """ADR-0002: the penalty is a fabrication *count* priced on A's denominator, not a rate
    over U(t). One fabrication costs the same λ/|S∪W| wherever it happens."""
    subject = toy.evidence_ramp()
    gold = subject.gold_at(0)
    card = PersonaCard(subject.subject_id, dict(gold.values))  # commits everywhere
    score = scorer.score_checkpoint(subject, 0, card, lam=0.5)
    expected = score.a - 0.5 * score.fabrication_count / score.a_denominator
    assert score.penalty == pytest.approx(0.5 * score.fabrication_count / score.a_denominator)
    assert score.f_raw == pytest.approx(expected)
    assert score.f == pytest.approx(max(0.0, expected))


def test_one_fabrication_costs_the_same_regardless_of_how_much_is_unevidenced():
    """The defect ADR-0002 fixes. Under the old rate-based penalty a fabrication cost
    λ/|U(t)|, so it got cheaper the less the transcript evidenced — cheapest in the sparse
    tier, which §7.4 builds so that abstention is the defensible move."""
    wide = toy.evidence_ramp()          # 17 unevidenced preference fields at checkpoint 0
    narrow = toy.interference_case()    # 15 unevidenced preference fields
    costs = []
    for subject in (wide, narrow):
        gold = subject.gold_at(0)
        unevidenced = subject.evidence.preference_band(0, Grade.UNEVIDENCED)
        base = _emit(subject, 0, **{
            f: gold.get(f) for f in fields.PREFERENCE_IDS if f not in unevidenced
        })
        one_more = _emit(subject, 0, **{
            f: gold.get(f) for f in fields.PREFERENCE_IDS if f not in unevidenced
        })
        one_more.values[unevidenced[0]] = gold.get(unevidenced[0])
        clean = scorer.score_checkpoint(subject, 0, base, lam=1.0)
        dirty = scorer.score_checkpoint(subject, 0, one_more, lam=1.0)
        costs.append((clean.f_raw - dirty.f_raw) * clean.a_denominator)
    # Scaled by the shared denominator, the price of one fabrication is exactly λ in both.
    assert costs[0] == pytest.approx(1.0)
    assert costs[1] == pytest.approx(1.0)


def test_f_is_clamped_into_the_unit_interval():
    """Low A plus full fabrication drives raw F negative; it clamps to 0 rather than
    going below the abstain-everything floor."""
    subject = toy.evidence_ramp()
    card = PersonaCard(subject.subject_id, dict(subject.gold_at(0).values))
    card.values["cuisines_liked"] = ["italian"]     # wrong on the strong fields
    card.values["spice_tolerance"] = "avoids_heat"
    score = scorer.score_checkpoint(subject, 0, card, lam=1.0)
    assert score.a is not None and score.a < 0.5
    assert score.fab > 0.5
    assert score.f_by_lambda[1.0] == 0.0


def test_lambda_zero_disables_the_penalty():
    """λ = 0 is no longer in REPORTED_LAMBDAS — with no penalty F collapses to A and the
    composite is switched off rather than reported — but it must still behave if asked for."""
    subject = toy.evidence_ramp()
    gold = subject.gold_at(0)
    card = PersonaCard(subject.subject_id, dict(gold.values))
    score = scorer.score_checkpoint(subject, 0, card, lam=0.0)
    assert score.f_by_lambda[0.0] == pytest.approx(score.a)
    assert 0.0 not in scorer.REPORTED_LAMBDAS


def test_all_reported_lambdas_are_present():
    subject = toy.evidence_ramp()
    score = scorer.score_checkpoint(subject, 0, PersonaCard.all_unknown("toy-ramp"))
    for candidate in scorer.REPORTED_LAMBDAS:
        assert candidate in score.f_by_lambda


# --- the oracle ceiling -----------------------------------------------------

@pytest.mark.parametrize("build", toy.ALL_TOYS, ids=lambda f: f.__name__)
def test_evidence_oracle_scores_a_perfect_trajectory(build):
    """Commit gold exactly where evidence supports it, abstain elsewhere -> F = 1.0 at
    every scorable checkpoint. Any deviation is a scorer bug, not an SUT result.

    `never_evidenced` has no scorable checkpoint by construction, so its AUFC is None
    rather than 1.0 — there is no fidelity to measure on a timeline that evidences nothing.
    """
    subject = build()
    subject.validate()
    oracle = EvidenceOracle(subject)
    emissions = [
        oracle.emit(t) for t in range(subject.evidence.checkpoint_count)
    ]
    result = scorer.score_subject(subject, emissions, min_evidenced=toy.TOY_MIN_EVIDENCED)

    for checkpoint in result.checkpoints:
        assert checkpoint.fab == 0.0
        assert not checkpoint.constraint_failure
        if checkpoint.scorable:
            assert checkpoint.f == pytest.approx(1.0)

    if result.scorable_checkpoints:
        assert result.aufc == pytest.approx(1.0)
    else:
        assert result.aufc is None


# --- corrections, staleness, latency (§8.4, §8.6) ---------------------------

def test_gold_rewinds_to_the_pre_correction_value():
    subject = toy.with_correction()
    assert subject.gold_at(0).get("dietary") == ["vegetarian"]
    assert subject.gold_at(1).get("dietary") == ["vegetarian"]
    # A correction at checkpoint 2 is in force *at* 2: the SUT saw the session first.
    assert subject.gold_at(2).get("dietary") == ["vegan"]
    assert subject.gold_at(3).get("dietary") == ["vegan"]


def test_holding_the_old_value_after_a_correction_is_staleness_not_misreading():
    subject = toy.with_correction()
    card = _emit(subject, 2, dietary=["vegetarian"])
    score = scorer.score_checkpoint(subject, 2, card)
    outcome = next(o for o in score.outcomes if o.field_id == "dietary")
    assert outcome.error_kind == scorer.STALENESS


def test_a_wrong_value_that_was_never_correct_is_misreading():
    subject = toy.with_correction()
    card = _emit(subject, 2, dietary=["halal"])
    score = scorer.score_checkpoint(subject, 2, card)
    outcome = next(o for o in score.outcomes if o.field_id == "dietary")
    assert outcome.error_kind == scorer.MISREADING


def test_post_correction_latency_counts_checkpoints_until_the_field_flips():
    subject = toy.with_correction()
    emissions = [
        _emit(subject, 0, dietary=["vegetarian"]),
        _emit(subject, 1, dietary=["vegetarian"]),
        _emit(subject, 2, dietary=["vegetarian"]),   # missed it
        _emit(subject, 3, dietary=["vegan"]),        # absorbed one checkpoint late
    ]
    result = scorer.score_subject(subject, emissions, min_evidenced=toy.TOY_MIN_EVIDENCED)
    assert result.post_correction_latency["dietary@2"] == 1


def test_latency_is_zero_when_absorbed_immediately():
    subject = toy.with_correction()
    emissions = [
        _emit(subject, 0, dietary=["vegetarian"]),
        _emit(subject, 1, dietary=["vegetarian"]),
        _emit(subject, 2, dietary=["vegan"]),
        _emit(subject, 3, dietary=["vegan"]),
    ]
    result = scorer.score_subject(subject, emissions, min_evidenced=toy.TOY_MIN_EVIDENCED)
    assert result.post_correction_latency["dietary@2"] == 0


def test_latency_is_none_when_never_absorbed():
    """spec §11 requires the reference SUT's latency to be nonzero but *finite*; this is
    the failure mode that criterion rules out."""
    subject = toy.with_correction()
    emissions = [_emit(subject, t, dietary=["vegetarian"]) for t in range(4)]
    result = scorer.score_subject(subject, emissions, min_evidenced=toy.TOY_MIN_EVIDENCED)
    assert result.post_correction_latency["dietary@2"] is None


def test_a_scripted_correction_does_not_count_as_a_regression():
    """Changing the answer when the user corrects you is the behaviour the event exists to
    elicit; counting it as a regression would penalize exactly that (§8.6)."""
    subject = toy.with_correction()
    gold_fields = ("cuisines_liked", "spice_tolerance")
    emissions = []
    for t in range(4):
        gold = subject.gold_at(t)
        emissions.append(_emit(
            subject, t,
            dietary=gold.get("dietary"),
            **{f: gold.get(f) for f in gold_fields},
        ))
    result = scorer.score_subject(subject, emissions, min_evidenced=toy.TOY_MIN_EVIDENCED)
    assert result.regression_count == 0


def test_an_unforced_flip_does_count_as_a_regression():
    subject = toy.evidence_ramp()
    gold = subject.gold_at(0)
    emissions = [
        _emit(subject, 0, cuisines_liked=gold.get("cuisines_liked"),
              spice_tolerance=gold.get("spice_tolerance")),
        _emit(subject, 1, cuisines_liked=gold.get("cuisines_liked"),
              spice_tolerance="avoids_heat"),        # was right, now wrong
        _emit(subject, 2, cuisines_liked=gold.get("cuisines_liked"),
              spice_tolerance="avoids_heat"),
    ]
    result = scorer.score_subject(subject, emissions, min_evidenced=toy.TOY_MIN_EVIDENCED)
    assert result.regression_count >= 1


# --- constraint gating (§8.2) -----------------------------------------------

def test_missing_an_evidenced_constraint_is_a_hard_failure():
    subject = toy.evidence_ramp()
    card = _emit(subject, 1, dietary=[])   # gold is ["vegetarian"], evidenced STRONG
    score = scorer.score_checkpoint(subject, 1, card)
    assert score.constraint_failure
    assert score.constraint_recall == 0.0


def test_inventing_an_unevidenced_constraint_is_a_hard_failure():
    subject = toy.evidence_ramp()
    assert subject.evidence.grade(0, "allergies") == Grade.UNEVIDENCED
    card = _emit(subject, 0, allergies=["shellfish"])
    score = scorer.score_checkpoint(subject, 0, card)
    assert score.constraint_failure


def test_declaring_no_constraint_is_not_an_invented_constraint():
    """`dietary: []` asserts the absence of a restriction. That is a legitimate claim and
    must not trip the precision failure."""
    subject = toy.evidence_ramp()
    card = _emit(subject, 0, dietary=[], allergies=[], accessibility_needs=[],
                 alcohol_free_required=False)
    score = scorer.score_checkpoint(subject, 0, card)
    assert not score.constraint_failure


def test_constraints_are_never_averaged_into_fidelity():
    """spec §8.2: reported separately, never averaged in. Constraint fields still get
    outcomes so §8.4's error decomposition covers them, but they must never enter A(t)."""
    subject = toy.evidence_ramp()
    card = PersonaCard.all_unknown(subject.subject_id)
    score = scorer.score_checkpoint(subject, 2, card)
    counted = {o.field_id for o in score.outcomes if o.counted_in_a}
    assert counted.isdisjoint(set(fields.CONSTRAINT_IDS))
    assert score.a_denominator == (
        len(subject.evidence.preference_band(2, Grade.STRONG))
        + len(subject.evidence.preference_band(2, Grade.WEAK))
    )


def test_constraint_fabrication_is_not_double_counted_in_fab():
    """A fabricated constraint is a gating failure under §8.2; counting it in λ·fab too
    would punish the same mistake twice."""
    subject = toy.evidence_ramp()
    card = PersonaCard.all_unknown(subject.subject_id)
    card.values["allergies"] = ["shellfish"]     # UNEVIDENCED at checkpoint 0
    score = scorer.score_checkpoint(subject, 0, card)
    assert score.constraint_failure
    assert score.fab == 0.0
    outcome = next(o for o in score.outcomes if o.field_id == "allergies")
    assert outcome.error_kind == scorer.FABRICATION


# --- trajectory metrics (§8.6) ----------------------------------------------

def test_aufc_is_the_mean_of_the_f_curve():
    subject = toy.evidence_ramp()
    oracle = EvidenceOracle(subject)
    emissions = [oracle.emit(t) for t in range(3)]
    result = scorer.score_subject(subject, emissions, min_evidenced=toy.TOY_MIN_EVIDENCED)
    expected = sum(c.f for c in result.checkpoints) / len(result.checkpoints)
    assert result.aufc == pytest.approx(expected)


def test_time_to_threshold_finds_the_first_qualifying_checkpoint():
    subject = toy.evidence_ramp()
    oracle = EvidenceOracle(subject)
    emissions = [oracle.emit(t) for t in range(3)]
    result = scorer.score_subject(subject, emissions, min_evidenced=toy.TOY_MIN_EVIDENCED)
    assert result.time_to_threshold == 0


def test_time_to_threshold_is_none_when_never_reached():
    subject = toy.evidence_ramp()
    emissions = [PersonaCard.all_unknown("toy-ramp") for _ in range(3)]
    result = scorer.score_subject(subject, emissions, min_evidenced=toy.TOY_MIN_EVIDENCED)
    assert result.time_to_threshold is None


def test_distractor_stability_is_one_when_the_card_does_not_move():
    subject = toy.evidence_ramp()   # carries a distractor at checkpoint 1
    oracle = EvidenceOracle(subject)
    emissions = [oracle.emit(0), oracle.emit(0), oracle.emit(2)]
    result = scorer.score_subject(subject, emissions, min_evidenced=toy.TOY_MIN_EVIDENCED)
    assert result.distractor_stability == pytest.approx(1.0)


def test_distractor_stability_drops_when_the_card_drifts_under_no_signal():
    subject = toy.evidence_ramp()
    gold = subject.gold_at(0)
    emissions = [
        _emit(subject, 0, cuisines_liked=gold.get("cuisines_liked")),
        _emit(subject, 1, cuisines_liked=["italian"], noise_preference="needs_quiet"),
        _emit(subject, 2, cuisines_liked=gold.get("cuisines_liked")),
    ]
    result = scorer.score_subject(subject, emissions, min_evidenced=toy.TOY_MIN_EVIDENCED)
    assert result.distractor_stability < 1.0


def test_interference_collateral_is_positive_when_settled_fields_degrade():
    subject = toy.interference_case()
    gold = subject.gold_at(0)
    settled = ("noise_preference", "travel_radius", "planning_style")
    good = {f: gold.get(f) for f in settled}
    good["cuisines_liked"] = gold.get("cuisines_liked")
    good["spice_tolerance"] = gold.get("spice_tolerance")

    damaged = dict(good)
    damaged["noise_preference"] = "needs_quiet"      # unrelated to the `taste` burst
    damaged["travel_radius"] = "out_of_town"

    emissions = [
        _emit(subject, 0, **good),
        _emit(subject, 1, **good),
        _emit(subject, 2, **damaged),
    ]
    result = scorer.score_subject(subject, emissions, min_evidenced=toy.TOY_MIN_EVIDENCED)
    assert result.interference_collateral is not None
    assert result.interference_collateral > 0.0


def test_interference_collateral_ignores_the_targeted_group():
    """Movement *inside* the burst's facet group is the burst working, not collateral."""
    subject = toy.interference_case()
    gold = subject.gold_at(0)
    settled = {
        f: gold.get(f) for f in ("noise_preference", "travel_radius", "planning_style")
    }
    before = dict(settled, cuisines_liked=gold.get("cuisines_liked"),
                  spice_tolerance=gold.get("spice_tolerance"))
    after = dict(settled, cuisines_liked=["italian"], spice_tolerance="avoids_heat")

    emissions = [_emit(subject, 0, **before), _emit(subject, 1, **before),
                 _emit(subject, 2, **after)]
    result = scorer.score_subject(subject, emissions, min_evidenced=toy.TOY_MIN_EVIDENCED)
    assert result.interference_collateral == pytest.approx(0.0)


# --- protocol guards --------------------------------------------------------

def test_wrong_emission_count_is_rejected():
    subject = toy.evidence_ramp()
    with pytest.raises(ValueError, match="checkpoints but got"):
        scorer.score_subject(subject, [PersonaCard.all_unknown("toy-ramp")])


def test_scorer_dispatches_on_schema_version():
    subject = toy.evidence_ramp()
    card = PersonaCard.all_unknown("toy-ramp")
    card.schema_version = "0.2"
    with pytest.raises(ValueError, match="schema_version"):
        scorer.score_checkpoint(subject, 0, card)


def test_carried_state_is_reported_alongside_every_score():
    """spec §1.3: state metering is a first-class number next to the score, not a footnote."""
    subject = toy.evidence_ramp()
    oracle = EvidenceOracle(subject)
    emissions = [oracle.emit(t) for t in range(3)]
    result = scorer.score_subject(
        subject, emissions, min_evidenced=toy.TOY_MIN_EVIDENCED,
        carried_state_bytes=[100, 200, 300],
        carried_state_tokens=[25, 50, 75],
    )
    assert [c.carried_state_tokens for c in result.checkpoints] == [25, 50, 75]
    assert result.peak_carried_state_tokens == 75


# --- ADR-0004: the AUFC density gate -------------------------------------


def test_the_default_threshold_is_the_one_the_ablation_derives():
    """ADR-0004 picks 7 by requiring the per-checkpoint penalty ceiling to stay at or under
    2.0 at the default lambda. If the derivation and the constant ever drift apart, the
    published justification stops describing the shipped behaviour."""
    from persona_twin import ablation

    derived = ablation.min_evidenced_for(2.0, scorer.DEFAULT_LAMBDA)
    assert derived == scorer.MIN_EVIDENCED_FOR_AUFC


def test_a_thin_checkpoint_is_scored_and_published_but_not_averaged():
    """The gate excludes from the *mean*, not from the report. A checkpoint below the
    threshold keeps every number it had — that is what separates ADR-0004 from convention 1,
    where A is genuinely undefined."""
    from persona_twin import ablation

    thin = ablation.synthetic_subject(ablation.BandMix(1, 1), seed=7, checkpoints=1)
    card = PersonaCard.all_unknown(thin.subject_id)
    result = scorer.score_subject(thin, [card])

    checkpoint = result.checkpoints[0]
    assert checkpoint.scorable, "two evidenced fields is scorable"
    assert not checkpoint.averageable(), "...but below the density gate"
    assert checkpoint.a is not None and checkpoint.f is not None
    assert checkpoint.a_floor is not None

    assert result.scorable_checkpoints == 1
    assert result.averaged_checkpoints == 0
    assert result.aufc is None, "nothing thick enough to average"


def test_a_thick_checkpoint_passes_the_gate():
    """The complement, so the test above is not passing for the wrong reason."""
    from persona_twin import ablation

    thick = ablation.synthetic_subject(ablation.BandMix(6, 6), seed=7, checkpoints=1)
    card = PersonaCard.all_unknown(thick.subject_id)
    result = scorer.score_subject(thick, [card])

    assert result.checkpoints[0].averageable()
    assert result.averaged_checkpoints == 1
    assert result.aufc is not None


def test_the_gate_excludes_only_the_thin_checkpoints_from_a_mixed_curve():
    """The realistic shape: a trajectory that starts thin and thickens. AUFC must be the mean
    over the surviving checkpoints only, and both counts must be published so the exclusion
    is legible rather than silent."""
    from persona_twin import ablation

    subject = ablation.synthetic_subject(ablation.BandMix(6, 6), seed=11, checkpoints=3)
    # Thin the first checkpoint below the gate, leaving the rest untouched. Grades must stay
    # monotone in the checkpoint index (evidence.py), so the one field left evidenced has to
    # be one that is still at least as strong later.
    survivor = next(
        f for f in fields.PREFERENCE_IDS
        if subject.evidence.grade(1, f) is Grade.STRONG
    )
    for field_id in list(subject.evidence.grades[0]):
        if field_id != survivor:
            subject.evidence.grades[0][field_id] = Grade.UNEVIDENCED
    subject.evidence.grades[0][survivor] = Grade.STRONG
    subject.evidence.validate()

    cards = [PersonaCard.all_unknown(subject.subject_id) for _ in range(3)]
    result = scorer.score_subject(subject, cards)

    assert result.scorable_checkpoints == 3
    assert result.averaged_checkpoints == 2
    averaged = [c for c in result.checkpoints if c.averageable()]
    assert result.aufc == pytest.approx(sum(c.f for c in averaged) / len(averaged))
    assert not result.checkpoints[0].averageable()


def test_the_toys_sit_below_the_production_gate():
    """Recorded as a fact about the fixtures, not a defect. ADR-0004's finding is that the
    alarming F_raw magnitudes are a property of a 2-3-of-20 evidence mix; the corollary is
    that the toys cannot be averaged at the production threshold, which is why everything
    scoring them passes `toy.TOY_MIN_EVIDENCED`."""
    subject = toy.evidence_ramp()
    cards = [PersonaCard.all_unknown(subject.subject_id)
             for _ in range(subject.evidence.checkpoint_count)]

    gated = scorer.score_subject(subject, cards)
    assert gated.aufc is None
    assert gated.averaged_checkpoints == 0
    assert gated.scorable_checkpoints > 0, "they are scorable, just not averageable"

    opted_out = scorer.score_subject(subject, cards, min_evidenced=toy.TOY_MIN_EVIDENCED)
    assert opted_out.aufc is not None


# --- episode error counting (`_error_episodes`, episode-error-count decision) ---------------

from types import SimpleNamespace  # noqa: E402


def _outcome(field_id, grade, kind):
    """A FieldOutcome carrying just what `_error_episodes` reads."""
    return scorer.FieldOutcome(
        field_id, grade, committed=kind is not None, agreement=None,
        counted_in_a=False, fabricated=(kind == scorer.FABRICATION), error_kind=kind,
    )


def _ckpt(t, *outcomes):
    return SimpleNamespace(checkpoint=t, outcomes=list(outcomes))


def _subject(*corrections):
    """A stand-in subject exposing only `.corrections` as (field_id, checkpoint) pairs."""
    return SimpleNamespace(
        corrections=[SimpleNamespace(field_id=f, checkpoint=c) for f, c in corrections]
    )


def test_episode_carried_wrong_value_counts_once():
    """A field wrong for three checkpoints with no new evidence is one mistake, not three —
    the whole point of counting episodes instead of the per-checkpoint tally."""
    scores = [
        _ckpt(t, _outcome("cuisines_liked", Grade.WEAK, scorer.MISREADING))
        for t in range(3)
    ]
    assert scorer._error_episodes(_subject(), scores)[scorer.MISREADING] == 1


def test_episode_reopens_when_grade_rises_and_still_wrong():
    """New evidence lands (WEAK→STRONG) and the SUT stays wrong: a fresh chance missed, so a
    second episode. This is the failed-update the owner asked to count."""
    scores = [
        _ckpt(0, _outcome("cuisines_liked", Grade.WEAK, scorer.MISREADING)),
        _ckpt(1, _outcome("cuisines_liked", Grade.STRONG, scorer.MISREADING)),
    ]
    assert scorer._error_episodes(_subject(), scores)[scorer.MISREADING] == 2


def test_episode_reopens_on_correction():
    """A correction fires on a still-wrong field: a fresh chance, so a new episode even though
    the grade did not move."""
    scores = [
        _ckpt(0, _outcome("diet", Grade.STRONG, scorer.STALENESS)),
        _ckpt(1, _outcome("diet", Grade.STRONG, scorer.STALENESS)),
    ]
    subject = _subject(("diet", 1))
    assert scorer._error_episodes(subject, scores)[scorer.STALENESS] == 2


def test_episode_does_not_reopen_without_new_evidence():
    """Same wrong value, same grade, no correction: carried, one episode — even across many
    checkpoints and even if the wrong value itself churns."""
    scores = [
        _ckpt(0, _outcome("diet", Grade.STRONG, scorer.STALENESS)),
        _ckpt(1, _outcome("diet", Grade.STRONG, scorer.STALENESS)),
        _ckpt(2, _outcome("diet", Grade.STRONG, scorer.STALENESS)),
    ]
    assert scorer._error_episodes(_subject(), scores)[scorer.STALENESS] == 1


def test_episode_fix_then_rebreak_counts_twice():
    """Wrong, then right, then wrong again is two episodes: the right checkpoint closes the
    first, and the relapse is a fresh onset."""
    scores = [
        _ckpt(0, _outcome("spice_tolerance", Grade.STRONG, scorer.MISREADING)),
        _ckpt(1, _outcome("spice_tolerance", Grade.STRONG, None)),
        _ckpt(2, _outcome("spice_tolerance", Grade.STRONG, scorer.MISREADING)),
    ]
    assert scorer._error_episodes(_subject(), scores)[scorer.MISREADING] == 2


def test_episode_kind_follows_the_checkpoint_that_opened_it():
    """A fabrication (U-band commit) that becomes a misreading once the field is evidenced is
    one fabrication episode and one misreading episode — the reopen takes the new kind."""
    scores = [
        _ckpt(0, _outcome("cuisines_liked", Grade.UNEVIDENCED, scorer.FABRICATION)),
        _ckpt(1, _outcome("cuisines_liked", Grade.WEAK, scorer.MISREADING)),
    ]
    episodes = scorer._error_episodes(_subject(), scores)
    assert episodes[scorer.FABRICATION] == 1
    assert episodes[scorer.MISREADING] == 1


def test_episode_totals_are_never_above_the_raw_tally():
    """Episodes de-duplicate the per-checkpoint tally, so on a real trajectory every kind's
    episode count is <= its raw tally. Guards the invariant the two columns are read against."""
    subject = toy.evidence_ramp()
    gold = subject.gold_at(subject.evidence.checkpoint_count - 1)
    # Commit a wrong cuisine everywhere to manufacture a carried error.
    cards = []
    for _ in range(subject.evidence.checkpoint_count):
        card = PersonaCard.all_unknown(subject.subject_id)
        card.values["cuisines_liked"] = ["definitely_not_a_real_cuisine"]
        cards.append(card)
    score = scorer.score_subject(subject, cards, min_evidenced=toy.TOY_MIN_EVIDENCED)
    raw = score.errors_by_kind()
    for kind, episodes in score.error_episodes.items():
        assert episodes <= raw[kind]


# --- ordering-stability recompute (`sut._recompute_f` / `_aufc_at`, avenue A) ---------------

def test_stability_recompute_matches_scorer_at_default_knobs():
    """The knob-grid recompute must reproduce `score_subject`'s AUFC exactly at the default
    (λ, WEAK_CREDIT, min_evidenced) — otherwise the whole stability grid is anchored to a
    number that does not match the leaderboard. Pinned on a toy trajectory with a mix of
    committed, abstained and wrong fields so weak-credit cells actually participate."""
    from persona_twin import sut, metrics as _metrics

    subject = toy.evidence_ramp()
    n = subject.evidence.checkpoint_count
    cards = []
    for t in range(n):
        gold = subject.gold_at(t)
        card = PersonaCard.all_unknown(subject.subject_id)
        # Commit the strong-ish fields correctly, leave the rest to abstain/miss.
        card.values["cuisines_liked"] = gold.get("cuisines_liked")
        cards.append(card)

    score = scorer.score_subject(subject, cards, min_evidenced=toy.TOY_MIN_EVIDENCED)
    recomputed = sut._aufc_at(
        score, scorer.DEFAULT_LAMBDA, _metrics.WEAK_CREDIT, toy.TOY_MIN_EVIDENCED,
    )
    assert score.aufc == pytest.approx(recomputed)


def test_stability_recompute_tracks_lambda_sweep():
    """Recomputing at λ=0.5 and 2.0 must match the scorer's own per-checkpoint `f_by_lambda`
    mean — the sweep columns the comparison table already prints."""
    from persona_twin import sut, metrics as _metrics

    subject = toy.evidence_ramp()
    n = subject.evidence.checkpoint_count
    cards = []
    for t in range(n):
        card = PersonaCard.all_unknown(subject.subject_id)
        card.values["cuisines_liked"] = ["not_a_real_cuisine"]   # a fabrication to price
        cards.append(card)
    score = scorer.score_subject(subject, cards, min_evidenced=toy.TOY_MIN_EVIDENCED)

    for lam in (0.5, 2.0):
        averaged = [c for c in score.checkpoints if c.a_denominator >= toy.TOY_MIN_EVIDENCED]
        expected = sum(c.f_by_lambda[lam] for c in averaged) / len(averaged)
        got = sut._aufc_at(score, lam, _metrics.WEAK_CREDIT, toy.TOY_MIN_EVIDENCED)
        assert got == pytest.approx(expected)


def test_stability_weak_credit_moves_aufc_up():
    """A higher WEAK_CREDIT can only raise (or hold) AUFC on a card that abstains on weak
    fields, since the credit is what those cells score. Guards the substitution's direction."""
    from persona_twin import sut, metrics as _metrics

    subject = toy.evidence_ramp()
    cards = [PersonaCard.all_unknown(subject.subject_id)
             for _ in range(subject.evidence.checkpoint_count)]   # abstains everywhere
    score = scorer.score_subject(subject, cards, min_evidenced=toy.TOY_MIN_EVIDENCED)
    low = sut._aufc_at(score, scorer.DEFAULT_LAMBDA, _metrics.WEAK_CREDIT - 0.1,
                       toy.TOY_MIN_EVIDENCED)
    high = sut._aufc_at(score, scorer.DEFAULT_LAMBDA, _metrics.WEAK_CREDIT + 0.1,
                        toy.TOY_MIN_EVIDENCED)
    assert low is not None and high is not None and high >= low
