"""Route-2 phantom prune: gold_at reads the evidenced-so-far subset for taste-list fields.

The defect (see runs/gt-review-candidates.md, 2026-08-27): a list field's card holds the full
multi-element set, the transcript surfaces only a subset, and a WEAK grade held the twin to the
whole set — so a faithful reader scored below abstaining. The fix drives `gold_at` for the four
taste-list fields from `card_at`, which a pruned base fills with the evidenced-so-far subset.
"""
from __future__ import annotations


import pytest

from persona_twin import fields, metrics, scorer, sut
from persona_twin.baselines import EvidenceOracle
from persona_twin.card import PersonaCard
from persona_twin.evidence import EvidenceMap, Grade
from persona_twin.subject import Subject



def _weak_map(n, field="cuisines_liked"):
    grades = [{field: Grade.WEAK} for _ in range(n)]
    return EvidenceMap("s", grades)


def test_gold_at_overlays_evidenced_subset_per_checkpoint():
    subj = Subject(
        subject_id="s",
        gold=PersonaCard("s", {"cuisines_liked": ["a", "b"]}),
        evidence=_weak_map(3),
        evidenced_gold_at={0: {"cuisines_liked": ["a"]},
                           1: {"cuisines_liked": ["a", "b"]},
                           2: {"cuisines_liked": ["a", "b"]}},
    )
    assert subj.gold_at(0).get("cuisines_liked") == ["a"]
    assert subj.gold_at(1).get("cuisines_liked") == ["a", "b"]
    assert subj.gold_at(2).get("cuisines_liked") == ["a", "b"]


def test_overlay_is_empty_by_default_so_gold_is_the_card():
    subj = Subject(
        subject_id="s",
        gold=PersonaCard("s", {"cuisines_liked": ["a", "b", "c"]}),
        evidence=_weak_map(2),
    )
    assert subj.gold_at(0).get("cuisines_liked") == ["a", "b", "c"]
    assert subj.evidenced_gold_at == {}


def test_evidenced_gold_at_reads_only_the_four_list_fields():
    item = {
        "card_at": {
            "0": {"cuisines_liked": ["a"], "spice_tolerance": "mild",
                  "event_types_avoided": ["live_rock"], "dietary": ["vegan"]},
        }
    }
    table = sut._evidenced_gold_at(item)
    assert table[0] == {"cuisines_liked": ["a"], "event_types_avoided": ["live_rock"]}
    # scalar and hard-constraint list fields are left to the card/rewind path
    assert "spice_tolerance" not in table[0]
    assert "dietary" not in table[0]


def test_evidenced_gold_at_skips_null_entries():
    item = {"card_at": {"0": {"cuisines_liked": None}, "1": {"cuisines_liked": ["a"]}}}
    table = sut._evidenced_gold_at(item)
    assert 0 not in table
    assert table[1] == {"cuisines_liked": ["a"]}


def test_committing_the_evidenced_subset_now_beats_abstaining():
    """The inverted incentive, gone. Before the prune, gold held the full set and committing the
    correctly-read subset scored Jaccard(subset, full) < WEAK_CREDIT (abstaining). After, the
    evidenced subset IS the gold, so committing it scores 1.0."""
    full = ["ramen", "malaysian", "cajun", "soul_food", "dim_sum", "mediterranean"]
    subset = ["ramen", "malaysian"]
    # before: full gold
    before = metrics.agreement("cuisines_liked", subset, full)
    abstain = metrics.weak_abstention_credit("cuisines_liked", full)
    assert before < abstain  # committing the truth scored worse than saying nothing
    # after: pruned gold
    after = metrics.agreement("cuisines_liked", subset, subset)
    assert after == pytest.approx(1.0)
    assert after > abstain


def test_grade_may_not_lead_the_value():
    """A pruned taste-list field graded WEAK before its value surfaces is refused on load.

    The grade-leads-value gate (2026-08-28): if `card_at` is empty until the value first surfaces
    at S, no checkpoint before S may be WEAK/STRONG — else a twin correctly holding `[]` is scored
    against a value the transcript has not surfaced (gen-002/007/022/041). A field that never
    surfaces (evidenced-empty) is left alone.
    """
    def item(grades_by_cp, card_at_by_cp):
        return {
            "subject_id": "t", "card": {"cuisines_liked": ["bbq"]},
            "provisional": {str(k): {"cuisines_liked": g} for k, g in grades_by_cp.items()},
            "card_at": {str(k): {"cuisines_liked": v} for k, v in card_at_by_cp.items()},
        }

    # WEAK at c0 while the value only surfaces at c2 -> refused.
    bad = item({0: "WEAK", 1: "WEAK", 2: "WEAK"}, {0: [], 1: [], 2: ["bbq"]})
    with pytest.raises(ValueError, match="grade may not lead the value"):
        sut.evidence_map(bad)

    # UNEVIDENCED until the surface, WEAK after -> accepted.
    good = item({0: "UNEVIDENCED", 1: "UNEVIDENCED", 2: "WEAK"}, {0: [], 1: [], 2: ["bbq"]})
    sut.evidence_map(good)

    # Evidenced-empty (never surfaces) stays WEAK-empty and is left alone.
    empty = item({0: "WEAK", 1: "WEAK"}, {0: [], 1: []})
    sut.evidence_map(empty)


@pytest.mark.data
def test_published_key_oracle_is_1_and_validates(answer_key):
    """The shipped answer key, held to the invariant the prune exists to produce."""
    for it in answer_key["items"]:
        subj = sut.scorer_subject(it)
        subj.validate()
        assert sut.verify_gold(subj, it) == []
        oracle = EvidenceOracle(subj)
        for cp in range(subj.evidence.checkpoint_count):
            cs = scorer.score_checkpoint(subj, cp, oracle.emit(cp))
            if cs.a is not None:
                assert cs.a == pytest.approx(1.0), f"{it['subject_id']} cp{cp}"
