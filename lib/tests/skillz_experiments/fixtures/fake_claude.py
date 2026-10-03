#!/usr/bin/env python3
"""Deterministic stand-in for the `claude` executable. It is not evidence of isolation.

The mode comes from a sidecar file named `<script>.mode`. Each call appends its argv, working
directory, settings, and standard input to `<script>.log`.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import cast

USAGE = {"input_tokens": 10, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 20, "output_tokens": 7}


def emit(event: dict[str, object]) -> None:
    print(json.dumps(event))


def _inside(path: str, roots: list[str]) -> bool:
    return any(path == root or path.startswith(root.rstrip("/") + "/") for root in roots)


def _readable(path: str, settings: dict[str, object]) -> bool:
    """Mimic the sandbox read rule: the narrower allow wins over a deny."""
    filesystem = cast(dict[str, list[str]], cast(dict[str, object], settings.get("sandbox", {})).get("filesystem", {}))
    denied = [root for root in filesystem.get("denyRead", []) if _inside(path, [root])]
    allowed = [root for root in filesystem.get("allowRead", []) if _inside(path, [root])]
    if not denied:
        return True
    return bool(allowed) and max(map(len, allowed)) > max(map(len, denied))


def main() -> int:
    script = Path(__file__).resolve()
    mode_file = script.with_name(script.name + ".mode")
    mode = mode_file.read_text().strip() if mode_file.is_file() else "ok"
    argv = sys.argv[1:]
    prompt = sys.stdin.read() if "--version" not in argv else ""
    if "--version" in argv:
        print("2.1.287 (Claude Code)")
        return 0
    settings = Path(argv[argv.index("--settings") + 1]).read_text() if "--settings" in argv else None
    with script.with_name(script.name + ".log").open("a") as log:
        _ = log.write(json.dumps({"argv": argv, "cwd": str(Path.cwd()), "settings": settings, "prompt": prompt,
                                  "environment": dict(os.environ)}) + "\n")
    if mode == "no-init-auth":
        print("Invalid API key - Please run /login", file=sys.stderr)
        return 1
    if mode == "sandbox-unavailable":
        print("sandbox is unavailable: bubblewrap is missing and failIfUnavailable is set", file=sys.stderr)
        return 1
    skills_root = Path.cwd() / ".claude/skills"
    skills = sorted(path.name for path in skills_root.iterdir()) if skills_root.is_dir() else []
    if mode == "foreign-skill":
        skills.append("personal-intruder")
    config = cast(dict[str, object], json.loads(settings or "{}"))
    bundled_off = config.get("disableBundledSkills") is True or os.environ.get("CLAUDE_CODE_DISABLE_BUNDLED_SKILLS") == "1"
    if mode == "bundled-skill" and not bundled_off:
        skills.append("code-review")
    if mode == "missing-skill":
        skills = []
    emit({"type": "system", "subtype": "init", "skills": skills, "tools": ["Bash", "Read", "Skill"]})
    if mode == "auth-fail":
        emit({"type": "result", "subtype": "success", "is_error": True, "result": "Invalid API key - Please run /login",
              "usage": {}})
        return 1
    sealed = re.search(r"Run `cat (\S+)` with the Bash tool", prompt)
    if sealed is not None:
        path = sealed.group(1)
        reads = mode == "read-host" or _readable(path, config)
        text = Path(path).read_text() if reads and Path(path).is_file() else "denied"
        emit({"type": "result", "subtype": "success", "is_error": False, "result": text, "usage": USAGE})
        return 0
    schema = cast(dict[str, object], json.loads(argv[argv.index("--json-schema") + 1])) if "--json-schema" in argv else {}
    properties = cast(dict[str, object], schema.get("properties", {}))
    marker = Path.cwd() / ".agents/skills/skillz/EXPERIMENT_MARKER"
    structured: dict[str, object]
    if "score_percent" in properties:
        structured = {"score_percent": 80}
    else:
        structured = {"result_json": "{}", "load_marker": marker.read_text() if marker.is_file() else ""}
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash",
          "input": {"command": "cat .agents/skills/skillz/SKILL.md"}}]}})
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": False}]}})
    emit({"type": "result", "subtype": "success", "is_error": False, "result": "done",
          "structured_output": structured, "usage": USAGE})
    return 0


if __name__ == "__main__":
    sys.exit(main())
