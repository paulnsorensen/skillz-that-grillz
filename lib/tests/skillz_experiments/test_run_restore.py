"""`run` and `export` behavior at the resume, budget, contract, and redaction boundaries."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast, final

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._cli import main
from skillz_experiments._harness import Configuration, EnvironmentDiffers, Harness
from skillz_experiments._records import read, write
from skillz_experiments._runtime import Budget, BudgetExhausted
from skillz_experiments._search import Edit
from skillz_experiments._workflow import Factory, Stop, _Session, export, run  # pyright: ignore[reportPrivateUsage]

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
    preflight_delay: float = 0.0
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
        if holdout and self.fail_holdout:
            raise RuntimeError("provider fell over")
        improved = "improved" in candidate.files["SKILL.md"]
        result: dict[str, object] = {"score": float(improved), "candidate_hash": candidate.identity,
                                     "case_hash": case.identifier} | self.extra
        if holdout and self.reject_baseline and not improved:
            result["status"] = "candidate-contract-rejected"
        return result

    def holdout_calls(self) -> int:
        return sum(held for _, held in self.evaluated)

    def factory(self) -> Factory:
        return lambda _model, _budget, _checkpoint: _Fake(self)


@final
class _Fake:
    def __init__(self, state: State) -> None:
        self.state: State = state

    def preflight(self) -> dict[str, object]:
        return self.state.preflight(None)

    def close(self) -> None:
        pass

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
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

    def evaluate(self: Harness, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
        del self
        return state.evaluate(candidate, case, holdout)

    def invoke(self: Harness, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del self
        return _Fake(state).invoke(prompt, candidate, case, holdout=holdout, schema=schema)

    def check_candidate(self: Harness, candidate: Candidate) -> bool:
        del self, candidate
        return not state.reject_baseline

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
    with pytest.raises(RuntimeError, match="fell over"):
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
        patch.setattr("skillz_experiments._workflow.judge_identity", _changed_judge)
        with pytest.raises(ValueError, match="judge differs"):
            _ = run(target, out, MODEL, live=True, factory=state.factory())
    assert run(target, out, MODEL, live=True, factory=state.factory())["phase"] == "complete"
    with monkeypatch.context() as patch:
        patch.setattr("skillz_experiments._workflow.judge_identity", _changed_judge)
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
    session = _Session(out, MODEL, "claude", state.factory(), None)
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


def test_a_draft_with_a_small_holdout_stops_with_cases_too_few(
        tmp_path: Path, make_target: Maker) -> None:
    target, out, state = make_target(tmp_path), tmp_path / "run", State()
    sizes = {"a": 1, "b": 1, "c": 2}
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


class _Spending:
    """A fake that claims the budget for each call and spends `delay` seconds on each search call.

    Its preflight reads the time left, as the real transports do through the sandbox probe.
    """

    def __init__(self, state: State, budget: Budget, delay: float) -> None:
        self.state: State = state
        self.budget: Budget = budget
        self.delay: float = delay

    def preflight(self) -> dict[str, object]:
        _ = self.budget.remaining()
        return self.state.preflight(None)

    def close(self) -> None:
        pass

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
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
    assert cast(dict[str, object], recorded["search"])["reason"] == "budget-exhausted-seed-retained"
    assert cast(dict[str, object], recorded["gate"])["reason"] == "winner-equals-baseline"


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
    assert str(cast(dict[str, object], recorded["search"])["reason"]).startswith("budget-exhausted")


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
