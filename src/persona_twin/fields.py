"""Field registry and closed vocabularies for the persona card (spec-v0 §2).

This module is the **single source of truth** for the card. `schema/persona_card.v0.1.json`
is generated from it (`python -m persona_twin.emit_schema`) and a test asserts the
checked-in JSON still matches, so the two cannot drift.

Three things live here and nowhere else:

1. the closed vocabularies for set-valued fields,
2. the ordered level anchors for 4-level ordinals (spec §2.2 — these are *behavioural*
   anchors, written so a simulator can perform each level distinctly, not Likert points),

   **Adjacent levels must differ by an observable event, not by intensity** (the rule the
   §15 step 3 audit produced; see `probe_anchors/`). An anchor pair that differs only in
   degree — "looks for deals" vs "plans around specials", "joins readily" vs "seeks out
   hands-on formats" — cannot be performed apart: a reader given a snippet written to one
   of them recovers the other. Seven of thirteen fields failed this way on the first pass
   and the misses were one-directional, level 3 read as level 4. The rewrite that fixes it
   is usually to make the top level a *different kind of decision* rather than more of the
   third: at level 3 the trait shows up in what they order or accept, at level 4 it decides
   where they go. Anchors marked with `*because*` are that shape. Any new anchor pair
   should be put through the same round trip before it ships.
3. the per-field declared scoring metric, which `scorer.py` dispatches on.

No scoring logic lives here. Agreement functions are in `metrics.py`.

Design notes carried over from the spec:

* `contexts.default` nesting is mandatory even though v0 has exactly one context (§2.1).
  v1 adds sibling blocks without a schema break. Do not flatten.
* Four ordinal levels, not three: three saturates and the fidelity curve goes flat early,
  which stops AUFC discriminating (§2.2).
* Anything where a point value would lie is a normalized distribution, not a mode (§2.2).

`provenance` is an addition to the spec, adopted from the literature review: a fidelity
number over ground truth *measured* from real behaviour is a materially stronger claim than
one over *synthesized* ground truth, and collapsing them would waste the grounding.

**Every field is currently `synthesized`, and that is accurate rather than provisional**
(decided 2026-08-09). The tag was introduced to serve a plan to seed roughly half the card
from the Yelp Open Dataset; that plan is withdrawn — its licence bars redistributing derived
artifacts, and `DietaryRestrictions` turned out to cover 31 of 150,346 businesses, so
`dietary` was never derivable regardless. Twelve fields previously carried
`PROVENANCE_MEASURED` against that source and have been flipped, because a tag asserting
grounding the dataset does not have is worse than no tag.

The mechanism is kept, not deleted: the v2 human-subject validation arm (spec §13b) is the
remaining route to genuinely measured ground truth, and it needs exactly this distinction.
`PROVENANCE_MEASURED` is therefore live but unused. Do not re-apply it to a field without a
real external source behind it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "0.1"

#: Sentinel an SUT emits for any field it cannot support with evidence (spec §1.1).
UNKNOWN = "unknown"


# --- closed vocabularies (spec §2.4) ----------------------------------------

CUISINES: tuple[str, ...] = (
    "american", "bbq", "brunch", "burgers", "cajun", "caribbean", "chinese",
    "deli", "dim_sum", "ethiopian", "filipino", "french", "german", "greek",
    "indian", "italian", "japanese", "korean", "latin_american", "lebanese",
    "malaysian", "mediterranean", "mexican", "middle_eastern", "moroccan",
    "peruvian", "pizza", "portuguese", "ramen", "seafood", "soul_food",
    "spanish", "steakhouse", "sushi", "taiwanese", "thai", "turkish",
    "vegan_restaurant", "vietnamese",
)

EVENT_TYPES: tuple[str, ...] = (
    "art_opening", "book_reading", "comedy", "cooking_class", "dance_night",
    "film_screening", "food_festival", "karaoke", "live_jazz", "live_rock",
    "market", "museum_late", "open_mic", "pub_quiz", "supper_club",
    "tasting_menu", "theater", "wine_tasting",
)

DIETS: tuple[str, ...] = (
    "vegetarian", "vegan", "pescatarian", "halal", "kosher", "gluten_free",
    "dairy_free", "low_sodium", "keto",
)

ALLERGENS: tuple[str, ...] = (
    "peanuts", "tree_nuts", "shellfish", "fish", "eggs", "milk", "soy",
    "wheat", "sesame",
)

ACCESSIBILITY_NEEDS: tuple[str, ...] = (
    "step_free_access", "accessible_restroom", "wide_aisles", "low_lighting_ok",
    "quiet_room_needed", "service_animal", "seating_required", "braille_menu",
    "assistive_listening",
)


# --- distribution bins (spec §2.4) ------------------------------------------

#: Concrete per-head price, not symbolic tiers — a page that says "£42 a head" or "mains
#: around £24" resolves to a bin without inference, where "$$$" required guessing what one
#: person's "expensive" means. See ADR-0045.
PRICE_BANDS: tuple[str, ...] = ("<£15", "£15-30", "£30-50", "£50+")
PARTY_SIZES: tuple[str, ...] = ("1", "2", "3-5", "6+")
VISIT_TIMES: tuple[str, ...] = (
    "weekday-lunch", "weekday-evening", "weekend-day", "weekend-evening", "late-night",
)


# --- metric kinds -----------------------------------------------------------
# `scorer.py` dispatches on these. Kept as plain strings so the emitted JSON Schema can
# carry them verbatim in each field's `x-metric` annotation.

METRIC_JACCARD = "jaccard"          # sets
METRIC_ORDINAL = "ordinal_distance"  # 4-level ordinals, adjacent = partial credit
METRIC_JSD = "one_minus_jsd"        # normalized distributions
METRIC_EXACT = "exact"              # booleans

#: Reserved for the v2 human-subject arm (spec §13b). Live but currently unused — no v0 field
#: has an external source behind it. See the module docstring before re-applying it.
PROVENANCE_MEASURED = "measured"        # derived from real behavioural data
PROVENANCE_SYNTHESIZED = "synthesized"  # model-generated; every v0 field


@dataclass(frozen=True)
class Field:
    """One scored card field.

    ``levels`` is the ordered anchor set for ordinals (index 0 = low end) and the ordered
    bin set for distributions; it is the closed vocabulary for sets and ``()`` for bools.
    ``anchors`` carries the behavioural level descriptions for ordinals — these are the
    authored artifact of spec §2.2 step 3 and are what the simulator performs.
    """

    field_id: str
    group: str            # "hard_constraints" or a contexts.default facet group
    metric: str           # one of the METRIC_* constants
    levels: tuple[str, ...] = ()
    anchors: tuple[str, ...] = ()
    provenance: str = PROVENANCE_SYNTHESIZED
    doc: str = ""

    @property
    def is_constraint(self) -> bool:
        return self.group == "hard_constraints"


# --- hard constraints: 4 fields, gating track (spec §2.3) -------------------
# Scored separately and never averaged into fidelity (§8.2). A missed constraint and an
# invented constraint are both hard failures.

HARD_CONSTRAINTS: tuple[Field, ...] = (
    Field("dietary", "hard_constraints", METRIC_JACCARD, DIETS,
          doc="Dietary rules the person will not break. Gating."),
    Field("allergies", "hard_constraints", METRIC_JACCARD, ALLERGENS,
          doc="Allergens that must be avoided. Gating; safety-critical."),
    Field("accessibility_needs", "hard_constraints", METRIC_JACCARD, ACCESSIBILITY_NEEDS,
          doc="Access requirements a venue must meet. Gating."),
    Field("alcohol_free_required", "hard_constraints", METRIC_EXACT,
          doc="True when the person requires an alcohol-free setting. Gating, exact match."),
)


# --- contexts.default: 20 scored preference fields (spec §2.4) --------------

_TASTE: tuple[Field, ...] = (
    Field("cuisines_liked", "taste", METRIC_JACCARD, CUISINES,
          doc="Cuisines the person seeks out. Closed vocabulary."),
    Field("cuisines_avoided", "taste", METRIC_JACCARD, CUISINES,
          doc="Cuisines the person declines. Closed vocabulary."),
    Field("spice_tolerance", "taste", METRIC_ORDINAL,
          ("avoids_heat", "mild_only", "likes_real_spice", "seeks_hottest"),
          ("avoids heat entirely",
           "mild only — will ask for it toned down",
           "likes real spice and orders for it",
           "seeks the hottest thing on the menu"),
          doc="Heat preference, 4-level behavioural ordinal."),
    Field("adventurousness", "taste", METRIC_ORDINAL,
          ("sticks_to_known", "familiar_variations", "orders_unfamiliar", "seeks_unidentifiable"),
          ("sticks to dishes they already know",
           "tries variations of the familiar",
           "orders the dish they don't recognize while the table plays safe",
           "picks the place *because* they can't read the menu"),
          doc="Willingness to order outside the known. Cluster C1."),
)

_ECONOMICS: tuple[Field, ...] = (
    Field("price_band_distribution", "economics", METRIC_JSD, PRICE_BANDS,
          doc="Normalized distribution over concrete per-head price bands (<£15, £15-30, "
              "£30-50, £50+). A distribution, not a mode: it preserves bimodal spend (cheap "
              "weeknights, occasional blowouts) that a point value would flatten. Cluster C2."),
    Field("deal_seeking", "economics", METRIC_ORDINAL,
          ("ignores_price", "notices_value", "looks_for_deals", "plans_around_specials"),
          ("ignores price",
           "notices value but doesn't act on it",
           "checks for a discount once they have already chosen the place",
           "lets the happy-hour window pick the place and the hour"),
          doc="Effort spent finding a better price. Cluster C2."),
)

_AMBIANCE: tuple[Field, ...] = (
    Field("noise_preference", "ambiance", METRIC_ORDINAL,
          ("needs_quiet", "prefers_low_hum", "fine_with_lively", "wants_loud"),
          ("needs it quiet enough to talk",
           "prefers a low hum",
           "fine with lively",
           "wants it loud and busy"),
          doc="Tolerated room volume. Cluster C3."),
    Field("formality_ceiling", "ambiance", METRIC_ORDINAL,
          ("counter_service", "casual_sit_down", "smart_casual", "white_tablecloth"),
          ("counter service only",
           "casual sit-down",
           "smart-casual",
           "white tablecloth"),
          doc="Highest formality the person will go to. Cluster C2."),
    Field("design_sensitivity", "ambiance", METRIC_ORDINAL,
          ("doesnt_notice", "mild_preference", "room_matters", "room_over_food"),
          ("doesn't notice the room",
           "mild preference for a nice room",
           "rules out a place whose room they dislike when there is an alternative",
           "will pick a worse meal for a better room"),
          doc="How much the space drives the decision. Cluster C2."),
)

_SOCIAL: tuple[Field, ...] = (
    Field("party_size_distribution", "social", METRIC_JSD, PARTY_SIZES,
          doc="Normalized distribution over party sizes. Cluster C3."),
    Field("solo_dining_comfort", "social", METRIC_ORDINAL,
          ("wont_eat_alone", "takeaway_or_counter", "comfortable_at_table", "enjoys_solo"),
          ("won't eat alone",
           "alone only for takeaway or at a counter",
           "comfortable at a table alone",
           "actively enjoys solo meals"),
          doc="Comfort eating alone. Cluster C3."),
)

_LOGISTICS: tuple[Field, ...] = (
    Field("travel_radius", "logistics", METRIC_ORDINAL,
          ("walking_distance", "own_neighborhood", "anywhere_in_city", "out_of_town"),
          ("only what they can walk to in ten minutes",
           "one short bus or subway ride, never across town",
           "will cross the city for it, but not leave it",
           "will get on a train out of town for one meal"),
          doc="How far the person will go."),
    Field("visit_time_distribution", "logistics", METRIC_JSD, VISIT_TIMES,
          doc="Normalized distribution over visit occasions."),
    Field("planning_style", "logistics", METRIC_ORDINAL,
          ("fully_spontaneous", "decides_same_day", "books_days_out", "books_weeks_ahead"),
          ("decides on the pavement, already out",
           "picks in the morning for that evening",
           "books a few days out",
           "books weeks ahead"),
          doc="Lead time on decisions. Cluster C4."),
    # The top anchor read "plans the evening around being early in the queue, and refuses a
    # table that skips it" until 2026-08-23, and that second clause was a second trait. The
    # first three rungs are CEILINGS — how much waiting the person will put up with — and the
    # fourth flipped to a target and then ruled out the alternative, which is not more
    # tolerance, it is an anti-reservation disposition riding on a ladder named for tolerance.
    # It is the only ordinal in this registry that does it: the four other top anchors carrying
    # a refusal (`participation_vs_spectating`, `rating_reliance`, `novelty_vs_loyalty`,
    # `travel_radius`) each refuse the LOW END OF THEIR OWN SCALE, so the ladder stays one
    # quantity.
    #
    # **Measured, on the store as it stood: every `wait_tolerance` contradiction ever filed
    # quoted that clause and no other level ever produced one** — 6 in the first full reading
    # (`gen-007` x4, `gen-014` x2), 2 surviving at `runs/audit-owner.json`, and 4 pages
    # rewritten to answer it. 5 of 15 subjects carry the level, which is 93 of 230 sessions,
    # and only 8 of those 93 brief the field as a beat — so on the other 85 the card demanded
    # the person refuse a booking while `_offer_problems` forbade either party from raising
    # queueing at all. See ADR-0061.
    Field("wait_tolerance", "logistics", METRIC_ORDINAL,
          ("leaves_if_line", "fifteen_min_max", "hour_for_the_right_place", "queue_is_part_of_it"),
          ("turns around if anyone is waiting ahead of them",
           "waits a quarter of an hour, then leaves",
           "will stand in an hour-long line once they are already there",
           "plans the evening around being early in the queue; a wait is never a reason "
           "to go elsewhere"),
          doc="Queue tolerance — how much waiting deters them, not how much they want. "
              "Cluster C4."),
)

_EVENTS: tuple[Field, ...] = (
    Field("event_types_liked", "events", METRIC_JACCARD, EVENT_TYPES,
          doc="Event formats the person seeks out. Closed vocabulary."),
    Field("event_types_avoided", "events", METRIC_JACCARD, EVENT_TYPES,
          doc="Event formats the person declines. Closed vocabulary."),
    Field("participation_vs_spectating", "events", METRIC_ORDINAL,
          ("watches_only", "joins_if_pushed", "joins_readily", "seeks_hands_on"),
          ("watches only",
           "takes part only when asked directly",
           "joins in unasked at whatever event they happen to be at",
           "won't book an event where they would only be watching"),
          doc="Participation disposition at events."),
)

_QUALITY: tuple[Field, ...] = (
    Field("rating_reliance", "quality_signals", METRIC_ORDINAL,
          ("ignores_ratings", "glances_at_them", "checks_before_going", "threshold_gated"),
          ("ignores ratings",
           "glances at them",
           "checks ratings before going",
           "won't go below a rating threshold"),
          doc="Weight placed on public ratings. Cluster C4."),
    Field("novelty_vs_loyalty", "quality_signals", METRIC_ORDINAL,
          ("same_few_places", "mostly_regulars", "mostly_new", "almost_never_repeats"),
          ("returns to the same few places",
           "mostly regulars, occasional new",
           "mostly new places, some regulars",
           "almost never repeats a venue"),
          doc="Repeat-visit disposition. Cluster C1."),
)

PREFERENCE_FIELDS: tuple[Field, ...] = (
    _TASTE + _ECONOMICS + _AMBIANCE + _SOCIAL + _LOGISTICS + _EVENTS + _QUALITY
)

ALL_FIELDS: tuple[Field, ...] = HARD_CONSTRAINTS + PREFERENCE_FIELDS

BY_ID: dict[str, Field] = {f.field_id: f for f in ALL_FIELDS}

#: The 20 scored preference field ids, in card order.
PREFERENCE_IDS: tuple[str, ...] = tuple(f.field_id for f in PREFERENCE_FIELDS)
#: The 4 gating constraint field ids.
CONSTRAINT_IDS: tuple[str, ...] = tuple(f.field_id for f in HARD_CONSTRAINTS)
#: All 24, constraints first, in card order.
ALL_FIELDS_IDS: tuple[str, ...] = tuple(f.field_id for f in ALL_FIELDS)

#: The multi-element taste/event list fields whose card assigns a full set but whose transcript
#: surfaces only a subset over time, so `card_at` holds the evidenced-so-far value rather than the
#: full card (route-2 phantom prune, 2026-08-27). The grade-leads-value coherence gate
#: (2026-08-28) and the credit layer's Key 1 both key on this set: for these fields a WEAK/STRONG
#: grade at *t* is only coherent once the value has surfaced in `card_at[t]`. Distinct from the
#: jaccard *constraint* fields (dietary, allergies, …), which are not pruned — their card_at is the
#: card. Defined here, the leaf registry, so `sut` and `credit` share one list rather than two.
PRUNED_LIST_FIELDS: frozenset[str] = frozenset(
    ("cuisines_liked", "cuisines_avoided", "event_types_liked", "event_types_avoided")
)


# --- correlated field clusters (spec §3) ------------------------------------
# Declared here because they drive both generation (§4.3) and the anti-correlation
# scoring artifact: a subject tagged `anti_correlated` for cluster C_i deliberately
# violates it, which is what stops the §8.1 fabrication rule from charging an SUT for
# reasoning correctly about within-cluster structure.

CLUSTERS: dict[str, tuple[str, ...]] = {
    "C1_exploration": ("adventurousness", "cuisines_liked", "novelty_vs_loyalty"),
    "C2_spend": ("price_band_distribution", "deal_seeking", "formality_ceiling",
                 "design_sensitivity"),
    "C3_sociality": ("party_size_distribution", "solo_dining_comfort", "noise_preference"),
    "C4_deliberateness": ("planning_style", "wait_tolerance", "rating_reliance"),
}


def ordinal_index(field_id: str, value: str) -> int:
    """Position of ``value`` in the field's ordered level set.

    Raises ``ValueError`` rather than returning a sentinel: a level outside the closed
    set is a malformed card, and silently coercing it would let an SUT dodge scoring.
    """
    field = BY_ID[field_id]
    try:
        return field.levels.index(value)
    except ValueError:
        raise ValueError(
            f"{value!r} is not a level of {field_id}; expected one of {field.levels}"
        ) from None


# --- what a value means, for a prompt ---------------------------------------
# A reader shown only `deal_seeking = 'ignores_price'` has to infer the field from the
# token, and in the pilot one did: it read `ignores_price` as "has no budget ceiling" and
# rejected a correct editor repair for naming a per-head figure, on a field whose doc says
# "Effort spent finding a better price". Budget level is `price_band_distribution` — a
# different field in the same cluster. That single misreading cost the one repair that had
# got `gen-004-s8` right, and the retry that replaced it introduced the contradiction the
# post-edit reader then raised. See ADR-0048.
#
# The anchors already exist and are the authored artifact of §2.2 step 3. They were simply
# never put in front of the reader. `card_block` is what puts them there, and both the
# auditor (`verify.audit_prompt`) and the editor (`editor._rewrite_prompt`) use it so the
# two cannot drift in what they think a level means.

#: Why a distribution needs a different reading rule from an ordinal. A person whose spend
#: is 54% `£50+` and 45% `£30-50` may spend £30-50 today without contradicting anything;
#: what contradicts the card is a claim about *frequency* — "same as usual", "nicer than
#: normal", "a rare treat" — that puts the mode in the wrong bin. Both halves of that were
#: live in the pilot: the original page framed £30-50 as a step up (implying a habitual band
#: below £30, ~1% of the card) and the repair framed it as the norm (implying £30-50 is the
#: mode, when £50+ is). Mirror images of one defect.
DISTRIBUTION_READING_RULE = (
    "A distribution is about what is USUAL, not about today. One occasion may fall in any "
    "bin without contradicting the card — people do unusual things. What contradicts a "
    "distribution is a claim about frequency: calling this visit typical, usual, normal, "
    "standard, a rare treat, or a step up from normal, when the card's weight sits in a "
    "different bin. Absent such a claim, a single choice is not a contradiction.\n"
    "  The exception is a bin the card gives no weight at all. 0.00 is not 'rare', it is "
    "'never', and a person whose card puts 0.00 in a bin walking cheerfully into it is a "
    "contradiction with no frequency claim needed."
)


def gloss(field_id: str) -> str:
    """What ``field_id`` means and what its values mean, as prompt lines.

    Returns the doc, and for ordinals the ordered level anchors, so a reader judging
    whether a page contradicts a value is reading the field's definition rather than
    guessing at its name.
    """
    field = BY_ID[field_id]
    out: list[str] = []
    if field.doc:
        out.append(field.doc)
    if field.metric == METRIC_ORDINAL and field.anchors:
        levels = " < ".join(
            f"{level} ({anchor})"
            for level, anchor in zip(field.levels, field.anchors, strict=False)
        )
        out.append(f"low to high: {levels}")
    elif field.metric == METRIC_JSD:
        out.append(f"bins: {', '.join(field.levels)}")
    elif field.metric == METRIC_JACCARD and field.levels:
        out.append(f"closed vocabulary of {len(field.levels)} values")
    return "\n".join(out)


def card_block(card: Mapping[str, Any], *, indent: str = "  ") -> list[str]:
    """The card as prompt lines: every field's value, followed by what that value means."""
    lines: list[str] = []
    for field_id in ALL_FIELDS_IDS:
        lines.append(f"{indent}{field_id} = {card.get(field_id)!r}")
        for line in gloss(field_id).splitlines():
            lines.append(f"{indent}    {line}")
    return lines
