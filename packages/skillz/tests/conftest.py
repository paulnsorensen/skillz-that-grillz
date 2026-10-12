"""Fixtures shared by the inspector tests and the experiment tests."""
from __future__ import annotations

import importlib
import os
from types import ModuleType
from typing import cast

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip each `live` test unless `SKILLZ_LIVE=1`, so an ordinary pytest run makes no model call."""
    del config
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
