"""Task behavior shared by external protocol fixtures."""
from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import cast


USAGE = {"input_tokens": 120, "cached_input_tokens": 20, "output_tokens": 20}
DATA_MARKER = "\nUNTRUSTED DATA (JSON):\n"
PROBE_PHRASES = ("read isolation failed", "network isolation failed")


class PromptFormatError(ValueError):
    """The prompt does not carry the untrusted-data block that production writes."""


def is_isolation_probe(argv: list[str]) -> bool:
    return (argv[:2] == ["/usr/bin/python3", "-c"] and len(argv) == 3
            and all(phrase in argv[2] for phrase in PROBE_PHRASES))


def settings() -> dict[str, str]:
    return cast(dict[str, str], json.loads(Path(__file__).with_name("vendor-settings.json").read_text()))


def parse_env_prefix(values: list[str]) -> tuple[dict[str, str], list[str]]:
    """Split leading NAME=value assignments from the command that follows `env -i`."""
    rest = list(values)
    environment: dict[str, str] = {}
    while rest and "=" in rest[0] and not rest[0].startswith("/"):
        name, value = rest.pop(0).split("=", 1)
        environment[name] = value
    return environment, rest


def log_entry(role: str, prompt: str, workspace: Path, marker: Path | None, usage: dict[str, int] | None,
              answer: Mapping[str, object]) -> dict[str, object]:
    """Build the vendor-log record; `env_names` lists names only, never values."""
    archive = workspace / "archive.sha256"
    task = role == "task"
    return {"role": role, "prompt": prompt, "candidate": marker.read_text() if marker else None,
            "visible_input": (workspace / "fixture.md").read_text() if task else None,
            "usage": usage, "answer": answer if task else None,
            "archive_sha256": archive.read_text().strip() if task and archive.is_file() else None,
            "env_names": sorted(os.environ)}


def append_log(entry: dict[str, object]) -> None:
    with Path(settings()["log"]).open("a") as stream:
        _ = stream.write(json.dumps(entry) + "\n")


def untrusted_payload(prompt: str) -> dict[str, object]:
    _, marker, data = prompt.partition(DATA_MARKER)
    if not marker:
        raise PromptFormatError("prompt has no untrusted-data block")
    return cast(dict[str, object], json.loads(data))


def task_usage(workspace: Path, skill_name: str, mode: str) -> dict[str, int] | None:
    skill = workspace / ".agents/skills" / skill_name
    instructions = (skill / "SKILL.md").read_text()
    helper = skill / "scripts/transform.py"
    improved = "Uppercase fixture.md." in instructions or (helper.is_file() and "text.upper()" in helper.read_text())
    if not improved:
        return USAGE
    if mode == "unknown-usage":
        return None
    if mode == "costly":
        return {**USAGE, "input_tokens": 240}
    if mode == "cheap":
        return {**USAGE, "input_tokens": 60}
    return USAGE


def execute(argv: list[str], workspace: Path) -> tuple[int, str, str]:
    result = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, check=False)
    return result.returncode, result.stdout, result.stderr


def propose(prompt: str, fields: list[str], mode: str = "normal", observed: str = "") -> dict[str, str | None]:
    data = cast(dict[str, object], json.loads(prompt.split("\n", 1)[1]))
    candidate = cast(dict[str, str], data["candidate"])
    answer = dict(candidate)
    for name in fields:
        if name == "SKILL.md":
            answer[name] = candidate[name].replace("Copy fixture.md unchanged.", "Uppercase fixture.md.")
        elif name.startswith("scripts/"):
            answer[name] = candidate[name].replace("text = text", "text = text.upper()")
    if mode == "targets":
        from targets import propose_target
        propose_target(candidate, fields, answer)
    elif mode == "leak-answer" and observed:
        answer["SKILL.md"] += "\n" + observed.upper()
    elif mode == "bad-helper" and "scripts/transform.py" in answer:
        answer["scripts/transform.py"] = candidate["scripts/transform.py"].replace("text = text", "raise RuntimeError('bad helper')")
    return {name: answer.get(name) for name in fields}


def task(workspace: Path, skill_name: str, mode: str = "normal") -> tuple[dict[str, str], list[dict[str, object]]]:
    skill = workspace / ".agents/skills" / skill_name
    command = ["cat", f".agents/skills/{skill_name}/SKILL.md"]
    code, instructions, stderr = execute(command, workspace)
    if code != 0:
        raise ValueError("staged skill cannot be read")
    trace: list[dict[str, object]] = [{"argv": command, "exit_code": code,
                                      "stdout": instructions, "stderr": stderr}]
    marker = (skill / "EXPERIMENT_MARKER").read_text()
    targeted = None
    if mode == "targets":
        from targets import task_target
        targeted = task_target(workspace, skill, instructions)
    helper = skill / "scripts/transform.py"
    if targeted is not None:
        value, extra = targeted
        trace.extend(extra)
    elif helper.is_file():
        command = ["/usr/bin/python3", "-I", f".agents/skills/{skill_name}/scripts/transform.py", "fixture.md"]
        code, text, stderr = execute(command, workspace)
        trace.append({"argv": command, "exit_code": code, "stdout": text, "stderr": stderr})
        if code != 0:
            raise ValueError("staged helper fails")
        value = cast(str, json.loads(text)["text"])
    else:
        value = (workspace / "fixture.md").read_text()
        if "Uppercase fixture.md." in instructions:
            value = value.upper()
            if mode == "regress" and value.startswith("PROTECTED-"):
                value = value.lower()
    output = workspace / "output"
    output.mkdir()
    _ = (output / "result.txt").write_text(value)
    return {"result_json": json.dumps({"text": value}), "load_marker": marker}, trace


def judge(prompt: str, fields: list[str]) -> dict[str, int]:
    payload = untrusted_payload(prompt)
    answer = cast(str, payload["answer"])
    reference = payload["reference"]
    score = 100 if json.loads(answer) == reference else 0
    return {name: score for name in fields}
