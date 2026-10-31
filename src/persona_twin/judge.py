"""The §7.3 grading runner: judge assignment, invocation, caching, and the run manifest.

Step 6's model-calling half. `grading.py` owns the question and the parser; this owns everything
around them — which judge grades which bundle, how a reply is obtained, when a cached one may be
reused, and what the run publishes about itself.

Nothing here decides a grade. The split is deliberate and is the same one `transcript.py` and
`evidence.py` keep: what a rule can decide, a rule decides, and the model is asked only the
question it is the only thing that can answer.

## The five rules this module exists to make executable

**1. Two judges per bundle, neither of them the author (§7.3, §7.1).** `assign` is a function
rather than a loop condition, because it is what makes a pool of three models matter: with two
authors and three judges every bundle gets exactly two graders, and dropping one model to three
leaves one author's text with a single eligible grader. `assign` raises rather than grading with
one — a map built from one judge is not a map, and `EvidenceMap.from_judges` would refuse it
later anyway, after the calls were paid for.

**2. A cached grade belongs to the instrument that produced it.** Every record carries a
fingerprint of the exact system prompt and user prompt behind it, and a cache entry whose
fingerprint does not match is a miss. The probes keyed their cache on the path alone, which is
safe only while nothing changes; this project changed §7.3's question once and the same
transcripts moved 38 points (ADR-0014), so a cache that cannot tell which question produced a
record is a live hazard rather than a hypothetical one. Re-authoring invalidates too, since the
transcript is part of the prompt.

**3. Retry transport, never content.** A non-zero exit, a timeout or an empty stdout is a
failure to obtain an answer and is retried. An answer that arrives and cannot be read is a
**wrong answer**, is recorded as one, and is never re-rolled. Retrying unreadable replies would
quietly repair the disposition §7.3 exists to measure, and would bias the pool toward whichever
model complies on the second attempt — the same defect, arriving through the harness instead of
the prompt. `probe_tension/` measured what the passive version of this costs: 23 of 80 gradings
unreadable, 19 from one model, and an unparsed grade defaults to the one band where committing
is charged as fabrication.

**4. The judge model is recorded per map and published (§7.3).** Evidence grade is
model-relative — ADR-0018 measures the same cells graded `strong` at 66%, 36% and 20% by the
three models in the pool — so a map is a joint product of the transcript and its grader, and a
score is not interpretable without knowing which grader produced its bands. The manifest carries
the pool, the per-judge call and failure counts, and the instrument fingerprint.

**5. A grader may be more than one call, and a cell is all of them or none.** ADR-0022 measured a
question's frame leaking across fields inside one reply: distribution fields carved out of the
rule-out *instruction* still came back empty 48% of the time against the 18% the same models
produce when the whole reply is a licensed set. The remedy is structural — ask the two questions
in two requests — and it belongs here rather than in `grading.py`, because the disposition is a
property of the reply and the reply is what a runner owns. `grader.passes` is the call list; the
cache identity covers every pass, so re-partitioning the schema is a miss on every cell; and a
pass that fails transport fails the whole cell without writing a record. That last one is not
tidiness: a record missing one pass's fields puts them in `missing`, and the reading that fills
omitted rows as `unevidenced` (ADR-0021 decision 5) would turn a transport failure into three
judges finding no evidence — the defect the split exists to remove, arriving through the harness.

## Parallelism

The unit is the call. §6.0 forbids parallel *authoring* within a subject, because session k is
written with 0..k-1 in context; grading has no such constraint — each (author, subject,
checkpoint, judge) is an independent read of a fixed prefix — so there is nothing to serialize
and the pool is flat.

    python -m persona_twin.judge --bundles run/authored --corpus run/corpus.json --out run/graded
    python -m persona_twin.judge ... --plan        # what it would cost, without spending it
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from . import grading
from .grading import GRADERS, Grader, Reply

#: The §7.3 judge pool. Three models, and the size is load-bearing: §7.3 needs two judges per
#: bundle excluding the author, so a pool of three is the smallest that grades both authors'
#: text under two graders each. §7.5 records what dropping to two would cost.
DEFAULT_POOL: tuple[str, ...] = (
    "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001",
)

DEFAULT_WORKERS: int = 8
DEFAULT_TIMEOUT: int = 420

#: Per-model OpenRouter provider pins. Default routing load-balances across every provider that
#: serves a model, which mixes latencies AND quantizations (fp4/fp8/bf16) within one run — a
#: silent confound, since a fp4 endpoint and a bf16 endpoint are not the same system. A model
#: listed here is instead pinned to one provider at one quantization with fallbacks OFF, so every
#: call it makes is the same endpoint. `deepseek/deepseek-v4-flash-0731` is pinned to Baidu/fp8:
#: default routing was landing on providers ~40x slower (~78s/call) than Baidu (~1.9s/call), and
#: pinning also fixes the quant to fp8. Keyed on the exact model id, so luna and glm (which Baidu
#: does not serve) are untouched and route by default.
PROVIDER_PINS: dict[str, dict[str, object]] = {
    "deepseek/deepseek-v4-flash-0731": {
        "only": ["baidu"], "quantizations": ["fp8"], "allow_fallbacks": False,
    },
    # deepseek-v4-pro-0813 pinned to Alibaba (the n50 pro run, 2026-08-28). OpenRouter reports
    # Alibaba's quant as "unknown", so it is not quant-selectable; provider is pinned and
    # fallbacks off so every call lands on the same Alibaba endpoint rather than mixing quants.
    "deepseek/deepseek-v4-pro-0813": {
        "only": ["alibaba"], "allow_fallbacks": False,
    },
    # z-ai/glm-5.3 pinned to Together (the n50 prompted run, 2026-08-29). Fallbacks off so every
    # call lands on the same Together endpoint rather than mixing providers/quants within the run.
    "z-ai/glm-5.3": {
        "only": ["together"], "allow_fallbacks": False,
    },
    # z-ai/glm-5.3-flash pinned to Z.AI's own fp8 endpoint (the n50 prompted v1-vs-v2 run,
    # 2026-08-30). Fallbacks off so every call is the same provider at the same quant.
    "z-ai/glm-5.3-flash": {
        "only": ["z-ai"], "quantizations": ["fp8"], "allow_fallbacks": False,
    },
}

#: Transport retries. Content is never retried — see rule 3 in the module docstring.
DEFAULT_ATTEMPTS: int = 3
RETRY_BACKOFF: float = 2.0


class JudgeAssignmentError(ValueError):
    """A pool that cannot supply two eligible judges for a bundle."""


def assign(pool: Sequence[str], author: str) -> tuple[str, ...]:
    """The judges eligible to grade one author's text: everyone but the author (§7.1, §7.3).

    Raises rather than returning one. §7.3's elementwise minimum needs two independent readings
    to have anything to combine, and the weak band exists to absorb their disagreement — with a
    single judge there is no disagreement to absorb and the band becomes that model's habit.
    ADR-0019 measures what that looks like: earned reads 26–29% under one judge and 70–84%
    under two, on the same text.
    """
    eligible = tuple(model for model in pool if model != author)
    if len(eligible) < 2:
        raise JudgeAssignmentError(
            f"pool {list(pool)} supplies {len(eligible)} judge(s) for text authored by "
            f"{author!r}; §7.3 requires 2 independent judges excluding the author"
        )
    return eligible


# --- what gets graded --------------------------------------------------------


@dataclass(frozen=True)
class Bundle:
    """One author's transcripts for one subject, as the SUT is served them (§1.3).

    ``sessions`` is ordered and each carries the checkpoint it belongs to, because a checkpoint
    may span more than one session and the judge must see the prefix the SUT saw — not the
    timeline, and not one session in isolation.
    """

    subject_id: str
    author: str
    sessions: tuple[tuple[int, int, str], ...] = ()      # (session index, checkpoint, text)

    @property
    def checkpoints(self) -> tuple[int, ...]:
        return tuple(sorted({checkpoint for _s, checkpoint, _t in self.sessions}))

    def prefix(self, checkpoint: int) -> str | None:
        """Sessions through ``checkpoint``, as one transcript. `None` when any is missing.

        A missing session is a hole in the prefix, not a shorter prefix: grading sessions 1 and
        3 as though they were 1 and 2 would score the SUT against evidence in an order it never
        saw. The caller skips the cell and the manifest counts it.
        """
        parts = []
        for index, session_checkpoint, text in sorted(self.sessions):
            if session_checkpoint > checkpoint:
                continue
            if not text.strip():
                return None
            parts.append(f"--- session {index + 1} ---\n{text}")
        return "\n\n".join(parts) if parts else None

    @classmethod
    def from_records(cls, records: Iterable[Mapping[str, Any]]) -> list[Bundle]:
        """Group authored-session records into bundles.

        Accepts the shape the authoring stage writes — one record per session, carrying
        ``subject_id``, the author model, the session index, its checkpoint and the text.
        ``model`` is accepted as a synonym for ``author`` because that is what the probe
        runners wrote, and re-grading existing text is the cheapest thing this runner does.
        """
        grouped: dict[tuple[str, str], list[tuple[int, int, str]]] = {}
        for record in records:
            author = str(record.get("author") or record.get("model") or "")
            if not author:
                raise ValueError(
                    f"authored record for {record.get('subject_id')!r} names no author model; "
                    "§7.1 records it per bundle and §7.3 excludes it from the judges"
                )
            key = (str(record["subject_id"]), author)
            grouped.setdefault(key, []).append((
                int(record["session"]), int(record["checkpoint"]),
                str(record.get("transcript") or ""),
            ))
        return [
            cls(subject_id, author, tuple(sorted(sessions)))
            for (subject_id, author), sessions in sorted(grouped.items())
        ]


# --- the model boundary ------------------------------------------------------


class Invoker(Protocol):
    """Everything this module needs from a model. One method, so a test never needs one."""

    def __call__(self, model: str, system: str, prompt: str) -> str: ...


@dataclass
class ClaudeCli:
    """The shipped invoker. Kept trivial: a subprocess, a timeout, and no interpretation.

    Tools are disabled explicitly. A judge that can read files could reach the card, the
    schedule or a sibling's transcript, and the grade would then be of the dataset rather than
    of the prefix it was shown — the leak §7.3's whole apparatus is built to prevent, arriving
    through the harness.
    """

    timeout: int = DEFAULT_TIMEOUT

    def __call__(self, model: str, system: str, prompt: str) -> str:
        proc = subprocess.run(
            ["claude", "-p", "--model", model,
             "--allowed-tools", "", "--system-prompt", system],
            input=prompt, capture_output=True, text=True, timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise TransportError(f"{model} exited {proc.returncode}: {_stderr_tail(proc.stderr)}")
        if not proc.stdout.strip():
            raise TransportError(f"{model} returned an empty reply")
        return proc.stdout


#: Lines the CLI prints on **every** invocation regardless of outcome. They are the operator's
#: settings, not this run's failure, and they are the reason this helper exists: a
#: misconfigured `~/.claude/settings.json` emitted ~900 characters of them, the old
#: `stderr[:300]` was consumed entirely by the first three, and 414 consecutive transport
#: failures were logged with an error message that named a permission rule instead of the rate
#: limit that actually caused them. The truncation is kept — a stack of CLI output in every log
#: line helps nobody — but it now falls on the *end* of stderr, where the cause is, and the
#: known-benign preamble is dropped rather than allowed to crowd it out.
_BENIGN_STDERR = ("Permission deny rule",)


def _stderr_tail(stderr: str, limit: int = 300) -> str:
    """The last `limit` characters of stderr that are not known-benign preamble.

    The two empty cases are reported **differently**, and the distinction is diagnostic rather
    than cosmetic: a past run whose cells failed with genuinely empty stderr turned out to be
    OOM-killed by the OS rather than refused by an API, and collapsing that into the same marker
    as "all we got was configuration noise" would lose the only signal that says so.
    """
    raw = stderr or ""
    kept = [
        line for line in raw.splitlines()
        if line.strip() and not line.lstrip().startswith(_BENIGN_STDERR)
    ]
    text = "\n".join(kept).strip()
    if text:
        return text[-limit:]
    if not raw.strip():
        return "<empty stderr — suspect the OS killed the child (out of memory?); try fewer workers>"
    return "<no stderr beyond CLI configuration warnings>"


class TransportError(RuntimeError):
    """No answer was obtained. Retryable — unlike an answer that cannot be read."""


@dataclass
class OpenRouter:
    """A judge outside the Claude family, over OpenRouter's OpenAI-compatible endpoint.

    This exists because of a defect in the pool that is structural rather than statistical.
    §7.1 excludes the author from grading its own text, and the pool's three models include
    **both** authors — so `assign` hands every bundle a pair containing haiku, and the two
    pairs §7.5 reports sit on disjoint text (haiku+sonnet grades only opus-authored
    transcripts, haiku+opus only sonnet-authored ones). Every κ this project has published is
    haiku-versus-something measured within one author. A judge that authored nothing is the
    only way to obtain a pair without haiku in it, and a judge outside the family is the only
    way to tell "haiku reads differently" apart from "the family shares a reading".

    Kept as trivial as `ClaudeCli` and for the same reason: no interpretation, no retry, no
    parsing. `parse_reply` owns what a reply means, and rule 3 owns when one is asked for
    again — an invoker that retried content would repair the disposition §7.3 measures.

    No tools are requested, which is the `--allowed-tools ""` of this transport: the endpoint
    only calls tools it is given, and it is given none.
    """

    timeout: int = DEFAULT_TIMEOUT
    base_url: str = "https://openrouter.ai/api/v1"
    _client: Any = None

    def client(self):
        """Built on first use, not in `__post_init__`. Constructing a client at import time
        would make the missing-key failure arrive when the module loads rather than when a
        call is attempted, and `plan`/`--plan` must stay runnable without a key."""
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:                             # pragma: no cover - env
                raise TransportError(f"openai SDK not installed: {exc}") from exc
            import os
            key = os.getenv("OPENROUTER_API_KEY")
            if not key:
                raise TransportError("OPENROUTER_API_KEY is not set")
            self._client = OpenAI(
                base_url=self.base_url, api_key=key, timeout=self.timeout,
                default_headers={"X-Title": "persona-twin s7.3 judge"},
            )
        return self._client

    def __call__(self, model: str, system: str, prompt: str) -> str:
        pin = PROVIDER_PINS.get(model)
        extra_body = {"provider": pin} if pin else None
        try:
            reply = self.client().chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": prompt}],
                extra_body=extra_body,
            )
        except TransportError:
            raise
        except Exception as exc:                                   # noqa: BLE001
            # Every failure to *obtain* an answer is one kind of thing here — HTTP, rate limit,
            # refusal to route — and the retry rule treats them identically. A reply that
            # arrives and is unreadable never reaches this branch.
            raise TransportError(f"{model} call failed: {type(exc).__name__}: {exc}") from exc
        choices = getattr(reply, "choices", None) or []
        text = (getattr(choices[0].message, "content", None) or "") if choices else ""
        if not text.strip():
            raise TransportError(f"{model} returned an empty reply")
        return text


@dataclass
class Router:
    """Sends each judge to the transport that can reach it, by the shape of its model id.

    A model id containing `/` is a `provider/model` OpenRouter route; anything else is a bare
    Claude model name for the CLI. That is the whole rule, and it is a rule about *ids* rather
    than a registry so that adding a fourth judge is a pool argument and not a code change.

    The pool may therefore mix families in one run, which is what makes the fourth judge
    affordable: the three Claude judges' records are already on disk and hit the cache, so a
    run with the new judge appended spends calls only on the cells that judge has not read.
    """

    timeout: int = DEFAULT_TIMEOUT
    cli: Invoker | None = None
    openrouter: Invoker | None = None

    def __post_init__(self) -> None:
        self.cli = self.cli or ClaudeCli(timeout=self.timeout)
        self.openrouter = self.openrouter or OpenRouter(timeout=self.timeout)

    def __call__(self, model: str, system: str, prompt: str) -> str:
        invoke = self.openrouter if "/" in model else self.cli
        return invoke(model, system, prompt)


def store_slug(model: str) -> str:
    """The on-disk name for a judge. `provider/model` ids carry a separator that would other-
    wise silently become a directory level, so `GradingStore.path` would write
    `graded/openai/gpt-5/...` and the per-judge grouping would read `openai` as the judge.

    Claude ids contain no `/`, so this is the identity on every record already written and
    changes no cache lookup. The record's `judge` field keeps the true id either way — the
    slug names a path, never a model.
    """
    return model.replace("/", "__")


# --- the cache ---------------------------------------------------------------


def fingerprint(system: str, prompt: str) -> str:
    """Identity of the instrument that produced a grade: the exact two strings sent.

    Twelve hex characters, which is plenty to detect a changed prompt and short enough to read
    in a diff. This covers the transcript as well as the question, since the transcript is in
    the prompt — so re-authoring a session invalidates every grade downstream of it, which is
    the correct behaviour and not a side effect.
    """
    digest = hashlib.sha256()
    digest.update(system.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(prompt.encode("utf-8"))
    return digest.hexdigest()[:12]


def instrument_id(parts: Sequence[tuple[str, str]]) -> str:
    """Identity of a whole cell's instrument, across however many calls it takes.

    A single-call grader gets `fingerprint` **exactly**, which is not a convenience: every
    record this project has on disk was written by one, and an identity that folded a
    one-element list differently would mark all 598 cached gradings stale and re-buy them.

    A split grader hashes its passes in order, so changing either question — or moving a field
    from one pass to the other — is a cache miss on every cell, which is the same guarantee
    rule 2 gives a single call.
    """
    if len(parts) == 1:
        return fingerprint(*parts[0])
    digest = hashlib.sha256()
    for system, prompt in parts:
        digest.update(fingerprint(system, prompt).encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()[:12]


@dataclass
class GradingStore:
    """Where grading records live, and the rule for reusing one.

    Layout is `<root>/<judge>/<author>/<subject>-c<checkpoint>.json`, matching the probes so
    their caches can be read by this runner and its output by their analyses.
    """

    root: Path

    def path(self, judge: str, author: str, subject_id: str, checkpoint: int) -> Path:
        return self.root / store_slug(judge) / author / f"{subject_id}-c{checkpoint}.json"

    def load(
        self, judge: str, author: str, subject_id: str, checkpoint: int,
        *, expect: str | None = None,
    ) -> dict | None:
        """A cached record, or `None` when there is none *or it was made by another instrument*.

        `expect` is the fingerprint of the prompt about to be sent. A record without one is
        treated as stale: the probes wrote records before fingerprints existed, and silently
        reusing them would mix grades from two questions inside one map — which is precisely
        the comparison ADR-0014 had to keep clean to attribute a 38-point move.
        """
        path = self.path(judge, author, subject_id, checkpoint)
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if expect is not None and record.get("instrument") != expect:
            return None
        return record

    def records(self) -> list[dict]:
        """Every grading record under this root, and **only** those.

        Two exclusions, both learned the expensive way.
        macOS writes an `._name` AppleDouble sidecar beside every file on a non-native
        filesystem, and they match `*.json`: a 72-call run once read as 144 across five
        documents because a glob counted them. And `manifest.json` sits in the same tree and is
        a run summary, not a grading — reading it as one would add a record with no subject.

        Callers that glob for themselves get this wrong eventually; this is the one place it is
        written down.
        """
        out = []
        for path in sorted(self.root.rglob("*.json")):
            if path.name.startswith("._") or path.name == "manifest.json":
                continue
            try:
                out.append(json.loads(path.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
        return out

    def write(self, record: Mapping[str, Any]) -> Path:
        path = self.path(
            record["judge"], record["author"], record["subject_id"], record["checkpoint"],
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2, default=str))
        return path


def to_record(
    reply: Reply, *, subject_id: str, checkpoint: int, judge: str, author: str,
    instrument: str, raw: str | None, grader: str, attempts: int = 1,
    passes: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """The published grading record — the shape `audit.Grading.from_record` reads.

    ``raw`` is kept whole. Every re-parse this project has run was possible because the reply
    was on disk (ADR-0010's precedent: a new rule over existing grades cost nothing and changed
    the reading twice), and a parser fix applied to stored replies is not a new measurement.

    A **split** grader has more than one reply and no single ``raw``. Those records carry
    ``passes`` — each pass's system fingerprint, scope and whole reply — and set ``raw`` to
    `None` rather than to a join of the two. A join looks re-parseable and is not: `parse_json`
    would salvage the last balanced object, return one pass's answer, and report the other
    pass's fields as missing with no error beside it. `raw_replies` is the reader.

    Single-pass records gain no key at all, so `test_replay`'s 598 cached gradings still come
    back byte for byte.
    """
    record = {
        "subject_id": subject_id,
        "checkpoint": checkpoint,
        "judge": judge,
        "author": author,
        "grader": grader,
        "instrument": instrument,
        "attempts": attempts,
        "grades": {
            field_id: entry.to_json() for field_id, entry in sorted(reply.entries.items())
        },
        "unparsed": list(reply.unparsed),
        "missing": list(reply.missing),
        "error": reply.error,
        "raw": raw,
    }
    if passes is not None:
        record["passes"] = [dict(p) for p in passes]
    return record


def raw_replies(record: Mapping[str, Any]) -> list[str]:
    """Every model reply behind one grading record, in call order.

    One place, because a caller that reaches for `record["raw"]` gets `None` on a split record
    and a caller that guesses gets half a cell. Both shapes are on disk and both are read by the
    same analyses.
    """
    if record.get("passes"):
        return [str(p.get("raw") or "") for p in record["passes"]]
    return [str(record.get("raw") or "")]


# --- the run -----------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    """One grading call: a judge, an author's bundle, and a checkpoint."""

    judge: str
    author: str
    subject_id: str
    checkpoint: int


@dataclass
class RunReport:
    """What the run publishes about itself (§7.3's recorded judge model, and the health §7.5
    scores judges on). Counted per judge, because every failure mode this project has measured
    in a judge pool was concentrated in one model.

    Every mutation takes the lock. `run` fans out across a thread pool and `counter[key] += 1`
    is a load, an add and a store — so an unlocked counter silently under-reports under
    exactly the conditions a real run has, and the number it under-reports is the unreadable
    reply rate that §7.5 scores judges on.
    """

    pool: tuple[str, ...] = ()
    graded: int = 0
    cached: int = 0
    skipped: list[str] = dc_field(default_factory=list)
    failed: list[str] = dc_field(default_factory=list)
    per_judge: dict[str, Counter] = dc_field(default_factory=dict)
    _lock: threading.Lock = dc_field(default_factory=threading.Lock, repr=False)

    def note(self, judge: str, key: str, count: int = 1) -> None:
        with self._lock:
            self.per_judge.setdefault(judge, Counter())[key] += count

    def record_graded(self) -> None:
        with self._lock:
            self.graded += 1

    def record_cached(self) -> None:
        with self._lock:
            self.cached += 1

    def record_skipped(self, message: str) -> None:
        with self._lock:
            self.skipped.append(message)

    def record_failed(self, message: str) -> None:
        with self._lock:
            self.failed.append(message)

    def to_json(self) -> dict[str, Any]:
        return {
            "pool": list(self.pool), "graded": self.graded, "cached": self.cached,
            "skipped": self.skipped, "failed": self.failed,
            "per_judge": {j: dict(c) for j, c in sorted(self.per_judge.items())},
        }


def plan(bundles: Sequence[Bundle], *, pool: Sequence[str] = DEFAULT_POOL) -> list[Cell]:
    """Every call the run would make, in a stable order. Costs nothing and is printed first.

    Deterministic ordering matters for more than tidiness: a partial run resumed after an
    interruption must cover the same cells in the same order, or the cache tells you it is
    complete when it is not.
    """
    cells: list[Cell] = []
    for bundle in sorted(bundles, key=lambda b: (b.subject_id, b.author)):
        for judge in assign(pool, bundle.author):
            for checkpoint in bundle.checkpoints:
                cells.append(Cell(judge, bundle.author, bundle.subject_id, checkpoint))
    return sorted(cells, key=lambda c: (c.subject_id, c.author, c.checkpoint, c.judge))


def grade_cell(
    cell: Cell, bundle: Bundle, store: GradingStore, invoke: Invoker,
    *, attempts: int = DEFAULT_ATTEMPTS, report: RunReport | None = None,
    sleep: Callable[[float], None] = time.sleep, grader: Grader,
) -> str:
    """Grade one cell, honouring the cache and the retry rule. Returns a one-line status.

    ``grader`` has no default. Rule 2 makes a grader swap a cache miss rather than an error —
    that is load-bearing, it is how the ruleout-split run was pointed at a store that already
    held licensed grades — but a *miss* still spends a call and overwrites the record with the
    new instrument's answer. A default here means an omitted argument is indistinguishable from
    a chosen one: it spends real calls under whichever grader happened to be built in, and
    replaces whatever the store already held with no error at any point, silently, on the same
    on-disk tree every other run reads and writes. Every caller — the CLI, a test, a one-off
    script — names the grader it means.
    """
    report = report or RunReport()
    text = bundle.prefix(cell.checkpoint)
    if text is None:
        message = f"SKIP {cell.subject_id}-c{cell.checkpoint} {cell.author}: transcript missing"
        report.record_skipped(message)
        return message

    # One prompt per pass. A single-question grader has exactly one, and takes the identical
    # path it took before split graders existed.
    prompts = [
        (step, step.build_prompt(text, cell.checkpoint, step.field_ids or None))
        for step in grader.passes
    ]
    # The fingerprint covers the system prompt, so swapping the question invalidates every
    # cached record by construction. That is the rule `judge.py` was built with and it is what
    # makes a boundary-protocol arm safe to run into a directory that already holds grades.
    instrument = instrument_id([(step.system, prompt) for step, prompt in prompts])
    cached = store.load(
        cell.judge, cell.author, cell.subject_id, cell.checkpoint, expect=instrument,
    )
    if cached is not None:
        report.record_cached()
        report.note(cell.judge, "cached")
        return f"cached {cell.subject_id}-c{cell.checkpoint} {cell.author}->{cell.judge}"

    replies: list[Reply] = []
    pass_records: list[dict[str, Any]] = []
    for step, prompt in prompts:
        raw, used, last = "", 0, None
        for attempt in range(1, attempts + 1):
            used = attempt
            try:
                raw = invoke(cell.judge, step.system, prompt)
                last = None
                break
            except Exception as exc:                               # noqa: BLE001
                # Transport only. An answer that arrives and cannot be read never reaches here —
                # `parse_reply` does not raise, it records, and rule 3 is why.
                last = exc
                report.note(cell.judge, "transport_retry")
                if attempt < attempts:
                    sleep(RETRY_BACKOFF ** attempt)
        if last is not None:
            # One failed pass fails the whole cell, and no record is written even though an
            # earlier pass may have answered. A record missing one pass's fields is not a
            # shorter reading of the cell: those fields land in `missing`, and the arm that
            # reads omitted rows as `unevidenced` (ADR-0021 decision 5) would then read a
            # transport failure as three judges finding no evidence — the empty-set defect this
            # split exists to remove, manufactured by the harness instead of the prompt.
            message = (f"FAILED {cell.subject_id}-c{cell.checkpoint} "
                       f"{cell.author}->{cell.judge}"
                       + (f" [{step.name}]" if len(prompts) > 1 else "")
                       + f": {type(last).__name__}: {last}")
            report.record_failed(message)
            report.note(cell.judge, "transport_failed")
            return message
        reply = grading.parse_reply(raw, step)
        replies.append(reply)
        pass_records.append({
            "pass": step.name,
            "instrument": fingerprint(step.system, prompt),
            "fields": list(step.scope),
            "attempts": used,
            "unparsed": list(reply.unparsed),
            "missing": list(reply.missing),
            "error": reply.error,
            "raw": raw,
        })

    reply = grading.merge_replies(replies)
    split = len(prompts) > 1
    store.write(to_record(
        reply, subject_id=cell.subject_id, checkpoint=cell.checkpoint, judge=cell.judge,
        author=cell.author, instrument=instrument,
        raw=None if split else pass_records[0]["raw"], grader=grader.name,
        passes=pass_records if split else None,
    ))
    report.record_graded()
    report.note(cell.judge, "graded")
    # Counted separately from `graded`, because a split grader buys one cell with two calls and
    # the spend is the thing a pre-registration has to state in advance.
    report.note(cell.judge, "calls", len(prompts))
    report.note(cell.judge, "fields", len(reply.entries))
    if reply.unparsed:
        report.note(cell.judge, "unparsed_rows", len(reply.unparsed))
    if reply.missing:
        report.note(cell.judge, "missing_fields", len(reply.missing))
    # Rows that populated a key their field's shape does not license. The content is dropped by
    # the parser either way; counting it here is what makes the drop visible, on the same
    # reasoning `unquoted` is counted — a judge that answers off-protocol is a distinct failure
    # from one that answers badly, and the two have different remedies.
    off_protocol = sum(1 for entry in reply.entries.values() if entry.off_protocol)
    if off_protocol:
        report.note(cell.judge, "off_protocol_rows", off_protocol)
    if reply.error:
        report.note(cell.judge, "unreadable_replies")
    return (f"{cell.subject_id}-c{cell.checkpoint} {cell.author}->{cell.judge}: "
            f"{len(reply.entries)} graded, {len(reply.unparsed)} unparsed, "
            f"{len(reply.missing)} missing" + (f", ERROR {reply.error}" if reply.error else ""))


def run(
    bundles: Sequence[Bundle], store: GradingStore, invoke: Invoker,
    *, pool: Sequence[str] = DEFAULT_POOL, workers: int = DEFAULT_WORKERS,
    attempts: int = DEFAULT_ATTEMPTS, on_result: Callable[[str], None] | None = None,
    grader: Grader,
) -> RunReport:
    """Grade every cell. Parallel across calls — grading has no §6.0 ordering constraint.

    ``grader`` has no default — see `grade_cell`.
    """
    by_bundle = {(b.subject_id, b.author): b for b in bundles}
    cells = plan(bundles, pool=pool)
    report = RunReport(pool=tuple(pool))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [
            executor.submit(
                grade_cell, cell, by_bundle[(cell.subject_id, cell.author)], store, invoke,
                attempts=attempts, report=report, grader=grader,
            )
            for cell in cells
        ]
        for future in futures:
            message = future.result()
            if on_result:
                on_result(message)
    return report


def format_report(report: RunReport) -> str:
    """The run manifest, printed and written beside the grades.

    Per-judge rather than pooled, and unreadable replies get their own column: an unparsed reply
    would default to `unevidenced` in a harness that defaulted, which is the one band where an
    SUT that commits is charged with fabrication — so a formatting habit would become a penalty,
    distributed non-uniformly across judge models. This runner never defaults; the column is
    here so the rate is visible rather than merely harmless.
    """
    lines = [
        "§7.3 grading run — the judge pool is recorded per map and published", "",
        f"pool: {', '.join(report.pool)}",
        f"graded {report.graded}, cached {report.cached}, "
        f"skipped {len(report.skipped)}, failed {len(report.failed)}",
        "",
        f"{'judge':28s} {'graded':>7s} {'calls':>6s} {'cached':>7s} {'unparsed':>9s} "
        f"{'missing':>8s} {'off-proto':>10s} {'unreadable':>11s} {'retries':>8s}",
    ]
    for judge, counts in sorted(report.per_judge.items()):
        lines.append(
            f"{judge:28s} {counts['graded']:>7d} {counts['calls']:>6d} "
            f"{counts['cached']:>7d} "
            f"{counts['unparsed_rows']:>9d} {counts['missing_fields']:>8d} "
            f"{counts['off_protocol_rows']:>10d} "
            f"{counts['unreadable_replies']:>11d} {counts['transport_retry']:>8d}"
        )
    if report.failed:
        lines += ["", "transport failures (no answer obtained — these cells have no record):"]
        lines += [f"  {message}" for message in report.failed[:20]]
    if report.skipped:
        lines += ["", "skipped (a session was missing, so the prefix had a hole):"]
        lines += [f"  {message}" for message in report.skipped[:20]]
    lines += [
        "",
        "An unreadable reply is recorded as a wrong answer and never re-rolled: retrying it "
        "would",
        "repair the disposition §7.3 exists to measure and bias the pool toward whichever "
        "model",
        "complies on the second attempt. Transport failures are retried and leave no record.",
    ]
    return "\n".join(lines)


# --- CLI ---------------------------------------------------------------------


def _load_bundles(root: Path) -> list[Bundle]:
    records = GradingStore(root).records()          # same two exclusions; see `records`
    if not records:
        raise SystemExit(f"no authored session records under {root}")
    return Bundle.from_records(records)


def main(argv: Sequence[str] | None = None) -> int:      # pragma: no cover - CLI
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bundles", type=Path, required=True,
                        help="directory of authored session records")
    parser.add_argument("--out", type=Path, required=True,
                        help="directory for grading records")
    parser.add_argument("--pool", nargs="+", default=list(DEFAULT_POOL),
                        help="the §7.3 judge pool; needs 2 non-author models per bundle")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS,
                        help="transport attempts per call; content is never retried")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--grader", choices=sorted(GRADERS), required=True,
                        help="which §7.3 question to ask; `ruleout-split` is §7.5's shipping "
                             "boundary protocol and costs 2 calls per cell. No default: an --out "
                             "directory (e.g. work/graded/) may already hold another "
                             "grader's records, and an unnamed grader has spent real calls "
                             "overwriting them with a different question's answer before")
    parser.add_argument("--plan", action="store_true",
                        help="print what the run would cost and exit without spending it")
    parser.add_argument("--subjects", nargs="+", default=None,
                        help="grade only these subjects. The gate is per tier (§7.4, §7.5), "
                             "so a run staged by tier yields a complete, readable verdict for "
                             "each one as it lands instead of a partial map of all three.")
    args = parser.parse_args(argv)

    bundles = _load_bundles(args.bundles)
    if args.subjects:
        wanted = set(args.subjects)
        bundles = [b for b in bundles if b.subject_id in wanted]
        if not bundles:
            raise SystemExit(f"no bundles among {sorted(wanted)}")
    cells = plan(bundles, pool=args.pool)
    store = GradingStore(args.out)

    print(f"{len(bundles)} bundles, {len(cells)} grading calls, pool {', '.join(args.pool)}"
          f", grader {args.grader}")
    for bundle in bundles:
        print(f"  {bundle.subject_id} by {bundle.author}: "
              f"{len(bundle.checkpoints)} checkpoints -> {assign(args.pool, bundle.author)}")
    if args.plan:
        fresh = sum(
            1 for cell in cells
            if store.load(cell.judge, cell.author, cell.subject_id, cell.checkpoint) is None
        )
        print(f"\n{fresh} of {len(cells)} have no record on disk at all.")
        print("Cells whose cached record was produced by a different prompt are re-graded "
              "too;\nthat is only knowable per call, so this is a lower bound on the spend.")
        return 0

    report = run(
        bundles, store, Router(timeout=args.timeout), pool=args.pool,
        workers=args.workers, attempts=args.attempts, on_result=lambda m: print(m),
        grader=GRADERS[args.grader],
    )
    manifest = args.out / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(report.to_json(), indent=2))
    print()
    print(format_report(report))
    return 1 if report.failed else 0


if __name__ == "__main__":                               # pragma: no cover - CLI
    sys.exit(main())
