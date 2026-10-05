from __future__ import annotations

import json
import shlex
from collections.abc import Mapping
from pathlib import PurePosixPath

from skillz_experiments._cases import loads_untrusted, mapping


def grade(answer: str, expected: object, *, loaded: bool, helper_executed: bool) -> float:
    if not loaded or not helper_executed:
        return 0.0
    try:
        result = loads_untrusted(answer)
        return float(json.dumps(result, sort_keys=True) == json.dumps(expected, sort_keys=True))
    except ValueError:
        return 0.0


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


def executed(events: list[dict[str, object]], filename: str, workspace: str | None = None,
             *, skill: str, isolated: bool = False, input_path: str | None = None) -> bool:
    """Check a completed skill command with its declared input, if any."""
    for event in events:
        if event.get("type") != "item.completed" or not isinstance(event.get("item"), dict):
            continue
        item = mapping(event["item"])
        command = item.get("command")
        if item.get("type") == "command_execution" and item.get("exit_code") == 0:
            if isinstance(command, str) and _command_matches(command, filename, workspace, skill, isolated, input_path):
                return True
    return False


def _command_matches(command: str, filename: str, workspace: str | None, skill: str,
                     isolated: bool, input_path: str | None) -> bool:
    try:
        parts = shlex.split(command)
        if len(parts) == 3 and PurePosixPath(parts[0]).name in {"sh", "bash", "zsh"} and parts[1] in {"-c", "-lc"}:
            parts = shlex.split(parts[2])
    except ValueError:
        return False
    if not parts or any(token in command for token in (";", "&&", "||", "|", ">", "<", "$", "`", "\n")):
        return False
    root = f".agents/skills/{skill}"
    if filename == "SKILL.md":
        return len(parts) == 2 and PurePosixPath(parts[0]).name == "cat" and parts[1] in _paths(f"{root}/SKILL.md", workspace)
    arguments = parts[1:]
    flagged = bool(arguments) and arguments[0] == "-I"
    if flagged:
        arguments = arguments[1:]
    expected = 1 if input_path is None else 2
    return (PurePosixPath(parts[0]).name in {"python3", "python"} and (flagged or not isolated)
            and len(arguments) == expected and arguments[0] in _paths(f"{root}/{filename}", workspace)
            and (input_path is None or arguments[1] in _paths(input_path, workspace)))


HELPER_FIXTURES: tuple[dict[str, object], ...] = (
    {"input": "---\nname: contract\ndescription: Inspect\n---\n# Body\n[Guide](references/guide.md)\n",
     "returncode": 0,
     "output": {"schema_version": 3, "advisory_sentences": [], "frontmatter_keys": ["description", "name"],
                "body_line_count": 2,
                "local_link_targets": ["references/guide.md"], "long_sentences": []}},
    {"input": "---\nname: contract\n---\n[Escape](../secret)\n", "returncode": 2,
     "output": {"schema_version": 3, "error": "link escapes package"}},
)


def fixture_result(fixture: Mapping[str, object], returncode: int, stdout: str) -> bool:
    """Check helper output against one declared fixture: exit code and exact JSON."""
    try:
        answer = loads_untrusted(stdout)
    except ValueError:
        return False
    return (returncode == fixture["returncode"] and type(returncode) is int
            and json.dumps(answer, sort_keys=True) == json.dumps(fixture["output"], sort_keys=True))


def _paths(relative: str, workspace: str | None) -> set[str]:
    return {relative, str(PurePosixPath(workspace) / relative)} if workspace else {relative}
