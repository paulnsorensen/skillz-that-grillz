"""The omp and pi adapters: two harnesses that share one session log format."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import cast

from agent_session_analytics._adapters._jsonl import JsonValue, iter_jsonl


def _pi_family_discover(config_dir: str) -> list[str]:
    root = os.path.expanduser(config_dir)
    if not os.path.isdir(root):
        return []
    out: list[str] = []
    for dirpath, _dirs, files in os.walk(root):
        out.extend(os.path.join(dirpath, f) for f in files if f.endswith(".jsonl"))
    return out


_PI_FAMILY_STOP_REASONS = {
    "stop": "end_turn",
    "toolUse": "tool_use",
    "length": "max_tokens",
}


def _pi_family_turn_meta(msg: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Map Pi-family turn metadata to canonical Claude field names."""
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    snapshot = msg.get("contextSnapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}
    stop = msg.get("stopReason")
    if isinstance(stop, str):
        stop = _PI_FAMILY_STOP_REASONS.get(stop, stop)
    elif isinstance(stop, (list, dict)):
        stop = None
    model = msg.get("model")
    if model and msg.get("provider"):
        model = f"{msg['provider']}/{model}"
    meta: dict[str, JsonValue] = {
        "model": model,
        "stop_reason": stop,
        "error_message": msg.get("errorMessage"),
        "usage": {
            "input_tokens": usage.get("input"),
            "output_tokens": usage.get("output"),
            "cache_read_input_tokens": usage.get("cacheRead"),
            "cache_creation_input_tokens": usage.get("cacheWrite"),
        },
        "duration_ms": msg.get("duration"),
        "ttft_ms": msg.get("ttft"),
        "prompt_tokens": snapshot.get("promptTokens"),
    }
    return {key: value for key, value in meta.items() if value is not None}


def omp_discover() -> list[str]:
    return _pi_family_discover("~/.omp/agent/sessions")


def pi_discover() -> list[str]:
    return _pi_family_discover("~/.pi/agent/sessions")


def _pi_family_normalize(path: str, harness: str) -> Iterator[dict[str, object]]:
    """Pi-family session JSONL -> canonical envelope.

    The ``session`` header entry carries id + cwd, threaded onto every row.
    ``message`` entries: assistant ``toolCall`` blocks become tool_use blocks
    (other blocks pass through); role ``toolResult`` becomes a user tool_result
    block joined on ``toolCallId``, with the msg-level ``isError`` boolean as
    the error flag; role ``user`` passes through with its ``attribution``, if
    present. Tool names (``bash``, ``read``,
    ``mcp__<server>_<tool>``) are kept verbatim.
    """
    session_id = None
    cwd = None
    for entry in iter_jsonl(path):
        if not isinstance(entry, dict):
            continue
        etype = entry.get("type")
        if etype == "session":
            session_id = entry.get("id") or session_id
            cwd = entry.get("cwd") or cwd
            continue
        if etype != "message":
            continue
        msg = entry.get("message")
        if not isinstance(msg, dict):
            continue
        ts = entry.get("timestamp")
        role = msg.get("role")
        if role == "assistant":
            blocks: list[dict[str, JsonValue]] = []
            content = msg.get("content")
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "toolCall":
                    args = block.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = cast(JsonValue, json.loads(args))
                        except (ValueError, RecursionError):
                            args = {"raw": args}
                    if not isinstance(args, dict):
                        args = {"raw": args}
                    args = cast(dict[str, JsonValue], args)
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": block.get("id"),
                            "name": block.get("name"),
                            "input": args,
                        }
                    )
                else:
                    blocks.append(block)
            yield {
                "harness": harness,
                "type": "assistant",
                "timestamp": ts,
                "sessionId": session_id,
                "cwd": cwd,
                "message": {"content": blocks, **_pi_family_turn_meta(msg)},
            }
        elif role == "toolResult":
            content = msg.get("content")
            parts: list[str] = []
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "text":
                    block_text = block.get("text", "")
                    if isinstance(block_text, str):
                        parts.append(block_text)
            text = "\n".join(parts)
            yield {
                "harness": harness,
                "type": "user",
                "timestamp": ts,
                "sessionId": session_id,
                "cwd": cwd,
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("toolCallId"),
                            "content": text,
                            "is_error": "true" if msg.get("isError") else "false",
                        }
                    ]
                },
            }
        elif role == "user":
            attribution = msg.get("attribution")
            yield {
                "harness": harness,
                "type": "user",
                "timestamp": ts,
                "sessionId": session_id,
                "cwd": cwd,
                "message": {
                    "content": msg.get("content"),
                    **({"attribution": attribution} if attribution is not None else {}),
                },
            }


def omp_normalize(path: str) -> Iterator[dict[str, object]]:
    return _pi_family_normalize(path, "omp")


def pi_normalize(path: str) -> Iterator[dict[str, object]]:
    return _pi_family_normalize(path, "pi")
