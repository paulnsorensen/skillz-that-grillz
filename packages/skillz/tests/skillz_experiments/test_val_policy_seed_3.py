"""Seed 3 of the five-seed validation policy study (#245, experiments.md). One seed per file fits the time ceiling."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from test_val_policy import measure_seed


@pytest.mark.ac("gh-245:AC-4")
def test_five_seed_policy_measurement_seed_3(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    assert measure_seed(3, tmp_path, make_target, write_draft, approvals) == (95, 68)
