"""gh-651: resume only the missing measured seed slots of holdout regression cases."""
# pyright: reportAny=false
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments.harness import EnvironmentDiffers
from skillz_experiments.run import run

from _reproduction_runs import Approver, Drafter, Maker, measure, record, reproduction, seed_zero, splits, start
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
        _ = measure(state, target, out, approved, calls)
    assert "headroom" not in record(out)
    holdout = splits(out, "holdout")
    done = [item for item in record(out)["outcomes"] if item.get("arm") == "baseline" and item["split"] == "holdout"]
    assert 0 < len(done) < 2 * len(holdout)
    before = state.holdout_calls
    state.holdout_fault = lambda _number: None
    assert run(target, out, MODEL, live=True, factory=state.factory)["search"] == "not-run"
    again = [(item["case_id"], item["repeat"]) for item in record(out)["outcomes"]
             if item.get("arm") == "baseline" and item["split"] == "holdout" and "score" in item]
    assert Counter(again) == Counter((case, repeat) for case in holdout for repeat in (0, 1))
    assert state.holdout_calls - before == 2 * len(holdout) - len(done)


@pytest.mark.ac("gh-651:AC-4")
def test_a_failed_holdout_reproduction_stops_before_headroom_and_a_resume_scores_only_that_case(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, approved, calls = start(tmp_path, make_target, write_draft, approvals)
    seed_zero(state)
    state.holdout_fault = lambda number: RuntimeError("provider fell over") if number == 1 else None
    with pytest.raises(CodedError) as caught:
        _ = measure(state, target, out, approved, calls)
    assert caught.value.code == "baseline-faulted" and "1 seed evaluation" in str(caught.value)
    assert "headroom" not in record(out) and record(out)["failure_code"] == "baseline-faulted"
    holdout = splits(out, "holdout")
    assert state.holdout_calls == 2 * len(holdout)
    state.holdout_fault = lambda _number: None
    assert run(target, out, MODEL, live=True, factory=state.factory)["search"] == "not-run"
    assert state.holdout_calls == 2 * len(holdout) + 1
    assert all(item["reproduces"] is True for name, item in reproduction(out).items() if name in holdout)
