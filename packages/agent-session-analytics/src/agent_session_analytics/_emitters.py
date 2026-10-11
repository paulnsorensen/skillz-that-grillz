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
import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import cast

Row = dict[str, object]
Entry = dict[str, object]
Emitter = Callable[[Entry], Iterable[Row]]

STOP_REASONS = ("end_turn", "stop_sequence", "max_tokens", "success", "error", "aborted")
CONTENT_CLIP = 500
# A Claude user text with this prefix marks an interrupt, not a prompt.
INTERRUPT_PREFIX = "[Request interrupted"
# The harnesses whose user prompts reach user_turns.
PROMPT_HARNESSES = ("claude", "codex", "omp", "pi")
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


def _texts(entry: Entry) -> list[str]:
    """Return the text of each `text` content block, in order."""
    texts: list[str] = []
    for block in _blocks(entry):
        text = _at(block, "text")
        if _at(block, "type") == "text" and isinstance(text, str):
            texts.append(text)
    return texts


def _joined(texts: list[str]) -> str | None:
    return "\n".join(texts) if texts else None


def _nonblank(text: str | None) -> str | None:
    """Return text, or None when it is absent, empty, or only whitespace."""
    return text if text and not text.isspace() else None


def _at(value: object, key: str) -> object:
    """Return value[key] when value is an object, else None (a JSON path miss)."""
    return cast(dict[str, object], value).get(key) if isinstance(value, dict) else None


def _float_text(value: float) -> str:
    """Return a float the way DuckDB prints it in JSON text.

    DuckDB writes the shortest round-trip digits. It uses plain notation when
    the decimal point falls 6 places before to 21 places after the first digit,
    with `.0` after a whole number. Other values use `<digits>e<exponent>`.
    """
    if not math.isfinite(value):
        return json.dumps(value)
    sign = "-" if math.copysign(1.0, value) < 0 else ""
    if value == 0:
        return f"{sign}0.0"
    parts = Decimal(repr(abs(value))).normalize().as_tuple()
    digits = "".join(map(str, parts.digits))
    point = len(digits) + cast(int, parts.exponent)
    if -6 < point <= 21:
        if point <= 0:
            return f"{sign}0.{'0' * -point}{digits}"
        if len(digits) <= point:
            return f"{sign}{digits}{'0' * (point - len(digits))}.0"
        return f"{sign}{digits[:point]}.{digits[point:]}"
    mantissa = digits[0] + (f".{digits[1:]}" if len(digits) > 1 else "")
    return f"{sign}{mantissa}e{point - 1}"


def _compact_json(value: object) -> str:
    if isinstance(value, float):
        return _float_text(value)
    if isinstance(value, list):
        return "[" + ",".join(_compact_json(item) for item in cast(list[object], value)) + "]"
    if isinstance(value, dict):
        items = cast(dict[str, object], value).items()
        return "{" + ",".join(f"{_compact_json(key)}:{_compact_json(item)}" for key, item in items) + "}"
    return json.dumps(value, ensure_ascii=False)


def _json_text(value: object) -> str:
    """Return compact JSON text the way DuckDB prints it.

    DuckDB keeps non-ASCII characters, writes control-character escapes with
    upper-case hex digits (Python uses lower case), and prints floats in its
    own notation (see _float_text).
    """
    text = _compact_json(value)
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
            has_input = "input" in cast(dict[str, object], block)
            yield {
                "harness": entry.get("harness"),
                "tool_name": _at(block, "name"),
                "tool_use_id": _at(block, "id"),
                # A one-item list keeps an explicit JSON null apart from a missing key.
                "input": [tool_input] if has_input else None,
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


def _claude_prompt(entry: Entry) -> str | None:
    """Return the text of a real Claude prompt, or None for any other user entry.

    A real prompt is not meta, a compact summary, or a sidechain entry. It has
    string content or at least one text block, so a tool-result-only entry is
    not a prompt. Its text is not blank and does not start with `<` (an
    injected tag) or with the interrupt marker.
    """
    if any(entry.get(flag) is True for flag in ("isMeta", "isCompactSummary", "isSidechain")):
        return None
    message = _message(entry)
    content = message.get("content") if message else None
    text = _nonblank(content if isinstance(content, str) else _joined(_texts(entry)))
    if text is None or text.startswith(("<", INTERRUPT_PREFIX)):
        return None
    return text


def _pi_prompt(entry: Entry) -> str | None:
    """Return the text of a real omp or pi prompt, or None for any other user entry.

    The agent writes rows with `attribution` 'agent', so they are not prompts.
    A prompt has string content or at least one text block, and its text is not blank.
    """
    message = _message(entry)
    if not message or message.get("attribution") == "agent":
        return None
    content = message.get("content")
    return _nonblank(content if isinstance(content, str) else _joined(_texts(entry)))


def _is_codex_subagent(harness: str, entry: Entry) -> bool:
    """Return True for an entry of an agent-run Codex rollout: an agent writes its prompts."""
    return harness == "codex" and entry.get("isSidechain") is True


def _user_turns(harness: str, source: str) -> Emitter:
    """One row per real user prompt, with the last assistant text before it.

    `prev_assistant_text` follows the file order inside one session. It starts
    as NULL in each source file and when the session id changes. Claude and
    Pi-family rows take the joined text blocks of the last assistant entry with
    non-blank text; Claude skips sidechain entries. Codex takes the last
    `task_complete` entry, so a turn that ended without `last_agent_message`,
    or a `turn_aborted` turn, resets it to NULL. Codex sub-agent rollouts give
    no rows. `source` and `ordinal` keep the file order for the turn_index
    tie-break.
    """
    prev_text: str | None = None
    session: object = None
    ordinal = 0

    def track(entry: Entry) -> None:
        nonlocal prev_text
        if harness == "codex":
            if entry.get("subtype") in ("task_complete", "turn_aborted"):
                prev_text = _nonblank(_joined(_texts(entry)))
            return
        if entry.get("type") != "assistant" or (harness == "claude" and entry.get("isSidechain") is True):
            return
        text = _nonblank(_joined(_texts(entry)))
        if text is not None:
            prev_text = text

    def emit(entry: Entry) -> Iterable[Row]:
        nonlocal ordinal, prev_text, session
        if harness not in PROMPT_HARNESSES or _is_codex_subagent(harness, entry):
            return
        session_id = entry.get("sessionId")
        if session_id is not None and session_id != session:
            session, prev_text = session_id, None
        if entry.get("type") != "user":
            track(entry)
            return
        if harness == "claude":
            text = _claude_prompt(entry)
        elif harness == "codex":
            text = _nonblank(_joined(_texts(entry)))
        else:
            text = _pi_prompt(entry)
        if text is None:
            return
        ordinal += 1
        yield {
            "harness": entry.get("harness"),
            **_envelope(entry, "sessionId", "timestamp"),
            "source": source,
            "ordinal": ordinal,
            "text": text,
            "prev_assistant_text": prev_text,
        }

    return emit


def _is_claude_interrupt(entry: Entry) -> bool:
    """Return True for a main-session Claude user entry whose text starts with the marker.

    The text is the string content, or else the first text block.
    """
    if entry.get("type") != "user" or entry.get("isSidechain") is True:
        return False
    message = _message(entry)
    content = message.get("content") if message else None
    if not isinstance(content, str):
        first = next((block for block in _blocks(entry) if _at(block, "type") == "text"), None)
        content = _at(first, "text")
    return isinstance(content, str) and content.startswith(INTERRUPT_PREFIX)


def _is_codex_interrupt(entry: Entry) -> bool:
    """Return True for a top-level Codex `turn_aborted` entry with reason 'interrupted'."""
    return (
        entry.get("type") == "system"
        and entry.get("subtype") == "turn_aborted"
        and entry.get("stopReason") == "interrupted"
        and not _is_codex_subagent("codex", entry)
    )


def _interrupt_events(harness: str, _source: str) -> Emitter:
    """One row per user interrupt from a structured marker (Claude and Codex only)."""
    detect = {"claude": _is_claude_interrupt, "codex": _is_codex_interrupt}.get(harness)

    def emit(entry: Entry) -> Iterable[Row]:
        if detect is not None and detect(entry):
            yield {"harness": entry.get("harness"), **_envelope(entry, "sessionId", "timestamp")}

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
    DerivedTable(
        "user_turns",
        {
            "harness": "VARCHAR",
            "sessionId": "VARCHAR",
            "timestamp": "VARCHAR",
            "source": "VARCHAR",
            "ordinal": "BIGINT",
            "text": "VARCHAR",
            "prev_assistant_text": "VARCHAR",
        },
        _user_turns,
    ),
    DerivedTable(
        "interrupt_events",
        {"harness": "VARCHAR", "sessionId": "VARCHAR", "timestamp": "VARCHAR"},
        _interrupt_events,
    ),
)
