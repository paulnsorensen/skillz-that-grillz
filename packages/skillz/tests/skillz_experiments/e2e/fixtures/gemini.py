#!/usr/bin/env python3
"""Offline Gemini protocol fixture. Container isolation is synthetic."""
from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import cast

from vendor_common import USAGE, execute, judge, propose, task, task_usage


def emit(event: dict[str, object]) -> None:
    print(json.dumps(event), flush=True)


def settings() -> dict[str, str]:
    return cast(dict[str, str], json.loads(Path(__file__).with_name("vendor-settings.json").read_text()))


def tool(identifier: str, argv: list[str], code: int, stdout: str, stderr: str) -> None:
    if argv[0] == "cat":
        name, parameters = "read_file", {"file_path": argv[1]}
    else:
        name, parameters = "run_shell_command", {"command": shlex.join(argv)}
    emit({"type": "tool_use", "tool_name": name, "tool_id": identifier, "parameters": parameters})
    emit({"type": "tool_result", "tool_id": identifier, "status": "success" if code == 0 else "error",
          "output": stdout if code == 0 else stderr or f"Exit Code: {code}"})


def main() -> int:
    argv = sys.argv[1:]
    if argv == ["skills", "list"]:
        for skill in sorted(Path.cwd().glob(".agents/skills/*/SKILL.md")):
            print(f"{skill.parent.name} [Enabled]\n  Location: {skill.parent.resolve()}")
        return 0
    if "--approval-mode=yolo" not in argv or "stream-json" not in argv:
        raise ValueError("unsupported Gemini fixture invocation")
    prompt = sys.stdin.read()
    source_prompt = prompt.split("\n\nReply with one JSON object", 1)[0]
    schema = cast(dict[str, object], json.loads(prompt.rsplit("\n", 1)[-1]))
    fields = cast(list[str], schema["required"])
    workspace = Path.cwd()
    markers = sorted(workspace.glob(".agents/skills/*/EXPERIMENT_MARKER"))
    mode = settings()["mode"]
    if markers:
        skill = markers[0].parent.name
        answer, trace = task(workspace, skill, mode)
        for index, item in enumerate(trace):
            tool(f"e2e-{index}", cast(list[str], item["argv"]), cast(int, item["exit_code"]),
                 cast(str, item.get("stdout", "")), cast(str, item.get("stderr", "")))
        command = ["cat", f".agents/skills/{skill}/EXPERIMENT_MARKER"]
        code, stdout, stderr = execute(command, workspace)
        tool("e2e-marker", command, code, stdout, stderr)
        role = "task"
        usage = task_usage(workspace, skill, mode)
    elif "score_percent" in fields:
        answer, role, usage = judge(source_prompt, fields), "judge", USAGE
    else:
        answer, role, usage = propose(source_prompt, fields, mode), "reflection", USAGE
    archive = workspace / "archive.sha256"
    home = Path.home()
    home_entries = sorted(str(path.relative_to(home)) + ("@" if path.is_symlink() else "")
                          for path in home.rglob("*"))
    with Path(settings()["log"]).open("a") as stream:
        _ = stream.write(json.dumps({"role": role, "prompt": prompt,
            "home": str(home), "home_entries": home_entries,
            "candidate": markers[0].read_text() if markers else None,
            "visible_input": (workspace / "fixture.md").read_text() if role == "task" else None,
            "usage": usage, "answer": answer if role == "task" else None,
            "archive_sha256": archive.read_text().strip() if role == "task" and archive.is_file() else None}) + "\n")
    emit({"type": "message", "role": "assistant",
          "content": "not JSON" if mode == "malformed-json" and role == "task" else json.dumps(answer)})
    stats: dict[str, object] = {}
    if usage is not None:
        stats = {"models": {"offline-gemini": {"input_tokens": usage["input_tokens"],
                 "cached": usage["cached_input_tokens"], "output_tokens": usage["output_tokens"]}}}
    emit({"type": "result", "status": "success", "stats": stats})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
