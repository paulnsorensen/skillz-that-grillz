#!/usr/bin/env python3
"""Deterministic stand-in for the `claude` executable. It is not evidence of isolation.

The mode comes from a sidecar file named `<script>.mode`. Each call appends its argv, working
directory, settings, and standard input to `<script>.log`.
"""
from __future__ import annotations

import json
import os
import re
import socket
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


def _writable(path: str, settings: dict[str, object]) -> bool:
    """Mimic the sandbox write rule: a path under a `denyWrite` root fails."""
    filesystem = cast(dict[str, list[str]], cast(dict[str, object], settings.get("sandbox", {})).get("filesystem", {}))
    return not any(_inside(path, [root]) for root in filesystem.get("denyWrite", []))


def write_probe(writes: list[tuple[str, str]], mode: str, config: dict[str, object]) -> list[str]:
    """Answer the preflight write probe. `write-agents` lets the `.agents` write succeed; `write-broken` fails every write.

    `write-skips-agents` runs the control write and skips the `.agents` write.
    """
    texts: list[str] = []
    for index, (token, path) in enumerate(writes):
        agents = "/.agents/" in path
        if mode == "write-skips-agents" and agents:
            continue
        writes_file = mode != "write-broken" and (_writable(path, config) or (mode == "write-agents" and agents))
        if writes_file:
            _ = Path(path).write_text(token)
        texts.append("" if writes_file else "denied")
        emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"w{index}", "name": "Bash",
              "input": {"command": f"printf {token} > {path}"}}]}})
        emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"w{index}",
              "is_error": not writes_file, "content": texts[-1]}]}})
    return texts


def _reach(port: int, payload: str) -> bool:
    with socket.socket() as client:
        reached = client.connect_ex(("127.0.0.1", port)) == 0
        if reached:
            client.sendall(payload.encode())
    return reached


def network_probe(prompt: str, mode: str) -> list[str]:
    """Answer the preflight network probes: one direct TCP command and two curl commands.

    `net-open` and `net-http-open` reach the listener over TCP and over HTTP. `net-lie` prints the denial
    under a command that is not the generated one. `net-lie-connect` connects and still prints the denial.
    `net-skipped` runs no command, `net-garbled` prints no token, and `net-no-curl` reports exit 127.
    `net-exit-<code>` reports that exit code for each curl command.
    """
    texts: list[str] = []
    for index, command in enumerate(cast(list[str], re.findall(r"`(/usr/bin/(?:python3 -c|curl) [^`]*)`", prompt))):
        if mode == "net-skipped":
            break
        direct = "connect_ex" in command
        port = int(cast(re.Match[str], re.search(r"(?:connect_ex\(\('127\.0\.0\.1',|:)(\d+)[)/]", command)).group(1))
        token = cast(re.Match[str], re.search(r"([0-9a-f]{32})", command)).group(1)
        reached = (direct and mode in {"net-open", "net-lie-connect"}
                   and _reach(port, token)) or (not direct and mode == "net-http-open"
                                                and _reach(port, f"GET /{token} HTTP/1.1\r\n\r\n"))
        if mode == "net-garbled":
            text = "garbled"
        elif direct:
            text = ("open-" if reached and mode == "net-open" else "denied-") + token
        else:
            forced = re.fullmatch(r"net-exit-(\d+)", mode)
            code = int(forced.group(1)) if forced else 127 if mode == "net-no-curl" else 0 if reached else 7
            text = f"exit-{code}-{token}"
        shown = f"echo {text} # {'connect_ex' if direct else 'curl'}" if mode == "net-lie" else command
        emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"n{index}", "name": "Bash",
              "input": {"command": shown}}]}})
        emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"n{index}",
              "is_error": False, "content": text}]}})
        texts.append(text)
    return texts


def probe(targets: list[str], writes: list[tuple[str, str]], mode: str, config: dict[str, object], prompt: str) -> int:
    """Answer the preflight read probe. Each target is one `cat` call; modes break the probe in one way."""
    if mode == "skips-cat":
        emit({"type": "result", "subtype": "success", "is_error": False, "result": "denied", "usage": USAGE})
        return 0
    texts: list[str] = []
    for index, path in enumerate(targets):
        reads = (mode == "read-host" and index == 0) or (mode not in {"bash-broken", "read-fallback"} and _readable(path, config))
        reads = reads or (mode == "read-fallback" and index == 0 and _readable(path, config))
        text = Path(path).read_text() if reads and Path(path).is_file() else "denied"
        texts.append(text)
        emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"p{index}", "name": "Bash",
              "input": {"command": f"cat {path}"}}]}})
        emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"p{index}",
              "is_error": text == "denied", "content": text}]}})
    if mode == "read-fallback":
        emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "r1", "name": "Read",
              "input": {"file_path": targets[-1]}}]}})
        emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "r1",
              "is_error": False, "content": Path(targets[-1]).read_text()}]}})
        texts[-1] = Path(targets[-1]).read_text()
    texts += write_probe(writes, mode, config)
    texts += network_probe(prompt, mode)
    emit({"type": "result", "subtype": "success", "is_error": False, "result": " ".join(texts), "usage": USAGE})
    return 0


def main() -> int:
    script = Path(__file__).resolve()
    mode_file = script.with_name(script.name + ".mode")
    mode = mode_file.read_text().strip() if mode_file.is_file() else "ok"
    argv = sys.argv[1:]
    prompt = sys.stdin.read()
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
    if mode != "no-init":
        emit({"type": "system", "subtype": "init", "skills": skills, "tools": ["Bash", "Read", "Skill"]})
    if mode == "auth-fail":
        emit({"type": "result", "subtype": "success", "is_error": True, "result": "Invalid API key - Please run /login",
              "usage": {}})
        return 1
    targets = re.findall(r"`cat (\S+)` with the Bash tool", prompt)
    if targets:
        writes = re.findall(r"`printf (\S+) > (\S+)` with the Bash tool", prompt)
        return probe(targets, writes, mode, config, prompt)
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
