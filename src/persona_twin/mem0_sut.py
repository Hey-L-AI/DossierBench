"""mem0 as a submission: the one row on the table this tree did not design (ADR-0062).

    python -m persona_twin.sut --sut mem0 --model claude-opus-5

The other three submissions were written by the same hands that wrote `CARD_SYSTEM`, which warns a
model in five lines that abstention is free in the untouched band and that a correct guess is
still a fabrication. All three are therefore pre-warned about the criterion they are scored on,
and a leaderboard of the three is a leaderboard of one author's prompts against that author's own
rule. This module is the row that did not read the rules first.

**What mem0 2.0.18 is, which is not what it is generally believed to be.** `Memory.add()` runs
`ADDITIVE_EXTRACTION_PROMPT` and is **ADD-only**: the ADD/UPDATE/DELETE/NONE memory manager most
people mean by "mem0" still ships in its `prompts.py` and has zero callers. The bank never
supersedes and never deletes, and its only deduplication is an exact MD5 match. A person who stops
eating meat in session 9 leaves session 3's memory standing beside the new one forever — which is
precisely what `MemoryExtractor` spends its second call resolving, and why the two rows are worth
reading as a pair.

Its extraction prompt also pushes the opposite way to the scoring rule: "When in doubt, extract",
and a hard quota of "5-15 memories" for a conversation of ten or more messages. That is not a
defect — it is a memory layer built for personalization, where the missed memory is the expensive
error. This benchmark inverts that price, and what the inversion costs is the measurement.

**What this module supplies and what it does not.** mem0 stores memories and has no notion of a
field-valued card, so the read-out is `sut.BANK_EMISSION_SYSTEM` — `MemoryExtractor`'s phase 2
minus the bank-maintenance half. The two SUTs are served the same sessions and read out by the
same instrument, so what is left between them is the memory layer and nothing else.

Four things are arranged around mem0 rather than inside it, all of them ADR-0062 decisions:
telemetry is dead before the import, its LLM is this tree's `Invoker` (so the calls cache, the
model is selectable and the tests stay free), its add-time `top_k=10` search is widened to the
whole bank because nothing here ranks, and the raw transcript it keeps in SQLite is metered as
carried state beside the memories.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field as dc_field
from typing import Any, Callable, Sequence

from . import sut
from .card import PersonaCard
from .judge import Invoker, Router, TransportError

#: Dimensions of the constant embedding. Any width works once nothing ranks on it; 32 is small
#: enough that the Qdrant collection costs nothing and wide enough not to look like a sentinel.
EMBEDDING_DIMS: int = 32

#: The constant every vector is. Not zero: Qdrant's default metric is cosine, which is undefined
#: on a zero-norm vector, and the failure would arrive as a rejected upsert rather than a warning.
EMBEDDING_VALUE: float = 0.1

#: What `search` is forced to return instead of mem0's hardcoded `top_k=10` (ADR-0062 decision 2).
#: The bank tops out around forty items on this corpus, so this is "all of it" with room spare.
SHOW_EVERYTHING: int = 10_000

#: mem0's own scope key for the SQLite `messages` table, which is where it keeps raw transcript.
#: Read back by `carried_state_*`; hardcoded in mem0 as `f"user_id={user_id}"`.
def _scope(subject_id: str) -> str:
    return f"user_id={subject_id}"


INSTALL_HINT = (
    "the mem0 submission needs the optional extra: "
    "pip install 'persona-twin-bench[mem0]'  (or: pip install mem0ai)"
)


#: Set the moment this module is imported, which is necessarily before it can import mem0 —
#: `MEM0_TELEMETRY` is read once, at mem0's own import, and there is no configuration flag.
os.environ["MEM0_TELEMETRY"] = "False"
#: mem0 otherwise `mkdir`s this in the user's home on import.
os.environ.setdefault("MEM0_DIR", tempfile.mkdtemp(prefix="mem0-home-"))
#: mem0 builds its OpenAI embedder before anything else and the SDK refuses to construct without
#: a key. It is never used: both the embedder and the LLM are replaced after construction.
os.environ.setdefault("OPENAI_API_KEY", "unused-by-this-harness")


def _import_mem0() -> tuple[Any, Any]:
    """`(Memory, EmbeddingBase)`, having *checked* that telemetry actually went off.

    ADR-0062 decision 5, and verified rather than trusted because the env var above only works if
    nothing imported mem0 first. Two things ride on it, and the second is easy to miss:

    * mem0 posts to PostHog on init, on add and on `get_all`, and blocks on a feature-flag request
      the first time it is used. A harness that phones a third party on every checkpoint is not
      one anybody should have to remember to switch off.
    * with telemetry live, **every `Memory` opens a second Qdrant on one shared path** and holds
      an exclusive file lock on it for its lifetime (`main.py`'s `_telemetry_vector_store`). Two
      subjects in flight then collide, so the four workers would serialise — or crash.
    """
    try:
        from mem0 import Memory                                  # type: ignore[import-untyped]
        from mem0.embeddings.base import EmbeddingBase           # type: ignore[import-untyped]
        from mem0.memory.telemetry import MEM0_TELEMETRY         # type: ignore[import-untyped]
    except ImportError as exc:                                   # pragma: no cover - env guard
        raise ImportError(INSTALL_HINT) from exc
    if MEM0_TELEMETRY:
        raise RuntimeError(
            "mem0 was imported with telemetry live: something imported `mem0` before "
            "`persona_twin.mem0_sut`, and mem0 reads MEM0_TELEMETRY once, at its own import. "
            "Import this module first. See ADR-0062 decision 5 — left on, mem0 posts to PostHog "
            "on every checkpoint and every store holds a lock on one shared directory."
        )
    return Memory, EmbeddingBase


# --- the three pieces bolted onto mem0 ---------------------------------------


@dataclass
class InvokerLLM:
    """mem0's LLM, routed through this tree's transport and reply cache (ADR-0062 decision 4).

    mem0's entire LLM contract is `generate_response(messages=[...]) -> str`, so the boundary is
    one method and everything the other SUTs get for free comes back: `--model` selects the model
    rather than mem0's pinned default, `EmissionStore` means a re-run pays only for the prompts
    that moved, and a fake invoker keeps every test in `test_sut.py` free.

    An exhausted transport returns `""`. mem0's JSON parse then fails and it records zero
    extractions without raising, so the accident costs that checkpoint's memories and nothing
    else — `parse_emission`'s rule, arrived at through someone else's code.
    """

    owner: Any
    invoke: Invoker
    attempts: int = sut.DEFAULT_ATTEMPTS
    store: Any = None
    on_problem: Callable[[str], None] | None = None

    #: Set by `Mem0Extractor.observe` before each `add()`, because the cache is keyed on them.
    subject_id: str = ""
    checkpoint: int = 0
    calls: int = 0

    def generate_response(self, messages: Sequence[Any], **_: Any) -> str:
        system, prompt = _split(messages)
        self.calls += 1
        cached = (
            self.store.load(self.owner, self.subject_id, self.checkpoint, prompt,
                            phase="extract", system=system)
            if self.store else None
        )
        if cached is not None:
            return cached
        last: Exception | None = None
        for _attempt in range(max(1, self.attempts)):
            try:
                raw = self.invoke(self.owner.model, system, prompt)
            except TransportError as exc:
                last = exc
                continue
            if self.store:
                self.store.write(self.owner, self.subject_id, self.checkpoint, prompt, raw,
                                 phase="extract", system=system)
            return raw
        self._report(f"no reply on extract ({last})")
        return ""

    def _report(self, problem: str) -> None:
        if self.on_problem:
            self.on_problem(f"{self.subject_id} c{self.checkpoint}: {problem}")


def _split(messages: Sequence[Any]) -> tuple[str, str]:
    """mem0's `messages` list into this tree's `(system, prompt)` pair.

    mem0 sends one system message and one user message. Anything else is concatenated into the
    prompt rather than dropped, so a future mem0 that adds a turn changes the fingerprint — and
    therefore invalidates the cache — instead of silently losing text out of the instrument.
    """
    system_parts, prompt_parts = [], []
    for message in messages:
        if isinstance(message, dict):
            role, content = str(message.get("role") or ""), str(message.get("content") or "")
        else:
            role, content = "user", str(message)
        (system_parts if role == "system" else prompt_parts).append(content)
    return "\n\n".join(system_parts), "\n\n".join(prompt_parts)


def _constant_embedder(base: Any) -> Any:
    """An embedding that is the same vector for every input (ADR-0062 decision 2).

    Honest only because nothing ranks on it: `_ShowEverything` widens mem0's add-time search to
    the whole bank, so the order the embedding would have imposed is never consulted. Left as
    mem0's default embedder it would cost an API key and a per-checkpoint charge outside
    `EmissionStore`, and make this the only non-deterministic row of the four.
    """

    class ConstantEmbedding(base):                               # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            class _Config:
                embedding_dims = EMBEDDING_DIMS
                model = "constant"
            self.config = _Config()

        def embed(self, text: Any, memory_action: Any = None) -> list[float]:
            return [EMBEDDING_VALUE] * EMBEDDING_DIMS

    return ConstantEmbedding()


@dataclass
class _ShowEverything:
    """mem0's vector store with its add-time `top_k` widened to the whole bank.

    mem0 picks what the extraction prompt sees with `search(..., top_k=10)` — hardcoded, with no
    score threshold, and unavoidable on the inferring path. Under a constant embedding every
    distance ties, so those ten would be an **arbitrary** ten rather than a relevant ten, which is
    a worse artifact than either showing all of them or ranking them properly. Widening the search
    removes the ranking instead of randomising it, and on a bank of a few dozen items it is what a
    real embedder with a generous `top_k` would have returned anyway.
    """

    inner: Any

    def search(self, *args: Any, **kwargs: Any) -> Any:
        if "top_k" in kwargs or len(args) < 3:
            kwargs["top_k"] = SHOW_EVERYTHING
            return self.inner.search(*args, **kwargs)
        args = (*args[:2], SHOW_EVERYTHING, *args[3:])
        return self.inner.search(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        if name == "inner":     # before the dataclass has set it; forwarding would recurse
            raise AttributeError(name)
        return getattr(self.inner, name)


# --- the submission ----------------------------------------------------------


def to_messages(sessions: Sequence[sut.ServedSession]) -> list[dict[str, str]]:
    """The served batch in mem0's shape: one role-tagged message per turn.

    The transcripts are `[n] USER:` / `[n] BOT:` lines (`author`'s format), and mem0 is built to
    read a conversation rather than a wall of text — handing it one blob would be measuring mem0
    on an input shape it was not written for. The session header rides on the first turn so the
    date survives, since mem0's prompt resolves relative dates against it.
    """
    out: list[dict[str, str]] = []
    for served in sessions:
        header = f"[session {served.session + 1}{', ' + served.date if served.date else ''}]"
        first = True
        for line in served.transcript.strip().splitlines():
            text = line.strip()
            if not text:
                continue
            role = "user"
            body = text
            for tag, mapped in (("USER:", "user"), ("BOT:", "assistant")):
                marker = text.find(tag)
                if marker != -1 and marker <= 6:
                    role, body = mapped, text[marker + len(tag):].strip()
                    break
            else:
                if out and not first:         # a wrapped continuation line, not a new turn
                    out[-1]["content"] += "\n" + body
                    continue                  # never across a session boundary: the header holds
            out.append({"role": role, "content": f"{header} {body}" if first else body})
            first = False
    return out


@dataclass
class Mem0Extractor:
    """mem0 2.0.18 as the memory layer, with this tree's card read-out on top (ADR-0062).

    Two calls a checkpoint like `MemoryExtractor`, and deliberately the same two shapes: mem0's
    `add()` reads the new sessions and writes memories, then `bank_emission_prompt` reads the bank
    back and emits the card. What differs from `MemoryExtractor` is only what happens in between —
    mem0's bank is append-only, so nothing here ever supersedes or deletes.
    """

    model: str = "claude-opus-5"
    invoke: Invoker | None = None
    attempts: int = sut.DEFAULT_ATTEMPTS
    name: str = "mem0"
    prompt_version: str = "v1"
    on_problem: Callable[[str], None] | None = None
    store: Any = None

    subject_id: str = ""
    _memory: Any = dc_field(default=None, repr=False)
    _llm: InvokerLLM | None = dc_field(default=None, repr=False)
    _card: PersonaCard | None = dc_field(default=None, repr=False)
    _vocab: sut.VocabTally = dc_field(default_factory=sut.VocabTally, repr=False)
    _home: str = dc_field(default="", repr=False)
    _checkpoint: int = 0

    def __post_init__(self) -> None:
        self.invoke = self.invoke or Router()
        if self.prompt_version not in sut.BANK_SYSTEMS:
            raise ValueError(
                f"unknown prompt version {self.prompt_version!r}; have {sorted(sut.BANK_SYSTEMS)}")

    @property
    def emission_system(self) -> str:
        """The read-out prompt (`sut.BANK_SYSTEMS`) — the only prompt of this submission's that
        this tree writes; the bank itself is mem0's."""
        return sut.BANK_SYSTEMS[self.prompt_version]

    # -- protocol ------------------------------------------------------

    def reset(self, subject_id: str) -> None:
        """A new store on a new directory, not a cleared one (ADR-0062 decision 6).

        §1.3's second rule is that nothing survives a subject boundary. With a third-party store
        the cheapest way to be sure of that is not to reuse the store, and a private directory
        also keeps the four workers off each other's Qdrant file locks.
        """
        self._close()
        self.subject_id = subject_id
        self._card = PersonaCard.all_unknown(subject_id)
        self._vocab = sut.VocabTally()
        self._checkpoint = 0

        Memory, EmbeddingBase = _import_mem0()
        self._home = tempfile.mkdtemp(prefix=f"mem0-{subject_id}-")
        memory = Memory.from_config({
            "vector_store": {"provider": "qdrant", "config": {
                "path": os.path.join(self._home, "qdrant"),
                "collection_name": "twin",
                "embedding_model_dims": EMBEDDING_DIMS,
            }},
            "history_db_path": os.path.join(self._home, "history.db"),
        })
        memory.embedding_model = _constant_embedder(EmbeddingBase)
        memory.vector_store = _ShowEverything(memory.vector_store)
        self._llm = InvokerLLM(
            owner=self, invoke=self.invoke, attempts=self.attempts,   # type: ignore[arg-type]
            store=self.store, on_problem=self.on_problem)
        memory.llm = self._llm
        self._memory = memory

    def observe(self, sessions: Sequence[sut.ServedSession]) -> PersonaCard:
        checkpoint = self._checkpoint
        self._checkpoint += 1
        carried = self._card or PersonaCard.all_unknown(self.subject_id)
        if not sessions or self._memory is None or self._llm is None:
            return carried

        self._llm.subject_id, self._llm.checkpoint = self.subject_id, checkpoint
        try:
            self._memory.add(messages=to_messages(sessions), user_id=self.subject_id)
        except Exception as exc:                                  # noqa: BLE001
            # mem0 swallows a parse failure but raises on a store failure. Neither should cost
            # the run twenty fields: the bank simply does not grow this checkpoint.
            self._report(checkpoint, f"mem0 add failed: {type(exc).__name__}: {exc}")

        prompt = sut.bank_emission_prompt(self.memories())
        raw = self._reply(checkpoint, self.emission_system, prompt)
        emission = sut.parse_emission(raw, self.subject_id, previous=carried)
        # The bank is re-read whole each checkpoint, so the distributions are re-derived from this
        # readout's datapoints rather than accumulated — there is no batch to add to. The vocab
        # tally still accrues per checkpoint: mem0 re-lists the whole bank each time, so its kept
        # and dropped counts are per-emission occasions, not once-per-occasion like the others —
        # the *rate* is what compares across submissions, not the absolute counts.
        self._vocab.add(emission.datapoints, emission.datapoint_drops)
        card, refused = sut.validated(sut.rederived_distributions(raw, emission.card))
        for problem in (*emission.problems, *refused):
            self._report(checkpoint, problem)
        self._card = card
        return card

    def carried_state_bytes(self) -> int:
        return len(self._state_blob().encode("utf-8"))

    def carried_state_tokens(self) -> int:
        return sut.estimate_tokens(self._state_blob())

    # -- state ---------------------------------------------------------

    def memories(self) -> list[str]:
        """The whole bank, read without embedding anything.

        `get_all` scrolls the collection; only `search` embeds. It needs a scope and has no
        dump-everything form, so the subject id is passed as the filter it was stored under.
        """
        if self._memory is None:
            return []
        try:
            found = self._memory.get_all(
                filters={"user_id": self.subject_id}, top_k=SHOW_EVERYTHING)
        except Exception as exc:                                  # noqa: BLE001
            self._report(self._checkpoint, f"mem0 get_all failed: {type(exc).__name__}")
            return []
        results = found.get("results", found) if isinstance(found, dict) else found
        return [str(row.get("memory") or "") for row in results if row.get("memory")]

    def _kept_transcript(self) -> list[str]:
        """The raw conversation mem0 keeps and re-reads (ADR-0062 decision 3).

        `add()` writes every turn to a SQLite `messages` table and feeds the most recent ten back
        into the next extraction prompt. That is transcript this submission will be able to read
        at the next checkpoint, so §1.3 puts it on the meter — metering only the memories would
        let this row carry a conversation for free while `notebook` pays for a scratchpad.
        """
        if self._memory is None or not hasattr(self._memory, "db"):
            return []
        try:
            rows = self._memory.db.get_last_messages(_scope(self.subject_id))
        except Exception:                                         # noqa: BLE001
            return []
        return [f"{row.get('role', '')}: {row.get('content', '')}" for row in rows or []]

    def _state_blob(self) -> str:
        """Everything mem0 can read at the next checkpoint: the bank and the kept transcript.

        The embeddings are not in here. Nothing ranks on them (decision 2) and every one of them
        is the same constant vector, so they carry no information the memory text does not.
        """
        return json.dumps({"memories": self.memories(), "messages": self._kept_transcript()})

    # -- internals -----------------------------------------------------

    def _reply(self, checkpoint: int, system: str, prompt: str) -> str:
        cached = (
            self.store.load(self, self.subject_id, checkpoint, prompt, phase="emit", system=system)
            if self.store else None
        )
        if cached is not None:
            return cached
        last: Exception | None = None
        for _attempt in range(max(1, self.attempts)):
            try:
                raw = self.invoke(self.model, system, prompt)      # type: ignore[misc]
            except TransportError as exc:
                last = exc
                continue
            if self.store:
                self.store.write(self, self.subject_id, checkpoint, prompt, raw,
                                 phase="emit", system=system)
            return raw
        self._report(checkpoint, f"no reply on emit ({last})")
        return ""

    def _report(self, checkpoint: int, problem: str) -> None:
        if self.on_problem:
            self.on_problem(f"{self.subject_id} c{checkpoint}: {problem}")

    def _close(self) -> None:
        """Release the Qdrant file lock and drop the subject's directory.

        Qdrant's own `__del__` runs during interpreter shutdown and raises out of it; closing
        here means a 230-subject run does not leave 230 embedded databases on the volume.
        """
        memory, self._memory = self._memory, None
        if memory is not None:
            try:
                memory.vector_store.client.close()
            except Exception:                                     # noqa: BLE001
                pass
        if self._home:
            shutil.rmtree(self._home, ignore_errors=True)
            self._home = ""

    def __del__(self) -> None:                                    # pragma: no cover - finaliser
        try:
            self._close()
        except Exception:                                         # noqa: BLE001
            pass
