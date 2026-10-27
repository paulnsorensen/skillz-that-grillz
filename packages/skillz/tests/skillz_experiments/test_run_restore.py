"""`run` and `export` behavior at the resume, budget, contract, and redaction boundaries."""
from __future__ import annotations

import fcntl
import json
import math
import os
import shutil
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import cast, final

import pytest
from typing_extensions import override

from skillz_experiments._candidate import Candidate
from skillz_experiments.evaluation import CheckResult
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._contract import Contract, Grader
from skillz_experiments._cli import main
from skillz_experiments.harness import ClaudeOptions, Configuration, EnvironmentDiffers, Harness
from skillz_experiments.harness._claude import ClaudeCode
from skillz_experiments.harness._roles import Role
from skillz_experiments._records import read, write
from skillz_experiments.gate import DEFAULT_STATISTICS
from skillz_experiments._budget import APPROVED_SECONDS, MAX_CONCURRENT_CALLS, Budget, BudgetExhausted
from skillz_experiments.search import Edit, overshoot
from skillz_experiments.run import Stop, export, run, summary
from skillz_experiments.run._plan import HOLDOUT_RETRIES_PER_ARM
from skillz_experiments.run._state import Factory
from skillz_experiments.run._plan import (
    _command_seconds as command_seconds,  # pyright: ignore[reportPrivateUsage]
    _plan as plan_run,  # pyright: ignore[reportPrivateUsage]
    plan_estimate,
)
from skillz_experiments.run._open import engine_hash
from skillz_experiments.run._session import Session
from fake_provider import PASSED, FakeRunProvider, ProviderCrash

pytestmark = pytest.mark.usefixtures("host_login")

MODEL = "local-test"
Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]


@dataclass
class State:
    """Shared knobs and counters for one fake provider. The seed scores 0 and a proposal that says `improved` scores 1."""

    environment: str = "e1"
    fail_holdout: bool = False
    reject_baseline: bool = False
    reject_winner: bool = False
    rejection: str | None = None
    preflight_delay: float = 0.0
    claims: bool = False
    extra: dict[str, object] = field(default_factory=lambda: cast(dict[str, object], {}))
    preflights: list[dict[str, object] | None] = field(default_factory=lambda: cast(list[dict[str, object] | None], []))
    evaluated: list[tuple[str, bool]] = field(default_factory=lambda: cast(list[tuple[str, bool]], []))
    lock: threading.Lock = field(default_factory=threading.Lock)

    def preflight(self, recorded: dict[str, object] | None) -> dict[str, object]:
        self.preflights.append(recorded)
        time.sleep(self.preflight_delay)
        return {"environment_hash": self.environment, "isolation": "passed"}

    def evaluate(self, candidate: Candidate, case: Case, holdout: bool) -> dict[str, object]:
        with self.lock:
            self.evaluated.append((case.identifier, holdout))
        improved = "improved" in candidate.files["SKILL.md"]
        if holdout and self.fail_holdout:
            raise ProviderCrash("provider fell over")
        result: dict[str, object] = {"score": float(improved), "candidate_hash": candidate.identity,
                                     "case_hash": case.identifier} | self.extra
        if holdout and (self.reject_winner if improved else self.reject_baseline):
            result["status"] = "candidate-contract-rejected"
            if self.rejection is not None:
                result["rejection"] = self.rejection
        return result

    def holdout_calls(self) -> int:
        return sum(held for _, held in self.evaluated)

    def factory(self) -> Factory:
        return lambda _model, budget, _checkpoint: _Fake(self, budget if self.claims else None)


class _Fake(FakeRunProvider):
    def __init__(self, state: State, budget: Budget | None = None) -> None:
        super().__init__()
        self.state: State = state
        self.budget: Budget | None = budget

    @override
    def preflight(self, recorded: dict[str, object] | None = None) -> dict[str, object]:
        if self.budget is not None:
            self.budget.claim()
        return self.state.preflight(recorded)

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False,
                 checks: object = None) -> dict[str, object]:
        del checks
        if self.budget is not None:
            self.budget.claim(holdout=holdout)
        return self.state.evaluate(candidate, case, holdout)

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del prompt, candidate, case, holdout
        assert schema is not None
        return {"answer": {name: "improved" for name in cast(list[str], schema["required"])}}


def _harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: State) -> Configuration:
    """Return a command-adapter configuration whose `Harness` calls the fake state instead of a process."""
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "command", "identity": "fixture",
                                      "command": [sys.executable]}))

    def preflight(self: Harness, recorded: dict[str, object] | None = None) -> dict[str, object]:
        del self
        return state.preflight(recorded)

    def evaluate(self: Harness, candidate: Candidate, case: Case, *, holdout: bool = False,
                 checks: object = None) -> dict[str, object]:
        del self, checks
        return state.evaluate(candidate, case, holdout)

    def invoke(self: Harness, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del self
        return _Fake(state).invoke(prompt, candidate, case, holdout=holdout, schema=schema)

    def check_candidate(self: Harness, candidate: Candidate) -> CheckResult:
        del self, candidate
        return CheckResult(False, None, "fail") if state.reject_baseline else PASSED

    monkeypatch.setattr(Harness, "preflight", preflight)
    monkeypatch.setattr(Harness, "check_candidate", check_candidate)
    monkeypatch.setattr(Harness, "evaluate", evaluate)
    monkeypatch.setattr(Harness, "invoke", invoke)
    return Configuration.load(config, MODEL)


def _stopped_at_gate(tmp_path: Path, target: Path, write_draft: Drafter, approvals: Approver, state: State,
                     configuration: Configuration | None = None) -> Path:
    """Run until the first holdout call fails. The record keeps the search at phase `searched`."""
    out = tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    state.fail_holdout = True
    with pytest.raises(ProviderCrash, match="fell over"):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
                factory=state.factory() if configuration is None else None, configuration=configuration)
    state.fail_holdout = False
    assert read(out / "run.json")["phase"] == "searched"
    return out


def _prepared(tmp_path: Path, target: Path, write_draft: Drafter, approvals: Approver) -> Path:
    """Give both approvals, then stop at `live-required`. The record holds phase `prepared`."""
    out = tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    with pytest.raises(Stop) as caught:
        _ = run(target, out, MODEL, approve_cases=case_hash, approve_budget=calls)
    assert caught.value.code == "live-required"
    return out


def test_resume_keeps_the_frozen_edit_and_repeats_when_no_flag_is_passed(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, capsys: pytest.CaptureFixture[str]) -> None:
    target, out = make_target(tmp_path, helper=True), tmp_path / "run"
    _ = write_draft(out)
    base = ["run", "--target", str(target), "--out", str(out), "--model", MODEL]
    flags = ["--edit", "prose+cli", "--repeats", "2"]
    assert main([*base, *flags]) == 1
    case_hash = cast(str, json.loads(capsys.readouterr().out)["case_hash"])
    assert main([*base, *flags, "--approve-cases", case_hash]) == 1
    calls = cast(dict[str, int], json.loads(capsys.readouterr().out)["estimate"])["calls"]
    assert main([*base, *flags, "--approve-cases", case_hash, "--approve-budget", str(calls)]) == 1
    assert json.loads(capsys.readouterr().err)["code"] == "live-required"
    record = read(out / "run.json")
    assert (record["edit"], record["repeats"]) == ("prose+cli", 2)
    assert main(base) == 1
    assert json.loads(capsys.readouterr().err)["code"] == "live-required"
    assert main([*base, "--repeats", "5"]) == 1
    assert "differs from the frozen record" in json.loads(capsys.readouterr().err)["error"]


def test_a_second_run_in_the_same_directory_stops_with_run_in_progress(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, state = make_target(tmp_path), State()
    out = _prepared(tmp_path, target, write_draft, approvals)
    with (out / "run.lock").open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(Stop) as caught:
            _ = run(target, out, MODEL, live=True, factory=state.factory())
    assert caught.value.code == "run-in-progress" and "next" in caught.value.data
    assert state.evaluated == [] and read(out / "run.json")["phase"] == "prepared"


def test_an_audit_graded_scored_kind_stops_before_any_approval_or_model_call(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target, out, state = make_target(tmp_path), tmp_path / "run", State()
    path = target / "evals/autoimprove.json"
    document = cast(dict[str, object], json.loads(path.read_text()))
    _ = path.write_text(json.dumps(document | {"kinds": {"echo": {"grader": "audit"}}}))
    _ = write_draft(out)
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, factory=state.factory())
    assert caught.value.code == "contract-audit-unsupported" and "'echo'" in str(caught.value)
    assert state.evaluated == [] and not (out / "run.json").exists()


def test_a_capture_kind_on_a_harness_other_than_claude_stops_before_any_approval_or_model_call(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target, out, state = make_target(tmp_path), tmp_path / "run", State()
    path = target / "evals/autoimprove.json"
    document = cast(dict[str, object], json.loads(path.read_text()))
    _ = path.write_text(json.dumps(document | {"kinds": {"echo": {
        "grader": "judge", "rubric": "r", "capture": ["/usr/bin/python3", "capture.py"]}}}))
    _ = write_draft(out)
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, adapter="codex", live=True, factory=state.factory())
    assert caught.value.code == "contract-capture-unsupported" and "echo" in str(caught.value)
    assert state.evaluated == [] and not (out / "run.json").exists()


def test_a_capture_kind_with_a_non_claude_judge_in_a_supplied_configuration_stops_before_approval(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target, out, state = make_target(tmp_path), tmp_path / "run", State()
    path = target / "evals/autoimprove.json"
    document = cast(dict[str, object], json.loads(path.read_text()))
    _ = path.write_text(json.dumps(document | {"kinds": {"echo": {
        "grader": "judge", "rubric": "r", "capture": ["/usr/bin/python3", "capture.py"]}}}))
    _ = write_draft(out)
    roles = {name: Role("claude" if name == "task" else "codex", "m", ("fake",), "id") for name in ("task", "judge")}
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, configuration=Configuration(roles), factory=state.factory())
    assert caught.value.code == "contract-capture-unsupported" and "echo" in str(caught.value)
    assert not (out / "run.json").exists()


@pytest.mark.parametrize(("variant", "code"), [
    ("helper-file-missing", "helper-file-missing"),
    ("prompt-components-missing", "prompt-components-missing"),
    ("helper-missing", "helper-missing"),
    ("editable-file-missing", "helper-file-missing"),
])
def test_editable_errors_are_coded_and_stop_before_a_provider_exists(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, variant: str, code: str) -> None:
    target, out, state = make_target(tmp_path, helper=variant != "helper-missing"), tmp_path / "run", State()
    path, edit = target / "evals/autoimprove.json", cast(Edit, "prose")
    document = cast(dict[str, object], json.loads(path.read_text()))
    if variant == "helper-file-missing":
        (target / "scripts/echo.py").unlink()
    elif variant == "prompt-components-missing":
        document["editable"] = ["scripts/echo.py"]
        edit = "prose+cli"
    elif variant == "helper-missing":
        edit = "prose+cli"
    else:
        document["editable"] = ["SKILL.md", "missing.md"]
    _ = path.write_text(json.dumps(document))
    _ = write_draft(out)
    with pytest.raises(Stop) as stop:
        _ = run(target, out, MODEL, live=True, edit=edit, factory=state.factory())
    case_hash = cast(str, stop.value.data["case_hash"])
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, edit=edit, approve_cases=case_hash, factory=state.factory())
    assert caught.value.code == code
    assert state.evaluated == [] and state.preflights == [] and not (out / "run.json").exists()


def test_resume_fails_before_any_call_when_the_environment_hash_changed(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, state = make_target(tmp_path), State()
    configuration = _harness(tmp_path, monkeypatch, state)
    out = _stopped_at_gate(tmp_path, target, write_draft, approvals, state, configuration)
    seen = len(state.evaluated)
    state.environment = "e2"
    with pytest.raises(EnvironmentDiffers):
        _ = run(target, out, MODEL, live=True, configuration=configuration)
    assert len(state.evaluated) == seen and read(out / "run.json")["failure"] == "provider fell over"


def test_resume_hands_the_recorded_preflight_to_the_harness_and_runs_it_once_per_run(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, state = make_target(tmp_path), State()
    configuration = _harness(tmp_path, monkeypatch, state)
    out = _stopped_at_gate(tmp_path, target, write_draft, approvals, state, configuration)
    recorded = read(out / "run.json")["preflight"]
    assert state.preflights == [None]
    result = run(target, out, MODEL, live=True, configuration=configuration)
    assert result["phase"] == "complete" and state.preflights == [None, recorded]
    _ = run(target, out, MODEL, live=True, configuration=configuration)
    assert len(state.preflights) == 2


def test_a_resume_with_another_installed_codex_version_stops_as_environment_differs(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A real `Harness` and `Codex` role; only the Codex preflight, which needs the CLI, reports a settable version."""
    from skillz_experiments.harness._codex import Codex

    host = tmp_path / "host-codex"
    host.mkdir()
    _ = (host / "auth.json").write_text("{}")
    stub = tmp_path / "bin" / "codex"
    stub.parent.mkdir()
    _ = stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_HOME", str(host))
    installed = {"version": "codex-cli 0.154.0"}
    target, state = make_target(tmp_path), State()

    def preflight(self: Codex) -> dict[str, object]:
        del self
        return {"codex_version": installed["version"], "isolation": "passed", "live_calls": 0}

    def evaluate(self: Harness, candidate: Candidate, case: Case, *, holdout: bool = False,
                 checks: object = None) -> dict[str, object]:
        del self, checks
        return state.evaluate(candidate, case, holdout)

    def invoke(self: Harness, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del self
        return _Fake(state).invoke(prompt, candidate, case, holdout=holdout, schema=schema)

    monkeypatch.setattr(Codex, "preflight", preflight)

    def accept(self: Codex, candidate: Candidate) -> CheckResult:
        del self, candidate
        return PASSED

    monkeypatch.setattr(Codex, "check_candidate", accept)
    monkeypatch.setattr(Harness, "evaluate", evaluate)
    monkeypatch.setattr(Harness, "invoke", invoke)
    configuration = Configuration.single("codex", MODEL)
    out = _stopped_at_gate(tmp_path, target, write_draft, approvals, state, configuration)
    assert read(out / "run.json")["codex_version"] == "codex-cli 0.154.0"
    seen = len(state.evaluated)
    installed["version"] = "codex-cli 0.160.0"
    with pytest.raises(EnvironmentDiffers):
        _ = run(target, out, MODEL, live=True, configuration=configuration)
    assert len(state.evaluated) == seen
    installed["version"] = "codex-cli 0.154.0"
    assert run(target, out, MODEL, live=True, configuration=configuration)["phase"] == "complete"


def _changed_judge(model: str) -> dict[str, str]:
    return {"model": model, "changed": "yes"}


def test_a_changed_judge_stops_a_resume_and_an_export(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, state = make_target(tmp_path), State()
    shutil.rmtree(target / "evals")
    out = _stopped_at_gate(tmp_path, target, write_draft, approvals, state)
    assert read(out / "run.json")["judge"]
    with monkeypatch.context() as patch:
        patch.setattr("skillz_experiments.run._open.judge_identity", _changed_judge)
        with pytest.raises(ValueError, match="judge differs"):
            _ = run(target, out, MODEL, live=True, factory=state.factory())
    assert run(target, out, MODEL, live=True, factory=state.factory())["phase"] == "complete"
    with monkeypatch.context() as patch:
        patch.setattr("skillz_experiments.run._export.judge_identity", _changed_judge)
        with pytest.raises(ValueError, match="judge differs"):
            _ = export(out, tmp_path / "export")
    assert not (tmp_path / "export").exists()


def test_export_keeps_only_allowed_fields_and_the_grader_scores(
        tmp_path: Path, make_target: Maker, approved_run: Callable[..., dict[str, object]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    state = State(extra={"answer_text": "SECRET-ANSWER", "scores": {"exact": 1.0, "judge": None},
                         "usage": {"input_tokens": 5, "cached_input_tokens": 1, "output_tokens": 2, "raw": "SECRET-USAGE"}})
    _ = approved_run(target, out, factory=state.factory())
    _ = export(out, tmp_path / "export")
    text = (tmp_path / "export/report.json").read_text()
    assert "SECRET-ANSWER" not in text and "SECRET-USAGE" not in text and "answer_text" not in text
    outcomes = cast(list[dict[str, object]], json.loads(text)["outcomes"])
    scored = [item for item in outcomes if "scores" in item]
    assert scored and all(item["scores"] == {"exact": 1.0, "judge": None} for item in scored)
    assert all(item["usage"] == {"input_tokens": 5, "cached_input_tokens": 1, "output_tokens": 2} for item in scored)


def test_the_deadline_is_checked_before_the_first_holdout_call(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, state = make_target(tmp_path), State()
    out = _stopped_at_gate(tmp_path, target, write_draft, approvals, state)
    before = state.holdout_calls()
    record = read(out / "run.json")
    elapsed = time.monotonic() - cast(float, record["started_monotonic"])
    cast(dict[str, object], record["budget"])["seconds"] = elapsed + 0.3
    write(out / "run.json", record)
    state.preflight_delay = 0.5
    with pytest.raises(BudgetExhausted, match="deadline") as caught:
        _ = run(target, out, MODEL, live=True, factory=state.factory())
    assert caught.value.code == "budget-exhausted"
    assert state.holdout_calls() == before
    assert read(out / "run.json")["phase"] == "searched"


def test_a_baseline_that_fails_the_candidate_contract_stops_the_gate(
        tmp_path: Path, make_target: Maker, approved_run: Callable[..., dict[str, object]]) -> None:
    target, out, state = make_target(tmp_path), tmp_path / "run", State(reject_baseline=True)
    with pytest.raises(ValueError, match="original candidate fails the frozen native/helper contract"):
        _ = approved_run(target, out, factory=state.factory())
    record = read(out / "run.json")
    assert record["phase"] == "searched" and "native/helper contract" in str(record["failure"])


def test_resuming_a_rejected_baseline_never_promotes(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state = make_target(tmp_path), tmp_path / "run", State(reject_baseline=True)
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    for attempt in range(12):
        with pytest.raises(Stop) as caught:
            _ = run(target, out, MODEL, live=True, approve_cases=case_hash if attempt == 0 else None,
                    approve_budget=calls if attempt == 0 else None, factory=state.factory())
        assert caught.value.code == "baseline-contract-rejected" and "next" in caught.value.data
    record = read(out / "run.json")
    assert record["phase"] == "searched" and "gate" not in record


@pytest.mark.parametrize("rejection", ["helper fixture 0 fails on stdout", None])
def test_a_rejected_baseline_reports_its_reason_as_the_stop_detail_on_every_run(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver, rejection: str | None) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    state = State(reject_baseline=True, rejection=rejection)
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    for attempt in range(2):
        with pytest.raises(Stop) as caught:
            _ = run(target, out, MODEL, live=True, approve_cases=case_hash if attempt == 0 else None,
                    approve_budget=calls if attempt == 0 else None, factory=state.factory())
        assert caught.value.code == "baseline-contract-rejected"
        assert caught.value.data.get("detail") == rejection
        assert ("detail" in caught.value.data) == (rejection is not None)


def _holdout(out: Path, arm: str) -> list[dict[str, object]]:
    return [item for item in cast(list[dict[str, object]], read(out / "run.json")["outcomes"])
            if item.get("split") == "holdout" and item.get("arm") == arm]


def test_a_winner_rejection_in_the_gate_keeps_its_rejected_status(
        tmp_path: Path, make_target: Maker, approved_run: Callable[..., dict[str, object]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    state = State(reject_winner=True, rejection="helper fixture 0 fails on stdout")
    _ = approved_run(target, out, factory=state.factory())
    winner = _holdout(out, "winner")
    assert winner and all(item["status"] == "candidate-contract-rejected" for item in winner)


def test_a_rejected_seed_stops_before_any_search_call(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, state = make_target(tmp_path), State(reject_baseline=True)
    configuration = _harness(tmp_path, monkeypatch, state)
    out = tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    with pytest.raises(Stop) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, configuration=configuration)
    assert caught.value.code == "baseline-contract-rejected"
    assert state.evaluated == [] and read(out / "run.json")["phase"] == "prepared"


def test_concurrent_case_evaluations_leave_a_valid_checkpoint_with_every_outcome(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, state = make_target(tmp_path), State()
    out = _prepared(tmp_path, target, write_draft, approvals)
    session = Session(out, MODEL, "claude", state.factory(), None, statistics=DEFAULT_STATISTICS)
    try:
        cases = session.cases * 4
        def evaluate(case: Case) -> dict[str, object]:
            return session.evaluate_case(session.seed, case, "search")

        with ThreadPoolExecutor(8) as pool:
            _ = list(pool.map(evaluate, cases))
    finally:
        session.provider.close()
    outcomes = cast(list[object], json.loads((out / "run.json").read_text())["outcomes"])
    assert len(outcomes) == len(cases) == len(state.evaluated)


@pytest.mark.parametrize("key", ["model", "contract", "editable", "budget"])
def test_a_record_that_lacks_a_field_names_it_in_the_error(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver, key: str) -> None:
    target, state = make_target(tmp_path), State()
    out = _prepared(tmp_path, target, write_draft, approvals)
    record = read(out / "run.json")
    del record[key]
    write(out / "run.json", record)
    with pytest.raises(ValueError, match=f"run record lacks {key}"):
        _ = run(target, out, MODEL, live=True, factory=state.factory())


def test_a_task_case_without_the_helper_input_stops_as_cases_invalid(tmp_path: Path, make_target: Maker) -> None:
    target, out, state = make_target(tmp_path, helper=True), tmp_path / "run", State()
    out.mkdir(mode=0o700)
    cases = [{"id": f"{family}-{index}", "family": family, "kind": "task", "request": f"request-{family}-{index}-end",
              "files": {"fixture.md" if family != "b" else "other.md": "hello\n"}, "expected": {}, "source": "skill"}
             for family in "abc" for index in range(3)]
    _ = (out / "cases.draft.json").write_text(json.dumps(cases))
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, factory=state.factory())
    assert caught.value.code == "cases-invalid"
    assert "b-0, b-1, b-2" in str(caught.value) and "fixture.md" in str(caught.value)
    assert state.evaluated == [] and not (out / "run.json").exists()


def test_a_draft_with_a_small_holdout_stops_with_cases_too_few(
        tmp_path: Path, make_target: Maker) -> None:
    target, out, state = make_target(tmp_path), tmp_path / "run", State()
    sizes = {"a": 2, "b": 2, "c": 2, "d": 2, "e": 2}
    out.mkdir(mode=0o700)
    draft = out / "cases.draft.json"
    cases = [{"id": f"{family}-{index}", "family": family, "kind": "task", "request": f"request-{family}-{index}-end",
              "files": {"fixture.md": "hello\n"}, "expected": {}, "source": "skill"}
             for family, count in sizes.items() for index in range(count)]
    _ = draft.write_text(json.dumps(cases))
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, factory=state.factory())
    assert caught.value.code == "cases-too-few" and "minimum is 6" in str(caught.value)
    assert state.evaluated == [] and not (out / "run.json").exists()


def test_the_cli_reports_budget_exhaustion_with_its_code(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def exhausted(*_arguments: object, **_options: object) -> dict[str, object]:
        raise BudgetExhausted("global deadline exhausted")

    monkeypatch.setattr("skillz_experiments._cli.run_workflow", exhausted)
    assert main(["run", "--target", str(tmp_path), "--out", str(tmp_path / "run"), "--model", MODEL]) == 1
    error = cast(dict[str, object], json.loads(capsys.readouterr().err))
    assert error["code"] == "budget-exhausted" and error["error"] == "global deadline exhausted"


def test_an_out_directory_open_to_other_users_is_refused_before_any_call(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target, out, state = make_target(tmp_path), tmp_path / "run", State()
    _ = write_draft(out)
    out.chmod(0o777)
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, factory=state.factory())
    assert caught.value.code == "out-unsafe" and "0700" in str(caught.value)
    assert state.evaluated == [] and not (out / "run.json").exists()


@pytest.mark.ac("gh-493:AC-1")
@pytest.mark.ac("gh-493:AC-4")
def test_an_out_directory_inside_the_target_is_refused_at_start_before_any_call(
        tmp_path: Path, make_target: Maker) -> None:
    target, state = make_target(tmp_path), State()
    out = target / "run"
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, factory=state.factory())
    assert caught.value.code == "out-inside-target" and "mktemp -d" in str(caught.value)
    assert state.evaluated == [] and state.preflights == [] and not out.exists()


@pytest.mark.ac("gh-493:AC-2")
@pytest.mark.ac("gh-493:AC-4")
def test_a_run_directory_moved_inside_the_target_is_refused_on_resume_before_any_call(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, state = make_target(tmp_path), State()
    prepared = _prepared(tmp_path, target, write_draft, approvals)
    out = target / "run"
    _ = shutil.move(prepared, out)
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, factory=state.factory())
    assert caught.value.code == "out-inside-target" and "mktemp -d" in str(caught.value)
    assert state.evaluated == [] and state.preflights == []
    assert read(out / "run.json")["phase"] == "prepared"


@pytest.mark.ac("gh-493:AC-3")
def test_a_sibling_out_directory_that_shares_the_target_name_prefix_passes_the_guard(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target = make_target(tmp_path)
    sibling = target.parent / f"{target.name}-runs"
    _ = write_draft(sibling)
    case_hash, calls = approvals(target, sibling)
    with pytest.raises(Stop) as caught:
        _ = run(target, sibling, MODEL, approve_cases=case_hash, approve_budget=calls)
    assert caught.value.code == "live-required"


def test_a_symlinked_out_directory_is_refused(tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target, state = make_target(tmp_path), State()
    real = tmp_path / "real"
    _ = write_draft(real)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(CodedError) as caught:
        _ = run(target, link, MODEL, live=True, factory=state.factory())
    assert caught.value.code == "out-unsafe"


def test_a_symlinked_run_lock_is_refused_and_its_target_stays_intact(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, state = make_target(tmp_path), State()
    out = _prepared(tmp_path, target, write_draft, approvals)
    victim = tmp_path / "victim.txt"
    _ = victim.write_text("keep me")
    (out / "run.lock").unlink(missing_ok=True)
    os.symlink(victim, out / "run.lock")
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, factory=state.factory())
    assert caught.value.code == "out-unsafe" and "symlink" in str(caught.value)
    assert victim.read_text() == "keep me" and state.evaluated == []


class _Spending(FakeRunProvider):
    """A fake that claims the budget for each call and spends `delay` seconds on each search call.

    Its preflight reads the time left, as the real transports do through the sandbox probe.
    """

    def __init__(self, state: State, budget: Budget, delay: float) -> None:
        super().__init__()
        self.state: State = state
        self.budget: Budget = budget
        self.delay: float = delay

    @override
    def preflight(self, recorded: dict[str, object] | None = None) -> dict[str, object]:
        _ = self.budget.remaining()
        return self.state.preflight(recorded)

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False,
                 checks: object = None) -> dict[str, object]:
        del checks
        self.budget.claim(holdout=holdout)
        if not holdout:
            time.sleep(min(self.delay, self.budget.remaining()))
        return self.state.evaluate(candidate, case, holdout)

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del prompt, candidate, case
        self.budget.claim(holdout=holdout)
        assert schema is not None
        return {"answer": {name: "improved" for name in cast(list[str], schema["required"])}}


def test_a_resume_with_less_time_than_the_reserve_still_reaches_the_gate(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, state = make_target(tmp_path), State()
    out = _prepared(tmp_path, target, write_draft, approvals)
    record = read(out / "run.json")
    budget = cast(dict[str, object], record["budget"])
    # Only half of the gate reserve is left: preflight runs, the search stops at once, and the gate runs.
    budget["seconds"] = time.time() - cast(float, record["started"]) + cast(float, budget["reserve_seconds"]) / 2
    write(out / "run.json", record)
    factory: Factory = lambda _model, spent, _checkpoint: _Spending(state, spent, 0.0)
    assert run(target, out, MODEL, live=True, factory=factory)["phase"] == "complete"
    recorded = read(out / "run.json")
    assert cast(dict[str, object], recorded["search"])["reason"] == "time-exhausted"
    assert "reason" not in cast(dict[str, object], recorded["gate"])


def test_a_slow_search_stops_at_the_gate_reserve_and_the_run_still_completes(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, state = make_target(tmp_path), State()
    out = _prepared(tmp_path, target, write_draft, approvals)
    record = read(out / "run.json")
    budget = cast(dict[str, object], record["budget"])
    reserved = cast(float, budget["reserve_seconds"])
    assert reserved > 0
    # The search gets about 0.5 s; each search call takes 0.2 s, so the planned search cannot finish.
    budget["seconds"] = time.time() - cast(float, record["started"]) + reserved + 0.5
    write(out / "run.json", record)
    factory: Factory = lambda _model, spent, _checkpoint: _Spending(state, spent, 0.2)
    result = run(target, out, MODEL, live=True, factory=factory)
    recorded = read(out / "run.json")
    assert result["phase"] == "complete" and "gate" in recorded
    # How many 0.2 s calls fit before the deadline varies, so the stop reason follows the gh-319 AC-4 rule:
    # a time stop with more than half of the search ceiling unspent is time-exhausted; otherwise it is a budget stop.
    search = cast(dict[str, object], recorded["search"])
    cases = cast(list[dict[str, object]], read(out / "cases.json")["cases"])
    validation = sum(case["split"] == "validation" for case in cases)
    ceiling = cast(int, cast(dict[str, object], recorded["estimate"])["metric_calls"]) - overshoot(validation)
    expected = "time-exhausted" if 2 * cast(int, search["scored"]) < ceiling else "budget-exhausted"
    assert str(search["reason"]).startswith(expected)


def test_a_resume_in_the_searched_phase_gets_the_reserved_time_back(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, state = make_target(tmp_path), State()
    out = _stopped_at_gate(tmp_path, target, write_draft, approvals, state)
    record = read(out / "run.json")
    budget = cast(dict[str, object], record["budget"])
    # Less time is left than the reserve; only the gate may use it now.
    budget["seconds"] = time.time() - cast(float, record["started"]) + cast(float, budget["reserve_seconds"]) / 2
    write(out / "run.json", record)
    assert run(target, out, MODEL, live=True, factory=state.factory())["phase"] == "complete"
    assert state.holdout_calls() > 0


def test_a_run_prepared_by_an_older_runner_stops_with_a_create_a_new_run_message(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target = make_target(tmp_path)
    out = _prepared(tmp_path, target, write_draft, approvals)
    record = read(out / "run.json")
    _ = cast(dict[str, object], record["budget"]).pop("reserve_seconds")
    _ = record.pop("engine_hash", None)
    write(out / "run.json", record)
    with pytest.raises(ValueError, match="older runner; create a new run"):
        _ = run(target, out, MODEL, live=True, factory=State().factory())


def test_a_change_in_a_harness_module_changes_the_engine_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    before = engine_hash()
    original = Path.read_text

    def edited(self: Path) -> str:
        text = original(self)
        return text + "\n# edited" if self.parent.name == "harness" and self.name == "_harness.py" else text

    monkeypatch.setattr(Path, "read_text", edited)
    assert engine_hash() != before


def test_a_prepared_run_records_the_engine_hash(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    out = _prepared(tmp_path, make_target(tmp_path), write_draft, approvals)
    assert isinstance(read(out / "run.json").get("engine_hash"), str)


def test_a_completed_run_returns_its_summary_even_when_the_target_moved(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, state = make_target(tmp_path), State()
    out = _prepared(tmp_path, target, write_draft, approvals)
    summary = run(target, out, MODEL, live=True, factory=state.factory())
    assert summary["phase"] == "complete" and "isolation" not in summary
    moved = tmp_path / "moved"
    _ = shutil.copytree(target, moved)
    assert run(moved, out, MODEL, live=True, factory=state.factory())["phase"] == "complete"


def test_a_taken_seed_name_stops_the_run_before_the_live_preflight(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, state = make_target(tmp_path), State()
    out = _prepared(tmp_path, target, write_draft, approvals)

    @final
    class Taken(_Fake):
        @override
        def check_name(self, candidate: object) -> None:
            assert isinstance(candidate, Candidate)
            raise CodedError("candidate-name-taken", f"{candidate.skill!r} is taken")

    with pytest.raises(CodedError) as stopped:
        _ = run(target, out, MODEL, live=True, factory=lambda _model, _budget, _checkpoint: Taken(state))
    assert stopped.value.code == "candidate-name-taken"
    assert state.preflights == [] and state.evaluated == []


def test_a_run_that_recorded_the_removed_nono_backend_stops_with_the_issue_link(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target = make_target(tmp_path)
    out = _prepared(tmp_path, target, write_draft, approvals)
    record = read(out / "run.json")
    record["isolation"] = "nono"
    write(out / "run.json", record)
    with pytest.raises(CodedError, match="issues/211") as stopped:
        _ = run(target, out, MODEL, live=True, factory=State().factory())
    assert stopped.value.code == "nono-removed"


def test_a_run_that_recorded_the_claude_backend_still_resumes(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, state = make_target(tmp_path), State()
    out = _prepared(tmp_path, target, write_draft, approvals)
    record = read(out / "run.json")
    record["isolation"] = "claude"
    write(out / "run.json", record)
    assert run(target, out, MODEL, live=True, factory=state.factory())["phase"] == "complete"


def test_a_terminated_run_resumed_with_another_flag_reports_the_termination_first(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target = make_target(tmp_path)
    out = _prepared(tmp_path, target, write_draft, approvals)
    record = read(out / "run.json")
    record["failure_code"] = "credential-changed"
    write(out / "run.json", record)
    with pytest.raises(Stop) as stopped:
        _ = run(target, out, MODEL, live=True, seed=999, factory=State().factory())
    assert stopped.value.code == "run-terminated"


def test_a_resume_with_another_model_stops_before_the_live_check(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target = make_target(tmp_path)
    out = _prepared(tmp_path, target, write_draft, approvals)
    with pytest.raises(CodedError) as stopped:
        _ = run(target, out, "other-model", live=False)
    assert stopped.value.code == "run-config-differs"


def test_a_first_run_records_the_claude_options_and_a_resume_must_match_them(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target = make_target(tmp_path)
    out = tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    with pytest.raises(Stop) as caught:
        _ = run(target, out, MODEL, approve_cases=case_hash, approve_budget=calls, effort="xhigh", sandbox_seconds=120)
    assert caught.value.code == "live-required"
    assert read(out / "run.json")["claude_options"] == {"effort": "xhigh", "sandbox_seconds": 120}
    with pytest.raises(Stop) as resumed:
        _ = run(target, out, MODEL, effort="xhigh")
    assert resumed.value.code == "live-required"
    with pytest.raises(CodedError) as seconds:
        _ = run(target, out, MODEL, sandbox_seconds=60)
    with pytest.raises(CodedError) as effort:
        _ = run(target, out, MODEL, effort="high")
    assert seconds.value.code == effort.value.code == "run-config-differs"
    assert "--sandbox-seconds" in str(seconds.value) and "--effort" in str(effort.value)


def test_claude_options_are_refused_for_the_codex_harness(tmp_path: Path, make_target: Maker) -> None:
    with pytest.raises(ValueError, match="only to --harness claude"):
        _ = run(make_target(tmp_path), tmp_path / "run", MODEL, adapter="codex", effort="high")


def _claude_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    executable = tmp_path / "bin" / "claude"
    executable.parent.mkdir()
    _ = shutil.copyfile(Path(__file__).parent / "fixtures/fake_claude.py", executable)
    executable.chmod(0o755)
    _ = executable.with_name("claude.mode").write_text("ok")
    monkeypatch.setenv("PATH", f"{executable.parent}{os.pathsep}{os.environ['PATH']}")
    return executable


def test_a_live_resume_gives_the_recorded_claude_options_to_every_call(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    with pytest.raises(Stop):
        _ = run(target, out, MODEL, approve_cases=case_hash, approve_budget=calls, effort="xhigh", sandbox_seconds=120)
    executable = _claude_on_path(tmp_path, monkeypatch)
    session = Session(out, MODEL, "claude", None, None, statistics=DEFAULT_STATISTICS)
    try:
        provider = cast(Harness, session.provider)
        _ = provider.transports["judge"].invoke("hello")
        assert cast(ClaudeCode, provider.transports["task"]).options.sandbox_seconds == 120
    finally:
        session.provider.close()
    argv = cast(list[str], json.loads(executable.with_name("claude.log").read_text().splitlines()[0])["argv"])
    assert argv[argv.index("--effort") + 1] == "xhigh"


def test_the_cli_passes_each_repeated_sandbox_read_to_run(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake(*_arguments: object, **options: object) -> dict[str, object]:
        seen.update(options)
        return {}

    monkeypatch.setattr("skillz_experiments._cli.run_workflow", fake)
    assert main(["run", "--target", str(tmp_path), "--out", str(tmp_path / "run"), "--model", MODEL,
                 "--sandbox-read", "/opt/a", "--sandbox-read", "/opt/b", "--effort", "high", "--sandbox-seconds", "90"]) == 0
    assert (seen["sandbox_read"], seen["effort"], seen["sandbox_seconds"]) == (["/opt/a", "/opt/b"], "high", 90)


def test_a_resume_compares_sandbox_read_as_a_canonical_set_and_the_summary_reports_it(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    roots = [tmp_path / "browsers", tmp_path / "fonts"]
    for root in roots:
        root.mkdir()
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    with pytest.raises(Stop):
        _ = run(target, out, MODEL, approve_cases=case_hash, approve_budget=calls,
                sandbox_read=[f"{roots[0]}/", str(roots[1]), str(roots[0])])
    record = read(out / "run.json")
    assert record["claude_options"] == {"sandbox_read": [str(roots[0]), str(roots[1])]}
    assert summary(record)["claude_options"] == record["claude_options"]
    with pytest.raises(Stop) as reordered:
        _ = run(target, out, MODEL, sandbox_read=[str(roots[1]), str(roots[0])])
    assert reordered.value.code == "live-required"
    with pytest.raises(CodedError) as fewer:
        _ = run(target, out, MODEL, sandbox_read=[str(roots[0])])
    assert fewer.value.code == "run-config-differs" and "--sandbox-read" in str(fewer.value)


def test_both_approval_stops_echo_the_claude_options(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    with pytest.raises(Stop) as cases:
        _ = run(target, out, MODEL, effort="high")
    assert cases.value.code == "cases-unapproved" and cases.value.data["claude_options"] == {"effort": "high"}
    with pytest.raises(Stop) as budget:
        _ = run(target, out, MODEL, effort="high", approve_cases=cast(str, cases.value.data["case_hash"]))
    assert budget.value.code == "budget-unapproved" and budget.value.data["claude_options"] == {"effort": "high"}


def test_a_supplied_configuration_refuses_option_values_and_its_roots_stop_before_the_approvals(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    configuration = _harness(tmp_path, monkeypatch, State())
    with pytest.raises(ValueError, match="built-in harness"):
        _ = run(target, out, MODEL, configuration=configuration, effort="high")
    with pytest.raises(ValueError, match="built-in harness"):
        _ = run(target, out, MODEL, factory=State().factory(), effort="high")
    roles = tmp_path / "roles.json"
    _ = roles.write_text(json.dumps({"schema_version": 1, "adapter": "claude", "command": [sys.executable],
                                     "sandbox_read": [str(tmp_path / "missing")]}))
    with pytest.raises(CodedError) as missing:
        _ = run(target, out, MODEL, configuration=Configuration.load(roles, MODEL))
    assert missing.value.code == "sandbox-read-invalid" and not (out / "run.json").exists()
    case_hash, calls = approvals(target, out)
    with pytest.raises(Stop) as prepared:
        _ = run(target, out, MODEL, approve_cases=case_hash, approve_budget=calls, configuration=configuration)
    assert prepared.value.code == "live-required" and "claude_options" not in read(out / "run.json")


def test_sandbox_time_of_command_graders_adds_to_the_estimate_and_the_gate_reserve() -> None:
    plain, slow = plan_run(6, 6, 6, 1, 3), plan_run(6, 6, 6, 1, 3, 30)
    gate_evaluations = 2 * 6 * 3 + 2 * HOLDOUT_RETRIES_PER_ARM + 6
    assert slow.sandbox_gate == math.ceil(gate_evaluations * 30 / MAX_CONCURRENT_CALLS)
    assert slow.sized.calls == plain.sized.calls
    assert slow.sized.seconds == plain.sized.seconds + slow.sandbox_total > plain.sized.seconds
    assert slow.reserve_seconds > plain.reserve_seconds
    assert plan_estimate(slow, 3, 6)["sandbox_seconds"] == slow.sandbox_total
    assert "sandbox_seconds" not in plan_estimate(plain, 3, 6)
    contract = Contract("s", "$s", {"task": Grader("command", ("x",)), "plain": Grader("exact-json")})
    task, other = (cast(Case, cast(object, SimpleNamespace(kind=kind))) for kind in ("task", "plain"))
    options = ClaudeOptions(sandbox_seconds=90)
    assert command_seconds(contract, [task], options) == 90 and command_seconds(contract, [other], options) == 0
    judged = Contract("s", "$s", {"look": Grader("judge", rubric="r", capture=("x",)),
                                   "mix": Grader("hybrid", ("x",), "r", ("x",)), "hy": Grader("hybrid", ("x",), "r")})
    look, mix, hybrid = (cast(Case, cast(object, SimpleNamespace(kind=kind))) for kind in ("look", "mix", "hy"))
    assert command_seconds(judged, [look], options) == 90 and command_seconds(judged, [hybrid], options) == 90
    assert command_seconds(judged, [mix], options) == 180 and command_seconds(judged, [look, mix], options) == 180


def test_the_search_shrinks_until_the_sandbox_time_fits_the_time_cap_and_a_fixed_cost_over_it_stops() -> None:
    # Each validation case runs twice (gh-500), so the plan admits 10 validation cases here, not 20.
    plain = plan_run(20, 10, 6, 1, 3)
    shrunk = [plan_run(20, 10, 6, 1, 3, seconds) for seconds in (30, 40, 60)]
    assert all(plan.sized.seconds <= APPROVED_SECONDS for plan in shrunk)
    assert shrunk[0].metric_calls < plain.metric_calls and shrunk[2].metric_calls < shrunk[0].metric_calls
    with pytest.raises(Stop, match="sandbox time alone need") as caught:
        _ = plan_run(20, 10, 6, 1, 3, 600)
    assert caught.value.code == "budget-unapproved"


def _command_target(tmp_path: Path, make_target: Maker) -> Path:
    target = make_target(tmp_path)
    contract = cast(dict[str, object], json.loads((target / "evals/autoimprove.json").read_text()))
    contract["kinds"] = {"echo": {"grader": "command", "argv": ["x"]}}
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(contract))
    return target


def test_a_run_prepared_before_command_grader_sandbox_time_stops_as_an_older_runner(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target = _command_target(tmp_path, make_target)
    out = _prepared(tmp_path, target, write_draft, approvals)
    record = read(out / "run.json")
    estimate = cast(dict[str, object], record["estimate"])
    assert "sandbox_seconds" in estimate
    _ = estimate.pop("sandbox_seconds")
    write(out / "run.json", record)
    with pytest.raises(ValueError, match="older runner; create a new run"):
        _ = run(target, out, MODEL, live=True, factory=State().factory())


def test_a_run_prepared_before_the_replay_stops_as_an_older_runner(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target = make_target(tmp_path)
    out = _prepared(tmp_path, target, write_draft, approvals)
    record = read(out / "run.json")
    estimate = cast(dict[str, object], record["estimate"])
    assert "replay_calls" in estimate
    _ = estimate.pop("replay_calls")
    write(out / "run.json", record)
    with pytest.raises(ValueError, match="older runner; create a new run"):
        _ = run(target, out, MODEL, live=True, factory=State().factory())

def test_a_supplied_configuration_sizes_the_run_from_its_task_role_and_leaves_claude_options_out_of_the_stops(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target, out = _command_target(tmp_path, make_target), tmp_path / "run"
    _ = write_draft(out)
    roles = tmp_path / "roles.json"
    _ = roles.write_text(json.dumps({"schema_version": 1, "adapter": "claude", "command": [sys.executable],
                                     "sandbox_seconds": 100}))
    configuration = Configuration.load(roles, MODEL)
    with pytest.raises(Stop) as cases:
        _ = run(target, out, MODEL, configuration=configuration)
    assert cases.value.code == "cases-unapproved" and "claude_options" not in cases.value.data
    case_hash = cast(str, cases.value.data["case_hash"])
    with pytest.raises(Stop) as budget:
        _ = run(target, out, MODEL, configuration=configuration, approve_cases=case_hash)
    assert budget.value.code == "budget-unapproved" and "claude_options" not in budget.value.data
    estimate = cast(dict[str, int], budget.value.data["estimate"])
    headroom = cast(dict[str, int], cast(dict[str, object], budget.value.data["estimate"])["headroom"])
    evaluations = (estimate["metric_calls"] + headroom["outcomes"] + 2 * estimate["holdout_cases"] * estimate["repeats"]
                   + 2 * HOLDOUT_RETRIES_PER_ARM + estimate["holdout_cases"])
    assert estimate["sandbox_seconds"] == math.ceil(evaluations * 100 / MAX_CONCURRENT_CALLS)

@pytest.mark.parametrize("change", ["seed", "target"])
def test_a_resume_with_another_seed_or_target_stops_before_any_call(
        change: str, tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, state = make_target(tmp_path), State()
    out = _prepared(tmp_path, target, write_draft, approvals)
    other = tmp_path / "copy"
    _ = shutil.copytree(target, other)
    with pytest.raises(CodedError) as caught:
        if change == "seed":
            _ = run(target, out, MODEL, live=True, seed=7, factory=state.factory())
        else:
            _ = run(other, out, MODEL, live=True, factory=state.factory())
    assert caught.value.code == "run-config-differs"
    assert state.preflights == [] and state.evaluated == []


def test_a_resume_with_the_recorded_seed_runs(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, state = make_target(tmp_path), State()
    out = _prepared(tmp_path, target, write_draft, approvals)
    seed = cast(int, read(out / "run.json")["split_seed"])
    assert run(target, out, MODEL, live=True, seed=seed, factory=state.factory())["phase"] == "complete"


def test_a_first_call_waits_for_the_run_lock_before_it_writes_the_record(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target = make_target(tmp_path)
    out = tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    frozen = (out / "cases.json").stat().st_mtime_ns if (out / "cases.json").exists() else None
    with (out / "run.lock").open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(Stop) as caught:
            _ = run(target, out, MODEL, approve_cases=case_hash, approve_budget=calls)
    assert caught.value.code == "run-in-progress"
    assert not (out / "run.json").exists()
    assert ((out / "cases.json").stat().st_mtime_ns if (out / "cases.json").exists() else None) == frozen


_USAGE = {"input_tokens": 10, "cached_input_tokens": 4, "output_tokens": 3}


def _spend_record(**extra: object) -> dict[str, object]:
    """Return a run record with one journey, one exact-json, and one reflection outcome."""
    outcomes: list[dict[str, object]] = [
        {"split": "train", "case_id": "j", "task_usage": _USAGE, "judge_usage": _USAGE | {"input_tokens": 1},
         "simulator_usage": _USAGE | {"input_tokens": 7}, "latency_seconds": 2.0, "judge_latency_seconds": 0.5},
        {"split": "train", "case_id": "e", "usage": _USAGE, "latency_seconds": 1.0},
        {"split": "reflection", "usage": _USAGE, "latency_seconds": 4.0}]
    return {"model": "task-m", "calls": 0, "outcomes": outcomes} | extra


def test_the_summary_reports_spend_per_role_with_the_model_each_role_used() -> None:
    harness = {name: {"model": model} for name, model in
               (("task", "task-m"), ("reflection", "task-m"), ("judge", "judge-m"))}
    roles = cast(dict[str, dict[str, object]], summary(_spend_record(harness=harness))["usage_by_role"])
    assert roles["task"] == {"outcomes": 2, "input_tokens": 20, "cached_tokens": 8, "output_tokens": 6,
                             "latency_seconds": 3.0, "model": "task-m"}
    assert roles["judge"] == {"outcomes": 1, "input_tokens": 1, "cached_tokens": 4, "output_tokens": 3,
                              "latency_seconds": 0.5, "model": "judge-m"}
    assert roles["simulator"]["input_tokens"] == 7 and roles["simulator"]["latency_seconds"] is None
    assert roles["simulator"]["model"] == "judge-m"
    assert roles["reflection"] == {"outcomes": 1, "input_tokens": 10, "cached_tokens": 4, "output_tokens": 3,
                                   "latency_seconds": 4.0, "model": "task-m"}


def test_a_role_without_usage_shows_null_tokens_and_no_model() -> None:
    record = _spend_record()
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    outcomes[1]["usage"] = None
    outcomes[2]["usage"] = {"input_tokens": None}
    del outcomes[0]["task_usage"]
    roles = cast(dict[str, dict[str, object]], summary(record)["usage_by_role"])
    assert roles["task"]["outcomes"] == 0 and roles["task"]["model"] is None
    assert all(roles["task"][name] is None for name in ("input_tokens", "cached_tokens", "output_tokens"))
    assert roles["reflection"]["outcomes"] == 1 and roles["reflection"]["input_tokens"] is None
    empty = cast(dict[str, dict[str, object]], summary({"model": "m", "outcomes": []})["usage_by_role"])
    assert empty["judge"] == {"outcomes": 0, "input_tokens": None, "cached_tokens": None, "output_tokens": None,
                              "latency_seconds": None, "model": None}


def test_an_outcome_without_usage_or_without_a_task_call_does_not_count_for_a_role() -> None:
    record = _spend_record()
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    outcomes.append({"split": "train", "case_id": "g", "usage": _USAGE, "task_usage": _USAGE, "judge_usage": None,
                     "latency_seconds": 1.0})
    outcomes.append({"split": "train", "case_id": "r", "status": "candidate-contract-rejected", "usage": _USAGE,
                     "latency_seconds": 1.0})
    roles = cast(dict[str, dict[str, object]], summary(record)["usage_by_role"])
    assert roles["judge"]["outcomes"] == 1
    assert roles["task"]["outcomes"] == 3


def test_a_simulator_without_judged_cases_reports_the_model_the_judge_role_ran_on() -> None:
    """`judge_model` stays unset when no case is judged, but a simulator then ran on the judge role."""
    record = _spend_record(judge_model_flag="flag-judge")
    roles = cast(dict[str, dict[str, object]], summary(record)["usage_by_role"])
    assert roles["simulator"]["model"] == "flag-judge"
    assert cast(dict[str, dict[str, object]], summary(_spend_record())["usage_by_role"])["simulator"]["model"] == "task-m"


def test_a_preflight_that_fails_after_its_claim_saves_the_count(
        tmp_path: Path, make_target: Maker, approved_run: Callable[..., dict[str, object]]) -> None:
    target, out, state = make_target(tmp_path), tmp_path / "run", State(claims=True)

    class Failing(_Fake):
        @override
        def preflight(self, recorded: dict[str, object] | None = None) -> dict[str, object]:
            _ = super().preflight(recorded)
            raise EnvironmentDiffers("the environment changed after the claim")

    def factory(_model: str, budget: Budget, _checkpoint: Callable[[], None]) -> Failing:
        return Failing(state, budget)

    with pytest.raises(EnvironmentDiffers):
        _ = approved_run(target, out, factory=factory)
    record = read(out / "run.json")
    assert record["preflight_calls"] == 1 == record["calls"]


def test_the_summary_reconciles_calls_with_the_worst_case_bound_and_flags_overspend(
        tmp_path: Path, make_target: Maker, approved_run: Callable[..., dict[str, object]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    state = State(extra={"usage": _USAGE}, claims=True)
    finished = approved_run(target, out, factory=state.factory())
    assert isinstance(finished["calls"], int) and isinstance(finished["calls_upper_bound"], int)
    assert finished["calls"] <= finished["calls_upper_bound"] and finished["calls_upper_bound"] > 0
    assert "usage_gap" not in finished
    assert read(out / "run.json")["preflight_calls"] == 1 and finished["calls"] > 1
    unrecorded = read(out / "run.json")
    del unrecorded["preflight_calls"]
    unrecorded["calls"] = finished["calls_upper_bound"] + 3
    legacy = summary(unrecorded, out)
    assert legacy["calls_upper_bound"] is None and "usage_gap" not in legacy
    rejected = read(out / "run.json")
    outcomes = cast(list[dict[str, object]], rejected["outcomes"])
    evaluated = next(item for item in outcomes if item.get("split") != "reflection")
    outcomes.append({**evaluated, "status": "candidate-contract-rejected"})
    assert summary(rejected, out)["calls_upper_bound"] == finished["calls_upper_bound"]
    task = cast(dict[str, dict[str, object]], finished["usage_by_role"])["task"]
    assert task["outcomes"] and task["input_tokens"] == 10 * cast(int, task["outcomes"])
    record = read(out / "run.json")
    record["calls"] = finished["calls_upper_bound"] + 3
    assert summary(record, out)["usage_gap"] == 3
    _ = export(out, tmp_path / "export")
    report = cast(dict[str, object], json.loads((tmp_path / "export/report.json").read_text()))
    assert report["usage_by_role"] == finished["usage_by_role"] and report["cost_usd"] is None
