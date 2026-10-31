"""§7.3's licensed-set grader: the parser, and the arithmetic that replaces the grade.

ADR-0013 moves the band off the judge's answer and onto the *size* of the judge's answer, so
the two things that can now go wrong are both here. A member dropped in parsing shrinks a set,
and a shrunk set is a stronger grade — the exact over-commitment this grader exists to measure,
manufactured out of a spelling. And a prompt that leaks the band vocabulary hands the judge its
grade back, which is the disposition the change exists to remove.

Every parse case is a shape a judge in `probe_sessions/runs/` or `probe_reauthor/runs*/`
actually produced under the commitment grader, re-pointed at the new keys.

**Pointed at the shipped module since step 6.** The probe runners keep their own copy — they
are the record of what was executed, and editing them would edit the artifact that produced
the measurements — so what guarantees the two are the same code is not an import but
`test_replay.py`, which re-parses all 144 cached replies through this module and asserts the
records come back byte for byte.
"""

from __future__ import annotations

import pytest

from persona_twin import fields, grading as sessions


ORDINAL = fields.BY_ID["spice_tolerance"]        # avoids_heat .. seeks_hottest
SET_VALUED = fields.BY_ID["cuisines_liked"]
BOOLEAN = fields.BY_ID["alcohol_free_required"]
DISTRIBUTION = fields.BY_ID["visit_time_distribution"]


def parse(field, **row):
    """`parse_licensed` returns a `LicensedEntry`; the cases below were written against the
    dict the probe produced, so they read it through the same five keys."""
    entry = sessions.parse_licensed(field, row)
    return {
        "grade": entry.grade.name.lower(), "value": entry.value,
        "open": list(entry.open), "maybe": list(entry.maybe), "why": entry.why,
    }


# --- the derivation: the band is the size of the set -------------------------

def test_one_value_is_strong():
    got = parse(ORDINAL, open=["seeks_hottest"])
    assert got["grade"] == "strong"
    assert got["value"] == "seeks_hottest"


def test_two_values_is_weak_and_commits_to_neither():
    got = parse(ORDINAL, open=["likes_real_spice", "seeks_hottest"])
    assert got["grade"] == "weak"
    assert got["value"] is None
    assert got["open"] == ["likes_real_spice", "seeks_hottest"]


def test_empty_is_unevidenced():
    assert parse(ORDINAL, open=[])["grade"] == "unevidenced"
    assert parse(ORDINAL)["grade"] == "unevidenced"


def test_three_values_is_still_weak():
    got = parse(ORDINAL, open=["mild_only", "likes_real_spice", "seeks_hottest"])
    assert got["grade"] == "weak"


def test_the_whole_vocabulary_is_unevidenced_not_weak():
    """A judge that lists every level has narrowed nothing.

    Both shapes for "this transcript says nothing" appear in replies — the empty list the
    prompt asks for, and the full vocabulary. Reading the second as `weak` would credit the
    grader with finding an ambiguity it explicitly declined to find, and would inflate the one
    number this whole run exists to measure.
    """
    got = parse(ORDINAL, open=list(ORDINAL.levels))
    assert got["grade"] == "unevidenced"
    assert got["open"] == []


def test_the_whole_vocabulary_is_a_real_claim_on_a_set_field():
    got = parse(SET_VALUED, open=list(SET_VALUED.levels))
    assert got["grade"] == "strong"
    assert len(got["value"]) == len(SET_VALUED.levels)


# --- set-valued fields: one unsettled member leaves the field unsettled ------

def test_set_field_established_only_is_strong():
    got = parse(SET_VALUED, open=["korean", "thai"])
    assert got["grade"] == "strong"
    assert got["value"] == ["korean", "thai"]


def test_set_field_with_a_maybe_is_weak():
    got = parse(SET_VALUED, open=["korean"], maybe=["thai"])
    assert got["grade"] == "weak"
    assert got["value"] is None


def test_set_field_maybe_only_is_weak():
    assert parse(SET_VALUED, open=[], maybe=["thai"])["grade"] == "weak"


def test_set_field_empty_is_unevidenced():
    assert parse(SET_VALUED, open=[], maybe=[])["grade"] == "unevidenced"


def test_maybe_is_ignored_off_set_valued_fields():
    """`maybe` has no meaning on an ordinal — the ambiguity is already the set itself.

    Honouring it there would let a judge report a one-value `open` plus a `maybe` and land on
    `weak` through a key the prompt tells it not to use, which is a second route to the grade.
    """
    got = parse(ORDINAL, open=["seeks_hottest"], maybe=["likes_real_spice"])
    assert got["grade"] == "strong"
    assert got["maybe"] == []


# --- vocabulary: format variance is not a different answer -------------------

@pytest.mark.parametrize("raw", ["Seeks_Hottest", " seeks_hottest ", "seeks-hottest",
                                 "seeks hottest", "SEEKS_HOTTEST"])
def test_member_normalization(raw):
    assert parse(ORDINAL, open=[raw])["value"] == "seeks_hottest"


def test_members_are_deduplicated_before_the_size_is_taken():
    got = parse(ORDINAL, open=["seeks_hottest", "Seeks_Hottest"])
    assert got["grade"] == "strong", "a repeat is one value, not two"


def test_a_bare_value_is_read_as_a_one_element_set():
    assert parse(ORDINAL, open="seeks_hottest")["grade"] == "strong"


def test_boolean_field_takes_true_false():
    assert parse(BOOLEAN, open=["true"])["value"] == "true"
    assert parse(BOOLEAN, open=[True])["value"] == "true"
    assert parse(BOOLEAN, open=[True, False])["grade"] == "unevidenced", "both = no narrowing"


def test_distribution_field_takes_candidate_bins():
    got = parse(DISTRIBUTION, open=["weekend-evening", "late-night"])
    assert got["grade"] == "weak"


# --- unreadable rows are counted, never repaired -----------------------------

def test_unknown_member_invalidates_the_row():
    """Dropping it would turn this `weak` into a `strong`."""
    with pytest.raises(sessions.LicensedParseError):
        parse(ORDINAL, open=["likes_real_spice", "enjoys_moderate_warmth"])


def test_bare_index_raises():
    """`parse_grade`'s reason: nothing in the reply says whether it is 0- or 1-based."""
    with pytest.raises(sessions.LicensedParseError):
        parse(ORDINAL, open=[3])


def test_a_digit_that_is_a_real_token_is_not_an_index():
    """`party_size_distribution`'s bins are `1`, `2`, `3-5`, `6+`.

    The first licensed run guarded against bare indices *before* the vocabulary lookup and lost
    this field in all 40 of its cells — every digit read as an index, so the row went to
    `unparsed` and the field to `missing`. The vocabulary is the authority on what a token
    means; the index guard only covers what the vocabulary does not claim.
    """
    party = fields.BY_ID["party_size_distribution"]
    assert party.levels[:2] == ("1", "2"), "fixture assumes the digit-named bins"
    assert parse(party, open=["1"])["grade"] == "strong"
    assert parse(party, open=[1])["value"] == "1"
    assert parse(party, open=["1", "2"])["grade"] == "weak"
    assert parse(party, open=["3-5"])["value"] == "3-5"
    with pytest.raises(sessions.LicensedParseError):
        parse(party, open=[9])          # not a bin, so still an unreadable token


def test_non_list_raises():
    with pytest.raises(sessions.LicensedParseError):
        parse(ORDINAL, open={"value": "seeks_hottest"})


def test_null_member_raises():
    with pytest.raises(sessions.LicensedParseError):
        parse(ORDINAL, open=["seeks_hottest", None])


# --- the prompt must not hand the judge its own grade ------------------------

def test_the_prompt_never_names_the_band_vocabulary():
    """The design rule of ADR-0013, as a regression test.

    A judge told that a two-element answer is scored `weak` is choosing a grade again, by
    another route — and the commitment grader's failure was precisely that it measured a
    disposition to answer rather than the evidence.
    """
    text = sessions.LICENSED_SYSTEM.lower() + sessions.licensed_prompt("...", 0).lower()
    for band in fields.__dict__.get("GRADES", ()) or ("strong", "weak", "unevidenced"):
        assert band not in text, f"the licensed prompt leaks the band token {band!r}"


def test_the_prompt_carries_every_field_and_its_vocabulary():
    prompt = sessions.licensed_prompt("TRANSCRIPT", 2)
    for field in fields.ALL_FIELDS:
        assert field.field_id in prompt
    assert "sessions 1 to 3" in prompt
    for level in ORDINAL.levels:
        assert level in prompt


def test_set_valued_fields_are_marked_for_the_two_key_shape():
    prompt = sessions.licensed_prompt("TRANSCRIPT", 0)
    line = next(l for l in prompt.splitlines() if l.strip().startswith("cuisines_liked:"))
    assert "LIST-VALUED" in line
    ordinal_line = next(
        l for l in prompt.splitlines() if l.strip().startswith("spice_tolerance:")
    )
    assert "LIST-VALUED" not in ordinal_line


# --- a self-correcting judge emits two objects, and the second one is the answer ---

def test_two_objects_takes_the_last_complete_one():
    """Measured on `probe_reauthor/runs/graded_licensed/`, `gen-009-c0` by opus.

    The judge closed an object with the JavaScript expression `.slice(0,2)`, wrote "Wait — I
    must output valid JSON only", and emitted a complete object after it. The greedy
    first-brace-to-last-brace span covers both plus the prose between, so it parses neither,
    and the regex salvage returned a flat dict with no `fields` key: 24 missing fields, and
    `error: None`. A silent wrong answer is the one outcome this parser exists to prevent.
    """
    raw = (
        '{"fields":[{"f":"spice_tolerance","open":["mild_only"].slice(0,2)}]}\n\n'
        'Wait — I must output valid JSON only.\n\n'
        '{"fields": [{"f": "spice_tolerance", "open": ["likes_real_spice", "seeks_hottest"]}]}'
    )
    reply = sessions.parse_json(raw)
    assert isinstance(reply, dict) and "fields" in reply, "fell through to the salvage path"
    rows = reply["fields"]
    assert len(rows) == 1
    assert parse(ORDINAL, **{k: v for k, v in rows[0].items() if k != "f"})["grade"] == "weak"


def test_a_brace_inside_a_string_does_not_end_an_object():
    raw = '{"fields": [{"f": "spice_tolerance", "open": [], "why": "said {curly} once"}]}'
    assert sessions.parse_json(raw)["fields"][0]["why"] == "said {curly} once"


def test_prose_around_a_single_object_still_parses():
    raw = 'Here is the grading:\n{"fields": [{"f": "spice_tolerance", "open": []}]}\nDone.'
    assert len(sessions.parse_json(raw)["fields"]) == 1
