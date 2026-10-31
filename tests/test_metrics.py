"""Agreement functions (spec-v0 §8.1)."""

from __future__ import annotations

import math

import pytest

from persona_twin import fields, metrics


# --- Jaccard ----------------------------------------------------------------

def test_jaccard_identical_sets_agree():
    assert metrics.jaccard(["thai", "korean"], ["korean", "thai"]) == 1.0


def test_jaccard_disjoint_sets_score_zero():
    assert metrics.jaccard(["thai"], ["italian"]) == 0.0


def test_jaccard_partial_overlap():
    # |{a}| / |{a, b, c}|
    assert metrics.jaccard(["thai", "korean"], ["thai", "italian"]) == pytest.approx(1 / 3)


def test_two_empty_sets_are_perfect_agreement():
    """"This person has no allergies" is a real claim and must be scorable as correct."""
    assert metrics.jaccard([], []) == 1.0


def test_empty_against_nonempty_scores_zero():
    assert metrics.jaccard([], ["peanuts"]) == 0.0


# --- ordinals ---------------------------------------------------------------

def test_ordinal_exact_match():
    assert metrics.ordinal_agreement("spice_tolerance", "mild_only", "mild_only") == 1.0


def test_adjacent_ordinal_keeps_partial_credit():
    """spec §8.1: adjacent level scored as partial credit. 4 levels -> 1 - 1/3."""
    score = metrics.ordinal_agreement("spice_tolerance", "mild_only", "likes_real_spice")
    assert score == pytest.approx(2 / 3)


def test_opposite_ends_score_zero():
    score = metrics.ordinal_agreement("spice_tolerance", "avoids_heat", "seeks_hottest")
    assert score == 0.0


def test_ordinal_is_symmetric():
    a = metrics.ordinal_agreement("wait_tolerance", "leaves_if_line", "fifteen_min_max")
    b = metrics.ordinal_agreement("wait_tolerance", "fifteen_min_max", "leaves_if_line")
    assert a == b


def test_off_vocabulary_ordinal_level_is_an_error():
    """A level outside the closed set is a malformed card, not a zero score. Coercing it
    would let an SUT dodge scoring by emitting garbage."""
    with pytest.raises(ValueError, match="not a level of"):
        metrics.ordinal_agreement("spice_tolerance", "extremely_hot", "mild_only")


# --- distributions ----------------------------------------------------------

def test_identical_distributions_agree():
    dist = {"<£15": 0.5, "£15-30": 0.5}
    assert metrics.distribution_agreement("price_band_distribution", dist, dist) == 1.0


def test_disjoint_distributions_score_zero():
    """JSD in base 2 is exactly 1 for disjoint supports, so agreement bottoms out at 0.
    Base e would top out at ~0.693 and quietly compress the bottom of the scale."""
    p = {"<£15": 1.0}
    q = {"£50+": 1.0}
    assert metrics.distribution_agreement("price_band_distribution", p, q) == pytest.approx(0.0)


def test_bimodal_distribution_is_preserved_not_flattened():
    """spec §2.2: distributions exist so bimodal behaviour survives a flat card. A bimodal
    gold must score better against a bimodal guess than against its own mean."""
    gold = {"<£15": 0.5, "£15-30": 0.0, "£30-50": 0.0, "£50+": 0.5}
    bimodal_guess = {"<£15": 0.45, "£15-30": 0.05, "£30-50": 0.05, "£50+": 0.45}
    flattened_guess = {"<£15": 0.25, "£15-30": 0.25, "£30-50": 0.25, "£50+": 0.25}
    better = metrics.distribution_agreement("price_band_distribution", bimodal_guess, gold)
    worse = metrics.distribution_agreement("price_band_distribution", flattened_guess, gold)
    assert better > worse


def test_missing_bins_count_as_zero_mass():
    full = {"<£15": 0.5, "£15-30": 0.5, "£30-50": 0.0, "£50+": 0.0}
    sparse = {"<£15": 0.5, "£15-30": 0.5}
    assert metrics.distribution_agreement("price_band_distribution", sparse, full) == 1.0


def test_unnormalized_distribution_is_rejected():
    with pytest.raises(ValueError, match="sums to"):
        metrics.distribution_agreement(
            "price_band_distribution", {"<£15": 0.5, "£15-30": 0.9}, {"<£15": 1.0}
        )


def test_negative_mass_is_rejected():
    with pytest.raises(ValueError, match="negative mass"):
        metrics.distribution_agreement(
            "price_band_distribution", {"<£15": 1.5, "£15-30": -0.5}, {"<£15": 1.0}
        )


def test_unknown_bin_is_rejected():
    with pytest.raises(ValueError, match="unknown distribution bins"):
        metrics.distribution_agreement(
            "price_band_distribution", {"free": 1.0}, {"<£15": 1.0}
        )


# --- distribution shape (diagnostic, not a scored metric) -------------------


def test_shape_flags_a_point_mass_as_a_spike():
    """The failure this exists to catch: a mode guess wearing a distribution's clothes."""
    shape = metrics.distribution_shape("price_band_distribution", {"<£15": 1.0})
    assert shape.is_spike
    assert shape.support == 1
    assert shape.zero_bins == 3          # three implicit "never" claims
    assert shape.entropy == 0.0          # a spike carries no spread


def test_shape_does_not_flag_a_real_spread():
    gold = {"<£15": 0.62, "£15-30": 0.34, "£30-50": 0.04, "£50+": 0.0}
    shape = metrics.distribution_shape("price_band_distribution", gold)
    assert not shape.is_spike
    assert shape.support == 3            # <£15, £15-30, and £30-50 (0.04 clears the 2% floor)
    assert shape.zero_bins == 1
    assert shape.entropy == pytest.approx(1.1425, abs=1e-3)


def test_shape_is_independent_of_agreement():
    """A spike on the right mode and the true spread can post near-equal 1-JSD while their
    shapes differ categorically -- the reason shape is reported apart from the score."""
    gold = {"<£15": 0.62, "£15-30": 0.34, "£30-50": 0.04, "£50+": 0.0}
    spike = {"<£15": 1.0}
    assert metrics.distribution_agreement("price_band_distribution", spike, gold) > 0.75
    assert metrics.distribution_shape("price_band_distribution", spike).is_spike
    assert not metrics.distribution_shape("price_band_distribution", gold).is_spike


def test_jsd_is_bounded_in_unit_interval():
    """Bounded output is the reason JSD is used instead of KL, which diverges the first
    time an SUT puts zero mass on a bin ground truth uses."""
    cases = [
        ([1.0, 0.0], [0.0, 1.0]),
        ([0.5, 0.5], [0.5, 0.5]),
        ([0.9, 0.1], [0.1, 0.9]),
    ]
    for p, q in cases:
        divergence = metrics.jensen_shannon_divergence(p, q)
        assert 0.0 <= divergence <= 1.0
        assert not math.isnan(divergence)


def test_kl_would_diverge_where_jsd_does_not():
    """Regression guard for the zero-support case that motivates the choice of JSD."""
    agreement = metrics.distribution_agreement(
        "party_size_distribution",
        {"1": 1.0, "2": 0.0, "3-5": 0.0, "6+": 0.0},
        {"1": 0.0, "2": 1.0, "3-5": 0.0, "6+": 0.0},
    )
    assert agreement == pytest.approx(0.0)
    assert math.isfinite(agreement)


# --- dispatch ---------------------------------------------------------------

def test_dispatch_routes_each_metric_family():
    assert metrics.agreement("cuisines_liked", ["thai"], ["thai"]) == 1.0
    assert metrics.agreement("spice_tolerance", "mild_only", "mild_only") == 1.0
    assert metrics.agreement("alcohol_free_required", True, True) == 1.0
    assert metrics.agreement(
        "price_band_distribution", {"<£15": 1.0}, {"<£15": 1.0}
    ) == 1.0


def test_dispatch_rejects_a_string_for_a_set_field():
    """Guards a real failure mode: an LLM SUT emitting "thai" instead of ["thai"] would
    otherwise be scored as a 4-character set and silently earn partial credit."""
    with pytest.raises(TypeError, match="set-valued"):
        metrics.agreement("cuisines_liked", "thai", ["thai"])


def test_every_registered_field_has_a_dispatchable_metric():
    known = {
        fields.METRIC_JACCARD, fields.METRIC_ORDINAL,
        fields.METRIC_JSD, fields.METRIC_EXACT,
    }
    for spec in fields.ALL_FIELDS:
        assert spec.metric in known, f"{spec.field_id} declares unknown metric"


# --- weak-band discernment (diagnostic, not scored) -------------------------

def _cell(committed, agreement=None, chance=None):
    return metrics.WeakCell(committed=committed, agreement=agreement, chance_agreement=chance)


def test_weak_discernment_empty_is_all_none_not_zero():
    """An unmeasured axis is None, never 0.0 — a system that produced no weak cells did not
    fail the diagnostic, it simply was not exercised on it."""
    wd = metrics.weak_discernment([])
    assert wd.n == 0 and wd.committed == 0
    assert wd.commit_rate is None
    assert wd.commit_accuracy is None
    assert wd.chance_accuracy is None
    assert wd.discernment is None
    assert wd.calibration is None
    assert wd.wrong_rate is None


def test_weak_discernment_all_abstain_has_rate_but_no_accuracy():
    wd = metrics.weak_discernment([_cell(False, chance=0.5), _cell(False, chance=0.5)])
    assert wd.n == 2 and wd.committed == 0
    assert wd.commit_rate == 0.0
    # Nothing was committed, so accuracy/discernment/calibration are undefined, not zero.
    assert wd.commit_accuracy is None
    assert wd.discernment is None
    assert wd.calibration is None
    assert wd.wrong_rate is None


def test_weak_discernment_lift_and_calibration_math():
    cells = [_cell(True, 0.9, 0.5), _cell(True, 0.3, 0.5), _cell(False, None, 0.5)]
    wd = metrics.weak_discernment(cells, error_threshold=0.5)
    assert wd.n == 3 and wd.committed == 2
    assert wd.commit_rate == pytest.approx(2 / 3)
    assert wd.commit_accuracy == pytest.approx(0.6)          # (0.9 + 0.3) / 2
    assert wd.chance_accuracy == pytest.approx(0.5)          # over the committed cells only
    assert wd.discernment == pytest.approx(0.1)              # 0.6 - 0.5
    assert wd.calibration == pytest.approx(0.6 - metrics.WEAK_CREDIT)
    assert wd.wrong_rate == pytest.approx(0.5)               # one of two below 0.5


def test_weak_discernment_chance_paired_over_committed_only():
    """The base-rate line must be scored on the cells the SUT actually committed, so an
    abstained cell's chance value never enters the comparison."""
    cells = [_cell(True, 1.0, 0.2), _cell(False, None, 0.9)]
    wd = metrics.weak_discernment(cells)
    # Only the committed cell's chance (0.2) counts; the abstained 0.9 is excluded.
    assert wd.chance_accuracy == pytest.approx(0.2)
    assert wd.discernment == pytest.approx(0.8)


def test_weak_discernment_missing_chance_excluded_but_accuracy_kept():
    """A cell the guesser cannot score (chance None) still counts its agreement; it is only
    dropped from the chance mean."""
    cells = [_cell(True, 0.8, None), _cell(True, 0.6, 0.4)]
    wd = metrics.weak_discernment(cells)
    assert wd.commit_accuracy == pytest.approx(0.7)          # both committed cells
    assert wd.chance_accuracy == pytest.approx(0.4)          # only the one with a chance value
    assert wd.discernment == pytest.approx(0.3)
