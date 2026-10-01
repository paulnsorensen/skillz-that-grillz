from __future__ import annotations

import json
import shlex
from pathlib import PurePosixPath
from typing import cast

from skillz_experiments._cases import mapping


def grade(answer: str, expected: object, *, loaded: bool, helper_executed: bool) -> float:
    if not loaded or not helper_executed:
        return 0.0
    try:
        result = cast(object, json.loads(answer))
    except json.JSONDecodeError:
        return 0.0
    return float(json.dumps(result, sort_keys=True) == json.dumps(expected, sort_keys=True))


def usage(events: list[dict[str, object]]) -> dict[str, int | None]:
    values: dict[str, int | None] = {
        "input_tokens": None, "cached_input_tokens": None, "output_tokens": None,
    }
    for event in events:
        if event.get("type") != "turn.completed" or not isinstance(event.get("usage"), dict):
            continue
        counts = mapping(event["usage"])
        for name in values:
            count = counts.get(name)
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                values[name] = (values[name] or 0) + count
    return values


def executed(events: list[dict[str, object]], filename: str, workspace: str | None = None) -> bool:
    for event in events:
        if event.get("type") != "item.completed" or not isinstance(event.get("item"), dict):
            continue
        item = mapping(event["item"])
        command = item.get("command")
        if item.get("type") == "command_execution" and item.get("exit_code") == 0:
            if isinstance(command, str) and _command_matches(command, filename, workspace):
                return True
    return False

def _command_matches(command: str, filename: str, workspace: str | None) -> bool:
    try:
        parts = shlex.split(command)
        if len(parts) == 3 and PurePosixPath(parts[0]).name in {"sh", "bash", "zsh"} and parts[1] in {"-c", "-lc"}:
            parts = shlex.split(parts[2])
    except ValueError:
        return False
    if not parts or any(token in command for token in (";", "&&", "||", "|", ">", "<", "$", "`", "\n")):
        return False
    if filename == "SKILL.md":
        return len(parts) == 2 and PurePosixPath(parts[0]).name == "cat" and parts[1] in _paths(".agents/skills/skillz/SKILL.md", workspace)
    arguments = parts[1:]
    if arguments and arguments[0] == "-I":
        arguments = arguments[1:]
    return (PurePosixPath(parts[0]).name in {"python3", "python"}
            and len(arguments) == 2 and arguments[0] in _paths(".agents/skills/skillz/scripts/inspect_skill.py", workspace))

HELPER_INPUTS = (
    "---\nname: contract\ndescription: Inspect\n---\n# Body\n[Guide](references/guide.md)\n",
    "---\nname: contract\n---\n[Escape](../secret)\n",
)


def helper_result(index: int, returncode: int, stdout: str) -> bool:
    expected: list[dict[str, object]] = [
        {"schema_version": 1, "frontmatter_keys": ["description", "name"],
         "body_line_count": 2, "local_link_targets": ["references/guide.md"]},
        {"schema_version": 1, "error": "link escapes package"},
    ]
    try:
        answer = cast(object, json.loads(stdout))
    except json.JSONDecodeError:
        return False
    return (returncode == (0 if index == 0 else 2)
            and json.dumps(answer, sort_keys=True) == json.dumps(expected[index], sort_keys=True))


def _paths(relative: str, workspace: str | None) -> set[str]:
    return {relative, str(PurePosixPath(workspace) / relative)} if workspace else {relative}
