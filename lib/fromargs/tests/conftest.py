"""Shared fixtures for the fromargs suite."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import cast

import pytest

JsonLine = Callable[[str], dict[str, object]]


def _single_json_line(err: str) -> dict[str, object]:
    lines = err.splitlines()
    assert len(lines) == 1, err
    envelope = cast("dict[str, object]", json.loads(lines[0]))
    assert set(envelope) == {"error", "exit_code"}
    return envelope


@pytest.fixture
def single_json_line() -> JsonLine:
    """Checker: ``err`` is exactly one ADR-001 JSON error envelope; returns it."""
    return _single_json_line
