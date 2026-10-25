"""Review fixes of wave 1: call slots and the per-call clock, reflection timeouts, and sandbox-aware call limits."""
from __future__ import annotations

import dataclasses
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from skillz_experiments import _runtime
from skillz_experiments._budget import SECONDS_PER_CALL, Budget, BudgetExhausted, CallTimeout
from skillz_experiments.gate import DEFAULT_STATISTICS
from skillz_experiments.run import Stop, _open, _outcomes, _plan, run
from skillz_experiments.run._session import Session
from test_run_search import MODEL, Search

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]


@pytest.fixture(autouse=True)
def _restore_call_slots() -> Iterator[None]:
    yield
    _runtime.reset_call_slots()


def _quick(tmp_path: Path, budget: Budget) -> object:
    return _runtime.model_call(budget, [sys.executable, "-c", "pass"], cwd=tmp_path, environment={})


@pytest.mark.ac("gh-319:AC-1")
def test_a_wait_for_a_slot_does_not_use_up_the_per_call_timeout(tmp_path: Path) -> None:
    _ = _runtime.halve_call_slots()
    assert _runtime._CHILDREN.acquire(1)  # pyright: ignore[reportPrivateUsage]
    threading.Timer(1.3, _runtime._CHILDREN.release).start()  # pyright: ignore[reportPrivateUsage]
    budget = Budget(10, 1000, 0, call_seconds=0.25)
    started = time.monotonic()
    _ = _quick(tmp_path, budget)
    assert time.monotonic() - started > budget.call_timeout()


@pytest.mark.ac("gh-319:AC-2")
def test_a_slot_wait_that_reaches_the_deadline_is_a_budget_stop_not_a_call_timeout(tmp_path: Path) -> None:
    _ = _runtime.halve_call_slots()
    assert _runtime._CHILDREN.acquire(1)  # pyright: ignore[reportPrivateUsage]
    try:
        with pytest.raises(BudgetExhausted):
            _ = _quick(tmp_path, Budget(10, 0.4, 0, call_seconds=100))
    finally:
        _runtime._CHILDREN.release()  # pyright: ignore[reportPrivateUsage]


@pytest.mark.ac("gh-318:AC-6")
def test_releasing_a_slot_that_no_call_holds_raises() -> None:
    with pytest.raises(ValueError, match="no call holds"):
        _runtime.CallSlots(2).release()


@pytest.mark.ac("gh-319:AC-3")
def test_a_snapshot_holds_the_calls_retries_and_both_role_maps() -> None:
    budget = Budget(10, 1000, 0)
    budget.claim(role="judge")
    budget.retry()
    with budget.timed("judge"):
        pass
    spent = budget.snapshot()
    budget.claim(role="task")
    assert (spent.calls, spent.retries, spent.calls_by_role) == (1, 1, {"judge": 1})
    assert set(spent.seconds_by_role) == {"judge"}


def _session_for(tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> tuple[Session, Search]:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out, families=12)
    case_hash, calls = approvals(target, out)
    with pytest.raises(Stop):
        _ = run(target, out, MODEL, approve_cases=case_hash, approve_budget=calls)
    state = Search(target)
    return Session(out, MODEL, "claude", state.factory, None, statistics=DEFAULT_STATISTICS), state


def _arguments(session: Session, unscored: bool) -> tuple[dict[str, str], dict[str, list[dict[str, object]]], list[str]]:
    components = list(session.seed.editable)
    item: dict[str, object] = {"unscored": True} if unscored else {"score": 0.5, "feedback": "fine"}
    return ({name: session.seed.files[name] for name in components}, {name: [item] for name in components}, components)


def _reflections(session: Session) -> list[dict[str, object]]:
    return [row for row in session.outcomes if row.get("split") == "reflection"]


@pytest.mark.ac("gh-319:AC-2")
def test_a_reflection_timeout_ends_the_proposal_so_gepas_retry_runs_no_second_call(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    session, state = _session_for(tmp_path, make_target, write_draft, approvals)
    state.invoke_fault = lambda number: CallTimeout("the model call exceeded its per-call timeout") if number == 1 else None
    try:
        candidate, feedback, components = _arguments(session, unscored=False)
        for _ in range(2):
            with pytest.raises(ValueError):
                _ = session._propose(candidate, feedback, components)  # pyright: ignore[reportPrivateUsage]
        rows = _reflections(session)
        assert state.invokes == 1 and len(rows) == 1 and rows[0]["status"] == "timeout"
        assert "proposed" not in rows[0]
    finally:
        session.provider.close()


@pytest.mark.ac("gh-316:AC-1")
def test_a_reflection_with_only_unscored_feedback_is_skipped_unpaid_and_once(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    session, state = _session_for(tmp_path, make_target, write_draft, approvals)
    try:
        candidate, feedback, components = _arguments(session, unscored=True)
        for _ in range(2):
            with pytest.raises(ValueError, match="no candidate"):
                _ = session._propose(candidate, feedback, components)  # pyright: ignore[reportPrivateUsage]
        rows = _reflections(session)
        assert state.invokes == 0 and session.budget.calls_by_role.get("reflection", 0) == 0
        assert [(row["status"], row["reason"]) for row in rows] == [("skipped", "no-scored-feedback")]
    finally:
        session.provider.close()


@pytest.mark.ac("gh-316:AC-1")
@pytest.mark.parametrize("status", ["timeout", "skipped", "proposal-build-failed"])
def test_a_failed_reflection_is_no_stall_observation(status: str) -> None:
    outcomes: list[dict[str, object]] = [
        {"arm": "search", "split": "validation", "case_id": "v1", "score": 0.5, "search_candidate": "seed"},
        {"arm": "search", "split": "reflection", "status": status}]
    assert _outcomes.stall_observations(outcomes, {"v1"}) == [None]


@pytest.mark.ac("gh-319:AC-3")
def test_a_checkpoint_reads_the_budget_through_one_snapshot(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    session, _state = _session_for(tmp_path, make_target, write_draft, approvals)
    seen: list[object] = []
    original = Budget.snapshot

    def spy(self: Budget) -> object:
        seen.append(self)
        return original(self)

    monkeypatch.setattr(Budget, "snapshot", spy)
    try:
        session.checkpoint()
    finally:
        session.provider.close()
    assert seen == [session.budget]


@pytest.mark.ac("gh-319:AC-1")
def test_the_per_call_estimate_adds_the_sandbox_seconds_of_one_evaluation(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    original = _plan.size_plan

    def sized(*arguments: object, **options: object) -> object:
        return dataclasses.replace(original(*arguments, **options), sandbox_seconds=300)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(_open, "size_plan", sized)
    session, _state = _session_for(tmp_path, make_target, write_draft, approvals)
    try:
        assert session.budget.call_seconds == SECONDS_PER_CALL + 300
        assert session.budget.call_timeout() > 240
    finally:
        session.provider.close()
