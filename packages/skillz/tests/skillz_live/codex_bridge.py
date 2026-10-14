"""Test-only command-adapter bridge to `codex exec`, protocol version 1, for the live smoke run.

The runner sends one JSON request on stdin and reads one JSON response on stdout. `sandbox` and `infer` run Codex with
the flags of the built-in Codex adapter, so `--harness command` drives a real model under a real OS sandbox.
Usage: python3 -I codex_bridge.py SETTINGS, where SETTINGS is a JSON file with `codex` and `codex_home` paths.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import cast

from skillz_experiments._protocol import USAGE_KEYS, VERSION
from skillz_experiments.harness._codex import configuration, private_task_tmpdir, task_tmpdir


def _environment(workspace: Path, codex_home: Path) -> dict[str, str]:
    tmpdir = private_task_tmpdir(workspace)
    return {"PATH": "/usr/bin:/bin", "HOME": str(workspace / "home"), "TMPDIR": str(tmpdir),
            "LANG": "C.UTF-8", "CODEX_HOME": str(codex_home)}


def _sandbox(request: dict[str, object], codex: Path, codex_home: Path) -> dict[str, object]:
    workspace = Path(str(request["workspace"]))
    safe = ["PATH=/usr/bin:/bin", f"HOME={workspace / 'home'}", f"TMPDIR={task_tmpdir(workspace)}", "LANG=C.UTF-8"]
    command = [str(codex), "sandbox", *configuration(workspace, codex, []), "-P", "skillz", "--include-managed-config",
               "-C", str(workspace), "--", "/usr/bin/env", "-i", *safe, *cast(list[str], request["argv"])]
    result = subprocess.run(command, cwd=workspace, env=_environment(workspace, codex_home), capture_output=True,
                            text=True, timeout=120, check=False)
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def _discover(request: dict[str, object]) -> dict[str, object]:
    """Report the staged skill when its frontmatter names it. Codex itself finds it under `.agents/skills`."""
    name, path = str(request["skill_name"]), Path(str(request["skill_path"]))
    text = (path / "SKILL.md").read_text(encoding="utf-8") if (path / "SKILL.md").is_file() else ""
    found = re.search(r"^name:\s*(\S+)\s*$", text.split("\n---", 1)[0], re.MULTILINE)
    return {"skills": [{"name": name, "path": str(path)}] if found and found.group(1) == name else []}


def _events(stdout: str) -> tuple[list[dict[str, object]], dict[str, int] | None]:
    """Return the completed commands and the token usage from the `codex exec --json` event lines."""
    trace: list[dict[str, object]] = []
    usage: dict[str, int] | None = None
    for line in stdout.splitlines():
        try:
            event = cast(object, json.loads(line))
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        fields = cast(dict[str, object], event)
        item, counts = fields.get("item"), fields.get("usage")
        if fields.get("type") == "item.completed" and isinstance(item, dict):
            entry = cast(dict[str, object], item)
            code = entry.get("exit_code")
            if entry.get("type") == "command_execution" and type(code) is int:
                text = str(entry.get("command", ""))
                try:
                    argv = shlex.split(text)
                except ValueError:
                    argv = [text]
                if argv and all(argv):
                    trace.append({"argv": argv, "exit_code": code})
        if fields.get("type") == "turn.completed" and isinstance(counts, dict):
            values = cast(dict[str, object], counts)
            usage = {key: value for key in USAGE_KEYS if type(value := values.get(key)) is int}
    return trace, usage


def _infer(request: dict[str, object], codex: Path, codex_home: Path) -> dict[str, object]:
    workspace = Path(str(request["workspace"]))
    with tempfile.TemporaryDirectory(prefix="codex-bridge-") as directory:
        schema, answer = Path(directory) / "schema.json", Path(directory) / "answer.json"
        _ = schema.write_text(json.dumps(request["response_schema"]), encoding="utf-8")
        command = [str(codex), "exec", "--strict-config", *configuration(workspace, codex, []), "--ignore-user-config",
                   "--ignore-rules", "--ephemeral", "--json", "--skip-git-repo-check", "-C", str(workspace),
                   "--model", str(request["model"]), "--output-schema", str(schema), "--output-last-message", str(answer), "-"]
        result = subprocess.run(command, cwd=workspace, env=_environment(workspace, codex_home), input=str(request["prompt"]),
                                capture_output=True, text=True, timeout=900, check=False)
        if result.returncode or not answer.is_file():
            raise RuntimeError(f"codex exec exited {result.returncode}: {result.stderr.strip()[-300:]}")
        trace, usage = _events(result.stdout)
        return {"answer": cast(object, json.loads(answer.read_text(encoding="utf-8"))), "trace": trace, "usage": usage}


def main() -> int:
    settings = cast(dict[str, str], json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    codex, host = Path(settings["codex"]), Path(settings["codex_home"])
    request = cast(dict[str, object], json.load(sys.stdin))
    operation = str(request["operation"])
    with tempfile.TemporaryDirectory(prefix="codex-bridge-home-") as directory:
        home = Path(directory)
        (home / "auth.json").symlink_to(host / "auth.json")
        try:
            if operation == "sandbox":
                body = _sandbox(request, codex, home)
            elif operation == "discover":
                body = _discover(request)
            else:
                body = _infer(request, codex, home)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            print(json.dumps({"error": str(error)[:400], "exit_code": 1}), file=sys.stderr)
            return 1
    print(json.dumps({"schema_version": VERSION, "operation": operation, **body}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
