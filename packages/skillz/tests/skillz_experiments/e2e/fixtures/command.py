#!/usr/bin/env python3
"""Protocol-correct command adapter with a synthetic isolation probe."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import cast

from vendor_common import USAGE, execute, is_isolation_probe, judge, propose, task, task_usage


def respond(request: dict[str, object], mode: str = "normal", observed: str = "") -> dict[str, object]:
    operation = cast(str, request["operation"])
    workspace = Path(cast(str, request["workspace"]))
    response: dict[str, object] = {"schema_version": 1, "operation": operation}
    if operation == "sandbox":
        argv = cast(list[str], request["argv"])
        if is_isolation_probe(argv):
            code, stdout, stderr = 0, "isolation-ok\n", ""
        else:
            code, stdout, stderr = execute(argv, workspace)
        response.update(returncode=code, stdout=stdout, stderr=stderr)
    elif operation == "discover":
        skill = Path(cast(str, request["skill_path"]))
        _ = (skill / "SKILL.md").read_text()
        response["skills"] = [{"name": request["skill_name"], "path": str(skill)}]
    elif operation == "infer":
        schema = cast(dict[str, object], request["response_schema"])
        fields = cast(list[str], schema["required"])
        prompt = cast(str, request["prompt"])
        if "score_percent" in fields:
            answer, trace = judge(prompt, fields), []
        elif "load_marker" in fields:
            answer, trace = task(workspace, cast(str, request["skill_name"]), mode)
        else:
            answer, trace = propose(prompt, fields, mode, observed), []
        ledger = [{"argv": item["argv"], "exit_code": item["exit_code"]} for item in trace]
        response.update(answer=answer, trace=ledger)
        usage = task_usage(workspace, cast(str, request["skill_name"]), mode) if "load_marker" in fields else USAGE
        response["usage"] = usage
    else:
        raise ValueError(f"unknown operation: {operation}")
    return response


def main() -> None:
    request = cast(dict[str, object], json.load(sys.stdin))
    settings = cast(dict[str, str], json.loads(Path(sys.argv[1]).read_text())) if len(sys.argv) > 1 else {}
    mode = settings.get("mode", "normal")
    log = Path(settings["log"]) if "log" in settings else None
    fields = cast(list[str], cast(dict[str, object], request.get("response_schema", {})).get("required", []))
    role = ("task" if "load_marker" in fields else "judge" if "score_percent" in fields
            else "reflection" if request["operation"] == "infer" else cast(str, request["operation"]))
    workspace = Path(cast(str, request["workspace"]))
    visible = log.with_suffix(".visible") if log is not None else None
    observed = (visible.read_text() if mode == "leak-answer" and role == "reflection"
                and visible is not None and visible.is_file() else "")
    if request["operation"] == "infer" and "pause_dir" in settings:
        pause_dir = Path(settings["pause_dir"])
        pending = pause_dir / f"{os.getpid()}.pending"
        release = pause_dir / f"{os.getpid()}.release"
        pending.touch()
        deadline = time.monotonic() + 30
        while not release.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not release.exists():
            raise TimeoutError("test inference release did not arrive")
        pending.unlink()
        release.unlink()
    response = respond(request, mode, observed)
    if log is not None:
        entry: dict[str, object] = {"role": role, "request": request,
                 "candidate": (workspace / ".agents/skills" / str(request.get("skill_name", "")) / "EXPERIMENT_MARKER").read_text()
                 if role == "task" else None}
        if role == "task":
            source = (workspace / "fixture.md").read_text()
            entry["visible_input"] = source
            entry["usage"] = response.get("usage")
            entry["answer"] = response["answer"]
            archive = workspace / "archive.sha256"
            if archive.is_file():
                entry["archive_sha256"] = archive.read_text().strip()
            if visible is not None:
                _ = visible.write_text(source)
        with log.open("a") as stream:
            _ = stream.write(json.dumps(entry) + "\n")
    print(json.dumps(response))


if __name__ == "__main__":
    main()
