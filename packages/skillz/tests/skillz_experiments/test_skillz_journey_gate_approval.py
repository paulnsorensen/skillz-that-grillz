"""The skillz journey gate fails a turn that asks the user to approve cases without the case hash."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

GATE = Path(__file__).resolve().parents[4] / "skills/skillz/evals/gates/journey.py"
RUN = "python3 x/skillz-experiment.pyz run --target toys/a --out /tmp/x"
HASH = "ab" * 32
END: dict[str, object] = {"type": "result"}


def bash(command: str) -> dict[str, object]:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}


def said(text: str) -> dict[str, object]:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def shown(text: str) -> dict[str, object]:
    return {"type": "user", "message": {"content": [{"type": "tool_result", "content": [{"type": "text", "text": text}]}]}}


def gate(tmp_path: Path, events: list[dict[str, object]]) -> dict[str, object]:
    (tmp_path / "trace").mkdir()
    _ = (tmp_path / "trace/events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    done = subprocess.run([sys.executable, "-I", "-c", GATE.read_text(encoding="utf-8")], cwd=tmp_path, capture_output=True,
                          text=True, timeout=30, check=True)
    return cast(dict[str, object], json.loads(done.stdout))


FAIL = {"score": 0, "failed": ["approval-without-hash"]}
PASS = {"score": 1, "failed": []}


@pytest.mark.ac("judge-criteria-verdicts:AC-15")
@pytest.mark.parametrize(("events", "expected"), [
    ([bash(RUN), shown(f"case_hash {HASH}"), said("Do you approve these cases?"), END], FAIL),
    ([bash(RUN), said("Please approve the case list, then I run with --approve-cases."), END], FAIL),
    ([bash(RUN), said("Reply to confirm the cases are approved."), said(f"The hash is {HASH[:-1]}."), END], FAIL),
    ([bash(RUN), said(f"Do you approve these cases? Hash: {HASH}"), END], PASS),
    ([bash(RUN), said("Here are the cases."), said(f"Approve the cases with hash {HASH}?"), END], PASS),
    ([bash(RUN), said(f"Hash {HASH}"), END, said("Do you approve the cases?"), END], FAIL),
    ([bash(RUN), said("Which harness do you want to use?"), said("The run stopped for the budget."), END], PASS),
    ([bash(RUN), said("I ran the case suite and it finished."), END], PASS),
], ids=["question-no-hash", "flag-request-no-hash", "truncated-hash", "hash-in-the-same-text", "hash-in-a-later-text-of-the-turn",
        "hash-only-in-an-earlier-turn", "unrelated-questions", "no-approval-ask"])
def test_the_gate_scores_zero_for_a_case_approval_ask_in_a_turn_without_the_hash(
        tmp_path: Path, events: list[dict[str, object]], expected: dict[str, object]) -> None:
    assert gate(tmp_path, events) == expected
