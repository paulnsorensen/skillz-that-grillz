"""Fixtures shared by the inspector tests and the experiment tests."""
from __future__ import annotations

import importlib
import re
from types import ModuleType
from typing import cast

import pytest


@pytest.fixture(scope="session")
def inspector() -> ModuleType:
    """Import the `skillz_inspect` module once, so a broken inspector fails tests instead of collection."""
    return importlib.import_module("skillz_inspect._inspect")


@pytest.fixture(scope="session")
def limits(inspector: ModuleType) -> tuple[int, int]:
    """Return the inspector's `(ADVISORY_WORDS, MAX_WORDS)` sentence limits."""
    return cast(int, getattr(inspector, "ADVISORY_WORDS")), cast(int, getattr(inspector, "MAX_WORDS"))


AC_MARKER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*:[A-Z]+-[A-Z]*[0-9]+")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Fail collection when an ``ac`` marker does not name its source as ``<source>:<id>``."""
    for item in items:
        for marker in item.iter_markers(name="ac"):
            argument = marker.args[0] if len(marker.args) == 1 and not marker.kwargs else None
            if not isinstance(argument, str) or not AC_MARKER.fullmatch(argument):
                form = "one string in the form <source>:<id>, for example gh-267:AC-1"
                raise pytest.UsageError(
                    f"{item.nodeid}: ac marker {marker.args!r} must be {form} (see docs/agents/criteria/index.md)"
                )
