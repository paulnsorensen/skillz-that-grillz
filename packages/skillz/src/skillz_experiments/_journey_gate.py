"""Generic gate of the `journey` grader. The scaffold command copies this file to `evals/gates/journey.py`.

The grader runs it as `python3 -I -c SOURCE` in the journey workspace. It reads `trace/events.jsonl`
and prints `{"score": 0 or 1, "failed": [...]}`. Fill `RECEIPTS` and `TERMINAL` below, and add checks below `main`.
Standard library only. Keep it valid on Python 3.9, the oldest `python3` that a host may resolve.
"""
from __future__ import annotations

import json
import pathlib
import re
from typing import NamedTuple, cast


class Matcher(NamedTuple):
    """All given fields must match one step: `tool` exactly, `input` and `text` with `re.search`."""

    name: str
    tool: str | None = None
    input: str | None = None
    text: str | None = None


class Step(NamedTuple):
    tool: str | None
    input: str
    text: str | None


# Steps that must appear in this order. Other steps may sit between them. Empty means no receipt check.
# Example: Matcher("ran-validator", tool="Bash", input=r"validate_skills")
RECEIPTS: list[Matcher] = []
# The first step that matches ends the flow. A receipt after it does not count. None counts every step.
TERMINAL: Matcher | None = None

EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def _map(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _tool_step(name: object, raw: object) -> Step:
    tool = name if isinstance(name, str) else ""
    data = _map(raw)
    if tool == "Bash":
        return Step(tool, str(data.get("command", "")), None)
    if tool in EDIT_TOOLS and "file_path" in data:
        return Step(tool, str(data["file_path"]), None)
    return Step(tool, json.dumps(data, sort_keys=True), None)


def steps(event: object) -> list[Step]:
    """Turn one Claude or Codex trace event into steps. An unknown event gives none."""
    data = _map(event)
    if data.get("type") == "assistant":
        found: list[Step] = []
        for block in _list(_map(data.get("message")).get("content")):
            part = _map(block)
            if part.get("type") == "tool_use":
                found.append(_tool_step(part.get("name"), part.get("input")))
            elif part.get("type") == "text":
                found.append(Step(None, "", str(part.get("text", ""))))
        return found
    if data.get("method") != "item/completed":
        return []
    item = _map(_map(data.get("params")).get("item"))
    kind = item.get("type")
    if kind == "commandExecution" and isinstance(item.get("id"), str) and type(item.get("exitCode")) is int:
        return [_tool_step("Bash", {"command": item.get("command", "")})]
    if kind == "agentMessage":
        return [Step(None, "", str(item.get("text", "")))]
    if kind == "fileChange":
        return [Step("Edit", str(_map(change).get("path", "")), None) for change in _list(item.get("changes"))]
    return []


def matches(matcher: Matcher, step: Step) -> bool:
    if matcher.tool is not None and step.tool != matcher.tool:
        return False
    if matcher.input is not None and not re.search(matcher.input, step.input):
        return False
    return matcher.text is None or (step.text is not None and re.search(matcher.text, step.text) is not None)


def missing_receipt(found: list[Step], receipts: list[Matcher], terminal: Matcher | None) -> str | None:
    """Return the name of the first receipt that has no step in order before the terminal step."""
    if terminal is not None:
        for index, step in enumerate(found):
            if matches(terminal, step):
                found = found[:index]
                break
    position = 0
    for receipt in receipts:
        while position < len(found) and not matches(receipt, found[position]):
            position += 1
        if position == len(found):
            return receipt.name
        position += 1
    return None


def agent_turn(line: str) -> bool:
    try:
        event = cast(object, json.loads(line))
    except json.JSONDecodeError:
        return False
    if not isinstance(event, dict):
        return False
    data = cast(dict[str, object], event)
    if data.get("type") == "assistant":
        return True
    if data.get("method") != "item/completed":
        return False
    params = data.get("params")
    if not isinstance(params, dict):
        return False
    item = cast(dict[str, object], params).get("item")
    return isinstance(item, dict) and cast(dict[str, object], item).get("type") == "agentMessage"


def trace_steps(lines: list[str]) -> list[Step]:
    found: list[Step] = []
    for line in lines:
        try:
            found.extend(steps(cast(object, json.loads(line))))
        except json.JSONDecodeError:
            continue
    return found


def main() -> None:
    trace = pathlib.Path("trace/events.jsonl")
    failed: list[str] = []
    if not trace.is_file():
        failed.append("no-trace")
    else:
        lines = trace.read_text(encoding="utf-8").splitlines()
        if not any(agent_turn(line) for line in lines):
            failed.append("no-agent-turn")
        elif (name := missing_receipt(trace_steps(lines), RECEIPTS, TERMINAL)) is not None:
            failed.append(f"receipt-missing:{name}")
    print(json.dumps({"score": 0 if failed else 1, "failed": failed}))


if __name__ == "__main__":
    main()
