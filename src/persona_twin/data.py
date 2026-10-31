"""Where the corpus and the served transcripts come from.

This repository ships **no data**. The published corpus lives on HuggingFace as
`tangents/dossierbench`; everything here resolves through this module so that a path is
never written down twice and a base bump changes one thing.

    from persona_twin import data

    data.corpus()             # -> Path to the credited answer key (the default gold)
    data.corpus("map")        # -> Path to the model-free, schedule-only gold
    data.transcripts()        # -> Path to the directory of 612 served sessions
    data.work()               # -> Path to the scratch directory runs write into

**Two ways to point at the data**, in this order:

1. `DOSSIERBENCH_DATA=/path/to/local/copy` — any directory laid out like the release
   (`gold/`, `transcripts/`). Use this when you built a release locally with
   `scripts/build_release.py`, or when you are working offline.
2. Otherwise the release is fetched from HuggingFace on first use and cached by
   `huggingface_hub` in the usual place (`HF_HOME`, else `~/.cache/huggingface`).
   Requires the `data` extra: `pip install -e '.[data]'`.

**What the published transcripts do and do not carry.** The release trims each session
record to `{task_id, subject_id, session, checkpoint, date, is_distractor, removable,
transcript}` — everything a submission is served, and nothing more. That is the whole
scoring side.

It is **not** enough for the curation side. `verify`, `credit` and `editor` read
`self_report`, `free_offers` and `instrument` off the store record; those are authoring-time
facts, they exist nowhere else, and they are not published. They also read the record's own
`beats` — not because beats are secret (the gold corpus carries the full schedule, beats and
turns included) but because `verify` compares the two copies to detect a store that has
drifted from the corpus it was authored against, and one copy cannot answer that.

So curation runs against a store **you authored yourself** (`python -m persona_twin.author`),
not against the download. `docs/PIPELINE.md` says which stage needs which.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

REPO_ID = "tangents/dossierbench"
REPO_TYPE = "dataset"

#: Environment variable naming a local copy of the release, bypassing the download.
DATA_ENV = "DOSSIERBENCH_DATA"
#: Environment variable naming the scratch directory that runs write into.
WORK_ENV = "DOSSIERBENCH_WORK"

GOLD_DIR = "gold"
TRANSCRIPT_DIR = "transcripts"


class DataUnavailable(RuntimeError):
    """The release could not be located, and the message says what to do about it."""


def _hub():
    try:
        import huggingface_hub
    except ImportError as exc:  # pragma: no cover - depends on the install extra
        raise DataUnavailable(
            f"The dataset is not in this repository and `huggingface_hub` is not installed, "
            f"so it cannot be fetched.\n"
            f"  pip install -e '.[data]'        # then re-run\n"
            f"  or set {DATA_ENV}=/path/to/a/local/release"
        ) from exc
    return huggingface_hub


def local_root() -> Path | None:
    """The local release `DOSSIERBENCH_DATA` names, or None if it is unset.

    Set, it must look like a release (`gold/` present) — a typo'd path is an error here rather
    than a silent fall-through to a 630 MB download nobody asked for.
    """
    raw = os.environ.get(DATA_ENV)
    if not raw:
        return None
    root = Path(raw).expanduser().resolve()
    if not (root / GOLD_DIR).is_dir():
        raise DataUnavailable(f"{DATA_ENV}={root} does not look like a release: no `{GOLD_DIR}/` in it.")
    return root


@lru_cache(maxsize=1)
def _remote_files() -> tuple[str, ...]:
    hub = _hub()
    try:
        return tuple(hub.list_repo_files(repo_id=REPO_ID, repo_type=REPO_TYPE))
    except Exception as exc:  # pragma: no cover - network
        raise DataUnavailable(
            f"Could not list `{REPO_ID}` on HuggingFace: {exc}\n"
            f"If you are offline, set {DATA_ENV} to a local copy of the release."
        ) from exc


def _split_golds(names: list[str]) -> tuple[str, str]:
    """(answer key, model-free map), resolved by shape rather than by a dated filename.

    Both are `gold/corpus-*.json`; the map is the one whose name carries `-map-`. Resolving
    this way means a base bump — which renames both files — costs nothing here or in any
    caller. It is the same rule `scripts/build_release.py` uses on the way out.
    """
    maps = [n for n in names if "-map-" in n]
    keys = [n for n in names if "-map-" not in n]
    if len(keys) != 1 or len(maps) != 1:
        raise DataUnavailable(
            f"Expected exactly one answer key and one map; found keys={keys} maps={maps}"
        )
    return keys[0], maps[0]


def _fetch(rel: str) -> Path:
    """One file from the release — local copy if there is one, else downloaded and cached."""
    root = local_root()
    if root is not None:
        path = root / rel
        if not path.exists():
            raise DataUnavailable(f"{DATA_ENV} release at {root} has no `{rel}`.")
        return path
    hub = _hub()
    try:
        return Path(hub.hf_hub_download(repo_id=REPO_ID, repo_type=REPO_TYPE, filename=rel))
    except Exception as exc:  # pragma: no cover - network
        raise DataUnavailable(f"Could not fetch `{rel}` from `{REPO_ID}`: {exc}") from exc


def corpus(which: str = "key") -> Path:
    """Path to a gold corpus. Fetches ~2.7 MB, not the whole release.

    `which="key"` (default) is the **credited answer key**: schedule-derived grades plus the
    reader-credit pass. `which="map"` is the **model-free map**: grades from the generation
    schedule alone, no LLM in the loop — more conservative and fully reproducible. They
    diverge on the 28 of 50 subjects that carry a reader-credited cell. Score against the key
    for the headline number and against the map for a model-independent check.
    """
    if which not in ("key", "map"):
        raise ValueError(f"which must be 'key' or 'map', not {which!r}")
    root = local_root()
    if root is not None:
        names = sorted(p.name for p in (root / GOLD_DIR).glob("corpus-*.json"))
    else:
        prefix = f"{GOLD_DIR}/corpus-"
        names = sorted(n.split("/", 1)[1] for n in _remote_files() if n.startswith(prefix))
    key, model_free = _split_golds(names)
    return _fetch(f"{GOLD_DIR}/{key if which == 'key' else model_free}")


def fields_json() -> Path:
    """Path to `gold/fields.json` — the 24-field registry as shipped."""
    return _fetch(f"{GOLD_DIR}/fields.json")


def transcripts() -> Path:
    """Path to the directory of served sessions, one `gen-*.json` per task id.

    This is the expensive one: without a local release it pulls the whole `transcripts/` tree
    (~9.4 MB of dialogue, though the file count is large). `corpus()` deliberately does not,
    so a gold-only check never pays for it.
    """
    root = local_root()
    if root is None:
        hub = _hub()
        try:
            snap = Path(hub.snapshot_download(
                repo_id=REPO_ID, repo_type=REPO_TYPE,
                allow_patterns=[f"{TRANSCRIPT_DIR}/*"],
            ))
        except Exception as exc:  # pragma: no cover - network
            raise DataUnavailable(
                f"Could not fetch `{TRANSCRIPT_DIR}/` from `{REPO_ID}`: {exc}\n"
                f"If you are offline, set {DATA_ENV} to a local copy of the release."
            ) from exc
        root = snap
    out = root / TRANSCRIPT_DIR
    if not out.is_dir():
        raise DataUnavailable(f"No `{TRANSCRIPT_DIR}/` in {root}")
    return out


def work() -> Path:
    """The scratch directory runs write into — `DOSSIERBENCH_WORK`, else `./work`.

    Created on demand. Nothing in this repository writes outside it, so a working tree stays
    clean and `.gitignore` has one entry rather than a growing list.
    """
    root = Path(os.environ.get(WORK_ENV, "work")).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


#: The author the shipped transcripts were written by, and the default for a new pass.
AUTHOR_MODEL = "claude-opus-5"


def authored_store(model: str = AUTHOR_MODEL) -> Path:
    """Where `python -m persona_twin.author --out work/authored` puts a store it wrote.

    This is the default for every curation script, and it is deliberately **not**
    `transcripts()`. The published sessions are trimmed to what a submission is served; the
    curation pipeline reads `beats`, `turns`, `instrument` and `self_report`, which are
    generation artifacts and are not published. Curation therefore runs on a store you
    authored, and pointing it at the download fails on a missing key rather than silently
    reading nothing. See `docs/PIPELINE.md`.
    """
    return work() / "authored" / model


def local_corpus(name: str = "corpus.json") -> Path:
    """A corpus this repository generated, under the work directory."""
    return work() / name
