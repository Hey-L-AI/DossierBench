"""Weak-band discernment reporter (`persona_twin.discern`).

The unit maths of `metrics.weak_discernment` live in `test_metrics.py`; here we check the
collection side — that a cell is drawn for exactly the weak preference fields, carries the
scorer's own grade/agreement, and pairs each with the base-rate guesser — using the toy
subjects rather than a run on disk.
"""

from __future__ import annotations

from persona_twin import discern, fields, metrics, scorer, toy
from persona_twin.card import PersonaCard
from persona_twin.evidence import Grade


def _mode() -> discern.ChanceModel:
    return discern.build_chance_model(toy.population_sample())


def test_cells_are_drawn_only_for_weak_preference_fields():
    subject = toy.evidence_ramp()
    mode = _mode()
    # Checkpoint 1 grades deal_seeking WEAK (ordinal) and price_band_distribution STRONG.
    committed_card = subject.gold_at(1)
    cells, strong = discern.cells_from_checkpoint(subject, 1, committed_card, mode)

    families = {fam for fam, _ in cells}
    field_count = len(cells)
    # Exactly one weak preference cell at cp1: deal_seeking.
    assert field_count == 1
    (family, cell), = cells
    assert family == "ordinal"
    assert cell.committed is True
    # Emitting gold commits the exact value, so a_f is 1.0 — the scorer's number, unrecomputed.
    assert cell.agreement == 1.0
    # STRONG committed fields feed the companion strong-accuracy list, all at 1.0 for gold.
    assert strong and all(a == 1.0 for a in strong)


def test_abstaining_yields_an_uncommitted_cell_with_no_agreement():
    subject = toy.evidence_ramp()
    mode = _mode()
    abstain = PersonaCard.all_unknown("toy-ramp")
    cells, strong = discern.cells_from_checkpoint(subject, 1, abstain, mode)

    (_, cell), = cells
    assert cell.committed is False
    assert cell.agreement is None
    # Abstaining commits nothing, so there are no strong-band agreements to report either.
    assert strong == []
    # The base-rate line is still available for the cell, independent of what the SUT did.
    assert cell.chance_agreement is not None


def test_chance_line_matches_population_mode_on_an_ordinal_cell():
    subject = toy.evidence_ramp()
    chance = _mode()
    cells, _ = discern.cells_from_checkpoint(subject, 1, subject.gold_at(1), chance)
    (_, cell), = cells
    # deal_seeking is ordinal, so the chance line is population-mode, unchanged by the set fix.
    expected = metrics.agreement(
        "deal_seeking",
        chance.mode.card_values["deal_seeking"],
        subject.gold_at(1).get("deal_seeking"),
    )
    assert cell.chance_agreement == expected


def test_grade_and_agreement_come_from_the_scorer():
    """The diagnostic must read the scorer's FieldOutcome, not its own grade lookup — so a
    field the scorer marks STRONG never appears as a weak cell even if committed wrongly."""
    subject = toy.evidence_ramp()
    mode = _mode()
    # cuisines_liked is STRONG at cp1; commit a wrong value for it and the correct weak one.
    values = dict(subject.gold_at(1).values)
    values["cuisines_liked"] = ["italian"]          # wrong, but STRONG → not a weak cell
    card = PersonaCard("toy-ramp", values)
    cells, strong = discern.cells_from_checkpoint(subject, 1, card, mode)

    weak_fields = {
        # recover the field via family isn't enough; assert no strong field leaked in
        cell.agreement for _, cell in cells
    }
    assert len(cells) == 1                            # still only deal_seeking
    # The wrong STRONG commit shows up in the strong companion list, scored below 1.0.
    assert any(a < 1.0 for a in strong)


def _mean_jaccard(candidate, golds):
    return sum(metrics.jaccard(candidate, g) for g in golds) / len(golds)


def test_best_population_set_beats_the_empty_mode_when_no_member_is_majority():
    """The caveat-1 fix: with a popular-but-sub-majority member, `baselines`' per-member-majority
    mode is empty (Jaccard 0 against every gold), but the best constant set beats it."""
    from persona_twin import baselines

    # 'thai' in 2 of 5, everything else in 1 — no member clears the 50% majority rule.
    golds = [
        frozenset({"thai", "korean"}),
        frozenset({"thai", "italian"}),
        frozenset({"french"}),
        frozenset({"greek"}),
        frozenset({"japanese"}),
    ]
    majority = baselines._modal_value(fields.BY_ID["cuisines_liked"], [sorted(g) for g in golds])
    assert majority == []                                   # the empty-mode problem, reproduced

    best = discern.best_population_set(golds)
    assert best != []                                       # a non-trivial guess instead
    assert _mean_jaccard(best, golds) > _mean_jaccard([], golds) == 0.0


def test_best_population_set_is_a_local_optimum():
    """The result's mean Jaccard cannot be improved by any single-member toggle — that is the
    contract, and it makes the discernment lift a fair 'could a population-only guesser match
    this' bar rather than a strawman."""
    golds = [
        frozenset({"thai", "korean"}),
        frozenset({"thai", "italian"}),
        frozenset({"french", "italian"}),
        frozenset({"greek"}),
    ]
    best = discern.best_population_set(golds)
    base = _mean_jaccard(best, golds)
    vocab = sorted(set().union(*golds))
    for member in vocab:
        toggled = set(best) ^ {member}
        assert _mean_jaccard(toggled, golds) <= base + 1e-12


def test_best_population_set_is_deterministic_and_empty_on_no_data():
    golds = [frozenset({"a", "b"}), frozenset({"b", "c"}), frozenset({"a", "c"})]
    assert discern.best_population_set(golds) == discern.best_population_set(golds)
    assert discern.best_population_set([]) == []


def test_chance_model_uses_best_set_for_jaccard_fields_only():
    chance = _mode()
    # Every Jaccard preference field has a set guess; ordinal/dist fields do not.
    for fid in discern._SET_PREF_IDS:
        assert fid in chance.set_guess
    assert "deal_seeking" not in chance.set_guess       # ordinal → still population-mode


def test_reporter_runs_end_to_end_on_toy_subjects(tmp_path):
    """`render` over a hand-built RunCells produces a table without raising."""
    subject = toy.evidence_ramp()
    mode = _mode()
    weak: list[metrics.WeakCell] = []
    by_family: dict[str, list[metrics.WeakCell]] = {n: [] for n, _ in discern._FAMILIES}
    strong: list[float] = []
    for cp in range(subject.evidence.checkpoint_count):
        cells, s = discern.cells_from_checkpoint(subject, cp, subject.gold_at(cp), mode)
        strong.extend(s)
        for fam, cell in cells:
            weak.append(cell)
            by_family[fam].append(cell)
    row = discern.RunCells("toy", "hand/written", weak, by_family, strong)
    text = discern.render([row])
    assert "discern" in text and "toy" in text
    wd = metrics.weak_discernment(weak, error_threshold=scorer.ERROR_THRESHOLD)
    # Committing the exact gold everywhere is perfect reading, so accuracy is 1.0 and it beats
    # the licensed indifference point (WEAK_CREDIT). Discernment vs the base-rate guesser is
    # *not* asserted positive here: on the toys the modal population value happens to equal
    # gold on the weak fields, so a base-rate guess scores 1.0 too and the lift is legitimately
    # zero — which is itself the point of pairing against a prior.
    assert wd.commit_accuracy == 1.0
    assert wd.discernment == 0.0
    assert wd.calibration is not None and wd.calibration > 0
