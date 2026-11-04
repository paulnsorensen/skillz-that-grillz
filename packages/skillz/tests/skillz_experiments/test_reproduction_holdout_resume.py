"""gh-651: a resume scores a holdout regression case under the reproduce arm only when no outcome holds it yet.

`test_reproduction_holdout.py` holds the first-run tests, so that each file stays under the test-time budget.
"""
# pyright: reportAny=false
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments.harness import EnvironmentDiffers
from skillz_experiments.run import run
from skillz_experiments.run._reproduce import REPRODUCE_ARM

from _reproduction_runs import Approver, Drafter, Maker, go, record, reproduction, seed_zero, splits, start
from test_run_search import MODEL

pytestmark = pytest.mark.usefixtures("host_login")


@pytest.mark.ac("gh-651:AC-4")
def test_a_resume_does_not_score_a_holdout_regression_case_again(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, approved, calls = start(tmp_path, make_target, write_draft, approvals)
    seed_zero(state)
    state.holdout_fault = lambda number: (
        EnvironmentDiffers("runtime environment differs from the frozen record") if number == 4 else None)
    with pytest.raises(EnvironmentDiffers):
        _ = go(state, target, out, approved, calls)
    assert "headroom" not in record(out)
    done = [item for item in record(out)["outcomes"] if item.get("arm") == REPRODUCE_ARM]
    assert 0 < len(done) < len(splits(out, "holdout"))
    state.holdout_fault = lambda _number: None
    _ = run(target, out, MODEL, live=True, factory=state.factory)
    again = Counter(item["case_id"] for item in record(out)["outcomes"] if item.get("arm") == REPRODUCE_ARM)
    assert again == Counter(dict.fromkeys(splits(out, "holdout"), 1))


@pytest.mark.ac("gh-651:AC-4")
def test_a_failed_holdout_reproduction_stops_before_headroom_and_a_resume_scores_only_that_case(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, approved, calls = start(tmp_path, make_target, write_draft, approvals)
    seed_zero(state)
    state.holdout_fault = lambda number: RuntimeError("provider fell over") if number == 1 else None
    with pytest.raises(CodedError) as caught:
        _ = go(state, target, out, approved, calls)
    assert caught.value.code == "holdout-faulted" and "holdout regression" in str(caught.value)
    assert "headroom" not in record(out) and record(out)["failure_code"] == "holdout-faulted"
    holdout = splits(out, "holdout")
    first = Counter(item["case_id"] for item in record(out)["outcomes"] if item.get("arm") == REPRODUCE_ARM)
    assert first == Counter(dict.fromkeys(holdout, 1))
    state.holdout_fault = lambda _number: None
    _ = run(target, out, MODEL, live=True, factory=state.factory)
    again = Counter(item["case_id"] for item in record(out)["outcomes"] if item.get("arm") == REPRODUCE_ARM)
    assert sum(again.values()) == len(holdout) + 1 and set(again) == set(holdout)
    assert all(item["reproduces"] is True for name, item in reproduction(out).items() if name in holdout)
