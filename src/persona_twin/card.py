"""The persona card record and its wire format (spec-v0 §2.1).

Internally a card is **flat**: ``{field_id: value_or_UNKNOWN}``. On the wire it is
**nested** under ``hard_constraints`` and ``contexts.default``, because v1 adds sibling
context blocks (`date_night`, `work_lunch`) and flattening now would force a schema break
then (spec §2.1: "Do not flatten").

`to_json`/`from_json` are the only places that know about the nesting. Everything
downstream — metrics, scorer, baselines — sees the flat form.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any

from . import fields
from .fields import UNKNOWN


class CardValidationError(ValueError):
    """A card violated the schema. Never repaired silently — see `metrics._normalize`."""


@dataclass
class PersonaCard:
    """A persona card, either ground truth or an SUT emission.

    ``values`` maps every field id in the registry to a committed value or the
    ``"unknown"`` sentinel. Ground-truth cards must not contain ``"unknown"``; SUT cards
    may contain it anywhere (spec §2.1).
    """

    subject_id: str
    values: dict[str, Any] = dc_field(default_factory=dict)
    schema_version: str = fields.SCHEMA_VERSION
    valid_from: str | None = None        # v2 drift hook; unused in v0 scoring (spec §2.1)
    idiosyncrasies: str = ""             # rubric-judged, not scored by this module (§8.5)
    importance_weights: dict[str, float] | None = None   # optional track, never a gate

    # -- construction ------------------------------------------------------

    @classmethod
    def all_unknown(cls, subject_id: str) -> PersonaCard:
        """A card abstaining on every field. This is baseline 3 and the empty prior."""
        return cls(subject_id, {f.field_id: UNKNOWN for f in fields.ALL_FIELDS})

    # -- access ------------------------------------------------------------

    def committed(self, field_id: str) -> bool:
        """True when the SUT put a value on this field rather than abstaining."""
        return self.values.get(field_id, UNKNOWN) != UNKNOWN

    def get(self, field_id: str) -> Any:
        return self.values.get(field_id, UNKNOWN)

    # -- validation --------------------------------------------------------

    def validate(self, *, allow_unknown: bool = True) -> None:
        """Structural check against the field registry.

        ``allow_unknown=False`` is used for ground-truth cards, which must commit
        everywhere — an abstention in ground truth would make the evidence bands of
        §8.1 undefined for that field.
        """
        missing = {f.field_id for f in fields.ALL_FIELDS} - set(self.values)
        if missing:
            raise CardValidationError(f"card is missing fields: {sorted(missing)}")
        extra = set(self.values) - {f.field_id for f in fields.ALL_FIELDS}
        if extra:
            raise CardValidationError(f"card has unknown fields: {sorted(extra)}")

        for spec in fields.ALL_FIELDS:
            value = self.values[spec.field_id]
            if value == UNKNOWN:
                if not allow_unknown:
                    raise CardValidationError(
                        f"{spec.field_id} is 'unknown' in a card that must be complete"
                    )
                continue
            _validate_value(spec, value)

        if self.importance_weights is not None:
            total = sum(self.importance_weights.values())
            if abs(total - 1.0) > 1e-6:
                raise CardValidationError(
                    f"importance_weights sum to {total}, expected 1.0"
                )

    # -- wire format -------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "subject_id": self.subject_id,
            "valid_from": self.valid_from,
            "hard_constraints": {
                f.field_id: self.values.get(f.field_id, UNKNOWN)
                for f in fields.HARD_CONSTRAINTS
            },
            "contexts": {
                "default": {
                    f.field_id: self.values.get(f.field_id, UNKNOWN)
                    for f in fields.PREFERENCE_FIELDS
                }
            },
            "idiosyncrasies": self.idiosyncrasies,
            "importance_weights": self.importance_weights,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> PersonaCard:
        version = payload.get("schema_version")
        if version != fields.SCHEMA_VERSION:
            # The scorer dispatches on schema_version (spec §8); refusing an unknown
            # version is the whole point of carrying it.
            raise CardValidationError(
                f"unsupported schema_version {version!r}; this scorer handles "
                f"{fields.SCHEMA_VERSION!r}"
            )
        contexts = payload.get("contexts", {})
        if "default" not in contexts:
            raise CardValidationError("contexts.default is mandatory (spec §2.1)")
        values: dict[str, Any] = {}
        values.update(payload.get("hard_constraints", {}))
        values.update(contexts["default"])
        return cls(
            subject_id=payload["subject_id"],
            values=values,
            schema_version=version,
            valid_from=payload.get("valid_from"),
            idiosyncrasies=payload.get("idiosyncrasies", ""),
            importance_weights=payload.get("importance_weights"),
        )


def _validate_value(spec: fields.Field, value: Any) -> None:
    """Type- and vocabulary-check one committed value."""
    if spec.metric == fields.METRIC_JACCARD:
        if isinstance(value, str) or not isinstance(value, (list, tuple, set, frozenset)):
            raise CardValidationError(
                f"{spec.field_id} must be a collection of strings, got {type(value).__name__}"
            )
        outside = set(value) - set(spec.levels)
        if outside:
            raise CardValidationError(
                f"{spec.field_id} has out-of-vocabulary members {sorted(outside)}"
            )
    elif spec.metric == fields.METRIC_ORDINAL:
        if value not in spec.levels:
            raise CardValidationError(
                f"{spec.field_id} must be one of {spec.levels}, got {value!r}"
            )
    elif spec.metric == fields.METRIC_JSD:
        if not isinstance(value, dict):
            raise CardValidationError(
                f"{spec.field_id} must be a bin->mass mapping, got {type(value).__name__}"
            )
        outside = set(value) - set(spec.levels)
        if outside:
            raise CardValidationError(
                f"{spec.field_id} has unknown bins {sorted(outside)}"
            )
        total = sum(float(v) for v in value.values())
        if abs(total - 1.0) > 1e-6:
            raise CardValidationError(
                f"{spec.field_id} masses sum to {total}, expected 1.0"
            )
        if any(float(v) < 0 for v in value.values()):
            raise CardValidationError(f"{spec.field_id} has negative mass")
    elif spec.metric == fields.METRIC_EXACT:
        if not isinstance(value, bool):
            raise CardValidationError(
                f"{spec.field_id} must be a bool, got {type(value).__name__}"
            )
    else:
        raise CardValidationError(f"unhandled metric {spec.metric!r}")
