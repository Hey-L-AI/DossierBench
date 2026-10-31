"""The cells two readers split on, and the owner's resolution of them.

Key 3 (`credit.py`) is a two-reader agreement rule and it fails closed, which is right: a cell
one model read and another could not reproduce does not belong in an answer key on the strength
of the first model's confidence. But "fails closed" silently converts every genuine reader
disagreement into a blocking finding the editor is then sent to repair — and where the readers
split because the page really does carry something the map missed, repairing the page is the
wrong fix applied to the wrong artifact. Ten cells sat in that state after the post-edit pass.

So the split is made addressable instead of absorbed. `confirm.py` first removes the cheap half
of the disagreement by asking the second reader directly rather than reading its silence. What
survives *that* is a real disagreement between two models that were each asked the same question
about the same page, and it is resolved the only way a real disagreement can be: a person looks
at the excerpt and decides.

**What an adjudication can and cannot do.**

    can    settle key 3, the two-reader agreement, in either direction
    cannot reach key 1 — a cell whose card holds no value stays refused however it is voted,
           because key 1 is what stops the map describing a person the generator never made
    cannot reach above WEAK — `credit.CREDIT_GRADE` is the ceiling for everything derived from
           a reading, and a human reading is still a reading. STRONG stays with the schedule.

**A second decision this module carries, and it is not credit.** `credit.absolve` settles a
finding by moving the *map* (`credited`) or by a person refusing the reading behind a leak
(`dismissed`), and both are bounded by `credit.ABSOLVABLE_KINDS` — leaks only, by ADR-0046,
because a leak is a claim about what the page makes readable and nothing else is. But the
reader also files findings it argues against in its own `detail`: two of the store's
contradictions read *"not itself a contradiction"* and *"so no defect"* while carrying
`BLOCKING`. Nothing could settle those. An edit is the wrong instrument — the page is not
broken — and credit cannot reach them.

`Dismissal` is that instrument, and it is deliberately a different artifact from `Decision`:
a decision resolves a disagreement *between two readers* about a cell; a dismissal is a person
overruling *one reader* about a finding, on any kind. Same pinning, same staleness rule, and a
sentence appended to `detail` so the next reader of the report can tell which of the three
settlements happened.

**A decision is pinned to the page it was made on.** Every decision records the `instrument` —
the fingerprint of exactly what the reader was shown, transcript included. Edit the transcript
and the fingerprint changes, the decision goes stale, and the cell returns to the queue rather
than carrying an approval granted for text that no longer exists. This is the failure this
module is most careful about: a stale approval is invisible, survives every falsifier that
counts readers, and quietly puts a fabrication in the answer key. `load` reports staleness as a
first-class result, not a warning.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field as dc_field, replace as dc_replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import fields
from .confirm import Answer
from .credit import APPROVE, REJECT, Candidate, read_fields
from .verify import BLOCKING, NOTE

#: Where a decision comes from when the file does not say. Recorded rather than assumed
#: because "who decided this" is the entire content of an adjudication.
DEFAULT_DECIDER: str = "owner"


@dataclass(frozen=True)
class Decision:
    """One owner resolution of one split cell, pinned to the page it was made on."""

    task_id: str
    field_id: str
    verdict: str
    instrument: str
    subject_id: str = ""
    checkpoint: int = -1
    decided_by: str = DEFAULT_DECIDER
    decided_at: str = ""
    note: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.task_id, self.field_id)

    def fresh_for(self, instrument: str) -> bool:
        """Was this decided on the page that is on disk now?

        A decision carrying no instrument is treated as stale rather than as universal. The
        permissive reading — "no fingerprint means it applies to any version" — is exactly the
        stale approval this module exists to prevent, and hand-written entries are the likeliest
        source of a missing one.
        """
        return bool(self.instrument) and self.instrument == instrument

    def to_json(self) -> dict[str, Any]:
        return asdict(self)



@dataclass(frozen=True)
class Dismissal:
    """One owner refusal of one blocking finding, pinned to the page it was made on.

    Keyed on `(task_id, field_id, kind)` rather than `Decision`'s `(task_id, field_id)`,
    because a page can carry two findings of different kinds on one field and dismissing a
    contradiction must not silence a leak beside it.

    `note` is not optional in practice even though the dataclass allows it: a dismissal with
    no stated reason is indistinguishable from a mistake six months later, and this project
    has already learned that lesson about `free_offers` and `self_report`.
    """

    task_id: str
    field_id: str
    kind: str
    instrument: str
    note: str = ""
    decided_by: str = DEFAULT_DECIDER
    decided_at: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.task_id, self.field_id, self.kind)

    def fresh_for(self, instrument: str) -> bool:
        """Same rule as `Decision.fresh_for`, and for the same reason: no fingerprint means
        stale, never universal."""
        return bool(self.instrument) and self.instrument == instrument

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def load_dismissals(path: Path) -> tuple[dict[tuple[str, str, str], Dismissal], list[Dismissal]]:
    """Dismissals on disk, and the malformed ones, separated.

    An entry naming no `kind` is malformed rather than a wildcard. The permissive reading
    would let one dismissal silence every finding on a field, which is the same class of
    mistake `fresh_for` refuses for a missing instrument.
    """
    payload = json.loads(Path(path).read_text()) if Path(path).exists() else {}
    good: dict[tuple[str, str, str], Dismissal] = {}
    bad: list[Dismissal] = []
    for raw in payload.get("dismissals") or ():
        try:
            entry = Dismissal(**{
                k: v for k, v in raw.items() if k in Dismissal.__dataclass_fields__
            })
        except TypeError:                                 # pragma: no cover - defensive
            continue
        if not entry.task_id or not entry.field_id or not entry.kind:
            bad.append(entry)
            continue
        good[entry.key] = entry
    return good, bad


def save_dismissals(path: Path, dismissals: Iterable[Dismissal]) -> Path:
    """Write the file `load_dismissals` reads."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"dismissals": [d.to_json() for d in dismissals]}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return path


def dismiss(
    findings: Sequence[Any], dismissals: Mapping[tuple[str, str, str], Dismissal], *,
    records: Mapping[str, Mapping[str, Any]] | None = None,
    verdicts: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[list[Any], int, list[Dismissal]]:
    """The report with every blocking finding a person refused demoted to a note.

    Returns `(findings, demoted, stale)` with new `Finding`s rather than mutating, exactly as
    `credit.absolve` does and for the same reason: the reader's own report stays as the reader
    wrote it, and the three settlements stay distinguishable in the artifact by the sentence
    appended to `detail`.

    **Staleness is enforced here rather than trusted to the caller**, because the whole risk of
    this mechanism is a dismissal outliving the page it was granted for. A dismissal whose
    instrument no longer matches the reader's verdict for that session demotes nothing and is
    returned in `stale` to be reported.
    """
    out: list[Any] = []
    demoted = 0
    stale: list[Dismissal] = []
    for finding in findings:
        key = (finding.task_id, getattr(finding, "field_id", ""), getattr(finding, "kind", ""))
        entry = dismissals.get(key)
        if entry is None or finding.severity != BLOCKING:
            out.append(finding)
            continue
        record = (records or {}).get(finding.task_id) or {}
        verdict = (verdicts or {}).get(finding.task_id) or {}
        instrument = str(verdict.get("instrument") or record.get("instrument") or "")
        if not entry.fresh_for(instrument):
            # Stale, and reported as such. A dismissal whose finding is simply no longer in
            # the report is NOT stale — the page moved on and there is nothing to say — so
            # only a dismissal that met its finding and failed the pin is collected here.
            stale.append(entry)
            out.append(finding)
            continue
        demoted += 1
        who = entry.decided_by or DEFAULT_DECIDER
        out.append(dc_replace(
            finding, severity=NOTE,
            detail=finding.detail + (
                f"; {who} read this page and did not uphold the finding"
                + (f" — {entry.note}" if entry.note else "")
            ),
        ))
    return out, demoted, stale


@dataclass(frozen=True)
class Question:
    """One split cell, with everything needed to decide it and nothing else.

    This is what the UI renders and what a person actually looks at. It carries both readers'
    positions because the decision is between them — showing only the finding would ask the
    owner to rubber-stamp the first reader, which is the failure mode that made this necessary.
    """

    task_id: str
    subject_id: str
    checkpoint: int
    field_id: str
    #: The card's value. Shown because key 1 has already passed and the owner should see what
    #: the credit would be evidence *for* — not so it can be reasoned from. A reader may not
    #: see this; a person deciding between two readers must.
    card_value: Any
    messages: tuple[int, ...]
    excerpt: str
    first_reader: str
    first_quote: str
    second_reader: str
    #: The second reader's own words when it declined, which is the argument against crediting.
    #: Empty when it was never asked — a state that should not survive `confirm.py`, and is
    #: shown as such rather than dressed up as a refusal.
    second_reads_as: str
    second_asked: bool
    second_quote_missing: bool
    instrument: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.task_id, self.field_id)

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        out["messages"] = list(self.messages)
        return out


def excerpt(transcript: str, messages: Iterable[int], *, context: int = 0) -> str:
    """The cited messages, verbatim, from a `[n] SPEAKER: ...` transcript.

    Returns the whole page when nothing is cited: an excerpt that silently narrows to nothing
    would present a decision as if there were no evidence for it.
    """
    wanted = {int(m) for m in messages}
    if not wanted:
        return transcript
    if context:
        wanted |= {m + d for m in list(wanted) for d in range(-context, context + 1)}
    out, keep = [], False
    for line in (transcript or "").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("["):
            number = stripped[1:stripped.find("]")] if "]" in stripped else ""
            keep = number.isdigit() and int(number) in wanted
        if keep:
            out.append(line)
    return "\n".join(out)


def pending(
    candidates: Sequence[Candidate], *,
    records: Mapping[str, Mapping[str, Any]],
    items: Mapping[str, Mapping[str, Any]],
    confirmations: Mapping[str, Mapping[str, Any]] | None = None,
    answers: Sequence[Answer] = (),
    verdicts: Mapping[str, Mapping[str, Any]] | None = None,
    decisions: Mapping[tuple[str, str], Decision] | None = None,
    reader_model: str = "",
    second_model: str = "",
) -> list[Question]:
    """Every candidate the two readers still disagree about and nobody has resolved.

    Candidates the second reader confirms are not questions — they credit. Candidates with a
    *fresh* decision are not questions either. What is left is the queue.
    """
    answered = {(a.task_id, a.field_id): a for a in answers}
    out: list[Question] = []
    for candidate in candidates:
        second = (confirmations or {}).get(candidate.task_id)
        if second and candidate.field_id in read_fields(second):
            continue
        record = records.get(candidate.task_id) or {}
        verdict = (verdicts or {}).get(candidate.task_id) or {}
        instrument = str(verdict.get("instrument") or record.get("instrument") or "")
        decision = (decisions or {}).get(candidate.key)
        if decision is not None and decision.fresh_for(instrument):
            continue
        answer = answered.get(candidate.key)
        card = (items.get(candidate.subject_id) or {}).get("card") or {}
        out.append(Question(
            task_id=candidate.task_id, subject_id=candidate.subject_id,
            checkpoint=candidate.checkpoint, field_id=candidate.field_id,
            card_value=card.get(candidate.field_id),
            messages=candidate.messages,
            excerpt=excerpt(record.get("transcript") or "", candidate.messages),
            first_reader=str(verdict.get("model") or reader_model),
            first_quote=candidate.quote,
            second_reader=str(getattr(answer, "model", "") or second_model
                              or str((second or {}).get("model") or "")),
            second_reads_as=str(getattr(answer, "reads_as", "") or ""),
            second_asked=answer is not None,
            second_quote_missing=bool(answer is not None and answer.readable
                                      and not answer.quote_found),
            instrument=instrument,
        ))
    return out


def load(path: Path) -> tuple[dict[tuple[str, str], Decision], list[Decision]]:
    """Decisions on disk, and the malformed ones, separated.

    Staleness is *not* decided here — it needs the instrument of the page as it stands now, and
    that belongs to whoever is holding the records. `pending` and `fresh` do it. What this does
    reject outright is a decision with no verdict this module recognises: an unknown verdict
    string is a typo or a UI version drift, and either way silently dropping it into "not
    approved" would turn a typo into a rejection.
    """
    payload = json.loads(Path(path).read_text()) if Path(path).exists() else {}
    good: dict[tuple[str, str], Decision] = {}
    bad: list[Decision] = []
    for raw in payload.get("decisions") or ():
        try:
            decision = Decision(**{
                k: v for k, v in raw.items() if k in Decision.__dataclass_fields__
            })
        except TypeError:                                 # pragma: no cover - defensive
            continue
        if decision.verdict not in (APPROVE, REJECT) or not decision.task_id:
            bad.append(decision)
            continue
        good[decision.key] = decision
    return good, bad


def fresh(
    decisions: Mapping[tuple[str, str], Decision], *,
    records: Mapping[str, Mapping[str, Any]],
    verdicts: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[Decision]]:
    """The decisions still attached to the page they were made on, and the stale ones.

    The fresh half is returned in the plain-mapping shape `credit.eligible(adjudications=...)`
    consumes; the stale half is returned so it can be *reported*. A stale decision is not an
    error — it is the expected result of editing a transcript — but it must be visible, because
    the cell it used to settle has silently gone back to blocking.
    """
    ok: dict[tuple[str, str], dict[str, Any]] = {}
    stale: list[Decision] = []
    for key, decision in decisions.items():
        record = records.get(decision.task_id) or {}
        verdict = (verdicts or {}).get(decision.task_id) or {}
        instrument = str(verdict.get("instrument") or record.get("instrument") or "")
        if not decision.fresh_for(instrument):
            stale.append(decision)
            continue
        ok[key] = decision.to_json()
    return ok, stale


def save(path: Path, decisions: Iterable[Decision]) -> Path:
    """Write the decision file the UI's export produces and `load` reads."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"decisions": [d.to_json() for d in decisions]}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return path


def describe(question: Question) -> str:
    """One question as a person reads it, for a terminal that has no UI."""
    label = fields.LABELS.get(question.field_id, question.field_id) \
        if hasattr(fields, "LABELS") else question.field_id
    lines = [
        f"{question.task_id}  {question.field_id}  (checkpoint {question.checkpoint})",
        f"  card says            {question.card_value!r}   [{label}]",
        f"  {question.first_reader or 'reader 1'} reads   {question.first_quote!r}",
    ]
    if question.second_asked:
        lines.append(
            f"  {question.second_reader or 'reader 2'} says    not readable"
            + (f" — {question.second_reads_as!r}" if question.second_reads_as else "")
        )
    else:
        lines.append(f"  {question.second_reader or 'reader 2'} was never asked")
    if question.second_quote_missing:
        lines.append("  NOTE: reader 2 said yes but its quote is not on the page")
    lines.append("  --- messages " + ", ".join(str(m) for m in question.messages) + " ---")
    lines += ["  " + line for line in (question.excerpt or "").splitlines()]
    return "\n".join(lines)
