"""Generate `schema/persona_card.v0.1.json` from the field registry (spec-v0 §10 item 1).

The registry in `fields.py` is canonical. This emits the JSON Schema deliverable from it,
carrying per-field documentation, level anchors, and the declared scoring metric as
``x-metric`` — so the published schema and the scorer cannot disagree about how a field is
scored. A test asserts the checked-in file still matches this output.

    python -m persona_twin.emit_schema [path]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import fields
from .fields import UNKNOWN

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "schema" / "persona_card.v0.1.json"

_UNKNOWN_SENTINEL: dict[str, Any] = {"const": UNKNOWN}


def _field_schema(spec: fields.Field) -> dict[str, Any]:
    """Schema for one field: the committed shape, or the 'unknown' sentinel.

    Every field accepts ``"unknown"`` (spec §2.1). Ground-truth cards are additionally
    validated in code with ``allow_unknown=False``; the JSON Schema deliberately admits
    both, because the same schema describes SUT emissions and ground truth.
    """
    committed: dict[str, Any]
    if spec.metric == fields.METRIC_JACCARD:
        committed = {
            "type": "array",
            "items": {"enum": list(spec.levels)},
            "uniqueItems": True,
        }
    elif spec.metric == fields.METRIC_ORDINAL:
        committed = {"enum": list(spec.levels)}
    elif spec.metric == fields.METRIC_JSD:
        committed = {
            "type": "object",
            "properties": {
                b: {"type": "number", "minimum": 0.0, "maximum": 1.0} for b in spec.levels
            },
            "additionalProperties": False,
            "description": "Normalized distribution; masses must sum to 1.0.",
        }
    elif spec.metric == fields.METRIC_EXACT:
        committed = {"type": "boolean"}
    else:
        raise ValueError(f"unhandled metric {spec.metric!r}")

    schema: dict[str, Any] = {
        "description": spec.doc,
        "x-metric": spec.metric,
        "x-provenance": spec.provenance,
        "oneOf": [committed, _UNKNOWN_SENTINEL],
    }
    if spec.anchors:
        schema["x-level-anchors"] = [
            {"level": level, "anchor": anchor}
            for level, anchor in zip(spec.levels, spec.anchors)
        ]
    return schema


def build() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://raw.githubusercontent.com/Hey-L-AI/DossierBench/main/schema/persona_card.v0.1.json",
        "title": "Persona Card v0.1",
        "description": (
            "A persona card for DossierBench (v0 card schema). The scorer "
            "dispatches on schema_version. contexts.default nesting is mandatory even "
            "though v0 populates only one context: v1 adds sibling blocks without a "
            "schema break. Do not flatten."
        ),
        "type": "object",
        "required": ["schema_version", "subject_id", "hard_constraints", "contexts"],
        "additionalProperties": False,
        "properties": {
            "schema_version": {"const": fields.SCHEMA_VERSION},
            "subject_id": {"type": "string"},
            "valid_from": {
                "type": ["string", "null"],
                "description": (
                    "Timestamp. Unused in v0 scoring; present as the v2 drift hook, where "
                    "ground truth becomes a time-indexed sequence of cards."
                ),
            },
            "hard_constraints": {
                "type": "object",
                "description": (
                    "Top-level, context-independent, scored separately as a gating track "
                    "(spec §8.2). Never averaged into fidelity."
                ),
                "required": [f.field_id for f in fields.HARD_CONSTRAINTS],
                "additionalProperties": False,
                "properties": {
                    f.field_id: _field_schema(f) for f in fields.HARD_CONSTRAINTS
                },
            },
            "contexts": {
                "type": "object",
                "required": ["default"],
                "additionalProperties": False,
                "properties": {
                    "default": {
                        "type": "object",
                        "required": [f.field_id for f in fields.PREFERENCE_FIELDS],
                        "additionalProperties": False,
                        "properties": {
                            f.field_id: _field_schema(f)
                            for f in fields.PREFERENCE_FIELDS
                        },
                    }
                },
            },
            "idiosyncrasies": {
                "type": "string",
                "description": "Free text, both sides. Scored by rubric judge (spec §8.5).",
            },
            "importance_weights": {
                "type": ["object", "null"],
                "description": (
                    "Optional per-field weight vector summing to 1 over a declared field "
                    "subset. When present in ground truth the scorer additionally reports "
                    "importance-weighted aggregates. Never a gate."
                ),
                "additionalProperties": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
        },
        "x-clusters": {name: list(ids) for name, ids in fields.CLUSTERS.items()},
    }


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build(), indent=2) + "\n")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
