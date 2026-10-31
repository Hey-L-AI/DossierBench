"""`confirm.py` — key 3 asked as a question, repeated, and checked for direction."""

from __future__ import annotations

import json

import pytest

from persona_twin import confirm, fields


PAGE = ("[0] USER: nothing fancy, we're not spending big — a tenner-ish each ideally\n"
        "[1] BOT: Got it. Counter service or a table?\n"
        "[2] USER: counter's fine\n")


def _record(task="gen-001-s7"):
    return {"task_id": task, "subject_id": "gen-001", "transcript": PAGE}


def _reply(**over):
    answer = {"field_id": "formality_ceiling", "readable": True,
              "levels": ["counter_service"], "messages": [0],
              "quote": "nothing fancy, we're not spending big", "reads_as": "counter service"}
    answer.update(over)
    return json.dumps({"answers": [answer]})


def _invoker(raw):
    return lambda model, system, prompt: raw


def _one(raw, field="formality_ceiling"):
    return confirm.confirm_session(_invoker(raw), "m", _record(), [field])[0]


def test_the_prompt_shows_the_page_and_the_levels_but_never_the_card():
    prompt = confirm.confirm_prompt(_record(), ["formality_ceiling"])
    assert "nothing fancy" in prompt
    for level in fields.BY_ID["formality_ceiling"].levels:
        assert level in prompt
    # The card is the thing a targeted yes/no question is most suggestible to. A reader that
    # can see the answer is being asked to agree, not to read.
    assert "THE PERSON'S CARD" not in prompt and "counter_service = " not in prompt


def test_a_confirmation_whose_quote_is_not_on_the_page_does_not_confirm():
    answer = _one(_reply(quote="somewhere with proper linen"))
    assert answer.readable and not answer.quote_found and not answer.confirms


def test_a_quote_stitched_with_an_ellipsis_is_found_on_the_page():
    answer = _one(_reply(quote="nothing fancy ... counter's fine"))
    assert answer.quote_found and answer.confirms


def test_naming_every_level_is_not_a_narrowing_and_does_not_confirm():
    everything = list(fields.BY_ID["formality_ceiling"].levels)
    answer = _one(_reply(levels=everything))
    assert answer.readable and not answer.narrows and not answer.confirms


def test_a_level_the_field_does_not_have_is_dropped_rather_than_believed():
    answer = _one(_reply(levels=["counter_service", "michelin_starred"]))
    assert answer.levels == ("counter_service",)


def test_direction_separates_a_reading_that_agrees_from_one_that_opposes():
    agrees = _one(_reply(levels=["counter_service", "casual_sit_down"]))
    assert confirm.direction(agrees, "counter_service") == confirm.AGREES
    # Readable, narrowed, quoted from the page — and reading a value the card does not hold.
    # Crediting this would put a cell in the answer key whose page says something else.
    assert confirm.direction(agrees, "white_tablecloth") == confirm.OPPOSES
    assert confirm.direction(agrees, None) == confirm.UNCHECKED


def test_as_confirmations_drops_a_reading_that_opposes_the_card():
    answer = _one(_reply(levels=["casual_sit_down"]))
    cards = {"gen-001-s7": {"formality_ceiling": "counter_service"}}
    assert confirm.as_confirmations([answer]) != {}
    assert confirm.as_confirmations([answer], cards=cards) == {}


def test_an_unreadable_answer_is_not_a_failed_quote_check():
    answer = _one(_reply(readable=False, quote="", levels=[]))
    assert not answer.confirms and answer.quote_found


def test_a_tally_credits_a_majority_that_agrees_and_refuses_one_that_does_not():
    cards = {"gen-001-s7": {"formality_ceiling": "counter_service"}}
    agrees = _one(_reply(levels=["counter_service"]))
    opposes = _one(_reply(levels=["casual_sit_down"]))
    silent = _one(_reply(readable=False, quote="", levels=[]))

    two_of_three = confirm.tally([[agrees], [agrees], [silent]], cards=cards)
    assert two_of_three[("gen-001-s7", "formality_ceiling")].credits

    # A cell read twice AGAINST the card and once for it is a majority reading — of the wrong
    # value. `gen-001-s7` did exactly this on the pilot, and a single run credited it.
    split = confirm.tally([[agrees], [opposes], [opposes]], cards=cards)
    entry = split[("gen-001-s7", "formality_ceiling")]
    assert not entry.credits and entry.verdict == "opposes"

    one_of_three = confirm.tally([[agrees], [silent], [silent]], cards=cards)
    assert not one_of_three[("gen-001-s7", "formality_ceiling")].credits


def test_a_session_missing_from_one_run_is_not_counted_as_a_refusal():
    cards = {"gen-001-s7": {"formality_ceiling": "counter_service"}}
    agrees = _one(_reply(levels=["counter_service"]))
    entry = confirm.tally([[agrees], [], []], cards=cards)[("gen-001-s7", "formality_ceiling")]
    assert entry.runs == 1 and entry.credits


def test_consensus_is_shaped_like_the_verdict_credit_already_reads():
    cards = {"gen-001-s7": {"formality_ceiling": "counter_service"}}
    agrees = _one(_reply(levels=["counter_service"]))
    tallies = confirm.tally([[agrees], [agrees]], cards=cards)
    from persona_twin import credit
    assert "formality_ceiling" in credit.read_fields(
        confirm.consensus(tallies)["gen-001-s7"]
    )


def test_merge_keeps_both_a_volunteered_reading_and_an_asked_one():
    free = {"gen-001-s7": {"model": "claude-sonnet-5",
                           "unscheduled": [{"field_id": "price_band_distribution"}]}}
    asked = {"gen-001-s7": {"model": "claude-sonnet-5",
                            "unscheduled": [{"field_id": "formality_ceiling"}]}}
    from persona_twin import credit
    both = credit.read_fields(confirm.merge(free, asked)["gen-001-s7"])
    assert both == {"price_band_distribution", "formality_ceiling"}


def test_the_cache_misses_when_the_question_list_changes():
    # The cache is fingerprinted on (system, prompt), so the prompt has to carry the question
    # list. Otherwise adding a field to an existing session returns the old reply, which
    # answers a different question than the one asked.
    one = confirm.confirm_prompt(_record(), ["formality_ceiling"])
    two = confirm.confirm_prompt(_record(), ["formality_ceiling", "travel_radius"])
    assert one != two


def test_an_unparseable_reply_raises_rather_than_counting_as_a_refusal():
    with pytest.raises(Exception):
        confirm.confirm_session(_invoker("not json at all"), "m", _record(),
                                ["formality_ceiling"])


# --- set-valued cards (owner decision, 2026-08-21) -----------------------------------
#
# `direction` stringified the whole card value, so `str(["vegan"])` was `"['vegan']"` and no
# set field could ever agree. 7 of 24 fields were structurally uncreditable and a reading of
# `vegan` against a card of `vegan` was recorded as reading AGAINST it.


def _answer(field_id: str, levels):
    return confirm.Answer(
        task_id="gen-000-s0", field_id=field_id, readable=True, messages=(),
        quote="", reads_as="", model="m", levels=tuple(levels), quote_found=True,
    )


def test_direction_set_field_exact_match_agrees():
    """The regression: card ['vegan'], read ['vegan'], recorded as OPPOSES."""
    assert confirm.direction(_answer("dietary", ["vegan"]), ["vegan"]) == confirm.AGREES


def test_direction_set_field_partial_reading_agrees():
    """One of four liked outings narrows the field. Credit is capped at WEAK regardless."""
    card = ["tasting_menu", "museum_late", "dance_night", "art_opening"]
    assert confirm.direction(_answer("event_types_liked", ["tasting_menu"]), card) \
        == confirm.AGREES


def test_direction_set_field_off_card_reading_opposes():
    """`gen-004-s0`: reads `cooking_class` against a card listing four other outings.

    The whole point of the guard — a cell credited here would hold a value the page narrows
    away from, so a twin reading the page correctly would be scored wrong.
    """
    card = ["tasting_menu", "museum_late", "dance_night", "art_opening"]
    assert confirm.direction(_answer("event_types_liked", ["cooking_class"]), card) \
        == confirm.OPPOSES


def test_direction_set_field_mixed_reading_opposes():
    """Some on-card, some off, and the off ones are NOT entailed. Still opposition.

    This is the half of the mixed case the pilot evidence actually supports, and it is
    unchanged by `ENTAILED_LEVELS`: nothing entails `cooking_class`.
    """
    card = ["tasting_menu", "museum_late"]
    assert confirm.direction(_answer("event_types_liked", ["tasting_menu", "cooking_class"]),
                             card) == confirm.OPPOSES


def test_direction_set_field_entailed_extras_agree():
    """`gen-004-s1`: card ['vegan'], read ['vegetarian', 'vegan', 'dairy_free'].

    The cell that forced `ENTAILED_LEVELS`. Every extra level is a consequence of the value
    the card holds, so the reading names the same constraint at a finer grain — and the old
    rule recorded it as a reading AGAINST its own card, 2 of 3 runs, which put the cell in
    the owner queue as a split the readers had not had.
    """
    assert confirm.direction(
        _answer("dietary", ["vegetarian", "vegan", "dairy_free"]), ["vegan"],
    ) == confirm.AGREES


def test_direction_entailment_does_not_forgive_an_unentailed_extra():
    """`keto` is not a consequence of `vegan`, so the guard still fires on the same field."""
    assert confirm.direction(_answer("dietary", ["vegan", "keto"]), ["vegan"]) \
        == confirm.OPPOSES


def test_direction_entailment_alone_is_not_agreement():
    """A reading of `['vegetarian']` against `['vegan']` names no value the card holds.

    `read & held` is still required. The extras are forgiven only once the reader has
    reached the card's own level, which is what keeps this an entailment rule rather than a
    quiet relaxation to "any overlap with anything nearby".
    """
    assert confirm.direction(_answer("dietary", ["vegetarian"]), ["vegan"]) \
        == confirm.OPPOSES


def test_direction_entailment_is_directional():
    """`vegan` entails `vegetarian`; the reverse is the reader claiming MORE than the card.

    A card of `['vegetarian']` read as `['vegetarian', 'vegan']` is the inversion `OPPOSES`
    exists to catch — a twin committing to `vegan` off that page would be fabricating.
    """
    assert confirm.direction(_answer("dietary", ["vegetarian", "vegan"]), ["vegetarian"]) \
        == confirm.OPPOSES


def test_entailed_levels_table_has_not_grown_past_its_evidence():
    """One entry, one measured cell. `schedule.ENTAILED_BY_BIN`'s comment applies here.

    This test exists to make an addition deliberate rather than incidental: extending the
    table is a decision about the answer key, and it should arrive with the cell that needs
    it and a recorded reason, not as a tidy-up.
    """
    assert set(confirm.ENTAILED_LEVELS) == {("dietary", "vegan")}


def test_direction_distribution_card_credits_a_bin_the_card_gives_weight():
    """`gen-007-s3`: reads `£30-50` on "forty a head ... that could work".

    The card gives that bin 0.32. Before stage 23 a mapping card fell through to the scalar
    branch, which asked `str({...}) in levels` — so every distribution cell was uncreditable
    BY CONSTRUCTION and recorded as a false OPPOSES rather than as a refusal.
    """
    card = {"<\u00a315": 0.0, "\u00a315-30": 0.0, "\u00a330-50": 0.32, "\u00a350+": 0.68}
    assert confirm.direction(_answer("price_band_distribution", ["\u00a330-50"]), card) \
        == confirm.AGREES


def test_direction_distribution_card_opposes_a_bin_the_card_zeroes():
    """A card giving the read bin 0.00 IS contradicted by the page, and the guard still fires.

    Same judgement and same 0.02 as `verify.py`'s `unscheduled_occasion`, which blocks only
    when every bin the page could be carrying has less than that.
    """
    card = {"<\u00a315": 0.0, "\u00a315-30": 0.0, "\u00a330-50": 0.32, "\u00a350+": 0.68}
    assert confirm.direction(_answer("price_band_distribution", ["<\u00a315"]), card) \
        == confirm.OPPOSES


def test_direction_distribution_reads_the_best_bin_it_named():
    """One plausible bin the card allows is agreement, exactly as `unscheduled_occasion` reads
    a set of candidate bins by their maximum share rather than requiring all of them."""
    card = {"<\u00a315": 0.0, "\u00a315-30": 0.0, "\u00a330-50": 0.32, "\u00a350+": 0.68}
    assert confirm.direction(_answer("price_band_distribution", ["<\u00a315", "\u00a350+"]),
                             card) == confirm.AGREES


def test_direction_scalar_field_is_unchanged_by_the_set_branch():
    """Scalar semantics stay overlap: the card's level among the reader's candidates."""
    a = _answer("noise_preference", ["fine_with_lively", "wants_loud"])
    assert confirm.direction(a, "wants_loud") == confirm.AGREES
    assert confirm.direction(a, "needs_quiet") == confirm.OPPOSES


def test_consensus_records_which_model_confirmed():
    """`consensus` hardcoded model="", so every credit through the tally path recorded
    `confirmed_by=()` and failed the "every credit has a 2nd reader" falsifier — on all 23
    credits of the 2026-08-21 pilot. The gate was live; only the provenance was lost."""
    answers = [confirm.Answer(
        task_id="gen-000-s0", field_id="noise_preference", readable=True, messages=(9,),
        quote="somewhere I can hear myself think", reads_as="needs_quiet",
        model="claude-sonnet-5", levels=("needs_quiet",), quote_found=True,
    )]
    cards = {"gen-000-s0": {"noise_preference": "needs_quiet"}}
    tallies = confirm.tally([answers], cards=cards)
    entry = next(iter(tallies.values()))
    assert entry.models == ("claude-sonnet-5",)
    assert entry.credits
    built = confirm.consensus(tallies)
    assert built["gen-000-s0"]["model"] == "claude-sonnet-5", \
        "the confirming reader must reach credit.eligible, or confirmed_by is empty"


def _tally_of(field_id, card, read, task_id="gen-003-s1"):
    a = confirm.Answer(task_id=task_id, field_id=field_id, readable=True, messages=(3,),
                       quote="q", reads_as="", model="claude-sonnet-5",
                       levels=tuple(read), quote_found=True)
    return confirm.tally([[a]], cards={task_id: {field_id: card}})


def test_veto_drops_a_cell_the_targeted_ask_read_against_the_card():
    """`gen-003-s1`: card room_matters, targeted ask reads mild_preference. Free recall had
    merely NAMED the field, which is recall, and it was overriding a direction check."""
    tallies = _tally_of("design_sensitivity", "room_matters", ["mild_preference"])
    recall = {"gen-003-s1": {"task_id": "gen-003-s1", "model": "claude-sonnet-5",
                             "unscheduled": [{"field_id": "design_sensitivity"}],
                             "beats": [], "contradictions": []}}
    out = confirm.veto(recall, tallies)
    assert out["gen-003-s1"]["unscheduled"] == []


def test_veto_keeps_a_cell_the_targeted_ask_agreed_with():
    tallies = _tally_of("design_sensitivity", "room_matters", ["room_matters"])
    recall = {"gen-003-s1": {"task_id": "gen-003-s1", "model": "claude-sonnet-5",
                             "unscheduled": [{"field_id": "design_sensitivity"}],
                             "beats": [], "contradictions": []}}
    assert confirm.veto(recall, tallies)["gen-003-s1"]["unscheduled"] != []


def test_veto_does_not_fire_on_silence():
    """A cell the targeted ask never reached falls back to free recall unchanged."""
    recall = {"gen-003-s1": {"task_id": "gen-003-s1", "model": "claude-sonnet-5",
                             "unscheduled": [{"field_id": "design_sensitivity"}],
                             "beats": [], "contradictions": []}}
    assert confirm.veto(recall, {})["gen-003-s1"]["unscheduled"] != []


# --- a question the reply did not answer (2026-08-23) --------------------------------
#
# `confirm_session` synthesised `readable: false` for any asked field the reply did not
# mention. Downstream that is indistinguishable from a reader that looked and said no: it
# lands in `Tally.silent` and costs the cell the majority `credits` requires. `tally`'s own
# docstring already decides the rule — a run that says nothing about a cell says nothing
# about it — and it can only apply that rule to an answer that is absent.


def test_a_question_the_reply_ignored_yields_no_answer_rather_than_a_refusal():
    raw = json.dumps({"answers": [{"field_id": "travel_radius", "readable": True,
                                   "levels": ["walking_distance"], "messages": [0],
                                   "quote": "counter's fine", "reads_as": "close"}]})
    got = confirm.confirm_session(_invoker(raw), "m", _record(),
                                  ["formality_ceiling", "travel_radius"])
    assert [a.field_id for a in got] == ["travel_radius"]


def test_an_ignored_question_does_not_reach_the_tally_as_silence():
    raw = json.dumps({"answers": []})
    got = confirm.confirm_session(_invoker(raw), "m", _record(), ["formality_ceiling"])
    assert got == []
    assert confirm.tally([got], cards={}) == {}


# --- a set field answered level by level ---------------------------------------------
#
# The regression: asked about `allergies`, the reader replied with one answer per allergen.
# `wheat` was readable and quoted; the cell was recorded as two silences and lost its credit.


def _allergen_reply(wheat_readable=True):
    answers = [{"field_id": name, "readable": False, "levels": [], "messages": [],
                "quote": "", "reads_as": f"no mention of {name}"}
               for name in fields.BY_ID["allergies"].levels if name != "wheat"]
    answers.append({"field_id": "wheat", "readable": wheat_readable,
                    "levels": ["wheat"] if wheat_readable else [], "messages": [2],
                    "quote": "counter's fine" if wheat_readable else "",
                    "reads_as": "a no-wheat note on the booking"})
    return json.dumps({"answers": answers})


def test_a_set_field_answered_one_level_at_a_time_is_still_an_answer():
    answer = _one(_allergen_reply(), field="allergies")
    assert answer.field_id == "allergies" and answer.readable
    assert answer.levels == ("wheat",) and answer.quote_found and answer.confirms


def test_a_level_by_level_reply_that_reads_nothing_is_a_refusal_it_actually_made():
    answer = _one(_allergen_reply(wheat_readable=False), field="allergies")
    assert answer.field_id == "allergies" and not answer.readable
    assert answer.reads_as                     # the sentence the prompt asks for, not ""


def test_a_level_by_level_reply_still_faces_the_direction_check():
    # Folding does not credit anything. An on-card level and an off-card one together is
    # the mixed case, and `direction` still calls it opposition.
    answer = _one(_allergen_reply(), field="allergies")
    assert confirm.direction(answer, ["wheat"]) == confirm.AGREES
    assert confirm.direction(answer, ["peanuts"]) == confirm.OPPOSES
