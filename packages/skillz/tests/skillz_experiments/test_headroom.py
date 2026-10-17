"""The headroom pass: score the seed once on the train and validation cases, then stop when a family is too easy."""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from skillz_experiments._budget import APPROVED_CALLS, MAX_CONCURRENT_CALLS, BudgetExhausted
from skillz_experiments._cases import CodedError
from skillz_experiments._records import read, write
from skillz_experiments.harness import EnvironmentDiffers
from skillz_experiments.run import Stop, _session, run
from skillz_experiments.run._plan import HOLDOUT_RETRIES_PER_ARM, _plan  # pyright: ignore[reportPrivateUsage]
from skillz_experiments.search import overshoot

from test_run_search import MODEL, Search, _start  # pyright: ignore[reportPrivateUsage]

pytestmark = pytest.mark.usefixtures("host_login")

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]


def _cases(out: Path, *splits: str) -> list[dict[str, object]]:
    return [case for case in cast(list[dict[str, object]], read(out / "cases.json")["cases"]) if case["split"] in splits]


def _record(out: Path) -> dict[str, object]:
    return read(out / "run.json")


def _headroom(out: Path) -> dict[str, Any]:  # pyright: ignore[reportExplicitAny]
    return cast(dict[str, Any], _record(out)["headroom"])  # pyright: ignore[reportExplicitAny]


def _means(out: Path) -> dict[str, float]:
    families = cast(dict[str, dict[str, object]], _headroom(out)["families"])
    return {name: cast(float, item["mean"]) for name, item in families.items()}


def _seed_identity(state: Search) -> str:
    return state.search_log[0][0]


def _saturate_train(state: Search) -> None:
    """Score the seed 1.0 on every train case. Every improved text keeps the default score."""
    state.score_of = lambda text, split: 1.0 if split == "train" and "improved" not in text else None


def _go(state: Search, target: Path, out: Path, case_hash: str, calls: int,
        statistics: Mapping[str, float] | None = None) -> dict[str, object]:
    return run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory,
               statistics=statistics)


def _grants(monkeypatch: pytest.MonkeyPatch, out: Path) -> list[tuple[int, bool]]:
    """Record the metric calls of each engine start, and whether the record held the headroom pass by then."""
    grants: list[tuple[int, bool]] = []
    real = _session.pareto_search  # pyright: ignore[reportPrivateLocalImportUsage]

    def spy(*args: Any, **kwargs: Any) -> Any:  # pyright: ignore[reportAny, reportExplicitAny]
        grants.append((cast(int, kwargs["metric_calls"]), "headroom" in _record(out)))
        return real(*args, **kwargs)  # pyright: ignore[reportAny]

    monkeypatch.setattr(_session, "pareto_search", spy)
    return grants


@pytest.mark.ac("autoimprove-authoring-headroom:AC-1")
def test_the_headroom_pass_scores_the_seed_once_on_each_case_before_the_engine_and_gepa_adds_no_seed_call(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    grants = _grants(monkeypatch, out)
    result = _go(state, target, out, case_hash, calls)
    assert result["phase"] == "complete"
    seed = _seed_identity(state)
    scored = [(seed, str(case["split"]), str(case["id"])) for case in _cases(out, "train", "validation")]
    log = state.search_log
    # Each seed case has one call in all. GEPA scores the seed validation pass from the recorded outcomes.
    assert Counter(entry for entry in log if entry[0] == seed) == Counter(scored)
    assert set(Counter(scored).values()) == {1}
    # The pass runs first: the engine starts after every seed call, with the headroom in the record.
    assert {entry[0] for entry in log[:len(scored)]} == {seed}
    assert len(grants) == 1 and grants[0][1]
    assert any(entry[0] != seed for entry in log[len(scored):])
    # The engine counts the recorded seed outcomes as spent calls, so its grant keeps the full search ceiling.
    # Without the reuse queue the grant drops by the recorded count and the engine scores the seed again as new work.
    estimate = cast(dict[str, Any], _record(out)["estimate"])  # pyright: ignore[reportExplicitAny]
    train, validation = len(_cases(out, "train")), len(_cases(out, "validation"))
    assert grants[0][0] == estimate["metric_calls"] + train - overshoot(validation)


@pytest.mark.ac("autoimprove-authoring-headroom:AC-2")
def test_a_saturated_train_family_stops_with_cases_too_easy_and_makes_no_search_call(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    _saturate_train(state)
    grants = _grants(monkeypatch, out)
    with pytest.raises(CodedError) as caught:
        _ = _go(state, target, out, case_hash, calls)
    assert caught.value.code == "cases-too-easy"
    message = str(caught.value)
    assert "(train) mean 1.00" in message and "(validation)" not in message and "new run directory" in message
    headroom = _headroom(out)
    assert headroom["verdict"] == "too-easy" and headroom["ceiling"] == 0.9
    train = {str(case["family"]) for case in _cases(out, "train")}
    saturated = {name for name, mean in _means(out).items() if mean == 1.0}
    assert saturated == train and all(headroom["families"][name]["split"] == "train" for name in train)
    assert all(name in message for name in train)
    # Only the headroom pass ran: no engine, no proposal, no reflection call, no non-seed evaluation.
    assert grants == [] and state.proposals == 0 and state.invokes == 0
    assert {entry[0] for entry in state.search_log} == {_seed_identity(state)}
    assert _record(out)["phase"] == "prepared"


@pytest.mark.ac("autoimprove-authoring-headroom:AC-3")
def test_a_run_with_headroom_continues_to_the_search_with_the_planned_metric_calls_and_the_train_outcomes(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    grants = _grants(monkeypatch, out)
    _ = _go(state, target, out, case_hash, calls)
    headroom = _headroom(out)
    assert headroom["verdict"] == "ok"
    assert all(mean < 0.9 for mean in _means(out).values())
    planned = cast(dict[str, Any], _record(out)["estimate"])  # pyright: ignore[reportExplicitAny]
    train = len(_cases(out, "train"))
    validation = len(_cases(out, "validation"))
    # The ceiling adds the train outcomes only: the validation outcomes replace the seed pass inside `metric_calls`.
    assert train != validation
    assert grants == [(planned["metric_calls"] + train - overshoot(validation), True)]


@pytest.mark.ac("autoimprove-authoring-headroom:AC-4")
def test_a_run_with_min_token_saving_records_saturated_scores_and_does_not_stop(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    statistics = {"min_token_saving": 0.3, "max_quality_loss": 0.5}
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out, statistics=statistics)
    state = Search(target)
    _saturate_train(state)
    result = _go(state, target, out, case_hash, calls, statistics)
    assert result["phase"] == "complete"
    headroom = _headroom(out)
    assert headroom["verdict"] == "ok"
    train = {str(case["family"]) for case in _cases(out, "train")}
    assert {name for name, mean in _means(out).items() if mean == 1.0} == train


@pytest.mark.ac("autoimprove-authoring-headroom:AC-5")
def test_the_plan_charges_the_headroom_at_the_train_case_cost_on_its_own_line(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    plan = _plan(3, 5, 6, 2, 3, headroom_costs=[2, 4, 6])
    without = _plan(3, 5, 6, 2, 3, headroom_costs=[])
    assert (plan.headroom_calls, plan.headroom_outcomes) == (12, 3)
    assert plan.sized.calls == without.sized.calls + 12
    default = _plan(3, 5, 6, 2, 3)
    assert (default.headroom_calls, default.headroom_outcomes) == (6, 3)
    assert default.sized.calls == without.sized.calls + 6
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    with pytest.raises(Stop) as asked:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash)
    planned = cast(dict[str, Any], asked.value.data["estimate"])  # pyright: ignore[reportExplicitAny]
    train = _cases(out, "train")
    assert planned["headroom"] == {"calls": sum(state.contract.calls(str(case["kind"])) for case in train),
                                   "outcomes": len(train)}
    assert planned["calls"] == calls


@pytest.mark.ac("autoimprove-authoring-headroom:AC-12")
def test_a_resume_after_the_headroom_pass_scores_no_seed_case_again(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    fault_at = len(_cases(out, "train", "validation")) + 3
    state.evaluate_fault = lambda number: (
        EnvironmentDiffers("runtime environment differs from the frozen record") if number == fault_at else None)
    with pytest.raises(EnvironmentDiffers):
        _ = _go(state, target, out, case_hash, calls)
    held = _headroom(out)
    seed = _seed_identity(state)
    state.search_log.clear()
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert result["phase"] == "complete"
    assert _headroom(out) == held
    assert state.search_log and all(entry[0] != seed for entry in state.search_log)


@pytest.mark.ac("autoimprove-authoring-headroom:AC-12")
def test_a_resume_after_a_cut_headroom_pass_scores_only_the_cases_that_the_record_lacks(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda number: (
        EnvironmentDiffers("runtime environment differs from the frozen record") if number == 3 else None)
    with pytest.raises(EnvironmentDiffers):
        _ = _go(state, target, out, case_hash, calls)
    assert "headroom" not in _record(out)
    seed = _seed_identity(state)
    held = {(str(item["split"]), str(item["case_id"])) for item in cast(list[dict[str, object]], _record(out)["outcomes"])
            if item.get("search_candidate") == seed and item.get("status") != "evaluation-failed"}
    assert held
    state.search_log.clear()
    _ = run(target, out, MODEL, live=True, factory=state.factory)
    again = {(split, case) for identity, split, case in state.search_log if identity == seed}
    assert not again & held
    scored = {(str(case["split"]), str(case["id"])) for case in _cases(out, "train", "validation")}
    assert again | held == scored


@pytest.mark.ac("autoimprove-authoring-headroom:AC-12")
def test_a_resume_of_a_too_easy_run_returns_the_same_stop_with_no_session_and_no_model_call(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    _saturate_train(state)
    with pytest.raises(CodedError) as first:
        _ = _go(state, target, out, case_hash, calls)
    assert first.value.code == "cases-too-easy"
    searched = len(state.search_log)
    built: list[object] = []

    def factory(*args: Any) -> Any:  # pyright: ignore[reportAny, reportExplicitAny]
        built.append(args)
        return state.factory(*args)  # pyright: ignore[reportAny]

    with pytest.raises(CodedError) as again:
        _ = run(target, out, MODEL, live=True, factory=factory)
    assert (again.value.code, str(again.value)) == (first.value.code, str(first.value))
    assert built == [] and len(state.search_log) == searched and state.invokes == 0


@pytest.mark.ac("autoimprove-authoring-headroom:AC-12")
def test_a_resume_of_a_too_easy_run_that_died_before_it_recorded_the_failure_returns_the_same_stop(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    _saturate_train(state)
    with pytest.raises(CodedError) as first:
        _ = _go(state, target, out, case_hash, calls)
    record = _record(out)
    # The process died after the headroom checkpoint and before `record_failure`.
    _ = record.pop("failure")
    _ = record.pop("failure_code")
    write(out / "run.json", record)
    with pytest.raises(CodedError) as again:
        _ = run(target, out, MODEL, live=True, factory=state.factory)
    assert (again.value.code, str(again.value)) == ("cases-too-easy", str(first.value))


@pytest.mark.ac("autoimprove-authoring-headroom:AC-12")
def test_a_resume_after_a_faulted_headroom_pass_does_not_count_the_stale_failures_again(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    total = len(_cases(out, "train", "validation"))
    state.evaluate_fault = lambda number: RuntimeError("provider outage") if number <= total else None
    with pytest.raises(CodedError) as caught:
        _ = _go(state, target, out, case_hash, calls)
    assert caught.value.code == "search-faulted" and "headroom" not in _record(out)
    # One new failure on the resume: with the stale rows counted it makes a majority, without them it does not.
    state.evaluate_fault = lambda number: RuntimeError("provider outage") if number == total + 2 else None
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert result["phase"] == "complete" and "headroom" in _record(out)


@pytest.mark.ac("autoimprove-authoring-headroom:AC-3")
def test_a_budget_stop_during_the_headroom_pass_retains_the_seed_and_runs_the_gate(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda number: BudgetExhausted("global invocation budget exhausted") if number == 3 else None
    result = _go(state, target, out, case_hash, calls)
    search = cast(dict[str, Any], result["search"])  # pyright: ignore[reportExplicitAny]
    assert search["reason"] == "budget-exhausted-seed-retained" and search["retained_seed"] is True
    assert result["phase"] == "complete"
    assert cast(dict[str, Any], result["gate"])["reasons"] == ["winner-equals-baseline"]  # pyright: ignore[reportExplicitAny]
    assert all(entry[0] == _seed_identity(state) for entry in state.search_log)


@pytest.mark.ac("autoimprove-authoring-headroom:AC-5")
def test_the_sandbox_time_estimate_includes_the_headroom_evaluations() -> None:
    plan = _plan(3, 5, 6, 2, 3, 30, headroom_costs=[2, 2, 2])
    evaluations = plan.metric_calls + 3 + 2 * 6 * 3 + 2 * HOLDOUT_RETRIES_PER_ARM + 6
    assert plan.sandbox_total == math.ceil(evaluations * 30 / MAX_CONCURRENT_CALLS)


@pytest.mark.ac("autoimprove-authoring-headroom:AC-5")
def test_a_headroom_pass_that_pushes_the_fixed_cost_over_the_call_cap_names_calls_not_seconds() -> None:
    messages: list[str] = []
    for train in range(1, 2 * APPROVED_CALLS):
        try:
            _ = _plan(train, 1, 6, 1, 3)
        except Stop as stop:
            messages.append(str(stop))
    named = [message for message in messages if "headroom pass alone need" in message]
    assert named, messages[:1]
    assert all("calls; the cap is" in message and "seconds" not in message.split("cap is")[0] for message in named)
