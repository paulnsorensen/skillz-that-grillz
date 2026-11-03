"""gh-651: a full run scores each holdout regression case once under the reproduce arm."""
# pyright: reportAny=false
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments.harness import EnvironmentDiffers
from skillz_experiments.run import Stop, _session, run
from skillz_experiments.run._reproduce import REPRODUCE_ARM

from _reproduction_runs import Approver, Drafter, Maker, go, record, reproduction, seed_zero, splits, start
from test_run_search import MODEL

pytestmark = pytest.mark.usefixtures("host_login")


@pytest.mark.ac("gh-651:AC-4")
@pytest.mark.ac("gh-651:AC-9")
def test_each_holdout_regression_case_scores_once_under_the_reproduce_arm_before_the_engine_starts(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state, approved, calls = start(tmp_path, make_target, write_draft, approvals)
    seed_zero(state)
    before_engine: list[int] = []
    real = _session.pareto_search  # pyright: ignore[reportPrivateLocalImportUsage]

    def spy(*args: Any, **kwargs: Any) -> Any:  # pyright: ignore[reportExplicitAny]
        before_engine.append(state.holdout_calls)
        return real(*args, **kwargs)

    monkeypatch.setattr(_session, "pareto_search", spy)
    result = go(state, target, out, approved, calls)
    holdout = splits(out, "holdout")
    assert result["phase"] == "complete" and holdout
    assert before_engine == [len(holdout)]
    outcomes = cast(list[dict[str, Any]], record(out)["outcomes"])  # pyright: ignore[reportExplicitAny]
    reproduced = [item for item in outcomes if item.get("arm") == REPRODUCE_ARM]
    assert Counter(item["case_id"] for item in reproduced) == Counter(dict.fromkeys(holdout, 1))
    assert {item["split"] for item in reproduced} == {"holdout"} and not any("search_candidate" in item for item in reproduced)
    assert {name for name, item in reproduction(out).items() if item["split"] == "holdout"} == set(holdout)
    assert result["headroom"] == record(out)["headroom"]
    # The gate scores the holdout on its own arms: the reproduce arm adds nothing to them.
    assert [item for item in outcomes if item.get("split") == "holdout" and item.get("arm") != REPRODUCE_ARM]


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


@pytest.mark.ac("gh-651:AC-6")
def test_holdout_regression_cases_add_their_calls_to_the_approved_total_and_show_on_their_own_line(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    plain = start(tmp_path / "plain", make_target, write_draft, approvals, regression=lambda _case: False)
    marked = start(tmp_path / "marked", make_target, write_draft, approvals)
    estimates: list[Mapping[str, object]] = []
    for target, out, state, approved, _calls in (plain, marked):
        with pytest.raises(Stop) as budget:
            _ = run(target, out, MODEL, live=True, approve_cases=approved, factory=state.factory)
        estimates.append(cast(Mapping[str, object], budget.value.data["estimate"]))
    holdout = len(splits(marked[1], "holdout"))
    per_case = marked[2].contract.calls("echo")
    assert "reproduce" not in estimates[0]
    assert estimates[1]["reproduce"] == {"calls": holdout * per_case}
    assert cast(int, estimates[1]["calls"]) - cast(int, estimates[0]["calls"]) == holdout * per_case
    assert marked[4] - plain[4] == holdout * per_case
