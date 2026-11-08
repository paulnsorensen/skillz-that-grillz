"""The journey judge trace: the step renderers of each harness, the bounds, and the scoring digest that covers them."""
from __future__ import annotations

import json
import shutil
from importlib.resources import files
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments.evaluation._trace import FIELD_CHARS, TRACE_CHARS, judge_trace
from skillz_experiments.harness._steps import claude_steps, codex_steps, journey_steps
from skillz_experiments.run._open import scoring_digest


def _assistant(*content: object) -> dict[str, object]:
    return {"type": "assistant", "message": {"content": list(content)}}


def _user(*content: dict[str, object]) -> dict[str, object]:
    return {"type": "user", "message": {"content": list(content)}}


@pytest.mark.ac("autoimprove-observability:AC-5")
def test_claude_steps_list_messages_and_tool_calls_with_their_output_by_turn() -> None:
    events: list[dict[str, object]] = [
        {"type": "system", "subtype": "init"},
        _assistant({"type": "text", "text": "Contract: A-D"}, {"type": "tool_use", "id": "u1", "name": "Bash",
                                                              "input": {"command": "python3 -m unittest"}}),
        _user({"type": "tool_result", "tool_use_id": "u1", "is_error": False,
               "content": [{"type": "text", "text": "Ran 4 tests"}, {"type": "image"}, {"type": "text", "text": "OK"}]}),
        _user({"type": "text", "text": "a user turn is not a step"}),
        {"type": "result", "result": "done"},
        _assistant({"type": "tool_use", "id": "u2", "name": "Write", "input": {"file_path": "a.py", "content": "x"}},
                   {"type": "tool_use", "id": "u3", "name": "Agent", "input": {"prompt": "review"}}, "not a block"),
        _user({"type": "tool_result", "tool_use_id": "u2", "is_error": True, "content": "denied"},
              {"type": "tool_result", "tool_use_id": "unknown", "content": "stray"}),
        {"type": "result", "result": "done"}]
    assert claude_steps(events) == [
        {"turn": 1, "text": "Contract: A-D"},
        {"turn": 1, "tool": "Bash", "input": {"command": "python3 -m unittest"}, "output": "Ran 4 tests\nOK", "error": False},
        {"turn": 2, "tool": "Write", "input": {"file_path": "a.py", "content": "x"}, "output": "denied", "error": True},
        {"turn": 2, "tool": "Agent", "input": {"prompt": "review"}, "output": None, "error": None}]


def _item(turn: str, item: dict[str, object]) -> dict[str, object]:
    return {"method": "item/completed", "params": {"threadId": "t", "turnId": turn, "item": item}}


@pytest.mark.ac("autoimprove-observability:AC-5")
def test_codex_steps_list_the_completed_items_of_each_turn() -> None:
    events: list[dict[str, object]] = [
        {"method": "thread/started", "params": {"thread": {"id": "t"}}},
        {"method": "item/started", "params": {"turnId": "a", "item": {"type": "commandExecution"}}},
        _item("a", {"type": "userMessage", "content": []}),
        _item("a", {"type": "reasoning", "summary": ["think"]}),
        _item("a", {"id": "1", "type": "commandExecution", "command": "just build", "exitCode": 0,
                    "aggregatedOutput": "all checks passed"}),
        _item("a", {"type": "agentMessage", "text": "Which value?"}),
        _item("b", {"type": "fileChange", "status": "completed", "changes": [{"path": "a.py", "diff": "+x"}]}),
        _item("b", {"type": "commandExecution", "command": "false", "exitCode": 1, "aggregatedOutput": ""}),
        _item("b", {"type": "commandExecution", "command": "sleep", "exitCode": None}),
        _item("b", {"type": "mcpToolCall", "server": "docs", "tool": "get", "arguments": {"q": 1}, "result": "r",
                    "error": None}),
        _item("b", {"id": "9", "type": "collabAgentToolCall", "tool": "spawnAgent", "status": "failed"}),
        _item("b", {"no": "type"}),
        {"method": "item/completed", "params": {"turnId": "b", "item": "not an object"}},
        {"method": "turn/completed", "params": {"turnId": "b"}}, {"method": "warning", "params": "text"}]
    assert codex_steps(events) == [
        {"turn": 1, "tool": "shell", "input": {"command": "just build"}, "output": "all checks passed", "error": False,
         "exit_code": 0},
        {"turn": 1, "text": "Which value?"},
        {"turn": 2, "tool": "file_change", "input": {"changes": [{"path": "a.py", "diff": "+x"}]}, "output": None,
         "error": False},
        {"turn": 2, "tool": "shell", "input": {"command": "false"}, "output": "", "error": True, "exit_code": 1},
        {"turn": 2, "tool": "shell", "input": {"command": "sleep"}, "output": None, "error": None, "exit_code": None},
        {"turn": 2, "tool": "mcp:docs/get", "input": {"q": 1}, "output": "r", "error": False},
        {"turn": 2, "tool": "collabAgentToolCall", "input": {"tool": "spawnAgent", "status": "failed"}, "output": None,
         "error": True}]
    assert (journey_steps("claude"), journey_steps("codex"), journey_steps("command")) == (claude_steps, codex_steps, None)


@pytest.mark.ac("autoimprove-observability:AC-6")
def test_the_judge_trace_redacts_and_cuts_each_field_and_keeps_the_ends_of_a_long_trace() -> None:
    long_output = "start " + "x" * 10_000 + " Ran 4 tests OK"
    steps: list[dict[str, object]] = [
        {"turn": 1, "tool": "Bash", "input": {"command": "env"}, "output": "api_key=sk-live-123456 done", "error": False},
        {"turn": 1, "tool": "Bash", "input": {"command": "pytest"}, "output": long_output, "error": False},
        {"turn": 1, "tool": "Write", "input": {"content": ["y" * 50] * 100}, "output": None, "error": None}]
    first, second, third = judge_trace(steps)
    assert "sk-live-123456" not in json.dumps(first) and str(first["output"]).endswith("done")
    output = str(second["output"])
    assert len(output) < FIELD_CHARS + 60 and output.startswith("start x") and output.endswith("Ran 4 tests OK")
    assert "characters cut" in output
    assert isinstance(third["input"], str) and "characters cut" in third["input"]
    many: list[dict[str, object]] = [{"turn": index, "tool": "Bash", "input": {"command": f"step {index}"},
                                      "output": "o" * 3_000} for index in range(60)]
    trace = judge_trace(many)
    gap = next(index for index, step in enumerate(trace) if "omitted_steps" in step)
    assert trace[0]["turn"] == 0 and trace[-1]["turn"] == 59
    assert len(trace) - 1 + cast(int, trace[gap]["omitted_steps"]) == 60
    assert len(json.dumps(trace)) <= TRACE_CHARS
    assert judge_trace(many[:2]) == many[:2]
    assert judge_trace([{"input": ["token=abc123secret", 1]}]) == [{"input": ["[redacted]", 1]}]


@pytest.mark.ac("autoimprove-observability:AC-6")
def test_nested_credential_keys_and_values_are_redacted_before_a_container_is_cut() -> None:
    secret = "credential-value-that-must-not-leak"
    steps: list[dict[str, object]] = [{"input": {"api_key": {"nested": secret},
                                                  f"label token={secret}": "safe",
                                                  "details": [{"password": secret}, {"command": "just build"}]}}]
    trace = judge_trace(steps)
    encoded = json.dumps(trace)
    assert secret not in encoded
    assert trace == [{"input": {"[redacted]": "[redacted]", "label [redacted]": "[redacted]",
                                  "details": [{"[redacted]": "[redacted]"}, {"command": "just build"}]}}]
    oversized = judge_trace([{"input": {"api_key": secret, "output": "x" * (FIELD_CHARS + 100)}}])
    assert secret not in json.dumps(oversized)
    assert "[redacted]" in str(oversized[0]["input"])


@pytest.mark.ac("autoimprove-observability:AC-6")
def test_many_short_steps_and_omission_marker_fit_the_complete_json_budget() -> None:
    steps: list[dict[str, object]] = [{"turn": index} for index in range(6_000)]
    trace = judge_trace(steps)
    assert len(json.dumps(trace)) <= TRACE_CHARS
    assert trace[0] == {"turn": 0} and trace[-1] == {"turn": 5_999}
    (marker,) = [step for step in trace if "omitted_steps" in step]
    assert marker["omitted_steps"] == len(steps) - len(trace) + 1
    boundary = judge_trace([{"text": "x" * 10} for _ in range(2_000)])
    assert len(json.dumps(boundary)) <= TRACE_CHARS
    assert boundary[0] == {"text": "x" * 10} and boundary[-1] == {"text": "x" * 10}


@pytest.mark.ac("autoimprove-observability:AC-7")
def test_an_edit_to_the_trace_renderer_or_bounds_changes_the_scoring_digest(tmp_path: Path) -> None:
    package = tmp_path / "skillz_experiments"
    _ = shutil.copytree(Path(str(files("skillz_experiments"))), package)
    before = scoring_digest(package)
    for name in ("evaluation/_trace.py", "harness/_steps.py", "_redact.py"):
        path = package / name
        _ = path.write_text(path.read_text() + "\n# changed\n")
        after = scoring_digest(package)
        assert after != before, name
        before = after
