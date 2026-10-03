from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, load_cases
from skillz_experiments._contract import Contract, parse
from skillz_experiments._harness import Configuration, Harness, Role
from skillz_experiments._runtime import Budget

RUBRIC = "RUBRIC_SENTINEL: rate how readable the rewrite is."
GATE_SCRIPT = {
    "pass": 'import json;print(json.dumps({"score": 1}))',
    "fail": 'import json;print(json.dumps({"score": 1}));raise SystemExit(1)',
    "bad-json": 'print("not json")',
    "no-leak": ("import json,pathlib\n"
                "roots = [pathlib.Path.cwd(), pathlib.Path.cwd().parent]\n"
                "leaks = [p for r in roots for p in r.rglob('*') if p.is_file() and 'EXPECTED_SENTINEL' in p.read_text()]\n"
                "print(json.dumps({'score': 0 if leaks else 1}))"),
}
TOKENS = {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 2}


def contract(script: str = GATE_SCRIPT["pass"], flags: tuple[str, ...] = ("-I",)) -> Contract:
    argv = [sys.executable, *flags, "-c", script]
    return parse({"schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
                  "kinds": {"echo": {"grader": "exact-json"},
                            "gate": {"grader": "command", "argv": argv},
                            "style": {"grader": "judge", "rubric": RUBRIC},
                            "rewrite": {"grader": "hybrid", "argv": argv, "rubric": RUBRIC}}}, "skill")


class FakeTask:
    """A task transport without the optional Sandbox capability."""

    def __init__(self, output: dict[str, str] | None = None) -> None:
        self.budget: Budget = Budget(40, 2400, reserve=0)
        self.output: dict[str, str] = {"result.txt": "rewritten\n"} if output is None else output
        self.prompts: list[str] = []
        self.workspaces: list[dict[str, str]] = []

    def preflight(self) -> dict[str, object]:
        return {}

    def close(self) -> None:
        pass

    def check_candidate(self, candidate: Candidate) -> bool:
        del candidate
        return True

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del case, schema
        assert candidate is not None
        self.budget.claim(holdout=holdout)
        self.prompts.append(prompt)
        events = [{"type": "item.completed", "item": {"type": "command_execution", "exit_code": 0,
                   "command": "cat .agents/skills/echo-skill/SKILL.md"}}]
        return {"answer": {"load_marker": candidate.identity, "result_json": '{"echo": "hello"}'}, "events": events,
                "usage": TOKENS.copy(), "workspace": "/TASK", "latency_seconds": 0.1,
                "output_files": dict(self.output)}


class SandboxedTask(FakeTask):
    """A task transport that runs the grader command like a sandbox would."""

    def sandbox(self, workspace: Path, argv: list[str]) -> tuple[int, str]:
        self.workspaces.append({str(path.relative_to(workspace)): path.read_text()
                                for path in sorted(workspace.rglob("*")) if path.is_file()})
        run = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, timeout=20)
        return run.returncode, run.stdout


class FakeJudge:
    def __init__(self, percent: object = 70) -> None:
        self.budget: Budget = Budget(40, 2400, reserve=0)
        self.percent: object = percent
        self.prompts: list[str] = []
        self.schemas: list[dict[str, object] | None] = []

    def preflight(self) -> dict[str, object]:
        return {}

    def close(self) -> None:
        pass

    def check_candidate(self, candidate: Candidate) -> bool:
        del candidate
        return True

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        assert candidate is None and case is None
        self.budget.claim(holdout=holdout)
        self.prompts.append(prompt)
        self.schemas.append(schema)
        return {"answer": {"score_percent": self.percent}, "usage": TOKENS.copy(), "latency_seconds": 0.1}


def harness(monkeypatch: pytest.MonkeyPatch, task: FakeTask, judge: FakeJudge) -> Harness:
    transports = {"task": task, "judge": judge, "reflection": judge}

    def create(self: Role, budget: Budget, checkpoint: object) -> FakeTask | FakeJudge:
        del checkpoint
        transport = transports[self.adapter]
        transport.budget = budget
        return transport

    monkeypatch.setattr(Role, "create", create)
    roles = {name: Role(name, "model", ("fake",), "fake-1") for name in ("task", "judge", "reflection")}
    return Harness(Configuration(roles), Budget(40, 2400, reserve=0), lambda: None)


def case_and_candidate(tmp_path: Path, kind: str, rules: Contract,
                       files: dict[str, str] | None = None) -> tuple[Case, Candidate]:
    item = {"id": "one", "family": "one", "split": "train", "kind": kind, "request": "Rewrite input.txt.",
            "files": {"input.txt": "hello\n"} | (files or {}), "expected": {"echo": "hello", "secret": "EXPECTED_SENTINEL"},
            "provenance": "test", "provider_approved": True}
    path = tmp_path / "cases.json"
    _ = path.write_text(json.dumps({"schema_version": 1, "cases": [item]}))
    return load_cases(path, rules.grader_types())[0], Candidate({"SKILL.md": "seed"}, ("SKILL.md",), contract=rules)


def test_contract_staging_prompt_uses_the_declared_invocation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rules = contract()
    task = FakeTask()
    case, candidate = case_and_candidate(tmp_path, "echo", rules)
    result = harness(monkeypatch, task, FakeJudge()).evaluate(candidate, case)
    prompt = task.prompts[0]
    assert prompt.startswith("$echo-skill run\n")
    assert ".agents/skills/echo-skill/SKILL.md" in prompt
    assert "skillz" not in prompt and "inspect_skill" not in prompt and "EXPECTED_SENTINEL" not in prompt
    assert result["score"] == 0.0 and result["loaded"] is True and result["helper_executed"] is True


@pytest.mark.parametrize(("name", "score"), [("pass", 1.0), ("fail", 0.0), ("bad-json", 0.0)])
def test_command_grader_scores_from_exit_code_and_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                       name: str, score: float) -> None:
    rules = contract(GATE_SCRIPT[name])
    task = SandboxedTask()
    case, candidate = case_and_candidate(tmp_path, "gate", rules)
    result = harness(monkeypatch, task, FakeJudge()).evaluate(candidate, case)
    assert result["score"] == score and result["scores"] == {"command": score}
    assert "output_files" not in result
    workspace = task.workspaces[0]
    assert workspace["output/result.txt"] == "rewritten\n" and workspace["input.txt"] == "hello\n"
    assert "result.txt" not in workspace
    assert "EXPECTED_SENTINEL" not in "".join(workspace.values()) + "".join(workspace) and not any(
        part.startswith("expected") for part in workspace)


def test_command_grader_finds_no_expected_value_anywhere_it_can_read(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    rules = contract(GATE_SCRIPT["no-leak"])
    case, candidate = case_and_candidate(tmp_path, "gate", rules)
    clean = harness(monkeypatch, SandboxedTask(), FakeJudge()).evaluate(candidate, case)
    leaked = harness(monkeypatch, SandboxedTask({"expected.json": "EXPECTED_SENTINEL"}), FakeJudge()).evaluate(candidate, case)
    assert clean["score"] == 1.0 and leaked["score"] == 0.0

def test_candidate_output_cannot_shadow_fixtures_or_modules_for_the_grader(tmp_path: Path,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    script = ('import json,pathlib;ok = pathlib.Path("input.txt").read_text() == "hello\\n"\n'
              'print(json.dumps({"score": 1 if ok else 0}))')
    rules = contract(script, flags=())
    case, candidate = case_and_candidate(tmp_path, "gate", rules)
    task = SandboxedTask({"input.txt": "tampered\n", "json.py": "raise SystemExit(7)\n"})
    result = harness(monkeypatch, task, FakeJudge()).evaluate(candidate, case)
    assert result["score"] == 1.0
    assert task.workspaces[0]["output/input.txt"] == "tampered\n" and task.workspaces[0]["input.txt"] == "hello\n"

def test_command_grader_runs_a_python_argv_as_given_so_fixture_modules_import(tmp_path: Path,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    helper = 'import json\nSCORE = 1\nif __name__ == "__main__":\n    print(json.dumps({"score": SCORE}))\n'
    rules = contract("import helper, json; print(json.dumps({'score': helper.SCORE}))", flags=())
    case, candidate = case_and_candidate(tmp_path, "gate", rules, files={"helper.py": helper})
    assert harness(monkeypatch, SandboxedTask(), FakeJudge()).evaluate(candidate, case)["score"] == 1.0
    module = parse({"schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
                    "kinds": {"gate": {"grader": "command", "argv": [sys.executable, "-m", "helper"]}}}, "skill")
    case, candidate = case_and_candidate(tmp_path, "gate", module, files={"helper.py": helper})
    assert harness(monkeypatch, SandboxedTask(), FakeJudge()).evaluate(candidate, case)["score"] == 1.0


def test_command_grader_needs_a_sandbox_and_output_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rules = contract()
    case, candidate = case_and_candidate(tmp_path, "gate", rules)
    with pytest.raises(ValueError, match="sandbox"):
        _ = harness(monkeypatch, FakeTask(), FakeJudge()).evaluate(candidate, case)
    bare = SandboxedTask()
    original = bare.invoke

    def without_files(*args: object, **kwargs: object) -> dict[str, object]:
        result = original(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        del result["output_files"]
        return result

    bare.invoke = without_files  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="output_files"):
        _ = harness(monkeypatch, bare, FakeJudge()).evaluate(candidate, case)


def test_hybrid_gate_failure_skips_the_judge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rules = contract(GATE_SCRIPT["fail"])
    case, candidate = case_and_candidate(tmp_path, "rewrite", rules)
    judge = FakeJudge()
    result = harness(monkeypatch, SandboxedTask(), judge).evaluate(candidate, case)
    assert judge.prompts == []
    assert result["score"] == 0.0 and result["scores"] == {"command": 0.0, "judge": None}
    assert result["judge_usage"] is None


def test_hybrid_gate_pass_uses_the_judge_score(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rules = contract(GATE_SCRIPT["pass"])
    case, candidate = case_and_candidate(tmp_path, "rewrite", rules)
    judge = FakeJudge(70)
    result = harness(monkeypatch, SandboxedTask(), judge).evaluate(candidate, case)
    assert len(judge.prompts) == 1
    assert result["score"] == 0.7 and result["scores"] == {"command": 1.0, "judge": 0.7}
    assert result["usage"] == {"input_tokens": 20, "cached_input_tokens": 0, "output_tokens": 4}
    assert result["task_usage"] == TOKENS and result["judge_usage"] == TOKENS


def test_judge_grader_uses_a_separate_invocation_and_hides_the_rubric(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rules = contract()
    task, judge = FakeTask(), FakeJudge(85)
    case, candidate = case_and_candidate(tmp_path, "style", rules)
    result = harness(monkeypatch, task, judge).evaluate(candidate, case)
    assert "RUBRIC_SENTINEL" not in task.prompts[0] and "EXPECTED_SENTINEL" not in task.prompts[0]
    assert "RUBRIC_SENTINEL" in judge.prompts[0] and "rewritten" in judge.prompts[0]
    assert judge.schemas[0] is not None and "score_percent" in cast(dict[str, object], judge.schemas[0]["properties"])
    assert result["score"] == 0.85 and result["scores"] == {"judge": 0.85}


@pytest.mark.parametrize("percent", [101, -1, 7.5, True, "70"])
def test_judge_grader_rejects_an_out_of_range_score(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, percent: object) -> None:
    rules = contract()
    case, candidate = case_and_candidate(tmp_path, "style", rules)
    with pytest.raises(ValueError, match="judge"):
        _ = harness(monkeypatch, FakeTask(), FakeJudge(percent)).evaluate(candidate, case)


class ScriptTask(FakeTask):
    """A task transport that reports a chosen script command."""

    def __init__(self, script_command: str) -> None:
        super().__init__()
        self.script_command: str = script_command

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,  # pyright: ignore[reportImplicitOverride]
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        result = super().invoke(prompt, candidate, case, holdout=holdout, schema=schema)
        events = cast(list[dict[str, object]], result["events"])
        events.append({"type": "item.completed", "item": {"type": "command_execution", "exit_code": 0,
                                                           "command": self.script_command}})
        return result


@pytest.mark.parametrize(("command", "executed"), [
    ("python3 -I .agents/skills/echo-skill/scripts/x.py input.txt", True),
    ("python3 .agents/skills/echo-skill/scripts/x.py input.txt", False)])
def test_wedge_script_prompt_requires_isolated_python(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                       command: str, executed: bool) -> None:
    rules = contract()
    task = ScriptTask(command)
    case, seed = case_and_candidate(tmp_path, "echo", rules)
    candidate = Candidate(seed.files | {"scripts/x.py": "print(1)\n"}, seed.editable, rules, script="scripts/x.py")
    result = harness(monkeypatch, task, FakeJudge()).evaluate(candidate, case)
    assert "python3 -I .agents/skills/echo-skill/scripts/x.py" in task.prompts[0]
    assert result["loaded"] is True and result["helper_executed"] is executed
