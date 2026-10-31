"""The optional verification pass: is the ground truth true of the page, conversation by
conversation?

ADR-0034 settled that the **schedule is the map** — the scorer reads beat placement, and no
model call stands between the corpus and a score. That decision holds, and this module is
what makes it safe to hold. If the map is derived rather than measured, then every way the
authored page can fail to match the schedule is an error the scorer cannot see, and the pilot
audit found four kinds of it in 67 sessions:

* **misattribution** — the beat was written, and it landed on somebody else. `gen-002`'s
  alcohol-free requirement became *"one of the four doesn't go anywhere there's a bar"*;
  every one of `gen-003`'s four `cuisines_avoided` beats was a companion's dislike. The map
  says STRONG about the subject; the page says something true about a person who is not in
  the dataset.
* **contradiction** — a session does the opposite of the card, usually in scenery the brief
  never mentioned. `gen-004` is vegan and eats pork in s2 and eggs in three later sessions;
  `gen-002` requires an alcohol-free room and picks a wine bar three sessions earlier.
* **under-delivery** — the beat is in the brief and not on the page.
* **leakage** — a field with no beat is nonetheless readable, which turns a twin's correct
  commitment into a fabrication under §8.1. This is the arm ADR-0034 deferred as pending 8.

Two arms, and the split is the same one `transcript.py` draws between `check_session` and
`probe_coverage`:

* `inspect()` is **deterministic and free**. Structure, drift against the corpus, replay of
  today's rules over stored text, format, the contradiction probe, and the countable
  distribution instances. It costs no model calls and should be run on every corpus.
* `audit()` is **one model call per session** and answers the questions a rule cannot:
  whose a statement is, whether a beat actually landed, whether a field the schedule did not
  schedule is readable anyway. Sampled by default, because a census of 612 sessions is a
  spend and ADR-0034 already decided this is an audit rather than a gate.

**The audit demotes; it does not promote.** v0's ADR-0009/0010 measured that where a judge
and the schedule disagreed about evidence, the schedule was the more accurate record — so a
reader saying "this cell is stronger than the schedule claims" is not taken as evidence and
is reported as a disagreement. What the reader *can* do is take a cell away, move it to a
different person, or add a leak. Those are the failures the schedule structurally cannot see,
which is exactly why a reader is being paid for.

Nothing here modifies a corpus or a store. It reports.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field as dc_field, replace as dc_replace
from datetime import date as date_type
from pathlib import Path
from time import sleep
from typing import Any, Iterable, Mapping, Sequence

from . import fields, schedule, transcript
from .judge import (
    DEFAULT_ATTEMPTS, RETRY_BACKOFF, ClaudeCli, Invoker, TransportError, fingerprint,
)

#: v0's operational ceiling, and it is not a style preference: 28 workers OOM-killed a 16 GB
#: box and logged 1,688 failures with empty stderr.
DEFAULT_WORKERS: int = 4

DEFAULT_MODEL: str = "claude-opus-5"

#: How many sessions the model arm reads when no explicit sample is given. A sample, not a
#: census: ADR-0034 moved judging downstream precisely so its cost became a choice.
DEFAULT_SAMPLE: int = 24


# --- findings ----------------------------------------------------------------


#: Severity is about what the finding does to the published ground truth, not about how
#: alarming it reads. `blocking` means a cell of the answer key is wrong as it stands.
BLOCKING: str = "blocking"
NOTE: str = "note"


@dataclass(frozen=True)
class Finding:
    kind: str
    severity: str
    task_id: str
    detail: str
    field_id: str = ""
    quote: str = ""
    #: Which turn `quote` came from, and its speaker — set by `_located` below, once, for
    #: every finding either arm raises, rather than at each of their construction sites. `None`
    #: means either there is no quote or `transcript.locate_quote` could not find it; either
    #: way there is no turn an editor could patch.
    turn_index: int | None = None
    speaker: str = ""
    #: Every message the defect spans, by v0.4 message number — the `[n]` the author wrote
    #: and the reader was shown. This is the field a repair should act on, and it exists
    #: because `turn_index` could not express the two defects the pilot's editor failed on:
    #: `gen-001-s1`'s denial is caused by the bot's question in the message *before* it, and
    #: `gen-003-s8`'s contradiction is a rejection in one message answered by an acceptance
    #: three later. A single quote resolving to a single turn names the symptom, not the
    #: defect. Empty means nobody said which messages, and `turn_index` is the fallback.
    messages: tuple[int, ...] = ()
    #: The reader's PROPOSAL that deleting exactly these messages resolves the defect, rather
    #: than rewriting anything. Never trusted: it is a hypothesis the editor tests against the
    #: same gates a rewrite faces, plus a beat-coverage check deletion needs and a rewrite does
    #: not. Empty means the reader did not think removal would fix it, which is the common case.
    #:
    #: Some defects are not a badly written message but a message that should not be there.
    #: `gen-001-s1` is the canonical one: the bot asks an intake question the session never
    #: briefed and the user denies having an allergy the card carries, so the page states the
    #: negation of ground truth. The 2026-08-21 editor spent all 3 attempts on it and failed,
    #: every rejection at `regression`, because there is no answer to that question that is
    #: both true to the card and permitted by the map. Removing the exchange resolves it, and
    #: costs no model call to generate — only to verify.
    removable_messages: tuple[int, ...] = ()

    @property
    def locatable(self) -> bool:
        return bool(self.messages) or self.turn_index is not None

    @property
    def deletable(self) -> bool:
        """The reader proposed a removal, and it is a subset of what the defect spans.

        The subset rule is what stops a reader proposing to delete a page. A removal that
        reaches beyond the defect's own span is not a repair of this finding.
        """
        return bool(self.removable_messages) and (
            not self.messages
            or set(self.removable_messages) <= set(self.messages)
        )

    @property
    def targets(self) -> tuple[int, ...]:
        """The messages a repair should rewrite, preferring what was cited over what was
        guessed. `messages` came from whoever raised the finding; `turn_index` came from
        `transcript.locate_quote` matching a quote back to a turn, which is a best effort."""
        if self.messages:
            return self.messages
        return (self.turn_index,) if self.turn_index is not None else ()

    def __str__(self) -> str:
        where = f"{self.task_id}" + (f" {self.field_id}" if self.field_id else "")
        at = f" @{','.join(str(m) for m in self.messages)}" if self.messages else ""
        tail = f" — {self.quote!r}" if self.quote else ""
        return f"[{self.severity}] {self.kind} {where}{at}: {self.detail}{tail}"

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "severity": self.severity, "task_id": self.task_id,
            "field_id": self.field_id, "detail": self.detail, "quote": self.quote,
            "turn_index": self.turn_index, "speaker": self.speaker,
            "messages": list(self.messages),
            # Serialised even when empty. The reader and the editor meet at a JSON file, so a
            # field that survives in memory and not on disk is a field the editor never sees —
            # the same shape as the `repeat_not_delivered` check that read a dict which had
            # dropped half the reader's answer.
            "removable": list(self.removable_messages),
        }


@dataclass
class Report:
    findings: list[Finding] = dc_field(default_factory=list)
    counted: Counter = dc_field(default_factory=Counter)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)
        self.counted[finding.kind] += 1

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == BLOCKING]

    def to_json(self) -> dict[str, Any]:
        return {
            "findings": [f.to_json() for f in self.findings],
            "counts": dict(self.counted),
            "blocking": len(self.blocking),
        }


# --- loading -----------------------------------------------------------------


def load_store(store: Path) -> dict[str, dict]:
    """Every authored record, keyed by task id.

    The `._*` filter is not decoration. Some filesystems (e.g. exFAT) write a `._*` sidecar beside
    every real file; v0 read a 72-call run as 144 across five documents before anyone noticed.
    """
    out: dict[str, dict] = {}
    for path in sorted(store.glob("gen-*.json")):
        if path.name.startswith("._"):
            continue
        record = json.loads(path.read_text())
        out[str(record["task_id"])] = record
    return out


def _items(corpus: Path) -> dict[str, dict]:
    payload = json.loads(corpus.read_text())
    items = payload["items"] if isinstance(payload, dict) else payload
    return {str(item["subject_id"]): item for item in items}


def _located(finding: Finding, records: Mapping[str, Mapping[str, Any]]) -> Finding:
    """`finding`, with `turn_index`/`speaker` resolved from its own `quote` if that quote can
    be found in the transcript it was raised against.

    One place rather than every `Finding(...)` call site in this file: both arms already put
    their best-effort excerpt in `quote`, and turning "some text" into "turn 4, USER" is the
    same lookup regardless of which rule or which reader produced it. A finding with no quote,
    or a quote `transcript.locate_quote` cannot find, is returned unchanged — `locatable`
    stays `False`, and it is still reported, just not patchable.
    """
    record = records.get(finding.task_id)
    if record is None:
        return finding
    text = str(record.get("transcript") or "")
    # A cited message number beats a matched quote: the reader was looking at `[6]` when it
    # wrote the finding, whereas `locate_quote` is inferring backwards from an excerpt that
    # may be paraphrased, may span two speakers, or may occur twice.
    if finding.messages:
        locator = transcript.locate_message(text, finding.messages[0])
        if locator is not None:
            return dc_replace(
                finding, turn_index=locator.turn_index, speaker=locator.speaker
            )
    if not finding.quote:
        return finding
    locator = transcript.locate_quote(text, finding.quote)
    if locator is None:
        return finding
    return dc_replace(
        finding, turn_index=locator.turn_index, speaker=locator.speaker,
        messages=finding.messages or (locator.turn_index,),
    )


# --- the deterministic arm ---------------------------------------------------


def inspect(corpus: Path, store: Path) -> Report:
    """Every finding available without a model call."""
    report = Report()
    items = _items(corpus)
    records = load_store(store)
    expected = {
        str(session["task_id"]): (sid, session)
        for sid, item in items.items() for session in item["sessions"]
    }

    found: list[Finding] = [
        Finding("missing", BLOCKING, task_id,
                "the corpus schedules it and the store has no record")
        for task_id in sorted(set(expected) - set(records))
    ] + [
        Finding("orphan", NOTE, task_id, "the store has it and the corpus does not")
        for task_id in sorted(set(records) - set(expected))
    ]
    for task_id in sorted(set(expected) & set(records)):
        subject_id, session = expected[task_id]
        record = records[task_id]
        found += _drift(task_id, record, session)
        found += _run_state(task_id, record)
        found += _page(task_id, record, session, items[subject_id])
    for subject_id, item in sorted(items.items()):
        found += _timeline(subject_id, item, records)
    for finding in found:
        report.add(_located(finding, records))
    return report


#: Session fields the store copies from the corpus. A mismatch means the transcript was
#: authored against a corpus that no longer exists — the silent no-op failure where every session cache-hits and it looks like the fix shipped.
_MIRRORED: tuple[str, ...] = (
    "subject_id", "session", "checkpoint", "turns", "date", "removable", "is_distractor",
)


def _drift(task_id: str, record: Mapping[str, Any], session: Mapping[str, Any]) -> list[Finding]:
    found = []
    for key in _MIRRORED:
        if record.get(key) != session.get(key):
            found.append(Finding(
                "drift", BLOCKING, task_id,
                f"{key}: the store says {record.get(key)!r}, the corpus says "
                f"{session.get(key)!r}",
            ))
    if record.get("beats") != session.get("beats"):
        found.append(Finding(
            "drift", BLOCKING, task_id,
            "the stored beats are not the corpus beats; this transcript was authored "
            "against a different schedule",
        ))
    return found


def _run_state(task_id: str, record: Mapping[str, Any]) -> list[Finding]:
    found = []
    if not (record.get("transcript") or "").strip():
        found.append(Finding("empty", BLOCKING, task_id, "no transcript was written"))
    if record.get("error"):
        found.append(Finding("error", BLOCKING, task_id, str(record["error"])))
    if record.get("exhausted"):
        found.append(Finding(
            "exhausted", BLOCKING, task_id,
            "the retry budget ran out and the session shipped with its problems",
        ))
    return found


def _page(
    task_id: str, record: Mapping[str, Any], session: Mapping[str, Any],
    item: Mapping[str, Any],
) -> list[Finding]:
    """What today's rules make of stored text, plus the probes that only report.

    Replaying `check_session` over an old store is the cheapest audit in the project and it
    is not redundant with authoring: the rules move, the text does not, and a rule added after
    a corpus was written has never been run against it.
    """
    text = record.get("transcript") or ""
    beats = record.get("beats") or []
    found = []
    for problem in transcript.check_session(
        text, self_report=record.get("self_report"), beats=beats,
        free_offers=record.get("free_offers") or (),
    ):
        found.append(Finding(
            f"rule:{problem.kind}",
            NOTE if problem.kind in ADVISORY_RULES else BLOCKING,
            task_id, problem.detail, quote=problem.quote,
        ))
    for number in record.get("free_offers") or ():
        # Report-only, and the point of the mechanism: a declared escape is allowed, and the
        # rate is read off the free arm rather than inferred from what the reader happened to
        # notice. `_offer_problems` has already checked the number points at a bot turn.
        found.append(Finding(
            "offer:escape", NOTE, task_id,
            f"message [{number}] is a bot turn the author declared as written outside the "
            "offer skeleton",
        ))
    for field_id, covered in transcript.probe_coverage(text, beats):
        if not covered:
            found.append(Finding(
                "probe:uncovered", NOTE, task_id,
                "the disclosure never names its field, so the map asserts a cell the page "
                "does not carry",
                field_id=field_id,
            ))
    for problem in transcript.contradiction_probes(text, item["card"], beats=beats):
        found.append(Finding(
            "probe:contradiction", NOTE, task_id, problem.detail,
            field_id=problem.detail.split(":")[0], quote=problem.quote,
        ))
    found += _incidental(task_id, text, session, item)
    found += _repeat_delivery(task_id, text, beats)
    found += _unbriefed_intake(task_id, text, beats, item)
    found += _question_budget(task_id, text, session)
    return found


#: The intake vocabulary lives in `transcript` because the **gate** uses it too: a bot turn
#: that screens the user is rejected at authoring time and retried, and a gate that reads a
#: different table from the audit would either spend retries on sessions this file calls clean
#: or pass sessions it calls broken. One table, two readers.
_INTAKE_FIELDS = transcript.INTAKE_FIELD_PATTERNS
_INTAKE_GENERIC = transcript.INTAKE_GENERIC
_INTAKE_ASK = transcript.INTAKE_ASK
_unbriefed_fields = transcript.unbriefed_intake_fields

#: How a turn opens when it denies. Repeatable, because *"no, nothing"* is one denial.
_DENY_HEAD = r"(?:no|none|nope|nothing|nah|not really)"

#: What may trail the denial before it closes: the thing being denied, named or gestured at.
_DENY_TAIL = (
    r"(?:[^.?!;]{0,40}?(?:to (?:flag|note|mention)|like that|at all|of note"
    r"|i can think of|we can think of|allergies|dietary|dietaries|restrictions"
    r"|requirements|preferences))?"
)

#: The user turn that answers one. Only ever matched against the turn **immediately after** an
#: unbriefed intake ask, which is what lets it stay this permissive about what follows.
#:
#: The first version required the denial to be the whole turn, and the re-authored pilot showed
#: what that costs: `gen-004-s8` answered *"no, nothing. just the two seats"* on a card carrying
#: vegan, and the check reported the softer `unbriefed_intake` instead of the blocking
#: `denied_constraint`. One clause after the full stop was the whole difference. That is the
#: second demonstration of the same recall gap — the reader arm found 9 denials to the lexical
#: arm's 8 before the fix, and 2 to its 1 after — so a check that exists to catch the page
#: stating the negation of ground truth was undercounting exactly that.
#:
#: What it must **not** do is read a disclosure as a denial. *"no, actually there is — I'm
#: vegan"* opens with the same word and means the opposite, and flagging it would report a
#: correct page as broken. So the denial has to *close*: end of turn, a sentence boundary, or a
#: comma that leads only into thanks. A trailing comma into anything else is a continuation and
#: is not matched — which is also why `_DENY_TAIL` naming a field is safe, since *"no allergies,
#: but I'm vegan"* fails on the same rule that *"no allergies."* passes.
_INTAKE_DENY = re.compile(
    rf"^\s*{_DENY_HEAD}(?:\s*,\s*{_DENY_HEAD})*{_DENY_TAIL}"
    rf"\s*(?:[.!;]|,\s*(?:thanks|thank you|cheers)|$)",
    re.I,
)


def _unbriefed_intake(
    task_id: str, text: str, beats: Sequence[Mapping[str, Any]], item: Mapping[str, Any]
) -> list[Finding]:
    """The bot screening the user for something this session did not brief.

    This is the check for the worst defect the pilot has produced, and it is deterministic
    because the reader missed most of it: the model arm surfaced 2 of the 9 sessions as
    `contradiction`, and a lexical sweep found all 9. A class that a sampled reader catches
    at 22% cannot be left to sampling.

    Nine of 67 sessions had the bot ask an intake question the schedule never scheduled —
    *"anything I should note, allergies or anything you avoid?"* — and the author, with no
    positive fact to answer from, wrote *"no, nothing to flag"* for people carrying
    `dairy_free`, `vegan` and a shellfish allergy. **The page then states the negation of
    ground truth**, which is strictly worse than a leak: a twin that reads it correctly
    concludes the opposite of the card, and the map cannot see the problem because the cell it
    concerns is UNEVIDENCED.

    The check fires on the **question**, matching the brief's `_INTAKE_BAR`, because the
    question is what forces the invention — and because the answer is the easier half to write
    around. A denial that follows one is reported as the severe case, since that is the
    variant that actually falsifies the page, but an unbriefed ask is a finding either way.

    A `disclosure` beat is the carve-out and it is read off the beats rather than assumed: the
    schedule scheduled that ask, `DISCLOSURE_PROBES` requires it to name its field, and the map
    counts the answer.

    The carve-out is per **field**, not per session, and both readers now get it from one
    place: `transcript.unbriefed_intake_fields`, which also holds the rule that a generic or
    umbrella ask is never exempted by a beat at all. This function did not.
    `briefed` used to gate the whole function as a bool, so one disclosure beat on `dietary`
    exempted every other constraint field too. `gen-001-s1` carries a `dietary` disclosure beat
    and a separate, unbriefed denial of `allergies` — the old code never looked at the second
    because the first made `briefed` truthy.
    """
    briefed = {
        str(b["field_id"]) for b in beats
        if "disclos" in str(b.get("move", "")) or str(b["field_id"]) in fields.CONSTRAINT_IDS
    }
    card = item.get("card") or {}
    carried = {
        fid for fid in fields.CONSTRAINT_IDS
        if card.get(fid) not in (None, [], (), False, "")
    }
    found: list[Finding] = []
    turns = transcript.speaker_turns(text)
    for index, (speaker, line) in enumerate(turns):
        if speaker != "BOT" or not _INTAKE_ASK.search(line):
            continue
        reached = _unbriefed_fields(line, briefed)
        if not reached:
            continue
        reply = ""
        if index + 1 < len(turns) and turns[index + 1][0] == "USER":
            reply = turns[index + 1][1].strip()
        contradicted = sorted(reached & carried)
        if _INTAKE_DENY.match(reply) and contradicted:
            found.append(Finding(
                "denied_constraint", BLOCKING, task_id,
                f"the bot asked an intake question this session did not brief and the user "
                f"denied having anything, while the card carries "
                f"{', '.join(contradicted)}; the page now states the negation of ground "
                "truth",
                field_id=contradicted[0],
                quote=f"{line[:70]} / {reply[:50]}",
                # Both messages, ask first. The ask is the causal half: the session never
                # briefed this field, so there is no answer to the question that is both
                # true to the card and permitted by the map — say the constraint and it is a
                # `leak` on an UNEVIDENCED cell, deny it and it is this finding. Only
                # removing the question resolves it. The pilot's editor was handed the reply
                # alone (`locate_quote` deliberately takes the second half) and burned three
                # attempts on `gen-001-s1` rewriting the one message that could not be fixed.
                messages=(index, index + 1),
            ))
        else:
            found.append(Finding(
                "unbriefed_intake", NOTE, task_id,
                "the bot screens the user for a hard constraint this session did not brief; "
                "no denial followed, but the ask is what forces the answer to be invented",
                quote=line[:90],
                messages=(index,),
            ))
    return found


#: §6.1 rules that bind the **author** but do not block a corpus already written. An unbriefed
#: preference ask makes the author invent an answer, which lands against the card sometimes and
#: reads as a leak the rest of the time — worth a retry while authoring, not worth blocking a
#: store over. The intake rules stay blocking: a denied hard constraint puts the negation of
#: ground truth on the page.
#: `offer_attribute` joins `unbriefed_screening` here for the same reason and it is the same
#: shape of rule: both gate at authoring, where a retry can fix the turn, and both would
#: otherwise light up every session in a store written before the rule existed. A replay is
#: "today's rules over old text" by design, but a rule that fires on the absence of a brief
#: block is reporting the store's date, not a defect in the page.
ADVISORY_RULES: frozenset[str] = frozenset({"unbriefed_screening", "offer_attribute"})


#: What the brief asks the bot for: enough to suggest on, not an interview. Report-only, and
#: deliberately not a gate.
#:
#: **The floor was dropped and the ceiling tightened, and the measurement is why.** The band
#: was (2, 4) on the reading that the rule would mostly have to *backfill* the questions the
#: screening bar takes away — 18 of the pre-re-author store's 67 sessions ask a single
#: question. Re-measured across three stores, the over-asking the floor was hedging against
#: is already gone: 8 of 67 sessions exceed three questions here against 29 of 67 in the
#: pre-ADR-0038 store, so the interview-shaped bot was ADR-0041's to fix and it fixed it.
#: What is left is a ceiling question, and a one-question session is not a defect — it is the
#: short, ordinary conversation §6.1 asks for. A floor of two would have pushed 18 sessions
#: *up*, which is the opposite of what the brief now wants.
#:
#: Still report-only. A gate that forced questions could not be satisfied by retrying and
#: would burn the attempt budget; this is measured across the re-author and granted authority
#: only if it needs it.
#:
#: **v0.5 drops the floor to zero, and it is this tree's change to `_INTAKE_BAR` that forces
#: it rather than a fresh reading of the old data.** The previous tree carried the floor of 1
#: as an open owner's call — it never agreed with the brief's own "none at all is fine" — and
#: the question rule here settles it in one direction. The permitted set is now the beats and
#: nothing else: the outing carve-out that used to license asking the area, the budget, the
#: party size and the hour is gone. A session whose beats are all reactions and anecdotes
#: therefore has no field the bot may ask about, and a silent bot is the page the brief asks
#: for — not a defect. Held at 1, this check would report the intended construction as a
#: finding across a large share of the corpus, which is the mistake its own distractor
#: exemption is documented avoiding.
#:
#: The ceiling is untouched and is now the whole of the check. It is also the number to read
#: first on the run that measures this tree: the `venue-pool-general` failure was a closed
#: ground discharging into `question_budget` (4 -> 11), and if closing the bot's questions
#: makes it ask *more* of what is left, this is where that shows up.
QUESTION_BUDGET: tuple[int, int] = (0, 3)


def _question_budget(
    task_id: str, text: str, session: Mapping[str, Any] | None = None
) -> list[Finding]:
    """How many questions the bot puts to the user before it has said enough to suggest on.

    **Under v0.5 this is a ceiling and nothing else** — see `QUESTION_BUDGET`. The floor the
    distractor used to be exempt from is gone for every session, because the brief's permitted
    set is now the beats alone and a session with no askable field is correctly silent. The
    exemption's own argument generalized: in a distractor the user is not choosing where or
    when to go, so there is no recommendation to clarify toward and a silent bot is the right
    page (`gen-004-s5` and `gen-004-s19` were both reported as under-asking on text that is
    exactly right). That is now true of an anecdote-only session too.

    The exemption was one-sided because its first draft was not, and its own data said so:
    exempting distractors outright also silenced `gen-003-s11`, a distractor whose bot asks
    **four** questions — the interview shape this check exists to report, and more suspect in
    a distractor rather than less, since a session that briefs nothing has nothing for four
    questions to be drawn from. Keeping the ceiling universal is what preserves that catch.
    """
    asked = sum(
        1
        for speaker, line in transcript.speaker_turns(text) if speaker == "BOT"
        for sentence in re.split(r"(?<=[.?!])\s+", line) if "?" in sentence
    )
    low, high = QUESTION_BUDGET
    if low <= asked <= high:
        return []
    return [Finding(
        "question_budget", NOTE, task_id,
        f"the bot asks {asked} questions; the brief asks for {low} to {high}, every one of "
        "them about a field a beat names rather than about anything the session did not brief",
    )]


def _incidental(
    task_id: str, text: str, session: Mapping[str, Any], item: Mapping[str, Any]
) -> list[Finding]:
    """Distribution occasions: does the page carry the bin the schedule drew for it?

    ADR-0037 made these part of the answer key, which changes what this check is for. It used
    to ask whether the transcript leaked a cell the map called UNEVIDENCED — a question that
    no longer has a stable meaning, because a conversation about going out instantiates a
    party size and a time whether anyone schedules them or not, and now the schedule does.

    Three outcomes, and only one of them is ever more than a pointer.

    An **unscheduled occasion** is scenery the author added where the schedule drew nothing:
    harmless when it agrees with the card, and a genuine contradiction when the card gives
    that bin no weight, which is the one case here that blocks.

    A **missing** or **mismatched** occasion is usually this probe being wrong, and both were
    measured being wrong on their first run. `BIN_SURFACE` matches a short phrase list, so
    *"just me, and I don't want to spend much"* delivers `party=1` and `price=$` while the
    probe scores the price as missing; and *"saturday around lunchtime"* is `weekend-day`,
    which the probe calls `weekday-lunch` because "lunchtime" is in that bin's phrase list
    with nothing to check the day against. Both are notes that defer to the reader. A gate
    here would send someone to re-author two correct sessions.
    """
    # A corpus built before ADR-0037 schedules no occasions, so every instance on the page
    # would read as unscheduled and the report would be mostly that. Say it once instead:
    # `_timeline` raises the corpus-level note, and the per-session checks stand down.
    if "incidentals" not in item:
        return []
    scheduled = {str(b["field_id"]) for b in session.get("beats") or ()}
    drawn = {
        str(i["field_id"]): str(i["bin"])
        for i in item.get("incidentals") or ()
        if int(i["session"]) == int(session["session"])
    }
    when = session.get("date")
    on_date = date_type.fromisoformat(str(when)) if when else None
    found = []
    for field_id in transcript.OCCASION_FIELDS:
        if field_id in scheduled:
            continue
        gold = item["card"].get(field_id) or {}
        want = drawn.get(field_id)
        # Set-valued on purpose (ADR-0043): the page can be consistent with two bins at
        # once — "at the upper end" is £30-50 and £50+ — and a probe that picks one invents a
        # resolution the text does not carry. A drawn bin inside the set is delivered.
        names, quote = transcript.read_occasion(field_id, text, on_date=on_date)

        if not names:
            if want is not None:
                found.append(Finding(
                    "occasion_missing", NOTE, task_id,
                    f"the schedule draws bin {want!r} for this session and the map counts "
                    "it, but neither the phrase list nor the structured reader finds it; "
                    "confirm with the reader before treating it as undelivered",
                    field_id=field_id,
                ))
            continue

        if want is None:
            # Read again from the USER's turns alone. Delivery is a property of the page —
            # the bot proposes a price band and the user says "yeah", and both halves are
            # the occasion — but a *contradiction of the card* is a claim about the person,
            # and the bot listing a venue whose "food reviews are middling" is not the user
            # instantiating a mid-range outing. That exact line was a blocking finding on
            # `gen-004-s2` before this split.
            names, quote = transcript.read_occasion(
                field_id, text, on_date=on_date, speakers="user"
            )
            if not names:
                continue
            # Blocking only when the card gives *every* bin the page could be carrying no
            # weight. One plausible reading that the card allows is not a contradiction.
            share = max(float(gold.get(name, 0.0)) for name in names)
            reading = ", ".join(sorted(names))
            found.append(Finding(
                "unscheduled_occasion", NOTE if share >= 0.02 else BLOCKING, task_id,
                f"bin {reading!r} is instantiated and the schedule drew none here; the card "
                f"gives it {share:.2f}"
                + ("" if share >= 0.02 else " — the page contradicts the card"),
                field_id=field_id, quote=quote,
            ))
        elif want not in names:
            reading = ", ".join(sorted(names))
            found.append(Finding(
                "occasion_mismatch", NOTE, task_id,
                f"the schedule drew {want!r} and this probe reads the page as {reading!r}. "
                "Confirm with the reader before acting: the probe reads a daypart, a day "
                "class and a head count, and cannot arbitrate a bin the page leaves open",
                field_id=field_id, quote=quote,
            ))
    return found


def _repeat_delivery(
    task_id: str, text: str, beats: Sequence[Mapping[str, Any]]
) -> list[Finding]:
    """A field scheduled N times in one session, reported so the model arm can count moments.

    The map charges the subject N pinning weights for it. Whether the page carries N separate
    moments or one restated is not a question a rule can answer, so this only says where to
    look — at n=50 eleven field-subject cells reach their pin threshold on a within-session
    repeat, and every one of them rests on this being true.
    """
    counts = Counter(str(b["field_id"]) for b in beats if b.get("attributed") != "third_party")
    return [
        Finding(
            "repeat", NOTE, task_id,
            f"scheduled {n} times in one session; the map counts {n}, so the page must "
            f"carry {n} separate moments",
            field_id=field_id,
        )
        for field_id, n in sorted(counts.items()) if n > 1
    ]


def _timeline(
    subject_id: str, item: Mapping[str, Any], records: Mapping[str, dict]
) -> list[Finding]:
    """Per-subject checks that no single session can see."""
    found = []
    if "incidentals" not in item:
        found.append(Finding(
            "stale_corpus", NOTE, subject_id,
            "this corpus predates ADR-0037 and schedules no distribution occasions, so the "
            "occasion checks are skipped; its answer key understates the three distribution "
            "fields and it must be rebuilt before it is scored",
        ))
    # A same-day subject's dates are aligned to the visit-time bin drawn for each session, so
    # an aligned gap is the drawn one plus up to six days (`schedule._alignment_days`). The
    # widened set is a real loss of resolution for these subjects — almost any gap passes — and
    # it is accepted because the check is a NOTE about the calendar scaffold, while the drift
    # check above compares the stored dates against the corpus itself and is what would catch
    # a date that had actually been tampered with.
    allowed_gaps = set(schedule.SESSION_GAP_DAYS)
    if str((item.get("card") or {}).get("planning_style") or "") in schedule.SAME_DAY_STYLES:
        allowed_gaps |= {
            g + a for g in schedule.SESSION_GAP_DAYS for a in range(7)
        }

    dates = []
    for session in item["sessions"]:
        record = records.get(str(session["task_id"]))
        if record and record.get("date"):
            dates.append((int(session["session"]), date_type.fromisoformat(record["date"])))
    for (before, then), (after, now) in zip(dates, dates[1:]):
        gap = (now - then).days
        if gap <= 0:
            found.append(Finding(
                "dates", BLOCKING, f"{subject_id}-s{after}",
                f"session {after} is {gap} days after session {before}",
            ))
        elif gap not in allowed_gaps:
            found.append(Finding(
                "dates", NOTE, f"{subject_id}-s{after}",
                f"gap of {gap} days is not drawn from SESSION_GAP_DAYS",
            ))

    # The `single_mention` hard case, checked on the page rather than on the schedule. The
    # scheduler already guarantees one beat; what voided the construction in the pilot was the
    # author being *consistent* and raising the constraint again three sessions later.
    for directive in item.get("directives") or ():
        kind, _, argument = str(directive).partition(":")
        if kind != "single_mention" or argument not in fields.BY_ID:
            continue
        beat_sessions = {
            int(s["session"]) for s in item["sessions"]
            for b in s.get("beats") or () if str(b["field_id"]) == argument
        }
        surfaces = _mention_surfaces(argument, item["card"].get(argument))
        for session in item["sessions"]:
            index = int(session["session"])
            if index in beat_sessions:
                continue
            record = records.get(str(session["task_id"]))
            if not record:
                continue
            for turn_index, turn in enumerate(
                transcript.user_turns(record.get("transcript") or "")
            ):
                hit = next((s for s in surfaces if s in turn.lower()), None)
                if hit:
                    found.append(Finding(
                        "single_mention", BLOCKING, str(session["task_id"]),
                        f"raised again in a session with no beat for it, so the hard case "
                        f"— is a once-mentioned value retained? — is not in the data",
                        field_id=argument, quote=turn[:90],
                    ))
                    break
    return found


def _mention_surfaces(field_id: str, value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(v).replace("_", " ").lower() for v in value)
    if field_id == "alcohol_free_required" and value:
        return ("alcohol", "licence", "licensed", "unlicensed", "no booze")
    return ()


# --- the model arm -----------------------------------------------------------


AUDIT_SYSTEM = """\
You are auditing one conversation from a research dataset. The dataset pairs conversations \
with a "persona card" — the ground truth about the person in them — and a schedule saying \
which facts each conversation was written to convey. Your job is to check the page against \
those claims. You are not grading anyone and you are not being asked to be generous.

Read only the conversation you are given. Do not reason from what the card says to what the \
conversation probably says; the entire point is to find the places where they differ.

Four questions, and the third and fourth are the ones this audit exists for:

1. DELIVERED. For each scheduled beat: does the conversation actually convey that fact about \
the person? A fact that is merely consistent with the conversation is not delivered — it has \
to be readable from it.
2. STRENGTH. If delivered: does the conversation PIN the value (a reader could name it) or \
only NARROW it (a reader could give a range)?
3. WHOSE IT IS. For each delivered beat: is this the USER's own, or does it belong to \
somebody else they are going with — a friend with an allergy, a partner who does not drink? \
Say THIRD_PARTY whenever the requirement is stated as another person's, even if the user is \
the one relaying it and the user accommodates it. Say AMBIGUOUS if the page genuinely does \
not settle whose it is. This is not a technicality: a constraint belonging to a companion is \
not a fact about the person the card describes.
4. UNSCHEDULED and CONTRADICTED. Which fields NOT in the beat list does the conversation \
nonetheless make readable? And does anything in it contradict the card — the user ordering \
something their diet excludes, choosing a cuisine they avoid, drinking when they do not \
drink? Quote it.

Every message is numbered — `[0]`, `[1]`, `[2]` — and you must cite those numbers. \
"messages" is the list of message numbers the thing you are reporting actually lives in. \
Give EVERY message it depends on, not just the one you quoted. A contradiction that is a \
refusal in one message and an acceptance four messages later is `"messages": [6, 10]`. A \
question in one message that forces a wrong answer in the next is `"messages": [11, 12]` — \
the question is part of the defect, not context for it. Getting this list right matters \
more than the quote: it is what a repair is applied to.

"removable" is OPTIONAL and usually absent. Give it only when the defect would be resolved by \
DELETING messages outright rather than rewriting them — because the messages should not be on \
the page at all, not because they are badly written. The clearest case is a question that \
should never have been asked together with the answer it forced. It must be a subset of \
"messages", and it must leave a page that still reads: USER and BOT alternate, so a removal \
is almost always an adjacent PAIR, never one half of an exchange. Do not propose removing a \
message that carries something from the beat list — that content has to survive. If you are \
unsure, leave it out; a rewrite will be attempted instead.

Output JSON only, no prose around it:

{"beats": [{"field_id": "...", "delivered": true, "strength": "pinned"|"narrowed", \
"whose": "user"|"third_party"|"ambiguous", "moments": 1, "messages": [4], "quote": "..."}],
 "unscheduled": [{"field_id": "...", "strength": "pinned"|"narrowed", "messages": [7], \
"quote": "..."}],
 "contradictions": [{"field_id": "...", "messages": [6, 10], "removable": [], "quote": "...", \
"why": "..."}]}

"moments" is how many separate places in the conversation carry that beat's fact. Quote \
verbatim and briefly. If a list is empty, give it as []."""


def audit_prompt(
    record: Mapping[str, Any], item: Mapping[str, Any]
) -> str:
    """Everything the reader is shown: the page, the claims about it, and the card."""
    beats = record.get("beats") or []
    lines = [
        f"=== THE CONVERSATION (session {int(record['session']) + 1} of this person's "
        f"history) ===",
        record.get("transcript") or "",
        "",
        "=== WHAT THIS CONVERSATION WAS WRITTEN TO CONVEY ===",
    ]
    if not beats:
        lines.append("  nothing. This conversation is scheduled to carry no facts about the "
                     "person at all. Report anything it conveys under 'unscheduled'.")
    for beat in beats:
        whose = (
            "SOMEBODY ELSE'S — a companion's requirement the user is relaying"
            if beat.get("attributed") == schedule.THIRD_PARTY else "the user's own"
        )
        lines.append(
            f"  {beat['field_id']} = {beat.get('gold_value')!r}, via {beat['move']}, "
            f"{whose}"
        )
    lines += [
        "",
        "=== THE PERSON'S CARD (ground truth; the conversation must not contradict it) ===",
    ]
    lines += fields.card_block(item["card"])
    lines += [
        "",
        "=== READING A DISTRIBUTION ===",
        "  " + fields.DISTRIBUTION_READING_RULE,
        "",
        "=== THE FIELD VOCABULARY ===",
        "  " + ", ".join(fields.ALL_FIELDS_IDS),
        "",
        "Answer for this conversation only.",
    ]
    return "\n".join(lines)


def parse_verdict(raw: str) -> dict[str, Any]:
    """The reader's reply as an object.

    Raises rather than defaulting. An unparsed reply is a wrong answer, not an abstention —
    this project has already published a table in which 19 of 23 non-answers from one model
    were format variance being read as a considered "no evidence".

    **A reply that stopped mid-structure is the exception, and it is a transport failure.**
    §7.3's rule is retry transport, never content, and the reason content is never re-rolled is
    that a wrong answer is a disposition worth recording — re-rolling it biases the pool toward
    whichever model complies on the second attempt. A truncated reply expresses no disposition.
    It is the same category as the empty stdout `ClaudeCli` already retries, arriving one
    thousand characters later.

    Measured, not assumed. The 67-session reader pass of 2026-08-18 lost `gen-002-s8` and
    `gen-004-s20` this way, cut at 1000 and 1099 characters. Both parsed on a plain retry, and
    the retries were **longer** than the cut — 1339 and 1540 characters — so the cut is not a
    length ceiling the model is hitting deterministically. A ceiling would truncate the retry in
    the same place. Delivery is what failed.

    The test is deliberately narrow: an incomplete reply is one that **opened** the object, does
    not parse, and does not close it. The opening brace is load-bearing and the existing suite is
    what found that out — a first draft keyed only on the missing close called *"I could not find
    anything wrong with this conversation."* a truncation. Prose is not a cut-off reply; it is
    the model answering in the wrong format, which is a disposition and is precisely the
    19-of-23 case this rule exists to record rather than re-roll. A reply that closes and still will not parse is malformed,
    stays a `ValueError`, and is never retried. A reply truncated immediately after a nested
    `}` is misclassified as malformed by this rule, which is the safe direction to be wrong in
    — it costs one lost call rather than a blind re-roll of a real answer.
    """
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        if start >= 0 and not text.endswith("}"):
            raise TransportError(
                f"reply stopped mid-structure after {len(raw)} chars: ...{raw[-80:]!r}"
            )
        raise ValueError(f"no JSON object in reply: {raw[:200]!r}")
    try:
        payload = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        if not text.endswith("}"):
            raise TransportError(
                f"reply stopped mid-structure after {len(raw)} chars: ...{raw[-80:]!r}"
            ) from exc
        repaired = _join_split_quote(text[start:end + 1])
        if repaired is None:
            raise
        try:
            payload = json.loads(repaired)
        except json.JSONDecodeError:
            # The repair is narrow but it is still a guess. A guess that does not parse must
            # surface the reply's own error, not the error the guess made — the second reads
            # as a defect in a place the reader never touched.
            raise exc from None
    for key in ("beats", "unscheduled", "contradictions"):
        payload.setdefault(key, [])
        if not isinstance(payload[key], list):
            raise ValueError(f"{key} is not a list")
    return payload


#: A closing quote, a bare ` / `, and an opening quote — two excerpts where one string belongs.
#:
#: The lookbehind is what keeps it off an escaped quote, which is how a `"` reaches the inside
#: of a string. The **required whitespace** is what keeps it off a one-character key: `"/"` is a
#: legal JSON key whose own two quotes sit either side of a slash, and a pattern with `\s*`
#: rewrites it into rubble. The suite caught that, not a reply — see the test of the same name.
_SPLIT_QUOTE = re.compile(r'(?<!\\)"\s+/\s+(?<!\\)"')


def _join_split_quote(text: str) -> str | None:
    """The one malformed reply worth repairing rather than losing, or `None`.

    The reader is told to cite every message a defect depends on and to quote verbatim, and is
    never told that `quote` is a single string. So a finding spanning two messages comes back
    as two excerpts joined the way the findings themselves read — ``"Just me." / "queue up for
    and take to a bench"`` — with the slash *outside* the strings. That is invalid JSON, and
    `parse_verdict` correctly refuses to guess at malformed replies.

    **This one is not a disposition, which is the whole reason it is repaired.** §7.3 declines
    to re-roll content because a reply in the wrong format is an answer worth recording — the
    19-of-23 case. Here nothing about the answer is in the wrong format: `field_id`, `strength`
    and `messages` parse, and `messages` is the part a repair is applied to. Only the separator
    between two excerpts of a free-text field is broken.

    It is repaired rather than retried because **the loss is not random with respect to what is
    being measured**. A reply can only break this way when the reader files a multi-message
    finding, and multi-message findings are what defect-heavy sessions produce — so losing them
    biases blocking counts down. Measured on the 2026-08-21 ADR-0052 reader pass: 6 of 57
    replies lost, 0 of 57 under the previous `AUDIT_SYSTEM`, and 4 of the 6 parsed on a plain
    re-call, at different columns each time. Non-deterministic, and concentrated where the
    findings are.

    Returns `None` when the pattern is absent, so a reply that is malformed some other way
    still raises and is still counted as unreadable.

    The cause is in the prompt and the fix there is one line — `quote` is one string; join
    excerpts inside it. It is deliberately NOT made here: changing `AUDIT_SYSTEM` invalidates
    every cached verdict, and this repair costs nothing and keeps them. Fold the prompt line
    into the next run that invalidates the cache for its own reasons.
    """
    repaired, count = _SPLIT_QUOTE.subn(" / ", text)
    return repaired if count else None


@dataclass
class VerdictStore:
    """Cached verdicts, keyed by task and fingerprinted on what the reader was shown.

    Same contract as `TranscriptStore`: change the transcript or the prompt and the cache
    misses, so a re-run after a re-authoring audits the new text rather than reporting the
    old one.
    """

    root: Path

    def path(self, model: str, task_id: str) -> Path:
        return self.root / model / f"{task_id}.json"

    def load(self, model: str, task_id: str, *, expect: str) -> dict | None:
        path = self.path(model, task_id)
        if not path.exists():
            return None
        record = json.loads(path.read_text())
        return record if record.get("instrument") == expect else None

    def write(self, model: str, record: Mapping[str, Any]) -> Path:
        path = self.path(model, str(record["task_id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2, sort_keys=True))
        return path


def audit_session(
    invoke: Invoker, model: str, record: Mapping[str, Any], item: Mapping[str, Any],
    *, store: VerdictStore | None = None, attempts: int = DEFAULT_ATTEMPTS,
) -> dict[str, Any]:
    """One session, read by one model. Returns the verdict, cached if a store is given.

    Transport is retried and content is not, which is §7.3's rule and the same one
    `judge.grade_cell` follows. The reader arm did not honour it before: it made exactly one
    call, so a timeout, a non-zero exit or a truncated reply cost the session outright, and the
    2026-08-18 pass lost 2 of 67 that way — 3%, each recorded as a **blocking** `unreadable`.
    At the 50-subject size that is ~18 sessions and ~18 false blockers.
    """
    prompt = audit_prompt(record, item)
    instrument = fingerprint(AUDIT_SYSTEM, prompt)
    task_id = str(record["task_id"])
    if store is not None:
        cached = store.load(model, task_id, expect=instrument)
        if cached is not None:
            return cached
    for attempt in range(1, max(1, attempts) + 1):
        try:
            raw = invoke(model, AUDIT_SYSTEM, prompt)
            verdict = parse_verdict(raw)
            break
        except TransportError:
            if attempt >= max(1, attempts):
                raise
            sleep(RETRY_BACKOFF ** attempt)
    out = {
        "task_id": task_id, "subject_id": record["subject_id"], "model": model,
        "instrument": instrument, "raw": raw, **verdict,
    }
    if store is not None:
        store.write(model, out)
    return out


def _cited(entry: Mapping[str, Any]) -> tuple[int, ...]:
    """The message numbers one reader entry cites, cleaned.

    A reply is model output, so it is validated rather than trusted: non-integers and
    negatives are dropped and duplicates collapsed. Dropping a bad number costs the finding
    its span and falls back to `locate_quote`; *keeping* one would point a repair at a
    message that does not exist, which is the failure this whole change exists to stop.
    """
    return _numbers(entry, "messages")


def _on_page(entry: Mapping[str, Any], *, distribution: bool) -> bool:
    """Whether the reader placed the scheduled occasion on the page.

    An ordinary beat is on the page when the reader marks it `delivered`. A distribution beat
    is briefed as exactly ONE bin instantiated as a single concrete occasion
    (`schedule._distribution_instruction`), and a reader never marks *that* `delivered` — a
    distribution is not pinned by one line — so for these fields the occasion is present when
    the reader narrowed toward the bin or quoted it. Reconciling a distribution beat on the
    binary `delivered` gate read six correctly narrowed n50 occasions as absent (gen-007-s16,
    -019-s19, -023-s5, -024-s9, -032-s14, -045-s11): every one reported the bin as `narrowed`
    or quoted it and still failed the gate. A genuinely absent occasion — no entry, or an
    entry with neither strength nor quote — still fails this and still fires `undelivered`.
    """
    if entry.get("delivered"):
        return True
    return distribution and (
        str(entry.get("strength") or "") in ("narrowed", "pinned")
        or bool(entry.get("quote"))
    )


def _removable(entry: Mapping[str, Any]) -> tuple[int, ...]:
    """The reader's proposed deletion, cleaned the same way `_cited` cleans a span.

    Validated identically and for a stronger reason: a bad number here would delete a message
    the reader did not mean, and unlike a misdirected rewrite that is not recoverable from the
    candidate — the content is simply gone. `Finding.deletable` then requires the result to be
    a subset of the defect's own span, so a reader cannot propose removing a page.
    """
    return _numbers(entry, "removable")


def _numbers(entry: Mapping[str, Any], key: str) -> tuple[int, ...]:
    out: list[int] = []
    for value in entry.get(key) or ():
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number >= 0 and number not in out:
            out.append(number)
    return tuple(out)


def reconcile(
    record: Mapping[str, Any], item: Mapping[str, Any], verdict: Mapping[str, Any]
) -> list[Finding]:
    """The reader's verdict against the schedule's claim, for one session.

    The demote-only rule lives here. A reader reporting a cell *stronger* than the schedule
    placed it produces a `disagreement` note and changes nothing, because ADR-0009/0010
    measured the schedule to be the more accurate record where the two differ. A reader
    taking a cell away, moving it to another person, or finding one nobody scheduled is taken
    at face value, because those are the three failures the schedule cannot see at all.
    """
    task_id = str(record["task_id"])
    beats = record.get("beats") or []
    scheduled = {str(b["field_id"]): b for b in beats}
    # Grouped, not keyed. A dict comprehension here was last-wins, and the reader's natural
    # shape for a field mentioned twice is TWO entries of `moments: 1` rather than one entry
    # of 2 — so `repeat_not_delivered` read the second entry's 1 against the map's 2 and fired
    # on a page that carried both moments. It fired on all three exposed sessions, in two
    # stores, across a full re-author, and the excerpts say every one of them was delivered.
    # The same collapse silently dropped the first entry from the attribution and strength
    # checks too.
    read: dict[str, list[Mapping[str, Any]]] = {}
    for entry in verdict.get("beats") or ():
        read.setdefault(str(entry.get("field_id")), []).append(entry)
    found: list[Finding] = []

    for field_id, beat in sorted(scheduled.items()):
        entries = read.get(field_id) or []
        # A distribution beat carries a `bin` and no other beat does. The reader never marks
        # one `delivered` (a distribution is not pinned by one line), so it reconciles on the
        # occasion the bin instantiates — narrowed or quoted — not on the binary gate. See
        # `_on_page`.
        distribution = beat.get("bin") is not None
        delivered = [e for e in entries if _on_page(e, distribution=distribution)]
        third = beat.get("attributed") == schedule.THIRD_PARTY
        # A field can carry two voices in one session — `gen-001-s2` has the subject's own
        # shellfish line and a companion's sesame line, and the schedule places the trap. So
        # the entry to reconcile against is the one in the voice the beat was placed in, and
        # only its absence is a collapse. Picking the first delivered entry instead read the
        # subject's own correct line as the trap collapsing, which is the last-wins bug
        # mirrored rather than fixed.
        want_whose = schedule.THIRD_PARTY if third else schedule.USER
        in_voice = [e for e in delivered if str(e.get("whose") or "") == want_whose]
        seen = (in_voice or delivered or entries or [None])[0]
        if seen is None or not _on_page(seen, distribution=distribution):
            found.append(Finding(
                "undelivered", BLOCKING, task_id,
                "the schedule places this beat and the reader does not find it on the page",
                field_id=field_id,
            ))
            continue
        whose = str(seen.get("whose") or "ambiguous")
        if third and whose != schedule.THIRD_PARTY:
            found.append(Finding(
                "trap_collapsed", BLOCKING, task_id,
                f"a companion's constraint reads as {whose!r}; the trap has become false "
                "evidence about the subject instead of a test of attribution",
                field_id=field_id, quote=str(seen.get("quote") or ""),
                messages=_cited(seen),
            ))
        # `misattributed` fires on a POSITIVE attribution to a companion and on nothing else.
        # `AUDIT_SYSTEM` gives the reader three words and defines the third itself: THIRD_PARTY
        # "whenever the requirement is stated as another person's", AMBIGUOUS "if the page
        # genuinely does not settle whose it is". Ambiguous is the reader declining to say —
        # not the subject's value being handed to somebody else, which is what this finding
        # names and what its own message claims.
        #
        # This started as a carve-out for distributions (`gen-023-s3`: a table for four reads
        # first-person-plural, so the reader hedges), and the carve-out was drawn too narrow.
        # Measured over the whole two-model n50 pass, 1224 verdicts: `whose` is `ambiguous` six
        # times, and **every `misattributed` either arm has ever filed — all five — is one of
        # them.** Zero were a companion's constraint. Four are the same shape one field over,
        # an ADR-0035 `allergies` disclosure answered for a group booking ("any allergies the
        # kitchen should know about?" — "no allergies, nothing like that", for six of us), and
        # the fifth (`gen-003-s7`) was hand-dismissed by the owner on 2026-08-25 with the same
        # reasoning. A finding class with five false positives and no true ones is not a strict
        # gate; it is a rule reading a hedge as an accusation.
        elif not third and whose == schedule.THIRD_PARTY:
            found.append(Finding(
                "misattributed", BLOCKING, task_id,
                f"the subject's own value reads as {whose!r}; the map credits the subject "
                "for evidence about somebody else",
                field_id=field_id, quote=str(seen.get("quote") or ""),
                messages=_cited(seen),
            ))
        want = sum(1 for b in beats if str(b["field_id"]) == field_id and not third)
        # Summed across the entries in the beat's own voice: one entry saying `moments: 2`
        # and two entries saying `moments: 1` are the same page and must reconcile the same
        # way, and a companion's line is not one of the subject's moments.
        got = sum(int(e.get("moments") or 1) for e in (in_voice or delivered))
        if want > 1 and got < want:
            found.append(Finding(
                "repeat_not_delivered", BLOCKING, task_id,
                f"the map counts {want} mentions and the reader finds {got}; the pin "
                "threshold is being met by an instruction rather than by the page",
                field_id=field_id,
            ))
        if beat.get("move") in schedule.NARROWING and seen.get("strength") == "pinned":
            found.append(Finding(
                "over_delivered", NOTE, task_id,
                "a narrowing move reads as pinning the value, so the page is stronger than "
                "the map claims; not acted on (ADR-0009/0010: the schedule is the record)",
                field_id=field_id, quote=str(seen.get("quote") or ""),
                messages=_cited(seen),
            ))

    checkpoint = str(record.get("checkpoint"))
    grades = item.get("provisional", {}).get(checkpoint, {})
    for entry in verdict.get("unscheduled") or ():
        field_id = str(entry.get("field_id") or "")
        if field_id in scheduled or field_id not in fields.BY_ID:
            continue
        if grades.get(field_id) == "UNEVIDENCED":
            found.append(Finding(
                "leak", BLOCKING, task_id,
                "readable here, and the map calls it UNEVIDENCED at this checkpoint — a twin "
                "that commits is scored as fabricating on a cell the page supports",
                field_id=field_id, quote=str(entry.get("quote") or ""),
                messages=_cited(entry), removable_messages=_removable(entry),
            ))
        else:
            found.append(Finding(
                "disagreement", NOTE, task_id,
                f"readable here with no beat, and the map already says "
                f"{grades.get(field_id)}; carried as reinforcement rather than as a leak",
                field_id=field_id, quote=str(entry.get("quote") or ""),
                messages=_cited(entry),
            ))

    for entry in verdict.get("contradictions") or ():
        found.append(Finding(
            "contradiction", BLOCKING, task_id,
            str(entry.get("why") or "the page contradicts the card"),
            field_id=str(entry.get("field_id") or ""),
            quote=str(entry.get("quote") or ""),
            messages=_cited(entry), removable_messages=_removable(entry),
        ))
    return found


def audit(
    corpus: Path, store: Path, *, invoke: Invoker, model: str = DEFAULT_MODEL,
    sample: Sequence[str] | None = None, workers: int = DEFAULT_WORKERS,
    verdicts: VerdictStore | None = None,
) -> Report:
    """The model arm over a chosen set of sessions."""
    items = _items(corpus)
    records = load_store(store)
    chosen = list(sample) if sample is not None else sorted(records)
    report = Report()

    def one(task_id: str) -> list[Finding]:
        record = records.get(task_id)
        if record is None:
            return [Finding("missing", BLOCKING, task_id, "not in the store")]
        item = items.get(str(record["subject_id"]))
        if item is None:                                  # pragma: no cover - defensive
            return [Finding("orphan", NOTE, task_id, "no corpus item")]
        try:
            verdict = audit_session(invoke, model, record, item, store=verdicts)
        except TransportError as exc:
            return [Finding("transport", NOTE, task_id, f"no answer: {exc}")]
        except ValueError as exc:
            return [Finding("unreadable", BLOCKING, task_id, f"unparsed reply: {exc}")]
        return reconcile(record, item, verdict)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for findings in pool.map(one, chosen):
            for finding in findings:
                report.add(_located(finding, records))
    return report


def stratified_sample(
    corpus: Path, store: Path, size: int = DEFAULT_SAMPLE
) -> list[str]:
    """Sessions worth spending a call on, chosen for what they can falsify.

    Not uniform. A uniform sample of 612 sessions spends most of its budget on the ordinary
    case, and the questions this audit exists to answer live in specific places: the traps,
    the sessions a rule already flagged, the within-session repeats the pin threshold rests
    on, and the distractors that are supposed to carry nothing. The remainder is filled at
    random so the ordinary case is still represented.
    """
    items = _items(corpus)
    records = load_store(store)
    priority: list[str] = []
    ordinary: list[str] = []
    for subject_id, item in sorted(items.items()):
        for session in item["sessions"]:
            task_id = str(session["task_id"])
            if task_id not in records:
                continue
            beats = session.get("beats") or []
            counts = Counter(str(b["field_id"]) for b in beats)
            interesting = (
                any(b.get("attributed") == schedule.THIRD_PARTY for b in beats)
                or any(n > 1 for n in counts.values())
                or bool(records[task_id].get("problem_kinds"))
                or bool(session.get("is_distractor"))
                or any(not b.get("supports_gold", True) for b in beats)
            )
            (priority if interesting else ordinary).append(task_id)
    chosen = priority[:size]
    step = max(1, len(ordinary) // max(1, size - len(chosen))) if len(chosen) < size else 1
    chosen += ordinary[::step][: size - len(chosen)]
    return sorted(chosen)


# --- reporting ---------------------------------------------------------------


def format_report(report: Report, *, limit: int = 12) -> str:
    lines = [
        f"{len(report.findings)} findings, {len(report.blocking)} blocking",
        "",
    ]
    by_kind: dict[str, list[Finding]] = {}
    for finding in report.findings:
        by_kind.setdefault(finding.kind, []).append(finding)
    for kind in sorted(by_kind, key=lambda k: (-len(by_kind[k]), k)):
        group = by_kind[kind]
        severity = "blocking" if any(f.severity == BLOCKING for f in group) else "note"
        lines.append(f"## {kind}  ({len(group)}, {severity})")
        for finding in group[:limit]:
            where = finding.task_id + (f" {finding.field_id}" if finding.field_id else "")
            quote = f"  — {finding.quote!r}" if finding.quote else ""
            lines.append(f"   {where}: {finding.detail}{quote}")
        if len(group) > limit:
            lines.append(f"   ... +{len(group) - limit} more")
        lines.append("")
    if not report.findings:
        lines.append("nothing found")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:      # pragma: no cover - CLI
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True,
                        help="the per-author transcript directory, e.g. authored-50/claude-opus-5")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="reader for the model arm; omit --audit to skip it entirely")
    parser.add_argument("--audit", action="store_true",
                        help="run the model arm. Costs one call per sampled session.")
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    parser.add_argument("--all", action="store_true",
                        help="audit every session rather than a sample")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--verdicts", type=Path, default=None,
                        help="cache directory for model verdicts")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    report = inspect(args.corpus, args.store)
    print("=== deterministic arm (free) ===\n")
    print(format_report(report))

    if args.audit:
        chosen = (
            sorted(load_store(args.store)) if args.all
            else stratified_sample(args.corpus, args.store, args.sample)
        )
        print(f"\n=== model arm: {len(chosen)} sessions, {args.model} ===\n")
        model_report = audit(
            args.corpus, args.store, invoke=ClaudeCli(), model=args.model,
            sample=chosen, workers=args.workers,
            verdicts=VerdictStore(args.verdicts) if args.verdicts else None,
        )
        print(format_report(model_report))
        report.findings.extend(model_report.findings)
        report.counted.update(model_report.counted)

    if args.json:
        args.json.write_text(json.dumps(report.to_json(), indent=2))
    return 1 if report.blocking else 0


if __name__ == "__main__":                               # pragma: no cover - CLI
    raise SystemExit(main())
