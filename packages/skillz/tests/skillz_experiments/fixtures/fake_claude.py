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
import time
from pathlib import Path
from typing import cast

USAGE = {"input_tokens": 10, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 20, "output_tokens": 7}
BUILTIN_COMMANDS = ("design", "doctor", "clear")
BUILTIN_SKILLS = ("design", "doctor")
BUILTIN_PLUGINS = [{"name": "cc-plugin-agents-md", "path": "builtin", "source": "cc-plugin-agents-md@builtin"}]
ACCOUNT_SKILL = "anthropic-skills:pdf"
PLUGIN_SKILL = "plug:tool"


def emit(event: dict[str, object]) -> None:
    print(json.dumps(event), flush=True)


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

    `write-skips-agents` runs the control write and skips the `.agents` write. `write-claude` and `write-skips-claude`
    do the same for the `.claude` write.
    """
    texts: list[str] = []
    for index, (token, path) in enumerate(writes):
        agents = "/.agents/" in path
        claude = "/.claude/" in path
        if (mode == "write-skips-agents" and agents) or (mode == "write-skips-claude" and claude):
            continue
        writes_file = mode != "write-broken" and (_writable(path, config) or (mode == "write-agents" and agents)
                                                 or (mode == "write-claude" and claude))
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
    `net-exit-<code>` reports that exit code for each proxied curl command. `net-dns-open` resolves the public name,
    and `net-external-open` connects to the non-loopback address; both report the open result.
    """
    texts: list[str] = []
    for index, command in enumerate(cast(list[str], re.findall(r"`(/usr/bin/(?:python3 -c|curl) [^`]*)`", prompt))):
        if mode == "net-skipped":
            break
        if "AF_UNIX" in command:
            texts.append(unix_probe(index, command, mode))
            continue
        direct = "connect_ex" in command and "'127.0.0.1'" in command
        lookup = "--noproxy '*'" in command
        external = "'1.1.1.1'" in command
        token = cast(re.Match[str], re.search(r"([0-9a-f]{32})", command)).group(1)
        if external or lookup:
            reached = mode == ("net-external-open" if external else "net-dns-open")
            text = ("open-" if reached else "denied-") + token if external else f"exit-{0 if reached else 6}-{token}"
            text = "garbled" if mode == "net-garbled" else "exit-127-" + token if lookup and mode == "net-no-curl" else text
            shown = f"echo {text} # probe" if mode == "net-lie" else command
        else:
            port = int(cast(re.Match[str], re.search(r"(?:connect_ex\(\('127\.0\.0\.1',|:)(\d+)[)/]", command)).group(1))
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
    """Answer the host Unix-socket probe. `unix-open` connects and sends the token; `unix-skipped` prints nothing.

    `abstract-open` and `abstract-skipped` do the same for the abstract-socket command only.
    """
    path = cast(re.Match[str], re.search(r"connect_ex\('([^']+)'\)", command)).group(1)
    abstract = path.startswith("\\0")
    path = "\0" + path[2:] if abstract else path
    token = cast(re.Match[str], re.search(r"([0-9a-f]{32})", command)).group(1)
    text = "" if mode == "unix-skipped" or (mode == "abstract-skipped" and abstract) else f"denied-{token}"
    if mode == "unix-open" or (mode == "abstract-open" and abstract):
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

    `inventory-error` sends an error response, `inventory-auth` fails to log in, and `inventory-hang` never answers.
    `inventory-nonzero` sends a valid list and still exits 1. `inventory-crash` exits 3 with stderr and no control response.

    `builtin-echo` also lists `echo-skill` as a Claude Code command, so the echo candidate name collides with it.
    """
    commands: list[dict[str, object]] = [{"name": name, "description": ""} for name in listed_skills(mode, config)]
    builtin = [*BUILTIN_COMMANDS, *(["echo-skill"] if mode == "builtin-echo" else [])]
    commands += [cast(dict[str, object], {"name": name, "description": "", "builtin": True}) for name in builtin]
    if mode == "inventory-hang":
        time.sleep(30)
    if mode == "inventory-crash":
        print("claude: shim crashed", file=sys.stderr)
        return 3
    if mode == "inventory-auth":
        print("Invalid API key - Please run /login", file=sys.stderr)
        return 1
    if mode == "inventory-error":
        emit({"type": "control_response", "response": {"subtype": "error", "request_id": "skillz-inventory",
                                                      "error": "profile rejected"}})
        print("claude: profile rejected", file=sys.stderr)
        return 2
    body: dict[str, object] = {} if mode == "inventory-broken" else {"commands": commands, "agents": []}
    if mode == "inventory-bad-row":
        body["commands"] = ["not-an-object"]
    emit({"type": "control_response", "response": {"subtype": "success", "request_id": "skillz-inventory",
                                                  "response": body}})
    return 1 if mode == "inventory-nonzero" else 0


def read_tool_probe(prompt: str, mode: str) -> list[str]:
    """Answer the preflight Read-tool step. It denies a path outside the working directory, as the live preflight expects.

    Real `claude -p` behavior is unverified here: the live preflight decides.
    `read-tool-escape` returns the file content. `read-tool-skipped` never calls the Read tool.
    """
    found = re.search(r"read the file `([^`]+)` with the Read tool", prompt)
    if found is None or mode == "read-tool-skipped":
        return []
    path = found.group(1)
    allowed = _inside(path, [str(Path.cwd()), str(Path.cwd().resolve())])
    text = Path(path).read_text() if mode == "read-tool-escape" or allowed else "denied"
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "rt1", "name": "Read",
          "input": {"file_path": path}}]}})
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "rt1",
          "is_error": text == "denied", "content": text}]}})
    return [text]


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
    texts += read_tool_probe(prompt, mode)
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


def staged_modes() -> dict[str, str]:
    """Return the octal mode of every path under the project's `.claude` directory."""
    project = next((root for root in (Path.cwd(), Path.cwd().parent) if (root / ".claude").is_dir()), None)
    if project is None:
        return {}
    return {str(path.relative_to(project)): oct(path.stat().st_mode & 0o777) for path in sorted((project / ".claude").rglob("*"))}


def stream(script: Path, argv: list[str], mode: str) -> int:
    """Answer a journey: read one user message per stdin line and answer each with an init, an assistant, and a result.

    The argv goes to `<script>.log` once (with an empty prompt), and each user message to `<script>.journey.log`.
    `journey-init-changes` adds a skill to the init event of turn 2.
    `journey-exit-before-result` exits on the first message without a result. `journey-writes` writes `notes.txt`.
    `journey-culture` writes `culture.md`. `journey-culture-bad` also writes the source file `app.py` with a Write call.
    `journey-mold` declares the MCP server `docs` and the agent `reviewer`. It calls `mcp__docs__search` and the Skill `helper-skill`.
    """
    settings = Path(argv[argv.index("--settings") + 1]).read_text() if "--settings" in argv else None
    with script.with_name(script.name + ".log").open("a") as log:
        _ = log.write(json.dumps({"argv": argv, "cwd": str(Path.cwd()), "settings": settings, "prompt": "",
                                  "environment": dict(os.environ), "config_entries": config_entries(),
                                  "captured": {}, "staged": staged_modes(), "mcp_config": None}) + "\n")
    config = cast(dict[str, object], json.loads(settings or "{}"))
    markers = sorted(Path.cwd().glob(".agents/skills/*/EXPERIMENT_MARKER"))
    for turn, line in enumerate(sys.stdin, 1):
        text = cast(str, json.loads(line)["message"]["content"])
        with script.with_name(script.name + ".journey.log").open("a") as log:
            _ = log.write(json.dumps({"turn": turn, "message": text, "line": json.loads(line)}) + "\n")
        if mode == "journey-exit-before-result":
            return 1
        skills = listed_skills(mode, config) + list(BUILTIN_SKILLS)
        if mode == "journey-init-changes" and turn == 2:
            skills.append("intruder-skill")
        mold = mode == "journey-mold"
        emit({"type": "system", "subtype": "init", "skills": skills, "tools": ["Bash", "Read", "Skill"],
              "plugins": list(BUILTIN_PLUGINS),
              "mcp_servers": [{"name": "docs", "status": "connected"}] if mold else [],
              "agents": ["general-purpose", "Explore", *(["reviewer"] if mold else [])]})
        if mold and turn == 1:
            emit({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "m1", "name": "mcp__docs__search", "input": {"query": "molds"}}]}})
            emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "m1", "is_error": False,
                  "content": "result"}]}})
            emit({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "m2", "name": "Skill", "input": {"skill": "helper-skill"}}]}})
            emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "m2", "is_error": False,
                  "content": "loaded"}]}})
        if mode.startswith("journey-culture"):
            _ = Path("culture.md").write_text(f"# Culture notes\n\nturn {turn}\n")
        if mode == "journey-culture-bad" and turn == 1:
            _ = Path("app.py").write_text("print('hello')\n")
            emit({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "w1", "name": "Write", "input": {"file_path": "app.py", "content": "print('hello')\n"}}]}})
            emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "w1", "is_error": False,
                  "content": "written"}]}})
        if turn == 1 and markers:
            skill = markers[0].parent.name
            emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash",
                  "input": {"command": f"cat .agents/skills/{skill}/SKILL.md"}}]}})
            emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": False,
                  "content": markers[0].read_text()}]}})
        if mode == "journey-writes":
            _ = Path("notes.txt").write_text(f"turn {turn}\n")
        answer = f"agent-{turn}"
        emit({"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}})
        emit({"type": "result", "subtype": "success", "is_error": mode == "journey-error-result", "result": answer, "usage": USAGE})
    return 0


def main() -> int:
    script = Path(__file__).resolve()
    mode_file = script.with_name(script.name + ".mode")
    mode = mode_file.read_text().strip() if mode_file.is_file() else "ok"
    argv = sys.argv[1:]
    if "--input-format" in argv and "--model" in argv:
        return stream(script, argv, mode)
    prompt = sys.stdin.read()
    settings = Path(argv[argv.index("--settings") + 1]).read_text() if "--settings" in argv else None
    captured: dict[str, str | None] = {}
    for line in prompt.split("\nCAPTURE FILES:\n", 1)[1].splitlines() if "\nCAPTURE FILES:\n" in prompt else []:
        captured[line] = Path(line).read_bytes().hex() if Path(line).is_file() else None
    listing = "--input-format" in argv
    with script.with_name(script.name + (".inventory.log" if listing else ".log")).open("a") as log:
        _ = log.write(json.dumps({"argv": argv, "cwd": str(Path.cwd()), "settings": settings, "prompt": prompt,
                                  "environment": dict(os.environ), "config_entries": config_entries(),
                                  "captured": captured, "staged": staged_modes(),
                                  "mcp_config": Path(argv[argv.index("--mcp-config") + 1]).read_text() if "--mcp-config" in argv else None}) + "\n")
    if argv == ["--version"]:
        if mode == "version-broken":
            print("claude: broken shim", file=sys.stderr)
            return 3
        print("2.1.296 (Claude Code)")
        return 0
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
        if mode == "spoofed-plugin":
            init["plugins"] = [{"name": "user-plugin", "path": "builtin", "source": "user-plugin@elsewhere"}]
        if mode == "foreign-mcp":
            init["mcp_servers"] = [{"name": "user-server", "status": "connected"}]
        if mode == "foreign-agent":
            init["agents"] = ["general-purpose", "user-agent"]
        if mode == "surface-mcp":
            init["mcp_servers"] = [{"name": "docs", "status": "connected"}]
        if mode == "surface-agent":
            init["agents"] = ["general-purpose", "Explore", "reviewer"]
        if mode == "surface-skill":
            skills.append("helper-skill")
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
    elif "reply" in properties:
        structured = {"reply": "Weekly reports.", "done": True}
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
