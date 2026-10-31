"""The pre-freeze editor: turn-level repair driven by the reader's own findings.

`verify.py` is deliberately read-only — ADR-0009/0010 measured that letting a judge's output
write back into the record it graded biases the pool, so the audit demotes and nothing there
ever patches a transcript. That rule protects *scoring*. It says nothing about *authoring*,
and until now there was no way to fix a transcript the reader flags as broken except a full
re-authoring of the session (`runs/reauthor_gen001_s1.py`) or hand-editing.

This module is scoped to what a `Finding` can actually point at: a locatable, `BLOCKING`
finding names one or more **messages** (`Finding.messages`, the `[n]` numbers the author
emitted and the reader was shown) that are wrong as written. `undelivered` and
`repeat_not_delivered` findings name none — the beat is simply missing, and fixing that means
inserting a message, not replacing one — so they are out of scope here and stay
`locatable=False`. Both are left for a human.

**A repair spans as many messages as the defect does (v0.4).** The pilot's editor rewrote
exactly one turn, and the two defects it could not fix were both multi-message by nature: a
bot asking an unbriefed intake question and the user answering it (`gen-001-s1`, where no
possible answer is both true to the card and permitted by the map, so only removing the
*question* resolves it), and a rejection contradicted by an acceptance four messages later
(`gen-003-s8`). Rewriting either half alone cannot converge, and three attempts each proved
it. See `runs/probe-edit-failures.json`.

The gate is the same shape §7.1's own content-retry loop already uses
(`author.author_session`'s `budget`, re-briefed on failure) and the same shape
`generator.py`'s card redraw uses (draw, verdict, retry, bounded): rewrite the named
messages, splice them in mechanically (`transcript.splice_turns` — the model is never asked
to reproduce anything else, and never touches a number or a speaker label), run the result
through `transcript.check_session` and a scoped re-audit (`verify.audit_session` +
`verify.reconcile`, called here as a tool to accept or reject a candidate, not as the record
a score is based on), and write only a candidate that clears both. A session that never
clears in `MAX_ATTEMPTS` is left untouched.

**A repair is judged against what the session already carried, not against zero** — see
`_regressions`. Requiring a patched session to be free of *every* blocking finding made
multi-defect sessions unrepairable in principle, which is what most of the pilot's editor
failures actually were.

**And it is judged on every field except the one being repaired.** A repair rarely removes a
field from the page; it changes what the page says, and the reader re-files the same field
under a different `kind`. Gating on `(field, kind)` scored that as damage and threw away
repairs whose target defect the gate itself had just confirmed was gone — 5 of the pilot's 20
unresolved repairs, `gen-002-s9`/`dietary` three times over. What the field is left carrying
is recorded on the result (`EditResult.traded`) rather than gated on, and settled by the
independent post-run audit.

**A repair may also leave an absolvable defect on a field it was not repairing, up to
`MAX_COLLATERAL` of them** — the same argument one field over, and the measurement is in that
constant's note: of the 25 regression rejections among the v0.5 n=15 run's 26 unresolved
repairs, 12 named nothing but leaks, and 8 findings — 4 of them contradictions — had an
attempt turned down for nothing else. What it costs is recorded on `EditResult.collateral`,
because the independent audit reads a leak with no memory of who wrote it. A candidate that
clears every gate and exceeds the cap is not thrown away: it goes to `EditResult.suggested`
with its price, for a person to apply or refuse.

**With one exception, and it is directional: a repair may not push its own field OUT of
`credit`'s reach** — see `_downgrades` and ADR-0056. Trading a contradiction for a leak gives
the defect a second route out and is the trade the rule above was relaxed for; trading a leak
for a contradiction takes that route away, and the post-run audit cannot arbitrate it because
it sees the contradiction with no memory that a leak used to be there.

**This is not the boundary ADR-0009/0010 draws.** That boundary is between authoring and
scoring. The frozen `Report` a score is computed from still has to come from an independent,
full `verify.audit`/`inspect` run over the corpus *after* this script, exactly as it would for
a session nobody ever touched — this module's internal re-audit calls decide whether to accept
a patch, and are discarded once they have.

**And a repair that arrives where it started is not written at all.** `TRANSITIONS` is the
whole rule in one line — `leak -> none`, `contradiction -> leak`, `contradiction -> none` — and
a leak swapped for a leak is none of them: `credit.absolve` settles either one on identical
terms, so the page has been rewritten to buy nothing. `_no_progress` catches both shapes of it
(the reader re-filing the same field, and the repair minting a fresh leak elsewhere), leaves
the page alone, lets the remaining attempts run in case one clears the leak outright, and ends
`neutral` rather than `unresolved` if none does.
"""

from __future__ import annotations

import re

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field as dc_field
from threading import Lock
from typing import Any, Callable, Mapping, MutableMapping, Sequence

from . import credit, fields, schedule, transcript
from .judge import Invoker, TransportError
from .verify import BLOCKING, DEFAULT_MODEL, Finding, audit_session, reconcile
from dataclasses import replace as dc_replace

#: Same bound as `author.RETRY_BUDGET` + 1 and `generator.py`'s card redraw: bounded and
#: published, never silently unbounded.
MAX_ATTEMPTS: int = 3

#: The only transitions a repair is allowed to make, and the one it is allowed to be.
#:
#:     leak         -> nothing      accepted
#:     contradiction -> nothing     accepted
#:     contradiction -> leak        accepted, and bounded by `MAX_COLLATERAL` off-field
#:     leak         -> leak         NULL MOVE — `_no_progress`, never written
#:     leak         -> contradiction  refused by `_downgrades`
#:     anything     -> contradiction on another field  refused by `_regressions`
#:
#: Read down the first column and the rule is one sentence: a repair must move the page
#: toward `credit`'s lane or out of the blocking set, and may never move it the other way. A
#: leak swapped for a leak does neither — `credit.absolve` settles either one on the same
#: terms, so the count that matters is identical before and after — and rewriting a frozen
#: transcript to arrive where it started is a change to the dataset with nothing bought.
TRANSITIONS = "leak -> none, contradiction -> leak, contradiction -> none"

#: Gates that mean the re-audit ran and judged the candidate, as against the ones that mean
#: it never got that far (`reply`, `too_short`, `check_session`, `beats_lost`, `audit`). Only
#: a judged attempt can say a repair is futile rather than merely unlucky.
VERDICT_GATES = frozenset({
    "still_blocking", "regression", "downgrade", "collateral", "no_progress",
})

#: How many `credit`-absolvable findings a repair may leave on fields it was NOT repairing.
#:
#: `_regressions` used to refuse every new blocking finding on another field, leak included,
#: and that refusal is what most of the v0.5 n=15 editor failures actually were: of the 25
#: regression rejections among 26 unresolved repairs, **12 named nothing but leaks**, and 8
#: findings — 4 of them contradictions, including all three `price_band_distribution` cases —
#: had at least one attempt turned down solely for putting a leak on some other field.
#:
#: The argument is the same one `_downgrades` makes, applied one field over. A leak is what
#: `credit.absolve` exists to settle; a contradiction is out of `credit`'s lane by ADR-0046
#: and can only be fixed here. Refusing to mint a leak in order to clear a contradiction
#: keeps the *expensive* defect and saves the cheap one, which is the trade backwards.
#:
#: It is a cap and not a licence, because the trade stops being progress when it is bulk:
#: `gen-013-s0`'s admissible attempt cleared one contradiction and would have minted **four**
#: leaks across four fields. `edit_session` returns on the first attempt that passes, so it
#: takes the earliest candidate rather than the cheapest — nothing in the loop would have
#: preferred a one-leak attempt over that one. At 1, a repair may trade a defect for a defect
#: and may not spray.
#:
#: A candidate that exceeds it is not silently dropped: it is written to `EditResult.suggested`
#: with the leaks it would cost, for a person to apply or refuse. See `edit_session`.
#:
#: **Only a repair of a defect `credit` cannot settle can spend this budget.** For a `leak`
#: repair, minting a leak elsewhere is not a trade at all — it is the null move above, and
#: `_no_progress` stops it before the cap is consulted. The budget exists to buy a
#: contradiction's way into `credit`'s lane, and there is nothing to buy when the defect is
#: already in it.
MAX_COLLATERAL: int = 1


EDITOR_SYSTEM = """\
You are repairing ONE turn of an already-written conversation from a research dataset. \
Every other turn is fixed and will not change — you are shown neighboring turns only for \
context, not to be reproduced.

Rules the rest of the dataset already follows, and your replacement must too:

1. THE BOT IS A PROP. Generic and only mildly helpful, if this is a bot turn.
2. THE USER NEVER RECITES A PREFERENCE AS A CATEGORY. No trait vocabulary, no scale \
language, no self-summaries, if this is a user turn.
3. THE USER NEVER GENERALIZES ABOUT THEMSELVES. Banned outright: "I always", "I never", \
"I usually", "I tend to", "as a rule", "that's just how I am", "I'm the kind of person who", \
and their like — if this is a user turn.
3b. AND NEVER RATES TODAY AGAINST THEIR OWN NORM. "same as usual", "the usual", "more than \
we'd normally spend", "nicer than normal", "a rare treat", "the sort of place we always end \
up at" — these are rule 3 wearing a different coat, and on a distribution field they are the \
only thing that can contradict the card. Today's choice is just today's choice: name it and \
stop there.
4. Match the register and length of the messages around it. This is a patch, not a rewrite: \
it should read as though it was always there.
5. REWRITE ONLY THE MESSAGES YOU ARE ASKED FOR, and return every one of them. Give the \
replacement text alone — no `[n]`, no `USER:`/`BOT:` label. Those are put back for you.
6. YOU MAY DELETE A MESSAGE INSTEAD OF REWRITING IT, by giving it as null. Do that when the \
defect is that the message should not be there at all — a question nobody should have asked \
is not fixed by asking it differently. Deleting renumbers everything after it, which is \
handled for you.
7. IF YOU DELETE, KEEP THE SPEAKERS ALTERNATING. The conversation strictly alternates \
USER / BOT. Deleting a bot's question but leaving the user's answer to it puts two user \
messages side by side and the result will be rejected — so delete the exchange, both \
messages, not one half of it.

8. PREFER TO CUT RATHER THAN TO INVENT. You are shown the beats this session exists to \
carry. Material that carries no beat and is the reason for the defect should be REMOVED, \
not replaced with new material saying something else. Every sentence you add is a fresh \
chance to contradict a part of the card you were not thinking about. Cut to the shortest \
turn that still reads naturally and still carries every beat listed.
8b. THE ANECDOTE IS USUALLY THE DEFECT, AND CUTTING IT IS USUALLY THE REPAIR. The \
reminiscence, the aside, the reason offered for a choice, the detail about the venue or \
the last time they went — this is set-dressing, it carries no beat, and it is what makes a \
field readable that this session was not meant to evidence. Delete the clause or the \
sentence and leave the turn to do its job. Do not swap one anecdote for another: replacing \
"I've had the salmon bowl there a few times" with a different remembered meal repairs \
nothing. If cutting the anecdote empties the message of everything it was there for, then \
and only then write a plain replacement that carries the beat and nothing else.
9. IF YOU MUST WRITE, BE CONSISTENT WITH THE WHOLE CARD — NOT ONLY THE FIELD AT ISSUE. \
You are shown the person's entire card below. A repair that fixes the named field and \
contradicts a different one is rejected and wasted; the constraint fields (dietary, \
allergies, accessibility_needs, alcohol_free_required) and travel_radius are where this \
goes wrong most often. Say nothing the card does not support, on any field.

Output JSON only, no prose around it: \
{"messages": {"6": "replacement for message 6", "10": null}}
where a string replaces that message and null deletes it."""


def _rewrite_prompt(
    *, page: str, targets: Sequence[tuple[int, str, str]], field_id: str, card_value: Any,
    move: str, attributed: str, defect: str,
    card: Mapping[str, Any] | None = None,
    beats: Sequence[Mapping[str, Any]] = (),
) -> str:
    """What the editor is shown: the whole numbered page, then the messages to replace.

    **And the whole card, plus this session's beats.** The pilot's editor was shown one
    field — the one being repaired — and was therefore rewriting blind against every other
    field on the card. Both of the v0.5 5x5 run's unresolved repairs failed that way and
    only that way: `gen-000-s3` was rejected three times for walking the person across town
    (`travel_radius`), `gen-001-s1` three times for burying a shellfish allergy, and neither
    field was in the prompt. The gate could see the whole card; the editor could not, which
    made those attempts unwinnable rather than merely hard. `fields.card_block`'s own
    comment already claimed the editor used it — this is the code catching up to that.

    The beats are shown for the opposite reason: to say what may be cut. A repair that
    deletes material carrying no beat is strictly safer than one that invents replacement
    prose, and the editor cannot prefer that if it does not know which material is load-
    bearing. `beats_survive` gates deletion after the fact; this tells the editor before.

    The whole page rather than one turn plus its neighbours. A multi-message defect is a
    relationship *between* messages — a question and the answer it forces, a rejection and
    the acceptance that contradicts it — and a window of three cannot show one that spans
    four. It is also what makes the numbers usable: the editor is asked for message 10 and
    can see message 10.
    """
    whose = (
        "a companion's, being relayed by the user"
        if attributed == schedule.THIRD_PARTY else "the user's own"
    )
    numbers = ", ".join(str(n) for n, _sp, _t in targets)
    lines: list[str] = []
    if card:
        lines += ["=== WHO THIS PERSON IS (the card the whole session was written from) ==="]
        lines += fields.card_block(card)
        lines += [""]
    if beats:
        lines += [
            "=== WHAT THIS SESSION MUST STILL CARRY (its beats) ===",
            "Every one of these has to survive your edit. Anything on the page that is NOT "
            "one of these is free to cut.",
        ]
        for beat in beats:
            bid = str(beat.get("field_id", ""))
            bits = [f"  {bid}"]
            if beat.get("move"):
                bits.append(f"via a {beat['move']!r} move")
            if beat.get("bin"):
                bits.append(f"instantiating bin {beat['bin']!r}")
            bits.append(f"attributed to the {beat.get('attributed', schedule.USER)}")
            lines.append(", ".join(bits))
            if beat.get("note"):
                lines.append(f"      {beat['note']}")
        lines += [""]
    lines += [
        "=== THE CONVERSATION (every message numbered) ===",
        page,
        "",
        f"=== WHAT IS WRONG: {defect}",
        "",
    ]
    if field_id:
        lines += [
            f"The field at issue is `{field_id}` = {card_value!r} ({whose})"
            + (f", via a {move!r} move ({transcript.MOVE_DEFINITIONS.get(move, move)})"
               if move else "")
            + ".",
        ]
        # The same gloss the reader now sees (`fields.card_block`). Without it the editor
        # is rewriting against a field name, and the reader is judging the result against
        # a definition — two different targets, and the pilot missed on exactly that gap.
        lines += [f"    {line}" for line in fields.gloss(field_id).splitlines()]
        if fields.BY_ID[field_id].metric == fields.METRIC_JSD:
            lines.append(f"    {fields.DISTRIBUTION_READING_RULE}")
        lines.append("")
    lines.append(f"=== REWRITE THESE MESSAGES: {numbers}")
    for number, speaker, text in targets:
        lines += ["", f"[{number}] {speaker}, currently: {text!r}"]
    lines += [
        "",
        f"Return replacements for all of: {numbers}. Every other message stays exactly as "
        "it is, so your replacements have to fit what surrounds them — including each "
        "other, if the defect is that they disagree.",
    ]
    return "\n".join(lines)


def _json_object(raw: str) -> dict[str, Any]:
    """The outermost JSON object in a reply.

    Same balanced-brace scan as `author._json_object`, copied rather than imported — it is
    eight lines, and `author.py` makes the same call for the same reason.
    """
    depth, start, best = 0, None, None
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
    return json.loads(best)


def _parse_edit_reply(raw: str, expected: Sequence[int]) -> dict[int, str | None]:
    """`{message number: replacement text}` from one editor reply, validated against
    `expected`.

    Strict in both directions, and both directions have bitten this project. A reply missing
    one of the messages would splice a half-repair and then be judged as though the whole one
    had been tried. A reply carrying an *extra* message would rewrite text nobody found a
    defect in — the editor quietly widening its own remit — which is the failure ADR-0009/0010
    draws its boundary around.
    """
    parsed = _json_object(raw)
    body = parsed.get("messages")
    if not isinstance(body, Mapping):
        # One target: accept the older single-turn shape too, so a model that answers
        # `{"turn": "..."}` is not thrown away over formatting.
        text = str(parsed.get("turn") or "").strip()
        if text and len(expected) == 1:
            return {int(expected[0]): text}
        raise ValueError("reply carries no 'messages' object")
    out: dict[int, str | None] = {}
    for key, value in body.items():
        try:
            number = int(str(key).strip().strip("[]"))
        except (TypeError, ValueError):
            raise ValueError(f"message key {key!r} is not a number") from None
        if value is None:
            out[number] = None                    # an explicit delete
            continue
        text = str(value).strip()
        if not text:
            # Distinguished from `null` on purpose: an empty string is a model that meant to
            # write something and produced nothing, which is a failed attempt. Deleting is a
            # decision and has to be spelled `null`.
            raise ValueError(f"message {number} came back empty; use null to delete it")
        out[number] = text
    if set(out) != set(expected):
        raise ValueError(
            f"reply rewrites {sorted(out)}, asked for {sorted(expected)}"
        )
    return out


@dataclass(frozen=True)
class EditResult:
    """What happened trying to fix one finding. Written whether or not it succeeded — a
    silent skip is indistinguishable from "nothing was wrong", and only one of those is true.
    """

    task_id: str
    field_id: str
    kind: str
    turn_index: int | None
    speaker: str
    original: str
    proposed: str | None
    attempts: int
    #: "fixed" | "deleted" | "neutral" | "unresolved" | "unlocatable"
    #:
    #: `neutral` is not a weaker `unresolved`, it is a different instruction. An unresolved
    #: repair is one to try again — a better draw, a wider message set, a person. A neutral
    #: one is a finding an editor cannot win: every candidate that reached a verdict swapped a
    #: leak for a leak (`_no_progress`), the blocking count was identical before and after,
    #: and nothing another attempt writes will change that. What moves it is `credit.absolve`
    #: or a correction to the map, neither of which is this module's to make.
    outcome: str
    #: Which messages this repair targeted.
    messages: tuple[int, ...] = ()
    #: One entry per rejected attempt: what was written and which gate turned it down.
    #:
    #: The pilot's editor recorded `proposed: null` on all 16 of its failures and dropped
    #: every candidate, so the artifact could not distinguish "the editor wrote bad turns"
    #: from "the gate threw away good ones" — and the answer turned out to be the second in
    #: 14 of 16 cases. Diagnosing that needed a separate instrumented re-run (a one-off probe,
    #: not released) which should never have been necessary.
    rejected: tuple[Mapping[str, Any], ...] = ()
    #: Message numbers this repair removed outright, in the page's numbering *before* the
    #: repair.
    deleted: tuple[int, ...] = ()
    #: `{old number: new number}` for every message a deletion moved, `None` where the
    #: message went away. Empty when nothing was deleted, since a replacement renumbers
    #: nothing. Callers holding other findings against this session must follow it — see
    #: `remap_finding`.
    remap: Mapping[int, int | None] = dc_field(default_factory=dict)
    #: What an accepted repair left on the field it was repairing: blocking findings the
    #: re-audit raised on the target field, under some other `kind`, that were not there
    #: before. `_regressions` no longer refuses these — see its docstring — so this is the
    #: only place the trade is written down. Usually one entry, usually a `leak` where a
    #: `contradiction` used to be, which is the trade the change was made for. A repair with
    #: an entry here is `fixed` and is also a thing to look at.
    #:
    #: Since ADR-0056 this can only hold trades that stay inside `credit.ABSOLVABLE_KINDS`'s
    #: reach — a leak where a contradiction was, never the reverse. The reverse is refused by
    #: `_downgrades` and shows up in `rejected` under the `downgrade` gate instead.
    traded: tuple[Mapping[str, Any], ...] = ()
    #: What an accepted repair left on OTHER fields: `credit`-absolvable blocking findings the
    #: re-audit raised on fields this repair was not repairing, that the page did not already
    #: carry. Bounded by `MAX_COLLATERAL`, and recorded whether or not anyone reads it — the
    #: independent post-edit audit cannot tell an editor-minted leak from an author-written
    #: one, so this file is the only place that distinction exists. See `_collateral`.
    collateral: tuple[Mapping[str, Any], ...] = ()
    #: Candidates refused ONLY for exceeding `MAX_COLLATERAL`, kept for a person to decide on.
    #:
    #: Every other gate says the candidate is wrong. This one says it is expensive: the defect
    #: it was aimed at is gone and nothing it introduced is out of `credit`'s reach, but it
    #: costs more than one leak to get there. That is a judgement about how much of the answer
    #: key one wants to send to a credit pass, which is an owner's call and not a rule's — so
    #: the candidate is written down with its price instead of being thrown away. `gen-013-s0`
    #: is the case: one contradiction cleared, four leaks minted.
    #:
    #: Each entry carries `attempt`, `proposed` (the same `{number: text}` shape `rejected`
    #: uses, so applying one is `transcript.apply_message_edits`), and `collateral`.
    suggested: tuple[Mapping[str, Any], ...] = ()

    #: Both accepted outcomes count. `deleted` is kept distinct from `fixed` rather than
    #: folded into it because the two are not the same claim about the page: a rewrite says
    #: the message was wrong, a deletion says it should not have been there. Counting them
    #: together would hide which defects are only ever solved by removal, which is the thing
    #: the marking was added to find out.
    ACCEPTED = ("fixed", "deleted")

    @property
    def verified(self) -> bool:
        return self.outcome in self.ACCEPTED

    def to_json(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "field_id": self.field_id, "kind": self.kind,
            "turn_index": self.turn_index, "speaker": self.speaker,
            "messages": list(self.messages),
            "original": self.original, "proposed": self.proposed,
            "attempts": self.attempts, "outcome": self.outcome, "verified": self.verified,
            "rejected": [dict(entry) for entry in self.rejected],
            "deleted": list(self.deleted),
            "remap": {str(k): v for k, v in self.remap.items()},
            "traded": [dict(entry) for entry in self.traded],
            "collateral": [dict(entry) for entry in self.collateral],
            "suggested": [dict(entry) for entry in self.suggested],
        }


def _as_entry(finding: Finding) -> dict[str, Any]:
    """One finding as it goes into `traded`, `collateral` or a `suggested` candidate."""
    return {"kind": finding.kind, "field_id": finding.field_id,
            "detail": finding.detail, "quote": finding.quote}


def _shown(proposed: Mapping[int, str | None]) -> dict[int, str]:
    """A rejected candidate as it goes into the record — `None` rendered as `<deleted>` so
    the artifact says what was attempted rather than dropping the fact that it was a
    deletion."""
    return {n: ("<deleted>" if text is None else text) for n, text in proposed.items()}


def beats_survive(
    original: str, candidate: str, beats: Sequence[Mapping[str, Any]]
) -> list[str]:
    """Beat facts the original page carried and the candidate no longer does.

    **The gate deletion needs and a rewrite does not.** A rewrite replaces a message, so the
    page's length and its scheduled content are the editor's to preserve and `check_session`
    already checks the shape. A deletion REMOVES content, and if what it removes is the only
    place a scheduled beat was delivered, the map now claims evidence the page does not carry:
    `undelivered`, which is a constructibility violation and the one criterion that still
    blocks outright (v0 ADR-0030). Nothing else in the pipeline would catch it — `check_session`
    reads beats for disclosure, third-party, intake and screening problems, and never for
    whether a beat was delivered at all.

    Deliberately crude and deliberately free: for each beat, the gold value's own words are
    looked for in the page before and after. A beat whose words were never findable in the
    original is not counted against the candidate — that is the author's business, already
    audited elsewhere, and failing a deletion for a defect it did not cause would make this
    gate refuse repairs for reasons unrelated to them. Only a fact that was THERE and is now
    GONE fails. False negatives are possible and acceptable; the reader arm re-audits the
    candidate afterwards regardless, and this runs first only because it costs nothing.
    """
    lost: list[str] = []
    before, after = original.lower(), candidate.lower()
    for beat in beats or ():
        value = beat.get("gold_value")
        words = [
            word for word in re.split(r"[^a-z0-9]+", str(value).lower()) if len(word) > 3
        ]
        if not words:
            continue
        was = [w for w in words if _has_word(before, w)]
        if not was:
            continue                       # never findable to begin with; not this edit's doing
        if not any(_has_word(after, w) for w in was):
            lost.append(f"{beat.get('field_id')}={value}")
    return lost


def _has_word(page: str, word: str) -> bool:
    """`word` present in `page` as a WORD, not as a substring.

    Substring matching was the bug. `gen-013-s24` carried the gold token *heat* — from
    `spice_tolerance=avoids_heat` — nowhere except inside the word **wheat**, in the very
    clause a repair was removing. `beats_survive` therefore reported the beat lost while the
    reader read it `delivered`/`pinned` off an untouched turn two messages earlier ("nothing
    with chilli in it — I don't want anything that comes hot"). The docstring above accepts
    false NEGATIVES on purpose; a false POSITIVE is different in kind, because in the deletion
    lane it refuses a repair that was fine. Stage 19 hit it by hand.
    """
    return re.search(rf"\b{re.escape(word)}\b", page) is not None


#: Run-state the AUTHOR wrote about the AUTHOR'S text. A repair replaces that text, so every
#: one of these describes a page that no longer exists and must not be carried forward.
_AUTHOR_RUN_STATE: dict[str, object] = {
    "exhausted": False, "error": None, "problems": [], "problem_kinds": [],
}


def _repaired(record: Mapping[str, Any], **changes: Any) -> dict[str, Any]:
    """`record` with a repaired transcript AND the author's run-state cleared.

    **The bug this fixes shipped for four passes.** `edit_session` used to return
    `{**record, "transcript": ...}`, which keeps `exhausted`, `error`, `problems` and
    `problem_kinds` — all of them statements about the text the repair just replaced. Stage 19
    found `gen-007-s9` still filed `exhausted` and still carrying a `speaker_repeats` problem
    on messages [3]/[4] that an earlier pass had already fixed: the stored page passes
    `transcript.check_session` with **0 problems**, and the flag was the only blocking finding
    left on it. `verify._run_state` reads `error` and `exhausted` straight off the record, so a
    stale flag is a permanent blocking finding no rewrite can reach.

    It is worse than a stale report, because `reconcile` runs `_run_state` inside the editor's
    OWN gate: a page carrying `exhausted: True` shows the gate a blocking finding present in
    every candidate, so `_regressions`/`_no_progress` would refuse every repair that page could
    ever be offered. Nothing had hit that yet only because the one exhausted session had no
    other blocking finding queued against it.

    `attempts` is deliberately KEPT. It is the authoring history — what was tried and why it
    was rejected — and it stays true whatever happens to the text afterwards. What is cleared
    is only the four fields that assert something about the CURRENT page.
    """
    return {**record, **_AUTHOR_RUN_STATE, **changes}


def edit_session(
    invoke: Invoker, model: str, record: Mapping[str, Any], item: Mapping[str, Any],
    finding: Finding, *, verify_invoke: Invoker | None = None,
    verify_model: str = DEFAULT_MODEL, max_attempts: int = MAX_ATTEMPTS,
    baseline: Sequence[Finding] = (),
) -> tuple[EditResult, dict[str, Any] | None]:
    """Try to fix one blocking finding, across every message it names.

    Returns `(result, patched_record)` — `patched_record` is `None` unless a candidate
    cleared every gate.

    Every attempt rewrites from the *original* transcript, never from a previous attempt's
    rejected candidate — the same "fresh draw, not an iterated one" shape `generator.py`'s
    card redraw uses, so a bad candidate cannot compound into the next attempt.

    ``baseline`` is **what this session's blocking findings were before the edit**, and
    passing it is what makes the gate correct rather than merely strict. See
    `_regressions` below.

    A candidate that clears every gate but costs more than `MAX_COLLATERAL` absolvable
    findings on other fields is refused *and* written to `EditResult.suggested`. That is the
    one refusal that is a price rather than a verdict, and the owner decides it — see
    `refuse` below and `MAX_COLLATERAL`'s note.
    """
    text = str(record.get("transcript") or "")

    rejected: list[Mapping[str, Any]] = []
    #: Candidates that cleared every gate except `MAX_COLLATERAL`. See `EditResult.suggested`.
    suggested: list[Mapping[str, Any]] = []

    def failed(outcome: str, rejected: list[Mapping[str, Any]],
               targets: tuple[int, ...] = (), turn: int | None = None,
               speaker: str = "", original: str = "") -> tuple[EditResult, None]:
        return EditResult(
            finding.task_id, finding.field_id, finding.kind, turn, speaker, original,
            None, len(rejected), outcome, targets, tuple(rejected),
            suggested=tuple(suggested),
        ), None

    if not finding.locatable:
        return failed("unlocatable", [])

    # Resolve every message the finding names. A cited number resolves directly; a finding
    # that carries only a quote falls back to matching it, which is what pre-v0.4 findings
    # and any reader reply that omitted `messages` still need.
    locators = []
    for number in finding.targets:
        locator = transcript.locate_message(text, number)
        if locator is not None:
            locators.append(locator)
    if not locators and finding.quote:
        locator = transcript.locate_quote(text, finding.quote)
        if locator is not None:
            locators.append(locator)
    if not locators:
        return failed("unlocatable", [])
    locators.sort(key=lambda loc: loc.start)

    targets = tuple(loc.turn_index for loc in locators)
    originals = {loc.turn_index: text[loc.start:loc.end] for loc in locators}
    joined = " / ".join(originals[n] for n in targets)

    beats = {str(b.get("field_id")): b for b in record.get("beats") or ()}
    beat = beats.get(finding.field_id, {})
    verify_invoke = verify_invoke or invoke

    #: What was already wrong with this session before anybody touched it, as
    #: `(field_id, kind)`. A repair is judged against this, not against zero.
    prior = {(f.field_id, f.kind) for f in baseline if f.severity == BLOCKING}
    target_key = (finding.field_id, finding.kind)

    def refuse(attempt: int, shown: Mapping[int, str] | str, still: Sequence[Finding],
               regressions: Sequence[Finding], downgrades: Sequence[Finding],
               collateral: Sequence[Finding], stalled: Sequence[Finding] = ()) -> None:
        """Record one refused candidate, and — if the only thing wrong with it was its
        price — record it a second time as something a person can still choose to apply.

        A null move is never offered as a suggestion. A suggestion says *this costs more than
        the rule allows, and you may decide it is worth it*; there is nothing to decide about
        a rewrite that swaps a leak for a leak, and putting one in the queue would spend a
        person's attention on a change with no outcome.
        """
        if not stalled and not still and not regressions and not downgrades:
            suggested.append({
                "attempt": attempt, "proposed": shown,
                "collateral": [_as_entry(f) for f in collateral],
                "why": f"{len(collateral)} new absolvable finding(s) on other fields "
                       f"exceeds MAX_COLLATERAL={MAX_COLLATERAL}; the defect it was aimed "
                       "at is gone and nothing it introduced is out of `credit`'s reach, "
                       "so this is a price to accept or refuse rather than a wrong answer",
            })
        # Which gate gets the blame, when more than one could take it.
        #
        # `still_blocking` keeps first refusal, as it always had: the target defect surviving
        # is the most useful thing to say about a candidate, and a repair that did not repair
        # is not made more interesting by what else it did. The exception is the target a
        # `credit`-absolvable defect makes, where "still there" and "swapped for another one"
        # are the same null move — `stalled` covers both, so the pair is reported as what it
        # is rather than as a failed repair.
        #
        # Damage outranks the null move, though, and that ordering is load-bearing: a repair
        # that both stalls AND regresses is dangerous, `neutral` is a quiet outcome, and a
        # dangerous candidate filed quietly is how a page gets worse with nobody reading it.
        if still and not stalled:
            gate, why = "still_blocking", still
        elif regressions:
            gate, why = "regression", regressions
        elif downgrades:
            gate, why = "downgrade", downgrades
        elif stalled:
            gate, why = "no_progress", stalled
        else:
            gate, why = "collateral", collateral
        rejected.append({
            "attempt": attempt,
            "gate": gate,
            "proposed": shown,
            "why": [f"{f.kind}/{f.field_id}: {f.detail}" for f in why],
            "pre_existing_left_alone": sorted(
                f"{k}/{fid}" for fid, k in prior if (fid, k) != target_key
            ),
        })

    def gated(candidate_text: str, shown: Mapping[int, str] | str, gate_label: str,
              attempt: int,
              free_offers: Sequence[int] | None = None) -> tuple[bool, list, list] | None:
        """Every gate a candidate faces, in cost order. `None` means it was rejected."""
        if len(transcript.speaker_turns(candidate_text)) < 2:
            rejected.append({"attempt": attempt, "gate": "too_short", "proposed": shown,
                             "why": "the repair would leave fewer than two messages"})
            return None
        problems = transcript.check_session(
            candidate_text, self_report=record.get("self_report"),
            beats=record.get("beats") or (), require_numbering=True,
            free_offers=(_remapped_escapes(record) if free_offers is None else free_offers),
        )
        if problems:
            rejected.append({"attempt": attempt, "gate": "check_session", "proposed": shown,
                             "why": [str(p) for p in problems]})
            return None
        lost = beats_survive(text, candidate_text, record.get("beats") or ())
        if lost:
            rejected.append({"attempt": attempt, "gate": "beats_lost", "proposed": shown,
                             "why": [f"the page no longer carries {item}" for item in lost]})
            return None
        candidate_record = _repaired(record, transcript=candidate_text)
        try:
            verdict = audit_session(verify_invoke, verify_model, candidate_record, item)
        except (TransportError, ValueError) as exc:
            rejected.append({"attempt": attempt, "gate": "audit", "proposed": shown,
                             "why": f"{type(exc).__name__}: {exc}"})
            return None
        resolved = [f for f in reconcile(candidate_record, item, verdict)
                    if f.severity == BLOCKING]
        return (True,
                [f for f in resolved if (f.field_id, f.kind) == target_key],
                _regressions(resolved, prior=prior, target=target_key),
                _downgrades(resolved, prior=prior, target=target_key),
                _collateral(resolved, prior=prior, target=target_key),
                _no_progress(resolved, prior=prior, target=target_key))

    # --- deletion, tried FIRST because it costs no call to propose -----------------------
    #
    # The reader marked these messages as ones the page should not have at all. Generating the
    # candidate is a splice, so the only spend is the gate's own re-audit — strictly cheaper
    # than a rewrite, which pays for a generation AND the same gate. It faces every gate a
    # rewrite faces plus `beats_survive`, which only deletion needs.
    if finding.deletable:
        removal = [transcript.locate_message(text, n) for n in finding.removable_messages]
        removal = [loc for loc in removal if loc is not None]
        if removal:
            deleted_text, remap = transcript.apply_message_edits(
                text, [(loc, None) for loc in removal]
            )
            shown = f"DELETE {sorted(finding.removable_messages)}"
            escapes = _remapped_escapes(record, remap)
            outcome = gated(deleted_text, shown, "deletion", 0, escapes)
            if outcome is not None:
                _ok, still, regressions, downgrades, collateral, stalled = outcome
                if (not stalled and not still and not regressions and not downgrades
                        and len(collateral) <= MAX_COLLATERAL):
                    return EditResult(
                        finding.task_id, finding.field_id, finding.kind,
                        removal[0].turn_index, removal[0].speaker, joined,
                        shown, len(rejected), "deleted", targets, tuple(rejected),
                        deleted=tuple(sorted(finding.removable_messages)), remap=remap,
                        collateral=tuple(_as_entry(f) for f in collateral),
                    ), _repaired(record, transcript=deleted_text,
                                 free_offers=list(escapes))
                refuse(0, shown, still, regressions, downgrades, collateral, stalled)

    for attempt in range(1, max_attempts + 1):
        prompt = _rewrite_prompt(
            page=text,
            targets=[(loc.turn_index, loc.speaker, originals[loc.turn_index])
                     for loc in locators],
            field_id=finding.field_id, card_value=beat.get("gold_value"),
            move=beat.get("move", ""),
            attributed=beat.get("attributed", schedule.USER), defect=finding.detail,
            card=item.get("card"), beats=record.get("beats") or (),
        )
        try:
            raw = invoke(model, EDITOR_SYSTEM, prompt)
            proposed = _parse_edit_reply(raw, targets)
        except (TransportError, ValueError) as exc:
            rejected.append({"attempt": attempt, "gate": "reply",
                             "why": f"{type(exc).__name__}: {exc}"})
            continue

        candidate_text, remap = transcript.apply_message_edits(
            text, [(loc, proposed[loc.turn_index]) for loc in locators]
        )
        if len(transcript.speaker_turns(candidate_text)) < 2:
            rejected.append({"attempt": attempt, "gate": "too_short",
                             "proposed": _shown(proposed),
                             "why": "the repair would leave fewer than two messages"})
            continue
        problems = transcript.check_session(
            candidate_text, self_report=record.get("self_report"),
            beats=record.get("beats") or (), require_numbering=True,
            free_offers=_remapped_escapes(record, remap),
        )
        if problems:
            rejected.append({"attempt": attempt, "gate": "check_session",
                             "proposed": _shown(proposed),
                             "why": [str(p) for p in problems]})
            continue

        candidate_record = _repaired(record, transcript=candidate_text)
        try:
            verdict = audit_session(verify_invoke, verify_model, candidate_record, item)
        except (TransportError, ValueError) as exc:
            rejected.append({"attempt": attempt, "gate": "audit",
                             "proposed": _shown(proposed),
                             "why": f"{type(exc).__name__}: {exc}"})
            continue
        resolved = [f for f in reconcile(candidate_record, item, verdict)
                    if f.severity == BLOCKING]
        still = [f for f in resolved if (f.field_id, f.kind) == target_key]
        regressions = _regressions(resolved, prior=prior, target=target_key)
        downgrades = _downgrades(resolved, prior=prior, target=target_key)
        collateral = _collateral(resolved, prior=prior, target=target_key)
        stalled = _no_progress(resolved, prior=prior, target=target_key)
        if (not stalled and not still and not regressions and not downgrades
                and len(collateral) <= MAX_COLLATERAL):
            # Everything blocking still standing on the field just repaired, under some
            # other kind, that the page did not already carry. Accepted, and recorded.
            traded = tuple(
                _as_entry(f) for f in resolved
                if f.field_id == finding.field_id and (f.field_id, f.kind) != target_key
                and (f.field_id, f.kind) not in prior
            )
            return EditResult(
                finding.task_id, finding.field_id, finding.kind, targets[0],
                locators[0].speaker, joined,
                " / ".join(
                    "<deleted>" if proposed[n] is None else proposed[n] for n in targets
                ), attempt, "fixed", targets, tuple(rejected),
                deleted=tuple(n for n in targets if proposed[n] is None),
                remap={k: v for k, v in remap.items() if k != v},
                traded=traded,
                collateral=tuple(_as_entry(f) for f in collateral),
                suggested=tuple(suggested),
            ), candidate_record
        refuse(attempt, _shown(proposed), still, regressions, downgrades, collateral, stalled)

    # `neutral`, not `unresolved`, when every attempt that reached a verdict was a null move.
    # The two are different instructions to the next person: `unresolved` is a repair to try
    # again, `neutral` is a finding an editor cannot win and only `credit` or the map can
    # move. An attempt that showed real damage keeps the whole result `unresolved` — a repair
    # that is both futile AND dangerous is not a quiet one, and the log has to say so.
    verdicted = [r for r in rejected if r["gate"] in VERDICT_GATES]
    if verdicted and all(r["gate"] == "no_progress" for r in verdicted):
        return failed("neutral", rejected, targets, targets[0], locators[0].speaker, joined)
    return failed("unresolved", rejected, targets, targets[0], locators[0].speaker, joined)


def _downgrades(
    resolved: Sequence[Finding], *, prior: set[tuple[str, str]],
    target: tuple[str, str],
) -> list[Finding]:
    """Blocking findings on the repaired field that moved it **out of `credit`'s lane** —
    the one trade this gate refuses.

    `_regressions` deliberately stopped looking at the target field, and the reason it gives
    is sound: a repair usually does not remove a field from the page, it changes what the
    page says, and the reader re-files the same field under a different `kind`. Refusing that
    threw away repairs whose target defect the gate had just confirmed gone. So the trade
    became a record (`EditResult.traded`) rather than a verdict, and the independent audit
    became the arbiter.

    **That is right for a trade that keeps a second route open, and wrong for one that closes
    it, and the two are not symmetric.** The argument `_regressions` makes is explicitly
    directional: a contradiction "is out of `credit`'s lane by ADR-0046 and can only be fixed
    here, while a leak is exactly what `credit.absolve` exists to settle". Trading the
    contradiction for the leak is progress because the defect gains a second way out. Trading
    the leak for the contradiction is the same sentence read backwards — the defect had two
    routes and now has one, and the one it has left is the expensive one this module already
    failed at once.

    `runs/result-leak-pass.txt` is the measurement. Of 19 leaks sent for repair, one came back
    as a contradiction: `gen-004-s1`/`dietary`, where the rewrite cleared the leak by having
    the user tell the bot there was nothing the kitchen needed to work around, on a card where
    vegan is a rule the person will not break. The editor recorded the trade, the run was
    scored a success on the blocking count, and the page was strictly worse than before it was
    touched. Nothing downstream would have caught it: the independent audit does see it, but
    it sees a contradiction with no memory that a leak used to be there, so it reads as a
    defect the author wrote.

    The direction is read off `credit.ABSOLVABLE_KINDS` rather than a literal `"leak"`, so the
    rule is stated once. If the target defect was never absolvable to begin with, there is no
    route to close and nothing here refuses anything — a `contradiction` becoming a `leak` is
    untouched by this function, which is the whole point.

    `prior` is subtracted for the same reason `_regressions` subtracts it: a contradiction the
    page already carried on this field is not one the rewrite introduced, and refusing the
    repair for it would make a multi-defect field unrepairable in principle.
    """
    field, kind = target
    if kind not in credit.ABSOLVABLE_KINDS:
        return []
    return [
        f for f in resolved
        if f.field_id == field
        and f.kind not in credit.ABSOLVABLE_KINDS
        and (f.field_id, f.kind) not in prior
    ]


def _no_progress(
    resolved: Sequence[Finding], *, prior: set[tuple[str, str]],
    target: tuple[str, str],
) -> list[Finding]:
    """A repair that leaves the page exactly as far along as it found it: a leak for a leak.

    Scoped to a target `credit` can already settle — see `TRANSITIONS`. Two shapes count, and
    they are the same fact wearing different addresses:

    * the reader re-files the target as a leak on the same field, which the gate used to call
      `still_blocking` and treat as a failed repair, and
    * the repair clears its own leak and the re-audit raises a fresh one somewhere else, which
      `_collateral` would otherwise wave through inside `MAX_COLLATERAL`.

    Either way the blocking count is unmoved and the credit pass has exactly the same work to
    do. **The candidate is not written.** It is not damage — nothing here is refused for being
    wrong — so it does not stop the remaining attempts either: a later draw may still clear
    the leak outright, and that one is worth having. A repair that never gets past this ends
    `neutral` rather than `unresolved`, because those are different instructions to whoever
    reads the log next: `unresolved` is work someone should try again, `neutral` is a finding
    with nothing for an editor to win, and only the credit pass or the map can move it.

    Returns the findings that make the move null, so the log can say which.
    """
    field, kind = target
    if kind not in credit.ABSOLVABLE_KINDS:
        return []
    return [
        f for f in resolved
        if f.kind in credit.ABSOLVABLE_KINDS
        and ((f.field_id, f.kind) == target or (f.field_id, f.kind) not in prior)
    ]


def _regressions(
    resolved: Sequence[Finding], *, prior: set[tuple[str, str]],
    target: tuple[str, str],
) -> list[Finding]:
    """Blocking findings the edit is answerable for: on the candidate, not on the target
    **field**, and **not already there before the edit**.

    That last clause is the fix for the pilot's dominant editor failure. The gate used to
    read:

        new_blocking = [f for f in resolved if f.severity == BLOCKING
                        and (f.field_id, f.kind) != (finding.field_id, finding.kind)]

    which is not "new" at all — it is "not the one we are fixing". With no baseline to
    compare against, a defect the session already carried on some other field was
    indistinguishable from one the rewrite had just introduced, so the acceptance test was
    really *"this session must now be free of every blocking finding"*. A single-message
    patch cannot satisfy that on a multi-defect session, and the pilot measured the cost
    exactly: of 16 unresolved repairs, **14 were in sessions carrying two or more blocking
    findings and could not have succeeded whatever the editor wrote** — `gen-000-s2` had 5,
    and burned 4 repair attempts that were unwinnable before the first call. The one success
    was the one session with a single blocking finding.

    Subtracting `prior` keeps what the gate was actually for — an edit must not make the page
    worse — while letting a session be repaired one finding at a time.

    **The exclusion is the target FIELD, not the target `(field, kind)` pair.** Keying it on
    the pair made the gate refuse the repairs that had worked. A repair does not usually
    delete a field from the page; it changes what the page says about it, and the reader
    re-files it under a different `kind`. `gen-002-s9`/`dietary` is the whole argument: three
    attempts, `still` empty every time — the contradiction was gone on all three — and all
    three refused on `leak/dietary`, which is the *same defect's replacement*, on the field
    being repaired, raised by the re-audit of the very text that fixed it. Under the pair key
    that leak is neither the target nor in `prior`, so it scored as damage the rewrite
    introduced.

    That trade is progress in this project's terms and not merely a relabelling: a
    contradiction is out of `credit`'s lane by ADR-0046 and can only be fixed here, while a
    leak is exactly what `credit.absolve` exists to settle. Refusing the trade left the
    contradiction on the page and sent nothing to credit.

    **And the same sentence is true one field over, which is why a `credit`-absolvable kind
    on ANY field is no longer counted here.** It goes to `_collateral` instead, and is
    bounded by `MAX_COLLATERAL` rather than refused: a repair may trade a defect for a
    defect, and may not spray. The measurement is in `MAX_COLLATERAL`'s note — 12 of the 25
    regression rejections among the n=15 run's unresolved repairs named nothing but leaks.
    What is left here is what it always should have been: a defect the rewrite introduced
    that nothing downstream can settle.

    What this gives up is bounded, and the bound is `_downgrades` (ADR-0056). A trade that
    leaves the target field with a defect `credit` could still settle is accepted here and
    recorded as `EditResult.traded`, with the independent full audit as arbiter — that audit
    sees the field with no memory of what it used to be, which is the right reading for a
    trade that cost nothing. A trade that leaves the field with a defect `credit` cannot
    touch is a different animal and is refused before it is written, because the independent
    audit is exactly the instrument that *cannot* see it: it reads a contradiction the
    rewrite introduced as one the author wrote.
    """
    field, _kind = target
    return [
        f for f in resolved
        if f.field_id != field and (f.field_id, f.kind) not in prior
        and f.kind not in credit.ABSOLVABLE_KINDS
    ]


def _collateral(
    resolved: Sequence[Finding], *, prior: set[tuple[str, str]],
    target: tuple[str, str],
) -> list[Finding]:
    """New `credit`-absolvable findings the repair put on fields it was not repairing.

    The other-field half of the trade `_regressions` and `_downgrades` argue about on the
    target field. These are not refused — see `MAX_COLLATERAL` for why, and for the bound —
    but they are *always recorded*, on an accepted repair (`EditResult.collateral`) as much
    as on a refused one.

    **Recording is the load-bearing half, not the bookkeeping half.** The independent
    post-edit audit is the arbiter for everything the editor accepts, and it reads a leak
    with no memory of who wrote it: an editor-minted leak and an author-written one are the
    same finding on the page. That blindness is the whole argument of `_downgrades`, and it
    bites harder here — `runs/result-reader-n15.txt` reads leak rate per session and per
    UNEVIDENCED cell by chain depth, and on an edited store those rates stop being about the
    author the moment the editor can add to them without saying so.

    Read off `credit.ABSOLVABLE_KINDS` rather than a literal `"leak"`, same as `_downgrades`,
    so the direction is stated once.
    """
    field, _kind = target
    return [
        f for f in resolved
        if f.field_id != field and (f.field_id, f.kind) not in prior
        and f.kind in credit.ABSOLVABLE_KINDS
    ]


#: Sessions repaired at the same time. Matches `verify.DEFAULT_WORKERS` — the same box, the
#: same CLI, and the same reason that number is 4 rather than 28.
DEFAULT_WORKERS: int = 4


def repair_corpus(
    invoke: Invoker, model: str, findings: Sequence[Finding], *,
    records: MutableMapping[str, dict[str, Any]],
    items: Mapping[str, Mapping[str, Any]],
    write: Callable[[Mapping[str, Any]], None],
    baseline_findings: Sequence[Finding] | None = None,
    verify_invoke: Invoker | None = None,
    verify_model: str = DEFAULT_MODEL, workers: int = DEFAULT_WORKERS,
    max_attempts: int = MAX_ATTEMPTS,
    on_session: Callable[[str, Sequence[EditResult], Sequence[str]], None] | None = None,
) -> list[EditResult]:
    """Repair every locatable blocking finding in `findings`, one worker per session.

    **The unit of work is the page, not the finding, and that is what makes this safe to run
    in parallel.** A repair has exactly two order-dependent inputs, and both are scoped to a
    single session: `baseline`, the session's own pre-edit findings that `_regressions` needs
    to tell an introduced defect from an inherited one; and `remap`, which a deletion forces
    onto the findings still queued behind it *on that same page*. The serial driver this
    replaces already skipped every finding whose `task_id` differed when it propagated a
    remap — so findings on different pages never observed each other, and running them at the
    same time cannot change an outcome. Findings on the same page still run in order.

    `records` is read and updated in place so a page's later findings see its earlier
    repairs. `write` persists an accepted candidate and is called under a lock, because the
    store is the one resource two workers can reach at once.

    Results come back in the order the sessions first appear in `findings` — thread
    completion order is not allowed to reach the artifact, or two runs over one report
    produce edit logs that diff.

    `on_session` is called once per finished session with `(task_id, results, log lines)`,
    under the same lock, for progress reporting and for checkpointing a log to disk. A run
    that is killed halfway must still leave an artifact saying what it changed.

    **`baseline_findings` is what the gate compares against, and it is not always
    `findings`.** `_regressions` accepts a candidate by diffing a *fresh* `audit_session` of
    it against the session's pre-edit findings, so the two sides have to be the same kind of
    reading. Run the editor over a report `credit.absolve` has demoted leaks in, and they
    are not the same kind: the gate's own re-audit is uncredited and re-raises every
    absolved leak, while the baseline no longer lists it — so a pre-existing defect is
    scored as one the rewrite introduced, and every repair on that page is refused. That is
    not hypothetical; it refused 5 of 5 sessions before this parameter existed, three of
    them naming `planning_style`, the single most-absolved field in the corpus.

    So: take the **work list** from the credited report, so no absolved leak is ever sent
    for repair, and the **baseline** from the raw one, so the gate compares like with like.
    Defaults to `findings`, which is right whenever no credit has been applied.
    """
    locatable = [f for f in findings if f.severity == BLOCKING and f.locatable]
    groups: dict[str, list[Finding | None]] = {}
    for finding in locatable:
        groups.setdefault(finding.task_id, []).append(finding)
    order = list(groups)

    blocking_by_task: dict[str, list[Finding]] = {}
    for finding in (findings if baseline_findings is None else baseline_findings):
        if finding.severity == BLOCKING:
            blocking_by_task.setdefault(finding.task_id, []).append(finding)

    lock = Lock()
    done: dict[str, list[EditResult]] = {}

    def one(task_id: str) -> None:
        queued = groups[task_id]
        out: list[EditResult] = []
        log: list[str] = []
        baseline = blocking_by_task.get(task_id, [])
        record = records.get(task_id)

        for index, finding in enumerate(queued):
            if finding is None:
                continue
            item = items.get(str(record["subject_id"])) if record else None
            if record is None or item is None:
                out.append(EditResult(
                    finding.task_id, finding.field_id, finding.kind, finding.turn_index,
                    finding.speaker, "", None, 0, "unlocatable",
                ))
                continue
            result, patched = edit_session(
                invoke, model, record, item, finding, verify_invoke=verify_invoke,
                verify_model=verify_model, max_attempts=max_attempts, baseline=baseline,
            )
            out.append(result)
            where = ",".join(str(m) for m in result.messages) or str(result.turn_index)
            log.append(f"  {result.outcome}: {finding.task_id} {finding.field_id} "
                       f"message {where} ({result.attempts} attempt(s))")
            log += [f"      rejected at {entry['gate']}: {entry.get('why')}"
                    for entry in result.rejected]
            log += [f"      traded for {entry['kind']}/{entry['field_id']}: "
                    f"{entry['detail']}" for entry in result.traded]
            if result.outcome == "neutral":
                log.append("      NEUTRAL: every candidate swapped a leak for a leak. The "
                           "page is unchanged and there is nothing here for an editor to "
                           "win — this one is credit's or the map's.")
            log += [f"      cost a {entry['kind']} on {entry['field_id']}: "
                    f"{entry['detail']}" for entry in result.collateral]
            log += [f"      SUGGESTION (attempt {entry['attempt']}, "
                    f"{len(entry['collateral'])} new "
                    f"{', '.join(sorted({e['field_id'] for e in entry['collateral']}))}): "
                    "clears every gate but the cap — a person decides"
                    for entry in result.suggested]
            if patched is None:
                continue
            with lock:
                write(patched)
            record = patched
            records[task_id] = patched
            # A deletion renumbers the rest of the page, so every finding still queued
            # against this session is now pointing one message too high. Follow the remap
            # before the next iteration reads it, and drop the ones whose text is gone.
            if result.remap:
                for position in range(index + 1, len(queued)):
                    other = queued[position]
                    if other is None:
                        continue
                    moved = remap_finding(other, result.remap)
                    if moved is None:
                        log.append(f"  dropped: {other.task_id} {other.field_id} — the "
                                   "messages it named were deleted by an earlier repair")
                    queued[position] = moved
                log.append(f"      renumbered: {result.remap}")

        with lock:
            done[task_id] = out
            if on_session is not None:
                on_session(task_id, out, log)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for _ in pool.map(one, order):
            pass
    return [r for task_id in order for r in done.get(task_id, ())]



def _remapped_escapes(
    record: Mapping[str, Any], remap: Mapping[int, int | None] | None = None
) -> tuple[int, ...]:
    """The record's declared `free_offers`, following a deletion's renumbering.

    A declared escape is a message NUMBER, so a deletion moves it exactly as it moves a
    finding's `messages` — `remap_finding`'s problem, one field over. Without this the repaired
    page's escapes point one message too high and `_offer_problems` reads a bot turn the author
    never declared, which is the gate eating its own repair.
    """
    numbers = [int(n) for n in (record.get("free_offers") or ())]
    if not remap:
        return tuple(numbers)
    moved = [remap.get(n, n) for n in numbers]
    return tuple(sorted(n for n in moved if n is not None))


def remap_finding(finding: Finding, remap: Mapping[int, int | None]) -> Finding | None:
    """`finding` with its message numbers moved to follow a deletion, or `None` if the text
    it pointed at is gone.

    Deleting message 3 makes every later message shift down by one, so a finding raised
    against the same session that cited message 11 now points at message 10's text. Nothing
    warns you: the number is still valid, it just names something else. A repair run applies
    findings one at a time to the same session, so without this the second edit on a session
    the first one shortened would rewrite the wrong message — silently, and in a store that
    is about to be frozen.

    A finding every one of whose messages was deleted returns `None`: whatever was wrong with
    that text, the text is no longer there. It is dropped rather than kept-and-skipped because
    the independent post-run audit is what decides whether the defect survived, and a stale
    finding carried into that decision would be answering about a page that no longer exists.
    """
    if not remap or not finding.messages:
        return finding
    moved = [remap.get(number, number) for number in finding.messages]
    kept = tuple(n for n in moved if n is not None)
    if not kept:
        return None
    # The reader's proposed deletion follows the same remap, for the same reason the span
    # does: a stale number here still resolves, it just names a different message — and a
    # deletion acting on the wrong message destroys text rather than misplacing a rewrite.
    # Any proposed message that has itself been deleted is dropped from the proposal.
    removable = tuple(
        n for n in (remap.get(number, number) for number in finding.removable_messages)
        if n is not None
    )
    return dc_replace(finding, messages=kept, turn_index=kept[0],
                      removable_messages=removable)
