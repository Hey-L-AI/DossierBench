"""The four required baselines, plus the SUT protocol they implement (spec-v0 §8.8, §1.3).

Every metric is reported against all four. They exist to make the headline number legible:
a fidelity score with nothing to compare it to says nothing about whether the benchmark is
measuring inference or measuring the base rate.

Two of them are load-bearing sanity checks rather than competitors (spec §11):

* **abstain-everything** must score perfect abstention calibration (zero fabrications) and
  **`f_skill` of exactly 0**. Since the 2026-08-10 rescoring its *raw* `F` is not near zero —
  it collects `WEAK_CREDIT` on every weak field, which is 0.694 in an illustrative sparse mix
  — so the tension between calibration and fidelity is checked on skill, where the floor is
  divided out. Asserting near-zero raw `F` would now be asserting the weak band is empty
  (ADR-0003).
* **population-mode** must score non-trivial `A(t)` and still be beaten on `F(t)`. If it
  isn't, the fabrication penalty isn't biting and an SUT could win by memorizing base
  rates. Note it now scores *below* abstain-everything on `f_skill`: under the count-based
  penalty (ADR-0002) inventing the whole unevidenced card costs more than the agreement it
  buys. Silence beating invention is the thesis, not an anomaly.

With 4-level ordinals the population-mode baseline is meaningfully stronger than it would
be on a 7-point scale. That is intended — it makes it an honest bar (§8.8).

`EvidenceOracle` is not one of the four. It is a **ceiling**: it commits ground truth
exactly where evidence supports it and abstains everywhere else, and must score F = 1.0.
It tests the scorer, not an SUT, and is never published as a competitor.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field as dc_field
from typing import Any, Protocol, Sequence, runtime_checkable

from . import fields
from .card import PersonaCard
from .evidence import Grade
from .fields import UNKNOWN
from .subject import Subject


@runtime_checkable
class SUT(Protocol):
    """The incremental, stateful contract of spec §1.3.

    At checkpoint *t* the SUT receives only the sessions since *t−1* plus whatever state it
    chose to carry. Prior transcripts are **not** re-served and may not be re-requested.
    Full-transcript replay is not the benchmark.

    State is unbounded but metered: `carried_state_bytes` and `carried_state_tokens` are
    reported alongside every score as first-class numbers. A system reaching 0.85 AUFC on
    500 tokens of state is doing something categorically different from one that needs 50k,
    and the leaderboard must show that difference rather than hide it.
    """

    def reset(self, subject_id: str) -> None:
        """Drop all state. Called between subjects — no cross-subject carryover of any kind.

        This is not a formality. An SUT that accumulates population base rates across
        subjects can guess unevidenced fields profitably, which defeats the fabrication
        rule outright.
        """

    def observe(self, sessions: Sequence[Any]) -> PersonaCard:
        """Consume the new sessions and emit the current best card."""

    def carried_state_bytes(self) -> int: ...

    def carried_state_tokens(self) -> int: ...


@dataclass
class _StatelessBaseline:
    """Shared plumbing for baselines that carry nothing between checkpoints."""

    subject_id: str = ""

    def reset(self, subject_id: str) -> None:
        self.subject_id = subject_id

    def carried_state_bytes(self) -> int:
        return 0

    def carried_state_tokens(self) -> int:
        return 0


@dataclass
class AbstainEverything(_StatelessBaseline):
    """Baseline 3: emits ``"unknown"`` on every field, forever.

    Expected profile: perfect abstention calibration (zero fabrications) and `f_skill` of
    exactly 0. Its raw `A` is the do-nothing floor — `WEAK_CREDIT·|W|/(|S|+|W|)`, not 0 — since
    weak-band abstention is credited (ADR-0001). It **defines** that floor, which is why
    `a_floor` is published on every checkpoint.
    """

    def observe(self, sessions: Sequence[Any]) -> PersonaCard:
        return PersonaCard.all_unknown(self.subject_id)


@dataclass
class RandomValid(_StatelessBaseline):
    """Baseline 1: a schema-valid card drawn uniformly, never abstaining.

    Seeded so a published run is reproducible. Uniform draws over a closed vocabulary are
    a *weak* bar for sets — expected Jaccard against a small gold set is near zero — which
    is why population-mode exists as the real bar.
    """

    seed: int = 0
    _rng: random.Random = dc_field(default_factory=lambda: random.Random(0), repr=False)

    def reset(self, subject_id: str) -> None:
        super().reset(subject_id)
        # Re-seed per subject so results don't depend on subject ordering.
        self._rng = random.Random(f"{self.seed}:{subject_id}")

    def observe(self, sessions: Sequence[Any]) -> PersonaCard:
        values: dict[str, Any] = {}
        for spec in fields.ALL_FIELDS:
            values[spec.field_id] = _random_value(spec, self._rng)
        return PersonaCard(self.subject_id, values)


@dataclass
class PopulationMode(_StatelessBaseline):
    """Baseline 2: the most common value per field across a reference population.

    Never abstains — that is the point. It should earn a respectable `A(t)` and then lose
    it all to `λ·fab(t)`, because it commits on every unevidenced field.

    Built from gold cards via `PopulationMode.fit`. Fitting on the *public* split only is
    the honest choice; fitting on the split being scored leaks ground truth.
    """

    card_values: dict[str, Any] = dc_field(default_factory=dict)

    @classmethod
    def fit(cls, golds: Sequence[PersonaCard], seed_subject: str = "") -> PopulationMode:
        values: dict[str, Any] = {}
        for spec in fields.ALL_FIELDS:
            observed = [g.get(spec.field_id) for g in golds if g.committed(spec.field_id)]
            values[spec.field_id] = _modal_value(spec, observed)
        return cls(subject_id=seed_subject, card_values=values)

    def observe(self, sessions: Sequence[Any]) -> PersonaCard:
        return PersonaCard(self.subject_id, dict(self.card_values))


@dataclass
class EvidenceOracle:
    """Scorer ceiling, not a published baseline.

    Commits the governing gold value on every STRONG and WEAK field and abstains on every
    UNEVIDENCED one. By construction: A = 1.0, zero fabrications, so F = 1.0 and `f_skill` =
    1.0 at every checkpoint, and AUFC = 1.0. Any deviation is a scorer bug.

    It commits on weak fields rather than abstaining, which is correct under ADR-0001: gold
    scores 1.0 there and `WEAK_CREDIT` is 5/6, so a system that *knows* the value should say
    it. The credit is calibrated to a licensed guess, not to omniscience.

    It reads the evidence map and ground truth directly, which no real SUT may do.
    """

    subject: Subject
    _checkpoint: int = 0

    def reset(self, subject_id: str) -> None:
        self._checkpoint = 0

    def observe(self, sessions: Sequence[Any]) -> PersonaCard:
        card = self.emit(self._checkpoint)
        self._checkpoint += 1
        return card

    def emit(self, checkpoint: int) -> PersonaCard:
        gold = self.subject.gold_at(checkpoint)
        values: dict[str, Any] = {}
        for spec in fields.ALL_FIELDS:
            grade = self.subject.evidence.grade(checkpoint, spec.field_id)
            if grade == Grade.UNEVIDENCED:
                values[spec.field_id] = UNKNOWN
            else:
                values[spec.field_id] = gold.get(spec.field_id)
        return PersonaCard(self.subject.subject_id, values)

    def carried_state_bytes(self) -> int:
        return 0

    def carried_state_tokens(self) -> int:
        return 0


def PromptedExtractor(*args: Any, **kwargs: Any) -> Any:
    """Baseline 4: the reference SUT — a simple prompted-LLM extractor (spec §8.8).

    **Built, and it lives in `sut.py`.** This name is kept because §8.8 names it and the other
    three baselines are here; the implementation is next to the harness that serves it, the
    prompt it is served with and the reply parser, because those four are one artifact and
    splitting them across modules would leave this file importing a model transport it has no
    other use for.

    Imported lazily for that same reason: `sut` imports this module for the `SUT` protocol and
    the three free baselines it competes against, so a module-level import here would be a
    cycle. Everything above stays stdlib-only and callable with no key, no CLI and no network.
    """
    from .sut import PromptedExtractor as _PromptedExtractor

    return _PromptedExtractor(*args, **kwargs)


# --- value sampling ---------------------------------------------------------


def _random_value(spec: fields.Field, rng: random.Random) -> Any:
    if spec.metric == fields.METRIC_ORDINAL:
        return rng.choice(spec.levels)
    if spec.metric == fields.METRIC_EXACT:
        return rng.random() < 0.5
    if spec.metric == fields.METRIC_JACCARD:
        # Small sets: a uniform subset of a 39-cuisine vocabulary would average ~20
        # members, which is not a plausible card and would make the baseline absurdly
        # weak on precision. Size is drawn from a plausible range instead.
        size = rng.randint(0, min(4, len(spec.levels)))
        return sorted(rng.sample(list(spec.levels), size))
    if spec.metric == fields.METRIC_JSD:
        weights = [rng.random() for _ in spec.levels]
        total = sum(weights) or 1.0
        return {b: w / total for b, w in zip(spec.levels, weights)}
    raise ValueError(f"unhandled metric {spec.metric!r}")


def _modal_value(spec: fields.Field, observed: Sequence[Any]) -> Any:
    """The population's most common value for one field.

    Sets take the per-member majority (members appearing in >50% of cards), not the modal
    whole set — the modal set over a 39-member vocabulary is almost always the empty set,
    which would make the baseline trivially weak for the wrong reason.

    Distributions take the elementwise mean, which is the population distribution and the
    correct "most common" analogue for a distributional field.
    """
    if not observed:
        return _neutral_value(spec)

    if spec.metric == fields.METRIC_JACCARD:
        counts: Counter[str] = Counter()
        for value in observed:
            counts.update(set(value))
        threshold = len(observed) / 2
        return sorted(m for m, c in counts.items() if c > threshold)

    if spec.metric == fields.METRIC_JSD:
        summed = {b: 0.0 for b in spec.levels}
        for value in observed:
            for b in spec.levels:
                summed[b] += float(value.get(b, 0.0))
        total = sum(summed.values()) or 1.0
        return {b: v / total for b, v in summed.items()}

    return Counter(observed).most_common(1)[0][0]


def _neutral_value(spec: fields.Field) -> Any:
    """Fallback when a field was never observed in the fitting population."""
    if spec.metric == fields.METRIC_JACCARD:
        return []
    if spec.metric == fields.METRIC_EXACT:
        return False
    if spec.metric == fields.METRIC_ORDINAL:
        return spec.levels[len(spec.levels) // 2]
    if spec.metric == fields.METRIC_JSD:
        return {b: 1.0 / len(spec.levels) for b in spec.levels}
    raise ValueError(f"unhandled metric {spec.metric!r}")


# --- harness ----------------------------------------------------------------


def run_trajectory(
    sut: Any, subject: Subject, sessions_by_checkpoint: Sequence[Sequence[Any]] | None = None
) -> tuple[list[PersonaCard], list[int], list[int]]:
    """Drive an SUT across one subject's checkpoints under the §1.3 protocol.

    Returns the emitted cards plus the per-checkpoint state meters. ``sessions_by_checkpoint``
    supplies only the *new* sessions at each checkpoint — the harness never re-serves an
    earlier batch, which is the protocol rule made structural rather than trusted.
    """
    sut.reset(subject.subject_id)
    count = subject.evidence.checkpoint_count
    batches = sessions_by_checkpoint or [[] for _ in range(count)]
    if len(batches) != count:
        raise ValueError(
            f"got {len(batches)} session batches for {count} checkpoints"
        )

    cards: list[PersonaCard] = []
    byte_meter: list[int] = []
    token_meter: list[int] = []
    for batch in batches:
        cards.append(sut.observe(batch))
        byte_meter.append(sut.carried_state_bytes())
        token_meter.append(sut.carried_state_tokens())
    return cards, byte_meter, token_meter
