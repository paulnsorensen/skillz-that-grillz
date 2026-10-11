"""Derived-table emitters for the staging pass.

The staging pass flattens each canonical entry into narrow rows, one JSONL
file per derived table. DuckDB reads those files with typed columns and never
runs json_extract over the wide raw_entries.message column.

A table is (name, columns, make_emitter). make_emitter(harness, source) runs
once per source file. It returns a function that maps one entry to zero or
more rows, so an emitter can keep per-file state. To add a table, append one
DerivedTable to DERIVED_TABLES.

JSON-typed columns hold the raw value found at a path. SQL then applies the
same json_extract_string / TRY_CAST that it applied to raw_entries.message.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import cast

Row = dict[str, object]
Entry = dict[str, object]
Emitter = Callable[[Entry], Iterable[Row]]

STOP_REASONS = ("end_turn", "stop_sequence", "max_tokens", "success", "error", "aborted")
CONTENT_CLIP = 500
# An escaped backslash (kept as is) or a \u00XX control escape (hex upper-cased).
_CONTROL_ESCAPE = re.compile(r"[\\][\\]|[\\]u00[0-9a-f]{2}")


@dataclass(frozen=True)
class DerivedTable:
    name: str
    columns: dict[str, str]
    make_emitter: Callable[[str, str], Emitter]


def _message(entry: Entry) -> dict[str, object] | None:
    message = entry.get("message")
    return cast(dict[str, object], message) if isinstance(message, dict) else None


def _blocks(entry: Entry) -> list[object]:
    message = _message(entry)
    content = message.get("content") if message else None
    return cast(list[object], content) if isinstance(content, list) else []


def _at(value: object, key: str) -> object:
    """Return value[key] when value is an object, else None (a JSON path miss)."""
    return cast(dict[str, object], value).get(key) if isinstance(value, dict) else None


def _json_text(value: object) -> str:
    """Return compact JSON text the way DuckDB prints it.

    DuckDB keeps non-ASCII characters and writes control-character escapes with
    upper-case hex digits (Python uses lower case).
    """
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return _CONTROL_ESCAPE.sub(lambda match: match.group()[:2] + match.group()[2:].upper(), text)


def _clip(value: object) -> str | None:
    """Return the first 500 characters of a string or of the compact JSON text."""
    if value is None:
        return None
    text = value if isinstance(value, str) else _json_text(value)
    return text[:CONTENT_CLIP]


def _envelope(entry: Entry, *names: str) -> Row:
    return {name: entry.get(name) for name in names}


def _tool_uses(_harness: str, _source: str) -> Emitter:
    def emit(entry: Entry) -> Iterable[Row]:
        if entry.get("type") != "assistant":
            return
        for block in _blocks(entry):
            if _at(block, "type") != "tool_use":
                continue
            tool_input = _at(block, "input")
            yield {
                "harness": entry.get("harness"),
                "tool_name": _at(block, "name"),
                "tool_use_id": _at(block, "id"),
                "input": tool_input,
                "bash_cmd": _at(tool_input, "command"),
                "skill_name": _at(tool_input, "skill"),
                "skill_args": _at(tool_input, "args"),
                "agent_type": _at(tool_input, "subagent_type"),
                "agent_desc": _at(tool_input, "description"),
                "agent_mode": _at(tool_input, "mode"),
                "grep_pattern": _at(tool_input, "pattern"),
                "file_path": _at(tool_input, "file_path"),
                "query": _at(tool_input, "query"),
                **_envelope(entry, "timestamp", "sessionId", "cwd", "gitBranch"),
            }

    return emit


def _tool_results(_harness: str, _source: str) -> Emitter:
    def emit(entry: Entry) -> Iterable[Row]:
        if entry.get("type") != "user":
            return
        for block in _blocks(entry):
            if _at(block, "type") != "tool_result":
                continue
            yield {
                "harness": entry.get("harness"),
                "tool_use_id": _at(block, "tool_use_id"),
                "content": _clip(_at(block, "content")),
                "is_error": _at(block, "is_error"),
                **_envelope(entry, "timestamp", "sessionId"),
            }

    return emit


def _stop_events(_harness: str, _source: str) -> Emitter:
    def emit(entry: Entry) -> Iterable[Row]:
        message = _message(entry)
        if entry.get("type") != "assistant" or message is None:
            return
        stop_reason = message.get("stop_reason")
        if isinstance(stop_reason, str) and stop_reason in STOP_REASONS:
            yield {
                "harness": entry.get("harness"),
                "stop_reason": stop_reason,
                **_envelope(entry, "timestamp", "sessionId", "cwd", "gitBranch"),
            }

    return emit


def _model_turns(harness: str, source: str) -> Emitter:
    # Entries without message.id keep one row each. The key is unique per source file.
    row_count = 0

    def emit(entry: Entry) -> Iterable[Row]:
        nonlocal row_count
        message = _message(entry)
        if entry.get("type") not in ("assistant", "token_usage") or message is None:
            return
        if message.get("model") is None:
            return
        row_count += 1
        usage = message.get("usage")
        tool_calls = (
            None
            if harness == "codex"
            else sum(1 for block in _blocks(entry) if _at(block, "type") == "tool_use")
        )
        yield {
            "harness": entry.get("harness"),
            "row_key": f"row:{source}:{row_count}",
            "message_id": message.get("id"),
            "model": message.get("model"),
            "stop_reason": message.get("stop_reason"),
            "error_message": message.get("error_message"),
            "input_tokens": _at(usage, "input_tokens"),
            "output_tokens": _at(usage, "output_tokens"),
            "cache_read_tokens": _at(usage, "cache_read_input_tokens"),
            "cache_creation_tokens": _at(usage, "cache_creation_input_tokens"),
            "prompt_tokens": message.get("prompt_tokens"),
            "duration_ms": message.get("duration_ms"),
            "ttft_ms": message.get("ttft_ms"),
            "tool_calls": tool_calls,
            **_envelope(entry, "timestamp", "sessionId", "cwd"),
        }

    return emit


_ENVELOPE = {"timestamp": "VARCHAR", "sessionId": "VARCHAR"}
_ENVELOPE_CWD = {**_ENVELOPE, "cwd": "VARCHAR"}
_ENVELOPE_GIT = {**_ENVELOPE_CWD, "gitBranch": "VARCHAR"}

DERIVED_TABLES = (
    DerivedTable(
        "tool_uses",
        {
            "harness": "VARCHAR",
            "tool_name": "JSON",
            "tool_use_id": "JSON",
            "input": "JSON",
            "bash_cmd": "JSON",
            "skill_name": "JSON",
            "skill_args": "JSON",
            "agent_type": "JSON",
            "agent_desc": "JSON",
            "agent_mode": "JSON",
            "grep_pattern": "JSON",
            "file_path": "JSON",
            "query": "JSON",
            **_ENVELOPE_GIT,
        },
        _tool_uses,
    ),
    DerivedTable(
        "tool_results",
        {
            "harness": "VARCHAR",
            "tool_use_id": "JSON",
            "content": "VARCHAR",
            "is_error": "JSON",
            **_ENVELOPE,
        },
        _tool_results,
    ),
    DerivedTable(
        "stop_events",
        {"harness": "VARCHAR", "stop_reason": "VARCHAR", **_ENVELOPE_GIT},
        _stop_events,
    ),
    DerivedTable(
        "model_turns",
        {
            "harness": "VARCHAR",
            "row_key": "VARCHAR",
            "message_id": "JSON",
            "model": "JSON",
            "stop_reason": "JSON",
            "error_message": "JSON",
            "input_tokens": "JSON",
            "output_tokens": "JSON",
            "cache_read_tokens": "JSON",
            "cache_creation_tokens": "JSON",
            "prompt_tokens": "JSON",
            "duration_ms": "JSON",
            "ttft_ms": "JSON",
            "tool_calls": "BIGINT",
            **_ENVELOPE_CWD,
        },
        _model_turns,
    ),
)
