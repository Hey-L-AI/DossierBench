"""Key 3, asked as a question instead of inferred from a silence.

`credit.py`'s third key requires a second, independent reader to also find a field readable on
the page. Until now that reader's answer was taken from its **free-recall** `unscheduled` list:
`verify.AUDIT_SYSTEM` asks the open question "which fields NOT in the beat list does the
conversation nonetheless make readable?" against a ~30-field vocabulary, and key 3 read a
field's absence from the resulting list as a denial.

Absence from a free-recall list is not a denial. It is recall. The post-edit pilot refused 10
cells as `unconfirmed`, and on the clearest of them — `gen-001-s7`, "nothing fancy, we're not
spending big" — the second reader had in fact read *the same sentence*, filing its next clause
("a tenner-ish each ideally") under `price_band_distribution`. The two readers did not disagree
about whether the page carried something. They disagreed about which field owned it, and only
one of them was ever asked about `formality_ceiling`.

So ask. One call per session, naming the fields actually in question, answerable in a sentence
each. This is the check `credit.Credit.instrument` was built to make possible — the provenance
records what the first reader was shown precisely so a second can be pointed at the same page
for a few dozen calls rather than re-reading the corpus.

**The card is withheld.** The audit prompt shows it, hedged with an instruction not to reason
from it; a targeted yes/no question is far more suggestible than an open one, and a reader that
can see `formality_ceiling = "counter_service"` is being asked to agree rather than to read. It
gets the page and the field name, nothing else. What it says the page conveys is compared to
the card **here**, by code, after the fact.

**A quote that is not on the page is not a confirmation.** Every `yes` must cite text that
appears verbatim in the transcript it was shown. This is the same rule the README has now paid
for five times — read the excerpt before believing the count — applied at the point where the
count is made rather than after it reaches a table.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from time import sleep
from typing import Any, Mapping, Sequence

from . import fields
from .judge import (
    DEFAULT_ATTEMPTS, RETRY_BACKOFF, Invoker, TransportError, fingerprint,
)
from .verify import VerdictStore, parse_verdict

#: The second reader. Kept distinct from `verify.DEFAULT_MODEL` on purpose: key 3 is worth
#: nothing if both readings come from the same model.
DEFAULT_MODEL: str = "claude-sonnet-5"

CONFIRM_SYSTEM = """\
You are checking one conversation from a research dataset for a single, narrow thing: whether \
certain facts about the user are READABLE from it.

A fact is readable if someone who had only this conversation could state it. A fact that is \
merely consistent with the conversation is not readable — it has to be readable *from* it.

One distinction this dataset turns on: whose fact it is. A preference the BOT expresses, or a \
companion's requirement the user is relaying, is not a fact about the user, however clearly the \
page states it. The user taking a suggestion up is different from the bot making it.

For each field, before you decide, say what the conversation actually conveys about it. Then \
decide. Both answers need that sentence — a "no" that cannot say what was considered is as \
unhelpful as a "yes" that cannot quote.

Each field comes with its ordered levels. When a field IS readable, say which levels the \
conversation is consistent with — all of them that are, not just the closest. Naming every \
level is the same as saying it is not readable, so if that is your answer, say `false`.

Output JSON only, no prose around it:

{"answers": [{"field_id": "...", "reads_as": "what the page conveys about this field, in your \
own words; if nothing, say what is closest and why it falls short", "readable": true|false, \
"levels": ["level_name", ...], "messages": [5, 6], \
"quote": "verbatim from the conversation, or \\"\\" if not readable"}]}

Answer every field you are asked about, once, in the order given. Quote VERBATIM — the quote is \
checked against the conversation, and an approximate one is treated as a no."""

#: The first version of the prompt above, kept because deleting it would hide a result. It
#: carried three worked examples of readings to reject — two of which were, verbatim, cells it
#: was about to be asked to judge — and the line "several of these should get one [a no]". Those
#: examples were lifted from `runs/prediction-key3-confirm.txt`: the author's own prediction,
#: handed to the reader as instruction. It returned 1 confirmation of 10 against a predicted 5.
#: `runs/result-key3-confirm.txt` scores both framings side by side, because the gap between
#: them is a fact about the instrument that the neutral run alone would not show.
LEADING_SYSTEM_V1: str = """\
You are checking one conversation from a research dataset for a single, narrow thing: whether \
certain facts about the user are READABLE from it.

A fact is readable if someone who had only this conversation could state it. It is NOT \
readable merely because it would be consistent with the conversation, and it is NOT readable \
because you can imagine the sort of person who would talk this way. If you have to reason \
about what a person like this probably prefers, the answer is no.

Two things that are easy to get wrong and matter here:

- WHOSE FACT IT IS. A restaurant the BOT suggests tells you what the bot thinks, not what the \
user wants, unless the user takes it up. A companion's requirement the user is relaying is not \
a fact about the user.
- IMPLICATION IS NOT STATEMENT. "We've been in a few times" says they have been there. It does \
not say whether they generally prefer regular haunts to new places. Naming a nearby street says \
where this one place is; it does not say how far they will travel in general. Say no to these.

You are not being asked to be generous and you are not being asked to be strict. You are being \
asked what the page says. "no" is a completely normal answer and several of these should get \
one.

Output JSON only, no prose around it:

{"answers": [{"field_id": "...", "readable": true|false, "messages": [5, 6], \
"quote": "verbatim from the conversation, or \\"\\" if not readable", \
"reads_as": "what the page says about this, in your own words, or \\"\\"" }]}

Answer every field you are asked about, once, in the order given. Quote VERBATIM — the quote \
is checked against the conversation, and an approximate one is treated as a no."""


@dataclass(frozen=True)
class Answer:
    """One field, asked and answered on one page."""

    task_id: str
    field_id: str
    readable: bool
    messages: tuple[int, ...]
    quote: str
    reads_as: str
    model: str
    #: Every level the reader says the page is consistent with. Empty where the field has no
    #: ordered levels, or where the reader declined to narrow. This is the *direction* of the
    #: reading, and it is checked against the card by `direction` — a reader can find a field
    #: perfectly readable and read the opposite of what the card says, and on this pilot four
    #: of six confirmations did exactly that.
    levels: tuple[str, ...] = ()
    #: False when `readable` was true but the quote is not on the page. Kept as a distinct
    #: state rather than folded into `readable`, because a reader that confirms with invented
    #: text is a different problem from one that declines, and only one of them is a defect.
    quote_found: bool = True

    @property
    def key(self) -> tuple[str, str]:
        """How a cell is addressed everywhere: by session and field, never by quote."""
        return (self.task_id, self.field_id)

    @property
    def confirms(self) -> bool:
        """Readable, quoted from the page, and actually narrowing.

        Direction is deliberately NOT here: it needs the card, and this object is built from
        a reply produced without one. `direction` applies it.
        """
        return bool(self.readable and self.quote_found and self.narrows)

    @property
    def narrows(self) -> bool:
        """Did the reading exclude anything?

        A field with no ordered levels cannot be narrowed and is not held to it. Naming every
        level is the reader saying the page is consistent with anything, which is what an
        unreadable field looks like — WEAK means a reader could give a range, not that one
        exists.
        """
        levels = getattr(fields.BY_ID.get(self.field_id), "levels", ()) or ()
        if not levels:
            return True
        return 0 < len(self.levels) < len(levels)


#: The framings this module can ask in, so a run can be repeated under both and the gap
#: measured. Two readers agreeing means nothing if either would have said the opposite under a
#: differently-worded question; that is the same doubt key 3 exists to answer, one level up.
SYSTEMS: dict[str, str] = {}


def confirm_prompt(record: Mapping[str, Any], field_ids: Sequence[str]) -> str:
    """The page and the questions. No card, no schedule, no first reader's answer."""
    lines = [
        "=== THE CONVERSATION ===",
        record.get("transcript") or "",
        "",
        "=== THE QUESTION ===",
        "For each field below: is this fact about the USER readable from the conversation "
        "above?",
        "",
    ]
    for field_id in field_ids:
        spec = fields.BY_ID.get(field_id)
        lines.append(f"  {field_id}")
        for index, level in enumerate(getattr(spec, "levels", ()) or ()):
            anchor = (getattr(spec, "anchors", ()) or ())
            gloss = f" — {anchor[index]}" if index < len(anchor) else ""
            lines.append(f"      {level}{gloss}")
    lines += [
        "",
        "Every message is numbered — `[0]`, `[1]`, `[2]`. Cite the numbers your answer "
        "lives in. Answer for this conversation only.",
    ]
    return "\n".join(lines)


def _normalise(text: str) -> str:
    """Whitespace- and quote-insensitive, everything else exact.

    Deliberately narrow. Collapsing case or punctuation would let a paraphrase pass as a
    verbatim quote, which is the one thing this check exists to catch; collapsing whitespace
    and smart quotes only forgives how the reply was typed.
    """
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip()


def quote_on_page(quote: str, transcript: str) -> bool:
    """Is this quote actually in the text the reader was shown?

    Ellipsis is honoured: readers stitch two spans with `...` and both halves have to land.
    An empty quote is not on the page — a confirmation with nothing behind it is the case
    this returns False for.
    """
    if not quote.strip():
        return False
    page = _normalise(transcript)
    parts = [p for p in re.split(r"\s*\.\.\.\s*|\s*…\s*", quote) if p.strip()]
    return all(_normalise(part) in page for part in parts)


def parse_answers(raw: str) -> list[dict[str, Any]]:
    """The reply as a list of answers. Raises on an unreadable reply, like `parse_verdict`."""
    payload = parse_verdict(raw)
    answers = payload.get("answers")
    if not isinstance(answers, list):
        raise ValueError("reply has no 'answers' list")
    return [a for a in answers if isinstance(a, dict)]


def _by_level(
    by_field: Mapping[str, Mapping[str, Any]], field_id: str
) -> dict[str, Any] | None:
    """One answer for a set field the reader answered LEVEL BY LEVEL, or `None`.

    Asked about `allergies` on `gen-013-s11`, the second reader twice replied with nine
    answers — one per allergen — of which `wheat` was `readable: true` and carried the
    verbatim quote the first reader had cited. `by_field` had no `allergies` key, so the
    question read as unanswered, was synthesised as `readable: false` with an empty
    `reads_as`, and entered the tally as two silences. The cell went 1 agree / 2 silent and
    lost its credit to two answers that had in fact agreed, in the model's own words, on
    disk. Nothing distinguished it from a real refusal but the empty sentence the prompt
    demands and the parser did not check.

    A reply that answers under the asked field's own level names is answering at a finer
    grain than it was asked, not failing to answer. Folded here rather than in `parse_answers`
    because only the caller knows what was asked. The union of the readable levels is the
    field's reading; `direction`'s set rule then decides whether it agrees with the card, so
    a reader that reads one on-card allergen and one off-card one still opposes.
    """
    levels = getattr(fields.BY_ID.get(field_id), "levels", ()) or ()
    parts = [by_field[level] for level in levels if level in by_field]
    if not parts:
        return None
    readable = [part for part in parts if part.get("readable")]
    if not readable:
        # The reader looked at every level and said no to all of them. That is a refusal it
        # actually made, and it is kept as one.
        return {"field_id": field_id, "readable": False, "levels": [], "messages": [],
                "quote": "", "reads_as": str(parts[0].get("reads_as") or "")}
    return {
        "field_id": field_id, "readable": True,
        "levels": [str(part.get("field_id")) for part in readable],
        "messages": list(readable[0].get("messages") or ()),
        "quote": str(readable[0].get("quote") or ""),
        "reads_as": str(readable[0].get("reads_as") or ""),
    }


def confirm_session(
    invoke: Invoker, model: str, record: Mapping[str, Any], field_ids: Sequence[str],
    *, store: VerdictStore | None = None, attempts: int = DEFAULT_ATTEMPTS,
    system: str = "",
) -> list[Answer]:
    """Put one session's open questions to the second reader.

    Cached and retried on exactly the terms `verify.audit_session` uses: the fingerprint covers
    the page AND the field list, so adding a question or editing the transcript misses the
    cache rather than answering the old one.
    """
    system = system or CONFIRM_SYSTEM
    asked = list(dict.fromkeys(str(f) for f in field_ids))
    prompt = confirm_prompt(record, asked)
    instrument = fingerprint(system, prompt)
    task_id = str(record["task_id"])
    cached = store.load(model, task_id, expect=instrument) if store is not None else None
    if cached is not None:
        raw = cached["raw"]
    else:
        for attempt in range(1, max(1, attempts) + 1):
            try:
                raw = invoke(model, system, prompt)
                parse_answers(raw)
                break
            except TransportError:
                if attempt >= max(1, attempts):
                    raise
                sleep(RETRY_BACKOFF ** attempt)
    transcript = record.get("transcript") or ""
    by_field = {str(a.get("field_id")): a for a in parse_answers(raw)}
    out: list[Answer] = []
    for field_id in asked:
        answer = by_field.get(field_id) or _by_level(by_field, field_id)
        if answer is None:
            # The reply did not answer this question. `tally` has already decided what that
            # means — "a run that failed to reach a session says nothing about it, and
            # treating an unreached session as a refusal is the same mistake free-recall
            # key 3 made" — and it can only apply that rule to an answer that is ABSENT.
            # An `Answer(readable=False)` synthesised here is indistinguishable downstream
            # from a reader that looked at the page and said no: it lands in `Tally.silent`
            # and costs the cell the majority `credits` requires. Emit nothing instead.
            continue
        quote = str(answer.get("quote") or "")
        readable = bool(answer.get("readable"))
        out.append(Answer(
            task_id=task_id, field_id=field_id, readable=readable,
            messages=tuple(
                int(m) for m in (answer.get("messages") or ())
                if isinstance(m, (int, float)) or str(m).isdigit()
            ),
            quote=quote, reads_as=str(answer.get("reads_as") or ""), model=model,
            levels=tuple(
                str(level) for level in (answer.get("levels") or ())
                if str(level) in (getattr(fields.BY_ID.get(field_id), "levels", ()) or ())
            ),
            quote_found=quote_on_page(quote, transcript) if readable else True,
        ))
    if store is not None and cached is None:
        store.write(model, {
            "task_id": task_id, "subject_id": record.get("subject_id"), "model": model,
            "instrument": instrument, "raw": raw, "asked": asked,
            "answers": [a.__dict__ for a in out],
        })
    return out


#: What `direction` can return. `OPPOSES` is the one that matters and the one nothing checked
#: before: a reader finding a field readable is not the same as a reader finding the card's
#: value readable, and crediting the first as if it were the second puts a cell in the answer
#: key whose page says the opposite. That cell is worse than an uncredited one — a twin reading
#: it correctly is then scored wrong, which is the exact inversion this benchmark measures.
AGREES, OPPOSES, UNCHECKED = "agrees", "opposes", "unchecked"


#: Levels a set field's card value **entails**, keyed on `(field_id, card_value)`.
#:
#: A reading that names the card's value and adds only levels that value entails is naming the
#: same constraint at a finer grain, not a different one. `gen-004-s1` is the cell that forced
#: this: a card of `['vegan']` against a reader that answered `['vegetarian', 'vegan',
#: 'dairy_free']` on *"no dairy in the pasta, no butter in the sauces"* — every extra level a
#: consequence of the value the card holds — which the set rule below recorded as a reading
#: AGAINST its own card, 2 of 3 runs.
#:
#: **Directional, and deliberately so.** `vegan` entails `vegetarian`; `vegetarian` does not
#: entail `vegan`, and a reading that goes the other way is the reader claiming MORE than the
#: card, which is the inversion `OPPOSES` exists to catch.
#:
#: **One entry, because one cell has been measured.** `schedule.ENTAILED_BY_BIN`'s comment
#: applies here word for word — a table extended past its evidence is how bare tokens get in —
#: so the obvious-looking additions (`kosher`, `halal`, the allergen/diet crossings) are NOT
#: here. Add one when a cell arrives that needs it, not before.
ENTAILED_LEVELS: dict[tuple[str, str], frozenset[str]] = {
    ("dietary", "vegan"): frozenset({"vegetarian", "dairy_free"}),
}


#: The weight a card must give a bin before a reading of that bin counts as agreement.
#:
#: Same number and same judgement as `verify.py`'s `unscheduled_occasion`, which calls a page
#: blocking only when the card gives EVERY bin the page could be carrying less than this. A card
#: that says 0.32 of this person's outings land in `£30-50` is not contradicted by a page showing
#: one of them; a card that gives the read bin 0.00 is.
CARD_BIN_FLOOR: float = 0.02


def _bin_weight(card_value: Mapping[str, Any], levels: Sequence[str]) -> float:
    """The most weight the card gives any bin the reader read. 0.0 if it read none of them."""
    weights = [float(card_value.get(level, 0.0) or 0.0) for level in levels]
    return max(weights) if weights else 0.0


def _entailed(field_id: str, held: set[str]) -> set[str]:
    """Every level the card's own values entail, for one set field."""
    out: set[str] = set()
    for value in held:
        out |= ENTAILED_LEVELS.get((field_id, value), frozenset())
    return out


def direction(answer: Answer, card_value: Any) -> str:
    """Does the reader's reading include the value the credit would be evidence for?

    THREE card shapes, and they are not interchangeable. A scalar field holds one level and
    `answer.levels` is the reader's *candidate* readings, so agreement is "the card's level is
    among them". A **set** field holds a list — `dietary`, `allergies`, `cuisines_liked`,
    `cuisines_avoided`, `event_types_liked`, `event_types_avoided`, `accessibility_needs` — and
    `str(card_value)` on one of those produces `"['vegan']"`, brackets and quotes included,
    which can never appear in `answer.levels`. Every set field therefore read as OPPOSES and
    **could not be credited at all**: 7 of 24 fields, silently, including a reading of `vegan`
    against a card of `vegan`.

    It failed closed, so nothing wrong ever entered the answer key — but it refused correct
    credits, which is the seventh time a count here looked credible until the excerpt was read.

    The rule for a set field, decided by the owner 2026-08-21: a reading agrees when it names
    at least one value the card holds and **none the card does not**. Partial evidence counts,
    because credit is capped at WEAK either way — naming one of four liked event types narrows
    the field without settling it, exactly ADR-0044's argument for entailments. A reading that
    names something off the card does not count: `gen-004-s0` reads `cooking_class` against a
    card listing four other outings, and that is the inversion this guard exists to catch.

    The mixed case (some on-card, some off) arrived at stage 20 and split in two. Extras the
    card's own value ENTAILS agree — `ENTAILED_LEVELS` above, and `gen-004-s1` is the cell that
    forced it. Extras the card does not entail still oppose, which is the reading this guard was
    built for and the one the pilot evidence actually supports: `gen-004-s0` reads
    `cooking_class` against a card listing four other outings, and that overlaps with nothing, so
    it is refused under either rule. `test_direction_set_field_mixed_reading_opposes` pins the
    unentailed half; `test_direction_set_field_entailed_extras_agree` pins the new one.

    An entailment alone does not agree. A reading of `['vegetarian']` against a card of
    `['vegan']` names no value the card holds, and `read & held` is still required — the reader
    has to reach the card's own level before the extras are forgiven.

    A **distribution** field holds a MAPPING of bin -> weight, and until stage 23 it fell through
    to the scalar branch, which asked `str({'<£15': 0.0, ...}) in levels` — a comparison no
    reading could ever satisfy. Every distribution cell was therefore uncreditable BY
    CONSTRUCTION and recorded as OPPOSES, which is a false opposition rather than a refusal:
    `gen-007-s3` read `£30-50` on "forty a head ... that could work", a bin its own card gives
    0.32, and the code called that a reading against the card.

    The rule is `CARD_BIN_FLOOR`: a reading agrees when the card gives some bin the reader read
    real weight. **This is the owner's call from 2026-08-23 and it narrows Pending 1b.** One
    observation still is not a rate — nothing here credits a SHAPE — but one observed bin does
    narrow a distribution, and WEAK is the band for narrowing without pinning. `schedule`'s
    `ENTAILED_BY_BIN` already relies on the same premise, entailing `deal_seeking` from a single
    `£30-50` or `£50+` occasion.
    """
    levels = getattr(fields.BY_ID.get(answer.field_id), "levels", ()) or ()
    if not levels or not answer.levels or card_value in (None, "", [], (), {}):
        return UNCHECKED
    if isinstance(card_value, (list, tuple, set, frozenset)):
        held = {str(v) for v in card_value}
        read = {str(v) for v in answer.levels}
        allowed = held | _entailed(answer.field_id, held)
        return AGREES if (read & held) and not (read - allowed) else OPPOSES
    if isinstance(card_value, Mapping):
        return AGREES if _bin_weight(card_value, answer.levels) >= CARD_BIN_FLOOR else OPPOSES
    return AGREES if str(card_value) in answer.levels else OPPOSES


def as_confirmations(
    answers: Sequence[Answer], *, cards: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """The answers in the shape `credit.eligible(confirmations=...)` already reads.

    A confirmed field is emitted as an `unscheduled` entry because that is what it is — a
    field the reader finds on the page that the schedule did not place. This keeps the targeted
    ask and the free-recall read interchangeable at the credit boundary, so key 3 has one
    implementation rather than two.
    """
    out: dict[str, dict[str, Any]] = {}
    for answer in answers:
        if not answer.confirms:
            continue
        if cards is not None:
            card = cards.get(answer.task_id) or {}
            if direction(answer, card.get(answer.field_id)) == OPPOSES:
                continue
        record = out.setdefault(
            answer.task_id, {"task_id": answer.task_id, "model": answer.model,
                             "unscheduled": [], "beats": [], "contradictions": []},
        )
        record["unscheduled"].append({
            "field_id": answer.field_id, "quote": answer.quote,
            "messages": list(answer.messages), "strength": "narrowed",
        })
    return out


def merge(*sources: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Free-recall readings and targeted answers, unioned per session.

    Union rather than override: a field the second reader volunteered is confirmed whether or
    not it was later asked about, and asking about `formality_ceiling` must not discard its
    unprompted reading of `price_band_distribution`.
    """
    out: dict[str, dict[str, Any]] = {}
    for source in sources:
        for task_id, record in (source or {}).items():
            merged = out.setdefault(task_id, {"task_id": task_id, "model": "",
                                              "unscheduled": [], "beats": [],
                                              "contradictions": []})
            merged["model"] = merged["model"] or str(record.get("model") or "")
            for key in ("unscheduled", "beats", "contradictions"):
                merged[key] = list(merged.get(key) or ()) + list(record.get(key) or ())
    return out


def veto(
    confirmations: Mapping[str, Mapping[str, Any]],
    tallies: Mapping[tuple[str, str], "Tally"],
) -> dict[str, dict[str, Any]]:
    """`confirmations` with every cell the TARGETED ask read against the card removed.

    `merge` unions free recall with the targeted answers, and that union is right for adding
    a reading — but wrong for removing one, which is why this is a separate step rather than
    a change to `merge`. The two instruments are not equals. Free recall asks "what does this
    page make readable?" over a ~30-field vocabulary and `read_fields` then checks only that
    the field was *mentioned*: that is recall, and it says nothing about which value the page
    narrows to. The targeted ask checks the quote against the page, requires a level, and
    compares that level to the card. Where they disagree, the one that looked is right.

    Without this, `gen-003-s1 design_sensitivity` credits: the targeted ask reads the page as
    `mild_preference` against a card of `room_matters` — readable, stable, one level off —
    and free recall having merely named the field overrode it. That is `gen-001-s7` exactly,
    the inversion ADR-0047 was written for, arriving through the one path its direction check
    was never applied to. A cell credited that way puts a value in the answer key that the
    page narrows away from, so a twin reading the page correctly is scored wrong.

    Silence does not veto. A cell the targeted ask never reached, or found unreadable, falls
    back to free recall unchanged — refusing on silence is the mistake free-recall key 3 made
    and is not repeated here in the opposite direction.
    """
    opposed = {
        (entry.task_id, entry.field_id)
        for entry in tallies.values() if entry.oppose > entry.agree
    }
    if not opposed:
        return {task_id: dict(record) for task_id, record in (confirmations or {}).items()}
    out: dict[str, dict[str, Any]] = {}
    for task_id, record in (confirmations or {}).items():
        kept = dict(record)
        for key in ("unscheduled", "beats"):
            kept[key] = [
                entry for entry in (record.get(key) or ())
                if (task_id, str(entry.get("field_id"))) not in opposed
            ]
        out[task_id] = kept
    return out


#: A RECONSTRUCTION of the neutral prompt as it stood before the level question was added,
#: kept only to measure what that question changed. Under it the reader called 6 of 10 cells
#: readable and its own summaries described the OPPOSITE of the card on several — it was reading
#: the user's register ("casual and colloquial throughout") rather than the venue they chose.
#: Made to commit to a level, the same model read the same pages the other way. Not a framing
#: this pipeline should use; a framing it has to be able to reproduce, because "the answers moved
#: when we asked differently" is a claim that needs an artifact behind it.
_LEVELS_PARAGRAPH = (
    "Each field comes with its ordered levels. When a field IS readable, say which levels "
    "the conversation is consistent with — all of them that are, not just the closest. "
    "Naming every level is the same as saying it is not readable, so if that is your "
    "answer, say `false`.\n\n"
)

NEUTRAL_NO_LEVELS_V2: str = (
    CONFIRM_SYSTEM
    .replace(_LEVELS_PARAGRAPH, "")
    .replace('"levels": ["level_name", ...], ', "")
)

SYSTEMS.update({
    "neutral": CONFIRM_SYSTEM,
    "neutral-no-levels-v2": NEUTRAL_NO_LEVELS_V2,
    "leading-v1": LEADING_SYSTEM_V1,
})


@dataclass(frozen=True)
class Tally:
    """One cell's answers across repeated runs of the same question.

    A single ask is not a measurement. Repeating the shipping framing three times over the
    pilot's ten open cells moved three of them, and one — `gen-001-s7`, "nothing fancy, we're
    not spending big" — came back `casual_sit_down` twice against a card that says
    `counter_service`: readable, stable, and one level off. A single run would have credited
    it (it did, on run 1) and put a cell in the answer key whose page narrows to the wrong
    value. Repetition is what makes that visible; the tally is what makes it decidable.
    """

    task_id: str
    field_id: str
    #: Runs where the reader read the field AND its levels included the card's value.
    agree: int = 0
    #: Runs where the reader read the field but its levels EXCLUDED the card's value. Worse
    #: than a refusal, and counted apart from one for that reason.
    oppose: int = 0
    #: Runs where the reader found nothing readable, or quoted text that was not on the page.
    silent: int = 0
    levels: tuple[str, ...] = ()
    reads_as: str = ""
    quote: str = ""
    messages: tuple[int, ...] = ()
    #: Which model(s) actually answered the targeted ask for this cell. `consensus` hardcoded
    #: `"model": ""`, so `credit.eligible` read an empty confirmer and wrote `confirmed_by=()`
    #: on every cell credited through the tally path. The gate was never wrong — a confirming
    #: read was still required, and cells without one were refused — but the credit could not
    #: say WHO confirmed it, which is what README's "every credit carries provenance — both
    #: reader models" promises and what the "every credit has a 2nd reader" falsifier checks.
    #: It failed that falsifier on all 23 credits of the 2026-08-21 pilot.
    models: tuple[str, ...] = ()

    @property
    def runs(self) -> int:
        return self.agree + self.oppose + self.silent

    @property
    def key(self) -> tuple[str, str]:
        return (self.task_id, self.field_id)

    @property
    def credits(self) -> bool:
        """A strict majority read it, and more runs agreed with the card than opposed it.

        Both halves are load-bearing. Majority alone would credit a cell read 2-1 *against*
        the card; `agree > oppose` alone would credit a cell read once in three runs.
        """
        return self.agree * 2 > self.runs and self.agree > self.oppose

    @property
    def verdict(self) -> str:
        if self.credits:
            return "credit"
        if self.oppose >= self.agree and self.oppose:
            return "opposes"
        return "split" if self.agree else "refused"


def tally(
    runs: Sequence[Sequence[Answer]], *, cards: Mapping[str, Mapping[str, Any]],
) -> dict[tuple[str, str], Tally]:
    """Fold repeated runs into one tally per cell.

    `runs` is one list of answers per repetition. Cells missing from a run are not counted as
    silence — a run that failed to reach a session says nothing about it, and treating an
    unreached session as a refusal is the same mistake free-recall key 3 made.
    """
    out: dict[tuple[str, str], Tally] = {}
    for answers in runs:
        for answer in answers:
            card = (cards.get(answer.task_id) or {}).get(answer.field_id)
            current = out.get(answer.key) or Tally(answer.task_id, answer.field_id)
            seen = current.models if answer.model in current.models else (
                current.models + ((answer.model,) if answer.model else ())
            )
            if not answer.confirms:
                out[answer.key] = replace(current, silent=current.silent + 1,
                                          models=seen,
                                          reads_as=current.reads_as or answer.reads_as)
                continue
            way = direction(answer, card)
            if way == OPPOSES:
                out[answer.key] = replace(
                    current, oppose=current.oppose + 1, models=seen,
                    levels=current.levels or answer.levels,
                    reads_as=answer.reads_as or current.reads_as,
                )
                continue
            out[answer.key] = replace(
                current, agree=current.agree + 1, models=seen,
                levels=answer.levels or current.levels,
                reads_as=answer.reads_as or current.reads_as,
                quote=current.quote or answer.quote,
                messages=current.messages or answer.messages,
            )
    return out


def consensus(tallies: Mapping[tuple[str, str], Tally]) -> dict[str, dict[str, Any]]:
    """The tallied credits, in the shape `credit.eligible(confirmations=...)` reads."""
    out: dict[str, dict[str, Any]] = {}
    for entry in tallies.values():
        if not entry.credits:
            continue
        record = out.setdefault(entry.task_id, {
            "task_id": entry.task_id, "model": "+".join(entry.models),
            "unscheduled": [], "beats": [], "contradictions": [],
        })
        if not record["model"]:
            record["model"] = "+".join(entry.models)
        record["unscheduled"].append({
            "field_id": entry.field_id, "quote": entry.quote,
            "messages": list(entry.messages), "strength": "narrowed",
            "levels": list(entry.levels), "votes": [entry.agree, entry.runs],
        })
    return out


def load_answers(path: Path) -> list[Answer]:
    """Answers as written by `scripts/confirm_key3.py`."""
    payload = json.loads(Path(path).read_text())
    return [Answer(**a) for a in payload.get("answers", [])]
