"""Fixtures shared by the inspector tests and the experiment tests."""
from __future__ import annotations

import importlib
import os
import re
from types import ModuleType
from typing import cast

import pytest
from hypothesis import strategies as st

AC_MARKER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*:[A-Z]+-[A-Z]*[0-9]+")


@pytest.hookimpl(trylast=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    """Load the Unicode tables of Hypothesis in each test process before the first test starts.

    The first `st.text` or `st.characters` strategy of a process loads the Unicode category table and the UTF-8 codec
    table from `.hypothesis`. It builds each table that has no cached copy. A CI checkout has no cached copy, so each
    xdist worker built both tables inside its first test that draws text, and the test-time ratchet charged that
    build to the test. Hypothesis already collects its local constants before the first test for the same reason.
    The hook runs after the Hypothesis plugin ends its initialization, so Hypothesis does not warn about slow plugin
    code.
    """
    del session
    st.characters(codec="utf-8").validate()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Fail collection on a malformed ``ac`` marker, then skip each `live` test unless `SKILLZ_LIVE=1`.

    An ``ac`` marker must name its source as ``<source>:<id>``. The live skip keeps an ordinary pytest run free of
    model calls.
    """
    del config
    for item in items:
        for marker in item.iter_markers(name="ac"):
            argument = marker.args[0] if len(marker.args) == 1 and not marker.kwargs else None
            if not isinstance(argument, str) or not AC_MARKER.fullmatch(argument):
                form = "one string in the form <source>:<id>, for example gh-267:AC-1"
                raise pytest.UsageError(
                    f"{item.nodeid}: ac marker {marker.args!r} must be {form} (see docs/agents/criteria/index.md)"
                )
    if os.environ.get("SKILLZ_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set SKILLZ_LIVE=1 and use `just live-smoke` to make real model calls")
    for item in items:
        if item.get_closest_marker("live") is not None:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def inspector() -> ModuleType:
    """Import the `skillz_inspect` module once, so a broken inspector fails tests instead of collection."""
    return importlib.import_module("skillz_inspect._inspect")


@pytest.fixture(scope="session")
def limits(inspector: ModuleType) -> tuple[int, int]:
    """Return the inspector's `(ADVISORY_WORDS, MAX_WORDS)` sentence limits."""
    return cast(int, getattr(inspector, "ADVISORY_WORDS")), cast(int, getattr(inspector, "MAX_WORDS"))