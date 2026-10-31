"""`author.py` — the §6 authoring runner, exercised without a model call.

The rules under test: v0.4's independent sessions (no session is briefed on another's text;
a failure does not stop its successors), the two different retry rules, the marked-not-dropped
exhaustion, and the fingerprinted cache. Plus the one that ties the two runners together — a
record this writes must load as a `judge.Bundle` with no adapter.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from persona_twin import author, dataset, judge, transcript


# --- fakes -------------------------------------------------------------------


class Recorder:
    """An invoker that records its prompts and replies from a scripted queue."""

    def __init__(self, replies: list[str] | None = None, default: str | None = None) -> None:
        self.replies = list(replies or [])
        self.default = default if default is not None else _reply("A clean session.")
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, model: str, system: str, prompt: str) -> str:
        self.calls.append((model, system, prompt))
        return self.replies.pop(0) if self.replies else self.default


def _reply(text: str, self_report: str | None = None) -> str:
    body: dict[str, object] = {"transcript": f"[0] USER: {text}\n[1] BOT: Sure."}
    if self_report:
        body["self_report"] = self_report
    return json.dumps(body)


def _task(session: int = 0, **kwargs) -> author.Task:
    return author.Task(
        task_id=kwargs.get("task_id", f"gen-000-s{session}"),
        subject_id="gen-000", session=session,
        checkpoint=session, is_distractor=False, turns=12, date="2025-03-04",
        beats=({"field_id": "adventurousness", "move": "request",
                "supports_gold": True, "note": "", "gold_value": "adventurous"},),
    )


def _chain(sessions: int = 3) -> author.Chain:
    return author.Chain("gen-000", tuple(_task(i) for i in range(sessions)))


# --- parsing -----------------------------------------------------------------


def test_parse_reads_transcript_and_declared_self_report() -> None:
    text, self_report, free_offers = author.parse_reply(
        _reply("Hello", self_report="I shop around.")
    )
    assert "Hello" in text
    assert self_report == "I shop around."
    assert free_offers == ()


def test_parse_takes_the_last_balanced_object() -> None:
    raw = 'Here you go:\n{"note": "ignore"}\n{"transcript": "[0] USER: hi"}'
    text, _, _ = author.parse_reply(raw)
    assert text == "[0] USER: hi"


def test_declared_offer_escapes_are_read_as_message_numbers() -> None:
    raw = '{"transcript": "[0] USER: hi", "free_offers": [5, "3", 5]}'
    _text, _self_report, free_offers = author.parse_reply(raw)
    assert free_offers == (3, 5)


def test_an_unreadable_escape_entry_is_dropped_not_raised_on() -> None:
    """A stray list element leaves the turn undeclared, which is a finding rather than a
    thrown-away transcript. `_offer_problems` is what reports it."""
    _text, _self_report, free_offers = author.parse_reply(
        '{"transcript": "[0] USER: hi", "free_offers": ["message 5", null, 7]}'
    )
    assert free_offers == (7,)


@pytest.mark.parametrize("raw", ["", "no json here", '{"transcript": ""}', "[1, 2]"])
def test_parse_raises_on_anything_unusable(raw: str) -> None:
    with pytest.raises(Exception):
        author.parse_reply(raw)


# --- v0.4: independent sessions -----------------------------------------------


def test_sessions_are_authored_independently_with_no_prior_text_in_the_prompt(tmp_path) -> None:
    invoke = Recorder()
    store = author.TranscriptStore(tmp_path)
    author.author_chain("claude-opus-5", _chain(3), store, invoke)

    assert len(invoke.calls) == 3
    assert store.load("claude-opus-5", "gen-000-s0")["transcript"]
    second_prompt = invoke.calls[1][2]
    third_prompt = invoke.calls[2][2]
    # No other session's text ever appears in the brief.
    assert "PREVIOUS CONVERSATIONS" not in second_prompt
    assert "--- conversation " not in second_prompt
    assert "--- conversation " not in third_prompt
    # The immediately-previous session's *date* still frames the gap — schedule data, known
    # up front, not authored text.
    assert "last conversation with this app was on 2025-03-04" in second_prompt


def test_a_failed_session_does_not_stop_the_rest_of_the_chain(tmp_path) -> None:
    """v0.4: a session's brief no longer embeds another session's text, so exhausting one
    session's content budget carries no consequence for its successors."""
    invoke = Recorder(replies=["not json", "not json", "not json"])
    report = author.RunReport()
    store = author.TranscriptStore(tmp_path)
    author.author_chain("claude-opus-5", _chain(3), store, invoke, report=report)

    # 3 exhausted attempts against session 0, then one clean call each for sessions 1 and 2.
    assert len(invoke.calls) == 5
    first = store.load("claude-opus-5", "gen-000-s0")
    assert first["transcript"] == "" and first["error"]
    for session in (1, 2):
        record = store.load("claude-opus-5", f"gen-000-s{session}")
        assert record["transcript"], f"session {session} should still have been authored"
        assert record.get("stopped") is not True


def test_run_is_parallel_across_subjects_and_authors(tmp_path) -> None:
    chains = [
        author.Chain("gen-000", (_task(0),)),
        author.Chain("gen-001", (_task(0, task_id="gen-001-s0"),)),
    ]
    invoke = Recorder()
    report = author.run(chains, author.TranscriptStore(tmp_path), invoke, workers=4)
    assert report.written == 4                      # 2 subjects x 2 author models
    assert set(report.per_author) == set(author.DEFAULT_AUTHORS)


def test_run_fans_out_per_session_not_per_chain(tmp_path) -> None:
    """The unit of parallelism is the session, and on a lopsided corpus that is the whole
    wall clock.

    Two subjects, one of them eight times longer than the other. Per chain, at most two calls
    can be in flight and the run cannot finish before the long subject's eight serial calls
    do. Per session, all nine saturate the pool. This measures the concurrency actually
    reached rather than the elapsed time, which is a fake's timing and proves nothing.
    """
    import threading

    chains = [
        author.Chain("gen-000", tuple(_task(i) for i in range(8))),
        author.Chain("gen-001", (_task(0, task_id="gen-001-s0"),)),
    ]

    class Concurrent(Recorder):
        """Blocks every call until `expected` of them have arrived, then releases all."""

        def __init__(self, expected: int) -> None:
            super().__init__()
            self.gate = threading.Barrier(expected, timeout=10)

        def __call__(self, model, system, prompt):
            self.gate.wait()
            return super().__call__(model, system, prompt)

    # Nine calls at nine workers: if any two sessions of gen-000 were forced to queue behind
    # each other, the barrier never fills and this raises BrokenBarrierError.
    invoke = Concurrent(expected=9)
    report = author.run(
        chains, author.TranscriptStore(tmp_path), invoke,
        authors=["claude-opus-5"], workers=9,
    )
    assert report.written == 9


def test_the_session_unit_authors_exactly_what_the_chain_unit_did(tmp_path) -> None:
    """Scheduling changed; nothing a call is made from did. `author_chain` is the old unit,
    still used directly elsewhere, so it is the reference the new fan-out is held against —
    same task ids, same briefs, same store contents."""
    chains = [author.Chain("gen-000", tuple(_task(i) for i in range(5)))]

    by_chain = Recorder()
    chain_store = author.TranscriptStore(tmp_path / "by-chain")
    author.author_chain("claude-opus-5", chains[0], chain_store, by_chain)

    by_session = Recorder()
    session_store = author.TranscriptStore(tmp_path / "by-session")
    author.run(
        chains, session_store, by_session, authors=["claude-opus-5"], workers=4,
    )

    # Same prompts, up to the order they were issued in — which is the only thing that moved.
    assert sorted(p for _, _, p in by_chain.calls) == sorted(
        p for _, _, p in by_session.calls
    )

    def _contents(root: Path) -> dict[str, str]:
        return {
            path.name: path.read_text()
            for path in (root / "claude-opus-5").glob("gen-*.json")
        }

    assert _contents(tmp_path / "by-chain") == _contents(tmp_path / "by-session")


def test_a_re_run_over_an_authored_store_still_cache_hits(tmp_path) -> None:
    """The cache key is per session and untouched by the fan-out change. A second run must
    make no calls at all — this is the property extension depends on."""
    chains = [author.Chain("gen-000", tuple(_task(i) for i in range(4)))]
    store = author.TranscriptStore(tmp_path)

    first = Recorder()
    author.run(chains, store, first, authors=["claude-opus-5"], workers=4)

    second = Recorder()
    report = author.run(chains, store, second, authors=["claude-opus-5"], workers=4)
    assert second.calls == []
    assert report.cached == 4 and report.written == 0


# --- the two retry rules -----------------------------------------------------


def test_transport_is_retried_and_content_is_not_rerolled_blind(tmp_path) -> None:
    class Flaky(Recorder):
        def __call__(self, model, system, prompt):
            if len(self.calls) < 2:
                self.calls.append((model, system, prompt))
                raise author.TransportError("exit 1")
            return super().__call__(model, system, prompt)

    invoke = Flaky()
    report = author.RunReport()
    text, message = author.author_session(
        "claude-opus-5", _task(), author.TranscriptStore(tmp_path), invoke,
        report=report, sleep=lambda _s: None,
    )
    assert text and "chars" in message
    assert report.per_author["claude-opus-5"]["transport_retry"] == 2
    assert report.per_author["claude-opus-5"]["content_retry"] == 0


def test_a_flagged_session_is_reauthored_with_its_violations_named(tmp_path) -> None:
    bad = json.dumps({"transcript": "[0] USER: I always order the spiciest thing on the menu."})
    assert transcript.check_session(json.loads(bad)["transcript"]), (
        "the fixture no longer trips a §6.1 rule; this test would pass vacuously"
    )
    invoke = Recorder(replies=[bad, _reply("A clean session.")])
    report = author.RunReport()
    text, message = author.author_session(
        "claude-opus-5", _task(), author.TranscriptStore(tmp_path), invoke,
        report=report,
    )
    assert len(invoke.calls) == 2
    assert "broke these rules" in invoke.calls[1][2]
    assert "EXHAUSTED" not in message
    assert text
    assert report.per_author["claude-opus-5"]["content_retry"] == 1
    assert report.problem_kinds["claude-opus-5"]


def test_exhausting_the_budget_keeps_and_marks(tmp_path) -> None:
    """`probe_coherence/`'s rule: a retry loop against a known defect regenerates the
    construction the quota exists to carry. The session ships marked."""
    bad = json.dumps({"transcript": "[0] USER: I always order the spiciest thing on the menu."})
    invoke = Recorder(replies=[bad, bad, bad])
    report = author.RunReport()
    store = author.TranscriptStore(tmp_path)
    text, message = author.author_session(
        "claude-opus-5", _task(), store, invoke, report=report,
    )
    assert len(invoke.calls) == 3                      # budget 2 + the first attempt
    assert message.startswith("EXHAUSTED")
    assert text, "a rule-breaking session is kept, not dropped"
    record = store.load("claude-opus-5", "gen-000-s0")
    assert record["exhausted"] is True
    assert record["problems"] and record["transcript"]
    assert len(record["attempts"]) == 3


def test_an_unreadable_reply_leaves_no_text(tmp_path) -> None:
    invoke = Recorder(replies=["not json"] * 3)
    store = author.TranscriptStore(tmp_path)
    text, message = author.author_session(
        "claude-opus-5", _task(), store, invoke,
    )
    assert text == ""
    assert message.startswith("EXHAUSTED")
    record = store.load("claude-opus-5", "gen-000-s0")
    assert record["transcript"] == "" and record["error"]


def test_transport_failure_writes_no_record(tmp_path) -> None:
    """`judge.py`'s rule, and for the same reason: an empty record is a timeline claiming
    nothing happened, which is a stronger statement than 'no answer was obtained'."""
    class Dead(Recorder):
        def __call__(self, model, system, prompt):
            self.calls.append((model, system, prompt))
            raise author.TransportError("exit 1")

    store = author.TranscriptStore(tmp_path)
    report = author.RunReport()
    text, message = author.author_session(
        "claude-opus-5", _task(), store, Dead(), report=report,
        sleep=lambda _s: None,
    )
    assert text == "" and message.startswith("FAILED")
    assert store.load("claude-opus-5", "gen-000-s0") is None
    assert report.failed


def test_the_declared_self_report_is_exempt_and_is_not_retried(tmp_path) -> None:
    """§4.2's scripted claim is the hard case, not a defect. If the runner re-authored it,
    the retry loop would regenerate the quota member until it stopped being one."""
    claim = "I usually shop around and compare prices."
    reply = json.dumps({
        "transcript": f"[0] USER: {claim}\n[1] BOT: Noted.",
        "self_report": claim,
    })
    invoke = Recorder(replies=[reply])
    text, message = author.author_session(
        "claude-opus-5", _task(), author.TranscriptStore(tmp_path), invoke,
    )
    assert len(invoke.calls) == 1, "the scripted self-report was retried"
    assert claim in text and "EXHAUSTED" not in message


# --- the cache ---------------------------------------------------------------


def test_a_cached_session_is_not_reauthored(tmp_path) -> None:
    store = author.TranscriptStore(tmp_path)
    invoke = Recorder()
    author.author_session("claude-opus-5", _task(), store, invoke)
    report = author.RunReport()
    author.author_session("claude-opus-5", _task(), store, invoke, report=report)
    assert len(invoke.calls) == 1
    assert report.cached == 1


def test_a_changed_brief_is_a_cache_miss(tmp_path) -> None:
    """The fingerprint covers the whole assembled brief, so anything that changes it —
    `prior_date` included — is a cache miss, by construction rather than by a rule someone
    has to remember."""
    store = author.TranscriptStore(tmp_path)
    invoke = Recorder()
    author.author_session("claude-opus-5", _task(1), store, invoke, prior_date="2025-02-01")
    author.author_session("claude-opus-5", _task(1), store, invoke, prior_date="2025-02-20")
    assert len(invoke.calls) == 2


def test_a_record_without_a_fingerprint_is_stale(tmp_path) -> None:
    store = author.TranscriptStore(tmp_path)
    path = store.path("claude-opus-5", "gen-000-s0")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "task_id": "gen-000-s0", "subject_id": "gen-000", "author": "claude-opus-5",
        "session": 0, "checkpoint": 0, "transcript": "from a probe run",
    }))
    invoke = Recorder()
    author.author_session("claude-opus-5", _task(), store, invoke)
    assert len(invoke.calls) == 1, "a fingerprint-less probe record was reused"


def test_records_excludes_sidecars_and_the_manifest(tmp_path) -> None:
    """`find-double-counts-on-this-volume`: AppleDouble sidecars match *.json and once made
    a 72-call run read as 144."""
    store = author.TranscriptStore(tmp_path)
    author.author_session("claude-opus-5", _task(), store, Recorder())
    (tmp_path / "manifest.json").write_text("{}")
    (tmp_path / "claude-opus-5" / "._gen-000-s0.json").write_bytes(b"\x00garbage")
    assert len(store.records()) == 1


# --- the seam with judge.py --------------------------------------------------


def test_authored_records_load_as_bundles_with_no_adapter(tmp_path) -> None:
    store = author.TranscriptStore(tmp_path)
    author.author_chain("claude-opus-5", _chain(3), store, Recorder())
    bundles = judge.Bundle.from_records(store.records())
    assert len(bundles) == 1
    bundle = bundles[0]
    assert bundle.author == "claude-opus-5"
    assert bundle.checkpoints == (0, 1, 2)
    assert bundle.prefix(1).count("--- session ") == 2


def test_a_hole_in_the_chain_makes_the_grader_skip_rather_than_shorten(tmp_path) -> None:
    store = author.TranscriptStore(tmp_path)
    author.author_chain("claude-opus-5", _chain(3), store, Recorder())
    path = store.path("claude-opus-5", "gen-000-s1")
    record = json.loads(path.read_text())
    record["transcript"] = ""
    path.write_text(json.dumps(record))

    bundle = judge.Bundle.from_records(store.records())[0]
    assert bundle.prefix(0) is not None
    assert bundle.prefix(1) is None and bundle.prefix(2) is None


# --- the corpus seam ---------------------------------------------------------


def test_chains_load_from_the_dataset_corpus(tmp_path) -> None:
    data = dataset.build(
        n=6, seed=dataset.DEFAULT_SEED,
    )
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(data.to_json(), default=str))
    chains = author.load_corpus(path)
    assert len(chains) == len(data.items)
    assert len({len(c.tasks) for c in chains}) >= 1
    for chain, item in zip(chains, data.items):
        assert [t.task_id for t in chain.tasks] == [t.task_id for t in item.sessions]


def test_a_gapped_corpus_item_raises(tmp_path) -> None:
    with pytest.raises(ValueError, match="dense from 0"):
        author.Chain.from_item({
            "subject_id": "gen-000",
            "sessions": [
                {"task_id": "gen-000-s0", "subject_id": "gen-000",
                 "session": 0, "checkpoint": 0, "is_distractor": False, "beats": []},
                {"task_id": "gen-000-s2", "subject_id": "gen-000",
                 "session": 2, "checkpoint": 2, "is_distractor": False, "beats": []},
            ],
        })


def test_plan_is_stable_and_covers_every_call() -> None:
    chains = [_chain(3), author.Chain("gen-001", (_task(0, task_id="gen-001-s0"),))]
    calls = author.plan(chains)
    assert len(calls) == (3 + 1) * len(author.DEFAULT_AUTHORS)
    assert calls == author.plan(chains)


# --- the hole the grader cannot see ------------------------------------------


def test_sessions_whose_transport_fails_outright_get_visible_placeholders(tmp_path) -> None:
    """A session with *no record* is not a hole — the bundle just has fewer sessions, and a
    rich checkpoint spanning two of them would be graded on one. The placeholder is what
    turns that into a skip. Each of sessions 1 and 2 fails independently here (v0.4: neither
    depends on the other, or on session 0), not as a single cascading stop."""
    class Dead(Recorder):
        def __call__(self, model, system, prompt):
            self.calls.append((model, system, prompt))
            if len(self.calls) > 1:
                raise author.TransportError("exit 1")
            return super().__call__(model, system, prompt)

    store = author.TranscriptStore(tmp_path)
    author.author_chain(
        "claude-opus-5", _chain(3), store, Dead(), attempts=1,
    )
    for session in (1, 2):
        record = store.load("claude-opus-5", f"gen-000-s{session}")
        assert record is not None, f"session {session} left no record"
        assert record["transcript"] == "" and record["stopped"] is True

    bundle = judge.Bundle.from_records(store.records())[0]
    assert bundle.prefix(0) is not None
    assert bundle.prefix(1) is None and bundle.prefix(2) is None


def test_a_placeholder_is_never_a_cache_hit(tmp_path) -> None:
    """Otherwise a re-run would treat the hole as settled and never re-author it."""
    store = author.TranscriptStore(tmp_path)
    chain = _chain(3)
    author.mark_stopped("claude-opus-5", chain.tasks[0], store)
    invoke = Recorder()
    author.author_session("claude-opus-5", chain.tasks[1], store, invoke)
    assert len(invoke.calls) == 1


def test_verify_names_missing_and_empty_sessions(tmp_path) -> None:
    store = author.TranscriptStore(tmp_path)
    chain = _chain(3)
    assert author.verify([chain], store, authors=["claude-opus-5"]), "empty store passed"

    author.author_chain("claude-opus-5", chain, store, Recorder())
    assert author.verify([chain], store, authors=["claude-opus-5"]) == []

    path = store.path("claude-opus-5", "gen-000-s2")
    record = json.loads(path.read_text())
    record["transcript"] = ""
    path.write_text(json.dumps(record))
    problems = author.verify([chain], store, authors=["claude-opus-5"])
    assert len(problems) == 1 and "empty session" in problems[0]

    path.unlink()
    problems = author.verify([chain], store, authors=["claude-opus-5"])
    assert len(problems) == 1 and "no record at all" in problems[0]


# --- v0.5: the card reaches the brief ----------------------------------------

def _item(**extra) -> dict:
    return {
        "subject_id": "gen-000",
        "sessions": [
            {"task_id": f"gen-000-s{i}", "subject_id": "gen-000", "session": i,
             "checkpoint": i, "is_distractor": False, "beats": []}
            for i in range(3)
        ],
        **extra,
    }


def test_the_card_is_attached_from_the_item_to_every_session() -> None:
    """It is a property of the subject and the corpus stores it once, at the item level.
    Attaching it in `Chain.from_item` rather than in `Task.from_json` keeps the session record
    the schedule writes unchanged — `from_json` only ever sees one session."""
    chain = author.Chain.from_item(
        _item(card={"noise_preference": "needs_quiet"}, idiosyncrasies="always early")
    )
    assert len(chain.tasks) == 3
    for task in chain.tasks:
        assert task.card == {"noise_preference": "needs_quiet"}
        assert task.idiosyncrasies == "always early"


def test_a_corpus_without_a_card_still_loads_and_still_briefs_the_v04_way() -> None:
    """The brief is fingerprinted, so this is a cache guarantee rather than a courtesy: a
    portrait appearing unbidden would invalidate every transcript in every pre-v0.5 store."""
    task = author.Chain.from_item(_item()).tasks[0]
    assert task.card == {} and task.idiosyncrasies == ""
    assert "WHO THIS PERSON IS" not in author.brief_for(task)


def test_the_brief_carries_the_portrait_when_the_corpus_carries_a_card() -> None:
    task = author.Chain.from_item(
        _item(card={"noise_preference": "needs_quiet"}, idiosyncrasies="always early")
    ).tasks[0]
    brief = author.brief_for(task)
    assert "=== WHO THIS PERSON IS ===" in brief
    assert "needs it quiet enough to talk" in brief
    assert "always early" in brief


def test_showing_the_card_changes_the_fingerprint_and_so_misses_a_v04_cache() -> None:
    """The whole reason the fingerprint covers the prompt rather than the beats. A session
    authored blind and one authored under a portrait are different artifacts, and a store that
    served the first for the second would report a run that never happened."""
    blind = author.Chain.from_item(_item()).tasks[0]
    seeing = author.Chain.from_item(
        _item(card={"noise_preference": "needs_quiet"})
    ).tasks[0]
    assert judge.fingerprint(
        transcript.AUTHOR_SYSTEM, author.brief_for(blind)
    ) != judge.fingerprint(transcript.AUTHOR_SYSTEM, author.brief_for(seeing))
