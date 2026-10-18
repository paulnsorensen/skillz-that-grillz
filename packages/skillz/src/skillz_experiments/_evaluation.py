from __future__ import annotations

import json
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from skillz_experiments._cases import loads_untrusted, mapping
from skillz_experiments._contract import Contract
from skillz_experiments._graders import Sandbox


CheckKind = Literal["pass", "fail", "timeout"]


@dataclass(frozen=True)
class CheckResult:
    """The result of one candidate contract check. The `evaluation` barrel exports it.

    `passed` is true only for kind `pass`. `reason` says why a check fails, or is None when the transport gives no reason.
    """

    passed: bool
    reason: str | None
    kind: CheckKind

    def __post_init__(self) -> None:
        if self.passed != (self.kind == "pass") or (self.passed and self.reason is not None):
            raise ValueError(f"inconsistent check result: passed={self.passed}, kind={self.kind}")


PASSED = CheckResult(True, None, "pass")


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


def witnessed_events(events: list[dict[str, object]], ledger: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return the trace events that a ledger line backs. A ledger line backs one event.

    A line backs an event when the event runs `python`, then an optional `-I`, then the argv of the line,
    and both have the same exit code.
    """
    unused = list(ledger)
    kept: list[dict[str, object]] = []
    for event in events:
        if not isinstance(event.get("item"), dict):
            continue
        item = mapping(event["item"])
        command = item.get("command")
        parts = _words(command) if isinstance(command, str) else None
        python = None if parts is None else _python_argv(parts)
        if python is None:
            continue
        arguments = python[0]
        code = item.get("exit_code")
        for line in unused:
            if line["argv"] == arguments and line["exit_code"] == code:
                unused.remove(line)
                kept.append(event)
                break
    return kept


def _words(command: str) -> list[str] | None:
    try:
        parts = shlex.split(command)
        if len(parts) == 3 and PurePosixPath(parts[0]).name in {"sh", "bash", "zsh"} and parts[1] in {"-c", "-lc"}:
            parts = shlex.split(parts[2])
    except ValueError:
        return None
    if not parts or any(token in command for token in (";", "&&", "||", "|", ">", "<", "$", "`", "\n")):
        return None
    return parts


def _python_argv(parts: list[str]) -> tuple[list[str], bool] | None:
    """Return the arguments after a python name and an optional `-I`, and whether `-I` was present."""
    if PurePosixPath(parts[0]).name not in {"python3", "python"}:
        return None
    flagged = parts[1:2] == ["-I"]
    return (parts[2:] if flagged else parts[1:]), flagged


def _command_matches(command: str, filename: str, workspace: str | None, skill: str,
                     isolated: bool, input_path: str | None) -> bool:
    parts = _words(command)
    if parts is None:
        return False
    root = f".agents/skills/{skill}"
    if filename == "SKILL.md":
        return len(parts) == 2 and PurePosixPath(parts[0]).name == "cat" and parts[1] in _paths(f"{root}/SKILL.md", workspace)
    python = _python_argv(parts)
    if python is None:
        return False
    arguments, flagged = python
    expected = 1 if input_path is None else 2
    return ((flagged or not isolated)
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


def helper_failure(sandbox: Sandbox, workspace: Path, rules: Contract) -> CheckResult:
    """Run each declared helper fixture in the sandbox. Return the first failure, or `PASSED` when all pass."""
    helper = rules.helper
    if helper is None:
        return PASSED
    for index, fixture in enumerate(helper.fixtures):
        (workspace / helper.input).parent.mkdir(parents=True, exist_ok=True)
        _ = (workspace / helper.input).write_text(cast(str, fixture["input"]))
        code, stdout, stderr = sandbox.sandbox(workspace, [
            "/usr/bin/python3", "-I", f".agents/skills/{rules.skill}/{helper.path}", helper.input])
        stream = fixture_failure(fixture, code, stdout, stderr)
        if stream is not None:
            return CheckResult(False, f"helper fixture {index} fails on {stream}", "fail")
    return PASSED


def _paths(relative: str, workspace: str | None) -> set[str]:
    return {relative, str(PurePosixPath(workspace) / relative)} if workspace else {relative}
