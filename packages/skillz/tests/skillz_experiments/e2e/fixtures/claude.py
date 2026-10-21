#!/usr/bin/env python3
"""Deterministic Claude protocol fixture; OS isolation is synthetic in this profile."""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import cast

import fake_claude
from vendor_common import USAGE, append_log, execute, judge, log_entry, propose, settings, task, task_usage

# The fixture runs under the host python3 without skillz_experiments; a test pins this to `_keys.API_KEY`.
API_KEY = re.compile(r"[a-zA-Z0-9_.-]{1,64}")


def emit(event: dict[str, object]) -> None:
    print(json.dumps(event), flush=True)


def log(entry: dict[str, object]) -> None:
    append_log(entry | {"config_dir": os.environ.get("CLAUDE_CONFIG_DIR")})


def init(skill: str | None) -> None:
    emit({"type": "system", "subtype": "init", "skills": [*([skill] if skill else []), "design", "doctor"],
          "tools": ["Bash", "Read", "Skill"], "plugins": fake_claude.BUILTIN_PLUGINS,
          "mcp_servers": [], "agents": ["general-purpose", "Explore"]})


def real_names(properties: dict[str, dict[str, str]], fields: list[str], names: set[str]) -> dict[str, str]:
    """Map each sent key to its real name; only a renamed key carries its real name in `description`."""
    return {key: key if API_KEY.fullmatch(key) and key in names else properties[key].get("description", key)
            for key in fields}


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
    elif "criteria" in fields:
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
        # The real API refuses unsafe keys, so the transport sends safe ones and keeps each real key in `description`.
        properties = cast(dict[str, dict[str, str]], schema["properties"])
        candidate = cast(dict[str, object], json.loads(prompt.split("\n", 1)[1])["candidate"])
        real = real_names(properties, fields, set(candidate))
        proposed = propose(prompt, list(real.values()), mode)
        answer, role = {key: proposed[name] for key, name in real.items() if name in proposed}, "reflection"
    usage = task_usage(Path.cwd(), skill, mode) if skill is not None else USAGE
    log(log_entry(role, prompt, Path.cwd(), markers[0] if markers else None, usage, answer))
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
