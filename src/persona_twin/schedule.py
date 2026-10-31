"""The v0.1 evidence schedule: one lane, one timeline per subject, extensible by construction.

What changed from v0, and why
-----------------------------

v0 had three tiers (sparse/medium/rich) that differed in length *and* in structure. v0.1
has **one lane**. A subject is a single timeline; "sparse behaviour" is what you read at an
early checkpoint of it, and every subject passes through that state on its way to a richer
one. The evidence tier stops being a property of the subject and becomes a property of the
prefix you read.

The rule that makes this work, and the one rule this module exists to keep:

    **The scheduler never looks forward.**

Session *i*'s beats are a function of ``(seed, subject_id, i)`` and of sessions ``< i``.
Nothing — not beat placement, not session length, not the distractor draw, not a
correction's landing site — may depend on ``session_count``. That is what makes a timeline
**extensible**: raising a subject from 8 sessions to 25 leaves sessions 0..7 byte-identical,
so their transcripts cache-hit in `author.py` and their gradings cache-hit in `judge.py`
(the grading prompt says "sessions 1 to k of a longer timeline" and never names a total).
Only the new sessions cost anything. `tests/test_schedule.py::test_extension_is_prefix_stable`
is the guard, and it is not optional decoration: v0's `build_schedule` drew `session_count`
first and then scattered beats with `rng.choice(signal_sessions)`, so changing the length
moved every beat in the timeline and re-authoring the whole subject was the only option.

How a field gets revealed
-------------------------

Each field carries its own independent plan, drawn on its own RNG stream from
``(seed, subject_id, field_id)``:

* an **onset** — the session where it first surfaces, drawn from an exponential whose mean
  is set by the subject's own disclosure pace. Fields with a late onset simply never appear
  in a short timeline, and that is where the unevidenced band comes from. v0 had to *reserve*
  fields as never-scheduled (`must_unevidenced`); here it falls out of the length.
* a **cadence** — gaps between that field's successive beats.
* a **move sequence** — narrowing moves (`reaction`, `anecdote`) first, then pinning moves
  (`request`, `rejection`) until the field's threshold is met. So every field's trajectory is
  UNEVIDENCED → WEAK → … → STRONG, and a field reaches STRONG only when the transcript has
  actually pinned it.

Because the plan is finite and per-field, "eventually everything is revealed" is reachable:
`beats_to_pin_all` reports the cost, and a long enough timeline pays it. A short one does not,
which is the point.

Per-subject variation in *how fast* someone reveals themselves is `_pace` — the thing v0
could not express, because its `_BAND_BUDGET` fixed the strong/weak counts per tier and every
subject in a tier disclosed at the same rate.

Difficulty lanes
----------------

Distractors, corrections and interference bursts are scheduled for **every** subject. Two of
the three are marked `removable` on the session, so an "easy" lane can be taken by dropping
those sessions:

* **distractor** sessions carry no persona signal and nothing back-references them;
* **interference** sessions are a dense burst on one facet group and are likewise
  self-contained.

**Corrections are not removable and are not marked so.** A correction changes ground truth,
so every later session expresses the post-correction value; drop the correction and the gold
card at that prefix says one thing while the transcript shows another, which is a
constructibility violation — the one dataset criterion that still blocks (v0 ADR-0030).

Dropping sessions changes every downstream prefix, so an easy lane needs its own grading
pass. Nothing here assumes one has been run.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field as dc_field, replace as dc_replace
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Sequence

from . import fields
from .evidence import EvidenceMap, Grade
from .card import PersonaCard
from .subject import ScriptedEvent

DEFAULT_SEED: int = 7


# --- conversational moves (unchanged from v0 §5) -----------------------------


@dataclass(frozen=True)
class Move:
    name: str
    weight: int
    definition: str


MOVES: dict[str, Move] = {
    "request": Move(
        "request", 2,
        "The user asks for the thing directly. Names the level they need, so it pins an "
        "ordinal on its own: 'somewhere quiet enough that we can actually talk.'",
    ),
    "rejection": Move(
        "rejection", 2,
        "The user refuses a candidate and says what was wrong with it. Pins by exclusion, "
        "which is why it is worth the same as a request: 'not that one, it's a warehouse.'",
    ),
    "reaction": Move(
        "reaction", 1,
        "The user responds to a place they went. Narrows from one side without fixing the "
        "level — 'it was way too loud' rules out the top but not the other three.",
    ),
    "anecdote": Move(
        "anecdote", 1,
        "The preference surfaces inside a story told for another reason. The weakest move "
        "and the one §6.1 most wants, since it cannot recite a schema value.",
    ),
    "disclosure": Move(
        "disclosure", 2,
        "The user is *asked* and answers in the negative: they have none of this "
        "constraint. Worth what a request is worth, for the same reason — a hard "
        "constraint is declared, not inferred, and 'no, nothing like that' declares it "
        "as flatly as naming one would. The only move the bot initiates, and it has to "
        "be: a preference can be volunteered, an absence essentially cannot. A user who "
        "spontaneously says 'I don't have any allergies' is reciting a profile, which is "
        "what §6.1 rule 3 bans and what the leakage audit fails a transcript for.",
    ),
}

MOVE_NAMES: tuple[str, ...] = tuple(MOVES)

#: Moves that pin, and moves that only narrow. A narrowing beat never accumulates toward
#: STRONG however often it recurs — it is briefed with a licensed interval and forbidden to
#: perform either level distinctly, so two of them still leave a reader unable to choose.
PINNING: tuple[str, ...] = ("request", "rejection", "disclosure")
NARROWING: tuple[str, ...] = ("reaction", "anecdote")

#: The pinning moves a *preference* field may draw. `disclosure` pins (it is in `PINNING`,
#: so `_map_of` accumulates its weight) but is never drawn: it is issued only by the absent
#: -constraint branch of `field_plan`, which is the one place a bot asks and a user says no.
PREFERENCE_PINNING: tuple[str, ...] = ("request", "rejection")

#: The move that puts an absent hard constraint on the record. See ADR-0035.
DISCLOSURE: str = "disclosure"

#: Whose the value on the page is. See `Beat.attributed` and ADR-0036.
USER: str = "user"
THIRD_PARTY: str = "third_party"
ATTRIBUTIONS: tuple[str, ...] = (USER, THIRD_PARTY)

#: Fields a companion may plausibly own in a conversation about where to go together. Hard
#: constraints dominate because that is how the defect actually appeared — a companion's
#: dietary rule or access need is the ordinary reason a group's venue is constrained, and it
#: is stated in exactly the register a real constraint of the subject's would be.
THIRD_PARTY_FIELDS: tuple[str, ...] = (
    "dietary", "allergies", "accessibility_needs", "alcohol_free_required",
    "cuisines_avoided",
)

#: Earliest session that may carry the companion's constraint. Not session 0 or 1: the trap is
#: only a trap once the subject's own record has started, and a reader with nothing to compare
#: against is being asked a different question.
THIRD_PARTY_EARLIEST: int = 2

#: Accumulated pinning weight at which a preference field is predicted STRONG, by metric.
#: Sets and distributions cost more because one mention cannot establish an extent or a
#: shape — the reader knows a member or a bin, not the field's value.
PIN_THRESHOLD: dict[str, int] = {
    fields.METRIC_ORDINAL: 2,
    fields.METRIC_EXACT: 2,
    fields.METRIC_JACCARD: 4,
    # Occasions, not move weights — see `evidence_weight`. Kept at 5 and now *justified*
    # rather than assumed: drawing k occasions from each of the 50 gold distributions and
    # scoring the empirical shape against the card gives mean 1−JSD of 0.578 at k=1, 0.766 at
    # k=2, 0.826 at k=3, 0.902 at **k=5**, 0.934 at k=8, 0.946 at k=10. The share of
    # subject-fields reconstructing above 0.80 is 63% at k=3 and 93% at k=5. Five is the knee;
    # everything past it buys a third of a point per occasion. Re-measure this if
    # `generator.py` ever makes the gold distributions less concentrated, because the curve is
    # this steep only because they carry their mass on two bins.
    fields.METRIC_JSD: 5,
}

#: Hard constraints are **declared, not inferred**, so they pin at the ordinal cost whatever
#: their metric. "I'm allergic to peanuts" closes the set; no amount of further conversation
#: makes it more closed.
CONSTRAINT_PIN_THRESHOLD: int = 2


def evidence_weight(beat: "Beat") -> int:
    """What one beat contributes toward its field's threshold.

    For every metric but one this is the move's weight: a request pins and a reaction
    narrows, and the difference is the whole §5 model.

    **A distribution field is the exception, and it always was.** `_distribution_instruction`
    briefs a scheduled distribution beat as *"exactly ONE bin in this session, as one concrete
    occasion"* — it is forbidden from listing bins or comparing their frequencies, because
    that is the §6.1 collision the brief exists to avoid. So a `request` on a distribution
    puts exactly as much on the page as an `anecdote` does: one occasion, in one bin. Pricing
    it at 2 counted one observation twice, and the arithmetic only survived because scenery
    occasions were not counted at all. ADR-0037 counts them, so the unit has to be the
    occasion.
    """
    if fields.BY_ID[beat.field_id].metric == fields.METRIC_JSD:
        return 1
    return beat.weight if beat.move in PINNING else 0


def pin_threshold(field_id: str) -> int:
    """Weight at which `field_id` is predicted STRONG."""
    spec = fields.BY_ID[field_id]
    if spec.is_constraint:
        return CONSTRAINT_PIN_THRESHOLD
    return PIN_THRESHOLD[spec.metric]


#: Fields whose move pool is restricted, because some moves cannot carry some fields: you do
#: not *react* to your own allergy, and a travel radius rarely surfaces as a rejection.
_MOVE_BIAS: dict[str, tuple[str, ...]] = {
    "allergies": ("request",),
    "dietary": ("request",),
    "accessibility_needs": ("request",),
    "alcohol_free_required": ("request",),
}


# --- session shape -----------------------------------------------------------

#: Turns per session. v0 hardcoded "8-16 turns" into the authoring prompt for every session;
#: real conversations are mostly short, and the length is now drawn per session.
#:
#: The ceiling was 16 and the pilot measured that it was never reached: briefed 6 → 6.5
#: realised user turns and 8 → 8.5, but **16 → 10.8**. A session is briefed `capacity` beats
#: and then runs out of things to do, so the tail of the range asked for turns no author
#: produced. Briefing a length that does not arrive is not a harmless overshoot — it is the
#: brief asserting something the page then contradicts, which is the same class of defect
#: ADR-0036 found on the content side. The ceiling is now set to where the measurement
#: actually lands. See ADR-0038.
TURNS_MIN: int = 6
TURNS_MAX: int = 12

#: Beats a session of `turns` turns may carry. Coupled to length rather than drawn
#: independently: four beats in a six-turn session is a user reciting preferences, which is
#: what the §7.2 leakage audit fails a transcript for.
#:
#: Loosened 3.5 → 3.0 alongside the shorter ceiling. Cutting `TURNS_MAX` alone would have cut
#: per-session capacity with it; 3.0 holds a non-distractor session's mean capacity at 3.00
#: beats against 3.18 before, so the corpus loses the unfillable turns without losing the
#: evidence they were nominally carrying. It is a loosening in the direction that does not
#: buy length: 6 turns still carries 2 beats, which is what the coupling exists to protect.
TURNS_PER_BEAT: float = 3.0

#: The briefed length of a session is **derived from the beats that actually landed in it**,
#: not from the draw above. `TURNS_MIN/MAX` and `TURNS_PER_BEAT` stay exactly what they were —
#: the *placement budget*, drawn without looking forward, which is what bounds how much a
#: session may be asked to carry. What changes is which number reaches the author: a session
#: briefed 12 turns that placement then filled with 3 beats was asking for 4 turns per beat,
#: and the turns with nothing to carry are padding the author has to invent something for.
#:
#: The pilot (`runs/corpus-pilot-constrained.json`, 57 sessions) measured both halves of the
#: gap. On the signal side, 4 of 46 non-distractor sessions came in under their capacity, the
#: worst a capacity-4 session that placement filled with one beat. On the zero-signal side it
#: is not an outlier but the rule: **all 11 distractors carry no beats by construction and were
#: briefed 6 to 12 turns anyway**, up to a dozen turns of conversation written to evidence
#: nothing.
#:
#: Deriving length from realised load closes both. The rate is deliberately gentler than the
#: capacity guard (2.0 against 3.0) with a constant to sit it on, so the curve shortens the
#: thin end without stretching the rich end past where ADR-0038 measured authors stop
#: honouring the number: 1 beat → 4 turns, 4 → 10. Against the pilot's realised beat counts
#: that takes the corpus mean from 8.7 briefed turns to ~7.4.
#:
#: **This is still prefix-stable, and that is not incidental.** The beats in session *i* are a
#: function of `(seed, subject_id, i)` and of sessions `< i` — the invariant this whole module
#: exists to keep — so a length derived from them inherits it. Raising a subject from 8
#: sessions to 25 still leaves sessions 0..7 byte-identical, briefs included.
#:
#: A consequence worth naming: with length fixed at a rate per beat, `_no_session_over_capacity`
#: becomes true by construction rather than by check. The check stays — it guards the
#: *budget*, which is what stops placement over-filling a session in the first place.
TURNS_BASE: int = 2
TURNS_PER_BEAT_BRIEF: float = 2.0
TURNS_JITTER: int = 1
TURNS_FLOOR: int = 3

#: A distractor evidences nothing, so there is no load for its length to follow. It gets a
#: short fixed band instead of the draw: enough room for the scenario to be a real
#: conversation, not enough to need filling. The scenarios in `transcript.DISTRACTOR_SCENARIOS`
#: are self-contained errands and none of them needs more.
DISTRACTOR_TURNS: tuple[int, int] = (4, 5)


def briefed_turns(session: int, beat_count: int, *, is_distractor: bool = False,
                  subject_id: str = "", seed: int = DEFAULT_SEED) -> int:
    """How long a session is briefed to be, given what actually landed in it.

    Reads `beat_count` and the session index only — never the total session count, and never
    a later session. The jitter is drawn on its own stream so it moves no other plan.
    """
    rng = random.Random(f"{seed}|{subject_id}|turns|{session}")
    if is_distractor:
        return rng.randint(*DISTRACTOR_TURNS)
    target = TURNS_BASE + TURNS_PER_BEAT_BRIEF * beat_count
    jitter = rng.randint(-TURNS_JITTER, TURNS_JITTER)
    return max(TURNS_FLOOR, min(TURNS_MAX, round(target) + jitter))

#: How often a session carries no persona signal at all. Kept for every subject — people do
#: talk about irrelevant things, and §8.6's distractor stability needs a zero-signal
#: checkpoint to measure card movement against.
DISTRACTOR_RATE: float = 0.14

#: The rate above is a per-session roll, and a roll gives no per-subject guarantee: at 0.14
#: over the four eligible sessions of a six-session subject, about half go without. Measured
#: at `n=50, seed=7`: the pooled realised rate was correct within noise and **21 of 50
#: subjects still had no distractor at all**, two of them 25 sessions long. §8.6's stability
#: claim is per subject, so a rate that is right on average and absent for 42% of the corpus
#: does not support it.
#:
#: The fix is a deadline rather than a first-eligible placement. Rolling until the deadline
#: keeps the spread — a distractor that always lands on session 2 is as uninformative as none
#: at all — and forcing at it makes the guarantee unconditional for any subject long enough to
#: reach it. Both halves read only the index and what has already been placed, so the
#: prefix-stability invariant is untouched. See ADR-0038.
DISTRACTOR_DEADLINE: int = 4

#: Days between consecutive sessions. A twin is a point-in-time object, so sessions carry
#: dates rather than bare indices: gaps are signal ("haven't been out in two months"), and a
#: visit-time distribution is only meaningful over a stated period.
SESSION_GAP_DAYS: tuple[int, ...] = (2, 3, 4, 5, 7, 9, 11, 14, 18, 24, 31, 45)

#: Fixed so a corpus is reproducible. Session 0 lands within a year after this.
EPOCH: date = date(2025, 1, 6)


def capacity(turns: int) -> int:
    """Beats a session of `turns` turns may carry."""
    return max(1, round(turns / TURNS_PER_BEAT))


@dataclass(frozen=True)
class SessionShape:
    """Everything about session *i* that does not depend on which beats land in it.

    Drawn from `(seed, subject_id, i)` alone — no other session, and never the total count.
    """

    session: int
    turns: int
    is_distractor: bool
    gap_days: int

    @property
    def capacity(self) -> int:
        return 0 if self.is_distractor else capacity(self.turns)


def session_shape(subject_id: str, session: int, *, seed: int = DEFAULT_SEED) -> SessionShape:
    rng = random.Random(f"{seed}|{subject_id}|session|{session}")
    turns = rng.randint(TURNS_MIN, TURNS_MAX)
    # Never session 0 or 1: §8.6 measures movement against a settled card, and there is
    # nothing settled to be stable against at the start of a timeline.
    is_distractor = session >= 2 and rng.random() < DISTRACTOR_RATE
    gap = 0 if session == 0 else rng.choice(SESSION_GAP_DAYS)
    return SessionShape(session, turns, is_distractor, gap)


#: Planning styles whose outing is **today**: the conversation and the meal are the same day,
#: so the session's own weekday has to agree with the visit-time bin drawn for it.
SAME_DAY_STYLES: frozenset[str] = frozenset({"fully_spontaneous", "decides_same_day"})

#: Visit-time bins that only happen at a weekend. `late-night` is deliberately absent — it
#: sits on either side of the week and constrains nothing.
WEEKEND_BINS: frozenset[str] = frozenset({"weekend-day", "weekend-evening"})

#: Weekday bins, the mirror of `WEEKEND_BINS`.
WEEKDAY_BINS: frozenset[str] = frozenset({"weekday-lunch", "weekday-evening"})


def _alignment_days(when: date, bin_id: str | None) -> int:
    """Days to add so `when` falls on the day-class the drawn visit-time bin needs.

    Zero unless the two disagree, and never negative — a session may only move later, so the
    dates stay strictly increasing and the prefix stays stable.

    This exists because the alternative was a contradiction the author could not write its way
    out of. A `fully_spontaneous` subject decides on the pavement, so the outing is today; if
    the session lands on a Tuesday and the bin drawn for it is `weekend-evening`, then either
    the page books four days ahead — contradicting the card — or it moves the meal off the bin
    the map counted. **The calendar is the thing that yields**: dates are `EPOCH` plus a draw
    from `SESSION_GAP_DAYS` and mean nothing on their own, while the bin comes from the card's
    gold distribution and is a scoring input. The cost is that an aligned gap is up to six days
    longer than the one drawn, which `verify` knows about and reports as its own finding.

    The reader arm found the defect this closes on 2026-08-18: `gen-004-s9`, card
    `fully_spontaneous`, page *"Requesting a table for two … one o'clock Saturday"*.
    """
    if bin_id in WEEKEND_BINS:
        # Sunday is already a weekend day and must not be pushed six days to the next
        # Saturday — the first draft did exactly that, and the test caught it.
        return 0 if when.weekday() >= 5 else 5 - when.weekday()
    if bin_id in WEEKDAY_BINS and when.weekday() >= 5:
        return 7 - when.weekday()
    return 0


def session_dates(
    subject_id: str, count: int, *, seed: int = DEFAULT_SEED, card: PersonaCard | None = None,
) -> list[date]:
    """Calendar date of each session. Prefix-stable: extending appends, never shifts.

    Given a `card`, a same-day subject's sessions are aligned to the visit-time bin drawn for
    them — see `_alignment_days`. Without one the dates are the raw gap draw, which is what
    every caller that only wants the calendar scaffold gets.
    """
    style = str((card or {}).get("planning_style") or "") if card is not None else ""
    align = style in SAME_DAY_STYLES
    start = EPOCH + timedelta(days=random.Random(f"{seed}|{subject_id}|start").randint(0, 364))
    out, cursor = [], start
    for index in range(count):
        cursor += timedelta(days=session_shape(subject_id, index, seed=seed).gap_days)
        if align:
            drawn = incidental_draws(subject_id, index, card, seed=seed)
            cursor += timedelta(
                days=_alignment_days(cursor, drawn.get("visit_time_distribution"))
            )
        out.append(cursor)
    return out


# --- the per-field reveal plan -----------------------------------------------

#: Mean onset session, before the subject's pace multiplier. A field whose onset lands past
#: the end of a timeline is simply never evidenced in it.
MEAN_ONSET: float = 6.0

#: Per-subject disclosure pace. Below 1.0 is someone who reveals themselves quickly; above,
#: someone who takes a long time to say what they want. The thing v0's per-tier band budget
#: could not express.
PACE_RANGE: tuple[float, float] = (0.55, 1.9)

#: Sessions between a field's last narrowing beat and its first pinning beat. Heavy-tailed on
#: purpose — see `field_plan`. The long entries are what keep a weak band alive at any length.
PIN_DELAY: tuple[int, ...] = (0, 0, 1, 1, 2, 3, 4, 6, 9, 14, 22)


def pace(subject_id: str, *, seed: int = DEFAULT_SEED) -> float:
    rng = random.Random(f"{seed}|{subject_id}|pace")
    return round(rng.uniform(*PACE_RANGE), 3)


@dataclass(frozen=True)
class PlannedBeat:
    """One beat in a field's own plan, before contention with other fields is resolved."""

    session: int          # where the field *wants* to land; deferral may push it later
    field_id: str
    move: str
    bin: str | None = None
    note: str = ""


def field_plan(
    field_id: str, card: PersonaCard, subject_id: str, *, seed: int = DEFAULT_SEED
) -> list[PlannedBeat]:
    """Every beat this field will ever need, in order, on its own RNG stream.

    Finite by construction: narrowing beats first, then pinning beats until the field's
    threshold is met, then nothing. So "eventually revealed" is a length the timeline either
    reaches or does not — never a budget divided across a known number of sessions.

    The stream is keyed on the field, so adding a field, changing another field's plan, or
    extending the timeline cannot perturb this one. v0 learned this the expensive way with
    `bin_rng`: sampling a distribution bin from the shared stream shifted every later draw
    and reshuffled beat placement for *every* field.
    """
    spec = fields.BY_ID[field_id]
    rng = random.Random(f"{seed}|{subject_id}|field|{field_id}")
    onset = int(rng.expovariate(1.0 / (MEAN_ONSET * pace(subject_id, seed=seed))))

    if not carries(field_id, card):
        # An absent hard constraint: one beat, where something asks and the user says no.
        #
        # **The onset is drawn from the same stream as any other field and is deliberately
        # not forced early** (ADR-0035). The tempting implementation is "ask in session 1,
        # done", and it would evidence every constraint almost immediately — losing the
        # long-lived UNEVIDENCED constraint cells that test whether a twin invents a dietary
        # requirement nobody mentioned. A short timeline that never reaches this beat leaves
        # the field unevidenced, where abstaining is the correct answer and committing to the
        # empty set is a fabrication. That is the rule, unchanged; the data stops producing
        # the case where it reads wrong.
        return [PlannedBeat(
            session=onset,
            field_id=field_id,
            move=DISCLOSURE,
            bin=None,
            note="the person has none of these, and this beat puts that on the record",
        )]

    pool = _MOVE_BIAS.get(field_id)
    narrowing = 0 if pool else rng.choice((1, 1, 2))
    threshold = pin_threshold(field_id)

    moves: list[str] = [rng.choice(NARROWING) for _ in range(narrowing)]
    weight = 0
    while weight < threshold:
        moves.append(rng.choice(pool or PREFERENCE_PINNING))
        weight += MOVES[moves[-1]].weight

    # The gap before the field's *first pinning* beat, on its own draw and heavy-tailed.
    #
    # Without this the weak band empties out on a long timeline: every field that has been
    # heard at all gets pinned a few sessions later, so by session 25 the map is all STRONG
    # and UNEVIDENCED with nothing in between — measured at 21/0/3 on the 25-session pilot
    # subject before this existed. That is the wrong shape. A persistent weak band is the
    # benchmark's whole subject matter (v0's shipped map is majority-WEAK at every tier), and
    # it exists because some preferences are genuinely slow to pin, not because the timeline
    # is short. `PIN_DELAY` is that slowness, and its tail is what keeps a handful of fields
    # hinted-but-unpinned no matter how long the timeline runs.
    pin_delay = rng.choice(PIN_DELAY)

    plan: list[PlannedBeat] = []
    cursor = onset
    for index, move in enumerate(moves):
        if index == narrowing and narrowing:
            cursor += pin_delay
        plan.append(PlannedBeat(
            session=cursor,
            field_id=field_id,
            move=move,
            bin=_bin_for(spec, card, index, rng),
            note="narrows without pinning" if move in NARROWING else "",
        ))
        cursor += rng.randint(1, 4)
    return plan


def _bin_for(
    spec: fields.Field, card: PersonaCard, index: int, rng: random.Random
) -> str | None:
    """One bucket of a distribution field for one beat. `None` for every other field.

    **The first beat always briefs the mode; later beats sample the shape.** Both halves are
    load-bearing and both are v0 findings. Sampling every beat is what makes a beat sequence
    a sample of the distribution rather than a repeated performance of its mode (v0
    ADR-0028). But one IID draw is a coin flip, not a sample: at n=1 it lands on the mode only
    as often as the mode's own weight, and when it misses it lands somewhere that scores far
    worse against gold under JSD — measured at 0.672 against a mode-only 0.679.

    v0 fixed that by looking at how many beats the field ended up with and rewriting the
    single-beat case to the mode. **That is a look-forward and it cannot survive here**: a
    timeline extended from 8 sessions to 25 would turn a one-beat field into a three-beat
    field and silently change the first beat's brief, breaking every cached transcript before
    it. Pinning the mode to *index 0* gets the same protection out of a rule that only reads
    the field's own position in its own plan.
    """
    if spec.metric != fields.METRIC_JSD:
        return None
    value = card.get(spec.field_id)
    if not value:
        return None
    if index == 0:
        return max(value, key=value.get)
    bins, weights = list(value.keys()), list(value.values())
    if sum(weights) <= 0:
        return rng.choice(bins)
    return rng.choices(bins, weights=weights, k=1)[0]


#: How often an ordinary session instantiates each distribution field in passing. An outing
#: has a party size and a time whether or not anyone is making a point of them; what it costs
#: comes up less often.
#:
#: The pilot's unbidden instances — 27 party-size, 4 visit-time, 2 price-band across 67
#: sessions — support the **ranking** and nothing more. They are a detection floor, not a
#: rate: `BIN_SURFACE` matches a short list of phrases ("four of us", "on my own"), so a
#: session that says "me and Ruth" is a party of two that no probe counted. The true rate for
#: party size is near 1 — you cannot book a table without one — which is what these numbers
#: encode.
#: **Party size and time are 1.0 because they are not optional.** They were 0.9 and 0.85 for
#: one authoring run, and the run measured what the gap costs: in the sessions where nothing
#: was drawn the author invented one anyway — *"three of us"* twice for a subject whose card
#: gives `3-5` a weight of 0.01, and *"mid-range"* for one that gives `$$` 0.01. A field the
#: conversation cannot avoid instantiating must be drawn every time or it is drawn by the
#: author, and the author has no card. Price is genuinely avoidable — plenty of these
#: conversations never settle a number — so it keeps a rate, and the brief tells the author
#: when cost does not come up.
INCIDENTAL_RATE: dict[str, float] = {
    "party_size_distribution": 1.0,
    "visit_time_distribution": 1.0,
    "price_band_distribution": 0.5,
}

#: What one observed occasion is worth toward reading a shape. Equal to a narrowing beat's
#: weight, because on the page it *is* a narrowing beat: `_distribution_instruction` briefs a
#: scheduled distribution beat as "exactly ONE bin, as one concrete occasion", which is
#: precisely what scenery already carried. Two occasions that a reader cannot tell apart
#: cannot be worth different amounts.
INCIDENTAL_WEIGHT: int = 1


#: Which occasion bins are evidence about an ordinal the schedule never placed a beat on.
#:
#: ADR-0037 counted the occasion for the distribution field it instantiates. This is the other
#: half, and it took the reader arm to find it: the draw is made **from the card**, and the
#: generator's own coherence rules tie these pairs together, so a session that says *"table for
#: one"* has said something about `solo_dining_comfort` whether or not anything scheduled it.
#: Both pairs are same-cluster (`fields.CLUSTERS` C3, C2), the C2 pair is exactly that cluster's
#: declared `generator._ANTI_FLIP` pair, and C3 carries the hard `solo_comfort_conflicts_party_sizes`
#: coherence rule — which is *why* `_ANTI_FLIP` picks `noise_preference` there instead. Across
#: all five pilot cards the correlation is exceptionless.
#:
#: So this is not the map crediting author invention, which ADR-0034 closed by putting the
#: schedule in charge: the occasion draw is schedule state, and the credit is derivable with no
#: model call. What was leaking was never the scenery's wording — `runs/result-scenery-narrowing.txt`
#: refuted that, at 16 of 16 exposed sessions with zero attitude matches — it was the map
#: failing to count evidence the schedule itself placed.
#:
#: Deliberately not extended to the other two C2 members (`formality_ceiling`,
#: `design_sensitivity`). The reader saw each once; one sighting is not the reading that put
#: these two here, and a table extended past its evidence is how bare tokens get in.
ENTAILED_BY_BIN: dict[tuple[str, str], str] = {
    ("party_size_distribution", "1"): "solo_dining_comfort",
    ("price_band_distribution", "£30-50"): "deal_seeking",
    ("price_band_distribution", "£50+"): "deal_seeking",
}

#: The lead time an outing is arranged at, and the occasion that carries it.
#:
#: This is the same problem `ENTAILED_BY_BIN` solves, arriving by a different route. A page that
#: says when the outing is, in a conversation that is happening now, has also said how far ahead
#: this person decides — there is no way to arrange an outing without the gap between the two
#: dates being on the page. The brief already draws that gap **from the card** rather than
#: letting the author guess it (`transcript._HORIZON_PHRASING`), so the value on the page is
#: right; what was missing was any record that it is there.
#:
#: The cost of the gap was measured on the pilot and it is the single largest defect in the
#: corpus: `planning_style` carries **20 of the 42 blocking leaks and zero contradictions**. Zero
#: contradictions is the proof that the page is not at fault — the lead time never disagrees with
#: the card, because it is drawn from it. Twenty leaks is the map calling a cell UNEVIDENCED that
#: the page plainly carries, and a twin that reads it correctly and commits is scored as
#: fabricating. The three fields already counted as occasions — party size, visit time, price —
#: raise **zero** leaks between them for exactly this reason: the map knows they are there.
#:
#: Credited as an entailment rather than as an `Incidental`, which is the whole of the design:
#: an entailment reaches WEAK and stops. A lead time mentioned in passing narrows the field, it
#: does not pin it, and accumulating one per session would otherwise walk `planning_style` to
#: STRONG on scenery alone.
#:
#: **Nothing about placement changes.** `_map_of` does not read entailments — the generation-time
#: map is beats and occasions only — so the beats, the occasions and therefore every authored
#: brief are byte-identical to what they were before this existed. What moves is the derived map,
#: which is what was wrong.
HORIZON_FIELD: str = "planning_style"
HORIZON_SOURCE: str = "visit_time_distribution"


def horizon_lands(*, occasions: Iterable[str], here: Iterable[str]) -> bool:
    """Does this session's scenery state a lead time?

    The one condition, and it must stay the one condition on both sides: the brief emits the
    horizon only where it has a "when" sentence to hang it on and `planning_style` is not being
    written deliberately here. `transcript.continuity_for` calls this rather than restating it —
    a map that credits a cell the brief did not ask for is the failure this shares a predicate
    to prevent, and it would be silent.

    ``here`` is every field with a beat in this session at any attribution, matching the brief's
    own suppression: a counter-beat still puts the field on the page, and the map's
    `counts_toward_gold` line is deliberately not drawn here.
    """
    drawn, spoken = set(occasions), set(here)
    return (
        HORIZON_SOURCE in drawn
        and HORIZON_SOURCE not in spoken
        and HORIZON_FIELD not in spoken
    )


#: The formality of the outing, and the ceiling it is evidence about.
#:
#: The third instance of one problem, after the lead time and the venue pool: **the scenery only
#: knows how to book a table.** `_OCCASION_LABELS` says who is going, when, and roughly what it
#: costs, and never what kind of place this is — so the author writes the only outing the app
#: appears to offer, a reserved sit-down table, for every subject regardless of their card.
#:
#: For a person whose ceiling is high that costs nothing: any venue is under it. For a person
#: capped at `counter_service` it is unwritable, and the 2026-08-21 pilot measured exactly that
#: shape. `formality_ceiling` raised **11 contradictions, 13 of its 15 blocking findings on the
#: three `counter_service` subjects and ZERO on the two `white_tablecloth` ones**, every one of
#: them the same sentence: the card caps at counter service, the user books a sit-down table. In
#: several the user *rejects the counter option and demands a table* — the author writing
#: against the card because the brief gave it nothing else to write.
#:
#: So the outing's format is drawn here, from the card, exactly as party size and visit time
#: are, and the brief states it. A ceiling is not a distribution, so the draw is uniform over
#: the levels **at or below** it: capped at `counter_service` that is deterministic, which is
#: the point — every outing is then one the person would actually go on.
#:
#: Credited as an ENTAILMENT rather than as an `Incidental`, and the distinction is the whole
#: containment argument. `_map_of` reads occasions and feeds `emap_so_far` into placement, so an
#: `Incidental` here would move other fields' beats across the whole corpus. An entailment is
#: not read by `_map_of`, so **every beat and every placement decision is unchanged** — the same
#: property ADR-0044 relied on. What changes is the brief's scenery and the derived map.
FORMALITY_FIELD: str = "formality_ceiling"


def formality_occasion(
    subject_id: str, session: int, card: PersonaCard, *, seed: int = DEFAULT_SEED,
) -> str:
    """The formality of this session's outing: a level at or below the card's ceiling.

    Deterministic in `(seed, subject_id, session)` on its own RNG stream, so it is
    prefix-stable and cannot shift any other field's plan — the first of the three rules that
    keep the invariant true.
    """
    ceiling = card.get(FORMALITY_FIELD)
    levels = list(getattr(fields.BY_ID.get(FORMALITY_FIELD), "levels", ()) or ())
    if not levels or ceiling not in levels:
        return ""
    permitted = levels[: levels.index(str(ceiling)) + 1]
    rng = random.Random(f"{seed}|{subject_id}|formality|{session}")
    return rng.choice(permitted)


def formality_lands(
    drawn: str, card: PersonaCard, *, here: Iterable[str] = ()
) -> bool:
    """Does this session's outing evidence where the ceiling actually sits?

    Only when the outing is drawn AT the ceiling. An outing below it says the person went
    somewhere casual, which every level above `counter_service` also permits — it does not
    narrow the ceiling, and counting it would credit a cell the page does not support. At
    `counter_service` the draw is always the ceiling, which is why the subjects that carried
    every contradiction are the ones this credits from session 0.

    Suppressed where the field has a beat here, for the same reason `horizon_lands` suppresses
    its own: the field is being written deliberately and the map already counts that.
    """
    return bool(drawn) and drawn == card.get(FORMALITY_FIELD) and FORMALITY_FIELD not in set(here)


#: How many entailing occasions before the ordinal is credited. One, because one *"table for
#: one"* is one observation of the arrangement and the grade it buys is WEAK — the band that
#: says a reader could tell, not the band that says the page pins it. Two would move 51 cells
#: instead of 63 and would be a claim about how much a reader accumulates, which nothing here
#: has measured. Entailment never reaches STRONG at any count: it is inference from a
#: correlate, and pinning is for the field's own evidence.
ENTAILMENT_THRESHOLD: int = 1


@dataclass(frozen=True)
class Incidental:
    """One distribution bin instantiated as scenery, rather than as a scheduled beat.

    A conversation about where to go cannot avoid saying who is coming and when, so these
    happen whether or not anything schedules them — the pilot proved it, with 33 unbidden bin
    instances across 67 sessions and, worse, **three of eight subject-fields where the
    instances contradicted the card outright.** The choice is not whether they exist; it is
    whether they are drawn from the card and counted, or invented and ignored.

    ADR-0037 draws and counts them. They carry no `move` and get no beat block in the brief:
    the author is told what this outing looks like, not to make a point of it.
    """

    session: int
    field_id: str
    bin: str

    def to_json(self) -> dict[str, Any]:
        return {"session": self.session, "field_id": self.field_id, "bin": self.bin}


def incidental_draws(
    subject_id: str, session: int, card: PersonaCard, *, seed: int = DEFAULT_SEED,
) -> dict[str, str]:
    """The distribution bins this session instantiates in passing, drawn from the card.

    Deterministic in `(seed, subject_id, session)` and reading nothing else, so it is
    prefix-stable for the same reason `session_shape` is. Drawn **from the gold distribution**,
    which is the whole point: an unbidden instance the author invents is a coin flip against
    the card, and across a timeline those flips are what a reader accumulates.
    """
    out: dict[str, str] = {}
    for field_id, rate in sorted(INCIDENTAL_RATE.items()):
        value = card.get(field_id)
        if not isinstance(value, Mapping) or not value:
            continue
        rng = random.Random(f"{seed}|{subject_id}|incidental|{field_id}|{session}")
        if rng.random() >= rate:
            continue
        bins, weights = list(value.keys()), list(value.values())
        if sum(weights) <= 0:                          # pragma: no cover - defensive
            continue
        out[field_id] = rng.choices(bins, weights=weights, k=1)[0]
    return out


def carries(field_id: str, card: PersonaCard) -> bool:
    """Does this person actually have this hard constraint?

    `False` for `allergies: []`, `alcohol_free_required: False` — the person has none. Always
    `True` for a preference field, which has no absent state: an ordinal always sits at some
    level. This is the branch `field_plan` reads to decide *which kind* of beat a constraint
    gets, not whether it gets one; see `schedulable`.
    """
    spec = fields.BY_ID[field_id]
    if not spec.is_constraint:
        return True
    value = card.get(field_id)
    if value == fields.UNKNOWN:
        return False
    if isinstance(value, bool):
        return value
    return bool(value)


def schedulable(field_id: str, card: PersonaCard) -> bool:
    """Can this field be evidenced at all?

    **This used to read "a hard constraint the person does not carry cannot be evidenced",
    and ADR-0035 is the record of why that was wrong.** An absence is disclosable — a booking
    conversation asks whether there is anything it needs to know, and "no, nothing like that"
    puts the empty set on the record as flatly as naming an allergy would. What could not be
    evidenced was never the absence; it was the absence *under a schedule that had no move for
    being asked*.

    The cost of the old reading was measured on the pilot: **16 of 20 constraint cells were
    unschedulable**, and two of five subjects carried no schedulable constraint at all, so
    §8.2's gating track — constraint recall and precision, the safety-critical half — had
    nothing to measure on them. It also made ADR-0033 decision 3 false as written: the
    unevidenced band was supposed to be a length effect rather than a reservation, and these
    cells were permanently unevidenced at any length.

    **This now excludes nothing `generator.generate` emits**, and that is the point of the
    change rather than an oversight — no generated card sets a constraint to `UNKNOWN`. The
    guard is kept because a card read from elsewhere may, and because the seam is where a
    genuinely unevidenceable field would have to be handled. Do not read it as a live filter.
    The distinction that is live is `carries`.
    """
    spec = fields.BY_ID[field_id]
    if not spec.is_constraint:
        return True
    return card.get(field_id) != fields.UNKNOWN


def beats_to_pin_all(card: PersonaCard) -> int:
    """Beats a timeline must land to reveal this card completely.

    The price of "eventually everything is revealed", reported rather than assumed — it is
    what sets how long a full-reveal subject has to be.
    """
    total = 0
    for spec in fields.ALL_FIELDS:
        if not schedulable(spec.field_id, card):
            continue
        if not carries(spec.field_id, card):
            total += 1          # one disclosure beat, and it pins on its own
            continue
        pool = _MOVE_BIAS.get(spec.field_id)
        threshold = pin_threshold(spec.field_id)
        pinning = -(-threshold // 2)          # every pinning move is worth 2
        total += pinning + (0 if pool else 1)  # + one narrowing beat, minimum
    return total


# --- the schedule ------------------------------------------------------------


@dataclass(frozen=True)
class Beat:
    """One scheduled moment where a field surfaces in one session.

    ``supports_gold=False`` marks the scripted self-report of a stated-vs-revealed subject: a
    turn that evidences the *opposite* of ground truth on purpose. It is the one beat kind
    that never raises a grade.

    ``attributed`` says **whose** the value on the page is. ``"third_party"`` marks a
    companion's constraint: it is stated plainly, it is true, and it is not the subject's, so
    it moves no grade either. The two flags are deliberately separate because they are
    different traps — a counter-beat is the subject saying something false about themselves,
    a third-party beat is somebody else saying something true about *themselves*. A reader
    that collapses either into the card fabricates, but for opposite reasons, and a dataset
    that cannot tell them apart cannot report which mistake a twin made.

    ``foreign_value`` is the companion's value, carried on the beat because it is by
    construction *not* on the card — the card is where the subject's values live, and the
    whole point is that this one never goes there.

    ``bin`` is set only for a distribution field's beat — see `_bin_for`.
    """

    session: int
    field_id: str
    move: str
    supports_gold: bool = True
    note: str = ""
    bin: str | None = None
    attributed: str = "user"
    foreign_value: Any = None

    def __post_init__(self) -> None:
        if self.move not in MOVES:
            raise ValueError(f"unknown conversational move {self.move!r}")
        if self.field_id not in fields.BY_ID:
            raise ValueError(f"beat on unknown field {self.field_id!r}")
        if self.session < 0:
            raise ValueError("session index must be non-negative")
        if self.bin is not None and self.bin not in fields.BY_ID[self.field_id].levels:
            raise ValueError(f"bin {self.bin!r} is not a level of {self.field_id!r}")
        if self.attributed not in ATTRIBUTIONS:
            raise ValueError(f"unknown attribution {self.attributed!r}")
        if self.attributed == THIRD_PARTY and self.foreign_value is None:
            raise ValueError("a third-party beat must carry the companion's value")
        if self.attributed == USER and self.foreign_value is not None:
            raise ValueError("only a third-party beat may carry a foreign value")

    @property
    def weight(self) -> int:
        return MOVES[self.move].weight

    @property
    def counts_toward_gold(self) -> bool:
        """Whether this beat may raise a grade on the subject's own card.

        The single predicate every derived map reads. Both exclusions were live defects
        before they were flags: `must_unevidenced` was computed from *every* beat's field, so
        a field touched only by a counter-beat or only by a companion left the never-evidenced
        list while the subject's own record on it stayed empty.
        """
        return self.supports_gold and self.attributed == USER

    def to_json(self) -> dict[str, Any]:
        return {
            "session": self.session, "field_id": self.field_id, "move": self.move,
            "supports_gold": self.supports_gold, "note": self.note, "bin": self.bin,
            "attributed": self.attributed, "foreign_value": self.foreign_value,
        }


class ScheduleError(ValueError):
    """A schedule violated a placement rule. Never repaired silently."""


@dataclass
class EvidenceSchedule:
    """One subject's timeline: sessions, beats, scripted events, dates.

    **Checkpoints are sessions** in v0.1 — one card emission per conversation. v0 checkpointed
    rich every two sessions to hold grading cost down; that is a grading decision, and grading
    is being revisited, so the schedule stops encoding it.
    """

    subject_id: str
    session_count: int
    #: The seed the timeline was drawn under. Carried so a *derived* draw — `turns_in`'s
    #: jitter — can reproduce the same stream without the caller having to remember it. Never
    #: read by placement, which takes `seed` as an argument.
    seed: int = DEFAULT_SEED
    beats: list[Beat] = dc_field(default_factory=list)
    #: Distribution bins the sessions instantiate as scenery. Not beats — they carry no move
    #: and get no block in the brief — but they are evidence a reader accumulates, so the map
    #: counts them. See ADR-0037.
    incidentals: list[Incidental] = dc_field(default_factory=list)
    events: list[ScriptedEvent] = dc_field(default_factory=list)
    shapes: tuple[SessionShape, ...] = ()
    dates: tuple[str, ...] = ()
    pace: float = 1.0
    directives: tuple[str, ...] = ()
    anti_correlated: tuple[str, ...] = ()
    hard_case_tags: tuple[str, ...] = ()
    deferred_depth: tuple[int, ...] = ()   # queue length after each session, for diagnostics
    #: The formality drawn for each session's outing, and the card's ceiling it was drawn
    #: under. Both are schedule *inputs* — the draw and the card value — never a derived
    #: answer, so `formality_sessions` stays derived for the same reason `horizon_sessions`
    #: is: a corpus drawn before this existed re-derives the same result and no serialized
    #: field can go stale against it.
    formality: tuple[str, ...] = ()
    formality_ceiling: str = ""

    # -- structure ---------------------------------------------------------

    @property
    def checkpoint_count(self) -> int:
        return self.session_count

    def checkpoint_of(self, session: int) -> int:
        return session

    def beats_in(self, session: int) -> list[Beat]:
        return [b for b in self.beats if b.session == session]

    def shape_of(self, session: int) -> SessionShape:
        return self.shapes[session]

    def turns_in(self, session: int) -> int:
        """The briefed length of a session, derived from the beats that landed in it.

        `shape_of(session).turns` is the *budget* placement drew against; this is the number
        the author is given. They differ whenever placement filled a session below its
        capacity, and always for a distractor, which has no load to follow.
        """
        return briefed_turns(
            session, len(self.beats_in(session)),
            is_distractor=session in self.distractor_sessions,
            subject_id=self.subject_id, seed=self.seed,
        )

    @property
    def distractor_sessions(self) -> tuple[int, ...]:
        carrying = {b.session for b in self.beats}
        return tuple(s for s in range(self.session_count) if s not in carrying)

    @property
    def distractor_checkpoints(self) -> tuple[int, ...]:
        return self.distractor_sessions

    def events_of(self, kind: str) -> list[ScriptedEvent]:
        return [e for e in self.events if e.kind == kind]

    def removable_sessions(self) -> tuple[int, ...]:
        """Sessions an "easy" lane may drop — distractor and interference only.

        Corrections are excluded on purpose: dropping one leaves later sessions expressing a
        value the prefix no longer evidences. See the module docstring.

        **The subtraction is not belt-and-braces.** Until ADR-0037 this held by luck — a
        correction and an interference burst had simply never landed on the same session, so
        "distractor or interference" and "not a correction" agreed everywhere it was checked.
        Counting scenery occasions moves the map, `_burst_group` reads the map to choose when
        to fire, and the two collided immediately. A structural invariant that survives on the
        arithmetic of an unrelated feature is not an invariant.
        """
        interference = {e.checkpoint for e in self.events_of("interference")}
        corrections = {e.checkpoint for e in self.events_of("correction")}
        return tuple(
            sorted((set(self.distractor_sessions) | interference) - corrections)
        )

    # -- the provisional map -----------------------------------------------

    def formality_sessions(self) -> tuple[int, ...]:
        """Sessions whose outing is drawn AT the ceiling, and so evidence where it sits.

        An outing *below* the ceiling is not evidence about it: every level above
        `counter_service` also permits a casual meal, so seeing one narrows nothing. Only an
        outing at the top of what this person would book says where the top is. At a
        `counter_service` ceiling every draw is the ceiling — which is why the three subjects
        that carried all 11 contradictions are credited from session 0.
        """
        ceiling = self.formality_ceiling
        if not ceiling or not self.formality:
            return ()
        out: list[int] = []
        for checkpoint in range(self.checkpoint_count):
            if checkpoint >= len(self.formality):
                break
            here = [b.field_id for b in self.beats_in(checkpoint)]
            if formality_lands(self.formality[checkpoint], {FORMALITY_FIELD: ceiling},
                               here=here):
                out.append(checkpoint)
        return tuple(out)

    def horizon_sessions(self) -> tuple[int, ...]:
        """Sessions whose scenery states a lead time, and so evidence `planning_style`.

        Derived, not stored: it is a function of the occasions and the beats the schedule
        already holds, so a corpus drawn before this existed re-derives the same answer and no
        serialized field can go stale against it. `horizon_lands` is the whole rule and
        `transcript.continuity_for` calls the same one.
        """
        out: list[int] = []
        for checkpoint in range(self.checkpoint_count):
            occasions = [i.field_id for i in self.incidentals if i.session == checkpoint]
            here = [b.field_id for b in self.beats_in(checkpoint)]
            if horizon_lands(occasions=occasions, here=here):
                out.append(checkpoint)
        return tuple(out)

    def provisional_map(self) -> EvidenceMap:
        """Predicted grades per checkpoint, derived from beat placement.

        A field is WEAK once any supporting beat has landed and STRONG once accumulated
        *pinning* weight reaches its threshold. A counter-beat contributes nothing and does
        not hold the field back — v0's `probe_tension/` measured judges grading a contradicted
        field `strong` anyway, so predicting WEAK there would be wrong on exactly the fields
        the hard case is about.
        """
        weights: dict[str, int] = {f.field_id: 0 for f in fields.ALL_FIELDS}
        seen: dict[str, int] = {f.field_id: 0 for f in fields.ALL_FIELDS}
        entailed: dict[str, int] = {f.field_id: 0 for f in fields.ALL_FIELDS}
        horizons = set(self.horizon_sessions())
        formalities = set(self.formality_sessions())
        grades: list[dict[str, Grade]] = []

        for checkpoint in range(self.checkpoint_count):
            for beat in self.beats_in(checkpoint):
                if not beat.counts_toward_gold:
                    continue
                seen[beat.field_id] += 1
                weights[beat.field_id] += evidence_weight(beat)
            for occasion in self.incidentals:
                if occasion.session != checkpoint:
                    continue
                seen[occasion.field_id] += 1
                weights[occasion.field_id] += INCIDENTAL_WEIGHT
                correlate = ENTAILED_BY_BIN.get((occasion.field_id, occasion.bin))
                if correlate is not None:
                    entailed[correlate] += 1
            # The lead time rides on the "when" sentence and is drawn from the card, so it is
            # evidence the same way a correlate is: enough to say a reader could tell, never
            # enough to say the page pinned it.
            if checkpoint in horizons:
                entailed[HORIZON_FIELD] += 1
            # The outing's format is drawn from the card and stated by the brief, so where it
            # is drawn AT the ceiling the page carries where that ceiling sits. Same terms as
            # the lead time: WEAK and no further, because one outing at the top says a reader
            # could tell, not that the page pinned it.
            if checkpoint in formalities:
                entailed[FORMALITY_FIELD] += 1

            per_field: dict[str, Grade] = {}
            for spec in fields.ALL_FIELDS:
                if seen[spec.field_id] == 0:
                    # An entailing occasion reaches WEAK and stops there. It is evidence about
                    # a correlate, not about the field, so it can say "a reader could tell" and
                    # must never say "the page pins it".
                    per_field[spec.field_id] = (
                        Grade.WEAK
                        if entailed[spec.field_id] >= ENTAILMENT_THRESHOLD
                        else Grade.UNEVIDENCED
                    )
                    continue
                per_field[spec.field_id] = (
                    Grade.STRONG if weights[spec.field_id] >= pin_threshold(spec.field_id)
                    else Grade.WEAK
                )
            grades.append(per_field)

        return EvidenceMap(self.subject_id, grades, turn_refs=self._turn_refs())

    def _turn_refs(self) -> dict[str, list[str]]:
        refs: dict[str, list[str]] = {}
        for beat in sorted(self.beats, key=lambda b: (b.field_id, b.session)):
            tag = f"s{beat.session}:{beat.move}"
            if not beat.supports_gold:
                tag += ":counter"
            if beat.attributed == THIRD_PARTY:
                tag += ":third_party"
            refs.setdefault(beat.field_id, []).append(tag)
        for occasion in sorted(self.incidentals, key=lambda i: (i.field_id, i.session)):
            refs.setdefault(occasion.field_id, []).append(
                f"s{occasion.session}:incidental:{occasion.bin}"
            )
            # The entailed field gets a ref of its own, naming the occasion it was read off.
            # A credited cell with no way to say where the credit came from is a grade nobody
            # can audit, which is the objection ADR-0034 raised against judge-sourced maps.
            correlate = ENTAILED_BY_BIN.get((occasion.field_id, occasion.bin))
            if correlate is not None:
                refs.setdefault(correlate, []).append(
                    f"s{occasion.session}:entailed:{occasion.field_id}={occasion.bin}"
                )
        # A credited cell that cannot say where the credit came from is a grade nobody can
        # audit, so the horizon names its session too.
        for session in self.horizon_sessions():
            refs.setdefault(HORIZON_FIELD, []).append(f"s{session}:horizon")
        return refs

    def profile(self, checkpoint: int | None = None) -> dict[str, float]:
        """Predicted strong/weak/unevidenced fractions at a checkpoint (default: last)."""
        emap = self.provisional_map()
        index = emap.checkpoint_count - 1 if checkpoint is None else checkpoint
        total = len(fields.ALL_FIELDS)
        counts = {g.name: 0 for g in Grade}
        for spec in fields.ALL_FIELDS:
            counts[emap.grade(index, spec.field_id).name] += 1
        return {name: counts[name] / total for name in counts}

    # -- validation --------------------------------------------------------

    def validate(self, card: PersonaCard | None = None) -> None:
        problems = self.check(card)
        if problems:
            raise ScheduleError(f"{self.subject_id}: " + "; ".join(problems))

    def check(self, card: PersonaCard | None = None) -> list[str]:
        """Every rule violation, as strings.

        Deliberately shorter than v0's. Gone with the tiers: the per-tier session-count range,
        and `on_target` — the §7.4 profile bands were per-tier targets, and a single lane whose
        profile is a function of *where you read it* has no single profile to hit. The
        constructibility rules that survive are the ones about beats and events being placed
        where the transcript can actually carry them.
        """
        problems: list[str] = []
        for beat in self.beats:
            if beat.session >= self.session_count:
                problems.append(
                    f"beat on {beat.field_id} in session {beat.session}, past the end"
                )
            if self.shapes and self.shapes[beat.session].is_distractor:
                problems.append(
                    f"beat on {beat.field_id} lands in distractor session {beat.session}"
                )
        problems += self._check_capacity()
        problems += self._check_third_party(card)
        problems += self._check_events()
        problems += self._check_directives(card)
        problems += self._check_anti_correlation()
        try:
            self.provisional_map().validate()
        except ValueError as exc:  # pragma: no cover - defensive
            problems.append(f"provisional map is invalid: {exc}")
        return problems

    def _check_third_party(self, card: PersonaCard | None) -> list[str]:
        """The companion's value must be one the subject does not hold.

        This is the rule that keeps the trap a trap. If the companion is gluten-free and so is
        the subject, the beat is corroboration wearing a trap's label, and every reading of
        it — the map's, a judge's, a twin's — is defensible. `third_party_pick` already
        guarantees this; the check exists because a hand-built schedule and a future picker
        both bypass it, and a silently-degraded trap looks exactly like a working one.
        """
        problems: list[str] = []
        for beat in self.beats:
            if beat.attributed != THIRD_PARTY:
                continue
            if beat.counts_toward_gold:            # pragma: no cover - defensive
                problems.append(
                    f"third-party beat on {beat.field_id} is counting toward gold"
                )
            if card is None:
                continue
            held = card.get(beat.field_id)
            foreign = beat.foreign_value
            if isinstance(foreign, list):
                shared = sorted(set(foreign) & set(held or ()))
                if shared:
                    problems.append(
                        f"third-party {beat.field_id} names {shared}, which the subject "
                        "also carries; the beat corroborates instead of misleading"
                    )
            elif held == foreign:
                problems.append(
                    f"third-party {beat.field_id}={foreign!r} matches the subject's own "
                    "value; the beat corroborates instead of misleading"
                )
        return problems

    def _check_capacity(self) -> list[str]:
        """No session may carry more beats than its length can hold without reciting.

        Interference bursts are allowed one over, because a dense burst is the point.
        """
        problems: list[str] = []
        burst = {e.checkpoint for e in self.events_of("interference")}
        for session in range(self.session_count):
            landed = len(self.beats_in(session))
            allowed = self.shapes[session].capacity + (1 if session in burst else 0)
            if landed > allowed:
                problems.append(
                    f"session {session} carries {landed} beats in "
                    f"{self.shapes[session].turns} turns (capacity {allowed})"
                )
        return problems

    def _check_events(self) -> list[str]:
        problems: list[str] = []
        emap = self.provisional_map()
        last = self.checkpoint_count - 1

        # Only asked of a timeline long enough to owe one. The distractor draw is per-session
        # and cannot fire before session 2, so a three- or four-session subject legitimately
        # has none — and demanding one would mean looking at the total length to decide where
        # to force it, which is the forward look this module exists to avoid.
        if self.session_count >= 5 and not self.distractor_sessions:
            problems.append("no distractor session; >=1 must carry no persona signal")
        if not self._reinforced_fields():
            problems.append("no field is evidenced in >=2 sessions; a reinforcement is required")

        for event in self.events_of("correction"):
            if event.checkpoint >= last:
                problems.append(
                    f"correction on {event.field_id} lands at the final checkpoint "
                    f"{event.checkpoint}; post-correction latency has no room to be measured"
                )
            if emap.grade(event.checkpoint, event.field_id or "") < Grade.WEAK:
                problems.append(
                    f"correction on {event.field_id} lands where the field is not evidenced; "
                    "the SUT could never recover from it"
                )
        for event in self.events_of("interference"):
            settled_outside = [
                fid for fid in fields.PREFERENCE_IDS
                if fields.BY_ID[fid].group != event.facet_group
                and emap.grade(max(event.checkpoint - 1, 0), fid) == Grade.STRONG
            ]
            if len(settled_outside) < 3:
                problems.append(
                    f"interference burst on {event.facet_group} at checkpoint "
                    f"{event.checkpoint} has only {len(settled_outside)} settled fields "
                    "outside its group; interference collateral is unmeasurable below 3"
                )
        for event in self.events:
            if not 0 <= event.checkpoint < self.checkpoint_count:
                problems.append(
                    f"{event.kind} at checkpoint {event.checkpoint} is outside the subject's "
                    f"{self.checkpoint_count} checkpoints"
                )
        return problems

    def _reinforced_fields(self) -> tuple[str, ...]:
        per_field: dict[str, set[int]] = {}
        for beat in self.beats:
            if beat.counts_toward_gold:
                per_field.setdefault(beat.field_id, set()).add(beat.session)
        return tuple(sorted(fid for fid, seen in per_field.items() if len(seen) >= 2))

    def _check_directives(self, card: PersonaCard | None) -> list[str]:
        problems: list[str] = []
        for directive in self.directives:
            kind, _, argument = directive.partition(":")
            if kind == "stated_vs_revealed":
                problems += self._check_tension(argument, card)
            elif kind == "single_mention":
                problems += self._check_single_mention(argument, card)
        return problems

    def _check_tension(self, field_id: str, card: PersonaCard | None) -> list[str]:
        """The tension, with the polarity settled: gold is the *revealed* value.

        Both halves must be present. A tension with no counter-beat is an ordinary field; a
        counter-beat with no supporting behaviour is an unfaithful transcript.

        Unlike v0 this is **not** a hard floor on supporting instances. v0's
        `TENSION_MIN_INSTANCES = 3` was enforceable because the whole timeline was allocated at
        once; here a short timeline may legitimately be read before the behaviour has
        accumulated, and that early prefix — one claim, one counter-observation — is a real
        state of a real twin rather than a defect. It is reported, not raised.
        """
        problems: list[str] = []
        counters = [b for b in self.beats if b.field_id == field_id and not b.supports_gold]
        supports = [b for b in self.beats if b.field_id == field_id and b.counts_toward_gold]
        if not counters:
            problems.append(
                f"stated_vs_revealed:{field_id} has no counter-beat, so no tension is scheduled"
            )
        if counters and supports and min(b.session for b in counters) > min(
            b.session for b in supports
        ):
            problems.append(
                f"stated_vs_revealed:{field_id} states the claim after the behaviour has "
                "already shown; plant the self-report first"
            )
        if card is not None and card.get(field_id) == fields.UNKNOWN:
            problems.append(f"stated_vs_revealed:{field_id} is unknown in ground truth")
        return problems

    def _check_single_mention(self, constraint: str, card: PersonaCard | None) -> list[str]:
        problems: list[str] = []
        # The subject's own mentions. A companion's constraint on the same field is not one
        # of them — the hard case asks whether a value stated once about *this person* is
        # retained, and a beat that says something true about somebody else neither states it
        # nor restates it.
        mentions = [
            b for b in self.beats if b.field_id == constraint and b.counts_toward_gold
        ]
        if len(mentions) > 1:
            problems.append(
                f"single_mention:{constraint} has {len(mentions)} beats; exactly one is wanted"
            )
        if card is not None and constraint in fields.BY_ID:
            value = card.get(constraint)
            if value in (fields.UNKNOWN, False, (), [], set(), None):
                problems.append(
                    f"single_mention:{constraint} names a constraint the card does not carry"
                )
        return problems

    def _check_anti_correlation(self) -> list[str]:
        """A cluster violation the SUT is never shown is worse than no violation at all.

        The SUT infers the sibling from the cluster, is charged with fabrication for reasoning
        correctly, and the artifact is indistinguishable from genuine failure.

        **Reported, not raised, in v0.1.** In v0 the whole timeline was allocated at once so
        the quota could be forced; here a violated cluster whose fields have late onsets is
        simply not yet visible at a short length, which is a fact about the prefix.
        """
        problems: list[str] = []
        emap = self.provisional_map()
        last = self.checkpoint_count - 1
        for cluster in self.anti_correlated:
            members = fields.CLUSTERS.get(cluster, ())
            unseen = [fid for fid in members if emap.grade(last, fid) == Grade.UNEVIDENCED]
            if len(members) - len(unseen) < 2:
                problems.append(
                    f"subject violates {cluster} but the schedule evidences fewer than two of "
                    f"its fields ({sorted(unseen)} never appear) by session {last}"
                )
        return problems

    # -- serialization -----------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "session_count": self.session_count,
            "checkpoint_count": self.checkpoint_count,
            "pace": self.pace,
            "directives": list(self.directives),
            "anti_correlated": list(self.anti_correlated),
            "hard_case_tags": list(self.hard_case_tags),
            "distractor_sessions": list(self.distractor_sessions),
            "removable_sessions": list(self.removable_sessions()),
            "reinforced_fields": list(self._reinforced_fields()),
            "turns": [self.turns_in(i) for i in range(self.session_count)],
            "turn_budget": [s.turns for s in self.shapes],
            "dates": list(self.dates),
            "deferred_depth": list(self.deferred_depth),
            "beats": [b.to_json() for b in self.beats],
            "events": [
                {
                    "kind": e.kind, "checkpoint": e.checkpoint, "field_id": e.field_id,
                    "old_value": e.old_value, "new_value": e.new_value,
                    "facet_group": e.facet_group,
                }
                for e in self.events
            ],
            "predicted_profile": {k: round(v, 4) for k, v in self.profile().items()},
        }


# --- construction ------------------------------------------------------------

#: At most this many scripted corrections per subject. A correction changes ground truth, so
#: it is the one scheduled event a consumer cannot opt out of; two is enough to measure
#: recovery twice without making the timeline about corrections.
#:
#: **Disabled for v0.5 (set to 0).** `_correction` overrides an ordinal the timeline has already
#: pinned toward the *final* card value, invents a random `old_value` >=2 levels away, and rewinds
#: `card_at` to it — but never authors a session expressing that old value. So every scheduled
#: correction was a phantom: gold before the flip held a value the transcript never showed, and a
#: correct reader was charged a misreading for reading the page. All 44 corrections in the n50 base
#: were phantom (28/50 subjects). Rather than author a real "before" phase (which needs the field's
#: beats placed toward `old_value` pre-flip — a beat-placement change that breaks prefix-stability),
#: v0.5 ships without corrections. `_correction` and the scheduling block below are kept intact,
#: gated off by this `0`, so v1/v2 can revive them once the before-phase is authored for real.
MAX_CORRECTIONS: int = 0

#: Earliest session that may carry a correction. Something has to be settled first.
CORRECTION_EARLIEST: int = 4
CORRECTION_RATE: float = 0.16

#: Interference fires at most once, and not before the card has enough settled fields outside
#: the burst's group for collateral to be measurable.
INTERFERENCE_EARLIEST: int = 6
INTERFERENCE_RATE: float = 0.3

#: Past these, a subject that has not yet had its first correction or its burst stops rolling
#: and attempts one every session. Same deadline construction as `DISTRACTOR_DEADLINE`, and
#: the same reason: 27 of 50 subjects had no correction and 34 of 50 no interference burst.
#:
#: **A deadline is not a guarantee here, and deliberately so.** Both events have structural
#: preconditions that a short timeline cannot meet — a correction overrides a value the user
#: has already pinned, so it needs a STRONG ordinal to exist, and a burst needs ≥3 settled
#: fields outside its own group for the collateral to be measurable. Where `_correction` or
#: `_burst_group` returns `None` the attempt simply does not land, and it is retried next
#: session. So the honest statement of the intent is length-conditional: *every subject long
#: enough to carry one, has one* — not "all personas". That restatement is half of ADR-0038's
#: decision; forcing the events on timelines that have established nothing to correct would
#: have manufactured the coverage rather than earned it.
CORRECTION_DEADLINE: int = 6
INTERFERENCE_DEADLINE: int = 8


def build_schedule(
    *,
    subject_id: str,
    card: PersonaCard,
    session_count: int,
    directives: Sequence[str] = (),
    anti_correlated: Sequence[str] = (),
    hard_case_tags: Sequence[str] = (),
    seed: int = DEFAULT_SEED,
) -> EvidenceSchedule:
    """Build one subject's timeline. Deterministic in `(seed, subject_id)`, prefix-stable.

    The loop walks sessions forward and **never reads `session_count` for any decision** — it
    is only how far the walk goes. Everything a session does is decided from its own shape,
    from the per-field plans, and from what already happened. That is the whole extensibility
    property, and it is why the deferral queue is carried forward rather than the beats being
    allocated across a known number of slots.
    """
    if session_count < 1:
        raise ScheduleError(f"{subject_id}: session_count must be >= 1")

    tension_field, single_mention = _parse_directives(directives)
    subject_pace = pace(subject_id, seed=seed)

    plans: dict[str, list[PlannedBeat]] = {}
    for spec in fields.ALL_FIELDS:
        if not schedulable(spec.field_id, card):
            continue
        plan = field_plan(spec.field_id, card, subject_id, seed=seed)
        if spec.field_id == single_mention:
            plan = plan[-1:]      # exactly one mention, and it is the one that pins
        plans[spec.field_id] = plan

    shapes = tuple(session_shape(subject_id, i, seed=seed) for i in range(session_count))
    dates = tuple(
        d.isoformat()
        for d in session_dates(subject_id, session_count, seed=seed, card=card)
    )

    cursor = {fid: 0 for fid in plans}      # next unplaced beat per field
    queue: list[PlannedBeat] = []           # beats due but not yet placed
    beats: list[Beat] = []
    incidentals: list[Incidental] = []
    events: list[ScriptedEvent] = []
    depth: list[int] = []
    corrections = 0
    interfered = False
    distracted = False
    counter_placed = tension_field is None
    third_party = third_party_pick(card, subject_id, seed=seed)
    third_party_placed = third_party is None

    for index in range(session_count):
        shape = shapes[index]

        # Everything whose planned session has arrived joins the queue. Nothing here reads
        # ahead: a beat becomes due when the walk reaches it.
        for fid, plan in plans.items():
            while cursor[fid] < len(plan) and plan[cursor[fid]].session <= index:
                queue.append(plan[cursor[fid]])
                cursor[fid] += 1

        # The roll, plus a deadline. Reaching `DISTRACTOR_DEADLINE` without one having landed
        # makes this session the distractor — reading only the index and what is already
        # placed, so extending the timeline can never move it.
        if shape.is_distractor or (not distracted and index >= DISTRACTOR_DEADLINE):
            events.append(ScriptedEvent("distractor", checkpoint=index))
            distracted = True
            depth.append(len(queue))
            continue

        # Drawn before the beats are placed, and filtered after: a session that makes a
        # deliberate point of a distribution does not also instantiate it as scenery, because
        # on the page that is one outing, not two.
        drawn = incidental_draws(subject_id, index, card, seed=seed)

        emap_so_far = _map_of(beats, index, incidentals)
        placed: list[PlannedBeat] = []

        # Scripted beats are decided *before* the queue is drained, and their slots come out
        # of the session's capacity rather than being appended on top of it. Appending was the
        # first thing this loop got wrong: six sessions across the pilot ended up over
        # capacity — three beats in a seven-turn conversation — because a correction and a
        # counter-beat were added after `room` had already been spent, which is exactly the
        # recitation density the leakage audit fails a transcript for.
        scripted = 0
        counter_due = (
            not counter_placed and any(b.field_id == tension_field for b in beats)
        )
        if counter_due:
            scripted += 1

        # Deterministic on first eligible, not a per-session roll. The pilot measured what
        # rolling costs: at 0.14 per eligible session a short subject goes without a
        # distractor about half the time, and 21 of 50 subjects ended up with none at all
        # while the pooled rate was correct. A trap that only half the corpus carries cannot
        # support a claim about how twins handle it.
        third_party_due = not third_party_placed and index >= THIRD_PARTY_EARLIEST
        if third_party_due:
            scripted += 1
        correction = None
        if (
            corrections < MAX_CORRECTIONS
            and index >= CORRECTION_EARLIEST
            # Deliberately *not* "and this is not the last session". A correction wants room
            # after it for recovery to be measurable, and v0 enforced that by refusing the
            # final checkpoint — which it could do because it knew where the end was. Here the
            # end is not knowable: the same subject extended from 8 sessions to 25 would move
            # its correction, invalidating every transcript after it. So the rule becomes a
            # published note in `_check_events`, and extending the timeline is what fixes it.
            # The roll, or the deadline once a subject has reached it with none at all. The
            # deadline clause is guarded on `corrections == 0` rather than on
            # `MAX_CORRECTIONS`: it exists to guarantee a first correction, not to hurry a
            # second one, and without the guard every session past the deadline would attempt
            # one until the cap was hit.
            and (
                (corrections == 0 and index >= CORRECTION_DEADLINE)
                or random.Random(f"{seed}|{subject_id}|correction|{index}").random()
                < CORRECTION_RATE
            )
        ):
            correction = _correction(
                emap_so_far, card, index, tension_field, single_mention,
                {e.field_id for e in events if e.kind == "correction"},
                random.Random(f"{seed}|{subject_id}|correction-pick|{index}"),
            )
            if correction is not None:
                scripted += 1

        room = max(shape.capacity - scripted, 0)

        # An interference burst overrides ordinary priority for one session: a dense burst on
        # one facet group is the point, and a burst that has to wait its turn is not dense.
        if (
            not interfered
            and index >= INTERFERENCE_EARLIEST
            and (
                index >= INTERFERENCE_DEADLINE
                or random.Random(f"{seed}|{subject_id}|interference|{index}").random()
                < INTERFERENCE_RATE
            )
        ):
            group = _burst_group(emap_so_far)
            if group is not None:
                members = {f.field_id for f in fields.PREFERENCE_FIELDS if f.group == group}
                in_group = [b for b in queue if b.field_id in members]
                # Pull the group's next beats forward if they are not already due — a burst
                # is allowed to bring a field's schedule toward it.
                for fid in sorted(members & set(plans)):
                    if len(in_group) >= 3:
                        break
                    if cursor[fid] < len(plans[fid]):
                        pulled = plans[fid][cursor[fid]]
                        cursor[fid] += 1
                        queue.append(pulled)
                        in_group.append(pulled)
                if len(in_group) >= 3:
                    placed = in_group[: room + 1]
                    events.append(ScriptedEvent(
                        "interference", checkpoint=index, facet_group=group,
                    ))
                    interfered = True

        if not placed:
            queue.sort(key=lambda b: (b.session, b.field_id))
            placed = queue[:room]

        # A signal session that nothing is due for pulls the next planned beat forward rather
        # than going out empty.
        #
        # Without this, an empty session is indistinguishable from a drawn distractor —
        # `distractor_sessions` derives from "carries no beats" — so it gets authored with a
        # distractor scenario and the conversation becomes off-topic chatter that nobody
        # scheduled. Measured on the first pilot corpus: 3 of 11 distractor-authored sessions
        # were starved signal sessions, including a subject whose *first ever* conversation
        # came out as a lost-booking-reference call. It also means the distractor rate is not
        # the rate `DISTRACTOR_RATE` declares. Pulling forward reads only the field plans,
        # which are fixed, so it costs nothing in prefix stability.
        if not placed and room > 0:
            pending = [
                (plans[fid][cursor[fid]], fid) for fid in sorted(plans)
                if cursor[fid] < len(plans[fid])
            ]
            if pending:
                nxt, fid = min(pending, key=lambda p: (p[0].session, p[1]))
                cursor[fid] += 1
                placed = [nxt]

        for planned in placed:
            note = planned.note
            if interfered and events and events[-1].checkpoint == index \
                    and events[-1].kind == "interference":
                note = f"interference burst on {events[-1].facet_group}"
            beats.append(Beat(
                session=index, field_id=planned.field_id, move=planned.move,
                note=note, bin=planned.bin,
            ))
        queue = [b for b in queue if b not in placed]

        spoken_for = {b.field_id for b in beats if b.session == index}
        incidentals += [
            Incidental(index, field_id, bin_id)
            for field_id, bin_id in sorted(drawn.items()) if field_id not in spoken_for
        ]

        # The counter-beat of a stated-vs-revealed subject is planted as soon as the field it
        # contradicts has been heard at all, so the claim lands before the behaviour piles up.
        if counter_due:
            beats.append(Beat(
                session=index, field_id=tension_field or "", move="anecdote",
                supports_gold=False,
                note="self-describes as the opposite of ground truth",
            ))
            counter_placed = True

        # The companion's constraint. `request` because that is how it reaches the bot — the
        # subject relays it, plainly, as a requirement on the venue. It moves no grade
        # (`counts_toward_gold` is False), and the card it belongs to does not exist.
        if third_party_due and third_party is not None:
            field_id, foreign = third_party
            beats.append(Beat(
                session=index, field_id=field_id, move="request",
                attributed=THIRD_PARTY, foreign_value=foreign,
                note="a companion's constraint, relayed by the subject; not the subject's own",
            ))
            events.append(ScriptedEvent(
                "third_party", checkpoint=index, field_id=field_id, new_value=foreign,
            ))
            third_party_placed = True

        # A correction: the user overrides a value they have already established.
        if correction is not None:
            beats.append(Beat(
                session=index, field_id=correction.field_id or "", move="request",
                note=f"overrides the earlier {correction.old_value!r} with "
                     f"{correction.new_value!r}",
            ))
            events.append(correction)
            corrections += 1

        depth.append(len(queue))

    schedule = EvidenceSchedule(
        subject_id=subject_id,
        session_count=session_count,
        seed=seed,
        beats=sorted(beats, key=lambda b: (b.session, b.field_id)),
        incidentals=sorted(incidentals, key=lambda i: (i.session, i.field_id)),
        events=sorted(events, key=lambda e: (e.checkpoint, e.kind, e.field_id or "")),
        shapes=shapes,
        dates=dates,
        pace=subject_pace,
        directives=tuple(directives),
        anti_correlated=tuple(anti_correlated),
        hard_case_tags=tuple(hard_case_tags),
        deferred_depth=tuple(depth),
        # Drawn per session on its own RNG stream, after every placement decision has been
        # made and read by nothing that makes one. This is what keeps the change contained:
        # `_map_of` never sees it, so no beat moves.
        formality=tuple(
            formality_occasion(subject_id, index, card, seed=seed)
            for index in range(session_count)
        ),
        formality_ceiling=str(card.get(FORMALITY_FIELD) or ""),
    )
    return schedule


def _map_of(
    beats: Sequence[Beat], upto: int, incidentals: Sequence[Incidental] = (),
) -> dict[str, Grade]:
    """Grades implied by the evidence placed so far. Looks only backward, by construction."""
    weights: dict[str, int] = {}
    seen: dict[str, int] = {}
    for beat in beats:
        if beat.session >= upto or not beat.counts_toward_gold:
            continue
        seen[beat.field_id] = seen.get(beat.field_id, 0) + 1
        weights[beat.field_id] = weights.get(beat.field_id, 0) + evidence_weight(beat)
    for occasion in incidentals:
        if occasion.session >= upto:
            continue
        seen[occasion.field_id] = seen.get(occasion.field_id, 0) + 1
        weights[occasion.field_id] = weights.get(occasion.field_id, 0) + INCIDENTAL_WEIGHT
    out: dict[str, Grade] = {}
    for spec in fields.ALL_FIELDS:
        if not seen.get(spec.field_id):
            out[spec.field_id] = Grade.UNEVIDENCED
        elif weights.get(spec.field_id, 0) >= pin_threshold(spec.field_id):
            out[spec.field_id] = Grade.STRONG
        else:
            out[spec.field_id] = Grade.WEAK
    return out


def _burst_group(grades: Mapping[str, Grade]) -> str | None:
    """A facet group worth interfering with: >=3 members, and >=3 settled fields outside it."""
    groups = sorted({
        f.group for f in fields.PREFERENCE_FIELDS
        if sum(1 for o in fields.PREFERENCE_FIELDS if o.group == f.group) >= 3
    })
    for group in groups:
        outside = [
            fid for fid in fields.PREFERENCE_IDS
            if fields.BY_ID[fid].group != group and grades.get(fid) == Grade.STRONG
        ]
        if len(outside) >= 3:
            return group
    return None


def _correction(
    grades: Mapping[str, Grade],
    card: PersonaCard,
    session: int,
    tension_field: str | None,
    single_mention: str | None,
    already: set[str | None],
    rng: random.Random,
) -> ScriptedEvent | None:
    """Override an ordinal the user has already pinned, if there is one to override.

    Excluded: the tension field, whose earlier evidence was never true rather than superseded,
    and the once-mentioned constraint, which the SUT gets no second chance to re-read.
    """
    candidates = sorted(
        fid for fid in fields.PREFERENCE_IDS
        if fields.BY_ID[fid].metric == fields.METRIC_ORDINAL
        and fid not in (tension_field, single_mention)
        and fid not in already
        and grades.get(fid) == Grade.STRONG
    )
    if not candidates:
        return None
    field_id = rng.choice(candidates)
    spec = fields.BY_ID[field_id]
    new_value = card.get(field_id)
    gold_index = spec.levels.index(new_value)
    far = [lvl for i, lvl in enumerate(spec.levels) if abs(i - gold_index) >= 2]
    old_value = rng.choice(far or [lvl for lvl in spec.levels if lvl != new_value])
    return ScriptedEvent(
        "correction", checkpoint=session, field_id=field_id,
        old_value=old_value, new_value=new_value,
    )


#: The closed vocabulary a companion's value is drawn from, per field. `alcohol_free_required`
#: is a boolean and only works in one direction: a companion who *requires* an alcohol-free
#: room constrains the evening, a companion who does not requires nothing and says nothing.
_THIRD_PARTY_POOL: dict[str, tuple[str, ...]] = {
    "dietary": fields.DIETS,
    "allergies": fields.ALLERGENS,
    "accessibility_needs": fields.ACCESSIBILITY_NEEDS,
    "cuisines_avoided": fields.CUISINES,
}


def third_party_pick(
    card: PersonaCard, subject_id: str, *, seed: int = DEFAULT_SEED
) -> tuple[str, Any] | None:
    """The companion's constraint for this subject: a `(field_id, value)` the card lacks.

    Deterministic in `(seed, subject_id)` and a function of the card alone, so it is
    prefix-stable for free — extending a timeline cannot move it.

    The value is required to be one the subject does **not** carry, and for
    `cuisines_avoided` one they neither avoid nor seek. That is not fastidiousness: a
    companion who happens to share the subject's constraint is not a trap at all, it is
    ordinary corroborating evidence, and a beat that is sometimes a trap and sometimes
    evidence cannot be scored either way.
    """
    rng = random.Random(f"{seed}|{subject_id}|third_party")
    for field_id in rng.sample(THIRD_PARTY_FIELDS, len(THIRD_PARTY_FIELDS)):
        held = card.get(field_id)
        if field_id == "alcohol_free_required":
            if not held:
                return field_id, True
            continue
        taken = set(held or ())
        if field_id == "cuisines_avoided":
            taken |= set(card.get("cuisines_liked") or ())
        pool = [v for v in _THIRD_PARTY_POOL[field_id] if v not in taken]
        if pool:
            return field_id, [rng.choice(pool)]
    return None


def _parse_directives(directives: Sequence[str]) -> tuple[str | None, str | None]:
    tension: str | None = None
    mention: str | None = None
    for directive in directives:
        kind, _, argument = directive.partition(":")
        if kind == "stated_vs_revealed":
            tension = argument
        elif kind == "single_mention":
            mention = argument
    return tension, mention
