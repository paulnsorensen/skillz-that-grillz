"""The session memo of the candidate contract check: one check per candidate identity, with its rejection reason."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import final

import pytest

from skillz_experiments._budget import Budget
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, load_cases
from skillz_experiments._contract import Contract, parse
from skillz_experiments._evaluation import TIMEOUT_REJECTION, helper_failure, transient
from skillz_experiments.evaluation import CheckMemo, evaluate
from typing_extensions import override
from fake_provider import FakeProvider

SKILL = "memo-skill"


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


def _rules() -> Contract:
    return parse({"schema_version": 1, "status": "approved", "skill": SKILL, "invocation": f"${SKILL} run",
                  "kinds": {"echo": {"grader": "exact-json"}}}, "skill")


def _candidate(text: str = "seed") -> Candidate:
    return Candidate({"SKILL.md": text}, ("SKILL.md",), contract=_rules())


def _cases(tmp_path: Path) -> list[Case]:
    items = [{"id": name, "family": name, "split": "train", "kind": "echo", "request": "Echo.", "files": {},
              "expected": {"echo": "hello"}, "provenance": "test", "provider_approved": True} for name in ("a", "b", "c")]
    path = tmp_path / "cases.json"
    _ = path.write_text(json.dumps({"schema_version": 1, "cases": items}))
    return load_cases(path, _rules().grader_types())


@pytest.mark.ac("gh-247:AC-1")
def test_one_memo_checks_each_candidate_identity_once(tmp_path: Path) -> None:
    task, memo = Task(passed=True), CheckMemo()
    first, second = _candidate(), _candidate("other")
    for candidate in (first, second, first, second):
        for case in _cases(tmp_path):
            with pytest.raises(ModelCalled):
                _ = evaluate(task, task, candidate, case, checks=memo)
    assert task.checks == 2
    assert task.invokes == 12


@pytest.mark.ac("gh-247:AC-1")
def test_without_a_memo_each_evaluation_checks_again(tmp_path: Path) -> None:
    task = Task(passed=False, reason="helper fixture failed")
    for case in _cases(tmp_path):
        _ = evaluate(task, task, _candidate(), case)
    assert task.checks == 3


@pytest.mark.ac("gh-247:AC-2")
@pytest.mark.parametrize("reason", ["helper fixture failed", None])
def test_a_rejected_candidate_scores_zero_on_every_case_with_the_same_reason(tmp_path: Path, reason: str | None) -> None:
    task, memo = Task(passed=False, reason=reason), CheckMemo()
    results = [evaluate(task, task, _candidate(), case, checks=memo) for case in _cases(tmp_path)]
    task.rejection = "a later check changed the transport"
    results.append(evaluate(task, task, _candidate(), _cases(tmp_path)[0], checks=memo))
    assert [(item["score"], item["status"], item["rejection"]) for item in results] == [
        (0.0, "candidate-contract-rejected", reason)] * 4
    assert (task.checks, task.invokes) == (1, 0)


@pytest.mark.ac("gh-247:AC-3")
def test_concurrent_evaluations_of_a_new_identity_check_it_once(tmp_path: Path) -> None:
    hold = threading.Event()
    task, memo = Task(passed=False, reason="slow rejection", hold=hold), CheckMemo()
    cases = _cases(tmp_path)
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


@pytest.mark.parametrize(("code", "reason", "is_transient"), [
    (124, "helper fixture 0 times out", True), (3, "helper fixture 0 fails on returncode", False)])
def test_only_a_sandbox_timeout_gives_a_transient_helper_rejection(
        tmp_path: Path, code: int, reason: str, is_transient: bool) -> None:
    rules = parse({"schema_version": 1, "status": "approved", "skill": SKILL, "invocation": f"${SKILL} run",
                   "kinds": {"echo": {"grader": "exact-json"}},
                   "helper": {"path": "scripts/echo.py", "input": "input.md",
                              "fixtures": [{"input": "hi", "returncode": 0, "output": {"ok": True}}]}}, "skill")
    rejection = helper_failure(_Sandbox(code), tmp_path, rules)
    assert (rejection, transient(rejection)) == (reason, is_transient)


@pytest.mark.ac("gh-247:AC-1")
def test_the_memo_does_not_reuse_a_sandbox_timeout_and_reuses_the_later_pass(tmp_path: Path) -> None:
    task, memo = Task(passed=False, reason="helper fixture 0 times out"), CheckMemo()
    first, *rest = _cases(tmp_path)
    timed_out = evaluate(task, task, _candidate(), first, checks=memo)
    task.passed, task.reason = True, None
    for case in rest:
        with pytest.raises(ModelCalled):
            _ = evaluate(task, task, _candidate(), case, checks=memo)
    assert (timed_out["status"], timed_out["rejection"]) == ("candidate-contract-rejected", "helper fixture 0 times out")
    assert (task.checks, task.invokes) == (2, 2)


@pytest.mark.ac("gh-247:AC-1")
def test_the_memo_checks_a_helper_that_always_times_out_at_most_twice(tmp_path: Path) -> None:
    timeout = TIMEOUT_REJECTION.format(index=0)
    task, memo = Task(passed=False, reason=timeout), CheckMemo()
    results = [evaluate(task, task, _candidate(), case, checks=memo) for case in _cases(tmp_path) * 4]
    assert task.checks == 2 and task.invokes == 0
    assert {(item["status"], item["rejection"]) for item in results} == {("candidate-contract-rejected", timeout)}


@pytest.mark.parametrize(("rejection", "is_transient"), [
    (TIMEOUT_REJECTION.format(index=0), True), (TIMEOUT_REJECTION.format(index=12), True),
    ("the helper times out", False), ("helper fixture x times out", False),
    (TIMEOUT_REJECTION.format(index=0) + " again", False), (None, False)])
def test_transient_matches_only_the_timeout_rejection_that_helper_failure_builds(
        rejection: str | None, is_transient: bool) -> None:
    assert transient(rejection) is is_transient
