"""The session memo of the candidate contract check: one check per candidate identity, with its rejection reason."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import final

import pytest

from skillz_experiments._budget import Budget
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case
from skillz_experiments._evaluation import helper_failure
from skillz_experiments.evaluation import CheckMemo, evaluate
from typing_extensions import override
from fake_provider import FakeProvider
from memo_fixtures import cases as _cases, contract as _contract


class ModelCalled(RuntimeError):
    """The evaluation reached a model call."""


@final
class Task(FakeProvider):
    """A task transport that counts contract checks, keeps the last reason, and fails on any model call."""

    def __init__(self, passed: bool, reason: str | None = None, hold: threading.Event | None = None) -> None:
        self.budget = Budget(10, 60.0)
        self.passed = passed
        self.reason = reason
        self.hold = hold
        self.checks = 0
        self.invokes = 0
        self.lock = threading.Lock()

    @override
    def check_candidate(self, candidate: object) -> bool:
        del candidate
        with self.lock:
            self.checks += 1
        self.rejection = None
        if self.hold is not None:
            _ = self.hold.wait(5)
        self.rejection = None if self.passed else self.reason
        return self.passed

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del prompt, candidate, case, holdout, schema
        self.invokes += 1
        raise ModelCalled("model call reached")


def _candidate(text: str = "seed") -> Candidate:
    return Candidate({"SKILL.md": text}, ("SKILL.md",), contract=_contract())


def _echo_cases(tmp_path: Path) -> list[Case]:
    return _cases(tmp_path, _contract())


@pytest.mark.ac("gh-247:AC-1")
def test_one_memo_checks_each_candidate_identity_once(tmp_path: Path) -> None:
    task, memo = Task(passed=True), CheckMemo()
    first, second = _candidate(), _candidate("other")
    for candidate in (first, second, first, second):
        for case in _echo_cases(tmp_path):
            with pytest.raises(ModelCalled):
                _ = evaluate(task, task, candidate, case, checks=memo)
    assert task.checks == 2
    assert task.invokes == 12


@pytest.mark.ac("gh-247:AC-2")
@pytest.mark.parametrize("reason", ["helper fixture failed", None])
def test_a_rejected_candidate_scores_zero_on_every_case_with_the_same_reason(tmp_path: Path, reason: str | None) -> None:
    task, memo = Task(passed=False, reason=reason), CheckMemo()
    results = [evaluate(task, task, _candidate(), case, checks=memo) for case in _echo_cases(tmp_path)]
    task.rejection = "a later check changed the transport"
    results.append(evaluate(task, task, _candidate(), _echo_cases(tmp_path)[0], checks=memo))
    assert [(item["score"], item["status"], item["rejection"]) for item in results] == [
        (0.0, "candidate-contract-rejected", reason)] * 4
    assert (task.checks, task.invokes) == (1, 0)


@pytest.mark.ac("gh-247:AC-3")
def test_concurrent_evaluations_of_a_new_identity_check_it_once(tmp_path: Path) -> None:
    hold = threading.Event()
    task, memo = Task(passed=False, reason="slow rejection", hold=hold), CheckMemo()
    cases = _echo_cases(tmp_path)
    with ThreadPoolExecutor(max_workers=len(cases)) as pool:
        futures = [pool.submit(evaluate, task, task, _candidate(), case, checks=memo) for case in cases]
        _ = threading.Event().wait(0.2)
        hold.set()
        results = [future.result(timeout=10) for future in futures]
    assert task.checks == 1
    assert {item["rejection"] for item in results} == {"slow rejection"}


@final
class _Sandbox:
    """A sandbox that exits every helper run with one code."""

    def __init__(self, code: int) -> None:
        self.code = code

    def sandbox(self, workspace: Path, argv: list[str]) -> tuple[int, str, str]:
        del workspace, argv
        return self.code, "", ""


@pytest.mark.ac("gh-247:AC-1")
def test_the_memo_keeps_a_sandbox_timeout_rejection_for_the_whole_run(tmp_path: Path) -> None:
    timeout = helper_failure(_Sandbox(124), tmp_path, _contract(helper=True))
    task, memo = Task(passed=False, reason=timeout), CheckMemo()
    results = [evaluate(task, task, _candidate(), case, checks=memo) for case in _echo_cases(tmp_path) * 4]
    assert timeout is not None and timeout.strip()
    assert [item["score"] for item in results] == [0.0] * 12
    assert (task.checks, task.invokes) == (1, 0)
    assert {(item["status"], item["rejection"]) for item in results} == {("candidate-contract-rejected", timeout)}
