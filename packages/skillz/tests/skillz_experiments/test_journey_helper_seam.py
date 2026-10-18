"""The real `evaluate` path: a contract helper applies to task cases only, never to a journey case."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._budget import Budget
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import load_cases
from skillz_experiments._contract import Contract, parse
from skillz_experiments.evaluation import CheckMemo
from skillz_experiments.evaluation._evaluator import evaluate
from skillz_experiments.harness._claude import ClaudeCode
from test_journey_press import REQUEST, RUBRIC, SIMULATE_THEN_DONE, Judge, gate_argv, install, open_session

pytestmark = pytest.mark.usefixtures("host_login")

SKILL_TEXT = "---\nname: skillz\ndescription: x\n---\nbody\n"


def _contract() -> Contract:
    document: dict[str, object] = {
        "schema_version": 1, "status": "approved", "skill": "skillz", "invocation": "$skillz run",
        "helper": {"path": "run.py", "input": "input.md", "fixtures": []},
        "kinds": {"task": {"grader": "judge", "rubric": RUBRIC},
                  "journey": {"grader": "journey", "argv": gate_argv(), "rubric": RUBRIC}}}
    return parse(document, "skill")


def _cases(tmp_path: Path, rules: Contract) -> dict[str, object]:
    common = {"family": "f", "split": "train", "request": REQUEST, "provenance": "test", "provider_approved": True}
    items = [common | {"id": "t1", "kind": "task", "files": {"input.md": "x\n"}, "expected": {"expected_output": "x"}},
             common | {"id": "j1", "kind": "journey", "brief": "wants a plan", "max_turns": 4,
                       "files": {"input.txt": "hello\n"}, "expected": {"must": ["a plan"]}}]
    path = tmp_path / "cases.json"
    _ = path.write_text(json.dumps({"schema_version": 1, "cases": items}))
    return {case.identifier: case for case in load_cases(path, rules.grader_types())}


@pytest.mark.ac("gh-385:AC-3")
def test_evaluate_rejects_a_task_case_for_a_helper_that_never_ran_but_not_a_journey_case(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def sandbox(self: ClaudeCode, workspace: Path, argv: list[str], seconds: int | None = None) -> tuple[int, str, str]:
        del self, seconds
        done = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, timeout=30, check=False)
        return done.returncode, done.stdout, done.stderr

    monkeypatch.setattr(ClaudeCode, "sandbox", sandbox)
    rules = _contract()
    cases = cast(dict[str, object], _cases(tmp_path, rules))
    candidate = Candidate({"SKILL.md": SKILL_TEXT, "run.py": "pass\n"}, ("SKILL.md",), contract=rules)
    session = open_session(tmp_path, install(tmp_path, {}), budget=Budget(40, 2400, 0))
    task = cast(ClaudeCode, session.transports["task"])
    judge = Judge([*SIMULATE_THEN_DONE, {"score_percent": 70}, {"score_percent": 70}])
    judge.budget = task.budget
    try:
        checks = CheckMemo()
        journey = evaluate(task, judge, candidate, cases["j1"], checks=checks)  # pyright: ignore[reportArgumentType]
        plain = evaluate(task, judge, candidate, cases["t1"], checks=checks)  # pyright: ignore[reportArgumentType]
    finally:
        session.close()
    assert journey["loaded"] is True and journey["helper_executed"] is True
    assert journey.get("status") != "activation-rejected" and journey["score"] == 0.7
    assert plain["loaded"] is True and plain["helper_executed"] is False
    assert plain["status"] == "activation-rejected"
