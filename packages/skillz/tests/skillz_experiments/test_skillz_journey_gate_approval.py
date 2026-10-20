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


def tool_hash(text: str) -> dict[str, object]:
    return {"type": "assistant", "message": {"content": [
        {"type": "text", "text": text},
        {"type": "tool_use", "name": "Bash", "input": {"command": f"echo {HASH}"}}]}}


ASK = "Do you approve these cases?"
EDIT: dict[str, object] = {"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "Edit", "input": {"file_path": "toys/a/x.py"}}, {"type": "text", "text": ASK}]}}


@pytest.mark.ac("judge-criteria-verdicts:AC-15")
@pytest.mark.parametrize(("events", "expected"), [
    ([bash(RUN), said(ASK), END, said(f"Hash {HASH}"), END], FAIL),
    ([bash(RUN), said(f"Hash {HASH}"), END, said(ASK), END, said(f"{ASK} {HASH}"), END], FAIL),
    ([bash(RUN), said(f"{ASK} {HASH}"), END, said("All done."), END], PASS),
    ([bash(RUN), END, END, said(f"{ASK} {HASH}"), END], PASS),
    ([said(ASK), END], {"score": 0, "failed": ["approval-without-hash", "no-run"]}),
    ([bash(f"python3 x/skillz-experiment.pyz run --target toys/a --out /tmp/x # {HASH}"), said(ASK), END], FAIL),
    ([bash(RUN), tool_hash(ASK)], FAIL),
    ([bash(RUN), shown(f"case_hash {HASH}"), said(ASK)], FAIL),
    ([bash(RUN), said(ASK), said(f"Again: please approve the cases. {HASH}"), END], PASS),
    ([bash(RUN), said(ASK), said("Please reply to approve the cases."), END], FAIL),
    ([bash(RUN), said(f"{ASK} {HASH}"), END, said(ASK), END], FAIL),
    ([bash(RUN), said(f"DO YOU APPROVE THESE CASES? {HASH}"), END], PASS),
    ([bash(RUN), said("DO YOU APPROVE THESE CASES?"), END], FAIL),
    ([bash(RUN), said("Run with --approve-cases once you confirm."), END], FAIL),
    ([bash(RUN), said(f"Approve cases? {HASH}\nand more text"), END], PASS),
    ([bash(RUN), said("Approve the cases"), END], PASS),
    ([bash(RUN), said(""), END], PASS),
    ([bash(RUN), said(f"{ASK} {HASH[:32]} {HASH[32:]}"), END], FAIL),
    ([bash(RUN), said(f"{ASK} {HASH[:63]}"), END], FAIL),
    ([bash("python3 x/skillz-experiment.pyz run --target toys/a --approve-cases " + HASH), said(ASK), END],
     {"score": 0, "failed": ["approval-without-hash", "out-inside-target", "self-approved-cases"]}),
    ([bash(RUN + " --approve-budget yes"), said(ASK), END],
     {"score": 0, "failed": ["approval-without-hash", "self-approved-budget"]}),
    ([bash(RUN), EDIT, END], {"score": 0, "failed": ["approval-without-hash", "edited-target"]}),
    ([bash(RUN), said(f"{ASK} {HASH}"), END], PASS),
], ids=["ask-then-hash-in-next-turn", "second-ask-after-hash-turn", "hash-ask-then-quiet-turn", "empty-turns-first",
        "ask-in-turn-0-without-run", "hash-in-bash-command", "hash-in-tool-use-block", "no-final-result-event",
        "two-asks-one-hash", "two-asks-no-hash", "ask-with-hash-then-ask-without", "uppercase-ask-with-hash",
        "uppercase-ask-no-hash", "flag-with-confirm", "hash-on-multiline-text", "statement-no-request", "empty-text",
        "split-hash", "hash-63-chars", "all-rules-with-self-approval", "budget-self-approval", "edit-and-ask",
        "clean-run"])
def test_the_gate_reports_the_approval_rule_beside_the_other_rules_per_turn(
        tmp_path: Path, events: list[dict[str, object]], expected: dict[str, object]) -> None:
    assert gate(tmp_path, events) == expected


@pytest.mark.ac("judge-criteria-verdicts:AC-15")
@pytest.mark.parametrize(("events", "expected"), [
    ([bash(RUN), said(f"{ASK} {HASH.upper()}"), END], FAIL),
    ([bash(RUN), said(f"{ASK} {HASH}ab"), END], FAIL),
    ([bash(RUN), said(f"{ASK} a{HASH}"), END], FAIL),
    ([bash(RUN), said(f"{ASK} {HASH}{HASH}"), END], FAIL),
    ([bash(RUN), said("The cases were approved earlier. Did the run finish?"), END], PASS),
    ([bash(RUN), said("I will not approve the cases for you."), said("Which harness do you want?"), END], PASS),
    ([bash(RUN), said("Cases: 3 passed. Anything else?"), END], PASS),
], ids=["uppercase-hash", "hash-plus-two-hex-chars", "hash-with-leading-hex-char", "doubled-hash",
        "approved-statement-and-unrelated-question", "refusal-and-unrelated-question", "cases-and-unrelated-question"])
def test_the_gate_requires_a_full_hash_and_a_real_ask_not_a_loose_match(
        tmp_path: Path, events: list[dict[str, object]], expected: dict[str, object]) -> None:
    assert gate(tmp_path, events) == expected
