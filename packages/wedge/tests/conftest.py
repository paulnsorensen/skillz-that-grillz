"""Collection hook shared by every wedge test."""

from __future__ import annotations

import re

import pytest


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
