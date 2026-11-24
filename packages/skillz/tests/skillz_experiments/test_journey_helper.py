"""The contract helper applies to task cases only: a journey case gets no helper instruction and no helper credit."""
from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case
from skillz_experiments._contract import Contract, Grader, Helper
from skillz_experiments.evaluation._evaluator import _activation, _prompt  # pyright: ignore[reportPrivateUsage]

RULES = Contract("echo", "$echo run", {"task": Grader("exact-json")}, Helper("run.py", "input.md"))
CANDIDATE = Candidate({"SKILL.md": "skill", "run.py": "pass"}, ("SKILL.md",), RULES)


def _case(journey: bool) -> Case:
    return cast(Case, cast(object, SimpleNamespace(request="do it", journey=object() if journey else None)))


def _loaded_only() -> dict[str, object]:
    events = [{"type": "item.completed", "item": {"type": "command_execution", "exit_code": 0,
                                                "command": "cat .agents/skills/echo/SKILL.md"}}]
    return {"answer": {"load_marker": CANDIDATE.identity}, "events": events, "workspace": "/work"}


@pytest.mark.ac("gh-385:AC-1")
def test_a_journey_case_is_not_rejected_for_a_helper_that_did_not_run() -> None:
    _, loaded, helper = _activation(_loaded_only(), CANDIDATE, RULES, journey=True)
    assert loaded and helper
    assert "run.py" not in _prompt(RULES, "", _case(True))


@pytest.mark.ac("gh-385:AC-2")
def test_a_task_case_is_still_rejected_for_a_helper_that_did_not_run() -> None:
    _, loaded, helper = _activation(_loaded_only(), CANDIDATE, RULES)
    assert loaded and not helper
    assert "python3 -I .agents/skills/echo/run.py input.md" in _prompt(RULES, "", _case(False))


@pytest.mark.ac("gh-746:AC-1")
@pytest.mark.ac("gh-746:AC-2")
def test_task_prompt_distinguishes_output_file_from_json_and_limits_load_steps() -> None:
    task = _prompt(RULES, "Return JSON.", _case(False))
    assert "simple commands, without shell operators, for these skill-load steps" in task
    assert "create it with an available, permitted writer" in task
    assert "Write if declared" in task and "Bash when available and permitted" in task
    assert "Verify the file exists" in task
    assert "JSON text does not create the file" in task
    assert "create it with an available, permitted writer" not in _prompt(RULES, "", _case(True))
