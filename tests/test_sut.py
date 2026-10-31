"""The submission side: the §1.3 protocol, reading a reply, and the harness that serves it.

Every test here is free. The model boundary is one callable (`judge.Invoker`), so a fake that
returns canned JSON exercises the whole loop — which is the point of keeping the transport that
thin in the first place.
"""

from __future__ import annotations

import json

import pytest

from persona_twin import fields, scorer, sut
from persona_twin.card import PersonaCard
from persona_twin.fields import UNKNOWN


# --- a corpus small enough to read ------------------------------------------


CARD = {
    "dietary": ["vegetarian"],
    "allergies": [],
    "cuisines_liked": ["japanese", "italian"],
    "spice_tolerance": "mild_only",
    "price_band_distribution": {"<£15": 0.1, "£15-30": 0.6, "£30-50": 0.2, "£50+": 0.1},
    "deal_seeking": "notices_value",
    "noise_preference": "needs_quiet",
    "formality_ceiling": "casual_sit_down",
    "party_size_distribution": {"1": 0.2, "2": 0.5, "3-5": 0.2, "6+": 0.1},
    "planning_style": "books_days_out",
    "travel_radius": "own_neighborhood",
    "wait_tolerance": "fifteen_min_max",
}


def _card() -> dict:
    """The gold card, filled from the registry so a new field cannot silently break these
    fixtures — an unlisted field takes its first level, which is a value like any other."""
    out = dict(CARD)
    for spec in fields.ALL_FIELDS:
        if spec.field_id in out:
            continue
        if spec.metric == fields.METRIC_JACCARD:
            out[spec.field_id] = []
        elif spec.metric == fields.METRIC_EXACT:
            out[spec.field_id] = False
        elif spec.metric == fields.METRIC_ORDINAL:
            out[spec.field_id] = spec.levels[0]
        else:
            out[spec.field_id] = {b: 1.0 / len(spec.levels) for b in spec.levels}
    return out


#: Wide enough for a checkpoint to enter AUFC. `scorer.MIN_EVIDENCED_FOR_AUFC` is 7 (ADR-0004):
#: a checkpoint with two evidenced fields is scored and published but kept out of the mean,
#: because one fabrication there moves `F_raw` further than the whole agreement range.
_STRONG = ("cuisines_liked", "dietary", "noise_preference", "travel_radius", "planning_style")
_WEAK = ("spice_tolerance", "deal_seeking", "party_size_distribution", "wait_tolerance")


def _item(subject_id="gen-000", checkpoints=3, strong=_STRONG, weak=_WEAK):
    card = _card()
    provisional = {
        str(c): {
            f.field_id: (
                "STRONG" if f.field_id in strong and c >= 1
                else "WEAK" if f.field_id in weak and c >= 1
                else "UNEVIDENCED"
            )
            for f in fields.ALL_FIELDS
        }
        for c in range(checkpoints)
    }
    return {
        "subject_id": subject_id,
        "split": "public",
        "card": card,
        "card_at": {str(c): card for c in range(checkpoints)},
        "provisional": provisional,
        "corrections": [],
        "distractor_checkpoints": [],
        "hard_case_tags": [],
        "sessions": [
            {"task_id": f"{subject_id}-s{c}", "subject_id": subject_id, "session": c,
             "checkpoint": c, "date": f"2025-01-0{c + 1}"}
            for c in range(checkpoints)
        ],
    }


def _records(item, text="[0] USER: hello\n[1] BOT: hi"):
    return {
        task["task_id"]: {
            "task_id": task["task_id"], "subject_id": item["subject_id"],
            "author": "claude-opus-5", "session": task["session"],
            "checkpoint": task["checkpoint"],
            "transcript": f"{text} (session {task['session']})",
            # Answer key, deliberately present on the record and never served.
            "beats": [{"field_id": "cuisines_liked", "gold_value": ["japanese"]}],
            "is_distractor": False, "self_report": "I am adventurous",
        }
        for task in item["sessions"]
    }


class FakeModel:
    """An invoker that returns a canned card and remembers what it was shown."""

    def __init__(self, reply=None):
        self.prompts: list[str] = []
        self.systems: list[str] = []
        self.reply = reply if reply is not None else json.dumps(
            {"card": {"cuisines_liked": ["japanese", "italian"]}, "notes": "they like japanese"}
        )

    def __call__(self, model, system, prompt):
        self.prompts.append(prompt)
        self.systems.append(system)
        return self.reply(len(self.prompts) - 1) if callable(self.reply) else self.reply


# --- the §1.3 protocol -------------------------------------------------------


def test_a_session_is_served_once_and_only_at_its_own_checkpoint():
    """The protocol rule made structural: the harness hands over the new sessions and never
    rebuilds the prefix. Re-serving would make full-transcript replay the benchmark."""
    item = _item(checkpoints=4)
    served, problems = sut.serve(item, _records(item))
    assert problems == []
    seen = [[s.session for s in batch] for batch in served.batches]
    assert seen == [[0], [1], [2], [3]]


def test_a_checkpoint_carrying_two_sessions_serves_both_together():
    """v0.1 checkpoints sessions 1:1 but nothing in the harness may assume it — a checkpoint is
    a batch, and the scorer indexes the map by checkpoint."""
    item = _item(checkpoints=2)
    item["sessions"].append(
        {"task_id": "gen-000-s2", "subject_id": "gen-000", "session": 2, "checkpoint": 1,
         "date": "2025-01-03"})
    served, _ = sut.serve(item, _records(item))
    assert [[s.session for s in b] for b in served.batches] == [[0], [1, 2]]


def test_a_hole_makes_the_subject_unservable_rather_than_shorter():
    """`judge.Bundle.prefix`'s rule. Serving sessions 1 and 3 as 1 and 2 shows evidence in an
    order the timeline never had, and every later checkpoint is then scored against a map that
    counted the missing one."""
    item = _item(checkpoints=3)
    records = _records(item)
    records["gen-000-s1"]["transcript"] = "   "
    served, problems = sut.serve(item, records)
    assert served is None
    assert any("s1 is missing or empty" in p for p in problems)


def test_the_submission_is_served_transcripts_and_nothing_else():
    """Beats, the self-report and `is_distractor` are answer key. `ServedSession` is where that
    line is drawn once instead of at each call site."""
    item = _item()
    served, _ = sut.serve(item, _records(item))
    text = sut.render_batch(served.batches[0])
    assert "session 1" in text and "USER: hello" in text
    assert "gold_value" not in text and "adventurous" not in text


def test_the_prompt_carries_the_new_sessions_and_not_the_earlier_ones():
    """What the SUT wants from an earlier session it has to have kept — and the keeping shows
    up on its own state meter, which is where the leaderboard can see it."""
    item = _item(checkpoints=3)
    served, _ = sut.serve(item, _records(item))
    model = FakeModel()
    extractor = sut.PromptedExtractor(model="fake", invoke=model)
    sut.run_one(extractor, served)
    assert "session 3" in model.prompts[-1]
    assert "session 1)" not in model.prompts[-1]


def test_state_does_not_survive_a_reset():
    """No cross-subject carryover. An SUT accumulating base rates across subjects can guess
    unevidenced fields profitably, which defeats the fabrication rule outright."""
    extractor = sut.PromptedExtractor(model="fake", invoke=FakeModel())
    extractor.reset("gen-000")
    extractor.observe([sut.ServedSession("gen-000", 0, 0, "2025-01-01", "[0] USER: hi")])
    assert extractor.carried_state_tokens() > 0
    extractor.reset("gen-001")
    assert extractor._card.values["cuisines_liked"] == UNKNOWN


def test_a_fresh_submission_is_built_for_every_subject():
    """Belt and braces over `reset`: the harness should not be the only thing between a leaky
    submission and a profitable guess."""
    built: list[object] = []

    def factory():
        one = sut.PromptedExtractor(model="fake", invoke=FakeModel())
        built.append(one)
        return one

    items = [_item("gen-000"), _item("gen-001")]
    served = [sut.serve(i, _records(i))[0] for i in items]
    sut.run(factory, served, workers=1)
    assert len(built) == 2 and built[0] is not built[1]


# --- state metering ----------------------------------------------------------


def test_the_notebook_carries_more_state_than_the_card_alone():
    """The axis the two reference SUTs exist to make legible. Same loop, same protocol, an
    order of magnitude between their meters."""
    session = [sut.ServedSession("gen-000", 0, 0, "2025-01-01", "[0] USER: hi")]
    plain = sut.PromptedExtractor(model="fake", invoke=FakeModel())
    plain.reset("gen-000")
    plain.observe(session)
    book = sut.NotebookExtractor(model="fake", invoke=FakeModel(json.dumps(
        {"card": {}, "notes": "x" * 400})))
    book.reset("gen-000")
    book.observe(session)
    assert book.carried_state_tokens() > plain.carried_state_tokens()
    assert plain.carried_state_bytes() > 0


def test_an_empty_batch_costs_no_call():
    """A checkpoint with no new text cannot move the card, and spending a call to be told so is
    spending a call to be told so."""
    model = FakeModel()
    extractor = sut.PromptedExtractor(model="fake", invoke=model)
    extractor.reset("gen-000")
    extractor.observe([])
    assert model.prompts == []


# --- reading a reply ---------------------------------------------------------


def test_an_empty_list_is_a_commitment_and_unknown_is_not():
    """ADR-0035 made an absence something the dataset discloses and the map grades. Reading
    `[]` as an abstention would turn every correct 'they have no allergies' into a miss on a
    gating field."""
    spec = fields.BY_ID["allergies"]
    assert sut.coerce_value(spec, []) == ([], None)
    assert sut.coerce_value(spec, "unknown") == (UNKNOWN, None)
    assert sut.coerce_value(spec, None) == (UNKNOWN, None)


def test_an_out_of_vocabulary_member_is_dropped_and_the_rest_stands():
    value, problem = sut.coerce_value(fields.BY_ID["cuisines_liked"], ["Japanese", "martian"])
    assert value == ["japanese"]
    assert "martian" in problem


def test_a_distribution_that_nearly_sums_to_one_is_renormalized():
    """A rounding habit is not a malformed card, and `_validate_value` would refuse it."""
    spec = fields.BY_ID["price_band_distribution"]
    value, problem = sut.coerce_value(spec, {b: 0.24 for b in spec.levels})
    assert sum(value.values()) == pytest.approx(1.0)
    assert "renormalized" in problem


def test_an_unreadable_value_abstains_on_that_field_and_no_other():
    """A malformed value degrades one field. Refusing the whole reply would score a formatting
    habit as a total failure at that checkpoint."""
    emission = sut.parse_emission(
        json.dumps({"card": {"spice_tolerance": "quite hot", "cuisines_liked": ["italian"]}}),
        "gen-000")
    assert emission.card.get("spice_tolerance") == UNKNOWN
    assert emission.card.get("cuisines_liked") == ["italian"]
    assert any("spice_tolerance" in p for p in emission.problems)


def test_a_reply_that_parses_as_nothing_holds_the_previous_card():
    """Standing still is the honest reading of 'no answer arrived'. Emptying the card would
    charge a transport accident to twenty fields at once."""
    previous = PersonaCard.all_unknown("gen-000")
    previous.values["cuisines_liked"] = ["italian"]
    emission = sut.parse_emission("the model apologises", "gen-000", previous=previous)
    assert emission.card.get("cuisines_liked") == ["italian"]
    assert emission.problems


def test_fields_emitted_without_the_wrapper_are_read_rather_than_refused():
    emission = sut.parse_emission(json.dumps({"cuisines_liked": ["italian"]}), "gen-000")
    assert emission.card.get("cuisines_liked") == ["italian"]


def test_a_card_the_schema_refuses_abstains_on_that_field():
    """The last gate, so a field whose metric `coerce_value` does not know cannot put a card the
    scorer raises on into a run that has already spent its calls."""
    card = PersonaCard.all_unknown("gen-000")
    card.values["spice_tolerance"] = "scorching"
    fixed, problems = sut.validated(card)
    assert fixed.get("spice_tolerance") == UNKNOWN and problems


# --- the distribution histogram ----------------------------------------------


def _one(subject="gen-000", checkpoint=0, text="[0] USER: hi"):
    return [sut.ServedSession(subject, checkpoint, checkpoint, "2025-01-01", text)]


def test_datapoints_are_read_by_count_and_out_of_vocabulary_is_dropped():
    """Each entry is one occasion, so a repeated bin is two datapoints, not a de-duplicated one;
    a bin outside the field's vocabulary is dropped with a complaint, the rest standing."""
    payload = {"datapoints": {
        "party_size_distribution": ["2", "2", "6+"],
        "price_band_distribution": ["£30-50", "a fiver"],
    }}
    datapoints, problems, drops = sut.parse_datapoints(payload)
    assert datapoints["party_size_distribution"] == ("2", "2", "6+")
    assert datapoints["price_band_distribution"] == ("£30-50",)
    assert datapoints["visit_time_distribution"] == ()
    assert any("a fiver" in p for p in problems)
    assert drops["price_band_distribution"] == 1                  # "a fiver" dropped once
    assert drops["party_size_distribution"] == 0


def test_a_histogram_normalizes_its_tally_and_an_untouched_field_is_unknown():
    """The value is the tally, always — there is no share the model estimated. A field that has
    seen no occasion is `"unknown"`, not a uniform guess, which is the calibration the whole
    benchmark prices."""
    hist = sut.Histogram()
    hist.add({"party_size_distribution": ("2", "2", "6+")})
    derived = hist.derive()
    assert derived["party_size_distribution"] == {"1": 0.0, "2": 2 / 3, "3-5": 0.0, "6+": 1 / 3}
    assert derived["visit_time_distribution"] == UNKNOWN


def test_the_distributions_are_built_from_datapoints_not_the_emitted_card():
    """Even if the model writes a distribution object into the card, the histogram overrides it —
    the occasions the model reported are the only thing that counts. With no datapoints the field
    is `"unknown"` however confident that stray object was."""
    reply = json.dumps({
        "card": {"price_band_distribution": {"<£15": 0.0, "£15-30": 0.0, "£30-50": 1.0, "£50+": 0.0}},
        "datapoints": {"party_size_distribution": ["2"]},
    })
    extractor = sut.PromptedExtractor(model="fake", invoke=FakeModel(reply))
    extractor.reset("gen-000")
    card = extractor.observe(_one())
    assert card.get("price_band_distribution") == UNKNOWN         # in the card, but no datapoint
    assert card.get("party_size_distribution") == {"1": 0.0, "2": 1.0, "3-5": 0.0, "6+": 0.0}


def test_datapoints_accumulate_across_checkpoints():
    """The point of the histogram: a batch reports only its new occasions, and the distribution is
    the running tally over every batch — not something re-estimated whole each checkpoint."""
    replies = [
        json.dumps({"card": {}, "datapoints": {"price_band_distribution": ["£30-50"]}}),
        json.dumps({"card": {}, "datapoints": {"price_band_distribution": ["£50+"]}}),
    ]
    extractor = sut.PromptedExtractor(model="fake", invoke=FakeModel(lambda i: replies[i]))
    extractor.reset("gen-000")
    extractor.observe(_one(checkpoint=0))
    card = extractor.observe(_one(checkpoint=1))
    assert card.get("price_band_distribution") == {
        "<£15": 0.0, "£15-30": 0.0, "£30-50": 0.5, "£50+": 0.5}


def test_the_histogram_counts_are_on_the_state_meter():
    """The tally is carried state — it is what keeps the distribution growing, and its denominator
    is not recoverable from the normalised card — so it must show up on the meter (§1.3)."""
    with_points = sut.PromptedExtractor(model="fake", invoke=FakeModel(json.dumps(
        {"card": {}, "datapoints": {"party_size_distribution": ["2", "3-5", "6+"]}})))
    with_points.reset("gen-000")
    with_points.observe(_one())
    without = sut.PromptedExtractor(model="fake", invoke=FakeModel(json.dumps({"card": {}})))
    without.reset("gen-000")
    without.observe(_one())
    assert with_points.carried_state_tokens() > without.carried_state_tokens()


def test_the_histogram_does_not_survive_a_reset():
    """No cross-subject carryover of the tally either — a distribution accumulated on one person
    must not seed the next (`baselines.SUT.reset`)."""
    extractor = sut.PromptedExtractor(model="fake", invoke=FakeModel(json.dumps(
        {"card": {}, "datapoints": {"party_size_distribution": ["2"]}})))
    extractor.reset("gen-000")
    extractor.observe(_one())
    assert extractor._histogram.state()
    extractor.reset("gen-001")
    assert extractor._histogram.state() == {}


def test_the_memory_sut_counts_datapoints_during_extraction():
    """Integration never sees the conversation, so the occasions are counted in phase 1 and the
    distribution is filled in from the tally rather than emitted by phase 2."""
    extract = json.dumps({
        "memories": ["a table for two"],
        "datapoints": {"party_size_distribution": ["2", "2"]}})
    integrate = json.dumps({"memory_ops": [], "card": {}})
    model = TwoPhaseFakeModel(extraction_reply=extract, integration_reply=integrate)
    extractor = sut.MemoryExtractor(model="fake", invoke=model)
    extractor.reset("gen-000")
    card = extractor.observe(_one())
    assert card.get("party_size_distribution") == {"1": 0.0, "2": 1.0, "3-5": 0.0, "6+": 0.0}
    assert extractor._histogram.state()["party_size_distribution"] == {"2": 2}


def test_off_vocab_datapoints_surface_as_a_drop_rate_on_the_score():
    """The diagnostic the scattered `! …: dropped out-of-vocabulary …` warnings never rolled up:
    a submission whose occasions miss the closed bins is silently starved of the distribution
    fields, and `SubjectScore` now publishes how badly. Two in-vocab occasions and two off-vocab
    (`"4"` for the `"3-5"` bucket, `"weekend-daytime"` for `"weekend-day"`) is a 50% drop."""
    item = _item(checkpoints=1)
    served, _ = sut.serve(item, _records(item))
    reply = json.dumps({"card": {}, "datapoints": {
        "party_size_distribution": ["2", "4"],                   # "4" is off-vocab
        "visit_time_distribution": ["weekend-day", "weekend-daytime"],  # second is off-vocab
    }})
    score = sut.run_one(sut.PromptedExtractor(model="fake", invoke=FakeModel(reply)), served)
    assert score.datapoint_kept == 2
    assert score.datapoint_dropped == 2
    assert score.datapoint_off_vocab_rate() == pytest.approx(0.5)
    (cell,) = scorer.aggregate([score])
    assert cell.datapoints_dropped == 2
    assert cell.datapoint_off_vocab_rate == pytest.approx(0.5)


def test_a_submission_that_emits_no_datapoints_reports_no_drop_rate():
    """A clean 0/0, not a divide-by-zero: `abstain` emits nothing, so its drop rate is None
    rather than 0% — there were no occasions to be faithful or unfaithful about."""
    item = _item(checkpoints=2)
    served, _ = sut.serve(item, _records(item))
    factory = sut.factories(["abstain"], [served], model="fake")["abstain"]
    score = sut.run_one(factory(), served)
    assert (score.datapoint_kept, score.datapoint_dropped) == (0, 0)
    assert score.datapoint_off_vocab_rate() is None
    (cell,) = scorer.aggregate([score])
    assert cell.datapoint_off_vocab_rate is None


def test_a_whole_store_readout_rebuilds_the_distribution_and_an_unparse_holds_it():
    """`rederived_distributions` is the mem0 path: no batch to accumulate, so the tally is rebuilt
    from the whole-store readout each time — and a reply that will not parse holds the previous
    distribution rather than wiping a field the store still supports."""
    previous = PersonaCard("gen-000", {
        "party_size_distribution": {"1": 0.0, "2": 1.0, "3-5": 0.0, "6+": 0.0}})
    readout = json.dumps({"card": {}, "datapoints": {"party_size_distribution": ["1", "2", "2", "2"]}})
    rebuilt = sut.rederived_distributions(readout, previous)
    assert rebuilt.get("party_size_distribution") == {"1": 0.25, "2": 0.75, "3-5": 0.0, "6+": 0.0}
    held = sut.rederived_distributions("the model apologises", previous)
    assert held.get("party_size_distribution") == {"1": 0.0, "2": 1.0, "3-5": 0.0, "6+": 0.0}


# --- the answer key it is scored against -------------------------------------


def test_the_oracle_scores_one_through_the_whole_harness():
    """The scorer's ceiling, asserted through the loader rather than through a toy: if the map
    or the gold card were wired wrong here, this is the test that says so."""
    item = _item(checkpoints=3)
    served, _ = sut.serve(item, _records(item))
    factory = sut.factories(["oracle"], [served], model="fake")["oracle"]
    score = sut.run_one(factory(), served)
    assert score.aufc == pytest.approx(1.0)
    assert score.errors_by_kind()[scorer.FABRICATION] == 0


def test_abstaining_everywhere_scores_exactly_zero_skill():
    """spec §11's floor. `f_skill` divides out the do-nothing floor, so anything below 0 on
    that column has been beaten by silence."""
    item = _item(checkpoints=3)
    served, _ = sut.serve(item, _records(item))
    factory = sut.factories(["abstain"], [served], model="fake")["abstain"]
    score = sut.run_one(factory(), served, )
    assert score.aufc_skill == pytest.approx(0.0)


def test_a_committed_value_on_an_untouched_field_is_a_fabrication():
    """The rule the whole submission side exists to price: right for the wrong reason still
    costs, because nothing in the conversations supported it."""
    item = _item(checkpoints=2, strong=(), weak=_WEAK + ("noise_preference", "travel_radius", "planning_style"))
    served, _ = sut.serve(item, _records(item))
    truthful = json.dumps({"card": {"cuisines_liked": CARD["cuisines_liked"]}})
    score = sut.run_one(
        sut.PromptedExtractor(model="fake", invoke=FakeModel(truthful)), served)
    assert score.errors_by_kind()[scorer.FABRICATION] > 0


def test_the_rewound_card_is_checked_against_the_corpus_table():
    """One fact recorded twice is the shape ADR-0033 decision 2 refuses in the schedule, and it
    is refused here too — a drifted `card_at` would score corrected fields against a card nobody
    authored."""
    item = _item(checkpoints=3)
    item["card_at"]["1"] = {**item["card"], "spice_tolerance": "seeks_hottest"}
    subject = sut.scorer_subject(item)
    problems = sut.verify_gold(subject, item)
    assert any("spice_tolerance" in p and "c1" in p for p in problems)


def test_a_correction_moves_the_gold_the_submission_is_scored_against():
    """§5 corrections are time-indexed ground truth: a value that was right at checkpoint 1 is
    stale at checkpoint 2, and the scorer must not read the final card at both."""
    item = _item(checkpoints=3)
    item["corrections"] = [{"field_id": "spice_tolerance", "checkpoint": 2,
                            "old_value": "mild_only", "new_value": "seeks_hottest"}]
    item["card"]["spice_tolerance"] = "seeks_hottest"
    item["card_at"] = {
        "0": {**item["card"], "spice_tolerance": "mild_only"},
        "1": {**item["card"], "spice_tolerance": "mild_only"},
        "2": dict(item["card"]),
    }
    subject = sut.scorer_subject(item)
    assert sut.verify_gold(subject, item) == []
    assert subject.gold_at(1).get("spice_tolerance") == "mild_only"
    assert subject.gold_at(2).get("spice_tolerance") == "seeks_hottest"


# --- caching -----------------------------------------------------------------


def test_a_cached_reply_is_reused_and_a_changed_prompt_is_not(tmp_path):
    """`judge.GradingStore`'s rule: fingerprinted on what the model was shown, so a re-run after
    a corpus change pays only for the checkpoints whose prompt actually moved."""
    store = sut.EmissionStore(tmp_path)
    model = FakeModel()
    extractor = sut.PromptedExtractor(model="fake", invoke=model, store=store)
    session = [sut.ServedSession("gen-000", 0, 0, "2025-01-01", "[0] USER: hi")]

    extractor.reset("gen-000")
    extractor.observe(session)
    again = sut.PromptedExtractor(model="fake", invoke=model, store=store)
    again.reset("gen-000")
    again.observe(session)
    assert len(model.prompts) == 1

    moved = sut.PromptedExtractor(model="fake", invoke=model, store=store)
    moved.reset("gen-000")
    moved.observe([sut.ServedSession("gen-000", 0, 0, "2025-01-01", "[0] USER: different")])
    assert len(model.prompts) == 2


def test_replay_returns_the_cached_reply_by_position_despite_a_moved_prompt(tmp_path):
    """`--replay-cached`: for re-scoring an unchanged run against a corrected key, the reply is
    returned by (subject, checkpoint, phase) even though the prompt moved — the mechanism that lets
    a non-fingerprint-stable submission (mem0) replay for free. A missing file still returns None."""
    writer = sut.EmissionStore(tmp_path)
    model = FakeModel()
    seed = sut.PromptedExtractor(model="fake", invoke=model, store=writer)
    seed.reset("gen-000")
    seed.observe([sut.ServedSession("gen-000", 0, 0, "2025-01-01", "[0] USER: hi")])
    assert len(model.prompts) == 1

    replay = sut.EmissionStore(tmp_path, replay=True)
    guard = sut.PromptedExtractor(model="fake", invoke=model, store=replay)
    guard.reset("gen-000")
    # A different prompt would miss on fingerprint and force a call; under replay it is served
    # from the cached position instead, so no new call is made.
    guard.observe([sut.ServedSession("gen-000", 0, 0, "2025-01-01", "[0] USER: totally different")])
    assert len(model.prompts) == 1

    # No file at this position -> still None, so the caller pays (replay needs full coverage).
    assert replay.load(seed, "gen-000", 5, "any prompt") is None


def test_a_transport_failure_is_retried_and_then_reported(tmp_path):
    """A failure to *obtain* an answer is retryable; a reply that arrives and cannot be read is
    not. Same split as `judge`."""
    from persona_twin.judge import TransportError

    calls: list[int] = []

    def flaky(model, system, prompt):
        calls.append(1)
        raise TransportError("nope")

    complaints: list[str] = []
    extractor = sut.PromptedExtractor(
        model="fake", invoke=flaky, attempts=3, on_problem=complaints.append)
    extractor.reset("gen-000")
    card = extractor.observe([sut.ServedSession("gen-000", 0, 0, "2025-01-01", "[0] USER: hi")])
    assert len(calls) == 3
    assert any("no reply" in c for c in complaints)
    assert all(card.get(f) == UNKNOWN for f in fields.ALL_FIELDS_IDS)


# --- the field menu ----------------------------------------------------------


def test_the_menu_is_built_from_the_registry_not_written_out():
    """A field added to `fields.py` reaches a submission without anyone remembering a prompt."""
    menu = sut.field_menu()
    for spec in fields.ALL_FIELDS:
        assert spec.field_id in menu
    assert "seeks_hottest" in menu          # an ordinal's levels
    assert "£50+" in menu                   # a distribution's bins


def test_the_fixture_card_is_a_valid_ground_truth_card():
    """These tests would otherwise be scoring against a card the schema refuses, and the failure
    would arrive as a `metrics` ValueError three layers down rather than here."""
    PersonaCard("gen-000", _card()).validate(allow_unknown=False)


# --- choosing a model --------------------------------------------------------


def test_a_submission_carries_its_own_model_and_the_label_says_so():
    """A leaderboard row is a system *and* the model it was built on. Two rows differing only in
    model are two rows, and the label is where that is recorded rather than in the shell history
    of whoever ran it."""
    assert sut.parse_spec("prompted:claude-sonnet-5", "claude-opus-5") == (
        "prompted:claude-sonnet-5", "prompted", "claude-sonnet-5")
    assert sut.parse_spec("prompted", "claude-opus-5") == (
        "prompted:claude-opus-5", "prompted", "claude-opus-5")


def test_an_openrouter_route_survives_the_split():
    """`provider/model` ids carry a `/` and no colon, and `judge.Router` dispatches on exactly
    that — so a non-Claude twin is a spec string and not a code change."""
    assert sut.parse_spec("notebook:openai/gpt-5.6-luna", "claude-opus-5")[2] == (
        "openai/gpt-5.6-luna")


def test_naming_a_model_for_a_submission_that_makes_no_call_is_refused():
    """Putting a model on a row that never used one would make the table lie about what was
    measured."""
    with pytest.raises(ValueError, match="makes no model call"):
        sut.parse_spec("abstain:claude-opus-5", "claude-opus-5")


# --- the memory-bank SUT -----------------------------------------------------


class TwoPhaseFakeModel:
    """An invoker that answers extraction and integration differently, keyed on which system
    prompt it was called with — `MemoryExtractor` gives the two calls different systems and
    nothing else distinguishes them at the transport boundary."""

    def __init__(self, extraction_reply=None, integration_reply=None):
        self.calls: list[tuple[str, str]] = []          # (phase, prompt)
        self.extraction_reply = extraction_reply if extraction_reply is not None else json.dumps(
            {"memories": ["they said directly: I'm vegetarian"]})
        self.integration_reply = integration_reply if integration_reply is not None else json.dumps(
            {"memory_ops": [{"op": "add", "text": "they said directly: I'm vegetarian"}],
             "card": {"dietary": ["vegetarian"]}})

    def __call__(self, model, system, prompt):
        phase = "extract" if system == sut.MEMORY_EXTRACTION_SYSTEM else "integrate"
        self.calls.append((phase, prompt))
        reply = self.extraction_reply if phase == "extract" else self.integration_reply
        return reply(len(self.calls) - 1) if callable(reply) else reply


def test_extraction_and_integration_are_two_separate_calls_and_neither_sees_the_others_input():
    """Extraction reads the raw conversation and never the bank; integration reads the bank and
    the candidates and never the raw conversation. That handoff is the whole point of the SUT."""
    model = TwoPhaseFakeModel()
    extractor = sut.MemoryExtractor(model="fake", invoke=model)
    extractor.reset("gen-000")
    extractor.observe([sut.ServedSession("gen-000", 0, 0, "2025-01-01", "[0] USER: I'm vegetarian")])
    assert [phase for phase, _ in model.calls] == ["extract", "integrate"]
    extract_prompt, integrate_prompt = (p for _, p in model.calls)
    assert "USER: I'm vegetarian" in extract_prompt
    assert "USER: I'm vegetarian" not in integrate_prompt
    assert "they said directly: I'm vegetarian" in integrate_prompt


def test_a_candidate_marked_add_lands_in_the_bank():
    model = TwoPhaseFakeModel()
    extractor = sut.MemoryExtractor(model="fake", invoke=model)
    extractor.reset("gen-000")
    extractor.observe([sut.ServedSession("gen-000", 0, 0, "2025-01-01", "[0] USER: I'm vegetarian")])
    assert [item.text for item in extractor._bank] == ["they said directly: I'm vegetarian"]


def test_a_candidate_marked_duplicate_is_dropped_not_added():
    integration = json.dumps({
        "memory_ops": [{"op": "duplicate", "text": "they said directly: I'm vegetarian"}],
        "card": {},
    })
    model = TwoPhaseFakeModel(integration_reply=integration)
    extractor = sut.MemoryExtractor(model="fake", invoke=model)
    extractor.reset("gen-000")
    extractor.observe([sut.ServedSession("gen-000", 0, 0, "2025-01-01", "[0] USER: hi")])
    assert extractor._bank == []


def test_a_supersede_drops_the_named_item_and_keeps_the_new_one():
    first_integration = json.dumps({
        "memory_ops": [{"op": "add", "text": "mild_only, as far as we've seen"}],
        "card": {},
    })
    extractor = sut.MemoryExtractor(
        model="fake",
        invoke=TwoPhaseFakeModel(
            extraction_reply=json.dumps({"memories": ["mild_only, as far as we've seen"]}),
            integration_reply=first_integration))
    extractor.reset("gen-000")
    extractor.observe([sut.ServedSession("gen-000", 0, 0, "2025-01-01", "[0] USER: hi")])
    (old,) = extractor._bank

    second_integration = json.dumps({
        "memory_ops": [{"op": "supersede", "supersedes": old.id,
                        "text": "they said directly: bring on the ghost peppers"}],
        "card": {"spice_tolerance": "seeks_hottest"},
    })
    extractor.invoke = TwoPhaseFakeModel(
        extraction_reply=json.dumps({"memories": ["they said directly: bring on the ghost peppers"]}),
        integration_reply=second_integration)
    extractor.observe([sut.ServedSession("gen-000", 1, 1, "2025-01-02", "[0] USER: hotter please")])
    assert [item.text for item in extractor._bank] == [
        "they said directly: bring on the ghost peppers"]


def test_a_candidate_integration_never_mentions_is_added_anyway():
    """A malformed or incomplete `memory_ops` reply cannot silently forget evidence extraction
    already surfaced — the same problem-tolerant rule `parse_emission` applies to a lone field."""
    integration = json.dumps({"memory_ops": [], "card": {}})
    model = TwoPhaseFakeModel(integration_reply=integration)
    complaints: list[str] = []
    extractor = sut.MemoryExtractor(model="fake", invoke=model, on_problem=complaints.append)
    extractor.reset("gen-000")
    extractor.observe([sut.ServedSession("gen-000", 0, 0, "2025-01-01", "[0] USER: hi")])
    assert [item.text for item in extractor._bank] == ["they said directly: I'm vegetarian"]
    assert any("never decided" in c for c in complaints)


def test_a_failed_extraction_still_lets_integration_run_on_zero_candidates():
    model = TwoPhaseFakeModel(extraction_reply="the model apologises")
    extractor = sut.MemoryExtractor(model="fake", invoke=model)
    extractor.reset("gen-000")
    card = extractor.observe(
        [sut.ServedSession("gen-000", 0, 0, "2025-01-01", "[0] USER: I'm vegetarian")])
    assert [phase for phase, _ in model.calls] == ["extract", "integrate"]
    assert card.get("dietary") == ["vegetarian"]          # the fixture's integration reply, unconditional


def test_the_memory_state_meter_is_the_bank_not_the_card():
    model = TwoPhaseFakeModel(integration_reply=json.dumps(
        {"memory_ops": [{"op": "add", "text": "they said directly: I'm vegetarian"}],
         "card": {}}))
    extractor = sut.MemoryExtractor(model="fake", invoke=model)
    extractor.reset("gen-000")
    extractor.observe([sut.ServedSession("gen-000", 0, 0, "2025-01-01", "[0] USER: hi")])
    assert extractor.carried_state_tokens() > 0
    assert "they said directly: I'm vegetarian" in extractor._state_blob()
    assert "dietary" not in extractor._state_blob()


def test_state_does_not_survive_a_reset_for_the_memory_sut():
    extractor = sut.MemoryExtractor(model="fake", invoke=TwoPhaseFakeModel())
    extractor.reset("gen-000")
    extractor.observe([sut.ServedSession("gen-000", 0, 0, "2025-01-01", "[0] USER: hi")])
    assert extractor._bank
    extractor.reset("gen-001")
    assert extractor._bank == [] and extractor._card.values["dietary"] == UNKNOWN


def test_an_empty_batch_costs_no_calls_for_the_memory_sut():
    model = TwoPhaseFakeModel()
    extractor = sut.MemoryExtractor(model="fake", invoke=model)
    extractor.reset("gen-000")
    extractor.observe([])
    assert model.calls == []


def test_extraction_and_integration_cache_as_separate_files(tmp_path):
    store = sut.EmissionStore(tmp_path)
    model = TwoPhaseFakeModel()
    extractor = sut.MemoryExtractor(model="fake", invoke=model, store=store)
    extractor.reset("gen-000")
    extractor.observe([sut.ServedSession("gen-000", 0, 0, "2025-01-01", "[0] USER: hi")])
    names = {p.name for p in (tmp_path / "memory" / "fake").glob("*.json")}
    assert names == {"gen-000-c0-extract.json", "gen-000-c0-integrate.json"}

    again = sut.MemoryExtractor(model="fake", invoke=model, store=store)
    again.reset("gen-000")
    again.observe([sut.ServedSession("gen-000", 0, 0, "2025-01-01", "[0] USER: hi")])
    assert len(model.calls) == 2          # nothing new spent on the identical checkpoint


def test_memory_is_wired_into_factories_and_spends_a_model_per_call():
    item = _item(checkpoints=1)
    served, _ = sut.serve(item, _records(item))
    seen: list[str] = []

    def invoke(model, system, prompt):
        seen.append(model)
        if system == sut.MEMORY_EXTRACTION_SYSTEM:
            return json.dumps({"memories": []})
        return json.dumps({"memory_ops": [], "card": {}})

    factory = sut.factories(["memory:model-a"], [served], model="unused", invoke=invoke)["memory:model-a"]
    sut.run_one(factory(), served)
    assert seen == ["model-a", "model-a"]


def test_two_models_of_one_submission_run_side_by_side_and_cache_apart(tmp_path):
    """One invocation, two rows. `EmissionStore.path` is `<root>/<kind>/<model>/…`, so adding a
    second model to a run that has already paid for the first costs only the new calls."""
    item = _item(checkpoints=2)
    served, _ = sut.serve(item, _records(item))
    store = sut.EmissionStore(tmp_path)
    seen: list[str] = []

    def invoke(model, system, prompt):
        seen.append(model)
        return json.dumps({"card": {}})

    built = sut.factories(
        ["prompted:model-a", "prompted:model-b"], [served],
        model="unused", invoke=invoke, store=store)
    assert sorted(built) == ["prompted:model-a", "prompted:model-b"]
    for factory in built.values():
        sut.run_one(factory(), served)
    assert set(seen) == {"model-a", "model-b"}
    assert {p.name for p in tmp_path.glob("prompted/*")} == {"model-a", "model-b"}


def test_the_scorer_refuses_a_map_that_expects_a_twin_to_forget():
    """A WEAK island — evidenced at t, UNEVIDENCED at t+1 — is the answer key asking for
    amnesia, and `test_evidence_grades_must_be_monotone` says scoring it penalises correct
    behaviour. The schedule's own map is monotone by construction, so this only ever fires on
    a map some other module built on top of it; the 2026-08-25 n50 base reached the scorer
    with 22 of 50 subjects in that state and the oracle still scored 1.000, because the oracle
    reads the same map. Nothing downstream can catch this. The invariant can."""
    item = _item(checkpoints=3)
    item["provisional"]["2"]["cuisines_liked"] = "UNEVIDENCED"
    with pytest.raises(ValueError, match="must be monotone"):
        sut.evidence_map(item)


def test_a_monotone_map_loads():
    assert sut.evidence_map(_item(checkpoints=3)).checkpoint_count == 3


# --- prompt versions ---------------------------------------------------------


def test_v2_is_v1_with_exactly_the_evidence_block_swapped():
    """`CARD_SYSTEM` is the fingerprint every cached v1 reply was written under, so it may not
    move; v2 is that text with one paragraph replaced. Swapping the paragraph back must give v1
    byte-for-byte — anything else and the two rows differ by more than the disclosed rule."""
    assert sut.CARD_SYSTEM_V2 != sut.CARD_SYSTEM
    assert sut.CARD_SYSTEM_V2.replace(sut._EVIDENCE_V2, sut._EVIDENCE_V1) == sut.CARD_SYSTEM
    assert "booking a venue is not liking" in sut.CARD_SYSTEM_V2
    assert "booking a venue is not liking" not in sut.CARD_SYSTEM
    # v2 states the GT-POLICIES conventions and none of the traps.
    for convention in ("scenery", "formality_ceiling", "sushi is not japanese", "COLLEAGUE"):
        assert convention in sut.CARD_SYSTEM_V2
    assert sut.CARD_SYSTEMS == {"v1": sut.CARD_SYSTEM, "v2": sut.CARD_SYSTEM_V2}


def test_prompt_version_reaches_the_model_and_keeps_its_own_cache(tmp_path):
    """The version rides in the name, so the cache path splits (`prompted/` vs `prompted-v2/`)
    and neither version can serve or overwrite the other's replies — and the fingerprint is on
    the versioned system prompt, so a v1 record could not satisfy a v2 load even at one path."""
    store = sut.EmissionStore(tmp_path)
    model = FakeModel()
    session = [sut.ServedSession("gen-000", 0, 0, "2025-01-01", "[0] USER: hi")]

    v1 = sut.PromptedExtractor(model="fake", invoke=model, store=store)
    v1.reset("gen-000")
    v1.observe(session)
    assert model.systems == [sut.CARD_SYSTEM]
    assert v1.system == sut.CARD_SYSTEM and v1.name == "prompted"

    v2 = sut.PromptedExtractor(
        model="fake", invoke=model, store=store, name="prompted-v2", prompt_version="v2")
    v2.reset("gen-000")
    v2.observe(session)
    assert model.systems == [sut.CARD_SYSTEM, sut.CARD_SYSTEM_V2]
    assert (tmp_path / "prompted" / "fake" / "gen-000-c0.json").exists()
    assert (tmp_path / "prompted-v2" / "fake" / "gen-000-c0.json").exists()

    # Each version is served from its own cache on a second pass: no new calls.
    for again in (
        sut.PromptedExtractor(model="fake", invoke=model, store=store),
        sut.PromptedExtractor(
            model="fake", invoke=model, store=store, name="prompted-v2", prompt_version="v2"),
    ):
        again.reset("gen-000")
        again.observe(session)
    assert len(model.prompts) == 2

    with pytest.raises(ValueError, match="unknown prompt version"):
        sut.PromptedExtractor(model="fake", invoke=model, prompt_version="v9")


def test_factories_build_the_versioned_kinds():
    """`--sut prompted --sut prompted-v2` on one table: both kinds are paid, the suffix selects
    the prompt, and the label carries the model like every other paid row."""
    assert sut.prompt_kind("prompted") == ("prompted", "v1")
    assert sut.prompt_kind("prompted-v2") == ("prompted", "v2")
    assert sut.prompt_kind("notebook-v2") == ("notebook", "v2")
    built = sut.factories(
        ["prompted", "prompted-v2", "notebook-v2"], [], model="fake", invoke=FakeModel())
    assert set(built) == {"prompted:fake", "prompted-v2:fake", "notebook-v2:fake"}
    plain = built["prompted:fake"]()
    assert (plain.name, plain.prompt_version, plain.notebook) == ("prompted", "v1", False)
    v2 = built["prompted-v2:fake"]()
    assert (v2.name, v2.prompt_version, v2.notebook) == ("prompted-v2", "v2", False)
    nb2 = built["notebook-v2:fake"]()
    assert (nb2.name, nb2.prompt_version, nb2.notebook) == ("notebook-v2", "v2", True)
    assert nb2.system == sut.CARD_SYSTEM_V2
    assert sut.CALLS_PER_CHECKPOINT["prompted-v2"] == 1
    mem = sut.factories(["memory-v2", "mem0-v2"], [], model="fake", invoke=FakeModel())
    m2 = mem["memory-v2:fake"]()
    assert (m2.name, m2.prompt_version) == ("memory-v2", "v2")
    assert (m2.extraction_system, m2.integration_system) == (
        sut.MEMORY_EXTRACTION_SYSTEM_V2, sut.MEMORY_INTEGRATION_SYSTEM_V2)
    z2 = mem["mem0-v2:fake"]()
    assert (z2.name, z2.prompt_version, z2.emission_system) == (
        "mem0-v2", "v2", sut.BANK_EMISSION_SYSTEM_V2)
    assert sut.CALLS_PER_CHECKPOINT["memory-v2"] == sut.CALLS_PER_CHECKPOINT["mem0-v2"] == 2


def test_memory_side_v2_prompts_are_v1_plus_the_conventions_and_nothing_else():
    """Same guarantee as the card prompt: every v1 constant is what the cached replies were
    fingerprinted on and does not move; v2 is v1 with one block swapped (extraction, which reads
    the text) or one block inserted (the two read-outs, which only ever see memories)."""
    assert sut.MEMORY_EXTRACTION_SYSTEM_V2.replace(
        sut._MEMORY_EVIDENCE_V2, sut._MEMORY_EVIDENCE_V1) == sut.MEMORY_EXTRACTION_SYSTEM
    assert sut.MEMORY_INTEGRATION_SYSTEM_V2.replace(
        sut._READOUT_EVIDENCE_V2, "") == sut.MEMORY_INTEGRATION_SYSTEM
    assert sut.BANK_EMISSION_SYSTEM_V2.replace(
        sut._READOUT_EVIDENCE_V2, "") == sut.BANK_EMISSION_SYSTEM
    for v1, v2 in (
        (sut.MEMORY_EXTRACTION_SYSTEM, sut.MEMORY_EXTRACTION_SYSTEM_V2),
        (sut.MEMORY_INTEGRATION_SYSTEM, sut.MEMORY_INTEGRATION_SYSTEM_V2),
        (sut.BANK_EMISSION_SYSTEM, sut.BANK_EMISSION_SYSTEM_V2),
    ):
        assert "booking a venue is not liking" in v2 and "formality_ceiling" in v2
        assert "booking a venue is not liking" not in v1
    assert sut.MEMORY_SYSTEMS["v1"] == (sut.MEMORY_EXTRACTION_SYSTEM, sut.MEMORY_INTEGRATION_SYSTEM)
    assert sut.BANK_SYSTEMS["v1"] == sut.BANK_EMISSION_SYSTEM


def test_memory_prompt_version_reaches_both_phases_and_keeps_its_own_cache(tmp_path):
    class Recorder:
        def __init__(self):
            self.systems: list[str] = []

        def __call__(self, model, system, prompt):
            self.systems.append(system)
            if system in (sut.MEMORY_EXTRACTION_SYSTEM, sut.MEMORY_EXTRACTION_SYSTEM_V2):
                return json.dumps({"memories": ["they said directly: I'm vegetarian"]})
            return json.dumps({"memory_ops": [], "card": {"dietary": ["vegetarian"]}})

    store = sut.EmissionStore(tmp_path)
    model = Recorder()
    session = [sut.ServedSession("gen-000", 0, 0, "2025-01-01", "[0] USER: I'm vegetarian")]
    v2 = sut.MemoryExtractor(
        model="fake", invoke=model, store=store, name="memory-v2", prompt_version="v2")
    v2.reset("gen-000")
    card = v2.observe(session)
    assert card.get("dietary") == ["vegetarian"]
    assert model.systems == [sut.MEMORY_EXTRACTION_SYSTEM_V2, sut.MEMORY_INTEGRATION_SYSTEM_V2]
    assert (tmp_path / "memory-v2" / "fake" / "gen-000-c0-extract.json").exists()
    assert (tmp_path / "memory-v2" / "fake" / "gen-000-c0-integrate.json").exists()
    assert not (tmp_path / "memory").exists()

    v1 = sut.MemoryExtractor(model="fake", invoke=model, store=store)
    v1.reset("gen-000")
    v1.observe(session)
    assert model.systems[2:] == [sut.MEMORY_EXTRACTION_SYSTEM, sut.MEMORY_INTEGRATION_SYSTEM]
    assert (tmp_path / "memory" / "fake" / "gen-000-c0-extract.json").exists()

    with pytest.raises(ValueError, match="unknown prompt version"):
        sut.MemoryExtractor(model="fake", invoke=model, prompt_version="v9")
