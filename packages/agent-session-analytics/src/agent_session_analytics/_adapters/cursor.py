"""The Cursor adapter: agent-transcript JSONL to the canonical envelope."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from agent_session_analytics._adapters._jsonl import JsonValue, iter_jsonl
from agent_session_analytics._db import configured_path


def cursor_discover() -> list[str]:
    root = os.path.join(configured_path("CURSOR_HOME", "~/.cursor"), "projects")
    if not os.path.isdir(root):
        return []
    out: list[str] = []
    for dirpath, _dirs, files in os.walk(root):
        if "agent-transcripts" not in dirpath.split(os.sep):
            continue
        out.extend(os.path.join(dirpath, f) for f in files if f.endswith(".jsonl"))
    return out


_CURSOR_TIMESTAMP_RE = re.compile(
    r"<timestamp>\w+, (\w+ \d+, \d+, \d+:\d+ [AP]M) \(UTC([+-]\d+)\)</timestamp>"
)


def _cursor_parse_timestamp(text: str) -> str | None:
    """Parse a <timestamp>Weekday, Mon D, YYYY, H:MM AM (UTC±N)</timestamp> tag to ISO-8601 UTC."""
    m = _CURSOR_TIMESTAMP_RE.search(text)
    if not m:
        return None
    dt_str, offset = m.groups()
    try:
        # The UTC offset is applied manually below, so parse a wall-clock time
        # and tag it UTC; a %z-aware parse would double-count the offset.
        dt = datetime.strptime(dt_str, "%b %d, %Y, %I:%M %p").replace(tzinfo=UTC)
    except ValueError:
        return None
    dt -= timedelta(hours=int(offset))
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _cursor_session_id(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def _cursor_resolve_slug(slug: str, fs_root: str = "/") -> str:
    """Decode a dash-encoded absolute path, preferring the longest existing dir.

    Cursor project slugs replace path separators with dashes
    (``Users-paul-Dev-easy-cheese``). A directory whose own name contains a
    dash is ambiguous if every dash is treated as a separator. Walk
    left-to-right and at each step take the longest prefix of remaining
    segments that exists under ``fs_root``; fall back to a naive split for
    any unresolved tail.
    """
    parts = [p for p in slug.split("-") if p]
    if not parts:
        return "/"
    prefix = fs_root.rstrip("/")
    i = 0
    while i < len(parts):
        matched = None
        j = i
        for j in range(len(parts), i, -1):
            candidate = "-".join(parts[i:j])
            trial = f"{prefix}/{candidate}" if prefix else f"/{candidate}"
            if os.path.isdir(trial):
                matched = trial
                break
        if matched is None:
            rest = "/".join(parts[i:])
            return f"{prefix}/{rest}" if prefix else f"/{rest}"
        prefix = matched
        i = j
    return prefix or "/"


def _cursor_project_cwd(path: str) -> str:
    """Decode the project-slug directory into a cwd via filesystem resolve."""
    root = os.path.join(configured_path("CURSOR_HOME", "~/.cursor"), "projects")
    rel = os.path.relpath(path, root)
    slug = rel.split(os.sep, 1)[0]
    return _cursor_resolve_slug(slug)


def _cursor_lineage(path: str) -> tuple[bool, str | None]:
    """Return (is_sidechain, parent_uuid) from an agent-transcripts path."""
    parts = os.path.normpath(path).split(os.sep)
    try:
        i = parts.index("agent-transcripts")
    except ValueError:
        return False, None
    if i + 3 < len(parts) and parts[i + 2] == "subagents":
        return True, parts[i + 1]
    return False, None


def _cursor_remap_mcp(name: JsonValue, tool_input: dict[str, JsonValue]) -> JsonValue:
    if name != "CallMcpTool":
        return name
    server = tool_input.get("server")
    tool_name = tool_input.get("toolName")
    if isinstance(server, str) and server and isinstance(tool_name, str) and tool_name:
        return f"mcp__{server}__{tool_name}"
    return name


def _cursor_stop_reason(entry: dict[str, JsonValue]) -> JsonValue:
    status = entry.get("status")
    err = entry.get("error")
    if status == "error" and isinstance(err, str) and "abort" in err.lower():
        return "aborted"
    return status


def cursor_normalize(path: str) -> Iterator[dict[str, object]]:
    """Cursor agent-transcript JSONL -> canonical envelope.

    One file per transcript (session id = the uuid filename); subagent
    transcripts live in a sibling ``subagents/`` dir with their own uuid/file.
    ``cwd`` is decoded from the project-slug directory name (longest existing
    path wins). Subagent files set ``isSidechain`` + ``parentUuid``. There are
    no per-call timestamps or tool_result blocks: the most recent <timestamp>
    seen in a user turn is carried forward onto later tool_use rows, and
    tool_use ids are synthesized (``session:line:block``) since results can
    never be joined. ``CallMcpTool`` calls with both ``server`` and
    ``toolName`` are remapped to ``mcp__<server>__<toolName>`` so they land
    in ``mcp_calls``; incomplete wrappers keep the native name.
    """
    session_id = _cursor_session_id(path)
    cwd = _cursor_project_cwd(path)
    is_sidechain, parent_uuid = _cursor_lineage(path)
    timestamp = None
    for line_no, entry in enumerate(iter_jsonl(path)):
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "turn_ended":
            yield {
                "harness": "cursor",
                "type": "assistant",
                "timestamp": timestamp,
                "sessionId": session_id,
                "cwd": cwd,
                "isSidechain": is_sidechain,
                "parentUuid": parent_uuid,
                "message": {"stop_reason": _cursor_stop_reason(entry)},
            }
            continue
        role = entry.get("role")
        msg = entry.get("message")
        if not isinstance(msg, dict):
            continue
        blocks = msg.get("content")
        if not isinstance(blocks, list):
            continue
        if role == "user":
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    block_text = block.get("text") or ""
                    if not isinstance(block_text, str):
                        continue
                    ts = _cursor_parse_timestamp(block_text)
                    if ts:
                        timestamp = ts
            continue
        if role != "assistant":
            continue
        out_blocks: list[dict[str, JsonValue]] = []
        for block_idx, block in enumerate(blocks):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            tool_input = block.get("input")
            if not isinstance(tool_input, dict):
                tool_input = {}
            name = _cursor_remap_mcp(name, tool_input)
            out_blocks.append(
                {
                    "type": "tool_use",
                    "id": f"{session_id}:{line_no}:{block_idx}",
                    "name": name,
                    "input": tool_input,
                }
            )
        if out_blocks:
            yield {
                "harness": "cursor",
                "type": "assistant",
                "timestamp": timestamp,
                "sessionId": session_id,
                "cwd": cwd,
                "isSidechain": is_sidechain,
                "parentUuid": parent_uuid,
                "message": {"content": out_blocks},
            }
