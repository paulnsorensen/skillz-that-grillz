"""Fixtures shared by the inspector tests and the experiment tests."""
from __future__ import annotations

import importlib
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
