"""`adjudicate.py` — the queue of reader disagreements and the owner's resolution of them."""

from __future__ import annotations

import json

from persona_twin import adjudicate, confirm, credit, fields, verify


PAGE = ("[0] USER: nothing fancy, we're not spending big\n"
        "[1] BOT: Counter service or a table?\n"
        "[2] USER: counter's fine\n")


def _items(value="counter_service"):
    card = {fid: None for fid in fields.ALL_FIELDS_IDS}
    card["formality_ceiling"] = value
    return {"gen-001": {
        "subject_id": "gen-001", "card": card,
        "provisional": {"7": {fid: "UNEVIDENCED" for fid in fields.ALL_FIELDS_IDS}},
    }}


def _records(instrument="page-v1"):
    return {"gen-001-s7": {"task_id": "gen-001-s7", "subject_id": "gen-001", "session": 7,
                           "checkpoint": 7, "transcript": PAGE, "instrument": instrument}}


def _leak(field_id="formality_ceiling"):
    return verify.Finding("leak", verify.BLOCKING, "gen-001-s7", "readable here",
                          field_id=field_id, quote="nothing fancy, we're not spending big",
                          messages=(0,))


def _decision(verdict="approve", instrument="page-v1", field_id="formality_ceiling"):
    return adjudicate.Decision(
        task_id="gen-001-s7", field_id=field_id, verdict=verdict, instrument=instrument,
        subject_id="gen-001", checkpoint=7, decided_by="owner", note="read the excerpt",
    )


def _candidates(items=None):
    got, _ = credit.screen([_leak()], records=_records(), items=items or _items())
    return got


def test_screen_passes_keys_one_and_two_without_any_second_reader():
    assert len(_candidates()) == 1


def test_an_approved_decision_credits_the_cell_at_weak_and_records_who_decided():
    got, refused = credit.eligible(
        [_leak()], records=_records(), items=_items(), confirmations={},
        adjudications={("gen-001-s7", "formality_ceiling"): _decision().to_json()},
    )
    assert len(got) == 1 and not refused
    assert got[0].adjudicated_by == "owner"
    assert got[0].to_json()["grade"] == "WEAK" == credit.CREDIT_GRADE
    after, _ = credit.apply(_items(), got)
    assert after["gen-001"]["provisional"]["7"]["formality_ceiling"] == "WEAK"


def test_a_rejected_decision_leaves_the_cell_unevidenced():
    got, refused = credit.eligible(
        [_leak()], records=_records(), items=_items(), confirmations={},
        adjudications={("gen-001-s7", "formality_ceiling"): _decision("reject").to_json()},
    )
    assert not got and refused["owner_rejected"] == 1


def test_an_approval_cannot_reach_past_key_one():
    # The owner settles key 3 — whether the readers agree. Key 1 is what stops the map
    # describing a person the generator never made, and no sign-off is allowed past it.
    got, refused = credit.eligible(
        [_leak()], records=_records(), items=_items(value=None), confirmations={},
        adjudications={("gen-001-s7", "formality_ceiling"): _decision().to_json()},
    )
    assert not got and refused["card_has_no_value"] == 1


def test_a_decision_made_on_a_page_that_has_since_changed_is_stale():
    decisions = {("gen-001-s7", "formality_ceiling"): _decision(instrument="page-v1")}
    ok, stale = adjudicate.fresh(decisions, records=_records(instrument="page-v2"))
    assert not ok and len(stale) == 1
    ok, stale = adjudicate.fresh(decisions, records=_records(instrument="page-v1"))
    assert len(ok) == 1 and not stale


def test_a_decision_with_no_instrument_is_stale_rather_than_universal():
    decisions = {("gen-001-s7", "formality_ceiling"): _decision(instrument="")}
    ok, stale = adjudicate.fresh(decisions, records=_records())
    assert not ok and len(stale) == 1


def test_a_stale_decision_puts_its_cell_back_in_the_queue():
    decisions = {("gen-001-s7", "formality_ceiling"): _decision(instrument="page-v1")}
    queue = adjudicate.pending(
        _candidates(), records=_records(instrument="page-v2"), items=_items(),
        confirmations={}, decisions=decisions,
    )
    assert [q.key for q in queue] == [("gen-001-s7", "formality_ceiling")]


def test_a_cell_a_second_reader_confirms_is_not_a_question():
    confirmed = {"gen-001-s7": {"model": "claude-sonnet-5",
                                "unscheduled": [{"field_id": "formality_ceiling"}]}}
    assert not adjudicate.pending(
        _candidates(), records=_records(), items=_items(), confirmations=confirmed,
    )


def test_a_question_carries_both_readings_and_the_card_value():
    answer = confirm.Answer(
        task_id="gen-001-s7", field_id="formality_ceiling", readable=False, messages=(),
        quote="", reads_as="the page never settles how dressy", model="claude-sonnet-5",
    )
    question = adjudicate.pending(
        _candidates(), records=_records(), items=_items(), confirmations={},
        answers=[answer], reader_model="claude-opus-5",
    )[0]
    assert question.card_value == "counter_service"
    assert question.first_reader == "claude-opus-5" and "nothing fancy" in question.first_quote
    assert question.second_asked and "never settles" in question.second_reads_as
    assert "nothing fancy" in question.excerpt


def test_a_question_says_when_the_second_reader_was_never_asked():
    question = adjudicate.pending(
        _candidates(), records=_records(), items=_items(), confirmations={},
    )[0]
    assert not question.second_asked
    assert "never asked" in adjudicate.describe(question)


def test_the_excerpt_is_the_cited_messages_and_not_the_whole_page():
    assert adjudicate.excerpt(PAGE, [2]).strip() == "[2] USER: counter's fine"
    # Nothing cited means nothing to narrow to; showing an empty excerpt would present the
    # decision as if there were no evidence behind it.
    assert adjudicate.excerpt(PAGE, []) == PAGE


def test_load_separates_a_verdict_it_does_not_recognise(tmp_path):
    path = tmp_path / "adjudications.json"
    path.write_text(json.dumps({"decisions": [
        _decision().to_json(),
        {**_decision().to_json(), "field_id": "travel_radius", "verdict": "maybe"},
    ]}))
    good, bad = adjudicate.load(path)
    assert set(good) == {("gen-001-s7", "formality_ceiling")}
    assert [d.field_id for d in bad] == ["travel_radius"]


def test_load_of_a_missing_file_is_empty_rather_than_an_error(tmp_path):
    good, bad = adjudicate.load(tmp_path / "nothing.json")
    assert not good and not bad


def test_save_and_load_round_trip(tmp_path):
    path = adjudicate.save(tmp_path / "adjudications.json", [_decision()])
    good, bad = adjudicate.load(path)
    assert not bad and good[("gen-001-s7", "formality_ceiling")].note == "read the excerpt"


# --- Dismissal: a person overruling one reader about one finding ------------------------


def _contradiction(field_id="visit_time_distribution", task_id="gen-001-s7"):
    return verify.Finding("contradiction", verify.BLOCKING, task_id,
                          "a single occasion in a low-but-nonzero bin is not itself a "
                          "contradiction", field_id=field_id)


def _dismissal(**kw):
    base = dict(task_id="gen-001-s7", field_id="visit_time_distribution",
                kind="contradiction", instrument="page-v1", note="the reader disowns it",
                decided_by="owner@example.com", decided_at="2026-08-23")
    base.update(kw)
    return adjudicate.Dismissal(**base)


def test_a_dismissal_demotes_a_contradiction_credit_cannot_reach():
    """`credit.ABSOLVABLE_KINDS` is leaks only, so nothing else could settle this one."""
    entry = _dismissal()
    out, demoted, stale = adjudicate.dismiss(
        [_contradiction()], {entry.key: entry}, records=_records("page-v1"))
    assert demoted == 1 and not stale
    assert out[0].severity == verify.NOTE
    assert "owner@example.com read this page and did not uphold" in out[0].detail
    assert "the reader disowns it" in out[0].detail


def test_a_dismissal_is_pinned_to_the_page_it_was_decided_on():
    entry = _dismissal()
    out, demoted, stale = adjudicate.dismiss(
        [_contradiction()], {entry.key: entry}, records=_records("page-v2"))
    assert demoted == 0 and out[0].severity == verify.BLOCKING
    assert [d.key for d in stale] == [entry.key]


def test_a_dismissal_with_no_instrument_is_stale_rather_than_universal():
    entry = _dismissal(instrument="")
    _out, demoted, stale = adjudicate.dismiss(
        [_contradiction()], {entry.key: entry}, records=_records("page-v1"))
    assert demoted == 0 and len(stale) == 1


def test_a_dismissal_names_a_kind_and_does_not_silence_the_field():
    """One field can carry two findings; refusing the contradiction leaves the leak alone."""
    entry = _dismissal()
    leak = _leak("visit_time_distribution")
    out, demoted, _stale = adjudicate.dismiss(
        [_contradiction(), leak], {entry.key: entry}, records=_records("page-v1"))
    assert demoted == 1
    assert [f.severity for f in out] == [verify.NOTE, verify.BLOCKING]


def test_a_dismissal_whose_finding_is_gone_is_not_reported_as_stale():
    """The page moved on. That is not a stale approval and must not read as one."""
    entry = _dismissal()
    _out, demoted, stale = adjudicate.dismiss([], {entry.key: entry}, records=_records())
    assert demoted == 0 and stale == []


def test_load_dismissals_rejects_an_entry_naming_no_kind(tmp_path):
    path = tmp_path / "dismissals.json"
    path.write_text(json.dumps({"dismissals": [
        {"task_id": "gen-001-s7", "field_id": "visit_time_distribution", "kind": "",
         "instrument": "page-v1"},
        {"task_id": "gen-001-s7", "field_id": "visit_time_distribution",
         "kind": "contradiction", "instrument": "page-v1"},
    ]}))
    good, bad = adjudicate.load_dismissals(path)
    assert list(good) == [("gen-001-s7", "visit_time_distribution", "contradiction")]
    assert len(bad) == 1


def test_dismissals_round_trip(tmp_path):
    path = tmp_path / "dismissals.json"
    adjudicate.save_dismissals(path, [_dismissal()])
    good, bad = adjudicate.load_dismissals(path)
    assert not bad and good[("gen-001-s7", "visit_time_distribution", "contradiction")].note
