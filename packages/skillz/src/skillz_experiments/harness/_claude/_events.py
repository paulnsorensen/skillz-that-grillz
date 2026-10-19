from __future__ import annotations

import json
import re
import secrets
import time
from collections.abc import Collection
from pathlib import Path
from typing import cast

from skillz_experiments._candidate import snapshot_outputs
from skillz_experiments._cases import CodedError, loads_untrusted, mapping
from skillz_experiments._evaluation import usage
from skillz_experiments.harness._claude._constants import AUTH_FAILED, PROBE_PREFIX
from skillz_experiments.harness._claude._frontmatter import read_frontmatter
from skillz_experiments.harness._claude._sandbox import sandbox_fix, SandboxUnavailable
from skillz_experiments.harness._claude._settings import Declared


# Agents that Claude Code lists without any user file. The `--tools` list leaves no way to run them.
BUILTIN_AGENTS = frozenset({"general-purpose", "Explore", "Plan", "statusline-setup", "output-style-setup",
                            "claude-code-guide", "claude"})


def control_response(events: list[dict[str, object]]) -> dict[str, object]:
    response = next((event.get("response") for event in events
                     if event.get("type") == "control_response" and isinstance(event.get("response"), dict)), None)
    return cast(dict[str, object], response) if isinstance(response, dict) else {}


def declared_commands(events: list[dict[str, object]]) -> tuple[frozenset[str], frozenset[str]] | None:
    """Read the `initialize` control response. Return the built-in names and the other names, or None without a valid list."""
    body = control_response(events).get("response")
    rows = cast(dict[str, object], body).get("commands") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return None
    builtin: set[str] = set()
    other: set[str] = set()
    for row in cast(list[object], rows):
        entry = cast(dict[str, object], row) if isinstance(row, dict) else {}
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            return None
        (builtin if entry.get("builtin") is True else other).add(name)
    return frozenset(builtin), frozenset(other)


def parse_events(stdout: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            events.append(mapping(loads_untrusted(line)))
        except ValueError:
            raise RuntimeError("invalid Claude Code JSON event stream") from None
    return events


def final_event(events: list[dict[str, object]]) -> dict[str, object] | None:
    return next((event for event in reversed(events) if event.get("type") == "result"), None)


def loaded_skills(events: list[dict[str, object]], builtin: Collection[str] = ()) -> list[str] | None:
    """Return the skill names in every init event except Claude Code's own commands, or None without an init event."""
    inits = [event for event in events if event.get("type") == "system" and event.get("subtype") == "init"]
    if not inits:
        return None
    loaded: set[str] = set()
    for event in inits:
        raw = event.get("skills")
        items = cast(list[object], raw) if isinstance(raw, list) else None
        names = [cast(dict[str, object], item).get("name") if isinstance(item, dict) else item for item in items or []]
        if items is None or not all(isinstance(name, str) for name in names):
            raise CodedError("isolation-failed", "Claude Code isolation fails: the init event skills field is not a list of names")
        loaded.update(cast(list[str], names))
    return sorted(loaded.difference(builtin))


def _entries(event: dict[str, object], field: str) -> list[str]:
    raw = event.get(field)
    names: list[str] = []
    for item in cast(list[object], raw) if isinstance(raw, list) else []:
        names.append(str(mapping(cast(object, item)).get("name", "")) if isinstance(item, dict) else str(item))
    return names


def _plugins(event: dict[str, object]) -> list[str]:
    """Name each plugin in an init event that does not ship inside Claude Code.

    A built-in plugin has the path `builtin` and a source that ends with `@builtin`.
    """
    raw = event.get("plugins")
    names: list[str] = []
    for item in cast(list[object], raw) if isinstance(raw, list) else []:
        entry: dict[str, object] = mapping(cast(object, item)) if isinstance(item, dict) else {}
        if entry.get("path") == "builtin" and str(entry.get("source", "")).endswith("@builtin"):
            continue
        names.append(str(entry.get("name", "")) if entry else str(cast(object, item)))
    return names


def find_leak(events: list[dict[str, object]], skills: list[str], builtin: Collection[str] = (),
          declared: Declared | None = None) -> str | None:
    """Name the entry in an init event that the transport does not configure, or return None.

    The transport configures only the candidate skill and what the contract surface `declared`. It loads no plugin.
    Claude Code's own commands and built-in plugins ship with the executable, so they do not count.
    """
    declared = declared or Declared()
    for event in (event for event in events if event.get("type") == "system" and event.get("subtype") == "init"):
        found = {"skill": [name for name in _entries(event, "skills")
                           if name not in skills and name not in builtin and name not in declared.skills],
                 "plugin": _plugins(event),
                 "agent": [name for name in _entries(event, "agents")
                           if name not in BUILTIN_AGENTS and name not in declared.agents],
                 "MCP server": [name for name in _entries(event, "mcp_servers") if name not in declared.servers]}
        for kind, names in found.items():
            if names:
                return f"foreign {kind} in init event: {sorted(names)}"
    return None


def failure(returncode: int, stderr: str, events: list[dict[str, object]], skills: list[str],
             builtin: Collection[str] = (), *, allowed: Collection[str] = ()) -> str | None:
    """Name the isolation failure that a probe run shows, or return None when the run is isolated.

    Raise SandboxUnavailable when the Bash sandbox cannot start.
    `allowed` names the contract surface skills that may load without being required.
    """
    final = final_event(events)
    errored = returncode != 0 or (final is not None and final.get("is_error") is True)
    detail = (stderr[:8192] + " " + (str(final.get("result", ""))[:2048] if final else "")).lower()
    if returncode != 0 and "sandbox" in detail:
        fix = sandbox_fix(detail)
        raise SandboxUnavailable(f"the Bash sandbox cannot start ({' '.join(stderr[:200].split())})", fix)
    if errored and any(word in detail for word in ("login", "api key", "authenticat", "unauthorized", "401", "oauth")):
        return AUTH_FAILED
    loaded = loaded_skills(events, [*builtin, *allowed])
    if loaded is None:
        return "no init event" if returncode == 0 else f"execution failed (exit {returncode})"
    if set(loaded) - set(skills):
        return f"foreign skill in init event: {loaded}"
    if loaded != sorted(skills):
        return f"expected skill missing from init event: {loaded}"
    if errored or final is None:
        return f"execution failed (exit {returncode})"
    return None


def not_loaded(workspace: Path, started: float, trace: list[dict[str, object]]) -> dict[str, object]:
    """Return the result of a run in which Claude Code never registered the candidate: an empty answer and no events."""
    return {"answer": {"result_json": "", "load_marker": ""}, "events": [], "usage": usage(trace), "workspace": str(workspace),
            "latency_seconds": time.monotonic() - started, "output_files": snapshot_outputs(workspace)}


def event_trace(events: list[dict[str, object]]) -> list[dict[str, object]]:
    """Turn Bash tool calls into completed command events. A tool error means exit code 1."""
    commands: dict[str, str] = {}
    trace: list[dict[str, object]] = []
    for event in events:
        message = event.get("message")
        content = cast(dict[str, object], message).get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in (cast(dict[str, object], item) for item in cast(list[object], content) if isinstance(item, dict)):
            if block.get("type") == "tool_use" and block.get("name") == "Bash" and isinstance(block.get("id"), str):
                command = mapping(block.get("input")).get("command") if isinstance(block.get("input"), dict) else None
                if isinstance(command, str):
                    commands[cast(str, block["id"])] = command
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in commands:
                trace.append({"type": "item.completed", "item": {
                    "type": "command_execution", "command": commands[cast(str, block["tool_use_id"])],
                    "exit_code": 1 if block.get("is_error") is True else 0}})
    return trace


def _blocks(content: object) -> list[dict[str, object]]:
    """Return the object blocks of a content list, or none when `content` is not a list."""
    items = cast(list[object], content) if isinstance(content, list) else []
    return [cast(dict[str, object], item) for item in items if isinstance(item, dict)]


def _result_text(content: object) -> str:
    """Join the text of a tool result. Claude Code sends it as a string or as a list of text blocks."""
    return content if isinstance(content, str) else "\n".join(
        str(block.get("text", "")) for block in _blocks(content) if block.get("type") == "text")


def tool_transcript(events: list[dict[str, object]]) -> list[dict[str, object]]:
    """List each tool use in stream order: the tool, its input, and the full text and error flag of its result.

    A tool use with no result keeps `result` and `is_error` as None.
    """
    uses: dict[str, dict[str, object]] = {}
    for event in events:
        message = event.get("message")
        for block in _blocks(cast(dict[str, object], message).get("content") if isinstance(message, dict) else None):
            if block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                uses[cast(str, block["id"])] = {"tool": block.get("name"), "input": block.get("input"),
                                                "result": None, "is_error": None}
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in uses:
                uses[cast(str, block["tool_use_id"])] |= {"result": _result_text(block.get("content")),
                                                         "is_error": block.get("is_error") is True}
    return list(uses.values())


def token_events(final: dict[str, object]) -> list[dict[str, object]]:
    """Map Claude usage to the shared counts. Input tokens include cache creation and cache reads."""
    raw = final.get("usage")
    if not isinstance(raw, dict) or not raw:
        return []
    counts = mapping(cast(object, raw))

    def count(name: str, *, optional: bool = False) -> int | None:
        if optional and name not in counts:
            return 0
        value = counts.get(name)
        return value if type(value) is int and value >= 0 else None

    input_tokens = count("input_tokens")
    created = count("cache_creation_input_tokens", optional=True)
    cached = count("cache_read_input_tokens", optional=True)
    total = input_tokens + created + cached if (input_tokens is not None and created is not None
                                                 and cached is not None) else None
    return [{"type": "turn.completed", "usage": {
        "input_tokens": total, "cached_input_tokens": cached, "output_tokens": count("output_tokens")}}]


def declares(text: str, skill: str) -> bool:
    """Check that the SKILL.md frontmatter names the contract skill, has a description, and declares no hooks."""
    try:
        parsed = read_frontmatter(text)
    except ValueError:
        return False
    fields = {key.lower(): value for key, value in parsed.items()}
    return (fields.get("name") == skill and bool(fields.get("description"))
            and "hooks" not in fields and "user-invocable" not in fields)


def _bash_control_passed(events: list[dict[str, object]], command_marker: str, control_token: str) -> bool:
    """Accept the control token only from the Bash `tool_result` that answers the `cat` of the control file."""
    uses: set[str] = set()
    for event in events:
        message = event.get("message")
        content = cast(dict[str, object], message).get("content") if isinstance(message, dict) else None
        for block in cast(list[object], content) if isinstance(content, list) else []:
            item = mapping(block)
            if item.get("type") == "tool_use" and item.get("name") == "Bash":
                command = mapping(item.get("input", {})).get("command")
                if isinstance(command, str) and command_marker in command and isinstance(item.get("id"), str):
                    uses.add(cast(str, item["id"]))
            if item.get("type") == "tool_result" and item.get("tool_use_id") in uses and control_token in json.dumps(item):
                return True
    return False


APPROVAL_BLOCK = re.compile(r"(?:requires|needs) approval", re.IGNORECASE)


def approval_blocked(events: list[dict[str, object]]) -> bool:
    """Return True when a Bash tool result says that the command needs approval, so the sandbox never ran it."""
    uses: set[str] = set()
    for event in events:
        message = event.get("message")
        content = cast(dict[str, object], message).get("content") if isinstance(message, dict) else None
        for block in cast(list[object], content) if isinstance(content, list) else []:
            item = mapping(block)
            if item.get("type") == "tool_use" and item.get("name") == "Bash" and isinstance(item.get("id"), str):
                uses.add(cast(str, item["id"]))
            elif item.get("type") == "tool_result" and item.get("tool_use_id") in uses and APPROVAL_BLOCK.search(json.dumps(item)):
                return True
    return False


APPROVAL_MESSAGE = ("the preflight Bash commands hit an approval block: Claude Code asked for approval of a Bash command, "
                    "so the sandbox probe did not run")


def read_failure(events: list[dict[str, object]], sealed_token: str, sealed: str, control_token: str,
                  control_file: str) -> str | None:
    """Judge the read probe. It needs evidence that Bash ran: a command on the sealed path, and the workspace token."""
    text = json.dumps(events)
    if sealed_token in text:
        return "read isolation failed: a host file is readable"
    if approval_blocked(events):
        return f"read probe has no evidence: {APPROVAL_MESSAGE}"
    commands = [cast(str, mapping(item["item"])["command"]) for item in event_trace(events)]
    if not any(sealed in command for command in commands):
        return "read probe has no evidence: no Bash command read the sealed host file"
    if not _bash_control_passed(events, control_file, control_token):
        return "read probe has no positive control: Bash cannot read a workspace file"
    return None


def read_tool_failure(events: list[dict[str, object]], sealed_token: str, sealed: Path, *, evidence: bool) -> str | None:
    """Judge the Read-tool probe. The Read tool must try the sealed host file and fail to return its token.

    Without `evidence`, only a returned token fails. With it, a missing Read call also fails.

    Claude Code's `claude -p` has no flag that confines the Read tool to the workspace, so only this probe proves it.
    """
    uses: set[str] = set()
    answered = False
    for event in events:
        message = event.get("message")
        content = cast(dict[str, object], message).get("content") if isinstance(message, dict) else None
        for block in cast(list[object], content) if isinstance(content, list) else []:
            item = mapping(block)
            if item.get("type") == "tool_use" and item.get("name") == "Read" and isinstance(item.get("id"), str):
                target = mapping(item.get("input", {})).get("file_path")
                if isinstance(target, str) and Path(target).resolve() == sealed.resolve():
                    uses.add(cast(str, item["id"]))
            if item.get("type") == "tool_result" and item.get("tool_use_id") in uses:
                if sealed_token in json.dumps(item):
                    return "read isolation failed: the Read tool reached a path outside the workspace"
                answered = True
    if evidence and not answered:
        return "read-tool probe has no evidence: no Read tool call answered for the sealed host file"
    return None


def write_failure(events: list[dict[str, object]], files: list[str], wrote: bool, controlled: bool) -> str | None:
    """Judge the write probe. It needs a failed Bash write to each of `files` (in `.agents` and `.claude`),
    and a workspace write that succeeds.
    """
    if wrote:
        return "write isolation failed: the task model can write to .agents or .claude"
    if approval_blocked(events):
        return f"write probe has no evidence: {APPROVAL_MESSAGE}"
    attempts = [mapping(item["item"]) for item in event_trace(events)]
    for file in files:
        if not any(cast(str, item["command"]).startswith("printf") and file in cast(str, item["command"])
                   and item["exit_code"] == 1 for item in attempts):
            return f"write probe has no evidence: no failed Bash printf to {'.agents' if '/.agents/' in file else '.claude'}"
    if not controlled:
        return "write probe has no positive control: Bash cannot write a workspace file"
    return None


def answer(final: dict[str, object]) -> dict[str, object]:
    structured = final.get("structured_output")
    if isinstance(structured, dict):
        return mapping(cast(object, structured))
    text = final.get("result")
    try:
        return mapping(loads_untrusted(text)) if isinstance(text, str) else {}
    except ValueError:
        return {}


def user_line(text: str) -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}})


def is_result(line: str) -> bool:
    try:
        return mapping(loads_untrusted(line)).get("type") == "result"
    except ValueError:
        return False


def init_inventory(event: dict[str, object]) -> tuple[list[str], ...]:
    return (sorted(_entries(event, "skills")), sorted(_entries(event, "tools")), sorted(_entries(event, "agents")),
            sorted(_entries(event, "mcp_servers")), sorted(_plugins(event)))


def assistant_text(events: list[dict[str, object]]) -> str:
    texts: list[str] = []
    for event in events:
        message = event.get("message")
        content = cast(dict[str, object], message).get("content") if event.get("type") == "assistant" and isinstance(message, dict) else None
        for block in cast(list[object], content) if isinstance(content, list) else []:
            if isinstance(block, dict) and cast(dict[str, object], block).get("type") == "text":
                text = cast(dict[str, object], block).get("text")
                if isinstance(text, str):
                    texts.append(text)
    final = final_event(events)
    return "\n".join(texts) or (str(final.get("result", "")) if final else "")


def marker_seen(events: list[dict[str, object]], identity: str) -> bool:
    """Return True when a tool result shows the candidate marker, so the agent read EXPERIMENT_MARKER."""
    return any(event.get("type") == "user" and identity in json.dumps(event) for event in events)


def probe_name() -> str:
    return f"{PROBE_PREFIX}{secrets.token_hex(8)}"


def probe_skill(workspace: Path, name: str) -> None:
    skill = workspace / ".agents/skills" / name
    skill.mkdir()
    _ = (skill / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Inspect public fixtures\n---\nInspection probe.\n")
