"""A benchmark subject: ground truth + evidence map + scripted event log (spec-v0 §1.1, §5).

A subject is one ground-truth persona card plus the ordered timeline of sessions that
evidence it. The scorer never sees transcripts — it consumes this record, which is the
audited output of the generation pipeline.

**Scripted corrections are modelled as time-indexed ground truth.** A correction ("actually
I stopped eating meat last year") changes the governing gold value for one field from a
known checkpoint onward. `gold_at(t)` returns whichever card governs checkpoint *t*.

This is deliberately the v2 drift machinery in degenerate form (spec §13b): v2 makes ground
truth a time-indexed sequence of cards and diffs each checkpoint against its governing card,
which is exactly what `gold_at` already does. v0 restricts itself to one-field changes at
scripted points. Nothing about the scorer needs to change to generalize.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any

from .card import PersonaCard
from .evidence import EvidenceMap, Grade

#: Difficulty tiers (spec §6.2). Separate subject splits, identical scoring, never pooled
#: in headline numbers (§8.9) because AUFC is computed at different checkpoint densities.
TIERS: tuple[str, ...] = ("sparse", "medium", "rich")

#: Scripted event kinds from the §5 library.
EVENT_KINDS: tuple[str, ...] = (
    "reinforcement",  # field re-expressed consistently across >=2 sessions
    "correction",     # user overrides earlier evidence; one-field gold change
    "interference",   # burst of dense new evidence on one facet group
    "distractor",     # session with no persona signal at all
    # A companion's constraint, stated plainly and belonging to somebody else. The card does
    # not move: that is the whole construction. See ADR-0036 — it was found in the pilot as a
    # defect (the author displaced the *subject's* alcohol-free requirement onto "one of the
    # four") and is kept as a deliberate trap, because a reader that credits it to the
    # subject fabricates a constraint the person does not have.
    "third_party",
)


@dataclass(frozen=True)
class ScriptedEvent:
    """One entry from the §5 event library, with its position logged.

    Corrections carry all three of ``field_id``, ``old_value`` and ``new_value``. Storing
    the replaced value on the event (rather than deriving it) is what lets `gold_at` rewind
    a timeline from the single published final card — the log and the card cannot disagree,
    because the log is the only record of the earlier state.

    ``facet_group`` is the targeted group for interference bursts, which is what lets the
    scorer measure *collateral* damage to settled fields outside that group.
    """

    kind: str
    checkpoint: int
    field_id: str | None = None
    old_value: Any = None
    new_value: Any = None
    facet_group: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {self.kind!r}")
        if self.kind == "correction":
            if self.field_id is None:
                raise ValueError("a correction must name a field_id")
            if self.old_value is None or self.new_value is None:
                raise ValueError(
                    "a correction must carry both the value it replaced (old_value) and "
                    "the value that replaces it (new_value)"
                )
        if self.kind == "interference" and self.facet_group is None:
            raise ValueError("an interference burst must name the facet_group it targets")
        if self.kind == "third_party":
            if self.field_id is None:
                raise ValueError("a third-party constraint must name a field_id")
            if self.new_value is None:
                raise ValueError(
                    "a third-party constraint must carry the companion's value; it is not "
                    "on the card by construction, so the event is the only record of it"
                )


@dataclass
class Subject:
    """One audited subject bundle.

    ``gold`` is the card as of the *final* checkpoint — i.e. after every scripted
    correction has landed. Earlier checkpoints are reconstructed by rewinding corrections,
    which keeps the authored artifact single-valued and makes it impossible for the
    published card and the correction log to disagree.
    """

    subject_id: str
    gold: PersonaCard
    evidence: EvidenceMap
    tier: str = "medium"
    simulator_model: str = ""            # recorded per subject; results broken out by it (§7.1)
    events: list[ScriptedEvent] = dc_field(default_factory=list)
    # Per-checkpoint gold for multi-element list fields, holding the union of card elements
    # *surfaced in the transcript so far* rather than the full card (route-2 phantom prune,
    # 2026-08-27). The card assigns a full list; the transcript surfaces only a subset, and
    # holding a WEAK-graded twin to the never-surfaced remainder scores a faithful reader below
    # abstaining — the value-dimension sibling of the WEAK-island defect corrections already
    # avoid on the time dimension. Keyed checkpoint -> {field_id: evidenced-so-far list}.
    # Empty by default, so a subject with no pruned cell scores exactly as before.
    evidenced_gold_at: dict[int, dict[str, Any]] = dc_field(default_factory=dict)
    anti_correlated: tuple[str, ...] = ()  # cluster ids this subject deliberately violates (§4.3)
    hard_case_tags: tuple[str, ...] = ()   # e.g. "single_mention", "stated_vs_revealed" (§4.2)
    split: str = "public"                  # "public" | "private" (§9.2)

    # -- derived ground truth ---------------------------------------------

    @property
    def corrections(self) -> list[ScriptedEvent]:
        return sorted(
            (e for e in self.events if e.kind == "correction"),
            key=lambda e: e.checkpoint,
        )

    def gold_at(self, checkpoint: int) -> PersonaCard:
        """The card governing ``checkpoint``, with later corrections rewound.

        A correction scheduled *at* checkpoint k is in force from k onward: the SUT saw the
        session containing it before emitting at k, so it is accountable for it at k.

        Where an ``evidenced_gold_at`` entry governs a field at this checkpoint, it wins over
        the card: the list field's gold is the evidenced-so-far subset, not the full card.
        """
        values = dict(self.gold.values)
        for event in reversed(self.corrections):
            if event.checkpoint > checkpoint:
                values[event.field_id] = event.old_value  # type: ignore[index]
        values.update(self.evidenced_gold_at.get(checkpoint, {}))
        return PersonaCard(
            subject_id=self.subject_id,
            values=values,
            schema_version=self.gold.schema_version,
            idiosyncrasies=self.gold.idiosyncrasies,
            importance_weights=self.gold.importance_weights,
        )

    def correction_for(self, field_id: str, checkpoint: int) -> ScriptedEvent | None:
        """The most recent correction on ``field_id`` at or before ``checkpoint``."""
        applicable = [
            e for e in self.corrections
            if e.field_id == field_id and e.checkpoint <= checkpoint
        ]
        return applicable[-1] if applicable else None

    # -- validation --------------------------------------------------------

    def validate(self) -> None:
        if self.tier not in TIERS:
            raise ValueError(f"unknown tier {self.tier!r}")
        self.gold.validate(allow_unknown=False)
        self.evidence.validate()
        if self.evidence.subject_id != self.subject_id:
            raise ValueError("evidence map belongs to a different subject")

        n = self.evidence.checkpoint_count
        for event in self.events:
            if not 0 <= event.checkpoint < n:
                raise ValueError(
                    f"{event.kind} at checkpoint {event.checkpoint} is outside the "
                    f"subject's {n} checkpoints"
                )

        # A correction the SUT cannot possibly observe is an authoring bug: it would be
        # scored as staleness forever with no recoverable signal in the transcript.
        for event in self.corrections:
            if self.evidence.grade(event.checkpoint, event.field_id) < Grade.WEAK:  # type: ignore[arg-type]
                raise ValueError(
                    f"correction on {event.field_id} at checkpoint {event.checkpoint} "
                    "lands on a field that is not evidenced there; the SUT could never "
                    "recover from it"
                )

        # Two corrections on the same field at the same checkpoint make `gold_at`
        # order-dependent, and the rewind would silently pick one.
        seen: set[tuple[str, int]] = set()
        for event in self.corrections:
            key = (event.field_id, event.checkpoint)  # type: ignore[arg-type]
            if key in seen:
                raise ValueError(
                    f"two corrections on {event.field_id} at checkpoint {event.checkpoint}"
                )
            seen.add(key)
