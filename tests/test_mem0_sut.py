"""The fourth submission: mem0 as the memory layer, this tree's read-out on top (ADR-0062).

Every test here is free, which was the property a third-party dependency most easily costs.
mem0's whole LLM contract is one method returning a string, so `InvokerLLM` routes it through
`judge.Invoker` and the same fake that drives the other three drives this one — including mem0's
own extraction call, which is the half that would otherwise have needed an API key.

What is *not* faked is mem0: a real `Memory` on a real embedded Qdrant runs in every test below,
because the properties worth asserting here — that the bank is append-only, that nothing survives
`reset`, that raw transcript is kept and re-read — are mem0's behaviour and not ours.
"""

from __future__ import annotations

import json
from importlib.util import find_spec

import pytest

from persona_twin import sut

if find_spec("mem0") is None:                                            # the extra is optional
    pytest.skip("the mem0 submission needs mem0ai", allow_module_level=True)

# `find_spec` rather than an import, and `mem0_sut` before anything reaches `mem0`: mem0 reads
# MEM0_TELEMETRY once at its own import, and importing it here first would leave telemetry live
# for the whole session — which also makes every `Memory` fight for one lock (ADR-0062).
from persona_twin import mem0_sut                                        # noqa: E402


TRANSCRIPT = (
    "[0] USER: I'm vegetarian, so nowhere steak-forward please.\n"
    "[1] BOT: Understood. Anything else?\n"
    "[2] USER: Somewhere quiet. I can't hear myself think in the loud ones."
)


def _session(checkpoint: int = 0, transcript: str = TRANSCRIPT) -> sut.ServedSession:
    return sut.ServedSession("gen-000", checkpoint, checkpoint, "2025-01-01", transcript)


class FakeModel:
    """Answers mem0's extraction call and our emission call differently, keyed on the system
    prompt — the same trick `TwoPhaseFakeModel` plays, and for the same reason: nothing else
    distinguishes the two at the transport boundary."""

    def __init__(self, memories=None, card=None):
        self.calls: list[tuple[str, str]] = []              # (phase, prompt)
        self._memories = memories if memories is not None else ["User is vegetarian."]
        self._card = card if card is not None else {"dietary": ["vegetarian"]}

    def __call__(self, model, system, prompt):
        phase = "emit" if system == sut.BANK_EMISSION_SYSTEM else "extract"
        self.calls.append((phase, prompt))
        if phase == "emit":
            card = self._card(len(self.calls)) if callable(self._card) else self._card
            return json.dumps({"card": card})
        texts = self._memories(len(self.calls)) if callable(self._memories) else self._memories
        return json.dumps({
            "memory": [{"id": str(i), "text": t, "attributed_to": "user"}
                       for i, t in enumerate(texts)]})

    def phases(self) -> list[str]:
        return [phase for phase, _ in self.calls]

    def prompt(self, phase: str) -> str:
        return next(p for ph, p in self.calls if ph == phase)


@pytest.fixture
def extractor():
    made: list[mem0_sut.Mem0Extractor] = []

    def build(**kwargs):
        one = mem0_sut.Mem0Extractor(model="fake", **kwargs)
        made.append(one)
        return one

    yield build
    for one in made:
        one._close()


# --- the transcript, in the shape mem0 reads ---------------------------------


def test_a_transcript_becomes_role_tagged_turns_and_the_header_rides_on_the_first():
    """mem0 is built to read a conversation, not a wall of text. Handing it one blob would be
    measuring mem0 on an input shape it was not written for."""
    messages = mem0_sut.to_messages([_session()])
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"].startswith("[session 1, 2025-01-01] ")
    assert messages[0]["content"].endswith("nowhere steak-forward please.")
    assert "[session" not in messages[1]["content"]
    assert messages[1]["content"] == "Understood. Anything else?"


def test_a_wrapped_line_joins_the_turn_above_it_rather_than_becoming_a_user_turn():
    wrapped = "[0] USER: I'm vegetarian,\nand nowhere loud.\n[1] BOT: Understood."
    messages = mem0_sut.to_messages([_session(transcript=wrapped)])
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"].endswith("I'm vegetarian,\nand nowhere loud.")


def test_each_session_in_a_batch_keeps_its_own_header():
    messages = mem0_sut.to_messages([_session(0), _session(1)])
    headers = [m["content"] for m in messages if m["content"].startswith("[session")]
    assert len(headers) == 2


# --- the two calls a checkpoint ----------------------------------------------


def test_the_v2_read_out_is_the_only_v2_lever_and_it_reaches_the_emission_call(extractor):
    """mem0 writes its own bank with its own prompts (ADR-0062), so `mem0-v2` can only differ
    from `mem0` at the read-out — and there it must, on the v2 system, under its own name."""
    class Fake(FakeModel):
        def __call__(self, model, system, prompt):
            self.systems = getattr(self, "systems", []) + [system]
            phase = "emit" if system in sut.BANK_SYSTEMS.values() else "extract"
            self.calls.append((phase, prompt))
            if phase == "emit":
                return json.dumps({"card": {"dietary": ["vegetarian"]}})
            return json.dumps({"memory": [
                {"id": "0", "text": "User is vegetarian.", "attributed_to": "user"}]})

    model = Fake()
    one = extractor(invoke=model, name="mem0-v2", prompt_version="v2")
    one.reset("gen-000")
    card = one.observe([_session()])
    assert card.get("dietary") == ["vegetarian"]
    assert model.phases() == ["extract", "emit"]
    assert model.systems[-1] == sut.BANK_EMISSION_SYSTEM_V2
    assert sut.BANK_EMISSION_SYSTEM not in model.systems
    with pytest.raises(ValueError, match="unknown prompt version"):
        extractor(invoke=model, prompt_version="v9")


def test_a_checkpoint_is_one_mem0_extraction_and_one_emission(extractor):
    model = FakeModel()
    one = extractor(invoke=model)
    one.reset("gen-000")
    one.observe([_session()])
    assert model.phases() == ["extract", "emit"]
    assert sut.CALLS_PER_CHECKPOINT["mem0"] == len(model.calls)


def test_the_emission_call_sees_the_bank_and_never_the_transcript(extractor):
    """The whole shape of the submission: mem0's memories are the only channel from the
    conversation to the card, exactly as `MemoryExtractor`'s integration call has it."""
    model = FakeModel(memories=["User is vegetarian and asked for somewhere quiet."])
    one = extractor(invoke=model)
    one.reset("gen-000")
    one.observe([_session()])

    extract_prompt, emit_prompt = model.prompt("extract"), model.prompt("emit")
    assert "steak-forward" in extract_prompt
    assert "steak-forward" not in emit_prompt
    assert "User is vegetarian and asked for somewhere quiet." in emit_prompt


def test_the_emitted_card_is_validated_and_reaches_the_scorer(extractor):
    one = extractor(invoke=FakeModel(card={"dietary": ["vegetarian"], "noise_preference": "nonsense"}))
    one.reset("gen-000")
    card = one.observe([_session()])
    assert card.values["dietary"] == ["vegetarian"]
    assert card.values["noise_preference"] == sut.UNKNOWN     # refused by the schema, not raised


def test_an_empty_batch_stands_still_and_costs_nothing(extractor):
    model = FakeModel()
    one = extractor(invoke=model)
    one.reset("gen-000")
    assert one.observe([]).values["dietary"] == sut.UNKNOWN
    assert model.calls == []


# --- the property the row exists to measure ----------------------------------


def test_the_bank_is_append_only_and_a_later_memory_does_not_displace_an_earlier_one(extractor):
    """ADR-0062's headline finding, asserted against the real library rather than trusted.

    mem0 2.0.18 is ADD-only: `DEFAULT_UPDATE_MEMORY_PROMPT` ships with zero callers, so a person
    who changes their mind leaves both memories standing. `MemoryExtractor` supersedes and this
    does not, and that is the single variable the two rows bracket.
    """
    replies = {1: ["User is vegetarian."], 3: ["User said they have started eating meat again."]}
    model = FakeModel(memories=lambda n: replies[n])
    one = extractor(invoke=model)
    one.reset("gen-000")
    one.observe([_session(0)])
    one.observe([_session(1, "[0] USER: I've started eating meat again.")])

    assert one.memories() == [
        "User is vegetarian.",
        "User said they have started eating meat again.",
    ] or sorted(one.memories()) == sorted([
        "User is vegetarian.",
        "User said they have started eating meat again.",
    ])
    assert len(one.memories()) == 2, "a contradiction must not remove the memory it contradicts"


# --- §1.3's rules ------------------------------------------------------------


def test_reset_drops_the_whole_store_so_nothing_crosses_a_subject_boundary(extractor):
    one = extractor(invoke=FakeModel())
    one.reset("gen-000")
    one.observe([_session()])
    assert one.memories()

    one.reset("gen-001")
    assert one.memories() == []
    assert one.carried_state_bytes() == len(json.dumps({"memories": [], "messages": []}))


def test_carried_state_counts_the_transcript_mem0_keeps_as_well_as_the_memories(extractor):
    """ADR-0062 decision 3. mem0 writes every turn to SQLite and feeds the last ten back into the
    next extraction prompt, so metering only the memories would let this row carry a conversation
    for free while `notebook` pays for a scratchpad."""
    one = extractor(invoke=FakeModel())
    one.reset("gen-000")
    one.observe([_session()])

    blob = one._state_blob()
    assert "User is vegetarian." in blob                       # the bank
    assert "steak-forward" in blob                             # the transcript mem0 kept
    assert one.carried_state_tokens() == sut.estimate_tokens(blob)
    assert one.carried_state_tokens() > sut.estimate_tokens(json.dumps(one.memories()))


def test_the_whole_bank_reaches_the_extraction_prompt_rather_than_mem0s_top_ten(extractor):
    """ADR-0062 decision 2: with a constant embedding, mem0's hardcoded `top_k=10` would hand the
    prompt an *arbitrary* ten. `_ShowEverything` removes the ranking instead of randomising it."""
    model = FakeModel(memories=lambda n: [f"memory number {n}"])
    one = extractor(invoke=model)
    one.reset("gen-000")
    for checkpoint in range(12):
        one.observe([_session(checkpoint, f"[0] USER: fact {checkpoint}")])

    assert len(one.memories()) == 12
    last_extract = [p for phase, p in model.calls if phase == "extract"][-1]
    assert "memory number 1" in last_extract and "memory number 21" in last_extract


# --- failure, and the cache --------------------------------------------------


def test_an_exhausted_transport_on_extraction_costs_the_memories_and_not_the_card(extractor):
    from persona_twin.judge import TransportError

    def flaky(model, system, prompt):
        if system != sut.BANK_EMISSION_SYSTEM:
            raise TransportError("down")
        return json.dumps({"card": {"dietary": ["vegetarian"]}})

    complaints: list[str] = []
    one = extractor(invoke=flaky, attempts=2, on_problem=complaints.append)
    one.reset("gen-000")
    card = one.observe([_session()])

    assert one.memories() == []
    assert card.values["dietary"] == ["vegetarian"]
    assert any("no reply on extract" in c for c in complaints)


def test_replies_cache_on_both_phases_so_a_rerun_pays_nothing(extractor, tmp_path):
    store = sut.EmissionStore(tmp_path)
    first = FakeModel()
    one = extractor(invoke=first, store=store)
    one.reset("gen-000")
    one.observe([_session()])
    assert len(first.calls) == 2

    def refuse(model, system, prompt):
        raise AssertionError("the cache should have answered")

    again = extractor(invoke=refuse, store=store)
    again.reset("gen-000")
    card = again.observe([_session()])
    assert card.values["dietary"] == ["vegetarian"]


# --- the registry ------------------------------------------------------------


def test_mem0_is_a_paid_kind_and_carries_its_model_on_the_label():
    assert "mem0" in sut.PAID
    assert sut.parse_spec("mem0:claude-sonnet-5", "default") == (
        "mem0:claude-sonnet-5", "mem0", "claude-sonnet-5")


def test_factories_builds_the_mem0_submission_under_a_label_carrying_the_model():
    built = sut.factories(["mem0"], [], model="fake", invoke=FakeModel())
    assert list(built) == ["mem0:fake"]                 # a row is a system *and* its model
    one = built["mem0:fake"]()
    try:
        assert one.name == "mem0" and one.model == "fake"
    finally:
        one._close()
