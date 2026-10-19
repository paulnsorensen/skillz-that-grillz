"""The session memo of the candidate contract check: one typed check result per candidate identity."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast, final

import pytest

from skillz_experiments._budget import Budget
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case
from skillz_experiments.evaluation import helper_failure
from skillz_experiments.evaluation import CheckMemo, CheckResult, evaluate
from skillz_experiments.evaluation._memo import Checker
from skillz_experiments.harness import Harness
from skillz_experiments.harness._transport import Transport
from skillz_experiments.run._session import _Provider  # pyright: ignore[reportPrivateUsage]
from typing_extensions import override
from fake_provider import PASSED, FakeProvider
from memo_fixtures import cases as _cases, contract as _contract

WAIT = 5.0


class ModelCalled(RuntimeError):
    """The evaluation reached a model call."""


@final
class Task(FakeProvider):
    """A task transport that counts contract checks, returns one fixed result, and fails on any model call."""

    def __init__(self, result: CheckResult, hold: threading.Event | None = None) -> None:
        self.budget = Budget(10, 60.0)
        self.result = result
        self.hold = hold
        self.checks = 0
        self.invokes = 0
        self.lock = threading.Lock()

    @override
    def check_candidate(self, candidate: object) -> CheckResult:
        del candidate
        with self.lock:
            self.checks += 1
        if self.hold is not None:
            _ = self.hold.wait(WAIT)
        return self.result

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del prompt, candidate, case, holdout, schema
        self.invokes += 1
        raise ModelCalled("model call reached")


def _rejected(reason: str | None) -> CheckResult:
    return CheckResult(False, reason, "fail")


def _candidate(text: str = "seed") -> Candidate:
    return Candidate({"SKILL.md": text}, ("SKILL.md",), contract=_contract())


def _echo_cases(tmp_path: Path) -> list[Case]:
    return _cases(tmp_path, _contract())


@pytest.mark.ac("gh-247:AC-1")
def test_one_memo_checks_each_candidate_identity_once(tmp_path: Path) -> None:
    task, memo = Task(PASSED), CheckMemo()
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
    task, memo = Task(_rejected(reason)), CheckMemo()
    results = [evaluate(task, task, _candidate(), case, checks=memo) for case in _echo_cases(tmp_path)]
    task.result = _rejected("a later check would give another reason")
    results.append(evaluate(task, task, _candidate(), _echo_cases(tmp_path)[0], checks=memo))
    assert [(item["score"], item["status"], item["rejection"]) for item in results] == [
        (0.0, "candidate-contract-rejected", reason)] * 4
    assert (task.checks, task.invokes) == (1, 0)


@pytest.mark.ac("gh-247:AC-3")
def test_concurrent_evaluations_of_a_new_identity_check_it_once(tmp_path: Path) -> None:
    hold = threading.Event()
    task, memo = Task(_rejected("slow rejection"), hold=hold), CheckMemo()
    cases = _echo_cases(tmp_path)
    with ThreadPoolExecutor(max_workers=len(cases)) as pool:
        futures = [pool.submit(evaluate, task, task, _candidate(), case, checks=memo) for case in cases]
        _ = threading.Event().wait(0.2)
        hold.set()
        results = [future.result(timeout=10) for future in futures]
    assert task.checks == 1
    assert {item["rejection"] for item in results} == {"slow rejection"}


@pytest.mark.ac("gh-312:AC-1")
def test_no_check_protocol_has_a_rejection_member() -> None:
    for protocol in (Checker, Transport, _Provider, Harness):
        members = set(vars(protocol)) | set(getattr(protocol, "__annotations__", {}))
        assert "rejection" not in members, protocol.__name__
        assert protocol.check_candidate.__annotations__["return"] == "CheckResult", protocol.__name__


@pytest.mark.ac("gh-312:AC-1")
@pytest.mark.parametrize(("passed", "reason", "kind"), [
    (True, "no reason on a pass", "pass"), (True, None, "fail"), (False, None, "pass"), (False, "x", "pass")])
def test_a_check_result_refuses_an_inconsistent_value(passed: bool, reason: str | None, kind: str) -> None:
    with pytest.raises(ValueError, match="inconsistent check result"):
        _ = CheckResult(passed, reason, kind)  # pyright: ignore[reportArgumentType]


@pytest.mark.ac("gh-312:AC-1")
def test_a_check_result_is_frozen() -> None:
    with pytest.raises(AttributeError):
        PASSED.passed = False  # pyright: ignore[reportAttributeAccessIssue]


@final
class _Strict:
    """A checker that records in `read` every attribute that the memo reads from it."""

    def __init__(self, result: CheckResult, read: list[str]) -> None:
        self._result = result
        self._read = read

    @override
    def __getattribute__(self, name: str) -> object:
        if not name.startswith("_"):
            cast(list[str], object.__getattribute__(self, "_read")).append(name)
        return cast(object, object.__getattribute__(self, name))

    def check_candidate(self, candidate: Candidate) -> CheckResult:
        del candidate
        return self._result


@pytest.mark.ac("gh-312:AC-2")
def test_the_memo_stores_the_returned_result_and_reads_nothing_else_from_the_transport() -> None:
    result, read = _rejected("helper fixture 0 fails on stdout"), cast(list[str], [])
    task, memo = _Strict(result, read), CheckMemo()
    assert memo.check(task, _candidate()) is result
    assert memo.check(task, _candidate()) is result
    assert read == ["check_candidate"]


@final
class _Meeting:
    """A checker whose check of each identity waits until `parties` checks are inside `check_candidate` at once."""

    def __init__(self, parties: int) -> None:
        self.barrier = threading.Barrier(parties, timeout=WAIT)
        self.lock = threading.Lock()
        self.checked: list[str] = []

    def check_candidate(self, candidate: Candidate) -> CheckResult:
        with self.lock:
            self.checked.append(candidate.identity)
        _ = self.barrier.wait()
        return _rejected(candidate.identity)


@pytest.mark.ac("gh-312:AC-3")
def test_checks_of_two_different_identities_run_at_the_same_time() -> None:
    """Each check waits for the other inside `check_candidate`. A global lock breaks the barrier."""
    task, memo = _Meeting(2), CheckMemo()
    first, second = _candidate("first"), _candidate("second")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(memo.check, task, candidate) for candidate in (first, second)]
        results = [future.result(timeout=2 * WAIT) for future in futures]
    assert [item.reason for item in results] == [first.identity, second.identity]
    assert sorted(task.checked) == sorted([first.identity, second.identity])


@pytest.mark.ac("gh-312:AC-4")
def test_two_threads_on_one_identity_share_one_check_and_one_result() -> None:
    hold = threading.Event()
    task, memo = Task(_rejected("slow rejection"), hold=hold), CheckMemo()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(memo.check, task, _candidate()) for _ in range(2)]
        _ = threading.Event().wait(0.2)
        hold.set()
        first, second = (future.result(timeout=2 * WAIT) for future in futures)
    assert first is second
    assert task.checks == 1


def test_a_check_that_raises_caches_nothing_and_frees_the_identity() -> None:
    @final
    class Flaky:
        def __init__(self) -> None:
            self.calls = 0

        def check_candidate(self, candidate: Candidate) -> CheckResult:
            del candidate
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transport crashed")
            return PASSED

    task, memo = Flaky(), CheckMemo()
    with pytest.raises(RuntimeError, match="transport crashed"):
        _ = memo.check(task, _candidate())
    assert memo.check(task, _candidate()) is PASSED
    assert task.calls == 2


@final
class _Sandbox:
    """A sandbox that exits every helper run with one code."""

    def __init__(self, code: int) -> None:
        self.code = code

    def sandbox(self, workspace: Path, argv: list[str]) -> tuple[int, str, str]:
        del workspace, argv
        return self.code, "", ""


@pytest.mark.ac("gh-247:AC-1")
def test_the_memo_keeps_an_exit_124_rejection_for_the_whole_run(tmp_path: Path) -> None:
    exited = helper_failure(_Sandbox(124), tmp_path, _contract(helper=True))
    task, memo = Task(exited), CheckMemo()
    results = [evaluate(task, task, _candidate(), case, checks=memo) for case in _echo_cases(tmp_path) * 4]
    assert exited == CheckResult(False, "helper fixture 0 fails on returncode", "fail")
    assert [item["score"] for item in results] == [0.0] * 12
    assert (task.checks, task.invokes) == (1, 0)
    assert {(item["status"], item["rejection"]) for item in results} == {("candidate-contract-rejected", exited.reason)}