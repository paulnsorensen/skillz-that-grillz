"""Retryable sandbox timeouts (gh-287): the timeout signal, the in-memo retry, and the resumable gate stops."""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast, final

import pytest
from typing_extensions import override

from skillz_experiments import _graders, _runtime
from skillz_experiments._budget import Budget, BudgetExhausted, CallTimeout
from skillz_experiments._candidate import Candidate, make_workspace
from skillz_experiments._cases import Case
from skillz_experiments.evaluation import helper_failure
from skillz_experiments._records import read
from skillz_experiments.evaluation import CheckMemo, CheckResult, evaluate
from skillz_experiments.harness import _command
from skillz_experiments.harness._codex import Codex
from skillz_experiments.harness._command import Command
from skillz_experiments.run import Stop, run
from skillz_experiments.run._export import _frozen, calls_upper_bound  # pyright: ignore[reportPrivateUsage]
from skillz_experiments.run._outcomes import holdout_scores
from skillz_experiments.run._session import Session
from fake_provider import PASSED, FakeProvider, FakeRunProvider, ProviderCrash
from memo_fixtures import SKILL, cases as _cases, contract as _contract

pytestmark = pytest.mark.usefixtures("host_login", "headroom_off")

MODEL = "local-test"
WAIT = 5.0
TIMEOUT = CheckResult(False, "helper fixture 0 reaches the sandbox time limit", "timeout")
Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]


# --- the timeout signal ---------------------------------------------------------------------------------------


@pytest.mark.ac("gh-287:AC-5")
def test_a_child_past_the_sandbox_limit_raises_sandbox_timeout_and_dies(tmp_path: Path) -> None:
    started = time.monotonic()
    with pytest.raises(_runtime.SandboxTimeout, match="1 second limit"):
        _ = _runtime.sandboxed(["/bin/sleep", "30"], cwd=tmp_path, limit=1, budget=Budget(10, 120, 0),
                               environment={"PATH": "/usr/bin:/bin"})
    assert time.monotonic() - started < 10


@pytest.mark.ac("gh-287:AC-5")
def test_a_child_that_exits_124_by_itself_is_an_ordinary_exit(tmp_path: Path) -> None:
    result = _runtime.sandboxed(["/bin/sh", "-c", "exit 124"], cwd=tmp_path, limit=5, budget=Budget(10, 120, 0),
                                environment={"PATH": "/usr/bin:/bin"})
    assert result.returncode == 124


@pytest.mark.ac("gh-287:AC-7")
def test_a_run_budget_shorter_than_the_limit_raises_budget_exhausted(tmp_path: Path) -> None:
    with pytest.raises(BudgetExhausted, match="terminated at deadline"):
        _ = _runtime.sandboxed(["/bin/sleep", "30"], cwd=tmp_path, limit=60, budget=Budget(10, 1, 0),
                               environment={"PATH": "/usr/bin:/bin"})
    assert BudgetExhausted not in _runtime.SandboxTimeout.__mro__


SETSID_CHILD = """
import os, pathlib, sys, time
if os.fork() == 0:
    os.setsid()
    pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
    time.sleep(30)
    os._exit(0)
time.sleep(30)
"""


@pytest.mark.ac("gh-287:AC-6")
def test_a_descendant_that_leaves_the_group_cannot_hold_the_runner_past_the_limit(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A setsid grandchild keeps the child's stdout open. The runner must not read that pipe to the end."""
    monkeypatch.setattr(_runtime, "TERM_GRACE_SECONDS", 1.0)
    marker = tmp_path / "grandchild"
    started = time.monotonic()
    try:
        with pytest.raises(_runtime.SandboxTimeout):
            _ = _runtime.sandboxed([sys.executable, "-c", SETSID_CHILD, str(marker)], cwd=tmp_path, limit=1,
                                   budget=Budget(10, 120, 0), environment={"PATH": "/usr/bin:/bin"})
        assert time.monotonic() - started < 10
    finally:
        if marker.exists():
            try:
                os.kill(int(marker.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


@final
class _Sandbox:
    """A sandbox that raises `SandboxTimeout` or exits with one code on each helper run."""

    def __init__(self, code: int | None) -> None:
        self.code = code

    def sandbox(self, workspace: Path, argv: list[str]) -> tuple[int, str, str]:
        del workspace, argv
        if self.code is None:
            raise _runtime.SandboxTimeout("the sandbox run reached its 20 second limit")
        return self.code, "", ""


@pytest.mark.ac("gh-287:AC-5")
def test_helper_failure_maps_a_sandbox_timeout_to_kind_timeout_and_exit_124_to_kind_fail(tmp_path: Path) -> None:
    rules = _contract(helper=True)
    assert helper_failure(_Sandbox(None), tmp_path, rules) == TIMEOUT
    assert helper_failure(_Sandbox(124), tmp_path, rules) == CheckResult(False, "helper fixture 0 fails on returncode", "fail")


def _helper_candidate() -> Candidate:
    files = {"SKILL.md": f"---\nname: {SKILL}\ndescription: echo\n---\n", "scripts/echo.py": "print(1)\n"}
    return Candidate(files, ("SKILL.md", "scripts/echo.py"), _contract(helper=True))


@pytest.mark.ac("gh-287:AC-5")
def test_codex_maps_a_sandbox_run_past_sandbox_seconds_to_kind_timeout(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = object.__new__(Codex)
    adapter.executable, adapter.codex_home, adapter.budget = Path("/bin/codex"), tmp_path, Budget(5, 60, reserve=0)
    limits: list[object] = []

    def discover(self: Codex, workspace: Path, skill: str) -> bool:
        del self, workspace, skill
        return True

    def sandboxed(command: list[str], **options: object) -> object:
        del command
        limits.append(options["limit"])
        raise _runtime.SandboxTimeout("the sandbox run reached its 20 second limit")

    monkeypatch.setattr(Codex, "_discover", discover)
    monkeypatch.setattr("skillz_experiments.harness._codex.sandboxed", sandboxed)
    assert adapter.check_candidate(_helper_candidate()) == TIMEOUT
    assert limits == [_runtime.SANDBOX_SECONDS]


WRAPPER = """
import json, os, subprocess, sys, time
request = json.load(sys.stdin)
with open(sys.argv[1], 'a') as log:
    log.write(json.dumps(sorted(request)) + '\\n')
if request['operation'] == 'discover':
    print(json.dumps(dict(schema_version=1, operation='discover',
                          skills=[dict(name=request['skill_name'], path=request['skill_path'])])))
else:
    child = subprocess.Popen(['/bin/sleep', '60'])
    with open(sys.argv[2], 'w') as pids:
        pids.write(f'{os.getpid()} {child.pid}')
    time.sleep(60)
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        with open(f"/proc/{pid}/stat") as stat:
            return stat.read().split()[2] != "Z"
    except (ProcessLookupError, FileNotFoundError):
        return False


def _wait_until_dead(*pids: int, seconds: float = 5) -> None:
    """Wait for the processes to exit. SIGKILL is asynchronous, so a killed process can still be exiting."""
    deadline = time.monotonic() + seconds
    while any(_alive(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.05)


@pytest.mark.ac("gh-287:AC-5")
@pytest.mark.ac("gh-287:AC-6")
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_the_command_harness_kills_the_wrapper_group_at_sandbox_seconds_and_sends_no_new_field(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper, log, pids = tmp_path / "wrapper.py", tmp_path / "requests.log", tmp_path / "pids"
    _ = wrapper.write_text(WRAPPER)
    # The cap must exceed the wrapper's own start-up time, which can pass 1 s on a loaded host.
    monkeypatch.setattr(_command, "SANDBOX_SECONDS", 5)
    adapter = Command((sys.executable, str(wrapper), str(log), str(pids)), "m", Budget(10, 120, 0), lambda: None)
    started = time.monotonic()
    assert adapter.check_candidate(_helper_candidate()) == TIMEOUT
    assert 5 <= time.monotonic() - started < 30
    wrapper_pid, child_pid = (int(part) for part in pids.read_text().split())
    _wait_until_dead(wrapper_pid, child_pid)
    assert not _alive(wrapper_pid) and not _alive(child_pid)
    keys = [cast(list[str], json.loads(line)) for line in log.read_text().splitlines()]
    assert keys[-1] == ["argv", "model", "operation", "schema_version", "workspace"]


SLOW_PROBE_WRAPPER = """
import json, sys, time
request = json.load(sys.stdin)
if request['operation'] == 'discover':
    print(json.dumps(dict(schema_version=1, operation='discover',
                          skills=[dict(name=request['skill_name'], path=request['skill_path'])])))
else:
    time.sleep(2)
    print(json.dumps(dict(schema_version=1, operation='sandbox', returncode=0, stdout='isolation-ok\\n', stderr='')))
"""


def test_the_command_preflight_probe_gets_a_longer_bounded_limit_than_a_helper_fixture(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A container wrapper can start slowly once. The probe has its own limit, and a probe hang fails preflight."""
    wrapper = tmp_path / "wrapper.py"
    _ = wrapper.write_text(SLOW_PROBE_WRAPPER)
    monkeypatch.setattr(_command, "SANDBOX_SECONDS", 1)
    monkeypatch.setattr(_command, "PROBE_SECONDS", 10)
    adapter = Command((sys.executable, str(wrapper)), "m", Budget(10, 120, 0), lambda: None)
    assert adapter.preflight()["isolation"] == "passed"
    with pytest.raises(_runtime.SandboxTimeout):
        _ = adapter.sandbox(make_workspace(tmp_path / "workspace"), ["/usr/bin/true"])
    monkeypatch.setattr(_command, "PROBE_SECONDS", 1)
    with pytest.raises(RuntimeError, match="the sandbox probe timed out"):
        _ = adapter.preflight()


@final
class _Grading:
    """A task transport whose sandbox raises `SandboxTimeout`, or returns the exit 124 that a timeout gave before."""

    def __init__(self, timeout: bool) -> None:
        self.timeout = timeout

    def sandbox(self, workspace: Path, argv: list[str]) -> tuple[int, str, str]:
        del workspace, argv
        if self.timeout:
            raise _runtime.SandboxTimeout("the sandbox run reached its 20 second limit")
        return 124, "", ""


@pytest.mark.ac("gh-287:AC-14")
def test_the_command_and_capture_graders_treat_a_sandbox_timeout_as_exit_124(tmp_path: Path) -> None:
    case = _cases(tmp_path, _contract())[0]
    argv = ("/usr/bin/python3", "grade.py")
    scores = [_graders.command(_Grading(timeout), argv, case, {}) for timeout in (True, False)]
    assert scores == [0.0, 0.0]
    failures: list[str] = []
    for timeout in (True, False):
        with pytest.raises(_graders.CaptureFailed) as caught:
            _ = _graders.capture(_Grading(timeout), argv, case, {})
        failures.append(str(caught.value))
    assert failures == ["the capture argv exits with code 124"] * 2


# --- the in-memo retry ----------------------------------------------------------------------------------------


@final
class _Scripted:
    """A checker that returns its scripted results in order, then repeats the last one."""

    def __init__(self, *results: CheckResult, hold: threading.Event | None = None) -> None:
        self.results = list(results)
        self.hold = hold
        self.calls = 0
        self.lock = threading.Lock()

    def check_candidate(self, candidate: Candidate) -> CheckResult:
        del candidate
        with self.lock:
            self.calls += 1
            result = self.results[min(self.calls, len(self.results)) - 1]
        if self.hold is not None:
            _ = self.hold.wait(WAIT)
        return result


def _candidate(text: str = "seed") -> Candidate:
    return Candidate({"SKILL.md": text}, ("SKILL.md",), contract=_contract())


@pytest.mark.ac("gh-287:AC-8")
@pytest.mark.parametrize("second", [PASSED, CheckResult(False, "helper fixture 0 fails on stdout", "fail")])
def test_a_first_timeout_checks_again_at_once_and_caches_the_second_result(second: CheckResult) -> None:
    task, memo = _Scripted(TIMEOUT, second), CheckMemo()
    assert memo.check(task, _candidate()) is second
    assert memo.check(task, _candidate()) is second
    assert task.calls == 2


@pytest.mark.ac("gh-287:AC-9")
def test_two_timeouts_are_final_for_the_run_and_later_checks_run_no_transport_check() -> None:
    task, memo = _Scripted(TIMEOUT, TIMEOUT, PASSED), CheckMemo()
    results = [memo.check(task, _candidate()) for _ in range(4)]
    assert results == [TIMEOUT] * 4
    assert task.calls == 2


@pytest.mark.ac("gh-287:AC-4")
def test_two_threads_on_one_identity_share_the_retry_and_the_final_timeout() -> None:
    hold = threading.Event()
    task, memo = _Scripted(TIMEOUT, TIMEOUT, PASSED, hold=hold), CheckMemo()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(memo.check, task, _candidate()) for _ in range(2)]
        _ = threading.Event().wait(0.2)
        hold.set()
        first, second = (future.result(timeout=2 * WAIT) for future in futures)
    assert first is second is TIMEOUT
    assert task.calls == 2


@pytest.mark.ac("gh-287:AC-4")
def test_a_reader_during_the_retry_waits_for_the_final_result_and_never_sees_the_first_timeout() -> None:
    retrying, release = threading.Event(), threading.Event()

    @final
    class Gated:
        """A checker whose first attempt times out and whose retry waits for `release`."""

        def __init__(self) -> None:
            self.calls = 0

        def check_candidate(self, candidate: Candidate) -> CheckResult:
            del candidate
            self.calls += 1
            if self.calls == 1:
                return TIMEOUT
            retrying.set()
            _ = release.wait(WAIT)
            return PASSED

    task, memo = Gated(), CheckMemo()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(memo.check, task, _candidate())
        assert retrying.wait(WAIT)
        reader = pool.submit(memo.check, task, _candidate())
        _ = threading.Event().wait(0.2)
        assert not reader.done()
        release.set()
        results = (first.result(timeout=2 * WAIT), reader.result(timeout=2 * WAIT))
    assert results[0] is results[1] is PASSED
    assert task.calls == 2


@pytest.mark.ac("gh-287:AC-10")
def test_a_final_timeout_outcome_carries_its_rejection_kind(tmp_path: Path) -> None:
    @final
    class Task(FakeProvider):
        def __init__(self) -> None:
            self.budget = Budget(10, 60.0)

        @override
        def check_candidate(self, candidate: object) -> CheckResult:
            del candidate
            return TIMEOUT

        def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
                   *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
            del prompt, candidate, case, holdout, schema
            raise AssertionError("a rejected candidate makes no model call")

    task = Task()
    result = evaluate(task, task, _candidate(), _cases(tmp_path, _contract())[0], checks=CheckMemo())
    assert (result["status"], result["rejection"], result["rejection_kind"], result["score"]) == (
        "candidate-contract-rejected", TIMEOUT.reason, "timeout", 0.0)


# --- the run stops ----------------------------------------------------------------------------------------------


@dataclass
class Plan:
    """The knobs of the run fake. The seed scores 0, a proposal that says `improved` scores 1.

    `timeouts` maps a role (`seed` or `winner`) to the number of timeout check results that it still returns.
    """

    timeouts: dict[str, int] = field(default_factory=lambda: cast(dict[str, int], {}))
    crash_holdout: bool = False
    preflight_error: Exception | None = None
    checks: Counter[str] = field(default_factory=lambda: cast(Counter[str], Counter()))
    lock: threading.Lock = field(default_factory=threading.Lock)

    @staticmethod
    def role(candidate: Candidate) -> str:
        return "winner" if "improved" in candidate.files["SKILL.md"] else "seed"

    def check(self, candidate: Candidate) -> CheckResult:
        role = self.role(candidate)
        with self.lock:
            self.checks[role] += 1
            if self.timeouts.get(role, 0) > 0:
                self.timeouts[role] -= 1
                return TIMEOUT
        return PASSED

    def factory(self) -> Callable[[str, Budget, Callable[[], None]], _Timed]:
        return lambda _model, _budget, _checkpoint: _Timed(self)


@final
class _Timed(FakeRunProvider):
    """A provider that routes each evaluation through the session memo, as `evaluate` does."""

    def __init__(self, plan: Plan) -> None:
        super().__init__()
        self.plan = plan

    @override
    def preflight(self) -> dict[str, object]:
        if self.plan.preflight_error is not None:
            raise self.plan.preflight_error
        return {}

    @override
    def check_candidate(self, candidate: object) -> CheckResult:
        return self.plan.check(cast(Candidate, candidate))

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False,
                 checks: CheckMemo) -> dict[str, object]:
        if holdout and self.plan.crash_holdout:
            raise ProviderCrash("provider fell over")
        checked = checks.check(self, candidate)
        base: dict[str, object] = {"candidate_hash": candidate.identity, "case_hash": case.identifier}
        if not checked.passed:
            return base | {"score": 0.0, "status": "candidate-contract-rejected", "rejection": checked.reason,
                           "rejection_kind": checked.kind}
        return base | {"score": float(self.plan.role(candidate) == "winner")}

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del prompt, candidate, case, holdout
        assert schema is not None
        return {"answer": {name: "improved" for name in cast(list[str], schema["required"])}}


def _outcomes(out: Path) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], read(out / "run.json")["outcomes"])


def _gate_rows(out: Path) -> list[dict[str, object]]:
    return [item for item in _outcomes(out) if item.get("split") == "holdout" and item.get("arm") in ("baseline", "winner")]


def _at_gate(tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver, plan: Plan) -> tuple[Path, Path]:
    """Run until the first holdout call crashes. The record keeps the search at phase `searched`."""
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    plan.crash_holdout = True
    with pytest.raises(ProviderCrash):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=plan.factory())
    plan.crash_holdout = False
    assert read(out / "run.json")["phase"] == "searched"
    return target, out


@pytest.mark.ac("gh-287:AC-11")
def test_a_final_seed_check_timeout_stops_resumably_and_a_resume_checks_again(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, plan = make_target(tmp_path), tmp_path / "run", Plan(timeouts={"seed": 2})
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    with pytest.raises(Stop) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=plan.factory())
    assert caught.value.code == "baseline-check-timeout"
    assert "resume" in str(caught.value.data["next"]) and "retries the check" in str(caught.value.data["next"])
    assert caught.value.data["detail"] == TIMEOUT.reason
    record = read(out / "run.json")
    assert (record["phase"], record["failure_code"], plan.checks["seed"]) == ("prepared", "baseline-check-timeout", 2)
    _ = run(target, out, MODEL, live=True, factory=plan.factory())
    assert read(out / "run.json")["phase"] == "complete"


@pytest.mark.ac("gh-460:AC-1")
@pytest.mark.ac("gh-460:AC-2")
@pytest.mark.ac("gh-460:AC-3")
def test_a_preflight_call_timeout_stops_resumably_and_a_resume_passes_the_preflight(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, plan = make_target(tmp_path), tmp_path / "run", Plan(preflight_error=CallTimeout("per-call timeout"))
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    with pytest.raises(Stop) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=plan.factory())
    assert caught.value.code == "preflight-timeout"
    assert "resume" in str(caught.value.data["next"])
    assert isinstance(caught.value.__cause__, CallTimeout)
    record = read(out / "run.json")
    assert (record["phase"], record["failure_code"], "preflight" in record) == ("prepared", "preflight-timeout", False)
    plan.preflight_error = None
    _ = run(target, out, MODEL, live=True, factory=plan.factory())
    resumed = read(out / "run.json")
    assert resumed["phase"] == "complete" and "preflight" in resumed


@pytest.mark.ac("gh-460:AC-4")
def test_a_preflight_runtime_error_that_is_not_a_call_timeout_propagates_unchanged(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    error = RuntimeError("provider fell over")
    target, out, plan = make_target(tmp_path), tmp_path / "run", Plan(preflight_error=error)
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    with pytest.raises(RuntimeError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=plan.factory())
    assert caught.value is error and not isinstance(caught.value, Stop)


@pytest.mark.ac("gh-287:AC-8")
def test_one_seed_check_timeout_is_retried_inside_the_check_and_never_stops_the_run(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, plan = make_target(tmp_path), tmp_path / "run", Plan(timeouts={"seed": 1})
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=plan.factory())
    assert read(out / "run.json")["phase"] == "complete"
    assert not any(item.get("rejection_kind") == "timeout" for item in _outcomes(out))


@pytest.mark.ac("gh-287:AC-12")
@pytest.mark.ac("gh-287:AC-13")
@pytest.mark.parametrize(("role", "arm", "code"), [
    ("seed", "baseline", "baseline-check-timeout"), ("winner", "winner", "winner-check-timeout")])
def test_a_final_gate_check_timeout_stops_resumably_and_the_resume_scores_those_cases_again(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        role: str, arm: str, code: str) -> None:
    plan = Plan()
    target, out = _at_gate(tmp_path, make_target, write_draft, approvals, plan)
    plan.timeouts, plan.checks = {role: 2}, Counter()
    with pytest.raises(Stop) as caught:
        _ = run(target, out, MODEL, live=True, factory=plan.factory())
    assert caught.value.code == code and "retries the check" in str(caught.value.data["next"])
    assert plan.checks[role] == 2
    timed_out = [item for item in _gate_rows(out) if item.get("rejection_kind") == "timeout"]
    assert timed_out and all(item["arm"] == arm for item in timed_out)
    before = [item for item in _gate_rows(out) if item.get("rejection_kind") != "timeout"]
    _ = run(target, out, MODEL, live=True, factory=plan.factory())
    record = read(out / "run.json")
    assert "gate" in record and record["phase"] == "complete" and plan.checks[role] == 3
    rows = _gate_rows(out)
    assert [item for item in rows if item.get("rejection_kind") == "timeout"] == timed_out
    scored = Counter((item["arm"], item["case_id"], item["repeat"]) for item in rows if item.get("rejection_kind") != "timeout")
    holdout = [str(item["id"]) for item in cast(list[dict[str, object]], json.loads((out / "cases.json").read_text())["cases"])
               if item["split"] == "holdout"]
    repeats = cast(int, record["repeats"])
    assert scored == Counter({(each, case_id, repeat): 1 for each in ("baseline", "winner")
                              for case_id in holdout for repeat in range(repeats)})
    assert all(item in rows for item in before)
    scores = holdout_scores(rows, arm)
    assert {case_id: sorted(by_repeat) for case_id, by_repeat in scores.items()} == {
        case_id: list(range(repeats)) for case_id in holdout}
    assert {score for by_repeat in scores.values() for score in by_repeat.values()} == {float(role == "winner")}


@pytest.mark.ac("gh-287:AC-10")
def test_a_search_candidate_with_a_final_timeout_is_recorded_rejected_and_adds_no_planned_call(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, plan = make_target(tmp_path), tmp_path / "run", Plan(timeouts={"winner": 10**6})
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=plan.factory())
    record = read(out / "run.json")
    timed_out = [item for item in _outcomes(out) if item.get("rejection_kind") == "timeout"]
    assert timed_out and all(item["arm"] == "search" and item["status"] == "candidate-contract-rejected"
                             and item["rejection"] == TIMEOUT.reason for item in timed_out)
    frozen = _frozen(record, out)
    kept = {**record, "outcomes": [item for item in _outcomes(out) if item.get("rejection_kind") != "timeout"]}
    assert calls_upper_bound(record, frozen) == calls_upper_bound(kept, frozen)


@pytest.mark.ac("gh-287:AC-12")
@pytest.mark.ac("gh-287:AC-13")
def test_a_final_replay_check_timeout_stops_resumably_and_never_refutes_the_export(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A resume at phase `gated` starts a new memo, so the replay checks the winner again."""
    plan = Plan()
    target, out = _at_gate(tmp_path, make_target, write_draft, approvals, plan)
    gate = Session.gate

    def gate_then_crash(self: Session) -> None:
        gate(self)
        plan.crash_holdout = True

    monkeypatch.setattr(Session, "gate", gate_then_crash)
    with pytest.raises(ProviderCrash):
        _ = run(target, out, MODEL, live=True, factory=plan.factory())
    monkeypatch.setattr(Session, "gate", gate)
    assert read(out / "run.json")["phase"] == "gated"
    plan.crash_holdout, plan.timeouts, plan.checks = False, {"winner": 2}, Counter()
    with pytest.raises(Stop) as caught:
        _ = run(target, out, MODEL, live=True, factory=plan.factory())
    assert caught.value.code == "winner-check-timeout" and plan.checks["winner"] == 2
    record = read(out / "run.json")
    replays = [item for item in _outcomes(out) if item.get("arm") == "replay"]
    assert record["phase"] == "gated" and "replay" not in record
    assert replays and all(item.get("rejection_kind") == "timeout" for item in replays)
    _ = run(target, out, MODEL, live=True, factory=plan.factory())
    record = read(out / "run.json")
    replay = cast(dict[str, object], record["replay"])
    holdout = [item for item in cast(list[dict[str, object]], json.loads((out / "cases.json").read_text())["cases"])
               if item["split"] == "holdout"]
    assert record["phase"] == "complete" and replay["reasons"] == []
    assert replay["cases"] == len(holdout)
