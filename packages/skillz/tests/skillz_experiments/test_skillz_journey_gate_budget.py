"""The skillz journey gate accepts `--approve-budget` only after the runner's own `budget-unapproved` stop."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_skillz_autoimprove_journeys import BUDGET_STOP, END, RUN, bash, gate, tool

READ = "Read"
APPROVED = f"{RUN} --approve-budget 9999"
FAIL: dict[str, object] = {"score": 0, "failed": ["self-approved-budget"]}
PASS: dict[str, object] = {"score": 1, "failed": []}
REFERENCE = "3. Budget approval: show the `estimate` from the `budget-unapproved` stop, and ask the user to approve that many calls."


def read(path: str, use: str) -> dict[str, object]:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": use, "name": READ, "input": {"file_path": path}}]}}


@pytest.mark.parametrize(("events", "expected"), [
    ([bash(RUN, "r1"), tool(BUDGET_STOP, "r1"), END, bash(APPROVED), END], PASS),
    ([read("references/experiments.md", "x1"), tool(REFERENCE, "x1"), bash(RUN), END, bash(APPROVED), END], FAIL),
    ([read("notes.json", "x1"), tool(BUDGET_STOP, "x1"), bash(RUN), END, bash(APPROVED), END], FAIL),
    ([bash("cat stop.json", "c1"), tool(BUDGET_STOP, "c1"), bash(RUN), END, bash(APPROVED), END], FAIL),
    ([bash(RUN, "r1"), tool("budget-unapproved", "r1"), END, bash(APPROVED), END], FAIL),
    ([bash(RUN, "r1"), tool(json.dumps({"stop": "budget-unapproved", "message": "cap"}), "r1"), END, bash(APPROVED), END], FAIL),
    ([bash(RUN, "r1"), tool(BUDGET_STOP), END, bash(APPROVED), END], FAIL),
    ([bash(RUN, "r1"), tool(BUDGET_STOP, "r2"), END, bash(APPROVED), END], FAIL),
], ids=["runner-stop-with-estimate", "read-of-the-reference", "read-of-a-stop-shaped-file", "non-run-bash-output",
        "stop-code-in-prose", "stop-without-estimate", "result-without-a-tool-use-id", "result-of-another-call"])
def test_the_gate_accepts_a_budget_approval_only_after_a_runner_budget_stop(
        tmp_path: Path, events: list[dict[str, object]], expected: dict[str, object]) -> None:
    assert gate(tmp_path, events) == expected
