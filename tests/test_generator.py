"""Card generator: stratification, quotas, tags, accounting (spec-v0 §4, step 2)."""

from __future__ import annotations

from collections import Counter

import pytest

from persona_twin import coherence, fields, generator, metrics
from persona_twin.coherence import Verdict

N = 36


@pytest.fixture(scope="module")
def run():
    return generator.generate(n=N, seed=7)


# --- the run as a whole -----------------------------------------------------

def test_the_shipped_run_satisfies_every_set_level_invariant(run):
    assert generator.verify(run) == []


def test_every_card_is_schema_valid_and_complete(run):
    for subject in run.subjects:
        subject.card.validate(allow_unknown=False)


def test_generation_is_deterministic_in_seed(run):
    again = generator.generate(n=N, seed=7)
    assert [s.card.values for s in again.subjects] == [s.card.values for s in run.subjects]
    other = generator.generate(n=N, seed=8)
    assert [s.card.values for s in other.subjects] != [s.card.values for s in run.subjects]


def test_no_two_subjects_share_a_card(run):
    fingerprints = {
        tuple(sorted((k, str(v)) for k, v in s.card.values.items())) for s in run.subjects
    }
    assert len(fingerprints) == len(run.subjects)


# --- §4.1 stratification ----------------------------------------------------

def test_every_stratum_is_covered_and_balanced(run):
    counts = Counter(s.stratum_id for s in run.subjects)
    assert set(counts) == {s.id for s in generator.STRATA}
    assert max(counts.values()) - min(counts.values()) <= 1


def test_secondary_axis_marginals_are_balanced(run):
    """Sociality and deliberateness are rotated rather than crossed (§4.1 note). Rotation
    only buys coverage if the marginals come out even *and* all four combinations appear —
    locking them in step would silently sample two of the four."""
    for axis in ("sociality", "deliberateness"):
        counts = Counter(getattr(s, axis) for s in run.subjects)
        assert max(counts.values()) - min(counts.values()) <= 1, (axis, counts)
    combinations = {(s.sociality, s.deliberateness) for s in run.subjects}
    assert len(combinations) == 4


def test_constraint_load_matches_the_stratum(run):
    for subject in run.subjects:
        load = subject.stratum_id.split("con:")[1]
        carried = [
            field_id for field_id in fields.CONSTRAINT_IDS
            if subject.card.get(field_id) not in ([], False)
        ]
        if load == "none":
            assert carried == [], subject.subject_id
        elif load == "single":
            assert len(carried) == 1, subject.subject_id
        else:
            assert len(carried) >= 2, subject.subject_id


# --- §4.2 hard cases --------------------------------------------------------

def test_hard_case_quota_is_met_and_every_kind_realized(run):
    assert len(run.hard_case_subjects) >= -(-N * 25 // 100)
    realized = {tag for s in run.subjects for tag in s.hard_case_tags}
    assert realized == set(generator.HARD_CASE_KINDS)


def test_timeline_hard_cases_carry_a_directive_for_the_steps_that_realize_them(run):
    """Step 2 cannot build a stated-vs-revealed tension or a once-mentioned constraint —
    both are properties of the timeline (§5, §6). The directive is the seam, so it has to
    exist and has to name a field the card actually has."""
    for subject in run.subjects:
        if "stated_vs_revealed" in subject.hard_case_tags:
            named = [d for d in subject.directives if d.startswith("stated_vs_revealed:")]
            assert len(named) == 1
            assert named[0].split(":")[1] in fields.PREFERENCE_IDS
        if "single_mention" in subject.hard_case_tags:
            named = [d for d in subject.directives if d.startswith("single_mention:")]
            assert len(named) == 1
            field_id = named[0].split(":")[1]
            assert field_id in fields.CONSTRAINT_IDS
            assert subject.card.get(field_id) not in ([], False), subject.subject_id


def test_bimodal_hard_cases_actually_carry_a_bimodal_distribution(run):
    for subject in run.subjects:
        if "bimodal_distribution" not in subject.hard_case_tags:
            continue
        masses = sorted(subject.card.get("price_band_distribution").values(), reverse=True)
        assert masses[0] >= 0.3 and masses[1] >= 0.3, subject.subject_id
        assert masses[2] <= 0.15, subject.subject_id


def test_the_mid_scale_exemption_is_load_bearing_and_tracked(run):
    """Every card claiming §4.2's mid-scale case must actually need the §4.5 exemption —
    otherwise the tag is decoration — and must appear in the texture debt until step 3
    authors the idiosyncrasy the exemption was granted against."""
    exempt = [
        s for s in run.subjects
        if coherence.MID_SCALE_HARD_CASE in s.hard_case_tags
    ]
    assert exempt
    for subject in exempt:
        assert not coherence.check_texture(subject.card).ok
        assert coherence.check_texture(subject.card, subject.hard_case_tags).ok
        assert subject.subject_id in run.texture_debt


# --- §4.3 anti-correlation --------------------------------------------------

def test_anti_correlation_quota_is_met_and_spread_across_clusters(run):
    # Measured against the eligible population rather than N: mid-scale hard cases are
    # skipped by §4.3, so N was never the population the assigner could draw from.
    eligible = len([
        s for s in run.subjects
        if coherence.MID_SCALE_HARD_CASE not in s.hard_case_tags
    ])
    assert len(run.anti_correlated_subjects) == \
        generator.anti_correlation_target(eligible)
    clusters = {
        c for s in run.anti_correlated_subjects for c in s.deliberate_anti_correlated
    }
    assert len(clusters) >= 3, clusters


def test_every_planned_violation_is_measurable(run):
    """The quota is only a control if the violation is large enough to measure. A
    subject tagged for a cluster whose values sit inside the consistent range is a
    nominal quota member and defends nothing."""
    for subject in run.anti_correlated_subjects:
        for cluster in subject.deliberate_anti_correlated:
            assert coherence.cluster_spread(subject.card, cluster) >= \
                coherence.ANTI_CORRELATION_SPREAD, subject.subject_id


def test_tags_record_measured_violations_including_incidental_ones(run):
    for subject in run.subjects:
        assert set(subject.anti_correlated) == \
            set(coherence.violated_clusters(subject.card))
        assert set(subject.deliberate_anti_correlated) <= set(subject.anti_correlated)


def test_incidental_violations_stay_a_minority_of_the_quota(run):
    """Incidental violations are tagged and therefore safe, but they are not planned. If
    they ever outnumber the quota the grid is producing §4.3 subjects by accident and the
    ~15% the fabrication rule was calibrated against is no longer the realized rate."""
    assert len(run.incidental_violations) < len(run.anti_correlated_subjects)


def test_anti_correlated_cards_are_never_rejected_by_the_shipped_auditor(run):
    """§4.4's silent-deletion failure, checked on the real quota rather than on fixtures."""
    auditor = coherence.RuleAuditor()
    for subject in run.anti_correlated_subjects:
        verdict = auditor(
            subject.card, anti_correlated=subject.anti_correlated,
            hard_case_tags=subject.hard_case_tags,
        )
        assert verdict.ok, (subject.subject_id, verdict.reasons)


# --- §4.5 texture -----------------------------------------------------------

def test_no_accepted_card_is_texture_free(run):
    for subject in run.subjects:
        assert coherence.check_texture(subject.card, subject.hard_case_tags).ok


def test_the_population_mode_card_is_not_an_oracle(run):
    """`toy.py`'s lesson applied to generated data: if the grid concentrates each field on
    one modal value, the population-mode baseline stops being an honest bar and §11's
    "population-mode earns real agreement and is still beaten" check goes vacuous."""
    ordinals = [
        f.field_id for f in fields.PREFERENCE_FIELDS if f.metric == fields.METRIC_ORDINAL
    ]
    mode = {}
    for field_id in ordinals:
        counts = Counter(s.card.get(field_id) for s in run.subjects)
        value, count = counts.most_common(1)[0]
        assert count / len(run.subjects) <= 0.5, (field_id, counts)
        mode[field_id] = value
    best = max(
        sum(metrics.agreement(f, mode[f], s.card.get(f)) for f in ordinals) / len(ordinals)
        for s in run.subjects
    )
    assert best < 0.95


# --- §4.6 regeneration accounting -------------------------------------------

class _RejectFirstAttempt:
    """Rejects each subject's first draw, accepts the second."""

    name = "reject-first"

    def __init__(self) -> None:
        self.seen: Counter[str] = Counter()

    def __call__(self, card, *, anti_correlated=(), hard_case_tags=()):
        self.seen[card.subject_id] += 1
        if self.seen[card.subject_id] == 1:
            return Verdict.failed("stub_rule: rejected on purpose")
        return Verdict.passed()


class _RejectEverything:
    name = "reject-all"

    def __call__(self, card, *, anti_correlated=(), hard_case_tags=()):
        return Verdict.failed("stub_rule: rejected on purpose")


def test_a_rejected_draw_is_redrawn_rather_than_repeated():
    """§4.6 caps attempts, which is only meaningful if an attempt is a fresh draw. Same
    plan, different values — otherwise the loop burns the budget re-offering one card."""
    auditor = _RejectFirstAttempt()
    run = generator.generate(n=6, seed=3, auditor=auditor)
    assert len(run.subjects) == 6
    assert all(s.attempts == 2 for s in run.subjects)
    first_draws = generator.generate(n=6, seed=3)
    for accepted, rejected in zip(run.subjects, first_draws.subjects):
        assert accepted.card.values != rejected.card.values


def test_rejections_are_logged_per_stratum_with_reasons():
    run = generator.generate(n=6, seed=3, auditor=_RejectFirstAttempt())
    touched = [r for r in run.records.values() if r.attempts]
    assert touched
    for record in touched:
        assert record.attempts == 2 * record.accepted
        assert record.rejections == record.accepted
        assert record.reasons == {"stub_rule": record.rejections}
        assert record.regeneration_rate == pytest.approx(0.5)


def test_exhausting_the_cap_quarantines_rather_than_drops():
    """§4.6: cards over the cap are published as a quarantine list. A silent drop would
    empty a stratum without saying so, which is the drift §4.6 exists to catch."""
    run = generator.generate(n=6, seed=3, auditor=_RejectEverything())
    assert run.subjects == []
    assert len(run.quarantine) == 6
    assert all(reasons for _, reasons in run.quarantine)
    assert sum(r.quarantined for r in run.records.values()) == 6
    assert sum(r.attempts for r in run.records.values()) == 6 * generator.MAX_ATTEMPTS
    assert generator.verify(run)  # a run that produced nothing must not verify clean


def test_outlier_strata_are_named_when_regeneration_concentrates():
    """§4.6's 2×-median rule. With the shipped auditor nothing rejects, so the rule is
    exercised here rather than by the run — see the module docstring on why it is built
    before it earns its keep."""
    class _RejectOneStratum:
        name = "reject-one-stratum"

        def __init__(self) -> None:
            self.target = generator.STRATA[0].id
            self.seen: Counter[str] = Counter()

        def __call__(self, card, *, anti_correlated=(), hard_case_tags=()):
            plans = {p.subject_id: p for p in generator.plan_subjects(18)}
            if plans[card.subject_id].stratum.id != self.target:
                return Verdict.passed()
            self.seen[card.subject_id] += 1
            if self.seen[card.subject_id] == 1:
                return Verdict.failed("stub_rule: rejected on purpose")
            return Verdict.passed()

    run = generator.generate(n=18, seed=3, auditor=_RejectOneStratum())
    assert run.outlier_strata() == [generator.STRATA[0].id]


# --- planning ---------------------------------------------------------------

def test_the_plan_is_independent_of_the_length_it_is_asked_for():
    """The invariant ADR-0043 exists for: what a subject is *for* must be decided by its
    own index and the ones before it, never by how many come after.

    Before the fix `plan_subjects(50)[:5]` disagreed with `plan_subjects(5)` on three of
    five subjects — `gen-002`, `gen-003` and `gen-004` each gained a hard case at 50 —
    so appending personas rewrote the cards of subjects already authored against, and
    the 67-session pilot did not carry over.
    """
    full = generator.plan_subjects(50)
    for m in range(1, 51):
        assert generator.plan_subjects(m) == full[:m], m


def test_the_pilots_own_plan_survives_the_stride():
    """The five pilot subjects are authored text on disk (`runs/authored-pilot/`), so
    their plans are a fixture, not an output. A stride anchored at index 0 would have
    moved `gen-001` and `gen-004`; this pins the anchor that does not."""
    plans = {p.subject_id: p for p in generator.plan_subjects(50)}
    assert plans["gen-000"].hard_case_tags == ("stated_vs_revealed",)
    assert plans["gen-000"].directives == ("stated_vs_revealed:adventurousness",)
    assert plans["gen-000"].anti_correlated == ("C1_exploration",)
    assert plans["gen-001"].hard_case_tags == ("single_mention",)
    assert plans["gen-001"].directives == ("single_mention:allergies",)
    for subject_id in ("gen-002", "gen-003", "gen-004"):
        assert plans[subject_id].hard_case_tags == (), subject_id
        assert plans[subject_id].anti_correlated == (), subject_id


def test_the_quotas_hold_at_every_prefix_not_only_at_n():
    """§4.2's floor is a floor at every length, which is what lets a corpus be extended:
    a run stopped or resumed at any `m` still satisfies the spec."""
    full = generator.plan_subjects(60)
    for m in range(1, 61):
        prefix = full[:m]
        hard = len([p for p in prefix if p.hard_case_tags])
        assert hard >= -(-m * 25 // 100), (m, hard)
        eligible = len([
            p for p in prefix
            if coherence.MID_SCALE_HARD_CASE not in p.hard_case_tags
        ])
        anti = len([p for p in prefix if p.anti_correlated])
        assert anti == generator.anti_correlation_target(eligible), (m, anti)


def test_a_scarce_hard_case_kind_is_not_skipped_past(run):
    """`bimodal_distribution` fits one spend stratum in twelve. A plain rotation would
    offer it to whichever subject held the slot and burn its turn when that subject was
    ineligible; least-assigned holds it at zero until an eligible subject appears."""
    realized = {tag for s in run.subjects for tag in s.hard_case_tags}
    assert "bimodal_distribution" in realized
    bimodal = [
        p for p in generator.plan_subjects(50)
        if "bimodal_distribution" in p.hard_case_tags
    ]
    assert bimodal
    for plan in bimodal:
        assert plan.stratum.spend == "bimodal", plan.subject_id


def test_the_plan_is_independent_of_the_draw():
    """A regeneration must not change what a subject is for: if quota membership were
    redrawn with the values, an unlucky redraw could retire a §4.3 subject silently —
    §4.4's failure in a different costume."""
    assert generator.plan_subjects(N) == generator.plan_subjects(N)
    for plan, subject in zip(generator.plan_subjects(N),
                             generator.generate(n=N, seed=99).subjects):
        assert plan.subject_id == subject.subject_id
        assert plan.hard_case_tags == subject.hard_case_tags
        assert plan.anti_correlated == subject.deliberate_anti_correlated


def test_the_draw_avoids_the_cross_field_tensions_rather_than_regenerating(run):
    """Both repairs (`_resolve_cross_field_tensions`) happen at the draw, so no card
    reaches the auditor carrying them and §4.6's rate stays a signal about the stratum.
    The first run before the repair had 17 of 60 cards wanting a quiet room while liking
    karaoke."""
    for subject in run.subjects:
        values = subject.card.values
        if values["noise_preference"] == "needs_quiet":
            assert not set(values["event_types_liked"]) & coherence._LOUD_EVENTS
        assert len(values["event_types_liked"]) >= 2
        assert not (values["planning_style"] == "fully_spontaneous"
                    and values["deal_seeking"] == "plans_around_specials")
    assert all(r.rejections == 0 for r in run.records.values())


# --- §8.5 idiosyncrasies, authored in step 3 --------------------------------

def test_attaching_idiosyncrasies_clears_the_texture_debt_without_moving_a_card(run):
    """Step 3 authors the text `generator.py` cannot draw. It is attached after the audit,
    so it can never change which cards were accepted — only whether the §4.2 mid-scale
    exemption is still owed."""
    text = "Photographs every menu before ordering and never looks at the photo again."
    authored = {s.subject_id: text for s in run.subjects}
    with_text = generator.generate(n=N, seed=7, idiosyncrasies=authored)

    assert [s.card.values for s in with_text.subjects] == \
        [s.card.values for s in run.subjects]
    assert run.texture_debt          # the debt exists before step 3 runs
    assert with_text.texture_debt == []
    assert generator.verify(with_text) == []


def test_a_token_idiosyncrasy_does_not_discharge_the_debt(run):
    """The floor is structural, not a quality bar — but without it "quirky" would clear a
    debt taken on against a *strong* idiosyncrasy (§4.2)."""
    authored = {s.subject_id: "quirky" for s in run.subjects}
    problems = generator.verify(generator.generate(n=N, seed=7, idiosyncrasies=authored))
    assert any("char floor" in p for p in problems)


# --- the draw cannot emit an ingredient collision ----------------------------

def test_no_drawn_card_seeks_out_a_cuisine_its_constraints_forbid(run):
    """The falsifier for the constrained draw.

    Before 2026-08-21 the ban list lived in `_respect_constraints` as a literal and had
    drifted from the one `coherence` checked against: it knew nothing about dairy, about
    `sushi` being fish, or about `american` being meat, so the draw emitted cards that
    were unwritable and the checker was too incomplete to catch them. Both sides now read
    `coherence.forbidden_ingredients`, and this asserts the consequence rather than the
    mechanism — no card, whatever it was drawn from, seeks out what it will not eat.
    """
    offenders = []
    for card in (s.card for s in run.subjects):
        for reason in coherence.check_contradictions(card).reasons:
            if "cuisine" in reason:
                offenders.append((card.subject_id, reason))
    assert offenders == [], offenders


def test_a_constrained_card_keeps_the_breadth_it_drew(run):
    """`cuisines_liked` breadth is the C1 cluster position scalar.

    Backfilling to two on a constrained card — which the pre-fix `_respect_constraints`
    did — would make the measured C1 position a function of whether the subject happens to
    be vegan, silently narrowing the axis §4.3 exists to measure.
    """
    constrained = [
        s.card for s in run.subjects
        if coherence.forbidden_ingredients(s.card.values)
    ]
    assert constrained, "fixture carries no constrained card; the test proves nothing"
    for card in constrained:
        assert len(card.values["cuisines_liked"]) >= 2


def test_an_avoided_cuisine_never_rules_out_a_liked_one(run):
    """`seafood` avoided beside `sushi` liked — the case a name check cannot see."""
    for card in (s.card for s in run.subjects):
        entailed = set().union(*(
            coherence.CUISINE_INGREDIENTS.get(c, frozenset())
            for c in card.values["cuisines_liked"]
        )) if card.values["cuisines_liked"] else set()
        for avoided in card.values["cuisines_avoided"]:
            assert not (coherence.AVOIDING_FORBIDS.get(avoided, frozenset()) & entailed), (
                card.subject_id, avoided, sorted(entailed)
            )


def test_a_constrained_card_is_refilled_from_its_own_pool(run):
    """An adventurous vegan must stay as adventurous as an adventurous omnivore.

    `cuisines_liked` breadth *and* composition both feed the C1 position scalar, so
    refilling a constrained `_ADVENTUROUS` card out of `_FAMILIAR` would make the measured
    exploration axis a function of the subject's diet.
    """
    familiar = set(generator._FAMILIAR)
    adventurous = [
        s for s in run.subjects
        if coherence.forbidden_ingredients(s.card.values)
        and not set(s.card.values["cuisines_liked"]) <= familiar
    ]
    assert adventurous, "fixture carries no constrained adventurous card"
    for subject in adventurous:
        liked = set(subject.card.values["cuisines_liked"])
        assert not liked & familiar - set(generator._ADVENTUROUS), (
            subject.subject_id, sorted(liked)
        )
