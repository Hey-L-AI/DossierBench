"""§7.3's grading instrument: three questions, and the parsers that read them.

Step 6's first deliverable. The question, the prompt and the parser were built and measured in
`probe_reauthor/` (ADR-0013, ADR-0014) and they ship here — the probe supplied the model and
the retry loop, and keeps them; what is measured is the instrument, and the instrument belongs
to the pipeline.

Three questions live here and **all three stay runnable** (`GRADERS`), on ADR-0015's precedent:
a question that produced published figures is not deleted when it is superseded, because that
would make its figures unreproducible.

* **`LICENSED`** — "which values does this transcript license?" Every published figure on this
  project before ADR-0021 was graded by it, and it is still what the distribution fields are
  asked, because ruling out a *superlative* is not a question a quote can answer.
* **`RULEOUT`** — §7.5's boundary protocol: rule levels out with a verbatim quote, derive the
  set from what survives. It exists because the judge pool disagrees about the **width** of the
  licensed set rather than its location, and this question takes the width decision away from
  the judge (ADR-0021, ADR-0022).
* **`RULEOUT_SPLIT`** — the shipping question (ADR-0023). The same two readings, asked in **two
  separate requests**, because a question's frame leaks across fields inside one reply. This is
  the configuration under which §7.5's release gate closes.

**The question is "which values does this transcript license?", never "what would you commit
to".** Asked for one value, a judge returns one value: on 86 sparse cells the schedule briefed
as narrowing-not-pinning, judges named the same value 67% of the time and were right 34% of the
time, and all 24 of the agreed-and-wrong ordinal answers were the other member of the two-level
interval the author was briefed to write. The judges narrow correctly and the narrowing is
discarded on the way out. Replacing the question on the same transcripts, byte for byte, took
§11's earned condition from 39% to 74% against a 50% floor.

Three rules from §7.3 are structural here rather than instructed:

1. **The grade vocabulary is never shown to the judge.** The judge reports a set; the band is
   arithmetic on the size of that set — one value `strong`, two or more `weak`, none
   `unevidenced`. A judge told that a two-element answer scores `weak` is selecting a band
   again by another route, which is the disposition this question exists to remove.
2. **An unreadable member invalidates its whole row, and is counted rather than repaired.**
   Dropping one unrecognized member of a two-value set silently converts `weak` into `strong`.
   That manufactures the over-commitment this question exists to measure, out of a spelling.
3. **Nothing here defaults to a grade.** An unparsed reply in a pipeline that defaults to
   `unevidenced` becomes a *wrong penalty* — that is the one band where an SUT that commits is
   charged with fabrication (§8.1) — distributed non-uniformly across judge models. Measured
   under the commitment grader at 23 of 80 gradings, 19 from a single model, purely from how it
   formats a number.

Nothing here calls a model. A runner supplies the invocation, the concurrency and the cache;
`parse_reply` is the whole boundary between a model's reply and the map.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Mapping, Sequence

from . import fields
from .evidence import Grade

# --- the system prompt -------------------------------------------------------

LICENSED_SYSTEM = """\
You read a conversation transcript and report, for each of a list of facts about the user, \
**which values the transcript leaves open**.

You are not being asked what the user is probably like, and you are not being asked for your \
best guess. You are reporting how much ambiguity is left: after reading this transcript, which \
values could a careful reader still be looking at?

For each field, list every value the transcript is consistent with.

  - If the transcript rules out everything but one value, list that one value.
  - If it narrows the field but two or more values are still live, LIST THEM ALL. This is the \
expected answer whenever a transcript gestures at something without settling it. It is not a \
failure to decide — it is the finding.
  - If the transcript gives you nothing to narrow with, return an empty list. Most fields in \
most transcripts say nothing at all. That is the expected answer, not a failure to find \
something. Do not list the whole vocabulary to mean this; return [].

Never list a single value as shorthand for "roughly here". Listing one value asserts that the \
transcript positively rules out every neighbouring value. If it does not rule them out, list \
them too.

Rules, all binding:

1. USE ONLY EVIDENCE ABOUT THAT FIELD. Do not narrow a field because a related field is \
evidenced. Traits that usually go together do not license each other here — some of these \
users are deliberately unusual, and inferring one field from another is the specific error \
this reading exists to avoid.
2. ATTRIBUTE CORRECTLY. Evidence about the user's friend, partner, colleague or family \
member is not evidence about the user.
3. WHEN IN DOUBT WHETHER A VALUE IS STILL LIVE, INCLUDE IT. Ruling a value out is the bigger \
claim, and it needs something in the transcript behind it.
4. Absence of a mention is an empty list, never a guess.

Output JSON only, no prose outside it:
{"fields": [{"f": "<field_id>", "open": ["<value>", ...], "maybe": ["<value>", ...], \
"why": "<<=15 words, only when `open` or `maybe` is non-empty>"}]}

Include every field you are given, exactly once. Use only values from that field's listed \
vocabulary, spelled exactly as given. `maybe` is used only by the fields marked LIST-VALUED \
below; leave it out everywhere else."""


def selected_fields(field_ids: Sequence[str] | None) -> tuple[fields.Field, ...]:
    """The fields one call asks about, in `ALL_FIELDS` order. `None`/empty means all of them.

    Declaration order is kept rather than the caller's, because the prompt is part of the
    instrument: two callers naming the same fields in different orders would produce two
    fingerprints and re-grade a cache that is not stale.
    """
    if not field_ids:
        return tuple(fields.ALL_FIELDS)
    wanted = set(field_ids)
    unknown = wanted - set(fields.BY_ID)
    if unknown:
        raise KeyError(f"not fields: {sorted(unknown)}")
    return tuple(f for f in fields.ALL_FIELDS if f.field_id in wanted)


def licensed_prompt(
    transcript: str, checkpoint: int, field_ids: Sequence[str] | None = None,
) -> str:
    """The §7.3 prefix question, asked as a licensed set.

    The transcript is the *prefix* through checkpoint `t`, never the whole timeline: the map is
    per-checkpoint and a judge shown sessions the SUT has not seen would grade evidence the SUT
    cannot have (§1.3).

    ``field_ids`` restricts the question to part of the schema, which is what makes a split call
    possible. It defaults to every field, so the prompt behind all 598 cached gradings is
    reproduced byte for byte.
    """
    lines = [
        f"TRANSCRIPT (sessions 1 to {checkpoint + 1} of a longer timeline):",
        "",
        transcript,
        "",
        "=== END TRANSCRIPT ===",
        "",
        "For every field below, list the values this transcript leaves open.",
        "",
    ]
    for field in selected_fields(field_ids):
        if field.metric == fields.METRIC_JACCARD:
            # A set-valued field cannot take a list of candidate *sets*, so the ambiguity is
            # reported per member instead: `open` is what the transcript establishes as in the
            # set, `maybe` is what it raises without establishing. One unsettled member is
            # enough to leave the field unsettled, which is the same rule the ordinals get.
            vocab = (
                f" — LIST-VALUED. `open`: members the transcript establishes. `maybe`: "
                f"members it raises without establishing. Vocabulary: "
                f"{', '.join(field.levels)}"
            )
        elif field.metric == fields.METRIC_ORDINAL and field.levels:
            vocab = f" — any of: {', '.join(field.levels)}"
        elif field.metric == fields.METRIC_EXACT:
            vocab = " — any of: true, false"
        elif field.metric == fields.METRIC_JSD:
            vocab = (
                f" — which bin could be the heaviest; any of: {', '.join(field.levels)}"
            )
        else:                                                     # pragma: no cover - guard
            vocab = ""
        lines.append(f"  {field.field_id}: {field.doc}{vocab}")
    return "\n".join(lines)


# --- §7.5's boundary protocol: the same reading, asked as a rule-out ---------
#
# The licensed-set question above leaves the *width* of the answer to the judge, and the pool
# does not share a width. Measured on the shipping arm and replicated on ADR-0020's independent
# re-grade (preregistered in the unreleased development tree): of the grade disagreements on the priced
# boundary, **76–81% are nested sets** — one judge's answer contains the other's — against 1.4%
# disjoint. On ordinal fields the distance between two judges' set centres is zero levels in 64
# of 68 disagreements. The judges agree about where the answer is; they cut the same latent
# ordering at different points, and the pool is a chain: haiku (mean 1.34) subset-of sonnet
# (1.78) subset-of opus (2.20).
#
# That is the one configuration κ cannot rescue. κ corrects for marginals, but two raters
# cutting one ordering at different points disagree on every cell between their cuts.
#
# So the width stops being chosen. For a closed vocabulary the judge rules levels **out**, with
# a verbatim quote for each, and the licensed set is what is left. There is no inclusion
# threshold to hold: the question is about the text ("is there something here incompatible with
# this level"), which a quote answers, rather than about the judge's confidence, which nothing
# does. It extends the move §7.3 already makes — the grade vocabulary is never shown, because a
# judge told a two-element answer scores `weak` is picking a band by another route — one level
# further, to the set itself.
#
# Set-valued fields keep the licensed shape: they carry 9–39 members and ruling out
# thirty-eight cuisines is not a question anyone can answer. What they get instead is the
# *routing* rule they never had. 12 of their 32 priced-boundary disagreements are between judges
# reporting the **identical** member set and splitting it differently across `open` and `maybe`,
# a threshold the prompt specified in nine words. It is now §5's move vocabulary, which already
# ranks exactly this distinction and ranks it for this reason.

RULEOUT_SYSTEM = """\
You read a conversation transcript and rule out what it makes impossible.

For each of a list of facts about the user you are shown the full range of values that fact can \
take. Your job is to say which of those values this transcript RULES OUT, and to quote the \
words that rule each one out.

You are not being asked what the user is probably like, which value fits best, or what you \
would guess. A value is ruled out only when the transcript contains something INCOMPATIBLE \
with it. Another value being more likely is not incompatibility.

For most facts in most transcripts you will rule out nothing at all. That is the expected \
answer, not a failure to find something. Return an empty list and move on.

For every value you rule out, give a VERBATIM quote from the transcript — the user's actual \
words, copied exactly — that is incompatible with that value. **If you cannot quote something, \
you cannot rule it out.** A rule-out with no quote will be discarded.

Rules, all binding:

1. USE ONLY EVIDENCE ABOUT THAT FACT. Do not rule out a value because a related fact is \
evidenced. Traits that usually go together do not license each other here — some of these users \
are deliberately unusual, and inferring one fact from another is the specific error this reading \
exists to avoid.
2. ATTRIBUTE CORRECTLY. Evidence about the user's friend, partner, colleague or family member \
is not evidence about the user, and cannot rule anything out about the user.
3. A NEAR MISS IS NOT A RULING OUT. If the transcript points somewhere and a neighbouring value \
is merely less likely, that neighbour stays. Ruling out is the strong claim and needs the \
transcript behind it.
4. ABSENCE RULES OUT NOTHING. A fact the transcript never touches has every value still \
standing, and your answer for it is an empty list.

LIST-VALUED facts are different — they have too many possible members to rule out one at a \
time, so for those you report what the transcript RAISES instead, split by how the user met it:

  - `open`: members the user REQUESTED, CHOSE, or REJECTED. The user acted on it.
  - `maybe`: members that arrive any other way — reacted to, mentioned in a story, suggested by \
a companion, raised and left unresolved.
  - members the transcript never raises go in neither list.

DISTRIBUTION facts are different again. They ask which option is the user's HEAVIEST — the one \
they do most — and that is a comparison between options, not a claim any single quote can \
contradict. So do not rule those out either. List in `open` every option that could be the \
heaviest for this user, and return an empty list when the transcript gives you no way to \
compare them at all. Listing every option means the same as listing none: return [].

Output JSON only, no prose outside it:
{"fields": [{"f": "<field_id>", "out": [{"v": "<value>", "q": "<verbatim quote>"}, ...], \
"open": ["<member>", ...], "maybe": ["<member>", ...]}]}

Use `out` for ordinary facts, `open`/`maybe` for the facts marked LIST-VALUED, and `open` alone \
for the facts marked DISTRIBUTION; leave the keys you are not using out entirely. Include every \
fact you are given, exactly once. Use only values from that fact's listed range, spelled exactly \
as given."""


def ruleout_prompt(
    transcript: str, checkpoint: int, field_ids: Sequence[str] | None = None,
) -> str:
    """The §7.3 prefix question, asked as a rule-out over a closed range.

    Same prefix rule as `licensed_prompt`: the transcript is the prefix through checkpoint `t`
    and never the whole timeline, because a judge shown sessions the SUT has not seen grades
    evidence the SUT cannot have (§1.3).
    """
    lines = [
        f"TRANSCRIPT (sessions 1 to {checkpoint + 1} of a longer timeline):",
        "",
        transcript,
        "",
        "=== END TRANSCRIPT ===",
        "",
        "For every fact below, list the values this transcript rules out, each with the quote "
        "that rules it out.",
        "",
    ]
    for field in selected_fields(field_ids):
        if field.metric == fields.METRIC_JACCARD:
            vocab = (
                f" — LIST-VALUED, report `open`/`maybe`. Possible members: "
                f"{', '.join(field.levels)}"
            )
        elif field.metric == fields.METRIC_JSD:
            vocab = (
                f" — DISTRIBUTION, report `open`. Which could be the heaviest; options: "
                f"{', '.join(field.levels)}"
            )
        elif field.metric == fields.METRIC_EXACT:
            vocab = " — full range: true, false"
        else:
            vocab = f" — full range: {', '.join(field.levels)}"
        lines.append(f"  {field.field_id}: {field.doc}{vocab}")
    return "\n".join(lines)


# --- ADR-0023: the frame leaks across fields, so the call is split ------------
#
# ADR-0022 carved the distribution fields out of the rule-out *instruction* and they still came
# back empty **48%** of the time against the 18% the same three models produce when the whole
# reply is a licensed set. The carve-out is a paragraph; the disposition is not. A judge holding
# a rule-out frame applies it to fields the prompt asked it to do something else with, which is
# the fourth time on this project that prose has lost to structure.
#
# So the frame stops being shared. The distribution fields are asked in their own request, under
# `LICENSED_SYSTEM` **verbatim** — that is the prompt the 18% baseline was measured under, and an
# edited copy would leave the comparison with nothing to be a comparison against. The rule-out
# request then never mentions them at all: `selected_fields` drops their lines from the prompt,
# and the two paragraphs that briefed them come out of the system prompt below.


def _excise(text: str, needle: str, replacement: str = "") -> str:
    """Remove or replace an exact span, and raise if it is not there.

    `str.replace` on a needle that has drifted by one character is a silent no-op: the edited
    prompt would be byte-identical to the one it was supposed to differ from, the fingerprint
    would match, and the split arm would run the unsplit question under the split arm's name.
    That failure is invisible in every output this project publishes, so it is checked at import
    rather than in a test — a prompt that cannot be built is better than one that is quietly the
    wrong prompt.
    """
    if needle not in text:
        raise ValueError(f"cannot excise a span that is not present: {needle[:60]!r}...")
    return text.replace(needle, replacement)


#: The paragraph that briefs the distribution fields inside the rule-out request. In the split
#: question there are no distribution fields in that request, so briefing them would be briefing
#: a frame for fields the judge is not shown — which is the leak, arriving from the other side.
_DISTRIBUTION_BRIEF = """\
DISTRIBUTION facts are different again. They ask which option is the user's HEAVIEST — the one \
they do most — and that is a comparison between options, not a claim any single quote can \
contradict. So do not rule those out either. List in `open` every option that could be the \
heaviest for this user, and return an empty list when the transcript gives you no way to \
compare them at all. Listing every option means the same as listing none: return [].

"""

_FORMAT_ALL_SHAPES = """\
Use `out` for ordinary facts, `open`/`maybe` for the facts marked LIST-VALUED, and `open` alone \
for the facts marked DISTRIBUTION; leave the keys you are not using out entirely."""

_FORMAT_TWO_SHAPES = """\
Use `out` for ordinary facts and `open`/`maybe` for the facts marked LIST-VALUED; leave the \
keys you are not using out entirely."""

#: `RULEOUT_SYSTEM` with the distribution brief removed. Derived rather than copied: two prompt
#: literals differing by one paragraph drift, and the one that drifts is whichever is not the one
#: being read at the time.
RULEOUT_SPLIT_SYSTEM = _excise(
    _excise(RULEOUT_SYSTEM, _DISTRIBUTION_BRIEF), _FORMAT_ALL_SHAPES, _FORMAT_TWO_SHAPES,
)


#: A rule-out with no quote is discarded rather than honoured, and the level stays live.
#:
#: Declared before the run (in a preregistration kept in the unreleased development tree) because it is a substantive
#: rule and not a formatting detail. The instruction "if you cannot quote something you cannot
#: rule it out" is prose, and this project has measured prose failing twice on this exact
#: pipeline — §7.3 already said "absence of a mention is `unevidenced`, never a value" and got
#: 35% invention anyway. Enforcing it in the parser is what makes it a rule.
#:
#: The direction is the safe one: discarding widens the surviving set, which moves a cell
#: toward `weak` and away from the commitment the whole boundary protocol exists to stop being
#: free. A judge that quotes nothing gets a full-vocabulary reading, which the collapse rule
#: below reads as `unevidenced` — loud, and visible in the published counts rather than silent.
#:
#: What is enforced is the *presence* of a quote, which is unambiguous. Whether the quote
#: actually occurs in the transcript is **published, not enforced**: matching is fuzzy across
#: whitespace, ellipsis and light paraphrase, and a strict matcher would discard sound
#: rule-outs on formatting. `verify_quotes` is the published guard.
REQUIRE_QUOTE: bool = True


# --- the parser --------------------------------------------------------------


class LicensedParseError(ValueError):
    """A licensed set that cannot be read against the field's closed vocabulary.

    Raised rather than repaired. Dropping one unrecognized member of a two-value set silently
    turns `weak` into `strong` — it manufactures exactly the over-commitment this grader exists
    to measure, out of a spelling. So an unreadable member invalidates its whole row, which is
    then counted and published like any other unparsed record.
    """


@dataclass(frozen=True)
class LicensedEntry:
    """One field's reading of one transcript prefix, by one judge.

    ``grade`` is derived, never reported: it is arithmetic on the size of ``open`` (and, for a
    set-valued field, on whether ``maybe`` is empty). ``value`` is populated only where the set
    has collapsed to a commitment, which is the only place a value is a claim rather than a
    member of an ambiguity.
    """

    field_id: str
    grade: Grade
    value: Any = None
    open: tuple[str, ...] = ()
    maybe: tuple[str, ...] = ()
    why: str = ""
    #: (value, quote) for every level the rule-out grader removed. Empty under the licensed
    #: question, which never asks. Carried rather than discarded because it is the only
    #: evidence a rule-out was earned, and `verify_quotes` is scored on it.
    ruled_out: tuple[tuple[str, str], ...] = ()
    #: Rule-outs discarded for carrying no quote, per `REQUIRE_QUOTE`. Counted per judge:
    #: a model that rules out freely without quoting is a distinct failure from one that
    #: quotes badly, and the two have different remedies.
    unquoted: tuple[str, ...] = ()
    #: Response keys the judge populated that this field's shape does not license — see
    #: `off_protocol_keys`. Recorded on `unquoted`'s precedent: the content is still dropped,
    #: because reading a key the prompt forbade would be answering a question nobody asked, but
    #: dropping it *silently* is what let a 32%-of-rows behaviour go unseen through four ADRs.
    off_protocol: tuple[str, ...] = ()

    @property
    def reported(self) -> tuple[str, ...]:
        """Everything the judge says is still live. The set `contains_gold` is read over."""
        return (*self.open, *self.maybe)

    @property
    def set_size(self) -> int:
        """Published beside every accuracy figure. "The reported evidence contains gold" is
        trivially satisfiable by reporting everything, so the two numbers only mean something
        read together — ADR-0014's 59% is 59% at a mean set size of 1.62."""
        return len(self.reported)

    def to_json(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "grade": self.grade.name.lower(), "value": self.value,
            "open": list(self.open), "maybe": list(self.maybe), "why": self.why,
        }
        # Written only when non-empty, so a licensed record is byte-identical to what the
        # probes wrote. `test_replay.py` asserts every one of the 598 cached gradings comes
        # back unchanged, and a new key on every row would break that for no gain.
        if self.ruled_out:
            record["ruled_out"] = [list(pair) for pair in self.ruled_out]
        if self.unquoted:
            record["unquoted"] = list(self.unquoted)
        if self.off_protocol:
            record["off_protocol"] = list(self.off_protocol)
        return record

    @classmethod
    def from_json(cls, field_id: str, row: dict) -> LicensedEntry:
        """Read a record written by `to_json`, or by the probe runners that predate it.

        The probe wrote the same five keys with a lowercase grade string, so a cached grading
        loads without a migration. This is what lets step 6's pipeline be validated by replaying
        598 gradings that were paid for before it existed.
        """
        return cls(
            field_id=field_id,
            grade=Grade.parse(str(row["grade"])),
            value=row.get("value"),
            open=tuple(row.get("open") or ()),
            maybe=tuple(row.get("maybe") or ()),
            why=str(row.get("why") or ""),
            ruled_out=tuple(
                (str(pair[0]), str(pair[1])) for pair in (row.get("ruled_out") or ())
            ),
            unquoted=tuple(row.get("unquoted") or ()),
            off_protocol=tuple(row.get("off_protocol") or ()),
        )


def _vocabulary(field: fields.Field) -> tuple[str, ...]:
    if field.metric == fields.METRIC_EXACT:
        return ("true", "false")
    return tuple(field.levels or ())


def _canonical(raw: Any) -> str:
    return str(raw).strip().lower().replace("-", "_").replace(" ", "_")


def normalize_member(field: fields.Field, raw: Any) -> str:
    """Map one reported value onto the field's vocabulary, or raise.

    Accepts the shapes models actually use — case, surrounding space, a hyphen where the
    vocabulary has an underscore — because format variance is not a different answer.
    """
    if isinstance(raw, bool):
        raw = "true" if raw else "false"
    if raw is None:
        raise LicensedParseError("value is null, not a token from the vocabulary")
    key = _canonical(raw)
    for member in _vocabulary(field):
        # Both sides are canonicalized. The vocabularies are not written to one convention —
        # ordinal levels use underscores and the `visit_time_distribution` bins use hyphens —
        # so folding only the reply would have failed every distribution row into `unparsed`.
        if key == _canonical(member):
            return member
    # The bare-index guard fires only *after* the vocabulary lookup, and the order is
    # load-bearing: `party_size_distribution`'s bins are `1`, `2`, `3-5`, `6+`, so a digit is a
    # legitimate token there and an index nowhere. Guarding first cost that field all 40 of its
    # cells on the first licensed run — the digits were rejected as indices, the row went to
    # `unparsed`, and the field went to `missing`.
    if isinstance(raw, (int, float)) or str(raw).strip().isdigit():
        raise LicensedParseError(
            f"value {raw!r} is a bare index; {field.field_id} has no index convention"
        )
    raise LicensedParseError(f"{raw!r} is not in {field.field_id}'s vocabulary")


#: Which response keys each question licenses each field shape to populate, straight from the
#: two system prompts:
#:
#: * `LICENSED_SYSTEM` — "``maybe`` is used only by the fields marked LIST-VALUED below; leave
#:   it out everywhere else."
#: * `RULEOUT_SYSTEM` — "Use ``out`` for ordinary facts, ``open``/``maybe`` for the facts marked
#:   LIST-VALUED, and ``open`` alone for the facts marked DISTRIBUTION; leave the keys you are
#:   not using out entirely."
#:
#: So a `maybe` on a distribution field is not a hedge the schema invited — it is a reply that
#: did not follow the instruction, and the parser drops it. Both statements were already true;
#: what is new is that the drop is now counted.
_LICENSED_KEYS: dict[str, frozenset[str]] = {
    fields.METRIC_JACCARD: frozenset({"open", "maybe"}),
}
_RULEOUT_KEYS: dict[str, frozenset[str]] = {
    fields.METRIC_JACCARD: frozenset({"open", "maybe"}),
    fields.METRIC_JSD: frozenset({"open"}),
}
#: Every key either question can carry, so a typo'd or invented key is caught too rather than
#: being invisible for not appearing on a list of things to look for.
_ALL_ANSWER_KEYS = frozenset({"open", "maybe", "out"})


def off_protocol_keys(field: fields.Field, row: dict, *, ruleout: bool) -> tuple[str, ...]:
    """Answer keys this row populated that its field's shape does not license.

    Only **non-empty** values count. An empty `maybe: []` carries no information and costs the
    reading nothing; a populated one is a claim the judge made and the parser is about to throw
    away, which is the thing worth knowing about. Ordered for a stable record.
    """
    allowed = (_RULEOUT_KEYS if ruleout else _LICENSED_KEYS).get(
        field.metric, frozenset({"out"} if ruleout else {"open"})
    )
    return tuple(sorted(
        key for key in _ALL_ANSWER_KEYS - allowed if row.get(key)
    ))


def parse_licensed(field: fields.Field, row: dict) -> LicensedEntry:
    """Read one row into a `LicensedEntry`, deriving the grade from the size of the set.

    The judge supplies the set; the band is arithmetic on its size and is never asked for.
    """
    def members(key: str) -> list[str]:
        raw = row.get(key)
        if raw is None:
            return []
        if isinstance(raw, (str, bool)):                      # a bare value, not a list
            raw = [raw]
        if not isinstance(raw, list):
            raise LicensedParseError(f"`{key}` is {type(raw).__name__}, not a list")
        seen: list[str] = []
        for item in raw:
            member = normalize_member(field, item)
            if member not in seen:
                seen.append(member)
        return seen

    is_set_valued = field.metric == fields.METRIC_JACCARD
    opened = members("open")
    maybe = members("maybe") if is_set_valued else []

    # A judge that lists the entire vocabulary has narrowed nothing, which is `unevidenced` —
    # the same state as the empty list the prompt asks for. Both shapes are in the replies and
    # they mean one thing; reading the full list as `weak` would credit the grader with finding
    # ambiguity it explicitly declined to find. Not applied to set-valued fields, where naming
    # every member is a real (if unlikely) claim about the set rather than a shrug.
    if not is_set_valued and len(opened) > 1 and len(opened) == len(_vocabulary(field)):
        opened = []

    if is_set_valued:
        if not opened and not maybe:
            grade, value = Grade.UNEVIDENCED, None
        elif maybe:
            grade, value = Grade.WEAK, None
        else:
            grade, value = Grade.STRONG, list(opened)
    else:
        if not opened:
            grade, value = Grade.UNEVIDENCED, None
        elif len(opened) == 1:
            grade, value = Grade.STRONG, opened[0]
        else:
            grade, value = Grade.WEAK, None

    return LicensedEntry(
        field_id=field.field_id, grade=grade, value=value,
        open=tuple(opened), maybe=tuple(maybe), why=str(row.get("why") or ""),
        off_protocol=off_protocol_keys(field, row, ruleout=False),
    )


#: Metrics that keep the licensed shape under the rule-out grader, and the two different
#: reasons they do. Both carve-outs are exclusions from *the rule-out*, not from the benchmark.
#:
#: * `METRIC_JACCARD` — carved out on **size**. A rule-out over 39 cuisines is not a question
#:   anyone can answer, and asking it would trade one unanswerable question for another.
#: * `METRIC_JSD` — carved out on **kind**, and this one was measured rather than foreseen
#:   (ADR-0021). These fields ask which option is the *heaviest*, which is a comparison between
#:   options rather than a claim about the user, and no quote can contradict a superlative.
#:   "The transcript never shows a weekday lunch" is weak evidence that weekday lunch is not the
#:   mode — enough for a judge to rule it out — and doing that to each bin in turn leaves
#:   nothing. It did: **45 of 48** priced-boundary JSD cells came back with an empty surviving
#:   set, against 18% under the licensed question, and three judges emptying a field all agree
#:   it says nothing. That is agreement bought with vacuity, and it inflated the arm's headline
#:   κ from +0.42/+0.37 to +0.55/+0.52.
LICENSED_SHAPE_METRICS: frozenset[str] = frozenset({fields.METRIC_JACCARD, fields.METRIC_JSD})


def parse_ruleout(field: fields.Field, row: dict) -> LicensedEntry:
    """Read one rule-out row. The licensed set is the vocabulary minus what was ruled out.

    Fields in `LICENSED_SHAPE_METRICS` never take a rule-out and fall through to
    `parse_licensed`, graded exactly as the licensed question grades them. What changes for
    them is the *instruction* — the set-valued routing rule — and not the shape, which keeps
    them comparable across the two arms.
    """
    if field.metric in LICENSED_SHAPE_METRICS:
        return parse_licensed(field, row)

    vocabulary = _vocabulary(field)
    ruled: list[tuple[str, str]] = []
    unquoted: list[str] = []
    raw = row.get("out")
    if raw is None:
        raw = []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, list):
        raise LicensedParseError(f"`out` is {type(raw).__name__}, not a list")

    for item in raw:
        if isinstance(item, dict):
            value, quote = item.get("v", item.get("value")), item.get("q", item.get("quote"))
        else:
            # A bare token is a rule-out with no quote. Read, then discarded by the rule
            # below — never silently honoured, which is the whole point of asking for a quote.
            value, quote = item, None
        member = normalize_member(field, value)
        text = str(quote or "").strip()
        if REQUIRE_QUOTE and not text:
            if member not in unquoted:
                unquoted.append(member)
            continue
        if member not in [m for m, _q in ruled]:
            ruled.append((member, text))

    removed = {member for member, _quote in ruled}
    live = [level for level in vocabulary if level not in removed]

    # Three readings collapse to `unevidenced`, and each is a different thing the judge said:
    #   * nothing ruled out — the transcript never touched this fact, which is rule 4's
    #     expected answer and the overwhelming majority of cells;
    #   * everything ruled out — the judge has contradicted itself, and a band derived from an
    #     empty set would be `strong` on no value at all;
    # both leave the scorer with no evidence, which is what `unevidenced` means. The middle
    # readings are the band: one survivor is a commitment, two or more is the weak band. This
    # is the same arithmetic `parse_licensed` applies to the size of the reported set, and the
    # full-vocabulary case is the same collapse rule it already carries.
    if not live or len(live) == len(vocabulary):
        grade, value_out, opened = Grade.UNEVIDENCED, None, []
    elif len(live) == 1:
        grade, value_out, opened = Grade.STRONG, live[0], live
    else:
        grade, value_out, opened = Grade.WEAK, None, live

    return LicensedEntry(
        field_id=field.field_id, grade=grade, value=value_out,
        open=tuple(opened), maybe=(), why=str(row.get("why") or ""),
        ruled_out=tuple(ruled), unquoted=tuple(unquoted),
        off_protocol=off_protocol_keys(field, row, ruleout=True),
    )


def verify_quotes(entry: LicensedEntry, transcript: str) -> tuple[int, int]:
    """`(quotes found in the transcript, quotes checked)` for one entry.

    §7.5's new guard, and it exists because the treatment created something checkable that the
    licensed question never had. A judge that rules levels out with invented quotes would raise
    judge–judge agreement while destroying the map, and nothing else in this pipeline could see
    it — the same shape as the false-evidence floor `calibration.py` was rewritten to add.

    Published rather than enforced, and normalized rather than exact: models re-wrap
    whitespace, trim to an ellipsis and drop a trailing clause, and a matcher strict about that
    would report invention where there is only formatting. Non-verbatim quoting is a real but
    milder failure, and conflating the two would hide the one that matters.
    """
    haystack = _squash(transcript)
    found = 0
    for _value, quote in entry.ruled_out:
        needle = _squash(quote)
        if needle and needle in haystack:
            found += 1
    return found, len(entry.ruled_out)


_ELLIPSIS = re.compile(r"\.\.\.|…|\[\.\.\.\]")
_NONWORD = re.compile(r"[^a-z0-9 ]+")
_SPACE = re.compile(r"\s+")


def _squash(text: str) -> str:
    """Fold the differences a quote survives: case, punctuation, wrapping, smart quotes.

    An elided quote is checked on its **longest** run, not reassembled: "I want X ... not Y" is
    two claims joined by the judge, and matching the pair against the transcript would accept a
    join the transcript never made.
    """
    longest = max(_ELLIPSIS.split(text or ""), key=len, default="")
    folded = _NONWORD.sub(" ", longest.lower())
    return _SPACE.sub(" ", folded).strip()


# --- the two questions, as one seam ------------------------------------------


@dataclass(frozen=True)
class Grader:
    """One §7.3 question: its system prompt, its prompt builder, its row parser, and the fields
    it is asked about.

    A named seam rather than a branch, because every question has to stay runnable. ADR-0015's
    precedent is explicit about why: the interval brief was switched off and not deleted, since
    three ADRs rest on text it produced and a revert that makes them unreproducible destroys its
    own evidence. The same holds here for grades — every published figure on this project was
    graded by `LICENSED`, and a treatment that replaced it in place would strand all of them.

    A grader may be **more than one call**. `extra_passes` carries the further questions, each
    with its own system prompt and its own slice of the schema, and a runner sends one request
    per pass. The schema is partitioned across them: nothing is asked twice and nothing is
    dropped, which `test_split.py` checks rather than assumes.
    """

    name: str
    system: str
    build_prompt: Any
    parse_row: Any
    #: The fields this call asks about. Empty means the whole schema, which is what every
    #: single-call grader wants and what keeps the cached instrument fingerprints valid.
    field_ids: tuple[str, ...] = ()
    #: Further calls, asked separately over their own fields. Empty for a single-call grader.
    extra_passes: tuple[Grader, ...] = ()

    @property
    def passes(self) -> tuple[Grader, ...]:
        """Every call this grader makes, this one first. One element for a single-call grader,
        so a caller that loops over `passes` needs no branch for the common case."""
        return (self, *self.extra_passes)

    @property
    def scope(self) -> tuple[str, ...]:
        """The field ids this one call covers, resolved against the schema."""
        return tuple(f.field_id for f in selected_fields(self.field_ids))


LICENSED = Grader("licensed", LICENSED_SYSTEM, licensed_prompt, parse_licensed)
RULEOUT = Grader("ruleout", RULEOUT_SYSTEM, ruleout_prompt, parse_ruleout)

#: The fields the rule-out question is semantically sound for: everything whose levels are
#: mutually exclusive claims a quote can contradict, plus the set-valued fields, which keep the
#: licensed *shape* but take the rule-out request's routing instruction (ADR-0021).
RULEOUT_SCOPE: tuple[str, ...] = tuple(
    f.field_id for f in fields.ALL_FIELDS if f.metric != fields.METRIC_JSD
)

#: The distribution fields, asked on their own under the licensed question (ADR-0023).
DISTRIBUTION_SCOPE: tuple[str, ...] = tuple(
    f.field_id for f in fields.ALL_FIELDS if f.metric == fields.METRIC_JSD
)

#: §7.5's boundary protocol, with the frame no longer shared. Two calls per cell: the rule-out
#: over everything it fits, and the three distribution fields under `LICENSED_SYSTEM` **verbatim**
#: — the prompt their 18% empty-set baseline was measured under, unedited so that the baseline
#: still means something. The second pass is `LICENSED` with a narrowed scope and nothing else
#: changed, which is the whole of the treatment.
RULEOUT_SPLIT = Grader(
    "ruleout-split", RULEOUT_SPLIT_SYSTEM, ruleout_prompt, parse_ruleout,
    field_ids=RULEOUT_SCOPE,
    extra_passes=(
        Grader(
            "ruleout-split:distribution", LICENSED_SYSTEM, licensed_prompt, parse_licensed,
            field_ids=DISTRIBUTION_SCOPE,
        ),
    ),
)

GRADERS: dict[str, Grader] = {g.name: g for g in (LICENSED, RULEOUT, RULEOUT_SPLIT)}


# --- diagnostic: does the distribution pass need its own bar? ----------------
#
# The split's distribution pass asks under `LICENSED_SYSTEM` verbatim (test above), which never
# mentions DISTRIBUTION fields on its own terms — it folds "which bin could be the heaviest"
# into the same "list everything the transcript is consistent with" framing built for plain
# elimination. Measured across the dataset: `open` comes back empty on distribution fields at
# 8.5-8.7% for opus/sonnet, 24.0% for haiku, and 78.6-82.5% for two independent judges outside
# the Claude family (openai/gpt-5.6-luna, google/gemini-3.7-flash) — the same instruction, read
# as a far higher bar by every model that isn't Claude.
#
# `DISTRIBUTION_TIGHTENED` is not wired into `RULEOUT_SPLIT` and is not in `GRADERS`: it is not
# the shipped protocol, it is the candidate a validation run measures before anyone decides
# whether it becomes one. Swapping it in would need a fresh instrument fingerprint (a full
# re-grade of the distribution pass) and a decision this file does not make on its own.
#
# **Read this next, before extending anything below (ADR-0028, 2026-08-16).** The tightened bar
# was measured at dataset scale (ADR-0027) and helps without closing the gate. A fourth arm then
# changed the question's *shape* rather than its wording — `extraction.py` asks each session for
# its concrete occasions and does the aggregation in code — and took the same six pairs to
# +0.51…+0.66 with four established above the floor. Two consequences for this file:
#
#   1. A fifth wording of this prompt is very unlikely to be the right next move. Four have now
#      been tried; each helped and none was enough, which is the signature of a question that is
#      wrong rather than a prompt that is badly written.
#   2. The residual is not in this file at all. `transcript._distribution_instruction` briefs
#      every beat with the distribution's *heaviest* bin and `schedule.Beat` carries no target,
#      so the authored text holds a point mass where gold holds 2.43 buckets. No wording here can
#      recover a shape the transcript never carried.

_DISTRIBUTION_TIGHTENED_ANCHOR = (
    'Never list a single value as shorthand for "roughly here". Listing one value asserts that '
    "the transcript positively rules out every neighbouring value. If it does not rule them "
    "out, list them too."
)

_DISTRIBUTION_TIGHTENED_ADDITION = """

DISTRIBUTION fields ask something different from the rest: not "what's ruled out," but which \
option is the user's HEAVIEST — the one they do most. That is a comparison, and it needs its \
own bar. A single anecdote, a stated preference, or one described instance is enough to put an \
option in `open` — you do not need repeated evidence or an explicit ranking, the same as any \
other field. Return an empty list only when the transcript never touches this dimension of the \
user's life at all; if it gives you anything to reason from, `open` is non-empty. Listing every \
option is the same as listing none, though: if nothing distinguishes the options from each \
other, return [] rather than all of them."""

#: `LICENSED_SYSTEM` plus one paragraph. Built by insertion rather than written out separately,
#: for the same reason `RULEOUT_SPLIT_SYSTEM` is built by excision: two independently-typed
#: prompt literals drift, and `_excise`'s anchor check means a drifted anchor fails loudly at
#: import instead of silently building the old prompt under the new name.
DISTRIBUTION_TIGHTENED_SYSTEM = LICENSED_SYSTEM.replace(
    _DISTRIBUTION_TIGHTENED_ANCHOR,
    _DISTRIBUTION_TIGHTENED_ANCHOR + _DISTRIBUTION_TIGHTENED_ADDITION,
)
if DISTRIBUTION_TIGHTENED_SYSTEM == LICENSED_SYSTEM:                    # pragma: no cover - guard
    raise ValueError("distribution-tightened anchor not found in LICENSED_SYSTEM")

#: Single-pass, scoped to the distribution family only — the same shape as `RULEOUT_SPLIT`'s
#: second pass, so a validation run can be compared cell-for-cell against it.
DISTRIBUTION_TIGHTENED = Grader(
    "distribution-tightened", DISTRIBUTION_TIGHTENED_SYSTEM, licensed_prompt, parse_licensed,
    field_ids=DISTRIBUTION_SCOPE,
)

#: Which fields' **omitted rows** may be read as `unevidenced`, per grader (ADR-0021
#: decision 5). Under the rule-out question every field starts with its whole vocabulary
#: live, so "no row" and `"out": []` are the same claim and filling is a reading rather than
#: an inference. It is **not** available under the licensed question, where an empty `open`
#: list is a positive assertion — so on the split grader the rule applies to the rule-out
#: pass's fields and to nothing else. Filling the distribution fields there would manufacture
#: exactly the empty-set reading the split call exists to remove.
#:
#: `None` means every field; absence from this table means the grader licenses no filling.
FILLABLE: dict[str, tuple[str, ...] | None] = {
    RULEOUT.name: None,
    RULEOUT_SPLIT.name: RULEOUT_SCOPE,
}

#: What a filled row asserts: evidence was looked for and none survives.
EMPTY_ROW: dict[str, Any] = {
    "grade": "unevidenced", "value": None, "open": [], "maybe": [], "why": "",
}


def fill_omitted(
    record: Mapping[str, Any], scope: tuple[str, ...] | None,
) -> dict[str, Any]:
    """A grading record with its omitted rows read as `unevidenced`, within `scope`.

    Promoted out of `runs/compare_boundary.py`, where it was the reading rule behind
    ADR-0021 through ADR-0023 while living in an analysis script. That was safe for three
    arms of one probe and is not safe for a dataset: a second reader that re-implemented it
    slightly differently would move the release gate without touching a grade, and this
    project has already recorded what an unparsed-versus-omitted confusion costs (19 of 23
    abstentions from one model turned out to be format variance).

    Fields outside `scope` keep their place in `missing`, which is the count §7.5 scores the
    instrument on. Nothing already graded is overwritten.
    """
    filled = dict(record)
    grades = dict(record.get("grades") or {})
    allowed = None if scope is None else set(scope)
    missing: list[str] = []
    for field_id in record.get("missing") or ():
        if allowed is not None and field_id not in allowed:
            missing.append(field_id)
            continue
        grades.setdefault(field_id, dict(EMPTY_ROW))
    filled["grades"] = grades
    filled["missing"] = missing
    return filled


def fill_for(record: Mapping[str, Any]) -> dict[str, Any]:
    """`fill_omitted` with the scope taken from the record's own grader.

    The record names the instrument that produced it, so a reader does not have to know which
    arm it is holding — which is the mistake the table above exists to prevent once more than
    one grader's records are on disk at the same time, as they now are.
    """
    grader = str(record.get("grader") or LICENSED.name)
    if grader not in FILLABLE:
        return dict(record)
    return fill_omitted(record, FILLABLE[grader])


# --- reading a whole reply ---------------------------------------------------

_FENCE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)
_FIELD_RE = re.compile(
    r'"(\w+)"\s*:\s*("(?:[^"\\]|\\.)*"|\[[^\]]*\]|\{[^}]*\}|null|true|false|-?\d+(?:\.\d+)?)'
)


def parse_json(text: str) -> Any:
    """Parse a reply, salvaging the leading fields of a truncated object.

    Truncation is not random: it falls on the records the judge had most to say about, which
    are the contested ones — exactly the cells the weak band is measured over.
    """
    cleaned = _FENCE.sub("", text).strip()
    for candidate in (cleaned, *_balanced_objects(cleaned), _outermost_object(cleaned)):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    salvaged: dict[str, Any] = {}
    for key, raw in _FIELD_RE.findall(cleaned):
        try:
            salvaged[key] = json.loads(raw)
        except json.JSONDecodeError:
            continue
    if salvaged:
        salvaged["_truncated"] = True
        return salvaged
    raise ValueError(f"no JSON object in reply: {text[:200]!r}")


def _outermost_object(text: str) -> str | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else None


def _balanced_objects(text: str) -> list[str]:
    """Every brace-balanced top-level `{...}` span, **last first**.

    `_outermost_object` matches greedily from the first `{` to the last `}`, which is one
    object only when the reply contains one. A judge that emits a broken object, notices, and
    re-emits a correct one produces two — and the greedy span then covers both plus the prose
    between them, so it does not parse either. Measured on a licensed run: one reply ended an
    object with the JavaScript expression `.slice(0,2)`, wrote *"Wait — I must output valid
    JSON only"*, and emitted a complete valid object after it. Both parse attempts failed, the
    regex salvage returned a flat dict with no `fields` key, and the record reported **24
    missing fields and no error at all**.

    Last-first because a self-correction supersedes what it corrects. Quotes and escapes are
    tracked so a brace inside a `why` string cannot end a span.
    """
    spans: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth:
                depth -= 1
                if depth == 0 and start >= 0:
                    spans.append(text[start:index + 1])
    return spans[::-1]


@dataclass(frozen=True)
class Reply:
    """One judge's whole answer for one (subject, checkpoint), after parsing.

    `unparsed` and `missing` are carried rather than folded into the grades, because §7.5
    scores a judge on the items it answered and says so in the denominator: a judge must not be
    able to improve its calibration by answering less, and it must not be charged a fabrication
    penalty for a formatting habit either.
    """

    entries: dict[str, LicensedEntry] = dc_field(default_factory=dict)
    unparsed: tuple[dict[str, Any], ...] = ()
    missing: tuple[str, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.unparsed and not self.missing


def parse_reply(raw: str, grader: Grader = LICENSED) -> Reply:
    """Read one judge reply into grades, unparsed rows, and an error.

    Split from any invocation so a parser fix can be applied to replies already on disk. A
    parser fix is not a new measurement — the model output is unchanged — and ADR-0010 is the
    precedent: running a new rule back over grades that already exist cost nothing and changed
    the reading twice.

    ``grader`` defaults to `LICENSED` so every existing caller and all 598 replayed gradings
    take the identical path they took before the rule-out question existed.

    One reply is one **pass**, so `missing` is computed over that pass's scope rather than over
    the schema: a distribution-only request is not missing the twenty fields it never asked
    about. `merge_replies` recomputes it over the whole schema once the passes are back together.
    """
    entries: dict[str, LicensedEntry] = {}
    unparsed: list[dict[str, Any]] = []
    error = None
    scope = grader.scope
    try:
        reply = parse_json(raw)
        # A judge that returns the bare array instead of {"fields": [...]} has answered in a
        # shape with exactly one reading — a list of row objects is the field list and nothing
        # else. Normalizing it is not guessing, and the alternative was measured: one reply did
        # it and the record lost all 24 fields to an AttributeError.
        rows = reply if isinstance(reply, list) else (reply.get("fields") or [])
        if not isinstance(rows, list):
            raise ValueError(f"`fields` is {type(rows).__name__}, not a list")
        # Zero rows out of a non-trivial reply is a parse failure wearing the costume of an
        # empty answer. The regex salvage returns a flat `{f, open, why, ...}` dict when no
        # candidate parses, `.get("fields")` is then empty, and the record lands as 24 missing
        # fields with `error: None` — silent, and indistinguishable from a judge that graded
        # nothing. Raising converts it into the loud failure the rest of this pipeline assumes.
        if not rows and len(raw.strip()) > 200:
            raise ValueError(
                f"no field rows recovered from a {len(raw.strip())}-char reply "
                f"(salvage keys: {sorted(reply)[:6] if isinstance(reply, dict) else 'list'})"
            )
        for row in rows:
            if not isinstance(row, dict):
                unparsed.append({"row": row, "error": "not an object"})
                continue
            field_id = str(row.get("f") or row.get("field") or "").strip()
            if field_id not in fields.BY_ID:
                unparsed.append({"row": row, "error": f"unknown field {field_id!r}"})
                continue
            if field_id not in scope:
                # A field this call did not ask about. Under a split question that is a judge
                # answering the *other* pass's fields from inside this one's frame, which is the
                # exact leak the split exists to remove — so it is counted where the counting is
                # loud, not dropped. Dropping it would hide the treatment failing.
                unparsed.append(
                    {"row": row, "error": f"{field_id!r} is not in this call's scope"}
                )
                continue
            try:
                entries[field_id] = grader.parse_row(fields.BY_ID[field_id], row)
            except LicensedParseError as exc:
                unparsed.append({"row": row, "error": str(exc)})
    except Exception as exc:                                      # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"

    missing = tuple(f for f in scope if f not in entries)
    return Reply(entries, tuple(unparsed), missing, error)


def merge_replies(replies: Sequence[Reply]) -> Reply:
    """One cell's passes, back together as the single reading the map is built from.

    `missing` is recomputed over the **whole schema**, not unioned from the passes: a pass that
    errored out entirely contributes no rows and no scope-derived misses, and unioning would
    report a clean cell. Errors are joined rather than taking the first, because a cell where
    both calls failed is not the same cell as one where one did, and §7.5 scores judges on it.
    """
    if len(replies) == 1:
        return replies[0]
    entries: dict[str, LicensedEntry] = {}
    unparsed: list[dict[str, Any]] = []
    errors: list[str] = []
    for reply in replies:
        entries.update(reply.entries)
        unparsed.extend(reply.unparsed)
        if reply.error:
            errors.append(reply.error)
    missing = tuple(f for f in fields.ALL_FIELDS_IDS if f not in entries)
    return Reply(entries, tuple(unparsed), missing, "; ".join(errors) or None)
