"""The verified evidence map: three-way grading per field per checkpoint (spec-v0 §7.3).

This is the artifact the entire abstention/fabrication apparatus rests on, so it is graded
rather than binary:

* ``STRONG`` — a competent reader can commit to a specific value from the transcript so far.
* ``WEAK``   — hinted, narrowed, or implied but not pinned; a careful reader could abstain.
* ``UNEVIDENCED`` — no support at all.

Three rules from §7.3 are enforced here rather than left to the annotation pipeline:

1. **Grading is by ≥2 independent judges, and a field is STRONG only where they agree.**
   Disagreement demotes. `EvidenceMap.from_judges` takes the elementwise minimum, so the
   rule is executable rather than a documentation promise. The weak band exists precisely
   to absorb judge uncertainty instead of exporting it into the SUT's score.
2. **The provisional map is a per-field ceiling the judges cannot raise** (ADR-0009). A
   field the schedule never evidences is UNEVIDENCED in the verified map by construction.
   `from_judges` requires the ceiling as an argument — it has no default, because the
   measurement that produced this rule found judges grading 35% of never-scheduled
   field-checkpoints as evidenced under a prompt that already forbade it, and an optional
   safeguard is one the pipeline can forget. Every clip is recorded on the map rather than
   discarded: the raises are §7.4's signal that the authoring evidenced something the
   schedule did not plan, and the input to the deferred advisory channel.
3. **Grades are monotone in the checkpoint index.** Evidence already seen cannot become
   unseen — the SUT was shown that session and may have kept it in carried state. A map
   that regrades a field downward is a map bug, and `validate` rejects it.

Rule 3 has one deliberate exception: a *scripted correction* (§5) changes the ground-truth
value, not the availability of evidence. The field stays STRONG across the correction; what
changes is which value is correct. That is handled in `subject.py`, not here.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field as dc_field
from enum import IntEnum
from typing import Iterable, Mapping

from . import fields


class Grade(IntEnum):
    """Ordered so ``min()`` implements the demote-on-disagreement rule directly."""

    UNEVIDENCED = 0
    WEAK = 1
    STRONG = 2

    @classmethod
    def parse(cls, name: str) -> Grade:
        try:
            return cls[name.strip().upper()]
        except KeyError:
            raise ValueError(f"unknown evidence grade {name!r}") from None


@dataclass(frozen=True)
class Raise:
    """One field-checkpoint where the judges graded above the schedule's ceiling.

    Kept rather than discarded. A raise means either that the author evidenced a field the
    schedule did not plan — a schedule fix, in the deferred advisory channel of ADR-0009 —
    or that the judges read evidence into a transcript that does not carry it, which is the
    behaviour the ceiling exists to bound and whose rate §7.5 publishes per judge.
    """

    checkpoint: int
    field_id: str
    judged: Grade
    ceiling: Grade


@dataclass
class EvidenceMap:
    """Per-checkpoint evidence grades for one subject.

    ``grades[checkpoint_index][field_id] -> Grade``. Checkpoints are 0-indexed and dense:
    checkpoint *t* covers everything the SUT has seen through session batch *t*.

    ``turn_refs`` optionally records where the supporting evidence sits, for the published
    per-field diagnosis of §8.9. It is not consumed by the scorer.

    ``raises`` records what the §7.3 ceiling clipped, when this map came from `from_judges`.
    Also not consumed by the scorer — it is a diagnostic about the authoring and the judges,
    not about the SUT.
    """

    subject_id: str
    grades: list[dict[str, Grade]] = dc_field(default_factory=list)
    turn_refs: dict[str, list[str]] = dc_field(default_factory=dict)
    raises: tuple[Raise, ...] = ()

    # -- construction ------------------------------------------------------

    @classmethod
    def from_judges(
        cls,
        subject_id: str,
        judge_grades: Iterable[list[Mapping[str, Grade | str]]],
        *,
        ceiling: EvidenceMap,
    ) -> EvidenceMap:
        """Combine ≥2 independent judges' per-checkpoint gradings (spec §7.3).

        Each judge supplies a list of ``{field_id: grade}`` dicts, one per checkpoint.
        A field is STRONG only where **every** judge said STRONG; any disagreement demotes
        it. Taking the elementwise minimum implements exactly that, and also demotes
        WEAK/UNEVIDENCED splits down to UNEVIDENCED — which is the conservative direction:
        it moves the field into the band where committing is penalized, so a split judgment
        never silently licenses a commit.

        ``ceiling`` is the schedule's provisional map (§5.1), and it enters the same minimum
        (ADR-0009). It is required and keyword-only: the alternative — a default that bounds
        nothing — is the version a caller omits by accident, and what it would admit is a
        `strong` grade on a field the transcript never mentions, which scores a correctly
        abstaining SUT at 0.0. Passing `permissive_ceiling` is the way to opt out, and it
        says so at the call site.

        Note the minimum over judges does **not** substitute for the ceiling: over-reading
        was measured at 56/67/68% across three judges, so it is correlated rather than
        independent and does not cancel.
        """
        judges = [list(g) for g in judge_grades]
        if len(judges) < 2:
            raise ValueError("evidence grading requires at least 2 independent judges")
        lengths = {len(j) for j in judges}
        if len(lengths) != 1:
            raise ValueError(f"judges disagree on checkpoint count: {sorted(lengths)}")
        checkpoints = lengths.pop()
        if ceiling.checkpoint_count != checkpoints:
            raise ValueError(
                f"ceiling has {ceiling.checkpoint_count} checkpoints, judges graded "
                f"{checkpoints}; the ceiling must be the same subject's provisional map"
            )

        combined: list[dict[str, Grade]] = []
        clipped: list[Raise] = []
        for checkpoint in range(checkpoints):
            per_field: dict[str, Grade] = {}
            for spec in fields.ALL_FIELDS:
                votes = [
                    _coerce(j[checkpoint].get(spec.field_id, Grade.UNEVIDENCED))
                    for j in judges
                ]
                judged = min(votes)
                bound = ceiling.grade(checkpoint, spec.field_id)
                if judged > bound:
                    clipped.append(Raise(checkpoint, spec.field_id, judged, bound))
                per_field[spec.field_id] = min(judged, bound)
            combined.append(per_field)
        return cls(subject_id, combined, raises=tuple(clipped))

    @classmethod
    def never_evidenced(cls, subject_id: str, checkpoints: int) -> EvidenceMap:
        """A map where nothing is ever evidenced. Used to test that committing anywhere is
        scored as pure fabrication."""
        return cls(
            subject_id,
            [{f.field_id: Grade.UNEVIDENCED for f in fields.ALL_FIELDS}
             for _ in range(checkpoints)],
        )

    @classmethod
    def permissive_ceiling(cls, subject_id: str, checkpoints: int) -> EvidenceMap:
        """A ceiling that bounds nothing — every field STRONG at every checkpoint.

        For tests and probes that mean to exercise the judge-combination rule alone. Never
        for a shipped map: it is exactly the state ADR-0009 measured and rejected. It is a
        named constructor rather than a default so that opting out is visible in the diff.
        """
        return cls(
            subject_id,
            [{f.field_id: Grade.STRONG for f in fields.ALL_FIELDS}
             for _ in range(checkpoints)],
        )

    # -- access ------------------------------------------------------------

    @property
    def checkpoint_count(self) -> int:
        return len(self.grades)

    def grade(self, checkpoint: int, field_id: str) -> Grade:
        return self.grades[checkpoint].get(field_id, Grade.UNEVIDENCED)

    def band(self, checkpoint: int, grade: Grade) -> tuple[str, ...]:
        """Field ids at exactly ``grade`` at this checkpoint. S(t)/W(t)/U(t) of §8.1."""
        return tuple(
            f.field_id for f in fields.ALL_FIELDS
            if self.grade(checkpoint, f.field_id) == grade
        )

    def preference_band(self, checkpoint: int, grade: Grade) -> tuple[str, ...]:
        """As `band`, restricted to the 20 scored preference fields.

        Fidelity is computed over preference fields only; the 4 hard constraints are a
        separate gating track and are never averaged in (spec §8.2).
        """
        return tuple(
            fid for fid in fields.PREFERENCE_IDS
            if self.grade(checkpoint, fid) == grade
        )

    def first_checkpoint_at(self, field_id: str, grade: Grade) -> int | None:
        """Earliest checkpoint where the field reached at least ``grade``."""
        for index in range(self.checkpoint_count):
            if self.grade(index, field_id) >= grade:
                return index
        return None

    # -- validation --------------------------------------------------------

    def validate(self) -> None:
        """Enforce monotonicity and completeness.

        Monotonicity is not cosmetic. The SUT is never re-served an earlier session
        (spec §1.3), so a field whose grade *drops* would mean the scorer expects the SUT
        to forget something it was legitimately allowed to carry — it would penalize
        correct behaviour.
        """
        if not self.grades:
            raise ValueError(f"evidence map for {self.subject_id} has no checkpoints")
        known = {f.field_id for f in fields.ALL_FIELDS}
        for index, checkpoint in enumerate(self.grades):
            extra = set(checkpoint) - known
            if extra:
                raise ValueError(
                    f"checkpoint {index} grades unknown fields: {sorted(extra)}"
                )
            if index == 0:
                continue
            for field_id in known:
                previous = self.grade(index - 1, field_id)
                current = self.grade(index, field_id)
                if current < previous:
                    raise ValueError(
                        f"{field_id} regressed from {previous.name} to {current.name} "
                        f"at checkpoint {index}; evidence grades must be monotone"
                    )

    def raise_counts(self) -> dict[str, int]:
        """Clipped cells grouped by what the ceiling said, keyed ``<ceiling>-><judged>``.

        The two kinds are different failures and are not summed. A raise off UNEVIDENCED is
        *invention* — the judge found a field the timeline never touches, which is the 35%
        of ADR-0009. A raise off WEAK is *over-pinning* — the evidence is real but the judge
        commits where the schedule says it cannot, which is the same upward error at a
        smaller amplitude and is what thins the weak band §11 rests on.
        """
        counts = Counter(
            f"{r.ceiling.name}->{r.judged.name}" for r in self.raises
        )
        return dict(sorted(counts.items()))

    def coverage(self) -> dict[str, int]:
        """Count of fields reaching each grade by the final checkpoint.

        Published per §9.1 so a reader can see how much of the card a timeline actually
        exercises. A tier whose final STRONG count is tiny is not measuring much.
        """
        last = self.checkpoint_count - 1
        counts = Counter(self.grade(last, f.field_id).name for f in fields.ALL_FIELDS)
        return {grade.name: counts.get(grade.name, 0) for grade in Grade}


def _coerce(value: Grade | str) -> Grade:
    return value if isinstance(value, Grade) else Grade.parse(value)
