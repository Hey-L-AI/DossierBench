"""DossierBench — scoring persona memory on calibration, not recall.

Measures how faithfully a system reconstructs a person's persona card from conversational
evidence alone, as that evidence arrives incrementally. The package keeps its development
name, `persona_twin`. `docs/SCORING.md` describes the scoring rule and `docs/PIPELINE.md` the
build; section references such as "§8" point into the original design spec, which is not
released.

The scorer is the benchmark; it is built first and everything upstream feeds it (§15).
Stdlib only, so it runs with `python3` and never depends on a model.
"""

from .fields import SCHEMA_VERSION, UNKNOWN

__all__ = ["SCHEMA_VERSION", "UNKNOWN"]
