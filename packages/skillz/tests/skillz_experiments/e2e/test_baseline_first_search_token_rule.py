"""A saturated family does not stop a first `--search --objective tokens` call when the contract sets `min_token_saving`."""
from __future__ import annotations

from pathlib import Path

import pytest
from baseline_support import check_first_approval_search


@pytest.mark.ac("gh-756:AC-12")
@pytest.mark.ac("gh-756:AC-15")
@pytest.mark.ac("gh-756:AC-16")
@pytest.mark.ac("gh-757:AC-3")
def test_first_search_call_with_token_rule_stops_at_the_search_budget(tmp_path: Path) -> None:
    check_first_approval_search(tmp_path, saturated=True, token_rule=True, expected="budget-unapproved", objective="tokens")