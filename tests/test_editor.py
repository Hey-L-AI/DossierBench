"""`editor.py` — the pre-freeze repair pass, exercised without a real model call.

Every test drives `edit_session` with a scripted `Invoker` (same fake shape `test_author.py`
uses) so the gate under test is `transcript.check_session` and `verify.reconcile`, not a real
reply. The one case worth a real defect is `gen-001-s1`'s `denied_constraint` — the session
`runs/reauthor_gen001_s1.py` fixed by hand — kept here as the shape a locatable finding
actually has.
"""

from __future__ import annotations

import re

from persona_twin import credit, editor, fields, schedule, transcript, verify


class Recorder:
    """An invoker that returns scripted replies in order, then a default forever."""

    def __init__(self, replies: list[str] | None = None, default: str | None = None) -> None:
        self.replies = list(replies or [])
        self.default = default
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, model: str, system: str, prompt: str) -> str:
        self.calls.append((model, system, prompt))
        if self.replies:
            return self.replies.pop(0)
        assert self.default is not None, "ran out of scripted replies"
        return self.default


#: `gen-001-s1`'s real shape, re-worded: a `dietary` disclosure beat whose ask names a diet
#: by example, so the ask itself is legal and only the user's blanket denial is the defect.
#: The umbrella phrasing this fixture used to carry is now caught by the intake gate before
#: the editor ever sees the page, which is the point of moving it there.
_TRANSCRIPT = (
    "[0] USER: hi, table for two tonight\n"
    "[1] BOT: sure — before I pass the details on, do you follow any particular diet?\n"
    "[2] USER: no, nothing like that\n"
    "[3] BOT: got it, I'll send the booking through\n"
)

_BEATS = [{"field_id": "dietary", "move": "disclosure", "attributed": "user", "gold_value": []}]


def _record(**over):
    base = {
        "task_id": "gen-001-s1", "subject_id": "gen-001", "session": 1, "checkpoint": 1,
        "transcript": _TRANSCRIPT, "beats": _BEATS, "self_report": None,
    }
    base.update(over)
    return base


def _item():
    card = {fid: None for fid in fields.ALL_FIELDS_IDS}
    card["allergies"] = ["shellfish"]
    return {"subject_id": "gen-001", "card": card, "provisional": {}}


def _finding():
    quote = ("are there do you follow any particular diet? / "
             "no, nothing like that")
    finding = verify.Finding(
        "denied_constraint", verify.BLOCKING, "gen-001-s1",
        "denies allergies unbriefed", field_id="allergies", quote=quote,
    )
    located = transcript.locate_quote(_TRANSCRIPT, quote)
    assert located is not None
    return verify._located(finding, {"gen-001-s1": _record()})


def _clean_verdict() -> str:
    return (
        '{"beats": [{"field_id": "dietary", "delivered": true, "strength": "pinned", '
        '"whose": "user", "moments": 1, "quote": "no particular diet"}], '
        '"unscheduled": [], "contradictions": []}'
    )


def test_edit_session_accepts_a_verified_fix():
    finding = _finding()
    assert finding.locatable
    invoke = Recorder(default='{"turn": "no, no particular diet to flag"}')
    verify_invoke = Recorder(default=_clean_verdict())

    result, patched = editor.edit_session(
        invoke, "fake-editor", _record(), _item(), finding, verify_invoke=verify_invoke,
    )

    assert result.outcome == "fixed"
    assert result.verified
    assert patched is not None
    assert "no, no particular diet to flag" in patched["transcript"]
    assert "no, nothing like that" not in patched["transcript"]
    # every other turn is untouched
    assert "table for two tonight" in patched["transcript"]
    assert "I'll send the booking through" in patched["transcript"]


def test_edit_session_rejects_a_candidate_that_fails_check_session():
    """A rewrite that trades one defect for a banned self-summary must never be spliced in,
    even though it would have resolved the original finding."""
    finding = _finding()
    invoke = Recorder(default='{"turn": "no, I never really eat anything unusual"}')

    def unreachable(model, system, prompt):
        raise AssertionError("check_session should reject before a re-audit call is made")

    result, patched = editor.edit_session(
        invoke, "fake-editor", _record(), _item(), finding, verify_invoke=unreachable,
        max_attempts=2,
    )

    assert result.outcome == "unresolved"
    assert patched is None
    assert result.attempts == 2


def test_edit_session_rejects_a_candidate_the_reaudit_still_blocks():
    """The splice is clean §6.1-wise but the scoped re-audit raises the target defect again —
    the fix must not be accepted just because it passed the free gate."""
    finding = verify.Finding(
        "contradiction", verify.BLOCKING, "gen-001-s1", "denies a constraint the card gives",
        field_id="allergies", messages=(2,),
    )
    # The candidate must clear `check_session` for the re-audit gate to be what rejects it.
    # It used to read "no, nothing at all", which `_blanket_denial_problems` now stops one
    # gate earlier — the editor's own fixture repair for this session was the same unbounded
    # denial the session shipped, which is the rule's case made twice over.
    invoke = Recorder(default='{"messages": {"2": "no allergies to flag"}}')
    still_blocking_verdict = (
        '{"beats": [], "unscheduled": [], '
        '"contradictions": [{"field_id": "allergies", "messages": [2], '
        '"quote": "no allergies to flag", "why": "the card gives a shellfish allergy"}]}'
    )
    verify_invoke = Recorder(default=still_blocking_verdict)

    result, patched = editor.edit_session(
        invoke, "fake-editor", _record(), _item(), finding, verify_invoke=verify_invoke,
        max_attempts=1, baseline=[finding],
    )

    assert result.outcome == "unresolved"
    assert patched is None
    assert result.rejected[0]["gate"] == "still_blocking"


def test_the_same_field_re_filed_by_the_reaudit_is_accepted_and_recorded():
    """The case the test above used to assert the opposite of.

    The re-audit finds nothing of the target kind and raises a *leak* on the same field
    instead. That is the repair having moved the defect out of the editor's lane and into
    `credit`'s — accepted, with the trade on the record. Before the field-level key this was
    refused, and `gen-002-s9`/`dietary` burned all three attempts on it.
    """
    finding = _finding()
    invoke = Recorder(default='{"turn": "no, no particular diet to flag"}')
    re_filed_verdict = (
        '{"beats": [{"field_id": "dietary", "delivered": true, "strength": "pinned", '
        '"whose": "user", "moments": 1, "quote": "no particular diet"}], '
        '"unscheduled": [{"field_id": "allergies", "strength": "pinned", '
        '"quote": "no, no particular diet to flag"}], "contradictions": []}'
    )
    verify_invoke = Recorder(default=re_filed_verdict)

    item = _item()
    # UNEVIDENCED at this checkpoint so the unscheduled allergies mention reads as a leak
    item["provisional"] = {"1": {"allergies": "UNEVIDENCED"}}

    result, patched = editor.edit_session(
        invoke, "fake-editor", _record(), item, finding, verify_invoke=verify_invoke,
        max_attempts=1, baseline=[finding],
    )

    assert result.outcome == "fixed"
    assert patched is not None
    assert [(e["kind"], e["field_id"]) for e in result.traded] == [("leak", "allergies")]


def test_edit_session_is_unlocatable_without_a_resolvable_turn():
    finding = verify.Finding(
        "undelivered", verify.BLOCKING, "gen-001-s1", "never delivered", field_id="dietary",
    )
    invoke = Recorder(default="should never be called")

    result, patched = editor.edit_session(invoke, "fake-editor", _record(), _item(), finding)

    assert result.outcome == "unlocatable"
    assert patched is None
    assert invoke.calls == []


def test_a_malformed_reply_costs_an_attempt_and_is_not_retried_as_a_free_pass():
    finding = _finding()
    invoke = Recorder(replies=["not json at all"], default='{"turn": "no, no particular diet"}')
    verify_invoke = Recorder(default=_clean_verdict())

    result, patched = editor.edit_session(
        invoke, "fake-editor", _record(), _item(), finding, verify_invoke=verify_invoke,
        max_attempts=2,
    )

    assert result.outcome == "fixed"
    assert len(invoke.calls) == 2, "the malformed reply consumed an attempt"


# --- v0.4: multi-message repair, and judging against a baseline ---------------


def _blocking(kind: str, field_id: str) -> verify.Finding:
    return verify.Finding(kind, verify.BLOCKING, "gen-001-s1", "wrong", field_id=field_id)


def test_a_pre_existing_defect_on_another_field_no_longer_blocks_a_repair():
    """The `new_blocking` bug, pinned.

    The gate used to accept a candidate only if the patched session carried NO blocking
    finding at all, so a session with two defects could never be repaired: fixing one left
    the other, and the other was counted as though the edit had introduced it. In the pilot
    that made 14 of 16 failed repairs unwinnable before the first call.
    """
    resolved = [_blocking("leak", "planning_style"), _blocking("contradiction", "allergies")]
    prior = {("planning_style", "leak"), ("allergies", "contradiction")}
    target = ("allergies", "contradiction")

    # What the old gate computed: everything that is not the target.
    old_gate = [f for f in resolved if (f.field_id, f.kind) != target]
    assert old_gate, "the old gate would have rejected this candidate"

    assert editor._regressions(resolved, prior=prior, target=target) == []


def test_a_defect_the_edit_actually_introduced_is_still_a_regression():
    """And it has to be one `credit` cannot settle — an introduced LEAK is `_collateral`'s."""
    resolved = [_blocking("leak", "planning_style"),
                _blocking("contradiction", "spice_tolerance")]
    prior = {("planning_style", "leak")}
    target = ("allergies", "denied_constraint")
    regressions = editor._regressions(resolved, prior=prior, target=target)
    assert [(f.field_id, f.kind) for f in regressions] == [
        ("spice_tolerance", "contradiction")]


def test_an_introduced_leak_on_another_field_is_collateral_and_not_a_regression():
    """The other-field half of the trade, and the reason it is not a refusal.

    12 of the 25 regression rejections among the n=15 run's unresolved repairs named nothing
    but leaks, and 4 of the 9 unresolved contradictions had an attempt turned down for one.
    A leak is what `credit.absolve` exists to settle; refusing to mint one in order to clear
    a contradiction keeps the defect that has no other route out.
    """
    resolved = [_blocking("leak", "planning_style"), _blocking("leak", "spice_tolerance")]
    prior = {("planning_style", "leak")}
    target = ("allergies", "denied_constraint")
    assert editor._regressions(resolved, prior=prior, target=target) == []
    collateral = editor._collateral(resolved, prior=prior, target=target)
    assert [(f.field_id, f.kind) for f in collateral] == [("spice_tolerance", "leak")]


def test_collateral_is_read_off_credit_rather_than_hardcoded(monkeypatch):
    """Same discipline as `_downgrades`: the direction is stated once, in `credit`."""
    resolved = [_blocking("contradiction", "spice_tolerance")]
    target = ("allergies", "denied_constraint")
    monkeypatch.setattr(editor.credit, "ABSOLVABLE_KINDS", frozenset({"contradiction"}))
    assert editor._regressions(resolved, prior=set(), target=target) == []
    assert len(editor._collateral(resolved, prior=set(), target=target)) == 1


def test_the_target_field_re_filed_under_another_kind_is_not_a_regression():
    """`gen-002-s9`/`dietary`, pinned.

    Three attempts, `still` empty on all three — the contradiction was gone every time — and
    all three refused on `leak/dietary`: the same field, re-filed by the re-audit of the very
    text that fixed it. Under the old `(field, kind)` key that leak was neither the target nor
    in `prior`, so the gate called it damage the rewrite had introduced and threw the repair
    away.
    """
    resolved = [_blocking("leak", "dietary")]
    prior = {("dietary", "contradiction")}
    target = ("dietary", "contradiction")

    old_gate = [f for f in resolved
                if (f.field_id, f.kind) != target and (f.field_id, f.kind) not in prior]
    assert old_gate, "the old gate would have rejected this candidate"

    assert editor._regressions(resolved, prior=prior, target=target) == []


def test_the_target_field_no_longer_gates_even_when_the_trade_is_downward():
    """What the field-level key gives up — and where the giving-up stops.

    `_regressions` still says nothing about the target field whichever way the trade went;
    that is the whole point of the field-level key. The downward trade is caught one function
    over, by `_downgrades`, so the two halves of the rule stay separately testable.
    """
    resolved = [_blocking("contradiction", "dietary")]
    assert editor._regressions(
        resolved, prior={("dietary", "leak")}, target=("dietary", "leak")
    ) == []


# --- ADR-0056: a repair may not push its own field out of credit's reach -----


def test_a_leak_traded_for_a_contradiction_is_refused():
    """`gen-004-s1`/`dietary` from `runs/result-leak-pass.txt`, pinned.

    The rewrite cleared `leak/dietary` by having the user say there was nothing the kitchen
    needed to work around, on a card where vegan is a rule the person will not break. The
    editor recorded the trade, the run scored it a success, and the page was worse than
    before it was touched.
    """
    resolved = [_blocking("contradiction", "dietary")]
    downgrades = editor._downgrades(
        resolved, prior=set(), target=("dietary", "leak")
    )
    assert [(f.field_id, f.kind) for f in downgrades] == [("dietary", "contradiction")]


def test_a_contradiction_traded_for_a_leak_is_still_accepted():
    """The rule is directional, and this is the direction it must not touch: `gen-002-s9`'s
    trade is the one `_regressions` was relaxed for, and ADR-0056 must not take it back."""
    resolved = [_blocking("leak", "dietary")]
    assert editor._downgrades(
        resolved, prior={("dietary", "contradiction")}, target=("dietary", "contradiction")
    ) == []


def test_a_contradiction_the_page_already_carried_is_not_a_downgrade():
    """Same reason `_regressions` subtracts `prior`: a defect the rewrite did not introduce
    must not make a multi-defect field unrepairable in principle."""
    resolved = [_blocking("contradiction", "dietary")]
    assert editor._downgrades(
        resolved, prior={("dietary", "contradiction")}, target=("dietary", "leak")
    ) == []


def test_a_downgrade_on_another_field_is_left_to_regressions():
    """`_downgrades` is scoped to the field being repaired. Damage elsewhere is
    `_regressions`' job and must not be counted twice."""
    resolved = [_blocking("contradiction", "allergies")]
    assert editor._downgrades(
        resolved, prior=set(), target=("dietary", "leak")
    ) == []


def test_the_direction_is_read_off_credit_rather_than_hardcoded():
    """The two modules make one decision between them. If `credit` ever learns to settle
    another kind, this gate loosens with it and nothing here needs editing."""
    assert "leak" in credit.ABSOLVABLE_KINDS
    assert "contradiction" not in credit.ABSOLVABLE_KINDS


def test_a_refused_downgrade_costs_the_attempt_and_names_its_gate(monkeypatch):
    """End to end: the candidate that cleared the leak is thrown away, the page is left as it
    was, and `rejected` says why — `traded` must NOT be where this shows up."""
    finding = verify.Finding(
        "leak", verify.BLOCKING, "gen-001-s1", "readable, unevidenced",
        field_id="allergies", messages=(2,),
    )
    invoke = Recorder(default='{"messages": {"2": "nothing the kitchen needs to know"}}')
    verify_invoke = Recorder(default=_clean_verdict())
    monkeypatch.setattr(editor, "reconcile", lambda record, item, verdict: [
        verify.Finding("contradiction", verify.BLOCKING, "gen-001-s1",
                       "denies a constraint the card gives", field_id="allergies"),
    ])

    result, patched = editor.edit_session(
        invoke, "fake-editor", _record(), _item(), finding, verify_invoke=verify_invoke,
        max_attempts=1, baseline=[finding],
    )

    assert result.outcome == "unresolved"
    assert patched is None, "the page must be left exactly as it was"
    assert result.traded == ()
    assert result.rejected[0]["gate"] == "downgrade"
    assert "contradiction/allergies" in result.rejected[0]["why"][0]


# --- leak -> leak is a null move (TRANSITIONS) ------------------------------------

def _leak_finding():
    return verify.Finding(
        "leak", verify.BLOCKING, "gen-001-s1", "readable, unevidenced",
        field_id="allergies", messages=(2,),
    )


def _leak_run(monkeypatch, resolved, max_attempts=1):
    invoke = Recorder(default='{"messages": {"2": "nothing the kitchen needs to know"}}')
    monkeypatch.setattr(editor, "reconcile", lambda record, item, verdict: resolved)
    finding = _leak_finding()
    return editor.edit_session(
        invoke, "fake-editor", _record(), _item(), finding,
        verify_invoke=Recorder(default=_clean_verdict()),
        max_attempts=max_attempts, baseline=[finding],
    )


def test_a_leak_re_filed_as_the_same_leak_is_neutral_and_not_a_failed_repair(monkeypatch):
    """`leak -> leak` on the field being repaired. This used to be `still_blocking`, which
    read as a repair that failed; it is a repair with nothing to win. `credit.absolve` settles
    the leak on the page and the leak the rewrite would leave on identical terms."""
    result, patched = _leak_run(monkeypatch, [
        verify.Finding("leak", verify.BLOCKING, "gen-001-s1", "still readable",
                       field_id="allergies"),
    ])
    assert result.outcome == "neutral"
    assert patched is None, "a null move must never be written"
    assert result.rejected[0]["gate"] == "no_progress"
    assert result.suggested == (), "there is nothing for a person to decide here"


def test_a_leak_swapped_for_a_leak_on_another_field_is_also_neutral(monkeypatch):
    """The other shape, and the one `MAX_COLLATERAL` would otherwise wave through: the target
    leak IS cleared, and a fresh one lands somewhere else. Net blocking unmoved."""
    result, patched = _leak_run(monkeypatch, [
        verify.Finding("leak", verify.BLOCKING, "gen-001-s1", "readable, unevidenced",
                       field_id="planning_style"),
    ])
    assert result.outcome == "neutral"
    assert patched is None
    assert result.rejected[0]["gate"] == "no_progress"
    assert [f.split("/")[0] for f in result.rejected[0]["why"]] == ["leak"]


def test_a_leak_actually_cleared_is_still_accepted(monkeypatch):
    """`leak -> none`, the transition the whole pass exists for. The null-move rule must not
    swallow it."""
    result, patched = _leak_run(monkeypatch, [])
    assert result.outcome == "fixed"
    assert patched is not None


def test_a_null_move_does_not_end_the_repair_early(monkeypatch):
    """A stalled attempt is not damage, so the remaining attempts still run — the next draw
    may clear the leak outright, and that one is worth having."""
    invoke = Recorder(default='{"messages": {"2": "nothing the kitchen needs to know"}}')
    verdicts = [
        [verify.Finding("leak", verify.BLOCKING, "gen-001-s1", "still readable",
                        field_id="allergies")],
        [],
    ]
    monkeypatch.setattr(editor, "reconcile",
                        lambda record, item, verdict: verdicts.pop(0))
    finding = _leak_finding()
    result, patched = editor.edit_session(
        invoke, "fake-editor", _record(), _item(), finding,
        verify_invoke=Recorder(default=_clean_verdict()), max_attempts=2, baseline=[finding],
    )
    assert result.outcome == "fixed"
    assert patched is not None
    assert result.attempts == 2
    assert result.rejected[0]["gate"] == "no_progress"


def test_a_repair_that_stalls_and_regresses_stays_unresolved(monkeypatch):
    """Damage outranks the null move, and the ordering is load-bearing: `neutral` is a quiet
    outcome, and a candidate that is both futile and dangerous must not be filed quietly."""
    result, _patched = _leak_run(monkeypatch, [
        verify.Finding("leak", verify.BLOCKING, "gen-001-s1", "still readable",
                       field_id="allergies"),
        verify.Finding("contradiction", verify.BLOCKING, "gen-001-s1", "contradicts the card",
                       field_id="spice_tolerance"),
    ])
    assert result.outcome == "unresolved"
    assert result.rejected[0]["gate"] == "regression"


def test_the_null_move_does_not_apply_to_a_defect_credit_cannot_settle(monkeypatch):
    """`contradiction -> leak` is the trade the cap was relaxed for and stays accepted. The
    rule reads off `credit.ABSOLVABLE_KINDS`, so it is scoped to targets already in that lane.
    """
    assert editor._no_progress(
        [_blocking("leak", "allergies")], prior=set(),
        target=("allergies", "contradiction")) == []


def test_a_repair_may_cost_one_leak_on_another_field_and_says_so(monkeypatch):
    """The n=15 rule, end to end: the contradiction is cleared, one leak lands on a field
    nobody was repairing, the repair is accepted, and the price is on the record."""
    finding = verify.Finding(
        "contradiction", verify.BLOCKING, "gen-001-s1", "denies a constraint the card gives",
        field_id="allergies", messages=(2,),
    )
    invoke = Recorder(default='{"messages": {"2": "nothing beyond the shellfish, thanks"}}')
    monkeypatch.setattr(editor, "reconcile", lambda record, item, verdict: [
        verify.Finding("leak", verify.BLOCKING, "gen-001-s1", "readable, unevidenced",
                       field_id="planning_style"),
    ])

    result, patched = editor.edit_session(
        invoke, "fake-editor", _record(), _item(), finding,
        verify_invoke=Recorder(default=_clean_verdict()), max_attempts=1, baseline=[finding],
    )

    assert result.outcome == "fixed"
    assert patched is not None
    assert [(e["kind"], e["field_id"]) for e in result.collateral] == [
        ("leak", "planning_style")]
    assert result.traded == (), "the trade was on another field, not the target's"
    assert result.suggested == (), "inside the cap is not a suggestion"
    assert result.to_json()["collateral"] == [dict(e) for e in result.collateral]


def test_a_repair_that_costs_more_than_the_cap_is_refused_and_suggested(monkeypatch):
    """`gen-013-s0`'s shape: one contradiction cleared, four leaks minted.

    The candidate is not wrong — the defect it was aimed at is gone and nothing it introduced
    is out of `credit`'s reach — it is expensive, and how much of the answer key to send to a
    credit pass is an owner's call. So the page is left alone AND the candidate is kept.
    """
    finding = verify.Finding(
        "contradiction", verify.BLOCKING, "gen-001-s1", "denies a constraint the card gives",
        field_id="allergies", messages=(2,),
    )
    invoke = Recorder(default='{"messages": {"2": "nothing beyond the shellfish, thanks"}}')
    monkeypatch.setattr(editor, "reconcile", lambda record, item, verdict: [
        verify.Finding("leak", verify.BLOCKING, "gen-001-s1", "readable, unevidenced",
                       field_id=fid)
        for fid in ("planning_style", "travel_radius", "deal_seeking")
    ])

    result, patched = editor.edit_session(
        invoke, "fake-editor", _record(), _item(), finding,
        verify_invoke=Recorder(default=_clean_verdict()), max_attempts=1, baseline=[finding],
    )

    assert result.outcome == "unresolved"
    assert patched is None, "the page must be left exactly as it was"
    assert result.rejected[0]["gate"] == "collateral"
    assert len(result.suggested) == 1
    suggestion = result.suggested[0]
    assert sorted(e["field_id"] for e in suggestion["collateral"]) == [
        "deal_seeking", "planning_style", "travel_radius"]
    assert suggestion["proposed"] == {2: "nothing beyond the shellfish, thanks"}, (
        "a suggestion has to carry enough to apply it")
    assert result.to_json()["suggested"] == [dict(e) for e in result.suggested]


def test_a_candidate_refused_by_another_gate_is_never_offered_as_a_suggestion(monkeypatch):
    """A suggestion says 'this costs too much'. A candidate that still blocks, regresses or
    downgrades says 'this is wrong', and offering it to a person to apply would be a lie."""
    finding = verify.Finding(
        "contradiction", verify.BLOCKING, "gen-001-s1", "denies a constraint the card gives",
        field_id="allergies", messages=(2,),
    )
    invoke = Recorder(default='{"messages": {"2": "nothing beyond the shellfish, thanks"}}')
    monkeypatch.setattr(editor, "reconcile", lambda record, item, verdict: [
        verify.Finding("leak", verify.BLOCKING, "gen-001-s1", "readable, unevidenced",
                       field_id=fid)
        for fid in ("planning_style", "travel_radius")
    ] + [
        verify.Finding("contradiction", verify.BLOCKING, "gen-001-s1", "still wrong",
                       field_id="allergies"),
    ])

    result, _patched = editor.edit_session(
        invoke, "fake-editor", _record(), _item(), finding,
        verify_invoke=Recorder(default=_clean_verdict()), max_attempts=1, baseline=[finding],
    )

    assert result.outcome == "unresolved"
    assert result.rejected[0]["gate"] == "still_blocking"
    assert result.suggested == ()


def test_an_accepted_repair_records_what_it_traded_the_defect_for(monkeypatch):
    """The `gen-002-s9` shape end to end: the target contradiction is gone, the re-audit
    re-files the same field as a leak, the candidate is accepted, and the trade is written
    down rather than lost."""
    finding = verify.Finding(
        "contradiction", verify.BLOCKING, "gen-001-s1", "denies a constraint the card gives",
        field_id="allergies", messages=(2,),
    )
    invoke = Recorder(default='{"messages": {"2": "nothing beyond the shellfish, thanks"}}')
    verify_invoke = Recorder(default=_clean_verdict())

    # The re-audit of the accepted text raises a leak on the field being repaired — the
    # defect moved out of the editor's lane and into credit's.
    monkeypatch.setattr(editor, "reconcile", lambda record, item, verdict: [
        verify.Finding("leak", verify.BLOCKING, "gen-001-s1", "readable, unevidenced",
                       field_id="allergies"),
    ])

    result, patched = editor.edit_session(
        invoke, "fake-editor", _record(), _item(), finding, verify_invoke=verify_invoke,
        baseline=[finding],
    )

    assert result.outcome == "fixed"
    assert patched is not None
    assert [(e["kind"], e["field_id"]) for e in result.traded] == [("leak", "allergies")]
    assert result.to_json()["traded"] == [dict(e) for e in result.traded]


def test_edit_session_rewrites_every_message_a_finding_names():
    """`gen-001-s1`'s real shape: the fix is the bot's question in message 1, not only the
    user's answer in message 2."""
    finding = verify.Finding(
        "denied_constraint", verify.BLOCKING, "gen-001-s1",
        "denies allergies unbriefed", field_id="allergies",
        quote="do you follow any particular diet? / no, nothing like that",
        messages=(1, 2),
    )
    reply = ('{"messages": {"1": "sure — I\'ll pass the details on now.", '
             '"2": "great, thanks"}}')
    invoke = Recorder(default=reply)
    verify_invoke = Recorder(default=_clean_verdict())

    result, patched = editor.edit_session(
        invoke, "fake-editor", _record(), _item(), finding, verify_invoke=verify_invoke,
    )

    assert result.outcome == "fixed"
    assert result.messages == (1, 2)
    turns = transcript.speaker_turns(patched["transcript"])
    assert turns[1] == ("BOT", "sure — I'll pass the details on now.")
    assert turns[2] == ("USER", "great, thanks")
    # untouched messages, and the numbering, survive
    assert turns[0] == ("USER", "hi, table for two tonight")
    assert transcript.message_numbers(patched["transcript"]) == [0, 1, 2, 3]


def test_a_reply_that_rewrites_the_wrong_messages_is_rejected():
    """Strict in both directions: a half-repair would be judged as a whole one, and an extra
    message would be the editor widening its own remit."""
    finding = verify.Finding(
        "denied_constraint", verify.BLOCKING, "gen-001-s1", "denies unbriefed",
        field_id="allergies", quote="no, nothing like that", messages=(1, 2),
    )
    invoke = Recorder(default='{"messages": {"1": "only half of it"}}')
    verify_invoke = Recorder(default=_clean_verdict())
    result, patched = editor.edit_session(
        invoke, "fake-editor", _record(), _item(), finding, verify_invoke=verify_invoke,
        max_attempts=2,
    )
    assert result.outcome == "unresolved" and patched is None
    assert all(entry["gate"] == "reply" for entry in result.rejected)


def test_every_rejected_candidate_is_kept_with_the_gate_that_refused_it():
    """The pilot recorded `proposed: null` on all 16 failures and dropped the candidates, so
    diagnosing them needed a separate instrumented re-run."""
    finding = verify.Finding(
        "denied_constraint", verify.BLOCKING, "gen-001-s1", "denies unbriefed",
        field_id="allergies", quote="no, nothing like that", messages=(2,),
    )
    invoke = Recorder(default='{"messages": {"2": "I always avoid shellfish"}}')
    verify_invoke = Recorder(default=_clean_verdict())
    result, _ = editor.edit_session(
        invoke, "fake-editor", _record(), _item(), finding, verify_invoke=verify_invoke,
        max_attempts=2,
    )
    assert result.outcome == "unresolved"
    assert len(result.rejected) == 2
    assert result.rejected[0]["gate"] == "check_session"       # "I always" is §6.1 rule 3
    assert result.rejected[0]["proposed"] == {2: "I always avoid shellfish"}


# --- v0.4: deletion, renumbering, and following the remap ---------------------


def test_the_editor_can_delete_an_exchange_and_the_page_renumbers():
    """`gen-001-s1`'s real repair: the bot asked something the session never briefed, and no
    answer to it is both true to the card and permitted by the map. The fix is that the
    question is not there."""
    finding = verify.Finding(
        "denied_constraint", verify.BLOCKING, "gen-001-s1",
        "denies allergies unbriefed", field_id="allergies",
        quote="do you follow any particular diet? / no, nothing like that",
        messages=(1, 2),
    )
    invoke = Recorder(default='{"messages": {"1": null, "2": null}}')
    verify_invoke = Recorder(default=_clean_verdict())

    result, patched = editor.edit_session(
        invoke, "fake-editor", _record(), _item(), finding, verify_invoke=verify_invoke,
    )

    assert result.outcome == "fixed"
    assert result.deleted == (1, 2)
    turns = transcript.speaker_turns(patched["transcript"])
    assert len(turns) == 2
    assert turns == [("USER", "hi, table for two tonight"),
                     ("BOT", "got it, I'll send the booking through")]
    assert transcript.message_numbers(patched["transcript"]) == [0, 1]
    assert transcript.check_session(
        patched["transcript"], require_numbering=True
    ) == []
    assert result.remap == {1: None, 2: None, 3: 1}


def test_deleting_one_half_of_an_exchange_is_rejected_for_leaving_speakers_doubled():
    finding = verify.Finding(
        "denied_constraint", verify.BLOCKING, "gen-001-s1", "denies unbriefed",
        field_id="allergies", quote="no, nothing like that", messages=(1,),
    )
    invoke = Recorder(default='{"messages": {"1": null}}')
    verify_invoke = Recorder(default=_clean_verdict())
    result, patched = editor.edit_session(
        invoke, "fake-editor", _record(), _item(), finding, verify_invoke=verify_invoke,
        max_attempts=1,
    )
    assert result.outcome == "unresolved" and patched is None
    assert result.rejected[0]["gate"] == "check_session"
    assert any("consecutive" in w for w in result.rejected[0]["why"])


def test_an_empty_string_is_a_failed_attempt_and_null_is_a_decision():
    finding = verify.Finding(
        "denied_constraint", verify.BLOCKING, "gen-001-s1", "denies unbriefed",
        field_id="allergies", quote="no, nothing like that", messages=(2,),
    )
    invoke = Recorder(default='{"messages": {"2": "   "}}')
    result, _ = editor.edit_session(
        invoke, "fake-editor", _record(), _item(), finding,
        verify_invoke=Recorder(default=_clean_verdict()), max_attempts=1,
    )
    assert result.rejected[0]["gate"] == "reply"
    assert "use null to delete" in result.rejected[0]["why"]


def test_a_later_finding_follows_the_renumbering_a_deletion_caused():
    """Without this the second repair on a shortened session rewrites the wrong message —
    silently, because the stale number is still a valid one."""
    later = verify.Finding(
        "leak", verify.BLOCKING, "gen-001-s1", "readable here",
        field_id="planning_style", quote="got it", messages=(3,),
    )
    moved = editor.remap_finding(later, {1: None, 2: None, 3: 1})
    assert moved.messages == (1,) and moved.turn_index == 1


def test_a_finding_whose_messages_were_all_deleted_is_dropped_not_carried():
    gone = verify.Finding(
        "leak", verify.BLOCKING, "gen-001-s1", "readable here",
        field_id="allergies", quote="no, nothing", messages=(1, 2),
    )
    assert editor.remap_finding(gone, {1: None, 2: None, 3: 1}) is None


def test_a_replacement_only_repair_renumbers_nothing():
    finding = verify.Finding(
        "denied_constraint", verify.BLOCKING, "gen-001-s1", "denies unbriefed",
        field_id="allergies", quote="no, nothing like that", messages=(1, 2),
    )
    reply = '{"messages": {"1": "sure, sending that on now.", "2": "great, thanks"}}'
    result, patched = editor.edit_session(
        Recorder(default=reply), "fake-editor", _record(), _item(), finding,
        verify_invoke=Recorder(default=_clean_verdict()),
    )
    assert result.outcome == "fixed"
    assert result.deleted == () and result.remap == {}
    assert transcript.message_numbers(patched["transcript"]) == [0, 1, 2, 3]


# --- repair_corpus: the parallel driver -------------------------------------
#
# `repair_corpus` runs one worker per SESSION. The claim it rests on is that a repair's two
# order-dependent inputs — `baseline` and a deletion's `remap` — are both scoped to one page,
# so pages were already independent when the driver was a serial loop. These tests hold that
# claim to the only standard that matters: the same report, run at one worker and at eight,
# produces the same edit log.


class Threadsafe:
    """A rewriter that returns the same reply however many workers ask it at once.

    `Recorder` hands out scripted replies *in order*, so under four workers the reply a
    session gets becomes a function of thread scheduling rather than of the session. Nothing
    below depends on which page is rewritten first, so the reply is constant instead.
    """

    def __init__(self, rewrite: str) -> None:
        self.rewrite = rewrite

    def __call__(self, model: str, system: str, prompt: str) -> str:
        return self.rewrite


def _corpus(task_ids):
    """One record per session, all carrying the same repairable defect."""
    return {
        task_id: _record(task_id=task_id, subject_id="gen-001")
        for task_id in task_ids
    }


def _findings(task_ids):
    quote = ("are there do you follow any particular diet? / "
             "no, nothing like that")
    out = []
    for task_id in task_ids:
        finding = verify.Finding(
            "denied_constraint", verify.BLOCKING, task_id,
            "denies allergies unbriefed", field_id="allergies", quote=quote,
        )
        out.append(verify._located(finding, {task_id: _record(task_id=task_id)}))
    return out


def _run(task_ids, *, workers):
    records = _corpus(task_ids)
    written = []
    results = editor.repair_corpus(
        Threadsafe('{"turn": "no, no particular diet to flag"}'),
        "fake-editor", _findings(task_ids),
        records=records, items={"gen-001": _item()},
        write=written.append, verify_invoke=Recorder(default=_clean_verdict()),
        workers=workers,
    )
    return results, written, records


def test_repair_corpus_fixes_every_page():
    task_ids = [f"gen-001-s{n}" for n in range(1, 9)]
    results, written, records = _run(task_ids, workers=4)

    assert [r.outcome for r in results] == ["fixed"] * 8
    assert len(written) == 8
    # `records` is updated in place so a page's later findings read its earlier repairs.
    for task_id in task_ids:
        assert "no, no particular diet to flag" in records[task_id]["transcript"]


def test_eight_workers_and_one_produce_the_same_edit_log():
    """The whole justification for parallelising. If this ever fails, the grouping is wrong —
    not the editor."""
    task_ids = [f"gen-001-s{n}" for n in range(1, 9)]
    serial, _, _ = _run(task_ids, workers=1)
    parallel, _, _ = _run(task_ids, workers=8)

    assert [r.to_json() for r in serial] == [r.to_json() for r in parallel]


def test_results_come_back_in_report_order_not_completion_order():
    """Thread completion order must not reach the artifact, or two runs over one report
    produce edit logs that diff for no reason anybody can act on."""
    task_ids = [f"gen-001-s{n}" for n in (7, 3, 5, 1)]
    results, _, _ = _run(task_ids, workers=4)

    assert [r.task_id for r in results] == task_ids


def test_two_findings_on_one_page_still_run_in_order_and_follow_the_renumbering():
    """The sequential half of the contract. A deletion renumbers the page under the finding
    queued behind it, and grouping by session is what keeps that repair local and correct."""
    first = verify.Finding(
        "denied_constraint", verify.BLOCKING, "gen-001-s1",
        "denies allergies unbriefed", field_id="allergies",
        quote="do you follow any particular diet? / no, nothing like that",
        messages=(1, 2),
    )
    second = verify.Finding(
        "leak", verify.BLOCKING, "gen-001-s1", "readable here",
        field_id="planning_style", quote="I'll send the booking through", messages=(3,),
    )
    seen: list[tuple[int, ...]] = []

    class Watcher(Threadsafe):
        """Records the message numbers each rewrite prompt was actually shown."""

        def __call__(self, model, system, prompt):
            seen.append(tuple(int(n) for n in re.findall(r"^\[(\d+)\]", prompt, re.M)))
            return super().__call__(model, system, prompt)

    records = {"gen-001-s1": _record()}
    results = editor.repair_corpus(
        Watcher('{"messages": {"1": null, "2": null}}'),
        "fake-editor", [first, second],
        records=records, items={"gen-001": _item()},
        write=lambda record: None, verify_invoke=Recorder(default=_clean_verdict()),
        workers=4,
    )

    # The deletion lands first and shortens the page from 4 messages to 2.
    assert results[0].outcome == "fixed" and results[0].remap == {1: None, 2: None, 3: 1}
    assert transcript.message_numbers(records["gen-001-s1"]["transcript"]) == [0, 1]
    # The second finding named message 3, which is now message 1. It must not have been
    # attempted against the stale number — that is a valid message, just the wrong one.
    assert results[1].messages == (1,)
    # And the second rewrite was shown the shortened page, not the one the deletion replaced:
    # message 3 exists in the prompt the first repair saw and nowhere in the second's.
    assert 3 in seen[0] and 3 not in seen[-1]


def test_a_page_with_no_record_is_reported_rather_than_dropped():
    records = _corpus(["gen-001-s1"])
    results = editor.repair_corpus(
        Threadsafe('{"turn": "x"}'), "fake-editor",
        _findings(["gen-001-s1", "gen-009-s4"]),
        records=records, items={"gen-001": _item()},
        write=lambda record: None, verify_invoke=Recorder(default=_clean_verdict()),
        workers=4,
    )
    assert [r.outcome for r in results] == ["fixed", "unlocatable"]


def test_on_session_fires_once_per_page_with_that_pages_results():
    task_ids = [f"gen-001-s{n}" for n in range(1, 6)]
    fired: list[tuple[str, int]] = []
    records = _corpus(task_ids)
    editor.repair_corpus(
        Threadsafe('{"turn": "no, no particular diet to flag"}'),
        "fake-editor", _findings(task_ids),
        records=records, items={"gen-001": _item()},
        write=lambda record: None, verify_invoke=Recorder(default=_clean_verdict()),
        workers=4, on_session=lambda t, out, log: fired.append((t, len(out))),
    )
    assert sorted(fired) == sorted((t, 1) for t in task_ids)


def test_only_blocking_locatable_findings_are_attempted():
    """A NOTE is not the editor's business, and an unlocatable finding has nothing to splice."""
    note = verify.Finding(
        "disagreement", verify.NOTE, "gen-001-s2", "reinforcement",
        field_id="allergies", quote="no, nothing like that",
    )
    unlocatable = verify.Finding(
        "undelivered", verify.BLOCKING, "gen-001-s3", "the beat never landed",
        field_id="allergies",
    )
    invoke = Threadsafe('{"turn": "no, no particular diet to flag"}')
    results = editor.repair_corpus(
        invoke, "fake-editor", _findings(["gen-001-s1"]) + [note, unlocatable],
        records=_corpus(["gen-001-s1", "gen-001-s2", "gen-001-s3"]),
        items={"gen-001": _item()},
        write=lambda record: None, verify_invoke=Recorder(default=_clean_verdict()),
        workers=4,
    )
    assert [r.task_id for r in results] == ["gen-001-s1"]


def test_the_gate_baseline_can_come_from_a_different_report_than_the_work_list():
    """The bug this parameter exists for, reproduced.

    Run the editor over a report `credit.absolve` has demoted a leak in, and the gate's own
    re-audit — which knows nothing about credit — raises that leak again on the candidate.
    Against a credited baseline it looks introduced; against the raw baseline it is what it
    actually is, a defect the page already had.

    **What that costs moved when `MAX_COLLATERAL` landed, and the parameter still matters.**
    It used to be a refusal: one re-raised leak was a regression and the repair was thrown
    away. Now a single introduced leak is inside the cap, so the repair is accepted — and the
    page's own pre-existing leak is written to `EditResult.collateral` as one the editor
    minted. That is worse than the refusal in one specific way and better in every other: the
    repair lands, but the only artifact that can tell an editor-written leak from an
    author-written one is now wrong about this one, which is exactly what `_collateral`'s
    docstring says the record is for. A quieter failure is a reason to keep testing it, not
    to stop.
    """
    target = verify.Finding(
        "denied_constraint", verify.BLOCKING, "gen-001-s1", "denies allergies unbriefed",
        field_id="allergies", quote="no, nothing like that", messages=(2,),
    )
    #: The same page also leaks `planning_style`, credited and demoted to a note.
    absolved = verify.Finding(
        "leak", verify.NOTE, "gen-001-s1", "credited to WEAK by ADR-0046",
        field_id="planning_style", quote="tonight", messages=(0,),
    )
    #: What the gate's uncredited re-audit says about the candidate: the leak, still there.
    verdict = (
        '{"beats": [{"field_id": "dietary", "delivered": true, "strength": "pinned", '
        '"whose": "user", "moments": 1, "quote": "no particular diet"}], '
        '"unscheduled": [{"field_id": "planning_style", "readable": true, '
        '"quote": "tonight", "messages": [0]}], "contradictions": []}'
    )

    # A leak is only BLOCKING where the map calls the cell UNEVIDENCED, so the item needs a
    # real map at this session's checkpoint — an empty one makes the finding a note and the
    # bug cannot appear at all.
    item = dict(_item())
    item["card"] = {**item["card"], "planning_style": "decides_same_day"}
    item["provisional"] = {"1": {fid: "UNEVIDENCED" for fid in fields.ALL_FIELDS_IDS}}

    def run(baseline_findings):
        return editor.repair_corpus(
            Threadsafe('{"messages": {"2": "no, no particular diet to flag"}}'),
            "fake-editor", [target, absolved],
            records={"gen-001-s1": _record()}, items={"gen-001": item},
            write=lambda record: None, baseline_findings=baseline_findings,
            verify_invoke=Recorder(default=verdict), workers=1,
        )

    # Credited baseline: planning_style is a NOTE there, so the gate reads the re-raised
    # leak as introduced. Inside the cap it no longer refuses the repair — it misfiles the
    # page's own leak as one this edit put there.
    credited = run(None)[0]
    assert credited.outcome == "fixed"
    assert [e["field_id"] for e in credited.collateral] == ["planning_style"]

    # Raw baseline: the same leak is listed blocking, so it is inherited, not introduced,
    # and the repair costs nothing.
    raw = verify.Finding(
        "leak", verify.BLOCKING, "gen-001-s1", "readable here",
        field_id="planning_style", quote="tonight", messages=(0,),
    )
    uncredited = run([target, raw])[0]
    assert uncredited.outcome == "fixed"
    assert uncredited.collateral == ()


# --- the editor is shown what a value means (ADR-0048) ----------------------

def test_the_rewrite_prompt_carries_the_level_anchors():
    """The editor and the reader must be aiming at the same target. In the pilot they were
    not: the editor rewrote against a field *name* and the reader judged the result against
    a field *definition*, and `gen-004-s8` fell straight into the gap."""
    prompt = editor._rewrite_prompt(
        page="[0] USER: hi\n[1] BOT: hello\n[2] USER: thirty to fifty each is fine",
        targets=[(2, "USER", "thirty to fifty each is fine")],
        field_id="deal_seeking", card_value="ignores_price", move="reaction",
        attributed=schedule.USER, defect="the user names a per-head ceiling",
    )
    assert "Effort spent finding a better price" in prompt
    assert "checks for a discount once they have already chosen the place" in prompt


def test_the_rewrite_prompt_says_how_to_read_a_distribution_only_for_one():
    """The rule is attached to the field at issue, not sprayed over every repair — an
    ordinal repair that also had to reason about bins would be reading a rule for a field
    it is not touching."""
    def build(field_id, card_value):
        return editor._rewrite_prompt(
            page="[0] USER: hi", targets=[(0, "USER", "hi")], field_id=field_id,
            card_value=card_value, move="", attributed=schedule.USER, defect="x",
        )
    head = fields.DISTRIBUTION_READING_RULE.splitlines()[0]
    assert head in build("price_band_distribution", {"£30-50": 0.45, "£50+": 0.54})
    assert head not in build("deal_seeking", "ignores_price")


# --- deletion marking (ADR pending, owner-approved 2026-08-21) ------------------------
#
# Some defects are not a badly written message but a message that should not be there. The
# reader PROPOSES a removal; nothing trusts it. `gen-001-s1` is the case that motivated it:
# the 2026-08-21 editor spent all 3 attempts on it and failed, every rejection at `regression`.


def _removal_finding(**kw):
    base = dict(kind="denied_constraint", severity=verify.BLOCKING, task_id="gen-001-s1",
                detail="d", field_id="allergies", messages=(7, 8))
    base.update(kw)
    return verify.Finding(**base)


def test_deletable_requires_a_proposal():
    assert not _removal_finding().deletable


def test_deletable_accepts_a_subset_of_the_span():
    assert _removal_finding(removable_messages=(7, 8)).deletable
    assert _removal_finding(removable_messages=(8,)).deletable


def test_deletable_refuses_a_removal_beyond_the_defects_span():
    """A reader must not be able to propose deleting a page."""
    assert not _removal_finding(messages=(7, 8), removable_messages=(7, 8, 9, 10)).deletable


def test_removable_survives_the_json_round_trip():
    """The reader and the editor meet at a file; a field that lives only in memory is a
    field the editor never sees."""
    f = _removal_finding(removable_messages=(7, 8))
    assert f.to_json()["removable"] == [7, 8]


def test_removable_numbers_are_validated_like_a_span():
    entry = {"messages": [7, 8], "removable": [7, "x", -1, 8, 8]}
    assert verify._removable(entry) == (7, 8)


def test_remap_follows_removable_messages():
    """An earlier deletion renumbers the page. A stale removable number still resolves — it
    just names a different message, and a deletion acting on the wrong one destroys text."""
    f = _removal_finding(messages=(11, 12), removable_messages=(11, 12))
    moved = editor.remap_finding(f, {11: 9, 12: 10})
    assert moved.messages == (9, 10)
    assert moved.removable_messages == (9, 10)


def test_beats_survive_flags_a_deletion_that_removes_a_scheduled_fact():
    """The gate deletion needs and a rewrite does not. `check_session` reads beats for
    disclosure/intake/screening and never for whether a beat was delivered at all."""
    original = "[0] USER: I need somewhere vegan for four.\n[1] BOT: Noted."
    candidate = "[0] USER: I need somewhere for four.\n[1] BOT: Noted."
    beats = [{"field_id": "dietary", "gold_value": "vegan"}]
    assert editor.beats_survive(original, candidate, beats) == ["dietary=vegan"]
    assert editor.beats_survive(original, original, beats) == []


def test_beats_survive_ignores_a_beat_the_page_never_carried():
    """Failing a deletion for a defect it did not cause would refuse repairs for reasons
    unrelated to them."""
    original = "[0] USER: anywhere for four.\n[1] BOT: Noted."
    candidate = "[0] USER: anywhere.\n[1] BOT: Noted."
    beats = [{"field_id": "dietary", "gold_value": "vegan"}]
    assert editor.beats_survive(original, candidate, beats) == []


def test_beats_survive_reads_words_not_substrings():
    """The stage-19 case, exactly. `spice_tolerance=avoids_heat` on `gen-013-s24`: the gold
    token *heat* was findable in the original ONLY inside the word **wheat**, in the clause the
    repair removed — so the substring version reported the beat lost while the reader read it
    `delivered`/`pinned` off an untouched turn. A false NEGATIVE here is documented and
    accepted; a false POSITIVE refuses a repair that was fine."""
    original = ("[0] USER: nothing with chilli in it, I don't want anything that comes hot.\n"
                "[1] USER: I'll check the wheat with them when we order.")
    candidate = "[0] USER: nothing with chilli in it, I don't want anything that comes hot."
    beats = [{"field_id": "spice_tolerance", "gold_value": "avoids_heat"}]
    assert editor.beats_survive(original, candidate, beats) == []


def test_beats_survive_still_catches_a_word_that_really_left():
    """The word-boundary fix must not turn the gate off."""
    original = "[0] USER: I need somewhere with proper heat on the menu."
    candidate = "[0] USER: I need somewhere with a menu."
    beats = [{"field_id": "spice_tolerance", "gold_value": "avoids_heat"}]
    assert editor.beats_survive(original, candidate, beats) == ["spice_tolerance=avoids_heat"]


def test_a_repair_clears_the_authors_run_state_and_keeps_the_attempt_history():
    """`exhausted`, `error`, `problems` and `problem_kinds` describe the text a repair just
    replaced. `verify._run_state` reads `exhausted` and `error` straight off the record, so
    carrying them forward is a permanent blocking finding no rewrite can reach — and because
    `reconcile` runs `_run_state` inside the editor's own gate, it is also a finding present in
    every candidate, which would refuse every repair that page is ever offered. `attempts` is
    the authoring history and stays true, so it is kept."""
    record = {
        "task_id": "gen-007-s9", "transcript": "old", "free_offers": [],
        "exhausted": True, "error": "boom",
        "problems": ["[speaker_repeats] user turn 1: turn 4 is a second consecutive BOT turn"],
        "problem_kinds": ["speaker_repeats"],
        "attempts": [{"attempt": 0, "problems": ["..."]}],
    }
    patched = editor._repaired(record, transcript="new")
    assert patched["transcript"] == "new"
    assert patched["exhausted"] is False
    assert patched["error"] is None
    assert patched["problems"] == [] and patched["problem_kinds"] == []
    assert patched["attempts"] == record["attempts"]
    assert record["exhausted"] is True          # the input is not mutated


def test_deleted_counts_as_an_accepted_outcome_but_stays_distinct():
    """Folding `deleted` into `fixed` would hide which defects are only solved by removal —
    the thing the marking exists to find out."""
    result = editor.EditResult("t", "f", "k", 7, "BOT", "o", "DELETE [7, 8]", 0, "deleted")
    assert result.verified
    assert result.outcome != "fixed"
