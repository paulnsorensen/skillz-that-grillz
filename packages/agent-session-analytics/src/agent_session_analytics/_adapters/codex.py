"""The Codex adapter: rollout JSONL to the canonical envelope."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from typing import cast

from agent_session_analytics._adapters._jsonl import JsonValue, iter_jsonl
from agent_session_analytics._db import configured_path


def codex_discover() -> list[str]:
    root = os.path.join(configured_path("CODEX_HOME", "~/.codex"), "sessions")
    if not os.path.isdir(root):
        return []
    out: list[str] = []
    for dirpath, _dirs, files in os.walk(root):
        out.extend(os.path.join(dirpath, f) for f in files if f.endswith(".jsonl"))
    return out


def _codex_payload_is_error(payload: JsonValue) -> bool:
    if not isinstance(payload, dict):
        return False
    status = payload.get("status")
    if isinstance(status, str) and status.casefold() in ("error", "failed", "failure"):
        return True
    for key in ("isError", "is_error"):
        value = payload.get(key)
        if value is True or (isinstance(value, str) and value.casefold() == "true"):
            return True
    code = payload.get("exit_code")
    return isinstance(code, int) and not isinstance(code, bool) and code != 0


def _codex_content_block_payloads(blocks: list[JsonValue]) -> Iterator[dict[str, JsonValue]]:
    if not blocks:
        return
    texts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "input_text":
            continue
        block_text = block.get("text")
        if not isinstance(block_text, str):
            continue
        texts.append(block_text)
    if not texts:
        return
    header = texts[0]
    if not re.fullmatch(
        r"Script completed\nWall time(?:\s*:\s*|\s+)[^\n]*\nOutput:\n",
        header,
    ):
        return
    for block_text in texts[1:]:
        try:
            payload = cast(JsonValue, json.loads(block_text.strip()))
        except ValueError:
            continue
        if isinstance(payload, dict):
            yield payload


def _codex_structured_payloads(out: JsonValue) -> Iterator[dict[str, JsonValue]]:
    if isinstance(out, dict):
        yield out
        return
    if isinstance(out, list):
        yield from _codex_content_block_payloads(out)
        return
    if not isinstance(out, str):
        return
    text = out.strip()
    try:
        parsed = cast(JsonValue, json.loads(text))
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        yield parsed
        return
    if isinstance(parsed, list):
        yield from _codex_content_block_payloads(parsed)
        return
    marker = re.search(
        r"^Script completed\n(?:Wall time(?:\s*:\s*|\s+)[^\n]*\n)?Output:\s*(.+)\s*\Z",
        out,
        re.DOTALL,
    )
    if not marker:
        return
    decoder = json.JSONDecoder()
    text = marker.group(1)
    position = 0
    while position < len(text):
        while position < len(text) and text[position].isspace():
            position += 1
        if position == len(text):
            return
        try:
            parsed, end = cast(tuple[JsonValue, int], decoder.raw_decode(text, position))
        except ValueError:
            return
        if isinstance(parsed, dict):
            yield parsed
        elif isinstance(parsed, list):
            yield from (item for item in parsed if isinstance(item, dict))
        position = end


def _codex_output_is_error(out: JsonValue) -> str:
    """Detect structured or legacy failure signals in a Codex tool output."""
    if any(
        _codex_payload_is_error(payload) for payload in _codex_structured_payloads(out)
    ):
        return "true"
    if isinstance(out, str):
        match = re.search(r"Process exited with code (-?\d+)\b", out)
        if match and any(int(digit) != 0 for digit in match.group(1).lstrip("-")):
            return "true"
    return "false"


def _codex_command(name: JsonValue, parsed: JsonValue, raw_input: JsonValue) -> str | None:
    """The executed command string for codex shell-ish tools, or None.

    Mirrors claude's ``input.command`` so ``tool_uses.bash_cmd`` populates:
    ``exec_command`` carries it in ``cmd``; legacy ``shell`` carries an argv
    array (the ``-lc``/``-c`` payload is the real command); the ``exec``
    custom tool's argument IS the raw code string.
    """
    if name == "exec_command" and isinstance(parsed, dict):
        cmd = parsed.get("cmd")
        return cmd if isinstance(cmd, str) else None
    if name == "shell" and isinstance(parsed, dict):
        cmd = parsed.get("command")
        if isinstance(cmd, str):
            return cmd
        if isinstance(cmd, list) and cmd:
            if len(cmd) == 3 and cmd[1] in ("-lc", "-c"):
                return str(cmd[2])
            return " ".join(str(c) for c in cmd)
        return None
    if name == "exec" and isinstance(raw_input, str):
        return raw_input
    return None


def _codex_token_usage(
    payload: dict[str, JsonValue], last_total: JsonValue
) -> tuple[dict[str, JsonValue] | None, JsonValue]:
    """Map a token_count payload to (Claude-style usage, total_token_usage).

    Returns usage None when last_token_usage is null or when total_token_usage
    repeats the previous event (Codex re-emits it). The returned total is the
    latest total seen on any token_count event, with or without usage.
    """
    info = payload.get("info")
    if not isinstance(info, dict):
        return None, last_total
    total = info.get("total_token_usage")
    new_total = last_total if total is None else total
    last = info.get("last_token_usage")
    if not isinstance(last, dict):
        return None, new_total
    if total is not None and total == last_total:
        return None, new_total
    usage: dict[str, JsonValue] = {
        "input_tokens": last.get("input_tokens"),
        "output_tokens": last.get("output_tokens"),
        "cache_read_input_tokens": last.get("cached_input_tokens"),
    }
    return usage, new_total


def _codex_parent_session(payload: dict[str, JsonValue]) -> str | None:
    """Parent thread id of a sub-agent rollout, or None.

    A sub-agent session_meta carries ``source.subagent.thread_spawn``; other
    sessions carry a plain string or no ``source``.
    """
    source = payload.get("source")
    subagent = source.get("subagent") if isinstance(source, dict) else None
    spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
    parent = spawn.get("parent_thread_id") if isinstance(spawn, dict) else None
    return parent if isinstance(parent, str) and parent else None


def _codex_is_subagent(payload: dict[str, JsonValue]) -> bool:
    """Return True when session_meta marks an agent-run rollout.

    Any ``source.subagent`` value counts: ``thread_spawn`` children, and
    ``other`` (for example ``guardian``) or ``review`` runs that name no parent.
    """
    source = payload.get("source")
    return isinstance(source, dict) and "subagent" in source


def _codex_event(payload: dict[str, JsonValue]) -> dict[str, JsonValue] | None:
    """Map one non-token ``event_msg`` payload to canonical fields, or None to drop it.

    An ``item_completed`` ``UserMessage`` item becomes a user entry with its
    text blocks. ``task_complete`` becomes an assistant entry with subtype
    ``task_complete`` and ``last_agent_message`` as its one text block (no block
    when the turn ended without one). ``turn_aborted`` becomes a system entry
    with subtype ``turn_aborted`` and the abort reason in ``stopReason``.
    """
    ptype = payload.get("type")
    if ptype == "item_completed":
        item = payload.get("item")
        if not isinstance(item, dict) or item.get("type") != "UserMessage":
            return None
        content = item.get("content")
        blocks: list[JsonValue] = [
            {"type": "text", "text": block["text"]}
            for block in (content if isinstance(content, list) else [])
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        ]
        return {"type": "user", "message": {"content": blocks}} if blocks else None
    if ptype == "task_complete":
        last = payload.get("last_agent_message")
        blocks = [{"type": "text", "text": last}] if isinstance(last, str) else []
        return {"type": "assistant", "subtype": "task_complete", "message": {"content": blocks}}
    if ptype == "turn_aborted":
        reason = payload.get("reason")
        return {
            "type": "system",
            "subtype": "turn_aborted",
            "stopReason": reason if isinstance(reason, str) else None,
        }
    return None


def codex_normalize(path: str) -> Iterator[dict[str, object]]:
    """Codex rollout JSONL -> canonical envelope.

    session_meta carries id + cwd; a response_item/function_call becomes an
    assistant tool_use block; a function_call_output OR custom_tool_call_output
    becomes a user tool_result block (dropping the custom outputs was the ~50%
    result-join gap — issue #704). Codex tool names (shell / apply_patch /
    custom tools) are kept verbatim, and ``input.command`` is normalized to the
    executed command string for shell-ish tools so ``bash_cmd`` extracts.
    ``tool_search_output`` items have no matching call item and are dropped.
    A forked rollout holds two ``session_meta`` records (own id first, parent
    second); the first id, ``cwd``, and ``cli_version`` describe the file. A
    token row before any id falls back to the file path, so ids stay unique.
    ``session_meta.cli_version`` stamps ``version`` on every entry. A
    sub-agent rollout names its parent in the first ``session_meta``; that id
    stamps ``parentSessionId`` on every entry. Any ``source.subagent`` value
    stamps ``isSidechain`` true on every entry, as Cursor does for sub-agent
    files, so agent-run rollouts without a parent id stay identifiable. Each
    non-duplicate
    ``token_count`` event with a ``last_token_usage`` becomes one
    ``token_usage`` entry (unique ``message.id``, empty content). Only
    model_turns reads it, as one row; no other table sees it. User prompts,
    ``task_complete``, and ``turn_aborted`` events map through _codex_event;
    ``response_item`` user messages (preambles and injected context) stay out.
    """
    session_id = None
    parent_session_id = None
    is_subagent = False
    cwd = None
    cli_version = None
    model = None
    last_total = None
    token_turns = 0
    for entry in iter_jsonl(path):
        if not isinstance(entry, dict):
            continue
        ts = entry.get("timestamp")
        payload = entry.get("payload")
        etype = entry.get("type")
        if etype == "session_meta" and isinstance(payload, dict):
            if session_id is None:
                parent_session_id = _codex_parent_session(payload)
                is_subagent = _codex_is_subagent(payload)
            session_id = session_id or payload.get("id")
            cwd = cwd or payload.get("cwd")
            cli_version = cli_version or payload.get("cli_version") or None
            continue
        if etype == "turn_context" and isinstance(payload, dict):
            cwd = payload.get("cwd") or cwd
            model = payload.get("model") or model
            continue
        if (
            etype == "event_msg"
            and isinstance(payload, dict)
            and payload.get("type") == "token_count"
        ):
            usage, last_total = _codex_token_usage(payload, last_total)
            if usage is None:
                continue
            token_turns += 1
            yield {
                "harness": "codex",
                "type": "token_usage",
                "timestamp": ts,
                "sessionId": session_id,
                "parentSessionId": parent_session_id,
                "isSidechain": is_subagent,
                "version": cli_version,
                "cwd": cwd,
                "message": {
                    "id": f"codex-tokens:{session_id or path}:{token_turns}",
                    "model": model or "unknown",
                    "content": [],
                    "usage": usage,
                },
            }
            continue
        if etype == "event_msg" and isinstance(payload, dict):
            event = _codex_event(payload)
            if event is not None:
                yield {
                    "harness": "codex",
                    "timestamp": ts,
                    "sessionId": session_id,
                    "parentSessionId": parent_session_id,
                    "isSidechain": is_subagent,
                    "version": cli_version,
                    "cwd": cwd,
                    **event,
                }
            continue
        if etype != "response_item" or not isinstance(payload, dict):
            continue
        ptype = payload.get("type")
        if ptype in ("function_call", "custom_tool_call"):
            raw_input = payload.get("arguments") or payload.get("input")
            try:
                parsed: JsonValue = (
                    cast(JsonValue, json.loads(raw_input)) if isinstance(raw_input, str) else raw_input
                )
            except (json.JSONDecodeError, TypeError):
                parsed = {"raw": raw_input}
            if not isinstance(parsed, dict):
                parsed = {"raw": parsed}
            name = payload.get("name")
            command = _codex_command(name, parsed, raw_input)
            if command is not None:
                parsed["command"] = command
            yield {
                "harness": "codex",
                "type": "assistant",
                "timestamp": ts,
                "sessionId": session_id,
                "parentSessionId": parent_session_id,
                "isSidechain": is_subagent,
                "version": cli_version,
                "cwd": cwd,
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": payload.get("call_id"),
                            "name": name,
                            "input": parsed,
                        }
                    ]
                },
            }
        elif ptype in ("function_call_output", "custom_tool_call_output"):
            out = payload.get("output")
            is_error = _codex_output_is_error(out)
            if isinstance(out, dict):
                out = json.dumps(out)
            yield {
                "harness": "codex",
                "type": "user",
                "timestamp": ts,
                "sessionId": session_id,
                "parentSessionId": parent_session_id,
                "isSidechain": is_subagent,
                "version": cli_version,
                "cwd": cwd,
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": payload.get("call_id"),
                            "content": out,
                            "is_error": is_error,
                        }
                    ]
                },
            }
