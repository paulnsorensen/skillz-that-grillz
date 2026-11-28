"""A saturated family stops a first `--search` call with the quality objective, also when the contract sets `min_token_saving`."""
from __future__ import annotations

from pathlib import Path

import pytest
from baseline_support import check_first_approval_search


@pytest.mark.ac("gh-757:AC-12")
def test_first_search_call_with_token_rule_and_no_objective_stops_too_easy(tmp_path: Path) -> None:
    check_first_approval_search(tmp_path, saturated=True, token_rule=True, expected="cases-too-easy")
