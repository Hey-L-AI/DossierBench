"""The v0.1 scheduler, and above all the invariant that makes it extensible."""

from __future__ import annotations

import pytest

from persona_twin import dataset, fields, generator, schedule, transcript
from persona_twin.evidence import Grade


def _subjects(n: int = 6, seed: int = 3):
    return generator.generate(n=n, seed=seed).subjects


def _build(subject, count: int, seed: int = 3):
    return schedule.build_schedule(
        subject_id=subject.subject_id, card=subject.card, session_count=count,
        directives=subject.directives, anti_correlated=subject.anti_correlated,
        hard_case_tags=subject.hard_case_tags, seed=seed,
    )


# --- the outing horizon (ADR-0040) -------------------------------------------


def test_alignment_moves_a_session_onto_the_day_its_bin_needs() -> None:
    from datetime import date

    tuesday = date(2025, 8, 12)
    assert tuesday.weekday() == 1
    assert schedule._alignment_days(tuesday, "weekend-evening") == 4
    assert schedule._alignment_days(tuesday, "weekday-lunch") == 0
    # `late-night` sits on either side of the week and constrains nothing.
    assert schedule._alignment_days(tuesday, "late-night") == 0
    assert schedule._alignment_days(tuesday, None) == 0

    sunday = date(2025, 8, 17)
    assert sunday.weekday() == 6
    assert schedule._alignment_days(sunday, "weekend-day") == 0
    # Never negative: a session may only move later, or the dates stop increasing.
    assert schedule._alignment_days(sunday, "weekday-evening") == 1


def test_a_same_day_subject_never_lands_on_a_day_its_bin_cannot_happen_on() -> None:
    """`gen-004-s9`: card `fully_spontaneous`, session on a Friday, bin `weekend-day`. The page
    could only satisfy both by booking four days ahead, which the card forbids."""
    from datetime import date as date_type

    checked = 0
    for subject in _subjects(n=12, seed=3):
        if str(subject.card.get("planning_style")) not in schedule.SAME_DAY_STYLES:
            continue
        sched = _build(subject, 20)
        drawn = {
            i.session: i.bin for i in sched.incidentals
            if i.field_id == "visit_time_distribution"
        }
        for index, stamp in enumerate(sched.dates):
            bin_id = drawn.get(index)
            weekend = date_type.fromisoformat(stamp).weekday() >= 5
            if bin_id in schedule.WEEKEND_BINS:
                checked += 1
                assert weekend, f"{subject.subject_id}-s{index}: {bin_id} on a weekday"
            elif bin_id in schedule.WEEKDAY_BINS:
                checked += 1
                assert not weekend, f"{subject.subject_id}-s{index}: {bin_id} at a weekend"
    assert checked, "no same-day subject drew a day-class bin; the test proved nothing"


def test_alignment_leaves_the_ahead_planners_dates_alone() -> None:
    """Only the same-day styles need it. A subject who books weeks ahead reaches any bin from
    any weekday, so their calendar is the raw gap draw and every gap stays in the tuple."""
    from datetime import date as date_type

    checked = 0
    for subject in _subjects(n=12, seed=3):
        if str(subject.card.get("planning_style")) in schedule.SAME_DAY_STYLES:
            continue
        dates = [date_type.fromisoformat(d) for d in _build(subject, 20).dates]
        for before, after in zip(dates, dates[1:]):
            checked += 1
            assert (after - before).days in schedule.SESSION_GAP_DAYS
    assert checked


# --- the invariant -----------------------------------------------------------


@pytest.mark.parametrize("short,long", [(3, 9), (5, 12), (8, 25), (12, 30)])
def test_extension_is_prefix_stable(short: int, long: int) -> None:
    """Lengthening a timeline must leave every earlier session byte-identical.

    This is the guard the whole v0.1 design rests on. If it fails, extending a subject
    invalidates every transcript already authored for it and every grading already paid for,
    and the only remedy is a full re-author — which is exactly the v0 behaviour this scheduler
    replaces.
    """
    for subject in _subjects():
        a, b = _build(subject, short), _build(subject, long)
        assert [beat.to_json() for beat in a.beats] == [
            beat.to_json() for beat in b.beats if beat.session < short
        ], f"{subject.subject_id}: beats moved when the timeline grew"
        assert a.shapes == b.shapes[:short]
        # The *briefed* length, not just the budget `shapes` carries. Since ADR-0055 these
        # differ — the author is given a length derived from the beats that actually landed —
        # and it is the derived number that goes into the brief and therefore into the
        # transcript cache fingerprint. A stable beat prefix under a drifting length would
        # still invalidate every authored session, which is the exact failure this test
        # exists to catch.
        assert [a.turns_in(i) for i in range(short)] == \
            [b.turns_in(i) for i in range(short)], \
            f"{subject.subject_id}: briefed length moved when the timeline grew"
        assert a.dates == b.dates[:short]
        assert [e for e in a.events] == [e for e in b.events if e.checkpoint < short]


def test_extension_preserves_the_provisional_map_prefix() -> None:
    """The gold twin at checkpoint k must not move when sessions are added after k."""
    for subject in _subjects():
        a, b = _build(subject, 6), _build(subject, 20)
        for checkpoint in range(6):
            assert a.provisional_map().grades[checkpoint] == \
                b.provisional_map().grades[checkpoint]


def test_corpus_extension_checks_the_prefix_before_writing(tmp_path) -> None:
    """`extend_corpus` refuses to write a corpus whose prefix changed."""
    import json

    path = tmp_path / "corpus.json"
    data = dataset.build(n=3, seed=3, lengths=[5, 5, 5])
    path.write_text(json.dumps(data.to_json(), indent=2))

    before = json.loads(path.read_text())["items"][0]
    after = dataset.extend_corpus(path, subject_id=before["subject_id"],
                                  session_count=14, seed=3)
    assert after["session_count"] == 14
    assert after["sessions"][:5] == before["sessions"]

    with pytest.raises(ValueError):
        dataset.extend_corpus(path, subject_id=before["subject_id"],
                              session_count=14, seed=3)


def test_adding_subjects_does_not_disturb_existing_lengths() -> None:
    """Length is drawn per subject, so a bigger corpus keeps every existing timeline."""
    five = dataset.assign_lengths([f"gen-{i:03d}" for i in range(5)], seed=3)
    fifty = dataset.assign_lengths([f"gen-{i:03d}" for i in range(50)], seed=3)
    assert all(fifty[sid] == length for sid, length in five.items())


# --- determinism -------------------------------------------------------------


def test_build_is_deterministic() -> None:
    for subject in _subjects(3):
        assert _build(subject, 10).to_json() == _build(subject, 10).to_json()


def test_field_plans_are_independent_streams() -> None:
    """One field's plan must not shift when another field's does.

    v0 lost a day to the shared-stream version of this: sampling a distribution bin off the
    common RNG shifted every later draw and reshuffled beat placement for every field.
    """
    subject = _subjects(3)[0]
    plan = schedule.field_plan("noise_preference", subject.card, subject.subject_id, seed=3)
    again = schedule.field_plan("noise_preference", subject.card, subject.subject_id, seed=3)
    assert plan == again


# --- the reveal model --------------------------------------------------------


def test_a_field_is_never_strong_before_it_is_pinned() -> None:
    """Narrowing beats may reach WEAK; only pinning beats reach STRONG.

    **Distribution fields are the exception, and it is the point of ADR-0037.** Their value is
    a shape over bins, which no single turn may state — `_distribution_instruction` forbids
    listing bins outright — so nothing about them is ever "pinned" in the §5 sense. What makes
    a shape readable is the count of occasions, whether those occasions were scheduled as
    beats or fell out of the conversation having to say who was coming. `evidence_weight`
    prices them the same because a reader cannot tell them apart.
    """
    for subject in _subjects(4):
        sched = _build(subject, 14)
        emap = sched.provisional_map()
        for spec in fields.ALL_FIELDS:
            if spec.metric == fields.METRIC_JSD:
                continue
            pinned_at = [
                b.session for b in sched.beats
                if b.field_id == spec.field_id and b.move in schedule.PINNING
            ]
            for checkpoint in range(sched.checkpoint_count):
                if emap.grade(checkpoint, spec.field_id).name != "STRONG":
                    continue
                assert pinned_at and min(pinned_at) <= checkpoint, (
                    f"{spec.field_id} is STRONG at {checkpoint} with no pinning beat before it"
                )


def test_a_distribution_is_strong_only_after_enough_occasions() -> None:
    """The replacement rule for the exception above: STRONG needs the threshold in occasions.

    The threshold is 5 because 5 is where reconstruction stops improving: drawing k occasions
    from the gold distributions and scoring the empirical shape gives mean 1−JSD of 0.826 at
    k=3, 0.902 at k=5 and 0.946 at k=10, with the share reconstructing above 0.80 going 63% →
    93% between k=3 and k=5.
    """
    for subject in _subjects(4):
        sched = _build(subject, 14)
        emap = sched.provisional_map()
        for spec in fields.ALL_FIELDS:
            if spec.metric != fields.METRIC_JSD:
                continue
            threshold = schedule.pin_threshold(spec.field_id)
            for checkpoint in range(sched.checkpoint_count):
                occasions = sum(
                    1 for b in sched.beats
                    if b.field_id == spec.field_id and b.session <= checkpoint
                    and b.counts_toward_gold
                ) + sum(
                    1 for i in sched.incidentals
                    if i.field_id == spec.field_id and i.session <= checkpoint
                )
                strong = emap.grade(checkpoint, spec.field_id).name == "STRONG"
                assert strong == (occasions >= threshold), (
                    spec.field_id, checkpoint, occasions, threshold, strong
                )


def test_a_long_timeline_reveals_more_than_a_short_one() -> None:
    """The tier gradient v0 got from three lanes now comes from the prefix you read."""
    subject = _subjects(3)[0]
    sched = _build(subject, 26)
    early = sched.profile(2)
    late = sched.profile(25)
    assert early["UNEVIDENCED"] > late["UNEVIDENCED"]
    assert late["STRONG"] > early["STRONG"]


def test_a_weak_band_survives_a_long_timeline() -> None:
    """Some fields stay hinted-but-unpinned however long the timeline runs.

    Without `PIN_DELAY` the map went all-STRONG-or-UNEVIDENCED by session 25, which is the
    wrong shape: abstention is the benchmark's subject matter, not an artifact of shortness.
    """
    weak = [
        _build(subject, 25).profile()["WEAK"] for subject in _subjects(8)
    ]
    assert sum(1 for w in weak if w > 0) >= 6, weak


def test_sessions_never_exceed_their_turn_budget() -> None:
    """Beat density is capped by conversation length, scripted beats included."""
    for subject in _subjects(8):
        sched = _build(subject, 20)
        assert [p for p in sched.check(subject.card) if "capacity" in p] == []


def test_distractor_sessions_carry_no_beats() -> None:
    for subject in _subjects(6):
        sched = _build(subject, 18)
        for session in sched.distractor_sessions:
            assert sched.beats_in(session) == []


def test_dates_increase_and_only_append() -> None:
    for subject in _subjects(4):
        short, long = _build(subject, 5), _build(subject, 15)
        assert list(short.dates) == sorted(short.dates)
        assert short.dates == long.dates[:5]


def test_corrections_are_not_marked_removable() -> None:
    """An easy lane may drop distractors and interference, never a correction.

    Dropping a correction leaves later sessions expressing a value the prefix no longer
    evidences — a constructibility violation, which is the criterion that still blocks.

    Holds vacuously in v0.5 (`MAX_CORRECTIONS == 0`, no corrections are scheduled); kept as a
    guard for whenever v1/v2 revives them.
    """
    for subject in _subjects(8):
        sched = _build(subject, 22)
        corrected = {e.checkpoint for e in sched.events_of("correction")}
        assert corrected.isdisjoint(sched.removable_sessions())


def test_full_reveal_is_reachable_and_priced() -> None:
    """`beats_to_pin_all` is the cost of "eventually everything is revealed".

    Read over **schedulable** fields, which is the only reading that means anything: a person
    with no allergies and no dietary restrictions can never evidence `allergies` or `dietary`,
    and those stay UNEVIDENCED at any length. That is not a gap in the timeline — the correct
    twin value there is the empty set, and whether a scorer credits an SUT for saying so is a
    grading question, not a scheduling one.
    """
    subject = _subjects(3)[0]
    assert schedule.beats_to_pin_all(subject.card) > 0
    sched = _build(subject, 40)
    emap = sched.provisional_map()
    unrevealed = [
        spec.field_id for spec in fields.ALL_FIELDS
        if schedule.schedulable(spec.field_id, subject.card)
        and emap.grade(sched.checkpoint_count - 1, spec.field_id).name == "UNEVIDENCED"
    ]
    assert unrevealed == [], unrevealed


def test_a_signal_session_is_never_starved_into_a_distractor() -> None:
    """Only a session the scheduler *decided* was a distractor may go out empty.

    `distractor_sessions` derives from "carries no beats", so an empty signal session is
    indistinguishable from a scheduled one and gets authored with a distractor scenario — the
    conversation becomes off-topic chatter nobody planned, and the realised distractor rate
    stops matching `DISTRACTOR_RATE`. The first pilot corpus had three, one of them a
    subject's opening conversation.

    The assertion used to be "was drawn by `session_shape`", which ADR-0038 outgrew: a
    distractor is now drawn *or* forced at `DISTRACTOR_DEADLINE`, and a forced one is empty
    without having been rolled. The property being protected was never really about the roll —
    it is that emptiness is always a decision the loop recorded, never a residue of the queue
    running dry. Comparing against the emitted events says exactly that, and says it more
    tightly than the old form did: a starved session lands in `distractor_sessions` and emits
    no event, so the two sets come apart precisely where the defect is.
    """
    for subject in _subjects(10):
        sched = _build(subject, 20)
        decided = {e.checkpoint for e in sched.events_of("distractor")}
        assert set(sched.distractor_sessions) == decided, (
            f"{subject.subject_id}: sessions "
            f"{sorted(set(sched.distractor_sessions) - decided)} are empty but the scheduler "
            "never decided they were distractors"
        )


def test_a_distractor_is_guaranteed_once_a_timeline_reaches_the_deadline() -> None:
    """Every subject long enough to reach `DISTRACTOR_DEADLINE` carries a distractor.

    The roll alone gave no per-subject guarantee — 21 of 50 subjects had none at `n=50,
    seed=7`, two of them 25 sessions long, while the *pooled* rate sat correctly at 0.121
    against a declared 0.14. §8.6's card-stability claim is made per subject, so a rate that
    is right on average and absent for 42% of the corpus cannot carry it.

    The guarantee is length-conditional and that is the honest form of the intent: a timeline
    that ends before the deadline still carries none, and forcing one there would mean
    spending a short subject's scarce sessions on a conversation with no signal in it.
    """
    for subject in _subjects(10):
        for length in (schedule.DISTRACTOR_DEADLINE + 1, 12, 20):
            sched = _build(subject, length)
            assert sched.events_of("distractor"), (
                f"{subject.subject_id} at {length} sessions reaches the deadline "
                f"({schedule.DISTRACTOR_DEADLINE}) and still has no distractor"
            )


def test_the_deadline_does_not_move_when_a_timeline_is_extended() -> None:
    """A forced distractor is prefix-stable, like everything else the loop decides.

    The deadline reads the index and what has already been placed, never `session_count`, so
    extending a subject cannot relocate it. This is the specific way a "guarantee" could have
    broken the invariant: a rule phrased as "make sure each subject gets one" would want to
    look at the end of the timeline, and the end is not knowable here.
    """
    for subject in _subjects(6):
        short = _build(subject, 7)
        long = _build(subject, 22)
        assert short.events_of("distractor") == [
            e for e in long.events_of("distractor") if e.checkpoint < 7
        ], f"{subject.subject_id}: extending moved a distractor"


# --- disclosed absence (ADR-0035) --------------------------------------------


def test_an_absent_constraint_gets_exactly_one_disclosure_beat() -> None:
    """A person with no allergies is *asked*, once, and says so.

    Before ADR-0035 an absent hard constraint was unschedulable, so the cell was permanently
    UNEVIDENCED at any length — 16 of 20 constraint cells on the pilot, and two of five
    subjects with nothing at all in §8.2's gating track.
    """
    for subject in _subjects(8):
        plans = {
            spec.field_id: schedule.field_plan(
                spec.field_id, subject.card, subject.subject_id, seed=3
            )
            for spec in fields.ALL_FIELDS if spec.is_constraint
        }
        for field_id, plan in plans.items():
            disclosures = [b for b in plan if b.move == schedule.DISCLOSURE]
            if schedule.carries(field_id, subject.card):
                assert not disclosures, (
                    f"{subject.subject_id}/{field_id}: a constraint the person carries must "
                    f"be evidenced by naming it, not by denying it"
                )
            else:
                assert len(plan) == len(disclosures) == 1, (
                    f"{subject.subject_id}/{field_id}: an absence is disclosed once"
                )


def test_a_disclosed_absence_pins_the_field() -> None:
    """The point of the change: the cell reaches STRONG, so the empty set is the right answer.

    A hard constraint is declared rather than inferred (§5.1), so one mention pins it — and
    "no, nothing like that" declares the empty set as flatly as naming an allergy would.
    """
    seen = 0
    for subject in _subjects(8):
        sched = _build(subject, 30)
        emap = sched.provisional_map()
        for beat in sched.beats:
            if beat.move != schedule.DISCLOSURE:
                continue
            seen += 1
            assert not schedule.carries(beat.field_id, subject.card)
            # A checkpoint is emitted *after* its session, so the disclosing session is
            # already STRONG and the one before it is still UNEVIDENCED.
            assert emap.grade(beat.session, beat.field_id).name == "STRONG", (
                f"{beat.field_id} should pin in the session that discloses it"
            )
            if beat.session:
                assert emap.grade(beat.session - 1, beat.field_id).name == "UNEVIDENCED", (
                    "the absence must be unevidenced before the beat that discloses it"
                )
    assert seen, "no absence was disclosed in any subject — the fixture proves nothing"


def test_a_disclosure_onset_is_not_forced_early() -> None:
    """A short timeline may never reach the beat, and then abstaining is still correct.

    The tempting implementation is "ask in session 1, done". It would evidence every
    constraint almost immediately and lose the long-lived UNEVIDENCED constraint cells that
    test whether a twin invents a dietary requirement nobody mentioned. §8.1 is unchanged:
    unmentioned means committing to the empty set is a fabrication.
    """
    unreached = 0
    for subject in _subjects(12):
        short = _build(subject, 4)
        emap = short.provisional_map()
        for spec in fields.ALL_FIELDS:
            if spec.is_constraint and not schedule.carries(spec.field_id, subject.card):
                if emap.grade(3, spec.field_id).name == "UNEVIDENCED":
                    unreached += 1
    assert unreached, (
        "every absence was disclosed within four sessions — the onset is effectively forced "
        "early, and the unevidenced constraint band has been destroyed"
    )


def test_disclosure_beats_are_prefix_stable() -> None:
    """The new beat draws from the same per-field stream, so extension still cannot move it."""
    for subject in _subjects(8):
        a, b = _build(subject, 6), _build(subject, 22)
        early_a = [x.to_json() for x in a.beats if x.move == schedule.DISCLOSURE]
        early_b = [
            x.to_json() for x in b.beats
            if x.move == schedule.DISCLOSURE and x.session < 6
        ]
        assert early_a == early_b, f"{subject.subject_id}: a disclosure beat moved"


def test_the_disclosure_move_is_never_drawn_by_a_preference_field() -> None:
    """`disclosure` pins, so it sits in `PINNING` — but it is issued, never drawn."""
    assert schedule.DISCLOSURE in schedule.PINNING
    assert schedule.DISCLOSURE not in schedule.PREFERENCE_PINNING
    for subject in _subjects(8):
        sched = _build(subject, 20)
        for beat in sched.beats:
            if beat.move == schedule.DISCLOSURE:
                assert fields.BY_ID[beat.field_id].is_constraint


def test_the_gating_track_is_no_longer_mostly_empty() -> None:
    """The measured reason for ADR-0035, pinned so a regression is visible.

    At `n=50, seed=7`: constraint cells evidenced by the final checkpoint went 39/200 to
    138/200, and subjects with an entirely empty §8.2 gating track went 20/50 to 4/50.
    """
    corpus = dataset.build(n=50, seed=7)
    constraints = [f.field_id for f in fields.ALL_FIELDS if f.is_constraint]
    evidenced = empty = 0
    for item in corpus.items:
        final = item.provisional[max(item.provisional)]
        carried = sum(1 for f in constraints if final[f] != "UNEVIDENCED")
        evidenced += carried
        empty += not carried
    assert evidenced >= 120, f"only {evidenced}/200 constraint cells evidenced"
    assert empty <= 8, f"{empty}/50 subjects have nothing in the gating track"


def test_no_corrections_are_scheduled_in_v0_5() -> None:
    """v0.5 ships without scripted corrections (`MAX_CORRECTIONS == 0`).

    `_correction` was found to override an ordinal the timeline had already pinned toward the
    *final* card value, invent a random `old_value`, and rewind `card_at` to it without ever
    authoring a session that expresses that old value — so every correction was a phantom, and a
    correct reader was charged a misreading before the flip. Until the "before" phase is authored
    for real (a beat-placement change deferred to v1/v2), generation must emit no corrections.

    This is the inverse of the old guarantee test: it fails if a regression re-enables the roll.
    The precondition it once exercised — an eligible STRONG ordinal on a non-distractor session
    past the deadline — is reproduced here to prove the gate holds *where a correction would
    otherwise have landed*, not merely where none could.
    """
    assert schedule.MAX_CORRECTIONS == 0
    would_have_carried = 0
    for subject in _subjects(12):
        sched = _build(subject, 26)
        assert not sched.events_of("correction"), (
            f"{subject.subject_id} scheduled a correction; v0.5 must schedule none"
        )
        tension, single = schedule._parse_directives(subject.directives)
        grades = sched.provisional_map().grades
        distractors = set(sched.distractor_sessions)
        eligible = [
            i for i in range(schedule.CORRECTION_DEADLINE, sched.session_count)
            if i not in distractors
            and any(
                fields.BY_ID[fid].metric == fields.METRIC_ORDINAL
                and fid not in (tension, single)
                and grade == Grade.STRONG
                for fid, grade in grades[i - 1].items()
                if fid in fields.PREFERENCE_IDS
            )
        ]
        if eligible:
            would_have_carried += 1
    assert would_have_carried >= 6, (
        f"only {would_have_carried} subjects had a correctable session; the gate is untested"
    )


def test_a_six_turn_session_still_cannot_recite() -> None:
    """Loosening `TURNS_PER_BEAT` to 3.0 must not buy density at the short end.

    The coupling exists for one reason: four beats in a six-turn conversation is a user
    reciting preferences, which is what the §7.2 leakage audit fails a transcript for. The
    ADR-0038 loosening was for the *long* end — it compensates for the shorter ceiling — and
    the check that it did not leak into the short end is that the floor of the range carries
    the same two beats it carried at 3.5.
    """
    assert schedule.capacity(schedule.TURNS_MIN) == 2
    assert schedule.capacity(schedule.TURNS_MAX) == 4
    assert schedule.TURNS_MAX == 12, "the measured realised ceiling; see ADR-0038"
    # Monotone, and never more than one beat per three turns anywhere in the range.
    caps = [schedule.capacity(t) for t in range(schedule.TURNS_MIN, schedule.TURNS_MAX + 1)]
    assert caps == sorted(caps)
    assert all(c * 3 <= t + 1 for t, c in zip(range(schedule.TURNS_MIN, schedule.TURNS_MAX + 1), caps))


# --- entailed occasions (pending 8a) -----------------------------------------


def _entailment_schedule(occasions, beats=()):
    """A bare schedule carrying just the occasions (and beats) a test needs."""
    return schedule.EvidenceSchedule(
        subject_id="unit", session_count=3, beats=list(beats),
        incidentals=[schedule.Incidental(session=s, field_id=f, bin=b)
                     for s, f, b in occasions],
    )


def test_an_entailing_occasion_credits_the_correlate_at_weak() -> None:
    """A table for one is evidence about `solo_dining_comfort`, and the map now says so.

    The pair is the generator's own: C3_sociality, with a hard coherence rule tying solo
    comfort to party-size mass. Before this, a twin that read the page correctly was scored as
    fabricating — 34 of the reader arm's 83 leaks on the pilot were exactly this.
    """
    sched = _entailment_schedule([(0, "party_size_distribution", "1")])
    emap = sched.provisional_map()
    assert emap.grade(0, "solo_dining_comfort") == Grade.WEAK
    # And it stays WEAK however many occasions accumulate: this is inference from a
    # correlate, and pinning is reserved for the field's own evidence.
    many = _entailment_schedule([(s, "party_size_distribution", "1") for s in range(3)])
    assert many.provisional_map().grade(2, "solo_dining_comfort") == Grade.WEAK


def test_a_non_entailing_bin_credits_nothing() -> None:
    """The control, and the one the pilot ran as a subject.

    `gen-001` moves zero cells because `wont_eat_alone` never draws bin '1'. At unit scale
    that is a party of four saying nothing about eating alone, and `$` saying nothing about
    `deal_seeking` — the table is deliberately one-directional, because the scenery-narrowing
    reading found 0 of 4 `deal_seeking` leaks on 'cheap' scenery against 5 of 5 on 'a blowout'.
    """
    sched = _entailment_schedule([
        (0, "party_size_distribution", "4-6"), (1, "price_band_distribution", "<£15"),
    ])
    emap = sched.provisional_map()
    assert emap.grade(2, "solo_dining_comfort") == Grade.UNEVIDENCED
    assert emap.grade(2, "deal_seeking") == Grade.UNEVIDENCED


def test_entailment_never_lowers_a_grade_the_fields_own_evidence_earned() -> None:
    """Credit applies only where the field is UNEVIDENCED on its own account."""
    beats: list = []
    for subject in _subjects(8):
        beats = [b for b in _build(subject, 8).beats if b.field_id == "deal_seeking"]
        if beats:
            break
    assert beats, "no subject schedules a deal_seeking beat; the test is vacuous"
    with_beat = schedule.EvidenceSchedule(
        subject_id="unit", session_count=max(b.session for b in beats) + 1, beats=beats,
        incidentals=[schedule.Incidental(0, "price_band_distribution", "£50+")],
    )
    last = with_beat.provisional_map().checkpoint_count - 1
    own = schedule.EvidenceSchedule(
        subject_id="unit", session_count=with_beat.session_count, beats=beats,
    ).provisional_map().grade(last, "deal_seeking")
    assert with_beat.provisional_map().grade(last, "deal_seeking") == own


def test_the_lead_time_credits_planning_style_at_weak() -> None:
    """A conversation that says when the outing is has said how far ahead it was decided.

    The brief already draws that gap from the card, so the page was never wrong — only the map
    was unaware, and 20 of the pilot's 42 blocking leaks were this one field against zero
    contradictions on it.
    """
    sched = _entailment_schedule([(0, "visit_time_distribution", "weekend-evening")])
    assert sched.horizon_sessions() == (0,)
    assert sched.provisional_map().grade(0, "planning_style") == Grade.WEAK


def test_the_lead_time_never_pins_however_many_sessions_state_it() -> None:
    """The ceiling is the whole reason this is an entailment and not an occasion.

    A lead time mentioned in passing narrows the field; it does not settle it. Counted as an
    occasion instead, one per session, scenery alone would walk `planning_style` to STRONG.
    """
    many = _entailment_schedule([
        (s, "visit_time_distribution", "weekend-evening") for s in range(3)
    ])
    assert many.horizon_sessions() == (0, 1, 2)
    assert many.provisional_map().grade(2, "planning_style") == Grade.WEAK


def test_the_lead_time_is_not_credited_where_the_brief_does_not_write_it() -> None:
    """Both suppressions, because the map must never claim a sentence the page lacks.

    A session with a `planning_style` beat is writing the field deliberately and the brief
    drops the horizon; a session whose visit time has a beat has no scenery "when" for the
    horizon to hang on. `here` is every beat at any attribution, matching the brief.
    """
    own = _entailment_schedule(
        [(0, "visit_time_distribution", "weekend-evening")],
        beats=[schedule.Beat(session=0, field_id="planning_style", move="reaction",
                             note="", bin=None)],
    )
    assert own.horizon_sessions() == ()
    timed = _entailment_schedule(
        [(0, "visit_time_distribution", "weekend-evening")],
        beats=[schedule.Beat(session=0, field_id="visit_time_distribution", move="reaction",
                             note="", bin="weekend-evening")],
    )
    assert timed.horizon_sessions() == ()


def test_the_lead_time_is_invisible_to_placement() -> None:
    """`_map_of` does not read entailments, and that is what keeps the corpus still.

    Generation reads the map to decide corrections and interference bursts. If the horizon
    reached that map, adding this would have moved beats — and every authored transcript with
    them. It does not: re-deriving the pilot corpus left all 57 briefs byte-identical.
    """
    grades = schedule._map_of(
        [], 1, [schedule.Incidental(0, "visit_time_distribution", "weekend-evening")],
    )
    assert grades["planning_style"] == Grade.UNEVIDENCED


def test_the_horizon_credit_names_the_session_it_was_read_off() -> None:
    sched = _entailment_schedule([(1, "visit_time_distribution", "late-night")])
    assert sched.provisional_map().turn_refs["planning_style"] == ["s1:horizon"]


def test_the_brief_writes_a_horizon_exactly_where_the_map_credits_one() -> None:
    """The drift guard, and it is the one that matters.

    Two copies of the condition would let the map grade a cell WEAK on a sentence the author
    was never asked to write, and nothing downstream would notice — the page would simply be
    missing the evidence the grade rests on. `transcript.continuity_for` calls
    `schedule.horizon_lands` rather than restating it, and this asserts the two agree on real
    schedules rather than on the shared import alone.
    """
    horizons = set(transcript._HORIZON_PHRASING.values())
    checked = 0
    for subject in _subjects(6):
        sched = _build(subject, 6)
        credited = set(sched.horizon_sessions())
        for session in range(sched.checkpoint_count):
            block = transcript.continuity_for(
                card=dataset._card_values(subject.card),
                this_session=[b.field_id for b in sched.beats_in(session)],
                occasions={i.field_id: i.bin
                           for i in sched.incidentals if i.session == session},
            )
            when = str(block["scenery"].get("when", ""))
            written = any(phrase in when for phrase in horizons)
            assert written == (session in credited), (
                f"{subject.subject_id} s{session}: brief writes horizon={written}, "
                f"map credits={session in credited}"
            )
            checked += 1
    assert checked >= 12, "the sweep did not reach enough sessions to mean anything"


def test_every_planning_style_level_has_a_horizon_phrase() -> None:
    """A level with no phrase is a session the map credits and the page leaves blank."""
    assert set(transcript._HORIZON_PHRASING) == set(
        fields.BY_ID["planning_style"].levels
    )


def test_a_credited_cell_names_the_occasion_it_was_read_off() -> None:
    """A grade nobody can trace is the objection ADR-0034 raised against judge-sourced maps."""
    sched = _entailment_schedule([(1, "price_band_distribution", "£30-50")])
    refs = sched.provisional_map().turn_refs
    assert refs["deal_seeking"] == ["s1:entailed:price_band_distribution=£30-50"]


def test_must_unevidenced_never_contradicts_the_map_it_ships_with() -> None:
    """Two records in one file, and they used to disagree on 8 cells.

    `must_unevidenced` was derived from beats alone, so ADR-0037's counted occasions — and
    then entailment credit — left fields listed as never-evidenced that the corpus's own
    `provisional` graded WEAK or STRONG. The scorer reads both.
    """
    built = dataset.build(n=4, seed=3)
    checked = 0
    for item in built.items:
        last = max(item.provisional, key=int)
        for field_id in item.must_unevidenced:
            checked += 1
            assert item.provisional[last][field_id] == Grade.UNEVIDENCED.name, (
                f"{item.subject_id}: {field_id} is must_unevidenced and graded "
                f"{item.provisional[last][field_id]}"
            )
    assert checked >= 10, f"only {checked} cells exercised; test is vacuous"


# --- the outing's formality (2026-08-21) ----------------------------------------------
#
# The third instance of "the scenery only knows how to book a table". `formality_ceiling`
# raised 11 contradictions on the 2026-08-21 pilot, 13 of its 15 blocking findings on the
# three `counter_service` subjects and none on the two `white_tablecloth` ones.


def _card_at(ceiling):
    return {"formality_ceiling": ceiling}


def test_formality_never_exceeds_the_ceiling():
    levels = list(fields.BY_ID["formality_ceiling"].levels)
    for ceiling in levels:
        card = _card_at(ceiling)
        permitted = set(levels[: levels.index(ceiling) + 1])
        drawn = {
            schedule.formality_occasion("gen-000", s, card, seed=7) for s in range(40)
        }
        assert drawn <= permitted, f"{ceiling}: drew {drawn - permitted}"


def test_a_counter_service_ceiling_leaves_exactly_one_option():
    """The subjects that carried every contradiction. There is nothing else they would go to,
    so the brief can state it without a draw's worth of doubt."""
    card = _card_at("counter_service")
    assert {schedule.formality_occasion("gen-001", s, card, seed=7)
            for s in range(30)} == {"counter_service"}


def test_formality_draw_is_prefix_stable():
    """Its own RNG stream, keyed on (seed, subject, session) — rule one of the three."""
    card = _card_at("white_tablecloth")
    first = [schedule.formality_occasion("gen-004", s, card, seed=7) for s in range(8)]
    longer = [schedule.formality_occasion("gen-004", s, card, seed=7) for s in range(16)]
    assert longer[:8] == first


def test_formality_entails_only_at_the_ceiling():
    """An outing below the ceiling narrows nothing — every level above counter_service also
    permits a casual meal. Crediting it would put a cell in the key the page does not carry."""
    card = _card_at("white_tablecloth")
    assert schedule.formality_lands("white_tablecloth", card)
    assert not schedule.formality_lands("casual_sit_down", card)
    assert not schedule.formality_lands("", card)


def test_formality_entailment_is_suppressed_where_the_field_has_a_beat():
    card = _card_at("counter_service")
    assert schedule.formality_lands("counter_service", card)
    assert not schedule.formality_lands(
        "counter_service", card, here=["formality_ceiling"]
    )


def test_the_formality_occasion_is_drawn_but_placement_never_reads_it():
    """The containment claim, and the reason this is an entailment and not an `Incidental`.

    `_map_of` reads occasions and feeds `emap_so_far` into placement, so an `Incidental` here
    would move other fields' beats across the whole corpus. Measured on the pilot draw: all
    five subjects' beats and occasions come out byte-identical and the only cells that move
    are `formality_ceiling`'s own. Here: the draw exists, and nothing in the placement inputs
    can see it.
    """
    subject = _subjects()[0]
    sched = _build(subject, 9)
    assert len(sched.formality) == 9
    assert sched.formality_ceiling == subject.card.get("formality_ceiling")
    # `_map_of` is what placement reads, and its only parameters are beats and incidentals.
    assert set(schedule._map_of.__code__.co_varnames[
        :schedule._map_of.__code__.co_argcount]) == {"beats", "upto", "incidentals"}


def test_formality_entailment_never_reaches_strong_on_its_own():
    """WEAK and no further, on the same terms as the horizon: one outing at the top says a
    reader could tell, not that the page pinned it."""
    subject = _subjects()[0]
    sched = _build(subject, 12)
    entailing = set(sched.formality_sessions())
    beat_sessions = {b.session for b in sched.beats if b.field_id == "formality_ceiling"}
    emap = sched.provisional_map()
    for checkpoint in sorted(entailing):
        if any(s <= checkpoint for s in beat_sessions):
            continue                      # its own beats have taken over; not an entailment
        assert emap.grade(checkpoint, "formality_ceiling") is Grade.WEAK
