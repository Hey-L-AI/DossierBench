"""The v0.1 corpus: one lane, per-subject timeline lengths, extensible in both directions.

v0's job here was tier allocation — how many subjects of which tier, spread evenly over the
§4.1 stratum grid so a tier never correlated with a corner of it. **v0.1 has no tiers**, so
that machinery is gone and what replaces it is smaller: each subject gets a *length*, and the
evidence state a consumer wants (sparse, medium, rich) is read at a prefix rather than
selected by subject.

Two kinds of extension have to stay cheap, and this module is built so both are:

* **more conversations** — raise a subject's `session_count`. `schedule.build_schedule` is
  prefix-stable, so sessions 0..k-1 come back byte-identical, their transcripts cache-hit in
  `author.py` and their gradings cache-hit in `judge.py`. Only the new tail costs anything.
* **more personas** — raise `n`. `generator.generate` is deterministic in `(n, seed)` and
  assigns subject ids by index, so… **it is not, and that is worth stating plainly**:
  `plan_subjects` distributes hard cases and anti-correlation quotas *across* the run, so
  generating 50 subjects does not reproduce the first 5 of a 5-subject run. `extend_corpus`
  exists for this: it holds the existing items fixed and appends new ones, rather than
  re-deriving the whole corpus at a new `n`.

The record shape is v0's, minus `tier` and plus what a dated, variable-length timeline needs
(`turns`, `date`, `removable`, `session_count`, `pace`). Keeping the rest identical is
deliberate: `author.py` and the readers already parse it.
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import fields, generator, schedule, transcript
from .evidence import Grade

DEFAULT_SEED: int = 7

#: Timeline lengths, drawn per subject. **Most conversations histories are short**; the long
#: tail is what makes "eventually everything is revealed" demonstrable at all. Weighted rather
#: than uniform so a 50-subject corpus is mostly cheap and still carries a few full-reveal
#: subjects. `beats_to_pin_all` is the cost that sets the top of this range.
LENGTH_CHOICES: tuple[int, ...] = (3, 4, 5, 6, 6, 7, 8, 8, 9, 10, 12, 14, 16, 20, 25, 30)

#: Held back as a private split. 0 for the pilot — a 5-subject corpus cannot carry a
#: meaningful holdout, and inventing one would only make the pilot look like a release.
PRIVATE_SHARE: float = 0.0

CONSTRAINT_IDS = frozenset(f.field_id for f in fields.HARD_CONSTRAINTS)


# --- the corpus record -------------------------------------------------------


@dataclass(frozen=True)
class SessionTask:
    """One authoring call: session `session` of `subject_id`, with 0..session-1 in context."""

    task_id: str
    subject_id: str
    session: int
    checkpoint: int
    is_distractor: bool
    #: Target conversation length, drawn per session. v0 hardcoded "8-16 turns" for every
    #: session in the authoring prompt.
    turns: int
    #: Calendar date. A twin is a point-in-time object and the gaps are themselves signal.
    date: str
    #: True when an "easy" lane may drop this session — distractor and interference only.
    #: Never a correction: see `schedule.EvidenceSchedule.removable_sessions`.
    removable: bool
    beats: tuple[Mapping[str, Any], ...] = ()
    #: What this session must keep true without evidencing it — see `transcript.continuity_for`.
    #: Derived from the card and from sessions strictly before this one, so it carries the
    #: prefix-stability property the beats have and `extend_corpus` can keep checking one thing.
    continuity: Mapping[str, Any] = dc_field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "subject_id": self.subject_id,
            "session": self.session, "checkpoint": self.checkpoint,
            "is_distractor": self.is_distractor, "turns": self.turns, "date": self.date,
            "removable": self.removable, "beats": [dict(b) for b in self.beats],
            "continuity": dict(self.continuity),
        }


@dataclass(frozen=True)
class Item:
    """One subject, as every later stage reads it."""

    subject_id: str
    split: str
    stratum: str
    sociality: str
    deliberateness: str
    session_count: int
    pace: float
    sessions: tuple[SessionTask, ...]
    card: Mapping[str, Any]
    card_at: Mapping[int, Mapping[str, Any]]
    provisional: Mapping[int, Mapping[str, str]]
    must_strong: Mapping[int, Sequence[str]]
    must_unevidenced: tuple[str, ...]
    distractor_checkpoints: tuple[int, ...]
    removable_sessions: tuple[int, ...]
    corrections: tuple[Mapping[str, Any], ...]
    #: The companion's constraint, and the published statement that it is *not* the subject's.
    #: A consumer that wants to score the trap needs the field, the value and the checkpoint;
    #: the card deliberately does not carry any of the three.
    third_party: tuple[Mapping[str, Any], ...]
    #: Distribution bins the conversations instantiate as scenery. Counted by the map — see
    #: ADR-0037 — so a consumer reproducing the answer key needs them.
    incidentals: tuple[Mapping[str, Any], ...]
    self_report_fields: tuple[str, ...]
    constraint_beats: tuple[Mapping[str, Any], ...]
    anti_correlated: tuple[str, ...]
    deliberate_anti_correlated: tuple[str, ...]
    hard_case_tags: tuple[str, ...]
    directives: tuple[str, ...]
    #: Beats this card needs to be revealed completely, against beats actually placed. The
    #: honest form of "eventually everything is revealed": a number, per subject, that a
    #: length either pays or does not.
    beats_to_pin_all: int
    beats_placed: int
    attempts: int = 1
    idiosyncrasies: str = ""

    @property
    def checkpoints(self) -> tuple[int, ...]:
        return tuple(sorted({task.checkpoint for task in self.sessions}))

    def to_json(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id, "split": self.split,
            "stratum": self.stratum, "sociality": self.sociality,
            "deliberateness": self.deliberateness,
            "session_count": self.session_count, "pace": self.pace,
            "sessions": [task.to_json() for task in self.sessions],
            "card": dict(self.card),
            "idiosyncrasies": self.idiosyncrasies,
            "card_at": {str(k): dict(v) for k, v in sorted(self.card_at.items())},
            "provisional": {str(k): dict(v) for k, v in sorted(self.provisional.items())},
            "must_strong": {str(k): list(v) for k, v in sorted(self.must_strong.items())},
            "must_unevidenced": list(self.must_unevidenced),
            "distractor_checkpoints": list(self.distractor_checkpoints),
            "removable_sessions": list(self.removable_sessions),
            "corrections": [dict(c) for c in self.corrections],
            "third_party": [dict(t) for t in self.third_party],
            "incidentals": [dict(i) for i in self.incidentals],
            "self_report_fields": list(self.self_report_fields),
            "constraint_beats": [dict(b) for b in self.constraint_beats],
            "anti_correlated": list(self.anti_correlated),
            "deliberate_anti_correlated": list(self.deliberate_anti_correlated),
            "hard_case_tags": list(self.hard_case_tags),
            "directives": list(self.directives),
            "beats_to_pin_all": self.beats_to_pin_all,
            "beats_placed": self.beats_placed,
            "attempts": self.attempts,
        }


@dataclass
class Dataset:
    """The corpus, minus the transcripts: what to author and what to score it against."""

    items: list[Item]
    seed: int
    strata: list[dict[str, Any]] = dc_field(default_factory=list)
    quarantine: list[tuple[str, tuple[str, ...]]] = dc_field(default_factory=list)
    #: `EvidenceSchedule.check` for every subject, collected at build time. Published rather
    #: than raised: a schedule that cannot place an event on a short timeline is a fact about
    #: that length, not a build failure.
    schedule_problems: list[str] = dc_field(default_factory=list)

    @property
    def public(self) -> list[Item]:
        return [item for item in self.items if item.split == "public"]

    @property
    def private(self) -> list[Item]:
        return [item for item in self.items if item.split == "private"]

    @property
    def session_count(self) -> int:
        return sum(len(item.sessions) for item in self.items)

    @property
    def checkpoint_count(self) -> int:
        return sum(len(item.checkpoints) for item in self.items)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": "v0.1",
            "seed": self.seed,
            "private_share": PRIVATE_SHARE,
            "strata": self.strata,
            "quarantine": [[sid, list(reasons)] for sid, reasons in self.quarantine],
            "items": [item.to_json() for item in self.items],
        }


def _card_values(card: Any) -> dict[str, Any]:
    return {fid: card.values[fid] for fid in fields.ALL_FIELDS_IDS if fid in card.values}


def _rewind(card: Any, sched: Any, checkpoint: int) -> Any:
    """The card as of `checkpoint`, with later corrections undone.

    A judge is scored against the value that was true *when the prefix ended*, so a corrected
    field graded at checkpoint 1 must not be marked wrong for naming the value the transcript
    had by then established.
    """
    values = dict(card.values)
    for event in sorted(sched.events, key=lambda e: -e.checkpoint):
        if event.kind == "correction" and event.checkpoint > checkpoint:
            values[event.field_id] = event.old_value
    return type(card)(
        subject_id=card.subject_id, values=values,
        schema_version=card.schema_version,
        idiosyncrasies=card.idiosyncrasies,
        importance_weights=card.importance_weights,
    )


# --- construction ------------------------------------------------------------


def assign_lengths(
    subject_ids: Sequence[str], *, seed: int = DEFAULT_SEED,
    lengths: Sequence[int] | None = None,
) -> dict[str, int]:
    """One timeline length per subject.

    Drawn on a **per-subject** stream, not by consuming one shared stream in subject order, so
    that adding a subject cannot change any existing subject's length. Same rule as everything
    in `schedule.py`, and for the same reason.

    ``lengths`` overrides the draw positionally — the pilot names its five lengths explicitly
    so the run exercises the short end and the full-reveal end in one go.
    """
    if lengths is not None:
        return {
            sid: lengths[index % len(lengths)] for index, sid in enumerate(subject_ids)
        }
    return {
        sid: random.Random(f"{seed}|{sid}|length").choice(LENGTH_CHOICES)
        for sid in subject_ids
    }


def build(
    *,
    n: int,
    seed: int = DEFAULT_SEED,
    lengths: Sequence[int] | None = None,
    idiosyncrasies: Mapping[str, str] | None = None,
    private_share: float = PRIVATE_SHARE,
) -> Dataset:
    """Generate the corpus. Deterministic in `(n, seed, lengths)` and free of model calls."""
    run = generator.generate(n=n, seed=seed, idiosyncrasies=dict(idiosyncrasies or {}))
    subject_ids = [s.subject_id for s in run.subjects]
    per_subject = assign_lengths(subject_ids, seed=seed, lengths=lengths)

    private: set[str] = set()
    if private_share > 0:
        held = round(len(subject_ids) * private_share)
        private = set(sorted(subject_ids)[-held:]) if held else set()

    items: list[Item] = []
    problems: list[str] = []
    quarantine = list(run.quarantine)
    for subject in run.subjects:
        try:
            sched = schedule.build_schedule(
                subject_id=subject.subject_id, card=subject.card,
                session_count=per_subject[subject.subject_id],
                directives=subject.directives, anti_correlated=subject.anti_correlated,
                hard_case_tags=subject.hard_case_tags, seed=seed,
            )
        except schedule.ScheduleError as exc:
            quarantine.append((subject.subject_id, (f"schedule: {exc}",)))
            continue
        problems += [f"{sched.subject_id}: {p}" for p in sched.check(subject.card)]
        items.append(_item(subject, sched, seed=seed, split=(
            "private" if subject.subject_id in private else "public"
        )))

    return Dataset(
        items=items, seed=seed,
        strata=[record.to_json() for record in run.records.values()],
        quarantine=quarantine, schedule_problems=problems,
    )


def _item(subject: Any, sched: Any, *, split: str,
          seed: int = DEFAULT_SEED) -> Item:
    prov_map = sched.provisional_map()
    # `must_unevidenced` is read off `prov_map` below. Only beats that can raise a grade
    # reach it: a field touched solely by a counter-beat or by a companion's constraint has no
    # record of the *subject's* value, and the pilot audit found the opposite reading would
    # quietly drop a field out of the never-evidenced list on the strength of evidence about
    # somebody else. That line is `counts_toward_gold`, and the map already draws it.
    removable = set(sched.removable_sessions())

    card_values = _card_values(subject.card)
    _, mention_once = schedule._parse_directives(sched.directives)

    def continuity_at(session: int) -> dict[str, Any]:
        # Established = the subject's *own* beats, strictly earlier. A companion's constraint
        # establishes nothing about the subject and must never reach the standing block; that
        # is the same `counts_toward_gold` line the map draws, drawn here for the same reason.
        established = sorted({
            b.field_id for b in sched.beats
            if b.session < session and b.counts_toward_gold
        })
        suppress = [mention_once] if (
            mention_once and any(
                b.field_id == mention_once and b.session < session for b in sched.beats
            )
        ) else []
        return transcript.continuity_for(
            card=card_values,
            established=established,
            this_session=[b.field_id for b in sched.beats_in(session)],
            # The guards key on the subject's **own** beats, for the same reason
            # `established` does: a companion's constraint says nothing about the subject, so
            # it must not silence the subject's own standing line. `attributed == USER` and
            # not `counts_toward_gold` — a counter-beat is still the subject speaking about
            # that field, and silencing it would leave a stated-vs-revealed session with no
            # value to be inconsistent about.
            this_session_own=[
                b.field_id for b in sched.beats_in(session)
                if b.attributed == schedule.USER
            ],
            suppress=suppress,
            # The members a narrowing set beat will not name, so the guard can cover exactly
            # them. Read off the same beats the brief is built from, never recomputed from the
            # card: the brief's subset and this guard's complement have to be one decision or
            # they drift, and a drift here is silent — the page simply loses its protection.
            withheld={
                b.field_id: transcript.withheld_members({
                    "field_id": b.field_id, "move": b.move, "attributed": b.attributed,
                    "supports_gold": b.supports_gold,
                    "gold_value": subject.card.values.get(b.field_id),
                })
                for b in sched.beats_in(session)
                if b.attributed == schedule.USER
            },
            occasions={
                i.field_id: i.bin for i in sched.incidentals if i.session == session
            },
            # Drawn from the card on its own RNG stream, so it shifts no other field's plan.
            # Not an `Incidental`: `_map_of` reads those and feeds placement, and this must
            # leave every beat where it was. See `schedule.formality_occasion`.
            formality=schedule.formality_occasion(
                subject.subject_id, session, card_values, seed=seed,
            ),
        )

    tasks = tuple(
        SessionTask(
            task_id=f"{subject.subject_id}-s{session}",
            subject_id=subject.subject_id,
            session=session,
            checkpoint=sched.checkpoint_of(session),
            is_distractor=session in sched.distractor_sessions,
            turns=sched.turns_in(session),
            date=sched.dates[session],
            removable=session in removable,
            beats=tuple({
                "field_id": b.field_id, "move": b.move,
                "supports_gold": b.supports_gold, "note": b.note, "bin": b.bin,
                "attributed": b.attributed,
                # A third-party beat's value is the companion's, and the subject's card is
                # the wrong place to look for it. Writing the card value here would hand the
                # author the subject's own constraint and ask it to put it in someone else's
                # mouth, which is the defect this construction exists to replace.
                "gold_value": (
                    b.foreign_value if b.attributed == schedule.THIRD_PARTY
                    else subject.card.values.get(b.field_id)
                ),
            } for b in sorted(sched.beats_in(session), key=lambda b: b.field_id)),
            # A distractor session carries no signal by construction, so it gets no
            # continuity block either: naming what must stay true is itself a way of putting
            # the person in the room, and §8.6 measures card movement against a session that
            # does not.
            continuity={} if session in sched.distractor_sessions else continuity_at(session),
        )
        for session in range(sched.session_count)
    )

    checkpoints = sorted({task.checkpoint for task in tasks})
    provisional = {
        cp: {
            fid: prov_map.grades[cp].get(fid, Grade.UNEVIDENCED).name
            for fid in fields.ALL_FIELDS_IDS
        }
        for cp in checkpoints
    }
    return Item(
        subject_id=subject.subject_id,
        split=split,
        stratum=subject.stratum_id,
        sociality=subject.sociality,
        deliberateness=subject.deliberateness,
        session_count=sched.session_count,
        pace=sched.pace,
        sessions=tasks,
        card=_card_values(subject.card),
        idiosyncrasies=getattr(subject.card, "idiosyncrasies", "") or "",
        card_at={cp: _card_values(_rewind(subject.card, sched, cp)) for cp in checkpoints},
        provisional=provisional,
        must_strong={
            cp: [f for f, g in provisional[cp].items() if g == Grade.STRONG.name]
            for cp in checkpoints
        },
        # Read off the map at the last checkpoint, not off the beat list. The two used to
        # agree and stopped agreeing at ADR-0037: a counted occasion evidences a distribution
        # field the beats never touched, so beat-derived `must_unevidenced` listed 8 cells the
        # corpus's own `provisional` graded WEAK or STRONG — one record contradicting the
        # other inside one file. Entailment credit widens the same gap. `calibration.py` has
        # always derived its never-evidenced set from the map; this is that line, drawn here.
        must_unevidenced=tuple(
            fid for fid in fields.ALL_FIELDS_IDS
            if provisional[checkpoints[-1]][fid] == Grade.UNEVIDENCED.name
        ),
        incidentals=tuple(i.to_json() for i in sched.incidentals),
        distractor_checkpoints=tuple(sched.distractor_checkpoints),
        removable_sessions=tuple(sorted(removable)),
        corrections=tuple({
            "field_id": e.field_id, "checkpoint": e.checkpoint,
            "old_value": e.old_value, "new_value": e.new_value,
        } for e in sched.events if e.kind == "correction"),
        third_party=tuple({
            "field_id": e.field_id, "checkpoint": e.checkpoint, "value": e.new_value,
            "card_value": subject.card.values.get(e.field_id),
        } for e in sched.events if e.kind == "third_party"),
        self_report_fields=tuple(sorted({
            b.field_id for b in sched.beats if not b.supports_gold
        })),
        constraint_beats=tuple({
            "field_id": b.field_id, "session": b.session, "move": b.move,
            "gold_value": subject.card.values.get(b.field_id),
            "only_mention": sum(1 for o in sched.beats if o.field_id == b.field_id) == 1,
        } for b in sorted(sched.beats, key=lambda b: (b.session, b.field_id))
            if b.field_id in CONSTRAINT_IDS),
        anti_correlated=tuple(subject.anti_correlated),
        deliberate_anti_correlated=tuple(subject.deliberate_anti_correlated),
        hard_case_tags=tuple(subject.hard_case_tags),
        directives=tuple(subject.directives),
        beats_to_pin_all=schedule.beats_to_pin_all(subject.card),
        beats_placed=len(sched.beats),
        attempts=subject.attempts,
    )


# --- extension ---------------------------------------------------------------


def extend_corpus(
    path: Path, *, subject_id: str, session_count: int, seed: int = DEFAULT_SEED,
    idiosyncrasies: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Lengthen one subject's timeline in place, leaving every earlier session identical.

    This is the operation the whole scheduler was shaped around, so it is worth having as a
    function rather than as a recipe. It rebuilds *only* the named subject at the new length
    and asserts the prefix came back unchanged before writing — if the invariant ever breaks,
    it breaks here, loudly, rather than silently invalidating a store of transcripts that
    cost real money to author.
    """
    payload = json.loads(path.read_text())
    items = payload["items"]
    index = next(
        (i for i, item in enumerate(items) if item["subject_id"] == subject_id), None
    )
    if index is None:
        raise KeyError(f"{subject_id} is not in {path}")
    before = items[index]
    if session_count <= before["session_count"]:
        raise ValueError(
            f"{subject_id} already has {before['session_count']} sessions; "
            "shortening is not an extension and would orphan graded checkpoints"
        )

    run = generator.generate(
        n=len(items), seed=seed, idiosyncrasies=dict(idiosyncrasies or {}),
    )
    subject = next(s for s in run.subjects if s.subject_id == subject_id)
    sched = schedule.build_schedule(
        subject_id=subject_id, card=subject.card, session_count=session_count,
        directives=subject.directives, anti_correlated=subject.anti_correlated,
        hard_case_tags=subject.hard_case_tags, seed=seed,
    )
    after = _item(subject, sched, seed=seed, split=before["split"]).to_json()

    kept = before["session_count"]
    if after["sessions"][:kept] != before["sessions"]:
        raise AssertionError(
            f"{subject_id}: extending to {session_count} sessions changed sessions 0..{kept-1}. "
            "The scheduler looked forward somewhere; every authored transcript for this "
            "subject is now invalid. Do not write this corpus."
        )

    items[index] = after
    path.write_text(json.dumps(payload, indent=2, sort_keys=False))
    return after


# --- verification ------------------------------------------------------------


def verify(data: Dataset) -> list[str]:
    """Properties of the *set*. Empty means it can be authored."""
    problems: list[str] = []
    if not data.items:
        return ["corpus is empty"]

    ids = [item.subject_id for item in data.items]
    if len(set(ids)) != len(ids):
        problems.append("duplicate subject ids")

    for item in data.items:
        sessions = [task.session for task in item.sessions]
        if sessions != list(range(len(sessions))):
            problems.append(
                f"{item.subject_id}: sessions {sessions} are not dense from 0; the author "
                "writes a prefix and a gap would be authored as though it were not there"
            )
        dates = [task.date for task in item.sessions]
        if dates != sorted(dates):
            problems.append(f"{item.subject_id}: session dates are not increasing")
        for task in item.sessions:
            if task.is_distractor and task.beats:
                problems.append(
                    f"{task.task_id}: distractor session carries {len(task.beats)} beats"
                )
    return problems


def summary(data: Dataset) -> str:
    lines = [
        f"subjects            {len(data.items)}",
        f"sessions            {data.session_count}",
        f"checkpoints         {data.checkpoint_count}",
        "",
        f"{'subject':10s} {'len':>4s} {'pace':>5s} {'beats':>6s} {'/full':>6s} "
        f"{'turns':>10s}  {'STRONG':>7s} {'WEAK':>6s} {'UNEV':>6s}  events",
    ]
    for item in data.items:
        final = item.provisional[max(item.provisional)]
        counts = {g.name: sum(1 for v in final.values() if v == g.name) for g in Grade}
        turns = [t.turns for t in item.sessions]
        events = []
        if item.corrections:
            events.append(f"{len(item.corrections)}x correction")
        if item.distractor_checkpoints:
            events.append(f"{len(item.distractor_checkpoints)}x distractor")
        removable_only = set(item.removable_sessions) - set(item.distractor_checkpoints)
        if removable_only:
            events.append("interference")
        lines.append(
            f"{item.subject_id:10s} {item.session_count:4d} {item.pace:5.2f} "
            f"{item.beats_placed:6d} {item.beats_to_pin_all:6d} "
            f"{min(turns):3d}-{max(turns):<6d} "
            f"{counts['STRONG']:7d} {counts['WEAK']:6d} {counts['UNEVIDENCED']:6d}  "
            + ", ".join(events)
        )

    if data.quarantine:
        lines += ["", "quarantined:"]
        lines += [f"  {sid}: {'; '.join(reasons)}" for sid, reasons in data.quarantine]
    if data.schedule_problems:
        lines += ["", f"schedule notes ({len(data.schedule_problems)}):"]
        lines += [f"  {p}" for p in data.schedule_problems]
    return "\n".join(lines)


def main() -> None:  # pragma: no cover - CLI
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-n", type=int, default=5, help="number of subjects")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--lengths", type=int, nargs="+", default=None,
                        help="explicit timeline lengths, assigned by subject index")
    parser.add_argument("--idiosyncrasies", type=Path, default=None)
    parser.add_argument("--json", type=Path, default=None, help="write the corpus here")
    parser.add_argument("--extend", nargs=2, metavar=("SUBJECT", "SESSIONS"),
                        help="lengthen one subject in an existing --json corpus, in place")
    args = parser.parse_args()

    idiosyncrasies: dict[str, str] = {}
    if args.idiosyncrasies:
        idiosyncrasies = json.loads(args.idiosyncrasies.read_text())

    if args.extend:
        subject_id, count = args.extend[0], int(args.extend[1])
        item = extend_corpus(
            args.json, subject_id=subject_id, session_count=count, seed=args.seed,
            idiosyncrasies=idiosyncrasies,
        )
        print(f"{subject_id} extended to {item['session_count']} sessions; "
              f"prefix verified unchanged")
        return

    data = build(
        n=args.n, seed=args.seed, lengths=args.lengths, idiosyncrasies=idiosyncrasies,
    )
    print(summary(data))
    for problem in verify(data):
        print(f"VERIFY: {problem}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(data.to_json(), indent=2))
        print(f"\nwrote {args.json}")


if __name__ == "__main__":  # pragma: no cover
    main()
