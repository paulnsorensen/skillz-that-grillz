"""Collection-time helper for ``mutate.py``.

``pytest -m`` cannot match a marker's *argument* (``@pytest.mark.ac("AC-3")``),
only its name. To let the mutation runner select, per acceptance criterion,
exactly the tests that must catch a broken build, this hook prints one
``nodeid<TAB>AC-n`` line per collected test to stdout during a
``--collect-only`` pass -- but only when ``FROMARGS_AC_DUMP`` is set, so a
normal test run stays silent.
"""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if not os.environ.get("FROMARGS_AC_DUMP"):
        return
    for item in items:
        for marker in item.iter_markers(name="ac"):
            if marker.args:
                print(f"{item.nodeid}\t{marker.args[0]}")
