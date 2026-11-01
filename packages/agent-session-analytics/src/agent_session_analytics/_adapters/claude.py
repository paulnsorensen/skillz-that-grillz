"""The Claude adapter: its logs already use the canonical envelope."""

from __future__ import annotations

import os
from collections.abc import Iterator

from agent_session_analytics._adapters._jsonl import JsonValue, iter_jsonl
from agent_session_analytics._db import configured_path


def claude_discover() -> list[str]:
    root = os.path.join(configured_path("CLAUDE_CONFIG_DIR", "~/.claude"), "projects")
    if not os.path.isdir(root):
        return []
    out: list[str] = []
    for dirpath, _dirs, files in os.walk(root):
        out.extend(os.path.join(dirpath, f) for f in files if f.endswith(".jsonl"))
    return out


def claude_normalize(path: str) -> Iterator[dict[str, JsonValue]]:
    """Claude logs are already in the canonical envelope; just tag them."""
    for entry in iter_jsonl(path):
        if not isinstance(entry, dict):
            continue
        entry["harness"] = "claude"
        yield entry
