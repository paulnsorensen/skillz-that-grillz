#!/usr/bin/env python3
"""Deterministic Claude protocol fixture; OS isolation is synthetic in this profile."""
from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import cast

import fake_claude
from vendor_common import USAGE, execute, judge, propose, task, task_usage


def emit(event: dict[str, object]) -> None:
    print(json.dumps(event), flush=True)


def settings() -> dict[str, str]:
    return cast(dict[str, str], json.loads(Path(__file__).with_name("vendor-settings.json").read_text()))


def log(entry: dict[str, object]) -> None:
    with Path(settings()["log"]).open("a") as stream:
        _ = stream.write(json.dumps(entry) + "\n")


def init(skill: str | None) -> None:
    emit({"type": "system", "subtype": "init", "skills": [*([skill] if skill else []), "design", "doctor"],
          "tools": ["Bash", "Read", "Skill"], "plugins": fake_claude.BUILTIN_PLUGINS,
          "mcp_servers": [], "agents": ["general-purpose", "Explore"]})


def tool(argv: list[str], code: int, output: str) -> None:
    name = "e2e-" + str(len(argv)) + "-" + argv[-1].split("/")[-1]
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": name, "name": "Bash",
          "input": {"command": shlex.join(argv)}}]}})
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": name,
          "is_error": code != 0, "content": output}]}})


def one_shot(argv: list[str]) -> int:
    prompt = sys.stdin.read()
    schema = cast(dict[str, object], json.loads(argv[argv.index("--json-schema") + 1]))
    fields = cast(list[str], schema["required"])
    mode = settings()["mode"]
    markers = sorted(Path.cwd().glob(".agents/skills/*/EXPERIMENT_MARKER"))
    skill = markers[0].parent.name if markers else None
    init(skill)
    if skill is not None:
        answer, trace = task(Path.cwd(), skill, mode)
        for item in trace:
            command = cast(list[str], item["argv"])
            code = cast(int, item["exit_code"])
            tool(command, code, cast(str, item.get("stdout", "")) or cast(str, item.get("stderr", "")))
        marker_command = ["cat", f".agents/skills/{skill}/EXPERIMENT_MARKER"]
        code, stdout, _ = execute(marker_command, Path.cwd())
        tool(marker_command, code, stdout)
        role = "task"
    elif "score_percent" in fields:
        if mode == "journey":
            from journey import judge as journey_judge
            answer = journey_judge(prompt, fields)
        else:
            answer = judge(prompt, fields)
        role = "judge"
    elif fields == ["reply", "done"] and mode == "journey":
        from journey import simulate
        answer, role = simulate(prompt, fields), "simulator"
    else:
        answer, role = propose(prompt, fields, mode), "reflection"
    archive = Path.cwd() / "archive.sha256"
    usage = task_usage(Path.cwd(), skill, mode) if skill is not None else USAGE
    log({"role": role, "prompt": prompt, "candidate": markers[0].read_text() if markers else None,
         "visible_input": (Path.cwd() / "fixture.md").read_text() if role == "task" else None,
         "usage": usage, "answer": answer if role == "task" else None,
         "archive_sha256": archive.read_text().strip() if role == "task" and archive.is_file() else None})
    event: dict[str, object] = {"type": "result", "subtype": "success", "is_error": False,
                                "result": "done", "structured_output": answer}
    if usage is not None:
        event["usage"] = {"input_tokens": usage["input_tokens"] - usage["cached_input_tokens"],
                          "cache_creation_input_tokens": 0,
                          "cache_read_input_tokens": usage["cached_input_tokens"],
                          "output_tokens": usage["output_tokens"]}
    emit(event)
    return 0


def conversation() -> int:
    from journey import handle
    markers = sorted(Path.cwd().glob(".agents/skills/*/EXPERIMENT_MARKER"))
    skill = markers[0].parent.name if markers else None
    for turn, line in enumerate(sys.stdin, 1):
        message = cast(str, cast(dict[str, object], json.loads(line)["message"])["content"])
        init(skill)
        if skill is None:
            raise ValueError("journey has no staged skill")
        reply, events = handle(Path.cwd(), skill, message, turn)
        for event in events:
            emit(event)
        emit({"type": "assistant", "message": {"content": [{"type": "text", "text": reply}]}})
        emit({"type": "result", "subtype": "success", "is_error": False, "result": reply,
              "usage": fake_claude.USAGE})
        log({"role": "task", "turn": turn, "message": message, "candidate": markers[0].read_text()})
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if "--version" in argv:
        print("2.1.291 (Claude Code)")
        return 0
    if "--input-format" in argv and "--model" in argv:
        return conversation()
    if "--json-schema" in argv:
        return one_shot(argv)
    fake_claude.__file__ = str(Path(__file__).with_name("fake-claude-delegate.py"))
    return fake_claude.main()


if __name__ == "__main__":
    raise SystemExit(main())
