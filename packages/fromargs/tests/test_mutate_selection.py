"""The mutation runner selects the same tests per criterion as before the source-qualified markers."""

from __future__ import annotations

import importlib.util
import re
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

SOURCE = "fromargs-cli-library"
MUTATE = Path(__file__).parent / "e2e" / "mutate.py"
# Collected test items per criterion in e2e/test_acceptance.py at the commit before the migration
# (each parametrized case counts once; measured with FROMARGS_AC_DUMP=1 pytest --collect-only).
TESTS_PER_CRITERION = {
    "AC-1": 2, "AC-2": 2, "AC-3": 1, "AC-4": 3, "AC-5": 3, "AC-6": 3, "AC-7": 2, "AC-8": 2, "AC-9": 1,
    "AC-10": 2, "AC-11": 5, "AC-12": 1, "AC-13": 2, "AC-14": 1, "AC-15": 1, "AC-16": 2, "AC-17": 1,
}


def _load_mutate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fromargs_mutate", MUTATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve string annotations through sys.modules
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[spec.name]
        raise
    return module


def _mutated_criteria() -> list[str]:
    """Return the ac_id of every mutation that edits a source file (not the n/a ones)."""
    blocks = MUTATE.read_text(encoding="utf-8").split("    Mutation(")[1:]
    ids = [re.search(r'ac_id="([^"]+)"', block) for block in blocks if "rel_file=" in block]
    return [found[1] for found in ids if found is not None]


@pytest.mark.ac("gh-267:AC-7")
def test_each_mutation_criterion_selects_the_pre_migration_tests() -> None:
    mutate = _load_mutate()
    collect = cast("Callable[[], dict[str, list[str]]]", getattr(mutate, "collect_node_ids"))
    selected = collect()
    assert {key: len(nodes) for key, nodes in selected.items()} == {
        f"{SOURCE}:{criterion}": count for criterion, count in TESTS_PER_CRITERION.items()
    }
    for ac_id in _mutated_criteria():
        assert selected.get(ac_id), ac_id
