"""A first `--search` call on a fresh run pays only for the baseline, then asks for the search budget."""
from __future__ import annotations

from pathlib import Path

import pytest
from baseline_support import check_first_approval_search


@pytest.mark.ac("gh-756:AC-12")
@pytest.mark.ac("gh-756:AC-15")
def test_first_search_call_stops_at_the_search_budget(tmp_path: Path) -> None:
    check_first_approval_search(tmp_path, saturated=False, token_rule=False, expected="budget-unapproved")
