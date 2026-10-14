"""Fixtures shared by the inspector tests and the experiment tests."""
from __future__ import annotations

import importlib
import os
import re
from types import ModuleType
from typing import cast

import pytest

AC_MARKER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*:[A-Z]+-[A-Z]*[0-9]+")


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