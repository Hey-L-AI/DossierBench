"""Contradiction rules, cluster position, and texture (spec-v0 §4.3–§4.5).

The load-bearing test in this file is `test_contradiction_rules_pass_every_maximally_*`:
§4.4 warns that a coherence audit which reads §4.3 subjects as incoherent "silently
deletes exactly the subjects the quota exists to create", and the §7.3 probe showed a
judge doing precisely that kind of cross-cluster reasoning while being told not to. The
shipped auditor is built so it cannot, and this is where that claim is checked rather
than asserted.
"""

from __future__ import annotations

import pytest

from persona_twin import coherence, fields, toy
from persona_twin.card import PersonaCard
from persona_twin.coherence import RuleAuditor, check_contradictions, check_texture


def _card(**overrides) -> PersonaCard:
    """A coherent, textured card. Overrides make one thing wrong at a time."""
    values = {
        "dietary": [], "allergies": [], "accessibility_needs": [],
        "alcohol_free_required": False,
        "cuisines_liked": ["thai", "korean", "ramen"],
        "cuisines_avoided": ["pizza"],
        "spice_tolerance": "seeks_hottest",
        "adventurousness": "orders_unfamiliar",
        "price_band_distribution": {"<£15": 0.6, "£15-30": 0.3, "£30-50": 0.1, "£50+": 0.0},
        "deal_seeking": "plans_around_specials",
        "noise_preference": "wants_loud",
        "formality_ceiling": "counter_service",
        "design_sensitivity": "doesnt_notice",
        "party_size_distribution": {"1": 0.0, "2": 0.1, "3-5": 0.5, "6+": 0.4},
        "solo_dining_comfort": "takeaway_or_counter",
        "travel_radius": "walking_distance",
        "visit_time_distribution": {
            "weekday-lunch": 0.1, "weekday-evening": 0.5, "weekend-day": 0.1,
            "weekend-evening": 0.25, "late-night": 0.05,
        },
        # Not `fully_spontaneous`: the base card also carries `plans_around_specials`,
        # and the two together are a contradiction (`spontaneity_conflicts_deal_planning`).
        # The C2 max-spread fixture below needs that deal-seeking value, so the fixture
        # yields rather than the rule — a rule the §4.3 quota can switch off would not be
        # a rule.
        "planning_style": "decides_same_day",
        "wait_tolerance": "leaves_if_line",
        "event_types_liked": ["food_festival", "market"],
        "event_types_avoided": ["karaoke"],
        "participation_vs_spectating": "seeks_hands_on",
        "rating_reliance": "ignores_ratings",
        "novelty_vs_loyalty": "mostly_new",
    }
    values.update(overrides)
    card = PersonaCard("fixture", values)
    card.validate(allow_unknown=False)
    return card


# --- contradictions (§4.4) --------------------------------------------------

def test_a_clean_card_passes_both_checks():
    card = _card()
    assert check_contradictions(card).ok
    assert check_texture(card).ok


@pytest.mark.parametrize("overrides,rule", [
    ({"cuisines_liked": ["thai", "pizza"], "cuisines_avoided": ["pizza"]},
     "liked_and_avoided_overlap"),
    ({"event_types_liked": ["karaoke"], "event_types_avoided": ["karaoke"]},
     "liked_and_avoided_overlap"),
    ({"dietary": ["vegan"], "cuisines_liked": ["steakhouse", "thai"]},
     "diet_conflicts_cuisine"),
    ({"dietary": ["vegan", "pescatarian"]}, "diet_pair_conflict"),
    ({"allergies": ["shellfish"], "cuisines_liked": ["seafood", "thai"]},
     "allergy_conflicts_cuisine"),
    ({"alcohol_free_required": True, "event_types_liked": ["wine_tasting"]},
     "alcohol_free_conflicts_events"),
    ({"solo_dining_comfort": "wont_eat_alone",
      "party_size_distribution": {"1": 0.5, "2": 0.3, "3-5": 0.2, "6+": 0.0}},
     "solo_comfort_conflicts_party_sizes"),
    ({"accessibility_needs": ["quiet_room_needed"], "noise_preference": "wants_loud"},
     "access_conflicts_noise"),
])
def test_each_contradiction_rule_fires(overrides, rule):
    verdict = check_contradictions(_card(**overrides))
    assert not verdict.ok
    assert any(reason.startswith(rule) for reason in verdict.reasons), verdict.reasons


def test_pescatarian_and_seafood_is_not_a_contradiction():
    """The diet rule is about the diet's own definition, not about what looks unusual.
    A rule that fired here would be reasoning about plausibility."""
    assert check_contradictions(
        _card(dietary=["pescatarian"], cuisines_liked=["seafood", "thai"])
    ).ok


#: One card per cluster, built at the maximum possible spread — the §4.3 subject in its
#: most extreme form. Each is atypical by construction and contradiction-free by
#: construction, which is exactly the distinction the audit must respect.
_MAX_SPREAD_CARDS = {
    "C1_exploration": {
        "adventurousness": "seeks_unidentifiable",
        "novelty_vs_loyalty": "same_few_places",
        "cuisines_liked": ["ethiopian", "peruvian", "malaysian", "filipino",
                           "taiwanese", "moroccan", "lebanese", "cajun"],
    },
    "C2_spend": {
        "price_band_distribution": {"<£15": 0.0, "£15-30": 0.0, "£30-50": 0.0, "£50+": 1.0},
        "deal_seeking": "plans_around_specials",
        "formality_ceiling": "white_tablecloth",
        "design_sensitivity": "room_over_food",
    },
    "C3_sociality": {
        "party_size_distribution": {"1": 0.0, "2": 0.0, "3-5": 0.0, "6+": 1.0},
        "solo_dining_comfort": "takeaway_or_counter",
        "noise_preference": "needs_quiet",
    },
    "C4_deliberateness": {
        "planning_style": "books_weeks_ahead",
        "wait_tolerance": "leaves_if_line",
        "rating_reliance": "threshold_gated",
    },
}


@pytest.mark.parametrize("cluster_id", sorted(_MAX_SPREAD_CARDS))
def test_contradiction_rules_pass_every_maximally_anti_correlated_card(cluster_id):
    """§4.4's silent-deletion failure, checked at the extreme.

    A card violating one cluster as hard as the schema permits must still be accepted.
    If a rule ever fires here, the audit has started charging cards for atypicality and
    the §4.3 quota is no longer safe from its own auditor.
    """
    card = _card(**_MAX_SPREAD_CARDS[cluster_id])
    assert coherence.cluster_spread(card, cluster_id) >= \
        coherence.ANTI_CORRELATION_SPREAD
    assert check_contradictions(card).ok, check_contradictions(card).reasons
    assert RuleAuditor()(card, anti_correlated=(cluster_id,)).ok


@pytest.mark.parametrize("cluster_id", sorted(_MAX_SPREAD_CARDS))
def test_the_auditors_verdict_does_not_depend_on_the_tag(cluster_id):
    """The structural claim behind §4.4's mitigation.

    The spec's remedy is to *tell* the auditor about the tag; the §7.3 probe is the reason
    not to rely on being told. `RuleAuditor` accepts the tag and cannot act on it, so the
    verdict is identical with and without — which is what makes the quota safe here and
    what a model-backed auditor has to be measured against (`probe_coherence/`).
    """
    card = _card(**_MAX_SPREAD_CARDS[cluster_id])
    tagged = RuleAuditor()(card, anti_correlated=(cluster_id,))
    untagged = RuleAuditor()(card)
    assert tagged == untagged


# --- cluster position (§4.3) ------------------------------------------------

def test_orientation_is_applied_to_backwards_running_fields():
    """`deal_seeking` and `solo_dining_comfort` run against their cluster's axis. Without
    `ORIENTATION` a card that is consistently frugal would measure as a violation."""
    frugal = _card(
        price_band_distribution={"<£15": 0.7, "£15-30": 0.3, "£30-50": 0.0, "£50+": 0.0},
        deal_seeking="plans_around_specials",
        formality_ceiling="counter_service",
        design_sensitivity="doesnt_notice",
    )
    assert coherence.cluster_spread(frugal, "C2_spend") < \
        coherence.ANTI_CORRELATION_SPREAD
    assert coherence.position("deal_seeking", "plans_around_specials") == 0.0
    assert coherence.position("solo_dining_comfort", "enjoys_solo") == 0.0


def test_the_hand_written_anti_correlated_toy_measures_as_violating_c1():
    """A cross-check against a fixture authored before this metric existed: `toy.py`
    tagged that subject C1 by hand, and the measurement has to agree."""
    subject = toy.anti_correlated()
    assert coherence.violated_clusters(subject.gold) == subject.anti_correlated


def test_every_toy_is_tagged_for_exactly_what_it_violates():
    """The same invariant `generator.verify` enforces on generated cards, applied to the
    hand-written ones. Two toys turned out to carry violations their authors did not
    intend — `with_correction` on C2 and `interference_case` on C3 — and are now tagged,
    because an SUT reasoning across a cluster on those subjects would be charged with
    fabrication for reasoning correctly whether or not the split was deliberate."""
    for factory in toy.ALL_TOYS:
        subject = factory()
        assert coherence.violated_clusters(subject.gold) == subject.anti_correlated, \
            subject.subject_id


def test_position_flattens_a_bimodal_distribution():
    """Documented limitation of the scalar (`coherence.position`): bimodal spend measures
    mid, which is why §4.2's bimodal hard case is a texture property and why the generator
    poles the anchor before building a C2 violation."""
    bimodal = {"<£15": 0.45, "£15-30": 0.05, "£30-50": 0.05, "£50+": 0.45}
    steady = {"<£15": 0.0, "£15-30": 0.5, "£30-50": 0.5, "£50+": 0.0}
    assert coherence.position("price_band_distribution", bimodal) == \
        pytest.approx(coherence.position("price_band_distribution", steady), abs=0.02)


# --- texture (§4.5) ---------------------------------------------------------

def test_uniformly_mid_scale_card_is_rejected():
    mid = {
        spec.field_id: spec.levels[1]
        for spec in fields.PREFERENCE_FIELDS
        if spec.metric == fields.METRIC_ORDINAL
    }
    verdict = check_texture(_card(**mid))
    assert not verdict.ok
    assert any(r.startswith("uniformly_mid_scale") for r in verdict.reasons)


def test_the_mid_scale_hard_case_is_exempt_only_with_its_tag():
    """§4.2's fourth hard case against §4.5's rule. The exemption is what reconciles them,
    and it is deliberately narrow: only the extreme-ordinal rule is waived."""
    mid = {
        spec.field_id: spec.levels[1]
        for spec in fields.PREFERENCE_FIELDS
        if spec.metric == fields.METRIC_ORDINAL
    }
    card = _card(**mid)
    assert not check_texture(card).ok
    assert check_texture(card, (coherence.MID_SCALE_HARD_CASE,)).ok
    # ... but not the distribution rule.
    flat = check_texture(
        _card(**mid, price_band_distribution={"<£15": 0.25, "£15-30": 0.25,
                                              "£30-50": 0.25, "£50+": 0.25}),
        (coherence.MID_SCALE_HARD_CASE,),
    )
    assert not flat.ok
    assert any(r.startswith("near_uniform_distribution") for r in flat.reasons)


def test_near_uniform_distribution_is_rejected():
    verdict = check_texture(_card(
        visit_time_distribution={
            "weekday-lunch": 0.2, "weekday-evening": 0.2, "weekend-day": 0.2,
            "weekend-evening": 0.2, "late-night": 0.2,
        }
    ))
    assert not verdict.ok
    assert any(r.startswith("near_uniform_distribution") for r in verdict.reasons)


def test_thin_cuisines_liked_is_rejected():
    verdict = check_texture(_card(cuisines_liked=["thai"]))
    assert any(r.startswith("thin_cuisines_liked") for r in verdict.reasons)


# --- rules adopted from the probe (round 1) ---------------------------------

def test_the_two_probe_derived_rules_fire():
    """`probe_coherence/` round 1 found both by rejecting generated cards for them. They
    are value-level contradictions like the rest — no cluster is consulted — and both are
    also repaired at the draw, so the rule is the guard rather than the mechanism."""
    quiet = check_contradictions(
        _card(noise_preference="needs_quiet", event_types_liked=["karaoke", "market"])
    )
    assert any(r.startswith("quiet_conflicts_loud_events") for r in quiet.reasons)
    planning = check_contradictions(
        _card(planning_style="fully_spontaneous", deal_seeking="plans_around_specials")
    )
    assert any(
        r.startswith("spontaneity_conflicts_deal_planning") for r in planning.reasons
    )


def test_a_quiet_room_with_quiet_events_is_fine():
    """The rule is about the volume of the event, not about quiet people being unusual."""
    assert check_contradictions(
        _card(noise_preference="needs_quiet",
              # One participatory format, because the base fixture seeks hands-on ones:
              # a cooking class is quiet and is not spectating.
              event_types_liked=["book_reading", "cooking_class"])
    ).ok


def test_disposition_conflicts_with_events_only_when_every_event_conflicts():
    """A hands-on person who also likes films is not contradictory — the rule fires on
    cards where *nothing* they seek out matches the disposition they declare."""
    assert check_contradictions(
        _card(participation_vs_spectating="seeks_hands_on",
              event_types_liked=["film_screening", "cooking_class"])
    ).ok
    verdict = check_contradictions(
        _card(participation_vs_spectating="seeks_hands_on",
              event_types_liked=["film_screening", "theater"])
    )
    assert any(r.startswith("disposition_conflicts_events") for r in verdict.reasons)


# --- ingredient entailment: the cuisine/constraint collision -----------------
#
# Every case below is a card the pre-2026-08-21 rules called clean and the pilot then
# proved unwritable. `runs/result-standing-read.txt` has the read; the short version is
# that `cuisines_liked` is drawn independently of the gating fields, a cuisine entails
# what goes on the plate, and the author is handed a brief that instructs it to evidence
# the thing the same brief forbids. All four historical misses are here as regressions.

@pytest.mark.parametrize("overrides,rule", [
    # gen-004: vegan, and told to make `american` and `pizza` unmistakable.
    ({"dietary": ["vegan"], "cuisines_liked": ["american", "thai"]},
     "diet_conflicts_cuisine"),
    ({"dietary": ["vegan"], "cuisines_liked": ["pizza", "thai"]},
     "diet_conflicts_cuisine"),
    # gen-002: dairy-free with pizza among the cuisines it seeks out.
    ({"dietary": ["dairy_free"], "cuisines_liked": ["pizza", "thai"]},
     "diet_conflicts_cuisine"),
    # gen-002 again, the half no name check could see: the tokens differ.
    ({"cuisines_avoided": ["seafood"], "cuisines_liked": ["sushi", "thai"]},
     "avoided_conflicts_cuisine"),
    # diets that were never checked at all before, only vegan/vegetarian were.
    ({"dietary": ["halal"], "cuisines_liked": ["german", "thai"]},
     "diet_conflicts_cuisine"),
    ({"dietary": ["gluten_free"], "cuisines_liked": ["ramen", "thai"]},
     "diet_conflicts_cuisine"),
    # allergens beyond shellfish/fish, which were the only two mapped.
    ({"allergies": ["milk"], "cuisines_liked": ["pizza", "thai"]},
     "allergy_conflicts_cuisine"),
    ({"allergies": ["fish"], "cuisines_liked": ["sushi", "thai"]},
     "allergy_conflicts_cuisine"),
])
def test_each_ingredient_collision_is_caught(overrides, rule):
    verdict = check_contradictions(_card(**overrides))
    assert not verdict.ok
    assert any(reason.startswith(rule) for reason in verdict.reasons), verdict.reasons


@pytest.mark.parametrize("overrides", [
    # THE TABLE IS DEFINITIONAL, NOT TYPICAL, and these are the cases that hold it there.
    # Thai food is full of peanuts and is not defined by them; mapping the two would make
    # this a typicality judge, which §4.4 says silently deletes subjects.
    {"allergies": ["peanuts"], "cuisines_liked": ["thai", "chinese"]},
    {"allergies": ["sesame"], "cuisines_liked": ["middle_eastern", "lebanese"]},
    {"dietary": ["vegan"], "cuisines_liked": ["ethiopian", "indian"]},
    {"dietary": ["vegan"], "cuisines_liked": ["italian", "mexican"]},
    # `pescatarian` eats fish: the one diet that must not conflict with seafood.
    {"dietary": ["pescatarian"], "cuisines_liked": ["seafood", "sushi"]},
    # Two meat cuisines, one sought and one declined, is an ordinary person. This is why
    # `AVOIDING_FORBIDS` holds one entry rather than eleven.
    {"cuisines_liked": ["bbq", "thai"], "cuisines_avoided": ["steakhouse"]},
    # A shellfish allergy alongside liked sushi stays clean: sushi is defined by fish, not
    # by shellfish, and `continuity_for` guards the allergen at brief time. gen-001 was
    # drawn this way and no page of 16 put the user near shellfish. The table covers what
    # a guard *cannot* reach — you cannot write "seeks out pizza" without the cheese.
    {"allergies": ["shellfish"], "cuisines_liked": ["sushi", "thai"]},
])
def test_the_table_does_not_reach_past_definition(overrides):
    verdict = check_contradictions(_card(**overrides))
    assert not [r for r in verdict.reasons if "cuisine" in r], verdict.reasons


def test_the_strictest_diet_still_leaves_a_workable_pool():
    """Over-including in the table starves the draw, so this is a floor on persona space."""
    from persona_twin import coherence
    for diet, forbids in coherence.DIET_FORBIDS.items():
        survivors = coherence.unconstrained_cuisines(forbids)
        assert len(survivors) >= 20, (diet, len(survivors))
