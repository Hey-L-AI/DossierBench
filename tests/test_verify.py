"""The verification pass, and the constructions ADR-0036 added for it to check.

Every test here is anchored to a defect the pilot actually shipped. The quotes are real: they
are the text the audit found, kept verbatim so a rule that stops catching them fails loudly
rather than passing on a paraphrase nobody wrote.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from persona_twin import dataset, fields, generator, judge, schedule, transcript, verify
from persona_twin.card import PersonaCard


def _shapes(count: int, subject_id: str = "s"):
    """`EvidenceSchedule.check` reads session shapes; a hand-built schedule has none."""
    return tuple(schedule.session_shape(subject_id, i) for i in range(count))


# --- the third-party construction --------------------------------------------


def _cards(n: int = 5, seed: int = 7):
    return {s.subject_id: s for s in generator.generate(n=n, seed=seed).subjects}


def test_a_third_party_beat_moves_no_grade():
    """The whole construction in one assertion: it is on the page and not on the card."""
    card = PersonaCard("s", values={"allergies": [], "dietary": []})
    plain = schedule.EvidenceSchedule(
        "s", 3, beats=[schedule.Beat(0, "allergies", "request")]
    )
    foreign = schedule.EvidenceSchedule(
        "s", 3, beats=[schedule.Beat(
            0, "allergies", "request",
            attributed=schedule.THIRD_PARTY, foreign_value=["sesame"],
        )],
    )
    assert plain.provisional_map().grade(0, "allergies").name == "STRONG"
    assert foreign.provisional_map().grade(0, "allergies").name == "UNEVIDENCED"
    del card


def test_a_third_party_beat_must_carry_a_value():
    with pytest.raises(ValueError, match="companion's value"):
        schedule.Beat(0, "allergies", "request", attributed=schedule.THIRD_PARTY)


def test_only_a_third_party_beat_carries_a_foreign_value():
    with pytest.raises(ValueError, match="foreign value"):
        schedule.Beat(0, "allergies", "request", foreign_value=["sesame"])


def test_the_companion_never_shares_the_subjects_constraint():
    """A trap that corroborates is not a trap, and `check` says so."""
    card = PersonaCard("s", values={"allergies": ["sesame"]})
    sched = schedule.EvidenceSchedule(
        "s", 3, shapes=_shapes(3), beats=[schedule.Beat(
            0, "allergies", "request",
            attributed=schedule.THIRD_PARTY, foreign_value=["sesame"],
        )],
    )
    problems = sched.check(card)
    assert any("corroborates" in p for p in problems), problems


def test_the_picker_never_names_a_value_the_card_carries():
    for subject in generator.generate(n=25, seed=7).subjects:
        pick = schedule.third_party_pick(subject.card, subject.subject_id)
        assert pick is not None, subject.subject_id
        field_id, value = pick
        held = subject.card.get(field_id)
        if isinstance(value, list):
            assert not (set(value) & set(held or ())), (subject.subject_id, field_id)
        else:
            assert held != value, (subject.subject_id, field_id)


def test_every_subject_long_enough_carries_the_trap():
    """Deterministic on first eligible, not a per-session roll. See pending 2's measurement."""
    subjects = generator.generate(n=20, seed=7).subjects
    for subject in subjects:
        sched = schedule.build_schedule(
            subject_id=subject.subject_id, card=subject.card, session_count=8
        )
        traps = [e for e in sched.events if e.kind == "third_party"]
        assert len(traps) == 1, (subject.subject_id, traps)
        assert traps[0].checkpoint >= schedule.THIRD_PARTY_EARLIEST


def test_the_trap_is_prefix_stable():
    subject = generator.generate(n=5, seed=7).subjects[4]
    short = schedule.build_schedule(
        subject_id=subject.subject_id, card=subject.card, session_count=8
    )
    long = schedule.build_schedule(
        subject_id=subject.subject_id, card=subject.card, session_count=25
    )
    kept = [b.to_json() for b in long.beats if b.session < 8]
    assert [b.to_json() for b in short.beats] == kept


def test_a_companions_field_stays_in_must_unevidenced():
    """The `counts_toward_gold` line, drawn where it is easiest to get wrong.

    `must_unevidenced` was computed from every beat's field. A subject whose only
    `accessibility_needs` beat belongs to a companion would have dropped off the
    never-evidenced list on the strength of somebody else's requirement.
    """
    corpus = dataset.build(n=5, seed=7)
    for item in corpus.items:
        for trap in item.third_party:
            field_id = str(trap["field_id"])
            own = [
                b for session in item.sessions for b in session.beats
                if b["field_id"] == field_id and b.get("attributed") != schedule.THIRD_PARTY
            ]
            if not own:
                assert field_id in item.must_unevidenced, (item.subject_id, field_id)


def test_the_trap_does_not_count_as_a_single_mention():
    card = PersonaCard("s", values={"allergies": ["shellfish"]})
    sched = schedule.EvidenceSchedule(
        "s", 6, shapes=_shapes(6),
        beats=[
            schedule.Beat(1, "allergies", "request"),
            schedule.Beat(3, "allergies", "request",
                          attributed=schedule.THIRD_PARTY, foreign_value=["sesame"]),
        ],
        directives=("single_mention:allergies",),
    )
    assert not any("single_mention" in p and "beats" in p for p in sched.check(card))


# --- the brief ---------------------------------------------------------------


def test_a_repeated_beat_is_briefed_once_and_counted():
    """`gen-004-s12` carried 'FIELD noise_preference — via REACTION' twice, verbatim."""
    beat = {
        "field_id": "noise_preference", "move": "reaction", "supports_gold": True,
        "note": "", "bin": None, "attributed": "user", "gold_value": "prefers_low_hum",
    }
    brief = transcript.session_brief(session=3, beats=[beat, dict(beat)], turns=12)
    assert brief.count("FIELD noise_preference") == 1
    assert "HAPPENS 2 TIMES" in brief
    assert "separate moments" in brief


def test_a_third_party_beat_is_briefed_as_someone_elses():
    beat = {
        "field_id": "dietary", "move": "request", "supports_gold": True, "note": "",
        "bin": None, "attributed": schedule.THIRD_PARTY, "gold_value": ["gluten_free"],
    }
    text = transcript.beat_brief(beat)
    assert "SOMEONE ELSE'S REQUIREMENT" in text
    assert "DOES NOT SHARE IT" in text
    assert "THIS IS THE USER'S OWN" not in text


def test_an_ordinary_beat_is_pinned_to_the_user():
    beat = {
        "field_id": "alcohol_free_required", "move": "request", "supports_gold": True,
        "note": "", "bin": None, "attributed": "user", "gold_value": True,
    }
    assert "THIS IS THE USER'S OWN" in transcript.beat_brief(beat)


def test_an_unestablished_constraint_is_silenced_not_declared():
    """The `avoid` block must forbid *raising* the subject, not only contradicting it.

    Listing what the user must not do invites the author to have them decline it, which would
    pin a field the schedule has not scheduled. Silence is the instruction.
    """
    card = {"dietary": ["vegan"], "cuisines_avoided": ["spanish"], "allergies": [],
            "accessibility_needs": [], "alcohol_free_required": False}
    block = transcript.continuity_for(card=card, established=(), this_session=())
    assert block["avoid"] and not block["standing"]
    text = transcript.session_brief(session=2, beats=[], turns=8, continuity=block)
    assert "NEVER COMES UP" in text
    assert "do not turn them down" in text


def test_an_established_constraint_becomes_standing():
    card = {"dietary": ["vegan"], "cuisines_avoided": [], "allergies": [],
            "accessibility_needs": [], "alcohol_free_required": False}
    block = transcript.continuity_for(
        card=card, established=("dietary",), this_session=()
    )
    assert block["standing"] == ["they do not eat meat, fish, dairy, cheese, eggs or honey"]
    assert not block["avoid"]


def test_the_field_being_written_now_is_never_also_silenced():
    card = {"dietary": ["vegan"], "cuisines_avoided": [], "allergies": [],
            "accessibility_needs": [], "alcohol_free_required": False}
    block = transcript.continuity_for(
        card=card, established=("dietary",), this_session=("dietary",)
    )
    assert not block["standing"] and not block["avoid"]


def test_the_bot_may_not_screen_for_an_unbriefed_preference():
    """ADR-0041. The ask is on 39 of 67 sessions in the store this was measured on, and the
    reader arm caught two of them as contradictions against `own_neighborhood`."""
    page = (
        "USER: somewhere for saturday\n"
        "BOT: Any part of town you'd like me to look at?\n"
        "USER: anywhere reachable\n"
        "BOT: Booked.\n"
    )
    problems = transcript._screening_problems(page, beats=[])
    assert [p.kind for p in problems] == ["unbriefed_screening"]
    assert "travel_radius" in problems[0].detail


def test_any_beat_on_the_field_exempts_the_ask():
    """Unlike the intake gate's disclosure-only carve-out: a preference is reached by
    `request`, `rejection` or `reaction` just as legitimately."""
    page = "USER: somewhere for saturday\nBOT: Any part of town?\nUSER: near me\n"
    for move in ("request", "reaction", "rejection", "anecdote"):
        beats = [{"field_id": "travel_radius", "move": move, "attributed": schedule.USER}]
        assert not transcript._screening_problems(page, beats=beats), move


def test_the_screening_gate_is_scoped_to_the_question():
    """Same sentence-scoping as the intake gate, and for the same reason: a bot that *describes*
    an area is not screening the user about one."""
    page = (
        "USER: somewhere for saturday\n"
        "BOT: The Foundry is in the north part of town, about ten minutes out. Shall I book?\n"
        "USER: go on\n"
    )
    assert not transcript._screening_problems(page, beats=[])


def test_the_question_budget_is_a_ceiling_reported_not_enforced():
    """A ceiling, and report-only.

    The band was (2, 4) on the reading that the rule would have to *backfill* questions the
    screening bar takes away. Re-measured, the over-asking bot is already gone — 8 of 67
    sessions exceed three here against 29 of 67 pre-ADR-0038 — so a single-question session is
    the short, ordinary conversation §6.1 wants, not a defect. A floor of two would have pushed
    18 of the store's 67 sessions *up*, which is the opposite of what the brief asks for.
    """
    def page(n: int) -> str:
        turns = ["USER: somewhere for saturday"]
        for index in range(n):
            turns += [f"BOT: Question {index}?", "USER: sure"]
        return "\n".join(turns + ["BOT: Booked."]) + "\n"

    assert verify._question_budget("t", page(1)) == []
    assert verify._question_budget("t", page(3)) == []
    assert [f.kind for f in verify._question_budget("t", page(4))] == ["question_budget"]
    assert [f.severity for f in verify._question_budget("t", page(6))] == ["note"]


def test_a_silent_bot_is_the_construction_and_not_a_defect_in_any_session():
    """`gen-004-s5` and `gen-004-s19` are the distractors that forced the old exemption, and
    v0.5 generalizes it: the floor is gone for every session, not only for those.

    The brief's permitted set is now the beats alone — the outing carve-out that licensed
    asking the area, the budget, the party size and the hour is gone with it. So a session
    with no askable field has nothing for the bot to clarify toward, and every question it
    could ask would be one the session did not brief. That was already true of a distractor
    and is now equally true of an anecdote-only ordinary session, which is what makes a floor
    of 1 a diagnostic that reports the intended construction.
    """
    page = (
        "USER: the app logged me out and lost my saved list\n"
        "BOT: That list cannot be restored from here.\n"
        "USER: brilliant\n"
    )
    assert verify._question_budget("t", page, {"is_distractor": True}) == []
    assert verify._question_budget("t", page, {"is_distractor": False}) == []
    assert verify._question_budget("t", page) == []
    assert verify.QUESTION_BUDGET[0] == 0

    # The ceiling is the whole of the check and it stays universal. `gen-003-s11` is a
    # distractor whose bot asks four questions — the interview shape this exists to report,
    # and more suspect in a session that briefs nothing rather than less.
    chatty = "USER: I lost a scarf somewhere\n" + "".join(
        f"BOT: Question {index}?\nUSER: no idea\n" for index in range(4)
    )
    assert [f.kind for f in verify._question_budget("t", chatty, {"is_distractor": True})] == [
        "question_budget"
    ]
    assert [f.kind for f in verify._question_budget("t", chatty)] == ["question_budget"]


def test_a_companions_beat_does_not_silence_the_subjects_own_guard():
    """`gen-003-s2`, found by the reader arm on 2026-08-18.

    The session schedules `cuisines_avoided` as an ADR-0036 third-party trap — a companion who
    will not eat french — and the guard keyed on the field alone, so the subject's own
    *vietnamese, sushi* line vanished from the brief and the page booked a kaiseki lunch. The
    beat naming a field is not the same claim as the subject having said something about it.
    """
    card = {"dietary": [], "cuisines_avoided": ["vietnamese", "sushi"], "allergies": [],
            "accessibility_needs": [], "alcohol_free_required": False}
    companion = transcript.continuity_for(
        card=card, established=(), this_session=("cuisines_avoided",), this_session_own=(),
    )
    assert companion["avoid"] == [
        "the cuisines vietnamese, sushi — as somewhere the user goes or wants to go"
    ]
    own = transcript.continuity_for(
        card=card, established=(), this_session=("cuisines_avoided",),
        this_session_own=("cuisines_avoided",),
    )
    assert not own["avoid"] and not own["standing"]


def test_a_counter_beat_still_silences_the_guard():
    """`attributed == USER` and not `counts_toward_gold`: a stated-vs-revealed counter-beat is
    the subject speaking about the field, and a session told to be inconsistent about a value
    cannot also be told the value never comes up."""
    card = {"dietary": ["vegan"], "cuisines_avoided": [], "allergies": [],
            "accessibility_needs": [], "alcohol_free_required": False}
    block = transcript.continuity_for(
        card=card, established=("dietary",), this_session=("dietary",),
        this_session_own=("dietary",),
    )
    assert not block["standing"] and not block["avoid"]


def test_the_contradiction_probe_keys_its_control_on_whose_beat_it_is():
    """The same defect in the instrument: the probe skipped `cuisines_avoided` because *some*
    beat named it, so the cheaper of the two arms was blind exactly where the brief was."""
    card = {"dietary": [], "cuisines_avoided": ["vietnamese"], "allergies": [],
            "accessibility_needs": [], "alcohol_free_required": False}
    page = "USER: the vietnamese one then\nBOT: Booked."
    companion = [{"field_id": "cuisines_avoided", "attributed": schedule.THIRD_PARTY}]
    assert transcript.contradiction_probes(page, card, beats=companion)
    own = [{"field_id": "cuisines_avoided", "attributed": schedule.USER}]
    assert not transcript.contradiction_probes(page, card, beats=own)


def test_the_scenery_states_the_horizon_the_card_implies():
    """ADR-0040. The horizon is settled by the session date and the drawn bin whether the brief
    mentions it or not; leaving it unstated meant the author guessed, and two of the reader
    arm's nine contradictions were that guess landing against the card."""
    card = {"planning_style": "fully_spontaneous",
            "visit_time_distribution": {"weekend-day": 1.0}}
    block = transcript.continuity_for(
        card=card, this_session=(), occasions={"visit_time_distribution": "weekend-day"},
    )
    assert block["scenery"]["when"] == (
        "daytime at the weekend, and it is for today — they are out already and sorting it now"
    )

    ahead = dict(card, planning_style="books_weeks_ahead")
    block = transcript.continuity_for(
        card=ahead, this_session=(), occasions={"visit_time_distribution": "weekend-day"},
    )
    assert block["scenery"]["when"].endswith("a few weeks off, booked well ahead")


def test_the_horizon_is_suppressed_where_planning_style_has_a_beat():
    """Same guard rule as the standing block: a session writing the field is not also told
    what the field is."""
    card = {"planning_style": "fully_spontaneous",
            "visit_time_distribution": {"weekend-day": 1.0}}
    block = transcript.continuity_for(
        card=card, this_session=("planning_style",),
        occasions={"visit_time_distribution": "weekend-day"},
    )
    assert block["scenery"]["when"] == "daytime at the weekend"


def test_the_brief_names_the_drawn_occasion_not_the_shape():
    """ADR-0037. Describing the shape hands the author the sentence rule 3 bans, and leaves
    the drawn bin — the one the map counts — up to it."""
    card = {"party_size_distribution": {"1": 0.41, "2": 0.58, "3-5": 0.01, "6+": 0.0}}
    block = transcript.continuity_for(
        card=card, this_session=(), occasions={"party_size_distribution": "2"}
    )
    assert block["scenery"] == {
        "who is going": "the user and one other person",
        # Price is the one occasion a session may leave alone, and it has to be *told* to —
        # an author left to itself supplied a bin the card gave a weight of 0.01.
        "roughly what it costs":
            "does not come up — nobody settles a number, and the user does not react to one",
    }
    text = transcript.session_brief(session=1, beats=[], turns=8, continuity=block)
    assert "THIS PARTICULAR OUTING" in text
    listed = [
        line for line in text.splitlines() if line.strip().startswith("- who is going")
    ]
    assert listed == ["  - who is going: the user and one other person"]
    # The frequency words appear only in the prohibition, never in what is asked for.
    assert not any("usually" in line or "0.58" in line for line in listed)


def test_an_occasion_is_dropped_when_the_field_has_a_beat_this_session():
    """One outing is one party size. A session making a point of it does not also have
    scenery quietly instantiating a different bin."""
    card = {"party_size_distribution": {"1": 0.41, "2": 0.58, "3-5": 0.01, "6+": 0.0}}
    block = transcript.continuity_for(
        card=card, this_session=("party_size_distribution",),
        occasions={"party_size_distribution": "2"},
    )
    assert block["scenery"] == {}


def test_a_scheduled_occasion_never_collides_with_a_beat():
    """The same rule, checked where it is actually enforced rather than in the brief."""
    for subject in generator.generate(n=12, seed=7).subjects:
        sched = schedule.build_schedule(
            subject_id=subject.subject_id, card=subject.card, session_count=14
        )
        for occasion in sched.incidentals:
            clash = [
                b for b in sched.beats_in(occasion.session)
                if b.field_id == occasion.field_id
            ]
            assert not clash, (subject.subject_id, occasion)


def test_an_occasion_never_lands_in_a_distractor_session():
    for subject in generator.generate(n=12, seed=7).subjects:
        sched = schedule.build_schedule(
            subject_id=subject.subject_id, card=subject.card, session_count=14
        )
        quiet = set(sched.distractor_sessions)
        assert not [i for i in sched.incidentals if i.session in quiet]


def test_occasions_are_prefix_stable():
    subject = generator.generate(n=5, seed=7).subjects[4]
    short = schedule.build_schedule(
        subject_id=subject.subject_id, card=subject.card, session_count=8
    )
    long = schedule.build_schedule(
        subject_id=subject.subject_id, card=subject.card, session_count=25
    )
    kept = [i.to_json() for i in long.incidentals if i.session < 8]
    assert [i.to_json() for i in short.incidentals] == kept


def test_a_counted_occasion_moves_the_map():
    """The whole of ADR-0037 in one assertion: the page instantiates it, so the map sees it."""
    card = PersonaCard("s", values={
        "party_size_distribution": {"1": 0.5, "2": 0.5, "3-5": 0.0, "6+": 0.0}
    })
    bare = schedule.EvidenceSchedule("s", 3, shapes=_shapes(3))
    with_one = schedule.EvidenceSchedule(
        "s", 3, shapes=_shapes(3),
        incidentals=[schedule.Incidental(0, "party_size_distribution", "2")],
    )
    assert bare.provisional_map().grade(0, "party_size_distribution").name == "UNEVIDENCED"
    assert with_one.provisional_map().grade(0, "party_size_distribution").name == "WEAK"
    del card


def test_a_once_mentioned_constraint_is_suppressed_afterwards():
    """`gen-001`'s allergy was scheduled once at s2 and raised again by the author at s4."""
    card = {"allergies": ["shellfish"], "dietary": [], "cuisines_avoided": [],
            "accessibility_needs": [], "alcohol_free_required": False}
    block = transcript.continuity_for(
        card=card, established=("allergies",), this_session=(), suppress=("allergies",)
    )
    text = transcript.session_brief(session=4, beats=[], turns=8, continuity=block)
    assert "DO NOT RAISE AGAIN" in text


def test_a_distractor_session_gets_no_continuity_block():
    corpus = dataset.build(n=5, seed=7)
    for item in corpus.items:
        for session in item.sessions:
            if session.is_distractor:
                assert session.continuity == {}


# --- the deterministic checks -------------------------------------------------


def test_consecutive_speaker_turns_are_caught():
    """The real `gen-001-s1` defect: the bot speaks twice and no §6.1 rule could see it."""
    text = (
        "USER: any thoughts on lunch spots near the office\n"
        "BOT: Sure — could you tell me roughly where the office is?\n"
        "BOT: Anything else I can narrow down?\n"
        "USER: off Weller Street\n"
    )
    kinds = [p.kind for p in transcript.check_session(text)]
    assert kinds == ["speaker_repeats"]


def test_a_clean_transcript_has_no_alternation_problem():
    text = "USER: hi\nBOT: hello\nUSER: somewhere for friday\nBOT: sure\n"
    assert not transcript.check_session(text)


def test_a_companionless_third_party_beat_is_caught():
    beat = {"field_id": "dietary", "move": "request",
            "attributed": schedule.THIRD_PARTY, "gold_value": ["gluten_free"]}
    lonely = "USER: I need somewhere gluten free\nBOT: Understood.\nUSER: cheers\n"
    kinds = [p.kind for p in transcript.check_session(lonely, beats=[beat])]
    assert "third_party_unattributed" in kinds

    shared = ("USER: one of them can't have gluten, so nowhere with a pasta-only menu\n"
              "BOT: Understood.\nUSER: cheers\n")
    assert not transcript.check_session(shared, beats=[beat])


def test_the_contradiction_probe_finds_the_pilots_contradictions():
    card = {"dietary": ["vegan"], "allergies": [], "cuisines_avoided": ["spanish"],
            "alcohol_free_required": False}
    text = "USER: I've had the pork one there like four times. what else do they do\n"
    problems = transcript.contradiction_probes(text, card)
    assert [p.kind for p in problems] == ["contradiction_suspect"]
    assert "dietary" in problems[0].detail


def test_the_contradiction_probe_skips_the_field_being_evidenced():
    """The user declining an avoided cuisine *is* the scheduled beat, not a contradiction."""
    card = {"dietary": [], "allergies": [], "cuisines_avoided": ["spanish"],
            "alcohol_free_required": False}
    text = "USER: not the spanish place, tapas never fills me up\n"
    beats = [{"field_id": "cuisines_avoided", "move": "rejection"}]
    assert not transcript.contradiction_probes(text, card, beats=beats)


# --- reconciliation ----------------------------------------------------------


def _record(**over):
    base = {
        "task_id": "gen-000-s3", "subject_id": "gen-000", "session": 3, "checkpoint": 3,
        "transcript": "USER: hi\nBOT: hello\n",
        "beats": [{"field_id": "alcohol_free_required", "move": "request",
                   "attributed": "user", "gold_value": True}],
    }
    base.update(over)
    return base


_ITEM = {
    "subject_id": "gen-000",
    "card": {fid: None for fid in fields.ALL_FIELDS_IDS},
    "provisional": {"3": {fid: "UNEVIDENCED" for fid in fields.ALL_FIELDS_IDS}},
}


def test_a_user_beat_read_as_a_companions_is_blocking():
    """`gen-002-s8`: 'one of the four doesn't go anywhere there's a bar'."""
    verdict = {"beats": [{
        "field_id": "alcohol_free_required", "delivered": True, "strength": "pinned",
        "whose": "third_party", "moments": 1,
        "quote": "one of the four doesn't go anywhere there's a bar",
    }], "unscheduled": [], "contradictions": []}
    findings = verify.reconcile(_record(), _ITEM, verdict)
    assert [f.kind for f in findings] == ["misattributed"]
    assert findings[0].severity == verify.BLOCKING


def test_a_trap_read_as_the_users_is_blocking():
    record = _record(beats=[{
        "field_id": "dietary", "move": "request",
        "attributed": schedule.THIRD_PARTY, "gold_value": ["gluten_free"],
    }])
    verdict = {"beats": [{"field_id": "dietary", "delivered": True, "strength": "pinned",
                          "whose": "user", "moments": 1, "quote": "I can't have gluten"}],
               "unscheduled": [], "contradictions": []}
    findings = verify.reconcile(record, _ITEM, verdict)
    assert [f.kind for f in findings] == ["trap_collapsed"]


def test_a_correctly_attributed_trap_is_silent():
    record = _record(beats=[{
        "field_id": "dietary", "move": "request",
        "attributed": schedule.THIRD_PARTY, "gold_value": ["gluten_free"],
    }])
    verdict = {"beats": [{"field_id": "dietary", "delivered": True, "strength": "pinned",
                          "whose": "third_party", "moments": 1,
                          "quote": "one of them can't have gluten"}],
               "unscheduled": [], "contradictions": []}
    assert not verify.reconcile(record, _ITEM, verdict)


def test_a_repeat_the_page_did_not_carry_is_blocking():
    beat = {"field_id": "noise_preference", "move": "reaction", "attributed": "user",
            "gold_value": "prefers_low_hum"}
    record = _record(beats=[beat, dict(beat)])
    verdict = {"beats": [{"field_id": "noise_preference", "delivered": True,
                          "strength": "narrowed", "whose": "user", "moments": 1,
                          "quote": "bright and open, no"}],
               "unscheduled": [], "contradictions": []}
    kinds = [f.kind for f in verify.reconcile(record, _ITEM, verdict)]
    assert "repeat_not_delivered" in kinds


def test_a_repeat_the_page_did_carry_is_silent():
    beat = {"field_id": "noise_preference", "move": "reaction", "attributed": "user",
            "gold_value": "prefers_low_hum"}
    record = _record(beats=[beat, dict(beat)])
    verdict = {"beats": [{"field_id": "noise_preference", "delivered": True,
                          "strength": "narrowed", "whose": "user", "moments": 2,
                          "quote": "bright and open, no"}],
               "unscheduled": [], "contradictions": []}
    assert not verify.reconcile(record, _ITEM, verdict)


def test_two_entries_of_one_moment_are_two_moments():
    """The shape every fixture above missed, and it cost three false blockers.

    A reader asked about a field mentioned twice writes TWO entries of `moments: 1` — two
    quotes, two places in the conversation — at least as readily as one entry saying 2. The
    reconciler keyed the reader's beats into a dict by field, so the second entry overwrote
    the first and `repeat_not_delivered` compared the map's 2 against the survivor's 1. It
    fired on `gen-002-s6`, `gen-003-s8` and `gen-004-s11` in two stores across a full
    re-author, and the excerpts say all three pages carried both moments.
    """
    beat = {"field_id": "noise_preference", "move": "reaction", "attributed": "user",
            "gold_value": "prefers_low_hum"}
    record = _record(beats=[beat, dict(beat)])
    entry = {"field_id": "noise_preference", "delivered": True, "strength": "narrowed",
             "whose": "user", "moments": 1}
    verdict = {"beats": [dict(entry, quote="a morgue in there"),
                         dict(entry, quote="you can hear yourself swallow")],
               "unscheduled": [], "contradictions": []}
    assert not verify.reconcile(record, _ITEM, verdict)


def test_a_companions_line_is_not_one_of_the_subjects_moments():
    """`gen-001-s2`: the subject's own shellfish line AND a companion's sesame line.

    Two voices on one field in one session. The beat is the trap, so it reconciles against
    the third-party entry; reading the subject's own correct line as the trap collapsing is
    the same defect as last-wins, mirrored. The subject's moments do not count toward a
    companion's beat either.
    """
    record = _record(beats=[{
        "field_id": "allergies", "move": "request",
        "attributed": schedule.THIRD_PARTY, "gold_value": ["sesame"],
    }])
    verdict = {"beats": [
        {"field_id": "allergies", "delivered": True, "strength": "pinned", "whose": "user",
         "moments": 2, "quote": "no shellfish anywhere near me"},
        {"field_id": "allergies", "delivered": True, "strength": "pinned",
         "whose": "third_party", "moments": 1,
         "quote": "the person I'm with can't have sesame"},
    ], "unscheduled": [], "contradictions": []}
    assert not verify.reconcile(record, _ITEM, verdict)


def test_a_field_delivered_in_the_wrong_voice_only_is_still_blocking():
    """The fix must not buy silence: with no entry in the beat's voice, it still fires."""
    record = _record(beats=[{
        "field_id": "allergies", "move": "request",
        "attributed": schedule.THIRD_PARTY, "gold_value": ["sesame"],
    }])
    verdict = {"beats": [
        {"field_id": "allergies", "delivered": True, "strength": "pinned", "whose": "user",
         "moments": 1, "quote": "no shellfish anywhere near me"},
    ], "unscheduled": [], "contradictions": []}
    assert [f.kind for f in verify.reconcile(record, _ITEM, verdict)] == ["trap_collapsed"]


def test_the_audit_demotes_and_does_not_promote():
    """ADR-0009/0010: where a reader and the schedule disagree, the schedule is the record."""
    beat = {"field_id": "noise_preference", "move": "reaction", "attributed": "user",
            "gold_value": "prefers_low_hum"}
    verdict = {"beats": [{"field_id": "noise_preference", "delivered": True,
                          "strength": "pinned", "whose": "user", "moments": 1, "quote": "x"}],
               "unscheduled": [], "contradictions": []}
    findings = verify.reconcile(_record(beats=[beat]), _ITEM, verdict)
    assert [f.kind for f in findings] == ["over_delivered"]
    assert findings[0].severity == verify.NOTE


def test_a_narrowed_distribution_bin_is_delivered_though_not_pinned():
    """`gen-007-s16`: 'Saturday night' narrows visit_time toward weekend-evening.

    A distribution beat carries a `bin` and is briefed as one bin as a single occasion; a
    reader marks it `delivered: false` because a distribution is never pinned by one line, yet
    quotes the occasion and reports it `narrowed`. Reconciling on the binary gate read that as
    absent. Six n50 pages failed exactly this way.
    """
    beat = {"field_id": "visit_time_distribution", "move": "anecdote", "attributed": "user",
            "bin": "weekend-evening", "gold_value": {"weekend-evening": 0.21}}
    verdict = {"beats": [{"field_id": "visit_time_distribution", "delivered": False,
                          "strength": "narrowed", "whose": "user", "moments": 0,
                          "quote": "Need somewhere for Saturday night"}],
               "unscheduled": [], "contradictions": []}
    assert not verify.reconcile(_record(beats=[beat]), _ITEM, verdict)


def test_a_distribution_bin_the_page_never_carried_is_still_undelivered():
    """The exemption must not buy silence: a bin with neither strength nor quote still fires."""
    beat = {"field_id": "visit_time_distribution", "move": "anecdote", "attributed": "user",
            "bin": "weekend-evening", "gold_value": {"weekend-evening": 0.21}}
    verdict = {"beats": [{"field_id": "visit_time_distribution", "delivered": False,
                          "strength": None, "whose": None, "moments": 0, "quote": ""}],
               "unscheduled": [], "contradictions": []}
    assert [f.kind for f in verify.reconcile(_record(beats=[beat]), _ITEM, verdict)] \
        == ["undelivered"]


def test_a_shared_occasion_read_as_ambiguous_is_not_misattributed():
    """`gen-023-s3`: 'we can't sit down before half ten' — the booker's own party, plural.

    A distribution beat is one shared occasion, so its voice reads as first-person-plural and
    the reader hedges to `ambiguous`. That is not the value handed to somebody else.
    """
    beat = {"field_id": "visit_time_distribution", "move": "rejection", "attributed": "user",
            "bin": "late-night", "gold_value": {"late-night": 0.2}}
    verdict = {"beats": [{"field_id": "visit_time_distribution", "delivered": True,
                          "strength": "narrowed", "whose": "ambiguous", "moments": 1,
                          "quote": "we can't sit down before half ten"}],
               "unscheduled": [], "contradictions": []}
    assert not verify.reconcile(_record(beats=[beat]), _ITEM, verdict)


def test_a_distribution_bin_attributed_to_a_companion_is_still_misattributed():
    """Ambiguous is exempt; a positive attribution to another voice is not."""
    beat = {"field_id": "visit_time_distribution", "move": "rejection", "attributed": "user",
            "bin": "late-night", "gold_value": {"late-night": 0.2}}
    verdict = {"beats": [{"field_id": "visit_time_distribution", "delivered": True,
                          "strength": "narrowed", "whose": "third_party", "moments": 1,
                          "quote": "my friend won't sit before half ten"}],
               "unscheduled": [], "contradictions": []}
    assert [f.kind for f in verify.reconcile(_record(beats=[beat]), _ITEM, verdict)] \
        == ["misattributed"]


def test_an_unscheduled_field_on_an_unevidenced_cell_is_a_leak():
    verdict = {"beats": [{"field_id": "alcohol_free_required", "delivered": True,
                          "strength": "pinned", "whose": "user", "moments": 1, "quote": "x"}],
               "unscheduled": [{"field_id": "travel_radius", "strength": "narrowed",
                                "quote": "near enough"}],
               "contradictions": []}
    findings = verify.reconcile(_record(), _ITEM, verdict)
    assert [f.kind for f in findings] == ["leak"]
    assert findings[0].severity == verify.BLOCKING


def test_an_unparsed_reply_raises_rather_than_reading_as_nothing_found():
    with pytest.raises(ValueError):
        verify.parse_verdict("I could not find anything wrong with this conversation.")


def test_a_fenced_reply_parses():
    payload = verify.parse_verdict(
        '```json\n{"beats": [], "unscheduled": [], "contradictions": []}\n```'
    )
    assert payload == {"beats": [], "unscheduled": [], "contradictions": []}


def test_a_reply_missing_a_list_is_filled_not_rejected():
    assert verify.parse_verdict('{"beats": []}') == {
        "beats": [], "unscheduled": [], "contradictions": []
    }


# --- the deterministic arm end to end ----------------------------------------


def test_inspect_finds_drift_between_corpus_and_store(tmp_path: Path):
    corpus = dataset.build(n=2, seed=7)
    payload = corpus.to_json()
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps(payload))
    store = tmp_path / "store"
    store.mkdir()
    for item in payload["items"]:
        for session in item["sessions"]:
            record = dict(session)
            record["transcript"] = "USER: hi\nBOT: hello\n"
            record["beats"] = []          # the drift
            (store / f"{session['task_id']}.json").write_text(json.dumps(record))
    report = verify.inspect(corpus_path, store)
    assert any(f.kind == "drift" for f in report.findings)
    assert report.blocking


def test_the_sidecar_files_this_volume_writes_are_not_counted(tmp_path: Path):
    """exFAT writes `._*` beside every file; v0 read a 72-call run as 144."""
    store = tmp_path / "store"
    store.mkdir()
    (store / "gen-000-s0.json").write_text(json.dumps(
        {"task_id": "gen-000-s0", "transcript": "USER: hi\n"}
    ))
    (store / "._gen-000-s0.json").write_bytes(b"\x00\x05\x16\x07")
    assert list(verify.load_store(store)) == ["gen-000-s0"]


def test_a_pre_adr_0037_corpus_says_so_once_rather_than_flagging_every_session():
    """A corpus with no scheduled occasions would report every instance as unscheduled.

    The pilot is exactly this — it is kept as the *audited* store and predates ADR-0037 — and
    without the guard its report went from 34 findings to 60, of which the 26 new ones all
    said the same thing about a corpus that cannot be fixed by re-reading it.
    """
    item = {
        "subject_id": "gen-000",
        "card": {"party_size_distribution": {"1": 1.0, "2": 0.0, "3-5": 0.0, "6+": 0.0}},
        "provisional": {"0": {fid: "UNEVIDENCED" for fid in fields.ALL_FIELDS_IDS}},
        "sessions": [{"task_id": "gen-000-s0", "session": 0, "checkpoint": 0, "beats": []}],
        "directives": [],
    }
    record = {
        "task_id": "gen-000-s0", "subject_id": "gen-000", "session": 0, "checkpoint": 0,
        "transcript": (
                "USER: table for four on friday\n"
                "BOT: Happy to. Lunch or dinner?\n"
                "USER: dinner\n"
                "BOT: Anything you'd like me to prioritise?\n"
                "USER: no, just book it\n"
                "BOT: Booked.\n"
            ), "beats": [],
    }
    assert verify._page("gen-000-s0", record, item["sessions"][0], item) == []
    kinds = [f.kind for f in verify._timeline("gen-000", item, {"gen-000-s0": record})]
    assert kinds == ["stale_corpus"]

    # With the key present, the same page is read normally.
    item["incidentals"] = []
    kinds = [f.kind for f in verify._page("gen-000-s0", record, item["sessions"][0], item)]
    assert "unscheduled_occasion" in kinds


def test_a_reflexive_myself_is_not_a_party_of_one():
    """"I'd just look it up myself" is not someone dining alone.

    The pilot shipped this and it was the whole of the ADR-0038 audit's blocking count. The
    `party_size_distribution` bin `1` carried a bare `"myself"` surface next to the specific
    `"by myself"` and `"just myself"`, and English uses the word reflexively far more often
    than it uses it to mean alone. Across 67 authored sessions nine user turns contained it,
    **one** was a person eating alone — and that one says "by myself", so the specific surface
    already had it. Zero unique true positives, eight false ones.

    The blocking one was read out of a *distractor* session — a support call about a lost
    booking link, no outing being arranged at all — on a subject whose card gives a party of
    one a weight of 0.00, so the probe reported the page contradicting the card on the
    strength of "if I remembered the name I'd just look it up myself".

    The general lesson is in `DELIVERY_SURFACE`'s own docstring, which records the opposite
    failure: surfaces that were too narrow answered "no" 12 times out of 12 where the author
    had delivered every one. Broadening bought recall and this is what it cost. A surface
    earns its place by catching something the specific forms miss.
    """
    reflexive = [
        "that's the problem, if I remembered the name I'd just look it up myself",
        "I'll dig it out myself, it's easier than explaining which one",
        "the larb's the one with all the chilli in it? I'd be sat there fanning myself",
        "I'll put it to the others this week and ring them myself if anything comes up",
    ]
    surfaces = transcript.DELIVERY_SURFACE["party_size_distribution"]["1"]
    for text in reflexive:
        assert not _matches_any(text.lower(), surfaces), text

    # ...and the genuine reading still lands, via the specific form.
    assert _matches_any(
        "hearing the next table's fine, I sat by myself at the counter", surfaces
    )


def _matches_any(lowered: str, surfaces) -> bool:
    """`verify._incidental`'s matching rule, kept in step with it deliberately."""
    import re
    return any(
        re.search(s if "\\" in s else r"\b" + re.escape(s) + r"\b", lowered)
        for s in surfaces
    )


# --- the bot screening the user (ADR-0038's reader finding) -------------------


def _intake(text: str, card: dict, beats=()) -> list:
    item = {"subject_id": "gen-000", "card": card}
    return verify._unbriefed_intake("gen-000-s3", text, list(beats), item)


def test_a_denial_of_a_constraint_the_card_carries_is_blocking():
    """The worst defect the pilot has produced, and the reader caught 2 of its 9 cases.

    The bot asks an intake question the session never briefed; the author, with no positive
    fact to answer from, writes the denial; and the page now states the negation of ground
    truth. That is strictly worse than a leak — a twin reading it correctly concludes the
    opposite of the card, and the map cannot see the problem because the cell is UNEVIDENCED.

    Deterministic rather than left to the reader precisely because of that 2-of-9: a class a
    sampled reader catches at 22% is not one sampling can be trusted with.
    """
    text = (
        "USER: somewhere for saturday\n"
        "BOT: Booked for 5:30pm, two. Anything I should note — allergies or anything "
        "you avoid?\n"
        "USER: no, nothing to flag\n"
    )
    found = _intake(text, {"dietary": ["dairy_free"]})
    assert [f.kind for f in found] == ["denied_constraint"]
    assert found[0].severity == verify.BLOCKING
    assert found[0].field_id == "dietary"


def test_a_denial_about_a_field_the_card_does_not_carry_is_only_a_note():
    """`gen-004-s11`: asked about step-free entry, and the only constraint is `vegan`.

    The denial is *true* — this person has no access needs — so blocking here would be the
    gate crying wolf on a correct page. Four of the first twelve blocking findings were this,
    and the fix is to match the question to the field it actually names. The ask is still a
    note, because an unbriefed question settles a cell the schedule left UNEVIDENCED.
    """
    text = (
        "USER: book it\n"
        "BOT: Before I do — any access requirements, steps or step-free entry?\n"
        "USER: no, nothing like that\n"
    )
    found = _intake(text, {"dietary": ["vegan"], "accessibility_needs": []})
    assert [f.kind for f in found] == ["unbriefed_intake"]
    assert found[0].severity == verify.NOTE


def test_a_generic_ask_is_answered_for_every_constraint_at_once():
    """"Anything I should note?" names no field, so it settles all four.

    This is the same cross-field collision ADR-0035 had to fix on the authoring side, where
    one generic question came back as evidence for `dietary` and `allergies` both. A twin that
    acts on it is doing exactly what §8.1 punishes, so the probe must read it the same way.
    """
    text = (
        "USER: sort it\n"
        "BOT: Anything I should flag?\n"
        "USER: no, nothing like that\n"
    )
    found = _intake(text, {"alcohol_free_required": True})
    assert [f.kind for f in found] == ["denied_constraint"]


def test_a_briefed_disclosure_beat_is_the_carve_out():
    """Where the schedule scheduled the ask, the ask is correct and the map counts the answer.

    ADR-0035's whole construction is that an absent constraint is *disclosed* rather than left
    permanently unevidenced — the bot asks, the user says there is nothing, and the cell
    reaches STRONG for the ordinary reason. A rule that fired there would delete the feature.
    """
    text = (
        "USER: book it\n"
        "BOT: Do you follow any particular diet?\n"
        "USER: no, nothing at all\n"
    )
    beats = [{"field_id": "dietary", "move": "disclosure", "attributed": "user"}]
    assert _intake(text, {"dietary": []}, beats) == []


def test_the_carve_out_is_per_field_not_per_session():
    """The bug `probe_constraint_denial.py` measured: `briefed` was a bool, not a set lookup.

    `gen-001-s1`'s shape exactly: a `dietary` disclosure beat is present and correct, but a
    *separate* ask denies `allergies`, which no beat ever briefed. The old code computed
    `briefed = {"dietary"}` and then did `if briefed: return []` — one disclosure beat on one
    field silenced the check for every constraint field in the session, so the allergies
    denial was never looked at. Reproduced in 4 of 5 authoring stores in near-identical words,
    so this is not a one-off roll.
    """
    text = (
        "USER: table for two saturday\n"
        "BOT: Do you follow any particular diet?\n"
        "USER: no, nothing at all\n"
        "BOT: And any allergies I should flag?\n"
        "USER: no, none\n"
    )
    beats = [{"field_id": "dietary", "move": "disclosure", "attributed": "user"}]
    found = _intake(text, {"dietary": [], "allergies": ["shellfish"]}, beats)
    assert [f.kind for f in found] == ["denied_constraint"]
    assert found[0].field_id == "allergies"


def test_dietary_requirements_is_read_as_the_umbrella_it_is():
    """"Dietary requirements" is asked and answered for allergies too, in ordinary English.

    `INTAKE_UMBRELLA` widens the reach of a "dietary"/"anything the kitchen"/"what you eat"
    ask to `allergies` as well — the reader's own verdict on `gen-001-s1`, where the bot's ask
    used exactly this phrasing. It lives in the shared intake table rather than local to this
    file, which it could not while `DISCLOSURE_PROBES` still prescribed the same words. Without the widening, a denial that only names
    "dietary requirements" could deny an allergy on the card and the lexical check would miss
    it, the same recall gap `_INTAKE_DENY`'s tail already had to close once.
    """
    text = (
        "USER: table for two saturday\n"
        "BOT: Any dietary requirements I should note?\n"
        "USER: no, nothing like that\n"
    )
    found = _intake(text, {"allergies": ["shellfish"]})
    assert [f.kind for f in found] == ["denied_constraint"]
    assert found[0].field_id == "allergies"


def test_an_ordinary_conversation_is_silent():
    """The control. A gate that fires on clean text is worse than no gate."""
    text = (
        "USER: somewhere near the park for friday\n"
        "BOT: There's a place on Bellwood with a set menu before 6.\n"
        "USER: that'll do, book it\n"
    )
    assert _intake(text, {"dietary": ["vegan"], "allergies": ["shellfish"]}) == []


def test_a_denial_with_a_clause_after_it_is_still_a_denial():
    """`gen-004-s8`, and the second demonstration of the same recall gap.

    The user answered an unbriefed ask with *"no, nothing. just the two seats"* on a card
    carrying `vegan`, and the first matcher — anchored to the whole turn — reported the softer
    `unbriefed_intake`. One clause after the full stop was the entire difference between a note
    and the blocking finding, on a page that states the negation of ground truth.

    The gap is not incidental. The reader arm found 9 denials to the lexical arm's 8 before the
    intake bar shipped and 2 to its 1 after, so the check written *because* the reader catches
    this class at 22% was itself undercounting it.
    """
    text = (
        "USER: lunch thursday, two of us\n"
        "BOT: Sorrel takes bookings from twelve. Anything I should know before I put it "
        "through?\n"
        "USER: no, nothing. just the two seats\n"
    )
    found = _intake(text, {"dietary": ["vegan"]})
    assert [f.kind for f in found] == ["denied_constraint"]
    assert found[0].severity == verify.BLOCKING


def test_a_bare_denial_and_a_named_one_both_match():
    """The shapes the pilot actually produced, plus the two the widened tail admits.

    `no allergies.` denies the field by naming it, and `none, thanks` closes on a comma that
    leads only into thanks — the one comma the matcher accepts, because everything else after
    one is a continuation.
    """
    ask = "BOT: Any allergies or dietary requirements I should note?\n"
    for reply in ("no", "nothing to flag", "no, nothing like that.", "nope, none at all",
                  "no allergies.", "none, thanks", "not really, no"):
        text = f"USER: book it\n{ask}USER: {reply}\n"
        found = _intake(text, {"allergies": ["shellfish"]})
        assert [f.kind for f in found] == ["denied_constraint"], reply


def test_a_disclosure_that_opens_with_no_is_not_a_denial():
    """The false positive that would matter most, because it inverts the finding.

    *"no, actually there is — I'm vegan"* opens on the same word and means the opposite. The
    page there is **correct**: the user contradicted the bot and stated the truth. Reading it
    as a denial would report a good page as the worst kind of broken one, so the denial has to
    close — end of turn, a sentence boundary, or a comma into thanks and nothing else.

    `no allergies, but I'm vegan` fails on that same rule that `no allergies.` passes, which is
    what makes naming a field in the tail safe.
    """
    ask = "BOT: Any allergies or dietary requirements I should note?\n"
    for reply in ("no, actually there is — I'm vegan",
                  "no allergies, but I'm vegan",
                  "no idea, what do you suggest?",
                  "nothing you can do about it, we're vegan and they never have anything"):
        text = f"USER: book it\n{ask}USER: {reply}\n"
        found = _intake(text, {"dietary": ["vegan"], "allergies": ["shellfish"]})
        assert [f.kind for f in found] == ["unbriefed_intake"], reply


def test_the_intake_bar_names_the_booking_moment():
    """All three asks that survived the first bar were the bot taking a reservation.

    `gen-001-s0` (*"note anything for the reservation"*), `gen-004-s8` (*"before I put it
    through"*) and one plain pre-suggestion screen were what remained of 33. The bar forbade
    screening and the author had stopped screening: noting a dietary requirement while booking
    reads as venue admin, so the rule did not appear to apply. The brief has to name the
    moment, because that is the moment the author does not classify as screening.
    """
    bar = "\n".join(transcript._INTAKE_BAR).lower()
    assert "booking" in bar and "reservation" in bar
    assert "put it through" in bar


def test_a_truncated_reply_is_transport_and_a_malformed_one_is_not():
    """`gen-002-s8` and `gen-004-s20`, lost from the 2026-08-18 reader pass.

    Both were cut mid-JSON at ~1000 characters and recorded as blocking `unreadable`, which
    §7.3 never retries. Both parsed on a plain retry, and the retries were *longer* than the
    cut — so no deterministic ceiling was being hit and the delivery is what failed. A reply
    that stopped mid-structure expresses no disposition, so re-rolling it cannot bias the pool
    toward whichever model complies, which is the whole reason content is never re-rolled.
    """
    good = '{"beats": [], "unscheduled": [], "contradictions": []}'
    assert verify.parse_verdict(good) == {
        "beats": [], "unscheduled": [], "contradictions": []
    }

    # Cut mid-string, exactly the shape both failures had.
    cut = '{"beats": [{"field_id": "dietary", "delivered": true, "quote": "no, nothing lik'
    with pytest.raises(judge.TransportError):
        verify.parse_verdict(cut)

    # Cut after a nested close, so it ends with '}'. Misclassified as malformed on purpose:
    # one lost call is the safe direction, a blind re-roll of a real answer is not.
    nested = '{"beats": [{"field_id": "dietary", "delivered": true}'
    with pytest.raises(ValueError):
        verify.parse_verdict(nested + "}")

    # Complete, closes, and still will not parse. Content, never retried.
    with pytest.raises(ValueError):
        verify.parse_verdict('{"beats": [,]}')

    # Prose that never opened an object is content however it ends. The opening brace is what
    # separates a cut-off reply from a model answering in the wrong format, and the latter is
    # the disposition §7.3 records rather than re-rolls.
    with pytest.raises(ValueError):
        verify.parse_verdict("I cannot answer that.}")
    with pytest.raises(ValueError):
        verify.parse_verdict("I could not find anything wrong with this conversation.")


def test_the_reader_retries_transport_and_never_retries_content():
    """The reader arm made exactly one call, so a truncated reply cost the session."""
    record = {"task_id": "t", "subject_id": "gen-000", "session": 0, "transcript": "USER: hi\n",
              "beats": []}
    item = {"subject_id": "gen-000", "card": {}, "sessions": [], "incidentals": []}

    calls = []

    def flaky(model, system, prompt):
        calls.append(model)
        if len(calls) < 3:
            return '{"beats": [{"field_id": "dietary", "quo'
        return '{"beats": [], "unscheduled": [], "contradictions": []}'

    verdict = verify.audit_session(flaky, "m", record, item, attempts=3)
    assert verdict["beats"] == []
    assert len(calls) == 3

    refusing = []

    def malformed(model, system, prompt):
        refusing.append(model)
        return '{"beats": [,]}'

    with pytest.raises(ValueError):
        verify.audit_session(malformed, "m", record, item, attempts=3)
    assert len(refusing) == 1, "content must never be re-rolled"


# --- locating a finding's turn, for the editor -------------------------------


def test_located_resolves_turn_index_from_a_real_quote():
    finding = verify.Finding(
        "rule:self_summary", verify.BLOCKING, "gen-000-s3",
        "quantified habit", quote="I always go for the same place",
    )
    records = {"gen-000-s3": _record(
        transcript="USER: hi\nBOT: hello\nUSER: I always go for the same place\n"
    )}
    located = verify._located(finding, records)
    assert located.locatable
    assert located.turn_index == 2 and located.speaker == "USER"
    # unrelated fields are untouched
    assert located.kind == finding.kind and located.detail == finding.detail


def test_located_leaves_an_unmatched_quote_unlocatable():
    """A model-arm quote the transcript never carried must not be guessed at."""
    finding = verify.Finding(
        "contradiction", verify.BLOCKING, "gen-000-s3", "eats pork",
        quote="a line the page never contains",
    )
    records = {"gen-000-s3": _record(transcript="USER: hi\nBOT: hello\n")}
    located = verify._located(finding, records)
    assert not located.locatable
    assert located is finding, "no quote match: return the finding unchanged, not a copy"


def test_located_leaves_a_quoteless_finding_unlocatable():
    """`undelivered`/`repeat_not_delivered` findings carry no quote — the beat is missing,
    not wrong, so there is nothing to splice and no turn to resolve."""
    finding = verify.Finding("undelivered", verify.BLOCKING, "gen-000-s3", "not on the page")
    located = verify._located(finding, {"gen-000-s3": _record()})
    assert not located.locatable


def test_inspect_populates_turn_index_on_a_real_defect(tmp_path: Path):
    corpus = dataset.build(n=1, seed=3)
    payload = corpus.to_json()
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps(payload))
    store = tmp_path / "store"
    store.mkdir()
    task_id = payload["items"][0]["sessions"][0]["task_id"]
    for session in payload["items"][0]["sessions"]:
        record = dict(session)
        record["transcript"] = (
            "USER: I always go for the same place\nBOT: noted\n"
            if session["task_id"] == task_id else "USER: hi\nBOT: hello\n"
        )
        (store / f"{session['task_id']}.json").write_text(json.dumps(record))

    report = verify.inspect(corpus_path, store)
    hit = next(f for f in report.findings if f.task_id == task_id
               and f.kind == "rule:self_summary")
    assert hit.locatable
    assert hit.turn_index == 0 and hit.speaker == "USER"


# --- v0.4 message numbers on findings ----------------------------------------


def _numbered_denial_record():
    return {
        "task_id": "gen-001-s1", "subject_id": "gen-001", "session": 1, "checkpoint": 1,
        "transcript": (
            "[0] USER: hi, table for four tonight\n"
            "[1] BOT: sure — before I pass the details on, any dietary requirements "
            "I should note?\n"
            "[2] USER: no, nothing like that.\n"
            "[3] BOT: booked.\n"
        ),
        "beats": [],
        "self_report": None,
    }


def _card_with_allergies():
    card = {fid: None for fid in fields.ALL_FIELDS_IDS}
    card["allergies"] = ["shellfish"]
    return {"subject_id": "gen-001", "card": card, "provisional": {}}


def test_denied_constraint_names_the_bot_ask_as_well_as_the_users_denial():
    """The defect is the *question*, not the answer.

    This session briefs no `allergies` beat, so the map calls the cell UNEVIDENCED — which
    means no rewrite of message 2 can be right: state the allergy and it is a `leak`, deny it
    and it is this finding. Only message 1 going away resolves it. The pilot's editor was
    handed message 2 alone and burned three attempts proving that (`runs/probe-edit-failures.json`).
    """
    record = _numbered_denial_record()
    found = verify._unbriefed_intake(
        "gen-001-s1", record["transcript"], record["beats"], _card_with_allergies()
    )
    denials = [f for f in found if f.kind == "denied_constraint"]
    assert len(denials) == 1
    assert denials[0].messages == (1, 2), "the ask and the answer, ask first"
    assert denials[0].targets == (1, 2)


def test_a_cited_message_beats_a_matched_quote_when_they_disagree():
    record = _numbered_denial_record()
    records = {"gen-001-s1": record}
    # The quote matches message 2; the finding says the defect is message 1.
    finding = verify.Finding(
        "denied_constraint", verify.BLOCKING, "gen-001-s1", "denies unbriefed",
        field_id="allergies", quote="no, nothing like that.", messages=(1, 2),
    )
    located = verify._located(finding, records)
    assert located.turn_index == 1 and located.speaker == "BOT"


def test_a_finding_with_only_a_quote_still_resolves_and_gains_a_message():
    record = _numbered_denial_record()
    finding = verify.Finding(
        "leak", verify.BLOCKING, "gen-001-s1", "readable here",
        field_id="allergies", quote="no, nothing like that.",
    )
    located = verify._located(finding, {"gen-001-s1": record})
    assert located.turn_index == 2
    assert located.messages == (2,)


def test_reader_message_numbers_are_validated_not_trusted():
    """A reply is model output. A dropped number costs a span; a kept bad one would point a
    repair at a message that does not exist."""
    assert verify._cited({"messages": [3, 1, 3, "2"]}) == (3, 1, 2)
    assert verify._cited({"messages": ["nope", -1, None]}) == ()
    assert verify._cited({}) == ()


# --- the reader is shown what a value means (ADR-0048) -----------------------

def test_the_audit_prompt_carries_the_level_anchors():
    """The pilot's most expensive single misreading. Shown only
    `deal_seeking = 'ignores_price'`, a reader took it to mean "has no budget ceiling" and
    rejected the one editor repair that had `gen-004-s8` right, for the offence of naming a
    per-head figure — on a field whose doc says "Effort spent finding a better price" and
    whose budget sibling is `price_band_distribution`. The anchors existed the whole time
    (§2.2 step 3) and were simply never put in front of the reader."""
    item = {"card": {"deal_seeking": "ignores_price"}}
    prompt = verify.audit_prompt(
        {"session": 0, "transcript": "[0] USER: hi", "beats": []}, item)
    assert "deal_seeking = 'ignores_price'" in prompt
    assert "Effort spent finding a better price" in prompt
    assert "checks for a discount once they have already chosen the place" in prompt


def test_the_audit_prompt_says_how_to_read_a_distribution():
    """One occasion in a bin is not a contradiction; a claim about the mode is. Without this
    the reader has to invent the rule, and in the pilot two different readers invented two
    different ones on the same session."""
    prompt = verify.audit_prompt(
        {"session": 0, "transcript": "[0] USER: hi", "beats": []},
        {"card": {"price_band_distribution": {"£30-50": 0.45, "£50+": 0.54}}},
    )
    assert fields.DISTRIBUTION_READING_RULE.splitlines()[0] in prompt


def test_every_field_has_a_gloss():
    """`card_block` renders all 24. A field whose doc is empty would silently ship a bare
    name back into the prompt, which is the state this ADR is undoing."""
    for field_id in fields.ALL_FIELDS_IDS:
        assert fields.gloss(field_id).strip(), field_id


def test_two_excerpts_split_out_of_one_quote_are_rejoined():
    """The 6-of-57 loss in the 2026-08-21 ADR-0052 reader pass, and why it is repaired.

    The reader is told to cite every message a defect depends on and to quote verbatim, and is
    never told `quote` is a single string, so a two-message finding comes back as two excerpts
    with the slash *outside* them. Both real failures are here verbatim. Neither is a
    disposition: `field_id`, `strength` and `messages` all parse, and `messages` is what a
    repair is applied to — only the separator of a free-text field is broken.

    The loss it caused was not random with respect to the measurement. A reply can only break
    this way when the reader files a multi-message finding, so the two sessions recovered here
    carried 7 and 5 `unscheduled` findings — leak-heavy pages, biasing blocking counts down.
    """
    split = (
        '{"beats": [], "unscheduled": [{"field_id": "solo_dining_comfort", '
        '"strength": "narrowed", "messages": [0, 2], '
        '"quote": "Just me." / "queue up for and take to a bench"}], "contradictions": []}'
    )
    payload = verify.parse_verdict(split)
    assert payload["unscheduled"][0]["messages"] == [0, 2]
    assert payload["unscheduled"][0]["quote"] == "Just me. / queue up for and take to a bench"

    # The other survivor of the pass, which broke in the same place with no closing period.
    other = (
        '{"beats": [], "unscheduled": [{"field_id": "event_types_liked", '
        '"strength": "narrowed", "messages": [7, 8], "quote": "a small gallery on the same '
        'street with a late opening tonight" / "the gallery, why not."}], '
        '"contradictions": []}'
    )
    assert verify.parse_verdict(other)["unscheduled"][0]["quote"].startswith("a small gallery")

    # A reply that already parses is never touched, and an embedded `/` is not this defect.
    intact = '{"beats": [], "unscheduled": [{"quote": "a / b"}], "contradictions": []}'
    assert verify.parse_verdict(intact)["unscheduled"][0]["quote"] == "a / b"

    # An escaped quote around a slash is inside one string and stays inside it.
    escaped = r'{"beats": [], "unscheduled": [{"quote": "he said \" / \" out loud"}], "contradictions": []}'
    assert verify.parse_verdict(escaped)["unscheduled"][0]["quote"] == 'he said " / " out loud'

    # Malformed some other way still raises: the repair is narrow, not a JSON fixer.
    with pytest.raises(ValueError):
        verify.parse_verdict('{"beats": [,]}')


def test_the_split_quote_repair_leaves_well_formed_replies_byte_identical():
    """`_join_split_quote` returns `None` unless its pattern is present.

    `parse_verdict` only reaches it after `json.loads` has already failed, so a well-formed
    reply cannot be rewritten — but the guard is asserted directly, because the pattern runs
    over arbitrary reader output and a false positive would silently edit a real verdict.
    """
    assert verify._join_split_quote('{"quote": "a / b", "n": 1}') is None
    assert verify._join_split_quote('{"a": "", "/": "b"}') is None      # a legal key, not a split
    assert verify._join_split_quote('{"a": "x / ", "b": "y"}') is None
    assert verify._join_split_quote('{"a": "x" / "y"}') == '{"a": "x / y"}'

    # A repair that does not parse surfaces the reply's own error, never the guess's. `"/"` is
    # a legal key the loose pattern reached; the tightened one does not, and this asserts the
    # outcome rather than the pattern, so it holds however the pattern is next rewritten.
    with pytest.raises(json.JSONDecodeError) as caught:
        verify.parse_verdict('{"a": "x" / "y", "b": }')
    assert 'line 1 column' in str(caught.value)


def test_a_narrowing_set_beat_guards_the_members_it_did_not_brief():
    """The partial-set hole: ADR-0036's bug on the value axis instead of the attribution one.

    A narrowing beat on a set field is briefed with a proper subset, because naming the whole
    list pins a field meant only to narrow. The guard used to key on the FIELD, so scheduling
    it switched protection off for the members the brief had just declined to name — leaving
    them unmentioned AND unguarded. `gen-001-s7` was briefed `latin_american`, was told nothing
    about `middle_eastern`, and wrote the user choosing a kebab place and committing to it.
    """
    card = {"cuisines_avoided": ["latin_american", "middle_eastern"]}
    beat = {
        "field_id": "cuisines_avoided", "move": "anecdote", "attributed": "user",
        "supports_gold": True, "gold_value": ["latin_american", "middle_eastern"],
    }
    assert transcript.briefed_subset(beat["gold_value"]) == ["latin_american"]
    assert transcript.withheld_members(beat) == ["middle_eastern"]

    # The brief names only the subset — unchanged, and the reason the hole exists.
    brief = transcript.beat_brief(beat)
    assert "latin_american" in brief and "middle_eastern" not in brief

    block = transcript.continuity_for(
        card=card, this_session=["cuisines_avoided"], this_session_own=["cuisines_avoided"],
        withheld={"cuisines_avoided": ["middle_eastern"]},
    )
    guard = " ".join(block["avoid"])
    assert "middle eastern" in guard, "the withheld member must still be guarded"
    assert "latin american" not in guard, "the briefed member is being written, not silenced"

    # Without the beat, the whole set is guarded — the pre-existing behaviour, unchanged.
    whole = transcript.continuity_for(card=card, this_session=[], this_session_own=[])
    assert "latin american" in " ".join(whole["avoid"])
    assert "middle eastern" in " ".join(whole["avoid"])

    # A beat that names the whole set leaves nothing to guard, which is the old skip, now
    # reached by exhausting the remainder rather than by dropping the field.
    full = transcript.continuity_for(
        card=card, this_session=["cuisines_avoided"], this_session_own=["cuisines_avoided"],
        withheld={"cuisines_avoided": []},
    )
    assert full["avoid"] == []


def test_the_withheld_remainder_is_silenced_and_never_stated_as_standing():
    """`standing` states a value; this is the session writing that field.

    Handing the author the withheld member as a true fact it may use is how a narrowing beat
    becomes a pinning one — the thing `briefed_subset` exists to prevent. So the remainder is
    guarded as silence even when the field is established, which is the one place this block
    departs from what an established field normally gets.
    """
    card = {"cuisines_avoided": ["vietnamese", "sushi"]}
    block = transcript.continuity_for(
        card=card, established=["cuisines_avoided"],
        this_session=["cuisines_avoided"], this_session_own=["cuisines_avoided"],
        withheld={"cuisines_avoided": ["sushi"]},
    )
    assert block["standing"] == []
    assert "sushi" in " ".join(block["avoid"])

    # Established and NOT written here still goes to standing, as before.
    settled = transcript.continuity_for(card=card, established=["cuisines_avoided"])
    assert "they will not go for" in " ".join(settled["standing"])
    assert settled["avoid"] == []


def test_withheld_members_is_empty_wherever_the_brief_names_the_whole_value():
    """The complement is only non-empty under the exact condition `beat_brief` narrows a set."""
    base = {
        "field_id": "cuisines_avoided", "move": "anecdote", "attributed": "user",
        "supports_gold": True, "gold_value": ["a", "b"],
    }
    assert transcript.withheld_members(base) == ["b"]
    assert transcript.withheld_members({**base, "attributed": "third_party"}) == []
    assert transcript.withheld_members({**base, "supports_gold": False}) == []
    assert transcript.withheld_members({**base, "move": "request"}) == []
    assert transcript.withheld_members({**base, "gold_value": ["a"]}) == []
    assert transcript.withheld_members({**base, "field_id": "spice_tolerance",
                                        "gold_value": "seeks_hottest"}) == []


def test_the_contradiction_probe_keeps_the_members_the_brief_did_not_name():
    """Skipping the whole field blinded the probe exactly where the brief was also blind.

    The same sentence `contradiction_probes` already carries about ADR-0036 and third-party
    beats, on the value axis: `gen-001-s7` briefed `latin_american`, the page chose middle
    eastern, and the probe that exists to catch that stayed silent because the field had a beat.
    """
    card = {"cuisines_avoided": ["latin_american", "middle_eastern"], "allergies": []}
    beats = [{
        "field_id": "cuisines_avoided", "move": "anecdote", "attributed": "user",
        "supports_gold": True, "gold_value": ["latin_american", "middle_eastern"],
    }]
    page = "USER: middle eastern sounds good, book it\nBOT: done\n"
    found = transcript.contradiction_probes(page, card, beats=beats)
    assert [p.kind for p in found] == ["contradiction_suspect"]
    assert "middle eastern" in found[0].detail

    # The briefed member is the scheduled evidence and must NOT be flagged — reporting it
    # would be reporting the dataset working, which is why the field was skipped to begin with.
    briefed = transcript.contradiction_probes(
        "USER: not the latin american place\nBOT: ok\n", card, beats=beats,
    )
    assert briefed == []


def test_a_group_disclosure_read_as_ambiguous_is_not_misattributed():
    """`gen-009-s3` / `-027-s0` / `-038-s5` / `-047-s0`, and the reason the distribution
    carve-out was too narrow.

    An ADR-0035 `allergies` disclosure answered for a group booking — "any allergies the
    kitchen should know about?", "no allergies, nothing like that", six of us — reads
    first-person-plural exactly as a shared occasion does, and it is not a distribution beat.
    `AUDIT_SYSTEM` defines AMBIGUOUS as the page not settling whose it is; that is the reader
    declining to say, not the value handed to a companion.
    """
    beat = {"field_id": "allergies", "move": "disclosure", "attributed": "user",
            "gold_value": []}
    verdict = {"beats": [{"field_id": "allergies", "delivered": True, "strength": "pinned",
                          "whose": "ambiguous", "moments": 1,
                          "quote": "no allergies, nothing like that"}],
               "unscheduled": [], "contradictions": []}
    assert not verify.reconcile(_record(beats=[beat]), _ITEM, verdict)


def test_a_reader_that_never_said_whose_it_is_does_not_accuse_anybody():
    """`whose` absent normalises to `ambiguous`, and silence is not a positive attribution.
    Four of the 1224 verdicts in the n50 two-model pass omit the field entirely."""
    beat = {"field_id": "allergies", "move": "disclosure", "attributed": "user",
            "gold_value": []}
    verdict = {"beats": [{"field_id": "allergies", "delivered": True, "strength": "pinned",
                          "moments": 1, "quote": "no allergies"}],
               "unscheduled": [], "contradictions": []}
    assert not verify.reconcile(_record(beats=[beat]), _ITEM, verdict)
