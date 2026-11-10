"""A run approved above the standard call cap completes against the raised cap (gh-588).

This full search over 28 families is the slowest test of `test_budget_cap.py`, so it has its own file and its own
test-time budget.
"""
from __future__ import annotations

from typing import cast
from pathlib import Path

import pytest

from skillz_experiments._budget import APPROVED_CALLS, JOURNEY_SECONDS
from skillz_experiments._records import read
from skillz_experiments.run import run
from test_budget_cap import BIG, Drafter, Maker, _refused, _start  # pyright: ignore[reportPrivateUsage]
from test_run_search import MODEL, Search

pytestmark = pytest.mark.usefixtures("host_login", "cheap_record_writes")


@pytest.mark.ac("gh-588:AC-1")
def test_approve_budget_above_the_standard_cap_sizes_the_run_against_it(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, headroom_off: None) -> None:
    del headroom_off
    target, out, case_hash = _start(tmp_path, make_target, write_draft, BIG)
    smallest = cast(int, _refused(target, out, case_hash, repeats=3).data["smallest_approval"])
    state = Search(target)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, repeats=3, approve_budget=smallest,
                 factory=state.factory)
    assert result["phase"] == "complete"
    budget = cast(dict[str, object], read(out / "run.json")["budget"])
    assert budget["maximum"] == smallest > APPROVED_CALLS and budget["cap"] == smallest
    assert cast(int, budget["seconds"]) <= JOURNEY_SECONDS
