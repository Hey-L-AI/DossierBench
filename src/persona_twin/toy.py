"""Hand-written toy subjects for building the scorer against (spec-v0 §15 step 1).

"Build it against hand-written toy subjects first." These are not dataset subjects and are
never published as such — they exist so the scorer can be falsified before any generation,
simulation, or audit machinery exists. They are deliberately small and legible: if a metric
misbehaves here, reading the fixture tells you why.

Each toy exercises one property the scorer must have:

* `evidence_ramp` — evidence accrues over three checkpoints. The base case.
* `with_correction` — a scripted one-field correction, for staleness and latency.
* `never_evidenced` — nothing is ever evidenced, so any commit is pure fabrication.
* `anti_correlated` — a subject violating cluster C1, the §4.3 scorer-validity case.
* `interference_case` — a burst on one facet group, for collateral damage.

**The toys must not share a modal card.** An earlier version built every toy from one base
card with a few overrides, which made the population-mode baseline a perfect oracle on them
(A = 1.000) and turned the §11 "population-mode is an honest bar" check vacuously true. Each
toy now carries a distinct profile, and `population_sample` supplies a wider, deliberately
varied set to fit the baseline on. If a change here makes the toys converge again, the
acceptance suite silently stops testing anything.
"""

from __future__ import annotations

#: AUFC density threshold to use when scoring these fixtures (ADR-0004).
#:
#: The toys evidence 2–3 of 20 preference fields — they were written to exercise the scorer's
#: edges, and a thin evidenced band is one of those edges. That puts every toy checkpoint
#: **below** `scorer.MIN_EVIDENCED_FOR_AUFC`, so scoring them at the production default yields
#: `aufc = None` for every subject and every baseline.
#:
#: That is the gate working, not a bug: ADR-0004's whole finding is that these magnitudes are
#: a property of the fixtures rather than of the rule. But a fixture that cannot be averaged
#: cannot check averaging arithmetic, so anything scoring a toy passes `1` — an explicit
#: opt-out that keeps the AUFC-mechanics tests meaningful while leaving the production
#: threshold untouched. Do **not** use this value on a real subject; regenerating the toys at
#: a realistic density is the standing fix (ADR-0004, Alternatives).
TOY_MIN_EVIDENCED = 1

from typing import Any

from . import fields
from .card import PersonaCard
from .evidence import EvidenceMap, Grade
from .subject import ScriptedEvent, Subject

S, W, U = Grade.STRONG, Grade.WEAK, Grade.UNEVIDENCED


def _base_values(**overrides: Any) -> dict[str, Any]:
    """A complete, plausible gold card. Overrides replace individual fields.

    Deliberately *not* uniformly mid-scale: spec §4.5 rejects texture-free cards because
    they give the simulator nothing expressible, and a mid-scale toy would also hide
    ordinal-distance bugs behind small errors.
    """
    values: dict[str, Any] = {
        # hard constraints
        "dietary": ["vegetarian"],
        "allergies": ["peanuts"],
        "accessibility_needs": [],
        "alcohol_free_required": False,
        # taste
        "cuisines_liked": ["thai", "vietnamese", "korean"],
        "cuisines_avoided": ["steakhouse"],
        "spice_tolerance": "seeks_hottest",
        "adventurousness": "orders_unfamiliar",
        # economics
        "price_band_distribution": {"<£15": 0.5, "£15-30": 0.4, "£30-50": 0.1, "£50+": 0.0},
        "deal_seeking": "looks_for_deals",
        # ambiance
        "noise_preference": "fine_with_lively",
        "formality_ceiling": "casual_sit_down",
        "design_sensitivity": "doesnt_notice",
        # social
        "party_size_distribution": {"1": 0.1, "2": 0.5, "3-5": 0.3, "6+": 0.1},
        "solo_dining_comfort": "comfortable_at_table",
        # logistics
        "travel_radius": "own_neighborhood",
        "visit_time_distribution": {
            "weekday-lunch": 0.3, "weekday-evening": 0.4, "weekend-day": 0.1,
            "weekend-evening": 0.2, "late-night": 0.0,
        },
        "planning_style": "decides_same_day",
        "wait_tolerance": "hour_for_the_right_place",
        # events
        "event_types_liked": ["food_festival", "market"],
        "event_types_avoided": ["karaoke"],
        "participation_vs_spectating": "joins_readily",
        # quality signals
        "rating_reliance": "checks_before_going",
        "novelty_vs_loyalty": "mostly_new",
    }
    values.update(overrides)
    return values


def _grades(pattern: dict[str, Grade], default: Grade = U) -> dict[str, Grade]:
    grades = {f.field_id: default for f in fields.ALL_FIELDS}
    grades.update(pattern)
    return grades


#: Distinct per-toy profiles, so no two toys share a modal value on the fields they
#: evidence. See the module docstring for why this matters.
_PROFILES: dict[str, dict[str, Any]] = {
    "toy-ramp": {},
    "toy-correction": {
        "spice_tolerance": "mild_only",
        "cuisines_liked": ["italian", "french"],
        "deal_seeking": "ignores_price",
        "formality_ceiling": "smart_casual",
    },
    "toy-blank": {
        "spice_tolerance": "avoids_heat",
        "cuisines_liked": ["american", "burgers"],
        "noise_preference": "needs_quiet",
        "travel_radius": "walking_distance",
    },
    "toy-interference": {
        "noise_preference": "wants_loud",
        "travel_radius": "anywhere_in_city",
        "planning_style": "books_weeks_ahead",
        "spice_tolerance": "likes_real_spice",
        "cuisines_liked": ["japanese", "sushi", "ramen"],
    },
}


def population_sample() -> list[PersonaCard]:
    """A deliberately varied set of gold cards for fitting the population-mode baseline.

    Fitting on the toys themselves would be circular — the baseline would be fit on the
    very cards it is scored against. These stand in for the public split (spec §8.8: fit on
    the public split, never on the split being scored).
    """
    variants: list[dict[str, Any]] = [
        {},
        {"spice_tolerance": "avoids_heat", "noise_preference": "needs_quiet",
         "formality_ceiling": "white_tablecloth", "cuisines_liked": ["french"],
         "travel_radius": "walking_distance", "planning_style": "books_weeks_ahead"},
        {"spice_tolerance": "mild_only", "noise_preference": "prefers_low_hum",
         "adventurousness": "sticks_to_known", "cuisines_liked": ["italian", "pizza"],
         "deal_seeking": "ignores_price", "novelty_vs_loyalty": "same_few_places"},
        {"spice_tolerance": "likes_real_spice", "noise_preference": "wants_loud",
         "cuisines_liked": ["mexican", "bbq"], "wait_tolerance": "leaves_if_line",
         "planning_style": "fully_spontaneous", "rating_reliance": "ignores_ratings"},
        {"spice_tolerance": "avoids_heat", "adventurousness": "familiar_variations",
         "cuisines_liked": ["american"], "solo_dining_comfort": "wont_eat_alone",
         "design_sensitivity": "room_over_food", "dietary": []},
        {"spice_tolerance": "mild_only", "noise_preference": "needs_quiet",
         "cuisines_liked": ["greek", "mediterranean"], "travel_radius": "out_of_town",
         "formality_ceiling": "counter_service", "allergies": []},
    ]
    return [
        PersonaCard(f"pop-{index}", _base_values(**overrides))
        for index, overrides in enumerate(variants)
    ]


def evidence_ramp() -> Subject:
    """Three checkpoints; evidence accrues monotonically.

    Checkpoint 0 pins two fields and hints at two: `price_band_distribution` (a distribution,
    so its WEAK grade is excluded by the §8.1a low-N carve-out) and `deal_seeking` (an ordinal
    that carries the WEAK band once the distribution is set aside). By checkpoint 2 most of the
    taste and economics block is strong. Everything else stays unevidenced throughout, which
    keeps a large U(t) band so the fabrication term is actually exercised.
    """
    gold = PersonaCard("toy-ramp", _base_values(**_PROFILES["toy-ramp"]))
    grades = [
        _grades({
            "cuisines_liked": S,
            "spice_tolerance": S,
            "price_band_distribution": W,
            "deal_seeking": W,
        }),
        _grades({
            "cuisines_liked": S,
            "spice_tolerance": S,
            "price_band_distribution": S,
            "deal_seeking": W,
            "dietary": S,
        }),
        _grades({
            "cuisines_liked": S,
            "spice_tolerance": S,
            "price_band_distribution": S,
            "deal_seeking": S,
            "adventurousness": S,
            "noise_preference": W,
            "dietary": S,
            "allergies": S,
        }),
    ]
    return Subject(
        subject_id="toy-ramp",
        gold=gold,
        evidence=EvidenceMap("toy-ramp", grades),
        tier="medium",
        simulator_model="hand-written",
        events=[ScriptedEvent("distractor", checkpoint=1)],
    )


def with_correction() -> Subject:
    """Four checkpoints with a dietary correction landing at checkpoint 2.

    The gold card is the *post-correction* state; `gold_at` rewinds it for checkpoints 0–1.
    This is the degenerate drift case v2 generalizes, and the fixture that makes staleness
    and post-correction latency testable.
    """
    gold = PersonaCard(
        "toy-correction",
        _base_values(**_PROFILES["toy-correction"], dietary=["vegan"]),
    )
    strong = {"dietary": S, "cuisines_liked": S, "spice_tolerance": S}
    grades = [_grades(strong) for _ in range(4)]
    return Subject(
        subject_id="toy-correction",
        gold=gold,
        evidence=EvidenceMap("toy-correction", grades),
        tier="medium",
        simulator_model="hand-written",
        events=[
            ScriptedEvent(
                "correction", checkpoint=2,
                field_id="dietary", old_value=["vegetarian"], new_value=["vegan"],
            ),
        ],
        # Not deliberate, and found only when `coherence.cluster_spread` was built for
        # §4.3: this profile spends at the bottom two bands while ignoring price, with a
        # smart-casual ceiling and no interest in the room — a C2 violation as the cluster
        # is declared. The tag records what a card *does* rather than what it was for,
        # because the §4.3 protection an SUT is owed does not depend on the author's
        # intent. Same convention as `generator.GeneratedSubject.anti_correlated`.
        anti_correlated=("C2_spend",),
    )


def never_evidenced() -> Subject:
    """Two checkpoints, nothing ever evidenced.

    Every preference field sits in U(t), so fab(t) is 1.0 for any card that commits
    anywhere and 0.0 for one that abstains everywhere. This is the fixture that proves the
    fabrication rule is band-determined rather than value-determined: committing the exact
    ground-truth card here must still score as fabrication.
    """
    gold = PersonaCard("toy-blank", _base_values(**_PROFILES["toy-blank"]))
    return Subject(
        subject_id="toy-blank",
        gold=gold,
        evidence=EvidenceMap.never_evidenced("toy-blank", 2),
        tier="sparse",
        simulator_model="hand-written",
    )


def anti_correlated() -> Subject:
    """An adventurous eater who is fiercely loyal to three places — violating cluster C1.

    The §4.3 case. Its purpose is scorer validity, not realism: without subjects like this,
    an SUT that infers `novelty_vs_loyalty` from `adventurousness` is charged with
    fabrication for reasoning correctly, and that artifact is indistinguishable from
    genuine failure.
    """
    gold = PersonaCard("toy-anti", _base_values(
        adventurousness="seeks_unidentifiable",
        novelty_vs_loyalty="same_few_places",
        cuisines_liked=["ethiopian", "peruvian", "malaysian", "filipino"],
    ))
    grades = [
        _grades({"adventurousness": S, "cuisines_liked": S}),
        _grades({"adventurousness": S, "cuisines_liked": S, "novelty_vs_loyalty": S}),
    ]
    return Subject(
        subject_id="toy-anti",
        gold=gold,
        evidence=EvidenceMap("toy-anti", grades),
        tier="medium",
        simulator_model="hand-written",
        anti_correlated=("C1_exploration",),
    )


def interference_case() -> Subject:
    """Three checkpoints with an interference burst on `taste` at checkpoint 2.

    Fields outside the `taste` group are already settled by checkpoint 1, so any drop in
    their agreement at checkpoint 2 is collateral damage from the burst.
    """
    gold = PersonaCard(
        "toy-interference", _base_values(**_PROFILES["toy-interference"])
    )
    settled = {
        "noise_preference": S, "travel_radius": S, "planning_style": S,
        "cuisines_liked": S, "spice_tolerance": S,
    }
    grades = [_grades(settled) for _ in range(3)]
    return Subject(
        subject_id="toy-interference",
        gold=gold,
        evidence=EvidenceMap("toy-interference", grades),
        tier="rich",
        simulator_model="hand-written",
        events=[ScriptedEvent("interference", checkpoint=2, facet_group="taste")],
        # Incidental, like `with_correction`'s: wants a loud room while dining in twos
        # and threes and being comfortable alone splits C3. See that function for why the
        # tag follows the values rather than the intent.
        anti_correlated=("C3_sociality",),
    )


ALL_TOYS = (
    evidence_ramp, with_correction, never_evidenced, anti_correlated, interference_case,
)
