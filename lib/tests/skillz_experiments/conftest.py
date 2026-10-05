"""Shared fixtures for the skillz experiment tests."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

INSPECTOR = Path(__file__).resolve().parents[3] / "skills/skillz/scripts/inspect_skill.py"


@pytest.fixture(scope="session")
def inspector() -> ModuleType:
    """Load `inspect_skill.py` once, so a broken helper fails tests instead of collection."""
    spec = importlib.util.spec_from_file_location("inspect_skill", INSPECTOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def limits(inspector: ModuleType) -> tuple[int, int]:
    """Return the inspector's `(ADVISORY_WORDS, MAX_WORDS)` sentence limits."""
    return cast(int, getattr(inspector, "ADVISORY_WORDS")), cast(int, getattr(inspector, "MAX_WORDS"))
