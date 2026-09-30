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
        argv = cast(list[str], request["argv"])
        if "-c" in argv and mode == "writable-candidate":
            # Execute the real probe with read/network denials but no write denial.
            prefix = ("import os,pathlib,socket\n"
                      + "def deny(*args,**kwargs): raise PermissionError()\n"
                      + "pathlib.Path.read_bytes=deny\nsocket.socket=deny\nos.environ.clear()\n")
            result = execute(argv[:-1] + [prefix + argv[-1]], workspace)
            assert "candidate write isolation failed" in result.stderr
            response.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        elif "-c" in argv:
            # A canned probe response tests protocol wiring only.
            response.update(returncode=0, stdout="isolation-ok\n", stderr="")
        else:
            result = execute(argv, workspace)
            response.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
    elif operation == "discover":
        skills = [{"name": "skillz", "path": str(request["skill_path"])}]
        response["skills"] = [] if mode == "no-discovery" else skills
    else:
        if mode == "fail":
            raise SystemExit(3)
        if mode == "timeout":
            time.sleep(10)
        schema = cast(dict[str, object], request["response_schema"])
        answer: dict[str, object]
        fields = cast(list[str], schema["required"])
        trace: list[dict[str, object]] = []
        if "result_json" in fields:
            skill = workspace / ".agents/skills/skillz"
            commands = [["cat", ".agents/skills/skillz/SKILL.md"],
                        ["/usr/bin/python3", "-I", ".agents/skills/skillz/scripts/inspect_skill.py", "fixture.md"]]
            results = [execute(argv, workspace) for argv in commands]
            trace = [{"argv": argv, "exit_code": result.returncode} for argv, result in zip(commands, results)]
            facts = cast(dict[str, object], json.loads(results[-1].stdout))
            keys = cast(list[str], facts["frontmatter_keys"])
            findings = [f"missing-{name}" for name in ("name", "description") if name not in keys]
            if "description: TODO" in (workspace / "fixture.md").read_text():
                findings.append("placeholder-description")
            content = (json.dumps({"facts": facts, "findings": sorted(findings)})
                       if "OPTIMIZED" in (skill / "SKILL.md").read_text() else "{}")
            if "Audit the supplied files" in str(request["prompt"]):
                content = '{"findings":[]}'
            answer = {"result_json": content, "load_marker": (skill / "EXPERIMENT_MARKER").read_text()}
        elif "matches" in fields:
            assert not (workspace / ".agents/skills/skillz").exists()
            answer = {"matches": []}
        else:
            payload = cast(dict[str, dict[str, str]], json.loads(str(request["prompt"]).split("\n", 1)[1]))
            answer = {key: payload["candidate"][key] + "\n# OPTIMIZED\n" for key in fields}
        if mode in {"empty-result", "whitespace-result", "invalid-result"}:
            answer["result_json"] = {"empty-result": "", "whitespace-result": " \n", "invalid-result": "not-json"}[mode]
        if mode == "wrong-schema":
            answer = {"unexpected": "value"}
        response.update(answer=answer, trace=[] if mode == "no-trace" else trace, usage=None)
    if mode == "bad-version":
        response["schema_version"] = 2
    print(json.dumps(response))


if __name__ == "__main__":
    main()
