"""Card-level coherence, texture, and cluster-position measurement (spec-v0 §4.3–§4.5).

Three things live here, and the separation between the first two is the point of the
module:

1. **Contradiction checking** (§4.4) — value-level impossibilities. A card claiming
   ``alcohol_free_required`` while listing `wine_tasting` among liked events is broken
   whoever the subject is.
2. **Cluster position measurement** (§4.3) — how far apart a card sits on the fields of
   one declared §3 cluster. This is *description*, never a verdict.
3. **Texture checking** (§4.5) — whether a card gives the simulator anything expressible.

**The audit must never be able to reject a card for atypicality.** §4.4 warns that a
coherence judge which reads §4.3 subjects as incoherent "silently deletes exactly the
subjects the quota exists to create" — the anti-correlation quota is the only instrument
protecting the §8.1 fabrication rule from charging an SUT for reasoning correctly about
cluster structure, so a loop that quietly removes it disables a load-bearing control while
every published number still looks fine.

The spec's mitigation is to *tell* the judge to check performability rather than
typicality. The §7.3 probe (`probe/`) is the reason not to rely on that alone: judges there
inferred a field from its §3 cluster siblings on the worst-agreeing item class **while the
prompt was explicitly telling them not to**. So the mitigation here is structural rather
than hortatory: `check_contradictions` never reads `CLUSTERS`, takes no `anti_correlated`
argument, and has no access to any notion of a typical card. It cannot delete the quota
because it cannot see it. `test_coherence.py` asserts this on maximally anti-correlated
cards.

What that structure gives up is the judgment §4.4 actually asks for — *performability*,
which is a prose question a rule cannot answer. That half stays model-mediated and is
measured before it is granted authority; see `probe_coherence/`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from . import fields
from .card import PersonaCard

#: Cluster spread at or above which a card counts as violating the cluster (§4.3).
#: 0.5 on the normalized position scale is two full ordinal steps of a 4-level field —
#: "adventurous eater who is fiercely loyal to three places" scores 1.0 here, an ordinary
#: card with one field a step out of line scores 0.33.
ANTI_CORRELATION_SPREAD = 0.5

#: Set size at which `cuisines_liked` counts as maximally broad, for the C1 position
#: scalar. Not a cap on the field — a card may list more.
BREADTH_REFERENCE = 8

#: Orientation of each cluster field, so that a higher position means "further along the
#: cluster's named axis". Two fields run backwards against their cluster's name and are
#: declared here rather than being silently mis-measured: high `deal_seeking` is *low*
#: spend, and high `solo_dining_comfort` is *low* sociality.
ORIENTATION: dict[str, int] = {
    # C1 exploration
    "adventurousness": +1,
    "cuisines_liked": +1,
    "novelty_vs_loyalty": +1,
    # C2 spend
    "price_band_distribution": +1,
    "deal_seeking": -1,
    "formality_ceiling": +1,
    "design_sensitivity": +1,
    # C3 sociality
    "party_size_distribution": +1,
    "solo_dining_comfort": -1,
    "noise_preference": +1,
    # C4 deliberateness
    "planning_style": +1,
    "wait_tolerance": +1,
    "rating_reliance": +1,
}


@dataclass(frozen=True)
class Verdict:
    """The outcome of one audit. ``reasons`` are machine-readable rule ids plus prose."""

    ok: bool
    reasons: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.ok

    @staticmethod
    def passed() -> Verdict:
        return Verdict(True, ())

    @staticmethod
    def failed(*reasons: str) -> Verdict:
        return Verdict(False, tuple(reasons))


class Auditor(Protocol):
    """What the generator calls to accept or reject a candidate card (§4.4).

    The ``anti_correlated`` tag is passed because §4.4 requires the auditor receive it.
    `RuleAuditor` ignores it — it has nothing to use it for, which is the property that
    makes it safe. A model-backed auditor needs it, and needs its false-reject rate on
    tagged cards measured before it is given authority to regenerate.
    """

    def __call__(
        self, card: PersonaCard, *, anti_correlated: tuple[str, ...] = (),
        hard_case_tags: tuple[str, ...] = (),
    ) -> Verdict: ...


# --- 1. contradictions (§4.4) -----------------------------------------------
# Every rule below is a value-level impossibility: two committed values that cannot both
# describe one person. None of them consults §3, and none of them asks whether a
# combination is *usual*. Adding a rule that does would reintroduce the failure this
# module is shaped to prevent.
#
# Two of them — `quiet_conflicts_loud_events` and `spontaneity_conflicts_deal_planning` —
# came from `probe_coherence/` round 1: the model judges rejected generated cards for
# tensions this rule set could not see, and on inspection they were right. 17 of 60 cards
# in the first run wanted a room quiet enough to talk in while listing karaoke among the
# events they seek out. That is the model auditor earning its place — and the reason the
# fix lands here *and* in the draw (`generator._resolve_cross_field_tensions`), since a
# defect the generator can avoid should not be spending §4.6's attempt budget.
#
# One rejection was **not** adopted: a judge called a card incoherent because
# `braille_menu` alongside a room-driven `design_sensitivity` meant a blind person could
# not care about the room. That is an inference about what a person with that access need
# is like, not a contradiction between two values, and adopting it would have deleted the
# subject. It is the concrete case for why the model advises here and does not delete.

#: --- ingredient entailment (the cuisine/constraint collision) -------------------
#:
#: `cuisines_liked` is drawn independently of `dietary`, `allergies` and
#: `cuisines_avoided`, and a cuisine is not orthogonal to any of them: it entails what
#: goes on the plate. The 2026-08-21 read of the pilot's blocking contradictions
#: (`runs/result-standing-read.txt`) found the whole `standing` class was this and
#: nothing else — a brief telling the author, in the same breath, that the person is
#: vegan and that this session must evidence `american` and `pizza`. The author obeyed
#: the instruction it was given last and wrote burgers. There is no page that satisfies
#: both halves, so this has to be prevented at the draw rather than repaired downstream.
#:
#: THE INCLUSION CRITERION IS DEFINITIONAL, NOT TYPICAL, and the distinction is the same
#: one this module's header is built around. An ingredient belongs in a cuisine's set only
#: when a person who never eats it could not meaningfully be said to *seek that cuisine
#: out*. "Usually contains" is not enough: `thai` is full of peanuts and is not defined by
#: them, `italian` is mostly not a meat cuisine, and `ethiopian` has a whole vegan
#: tradition. Widening this table past definition is how a value-level rule turns into a
#: typicality judge, which §4.4 warns silently deletes the subjects the quota exists to
#: create — and here it would also starve the draw, since `_FAMILIAR` is only 14 wide and
#: a low-exploration card takes 2-3 from it.
#:
#: Thirty-nine cuisines are in the vocabulary and ELEVEN carry an entailment. The other
#: twenty-eight are writable by anyone, which is the point.

#: Ingredient classes. `pork` is a subset of `meat` and cuisines that entail it name both,
#: so a reader of this table never has to know the hierarchy.
MEAT, PORK, FISH, SHELLFISH, DAIRY, EGG, WHEAT = (
    "meat", "pork", "fish", "shellfish", "dairy", "egg", "wheat",
)

#: What seeking a cuisine out puts on the plate. Absent means no entailment.
CUISINE_INGREDIENTS: dict[str, frozenset[str]] = {
    "bbq": frozenset({MEAT}),
    "burgers": frozenset({MEAT}),
    "steakhouse": frozenset({MEAT}),
    "soul_food": frozenset({MEAT}),
    "deli": frozenset({MEAT}),          # a deli is defined by the cured meat counter
    "american": frozenset({MEAT}),      # the residual once bbq/burgers/steakhouse split out
    "german": frozenset({MEAT, PORK}),  # sausage and schnitzel are the cuisine
    "seafood": frozenset({FISH, SHELLFISH}),
    "sushi": frozenset({FISH}),
    "pizza": frozenset({DAIRY, WHEAT}), # cheese on a wheat base
    "ramen": frozenset({WHEAT}),        # the wheat noodle is the dish; the broth varies
}

#: What each diet will not eat. `low_sodium` entails nothing structural and is absent.
DIET_FORBIDS: dict[str, frozenset[str]] = {
    "vegetarian": frozenset({MEAT, PORK, FISH, SHELLFISH}),
    "vegan": frozenset({MEAT, PORK, FISH, SHELLFISH, DAIRY, EGG}),
    "pescatarian": frozenset({MEAT, PORK}),   # deliberately not FISH/SHELLFISH
    "halal": frozenset({PORK}),
    "kosher": frozenset({PORK, SHELLFISH}),
    "gluten_free": frozenset({WHEAT}),
    "dairy_free": frozenset({DAIRY}),
    "keto": frozenset({WHEAT}),               # the wheat base is what a keto card refuses
}

#: What each allergen forbids. Five of the nine allergens map to nothing, because no
#: cuisine in the vocabulary is *defined* by peanuts, tree nuts, soy or sesame. That is a
#: deliberate absence, not an oversight: mapping `peanuts` to `thai` is the typicality
#: judgement this table exists to refuse.
ALLERGEN_FORBIDS: dict[str, frozenset[str]] = {
    "shellfish": frozenset({SHELLFISH}),
    "fish": frozenset({FISH}),
    "milk": frozenset({DAIRY}),
    "eggs": frozenset({EGG}),
    "wheat": frozenset({WHEAT}),
}

#: Cuisine tokens that name an ingredient class rather than a kitchen, so *avoiding* them
#: forbids the ingredient wherever else it turns up. Only `seafood` qualifies: "I don't go
#: for seafood" does mean no fish, whereas "I don't go for steakhouses" plainly does not
#: mean no meat. Keeping this separate from `CUISINE_INGREDIENTS` is what stops the check
#: from reading a liked `steakhouse` against an avoided `bbq` as a contradiction — two
#: meat cuisines, one sought and one declined, is an ordinary person.
AVOIDING_FORBIDS: dict[str, frozenset[str]] = {
    "seafood": frozenset({FISH, SHELLFISH}),
}

#: Diets that cannot be held simultaneously.
_DIET_CONFLICTS: tuple[tuple[str, str], ...] = (
    ("vegan", "pescatarian"),
    ("vegetarian", "pescatarian"),
)


def forbidden_ingredients(values: Mapping[str, Any]) -> frozenset[str]:
    """Every ingredient this card's gating fields rule out, from all three sources.

    Read by `check_contradictions` to detect and by `generator._respect_constraints` to
    prevent, so the two can no longer drift — before this there were four hardcoded ban
    lists across two modules and they already disagreed.
    """
    out: set[str] = set()
    for diet in values.get("dietary") or ():
        out |= DIET_FORBIDS.get(str(diet), frozenset())
    for allergen in values.get("allergies") or ():
        out |= ALLERGEN_FORBIDS.get(str(allergen), frozenset())
    for cuisine in values.get("cuisines_avoided") or ():
        out |= AVOIDING_FORBIDS.get(str(cuisine), frozenset())
    return frozenset(out)


def conflicting_cuisines(
    liked: Iterable[str], forbidden: Iterable[str]
) -> frozenset[str]:
    """The liked cuisines that entail something forbidden."""
    banned = frozenset(forbidden)
    return frozenset(
        c for c in liked
        if CUISINE_INGREDIENTS.get(str(c), frozenset()) & banned
    )


def unconstrained_cuisines(forbidden: Iterable[str]) -> tuple[str, ...]:
    """Every cuisine in the vocabulary a card with these exclusions can still seek out."""
    banned = frozenset(forbidden)
    return tuple(
        c for c in fields.CUISINES
        if not (CUISINE_INGREDIENTS.get(c, frozenset()) & banned)
    )

#: Event formats whose point is the alcohol.
_ALCOHOL_EVENTS = frozenset({"wine_tasting"})

#: Event formats whose point is the volume. Seeking these out while needing a room quiet
#: enough to talk in is a contradiction about the same behaviour, not a statement about
#: which combinations are common.
_LOUD_EVENTS = frozenset({"karaoke", "live_rock", "dance_night", "open_mic"})

#: Above this share of solo occasions, "won't eat alone" is not a description of the same
#: person. Set well above zero: an occasional solo takeaway is not a contradiction.
_SOLO_MASS_CONTRADICTION = 0.25

#: Event formats you can only watch, and formats you take part in. The rest of the
#: vocabulary (`food_festival`, `market`, `tasting_menu`) is neither, and a card is only
#: contradictory when *every* format it seeks out runs against its stated disposition —
#: seeking hands-on formats while the only events sought are films and readings.
_SPECTATOR_EVENTS = frozenset({
    "art_opening", "book_reading", "comedy", "film_screening", "live_jazz",
    "live_rock", "museum_late", "theater",
})
_PARTICIPATORY_EVENTS = frozenset({
    "cooking_class", "dance_night", "karaoke", "open_mic", "pub_quiz", "supper_club",
    "wine_tasting",
})


def check_contradictions(card: PersonaCard) -> Verdict:
    """Value-level coherence. No cluster, no typicality, no `anti_correlated` argument."""
    values = card.values
    reasons: list[str] = []

    def as_set(field_id: str) -> set[str]:
        return set(values.get(field_id) or ())

    for liked, avoided in (("cuisines_liked", "cuisines_avoided"),
                           ("event_types_liked", "event_types_avoided")):
        overlap = as_set(liked) & as_set(avoided)
        if overlap:
            reasons.append(
                f"liked_and_avoided_overlap: {liked} and {avoided} share "
                f"{sorted(overlap)}"
            )

    # The three cuisine-collision rules read one table (`forbidden_ingredients`) so that
    # detection here and prevention in `generator._respect_constraints` cannot disagree.
    # Each keeps its own rule id, because the id names which gating field was violated and
    # that is what a reader of the verdict needs.
    diets = as_set("dietary")
    liked_cuisines = as_set("cuisines_liked")
    for diet in sorted(diets):
        conflicting = conflicting_cuisines(liked_cuisines, DIET_FORBIDS.get(diet, ()))
        if conflicting:
            reasons.append(
                f"diet_conflicts_cuisine: {diet} alongside liked {sorted(conflicting)}"
            )
    for left, right in _DIET_CONFLICTS:
        if {left, right} <= diets:
            reasons.append(f"diet_pair_conflict: {left} and {right}")

    for allergen in sorted(as_set("allergies")):
        conflicting = conflicting_cuisines(
            liked_cuisines, ALLERGEN_FORBIDS.get(allergen, ())
        )
        if conflicting:
            reasons.append(
                f"allergy_conflicts_cuisine: {allergen} allergy alongside liked "
                f"{sorted(conflicting)}"
            )

    # An avoided cuisine that names an ingredient class rather than a kitchen. This is the
    # `gen-002` case: `cuisines_avoided` holds `seafood` and `cuisines_liked` holds
    # `sushi`, which the exact-overlap rule above cannot see because the two tokens differ.
    for avoided_cuisine in sorted(as_set("cuisines_avoided")):
        conflicting = conflicting_cuisines(
            liked_cuisines, AVOIDING_FORBIDS.get(avoided_cuisine, ())
        )
        if conflicting:
            reasons.append(
                f"avoided_conflicts_cuisine: avoided {avoided_cuisine} alongside liked "
                f"{sorted(conflicting)}"
            )

    if values.get("alcohol_free_required"):
        conflicting = as_set("event_types_liked") & _ALCOHOL_EVENTS
        if conflicting:
            reasons.append(
                f"alcohol_free_conflicts_events: alcohol-free alongside liked "
                f"{sorted(conflicting)}"
            )

    if values.get("noise_preference") == "needs_quiet":
        conflicting = as_set("event_types_liked") & _LOUD_EVENTS
        if conflicting:
            reasons.append(
                f"quiet_conflicts_loud_events: needs it quiet enough to talk alongside "
                f"liked {sorted(conflicting)}"
            )

    liked_events = as_set("event_types_liked")
    disposition = values.get("participation_vs_spectating")
    if liked_events and disposition == "seeks_hands_on" and \
            liked_events <= _SPECTATOR_EVENTS:
        reasons.append(
            f"disposition_conflicts_events: seeks hands-on formats but every event "
            f"sought is spectator-only {sorted(liked_events)}"
        )
    if liked_events and disposition == "watches_only" and \
            liked_events <= _PARTICIPATORY_EVENTS:
        reasons.append(
            f"disposition_conflicts_events: watches only but every event sought is "
            f"participatory {sorted(liked_events)}"
        )

    if (values.get("planning_style") == "fully_spontaneous"
            and values.get("deal_seeking") == "plans_around_specials"):
        reasons.append(
            "spontaneity_conflicts_deal_planning: fully spontaneous alongside planning "
            "around happy hours and specials"
        )

    if values.get("solo_dining_comfort") == "wont_eat_alone":
        solo_mass = float((values.get("party_size_distribution") or {}).get("1", 0.0))
        if solo_mass > _SOLO_MASS_CONTRADICTION:
            reasons.append(
                f"solo_comfort_conflicts_party_sizes: won't eat alone with "
                f"{solo_mass:.2f} of occasions solo"
            )

    if "quiet_room_needed" in as_set("accessibility_needs"):
        if values.get("noise_preference") in {"fine_with_lively", "wants_loud"}:
            reasons.append(
                "access_conflicts_noise: quiet_room_needed alongside "
                f"{values.get('noise_preference')}"
            )

    return Verdict(not reasons, tuple(reasons))


# --- 2. cluster position (§4.3) ---------------------------------------------


def position(field_id: str, value: Any) -> float:
    """Normalize a field's value to [0, 1] along its cluster's axis.

    Ordinals take their level index; a set takes its breadth against `BREADTH_REFERENCE`;
    a distribution takes its mass-weighted mean bin. **A distribution's mean flattens
    bimodality** — a card spending half at `$` and half at `$$$$` measures mid-spend, the
    same as one that always spends `$$`. That is a real limitation of this scalar and the
    reason bimodality is handled as a texture property (§4.5) rather than a position.
    """
    spec = fields.BY_ID[field_id]
    if spec.metric == fields.METRIC_ORDINAL:
        raw = fields.ordinal_index(field_id, value) / (len(spec.levels) - 1)
    elif spec.metric == fields.METRIC_JACCARD:
        raw = min(len(value or ()) / BREADTH_REFERENCE, 1.0)
    elif spec.metric == fields.METRIC_JSD:
        top = len(spec.levels) - 1
        raw = sum(float(mass) * i for i, b in enumerate(spec.levels)
                  if (mass := (value or {}).get(b, 0.0))) / top
    elif spec.metric == fields.METRIC_EXACT:
        raw = 1.0 if value else 0.0
    else:  # pragma: no cover - registry is closed
        raise ValueError(f"unhandled metric {spec.metric!r}")
    return raw if ORIENTATION.get(field_id, +1) > 0 else 1.0 - raw


def cluster_positions(card: PersonaCard, cluster_id: str) -> dict[str, float]:
    """Every field of one §3 cluster, oriented so higher means further along its axis."""
    return {
        field_id: position(field_id, card.get(field_id))
        for field_id in fields.CLUSTERS[cluster_id]
    }


def cluster_spread(card: PersonaCard, cluster_id: str) -> float:
    """How far apart the cluster's fields sit. 0.0 is perfectly consistent."""
    values = cluster_positions(card, cluster_id).values()
    return max(values) - min(values)


def violated_clusters(card: PersonaCard) -> tuple[str, ...]:
    """Clusters this card's *values* violate, measured rather than declared.

    Compare against the card's `anti_correlated` tag: a tagged cluster that does not
    appear here is a mislabelled subject, and an untagged cluster that does is an
    accidental violation the §4.3 accounting would miss.
    """
    return tuple(
        cluster_id for cluster_id in fields.CLUSTERS
        if cluster_spread(card, cluster_id) >= ANTI_CORRELATION_SPREAD
    )


# --- 3. texture (§4.5) ------------------------------------------------------

#: Minimum ordinals sitting at an extreme level (index 0 or 3) out of the 11 scored
#: ordinals. A card below this is the "uniformly mid-scale" card §4.5 rejects: it gives
#: the transcript author nothing to perform and it hides ordinal-distance bugs behind
#: small errors (the lesson `toy.py` already carries).
MIN_EXTREME_ORDINALS = 3

#: A distribution is near-uniform when its largest and smallest bins differ by less than
#: this. Near-uniform distributions are unperformable: no session can show them.
MIN_DISTRIBUTION_RANGE = 0.2

#: Minimum members on the one set field every subject must express.
MIN_CUISINES_LIKED = 2

#: Hard-case tag exempting a card from `MIN_EXTREME_ORDINALS`.
#:
#: §4.2's fourth hard case wants "strong idiosyncrasies with mid-scale ordinals
#: elsewhere", which is exactly the shape §4.5 rejects. The two rules are reconciled
#: rather than one overriding the other: texture may be carried by an authored
#: idiosyncrasy *instead of* by extreme ordinals, but only for a card tagged to have one,
#: and the card still has to satisfy the distribution and set rules. Until §15 step 3
#: authors the idiosyncrasy text the exemption is a **promise**, and
#: `GenerationRun.texture_debt` lists every card owed one.
MID_SCALE_HARD_CASE = "strong_idiosyncrasy_mid_ordinals"


def check_texture(card: PersonaCard, hard_case_tags: tuple[str, ...] = ()) -> Verdict:
    """Reject cards with nothing for the simulator to express (§4.5)."""
    reasons: list[str] = []
    values = card.values

    ordinals = [f for f in fields.PREFERENCE_FIELDS if f.metric == fields.METRIC_ORDINAL]
    extremes = sum(
        1 for spec in ordinals
        if fields.ordinal_index(spec.field_id, values[spec.field_id])
        in (0, len(spec.levels) - 1)
    )
    exempt = MID_SCALE_HARD_CASE in hard_case_tags
    if extremes < MIN_EXTREME_ORDINALS and not exempt:
        reasons.append(
            f"uniformly_mid_scale: {extremes} of {len(ordinals)} ordinals at an extreme, "
            f"need {MIN_EXTREME_ORDINALS}"
        )

    for spec in fields.PREFERENCE_FIELDS:
        if spec.metric != fields.METRIC_JSD:
            continue
        masses = [float(m) for m in values[spec.field_id].values()]
        if max(masses) - min(masses) < MIN_DISTRIBUTION_RANGE:
            reasons.append(
                f"near_uniform_distribution: {spec.field_id} spans "
                f"{max(masses) - min(masses):.2f}"
            )

    if len(values.get("cuisines_liked") or ()) < MIN_CUISINES_LIKED:
        reasons.append(
            f"thin_cuisines_liked: {len(values.get('cuisines_liked') or ())} members, "
            f"need {MIN_CUISINES_LIKED}"
        )

    return Verdict(not reasons, tuple(reasons))


# --- the shipped auditor ----------------------------------------------------


class RuleAuditor:
    """The §4.4 auditor the generator ships with: contradictions plus texture.

    Deterministic, stdlib, and structurally incapable of rejecting a card for being
    atypical — see the module docstring. It accepts `anti_correlated` to satisfy the
    `Auditor` protocol and ignores it, which is the property being relied on.

    What it does **not** do is §4.4's performability judgment. That is prose work, it is
    the half a model is needed for, and the model's false-reject rate on §4.3 subjects is
    measured in `probe_coherence/` before any model is wired into a regeneration loop.
    """

    name = "rule-auditor"

    def __call__(
        self, card: PersonaCard, *, anti_correlated: tuple[str, ...] = (),
        hard_case_tags: tuple[str, ...] = (),
    ) -> Verdict:
        contradictions = check_contradictions(card)
        texture = check_texture(card, hard_case_tags)
        return Verdict(
            contradictions.ok and texture.ok,
            contradictions.reasons + texture.reasons,
        )
