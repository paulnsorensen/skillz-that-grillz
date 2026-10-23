"""One model call has a per-call timeout, every claimed call has a role, and a deadline stop names its time."""
from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments import _runtime
from skillz_experiments._budget import CALL_TIMEOUT_FACTOR, FAILED, SEARCH_ROLES, SECONDS_PER_CALL, Budget, BudgetExhausted, CallTimeout
from skillz_experiments._records import read, write
from skillz_experiments.harness import _codex
from skillz_experiments.harness._codex import Codex
from skillz_experiments.harness._command import Command
from skillz_experiments.run import Stop, run
from skillz_experiments.run._export import summary
from test_codex_retry import _fake  # pyright: ignore[reportPrivateUsage]
from test_run_search import MODEL, Provider, Search, _start, _validation_runs  # pyright: ignore[reportPrivateUsage]

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]


@pytest.fixture(autouse=True)
def _restore_call_slots() -> Iterator[None]:
    yield
    _runtime.reset_call_slots()


@pytest.fixture
def timeouts(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record the timeout of every process the model calls start. The real process still runs."""
    seen: list[float] = []
    original = _runtime.process

    def spy(command: list[str], *, cwd: Path, timeout: float, **options: object) -> subprocess.CompletedProcess[str]:
        seen.append(timeout)
        return original(command, cwd=cwd, timeout=timeout, **options)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(_runtime, "process", spy)
    return seen


def _no_sleep(_seconds: float) -> None:
    return None


def _codex_call(budget: Budget, role: str = "task") -> dict[str, object]:
    adapter = Codex("gpt-6-sol", budget, lambda: None)
    adapter.role = role
    try:
        return adapter.invoke("hello")
    finally:
        adapter.close()


def _sleeper(budget: Budget) -> Command:
    return Command((sys.executable, "-c", "import time; time.sleep(30)"), "m", budget, lambda: None)


def test_the_per_call_estimate_is_the_plan_estimate() -> None:
    assert Budget(10, 1000, 0).call_seconds == SECONDS_PER_CALL and CALL_TIMEOUT_FACTOR == 4


@pytest.mark.ac("gh-319:AC-1")
def test_a_codex_call_gets_four_times_the_per_call_estimate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                           timeouts: list[float]) -> None:
    _ = _fake(tmp_path, monkeypatch, failures=0)
    _ = _codex_call(Budget(10, 1000, 0, call_seconds=2.5))
    assert timeouts == [10.0]


@pytest.mark.ac("gh-319:AC-1")
def test_a_codex_call_timeout_is_capped_by_the_remaining_search_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                    timeouts: list[float]) -> None:
    _ = _fake(tmp_path, monkeypatch, failures=0)
    _ = _codex_call(Budget(10, 5, 0))
    assert len(timeouts) == 1 and 0 < timeouts[0] <= 5 < CALL_TIMEOUT_FACTOR * SECONDS_PER_CALL


@pytest.mark.ac("gh-319:AC-1")
def test_a_codex_retry_attempt_gets_the_per_call_timeout_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                            timeouts: list[float]) -> None:
    _ = _fake(tmp_path, monkeypatch, failures=1, event='{"type":"error","message":"429 rate limit exceeded"}')
    monkeypatch.setattr(_codex, "_sleep", _no_sleep)
    _ = _codex_call(Budget(10, 1000, 0, call_seconds=2.5))
    assert timeouts == [10.0, 10.0]


@pytest.mark.ac("gh-319:AC-1")
def test_a_command_call_gets_four_times_the_per_call_estimate_and_the_remaining_cap(timeouts: list[float]) -> None:
    with pytest.raises(CallTimeout):
        _ = _sleeper(Budget(10, 1000, 0, call_seconds=0.25)).invoke("hello")
    with pytest.raises(BudgetExhausted):
        _ = _sleeper(Budget(10, 0.5, 0, call_seconds=100)).invoke("hello")
    assert timeouts[0] == 1.0 and 0 < timeouts[1] <= 0.5


@pytest.mark.ac("gh-319:AC-2")
def test_a_codex_call_that_outlasts_its_timeout_raises_a_call_timeout_and_counts_as_failed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = tmp_path / "host"
    host.mkdir()
    _ = (host / "auth.json").write_text("{}")
    fake = tmp_path / "bin/codex"
    fake.parent.mkdir()
    _ = fake.write_text("#!/bin/sh\nexec sleep 30\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}:/usr/bin:/bin")
    monkeypatch.setenv("CODEX_HOME", str(host))
    budget = Budget(10, 1000, 0, call_seconds=0.25)
    started = time.monotonic()
    with pytest.raises(CallTimeout, match="per-call timeout"):
        _ = _codex_call(budget, role="judge")
    assert time.monotonic() - started < 20
    assert budget.calls_by_role == {"judge": 1} and budget.seconds_by_role.get(FAILED, 0) >= 1.0
    assert "judge" not in budget.seconds_by_role


@pytest.mark.ac("gh-319:AC-2")
def test_a_call_stopped_by_the_search_deadline_is_not_a_call_timeout() -> None:
    with pytest.raises(BudgetExhausted):
        _ = _sleeper(Budget(10, 0.3, 0, call_seconds=100)).invoke("hello")


@pytest.mark.ac("gh-319:AC-2")
def test_a_task_call_timeout_is_an_evaluation_failed_outcome_with_reason_timeout(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda number: CallTimeout("the model call exceeded its per-call timeout") if number == 2 else None
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    rows = [row for row in cast(list[dict[str, object]], read(out / "run.json")["outcomes"]) if row.get("reason") == "timeout"]
    assert result["phase"] == "complete"
    assert len(rows) == 1 and rows[0]["status"] == "evaluation-failed" and rows[0]["score"] == 0.0


@pytest.mark.ac("gh-319:AC-2")
def test_a_reflection_timeout_ends_that_proposal_with_no_candidate(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.invoke_fault = lambda number: CallTimeout("the model call exceeded its per-call timeout") if number == 1 else None
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    outcomes = cast(list[dict[str, object]], read(out / "run.json")["outcomes"])
    rows = [row for row in outcomes if row.get("split") == "reflection" and row.get("reason") == "timeout"]
    assert result["phase"] == "complete"
    assert len(rows) == 1 and rows[0]["status"] == "timeout" and "proposed" not in rows[0]
    assert "search_candidate" not in rows[0]


@pytest.mark.ac("gh-319:AC-3")
def test_claims_and_timed_calls_sum_by_role() -> None:
    budget = Budget(10, 1000, 0)
    for role in ("task", "task", "judge", "reflection"):
        budget.claim(role=role)
    with budget.timed("task"):
        time.sleep(0.05)
    with pytest.raises(ValueError), budget.timed("reflection"):
        time.sleep(0.05)
        raise ValueError
    assert budget.calls_by_role == {"task": 2, "judge": 1, "reflection": 1} and sum(budget.calls_by_role.values()) == budget.calls == 4
    assert budget.seconds_by_role["task"] >= 0.05 and budget.seconds_by_role[FAILED] >= 0.05
    assert "reflection" not in budget.seconds_by_role


@pytest.mark.ac("gh-319:AC-3")
def test_failed_judge_and_reflection_calls_still_count_by_role(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _fake(tmp_path, monkeypatch, failures=99, event='{"type":"error","message":"boom"}')
    budget = Budget(10, 1000, 0)
    for role in ("judge", "reflection"):
        with pytest.raises(RuntimeError, match="Codex fails"):
            _ = _codex_call(budget, role=role)
    assert budget.calls_by_role == {"judge": 1, "reflection": 1} and budget.calls == 2
    assert set(budget.seconds_by_role) == {FAILED}


@pytest.mark.ac("gh-319:AC-3")
def test_a_run_records_calls_by_role_that_sum_to_calls_and_a_resume_keeps_them(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.abort_after_improved = _validation_runs(out)
    with pytest.raises(Exception, match="the process stopped"):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    stopped = read(out / "run.json")
    before = cast(dict[str, int], stopped["calls_by_role"])
    assert before.get("reflection", 0) >= 1 and sum(before.values()) == stopped["calls"]
    _ = run(target, out, MODEL, live=True, factory=state.factory)
    finished = read(out / "run.json")
    after = cast(dict[str, int], finished["calls_by_role"])
    assert sum(after.values()) == finished["calls"] and all(after[role] >= count for role, count in before.items())
    assert cast(dict[str, int], summary(finished)["calls_by_role"]) == after


@pytest.mark.ac("gh-319:AC-3")
def test_a_record_from_before_the_roles_resumes_with_its_calls_unattributed(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.abort_after_improved = _validation_runs(out)
    with pytest.raises(Exception, match="the process stopped"):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    del record["calls_by_role"], record["seconds_by_role"]
    write(out / "run.json", record)
    _ = run(target, out, MODEL, live=True, factory=state.factory)
    finished = read(out / "run.json")
    by_role = cast(dict[str, int], finished["calls_by_role"])
    assert by_role["unattributed"] == record["calls"] and sum(by_role.values()) == finished["calls"]


def _deadline_search(tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> tuple[Path, Path, Search]:
    """Prepare a run, then leave its search 0.5 s while each search evaluation takes 0.2 s."""
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    with pytest.raises(Stop):
        _ = run(target, out, MODEL, approve_cases=case_hash, approve_budget=calls)
    record = read(out / "run.json")
    budget = cast(dict[str, object], record["budget"])
    budget["seconds"] = time.time() - cast(float, record["started"]) + cast(float, budget["reserve_seconds"]) + 0.5
    write(out / "run.json", record)
    state = Search(target)
    state.search_delay = 0.2
    return target, out, state


@pytest.mark.ac("gh-500:AC-7")
@pytest.mark.ac("gh-319:AC-4")
def test_a_search_that_the_deadline_ends_early_reports_time_exhausted_with_seconds_by_role(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state = _deadline_search(tmp_path, make_target, write_draft, approvals)
    budgets: list[Budget] = []

    def factory(model: str, budget: Budget, checkpoint: Callable[[], None]) -> Provider:
        budgets.append(budget)
        return state.factory(model, budget, checkpoint)

    def evaluate_fault(_number: int) -> Exception | None:
        _ = budgets[0].remaining()
        return None

    state.evaluate_fault = evaluate_fault
    result = run(target, out, MODEL, live=True, factory=factory)
    search = cast(dict[str, object], result["search"])
    assert search["reason"] == "time-exhausted"
    assert cast(dict[str, object], read(out / "run.json")["search"])["stop_trigger"] == "search-deadline"
    assert set(cast(dict[str, float], search["seconds_by_role"])) == set(SEARCH_ROLES)
    assert result["phase"] == "complete"


@pytest.mark.ac("gh-319:AC-4")
@pytest.mark.ac("gh-500:AC-7")
def test_a_call_budget_stop_is_not_reported_as_time_exhausted(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.exhaust_after_improved = _validation_runs(out)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    search = cast(dict[str, object], result["search"])
    assert search["reason"] == "budget-exhausted-best-validated" and "seconds_by_role" not in search
    assert cast(dict[str, object], read(out / "run.json")["search"])["stop_trigger"] == "call-cap"
