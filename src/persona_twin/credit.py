"""Reader-derived evidence credit: the map counts what the page turned out to carry.

ADR-0034 put the **schedule** in charge of the map, and that is still right for what the
schedule can see. What it cannot see is a field the conversation form guarantees will be
readable whether or not anything scheduled it. ADR-0037 found the first instance (a
conversation about where to go cannot avoid saying who is coming and when) and counted those
as occasions; ADR-0044 found the second (an occasion entails a correlate the card ties it to)
and hardcoded two entries. This module is the general case, and the pilot is why it is needed:
`planning_style` is readable in 18 of 67 sessions, is **never scheduled at all** on two of the
five subjects, and 14 of the 18 readings are in *message 0* — you cannot book a table without
saying when you want it, and saying when you want it is stating your planning horizon. A
hardcoded table cannot anticipate which fields the conversation form guarantees. The reader
already knows, because it read the page.

**Three keys keep this from making one reader an oracle.**

    key 1 (the card)  the card holds a non-empty value for the field
    key 2 (the page)  the reader found the field readable here and raised no contradiction
                      on it in this session
    key 3 (a second reader)  an independent reader, on a different model, also reads the
                      field as present on this page

Key 3 was added 2026-08-20 after it was measured, and it is not a formality: of the 44 cells
the first two keys credited, **an independent reader could only reproduce 20**. The split was
not uniform — `planning_style`, which the conversation form guarantees, confirmed at 16 of 18
(88%), while everything else confirmed at 4 of 26 (15%). Without key 3 this module would have
put 24 cells into the answer key on one model's reading of text that same model wrote.

Key 3 is also not a new idea here. §7.3 already says a field is STRONG only where two
independent judges agree, and `evidence.EvidenceMap.from_judges` implements it as an
elementwise `min()`. This is that rule applied to the other end of the scale.

The card says *what is true*; the page says *what is readable*; a cell is credited only where
both agree. The reader may add evidence for a value the card already holds and may never
introduce one. Without key 1 the map would start describing people the generator never made.

**Never above WEAK.** ADR-0009/0010 measured that where a reader and the schedule disagree
about *strength*, the schedule is the more accurate record — which is why `over_delivered`
(the reader calling a narrowing move pinned) stays a note and is not in scope here. WEAK is
the band that says a reader could tell. Pinning is for the field's own scheduled evidence.

**Every credit carries provenance**, because a map that is partly derived and partly read has
to say which cell is which. `Credit.to_json` records the reader model and the fingerprint of
what it was shown, so a second reader can be pointed at exactly the credited set — a few dozen
calls — instead of re-reading the corpus to check whether the first reader's determination
holds. That check is the reason the provenance exists; ADR-0034's model-free answer key is
what it is protecting.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace as dc_replace
from typing import Any, Iterable, Mapping, Sequence

from . import fields
from .evidence import Grade
from .verify import NOTE

#: The grade a reader-derived credit may reach, and the ceiling it may never pass.
CREDIT_GRADE: str = "WEAK"

#: The blocking kinds this module can settle without anybody touching the page. `absolve`
#: demotes a credited `leak` and nothing else, and that is the whole list: a leak is the map
#: being wrong about what the page carries, which is a claim a credit can correct. Every other
#: blocking kind is a claim about the TEXT — a contradiction, a misattribution, a beat that
#: never landed — and no amount of crediting makes it go away, so the editor is the only place
#: it can be fixed.
#:
#: `editor` imports this rather than hardcoding `"leak"`, because the two modules are making
#: one decision between them: which defects have a second route out. A repair that moves a
#: defect OUT of this set has closed that route and is refused there (ADR-0056). If a kind is
#: ever added here, the editor's gate must loosen in the same commit or it will start
#: refusing repairs that are now perfectly good.
ABSOLVABLE_KINDS: frozenset[str] = frozenset({"leak"})

#: The two verdicts an owner adjudication can carry. A cell the readers split on is resolved
#: by hand or it is not resolved; there is deliberately no third value meaning "later", because
#: an undecided cell is already representable — by having no decision at all.
APPROVE: str = "approve"
REJECT: str = "reject"


@dataclass(frozen=True)
class Credit:
    """One field-checkpoint the reader found evidence for that the schedule did not place."""

    subject_id: str
    checkpoint: int
    field_id: str
    task_id: str
    #: Which messages the reader cited. This is what a second reader would be pointed at.
    messages: tuple[int, ...]
    quote: str
    reader_model: str
    #: Fingerprint of what the reader was shown, from the verdict record. Two readers agreeing
    #: on a cell means nothing unless they were shown the same page.
    instrument: str
    #: The independent readers that also read this field on this page (key 3). A credit
    #: without one is not written; this records *which* model, so a later disagreement can be
    #: traced to a reader rather than to the rule.
    confirmed_by: tuple[str, ...] = ()
    #: Set only where key 3's readers **disagreed** and the owner resolved it by hand. This is
    #: the third way a cell can be credited and it is deliberately the loudest: a credit
    #: carrying this is not a measurement, it is a judgement, and anything counting readers
    #: has to be able to tell the two apart. Never a substitute for key 1.
    adjudicated_by: str = ""

    @property
    def cell(self) -> tuple[str, int, str]:
        return (self.subject_id, self.checkpoint, self.field_id)

    def to_json(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id, "checkpoint": self.checkpoint,
            "field_id": self.field_id, "task_id": self.task_id,
            "messages": list(self.messages), "quote": self.quote,
            "grade": CREDIT_GRADE,
            "provenance": {
                "source": "reader",
                "model": self.reader_model,
                "instrument": self.instrument,
                "confirmed_by": list(self.confirmed_by),
                "readers": 1 + len(self.confirmed_by),
                "adjudicated_by": self.adjudicated_by,
            },
        }


def _has_value(card: Mapping[str, Any], field_id: str) -> bool:
    """Key 1. Empty is not a value: a person with no allergies has nothing to evidence, and a
    credit there would be evidence for a value that does not exist."""
    return card.get(field_id) not in (None, [], (), {}, False, "")


def read_fields(verdict: Mapping[str, Any]) -> set[str]:
    """Every field one verdict reads as present on the page.

    Both lists count. A second reader that files a field under `beats` rather than
    `unscheduled` has still read it — the two lists differ by whether the *schedule* placed
    it, which is a fact about the schedule and not about what the reader saw.
    """
    out = {str(e.get("field_id")) for e in (verdict.get("unscheduled") or ())}
    out |= {
        str(e.get("field_id")) for e in (verdict.get("beats") or ())
        if e.get("delivered")
    }
    return out


@dataclass(frozen=True)
class Candidate:
    """A cell that has passed keys 1 and 2 and is waiting on key 3.

    Split out so the *refused* half is addressable. `eligible` used to report its refusals as
    a bare Counter, which is enough to notice a reader drifting and not enough to do anything
    about it: "unconfirmed: 10" cannot be put to a second reader, shown in a UI, or adjudicated.
    Ten cells whose only defect was that nobody had asked about them sat blocking for exactly
    that reason.
    """

    subject_id: str
    checkpoint: int
    field_id: str
    task_id: str
    messages: tuple[int, ...]
    quote: str

    @property
    def cell(self) -> tuple[str, int, str]:
        return (self.subject_id, self.checkpoint, self.field_id)

    @property
    def key(self) -> tuple[str, str]:
        """How a confirmation or an adjudication addresses this candidate."""
        return (self.task_id, self.field_id)


def screen(
    findings: Sequence[Any], *, records: Mapping[str, Mapping[str, Any]],
    items: Mapping[str, Mapping[str, Any]],
) -> tuple[list[Candidate], Counter]:
    """Keys 1 and 2, which are free: no model call, no second reader.

    Everything here is a fact about the card and the map. Key 3 is the only key that costs
    anything, so it is applied last and only to what survives this.
    """
    contradicted = {
        (f.task_id, f.field_id) for f in findings
        if f.kind == "contradiction" and f.field_id
    }
    refused: Counter = Counter()
    out: list[Candidate] = []
    seen: set[tuple[str, int, str]] = set()

    for finding in findings:
        if finding.kind != "leak" or not finding.field_id:
            continue
        record = records.get(finding.task_id)
        if record is None:
            refused["no_record"] += 1
            continue
        subject_id = str(record["subject_id"])
        item = items.get(subject_id)
        if item is None:                                  # pragma: no cover - defensive
            refused["no_item"] += 1
            continue
        checkpoint = int(record["checkpoint"])

        if (finding.task_id, finding.field_id) in contradicted:
            # key 2 fails: the reader says this same field disagrees with the card here. It
            # cannot be evidence for the card's value and evidence against it at once.
            refused["contradicted"] += 1
            continue
        if not _has_value(item.get("card") or {}, finding.field_id):
            refused["card_has_no_value"] += 1
            continue
        # Key 1b (2026-08-28): the grade may not lead the value. For the pruned taste-list fields
        # the value must have surfaced in `card_at` **by this checkpoint**, not merely in the final
        # card. The final card carries the full set, so the check above passes even before an
        # element has surfaced; crediting there — and carrying the WEAK forward in `apply` — raised
        # the grade above an empty value (the WEAK-empty defect; gen-022 `cuisines_liked` fired
        # exactly here). Refusing keeps the cell UNEVIDENCED until the schedule surfaces the
        # element. A reader who genuinely reads it earlier than the schedule is a `card_at` fix,
        # not a grade raise; the conservative default never charges a twin for holding `[]`.
        if finding.field_id in fields.PRUNED_LIST_FIELDS:
            surfaced = (item.get("card_at") or {}).get(str(checkpoint)) or {}
            if not _has_value(surfaced, finding.field_id):
                refused["not_surfaced_at_checkpoint"] += 1
                continue
        grade = (item.get("provisional") or {}).get(str(checkpoint), {}).get(
            finding.field_id
        )
        if grade != Grade.UNEVIDENCED.name:
            # Only an unevidenced cell can be credited. A WEAK cell the reader also read is
            # agreement, not new evidence, and a STRONG one is out of reach by construction.
            refused[f"already_{str(grade).lower()}"] += 1
            continue
        cell = (subject_id, checkpoint, finding.field_id)
        if cell in seen:
            refused["duplicate_cell"] += 1
            continue
        seen.add(cell)
        out.append(Candidate(
            subject_id=subject_id, checkpoint=checkpoint, field_id=finding.field_id,
            task_id=finding.task_id, messages=tuple(finding.messages), quote=finding.quote,
        ))
    return out, refused


def eligible(
    findings: Sequence[Any], *, records: Mapping[str, Mapping[str, Any]],
    items: Mapping[str, Mapping[str, Any]], reader_model: str = "",
    verdicts: Mapping[str, Mapping[str, Any]] | None = None,
    confirmations: Mapping[str, Mapping[str, Any]] | None = None,
    require_confirmation: bool = True,
    adjudications: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
) -> tuple[list[Credit], Counter]:
    """Every cell the three-key rule credits, plus a tally of why the rest were refused.

    The tally is returned rather than logged because the refusals are the interesting half:
    a rule that credited everything offered would not be a rule, and the counts are what a
    later run compares against to notice the reader drifting.

    `adjudications` is the owner's resolution of cells where the two readers *disagreed*. It
    can only ever settle key 3 — an approval on a cell with no card value is still refused,
    because key 1 is what keeps the map describing the people the generator actually made,
    and no human sign-off is allowed past it.
    """
    candidates, refused = screen(findings, records=records, items=items)
    out: list[Credit] = []

    for candidate in candidates:
        confirmer, adjudicator = "", ""
        if require_confirmation:
            # key 3. Fails CLOSED: no second reading is not the same as a second reading that
            # agreed, and defaulting the other way is how 24 unreproducible cells nearly
            # reached the answer key.
            second = (confirmations or {}).get(candidate.task_id)
            if second and candidate.field_id in read_fields(second):
                confirmer = str(second.get("model") or "")
            else:
                decision = (adjudications or {}).get(candidate.key)
                if decision is None:
                    refused["unconfirmed" if second else "no_second_reading"] += 1
                    continue
                if str(decision.get("verdict")) != APPROVE:
                    refused["owner_rejected"] += 1
                    continue
                adjudicator = str(decision.get("decided_by") or "owner")
        verdict = (verdicts or {}).get(candidate.task_id) or {}
        out.append(Credit(
            subject_id=candidate.subject_id, checkpoint=candidate.checkpoint,
            field_id=candidate.field_id, task_id=candidate.task_id,
            messages=candidate.messages, quote=candidate.quote,
            reader_model=str(verdict.get("model") or reader_model),
            instrument=str(verdict.get("instrument") or ""),
            confirmed_by=(confirmer,) if confirmer else (),
            adjudicated_by=adjudicator,
        ))
    return out, refused


def apply(
    items: Mapping[str, Mapping[str, Any]], credits: Iterable[Credit]
) -> tuple[dict[str, dict], Counter]:
    """`items` with every credited cell moved to WEAK **and carried forward**, with provenance.

    Returns new items rather than mutating: the derived, model-free map (`ADR-0034`) stays
    exactly as `schedule.py` produced it, and the credited map is a separate artifact built on
    top. Anyone who wants the key without a model in it can still have it.

    **A credit raises every later checkpoint too, not just its own.** The map is monotone by
    construction (`docs/SCORING.md` Stage 1, §7.3's requirement "met rather than checked") because a
    checkpoint's page is a prefix of the next one's: evidence the reader could see at *t* is
    still on the page at *t+1*. Writing the credited checkpoint alone broke that — it left a
    WEAK island with UNEVIDENCED on both sides, which is the map telling a twin to *forget*
    something it was legitimately allowed to carry, and `test_evidence_grades_must_be_monotone`
    says in as many words that scoring it would penalise correct behaviour. Measured on the
    2026-08-25 n50 base before this change: 37 of 48 credits were islands, 141 cells, and 22 of
    50 subjects failed `EvidenceMap.validate()` on a map whose uncredited half passes.

    Only UNEVIDENCED cells are raised, so a later scheduled beat keeps its own grade and
    nothing is ever lowered. The carried cells record the same provenance with `carried_from`
    naming the checkpoint the reader actually read, because a map that is partly derived and
    partly read has to say which cell is which — and "a reader saw this here" and "this is
    still true here" are different claims.
    """
    out = {sid: dict(item) for sid, item in items.items()}
    moved: Counter = Counter()
    #: Cells this call raised by carrying, not by reading. A later credit landing on one of
    #: them is a reader who read the field *there* as well, and it must overwrite the carried
    #: provenance rather than be dropped: `eligible` has already refused every cell the
    #: schedule evidences, so the only way a credited cell is non-UNEVIDENCED here is a carry
    #: from this same call. Skipping it silently cost 11 of the 48 credits their provenance on
    #: the 2026-08-25 base — the map was identical either way, but the artifact no longer said
    #: which cells a reader had actually looked at.
    carried: set[tuple[str, str, str]] = set()
    for credit in sorted(credits, key=lambda c: (c.subject_id, c.field_id, c.checkpoint)):
        item = out.get(credit.subject_id)
        if item is None:                                  # pragma: no cover - defensive
            continue
        provisional = {cp: dict(g) for cp, g in (item.get("provisional") or {}).items()}
        checkpoint = str(credit.checkpoint)
        cell = (credit.subject_id, checkpoint, credit.field_id)
        grade = provisional.get(checkpoint, {}).get(credit.field_id)
        if grade != Grade.UNEVIDENCED.name and cell not in carried:
            continue
        provenance = {cp: dict(p) for cp, p in (item.get("evidence_provenance") or {}).items()}
        record = credit.to_json()["provenance"]
        provisional[checkpoint][credit.field_id] = CREDIT_GRADE
        provenance.setdefault(checkpoint, {})[credit.field_id] = record
        carried.discard(cell)
        for later in sorted(provisional, key=int):
            if int(later) <= credit.checkpoint:
                continue
            if provisional[later].get(credit.field_id) != Grade.UNEVIDENCED.name:
                continue
            provisional[later][credit.field_id] = CREDIT_GRADE
            provenance.setdefault(later, {})[credit.field_id] = {
                **record, "carried_from": credit.checkpoint,
            }
            carried.add((credit.subject_id, later, credit.field_id))
        item["provisional"] = provisional
        item["evidence_provenance"] = provenance
        # `must_unevidenced` is read off the map, not off the beats — ADR-0044, after 8 cells
        # came to be listed as never-evidenced while the same file's `provisional` graded them
        # WEAK. A credit is the other way into that gap, and the 2026-08-25 base was already in
        # it on two cells. One fact, one record: the list is re-read here from the map this
        # call just moved. Credit never reaches STRONG, so `must_strong` cannot change.
        if "must_unevidenced" in item:
            final = provisional[max(provisional, key=int)]
            item["must_unevidenced"] = type(item["must_unevidenced"])(
                fid for fid in item["must_unevidenced"]
                if final.get(fid) == Grade.UNEVIDENCED.name
            )
        moved[credit.field_id] += 1
    return out, moved


def absolve(
    findings: Sequence[Any], credits: Iterable[Credit],
    *, dismissed: Iterable[tuple[str, str]] = (),
) -> tuple[list[Any], int]:
    """The report with every leak a decision settled demoted from BLOCKING to a note.

    Two different settlements, one outcome, and they must not be confused with each other:

        credited   the MAP was wrong. ADR-0046's whole point is that a twin reading the page
                   correctly was being scored as fabricating; the cell moves to WEAK and there
                   is nothing left on the page to repair.
        dismissed  the READER was wrong, and a person said so. An owner rejection means the
                   cell stays UNEVIDENCED because the page does not carry the field after all
                   — so the map is right, and there is likewise nothing to repair.

    Either way, leaving the finding blocking would send the editor to rewrite a turn that is
    doing its job — the most expensive possible way to make a corpus worse — and would keep a
    question a person has already answered sitting in the blocking count, where it reads as
    outstanding work. A decision that does not change what is shown is a decision the next
    reader of the page has no way to know was ever made.

    `dismissed` is `(task_id, field_id)` pairs from *fresh* rejections only. A stale one must
    never silence a finding: the page it was decided on no longer exists, and the reading it
    refused may be about text nobody has looked at. `adjudicate.fresh` is what draws that line.

    Returns `(findings, demoted)` with new `Finding`s rather than mutating, for the same
    reason `apply` does: the reader's own report stays as the reader wrote it. The two reasons
    are distinguishable in the artifact by the sentence appended to `detail`.
    """
    credited = {(c.task_id, c.field_id) for c in credits}
    refused = {(str(t), str(f)) for t, f in dismissed}
    out, demoted = [], 0
    for finding in findings:
        key = (finding.task_id, finding.field_id)
        settled = key in credited or key in refused
        if (
            getattr(finding, "kind", "") in ABSOLVABLE_KINDS
            and settled
            and finding.severity != NOTE
        ):
            out.append(dc_replace(
                finding, severity=NOTE,
                detail=finding.detail + (
                    "; credited to WEAK by ADR-0046, so the map moved and the page needs no "
                    "repair" if key in credited else
                    "; a person read this page and did not uphold the reading, so the cell "
                    "stays UNEVIDENCED and the page needs no repair"
                ),
            ))
            demoted += 1
        else:
            out.append(finding)
    return out, demoted
