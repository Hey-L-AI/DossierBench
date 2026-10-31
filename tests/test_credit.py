"""`credit.py` — the reader-derived evidence credit and its two keys."""

from __future__ import annotations

from persona_twin import credit, fields, verify


def _item(**over):
    card = {fid: None for fid in fields.ALL_FIELDS_IDS}
    card["planning_style"] = "decides_same_day"
    card["allergies"] = []
    card["alcohol_free_required"] = False
    base = {
        "subject_id": "gen-000", "card": card,
        "provisional": {"0": {fid: "UNEVIDENCED" for fid in fields.ALL_FIELDS_IDS}},
    }
    base.update(over)
    return {"gen-000": base}


def _records():
    return {"gen-000-s0": {"task_id": "gen-000-s0", "subject_id": "gen-000",
                           "session": 0, "checkpoint": 0}}


def _confirm(field_id="planning_style", task="gen-000-s0", model="claude-sonnet-5"):
    """A second reader's verdict that also reads `field_id` on that page — key 3."""
    return {task: {"model": model, "unscheduled": [{"field_id": field_id}], "beats": []}}


def _leak(field_id="planning_style", task="gen-000-s0"):
    return verify.Finding("leak", verify.BLOCKING, task, "readable here",
                          field_id=field_id, quote="tonight", messages=(0,))


def test_a_leak_on_a_field_the_card_holds_is_credited_at_weak():
    got, refused = credit.eligible([_leak()], records=_records(), items=_item(), confirmations=_confirm())
    assert len(got) == 1 and not refused
    assert got[0].cell == ("gen-000", 0, "planning_style")
    after, moved = credit.apply(_item(), got)
    assert after["gen-000"]["provisional"]["0"]["planning_style"] == "WEAK"
    assert moved["planning_style"] == 1


def test_key_one_refuses_a_field_the_card_leaves_empty():
    """`allergies = []` and `alcohol_free_required = False` are absences, not values. Crediting
    evidence there would be evidence for something that is not true of the person."""
    for field_id in ("allergies", "alcohol_free_required"):
        got, refused = credit.eligible(
            [_leak(field_id)], records=_records(), items=_item(),
            confirmations=_confirm(field_id),
        )
        assert got == [] and refused["card_has_no_value"] == 1, field_id


def test_key_two_refuses_a_field_the_reader_also_says_contradicts_the_card():
    findings = [
        _leak("noise_preference"),
        verify.Finding("contradiction", verify.BLOCKING, "gen-000-s0",
                       "the page rejects loud rooms", field_id="noise_preference"),
    ]
    items = _item()
    items["gen-000"]["card"]["noise_preference"] = "wants_loud"
    got, refused = credit.eligible(findings, records=_records(), items=items,
                               confirmations=_confirm('noise_preference'))
    assert got == [] and refused["contradicted"] == 1


def test_credit_never_reaches_strong_and_never_touches_an_evidenced_cell():
    """ADR-0009/0010: where a reader and the schedule disagree about strength, the schedule is
    the record. This rule adds evidence where there was none; it never restates it."""
    for existing in ("WEAK", "STRONG"):
        items = _item()
        items["gen-000"]["provisional"]["0"]["planning_style"] = existing
        got, refused = credit.eligible([_leak()], records=_records(), items=items,
                               confirmations=_confirm())
        assert got == []
        assert refused[f"already_{existing.lower()}"] == 1
    assert credit.CREDIT_GRADE == "WEAK"


def test_applying_credits_leaves_the_derived_map_untouched():
    """The model-free map ADR-0034 promised stays available: `apply` returns a new artifact
    rather than mutating the one `schedule.py` produced."""
    items = _item()
    got, _ = credit.eligible([_leak()], records=_records(), items=items,
                             confirmations=_confirm())
    after, _ = credit.apply(items, got)
    assert items["gen-000"]["provisional"]["0"]["planning_style"] == "UNEVIDENCED"
    assert after["gen-000"]["provisional"]["0"]["planning_style"] == "WEAK"


def test_every_credited_cell_carries_provenance_a_second_reader_can_be_pointed_at():
    verdicts = {"gen-000-s0": {"model": "claude-opus-5", "instrument": "abc123"}}
    got, _ = credit.eligible(
        [_leak()], records=_records(), items=_item(), verdicts=verdicts,
        confirmations=_confirm(),
    )
    payload = got[0].to_json()
    assert payload["provenance"] == {
        "source": "reader", "model": "claude-opus-5", "instrument": "abc123",
        "confirmed_by": ["claude-sonnet-5"], "readers": 2,
        # Empty, and present: a credit two readers agreed on and one a person signed off are
        # different provenance, and a consumer counting readers has to be able to tell.
        "adjudicated_by": "",
    }
    assert payload["messages"] == [0], "which messages to re-read"
    after, _ = credit.apply(_item(), got)
    assert after["gen-000"]["evidence_provenance"]["0"]["planning_style"]["source"] == "reader"


def test_the_same_cell_offered_twice_is_credited_once():
    got, refused = credit.eligible(
        [_leak(), _leak()], records=_records(), items=_item(),
        confirmations=_confirm(),
    )
    assert len(got) == 1 and refused["duplicate_cell"] == 1


def test_key_three_refuses_a_cell_no_second_reader_confirms():
    """Measured, not hypothetical: without this key, 24 of 44 credited cells rested on one
    model's reading of text the same model wrote."""
    got, refused = credit.eligible(
        [_leak()], records=_records(), items=_item(),
        confirmations={"gen-000-s0": {"model": "claude-sonnet-5",
                                      "unscheduled": [], "beats": []}},
    )
    assert got == [] and refused["unconfirmed"] == 1


def test_key_three_fails_closed_when_there_is_no_second_reading_at_all():
    """No second reading is not the same as a second reading that agreed."""
    got, refused = credit.eligible([_leak()], records=_records(), items=_item())
    assert got == [] and refused["no_second_reading"] == 1


def test_a_second_reader_filing_the_field_as_a_delivered_beat_still_confirms():
    """The two lists differ by whether the *schedule* placed the field, which is a fact about
    the schedule, not about what the reader saw."""
    got, _ = credit.eligible(
        [_leak()], records=_records(), items=_item(),
        confirmations={"gen-000-s0": {
            "model": "claude-sonnet-5", "unscheduled": [],
            "beats": [{"field_id": "planning_style", "delivered": True}],
        }},
    )
    assert len(got) == 1 and got[0].confirmed_by == ("claude-sonnet-5",)


# --- absolve: a credited leak is not the editor's problem --------------------


def _a_credit(task_id="gen-000-s3", field_id="planning_style"):
    return credit.Credit(
        subject_id="gen-000", checkpoint=3, field_id=field_id, task_id=task_id,
        messages=(0,), quote="tonight", reader_model="claude-opus-5",
        instrument="abc", confirmed_by=("claude-sonnet-5",),
    )


def _absolve_leak(task_id="gen-000-s3", field_id="planning_style"):
    return verify.Finding(
        "leak", verify.BLOCKING, task_id, "readable here and the map says UNEVIDENCED",
        field_id=field_id, quote="tonight", messages=(0,),
    )


def test_a_credited_leak_stops_blocking():
    out, demoted = credit.absolve([_absolve_leak()], [_a_credit()])
    assert demoted == 1
    assert out[0].severity == verify.NOTE
    assert "credited to WEAK" in out[0].detail


def test_an_uncredited_leak_still_blocks():
    """The refusals are the half that matters. A leak the second reader would not confirm is
    still a page the editor has to answer for."""
    out, demoted = credit.absolve([_absolve_leak(field_id="allergies")], [_a_credit()])
    assert demoted == 0 and out[0].severity == verify.BLOCKING


def test_a_contradiction_is_never_absolved():
    """ADR-0046 puts contradictions explicitly out of scope: if the page disagrees with the
    card, the card is the subject's identity and the session is the thing that is wrong."""
    clash = verify.Finding(
        "contradiction", verify.BLOCKING, "gen-000-s3", "page disagrees with the card",
        field_id="planning_style", quote="tonight", messages=(0,),
    )
    out, demoted = credit.absolve([clash], [_a_credit()])
    assert demoted == 0 and out[0].severity == verify.BLOCKING


def test_absolve_returns_new_findings_and_leaves_the_report_as_the_reader_wrote_it():
    original = _absolve_leak()
    out, _ = credit.absolve([original], [_a_credit()])
    assert original.severity == verify.BLOCKING
    assert out[0] is not original


def test_credit_on_another_page_does_not_absolve_this_one():
    """The key is (task, field). The same field credited on session 3 says nothing about
    session 4, where the reader may have found nothing at all."""
    out, demoted = credit.absolve([_absolve_leak(task_id="gen-000-s4")], [_a_credit()])
    assert demoted == 0 and out[0].severity == verify.BLOCKING


# --- absolve: a leak a person refused is not the editor's problem either ------


def test_a_refused_leak_stops_blocking_without_being_credited():
    """The other half of a decision.

    A rejection says the reader's reading did not survive a person looking at the page. The
    cell stays UNEVIDENCED — that is what makes it a rejection — but there is no more repair
    to make than there is on a credited one, and leaving it blocking keeps a question the
    owner has already answered sitting in the outstanding count.
    """
    out, demoted = credit.absolve(
        [_absolve_leak()], [], dismissed=[("gen-000-s3", "planning_style")],
    )
    assert demoted == 1
    assert out[0].severity == verify.NOTE
    assert "did not uphold the reading" in out[0].detail
    assert "credited to WEAK" not in out[0].detail, "a rejection is not a credit"


def test_a_credit_and_a_rejection_are_distinguishable_in_the_report():
    """Same severity, opposite reasons: the map moved, or the reading did not hold. Anything
    reading the report back has to be able to tell which happened."""
    out, demoted = credit.absolve(
        [_absolve_leak(), _absolve_leak(task_id="gen-000-s4")], [_a_credit()],
        dismissed=[("gen-000-s4", "planning_style")],
    )
    assert demoted == 2 and [f.severity for f in out] == [verify.NOTE, verify.NOTE]
    assert "credited to WEAK" in out[0].detail
    assert "did not uphold the reading" in out[1].detail


def test_a_rejection_on_another_page_does_not_settle_this_one():
    out, demoted = credit.absolve(
        [_absolve_leak()], [], dismissed=[("gen-000-s4", "planning_style")],
    )
    assert demoted == 0 and out[0].severity == verify.BLOCKING


def test_a_rejection_cannot_settle_a_contradiction_either():
    """`ABSOLVABLE_KINDS` is the whole list, and a decision does not widen it: a page that
    disagrees with the card is wrong about the card whoever looks at it."""
    clash = verify.Finding(
        "contradiction", verify.BLOCKING, "gen-000-s3", "page disagrees with the card",
        field_id="planning_style", quote="tonight", messages=(0,),
    )
    out, demoted = credit.absolve([clash], [], dismissed=[("gen-000-s3", "planning_style")])
    assert demoted == 0 and out[0].severity == verify.BLOCKING


# --- apply: a credit is carried forward, because the map is monotone ----------


def _wide_item(checkpoints=4, **over):
    """One subject with several checkpoints, everything UNEVIDENCED."""
    items = _item(**over)
    items["gen-000"]["provisional"] = {
        str(c): {fid: "UNEVIDENCED" for fid in fields.ALL_FIELDS_IDS}
        for c in range(checkpoints)
    }
    items["gen-000"]["must_unevidenced"] = list(fields.ALL_FIELDS_IDS)
    return items


def test_a_credit_raises_every_later_checkpoint_and_leaves_no_island():
    """§7.3's monotonicity requirement, which `docs/SCORING.md` records as *met* by construction.
    A page is a prefix of the next page, so evidence a reader could see at t is still there at
    t+1; crediting the one cell told the twin to forget it again."""
    after, _ = credit.apply(_wide_item(), [_a_credit()])       # credited at checkpoint 3
    grades = after["gen-000"]["provisional"]
    assert [grades[str(c)]["planning_style"] for c in range(4)] == [
        "UNEVIDENCED", "UNEVIDENCED", "UNEVIDENCED", "WEAK",
    ], "nothing before the reader saw it"

    early = credit.Credit(
        subject_id="gen-000", checkpoint=1, field_id="planning_style",
        task_id="gen-000-s1", messages=(0,), quote="tonight",
        reader_model="claude-opus-5", instrument="abc", confirmed_by=("claude-sonnet-5",),
    )
    after, moved = credit.apply(_wide_item(), [early])
    grades = after["gen-000"]["provisional"]
    assert [grades[str(c)]["planning_style"] for c in range(4)] == [
        "UNEVIDENCED", "WEAK", "WEAK", "WEAK",
    ]
    assert moved["planning_style"] == 1, "one credit, however many cells it moves"


def test_a_carried_cell_says_which_checkpoint_the_reader_actually_read():
    """Provenance is the whole reason a partly-derived map is legible. 'A reader saw it here'
    and 'it is still true here' are different claims and the artifact has to tell them apart."""
    early = credit.Credit(
        subject_id="gen-000", checkpoint=1, field_id="planning_style",
        task_id="gen-000-s1", messages=(0,), quote="tonight",
        reader_model="claude-opus-5", instrument="abc", confirmed_by=("claude-sonnet-5",),
    )
    after, _ = credit.apply(_wide_item(), [early])
    prov = after["gen-000"]["evidence_provenance"]
    assert "carried_from" not in prov["1"]["planning_style"], "the cell the reader read"
    assert prov["2"]["planning_style"]["carried_from"] == 1
    assert prov["3"]["planning_style"]["model"] == "claude-opus-5"


def test_carrying_forward_never_lowers_a_grade_the_schedule_placed():
    """The scheduled beat lands after the leak. ADR-0009/0010: where the two disagree about
    strength the schedule is the record, so a carried WEAK must not touch a STRONG."""
    items = _wide_item()
    items["gen-000"]["provisional"]["3"]["planning_style"] = "STRONG"
    early = credit.Credit(
        subject_id="gen-000", checkpoint=1, field_id="planning_style",
        task_id="gen-000-s1", messages=(0,), quote="tonight",
        reader_model="claude-opus-5", instrument="abc", confirmed_by=("claude-sonnet-5",),
    )
    after, _ = credit.apply(items, [early])
    grades = after["gen-000"]["provisional"]
    assert [grades[str(c)]["planning_style"] for c in range(4)] == [
        "UNEVIDENCED", "WEAK", "WEAK", "STRONG",
    ]
    assert "planning_style" not in after["gen-000"]["evidence_provenance"].get("3", {})


def test_applying_a_credit_re_reads_must_unevidenced_off_the_map():
    """ADR-0044's rule, applied to the other way into the same gap: a field listed as never
    evidenced while the same file's map grades it WEAK is one fact with two records."""
    early = credit.Credit(
        subject_id="gen-000", checkpoint=1, field_id="planning_style",
        task_id="gen-000-s1", messages=(0,), quote="tonight",
        reader_model="claude-opus-5", instrument="abc", confirmed_by=("claude-sonnet-5",),
    )
    items = _wide_item()
    assert "planning_style" in items["gen-000"]["must_unevidenced"]
    after, _ = credit.apply(items, [early])
    assert "planning_style" not in after["gen-000"]["must_unevidenced"]
    assert "noise_preference" in after["gen-000"]["must_unevidenced"], "only the field moved"


def test_a_credit_at_the_last_checkpoint_still_leaves_must_unevidenced_consistent():
    after, _ = credit.apply(_wide_item(checkpoints=4), [_a_credit()])
    final = after["gen-000"]["provisional"]["3"]
    assert final["planning_style"] == "WEAK"
    assert "planning_style" not in after["gen-000"]["must_unevidenced"]


def test_a_second_reading_of_a_carried_cell_records_the_reader_that_read_it():
    """Two credits on one field, the later one landing where the earlier one had carried to.
    The map is the same either way; the artifact is not. `eligible` has already refused every
    cell the schedule evidences, so a credited cell that is not UNEVIDENCED can only be one
    this call carried — and dropping it would file a cell a reader looked at as hearsay."""
    def _at(cp):
        return credit.Credit(
            subject_id="gen-000", checkpoint=cp, field_id="planning_style",
            task_id=f"gen-000-s{cp}", messages=(0,), quote="tonight",
            reader_model="claude-opus-5", instrument=f"i{cp}",
            confirmed_by=("claude-sonnet-5",),
        )
    for order in ([_at(1), _at(3)], [_at(3), _at(1)]):
        after, moved = credit.apply(_wide_item(), order)
        prov = after["gen-000"]["evidence_provenance"]
        assert moved["planning_style"] == 2, "both readings counted"
        assert prov["2"]["planning_style"]["carried_from"] == 1, "nobody read it here"
        assert "carried_from" not in prov["3"]["planning_style"], "a reader read it here"
        assert prov["3"]["planning_style"]["instrument"] == "i3"
