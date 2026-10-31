"""Gate 1 for the per-checkpoint scalar overlay fields (`sut._verify_scalar_card_at`).

The scalar grade-leads-value defect (see runs/gt-review-candidates.md, 2026-08-28): a scalar
ordinal field's `card_at` held its END-STATE level at every checkpoint, so a faithful reader was
charged at the early checkpoints where the trait had surfaced only at a lower level (gen-020/024
`solo_dining_comfort`, gen-022 `formality_ceiling`). The fix drives `gold_at` for two scalar fields
from `card_at`, filled with the point-in-time level. This gate enforces the STRUCTURAL invariant
that fix produced; the semantic "value is readable by t" check is Gate 2 (LLM/human review, see the
gate docstring), not mechanized here.
"""
from __future__ import annotations


import pytest

from persona_twin import scorer, sut
from persona_twin.baselines import EvidenceOracle

FID = "solo_dining_comfort"  # levels: wont_eat_alone, takeaway_or_counter, comfortable_at_table, enjoys_solo


def _item(grades_by_cp, card_at_by_cp, final):
    return {
        "subject_id": "t",
        "card": {FID: final},
        "provisional": {str(k): {FID: g} for k, g in grades_by_cp.items()},
        "card_at": {str(k): {FID: v} for k, v in card_at_by_cp.items()},
    }


def test_monotone_climb_to_final_is_accepted():
    # takeaway_or_counter -> comfortable_at_table -> enjoys_solo, the gen-024 shape.
    good = _item(
        {0: "WEAK", 1: "WEAK", 2: "WEAK", 3: "STRONG"},
        {0: "takeaway_or_counter", 1: "comfortable_at_table", 2: "enjoys_solo", 3: "enjoys_solo"},
        "enjoys_solo",
    )
    sut.evidence_map(good)  # does not raise


def test_flat_constant_value_is_accepted():
    # The default for an immediately-readable trait: same level throughout, == final.
    good = _item({0: "WEAK", 1: "STRONG"},
                 {0: "takeaway_or_counter", 1: "takeaway_or_counter"}, "takeaway_or_counter")
    sut.evidence_map(good)


def test_unevidenced_prefix_value_is_ignored():
    # gen-022 shape: an UNEVIDENCED prefix may hold any level (unscored); only evidenced cps count.
    good = _item(
        {0: "UNEVIDENCED", 1: "WEAK", 2: "WEAK"},
        {0: "enjoys_solo", 1: "takeaway_or_counter", 2: "takeaway_or_counter"},
        "takeaway_or_counter",
    )
    sut.evidence_map(good)


def test_zigzag_away_from_final_is_refused():
    # climbs to comfortable_at_table then drops back — moves further from final(enjoys_solo).
    bad = _item(
        {0: "WEAK", 1: "WEAK", 2: "WEAK"},
        {0: "comfortable_at_table", 1: "takeaway_or_counter", 2: "enjoys_solo"},
        "enjoys_solo",
    )
    with pytest.raises(ValueError, match="moves further from the final"):
        sut.evidence_map(bad)


def test_never_reaches_final_is_refused():
    # monotone (flat) but the last evidenced value is not the final card value.
    bad = _item({0: "WEAK", 1: "WEAK"},
                {0: "comfortable_at_table", 1: "comfortable_at_table"}, "enjoys_solo")
    with pytest.raises(ValueError, match="end state must be reached"):
        sut.evidence_map(bad)


def test_off_vocab_level_is_refused():
    bad = _item({0: "WEAK", 1: "WEAK"},
                {0: "takeaway_or_counter", 1: "bistro"}, "takeaway_or_counter")
    with pytest.raises(ValueError, match="is not one of"):
        sut.evidence_map(bad)


@pytest.mark.data
def test_published_key_passes_gate_and_oracle(answer_key):
    """Gate 1 over the shipped answer key: it must not raise on any of the 50."""
    for it in answer_key["items"]:
        sut.evidence_map(it)  # the gate runs here; must not raise on any of the 50
        subj = sut.scorer_subject(it)
        assert sut.verify_gold(subj, it) == []
        oracle = EvidenceOracle(subj)
        for cp in range(subj.evidence.checkpoint_count):
            cs = scorer.score_checkpoint(subj, cp, oracle.emit(cp))
            if cs.f_raw is not None:
                assert abs(cs.f_raw - 1.0) < 1e-9
