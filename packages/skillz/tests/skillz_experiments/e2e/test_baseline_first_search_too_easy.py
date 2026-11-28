"""A first `--search` call stops at `cases-too-easy` when a family is saturated, and refuses an older engine."""
from __future__ import annotations

from pathlib import Path

import pytest
from baseline_support import check_first_approval_search


@pytest.mark.ac("gh-756:AC-12")
@pytest.mark.ac("gh-756:AC-15")
@pytest.mark.ac("gh-756:AC-16")
@pytest.mark.ac("gh-756:AC-18")
def test_first_search_call_stops_when_the_cases_are_too_easy(tmp_path: Path) -> None:
    check_first_approval_search(tmp_path, saturated=True, token_rule=False, expected="cases-too-easy")
