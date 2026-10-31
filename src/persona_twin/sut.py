"""The system under test: what a submission is, and the harness that serves it (§1.3, §8.8).

Everything else in this tree builds the *answer key* — the card, the schedule-derived map, the
authored conversations, the audit that checks the page delivered what was scheduled. Nothing in
it has ever consumed that key the way a competitor would. This module is that side of the wire:

    python -m persona_twin.sut --sut oracle --sut abstain
    python -m persona_twin.sut --sut prompted --model claude-opus-5

A submission is one object with four methods (`baselines.SUT`). It is handed **one checkpoint's
new conversations at a time**, and after each batch it emits the whole card — a value per field,
or `"unknown"`. `scorer.score_checkpoint` then reads that card against the map: a value on a
field the transcripts pin is scored, a value on a field they only narrow is scored, and a value
on a field they have not touched is a **fabrication whether or not it is correct**. Abstention is
free in the third band and cheap in the second. That asymmetry is the benchmark, and a submission
that has not internalised it will lose to `AbstainEverything`.

Three rules the harness makes structural rather than trusted, because all three are the kind of
thing a submission would otherwise break by accident:

1. **No re-serving.** `serve` returns one batch per checkpoint containing only the sessions at
   that checkpoint. The prefix is never rebuilt for the model — carrying it forward is the SUT's
   own decision, and its cost is what `carried_state_tokens` reports.
2. **No cross-subject carryover.** `reset(subject_id)` is called before every subject and the
   harness constructs nothing that outlives it. An SUT accumulating population base rates across
   subjects can guess unevidenced fields profitably, which is exactly the move the fabrication
   rule exists to price.
3. **A hole is not a shorter timeline.** A subject with an empty session is skipped and counted,
   never served sessions 1 and 3 as though they were 1 and 2 (`judge.Bundle.prefix`'s rule, and
   the same reason).

**The SUT sees transcripts and nothing else.** Not beats, not the map, not `is_distractor`, not
the self-report field, not the card. `serve` builds its batches from `transcript` alone, so a
submission cannot reach the key even by accident — and `EvidenceOracle`, which does read the key,
is a scorer ceiling and never a competitor.

## What is in here

`PromptedExtractor` is spec §8.8's baseline 4, the reference SUT, and it is deliberately the
dumbest thing that honours the protocol: its whole carried state is the card it last emitted.
`NotebookExtractor` is the same loop with a free-text scratchpad added, which is the cheapest way
to make the state axis legible — the two differ by roughly an order of magnitude in
`carried_state_tokens` and the leaderboard is supposed to show that.

`MemoryExtractor` is a third shape, not one of §8.8's four: two calls a checkpoint instead of
one. The first reads only the new sessions and writes atomic candidate memories; the second is
shown the memory bank on file plus those candidates — never the raw conversation — and decides
add / supersede / duplicate for each before emitting the card. The other two SUTs carry state by
re-deriving the whole card from scratch every checkpoint; this one carries an explicit,
addressable memory store and derives the card from *that*, which is the shape most real
memory-backed agents actually use and the other two do not test at all.

None of the three is tuned. They are the bar, not an attempt at the ceiling.

## The distribution fields are counted, not estimated (ADR-0063)

All four submissions handle the three distribution fields — `price_band_distribution`,
`party_size_distribution`, `visit_time_distribution` — through a `Histogram` rather than by asking
the model for the whole normalised share object at once. A distribution is a claim about
*frequency* (§2.4), so the model reports the concrete **occasions** it sees in each batch — one bin
per occasion, folded into the same emission JSON under `datapoints` — and the harness tallies them:
one occasion, one tally. The field's value is the normalised tally, or `"unknown"` while the tally
is empty (a non-empty tally always overrides; there is no thin-histogram abstain). The occasions
are read wherever a SUT already sees the raw conversation — the single emission call for `prompted`
/ `notebook`, phase 1 for `MemoryExtractor`, the whole-bank read-out re-derived each checkpoint for
`Mem0Extractor` — and the accumulated counts are carried state, metered like any other.

This is a **shape** lever, not a score one. `docs/distribution-shape.md` measured a perfect
occasion-counter's `1 − JSD` at 0.865 / 0.941 / 0.863 — at or below the models' own priors, because
each transcript exposes only a handful of legible occasions — so the histogram is not expected to
move the headline number the metric is near-blind to spread on. What it is for is a truer
distribution: more support and fewer mode-spikes than a one-shot estimate collapses to.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import baselines, fields, grading, metrics, scorer
from .card import CardValidationError, PersonaCard, _validate_value
from .evidence import EvidenceMap, Grade
from .fields import UNKNOWN
from .judge import ClaudeCli, Invoker, Router, TransportError, fingerprint, store_slug
from .subject import ScriptedEvent, Subject

#: v0's operational ceiling, for the same reason `verify.DEFAULT_WORKERS` carries it: 28 workers
#: OOM-killed a 16 GB box and logged 1,688 failures with empty stderr. Subjects are the unit —
#: a subject's own checkpoints are a serial chain by construction, since checkpoint t+1 is
#: emitted from the state checkpoint t left behind.
DEFAULT_WORKERS: int = 4

#: v0.1 dropped tiers (ADR-0033) but `Subject.validate` still requires one and `scorer.aggregate`
#: groups on it. Every subject built here carries this, so the tier column in a report is a
#: placeholder and the cell that means something is the author model beside it (§7.1).
PLACEHOLDER_TIER: str = "medium"

#: Attempts per checkpoint before the harness gives up on a model call. Matches `judge`'s rule:
#: a transport failure is retried, a reply that arrives and cannot be read is not.
DEFAULT_ATTEMPTS: int = 3

#: The distribution fields, which every SUT handles through a `Histogram` rather than by asking
#: the model for the whole normalized object at once. A distribution is a claim about *frequency*
#: (§2.4), and frequency is what you get by counting occasions — so the model reports the concrete
#: occasions it sees in each batch and the harness tallies them, instead of the model estimating
#: the shares in one shot from a running memory it never sees the denominator of.
DISTRIBUTION_FIELD_IDS: tuple[str, ...] = tuple(
    spec.field_id for spec in fields.ALL_FIELDS if spec.metric == fields.METRIC_JSD
)


# --- what a submission emits -------------------------------------------------


CARD_SYSTEM = """\
You are building a preference profile of one person from their conversations with a \
restaurant-recommendation assistant. You will be shown their conversations a few at a time, in \
order, and after each batch you output the profile as it stands.

You are scored on CALIBRATION, not on coverage. For each field:

  - the conversations so far PIN the value  -> give the value. Saying "unknown" scores zero.
  - the conversations so far NARROW it      -> give your best value. "unknown" gets partial credit.
  - the conversations have NOT TOUCHED it   -> say "unknown". A value here is a FABRICATION and \
is penalised EVEN IF IT HAPPENS TO BE CORRECT.

That last line is the whole task. Guessing from a stereotype, from a population base rate, or \
from another field ("they like Sichuan, so they must want it hot") earns nothing and costs you \
whenever the conversations have not shown it. This dataset deliberately contains people who \
violate the usual correlations, so the guess is not even reliably right.

Seven fields hold LISTS: dietary, allergies, accessibility_needs, cuisines_liked, \
cuisines_avoided, event_types_liked, event_types_avoided. On these, an empty list [] is NOT the \
same as "unknown": it is a positive claim that the person has NONE, scored as a commitment, so on \
a field the conversations have not established it is a FABRICATION. Two rules:

  - dietary, allergies, accessibility_needs: write [] ONLY once the person has said they have \
none ("no allergies", "no dietary needs"). Until they do, say "unknown".
  - cuisines_liked, cuisines_avoided, event_types_liked, event_types_avoided: never write [] — \
say "unknown" when nothing has been shown, otherwise list what they have shown.

Three things that are not evidence about this person:

  - what the ASSISTANT offers, suggests or assumes. Only what the person says, chooses, accepts \
or rejects.
  - what a FRIEND, PARTNER or COLLEAGUE needs. A companion's nut allergy is not theirs.
  - a claim about themselves that their behaviour contradicts. Record what they DO.

Output JSON only, no prose outside it."""

#: The v1 evidence block, verbatim from `CARD_SYSTEM`, so `CARD_SYSTEM_V2` is provably v1 with
#: exactly this paragraph swapped and nothing else moved. `CARD_SYSTEM` itself never changes:
#: every cached v1 reply is fingerprinted on it (`EmissionStore.load`), and the v1 row on a
#: leaderboard is the row that was scored on it.
_EVIDENCE_V1 = """\
Three things that are not evidence about this person:

  - what the ASSISTANT offers, suggests or assumes. Only what the person says, chooses, accepts \
or rejects.
  - what a FRIEND, PARTNER or COLLEAGUE needs. A companion's nut allergy is not theirs.
  - a claim about themselves that their behaviour contradicts. Record what they DO.
"""
if _EVIDENCE_V1 not in CARD_SYSTEM:      # pragma: no cover - a drift guard, not a branch
    raise RuntimeError("CARD_SYSTEM no longer contains the v1 evidence block verbatim")

#: v2: the `[TWIN-FACING]` evidence conventions of `docs/GT-POLICIES.md` — what counts as the
#: person's own act (§1.2 booking ≠ liking, §1.3 bot prose is scenery, §1.4 third party), what a
#: ceiling means (§1.5), and the literal level (§6.4). These are rules of the game a good reader
#: cannot infer, and v1 stated the first one *backwards* ("chooses, accepts" counted as evidence,
#: which is the `set_inflation` the key charges). Conventions only: the traps the benchmark exists
#: to measure — latent values, abstraction-trap words, correlation-breakers (§6.2, §6.3) — are
#: deliberately not disclosed.
_EVIDENCE_V2 = """\
What counts as evidence about this person:

  - only what the PERSON says, orders, chooses or rejects. What the ASSISTANT offers, describes \
or assumes is scenery, even about a place the person then books.
  - booking a venue is not liking its cuisine or format. If they took it for the distance, the \
quiet room, the price or the availability, the cuisine in the assistant's description is NOT \
evidenced — only the person naming or ordering the cuisine themselves is.
  - a FRIEND, PARTNER or COLLEAGUE's needs are theirs, not this person's. A companion's nut \
allergy is not a nut allergy here.
  - a claim about themselves that their behaviour contradicts. Record what they DO.
  - a CEILING field (formality_ceiling: the highest formality they will go to) is an upper \
bound. Asking for or accepting something BELOW it says nothing about where the bound sits; only \
the highest level they have actually gone to evidences it.
  - use the level whose words they used, not a neighbour: sushi is not japanese, a market is \
not a food_festival.
"""

CARD_SYSTEM_V2 = CARD_SYSTEM.replace(_EVIDENCE_V1, _EVIDENCE_V2)

#: Prompt version -> system prompt. `PromptedExtractor.prompt_version` selects one, and the
#: version rides in the submission *name* (`prompted-v2`), so the cache path
#: (`EmissionStore.path` is `<root>/<name>/<model>/…`) and the report label both carry it: two
#: versions sit on one table, and neither can overwrite the other's replies.
CARD_SYSTEMS: dict[str, str] = {"v1": CARD_SYSTEM, "v2": CARD_SYSTEM_V2}

_OUTPUT_DATAPOINTS = """
 "datapoints": {"<distribution_field_id>": ["<one bin per occasion seen in THESE conversations>", \
...], ...}"""

_OUTPUT_CARD = """
{"card": {"<non-distribution field_id>": <value or "unknown">, ...},""" + _OUTPUT_DATAPOINTS + "}"

_OUTPUT_NOTEBOOK = """
{"card": {"<non-distribution field_id>": <value or "unknown">, ...},""" + _OUTPUT_DATAPOINTS + """,
 "notes": "<what you want your future self to remember: evidence you have seen, what is still \
open, and why. You will be shown these notes and NOT these conversations again.>"}"""

#: Shown wherever a call reads raw conversations and must turn the distribution fields into
#: occasions. The instruction is deliberately about *events*, not estimates: one datapoint per
#: occasion, drawn from the person's own behaviour, reported only for the new batch — the harness
#: accumulates across checkpoints, so a re-count of earlier occasions would double them.
DATAPOINTS_INSTRUCTION = (
    "The distribution fields (" + ", ".join(DISTRIBUTION_FIELD_IDS) + ") are NOT emitted as a "
    "value in the card. Instead, under `datapoints`, list the concrete occasions you can see in "
    "THESE new conversations — one entry per occasion, each drawn from that field's bins. A "
    "dinner for two at about £40 a head on a Friday night is one price_band_distribution "
    "datapoint \"£30-50\", one party_size_distribution datapoint \"2\", one "
    "visit_time_distribution datapoint \"weekend-evening\". Count only occasions grounded in what "
    "the PERSON actually did, not what the assistant proposed. Report only the new occasions in "
    "this batch — the running histogram is kept for you across checkpoints, and the field's value "
    "is computed from it, so never re-list earlier occasions and never estimate the shares "
    "yourself. A batch with no such occasion contributes an empty list, which is correct."
)

#: The bank-readout variant, for a call that re-reads the whole memory bank every checkpoint
#: rather than a fresh batch (`Mem0Extractor`). It has no accumulator to add to — the histogram is
#: rebuilt from scratch each time — so it must list *every* occasion the bank records, not only new
#: ones. The accumulate variant's "new occasions only" rule would lose all but the latest here.
BANK_DATAPOINTS_INSTRUCTION = (
    "The distribution fields (" + ", ".join(DISTRIBUTION_FIELD_IDS) + ") are NOT emitted as a "
    "value in the card. Instead, under `datapoints`, list EVERY concrete occasion recorded "
    "anywhere in the memory bank — one entry per occasion, each drawn from that field's bins "
    "(price_band_distribution: a per-head spend; party_size_distribution: a table size; "
    "visit_time_distribution: when the visit was). Count only occasions grounded in what the "
    "person actually did. List them all every time — the field's value is computed from this "
    "list, so do not estimate the shares yourself, and do not leave out earlier occasions."
)


def field_menu() -> str:
    """The 24 fields, their value spaces, and what a value means — the public half of §2.

    Built from the registry rather than written out, so a field added to `fields.py` reaches a
    submission without anyone remembering to update a prompt. `fields.gloss` is the same helper
    the auditor and the editor read a level through (ADR-0048), so all three agree on what
    `ignores_price` means.
    """
    lines: list[str] = []
    for spec in fields.ALL_FIELDS:
        head = f"- {spec.field_id}"
        if spec.metric == fields.METRIC_ORDINAL:
            head += "  (one of: " + ", ".join(spec.levels) + ")"
        elif spec.metric == fields.METRIC_EXACT:
            head += "  (true or false)"
        elif spec.metric == fields.METRIC_JACCARD:
            head += "  (a list, possibly empty, from: " + ", ".join(spec.levels) + ")"
        elif spec.metric == fields.METRIC_JSD:
            head += (
                "  (a DISTRIBUTION over bins: " + ", ".join(spec.levels) + ". You do NOT emit a "
                "share object for this field — you report the concrete occasions you see, one bin "
                "per occasion, under `datapoints`; the running histogram is kept for you.)"
            )
        lines.append(head)
        for line in fields.gloss(spec.field_id).splitlines():
            lines.append(f"    {line}")
    return "\n".join(lines)


def render_batch(sessions: Sequence[ServedSession]) -> str:
    """The new conversations, in the shape `judge.Bundle.prefix` serves a prefix in.

    The date is on the header because a twin is a point-in-time object and the gaps between
    conversations are themselves signal (`dataset.SessionTask.date`) — a fortnight's silence and
    two conversations in one evening are different evidence about the same person.
    """
    parts = []
    for served in sessions:
        header = f"--- session {served.session + 1}"
        if served.date:
            header += f", {served.date}"
        parts.append(f"{header} ---\n{served.transcript.strip()}")
    return "\n\n".join(parts)


def emission_prompt(
    carried: PersonaCard,
    notes: str | None,
    sessions: Sequence[ServedSession],
    *,
    notebook: bool,
) -> str:
    """What the model is shown at one checkpoint: its own state, then the new conversations.

    Note what is *not* here: the earlier conversations. §1.3 serves each session once, and a
    submission that wants the text again has to have kept it — at which point the keeping is on
    its state meter, where the leaderboard can see it.
    """
    blocks = [
        "The fields, their value spaces, and what each value means:",
        field_menu(),
        "",
        "The profile you emitted after the previous conversations:",
        json.dumps(_card_payload(carried), indent=1),
    ]
    if notebook:
        blocks += ["", "Your own notes from then:", (notes or "(none yet — this is the first batch)")]
    blocks += [
        "",
        f"New conversations ({len(sessions)}), which you have not seen before:",
        render_batch(sessions),
        "",
        "Emit the profile as it stands after these conversations. Every non-distribution field, "
        "every time — a field you leave out is read as \"unknown\". You may revise an earlier "
        "value: a person who says they have stopped eating meat has changed, and the profile "
        "should change with them.",
        DATAPOINTS_INSTRUCTION,
        "Output JSON only:" + (_OUTPUT_NOTEBOOK if notebook else _OUTPUT_CARD),
    ]
    return "\n".join(blocks)


def _card_payload(card: PersonaCard) -> dict[str, Any]:
    return {fid: card.get(fid) for fid in fields.ALL_FIELDS_IDS}


# --- reading a reply ---------------------------------------------------------


@dataclass
class Emission:
    """One card a submission emitted, and everything that went wrong producing it.

    `problems` is never fatal. A malformed value degrades that **field** to `"unknown"` and the
    rest of the card stands, because the alternative — refusing the reply — scores a formatting
    habit as a total failure at that checkpoint, and §8.1 already prices the only thing that
    matters here: a field the submission did not commit is an abstention.
    """

    card: PersonaCard
    notes: str | None = None
    datapoints: Mapping[str, tuple[str, ...]] = dc_field(default_factory=dict)
    datapoint_drops: Mapping[str, int] = dc_field(default_factory=dict)
    problems: tuple[str, ...] = ()
    raw: str = ""


def parse_datapoints(
    payload: Any,
) -> tuple[dict[str, tuple[str, ...]], tuple[str, ...], dict[str, int]]:
    """The `datapoints` block into per-field occasion lists, out-of-vocabulary entries dropped.

    Total, like `parse_emission`: a missing or malformed block yields no occasions rather than
    raising, so a checkpoint that failed to report datapoints costs the histogram that batch's
    growth and nothing else. Each surviving entry is one occasion — the tally is by count, so
    `["2", "2"]` is two tables of two, not a de-duplicated one.

    The third return is the count of occasions *dropped* as off-vocabulary, per field, counted
    with multiplicity (a token dropped twice counts twice). It is the numerator of the vocab
    drop-rate `SubjectScore` publishes: a submission whose datapoints do not land in the closed
    bins is silently starved of the distribution fields, and that starvation was previously only
    visible as scattered `! …: dropped out-of-vocabulary …` warnings in the run log.
    """
    out: dict[str, tuple[str, ...]] = {fid: () for fid in DISTRIBUTION_FIELD_IDS}
    dropped_counts: dict[str, int] = {fid: 0 for fid in DISTRIBUTION_FIELD_IDS}
    problems: list[str] = []
    body = payload.get("datapoints") if isinstance(payload, Mapping) else None
    if not isinstance(body, Mapping):
        return out, tuple(problems), dropped_counts
    for fid in DISTRIBUTION_FIELD_IDS:
        raw_list = body.get(fid)
        if raw_list is None:
            continue
        if isinstance(raw_list, str) or not isinstance(raw_list, (list, tuple, set, frozenset)):
            problems.append(f"{fid}: datapoints is a {type(raw_list).__name__}, expected a list")
            continue
        levels = fields.BY_ID[fid].levels
        lowered = {level.lower(): level for level in levels}
        kept: list[str] = []
        dropped: list[str] = []
        for item in raw_list:
            token = str(item).strip()
            if token in levels:
                kept.append(token)
            elif token.lower() in lowered:
                kept.append(lowered[token.lower()])
            else:
                dropped.append(token)
        out[fid] = tuple(kept)
        dropped_counts[fid] = len(dropped)
        if dropped:
            problems.append(f"{fid}: dropped out-of-vocabulary datapoints {sorted(set(dropped))}")
    return out, tuple(problems), dropped_counts


def parse_emission(raw: str, subject_id: str, previous: PersonaCard | None = None) -> Emission:
    """A reply into a card. Total: it always returns a card, never raises.

    A reply that parses as nothing at all yields the **previous** card rather than an empty one.
    Losing a checkpoint's reply is a transport-shaped accident and forgetting everything the
    submission had already established would charge it for that accident on twenty fields at
    once; standing still is the honest reading of "no answer arrived".
    """
    problems: list[str] = []
    payload: Any = None
    try:
        payload = grading.parse_json(raw)
    except Exception as exc:                                        # noqa: BLE001
        problems.append(f"unparseable reply: {type(exc).__name__}")

    if not isinstance(payload, Mapping):
        base = previous if previous is not None else PersonaCard.all_unknown(subject_id)
        if payload is not None:
            problems.append(f"reply is a {type(payload).__name__}, expected an object")
        return Emission(
            card=PersonaCard(subject_id, dict(base.values)),
            problems=tuple(problems), raw=raw,
        )

    body = payload.get("card")
    if not isinstance(body, Mapping):
        # A model that skipped the wrapper and emitted the fields at the top level is read
        # rather than refused; the wrapper is our convenience, not the answer.
        body = payload
        if "card" in payload:
            problems.append("`card` is not an object; read the top level instead")

    values: dict[str, Any] = {}
    for spec in fields.ALL_FIELDS:
        raw_value = body.get(spec.field_id, UNKNOWN)
        value, problem = coerce_value(spec, raw_value)
        if problem:
            problems.append(f"{spec.field_id}: {problem}")
        values[spec.field_id] = value

    extra = sorted(set(body) - set(fields.ALL_FIELDS_IDS) - {"notes", "datapoints"})
    if extra:
        problems.append(f"ignored fields not in the registry: {extra}")

    datapoints, dp_problems, dp_drops = parse_datapoints(payload)
    notes = payload.get("notes")
    return Emission(
        card=PersonaCard(subject_id, values),
        notes=str(notes) if isinstance(notes, str) else None,
        datapoints=datapoints,
        datapoint_drops=dp_drops,
        problems=tuple(problems) + dp_problems,
        raw=raw,
    )


def coerce_value(spec: fields.Field, raw: Any) -> tuple[Any, str | None]:
    """One emitted value into a schema-valid one, or into an abstention with a reason.

    The distinction this function must not lose is **`[]` against `"unknown"`**. An empty list
    is a commitment — *this person has no dietary rules* — and ADR-0035 made it a value the
    dataset discloses and the map grades. Reading it as an abstention would silently convert
    every correct "they have no allergies" into a miss on a gating field.
    """
    if raw is None:
        return UNKNOWN, None
    if isinstance(raw, str) and raw.strip().lower() in {UNKNOWN, "", "null", "n/a", "none"}:
        return UNKNOWN, None

    if spec.metric == fields.METRIC_JACCARD:
        if isinstance(raw, str):
            return UNKNOWN, f"expected a list, got the string {raw!r}"
        if not isinstance(raw, (list, tuple, set, frozenset)):
            return UNKNOWN, f"expected a list, got {type(raw).__name__}"
        members, dropped = [], []
        for item in raw:
            token = str(item).strip().lower().replace(" ", "_").replace("-", "_")
            (members if token in spec.levels else dropped).append(token)
        value: Any = sorted(dict.fromkeys(members))
        return value, (f"dropped out-of-vocabulary {sorted(set(dropped))}" if dropped else None)

    if spec.metric == fields.METRIC_ORDINAL:
        token = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
        if token in spec.levels:
            return token, None
        return UNKNOWN, f"{raw!r} is not one of {spec.levels}"

    if spec.metric == fields.METRIC_EXACT:
        if isinstance(raw, bool):
            return raw, None
        token = str(raw).strip().lower()
        if token in {"true", "yes"}:
            return True, None
        if token in {"false", "no"}:
            return False, None
        return UNKNOWN, f"{raw!r} is not a boolean"

    if spec.metric == fields.METRIC_JSD:
        if not isinstance(raw, Mapping):
            return UNKNOWN, f"expected a bin->share object, got {type(raw).__name__}"
        masses: dict[str, float] = {}
        dropped = []
        for key, mass in raw.items():
            token = str(key).strip()
            if token not in spec.levels:
                dropped.append(token)
                continue
            try:
                masses[token] = max(0.0, float(mass))
            except (TypeError, ValueError):
                dropped.append(token)
        total = sum(masses.values())
        if total <= 0:
            return UNKNOWN, "every bin is zero or unreadable"
        # Renormalising rather than refusing: a distribution summing to 0.99 is a rounding
        # habit, and `_validate_value` would reject it as a malformed card.
        normalized = {b: masses.get(b, 0.0) / total for b in spec.levels}
        note = f"dropped unknown bins {sorted(set(dropped))}" if dropped else None
        if abs(total - 1.0) > 1e-6:
            note = "; ".join(x for x in (note, f"renormalized from {total:.3f}") if x)
        return normalized, note

    return UNKNOWN, f"unhandled metric {spec.metric!r}"      # pragma: no cover - registry guard


def validated(card: PersonaCard) -> tuple[PersonaCard, tuple[str, ...]]:
    """`card` with any value the schema refuses replaced by an abstention.

    A last gate rather than the first: `coerce_value` already handles every shape seen so far,
    and this exists so that a future field whose metric it does not know cannot put a card the
    scorer will raise on into a run that has already spent its calls.
    """
    problems: list[str] = []
    values = dict(card.values)
    for spec in fields.ALL_FIELDS:
        value = values.get(spec.field_id, UNKNOWN)
        if value == UNKNOWN:
            continue
        try:
            _validate_value(spec, value)
        except CardValidationError as exc:
            values[spec.field_id] = UNKNOWN
            problems.append(str(exc))
    return PersonaCard(card.subject_id, values), tuple(problems)


# --- the distribution histogram ----------------------------------------------


@dataclass
class Histogram:
    """The running per-occasion tally behind the distribution fields, built up over checkpoints.

    Every SUT derives its distribution fields from one of these rather than from a share object
    the model estimated whole. `add` folds in a batch's occasions (by count — two tables of two
    are two datapoints, not one), and `derive` normalises each field's tally into the distribution
    the card carries, or `"unknown"` while a field has seen nothing. A field's value is *always*
    the tally: there is no threshold below which a thin histogram abstains and a share the model
    guessed takes over, because the histogram is the only thing here that ever counted.

    The counts are the SUT's own carried state — the normalised distribution loses the denominator
    the next batch needs to keep counting — so `state` is what the state meter reads, not `derive`.
    """

    counts: dict[str, dict[str, int]] = dc_field(
        default_factory=lambda: {fid: {} for fid in DISTRIBUTION_FIELD_IDS})

    def add(self, datapoints: Mapping[str, Sequence[str]]) -> None:
        """Tally a batch's occasions. Bins are assumed vocabulary-checked (`parse_datapoints`)."""
        for fid in DISTRIBUTION_FIELD_IDS:
            bucket = self.counts.setdefault(fid, {})
            for token in datapoints.get(fid, ()):
                bucket[token] = bucket.get(token, 0) + 1

    def derive(self) -> dict[str, Any]:
        """Every distribution field's value: the normalised tally, or `"unknown"` while empty."""
        out: dict[str, Any] = {}
        for fid in DISTRIBUTION_FIELD_IDS:
            bucket = self.counts.get(fid) or {}
            total = sum(bucket.values())
            if total <= 0:
                out[fid] = UNKNOWN
            else:
                out[fid] = {b: bucket.get(b, 0) / total for b in fields.BY_ID[fid].levels}
        return out

    def state(self) -> dict[str, dict[str, int]]:
        """The raw counts, only for fields that have seen something — what the meter counts."""
        return {fid: dict(bucket) for fid, bucket in self.counts.items() if bucket}


@dataclass
class VocabTally:
    """How faithful a submission's distribution datapoints were to the closed bins.

    Every distribution field is a closed vocabulary the field menu spells out twice — once in
    the field's head (`a DISTRIBUTION over bins: …`) and once in its gloss (`bins: …`). A
    submission that emits an occasion outside those bins (`"weekend-daytime"` for
    `"weekend-day"`, a raw `"4"` for the `"3-5"` bucket) has it dropped by `parse_datapoints`,
    which costs its distribution fields silently. This is the running count of kept-vs-dropped
    occasions behind the `datapoint_off_vocab_rate` on `SubjectScore`; it lives on the SUT (not
    the `Histogram`, which `Mem0Extractor` rebuilds each checkpoint) so the drop count survives
    a submission that re-lists its whole bank every time.
    """

    kept: int = 0
    dropped: int = 0

    def add(
        self, datapoints: Mapping[str, Sequence[str]], drops: Mapping[str, int]
    ) -> None:
        self.kept += sum(len(v) for v in datapoints.values())
        self.dropped += sum(drops.values())

    @property
    def total(self) -> int:
        return self.kept + self.dropped

    @property
    def rate(self) -> float | None:
        """Fraction of emitted occasions dropped as off-vocabulary; None if none were emitted."""
        return self.dropped / self.total if self.total else None


def datapoint_vocab_stats(sut: Any) -> tuple[int, int]:
    """`(kept, off-vocab-dropped)` distribution occasions a submission emitted over a subject.

    Reads the SUT's own `VocabTally`, so a submission that emits no datapoints at all — every
    free baseline — reports `(0, 0)` rather than needing the meter the four paid submissions
    carry. Read once at the end of the trajectory: the tally is cumulative.
    """
    tally = getattr(sut, "_vocab", None)
    if not isinstance(tally, VocabTally):
        return (0, 0)
    return tally.kept, tally.dropped


def apply_histogram(card: PersonaCard, histogram: Histogram) -> PersonaCard:
    """`card` with its distribution fields replaced by the histogram's derived values.

    Unconditional: a non-empty tally overrides whatever is on the card, and an empty one sets the
    field to `"unknown"`. The card the model built the non-distribution fields into is left
    otherwise untouched — the two update paths (re-derived card, accumulated histogram) meet only
    here.
    """
    values = dict(card.values)
    values.update(histogram.derive())
    return PersonaCard(card.subject_id, values)


def rederived_distributions(raw: str, card: PersonaCard) -> PersonaCard:
    """`card` with its distribution fields rebuilt from a reply that re-lists the *whole* store.

    For an SUT that re-reads its entire memory every checkpoint rather than accumulating a batch
    at a time (`Mem0Extractor`): there is no running tally to grow, so the histogram is built from
    scratch from this reply's datapoints. A reply that does not parse leaves the distributions
    untouched — a transport-shaped accident holds the previous distributions the same way
    `parse_emission` holds the previous card, instead of wiping fields the store still supports.
    """
    try:
        payload = grading.parse_json(raw)
    except Exception:                                               # noqa: BLE001
        return card
    if not isinstance(payload, Mapping):
        return card
    datapoints, _, _ = parse_datapoints(payload)
    histogram = Histogram()
    histogram.add(datapoints)
    return apply_histogram(card, histogram)


# --- the memory-bank SUT's two prompts ---------------------------------------


MEMORY_EXTRACTION_SYSTEM = """\
You are reading someone's conversations with a restaurant-recommendation assistant and writing \
down what you learn about them, as durable memory items you can consult later without ever \
re-reading this conversation.

Write each memory as one atomic, self-contained sentence, with enough of its own evidence baked \
in that a later reader who never sees this conversation can judge how strong it is. Prefer \
"they said directly: ..." over a paraphrase that hides whether it was stated, implied, or merely \
consistent with what they ordered.

Three things that are NOT a memory about this person:

  - what the ASSISTANT offered, suggested or assumed. Only what the person said, chose, accepted \
or rejected.
  - what a FRIEND, PARTNER or COLLEAGUE needs. A companion's nut allergy is not theirs.
  - a claim about themselves their behaviour contradicts. Write what they DID, and say so if it \
conflicts with something they claimed.

Skip small talk and anything that reveals nothing about their preferences, habits or constraints.
A conversation with nothing durable in it can produce zero memories — that is a correct output,
not a failure.

Output JSON only, no prose outside it."""


MEMORY_INTEGRATION_SYSTEM = """\
You maintain one person's preference profile from a running memory bank, built from their \
conversations with a restaurant-recommendation assistant. You do not see the conversations \
themselves — only the memory items already on file and the new candidates just written from the \
latest ones. You are scored on CALIBRATION, not on coverage. For each tracked field:

  - the memories on file PIN the value   -> give the value. Saying "unknown" scores zero.
  - the memories NARROW it               -> give your best value. "unknown" gets partial credit.
  - no memory touches it                 -> say "unknown". A value here is a FABRICATION and is \
penalised EVEN IF IT HAPPENS TO BE CORRECT.

Guessing from a stereotype, a population base rate, or another field earns nothing and costs you \
whenever no memory on file supports it.

Seven fields hold LISTS: dietary, allergies, accessibility_needs, cuisines_liked, \
cuisines_avoided, event_types_liked, event_types_avoided. On these, an empty list [] is NOT the \
same as "unknown": it is a positive claim that the person has NONE, scored as a commitment, so on \
a field no memory on file establishes it is a FABRICATION. Two rules:

  - dietary, allergies, accessibility_needs: write [] ONLY once a memory on file says they have \
none ("no allergies", "no dietary needs"). Until one does, say "unknown".
  - cuisines_liked, cuisines_avoided, event_types_liked, event_types_avoided: never write [] — \
say "unknown" when no memory shows any, otherwise list what the memories support.

You also maintain the memory bank itself. For every new candidate below, decide exactly one of:

  - "add"       - genuinely new information; keep it as its own item.
  - "supersede" - it updates or corrects an item already on file; name that item's id in \
"supersedes" — the old item is dropped in favour of the new text. A person who says they have \
stopped eating meat has changed, and the bank should change with them.
  - "duplicate" - it says nothing the bank does not already have; it is dropped.

Output JSON only, no prose outside it."""


def memory_extraction_prompt(sessions: Sequence[ServedSession]) -> str:
    """Phase 1: only the new sessions. No memory bank, no card — extraction is a function of the
    new evidence alone, the same separation `serve` already makes between checkpoints.

    This is the only phase that sees the raw conversation, so it is also where the distribution
    occasions are read off: integration never sees the text and could not count them.
    """
    blocks = [
        "New conversations you have not seen before, to write memories and datapoints from:",
        render_batch(sessions),
        "",
        DATAPOINTS_INSTRUCTION,
        "",
        "Output JSON only:",
        '{"memories": ["<one atomic observation>", ...],\n'
        ' "datapoints": {"<distribution_field_id>": ["<one bin per occasion>", ...], ...}}',
        "An empty memories list is fine if nothing durable happened; empty datapoints lists too.",
    ]
    return "\n".join(blocks)


def memory_integration_prompt(active: Sequence["MemoryItem"], candidates: Sequence[str]) -> str:
    """Phase 2: the memory bank and the new candidates. Deliberately not the raw conversation —
    a memory extraction phrased badly is what this call is stuck reasoning from, the same way a
    real memory-backed system only has what it wrote down."""
    bank = "\n".join(f"[{item.id}] {item.text}" for item in active) or "(empty — nothing on file yet)"
    new = "\n".join(f"- {c}" for c in candidates) or "(none)"
    blocks = [
        "The fields, their value spaces, and what each value means:",
        field_menu(),
        "",
        f"The memory bank as it stands ({len(active)} items):",
        bank,
        "",
        f"New candidate memories just written from the latest conversations ({len(candidates)}):",
        new,
        "",
        "For every new candidate, decide add / supersede / duplicate, then emit the profile as "
        "it stands after your decisions. Every non-distribution field, every time — a field you "
        "leave out is read as \"unknown\".",
        "Leave the distribution fields (" + ", ".join(DISTRIBUTION_FIELD_IDS) + ") OUT of your "
        "card: they are maintained from occasions counted during extraction and filled in for "
        "you, not something you estimate here.",
        "Output JSON only:\n"
        '{"memory_ops": [{"op": "add"|"supersede"|"duplicate", '
        '"text": "<candidate text, verbatim>", "supersedes": "<id or null>"}, ...],\n'
        ' "card": {"<non-distribution field_id>": <value or "unknown">, ...}}',
    ]
    return "\n".join(blocks)


BANK_EMISSION_SYSTEM = """\
You maintain one person's preference profile from a running memory bank, built from their \
conversations with a restaurant-recommendation assistant. You do not see the conversations \
themselves — only the memory items already on file. You are scored on CALIBRATION, not on \
coverage. For each tracked field:

  - the memories on file PIN the value   -> give the value. Saying "unknown" scores zero.
  - the memories NARROW it               -> give your best value. "unknown" gets partial credit.
  - no memory touches it                 -> say "unknown". A value here is a FABRICATION and is \
penalised EVEN IF IT HAPPENS TO BE CORRECT.

Guessing from a stereotype, a population base rate, or another field earns nothing and costs you \
whenever no memory on file supports it.

Seven fields hold LISTS: dietary, allergies, accessibility_needs, cuisines_liked, \
cuisines_avoided, event_types_liked, event_types_avoided. On these, an empty list [] is NOT the \
same as "unknown": it is a positive claim that the person has NONE, scored as a commitment, so on \
a field no memory on file establishes it is a FABRICATION. Two rules:

  - dietary, allergies, accessibility_needs: write [] ONLY once a memory on file says they have \
none ("no allergies", "no dietary needs"). Until one does, say "unknown".
  - cuisines_liked, cuisines_avoided, event_types_liked, event_types_avoided: never write [] — \
say "unknown" when no memory shows any, otherwise list what the memories support.

You do not maintain the bank — something else writes it. Read what is on file and report the \
profile it supports, no more.

Output JSON only, no prose outside it."""


def bank_emission_prompt(memories: Sequence[str]) -> str:
    """Read a memory bank someone else maintains and emit the card (ADR-0062 decision 1).

    `memory_integration_prompt` without the bank-maintenance half, because `Mem0Extractor`'s bank
    is mem0's to write. Sharing this call with `MemoryExtractor`'s phase 2 is what makes the two
    rows a comparison: they are served the same sessions and read out by the same instrument, so
    what is left between them is the memory layer.
    """
    bank = "\n".join(f"- {text}" for text in memories) or "(empty — nothing on file yet)"
    return "\n".join([
        "The fields, their value spaces, and what each value means:",
        field_menu(),
        "",
        f"The memory bank as it stands ({len(memories)} items):",
        bank,
        "",
        "Emit the profile the bank supports. Every non-distribution field, every time — a field "
        "you leave out is read as \"unknown\".",
        BANK_DATAPOINTS_INSTRUCTION,
        "Output JSON only:\n"
        '{"card": {"<non-distribution field_id>": <value or "unknown">, ...},\n'
        ' "datapoints": {"<distribution_field_id>": ["<one bin per occasion in the bank>", ...], '
        "...}}",
    ])


#: The memory-side v1/v2 split, same construction as `CARD_SYSTEM_V2`: each v1 constant is
#: byte-stable (every cached reply is fingerprinted on it), and v2 is that text with one block
#: swapped or inserted. Extraction is the only memory-side call that reads the transcript, so it
#: gets the conventions in the same shape as `_EVIDENCE_V2`; the two read-out prompts never see
#: the text and get them as rules about what a *memory* licenses. For `Mem0Extractor` the
#: read-out is the only lever there is — its bank is written by mem0's own schema-blind prompts
#: (ADR-0062), which this tree does not edit.
_MEMORY_EVIDENCE_V1 = """\
Three things that are NOT a memory about this person:

  - what the ASSISTANT offered, suggested or assumed. Only what the person said, chose, accepted \
or rejected.
  - what a FRIEND, PARTNER or COLLEAGUE needs. A companion's nut allergy is not theirs.
  - a claim about themselves their behaviour contradicts. Write what they DID, and say so if it \
conflicts with something they claimed.
"""
_MEMORY_EVIDENCE_V2 = """\
What a memory about this person may record:

  - only what the PERSON said, ordered, chose or rejected. What the ASSISTANT offered, described \
or assumed is scenery, even about a place the person then booked — if you record the booking, \
record the reason they gave, and mark any cuisine or format as the assistant's description, not \
theirs.
  - booking a venue is not liking its cuisine or format. Taken for the distance, the quiet room, \
the price or the availability, it evidences nothing about the cuisine; only the person naming or \
ordering the cuisine themselves does.
  - a FRIEND, PARTNER or COLLEAGUE's needs are theirs, not this person's. A companion's nut \
allergy is not a nut allergy here.
  - a claim about themselves their behaviour contradicts. Write what they DID, and say so if it \
conflicts with something they claimed.
  - asking for or accepting something BELOW a ceiling (formality_ceiling: the highest formality \
they will go to) says nothing about where the ceiling sits. Record the highest level they \
actually went to, not every level below it.
  - record the words they used, not a neighbour: sushi is not japanese, a market is not a \
food_festival.
"""
_READOUT_EVIDENCE_V2 = """\
What a memory on file licenses:

  - only what the PERSON said, ordered, chose or rejected. A memory of what the ASSISTANT \
offered, described or assumed licenses nothing, even about a place the person booked.
  - booking a venue is not liking its cuisine or format. A memory that they took a venue the \
assistant called Mediterranean, for the distance, the quiet room, the price or the availability, \
does not put mediterranean in cuisines_liked; only a memory of the person naming or ordering the \
cuisine does.
  - a FRIEND, PARTNER or COLLEAGUE's needs are theirs, not this person's.
  - a CEILING field (formality_ceiling: the highest formality they will go to) is an upper \
bound. A memory of a request or a booking BELOW it says nothing about where the bound sits; only \
the highest level on file evidences it.
  - use the level whose words the memory records, not a neighbour: sushi is not japanese, a \
market is not a food_festival.

"""
_INTEGRATION_ANCHOR = "You also maintain the memory bank itself."
_BANK_ANCHOR = "You do not maintain the bank"
for _needle, _haystack, _where in (
    (_MEMORY_EVIDENCE_V1, MEMORY_EXTRACTION_SYSTEM, "MEMORY_EXTRACTION_SYSTEM"),
    (_INTEGRATION_ANCHOR, MEMORY_INTEGRATION_SYSTEM, "MEMORY_INTEGRATION_SYSTEM"),
    (_BANK_ANCHOR, BANK_EMISSION_SYSTEM, "BANK_EMISSION_SYSTEM"),
):
    if _haystack.count(_needle) != 1:      # pragma: no cover - a drift guard, not a branch
        raise RuntimeError(f"{_where} no longer carries its v2 anchor exactly once")

MEMORY_EXTRACTION_SYSTEM_V2 = MEMORY_EXTRACTION_SYSTEM.replace(_MEMORY_EVIDENCE_V1, _MEMORY_EVIDENCE_V2)
MEMORY_INTEGRATION_SYSTEM_V2 = MEMORY_INTEGRATION_SYSTEM.replace(
    _INTEGRATION_ANCHOR, _READOUT_EVIDENCE_V2 + _INTEGRATION_ANCHOR)
BANK_EMISSION_SYSTEM_V2 = BANK_EMISSION_SYSTEM.replace(_BANK_ANCHOR, _READOUT_EVIDENCE_V2 + _BANK_ANCHOR)

#: Prompt version -> (extraction, integration) for `MemoryExtractor`, and -> read-out for
#: `Mem0Extractor`. Selected the same way as `CARD_SYSTEMS`: by `prompt_version`, with the
#: version in the submission name (`memory-v2`, `mem0-v2`) so the cache and the label carry it.
MEMORY_SYSTEMS: dict[str, tuple[str, str]] = {
    "v1": (MEMORY_EXTRACTION_SYSTEM, MEMORY_INTEGRATION_SYSTEM),
    "v2": (MEMORY_EXTRACTION_SYSTEM_V2, MEMORY_INTEGRATION_SYSTEM_V2),
}
BANK_SYSTEMS: dict[str, str] = {"v1": BANK_EMISSION_SYSTEM, "v2": BANK_EMISSION_SYSTEM_V2}


@dataclass
class Candidates:
    """Phase 1's output: total, like `Emission` — a reply that will not parse yields no
    candidates rather than raising, so a transport hiccup on extraction costs those memories and
    nothing else. The `datapoints` are read the same way and feed the histogram directly, since
    integration never sees the conversation they were counted from."""

    texts: tuple[str, ...] = ()
    datapoints: Mapping[str, tuple[str, ...]] = dc_field(default_factory=dict)
    datapoint_drops: Mapping[str, int] = dc_field(default_factory=dict)
    problems: tuple[str, ...] = ()
    raw: str = ""


def parse_memory_candidates(raw: str) -> Candidates:
    try:
        payload = grading.parse_json(raw)
    except Exception as exc:                                        # noqa: BLE001
        return Candidates(problems=(f"unparseable reply: {type(exc).__name__}",), raw=raw)
    datapoints, dp_problems, dp_drops = parse_datapoints(payload)
    body = payload.get("memories") if isinstance(payload, Mapping) else payload
    if not isinstance(body, (list, tuple)):
        return Candidates(
            datapoints=datapoints,
            datapoint_drops=dp_drops,
            problems=(f"`memories` is a {type(body).__name__}, expected a list", *dp_problems),
            raw=raw,
        )
    texts: list[str] = []
    problems: list[str] = list(dp_problems)
    for item in body:
        text = str(item).strip()
        if text:
            texts.append(text)
        else:
            problems.append("dropped an empty memory candidate")
    return Candidates(
        texts=tuple(texts), datapoints=datapoints, datapoint_drops=dp_drops,
        problems=tuple(problems), raw=raw)


@dataclass
class Integration:
    """Phase 2's output: the card (read by `parse_emission`'s own rules, so a malformed value
    degrades that field and nothing else) plus the memory-bank decisions, as raw dicts — validated
    against the actual bank in `MemoryExtractor._apply_ops`, not here, since only the caller knows
    which ids currently exist."""

    card: PersonaCard
    ops: tuple[Mapping[str, Any], ...] = ()
    problems: tuple[str, ...] = ()
    raw: str = ""


def parse_integration(raw: str, subject_id: str, previous: PersonaCard | None = None) -> Integration:
    emission = parse_emission(raw, subject_id, previous=previous)
    ops: list[Mapping[str, Any]] = []
    try:
        payload = grading.parse_json(raw)
    except Exception:                                                # noqa: BLE001
        payload = None
    if isinstance(payload, Mapping):
        raw_ops = payload.get("memory_ops")
        if isinstance(raw_ops, (list, tuple)):
            ops = [dict(entry) for entry in raw_ops if isinstance(entry, Mapping)]
    return Integration(card=emission.card, ops=tuple(ops), problems=emission.problems, raw=raw)


# --- state metering ----------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """A stand-in for the submission's own tokenizer: ~4 characters a token.

    Honest metering is the submission's obligation (§1.3) and this is what the reference SUTs
    report, so the number is comparable *between them* and approximate against anything else. A
    real submission should report its own count and say which tokenizer produced it.
    """
    return math.ceil(len(text) / 4)


# --- the reference SUTs ------------------------------------------------------


@dataclass
class PromptedExtractor:
    """Baseline 4 (§8.8): one model call per checkpoint, and the card is the whole state.

    The simplest thing that honours §1.3. It is shown the card it last emitted and the new
    conversations, and it emits the card again. It cannot quote a conversation it has forgotten,
    so anything it wants to keep has to be *in the card* — which is why its state meter is small
    and why `NotebookExtractor` exists beside it.

    `on_problem` is called with every parse complaint rather than logged here, because a run
    wants them in its manifest and a test wants them in a list.
    """

    model: str = "claude-opus-5"
    invoke: Invoker | None = None
    attempts: int = DEFAULT_ATTEMPTS
    notebook: bool = False
    name: str = "prompted"
    prompt_version: str = "v1"
    on_problem: Callable[[str], None] | None = None
    store: EmissionStore | None = None

    subject_id: str = ""
    _card: PersonaCard | None = dc_field(default=None, repr=False)
    _notes: str | None = dc_field(default=None, repr=False)
    _histogram: Histogram = dc_field(default_factory=Histogram, repr=False)
    _vocab: VocabTally = dc_field(default_factory=VocabTally, repr=False)
    _checkpoint: int = 0

    def __post_init__(self) -> None:
        self.invoke = self.invoke or Router()
        if self.prompt_version not in CARD_SYSTEMS:
            raise ValueError(
                f"unknown prompt version {self.prompt_version!r}; have {sorted(CARD_SYSTEMS)}")

    @property
    def system(self) -> str:
        """The system prompt this version of the submission runs under (`CARD_SYSTEMS`)."""
        return CARD_SYSTEMS[self.prompt_version]

    # -- protocol ------------------------------------------------------

    def reset(self, subject_id: str) -> None:
        """Drop everything. Not a formality — see `baselines.SUT.reset`."""
        self.subject_id = subject_id
        self._card = PersonaCard.all_unknown(subject_id)
        self._notes = None
        self._histogram = Histogram()
        self._vocab = VocabTally()
        self._checkpoint = 0

    def observe(self, sessions: Sequence[ServedSession]) -> PersonaCard:
        checkpoint = self._checkpoint
        self._checkpoint += 1
        carried = self._card or PersonaCard.all_unknown(self.subject_id)
        if not sessions:
            # A checkpoint with no new text cannot move the card, and spending a call to be
            # told so is spending a call to be told so.
            return carried
        prompt = emission_prompt(carried, self._notes, sessions, notebook=self.notebook)
        raw = self._reply(checkpoint, prompt)
        emission = parse_emission(raw, self.subject_id, previous=carried)
        self._histogram.add(emission.datapoints)
        self._vocab.add(emission.datapoints, emission.datapoint_drops)
        card, refused = validated(apply_histogram(emission.card, self._histogram))
        for problem in (*emission.problems, *refused):
            if self.on_problem:
                self.on_problem(f"{self.subject_id} c{checkpoint}: {problem}")
        self._card = card
        if self.notebook:
            self._notes = emission.notes if emission.notes is not None else self._notes
        return card

    def carried_state_bytes(self) -> int:
        return len(self._state_blob().encode("utf-8"))

    def carried_state_tokens(self) -> int:
        return estimate_tokens(self._state_blob())

    # -- internals -----------------------------------------------------

    def _state_blob(self) -> str:
        """Everything this SUT will be able to read at the next checkpoint. Metered whole.

        The card is counted as the JSON it is actually re-shown, not as an abstract count of
        committed fields, because that JSON is what occupies the next prompt. The histogram counts
        ride here too: they are what keeps the distributions accumulating, and the denominator they
        hold is not recoverable from the normalised distribution the card shows.
        """
        card = self._card or PersonaCard.all_unknown(self.subject_id)
        blob = json.dumps(_card_payload(card), sort_keys=True)
        tally = self._histogram.state()
        if tally:
            blob += "\n" + json.dumps(tally, sort_keys=True)
        if self.notebook and self._notes:
            blob += "\n" + self._notes
        return blob

    def _reply(self, checkpoint: int, prompt: str) -> str:
        system = self.system
        cached = (
            self.store.load(self, self.subject_id, checkpoint, prompt, system=system)
            if self.store else None
        )
        if cached is not None:
            return cached
        last: Exception | None = None
        for _attempt in range(max(1, self.attempts)):
            try:
                raw = self.invoke(self.model, system, prompt)     # type: ignore[misc]
            except TransportError as exc:
                last = exc
                continue
            if self.store:
                self.store.write(self, self.subject_id, checkpoint, prompt, raw, system=system)
            return raw
        if self.on_problem:
            self.on_problem(f"{self.subject_id} c{checkpoint}: no reply ({last})")
        return ""


def NotebookExtractor(**kwargs: Any) -> PromptedExtractor:
    """The same loop with a scratchpad: the SUT writes notes to itself and is shown them back.

    One constructor rather than a subclass, because the difference *is* one flag — and the point
    of shipping both is the axis, not the second system. A submission that reaches the same AUFC
    on 300 tokens of state as another does on 6,000 is doing something categorically different,
    and `carried_state_tokens` is where the leaderboard shows it (§1.3).
    """
    kwargs.setdefault("name", "notebook")
    return PromptedExtractor(notebook=True, **kwargs)


@dataclass(frozen=True)
class MemoryItem:
    """One entry in a `MemoryExtractor`'s bank: an id it can be superseded by and the atomic
    text extraction wrote. There is no `status` field — a superseded item is removed from the
    bank outright rather than marked dead, because `carried_state_*` meters exactly what will be
    re-shown next checkpoint (ADR-0060 decision 9), and a dead item re-shown to nobody is not
    that."""

    id: str
    text: str


@dataclass
class MemoryExtractor:
    """Two calls a checkpoint, not one, and the second is not shown what the first was shown.

    Phase 1 (`memory_extraction_prompt`) reads only the new sessions and writes candidate
    memories — it never sees the bank, so it cannot rubber-stamp "nothing changed" without
    looking. Phase 2 (`memory_integration_prompt`) is shown the bank and the candidates — never
    the raw conversation — and must decide add / supersede / duplicate for each before emitting
    the card. A candidate phrased badly by extraction is what integration is stuck reasoning
    from; that lossy handoff is the thing this SUT exists to price, and `PromptedExtractor` never
    pays it because it never separates "what did I just learn" from "what do I now believe".

    Carried state is the bank, not the card — the card is *re-derived* from the bank every
    checkpoint rather than being the state itself, which is the other half of what makes this a
    different shape from the other two rather than a relabelling of `NotebookExtractor`.
    """

    model: str = "claude-opus-5"
    invoke: Invoker | None = None
    attempts: int = DEFAULT_ATTEMPTS
    name: str = "memory"
    prompt_version: str = "v1"
    on_problem: Callable[[str], None] | None = None
    store: EmissionStore | None = None

    subject_id: str = ""
    _bank: list[MemoryItem] = dc_field(default_factory=list, repr=False)
    _card: PersonaCard | None = dc_field(default=None, repr=False)
    _histogram: Histogram = dc_field(default_factory=Histogram, repr=False)
    _vocab: VocabTally = dc_field(default_factory=VocabTally, repr=False)
    _checkpoint: int = 0
    _next_id: int = 0

    def __post_init__(self) -> None:
        self.invoke = self.invoke or Router()
        if self.prompt_version not in MEMORY_SYSTEMS:
            raise ValueError(
                f"unknown prompt version {self.prompt_version!r}; have {sorted(MEMORY_SYSTEMS)}")

    @property
    def extraction_system(self) -> str:
        return MEMORY_SYSTEMS[self.prompt_version][0]

    @property
    def integration_system(self) -> str:
        return MEMORY_SYSTEMS[self.prompt_version][1]

    # -- protocol ------------------------------------------------------

    def reset(self, subject_id: str) -> None:
        self.subject_id = subject_id
        self._bank = []
        self._card = PersonaCard.all_unknown(subject_id)
        self._histogram = Histogram()
        self._vocab = VocabTally()
        self._checkpoint = 0
        self._next_id = 0

    def observe(self, sessions: Sequence[ServedSession]) -> PersonaCard:
        checkpoint = self._checkpoint
        self._checkpoint += 1
        carried = self._card or PersonaCard.all_unknown(self.subject_id)
        if not sessions:
            return carried

        extraction_raw = self._reply(
            checkpoint, "extract", self.extraction_system, memory_extraction_prompt(sessions))
        candidates = parse_memory_candidates(extraction_raw)
        for problem in candidates.problems:
            self._report(checkpoint, problem)
        # Extraction is the only phase that reads the conversation, so the histogram grows here —
        # integration is shown the bank, not the text, and could not count occasions from it.
        self._histogram.add(candidates.datapoints)
        self._vocab.add(candidates.datapoints, candidates.datapoint_drops)

        integration_prompt = memory_integration_prompt(self._bank, candidates.texts)
        integration_raw = self._reply(
            checkpoint, "integrate", self.integration_system, integration_prompt)
        integration = parse_integration(integration_raw, self.subject_id, previous=carried)
        card, refused = validated(apply_histogram(integration.card, self._histogram))
        for problem in (*integration.problems, *refused):
            self._report(checkpoint, problem)

        self._apply_ops(integration.ops, candidates.texts, checkpoint)
        self._card = card
        return card

    def carried_state_bytes(self) -> int:
        return len(self._state_blob().encode("utf-8"))

    def carried_state_tokens(self) -> int:
        return estimate_tokens(self._state_blob())

    # -- internals -----------------------------------------------------

    def _state_blob(self) -> str:
        """The bank plus the histogram counts. The card is re-derived from the bank and carries
        nothing it does not already say, but the distribution tallies are not in the bank text —
        they are their own carried state, the same as they are for `PromptedExtractor`."""
        return json.dumps({
            "bank": [item.text for item in self._bank],
            "histogram": self._histogram.state(),
        })

    def _apply_ops(
        self, ops: Sequence[Mapping[str, Any]], candidates: Sequence[str], checkpoint: int,
    ) -> None:
        """The bank after phase 2's decisions. A candidate phase 2 never mentions is added
        anyway rather than silently dropped — the alternative would let a malformed integration
        reply quietly forget evidence extraction had already surfaced, which is exactly the kind
        of transport-shaped accident `parse_emission`'s own rules refuse to charge a submission
        for."""
        decided: set[str] = set()
        for op in ops:
            text = str(op.get("text") or "").strip()
            if not text:
                continue
            decided.add(text)
            kind = str(op.get("op") or "add").strip().lower()
            if kind == "duplicate":
                continue
            if kind == "supersede":
                target = str(op.get("supersedes") or "").strip()
                before = len(self._bank)
                self._bank = [item for item in self._bank if item.id != target]
                if target and len(self._bank) == before:
                    self._report(checkpoint, f"supersede named unknown id {target!r}; added anyway")
            elif kind != "add":
                self._report(checkpoint, f"unrecognized memory op {kind!r}; treated as add")
            self._bank.append(MemoryItem(id=f"m{self._next_id}", text=text))
            self._next_id += 1
        for text in candidates:
            if text not in decided:
                self._report(checkpoint, "candidate memory never decided by integration; added anyway")
                self._bank.append(MemoryItem(id=f"m{self._next_id}", text=text))
                self._next_id += 1

    def _report(self, checkpoint: int, problem: str) -> None:
        if self.on_problem:
            self.on_problem(f"{self.subject_id} c{checkpoint}: {problem}")

    def _reply(self, checkpoint: int, phase: str, system: str, prompt: str) -> str:
        cached = (
            self.store.load(self, self.subject_id, checkpoint, prompt, phase=phase, system=system)
            if self.store else None
        )
        if cached is not None:
            return cached
        last: Exception | None = None
        for _attempt in range(max(1, self.attempts)):
            try:
                raw = self.invoke(self.model, system, prompt)     # type: ignore[misc]
            except TransportError as exc:
                last = exc
                continue
            if self.store:
                self.store.write(self, self.subject_id, checkpoint, prompt, raw, phase=phase, system=system)
            return raw
        self._report(checkpoint, f"no reply on {phase} ({last})")
        return ""


# --- caching -----------------------------------------------------------------


@dataclass
class EmissionStore:
    """Replies on disk, fingerprinted on what the SUT was shown.

    Same rule as `judge.GradingStore` and `author.TranscriptStore`, and for the same reason: a
    re-run after a corpus change should pay only for the checkpoints whose prompt actually
    moved. The fingerprint covers the prompt, which contains the carried state — so a changed
    reply at checkpoint 2 invalidates 3 onward automatically, without anyone tracking the chain.

    The `._*` exclusion is not decoration (some filesystems, e.g. exFAT, write `._*` sidecars);
    see `author.TranscriptStore.records`.

    ## Position replay (`replay=True`)

    `load` normally gates on the prompt fingerprint, so a moved prompt re-pays. `replay=True` drops
    that gate and returns the stored reply by **position** — `(subject, checkpoint, phase)` — whatever
    the current prompt is. It exists for one exact use: **re-scoring an unchanged run against a
    corrected answer key.** The SUT never sees gold (§8.8), so changing the gold cannot change any
    prompt; the emitted cards are provably identical, and only the scorer's key moved. Position
    replay reproduces those cards for free even where the prompt itself is not byte-stable — which is
    `Mem0Extractor`, whose read-out prompt embeds the bank in Qdrant scroll order and so never
    fingerprints the same twice.

    **Only sound while the store (transcripts) is unchanged.** If the served transcript moved, the
    prompt moved for a real reason and the stored reply is stale — position replay would then hand
    back a reply to a question no longer being asked. Use it for a gold-only re-score, never after
    re-authoring. A missing file still returns `None` (and the caller then pays a live call), so
    replay needs full cache coverage to stay free.
    """

    root: Path
    replay: bool = False

    def path(self, sut: Any, subject_id: str, checkpoint: int, *, phase: str = "emit") -> Path:
        suffix = f"-{phase}" if phase != "emit" else ""
        return (
            self.root / getattr(sut, "name", "sut") / store_slug(getattr(sut, "model", "none"))
            / f"{subject_id}-c{checkpoint}{suffix}.json"
        )

    def load(
        self, sut: Any, subject_id: str, checkpoint: int, prompt: str, *,
        phase: str = "emit", system: str = CARD_SYSTEM,
    ) -> str | None:
        path = self.path(sut, subject_id, checkpoint, phase=phase)
        if not path.exists() or path.name.startswith("._"):
            return None
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if not self.replay and record.get("instrument") != fingerprint(system, prompt):
            return None
        return str(record.get("raw") or "")

    def write(
        self, sut: Any, subject_id: str, checkpoint: int, prompt: str, raw: str, *,
        phase: str = "emit", system: str = CARD_SYSTEM,
    ) -> Path:
        path = self.path(sut, subject_id, checkpoint, phase=phase)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "sut": getattr(sut, "name", "sut"),
            "model": getattr(sut, "model", ""),
            "subject_id": subject_id,
            "checkpoint": checkpoint,
            "phase": phase,
            "instrument": fingerprint(system, prompt),
            "raw": raw,
        }, indent=2))
        return path


# --- serving the dataset -----------------------------------------------------


@dataclass(frozen=True)
class ServedSession:
    """One conversation, as a submission receives it: text, when it happened, and nothing else.

    Every other key on the authored record — beats, `is_distractor`, `self_report`, the problem
    list — is answer key, and this dataclass is where that line is drawn once instead of at each
    call site.
    """

    subject_id: str
    session: int
    checkpoint: int
    date: str
    transcript: str


@dataclass(frozen=True)
class Served:
    """One subject, ready to score: the batches to serve and the key to score against."""

    subject: Subject
    batches: tuple[tuple[ServedSession, ...], ...]

    @property
    def subject_id(self) -> str:
        return self.subject.subject_id


def load_items(corpus: Path | str) -> dict[str, dict]:
    """Corpus items by subject id. Accepts both the wrapped and bare list forms."""
    payload = json.loads(Path(corpus).read_text())
    items = payload["items"] if isinstance(payload, Mapping) else payload
    return {str(item["subject_id"]): item for item in items}


def load_store(store: Path | str) -> dict[str, dict]:
    """Authored records by task id, from one author's directory.

    `verify.load_store`'s rule, restated rather than imported so this module does not depend on
    the audit: `gen-*.json` only, and `._*` sidecars excluded — some filesystems (e.g. exFAT) write one
    beside every real file, and a glob that counted them read a 72-call run as 144.
    """
    out: dict[str, dict] = {}
    for path in sorted(Path(store).glob("gen-*.json")):
        if path.name.startswith("._"):
            continue
        try:
            out[str(json.loads(path.read_text())["task_id"])] = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    return out


def evidence_map(item: Mapping[str, Any]) -> EvidenceMap:
    """The corpus item's derived map, as the scorer's `EvidenceMap` (ADR-0034).

    The map is the schedule's own, produced without a model call, and — since ADR-0046 — with
    the reader-derived credit layer already folded in wherever the corpus that is being loaded
    carries it. Nothing here decides that: whichever corpus file is named is the answer key.

    **It is validated, not trusted.** The dense-checkpoint check below has always been here;
    monotonicity was not, because the schedule's own map is monotone by construction and
    `docs/SCORING.md` (Stage 1) records the property as "met rather than checked". A credited corpus is built
    on top of that map by a different module, and the 2026-08-25 base reached this function
    with 22 of 50 subjects carrying a WEAK island — a cell the answer key expects a twin to
    know at *t* and to have forgotten at *t+1*. The oracle scores 1.000 on such a map (it
    reads the same map), so nothing downstream can catch it; only the invariant can.
    """
    provisional = item["provisional"]
    indices = sorted(int(k) for k in provisional)
    if indices != list(range(len(indices))):
        raise ValueError(
            f"{item['subject_id']}: checkpoints {indices} are not dense from 0; the scorer "
            "indexes the map positionally"
        )
    grades = [
        {
            fid: Grade.parse(str(grade))
            for fid, grade in provisional[str(index) if str(index) in provisional else index].items()
            if fid in fields.BY_ID
        }
        for index in indices
    ]
    out = EvidenceMap(str(item["subject_id"]), grades)
    out.validate()
    _verify_grade_leads_value(item)
    _verify_scalar_card_at(item)
    return out


def _verify_grade_leads_value(item: Mapping[str, Any]) -> None:
    """Refuse a map that grades a pruned taste-list field evidenced before its value surfaces.

    **The grade may not lead the value** (2026-08-28). For the four `card_at`-pruned list fields,
    a WEAK or STRONG grade at checkpoint *t* asserts a twin should be able to read the field's
    value there. If `card_at[t]` is still empty and the value only first surfaces at a later
    checkpoint *S*, that assertion is false: a twin correctly holding `[]` is scored against a
    value the transcript has not yet surfaced, and — since `gold_at` reads `card_at` — committing
    the not-yet-surfaced element is the only way to score. That is the WEAK-empty defect the four
    windows carried (gen-002 `cuisines_avoided`, gen-007/022/041 `cuisines_liked`, 2026-08-28):
    on gen-002 it was schedule-authored, on the other three the credit layer's carry-forward wrote
    it, and neither `EvidenceMap.validate` (grades only) nor the oracle (reads the same map) can
    see it — only this cross-check of grade against `card_at` can.

    A field that **never** surfaces has no *S* and is left alone: that is the legitimate
    evidenced-empty case ("no particular diet" → the constraint list is WEAK and correctly empty
    for the whole timeline), where committing `[]` is the right move and earns credit.
    """
    card_at = item.get("card_at") or {}
    provisional = item.get("provisional") or {}
    evidenced = {Grade.WEAK.name, Grade.STRONG.name}
    for fid in _PRUNED_LIST_FIELDS:
        keys = sorted(card_at, key=int)
        surface = next((int(k) for k in keys if card_at[k].get(fid)), None)
        if surface is None:
            continue
        for k in keys:
            if int(k) >= surface:
                break
            if provisional.get(k, {}).get(fid) in evidenced:
                raise ValueError(
                    f"{item['subject_id']} c{k} {fid}: graded "
                    f"{provisional[k][fid]} but its value first surfaces at c{surface} "
                    f"(card_at empty until then); the grade may not lead the value — this "
                    f"cell must be UNEVIDENCED"
                )


def _verify_scalar_card_at(item: Mapping[str, Any]) -> None:
    """Gate 1 for the per-checkpoint **scalar** overlay fields (`_PER_CHECKPOINT_SCALAR_FIELDS`).

    The list gate above keys on an *empty* `card_at` sentinel — a scalar's `card_at[t]` is never
    empty, so that check is vacuous for scalars and cannot see the scalar grade-leads-value defect
    (end-state level written at every checkpoint; gen-020/024 `solo_dining_comfort`, gen-022
    `formality_ceiling`, 2026-08-28). This gate enforces the structural invariant the fix produced,
    over a field's **evidenced** (WEAK/STRONG) checkpoints only — the UNEVIDENCED prefix is unscored
    and its value irrelevant:

    1. every evidenced `card_at` value is a real vocab level;
    2. the ordinal distance to the final card value is **non-increasing** across evidenced
       checkpoints — the value approaches its end state monotonically, never zig-zagging or
       overshooting-then-returning;
    3. the last evidenced checkpoint's value **equals** the final card value — the end state is
       actually reached.

    **What this gate does NOT catch — the reviewer's job (read this before promoting a scalar edit).**
    The original defect — the end-state level written at *every* checkpoint — is distance-0 throughout,
    which trivially satisfies (1)–(3). A model-free gate cannot see it, because whether a level is
    *readable by t* is a fact about the transcript, not the map. So an **LLM/human review** of any
    per-checkpoint scalar cell must additionally verify, for each evidenced checkpoint *t*:

        the `card_at[t]` level is the highest level a competent reader can commit to from the
        USER turns **through t** — and in particular is NOT the end-state level before the
        transcript surfaces it (quote the turn that first surfaces each level; if the only cue for
        the final level appears at *t+k*, the value at *t*..*t+k-1* must be the lower level, not the
        end state).

    That transcript-vs-value check is what caught all three cells; it is `Gate 2` (an automated
    version needs the generator to author a per-checkpoint surfaced-level, which is not built)
    and is not yet mechanized. The scalar tier of `docs/GT-REVIEW.md` has the worked cases.
    """
    card_at = item.get("card_at") or {}
    provisional = item.get("provisional") or {}
    card = item.get("card") or {}
    evidenced = {Grade.WEAK.name, Grade.STRONG.name}
    keys = sorted(card_at, key=int)
    for fid in _PER_CHECKPOINT_SCALAR_FIELDS:
        spec = fields.BY_ID.get(fid)
        if spec is None or not spec.levels:
            continue
        order = {lvl: i for i, lvl in enumerate(spec.levels)}
        seq = [(int(k), card_at[k].get(fid)) for k in keys
               if provisional.get(k, {}).get(fid) in evidenced and card_at[k].get(fid) is not None]
        if not seq:
            continue  # field carries no per-checkpoint evidenced value here; nothing to check
        final = card.get(fid)
        if final not in order:
            raise ValueError(
                f"{item['subject_id']} {fid}: final card value {final!r} is not one of "
                f"{spec.levels}"
            )
        prev_dist: int | None = None
        for k, val in seq:
            if val not in order:
                raise ValueError(
                    f"{item['subject_id']} c{k} {fid}: card_at value {val!r} is not one of "
                    f"{spec.levels}"
                )
            dist = abs(order[val] - order[final])
            if prev_dist is not None and dist > prev_dist:
                raise ValueError(
                    f"{item['subject_id']} c{k} {fid}: value {val!r} moves further from the final "
                    f"value {final!r} than the previous evidenced checkpoint — a per-checkpoint "
                    f"scalar must approach its end state monotonically (no zig-zag or overshoot)"
                )
            prev_dist = dist
        last_k, last_val = seq[-1]
        if last_val != final:
            raise ValueError(
                f"{item['subject_id']} c{last_k} {fid}: last evidenced value {last_val!r} != final "
                f"card value {final!r}; the end state must be reached by the last evidenced checkpoint"
            )


def scorer_subject(item: Mapping[str, Any], *, author: str = "") -> Subject:
    """A corpus item as `subject.Subject` — gold, map and the scripted events §8.6 reads.

    `gold` is the card at the **final** checkpoint and earlier ones are reconstructed by
    rewinding the corrections, which is `Subject.gold_at`'s contract. The corpus also ships
    `card_at` per checkpoint; `verify_gold` checks the two agree rather than choosing between
    them, because a rewind that disagreed with the published table would score every corrected
    field against a card nobody authored.
    """
    events = [
        ScriptedEvent(
            kind="correction",
            checkpoint=int(c["checkpoint"]),
            field_id=str(c["field_id"]),
            old_value=c["old_value"],
            new_value=c["new_value"],
        )
        for c in item.get("corrections") or ()
    ]
    events += [
        ScriptedEvent(kind="distractor", checkpoint=int(c))
        for c in item.get("distractor_checkpoints") or ()
    ]
    return Subject(
        subject_id=str(item["subject_id"]),
        gold=PersonaCard(str(item["subject_id"]), dict(item["card"])),
        evidence=evidence_map(item),
        tier=PLACEHOLDER_TIER,
        simulator_model=author,
        events=events,
        evidenced_gold_at=_evidenced_gold_at(item),
        hard_case_tags=tuple(item.get("hard_case_tags") or ()),
        split=str(item.get("split") or "public"),
    )


#: List fields whose card assigns a full multi-element set but whose transcript surfaces only a
#: subset. A WEAK grade holds the twin to the whole set including never-surfaced elements, which
#: scores a faithful reader below abstaining (route-2 phantom prune, 2026-08-27). `gold_at` reads
#: the evidenced-so-far subset from `card_at` for exactly these fields; every other field's gold
#: is the card, rewound by corrections, unchanged. Defined once in `fields`; aliased here.
_PRUNED_LIST_FIELDS: frozenset[str] = fields.PRUNED_LIST_FIELDS

#: Scalar ordinal fields whose card_at holds the point-in-time evidenced *level* rather than the
#: final card value — the scalar analog of the phantom-list prune (2026-08-28). A progressively-
#: revealed trait (gen-024 `solo_dining_comfort` climbs takeaway_or_counter→comfortable_at_table→
#: enjoys_solo; gen-022 `formality_ceiling` climbs smart_casual→white_tablecloth) had its end-state
#: written into `card_at` at *every* checkpoint, so a faithful reader was charged a misread at the
#: early checkpoints where the transcript had only surfaced a lower level. Reading `card_at` here
#: scores the level the evidence has reached by *t*, not the end state. On any base whose `card_at`
#: holds the final value at every checkpoint (the default for these fields) the overlay is a no-op,
#: exactly as for the pruned list fields.
#:
#: Extended 2026-08-30 (route-1 ordinal gate, route-1b): `deal_seeking`, `novelty_vs_loyalty` and
#: `noise_preference` carry the same timing defect (e.g. gen-007 `deal_seeking` is graded WEAK from
#: c8 but only price-awareness — `notices_value` — is served until the specials-planning behavior
#: surfaces at c16), so they join the overlay and `_verify_scalar_card_at` now validates them too.
_PER_CHECKPOINT_SCALAR_FIELDS: frozenset[str] = frozenset(
    (
        "solo_dining_comfort",
        "formality_ceiling",
        "deal_seeking",
        "novelty_vs_loyalty",
        "noise_preference",
    )
)

#: Every field whose per-checkpoint gold is read from `card_at` rather than the rewound card. The
#: list-only grade-leads-value gate (`_verify_grade_leads_value`, `credit` Key 1b) still keys on
#: `_PRUNED_LIST_FIELDS` alone — its empty-value surface test is meaningless for an always-present
#: scalar — so the two sets are kept distinct.
_CARD_AT_GOLD_FIELDS: frozenset[str] = _PRUNED_LIST_FIELDS | _PER_CHECKPOINT_SCALAR_FIELDS


def _evidenced_gold_at(item: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    """Per-checkpoint gold read from the corpus's `card_at` table for the overlaid fields.

    On a base that has not been pruned, `card_at[t][field]` equals the full card at every
    checkpoint, so this reproduces `gold_at`'s pre-2026-08-27 behaviour exactly — the overlay is
    a no-op. On a pruned base, `card_at[t][field]` holds the evidenced-so-far subset (list fields)
    or the point-in-time level (scalar fields), and the overlay is what carries it into scoring.
    `card_at` and the rewound card are one fact recorded twice (`verify_gold` checks it); making
    `gold_at` read `card_at` for these fields keeps them that one fact rather than two that can
    drift.
    """
    table: dict[int, dict[str, Any]] = {}
    for key, published in (item.get("card_at") or {}).items():
        overrides = {
            fid: published[fid]
            for fid in _CARD_AT_GOLD_FIELDS
            if fid in published and published[fid] is not None
        }
        if overrides:
            table[int(key)] = overrides
    return table


def verify_gold(subject: Subject, item: Mapping[str, Any]) -> list[str]:
    """Where the rewound card disagrees with the corpus's own `card_at` table.

    Published rather than raised, and checked on every load: the two are one fact recorded
    twice, which is the shape ADR-0033 decision 2 refuses in the schedule and the same shape
    here. A disagreement means the correction log and the card table have drifted, and the
    scorer would then be reading one of them at a checkpoint the other governs.
    """
    problems: list[str] = []
    for index, published in sorted((int(k), v) for k, v in (item.get("card_at") or {}).items()):
        rewound = subject.gold_at(index)
        for fid in fields.ALL_FIELDS_IDS:
            if fid in published and published[fid] != rewound.get(fid):
                problems.append(
                    f"{subject.subject_id} c{index} {fid}: card_at says {published[fid]!r}, "
                    f"rewinding corrections gives {rewound.get(fid)!r}"
                )
    return problems


def serve(
    item: Mapping[str, Any], records: Mapping[str, Mapping[str, Any]], *, author: str = "",
) -> tuple[Served | None, list[str]]:
    """One subject's batches and key, or `None` with the reason it cannot be served.

    The batch at checkpoint *t* is exactly the sessions whose `checkpoint` is *t* — usually one,
    since v0.1 checkpoints sessions 1:1, but the harness never assumes it. A missing or empty
    session makes the whole subject unservable: that is `judge.Bundle.prefix`'s hole rule, and
    the reason is the same one — a timeline with session 2 quietly removed shows evidence in an
    order that never happened, and every checkpoint after it would be scored against a map that
    counted it.
    """
    subject = scorer_subject(item, author=author)
    problems = verify_gold(subject, item)
    count = subject.evidence.checkpoint_count
    batches: list[list[ServedSession]] = [[] for _ in range(count)]
    for task in item["sessions"]:
        record = records.get(str(task["task_id"]))
        text = str((record or {}).get("transcript") or "")
        if not text.strip():
            problems.append(f"{item['subject_id']}: {task['task_id']} is missing or empty")
            return None, problems
        checkpoint = int(task["checkpoint"])
        if not 0 <= checkpoint < count:
            problems.append(
                f"{item['subject_id']}: {task['task_id']} is at checkpoint {checkpoint}, "
                f"outside the map's {count}"
            )
            return None, problems
        batches[checkpoint].append(ServedSession(
            subject_id=str(item["subject_id"]),
            session=int(task["session"]),
            checkpoint=checkpoint,
            date=str(task.get("date") or ""),
            transcript=text,
        ))
    for batch in batches:
        batch.sort(key=lambda s: s.session)
    return Served(subject, tuple(tuple(b) for b in batches)), problems


def load_served(
    corpus: Path | str, store: Path | str, *, author: str = "", subjects: Sequence[str] | None = None,
) -> tuple[list[Served], list[str]]:
    """Every servable subject in a corpus, against one author's store."""
    items = load_items(corpus)
    records = load_store(store)
    author = author or _author_of(records)
    served: list[Served] = []
    problems: list[str] = []
    for subject_id, item in sorted(items.items()):
        if subjects and subject_id not in set(subjects):
            continue
        one, issues = serve(item, records, author=author)
        problems += issues
        if one is not None:
            served.append(one)
    return served, problems


def _author_of(records: Mapping[str, Mapping[str, Any]]) -> str:
    authors = {str(r.get("author") or r.get("model") or "") for r in records.values()}
    return sorted(authors)[0] if len(authors) == 1 else ""


# --- running -----------------------------------------------------------------


def run_one(
    sut: Any, served: Served, *,
    lam: float = scorer.DEFAULT_LAMBDA,
    min_evidenced: int = scorer.MIN_EVIDENCED_FOR_AUFC,
) -> scorer.SubjectScore:
    """Drive one subject end to end and score the trajectory.

    `baselines.run_trajectory` owns the protocol — reset, then one `observe` per checkpoint with
    only that checkpoint's new sessions — so this function and the four baselines cannot drift
    on what §1.3 means.
    """
    cards, byte_meter, token_meter = baselines.run_trajectory(
        sut, served.subject, [list(batch) for batch in served.batches]
    )
    kept, dropped = datapoint_vocab_stats(sut)
    return scorer.score_subject(
        served.subject, cards, lam=lam, min_evidenced=min_evidenced,
        carried_state_bytes=byte_meter, carried_state_tokens=token_meter,
        datapoint_kept=kept, datapoint_dropped=dropped,
    )


def run(
    factory: Callable[[], Any],
    served: Sequence[Served],
    *,
    lam: float = scorer.DEFAULT_LAMBDA,
    min_evidenced: int = scorer.MIN_EVIDENCED_FOR_AUFC,
    workers: int = DEFAULT_WORKERS,
) -> list[scorer.SubjectScore]:
    """Score one submission over many subjects.

    `factory` builds a **fresh SUT per subject** rather than reusing one, which is belt and
    braces over `reset`: a submission that leaks state across subjects can guess unevidenced
    fields profitably, and the harness should not be the only thing standing between it and
    that. Subjects run concurrently; the checkpoints inside one never can, since each is emitted
    from the state the last one left.
    """
    def one_subject(one: Served) -> scorer.SubjectScore:
        return run_one(factory(), one, lam=lam, min_evidenced=min_evidenced)

    if workers <= 1 or len(served) <= 1:
        return [one_subject(one) for one in served]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one_subject, served))


# --- the built-in submissions ------------------------------------------------


#: The submissions that need a model, and therefore the ones `kind:model` is meaningful on.
PAID: frozenset[str] = frozenset({
    "prompted", "notebook", "memory", "mem0",
    "prompted-v2", "notebook-v2", "memory-v2", "mem0-v2",
})

#: Model calls one checkpoint costs each paid kind. `main`'s spend estimate reads this rather
#: than assuming 1 — `MemoryExtractor` is two calls (extract, then integrate) and a dry-run that
#: quoted 230 for it would be wrong by half before the first one was made.
#:
#: `mem0` is also two, and the count is the honest half of what it costs: mem0's extraction system
#: prompt alone is ~8.4k tokens against ~2.8k for the *whole* `prompted` prompt (ADR-0062), so an
#: estimate in calls understates that row's spend against the other three.
CALLS_PER_CHECKPOINT: dict[str, int] = {
    "prompted": 1, "notebook": 1, "memory": 2, "mem0": 2,
    "prompted-v2": 1, "notebook-v2": 1, "memory-v2": 2, "mem0-v2": 2,
}


def prompt_kind(kind: str) -> tuple[str, str]:
    """`"prompted-v2"` -> `("prompted", "v2")`; `"prompted"` -> `("prompted", "v1")`.

    The version is a suffix on the kind rather than a flag on the run so that it is part of the
    submission's identity: the cache directory, the report label and the result JSON all carry
    it, and `--sut prompted --sut prompted-v2` scores both on one table with one paired CI.
    """
    base, _, version = kind.partition("-")
    return base, (version or "v1")


def parse_spec(spec: str, default_model: str) -> tuple[str, str, str]:
    """`"prompted:claude-sonnet-5"` -> `(label, kind, model)`.

    The model belongs to the *submission*, not to the run: a leaderboard row is a system and the
    model it was built on, and two rows differing only in model are two rows. Carrying it in the
    name is what lets one invocation put both on the same table — and what makes the label a
    submission is identified by say which model it was, rather than leaving that in the shell
    history of whoever ran it.

    An OpenRouter route carries its own `/` and no colon, so `openai/gpt-5.6-luna` survives this
    split intact; only the first colon separates.
    """
    kind, _, model = spec.partition(":")
    kind = kind.strip()
    model = model.strip() or default_model
    if kind not in PAID and ":" in spec:
        raise ValueError(
            f"{spec!r}: {kind} makes no model call, so naming a model for it would put a model "
            "on a row that never used one"
        )
    return (f"{kind}:{model}" if kind in PAID else kind), kind, model


def factories(
    specs: Sequence[str], served: Sequence[Served], *,
    model: str, invoke: Invoker | None = None, store: EmissionStore | None = None,
    on_problem: Callable[[str], None] | None = None,
) -> dict[str, Callable[[], Any]]:
    """Label -> factory, for the CLI and for a test that wants the same set.

    Each spec is `kind` or `kind:model` (`parse_spec`), so one run can score the same submission
    on two models and print them side by side. The cache keeps them apart on its own —
    `EmissionStore.path` is `<root>/<kind>/<model>/…` — so adding a second model to a run that
    has already paid for the first costs only the new model's calls.

    `population` is fitted on the **public** split's gold cards, which is the honest version of
    that baseline: fitting it on the split being scored would leak ground truth into the bar the
    headline is read against.
    """
    golds = [one.subject.gold for one in served if one.subject.split == "public"]
    by_id = {one.subject_id: one.subject for one in served}

    def build(kind: str, chosen: str) -> Callable[[], Any]:
        free: dict[str, Callable[[], Any]] = {
            "abstain": baselines.AbstainEverything,
            "random": lambda: baselines.RandomValid(seed=7),
            "population": lambda: baselines.PopulationMode.fit(golds),
            "oracle": lambda: _OracleFor(by_id),
        }
        if kind in free:
            return free[kind]
        base, version = prompt_kind(kind)
        if base == "prompted":
            return lambda: PromptedExtractor(
                model=chosen, invoke=invoke, store=store, on_problem=on_problem,
                name=kind, prompt_version=version)
        if base == "notebook":
            return lambda: NotebookExtractor(
                model=chosen, invoke=invoke, store=store, on_problem=on_problem,
                name=kind, prompt_version=version)
        if base == "memory":
            return lambda: MemoryExtractor(
                model=chosen, invoke=invoke, store=store, on_problem=on_problem,
                name=kind, prompt_version=version)
        if base == "mem0":
            # Lazy, and for `judge.OpenRouter`'s reason: `mem0ai` is an optional extra, and a
            # top-level import would cost every other submission — and `--dry-run` — a dependency
            # it never uses (ADR-0062).
            from .mem0_sut import Mem0Extractor
            return lambda: Mem0Extractor(
                model=chosen, invoke=invoke, store=store, on_problem=on_problem,
                name=kind, prompt_version=version)
        raise ValueError(
            f"unknown submission {kind!r}; have "
            f"{sorted({*free, *PAID})}"
        )

    out: dict[str, Callable[[], Any]] = {}
    for spec in specs:
        label, kind, chosen = parse_spec(spec, model)
        out[label] = build(kind, chosen)
    return out


@dataclass
class _OracleFor:
    """`EvidenceOracle` over whichever subject it is currently pointed at.

    The oracle is constructed per subject in every other caller; the harness builds one SUT per
    subject too, but it does not know which until `reset`. This is that indirection and nothing
    else — it reads ground truth and the map directly, which no submission may do, and it exists
    to assert the scorer's ceiling is 1.0 on this corpus.
    """

    subjects: Mapping[str, Subject]
    _inner: baselines.EvidenceOracle | None = None

    def reset(self, subject_id: str) -> None:
        self._inner = baselines.EvidenceOracle(self.subjects[subject_id])
        self._inner.reset(subject_id)

    def observe(self, sessions: Sequence[Any]) -> PersonaCard:
        return self._inner.observe(sessions)                    # type: ignore[union-attr]

    def carried_state_bytes(self) -> int:
        return 0

    def carried_state_tokens(self) -> int:
        return 0


# --- reporting ---------------------------------------------------------------


def format_report(results: Mapping[str, Sequence[scorer.SubjectScore]]) -> str:
    """One block per submission, then the comparison §8.8 exists to make readable.

    `f_skill` is the column to read first: it divides out the do-nothing floor, so
    `abstain` sits at exactly 0 by construction and anything below it on that column has been
    beaten by silence.
    """
    lines: list[str] = []
    for name, scores in results.items():
        lines.append(f"## {name}")
        lines.append(
            f"  {'subject':<10} {'ckpts':>5} {'AUFC':>7} {'skill':>7} {'final F':>8} "
            f"{'fab':>5} {'stale':>6} {'misread':>8} {'constraint':>11} {'state tok':>10} "
            f"{'off-vocab':>9}"
        )
        for score in sorted(scores, key=lambda s: s.subject_id):
            errors = score.errors_by_kind()
            failures = sum(1 for c in score.checkpoints if c.constraint_failure)
            lines.append(
                f"  {score.subject_id:<10} {score.averaged_checkpoints:>5} "
                f"{_num(score.aufc):>7} {_num(score.aufc_skill):>7} "
                f"{_num(score.final_fidelity):>8} "
                f"{errors[scorer.FABRICATION]:>5} {errors[scorer.STALENESS]:>6} "
                f"{errors[scorer.MISREADING]:>8} "
                f"{failures:>4}/{len(score.checkpoints):<6} "
                f"{_num(score.peak_carried_state_tokens, 0):>10} "
                f"{_pct(score.datapoint_off_vocab_rate()):>9}"
            )
        for cell in scorer.aggregate(scores):
            lines.append(
                f"  {'ALL':<10} {cell.n_scored_subjects:>5} {_num(cell.mean_aufc):>7} "
                f"{_num(cell.mean_aufc_skill):>7} {_num(cell.mean_final_fidelity):>8} "
                f"{cell.total_errors[scorer.FABRICATION]:>5} "
                f"{cell.total_errors[scorer.STALENESS]:>6} "
                f"{cell.total_errors[scorer.MISREADING]:>8} "
                f"{cell.constraint_failure_rate:>11.2f} "
                f"{_num(cell.mean_peak_carried_state_tokens, 0):>10} "
                f"{_pct(cell.datapoint_off_vocab_rate):>9}"
            )
            # The fab/stale/misread tallies above are per checkpoint-field and scale with
            # subject length; this row counts the same errors once per episode (onset + failed
            # update) — the length-independent "how many mistakes" number.
            lines.append(
                f"  {'ALL·epi':<10} {'':>5} {'':>7} {'':>7} {'':>8} "
                f"{cell.total_episodes[scorer.FABRICATION]:>5} "
                f"{cell.total_episodes[scorer.STALENESS]:>6} "
                f"{cell.total_episodes[scorer.MISREADING]:>8} "
                f"{'':>11} {'':>10} {'':>9}"
            )
        lines.append("")

    lines.append("## comparison (mean over subjects, lambda sweep)")
    lines.append(f"  {'submission':<12} {'AUFC':>7} {'skill':>7} {'l=0.5':>7} {'l=2.0':>7}")
    for name, scores in results.items():
        cells = scorer.aggregate(scores)
        by_lambda: dict[float, list[float]] = {}
        for cell in cells:
            for lam, value in cell.mean_aufc_by_lambda.items():
                by_lambda.setdefault(lam, []).append(value)
        mean_aufc = [c.mean_aufc for c in cells if c.mean_aufc is not None]
        mean_skill = [c.mean_aufc_skill for c in cells if c.mean_aufc_skill is not None]
        lines.append(
            f"  {name:<12} {_num(_avg(mean_aufc)):>7} {_num(_avg(mean_skill)):>7} "
            f"{_num(_avg(by_lambda.get(0.5))):>7} {_num(_avg(by_lambda.get(2.0))):>7}"
        )

    lines.extend(_significance_section(results))
    lines.extend(_stability_section(results))
    return "\n".join(lines)


#: Resamples and RNG seed for the paired bootstrap over subjects. Fixed so the CI is a
#: property of the run, not of the draw — a re-score of the same emissions reprints the same
#: interval. Sweeping it is meaningless; changing it only adds Monte-Carlo noise.
BOOTSTRAP_RESAMPLES = 10000
BOOTSTRAP_SEED = 0


def _percentile_ci(samples: list[float], alpha: float = 0.05) -> tuple[float, float]:
    """The [alpha/2, 1-alpha/2] percentile interval of a sorted-on-demand sample."""
    ordered = sorted(samples)
    n = len(ordered)
    lo = ordered[int((alpha / 2) * n)]
    hi = ordered[min(n - 1, int((1 - alpha / 2) * n))]
    return lo, hi


def _significance_section(
    results: Mapping[str, Sequence[scorer.SubjectScore]]
) -> list[str]:
    """Per-submission AUFC CIs and pairwise paired-bootstrap gaps over subjects (avenue #1).

    The headline table prints point AUFCs — 0.861 vs 0.852 reads like a result, but with 50
    subjects the gap can sit well inside the noise. This resamples subjects (the unit, §8.9)
    to put an interval on each mean and on every pairwise gap, so an ordering that does not
    survive the resample is visibly not a finding. AUFC is taken at the default λ; a subject
    with no averageable checkpoint (AUFC None) is dropped from its own submission's CI and
    from any pair it appears in.
    """
    per_subject: dict[str, dict[str, float]] = {}
    for name, scores in results.items():
        per_subject[name] = {
            s.subject_id: s.aufc for s in scores if s.aufc is not None
        }
    names = [n for n in results if per_subject[n]]
    if not names:
        return []

    rng = random.Random(BOOTSTRAP_SEED)
    lines = ["", f"## significance (paired bootstrap over subjects, "
             f"B={BOOTSTRAP_RESAMPLES}, seed={BOOTSTRAP_SEED})"]
    lines.append(f"  {'submission':<28} {'mean AUFC':>9} {'95% CI':>18}  n")
    for name in names:
        vals = list(per_subject[name].values())
        means = [
            _avg([vals[rng.randrange(len(vals))] for _ in vals])
            for _ in range(BOOTSTRAP_RESAMPLES)
        ]
        lo, hi = _percentile_ci([m for m in means if m is not None])
        lines.append(
            f"  {name:<28} {_num(_avg(vals)):>9} "
            f"{'[' + _num(lo) + ', ' + _num(hi) + ']':>18}  {len(vals)}"
        )

    if len(names) >= 2:
        lines.append("")
        lines.append(f"  {'pair (A vs B)':<40} {'ΔAUFC':>7} {'95% CI':>18} "
                     f"{'P(A>B)':>7}  wins")
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = names[i], names[j]
                shared = sorted(set(per_subject[a]) & set(per_subject[b]))
                if not shared:
                    continue
                diffs = [per_subject[a][s] - per_subject[b][s] for s in shared]
                boot = [
                    _avg([diffs[rng.randrange(len(diffs))] for _ in diffs])
                    for _ in range(BOOTSTRAP_RESAMPLES)
                ]
                lo, hi = _percentile_ci(boot)
                p_gt = sum(1 for m in boot if m > 0) / len(boot)
                wins = sum(1 for d in diffs if d > 1e-9)
                losses = sum(1 for d in diffs if d < -1e-9)
                ci = "[" + _num(lo) + ", " + _num(hi) + "]"
                lines.append(
                    f"  {a + ' vs ' + b:<40} {_signed(_avg(diffs)):>7} {ci:>18} "
                    f"{p_gt:>7.2f}  {wins}-{losses}"
                )
    return lines


def _signed(value: float | None, places: int = 3) -> str:
    if value is None:
        return "-"
    return f"{value:+.{places}f}"


#: The grid the ordering is stress-tested on (avenue A). Each axis is a scoring knob the AUFC
#: is computed against; a submission ordering that holds only at one corner is an artifact of
#: the knob, not a fact about the twins. λ reuses the scorer's reported set; the WEAK_CREDIT
#: window brackets the default ±0.08 (the credit is generous for sets, stingy for
#: distributions — `metrics.weak_abstention_credit` — so both directions are the ones that
#: could move a set-heavy or distribution-heavy submission); MIN_EVIDENCED brackets the
#: density gate. The default cell (`DEFAULT_LAMBDA`, `WEAK_CREDIT`, `MIN_EVIDENCED_FOR_AUFC`)
#: is always in the grid, so its ΔAUFC matches the comparison table exactly.
STABILITY_MIN_EVIDENCED: tuple[int, ...] = (5, scorer.MIN_EVIDENCED_FOR_AUFC, 10)
STABILITY_WEAK_CREDITS: tuple[float, ...] = tuple(sorted({
    round(metrics.WEAK_CREDIT - 0.08, 3),
    metrics.WEAK_CREDIT,
    round(metrics.WEAK_CREDIT + 0.08, 3),
}))


def _recompute_f(
    checkpoint: scorer.CheckpointScore, lam: float, weak_credit: float
) -> float | None:
    """`F(t)` re-derived at a different λ and WEAK_CREDIT, from the stored outcomes alone.

    No re-score and no emitted card is needed because WEAK_CREDIT is a flat constant
    (`metrics.weak_abstention_credit`): the only cells that carry it are weak fields the SUT
    abstained on, and those are exactly identifiable in the stored `FieldOutcome`s (WEAK grade,
    not committed, counted in A). Everything else — committed agreements, strong misses scored
    0, the fabrication count — is fixed regardless of the knobs. Substituting `weak_credit` for
    those cells and re-pricing the penalty at `lam` reproduces `score_checkpoint` exactly at the
    default knobs (a unit test pins it), and this is only valid while the credit stays flat —
    when §7.3 emits a licensed set per weak field the sweep must move to a re-score.
    """
    denom = checkpoint.a_denominator
    if denom == 0:
        return None                                  # not scorable — no defined A
    numerator = 0.0
    for outcome in checkpoint.outcomes:
        if not outcome.counted_in_a:
            continue
        if outcome.grade == Grade.WEAK and not outcome.committed:
            numerator += weak_credit                 # the one cell the credit reaches
        else:
            numerator += outcome.agreement           # committed value, or a strong miss's 0.0
    a = numerator / denom
    f_raw = a - lam * checkpoint.fabrication_count / denom
    return max(0.0, min(1.0, f_raw))


def _aufc_at(
    score: scorer.SubjectScore, lam: float, weak_credit: float, min_evidenced: int
) -> float | None:
    """Mean F over the subject's averageable checkpoints at one grid cell, or None if the gate
    leaves nothing to average."""
    values = [
        f for c in score.checkpoints
        if c.a_denominator >= min_evidenced
        and (f := _recompute_f(c, lam, weak_credit)) is not None
    ]
    return sum(values) / len(values) if values else None


def _stability_section(
    results: Mapping[str, Sequence[scorer.SubjectScore]]
) -> list[str]:
    """Whether each pairwise ordering survives the scoring knobs (avenue A).

    The significance block asks whether a gap beats the subject noise; this asks the orthogonal
    question — does the gap even point the same way as λ, WEAK_CREDIT and the density gate move?
    For every pair it recomputes each submission's mean AUFC at every cell of the knob grid
    (subjects where both are defined at that cell), and reports the default-cell gap, the range
    of the gap across the whole grid, and whether the sign ever flips. An ordering that flips is
    a property of the knob, not a finding.
    """
    names = list(results)
    if len(names) < 2:
        return []

    grid = [
        (lam, credit, m)
        for lam in scorer.REPORTED_LAMBDAS
        for credit in STABILITY_WEAK_CREDITS
        for m in STABILITY_MIN_EVIDENCED
    ]
    default_cell = (scorer.DEFAULT_LAMBDA, metrics.WEAK_CREDIT,
                    scorer.MIN_EVIDENCED_FOR_AUFC)
    # subject_id -> {cell -> aufc}, one map per submission.
    per: dict[str, dict[str, dict[tuple, float | None]]] = {}
    for name, scores in results.items():
        per[name] = {
            s.subject_id: {cell: _aufc_at(s, *cell) for cell in grid}
            for s in scores
        }

    def cell_gap(a: str, b: str, cell: tuple) -> float | None:
        pa, pb = per[a], per[b]
        shared = [
            s for s in pa if s in pb
            and pa[s][cell] is not None and pb[s][cell] is not None
        ]
        if not shared:
            return None
        return (sum(pa[s][cell] for s in shared) / len(shared)
                - sum(pb[s][cell] for s in shared) / len(shared))

    lines = ["", f"## ordering stability (λ×weak_credit×min_evidenced, {len(grid)} cells; "
             f"credits {', '.join(f'{c:.3f}' for c in STABILITY_WEAK_CREDITS)}, "
             f"m {', '.join(map(str, STABILITY_MIN_EVIDENCED))})"]
    lines.append(f"  {'pair (A vs B)':<40} {'Δ@default':>9} {'grid Δ range':>20} verdict")
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            gaps = {cell: cell_gap(a, b, cell) for cell in grid}
            defined = {c: g for c, g in gaps.items() if g is not None}
            if not defined:
                continue
            d0 = gaps.get(default_cell)
            lo, hi = min(defined.values()), max(defined.values())
            a_ahead = sum(1 for g in defined.values() if g > 1e-9)
            b_ahead = sum(1 for g in defined.values() if g < -1e-9)
            n = len(defined)
            if a_ahead == n:
                verdict = f"stable ({a} ahead {n}/{n})"
            elif b_ahead == n:
                verdict = f"stable ({b} ahead {n}/{n})"
            else:
                verdict = f"FLIPS ({a} {a_ahead}/{n}, {b} {b_ahead}/{n})"
            rng = "[" + _signed(lo) + ", " + _signed(hi) + "]"
            lines.append(
                f"  {a + ' vs ' + b:<40} {_signed(d0):>9} {rng:>20} {verdict}"
            )
    return lines


def _num(value: float | int | None, places: int = 3) -> str:
    if value is None:
        return "-"
    return f"{value:.{places}f}" if places else f"{value:.0f}"


def _pct(rate: float | None) -> str:
    """A drop rate as a percent, or `-` for a submission that emitted no datapoints."""
    return "-" if rate is None else f"{rate * 100:.1f}%"


def _avg(values: Sequence[float] | None) -> float | None:
    return sum(values) / len(values) if values else None


def to_json(results: Mapping[str, Sequence[scorer.SubjectScore]]) -> dict[str, Any]:
    """The same numbers, for a `runs/result-*.txt` to be scored against."""
    return {
        name: {
            "subjects": {
                s.subject_id: {
                    "aufc": s.aufc, "aufc_skill": s.aufc_skill,
                    "final_fidelity": s.final_fidelity,
                    "scorable_checkpoints": s.scorable_checkpoints,
                    "averaged_checkpoints": s.averaged_checkpoints,
                    "time_to_threshold": s.time_to_threshold,
                    "regression_count": s.regression_count,
                    "errors": s.errors_by_kind(),
                    "error_episodes": s.error_episodes,
                    "constraint_failures": sum(
                        1 for c in s.checkpoints if c.constraint_failure),
                    "peak_carried_state_tokens": s.peak_carried_state_tokens,
                    "datapoints_kept": s.datapoint_kept,
                    "datapoints_dropped": s.datapoint_dropped,
                    "datapoint_off_vocab_rate": s.datapoint_off_vocab_rate(),
                    "f_curve": [c.f for c in s.checkpoints],
                }
                for s in sorted(scores, key=lambda s: s.subject_id)
            },
            "aggregate": [vars(cell) for cell in scorer.aggregate(scores)],
        }
        for name, scores in results.items()
    }


# --- CLI ---------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:      # pragma: no cover - CLI
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True,
                        help="one author's directory of authored sessions")
    parser.add_argument("--sut", action="append", default=None,
                        help="repeatable, as `kind` or `kind:model`. free: abstain, random, "
                             "population, oracle. spends calls: prompted, notebook, memory "
                             "(2 calls/checkpoint: extract, then integrate). "
                             "`--sut prompted:claude-opus-5 --sut prompted:claude-sonnet-5` "
                             "puts two models on one table")
    parser.add_argument("--model", default="claude-opus-5",
                        help="the model for any --sut that does not name its own")
    parser.add_argument("--subjects", nargs="*", default=None)
    parser.add_argument("--emissions", type=Path, default=None,
                        help="cache directory for model replies")
    parser.add_argument("--replay-cached", action="store_true",
                        help="return cached replies by position (subject, checkpoint, phase), "
                             "skipping the prompt-fingerprint gate. For re-scoring an UNCHANGED run "
                             "against a corrected answer key only — the SUT never sees gold, so a "
                             "gold-only change cannot move any prompt. Makes mem0 (whose bank-order "
                             "prompt never fingerprints twice) re-scorable for free. Never use after "
                             "re-authoring the store: the transcripts would have moved for real.")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--lam", type=float, default=scorer.DEFAULT_LAMBDA)
    parser.add_argument("--min-evidenced", type=int, default=scorer.MIN_EVIDENCED_FOR_AUFC,
                        help="ADR-0004's AUFC gate: a checkpoint evidencing fewer fields than "
                             "this is scored and published but kept out of the mean")
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="load and serve, print what would be spent, call nothing")
    args = parser.parse_args(argv)

    names = args.sut or ["abstain", "population", "oracle"]
    served, problems = load_served(
        args.corpus, args.store, subjects=args.subjects)
    for problem in problems:
        print(f"! {problem}", file=sys.stderr)
    if not served:
        print("nothing servable; see the problems above", file=sys.stderr)
        return 1

    checkpoints = sum(len(one.batches) for one in served)
    paid = [n for n in names if parse_spec(n, args.model)[1] in PAID]
    print(f"{len(served)} subjects, {checkpoints} checkpoints, "
          f"{sum(len(b) for one in served for b in one.batches)} sessions")
    if paid:
        paid_calls = sum(
            checkpoints * CALLS_PER_CHECKPOINT.get(parse_spec(n, args.model)[1], 1) for n in paid)
        print(f"up to {paid_calls} model calls for {paid} "
              f"(minus whatever the cache holds)")
    if args.dry_run:
        return 0

    store = EmissionStore(args.emissions, replay=args.replay_cached) if args.emissions else None
    if args.replay_cached and store is None:
        print("--replay-cached needs --emissions", file=sys.stderr)
        return 1
    complaints: list[str] = []
    built = factories(
        names, served, model=args.model, store=store, on_problem=complaints.append)

    results = {
        name: run(factory, served, lam=args.lam, min_evidenced=args.min_evidenced,
                  workers=args.workers)
        for name, factory in built.items()
    }
    print()
    print(format_report(results))
    for complaint in complaints:
        print(f"! {complaint}", file=sys.stderr)
    if args.json:
        args.json.write_text(json.dumps(to_json(results), indent=2, default=str))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":      # pragma: no cover - CLI
    raise SystemExit(main())
