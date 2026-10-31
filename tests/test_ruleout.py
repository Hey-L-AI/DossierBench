"""§7.5's boundary protocol: the rule-out question and what its parser refuses to do.

The treatment exists because the pool disagrees about *width* and not about location — 76–81%
of priced-boundary grade disagreements are nested sets, replicated across two independent
samplings (preregistered in the unreleased development tree). Removing the width decision from the judge only
works if the parser derives it the same way every time, so the bias here is toward the rules
that would move a band silently: a rule-out honoured without its quote, a level dropped for a
spelling, a full sweep read as a commitment.

Model-free like the rest of the suite. `parse_ruleout` is the whole boundary.
"""

from __future__ import annotations

import json

import pytest

from persona_twin import fields, grading
from persona_twin.evidence import Grade

SPICE = fields.BY_ID["spice_tolerance"]            # ordinal, 4 levels
BINS = fields.BY_ID["visit_time_distribution"]      # jsd, 5 bins
BOOL = fields.BY_ID["alcohol_free_required"]        # exact, true/false
CUISINES = fields.BY_ID["cuisines_liked"]           # list-valued, 39 members


def out(*pairs):
    return {"out": [{"v": value, "q": quote} for value, quote in pairs]}


# --- the band is arithmetic on what survives ---------------------------------

def test_ruling_nothing_out_is_unevidenced():
    """Rule 4's expected answer and the overwhelming majority of cells. A fact the transcript
    never touches has every value standing, and a full vocabulary is not a finding."""
    entry = grading.parse_ruleout(SPICE, {"out": []})
    assert entry.grade == Grade.UNEVIDENCED
    assert entry.open == ()


def test_one_survivor_is_a_commitment():
    entry = grading.parse_ruleout(SPICE, out(
        ("avoids_heat", "asked for the hottest thing on the menu"),
        ("mild_only", "asked for the hottest thing on the menu"),
        ("likes_real_spice", "said the extra-hot was still too tame"),
    ))
    assert entry.grade == Grade.STRONG
    assert entry.value == "seeks_hottest"
    assert entry.open == ("seeks_hottest",)


def test_two_survivors_are_the_weak_band():
    """The band this whole protocol exists to make reachable without a disposition."""
    entry = grading.parse_ruleout(SPICE, out(
        ("avoids_heat", "ordered it spicy"), ("mild_only", "ordered it spicy"),
    ))
    assert entry.grade == Grade.WEAK
    assert entry.value is None
    assert entry.open == ("likes_real_spice", "seeks_hottest")


def test_the_survivors_keep_vocabulary_order():
    """`open` is read as an ordinal interval downstream. Emitting it in reply order would make
    an interval's endpoints depend on which level the judge happened to rule out first."""
    entry = grading.parse_ruleout(SPICE, out(("seeks_hottest", "hates heat")))
    assert entry.open == ("avoids_heat", "mild_only", "likes_real_spice")


def test_ruling_everything_out_is_unevidenced_not_a_commitment():
    """A judge that rules out its whole range has contradicted itself. The dangerous reading is
    an empty survivor set treated as a narrow one — `strong` on no value at all, which scores an
    SUT against a band nothing licenses."""
    entry = grading.parse_ruleout(SPICE, out(
        *((level, f"quote {i}") for i, level in enumerate(SPICE.levels))
    ))
    assert entry.grade == Grade.UNEVIDENCED
    assert entry.open == ()
    assert len(entry.ruled_out) == 4, "the contradiction is recorded, not erased"


# --- the quote is what makes a rule-out cost something -----------------------

def test_a_rule_out_with_no_quote_is_discarded_and_the_level_stays_live():
    """The instruction says "if you cannot quote something you cannot rule it out", and this
    project has measured prose failing on this exact pipeline twice. Discarding widens the
    surviving set, which is the safe direction: it moves the cell toward `weak` rather than
    manufacturing the commitment the protocol exists to stop being free."""
    entry = grading.parse_ruleout(SPICE, {"out": [
        {"v": "avoids_heat", "q": "said it was too mild"},
        {"v": "mild_only"},
        {"v": "likes_real_spice", "q": "   "},
    ]})
    assert entry.open == ("mild_only", "likes_real_spice", "seeks_hottest")
    assert entry.grade == Grade.WEAK
    assert entry.unquoted == ("mild_only", "likes_real_spice")
    assert [value for value, _q in entry.ruled_out] == ["avoids_heat"]


def test_a_bare_token_is_a_rule_out_with_no_quote():
    """Models emit `"out": ["mild_only"]` as readily as the object form. Reading it as
    unparseable would charge a formatting habit to the unparsed count, which `probe_tension/`
    measured manufacturing a per-model finding out of nothing; honouring it would make the quote
    decorative. It is read, and then discarded by the quote rule."""
    entry = grading.parse_ruleout(SPICE, {"out": ["mild_only"]})
    assert entry.unquoted == ("mild_only",)
    assert entry.grade == Grade.UNEVIDENCED, "nothing survived being ruled out"


def test_a_judge_that_quotes_nothing_grades_nothing():
    """The loud failure. A model ignoring the quote requirement wholesale produces a
    full-vocabulary reading on every field, which is `unevidenced` — visible in the published
    counts rather than silently inflating the strong band."""
    entry = grading.parse_ruleout(SPICE, {"out": [
        {"v": level} for level in SPICE.levels[:3]
    ]})
    assert entry.grade == Grade.UNEVIDENCED
    assert len(entry.unquoted) == 3


# --- a spelling must not move a band -----------------------------------------

def test_an_unreadable_value_invalidates_the_row():
    """§7.3's rule 2, unchanged. Dropping one unrecognized member of a rule-out list silently
    keeps a level live that the judge ruled out, which converts `strong` into `weak` out of a
    spelling — the same failure in the opposite direction to the licensed grader's."""
    with pytest.raises(grading.LicensedParseError):
        grading.parse_ruleout(SPICE, out(("quite_spicy", "some quote")))


def test_format_variance_is_not_a_different_answer():
    entry = grading.parse_ruleout(SPICE, out(("  Avoids-Heat ", "q")))
    assert [value for value, _q in entry.ruled_out] == ["avoids_heat"]


def test_a_repeated_rule_out_is_counted_once():
    entry = grading.parse_ruleout(SPICE, out(
        ("avoids_heat", "first"), ("avoids_heat", "second"),
    ))
    assert len(entry.ruled_out) == 1
    assert entry.open == ("mild_only", "likes_real_spice", "seeks_hottest")


def test_a_boolean_field_takes_the_same_arithmetic():
    entry = grading.parse_ruleout(BOOL, {"out": [{"v": False, "q": "needs somewhere dry"}]})
    assert entry.grade == Grade.STRONG
    assert entry.value == "true"


#: Distribution fields took a rule-out in the arm this file was first written for, and the
#: assertion that they did lived here. ADR-0021 removed that behaviour after measuring what it
#: cost — see the carve-out section at the end of this file for what replaced it.


def test_a_lone_rule_out_object_is_read_as_a_list_of_one():
    """Unambiguous, and the same normalization `parse_reply` already applies to a bare field
    array. Refusing it would charge a formatting habit to the unparsed count."""
    entry = grading.parse_ruleout(SPICE, {"out": {"v": "mild_only", "q": "asked for it hot"}})
    assert [value for value, _q in entry.ruled_out] == ["mild_only"]


def test_out_that_is_neither_a_list_nor_a_rule_out_raises():
    """No reading of a number as a set of rule-outs. Guessing one would put an arbitrary band
    on the record with `error: None` beside it."""
    with pytest.raises(grading.LicensedParseError):
        grading.parse_ruleout(SPICE, {"out": 5})


# --- list-valued fields keep the licensed shape ------------------------------

def test_a_list_valued_field_falls_through_to_the_licensed_parser():
    """39 members cannot be ruled out one at a time, so those fields get the routing
    *instruction* changed and the shape left alone. That keeps the 22% of the priced boundary
    they carry comparable across the two arms."""
    row = {"open": ["french"], "maybe": ["deli"]}
    assert grading.parse_ruleout(CUISINES, row) == grading.parse_licensed(CUISINES, row)


def test_the_routing_still_decides_the_band_on_list_valued_fields():
    """The second measured cause: 12 of 32 set-valued disagreements were judges reporting the
    identical member set and splitting it differently. The arithmetic is unchanged — what the
    treatment changes is the instruction that decides the split."""
    established = grading.parse_ruleout(CUISINES, {"open": ["deli", "french"], "maybe": []})
    split = grading.parse_ruleout(CUISINES, {"open": ["french"], "maybe": ["deli"]})
    assert established.grade == Grade.STRONG
    assert split.grade == Grade.WEAK
    assert set(established.reported) == set(split.reported), "identical members, opposite bands"


# --- the published quote guard -----------------------------------------------

def test_a_quote_that_occurs_in_the_transcript_verifies():
    entry = grading.parse_ruleout(SPICE, out(("avoids_heat", "the extra hot was still tame")))
    assert grading.verify_quotes(entry, "u: honestly the EXTRA HOT was still tame.") == (1, 1)


def test_a_quote_survives_wrapping_and_punctuation():
    """Models re-wrap and re-punctuate. A matcher strict about that would report invention
    where there is only formatting, and conflating the two hides the one that matters."""
    entry = grading.parse_ruleout(SPICE, out(("avoids_heat", "the  extra-hot,\nstill tame")))
    assert grading.verify_quotes(entry, "the extra hot still tame") == (1, 1)


def test_an_invented_quote_does_not_verify():
    entry = grading.parse_ruleout(SPICE, out(("avoids_heat", "I cannot handle chilli at all")))
    assert grading.verify_quotes(entry, "u: we went for pizza") == (0, 1)


def test_an_elided_quote_is_checked_on_its_longest_run():
    """"I want X ... not Y" is two claims the judge joined. Reassembling them and matching the
    pair would accept a join the transcript never made."""
    entry = grading.parse_ruleout(SPICE, out(("avoids_heat", "ordered it hot ... hated mild")))
    assert grading.verify_quotes(entry, "u: ordered it hot") == (1, 1)
    assert grading.verify_quotes(entry, "u: ordered it hot and hated mild") == (1, 1)


def test_a_field_with_no_rule_outs_has_nothing_to_verify():
    entry = grading.parse_ruleout(SPICE, {"out": []})
    assert grading.verify_quotes(entry, "anything") == (0, 0)


# --- the record round-trips, and the licensed record does not change ---------

def test_the_ruled_out_evidence_survives_the_record():
    """The quotes are the only evidence a rule-out was earned. A record that drops them makes
    the guard unrunnable after the fact, which is the state every other guard on this project
    had to be rescued from."""
    entry = grading.parse_ruleout(SPICE, out(("avoids_heat", "asked for it hot")))
    back = grading.LicensedEntry.from_json("spice_tolerance", entry.to_json())
    assert back == entry


def test_a_licensed_record_gains_no_keys():
    """`test_replay.py` asserts all 598 cached gradings come back byte for byte. A new key on
    every row would break that for a field the licensed question never populates."""
    entry = grading.parse_licensed(SPICE, {"open": ["mild_only"], "why": "x"})
    assert set(entry.to_json()) == {"grade", "value", "open", "maybe", "why"}


# --- off-protocol keys are dropped, and now counted ---------------------------
#
# Both system prompts restrict `maybe` to LIST-VALUED fields — `LICENSED_SYSTEM` says "leave it
# out everywhere else", `RULEOUT_SYSTEM` says "`open` alone for the facts marked DISTRIBUTION".
# Judges populate it there anyway, on up to 32% of distribution rows for one published-pool
# judge. The parser has always dropped it and must keep dropping it: reading a key the prompt
# forbade would answer a question nobody asked, and `open ∪ maybe` is the whole vocabulary often
# enough that "promote to weak" would move cells the wrong way. What changes is that the drop
# leaves a trace.

def test_a_stray_maybe_on_a_distribution_field_is_dropped_but_recorded():
    """The behaviour that went unseen through four ADRs. The grade is what it always was."""
    entry = grading.parse_licensed(BINS, {"open": ["late-night"], "maybe": ["weekday-lunch"]})
    assert entry.grade == Grade.STRONG          # unchanged — `open` has one member
    assert entry.maybe == ()                    # still dropped
    assert entry.off_protocol == ("maybe",)     # and no longer silently


def test_a_maybe_on_a_list_valued_field_is_protocol():
    """The one shape that licenses it. Counting this would make the column meaningless."""
    entry = grading.parse_licensed(CUISINES, {"open": ["french"], "maybe": ["deli"]})
    assert entry.grade == Grade.WEAK
    assert entry.off_protocol == ()


def test_an_empty_stray_key_is_not_counted():
    """An empty `maybe: []` costs the reading nothing. Counting it would drown the signal in
    rows where the judge merely echoed the output shape."""
    entry = grading.parse_licensed(BINS, {"open": ["late-night"], "maybe": []})
    assert entry.off_protocol == ()


def test_a_ruleout_field_answered_in_the_licensed_shape_is_counted():
    """The other direction of the same confusion, and the one ADR-0023 saw as a parse failure."""
    entry = grading.parse_ruleout(SPICE, {"out": [], "open": ["mild_only"]})
    assert entry.off_protocol == ("open",)


def test_the_off_protocol_record_round_trips():
    entry = grading.parse_licensed(BINS, {"open": ["late-night"], "maybe": ["weekday-lunch"]})
    assert grading.LicensedEntry.from_json(BINS.field_id, entry.to_json()) == entry


def test_every_question_stays_runnable():
    """ADR-0015's precedent: the interval brief was switched off rather than deleted because
    three ADRs rested on text it produced. Every published figure on this project was graded by
    `LICENSED`, and both boundary arms were graded by `RULEOUT`."""
    assert grading.LICENSED.system != grading.RULEOUT.system
    assert set(grading.GRADERS) == {"licensed", "ruleout", "ruleout-split"}
    assert grading.GRADERS["licensed"].parse_row is grading.parse_licensed


def test_the_ruleout_prompt_shows_the_range_and_never_the_bands():
    """§7.3's structural rule, carried over: a judge told a two-survivor answer scores `weak` is
    selecting a band by another route, which is the disposition being removed."""
    prompt = grading.ruleout_prompt("u: hello", 0)
    assert "avoids_heat, mild_only, likes_real_spice, seeks_hottest" in prompt
    for band in ("strong", "weak", "unevidenced"):
        assert band not in prompt.lower()
    assert all(f.field_id in prompt for f in fields.ALL_FIELDS)


def test_a_whole_ruleout_reply_reads_end_to_end():
    raw = json.dumps({"fields": [
        {"f": "spice_tolerance", "out": [{"v": "avoids_heat", "q": "asked for it hot"}]},
        {"f": "adventurousness", "out": []},
        {"f": "cuisines_liked", "open": ["french"], "maybe": ["deli"]},
    ]})
    reply = grading.parse_reply(raw, grading.RULEOUT)
    assert reply.entries["spice_tolerance"].grade == Grade.WEAK
    assert reply.entries["adventurousness"].grade == Grade.UNEVIDENCED
    assert reply.entries["cuisines_liked"].grade == Grade.WEAK
    assert not reply.unparsed and reply.error is None
    assert len(reply.missing) == len(fields.ALL_FIELDS) - 3


# --- ADR-0021's carve-out: a superlative is not a claim a quote can contradict ---

def test_a_distribution_field_no_longer_takes_a_rule_out():
    """Measured, not foreseen. Under the rule-out framing judges ruled out every bin in turn and
    45 of 48 priced-boundary JSD cells came back EMPTY against 18% under the licensed question —
    three judges emptying a field all agree it says nothing, which inflated the arm's κ from
    +0.42/+0.37 to +0.55/+0.52 (ADR-0021)."""
    row = {"open": ["weekend-evening", "weekend-day"]}
    assert grading.parse_ruleout(BINS, row) == grading.parse_licensed(BINS, row)


def test_a_distribution_field_ignores_rule_outs_if_a_judge_sends_them():
    """The prompt no longer asks for them, but a judge that carries the habit over must not be
    able to empty the field through a key the parser still reads. The failure this carve-out
    exists to stop has to be unreachable, not merely unasked-for."""
    entry = grading.parse_ruleout(BINS, {
        "out": [{"v": level, "q": "never mentioned"} for level in BINS.levels],
    })
    assert entry.grade == Grade.UNEVIDENCED
    assert entry.ruled_out == (), "a rule-out on a distribution field is not honoured"


def test_the_two_carve_outs_rest_on_different_grounds():
    """Set-valued fields are carved out on size, distribution fields on kind. Both are
    exclusions from the rule-out and neither is an exclusion from the benchmark."""
    assert fields.METRIC_JACCARD in grading.LICENSED_SHAPE_METRICS
    assert fields.METRIC_JSD in grading.LICENSED_SHAPE_METRICS
    assert fields.METRIC_ORDINAL not in grading.LICENSED_SHAPE_METRICS
    assert fields.METRIC_EXACT not in grading.LICENSED_SHAPE_METRICS


def test_the_prompt_marks_both_carve_outs_and_asks_neither_for_a_rule_out():
    prompt = grading.ruleout_prompt("u: hello", 0)
    for spec in fields.ALL_FIELDS:
        line = next(ln for ln in prompt.splitlines() if ln.strip().startswith(spec.field_id))
        if spec.metric == fields.METRIC_JSD:
            assert "DISTRIBUTION" in line
        elif spec.metric == fields.METRIC_JACCARD:
            assert "LIST-VALUED" in line
        else:
            assert "full range" in line


def test_an_ordinal_still_takes_a_rule_out_after_the_carve_out():
    """The carve-out must not quietly widen. Ordinals are 61% of the priced boundary and are
    where the treatment's whole effect was measured."""
    entry = grading.parse_ruleout(SPICE, out(("avoids_heat", "asked for it hot")))
    assert entry.ruled_out and entry.open == ("mild_only", "likes_real_spice", "seeks_hottest")
