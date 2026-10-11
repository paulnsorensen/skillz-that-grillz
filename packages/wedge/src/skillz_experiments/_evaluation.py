from __future__ import annotations

import json
import shlex
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import cast

from skillz_experiments._cases import loads_untrusted, mapping
from skillz_experiments._contract import Contract
from skillz_experiments._graders import Sandbox


def grade(answer: str, expected: object, *, loaded: bool, helper_executed: bool) -> float:
    if not loaded or not helper_executed:
        return 0.0
    return float(_same_json(answer, expected))


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


def _same_json(text: str, expected: object) -> bool:
    try:
        answer = loads_untrusted(text)
    except ValueError:
        return False
    return json.dumps(answer, sort_keys=True) == json.dumps(expected, sort_keys=True)


def fixture_failure(fixture: Mapping[str, object], returncode: int, stdout: str, stderr: str) -> str | None:
    """Check helper output against one declared fixture. Return the name of the failing stream, or None.

    The streams are `returncode`, `stdout`, and `stderr`. A `null` output means an empty stdout. A fixture with
    an `error` key also needs exactly that JSON on the last nonempty stderr line, so earlier transport
    warnings do not matter. A fixture without it leaves stderr unchecked.
    """
    if type(returncode) is not int or returncode != fixture["returncode"]:
        return "returncode"
    expected = fixture["output"]
    if expected is None:
        if stdout:
            return "stdout"
    elif not _same_json(stdout, expected):
        return "stdout"
    if "error" not in fixture:
        return None
    lines = [line for line in stderr.splitlines() if line.strip()]
    return None if lines and _same_json(lines[-1], fixture["error"]) else "stderr"


def helper_failure(sandbox: Sandbox, workspace: Path, rules: Contract) -> str | None:
    """Run each declared helper fixture in the sandbox. Return the first failure, or None when all pass."""
    helper = rules.helper
    if helper is None:
        return None
    for index, fixture in enumerate(helper.fixtures):
        (workspace / helper.input).parent.mkdir(parents=True, exist_ok=True)
        _ = (workspace / helper.input).write_text(cast(str, fixture["input"]))
        code, stdout, stderr = sandbox.sandbox(workspace, [
            "/usr/bin/python3", "-I", f".agents/skills/{rules.skill}/{helper.path}", helper.input])
        stream = fixture_failure(fixture, code, stdout, stderr)
        if stream is not None:
            return f"helper fixture {index} fails on {stream}"
    return None


def _paths(relative: str, workspace: str | None) -> set[str]:
    return {relative, str(PurePosixPath(workspace) / relative)} if workspace else {relative}
