#!/usr/bin/env python3
"""Deterministic stand-in for the `claude` executable. It is not evidence of isolation.

The mode comes from a sidecar file named `<script>.mode`. Each call appends its argv, working
directory, settings, standard input, environment, and the entries of its `CLAUDE_CONFIG_DIR` to `<script>.log`.
An `initialize` control request (the free skill inventory) logs to `<script>.inventory.log` instead.
Like Claude Code 2.1.29x, it lists its own commands and built-in plugins even when bundled skills are off.
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
BUILTIN_COMMANDS = ("design", "doctor", "clear")
BUILTIN_SKILLS = ("design", "doctor")
BUILTIN_PLUGINS = [{"name": "cc-plugin-agents-md", "path": "builtin", "source": "cc-plugin-agents-md@builtin"}]
ACCOUNT_SKILL = "anthropic-skills:pdf"
PLUGIN_SKILL = "plug:tool"


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
        if "AF_UNIX" in command:
            texts.append(unix_probe(index, command, mode))
            continue
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


def unix_probe(index: int, command: str, mode: str) -> str:
    """Answer the host Unix-socket probe. `unix-open` connects and sends the token; `unix-skipped` prints nothing."""
    path = cast(re.Match[str], re.search(r"connect_ex\('([^']+)'\)", command)).group(1)
    path = "\0" + path[2:] if path.startswith("\\0") else path
    token = cast(re.Match[str], re.search(r"([0-9a-f]{32})", command)).group(1)
    text = "" if mode == "unix-skipped" else f"denied-{token}"
    if mode == "unix-open":
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(path)
            client.sendall(token.encode())
        text = f"open-{token}"
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"u{index}", "name": "Bash",
          "input": {"command": command}}]}})
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"u{index}",
          "is_error": False, "content": text}]}})
    return text


def listed_skills(mode: str, config: dict[str, object]) -> list[str]:
    """Return the non-built-in skills that load: the project skills, then the mode's account or plugin skill.

    `skillOverrides` turns off every skill it names except a plugin skill, as in Claude Code.
    """
    project = next((root for root in (Path.cwd(), Path.cwd().parent) if (root / ".claude/skills").is_dir()), Path.cwd())
    skills_root = project / ".claude/skills"
    skills = sorted(path.name for path in skills_root.iterdir()) if skills_root.is_dir() else []
    if mode == "account-skill":
        skills.append(ACCOUNT_SKILL)
    off = {name for name, value in cast(dict[str, str], config.get("skillOverrides", {})).items() if value == "off"}
    skills = [name for name in skills if name not in off]
    return skills + ([PLUGIN_SKILL] if mode == "plugin-skill" else [])


def inventory(mode: str, config: dict[str, object]) -> int:
    """Answer an `initialize` control request. `inventory-broken` sends no command list.

    `builtin-echo` also lists `echo-skill` as a Claude Code command, so the echo candidate name collides with it.
    """
    commands: list[dict[str, object]] = [{"name": name, "description": ""} for name in listed_skills(mode, config)]
    builtin = [*BUILTIN_COMMANDS, *(["echo-skill"] if mode == "builtin-echo" else [])]
    commands += [cast(dict[str, object], {"name": name, "description": "", "builtin": True}) for name in builtin]
    body: dict[str, object] = {} if mode == "inventory-broken" else {"commands": commands, "agents": []}
    emit({"type": "control_response", "response": {"subtype": "success", "request_id": "skillz-inventory",
                                                  "response": body}})
    return 0


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


def config_entries() -> dict[str, str]:
    """List the config directory: each symlink maps to its target, and each other entry to `file`."""
    directory = Path(os.environ.get("CLAUDE_CONFIG_DIR", "/nonexistent"))
    if not directory.is_dir():
        return {}
    return {path.name: os.readlink(path) if path.is_symlink() else "file" for path in directory.iterdir()}


def main() -> int:
    script = Path(__file__).resolve()
    mode_file = script.with_name(script.name + ".mode")
    mode = mode_file.read_text().strip() if mode_file.is_file() else "ok"
    argv = sys.argv[1:]
    prompt = sys.stdin.read()
    settings = Path(argv[argv.index("--settings") + 1]).read_text() if "--settings" in argv else None
    listing = "--input-format" in argv
    with script.with_name(script.name + (".inventory.log" if listing else ".log")).open("a") as log:
        _ = log.write(json.dumps({"argv": argv, "cwd": str(Path.cwd()), "settings": settings, "prompt": prompt,
                                  "environment": dict(os.environ), "config_entries": config_entries()}) + "\n")
    config = cast(dict[str, object], json.loads(settings or "{}"))
    if listing:
        return inventory(mode, config)
    if mode == "no-init-auth":
        print("Invalid API key - Please run /login", file=sys.stderr)
        return 1
    if mode == "sandbox-unavailable":
        print("sandbox is unavailable: bubblewrap is missing and failIfUnavailable is set", file=sys.stderr)
        return 1
    skills = listed_skills(mode, config) + list(BUILTIN_SKILLS)
    if mode == "foreign-skill":
        skills.append("personal-intruder")
    bundled_off = config.get("disableBundledSkills") is True or os.environ.get("CLAUDE_CODE_DISABLE_BUNDLED_SKILLS") == "1"
    if mode == "bundled-skill" and not bundled_off:
        skills.append("code-review")
    if mode == "missing-skill":
        skills = []
    if mode != "no-init":
        init: dict[str, object] = {"type": "system", "subtype": "init", "skills": skills, "tools": ["Bash", "Read", "Skill"],
                                   "plugins": list(BUILTIN_PLUGINS), "mcp_servers": [], "agents": ["general-purpose", "Explore"]}
        if mode == "foreign-plugin":
            init["plugins"] = [{"name": "user-plugin", "path": "/x"}]
        if mode == "foreign-mcp":
            init["mcp_servers"] = [{"name": "user-server", "status": "connected"}]
        if mode == "foreign-agent":
            init["agents"] = ["general-purpose", "user-agent"]
        emit(init)
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
