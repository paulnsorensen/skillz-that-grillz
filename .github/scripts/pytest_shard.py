"""Pytest plugin: run one CI shard of the collected tests, as `GATE_SHARD=<index>/<count>` selects.

The shard keeps each test whose collection position modulo `count` equals `index - 1`.
The shards are disjoint, and together they run every test.
Adjacent tests in one slow module go to different shards.
"""
from __future__ import annotations

import os

import pytest
from run_gate import SHARD_VARIABLE, parse_shard


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Deselect the tests of the other shards. Run last, so that conftest checks see every collected test."""
    try:
        index, count = parse_shard(os.environ.get(SHARD_VARIABLE, "1/1"))
    except ValueError as error:
        raise pytest.UsageError(str(error)) from None
    kept = [item for position, item in enumerate(items) if position % count == index - 1]
    dropped = [item for position, item in enumerate(items) if position % count != index - 1]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept
