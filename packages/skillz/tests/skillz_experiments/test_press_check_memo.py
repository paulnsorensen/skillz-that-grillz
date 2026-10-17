"""Press attacks on the session `CheckMemo` (gh-247): rejection reuse, lock scope, and one check per identity.

The attacks use the real Claude transport for the early-return rejection, the real `evaluate` for the lock scope,
and a full command-harness run (GEPA with `parallel=True`) for the session memo.
"""
from __future__ import annotations

import json
import shutil
import stat
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, cast, final

import pytest

from skillz_experiments._budget import Budget
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case
from skillz_experiments._contract import Contract
from skillz_experiments._records import read
from skillz_experiments.evaluation import CheckMemo, evaluate
from skillz_experiments.harness import Configuration
from skillz_experiments.harness._claude import ClaudeCode
from skillz_experiments.harness._command import Command
from skillz_experiments._intake import intake_contract
from skillz_experiments.run import run
from skillz_experiments.run._plan import PREFLIGHT_CALLS
from typing_extensions import override
from fake_provider import FakeProvider
from memo_fixtures import SKILL, cases as _cases, contract as _contract

if TYPE_CHECKING:
    from .conftest import JudgedRun

pytestmark = pytest.mark.usefixtures("host_login", "headroom_off")

FAKE_CLAUDE = Path(__file__).parent / "fixtures/fake_claude.py"
WAIT = 10.0


@pytest.mark.ac("gh-247:AC-2")
def test_a_real_claude_early_rejection_keeps_none_after_another_candidate_left_a_reason(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D-7: `_transport.py` returns False without a reason. A reason that an earlier check left must not leak in."""
    def sandbox(self: ClaudeCode, workspace: Path, argv: list[str], seconds: int | None = None) -> tuple[int, str, str]:
        del self, workspace, argv, seconds
        return 3, "", ""

    monkeypatch.setattr(ClaudeCode, "sandbox", sandbox)
    executable = tmp_path / "bin" / "claude"
    executable.parent.mkdir()
    _ = shutil.copyfile(FAKE_CLAUDE, executable)
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    _ = executable.with_name("claude.mode").write_text("ok")
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "claude", "command": [str(executable)]}))
    budget = Budget(10, 120, 0)
    harness = Configuration.load(config, "claude-test-model").create("claude-test-model", budget, lambda: None)
    contract = _contract(helper=True)
    helper = Candidate({"SKILL.md": f"---\nname: {SKILL}\ndescription: echo\n---\n", "scripts/echo.py": "print(1)\n"},
                       ("SKILL.md", "scripts/echo.py"), contract)
    undeclared = Candidate({"SKILL.md": f"---\nname: {SKILL}\n---\n", "scripts/echo.py": "print(1)\n"},
                           ("SKILL.md", "scripts/echo.py"), contract)
    cases, memo = _cases(tmp_path, contract), CheckMemo()
    try:
        first = harness.evaluate(helper, cases[0], checks=memo)
        reason = harness.rejection
        assert isinstance(reason, str) and reason.startswith("helper fixture 0 fails on ")
        early = [harness.evaluate(undeclared, case, checks=memo) for case in cases]
        late = [harness.evaluate(helper, case, checks=memo) for case in cases[1:]]
    finally:
        harness.close()
    assert [(item["status"], item["score"], item["rejection"]) for item in early] == [
        ("candidate-contract-rejected", 0.0, None)] * 3
    assert [(item["status"], item["rejection"]) for item in [first, *late]] == [
        ("candidate-contract-rejected", reason)] * 3
    assert budget.calls == 0


class Timeout(RuntimeError):
    """A wait in the attack fake timed out."""


@final
class Gated(FakeProvider):
    """A task transport whose model call for `blocked` waits until another identity gets its contract check."""

    def __init__(self, blocked: str) -> None:
        self.budget = Budget(10, 60.0)
        self.blocked = blocked
        self.in_call = threading.Event()
        self.other_checked = threading.Event()
        self.lock = threading.Lock()
        self.checked: list[str] = []

    @override
    def check_candidate(self, candidate: object) -> bool:
        identity = cast(Candidate, candidate).identity
        with self.lock:
            self.checked.append(identity)
        if identity != self.blocked:
            self.other_checked.set()
        return True

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del prompt, case, holdout, schema
        if candidate is not None and candidate.identity == self.blocked:
            self.in_call.set()
            if not self.other_checked.wait(WAIT):
                raise Timeout("another identity could not get its contract check during a model call")
        raise Timeout("stop after the model call")


def test_the_memo_lock_is_free_while_a_model_call_runs(tmp_path: Path) -> None:
    """The lock must cover only the check. A model call that holds it would serialize or deadlock the search."""
    contract = _contract(helper=False)
    slow = Candidate({"SKILL.md": "slow"}, ("SKILL.md",), contract=contract)
    fresh = Candidate({"SKILL.md": "fresh"}, ("SKILL.md",), contract=contract)
    case = _cases(tmp_path, contract)[0]
    task, memo = Gated(slow.identity), CheckMemo()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(evaluate, task, task, slow, case, checks=memo)
        assert task.in_call.wait(WAIT)
        second = pool.submit(evaluate, task, task, fresh, case, checks=memo)
        with pytest.raises(Timeout, match="stop after the model call"):
            _ = second.result(timeout=WAIT)
        with pytest.raises(Timeout, match="stop after the model call"):
            _ = first.result(timeout=WAIT)
    assert task.checked == [slow.identity, fresh.identity]


@pytest.mark.ac("gh-247:AC-1")
def test_a_full_command_run_checks_the_seed_once_across_check_seed_and_every_case(
        judged: JudgedRun, monkeypatch: pytest.MonkeyPatch) -> None:
    """The production Harness and `check_seed` share the session memo, so the seed gets one check in the whole run."""
    original = Command.check_candidate
    lock = threading.Lock()
    counts: Counter[str] = Counter()

    def counted(self: Command, candidate: Candidate) -> bool:
        with lock:
            counts[candidate.identity] += 1
        return original(self, candidate)

    monkeypatch.setattr(Command, "check_candidate", counted)
    _ = judged.start()
    outcomes = cast(list[dict[str, object]], read(judged.out / "run.json")["outcomes"])
    evaluated = Counter(str(item["candidate_hash"]) for item in outcomes if "candidate_hash" in item)
    assert max(evaluated.values()) > 1
    assert set(evaluated) <= set(counts)
    assert {identity: count for identity, count in counts.items() if count != 1} == {}


@final
class Searcher(FakeProvider):
    """A provider that routes each evaluation through the session memo, as `Harness.evaluate` does, then scores.

    Proposals are new identities, so GEPA's `parallel=True` minibatch evaluates each one on several cases at once.
    """

    def __init__(self, budget: Budget, checkpoint: Callable[[], None], counts: Counter[str], contract: Contract) -> None:
        self.budget = budget
        self.checkpoint = checkpoint
        self.counts = counts
        self.contract = contract
        self.lock = threading.Lock()
        self.proposals = 0

    @override
    def preflight(self) -> dict[str, object]:
        if self.budget.calls == 0:
            for _ in range(PREFLIGHT_CALLS):
                self.budget.claim()
        return {"isolation": "test-provider"}

    @override
    def check_candidate(self, candidate: object) -> bool:
        identity = cast(Candidate, candidate).identity
        with self.lock:
            self.counts[identity] += 1
        time.sleep(0.02)
        return True

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False,
                 checks: CheckMemo) -> dict[str, object]:
        passed, _ = checks.check(self, candidate)
        assert passed
        for _ in range(self.contract.calls(case.kind)):
            self.budget.claim(holdout=holdout)
        self.checkpoint()
        score = 0.8 if "improved" in candidate.files["SKILL.md"] else 0.2
        return {"score": score, "candidate_hash": candidate.identity, "case_hash": case.identifier}

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del prompt, candidate, case
        assert schema is not None
        self.budget.claim(holdout=holdout)
        with self.lock:
            self.proposals += 1
            number = self.proposals
        return {"answer": {name: f"improved {number}" for name in cast(list[str], schema["required"])}}


@pytest.mark.ac("gh-247:AC-3")
def test_gepa_parallel_minibatches_check_each_new_identity_once(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    """GEPA scores a new proposal on its minibatch in parallel. The session memo must still check it once."""
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    counts: Counter[str] = Counter()
    contract = intake_contract(target)

    def factory(_model: str, budget: Budget, checkpoint: Callable[[], None]) -> Searcher:
        return Searcher(budget, checkpoint, counts, contract)

    _ = run(target, out, "local-test", live=True, approve_cases=case_hash, approve_budget=calls, factory=factory)
    outcomes = cast(list[dict[str, object]], read(out / "run.json")["outcomes"])
    seed = next(iter(counts))
    proposed = Counter(str(item["candidate_hash"]) for item in outcomes
                       if item.get("arm") == "search" and item.get("candidate_hash") not in (None, seed))
    assert proposed and max(proposed.values()) > 1
    assert {identity: count for identity, count in counts.items() if count != 1} == {}
