"""Deterministic protocol fixture, not evidence of operating-system isolation."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import cast


def execute(argv: list[str], workspace: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=workspace, capture_output=True, text=True, check=False)


def sandbox(request: dict[str, object], mode: str, workspace: Path) -> dict[str, object]:
    argv = cast(list[str], request["argv"])
    if "-c" in argv and mode == "writable-candidate":
        # Execute the real probe with read/network denials but no write denial.
        prefix = ("import os,pathlib,socket\n"
                  + "def deny(*args,**kwargs): raise PermissionError()\n"
                  + "pathlib.Path.read_bytes=deny\nsocket.socket=deny\nos.environ.clear()\n")
        result = execute(argv[:-1] + [prefix + argv[-1]], workspace)
        assert "candidate write isolation failed" in result.stderr
        return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    if "-c" in argv:
        # A canned probe response tests protocol wiring only.
        return {"returncode": 0, "stdout": "isolation-ok\n", "stderr": ""}
    result = execute(argv, workspace)
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def inspection(request: dict[str, object], workspace: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    name = str(request["skill_name"])
    skill = workspace / ".agents/skills" / name
    commands = [["cat", f".agents/skills/{name}/SKILL.md"]]
    if (skill / "scripts/inspect_skill.py").is_file():
        commands.append(["/usr/bin/python3", "-I", f".agents/skills/{name}/scripts/inspect_skill.py", "fixture.md"])
    results = [execute(argv, workspace) for argv in commands]
    trace: list[dict[str, object]] = [{"argv": argv, "exit_code": result.returncode}
                                      for argv, result in zip(commands, results)]
    if len(commands) == 1:
        content = "{}"
    else:
        facts = cast(dict[str, object], json.loads(results[-1].stdout))
        keys = cast(list[str], facts["frontmatter_keys"])
        findings = [f"missing-{key}" for key in ("name", "description") if key not in keys]
        if "description: TODO" in (workspace / "fixture.md").read_text():
            findings.append("placeholder-description")
        content = (json.dumps({"facts": facts, "findings": sorted(findings)})
                   if "OPTIMIZED" in (skill / "SKILL.md").read_text() else "{}")
    if "Audit the supplied files" in str(request["prompt"]):
        content = '{"findings":[]}'
    answer: dict[str, object] = {"result_json": content,
                                 "load_marker": (skill / "EXPERIMENT_MARKER").read_text()}
    return answer, trace


def infer(request: dict[str, object], mode: str, workspace: Path) -> dict[str, object]:
    if mode == "fail":
        raise SystemExit(3)
    if mode == "timeout":
        time.sleep(10)
    schema = cast(dict[str, object], request["response_schema"])
    fields = cast(list[str], schema["required"])
    trace: list[dict[str, object]] = []
    answer: dict[str, object]
    if "result_json" in fields:
        answer, trace = inspection(request, workspace)
    elif "score_percent" in fields:
        answer = {"score_percent": 100}
    elif "matches" in fields:
        assert not list((workspace / ".agents/skills").iterdir())
        answer = {"matches": []}
    else:
        payload = cast(dict[str, dict[str, str]], json.loads(str(request["prompt"]).rstrip("\n").rsplit("\n", 1)[-1]))
        answer = {key: payload["candidate"][key] + "\n# OPTIMIZED\n" for key in fields}
    if mode in {"empty-result", "whitespace-result", "invalid-result"}:
        answer["result_json"] = {"empty-result": "", "whitespace-result": " \n", "invalid-result": "not-json"}[mode]
    if mode == "wrong-schema":
        answer = {"unexpected": "value"}
    return {"answer": answer, "trace": [] if mode == "no-trace" else trace, "usage": None}


def main() -> None:
    request = cast(dict[str, object], json.load(sys.stdin))
    settings = cast(dict[str, str], json.loads(Path(sys.argv[1]).read_text()))
    operation = str(request["operation"])
    workspace = Path(str(request["workspace"]))
    with Path(settings["log"]).open("a") as log:
        _ = log.write(json.dumps(request) + "\n")
    mode = settings.get("mode", "normal")
    response: dict[str, object] = {"schema_version": 1, "operation": operation}
    if operation == "sandbox":
        response.update(sandbox(request, mode, workspace))
    elif operation == "discover":
        skills = [{"name": str(request["skill_name"]), "path": str(request["skill_path"])}]
        response["skills"] = [] if mode == "no-discovery" else skills
    else:
        response.update(infer(request, mode, workspace))
    if mode == "bad-version":
        response["schema_version"] = 2
    print(json.dumps(response))

if __name__ == "__main__":
    main()
