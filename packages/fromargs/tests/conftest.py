"""Shared fixtures for the fromargs suite."""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
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


@pytest.fixture(autouse=True)
def _isolated_temp_root(tmp_path: Path) -> Iterator[None]:
    """Point the temp root at ``tmp_path`` so traceback files never reach the shared temp directory."""
    saved = tempfile.tempdir
    tempfile.tempdir = str(tmp_path)
    try:
        yield
    finally:
        tempfile.tempdir = saved


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
