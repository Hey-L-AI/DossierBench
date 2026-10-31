"""§6's transcript author, as a runner: two author models, sequential within a subject.

`transcript.py` holds the prompts and the deterministic §6.1 checks; this is the loop that
spends them. It exists because the only authoring driver on this project was
`probe_reauthor/run.py` — six subjects, one tier, a path-keyed cache — and the dataset is 79
subjects across three tiers under §7.1's two author models, where every one of that driver's
shortcuts becomes a defect.

It is `judge.py`'s twin and deliberately so: the same injected `Invoker`, the same
instrument-fingerprinted cache, the same transport-only retry rule, the same published
manifest. Four rules are executable rather than instructed:

**Sequential within a subject, parallel across them (§6.0).** Session *k* is authored with
sessions 1..k-1 verbatim in the prompt, so a subject is a *chain*, not a set of independent
calls — callbacks, reinforcement and §5's scripted corrections do not cohere otherwise. The
unit of parallelism is therefore `(author, subject)`, and the rich tier's 11-session chains
are the run's critical path. A worker pool over sessions would be faster and would author
session 5 without session 4.

**Transport is retried; a rejected session is re-authored with its violations named.** Two
different failures with two different rules. A non-zero exit or an empty reply obtained no
answer and is retried (`judge.py`'s rule). A session that arrives and breaks a §6.1 check is
a *content* failure, and it is re-authored **with the flagged lines quoted** rather than
re-rolled blind, because a retry that repeats the brief is a lottery. The budget is bounded
(`RETRY_BUDGET`) and a session that exhausts it is **kept and marked**, never silently
dropped: `probe_coherence/` recorded that a retry budget spent against a known defect
corrupts the retry statistics you then read as a signal, and §4.6 wants the count published.

**An unreadable reply consumes a content attempt and is never kept as text.** Unlike a
grading, an authored session that cannot be parsed has nothing to record — there is no
transcript to grade. It costs an attempt, and on exhaustion the record is written with empty
text and `exhausted: true`. That is not a shorter timeline: `judge.Bundle.prefix` returns
`None` for any bundle with an empty session, so every checkpoint at or after the hole is
skipped by the grader and counted in its manifest. A hole is visible; a silently shortened
timeline is not.

**The cache knows which brief produced a transcript.** Every record carries a fingerprint of
the exact system prompt and brief behind it, and a mismatch is a miss. The brief contains
every prior session verbatim, so re-authoring session 3 invalidates sessions 4..n by
construction — which is the correct behaviour and the reason the fingerprint covers the
prompt rather than the beats.

Records are written in the shape `judge.Bundle.from_records` already reads, so the grading
runner consumes this output with no translation.

Usage:
    python -m persona_twin.author --corpus work/corpus.json \\
        --out work/authored --plan
    python -m persona_twin.author --corpus work/corpus.json \\
        --out work/authored --workers 8
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field as dc_field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import transcript
from .judge import ClaudeCli, Invoker, TransportError, fingerprint  # noqa: F401

#: §7.1: the transcripts are authored by **two** models, and the author is excluded from its
#: own judge pool (§7.3). Two is the floor, not a budget decision — one author model makes
#: every idiosyncrasy of that model a property of the dataset (§6.0's uniformity risk), which
#: `authoring/idiosyncrasies.py` measured directly and could only mitigate.
DEFAULT_AUTHORS: tuple[str, ...] = ("claude-opus-5", "claude-sonnet-5")

DEFAULT_WORKERS: int = 8
DEFAULT_TIMEOUT: int = 420

#: Transport attempts per call. Content is retried separately and on a different rule.
DEFAULT_ATTEMPTS: int = 3
RETRY_BACKOFF: float = 2.0

#: Re-authoring attempts for a session that breaks a §6.1 check or comes back unreadable.
#: Bounded and published — a session that exhausts it ships marked.
RETRY_BUDGET: int = 2


# --- what gets authored ------------------------------------------------------


@dataclass(frozen=True)
class Task:
    """One authoring call, before an author model is attached."""

    task_id: str
    subject_id: str
    session: int
    checkpoint: int
    is_distractor: bool
    #: Target conversation length and calendar date, both drawn per session in v0.1. v0 had
    #: neither: every session was briefed "8-16 turns" and carried no date at all.
    turns: int = 12
    date: str = ""
    removable: bool = False
    beats: tuple[Mapping[str, Any], ...] = ()
    #: What must stay true in this session without being evidenced in it. Empty for a corpus
    #: built before ADR-0036, which is why it defaults rather than being required — the
    #: absence changes the brief, so it also changes the fingerprint, and a pre-ADR store
    #: re-authors instead of cache-hitting a session written under different instructions.
    continuity: Mapping[str, Any] = dc_field(default_factory=dict)
    #: v0.5: the subject's whole card and its §8.5 free text, for `transcript.persona_portrait`.
    #: These live on the corpus **item**, not on the session — every session of a subject gets
    #: the same two — so they are attached by `Chain.from_item` rather than read by
    #: `Task.from_json`, which only ever sees one session's record. Defaulting to empty is what
    #: lets a pre-v0.5 corpus brief byte-identically and so keep its cache.
    card: Mapping[str, Any] = dc_field(default_factory=dict)
    idiosyncrasies: str = ""

    @classmethod
    def from_json(cls, record: Mapping[str, Any]) -> Task:
        return cls(
            task_id=str(record["task_id"]), subject_id=str(record["subject_id"]),
            session=int(record["session"]), checkpoint=int(record["checkpoint"]),
            is_distractor=bool(record.get("is_distractor")),
            turns=int(record.get("turns", 12)), date=str(record.get("date", "")),
            removable=bool(record.get("removable")),
            beats=tuple(dict(b) for b in record.get("beats") or ()),
            continuity=dict(record.get("continuity") or {}),
        )


@dataclass(frozen=True)
class Chain:
    """Every session of one subject, in order — the unit of work and of parallelism (§6.0)."""

    subject_id: str
    tasks: tuple[Task, ...]

    @classmethod
    def from_item(cls, item: Mapping[str, Any]) -> Chain:
        # The card is a property of the subject and the corpus stores it once, at this level.
        # Attaching it here rather than in `Task.from_json` keeps the session record the
        # schedule writes unchanged, and means a corpus without one simply briefs without a
        # portrait — see `Task.card`.
        card = dict(item.get("card") or {})
        idiosyncrasies = str(item.get("idiosyncrasies") or "")
        tasks = tuple(
            replace(Task.from_json(task), card=card, idiosyncrasies=idiosyncrasies)
            for task in sorted(item["sessions"], key=lambda t: int(t["session"]))
        )
        sessions = [task.session for task in tasks]
        if sessions != list(range(len(sessions))):
            raise ValueError(
                f"{item['subject_id']}: sessions {sessions} are not dense from 0; §6.0 "
                "authors a prefix and a gap would be authored as though it were not there"
            )
        return cls(str(item["subject_id"]), tasks)


def load_corpus(path: Path) -> list[Chain]:
    payload = json.loads(path.read_text())
    items = payload["items"] if isinstance(payload, dict) else payload
    return [Chain.from_item(item) for item in items]


# --- the store ---------------------------------------------------------------


@dataclass
class TranscriptStore:
    """Where authored sessions live, and the rule for reusing one.

    Layout is `<root>/<author>/<task_id>.json`, which is what `probe_reauthor/` wrote and
    what `judge.py --bundles` already reads — so the two runners meet at a directory rather
    than at an adapter.
    """

    root: Path

    def path(self, author: str, task_id: str) -> Path:
        return self.root / author / f"{task_id}.json"

    def load(self, author: str, task_id: str, *, expect: str | None = None) -> dict | None:
        """A cached record, or `None` when there is none or another brief produced it.

        A record without a fingerprint is stale by construction: the probe runners wrote
        transcripts before fingerprints existed, and reusing one would put text authored
        from a superseded brief into a dataset whose provenance claims otherwise.
        """
        path = self.path(author, task_id)
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if expect is not None and record.get("instrument") != expect:
            return None
        return record

    def write(self, record: Mapping[str, Any]) -> Path:
        path = self.path(str(record["author"]), str(record["task_id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2, default=str))
        return path

    def records(self) -> list[dict]:
        """Every authored record under this root, and only those.

        The same two exclusions `judge.GradingStore.records` documents: macOS AppleDouble
        `._name` sidecars match `*.json` on this filesystem and once made a 72-call run read
        as 144, and `manifest.json` is a run summary rather than a session.
        """
        out = []
        for path in sorted(self.root.rglob("*.json")):
            if path.name.startswith("._") or path.name == "manifest.json":
                continue
            try:
                out.append(json.loads(path.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
        return out


# --- parsing -----------------------------------------------------------------


def parse_reply(raw: str) -> tuple[str, str | None, tuple[int, ...]]:
    """`(transcript, self_report, free_offers)` from one author reply. Raises without a transcript.

    The §4.2 self-report is **declared by the author**, not inferred: `check_session` exempts
    exactly the line it is given, because a rule cannot tell a scripted self-description from
    an authoring defect and the one time it tried it flagged the hard case it was protecting.

    `free_offers` is the offer template's escape, read the same way and for the same stated
    reason (`transcript._OFFER_TEMPLATE`): the message numbers of bot turns written outside the
    skeleton. Unreadable entries are dropped rather than raised on — a malformed escape leaves
    the turn undeclared, which is a finding `_offer_problems` reports, and that is a better
    failure than throwing away a transcript over a stray list element.
    """
    reply = _json_object(raw)
    text = str(reply.get("transcript") or "").strip()
    if not text:
        raise ValueError("reply carries no transcript")
    self_report = reply.get("self_report") or None
    declared = reply.get("free_offers") or ()
    if isinstance(declared, (str, int)):
        declared = [declared]
    free_offers = []
    for entry in declared:
        try:
            free_offers.append(int(str(entry).strip().strip("[]")))
        except (TypeError, ValueError):
            continue
    return (
        text,
        (str(self_report).strip() or None if self_report else None),
        tuple(sorted(set(free_offers))),
    )


def _json_object(raw: str) -> dict[str, Any]:
    """The last balanced JSON object in a reply, which is what the author models emit.

    Copied in behaviour from the probes' `parse_json` rather than imported from one: `src/`
    does not depend on `probe_*/`, and this is eight lines.
    """
    depth, start = 0, None
    best: str | None = None
    for index, char in enumerate(raw):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                best = raw[start:index + 1]
    if best is None:
        raise ValueError("no JSON object in reply")
    parsed = json.loads(best)
    if not isinstance(parsed, dict):
        raise ValueError(f"reply parsed as {type(parsed).__name__}, not an object")
    return parsed


def retry_brief(brief: str, problems: Sequence[str]) -> str:
    """The re-authoring prompt: the original brief plus the lines that broke a rule.

    Naming the violation is the whole difference between a fix and a lottery, and it is
    measured — `probe_reauthor/` took §6.1 register defects from 5 sessions in 30 to 0 under
    this rule, on prompts that were otherwise unchanged.
    """
    return brief + (
        "\n\nYour previous attempt broke these rules. Rewrite the whole session, keeping "
        "everything else, and fix:\n" + "\n".join(f"  - {p}" for p in problems)
    )


# --- the run -----------------------------------------------------------------


@dataclass
class RunReport:
    """What the authoring run publishes about itself.

    Per author model, because §7.1's whole reason for two of them is that a defect rate is a
    property of a model — and §4.6 wants the regeneration accounting published rather than
    summarized. Every mutation takes the lock: `run` fans out over a thread pool, and an
    unlocked counter under-reports exactly the rate that is being measured.
    """

    authors: tuple[str, ...] = ()
    written: int = 0
    cached: int = 0
    failed: list[str] = dc_field(default_factory=list)
    exhausted: list[str] = dc_field(default_factory=list)
    per_author: dict[str, Counter] = dc_field(default_factory=dict)
    problem_kinds: dict[str, Counter] = dc_field(default_factory=dict)
    _lock: threading.Lock = dc_field(default_factory=threading.Lock, repr=False)

    def note(self, author: str, key: str, count: int = 1) -> None:
        with self._lock:
            self.per_author.setdefault(author, Counter())[key] += count

    def note_problem(self, author: str, kind: str) -> None:
        with self._lock:
            self.problem_kinds.setdefault(author, Counter())[kind] += 1

    def record_written(self) -> None:
        with self._lock:
            self.written += 1

    def record_cached(self) -> None:
        with self._lock:
            self.cached += 1

    def record_failed(self, message: str) -> None:
        with self._lock:
            self.failed.append(message)

    def record_exhausted(self, message: str) -> None:
        with self._lock:
            self.exhausted.append(message)

    def to_json(self) -> dict[str, Any]:
        return {
            "authors": list(self.authors), "written": self.written, "cached": self.cached,
            "failed": self.failed, "exhausted": self.exhausted,
            "per_author": {a: dict(c) for a, c in sorted(self.per_author.items())},
            "problem_kinds": {
                a: dict(c) for a, c in sorted(self.problem_kinds.items())
            },
        }


def plan(chains: Sequence[Chain], *, authors: Sequence[str] = DEFAULT_AUTHORS) -> list[str]:
    """Every call the run would make, in a stable order. Costs nothing and is printed first."""
    return [
        f"{author} {task.task_id}"
        for chain in sorted(chains, key=lambda c: c.subject_id)
        for author in authors
        for task in chain.tasks
    ]


def brief_for(task: Task, prior_date: str = "") -> str:
    return transcript.session_brief(
        session=task.session,
        beats=list(task.beats),
        turns=task.turns,
        date=task.date,
        prior_date=prior_date,
        is_distractor=task.is_distractor,
        distractor_scenario=transcript.distractor_scenario_for(
            task.subject_id, task.session
        ),
        continuity=task.continuity,
        card=task.card,
        idiosyncrasies=task.idiosyncrasies,
    )


def author_session(
    author: str, task: Task, store: TranscriptStore, invoke: Invoker,
    *, attempts: int = DEFAULT_ATTEMPTS, budget: int = RETRY_BUDGET,
    report: RunReport | None = None, sleep: Callable[[float], None] = time.sleep,
    prior_date: str = "",
) -> tuple[str, str]:
    """Author one session. Returns `(text, status)`; `text` is `""` when nothing usable arrived.

    The two retry rules meet here and are kept apart: `attempts` covers transport per call,
    `budget` covers content across re-authorings.

    v0.4: this session is briefed from its own beats and `prior_date` only — never from
    another session's authored text (`transcript.session_brief`'s docstring has the
    rationale). `prior_date` is schedule data (`Chain.tasks[session - 1].date`), known before
    anything is authored, so every task in a chain can be briefed and authored independently.
    """
    report = report or RunReport()
    brief = brief_for(task, prior_date)
    instrument = fingerprint(transcript.AUTHOR_SYSTEM, brief)

    cached = store.load(author, task.task_id, expect=instrument)
    if cached is not None:
        report.record_cached()
        report.note(author, "cached")
        return str(cached.get("transcript") or ""), f"cached {author} {task.task_id}"

    tries: list[dict[str, Any]] = []
    text, self_report, problems, error = "", None, [], None
    free_offers: tuple[int, ...] = ()
    prompt = brief
    for attempt in range(budget + 1):
        raw, last = "", None
        for transport in range(1, attempts + 1):
            try:
                raw = invoke(author, transcript.AUTHOR_SYSTEM, prompt)
                last = None
                break
            except Exception as exc:                                  # noqa: BLE001
                last = exc
                report.note(author, "transport_retry")
                if transport < attempts:
                    sleep(RETRY_BACKOFF ** transport)
        if last is not None:
            # No answer was obtained. Unlike a rejected session there is nothing to name in a
            # re-brief, so this session is left unwritten — `mark_stopped` covers it. v0.4:
            # sessions no longer depend on each other's text, so this does NOT stop the rest
            # of the chain; `author_chain` keeps going and authors k+1 regardless.
            message = (f"FAILED {author} {task.task_id}: "
                       f"{type(last).__name__}: {last}")
            report.record_failed(message)
            report.note(author, "transport_failed")
            return "", message

        try:
            text, self_report, free_offers = parse_reply(raw)
            error = None
        except Exception as exc:                                      # noqa: BLE001
            text, self_report, free_offers = "", None, ()
            error = f"{type(exc).__name__}: {exc}"
            report.note(author, "unreadable_replies")

        # `require_numbering`: v0.4 message numbers are part of the authored artifact, so a
        # session that omits or misnumbers them is a content failure and re-authored with the
        # violation named, exactly like any other §6.1 break. Enforced here rather than in
        # `check_session`'s default so that replaying today's rules over a pre-v0.4 store
        # (`verify.check_record`) does not flag every session in it for a rule that did not
        # exist when it was written.
        problems = [] if error else transcript.check_session(
            text, self_report=self_report, beats=task.beats, require_numbering=True,
            free_offers=free_offers,
        )
        tries.append({
            "attempt": attempt, "error": error,
            "problems": [str(p) for p in problems],
            "problem_kinds": [p.kind for p in problems],
            "raw": raw,
        })
        if not error and not problems:
            break
        for problem in problems:
            report.note_problem(author, problem.kind)
        if attempt < budget:
            report.note(author, "content_retry")
            prompt = retry_brief(brief, [str(p) for p in problems] or [str(error)])

    exhausted = bool(problems or error)
    store.write({
        "task_id": task.task_id, "subject_id": task.subject_id, "author": author,
        "model": author,                 # `Bundle.from_records` accepts either; both written
        "session": task.session, "checkpoint": task.checkpoint,
        "turns": task.turns, "date": task.date, "removable": task.removable,
        "is_distractor": task.is_distractor, "beats": [dict(b) for b in task.beats],
        "instrument": instrument,
        "transcript": text if not error else "",
        "self_report": self_report,
        # The declared escapes from the offer template. Written whether or not there are any,
        # so "no key" means a pre-template store and "[]" means an author that escaped nothing.
        "free_offers": list(free_offers),
        "problems": [str(p) for p in problems],
        "problem_kinds": [p.kind for p in problems],
        "attempts": tries,
        "exhausted": exhausted,
        "error": error,
        "raw": tries[-1]["raw"] if tries else None,
    })
    report.record_written()
    report.note(author, "written")
    if free_offers:
        report.note(author, "free_offers", len(free_offers))
        report.note(author, "sessions_with_free_offers")
    report.note(author, "calls", len(tries))
    if exhausted:
        message = (f"EXHAUSTED {author} {task.task_id}: "
                   + (error or "; ".join(str(p) for p in problems)))
        report.record_exhausted(message)
        report.note(author, "exhausted")
        return ("" if error else text), message
    return text, (f"{author} {task.task_id}: {len(text)} chars, "
                  f"{len(tries)} attempt(s)")


#: What a placeholder record's `instrument` says. It is deliberately not a fingerprint, so
#: the cache never matches it and a re-run re-authors the session rather than treating the
#: hole as settled.
STOPPED_INSTRUMENT = "stopped"


def mark_stopped(author: str, task: Task, store: TranscriptStore) -> bool:
    """Write an empty placeholder for `task`, unless a record already exists for it.

    Returns whether it wrote one.

    Without this, a session whose transport calls all failed is **invisible to the
    grader**, and invisible in the one direction that does damage. `Bundle.prefix` returns
    `None` when a session it can see is empty — but a session with no record at all is not
    seen: the bundle simply has fewer sessions, and a rich checkpoint spanning sessions 4 and
    5 would be graded on session 4 alone. That is a *silently shorter prefix*, which is
    exactly what `Bundle.prefix`'s docstring says must never happen, arriving through the
    authoring stage instead of the grading one.

    v0.4: this is per-session, not "from session onward" — a session that fails no longer
    implies anything about its successors (`author.py` module docstring), so there is nothing
    to cascade. `author_session` itself already writes a record for every outcome except a
    transport failure that exhausts every attempt; this covers exactly that gap. A session
    that exhausted its *content* budget already has a record — an empty transcript and its
    problems — and that record is evidence a placeholder must not overwrite, which is what
    the existing-record check guards.

    A placeholder is not a transcript and is never mistaken for one: empty text, `stopped`
    set, and an `instrument` that cannot match any fingerprint.
    """
    if store.load(author, task.task_id) is not None:
        return False
    store.write({
        "task_id": task.task_id, "subject_id": task.subject_id, "author": author,
        "model": author, "session": task.session,
        "checkpoint": task.checkpoint, "turns": task.turns, "date": task.date,
        "removable": task.removable, "is_distractor": task.is_distractor,
        "beats": [dict(b) for b in task.beats],
        "instrument": STOPPED_INSTRUMENT,
        "transcript": "", "self_report": None, "free_offers": [],
        "problems": [], "problem_kinds": [], "attempts": [],
        "exhausted": False, "stopped": True,
        "error": "every transport attempt failed; this session was never authored",
        "raw": None,
    })
    return True


def author_chain(
    author: str, chain: Chain, store: TranscriptStore, invoke: Invoker,
    *, attempts: int = DEFAULT_ATTEMPTS, budget: int = RETRY_BUDGET,
    report: RunReport | None = None, on_result: Callable[[str], None] | None = None,
) -> list[str]:
    """Author every session of one subject (§6.0's chain, now independent per session).

    v0.4: a session is briefed from its own beats and its predecessor's *date* only — never
    from another session's text (`author_session`'s docstring). `prior_date` therefore comes
    from `chain.tasks[i - 1].date`, schedule data available up front, not from having
    authored anything. One session failing does not stop, and does not require re-authoring,
    any other: each `task` in the loop is independent, and the loop's only remaining job is
    to run them and to leave a visible placeholder (`mark_stopped`) where transport failed
    outright.
    """
    report = report or RunReport()
    messages: list[str] = []
    for index, task in enumerate(chain.tasks):
        prior_date = chain.tasks[index - 1].date if index > 0 else ""
        text, message = author_session(
            author, task, store, invoke,
            attempts=attempts, budget=budget, report=report, prior_date=prior_date,
        )
        messages.append(message)
        if on_result:
            on_result(message)
        if not text:
            # A placeholder only where nothing is on disk yet — a transport failure, since
            # `author_session` already recorded its own FAILED/EXHAUSTED entry and (for
            # content exhaustion) its own record; this must not double either.
            mark_stopped(author, task, store)
    return messages


def run(
    chains: Sequence[Chain], store: TranscriptStore, invoke: Invoker,
    *, authors: Sequence[str] = DEFAULT_AUTHORS, workers: int = DEFAULT_WORKERS,
    attempts: int = DEFAULT_ATTEMPTS, budget: int = RETRY_BUDGET,
    on_result: Callable[[str], None] | None = None,
) -> RunReport:
    """Author every session. Parallel across `(author, subject, session)`.

    **The unit is the session, not the chain.** v0.4 made a session independent of every
    other — it is briefed from its own beats and its predecessor's *date*, which is schedule
    data known up front, never from another session's text (`author_chain`'s docstring). The
    unit stayed the chain anyway, and on a corpus with few long subjects that is the whole
    wall clock: the 5-subject pilot has 57 sessions but only 5 chains, so `--workers 8`
    could never run more than 5 calls at once and the run ended when its 16-session subject
    did, with four workers idle. Per session, the same 57 calls saturate whatever `workers`
    is set to.

    This changes scheduling and nothing else. Each call's cache key, brief, and retry budget
    are what they were, so a re-run over an authored store still cache-hits byte-for-byte —
    `tests/test_author.py` pins that the two units author the same set of tasks.
    """
    report = RunReport(authors=tuple(authors))
    work = [
        (author, chain, index, task)
        for chain in sorted(chains, key=lambda c: c.subject_id)
        for author in authors
        for index, task in enumerate(chain.tasks)
    ]

    def one(unit: tuple[str, Chain, int, Task]) -> None:
        author, chain, index, task = unit
        # Schedule data, not authored text — which is exactly why this is parallelisable.
        prior_date = chain.tasks[index - 1].date if index > 0 else ""
        text, message = author_session(
            author, task, store, invoke,
            attempts=attempts, budget=budget, report=report, prior_date=prior_date,
        )
        if on_result:
            on_result(message)
        if not text:
            # A placeholder only where nothing is on disk yet — a transport failure, since
            # `author_session` already recorded its own FAILED/EXHAUSTED entry and (for
            # content exhaustion) its own record; this must not double either.
            mark_stopped(author, task, store)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        for _ in executor.map(one, work):
            pass
    return report


def verify(
    chains: Sequence[Chain], store: TranscriptStore, *,
    authors: Sequence[str] = DEFAULT_AUTHORS,
) -> list[str]:
    """Every way the authored corpus is not what the grader will assume. Empty is shippable.

    Run **before** grading, because grading is three times the spend and reads this directory
    as ground truth. The check that matters is the third: a session with no record at all is
    not a hole the grader can see, so a chain that is short by one session grades a checkpoint
    on a prefix that never existed.
    """
    problems: list[str] = []
    have = {
        (str(r.get("author") or r.get("model")), str(r["task_id"])): r
        for r in store.records()
    }
    for chain in chains:
        for author in authors:
            missing = [
                t.task_id for t in chain.tasks if (author, t.task_id) not in have
            ]
            if missing:
                problems.append(
                    f"{chain.subject_id} by {author}: {len(missing)} session(s) with no "
                    f"record at all ({', '.join(missing[:3])}"
                    f"{'…' if len(missing) > 3 else ''}) — the grader would read a short "
                    "prefix rather than skip the cell"
                )
                continue
            empty = [
                t.task_id for t in chain.tasks
                if not str(have[(author, t.task_id)].get("transcript") or "").strip()
            ]
            if empty:
                problems.append(
                    f"{chain.subject_id} by {author}: {len(empty)} empty session(s) "
                    f"({', '.join(empty[:3])}) — the grader skips every checkpoint from "
                    f"the first one on, which is correct and costs the bundle"
                )
    return problems


def format_report(report: RunReport) -> str:
    """The run manifest, printed and written beside the transcripts.

    §4.6 wants regeneration accounting published rather than summarized, and §11 lists it as
    an acceptance criterion — so the re-authoring counts and the exhausted sessions are
    columns here, not a footnote.
    """
    lines = [
        "§6 authoring run — parallel across (author, subject, session)", "",
        f"authors: {', '.join(report.authors)}",
        f"written {report.written}, cached {report.cached}, "
        f"exhausted {len(report.exhausted)}, failed {len(report.failed)}",
        "",
        f"{'author':22s} {'written':>8s} {'calls':>6s} {'cached':>7s} {'re-auth':>8s} "
        f"{'exhausted':>10s} {'unreadable':>11s} {'retries':>8s}",
    ]
    for author, counts in sorted(report.per_author.items()):
        lines.append(
            f"{author:22s} {counts['written']:>8d} {counts['calls']:>6d} "
            f"{counts['cached']:>7d} {counts['content_retry']:>8d} "
            f"{counts['exhausted']:>10d} {counts['unreadable_replies']:>11d} "
            f"{counts['transport_retry']:>8d}"
        )
    escaped = {
        author: counts for author, counts in sorted(report.per_author.items())
        if counts["free_offers"]
    }
    if escaped:
        # The offer template's escape rate, pre-registered as a number to read rather than one
        # for the audit to discover (`transcript._OFFER_TEMPLATE`). Cached sessions are not
        # counted here — they were never parsed — so read it against `written`, not the corpus.
        lines += ["", "declared `free_offers` escapes (of sessions written this run):"]
        for author, counts in escaped.items():
            lines.append(
                f"  {author:22s} {counts['free_offers']} turn(s) across "
                f"{counts['sessions_with_free_offers']} of {counts['written']} sessions"
            )
    if report.problem_kinds:
        lines += ["", "§6.1 violations by kind (every attempt, not only the kept one):"]
        for author, counts in sorted(report.problem_kinds.items()):
            detail = ", ".join(f"{kind} {n}" for kind, n in sorted(counts.items()))
            lines.append(f"  {author:22s} {detail}")
    if report.exhausted:
        lines += ["", "exhausted the re-authoring budget (kept and marked, §4.6):"]
        lines += [f"  {message}" for message in report.exhausted[:20]]
    if report.failed:
        lines += ["", "no usable transcript (the chain stopped; the prefix has a hole):"]
        lines += [f"  {message}" for message in report.failed[:20]]
    lines += [
        "",
        "A session that exhausts the budget ships marked rather than being re-rolled: a "
        "retry loop",
        "against a known defect regenerates the construction the quota exists to carry "
        "(§4.4, §7.2,",
        "and §6.1's own self-report exemption are three instances). A chain that stops "
        "leaves a hole",
        "in the prefix, which the grader skips and counts — never a silently shorter "
        "timeline.",
    ]
    return "\n".join(lines)


# --- CLI ---------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:      # pragma: no cover - CLI
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", type=Path, required=True,
                        help="dataset.py's corpus.json")
    parser.add_argument("--out", type=Path, required=True,
                        help="directory for authored session records")
    parser.add_argument("--authors", nargs="+", default=list(DEFAULT_AUTHORS))
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS,
                        help="transport attempts per call; content is never retried blind")
    parser.add_argument("--budget", type=int, default=RETRY_BUDGET,
                        help="re-authorings of a session that breaks a §6.1 check")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--subjects", nargs="+", default=None)
    parser.add_argument("--plan", action="store_true",
                        help="print the calls this run would make and exit")
    parser.add_argument("--verify", action="store_true",
                        help="check the authored corpus is complete and exit; run this "
                             "before grading, which costs three times as much and reads "
                             "this directory as ground truth")
    args = parser.parse_args(argv)

    chains = load_corpus(args.corpus)
    if args.subjects:
        chains = [c for c in chains if c.subject_id in set(args.subjects)]
    if not chains:
        raise SystemExit("no chains selected")

    if args.verify:
        problems = verify(chains, TranscriptStore(args.out), authors=args.authors)
        print(f"{len(chains)} subjects x {len(args.authors)} authors; "
              f"{len(problems)} problem(s)")
        for problem in problems:
            print(f"  {problem}")
        return 1 if problems else 0

    calls = plan(chains, authors=args.authors)
    print(f"{len(chains)} subjects, {len(calls)} authoring calls, "
          f"authors {', '.join(args.authors)}")
    for chain in chains:
        print(f"  {chain.subject_id}: {len(chain.tasks)} sessions "
              f"x {len(args.authors)} authors")
    if args.plan:
        return 0

    store = TranscriptStore(args.out)
    report = run(
        chains, store, ClaudeCli(timeout=args.timeout), authors=args.authors,
        workers=args.workers, attempts=args.attempts, budget=args.budget,
        on_result=lambda message: print(message, flush=True),
    )
    text = format_report(report)
    print("\n" + text)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "manifest.json").write_text(
        json.dumps(report.to_json(), indent=2, default=str)
    )
    return 1 if report.failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
