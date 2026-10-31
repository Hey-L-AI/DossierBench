"""Field registry, card wire format, evidence map, and the generated JSON Schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from persona_twin import emit_schema, fields, toy
from persona_twin.card import CardValidationError, PersonaCard
from persona_twin.evidence import EvidenceMap, Grade
from persona_twin.fields import UNKNOWN
from persona_twin.subject import ScriptedEvent, Subject

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "persona_card.v0.1.json"


# --- registry shape (spec §2) -----------------------------------------------

def test_card_has_twenty_scored_preference_fields_and_four_constraints():
    assert len(fields.PREFERENCE_FIELDS) == 20
    assert len(fields.HARD_CONSTRAINTS) == 4


def test_field_ids_are_unique():
    ids = [f.field_id for f in fields.ALL_FIELDS]
    assert len(ids) == len(set(ids))


def test_every_ordinal_has_exactly_four_levels():
    """spec §2.2: four levels, not three. Three saturates, the fidelity curve goes flat
    early, and AUFC stops discriminating."""
    for spec in fields.ALL_FIELDS:
        if spec.metric == fields.METRIC_ORDINAL:
            assert len(spec.levels) == 4, spec.field_id


def test_every_ordinal_level_has_a_behavioural_anchor():
    """spec §2.2: anchors must be written so a simulator can *perform* each level
    distinctly. A level without one cannot be generated against."""
    for spec in fields.ALL_FIELDS:
        if spec.metric == fields.METRIC_ORDINAL:
            assert len(spec.anchors) == len(spec.levels), spec.field_id
            assert all(a.strip() for a in spec.anchors), spec.field_id


def test_anchors_are_distinct_within_a_field():
    for spec in fields.ALL_FIELDS:
        if spec.anchors:
            assert len(set(spec.anchors)) == len(spec.anchors), spec.field_id


def test_every_cluster_names_only_real_fields():
    for name, ids in fields.CLUSTERS.items():
        for field_id in ids:
            assert field_id in fields.BY_ID, f"{name} names unknown field {field_id}"


def test_distribution_fields_declare_their_bins():
    for spec in fields.ALL_FIELDS:
        if spec.metric == fields.METRIC_JSD:
            assert len(spec.levels) >= 2, spec.field_id


# --- card validation --------------------------------------------------------

def test_all_unknown_card_validates():
    card = PersonaCard.all_unknown("s1")
    card.validate()


def test_ground_truth_may_not_abstain():
    """An abstention in ground truth would make the §8.1 evidence bands undefined."""
    card = PersonaCard.all_unknown("s1")
    with pytest.raises(CardValidationError, match="must be complete"):
        card.validate(allow_unknown=False)


def test_toy_golds_are_complete_and_valid():
    for build in toy.ALL_TOYS:
        build().gold.validate(allow_unknown=False)


def test_missing_field_is_rejected():
    card = PersonaCard.all_unknown("s1")
    del card.values["spice_tolerance"]
    with pytest.raises(CardValidationError, match="missing fields"):
        card.validate()


def test_out_of_vocabulary_set_member_is_rejected():
    card = PersonaCard.all_unknown("s1")
    card.values["cuisines_liked"] = ["klingon"]
    with pytest.raises(CardValidationError, match="out-of-vocabulary"):
        card.validate()


def test_bare_string_for_a_set_field_is_rejected():
    card = PersonaCard.all_unknown("s1")
    card.values["cuisines_liked"] = "thai"
    with pytest.raises(CardValidationError, match="collection of strings"):
        card.validate()


def test_unnormalized_distribution_is_rejected():
    card = PersonaCard.all_unknown("s1")
    card.values["price_band_distribution"] = {"<£15": 0.5, "£15-30": 0.2}
    with pytest.raises(CardValidationError, match="sum to"):
        card.validate()


def test_importance_weights_must_sum_to_one():
    card = PersonaCard.all_unknown("s1")
    card.importance_weights = {"spice_tolerance": 0.5, "adventurousness": 0.2}
    with pytest.raises(CardValidationError, match="sum to"):
        card.validate()


# --- wire format ------------------------------------------------------------

def test_card_round_trips_through_json():
    original = toy.evidence_ramp().gold
    restored = PersonaCard.from_json(json.loads(json.dumps(original.to_json())))
    assert restored.values == original.values
    assert restored.subject_id == original.subject_id


def test_wire_format_nests_under_contexts_default():
    """spec §2.1: contexts.default nesting is mandatory even though v0 has one context.
    v1 adds sibling blocks without a schema break. Do not flatten."""
    payload = toy.evidence_ramp().gold.to_json()
    assert "default" in payload["contexts"]
    assert "spice_tolerance" in payload["contexts"]["default"]
    assert "dietary" in payload["hard_constraints"]
    assert "spice_tolerance" not in payload


def test_flattened_card_is_rejected():
    payload = toy.evidence_ramp().gold.to_json()
    payload["contexts"] = {}
    with pytest.raises(CardValidationError, match="contexts.default is mandatory"):
        PersonaCard.from_json(payload)


def test_unsupported_schema_version_is_refused():
    payload = toy.evidence_ramp().gold.to_json()
    payload["schema_version"] = "0.9"
    with pytest.raises(CardValidationError, match="unsupported schema_version"):
        PersonaCard.from_json(payload)


# --- evidence map (spec §7.3) -----------------------------------------------

def _open(checkpoints: int = 1) -> EvidenceMap:
    """A ceiling that bounds nothing, for the arms that test judge combination alone."""
    return EvidenceMap.permissive_ceiling("s1", checkpoints)


def test_judge_disagreement_demotes_strong_to_weak():
    """The §7.3 rule made executable: a field is STRONG only where judges agree."""
    judge_a = [{"spice_tolerance": Grade.STRONG, "adventurousness": Grade.STRONG}]
    judge_b = [{"spice_tolerance": Grade.STRONG, "adventurousness": Grade.WEAK}]
    combined = EvidenceMap.from_judges("s1", [judge_a, judge_b], ceiling=_open())
    assert combined.grade(0, "spice_tolerance") == Grade.STRONG
    assert combined.grade(0, "adventurousness") == Grade.WEAK


def test_a_split_weak_unevidenced_judgment_demotes_to_unevidenced():
    """The conservative direction: a split judgment never silently licenses a commit."""
    judge_a = [{"spice_tolerance": Grade.WEAK}]
    judge_b = [{"spice_tolerance": Grade.UNEVIDENCED}]
    combined = EvidenceMap.from_judges("s1", [judge_a, judge_b], ceiling=_open())
    assert combined.grade(0, "spice_tolerance") == Grade.UNEVIDENCED


def test_a_single_judge_is_refused():
    with pytest.raises(ValueError, match="at least 2 independent judges"):
        EvidenceMap.from_judges(
            "s1", [[{"spice_tolerance": Grade.STRONG}]], ceiling=_open()
        )


def test_grades_accept_string_names():
    combined = EvidenceMap.from_judges(
        "s1",
        [[{"spice_tolerance": "strong"}], [{"spice_tolerance": "STRONG"}]],
        ceiling=_open(),
    )
    assert combined.grade(0, "spice_tolerance") == Grade.STRONG


# --- the §7.3 ceiling (ADR-0009) --------------------------------------------

def test_judges_cannot_evidence_a_field_the_schedule_never_touches():
    """The rule ADR-0009 is: agreement between judges does not create evidence.

    Measured, this is 35% of never-scheduled field-checkpoints, correlated across judges at
    56/67/68% — so unanimity is exactly the case that has to be refused, not the exception.
    """
    unanimous = [{"spice_tolerance": Grade.STRONG}]
    ceiling = EvidenceMap("s1", [{"spice_tolerance": Grade.UNEVIDENCED}])
    combined = EvidenceMap.from_judges("s1", [unanimous, unanimous], ceiling=ceiling)
    assert combined.grade(0, "spice_tolerance") == Grade.UNEVIDENCED


def test_the_ceiling_demotes_a_pin_to_the_weak_band_the_schedule_predicted():
    """A field the schedule hinted but did not pin stays in the band where abstention is
    the defensible move (§8.1), whatever the judges committed to."""
    judge = [{"noise_preference": Grade.STRONG}]
    ceiling = EvidenceMap("s1", [{"noise_preference": Grade.WEAK}])
    combined = EvidenceMap.from_judges("s1", [judge, judge], ceiling=ceiling)
    assert combined.grade(0, "noise_preference") == Grade.WEAK


def test_the_ceiling_never_raises_a_grade():
    """It is a ceiling, not a substitute. A field the schedule pinned but the judges could
    not read stays at what the judges read — §7.4's realized profile is a measurement of
    the authoring, and a floor here would erase the miss it exists to show."""
    judge = [{"spice_tolerance": Grade.WEAK}]
    ceiling = EvidenceMap("s1", [{"spice_tolerance": Grade.STRONG}])
    combined = EvidenceMap.from_judges("s1", [judge, judge], ceiling=ceiling)
    assert combined.grade(0, "spice_tolerance") == Grade.WEAK
    assert combined.raises == ()


def test_every_clip_is_recorded_and_the_two_kinds_are_kept_apart():
    """Invention (off UNEVIDENCED) and over-pinning (off WEAK) are different failures with
    different remedies, so `raise_counts` does not sum them."""
    judge = [{
        "spice_tolerance": Grade.STRONG,      # ceiling UNEVIDENCED — invention
        "noise_preference": Grade.STRONG,     # ceiling WEAK        — over-pinning
        "adventurousness": Grade.WEAK,        # ceiling UNEVIDENCED — invention
    }]
    ceiling = EvidenceMap("s1", [{
        "spice_tolerance": Grade.UNEVIDENCED,
        "noise_preference": Grade.WEAK,
        "adventurousness": Grade.UNEVIDENCED,
    }])
    combined = EvidenceMap.from_judges("s1", [judge, judge], ceiling=ceiling)
    assert combined.raise_counts() == {
        "UNEVIDENCED->STRONG": 1,
        "UNEVIDENCED->WEAK": 1,
        "WEAK->STRONG": 1,
    }
    assert {r.field_id for r in combined.raises} == {
        "spice_tolerance", "noise_preference", "adventurousness"
    }


def test_a_ceiling_of_the_wrong_length_is_refused():
    """A mismatched ceiling is a wiring bug, and the direction it fails in is silent
    over-admission if it is tolerated."""
    judge = [{"spice_tolerance": Grade.STRONG}, {"spice_tolerance": Grade.STRONG}]
    with pytest.raises(ValueError, match="ceiling has 1 checkpoints"):
        EvidenceMap.from_judges("s1", [judge, judge], ceiling=_open(1))


def test_the_ceiling_bounds_a_real_schedule_map():
    """End to end against the shipped generator: judges that grade everything STRONG get
    back exactly the schedule's own provisional map, and nothing above it."""
    from persona_twin import generator, schedule as sched_mod

    run = generator.generate(n=3, seed=7)
    subject = run.subjects[0]
    sched = sched_mod.build_schedule(
        subject_id=subject.subject_id, card=subject.card, session_count=12,
        directives=subject.directives, anti_correlated=subject.anti_correlated,
        hard_case_tags=subject.hard_case_tags, seed=7,
    )
    provisional = sched.provisional_map()
    everything = [
        {f.field_id: Grade.STRONG for f in fields.ALL_FIELDS}
        for _ in range(provisional.checkpoint_count)
    ]
    combined = EvidenceMap.from_judges(
        subject.subject_id, [everything, everything], ceiling=provisional
    )
    for index in range(provisional.checkpoint_count):
        for spec in fields.ALL_FIELDS:
            assert combined.grade(index, spec.field_id) == provisional.grade(
                index, spec.field_id
            )
    combined.validate()
    assert combined.raises, "a maximally over-reading judge must leave a recorded trail"


def test_evidence_grades_must_be_monotone():
    """A field whose grade drops would mean the scorer expects the SUT to forget something
    it was legitimately allowed to carry (spec §1.3) — it would penalize correct behaviour."""
    grades = [
        {f.field_id: Grade.UNEVIDENCED for f in fields.ALL_FIELDS},
        {f.field_id: Grade.UNEVIDENCED for f in fields.ALL_FIELDS},
    ]
    grades[0]["spice_tolerance"] = Grade.STRONG
    grades[1]["spice_tolerance"] = Grade.WEAK
    with pytest.raises(ValueError, match="must be monotone"):
        EvidenceMap("s1", grades).validate()


def test_toy_evidence_maps_validate():
    for build in toy.ALL_TOYS:
        build().evidence.validate()


def test_coverage_reports_final_grade_counts():
    coverage = toy.evidence_ramp().evidence.coverage()
    # By the final checkpoint: 5 preference fields + dietary + allergies.
    assert coverage["STRONG"] == 7
    assert coverage["WEAK"] == 1
    assert sum(coverage.values()) == len(fields.ALL_FIELDS)


# --- subject validation -----------------------------------------------------

def test_all_toy_subjects_validate():
    for build in toy.ALL_TOYS:
        build().validate()


def test_a_correction_on_an_unevidenced_field_is_rejected():
    """Such a correction is unrecoverable: the SUT would be scored stale forever with no
    signal in the transcript to update from."""
    subject = toy.evidence_ramp()
    subject.events = [ScriptedEvent(
        "correction", checkpoint=0, field_id="wait_tolerance",
        old_value="leaves_if_line", new_value="queue_is_part_of_it",
    )]
    with pytest.raises(ValueError, match="could never recover"):
        subject.validate()


def test_an_event_outside_the_checkpoint_range_is_rejected():
    subject = toy.evidence_ramp()
    subject.events = [ScriptedEvent("distractor", checkpoint=99)]
    with pytest.raises(ValueError, match="outside the subject"):
        subject.validate()


def test_a_correction_must_carry_both_values():
    with pytest.raises(ValueError, match="old_value"):
        ScriptedEvent("correction", checkpoint=1, field_id="dietary", new_value=["vegan"])


def test_an_interference_burst_must_name_its_facet_group():
    with pytest.raises(ValueError, match="facet_group"):
        ScriptedEvent("interference", checkpoint=1)


# --- generated JSON Schema (spec §10 item 1) --------------------------------

def test_checked_in_schema_matches_the_registry():
    """The registry is canonical and the JSON Schema is generated from it. If this fails,
    run `python -m persona_twin.emit_schema` — do not hand-edit the JSON."""
    assert SCHEMA_PATH.exists(), f"missing {SCHEMA_PATH}; run emit_schema"
    on_disk = json.loads(SCHEMA_PATH.read_text())
    assert on_disk == emit_schema.build()


def test_schema_declares_a_metric_for_every_field():
    built = emit_schema.build()
    constraints = built["properties"]["hard_constraints"]["properties"]
    preferences = built["properties"]["contexts"]["properties"]["default"]["properties"]
    for spec in fields.ALL_FIELDS:
        block = constraints if spec.is_constraint else preferences
        assert block[spec.field_id]["x-metric"] == spec.metric


def test_schema_accepts_the_unknown_sentinel_on_every_field():
    """spec §2.1: every field in an SUT-emitted card accepts "unknown"."""
    built = emit_schema.build()
    preferences = built["properties"]["contexts"]["properties"]["default"]["properties"]
    for spec in fields.PREFERENCE_FIELDS:
        options = preferences[spec.field_id]["oneOf"]
        assert {"const": UNKNOWN} in options, spec.field_id


def test_schema_publishes_the_level_anchors():
    built = emit_schema.build()
    preferences = built["properties"]["contexts"]["properties"]["default"]["properties"]
    anchors = preferences["spice_tolerance"]["x-level-anchors"]
    assert len(anchors) == 4
    assert anchors[0]["anchor"] == "avoids heat entirely"
