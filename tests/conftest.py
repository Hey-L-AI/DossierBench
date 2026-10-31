"""Shared fixtures — chiefly, how a test gets at the published gold.

Most of this suite is hermetic: it builds toy corpora in memory and never touches the network.
A handful of tests are different in kind — they assert an invariant **of the shipped answer
key** (the oracle scores 1.000 on every subject, the gate accepts every `card_at`), which is
only checkable against real data. Those carry `@pytest.mark.data`.

`pytest` skips them by default (see `addopts` in `pyproject.toml`), because a fresh clone
running its test suite should not silently download a dataset. Run them with:

    pytest -m data                       # fetches ~2.7 MB of gold from HuggingFace
    DOSSIERBENCH_DATA=/path pytest -m data   # or against a local release, offline
"""
from __future__ import annotations

import json

import pytest

from persona_twin import data


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "data: needs the published corpus (network, or DOSSIERBENCH_DATA)"
    )


@pytest.fixture(scope="session")
def answer_key() -> dict:
    """The credited answer key, or a skip naming what would have made it available."""
    return _load("key")


@pytest.fixture(scope="session")
def model_free_map() -> dict:
    """The schedule-only gold — no reader model in it."""
    return _load("map")


def _load(which: str) -> dict:
    try:
        path = data.corpus(which)
    except data.DataUnavailable as exc:
        pytest.skip(f"published corpus unavailable: {exc}")
    return json.loads(path.read_text())
