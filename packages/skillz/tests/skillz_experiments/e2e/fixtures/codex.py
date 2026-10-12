#!/usr/bin/env python3
"""Deterministic Codex protocol fixture; OS isolation is synthetic."""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import cast

from vendor_common import USAGE, judge, propose, task, task_usage


def settings() -> dict[str, str]:
    return cast(dict[str, str], json.loads(Path(__file__).with_name("vendor-settings.json").read_text()))


def log(entry: dict[str, object]) -> None:
    with Path(settings()["log"]).open("a") as stream:
        _ = stream.write(json.dumps(entry) + "\n")


def sandbox(argv: list[str]) -> int:
    values = argv[argv.index("--") + 1:]
    if values[:2] == ["/usr/bin/env", "-i"]:
        values = values[2:]
    while values and "=" in values[0] and not values[0].startswith("/"):
        _ = values.pop(0)
    if (values[:2] == ["/usr/bin/python3", "-c"] and len(values) == 3
            and "read isolation failed" in values[2] and "network isolation failed" in values[2]):
        print("isolation-ok")
        return 0
    result = subprocess.run(values, cwd=Path.cwd(), capture_output=True, text=True, check=False)
    _ = sys.stdout.write(result.stdout)
    _ = sys.stderr.write(result.stderr)
    return result.returncode


def discover() -> int:
    for line in sys.stdin:
        request = cast(dict[str, object], json.loads(line))
        if request.get("id") == 0:
            print(json.dumps({"id": 0, "result": {"capabilities": {}}}), flush=True)
        elif request.get("id") == 1:
            params = cast(dict[str, object], request["params"])
            workspace = Path(cast(list[str], params["cwds"])[0])
            skills = sorted((workspace / ".agents/skills").glob("*/SKILL.md"))
            listing = [{"name": skill.parent.name, "enabled": True, "scope": "repo",
                        "pluginId": None, "path": str(skill)} for skill in skills]
            print(json.dumps({"id": 1, "result": {"data": [{"cwd": str(workspace),
                             "errors": [], "skills": listing}]}}), flush=True)
    return 0


def execute(argv: list[str]) -> int:
    if "--help" in argv:
        print("Codex fixture accepts isolated execution options")
        return 0
    workspace = Path(argv[argv.index("-C") + 1])
    schema = cast(dict[str, object], json.loads(Path(argv[argv.index("--output-schema") + 1]).read_text()))
    fields = cast(list[str], schema["required"])
    prompt = sys.stdin.read()
    markers = sorted(workspace.glob(".agents/skills/*/EXPERIMENT_MARKER"))
    if markers:
        answer, trace = task(workspace, markers[0].parent.name, settings()["mode"])
        role = "task"
        for item in trace:
            command = cast(list[str], item["argv"])
            print(json.dumps({"type": "item.completed", "item": {"type": "command_execution",
                  "command": shlex.join(command), "exit_code": item["exit_code"]}}), flush=True)
        marker_command = ["cat", f".agents/skills/{markers[0].parent.name}/EXPERIMENT_MARKER"]
        marker_result = subprocess.run(marker_command, cwd=workspace, capture_output=True, text=True, check=False)
        print(json.dumps({"type": "item.completed", "item": {"type": "command_execution",
              "command": shlex.join(marker_command), "exit_code": marker_result.returncode}}), flush=True)
    elif "score_percent" in fields:
        answer, role = judge(prompt, fields), "judge"
    else:
        answer, role = propose(prompt, fields, settings()["mode"]), "reflection"
    _ = Path(argv[argv.index("--output-last-message") + 1]).write_text(json.dumps(answer))
    usage = task_usage(workspace, markers[0].parent.name, settings()["mode"]) if markers else USAGE
    event: dict[str, object] = {"type": "turn.completed"}
    if usage is not None:
        event["usage"] = usage
    print(json.dumps(event), flush=True)
    archive = workspace / "archive.sha256"
    log({"role": role, "prompt": prompt, "candidate": markers[0].read_text() if markers else None,
         "visible_input": (workspace / "fixture.md").read_text() if role == "task" else None,
         "usage": usage, "answer": answer if role == "task" else None,
         "archive_sha256": archive.read_text().strip() if role == "task" and archive.is_file() else None})
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv == ["--version"]:
        print("codex-cli 0.154.0")
        return 0
    if argv[:1] == ["sandbox"]:
        return sandbox(argv)
    if argv[:1] == ["app-server"]:
        return discover()
    if argv[:1] == ["exec"]:
        return execute(argv)
    raise ValueError("unsupported Codex fixture invocation")


if __name__ == "__main__":
    raise SystemExit(main())
