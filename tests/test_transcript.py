"""§6 authoring: the prompts, and the deterministic checks that bound them.

Every case here is drawn from the 50 sessions in `probe_sessions/runs/authored/` — the
positives are lines an author actually wrote and the §7.2 leakage audit correctly flagged,
and the negatives are lines an earlier draft of these rules flagged wrongly. A check like
this is a gate with an error rate pointed at the corpus, so its false positives are tested
as carefully as its catches.
"""

from __future__ import annotations

import pytest

from persona_twin import fields, transcript


def _session(*user_lines: str) -> str:
    turns = []
    for index, line in enumerate(user_lines):
        turns.append(f"USER: {line}")
        turns.append(f"BOT: Sure, there are a few places like that. Want me to list {index}?")
    return "\n".join(turns)


# --- what the check must catch ----------------------------------------------

@pytest.mark.parametrize("line", [
    "few days out is about as far ahead as I ever plan this stuff",
    "I usually check before I commit to somewhere",
    "honestly I usually shop around and compare prices before picking",
    "probably just get what I always get, not really feeling like gambling",
    "I'll have the chicken anyway, i always do",
])
def test_the_habit_statements_the_audit_flagged_are_caught(line):
    """The `reaction` move realized as a generalized habit statement — a third of the
    leakage flags in `probe_sessions/`, and the defect ADR-0009 sent back to the prompt."""
    problems = transcript.check_session(_session(line))
    assert [p.kind for p in problems] == ["self_summary"]


def test_a_distribution_enumerated_in_one_turn_is_caught():
    """§6.1's widest collision: the only way one turn conveys a shape over bins is to list
    them with relative weights, which is what an auditor calls a filled-in profile."""
    line = ("Mostly plan around weekends for me - Saturday evening is the big one, "
            "sometimes a weekend afternoon thing too, and every now and then a late night")
    problems = transcript.check_session(_session(line))
    assert [p.kind for p in problems] == ["distribution_enumeration"]
    assert "visit_time_distribution" in problems[0].detail


def test_one_bin_at_a_time_is_the_authorable_form():
    """The positive half of the same rule — this is what the brief now asks for, and it
    must not trip the check, or the technique and the gate contradict each other."""
    clean = _session(
        "Saturday evening if that works, around eight",
        "that one was fine actually, the noise was the problem",
    )
    assert transcript.check_session(clean) == []


# --- what the check must NOT catch ------------------------------------------

def test_the_scripted_self_report_is_exempt_when_declared():
    """The load-bearing case. `gen-003-s0` holds gold `deal_seeking: ignores_price` with a
    §4.2 self-report claiming the opposite; unexempted, this check flags the scripted claim
    and a retry loop regenerates the quota member until it stops being one. That is the
    third gate on this project to eat the construction it protects — see ADR-0005 and
    ADR-0007 for the first two — and the first caught before it was wired in.
    """
    scripted = "honestly I usually shop around and compare prices before picking somewhere"
    session = _session("just get the table", scripted)
    assert [p.kind for p in transcript.check_session(session)] == ["self_summary"]
    assert transcript.check_session(session, self_report=scripted) == []


def test_the_exemption_is_a_line_not_a_licence():
    """A declared self-report exempts its own line from its own rule. It does not exempt
    the rest of the session, or the other rules — otherwise every §4.2 subject would ship
    with an unaudited transcript attached to it."""
    scripted = "I'm actually pretty adventurous with food"
    session = _session(scripted, "I always order the same thing at that place")
    problems = transcript.check_session(session, self_report=scripted)
    assert [p.kind for p in problems] == ["self_summary"]
    assert problems[0].turn == 1


def test_a_reply_about_the_bots_suggestion_is_not_a_self_summary():
    """Regression: an earlier pattern matched `that's (how|what) I` and flagged "that's what
    I thought", which is the opposite of a self-summary — it is the situated reaction §6.1
    is trying to get."""
    assert transcript.check_session(_session("that's what I thought, it's the getting "
                                             "back that's the problem")) == []


def test_two_occasions_in_an_anecdote_are_not_a_filled_in_profile():
    """Regression: `just me` fired on "just me and Ruth", which is a party of two, and made
    a two-occasion anecdote look like an enumerated shape. A person telling a story about
    two evenings is exactly what §6.1 wants."""
    line = ("it's usually four or five of us for the quiz on wednesdays; one week it was "
            "just me and Ruth and we came second from bottom")
    assert transcript.check_session(_session(line)) == []


def test_the_bot_may_say_anything():
    """The bot is a prop (§6 rule 1). Only user turns carry the register rules, and a check
    that policed the bot would fail sessions for the wrong speaker."""
    session = "USER: sounds fine\nBOT: I always suggest booking ahead for that one."
    assert transcript.check_session(session) == []


# --- the brief ---------------------------------------------------------------

def test_a_distribution_beat_asks_for_one_bin_not_a_shape():
    """No `bin` key on the beat — the pre-ADR-0028 shape, still reachable for a `Beat` built
    before that field existed. Falls back to the heaviest bin, the old behaviour."""
    brief = transcript.beat_brief({
        "field_id": "visit_time_distribution", "move": "anecdote", "supports_gold": True,
        "gold_value": {"weekday-lunch": 0.1, "weekend-evening": 0.6, "late-night": 0.3},
        "note": "",
    })
    assert "ONE bin" in brief
    assert "'weekend-evening'" in brief


def test_a_distribution_beat_briefs_its_own_sampled_bin_not_the_mode():
    """ADR-0028 decision 5. A beat with a sampled `bin` must be briefed with THAT bin, even
    when it is not the heaviest — otherwise every beat still converges on the mode and
    nothing accumulates, which is the exact defect this field was added to fix."""
    brief = transcript.beat_brief({
        "field_id": "visit_time_distribution", "move": "anecdote", "supports_gold": True,
        "gold_value": {"weekday-lunch": 0.1, "weekend-evening": 0.6, "late-night": 0.3},
        "note": "", "bin": "weekday-lunch",
    })
    distribution_line = next(
        line for line in brief.splitlines() if line.strip().startswith("DISTRIBUTION FIELD")
    )
    assert "ONE bin" in distribution_line
    assert "'weekday-lunch'" in distribution_line
    assert "'weekend-evening'" not in distribution_line


# --- the ordinal interval brief is reverted (ADR-0012, reverted by ADR-0015) -

def test_a_narrowing_ordinal_beat_names_its_level_again():
    """ADR-0015: the interval brief is off the shipped path.

    Completing the 2x2 of {plain, interval} authoring x {commitment, licensed-set} question
    put the entire effect on the *question* — §11's earned condition moves ~+38 points on
    either text — while this brief moved it by noise and cost 25 points of reports-gold on
    exactly the fields it applies to (ordinal, 77% -> 52%, n = 44; set-valued and distribution
    fields unchanged). The over-pinning it was written to prevent was in the reading.
    """
    brief = transcript.beat_brief({
        "field_id": "noise_preference", "move": "anecdote", "supports_gold": True,
        "gold_value": "needs_quiet", "note": "",
    })
    assert "true value: 'needs_quiet'" in brief
    assert "do NOT pin this field" not in brief


def test_the_interval_brief_is_still_reachable_for_reproduction():
    """`probe_reauthor --variant interval` must stay re-authorable.

    Three ADRs and every table in that probe's README rest on text this brief produced. A
    revert that deletes the code makes them unreproducible, which would destroy the evidence
    for the revert itself — so the brief is switched off, not removed.
    """
    beat = {
        "field_id": "noise_preference", "move": "anecdote", "supports_gold": True,
        "gold_value": "needs_quiet", "note": "",
    }
    assert transcript.BRIEF_ORDINAL_INTERVAL is False, "the shipped default is off"
    original = transcript.BRIEF_ORDINAL_INTERVAL
    try:
        transcript.BRIEF_ORDINAL_INTERVAL = True
        brief = transcript.beat_brief(beat)
    finally:
        transcript.BRIEF_ORDINAL_INTERVAL = original
    assert "true value: " not in brief
    assert "needs_quiet, prefers_low_hum" in brief
    assert "do NOT pin this field" in brief


def test_the_set_narrowing_brief_is_kept():
    """Only the *ordinal* half of ADR-0012 is reverted.

    Split by metric family, the reports-gold damage sits entirely on the ordinals; set-valued
    fields read 100% under the licensed question both before and after. n = 4 there, which is
    far too little to justify removing a brief, and no evidence against it either way.
    """
    brief = transcript.beat_brief({
        "field_id": "cuisines_liked", "move": "anecdote", "supports_gold": True,
        "gold_value": ["korean", "thai", "peruvian"], "note": "",
    })
    assert "do NOT pin this field" in brief
    assert "leave the extent open" in brief
    assert "'peruvian'" not in brief, "a narrowing set beat shows a proper subset"


def test_a_pinning_beat_still_names_the_level():
    """The strong band is not the problem and must not be touched: §7.4's `must_strong`
    guard passed at 83%, and a schedule that cannot pin anything measures nothing."""
    brief = transcript.beat_brief({
        "field_id": "noise_preference", "move": "request", "supports_gold": True,
        "gold_value": "needs_quiet", "note": "",
    })
    assert "true value: 'needs_quiet'" in brief
    assert "do NOT pin" not in brief


def test_every_licensed_interval_contains_the_true_value_and_exactly_one_neighbour():
    """Two levels, not three: `WEAK_CREDIT` (§8.1) is priced on one remaining binary
    ambiguity, so a wider interval would credit abstention against a weaker constraint than
    the scorer assumes."""
    for field in fields.ALL_FIELDS:
        if not field.anchors:
            continue
        for value in field.levels:
            interval = transcript.licensed_interval(field, value)
            assert value in interval
            assert len(interval) == 2
            indices = sorted(field.levels.index(level) for level in interval)
            assert indices[1] - indices[0] == 1, "an interval must be adjacent levels"


def test_no_level_is_left_out_of_every_interval():
    """A level that never appears in a licensed interval would be pinnable only, so the weak
    band would carry a bias aligned with the value — the correlation §4.3 spends a quota
    keeping out of the dataset."""
    for field in fields.ALL_FIELDS:
        if not field.anchors:
            continue
        covered = {
            level for value in field.levels
            for level in transcript.licensed_interval(field, value)
        }
        assert covered == set(field.levels)


def test_the_reaction_definition_names_the_failure_it_keeps_being_written_as():
    """The old definition was satisfied by a habit statement, which is how it kept being
    written. A move definition that does not exclude the wrong realization is not a
    definition."""
    reaction = transcript.MOVE_DEFINITIONS["reaction"]
    assert "must not be a statement about what they do in general" in reaction


def test_the_distractor_brief_removes_the_decision_rather_than_forbidding_the_leak():
    """Cell 13's fix. The old brief said in capitals that the session carried no persona
    signal and the author wrote `travel_radius` into it anyway; what produces that is a user
    still deciding where to go. Every scenario takes the decision out of the session."""
    for index, scenario in enumerate(transcript.DISTRACTOR_SCENARIOS):
        brief = transcript.session_brief(
            session=index, beats=(), turns=10, is_distractor=True,
            distractor_scenario=scenario,
        )
        assert scenario in brief
        assert "not choosing where to go" in brief


def test_distractor_scenarios_are_spread_and_deterministic():
    picks = [transcript.distractor_scenario_for(f"gen-{i:03d}", 0) for i in range(36)]
    assert len(set(picks)) == len(transcript.DISTRACTOR_SCENARIOS)
    assert picks == [transcript.distractor_scenario_for(f"gen-{i:03d}", 0) for i in range(36)]


def test_an_ordinary_brief_widens_the_bots_suggestions_past_restaurants():
    """The three `events` fields were scheduled in proportion and reached the page anyway.

    16 of the pilot's 124 beats land on them — their exact share of the card — and yet 40 of
    57 pages carried no event vocabulary at all. The cause was the scenery: it says who is
    going, when, and roughly what it costs, and never what kind of outing this is, so a meal
    is the only thing left to write. Widening what the BOT offers changes the page without
    touching a cell of the map, because a suggestion the user does not act on is not evidence
    about the user. On the gen-000 trial this moved event vocabulary from 1 of 8 sessions to 5.
    """
    brief = transcript.session_brief(
        session=1, beats=({"field_id": "noise_preference", "move": "reaction",
                           "supports_gold": True, "note": "narrows",
                           "gold_value": "prefers_low_hum"},),
        turns=10,
    )
    assert "not only restaurants" in brief
    # The clause that keeps it free. Without it the author writes the user seeking out the gig,
    # which pins `event_types_liked` — the leak the beats exist to authorize. Asserted as the
    # guarantee rather than as one sentence, so the prose can be reworded without the test
    # silently stopping to test the rule.
    lowered = brief.lower()
    assert "the bot's to mention and not the user's to want" in lowered
    # Generalising this clause past the events fields was measured and reverted — it made the
    # corpus worse on every axis, `_VENUE_POOL`'s comment carries the table. The events scope
    # is therefore deliberate and is asserted, so a future widening has to face that record
    # rather than look like a tidy-up.
    assert "names an events field" in lowered


def test_a_distractor_is_not_offered_a_wider_venue_pool():
    """A distractor carries no decision at all, so there is nothing to suggest into.

    The brief returns before the pool block for the same reason it returns before the beats:
    a session whose whole job is to leave a reader knowing nothing must not be handed more
    material to be interesting with.
    """
    brief = transcript.session_brief(
        session=0, beats=(), turns=8, is_distractor=True,
        distractor_scenario=transcript.DISTRACTOR_SCENARIOS[0],
    )
    assert "not only restaurants" not in brief


def test_the_system_prompt_bans_self_generalization_explicitly():
    """§6.1's measured defect is not covered by "no trait vocabulary" — a habit statement
    uses none. The rule has to name the construction."""
    assert "NEVER GENERALIZES ABOUT THEMSELVES" in transcript.AUTHOR_SYSTEM
    assert "self_report" in transcript.AUTHOR_SYSTEM


def test_every_distribution_field_has_bin_surfaces():
    """A check that silently covers two of the three distribution fields would report a
    clean rate on a rule it is not enforcing."""
    jsd = {f.field_id for f in fields.ALL_FIELDS if f.metric == fields.METRIC_JSD}
    assert jsd == set(transcript.BIN_SURFACE)
    for field_id, bins in transcript.BIN_SURFACE.items():
        assert set(bins) == set(fields.BY_ID[field_id].levels)


# --- turn splitting ----------------------------------------------------------

def test_an_unlabelled_transcript_is_checked_rather_than_skipped():
    """A transcript with no speaker labels is malformed. Returning zero user turns would
    make it pass every rule silently, which is the failure mode `probe_sessions/`'s grade
    parser was written to avoid."""
    assert transcript.user_turns("I always book weeks ahead") != []
    assert transcript.check_session("I always book weeks ahead")


# --- disclosed absence (ADR-0035) --------------------------------------------


def test_a_disclosure_beat_makes_the_bot_ask():
    """The load-bearing half of ADR-0035's authoring shape.

    A preference can be volunteered; an absence essentially cannot. A user who spontaneously
    says "I don't have any allergies" is reciting a profile, which is what rule 3 bans and
    what the §7.2 leakage audit fails a transcript for. Asked, the same information is an
    ordinary answer to an ordinary question — so the brief has to put the question in the
    bot's mouth rather than carve out an exemption for the user.
    """
    brief = transcript.beat_brief({
        "field_id": "allergies", "move": "disclosure", "supports_gold": True,
        "gold_value": [], "note": "",
    })
    assert "DISCLOSED ABSENCE" in brief
    assert "BOT must ask first" in brief
    assert "must NOT raise the subject themselves" in brief
    assert "not as a form field" not in brief, (
        "the carried-constraint instruction is wrong here — there is no requirement to state"
    )


def test_a_carried_constraint_is_still_briefed_as_a_requirement():
    """The disclosure branch must not swallow the ordinary constraint instruction."""
    brief = transcript.beat_brief({
        "field_id": "allergies", "move": "request", "supports_gold": True,
        "gold_value": ["shellfish"], "note": "",
    })
    assert "hard requirement, not a preference" in brief
    assert "DISCLOSED ABSENCE" not in brief


def test_the_disclosure_move_is_defined_for_the_author():
    """`session_brief` lists every move it may name; an undefined one is briefed blind."""
    assert "disclosure" in transcript.MOVE_DEFINITIONS
    assert "THE BOT ASKS" in transcript.MOVE_DEFINITIONS["disclosure"]


@pytest.mark.parametrize("answer", [
    "No, nothing like that.",
    "No allergies, no. Anywhere's fine.",
    "Nope, we're all easy on that front.",
])
def test_a_plain_negative_answer_is_not_a_self_summary(answer):
    """The authored form of a disclosure beat must survive the check it runs under.

    If these tripped `SELF_SUMMARY_PATTERNS` the beat would need a declared-span exemption
    like §4.2's scripted self-report, and every disclosure session would burn a retry. They
    do not, because the banned construction is a *generalization about oneself* and an answer
    to a direct question is not one.
    """
    problems = transcript.check_session(f"USER: {answer}\nBOT: Got it.")
    assert not problems, f"{answer!r} flagged: {problems}"


# --- the disclosure beat: who asks, and about what ---------------------------
#
# ADR-0035 decision 3 was designed and unverified until `gen-000` was authored into a scratch
# store. The move worked three times out of three — the bot asked, the user never volunteered
# — and two other things were wrong, so both of them are pinned here.

_ALLERGY_BEAT = ({"field_id": "allergies", "move": "disclosure"},)


def test_the_bot_asking_first_is_what_a_disclosure_beat_looks_like():
    """The authored form the design wants, which must not be flagged.

    The reply used to read *"No, nothing like that."* here, and this test asserted it clean.
    It is the exact sentence `DISCLOSURE_PROBES["allergies"]` forbids and the one `gen-001-s1`
    shipped: an exemplar that teaches the defect is worse than no exemplar, because it is what
    a reader of the test suite copies. Both halves of the exchange now name the field.
    """
    session = (
        "USER: book me somewhere thursday\n"
        "BOT: Before I do — any allergies the kitchen should know about?\n"
        "USER: No allergies.\n"
        "BOT: Then it's booked."
    )
    assert transcript.check_session(session, beats=_ALLERGY_BEAT) == []
    assert transcript.probe_coverage(session, _ALLERGY_BEAT) == [("allergies", True)]


def test_the_user_asking_the_bots_question_is_caught():
    """`gen-000-s3`, verbatim in shape: the author put the question in the user's mouth and
    had the bot take it back. Two attempts, no rule fired, and it shipped. The content rules
    are about what a user turn *says*; this one is about which speaker gets there first."""
    session = (
        "USER: that's the one\n"
        "BOT: Booked.\n"
        "USER: one thing before I go — any allergies you should know about?\n"
        "BOT: Sorry, that's my question. Any allergies I should note?\n"
        "USER: No, nothing like that."
    )
    problems = transcript.check_session(session, beats=_ALLERGY_BEAT)
    assert [p.kind for p in problems] == ["disclosure_volunteered"]
    assert problems[0].turn == 1


def test_a_disclosure_the_user_volunteers_outright_is_caught():
    """The failure ADR-0035 actually worried about, and the same rule covers it: unasked, an
    absence announced by the user is profile recitation."""
    session = (
        "USER: somewhere thursday, and no allergies here so anything goes\n"
        "BOT: Noted."
    )
    assert [p.kind for p in transcript.check_session(session, beats=_ALLERGY_BEAT)] == [
        "disclosure_volunteered"
    ]


def test_a_disclosure_that_never_names_its_field_is_reported_not_gated():
    """The measured defect: `dietary` and `allergies` came back as the same question, so the
    derived map credits a cell the page cannot distinguish. It is reported rather than
    enforced — the markers are narrow on purpose, and a retry gate over a phrasing judgement
    is how a gate ends up eating the construction it was built to protect."""
    session = (
        "USER: book me somewhere\n"
        "BOT: Anything I should know about what you eat?\n"
        "USER: No, nothing like that."
    )
    kinds = {p.kind for p in transcript.check_session(session, beats=_ALLERGY_BEAT)}
    assert "disclosure_volunteered" not in kinds
    assert transcript.probe_coverage(session, _ALLERGY_BEAT) == [("allergies", False)]
    # What *is* gated here is the other half of the same sentence, and it is a separate rule:
    # the session briefed `allergies` and the question reaches all four constraints at once,
    # so three of them are being screened for unbriefed. `probe_coverage` still owns the
    # phrasing judgement — whether this ask settles `allergies` — and it still says no.
    assert kinds == {"unbriefed_intake"}


def test_every_hard_constraint_has_a_probe():
    """A disclosure beat can be scheduled for any of the four, and a field missing from the
    table degrades silently to the generic question that produced the defect."""
    for field in fields.HARD_CONSTRAINTS:
        assert field.field_id in transcript.DISCLOSURE_PROBES


def test_the_brief_tells_the_author_which_field_the_question_must_name():
    """The fix is in the brief, not only in the check — the check cannot make the author
    write a better question, it can only refuse a worse one."""
    beat = {"field_id": "allergies", "move": "disclosure", "gold_value": [],
            "supports_gold": True}
    brief = transcript.session_brief(session=2, beats=[beat], turns=8)
    assert "THE BOT'S QUESTION MUST NAME THIS FIELD" in brief
    assert "allergies" in brief


def test_the_brief_tells_the_author_the_answer_must_name_the_field_too():
    """The scope guard has to be on BOTH halves of the exchange, and it was on one.

    `gen-001-s1` is the case. The brief spent a paragraph forcing the bot's question to name
    `dietary`, the author complied, and then the same brief prescribed the reply verbatim as
    *'No, nothing like that.'* — unbounded. The card carries a shellfish allergy, both readers
    read the blanket denial as ruling it out, and the page states the negation of ground truth
    on a field nothing scheduled. The reader reads the user's half; the guard was on the bot's.
    """
    beat = {"field_id": "dietary", "move": "disclosure", "gold_value": [],
            "supports_gold": True}
    brief = transcript.session_brief(session=2, beats=[beat], turns=8)
    assert "AND SO MUST THE USER'S ANSWER" in brief
    assert "No, no particular diet" in brief
    # The prescribed blanket denial is what caused the defect; it must not be handed to the
    # author as the exemplar to copy.
    assert "unremarkable: 'No, nothing like that.'" not in brief


# --- the answer half of a disclosure -----------------------------------------
#
# `gen-001-s1`, and it is the second contradiction the 5x5 store was left holding
# (`runs/result-credit-pass.txt`). The brief has closed the scope of the bot's question since
# ADR-0043 and has asked in capitals for the answer to be closed too since ADR-0055. Nothing
# read the answer. These pin the check that does.


_DIETARY_BEAT = ({"field_id": "dietary", "move": "disclosure"},)


def _disclosed(answer: str) -> str:
    return (
        "[0] USER: Need somewhere for tonight, four of us.\n"
        "[1] BOT: Got it — does anyone follow a particular diet, vegetarian or halal?\n"
        f"[2] USER: {answer}\n"
        "[3] BOT: Fine. Sandbar Kitchen, then — four at seven."
    )


def test_the_page_that_shipped_the_defect_is_caught():
    """`gen-001-s1` message [2], verbatim. The card carries a shellfish allergy first
    disclosed two sessions later; both readers took the second sentence as ruling it out."""
    problems = transcript.check_session(
        _disclosed("No, no particular diet. Nothing on that front."), beats=_DIETARY_BEAT
    )
    assert [p.kind for p in problems] == ["blanket_denial"]
    assert "Nothing on that front" in problems[0].detail
    assert problems[0].turn == 1


def test_the_same_answer_without_the_second_sentence_is_clean():
    """The positive half, and the repair: the prescribed reply and nothing after it."""
    assert transcript.check_session(
        _disclosed("No, no particular diet."), beats=_DIETARY_BEAT
    ) == []


def test_a_sentence_that_names_the_field_is_scoped_however_it_denies():
    """`_ANSWER_ANCHORS` is wider than the question markers on purpose. *"No dietary
    restrictions"* carries none of `DISCLOSURE_PROBES["dietary"]`'s markers and is scoped
    anyway; a gate that fired on it would retry a page nothing is wrong with."""
    for answer in ("No dietary restrictions, no.", "None of us is vegetarian or anything."):
        assert transcript.check_session(_disclosed(answer), beats=_DIETARY_BEAT) == [], answer


def test_the_scoping_is_per_sentence_not_per_turn():
    """The reason the rule is sentence-scoped: the turn as a whole *does* name its field —
    "particular diet" is in it — so a turn-level check passes the defect. What a reader reads
    is the unbounded sentence, and the sentence before it does not bind it."""
    turn_level_pass = _disclosed("No, no particular diet. Nothing like that at all.")
    assert "particular diet" in turn_level_pass
    assert [p.kind for p in transcript.check_session(turn_level_pass, beats=_DIETARY_BEAT)] \
        == ["blanket_denial"]


def test_an_ordinary_denial_elsewhere_in_the_session_is_not_on_the_hook():
    """Scoped to the turn that answers the ask. Denial is ordinary English and a user saying
    *"nothing else, thanks"* while booking is not making a claim about a constraint field."""
    page = _disclosed("No, no particular diet.").replace(
        "[3] BOT: Fine. Sandbar Kitchen, then — four at seven.",
        "[3] BOT: Sandbar Kitchen, then. Anything else?\n"
        "[4] USER: No, nothing like that. Seven is fine.",
    )
    assert transcript.check_session(page, beats=_DIETARY_BEAT) == []


def test_a_probe_that_never_names_its_field_has_no_answer_to_read():
    """The under-probed case stays with `probe_coverage`, unenforced. There is no answering
    turn to find without a marker, and inferring one from adjacency would put a phrasing
    judgement inside the retry gate."""
    session = (
        "[0] USER: book me somewhere\n"
        "[1] BOT: Anything I should know about what you eat?\n"
        "[2] USER: No, nothing like that."
    )
    kinds = {p.kind for p in transcript.check_session(session, beats=_DIETARY_BEAT)}
    assert "blanket_denial" not in kinds
    assert transcript.probe_coverage(session, _DIETARY_BEAT) == [("dietary", False)]


def test_the_user_asking_first_is_one_defect_reported_by_one_rule():
    """`_disclosure_problems` owns the role slip. Gating the same page twice would make one
    defect cost two findings and read as two."""
    session = (
        "[0] USER: somewhere thursday — any particular diet stuff you need from me?\n"
        "[1] BOT: Sorry, that's my question. Any particular diet?\n"
        "[2] USER: No, nothing like that."
    )
    assert [p.kind for p in transcript.check_session(session, beats=_DIETARY_BEAT)] == [
        "disclosure_volunteered"
    ]


def test_every_probe_answer_exemplar_survives_the_gate_it_teaches():
    """One table, two readers — the exemplar the brief hands the author must be a reply the
    check would accept. The old `allergies` exemplar failed this and shipped anyway."""
    for field_id, probe in transcript.DISCLOSURE_PROBES.items():
        exemplar = probe[2].split("—")[0].strip().strip("'")
        beats = ({"field_id": field_id, "move": "disclosure"},)
        page = (
            "[0] USER: book me somewhere\n"
            f"[1] BOT: Before I do — {probe[1][0]}?\n"
            f"[2] USER: {exemplar}"
        )
        problems = [p for p in transcript.check_session(page, beats=beats)
                    if p.kind == "blanket_denial"]
        assert problems == [], (field_id, exemplar, problems)


def test_every_disclosure_probe_carries_an_answer_exemplar_as_well_as_a_question():
    """Question and answer ship from one entry so they cannot drift apart — the same
    'one table, two readers' rule the intake vocabulary is held to."""
    for field_id, probe in transcript.DISCLOSURE_PROBES.items():
        assert len(probe) == 3, field_id
        instruction, markers, exemplar = probe
        assert exemplar.strip(), field_id
        # The exemplar must itself satisfy the marker check it exists to teach.
        assert any(m in exemplar.lower() for m in markers), (field_id, exemplar)


def test_an_ordinary_mention_of_food_does_not_trip_the_dietary_probe():
    """The markers are narrow because a wide set fires on the construction. 'I never eat late
    on weeknights' is an ordinary user turn and `don't eat` would have caught it."""
    session = (
        "USER: nothing too late, I don't eat much after nine\n"
        "BOT: Do you follow any particular diet — vegetarian, halal, that sort of thing?\n"
        "USER: No, no particular diet."
    )
    beats = ({"field_id": "dietary", "move": "disclosure"},)
    assert transcript.check_session(session, beats=beats) == []
    assert transcript.probe_coverage(session, beats) == [("dietary", True)]


def test_the_umbrella_ask_is_not_exempted_by_the_beat_it_was_scheduled_for():
    """The rewording's whole point, and `gen-001-s1`'s defect at its source.

    *"Any dietary requirements?"* is the umbrella term in ordinary English: the same question
    screens allergies, and the blanket denial it invites settles both. A `dietary` disclosure
    beat used to exempt it, because the beat and the phrasing were matched on the field alone.
    They are not matched any more — `DISCLOSURE_PROBES` asks for a diet named by example, and
    this phrasing is what it forbids — so the beat no longer licenses it and the gate retries
    the page.
    """
    session = (
        "USER: nothing too late\n"
        "BOT: Any dietary requirements before I book?\n"
        "USER: No, nothing like that."
    )
    beats = ({"field_id": "dietary", "move": "disclosure"},)
    problems = transcript.check_session(session, beats=beats)
    # Both halves of the exchange are wrong and each has its own rule now: the umbrella
    # question is `unbriefed_intake`, the unbounded answer to it is `blanket_denial`.
    assert {p.kind for p in problems} == {"blanket_denial", "unbriefed_intake"}
    intake = next(p for p in problems if p.kind == "unbriefed_intake")
    assert "allergies" in intake.detail
    # and it names the field it *was* scheduled for too, because the ask did not close on it
    assert "dietary" in problems[0].detail


def test_the_role_slip_rule_is_blind_without_a_field_naming_question():
    """The coupling, pinned so it is not mistaken for general coverage.

    This is the *old* `gen-000-s3` as authored under the generic brief: the user asks the
    bot's question and the rule finds nothing, because "what you eat or drink" carries no
    marker. `probe_coverage` is what catches this one. The two fixes ship together and the
    check is only as good as the brief that feeds it.
    """
    session = (
        "USER: one thing before I go — is there anything I should know about what you eat "
        "or drink?\n"
        "BOT: Sorry, that's my question. Anything I should know before I send the details?\n"
        "USER: No, nothing like that."
    )
    beats = ({"field_id": "alcohol_free_required", "move": "disclosure"},)
    problems = transcript.check_session(session, beats=beats)
    assert [p.kind for p in problems if p.kind == "disclosure_volunteered"] == []
    assert transcript.probe_coverage(session, beats) == [("alcohol_free_required", False)]
    # The intake gate reaches this session where the role-slip rule cannot, and by a different
    # route: it does not care who spoke first, only that the bot put `dietary`, `allergies` and
    # `accessibility_needs` to a user whose session briefed none of them. Two rules, one
    # session, and neither is a superset of the other.
    assert [p.kind for p in problems] == ["unbriefed_intake"]


# --- the intake gate --------------------------------------------------------
#
# Every positive below is a bot turn an author actually wrote into the pilot, and every
# negative is a turn the first draft of this gate flagged wrongly. The gate was measured
# against all three pilot stores *before* it was wired into the retry loop, because a gate
# with an unmeasured error rate pointed at the corpus is how §4.4's coherence audit ate the
# anti-correlation quota. The measurement: 32 / 3 / 2 sessions flagged across the
# pre-intake-bar, intake-bar and booking-bar stores, and **zero** of the 30 sessions that
# carry a scheduled `disclosure` beat.


def test_the_booking_shaped_ask_two_passes_of_prose_did_not_stop_is_caught():
    """`gen-000-s0`, verbatim, from the store the booking clause produced.

    This is the session that decided the gate: `_INTAKE_BAR` names the reservation moment in
    so many words — no "anything I should note for the reservation" — and the author asked
    anyway, one turn before booking. Prose took the class 9 -> 2 -> 2 and stopped.
    """
    session = (
        "USER: counter's fine. book me one for saturday, half eight\n"
        "BOT: Before I put that through — do you have any allergies I should be aware of?\n"
        "USER: no, nothing like that\n"
        "BOT: Great. I've booked one counter seat at Pham's for Saturday at 8:30pm."
    )
    problems = transcript.check_session(session)
    assert [p.kind for p in problems] == ["unbriefed_intake"]
    assert "allergies" in problems[0].detail


def test_the_gate_does_not_consult_the_card():
    """`gen-004-s22`: the denial is *true* — that card's `accessibility_needs` is empty — and
    the session is still rejected.

    This is the whole reason the gate fires on the question. A truthful denial and a false one
    are the same defect in different clothes: one states the negation of ground truth, the
    other answers a cell the derived map calls UNEVIDENCED for the entire timeline, and a twin
    that reads the page correctly is penalised either way. A gate that consulted the card would
    pass both of the sessions it exists to reject.
    """
    session = (
        "USER: fine. soonest they'll take the two of us\n"
        "BOT: I'll request the earliest slot tonight at Hulland's. Before I do — is step-free "
        "access needed at the venue? It's a staircase up, no lift.\n"
        "USER: No, nothing like that."
    )
    assert [p.kind for p in transcript.check_session(session)] == ["unbriefed_intake"]


def test_a_scheduled_disclosure_is_not_eaten_by_the_gate():
    """The carve-out, per field and read off the beats.

    Measured, not assumed: across the three pilot stores, 30 sessions carry a scheduled
    `disclosure` beat and the gate fires on none of them. A gate that flagged these would
    retry the beat out of existence — which is exactly what ADR-0035's disclosure move exists
    to put *in*.
    """
    session = (
        "USER: book that for thursday\n"
        "BOT: Will do. Are there any allergies the kitchen should know about?\n"
        "USER: no, no allergies."
    )
    beats = ({"field_id": "allergies", "move": "disclosure"},)
    assert transcript.check_session(session, beats=beats) == []
    # and the carve-out is that field, not the session
    other = ({"field_id": "dietary", "move": "disclosure"},)
    assert [p.kind for p in transcript.check_session(session, beats=other)] == [
        "unbriefed_intake"
    ]


@pytest.mark.parametrize("line", [
    # `gen-001-s2` and `gen-002-s9`, both from the booking-bar store and both correct pages.
    "The Larch Room lists allergens per dish on their menu and will note it with the kitchen.",
    "Sun House doesn't serve alcohol — noodle soups, rice plates, mostly under a tenner.",
    "It's a dry-aged steak place, and the Foundry two doors down does cocktails until one.",
])
def test_a_venue_described_is_not_a_user_screened(line):
    """The false positives the first draft produced, and the reason the gate is sentence-scoped.

    A bare keyword match reads "lists allergens per dish" as an allergy question and "doesn't
    serve alcohol" as an alcohol question. Those were 2 of 5 fires on one store and 1 of 3 on
    another — a third of the gate's output would have been retries spent regenerating pages
    that were already right. The unit is the sentence because that is where the question mark
    lives.
    """
    session = f"USER: what's it like\nBOT: {line}\nUSER: fine, book it"
    assert transcript.check_session(session) == []


def test_an_invitation_without_a_question_mark_still_counts():
    """*"Let me know if there are any allergies"* forces the same invented answer as asking.

    The question mark is the common case and not the rule; what the gate is after is a turn
    that puts the subject to the user, and an imperative does that without punctuation.
    """
    session = (
        "USER: book it for four\n"
        "BOT: Booked. Let me know if there are any allergies to pass on.\n"
        "USER: no, nothing to flag"
    )
    assert [p.kind for p in transcript.check_session(session)] == ["unbriefed_intake"]


def test_the_gate_reports_one_problem_per_distinct_ask():
    """A repeated ask is one defect, not a retry budget spent on arithmetic.

    `gen-004` in the pre-intake-bar store asked the same alcohol-free question in ten
    consecutive sessions; within a session the author sometimes asks twice. The retry brief
    lists problems verbatim, so the same sentence twice reads as two rules broken.
    """
    session = (
        "USER: book it\n"
        "BOT: Any allergies I should pass on?\n"
        "USER: no\n"
        "BOT: One more thing — any allergies for the others?\n"
        "USER: no"
    )
    assert len(transcript.check_session(session)) == 1


# --- reading an occasion off the page (ADR-0043) ----------------------------

def _page(*turns: str) -> str:
    return "\n".join(turns)


def test_a_party_size_is_read_as_a_number_not_as_a_phrase():
    """The phrase layer needed every count written out twice to reach these. Measured on
    the pilot: party size went from 41 of 46 scheduled occasions to 46 of 46."""
    cases = {
        "USER: seven confirmed, could creep to eight": {"6+"},
        "USER: eight o'clock, nine people": {"6+"},
        "USER: put me down for one at half twelve": {"1"},
        "USER: four of us round a table": {"3-5"},
        "USER: need a table for two saturday evening": {"2"},
    }
    for page, expected in cases.items():
        names, _ = transcript.read_occasion("party_size_distribution", page)
        assert names == expected, page


def test_a_clock_time_is_never_read_as_a_head_count():
    """*"twelve thirty wednesday, just me"* is a party of one at 12:30, and the number
    that matters is the one next to a person-word."""
    names, _ = transcript.read_occasion(
        "party_size_distribution", "USER: twelve thirty wednesday, just me")
    assert names == {"1"}
    # And the limit that guard buys, stated rather than papered over: a head count that
    # trails a time with no person-word — *"book us for five-forty-five, two"* — is not
    # read at all. Every pilot session carrying one says it a second way ("two of us"),
    # which is why party size still reaches 46 of 46.
    names, _ = transcript.read_occasion(
        "party_size_distribution", "USER: book us for five-forty-five, two")
    assert names == frozenset()


def test_a_booking_toggle_is_not_a_party_of_one():
    """`gen-003-s4` is a distractor session about notification settings. *"the booking
    one — if I turn that off"* read as a party of 1 on a card giving that bin 0.00, which
    the audit reported as the page contradicting the card. Blocking, and wrong.

    Third false positive of this exact shape — after a bare "myself" in this same field and
    a bare "dry" in `alcohol_free_required` — which is why the guard is a lookbehind in the
    surface rather than a special case at the call site.
    """
    page = _page(
        "USER: got the first one off. the booking one — if I turn that off does it still "
        "actually book the thing, or does it stop doing anything",
        "USER: done. that should be the lot",
    )
    assert transcript.read_occasion("party_size_distribution", page)[0] == frozenset()


def test_a_visit_time_needs_a_daypart_and_a_day_class():
    from datetime import date
    saturday, tuesday = date(2025, 10, 18), date(2025, 12, 30)
    names, _ = transcript.read_occasion(
        "visit_time_distribution", "USER: anything decent going on tonight",
        on_date=saturday)
    assert names == {"weekend-evening"}
    names, _ = transcript.read_occasion(
        "visit_time_distribution", "USER: three of us wanting to eat tonight, and it'd be "
        "late, we've only just worked out we're free", on_date=tuesday)
    assert names == {"late-night"}
    # The case `_incidental`'s own docstring recorded as unfixable under a phrase list:
    # "lunchtime" is filed under `weekday-lunch` with nothing to check the day against.
    names, _ = transcript.read_occasion(
        "visit_time_distribution", "USER: saturday, around lunchtime", on_date=tuesday)
    assert names == {"weekend-day"}


def test_the_old_visit_time_phrases_are_all_still_read():
    """`visit_time_distribution` left `DELIVERY_SURFACE` when the structured reader took
    over, so control 1 of the prediction — no cell lost — is checked by hand here for the
    phrases the list used to carry. Friday is the one deliberate move: the list filed
    "friday night" under `weekend-evening` and `schedule.WEEKEND_BINS` does not, so the
    probe was disagreeing with the map it audits."""
    expected = {
        "weekday lunch": "weekday-lunch", "midweek lunch": "weekday-lunch",
        "weeknight": "weekday-evening", "after work": "weekday-evening",
        "tuesday night": "weekday-evening", "friday night": "weekday-evening",
        "saturday afternoon": "weekend-day", "sunday lunch": "weekend-day",
        "saturday evening": "weekend-evening", "sunday evening": "weekend-evening",
        "late night": "late-night", "after midnight": "late-night",
        "small hours": "late-night", "open late": "late-night",
    }
    for phrase, name in expected.items():
        names, _ = transcript.read_occasion("visit_time_distribution", f"USER: {phrase}")
        assert name in names, (phrase, sorted(names))


def test_the_calendar_is_only_consulted_when_the_page_says_today():
    """The session date is the conversation's day, not the outing's. Where the page names
    its own day the page wins, and where it names neither the probe stays ambiguous rather
    than borrowing a day class the text does not support."""
    from datetime import date
    saturday = date(2025, 10, 18)
    names, _ = transcript.read_occasion(
        "visit_time_distribution", "USER: a table for a Tuesday night, the 21st",
        on_date=saturday)
    assert names == {"weekday-evening"}
    names, _ = transcript.read_occasion(
        "visit_time_distribution", "USER: dinner sometime, eight o'clock suits")
    assert names == {"weekday-evening", "weekend-evening"}


def test_an_ambiguous_price_phrase_answers_with_both_bins():
    """`gen-003` says *"at the upper end"* for `£30-50` and for `£50+` alike. A probe that
    picks one invents a resolution the page does not carry, so the answer is the set and
    the corpus is measured to one bin at the top of the scale."""
    names, _ = transcript.read_occasion(
        "price_band_distribution", "USER: dinner for four, at the upper end")
    assert names == {"£30-50", "£50+"}


def test_price_is_read_from_both_speakers_and_the_user_alone_on_request():
    """The bot proposes a band and the user assents — *"Are you happy for me to look at the
    upper end?" / "yeah"* — so delivery is a property of the page. A contradiction of the
    card is a claim about the person, so `verify` re-reads the user's turns alone before
    blocking, and a bot describing a venue whose *"food reviews are middling"* is neither."""
    page = _page(
        "BOT: Are you happy for me to look at the upper end?",
        "USER: yeah, that's the one we spend on",
    )
    assert transcript.read_occasion("price_band_distribution", page)[0] == {"£30-50", "£50+"}
    assert transcript.read_occasion(
        "price_band_distribution", page, speakers="user")[0] == frozenset()
    venue = _page("BOT: Food reviews are middling; several mention the room instead.")
    assert transcript.read_occasion("price_band_distribution", venue)[0] == frozenset()


def test_widening_the_probe_never_loses_a_cell_it_already_had():
    """Control 1 of `runs/prediction-delivery-surface.txt`: the structured readers are
    unioned onto the phrase layer, never substituted for it, so every bin the old surface
    matched is still matched. A widening that drops a cell is a rewrite."""
    for field_id, bins in transcript.DELIVERY_SURFACE.items():
        for name, surfaces in bins.items():
            for surface in surfaces:
                if "\\" in surface:          # regex entries carry their own context
                    continue
                page = f"USER: {surface}"
                names, _ = transcript.read_occasion(field_id, page)
                assert name in names, (field_id, name, surface)


def test_two_moves_on_one_field_get_no_separation_clause():
    """The retraction. ADR-0043 briefed a per-field separation clause for two beats on one
    field with different moves, on a reader finding that three such sessions carried one
    moment where the map counted two. The finding was `verify.reconcile` keying the reader's
    beats into a dict by field and losing the first entry — the pages carried both moments,
    in two stores, across a full re-author. See `runs/result-repeat-brief.txt`.

    The clause is not neutral to leave in: it moved 30 briefs (three sessions plus every
    session downstream of them, since prior sessions feed each brief), and 1 of the 3
    re-authors it bought introduced a real contradiction against the card.
    """
    beats = [
        {"field_id": "formality_ceiling", "move": "reaction", "supports_gold": True,
         "bin": None, "attributed": "user", "gold_value": "counter_service"},
        {"field_id": "spice_tolerance", "move": "request", "supports_gold": True,
         "bin": None, "attributed": "user", "gold_value": "avoids_heat"},
        {"field_id": "formality_ceiling", "move": "rejection", "supports_gold": True,
         "bin": None, "attributed": "user", "gold_value": "counter_service"},
    ]
    assert "TWO SEPARATE MOMENTS" not in transcript.session_brief(
        session=6, beats=beats, turns=12)


def test_two_identical_beats_are_still_briefed_as_a_count():
    """What did NOT get retracted: `_group_beats` collapses two identical beats into one
    block, so the count has to be stated or the brief asks for one mention and the map
    charges two."""
    beat = {"field_id": "noise_preference", "move": "reaction", "supports_gold": True,
            "bin": None, "attributed": "user", "gold_value": "prefers_low_hum"}
    brief = transcript.session_brief(session=6, beats=[beat, dict(beat)], turns=12)
    assert "THIS HAPPENS 2 TIMES IN THIS CONVERSATION" in brief


# --- locating and splicing a turn (the editor's addressing) ------------------
#
# The editor needs a `Finding.quote` to resolve to an exact, patchable turn, and needs that
# resolution to fail loudly rather than guess — a wrong guess would splice the wrong turn.

_SESSION = (
    "USER: hi there\n"
    "BOT: are there any dietary requirements I should note?\n"
    "USER: no, nothing like that. also no allergies at all\n"
    "BOT: got it, anything else?\n"
)


def test_locate_quote_finds_an_exact_substring():
    loc = transcript.locate_quote(_SESSION, "no allergies at all")
    assert (loc.turn_index, loc.speaker) == (2, "USER")
    assert _SESSION[loc.start:loc.end] == "no, nothing like that. also no allergies at all"


def test_locate_quote_tolerates_whitespace_paraphrase():
    """A reader is told to quote verbatim, not to preserve spacing; a paraphrase of a line
    break or a doubled space is still the same turn."""
    loc = transcript.locate_quote(_SESSION, "no allergies   at\nall")
    assert loc is not None
    assert loc.turn_index == 2 and loc.speaker == "USER"


def test_locate_quote_splits_the_denied_constraint_pairing():
    """`verify._unbriefed_intake`'s `denied_constraint` quote joins the bot's ask and the
    user's reply as `"{ask} / {reply}"` — unfindable as one string by construction. The
    second half, the user's denial, is the half an edit would change."""
    quote = "are there any dietary requirements I should note? / no, nothing like that"
    loc = transcript.locate_quote(_SESSION, quote)
    assert loc is not None
    assert loc.turn_index == 2 and loc.speaker == "USER"


def test_locate_quote_returns_none_for_a_quote_the_page_never_carried():
    """A model-arm quote is free text, not a verified substring — a hallucinated or heavily
    paraphrased one must come back unlocatable rather than land on the wrong turn."""
    assert transcript.locate_quote(_SESSION, "the venue needs step-free access") is None
    assert transcript.locate_quote(_SESSION, "") is None


def test_splice_turn_replaces_only_the_located_turn():
    loc = transcript.locate_quote(_SESSION, "no allergies at all")
    patched = transcript.splice_turn(_SESSION, loc, "nothing dietary I need to flag")
    assert patched == (
        "USER: hi there\n"
        "BOT: are there any dietary requirements I should note?\n"
        "USER: nothing dietary I need to flag\n"
        "BOT: got it, anything else?\n"
    )
    # every other turn is byte-identical
    before, after = transcript.speaker_turns(_SESSION), transcript.speaker_turns(patched)
    assert before[0] == after[0] and before[1] == after[1] and before[3] == after[3]


# --- v0.4 message numbering --------------------------------------------------


_NUMBERED = (
    "[0] USER: anywhere still open for food?\n"
    "[1] BOT: a few places. how many of you?\n"
    "[2] USER: just me\n"
    "[3] BOT: there's a noodle bar two streets over.\n"
)


def test_a_message_number_is_its_turn_index():
    """The whole point of 0-basing: a number a reader cites needs no translation before an
    editor can splice at it."""
    numbers = transcript.message_numbers(_NUMBERED)
    assert numbers == [0, 1, 2, 3]
    for number, (speaker, _text) in zip(numbers, transcript.speaker_turns(_NUMBERED)):
        locator = transcript.locate_message(_NUMBERED, number)
        assert locator.turn_index == number
        assert locator.speaker == speaker


def test_well_formed_numbering_passes_and_the_three_ways_it_breaks_do_not():
    assert transcript.check_session(_NUMBERED, require_numbering=True) == []

    missing = _NUMBERED.replace("[0] ", "").replace("[1] ", "").replace(
        "[2] ", "").replace("[3] ", "")
    kinds = {p.kind for p in transcript.check_session(missing, require_numbering=True)}
    assert "unnumbered" in kinds

    partial = _NUMBERED.replace("[2] ", "")
    kinds = {p.kind for p in transcript.check_session(partial, require_numbering=True)}
    assert "numbering_partial" in kinds

    gapped = _NUMBERED.replace("[2] USER", "[5] USER")
    kinds = {p.kind for p in transcript.check_session(gapped, require_numbering=True)}
    assert "misnumbered" in kinds


def test_unnumbered_transcripts_stay_parseable_so_old_stores_can_still_be_read():
    """Tolerant reader, strict gate. Every pre-v0.4 store is unnumbered and must remain
    readable, or replaying today's rules over it — the cheapest audit here — stops working."""
    plain = "USER: hi\nBOT: hello\nUSER: bye"
    assert transcript.speaker_turns(plain) == [
        ("USER", "hi"), ("BOT", "hello"), ("USER", "bye")
    ]
    assert transcript.user_turns(plain) == ["hi", "bye"]
    assert transcript.check_session(plain) == []          # lax by default
    assert transcript.check_session(plain, require_numbering=True)


def test_splicing_several_messages_keeps_every_other_byte_and_every_number():
    edits = [
        (transcript.locate_message(_NUMBERED, 1), "how many of you?"),
        (transcript.locate_message(_NUMBERED, 3), "there's a place round the corner."),
    ]
    out = transcript.splice_turns(_NUMBERED, edits)
    assert transcript.message_numbers(out) == [0, 1, 2, 3]
    turns = transcript.speaker_turns(out)
    assert turns[1] == ("BOT", "how many of you?")
    assert turns[3] == ("BOT", "there's a place round the corner.")
    # untouched messages are byte-identical
    assert turns[0] == ("USER", "anywhere still open for food?")
    assert turns[2] == ("USER", "just me")
    assert transcript.check_session(out, require_numbering=True) == []


def test_splicing_a_longer_replacement_first_does_not_corrupt_a_later_one():
    """The reason `splice_turns` works backwards: every Locator holds absolute offsets into
    the string it was computed from, so rewriting an early message with text of a different
    length moves every span after it."""
    much_longer = "how many of you are there tonight, and whereabouts are you starting from?"
    edits = [
        (transcript.locate_message(_NUMBERED, 1), much_longer),
        (transcript.locate_message(_NUMBERED, 3), "there's a noodle bar."),
    ]
    out = transcript.splice_turns(_NUMBERED, edits)
    assert transcript.speaker_turns(out)[1] == ("BOT", much_longer)
    assert transcript.speaker_turns(out)[3] == ("BOT", "there's a noodle bar.")
    assert transcript.message_numbers(out) == [0, 1, 2, 3]


def test_deleting_a_message_renumbers_the_page_and_reports_the_remap():
    out, remap = transcript.apply_message_edits(
        _NUMBERED,
        [(transcript.locate_message(_NUMBERED, 1), None),
         (transcript.locate_message(_NUMBERED, 2), None)],
    )
    assert transcript.message_numbers(out) == [0, 1]
    assert transcript.speaker_turns(out) == [
        ("USER", "anywhere still open for food?"),
        ("BOT", "there's a noodle bar two streets over."),
    ]
    assert remap == {0: 0, 1: None, 2: None, 3: 1}


def test_a_replacement_only_edit_is_byte_preserving_and_the_remap_is_the_identity():
    """Deletion rebuilds the page; replacement must not, or every untouched message would be
    reflowed by a repair that never looked at it."""
    out, remap = transcript.apply_message_edits(
        _NUMBERED, [(transcript.locate_message(_NUMBERED, 2), "just me, actually")]
    )
    assert remap == {0: 0, 1: 1, 2: 2, 3: 3}
    assert out == _NUMBERED.replace("just me", "just me, actually")


# --- rating today against the norm (ADR-0048) -------------------------------

@pytest.mark.parametrize("line", [
    # Both halves of `gen-004-s8`, whose card puts 0.54 of spend at £50+ and 0.45 at
    # £30-50. The author framed £30-50 as a step up; the editor, repairing that, framed it
    # as the norm. Mirror images, both blocking contradictions to the reader, and neither
    # visible to any deterministic check before this one.
    "bit of a nicer one than normal, we're celebrating something small. "
    "thirty to fifty each is fine.",
    "thirty to fifty each, same as usual. we're celebrating something small so somewhere "
    "with a bit of care about it would be good.",
    # `gen-004-s14`, on a card whose single most common slot *is* weekday-lunch.
    "great, thanks. don't often get to do this midweek.",
    "it's a rare treat, this",
    "bit of a step up from what we'd go for on a weeknight",
    "more than we'd normally spend but it's worth it",
])
def test_rating_today_against_the_norm_is_caught(line):
    problems = transcript.check_session(_session(line))
    assert [p.kind for p in problems] == ["norm_comparison"]


def test_the_norm_rule_does_not_need_a_bin_to_fire():
    """The reason it catches anything at all. `BIN_SURFACE` carries `£3\\d` but not the
    words "thirty to fifty", so a version of this rule gated on a bin hit found **zero** of
    the three real norm claims in the 57-session pilot corpus — `gen-004-s8` included."""
    assert not any(
        surface in "thirty to fifty each, same as usual"
        for surface in transcript.BIN_SURFACE["price_band_distribution"]["£30-50"]
    )
    problems = transcript.check_session(_session("thirty to fifty each, same as usual"))
    assert [p.kind for p in problems] == ["norm_comparison"]


def test_the_usual_about_something_other_than_eating_is_not_caught():
    """`gen-002-s4`'s opening line, and the only false positive a bare `the usual` produced
    across 114 session-readings. It is about the app, not about how this person eats."""
    clean = _session("quick question, not the usual thing I use this for")
    assert transcript.check_session(clean) == []


def test_a_scripted_self_report_may_rate_today_against_the_norm():
    """Same exemption, same reason, as `test_the_scripted_self_report_is_exempt_when_declared`:
    a §4.2 self-report is the construction the gate exists to protect, not a defect in it."""
    line = "we usually spend about thirty to fifty each, same as usual really"
    assert transcript.check_session(_session(line))
    assert transcript.check_session(_session(line), self_report=line) == []


def test_a_single_occasion_named_without_a_norm_claim_is_clean():
    """The positive half: naming today's bin is exactly what the brief asks for."""
    clean = _session("thirty to fifty each is fine", "midweek lunch, two of us")
    assert transcript.check_session(clean) == []


# --- the offer template ------------------------------------------------------
#
# The five closed attributes are read off `runs/audit-0056.json`: 11 of 33 blocking findings
# are attributed to a bot turn, and nine of the eleven are a volunteered venue attribute the
# user then reacts to. The pages quoted below are those sessions.


def _offer(bot_line: str, *, user_line: str = "somewhere for Saturday, four of us") -> str:
    return f"[0] USER: {user_line}\n[1] BOT: {bot_line}\n[2] USER: that'll do"


@pytest.mark.parametrize("bot_line,field_id", [
    # gen-001-s6: "a bar running along one side and it's fairly busy late" -> noise leak.
    ("The Ledger has a bar along one side and it gets busy late.", "noise_preference"),
    # gen-003-s7: "Shared tables and a warehouse isn't it." -> design leak.
    ("It's a warehouse space with shared tables.", "design_sensitivity"),
    # gen-002-s9: "nothing to reserve, you just turn up and queue at the stall."
    ("Nothing to reserve — you turn up and queue at the stall.", "wait_tolerance"),
    # gen-001-s6: "a couple of the stalls do rotating specials" -> deal_seeking contradiction.
    ("A couple of the stalls do rotating specials.", "deal_seeking"),
    ("There's a quiz on from eight.", "event_types_liked"),
    # gen-004-s3: "an artichoke thing with chilli" -> spice_tolerance contradiction.
    ("Today there's a smoked tomato orecchiette and an artichoke thing with chilli.",
     "spice_tolerance"),
])
def test_the_six_attributes_an_offer_does_not_volunteer_are_caught(bot_line, field_id):
    problems = transcript.check_session(_offer(bot_line))
    assert [p.kind for p in problems] == ["offer_attribute"]
    assert field_id in problems[0].detail


def test_an_offer_that_names_only_its_slots_is_clean():
    """The positive half. Name, kind, one line of what it is, and how it answers what the
    user already said — which is the whole skeleton, and it is a page the bot can still work
    from. That matters: the failure of `venue-pool-general` was a bot left with nothing to
    offer on, which turned the session into an interview."""
    clean = _offer(
        "The Ledger is a wine bar on Mill Street — small plates, and they can do four at "
        "eight on Saturday."
    )
    assert transcript.check_session(clean) == []


def test_a_beat_on_the_field_exempts_it_because_the_beat_is_what_asked_for_it():
    """Same carve-out as `_screening_problems`, for the same reason: a session briefed on
    `wait_tolerance` is one where the queue is what the page is for."""
    page = _offer("Nothing to reserve — you turn up and queue at the stall.")
    assert transcript.check_session(page)
    beats = [{"field_id": "wait_tolerance", "move": "reaction"}]
    assert transcript.check_session(page, beats=beats) == []


def test_either_events_field_exempts_the_other_because_they_are_one_vocabulary():
    page = _offer("There's a quiz on from eight.")
    beats = [{"field_id": "event_types_avoided", "move": "rejection"}]
    assert transcript.check_session(page, beats=beats) == []


# The sixth attribute is not a room attribute, and the asymmetry it fixes is worth stating:
# heat is the one where DECLINING is as much evidence as accepting. On the other five a user
# can react to the volunteered attribute or ignore it. Once a chilli dish is on the menu the
# user must take it or pass it, and on a `seeks_hottest` card taking it leaks a field the map
# holds UNEVIDENCED while passing it contradicts the card. `gen-004-s3` is that page and the
# reader filed it against the user's turn, which is not where it could have been avoided.


def test_the_bot_may_name_the_dish_but_not_its_heat():
    """The line the sixth attribute draws. The food is what an offer turn carries — the
    skeleton says so and a menu the bot cannot describe is `venue-pool-general` again."""
    clean = _offer("Corradi does handmade pasta — there's a smoked tomato orecchiette on today.")
    assert transcript.check_session(clean) == []
    hot = _offer("Corradi does handmade pasta — the orecchiette comes with a kick of heat.")
    assert [p.kind for p in transcript.check_session(hot)] == ["offer_attribute"]


def test_hot_food_is_temperature_and_the_marker_knows_it():
    """`gen-001-s1` carries *"sandwiches and a couple of hot things"* on a food-hall counter.
    A marker that reads that as heat fires on the construction, so `hot` alone is not one."""
    assert transcript.check_session(
        _offer("Sandbar Kitchen is a food hall — sandwiches and a couple of hot things.")
    ) == []


def test_the_user_asking_for_heat_first_lets_the_bot_answer():
    """`OFFER_RAISED_EXTRA`, and the same direction of error the other five get: a bot that
    cannot answer the question it was just asked is the failure this block was built after."""
    assert transcript.check_session(
        _offer("The artichoke one has chilli in it, if that's what you're after.",
               user_line="somewhere for lunch — anything properly spicy?")
    ) == []


def test_a_declared_escape_passes_and_an_undeclared_one_does_not():
    """The `self_report` mechanism one level up, and the reason the escape rate is a number
    to read rather than one the audit discovers."""
    page = _offer("It's a warehouse space with shared tables.")
    assert [p.kind for p in transcript.check_session(page)] == ["offer_attribute"]
    assert transcript.check_session(page, free_offers=[1]) == []


def test_an_escape_pointing_at_a_turn_that_is_not_the_bot_is_itself_a_finding():
    """A declaration that points nowhere excuses nothing, and it is the shape a renumbering
    leaves behind — `editor._remapped_escapes` is what keeps a repair from producing one."""
    page = _offer("It's a warehouse space with shared tables.")
    kinds = [p.kind for p in transcript.check_session(page, free_offers=[2, 9])]
    assert kinds == ["offer_attribute", "escape_undeclarable", "escape_undeclarable"]


def test_the_user_may_still_say_all_five_because_the_bar_is_on_the_offer():
    """The whole difference from `venue-pool-general`, which closed the user's GROUNDS and
    made the corpus worse on every axis. The user's reaction is the dataset; only what the
    bot volunteers unasked is closed."""
    page = (
        "[0] USER: is it going to be a queue? I'd rather not stand about in a loud room\n"
        "[1] BOT: I can check what they say about booking.\n"
        "[2] USER: fine either way"
    )
    assert transcript.check_session(page) == []


def test_the_block_is_in_the_brief_and_not_in_a_distractor_brief():
    """A distractor has no decision in it for an offer to be made in, and keeping it out is
    what preserves `runs/result-venue-pool-general.txt`'s cache property: the 11 distractors
    return before the pool block and stay byte-identical across this change."""
    beats = [{"field_id": "noise_preference", "move": "reaction", "gold_value": "quiet"}]
    briefed = transcript.session_brief(session=0, beats=beats, turns=10)
    assert "CARRIES FOUR THINGS AND NOTHING ELSE" in briefed
    assert "free_offers" in briefed
    distractor = transcript.session_brief(
        session=0, beats=[], turns=10, is_distractor=True
    )
    assert "CARRIES FOUR THINGS AND NOTHING ELSE" not in distractor


def test_an_attribute_the_user_raised_first_is_not_the_offer_introducing_it():
    """Proposal point 4, as a rule: the free lane recombines and never introduces. A bot that
    cannot answer *"can I book it?"* is `venue-pool-general` all over again — a bot with
    nothing to work from asks, and an interview is where invented answers come from. On the
    57-session store this exemption is the difference between 24 and 22 sessions firing."""
    page = (
        "[0] USER: can I book a table for four on Saturday?\n"
        "[1] BOT: The Ledger doesn't take bookings — you just turn up.\n"
        "[2] USER: fine, we'll go early"
    )
    assert transcript.check_session(page) == []
    # The same bot line, with nothing from the user to recombine, is the finding.
    introduced = (
        "[0] USER: somewhere for four on Saturday\n"
        "[1] BOT: The Ledger doesn't take bookings — you just turn up.\n"
        "[2] USER: fine, we'll go early"
    )
    assert [p.kind for p in transcript.check_session(introduced)] == ["offer_attribute"]


def test_the_offer_slots_printed_as_a_form_are_caught():
    """11 of the first 14 sessions authored under `_OFFER_TEMPLATE` did this — the author
    transcribed the block's slot list instead of speaking it. The brief has said "Do not label
    anything" since v0 and it did not survive a block that looked like a form."""
    page = (
        "[0] USER: somewhere for four on Saturday\n"
        "[1] BOT: Here's one.\n\nNAME: Tarik's Grill House\nKIND: A restaurant with a counter.\n"
        "[2] USER: that'll do"
    )
    assert [p.kind for p in transcript.check_session(page)] == ["offer_labelled"]
    spoken = _offer("Tarik's Grill House is a grill with counter service on Mill Street.")
    assert transcript.check_session(spoken) == []


# --- v0.5: the author sees the person ----------------------------------------

#: A card the portrait can render every branch of: two set fields with members, two empty,
#: the boolean in both directions across the two tests, every ordinal, and all three
#: distributions — which are the ones that must not come out the other side.
_PORTRAIT_CARD: dict[str, object] = {
    "dietary": ["vegan"],
    "allergies": [],
    "accessibility_needs": [],
    "alcohol_free_required": False,
    "cuisines_liked": ["french", "deli"],
    "cuisines_avoided": ["cajun"],
    "spice_tolerance": "seeks_hottest",
    "adventurousness": "familiar_variations",
    "price_band_distribution": {"<£15": 0.62, "£15-30": 0.34, "£30-50": 0.04, "£50+": 0.0},
    "deal_seeking": "plans_around_specials",
    "noise_preference": "needs_quiet",
    "formality_ceiling": "counter_service",
    "design_sensitivity": "mild_preference",
    "party_size_distribution": {"1": 0.52, "2": 0.44, "3-5": 0.04, "6+": 0.0},
    "solo_dining_comfort": "enjoys_solo",
    "travel_radius": "own_neighborhood",
    "visit_time_distribution": {"weekday-lunch": 0.1, "weekday-evening": 0.1,
                                "weekend-day": 0.3, "weekend-evening": 0.4,
                                "late-night": 0.1},
    "planning_style": "decides_same_day",
    "wait_tolerance": "fifteen_min_max",
    "event_types_liked": ["theater"],
    "event_types_avoided": ["live_jazz"],
    "participation_vs_spectating": "joins_if_pushed",
    "rating_reliance": "ignores_ratings",
    "novelty_vs_loyalty": "almost_never_repeats",
}

_A_BEAT: dict[str, object] = {
    "field_id": "noise_preference", "move": "reaction", "supports_gold": True,
    "note": "narrows without pinning", "gold_value": "needs_quiet",
}


def test_the_portrait_never_prints_a_distribution():
    """The one part of the card whose disclosure would break a rule rather than risk one.

    `continuity_for` shows the *drawn* bin and never the shape, and says why: an author told
    "usually two, sometimes one" has been handed the sentence rule 3b forbids the user to
    say. A mass table is that sentence in tabular form. Asserted on the bins and on the
    masses, because either half is enough to write the banned line from.
    """
    text = "\n".join(transcript.persona_portrait(_PORTRAIT_CARD))
    for field_id in ("price_band_distribution", "party_size_distribution",
                     "visit_time_distribution"):
        assert field_id not in transcript.PORTRAIT_LABELS
        for bin_name in fields.BY_ID[field_id].levels:
            assert bin_name not in text
    assert "0.62" not in text and "0.44" not in text


def test_the_portrait_says_the_behaviour_and_never_the_level_name():
    """A brief that prints `noise_preference: needs_quiet` has handed the author the trait
    vocabulary rule 2 bans the *user* from reciting, and an author that has just read a scale
    name is the one most likely to write it back as dialogue. Every ordinal renders as its
    own behavioural anchor, which is already written in the register a person speaks in."""
    text = "\n".join(transcript.persona_portrait(_PORTRAIT_CARD))
    for spec in fields.ALL_FIELDS:
        if spec.metric != fields.METRIC_ORDINAL:
            continue
        assert spec.field_id not in text
        assert _PORTRAIT_CARD[spec.field_id] not in text
        assert spec.anchors[spec.levels.index(_PORTRAIT_CARD[spec.field_id])] in text


def test_the_portrait_covers_every_field_it_does_not_deliberately_withhold():
    """The registry is the source of truth for the card, so a field added there must either
    get a label or be a distribution. Without this, a new field reaches the corpus as a hole
    in the portrait that nothing reports."""
    labelled = set(transcript.PORTRAIT_LABELS)
    withheld = {f.field_id for f in fields.ALL_FIELDS if f.metric == fields.METRIC_JSD}
    assert labelled | withheld == set(fields.ALL_FIELDS_IDS)
    assert not labelled & withheld


def test_an_empty_set_field_reads_as_none_rather_than_as_absent():
    """`allergies: []` is a fact about the person — they have none — and it is the fact a
    `disclosure` beat exists to put on the record. Rendering it as a blank line would leave
    the author unable to tell it from a field the brief declined to mention."""
    text = "\n".join(transcript.persona_portrait(_PORTRAIT_CARD))
    assert "allergies:" in text
    assert "none" in text


def test_the_boolean_reads_as_a_fact_in_both_directions():
    """`False` printed as "false" is a value an author can read as "not established", which
    is the one reading that licenses inventing either answer."""
    off = "\n".join(transcript.persona_portrait(_PORTRAIT_CARD))
    assert "a bar on site is fine" in off
    on = "\n".join(transcript.persona_portrait(dict(_PORTRAIT_CARD,
                                                    alcohol_free_required=True)))
    assert "will not go anywhere that serves alcohol" in on


def test_the_idiosyncrasy_rides_along_when_the_card_has_one():
    """§8.5's free text is the most persona-ish thing on the card and the least scored —
    nothing in `scorer.py` reads it — so it is the cheapest texture an author can be given."""
    text = "\n".join(transcript.persona_portrait(_PORTRAIT_CARD, "orders the same starter"))
    assert "orders the same starter" in text
    assert "orders the same starter" not in "\n".join(
        transcript.persona_portrait(_PORTRAIT_CARD)
    )


def test_a_brief_with_a_card_fences_it_in_the_same_breath():
    """The portrait is paired with, and useless without, the fence. Seeing the card licenses
    nothing: the beats stay the whole of what may be evidenced, and the failure the fence
    names is the *apt* detail rather than the wrong one — an author that has just read "seeks
    the hottest thing on the menu" writes the user asking for extra chilli, which is in
    character, is not a contradiction, and settles a field nothing scheduled."""
    brief = transcript.session_brief(
        session=1, beats=(_A_BEAT,), turns=10, card=_PORTRAIT_CARD,
    )
    assert "=== WHO THIS PERSON IS ===" in brief
    assert brief.index("=== WHO THIS PERSON IS ===") < brief.index("FIELD noise_preference")
    lowered = brief.lower()
    assert "almost none of it may reach the page" in lowered
    assert "it is not a list of things to get in" in lowered
    # The whole point of the fence, asserted as the guarantee rather than as one sentence.
    assert "writing something true that no beat asked for" in lowered


def test_a_brief_without_a_card_is_byte_identical_to_the_v04_one():
    """The brief is fingerprinted, so a portrait that appeared unbidden would invalidate
    every cached transcript on disk. Both new parameters default to nothing and a corpus
    built before this tree briefs exactly as it did."""
    without = transcript.session_brief(session=1, beats=(_A_BEAT,), turns=10)
    assert "WHO THIS PERSON IS" not in without
    assert without == transcript.session_brief(
        session=1, beats=(_A_BEAT,), turns=10, card={}, idiosyncrasies="",
    )


def test_a_distractor_is_never_shown_the_card():
    """It schedules no beats, so the fence has nothing to fence *to*, and the block would be
    the whole card handed to the one session whose entire requirement is to leave a reader
    knowing nothing new. Same argument as `_VENUE_POOL`'s exclusion, one block over."""
    brief = transcript.session_brief(
        session=0, beats=(), turns=8, is_distractor=True,
        distractor_scenario=transcript.DISTRACTOR_SCENARIOS[0],
        card=_PORTRAIT_CARD, idiosyncrasies="orders the same starter",
    )
    assert "WHO THIS PERSON IS" not in brief
    assert "seeks the hottest thing on the menu" not in brief
    assert "orders the same starter" not in brief


def test_the_system_prompt_says_the_beats_are_the_whole_job():
    """Rule 4 was written for an author with no way to write anything else. Under a portrait
    it is the only rule holding the dataset up, so it names the beats as the complete list and
    rule 4b answers the move the card makes available: knowing a fact is not permission."""
    assert "THE BEATS ARE THE WHOLE JOB" in transcript.AUTHOR_SYSTEM
    assert "KNOWING SOMETHING IS NOT PERMISSION TO WRITE IT" in transcript.AUTHOR_SYSTEM


def test_the_bot_may_only_ask_about_a_field_a_beat_names():
    """v0.4 read "draw every one of them from the beats above OR FROM THE OUTING DESCRIBED IN
    THIS OUTING", and the second clause was a hole the size of the scenery: party size, hour,
    price and formality are four fields the bot could ask about in any session. Under a
    portrait that hole gets worse — an author that has read the card can answer any of the
    four in character, which is the leak the fence names arriving as an answer to a question
    that looked licensed."""
    bar = "\n".join(transcript._INTAKE_BAR)
    assert "EVERY QUESTION THE BOT ASKS IS ABOUT A FIELD A BEAT ABOVE NAMES" in bar
    assert "THIS OUTING" not in bar
    lowered = bar.lower()
    # The four the carve-out used to license, closed by name — the audit's `question_budget`
    # is the number that reads this and the ordinary ones are what it kept catching.
    for ordinary in ("not the area", "not the budget", "not the party size", "not the hour"):
        assert ordinary in lowered
