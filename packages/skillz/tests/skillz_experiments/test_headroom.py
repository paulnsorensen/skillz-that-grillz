"""Measure the seed before search; reuse the measured scores and reject cases that are too easy."""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from skillz_experiments._budget import APPROVED_CALLS, MAX_CONCURRENT_CALLS, BudgetExhausted
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._records import read
from skillz_experiments.harness import EnvironmentDiffers
from skillz_experiments.run import Stop, _session, run
from skillz_experiments.run._plan import (
    HOLDOUT_RETRIES_PER_ARM, MINIMUM_SEARCH_MARGIN, PREFLIGHT_CALLS, VALIDATION_REPEATS,
)
from skillz_experiments.run._plan import _plan  # pyright: ignore[reportPrivateUsage]
from skillz_experiments.search import overshoot, search_allowance

from test_run_search import MODEL, Provider, Search, _start, run_search  # pyright: ignore[reportPrivateUsage]

pytestmark = pytest.mark.usefixtures("host_login")

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]


def _cases(out: Path, *splits: str) -> list[dict[str, object]]:
    return [case for case in cast(list[dict[str, object]], read(out / "cases.json")["cases"]) if case["split"] in splits]


def _seed_runs(out: Path) -> list[tuple[str, str]]:
    """Return the split and case of each measured train and validation slot."""
    return [(str(case["split"]), str(case["id"])) for case in _cases(out, "train", "validation") for _ in range(2)]


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
        statistics: Mapping[str, float] | None = None, objective: str | None = None) -> dict[str, object]:
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory,
                 statistics=statistics)
    assert result["search"] == "not-run"
    return run_search(target, out, state, case_hash, calls, objective=objective)


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
@pytest.mark.ac("gh-500:AC-10")
def test_the_measurement_scores_the_seed_twice_on_each_case_and_gepa_adds_no_seed_call(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    grants = _grants(monkeypatch, out)
    result = _go(state, target, out, case_hash, calls)
    assert result["phase"] == "complete"
    seed = _seed_identity(state)
    scored = [(seed, split, case) for split, case in _seed_runs(out)]
    log = state.search_log
    # GEPA reads its seed validation pass from the recorded outcomes.
    assert Counter(entry for entry in log if entry[0] == seed) == Counter(scored)
    assert {split: set(count for (_, name, _), count in Counter(scored).items() if name == split)
            for split in ("train", "validation")} == {"train": {2}, "validation": {2}}
    # The pass runs first: the engine starts after every seed call, with the headroom in the record.
    assert {entry[0] for entry in log[:len(scored)]} == {seed}
    assert len(grants) == 1 and grants[0][1]
    assert any(entry[0] != seed for entry in log[len(scored):])
    # The engine counts the recorded seed outcomes as spent calls, so its grant keeps the full search ceiling.
    # Without the reuse queue the grant drops by the recorded count and the engine scores the seed again as new work.
    estimate = cast(dict[str, Any], _record(out)["estimate"])  # pyright: ignore[reportExplicitAny]
    train, validation = len(_cases(out, "train")), len(_cases(out, "validation"))
    assert grants[0][0] == estimate["metric_calls"] + train - overshoot(VALIDATION_REPEATS * validation)


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
    assert grants == [(planned["metric_calls"] + train - overshoot(VALIDATION_REPEATS * validation), True)]


@pytest.mark.ac("autoimprove-authoring-headroom:AC-4")
@pytest.mark.ac("gh-757:AC-3")
def test_a_token_objective_run_records_saturated_scores_and_does_not_stop(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    statistics = {"min_token_saving": 0.3, "max_quality_loss": 0.5}
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out, statistics=statistics)
    state = Search(target)
    _saturate_train(state)
    result = _go(state, target, out, case_hash, calls, statistics, objective="tokens")
    assert result["phase"] == "complete" and _record(out)["objective"] == "tokens"
    # The block keeps the quality verdict (gh-757); the token objective skips that stop.
    headroom = _headroom(out)
    assert headroom["verdict"] == "too-easy"
    train = {str(case["family"]) for case in _cases(out, "train")}
    assert {name for name, mean in _means(out).items() if mean == 1.0} == train


@pytest.mark.ac("autoimprove-authoring-headroom:AC-5")
def test_the_plan_counts_one_headroom_outcome_per_train_case_and_charges_the_search_no_headroom_calls(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    plan = _plan(3, 2, 6, 2, 3)
    assert plan.headroom_outcomes == 3
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    baseline = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert baseline["search"] == "not-run"
    with pytest.raises(Stop) as asked:
        _ = run(target, out, MODEL, search=True)
    assert asked.value.code == "budget-unapproved"
    planned = cast(dict[str, Any], asked.value.data["estimate"])  # pyright: ignore[reportExplicitAny]
    train = _cases(out, "train")
    assert planned["headroom"] == {"outcomes": len(train)}
    estimate = cast(dict[str, int], _record(out)["estimate"])
    assert estimate["calls"] == calls and planned["reflection"]["calls"] > 0


@pytest.mark.ac("autoimprove-authoring-headroom:AC-12")
def test_a_resume_after_the_headroom_pass_scores_no_seed_case_again(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    baseline = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert baseline["search"] == "not-run"
    fault_at = state.search_calls + 3
    state.evaluate_fault = lambda number: (
        EnvironmentDiffers("runtime environment differs from the frozen record") if number == fault_at else None)
    with pytest.raises(EnvironmentDiffers):
        _ = run_search(target, out, state, case_hash, calls)
    held = _headroom(out)
    seed = _seed_identity(state)
    state.search_log.clear()
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert result["phase"] == "complete"
    assert _headroom(out) == held
    assert state.search_log and all(entry[0] != seed for entry in state.search_log)


@pytest.mark.ac("autoimprove-authoring-headroom:AC-12")
def test_a_resume_after_a_cut_headroom_pass_scores_only_the_cases_that_the_record_lacks(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    evaluate = Provider.evaluate
    refused = False

    def refuse_before_spend(provider: Provider, candidate: Candidate, case: Case, *, holdout: bool = False,
                            checks: object = None) -> dict[str, object]:
        nonlocal refused
        with state.lock:
            if state.search_calls and not refused:
                refused = True
                raise EnvironmentDiffers("runtime environment differs from the frozen record")
        return evaluate(provider, candidate, case, holdout=holdout, checks=checks)

    with monkeypatch.context() as patch:
        patch.setattr(Provider, "evaluate", refuse_before_spend)
        with pytest.raises(EnvironmentDiffers, match="runtime environment differs from the frozen record"):
            _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    partial = _record(out)
    assert "headroom" not in partial
    seed = cast(str, partial["seed_hash"])

    def slots(record: dict[str, object]) -> Counter[tuple[object, object, int | None]]:
        outcomes = cast(list[dict[str, object]], record["outcomes"])
        assert all(item["candidate_hash"] == seed and "score" in item for item in outcomes)
        return Counter(
            (item["split"], item["case_id"], cast(int | None, item.get("repeat"))) for item in outcomes)

    expected = Counter((case["split"], case["id"], repeat) for case in _cases(out, "train", "validation", "holdout")
                       for repeat in (None if case["split"] == "train" else 0, 1))
    held = slots(partial)
    assert held < expected and held
    assert partial["calls"] == PREFLIGHT_CALLS + held.total()
    missing = expected - held

    state.search_log.clear()
    holdout_calls = state.holdout_calls
    assert run(target, out, MODEL, live=True, factory=state.factory)["search"] == "not-run"
    assert Counter(state.search_log) == Counter(
        (seed, split, case) for split, case, _ in missing.elements() if split != "holdout")
    assert state.holdout_calls - holdout_calls == sum(split == "holdout" for split, _, _ in missing.elements())
    assert state.invokes == 0
    final = _record(out)
    assert slots(final) == expected
    assert final["calls"] == calls == cast(dict[str, int], final["budget"])["maximum"]


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
        _ = run(target, out, MODEL, search=True, live=True, factory=factory)
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
    assert "headroom" in record and "failure" not in record and "failure_code" not in record
    with pytest.raises(CodedError) as again:
        _ = run(target, out, MODEL, search=True, live=True, factory=state.factory)
    assert (again.value.code, str(again.value)) == ("cases-too-easy", str(first.value))


@pytest.mark.ac("autoimprove-authoring-headroom:AC-12")
def test_a_resume_after_a_faulted_headroom_pass_does_not_count_the_stale_failures_again(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.holdout_fault = lambda number: RuntimeError("provider outage") if number == 3 else None
    with pytest.raises(CodedError) as caught:
        _ = _go(state, target, out, case_hash, calls)
    assert caught.value.code == "baseline-faulted" and "headroom" not in _record(out)
    assert run(target, out, MODEL, live=True, factory=state.factory)["search"] == "not-run"
    assert "headroom" in _record(out) and state.invokes == 0


@pytest.mark.ac("autoimprove-authoring-headroom:AC-3")
def test_a_budget_stop_during_measurement_does_not_start_the_gate(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda number: BudgetExhausted("global invocation budget exhausted") if number == 3 else None
    with pytest.raises(BudgetExhausted, match="global invocation budget exhausted"):
        _ = _go(state, target, out, case_hash, calls)
    record = _record(out)
    assert record["plan_mode"] == "baseline" and record["phase"] == "prepared"
    assert not {"gate", "headroom", "search"} & record.keys()
    assert state.invokes == 0 and all(entry[0] == _seed_identity(state) for entry in state.search_log)


@pytest.mark.ac("autoimprove-authoring-headroom:AC-5")
def test_the_sandbox_time_estimate_counts_the_search_and_gate_evaluations_but_no_headroom_pass() -> None:
    plan = _plan(3, 5, 6, 2, 3, 30)
    gate_repeats = 2 * 3 - min(3, 2)
    evaluations = plan.metric_calls - VALIDATION_REPEATS * 5 + 6 * (gate_repeats + 1) + 2 * HOLDOUT_RETRIES_PER_ARM
    assert plan.sandbox_total == math.ceil(evaluations * 30 / MAX_CONCURRENT_CALLS)


@pytest.mark.ac("autoimprove-authoring-headroom:AC-5")
def test_a_holdout_gate_that_pushes_the_fixed_cost_over_the_call_cap_names_calls_not_seconds() -> None:
    messages: list[str] = []
    for holdout in range(1, 2 * APPROVED_CALLS):
        try:
            _ = _plan(1, 1, holdout, 1, 3)
        except Stop as stop:
            messages.append(str(stop))
    named = [message for message in messages if "the holdout gate, and the replay alone need" in message]
    assert named, messages[:1]
    assert all("calls; the cap is" in message and "seconds" not in message.split("cap is")[0] for message in named)


@pytest.mark.ac("gh-459:AC-1")
def test_the_plan_stops_when_the_validation_pass_leaves_too_little_search_room() -> None:
    with pytest.raises(Stop) as stop:
        _ = _plan(20, 26, 12, 2, 1, 0, holdout_costs=[2] * 12)
    assert stop.value.code == "budget-unapproved"
    assert "26 validation cases" in str(stop.value)
    assert "--seed" in str(stop.value)


@pytest.mark.ac("gh-459:AC-2")
def test_the_plan_admits_a_run_with_enough_search_room_after_the_validation_pass() -> None:
    plan = _plan(33, 5, 8, 2, 1, 0, holdout_costs=[2] * 8)
    assert search_allowance(plan.metric_calls, VALIDATION_REPEATS * 5) >= MINIMUM_SEARCH_MARGIN


@pytest.mark.ac("gh-459:AC-3")
def test_every_admitted_plan_leaves_the_minimum_margin_at_runtime() -> None:
    admitted = 0
    for train in (4, 10, 20, 33):
        for validation in (1, 6, 11, 26):
            for holdout in (2, 6, 8):
                try:
                    plan = _plan(train, validation, holdout, 2, 1, 0, holdout_costs=[2] * holdout)
                except Stop as stop:
                    assert stop.code == "budget-unapproved"
                    continue
                admitted += 1
                runs = VALIDATION_REPEATS * validation
                ceiling = plan.metric_calls + plan.headroom_outcomes - overshoot(runs)
                assert ceiling - (train + runs) >= MINIMUM_SEARCH_MARGIN
    assert admitted
