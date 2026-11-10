"""Turn the raw events of a journey task into harness-neutral steps for the judge.

A step is a message, `{"turn", "text"}`, or a tool call, `{"turn", "tool", "input", "output", "error"}`.
A Codex command also has `exit_code`. `turn` counts the agent turns from 1, so a step lines up with the transcript.
The evaluator bounds and redacts the steps before the judge sees them.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import cast

from skillz_experiments._cases import mapping
from skillz_experiments.harness._claude._pairs import blocks

Step = dict[str, object]
Renderer = Callable[[list[dict[str, object]]], list[Step]]
# Codex items that the steps leave out: the user message is in the transcript, and reasoning is not task evidence.
_SKIPPED_ITEMS = frozenset({"userMessage", "reasoning"})


def _text(content: object) -> str:
    """Join the text of a Claude tool result. Claude Code sends it as a string or as a list of text blocks."""
    return content if isinstance(content, str) else "\n".join(
        str(block.get("text", "")) for block in blocks(content) if block.get("type") == "text")


def claude_steps(events: list[dict[str, object]]) -> list[Step]:
    """Return the assistant messages and tool calls of a Claude stream. Each `result` event ends a turn."""
    steps: list[Step] = []
    calls: dict[str, Step] = {}
    turn = 1
    for event in events:
        if event.get("type") == "result":
            turn += 1
            continue
        message = event.get("message")
        for block in blocks(cast(dict[str, object], message).get("content") if isinstance(message, dict) else None):
            kind, identifier = block.get("type"), block.get("id")
            if kind == "text" and event.get("type") == "assistant" and isinstance(block.get("text"), str):
                steps.append({"turn": turn, "text": block["text"]})
            elif kind == "tool_use" and isinstance(identifier, str):
                calls[identifier] = {"turn": turn, "tool": block.get("name"), "input": block.get("input"),
                                     "output": None, "error": None}
                steps.append(calls[identifier])
            elif kind == "tool_result" and isinstance(answered := block.get("tool_use_id"), str) and answered in calls:
                calls[answered] |= {"output": _text(block.get("content")), "error": block.get("is_error") is True}
    return steps


def _codex_item(item: dict[str, object]) -> Step | None:
    kind = item.get("type")
    if kind == "agentMessage":
        return {"text": item.get("text")}
    if kind == "commandExecution":
        code = item.get("exitCode")
        return {"tool": "shell", "input": {"command": item.get("command")}, "output": item.get("aggregatedOutput"),
                "error": code != 0 if isinstance(code, int) else None, "exit_code": code}
    if kind == "fileChange":
        return {"tool": "file_change", "input": {"changes": item.get("changes")}, "output": None,
                "error": item.get("status") not in (None, "completed")}
    if kind == "mcpToolCall":
        return {"tool": f"mcp:{item.get('server')}/{item.get('tool')}", "input": item.get("arguments"),
                "output": item.get("result"), "error": item.get("error") is not None}
    if not isinstance(kind, str) or kind in _SKIPPED_ITEMS:
        return None
    rest = {key: value for key, value in item.items() if key not in ("id", "type")}
    return {"tool": kind, "input": rest, "output": None, "error": item.get("status") not in (None, "completed")}


def codex_steps(events: list[dict[str, object]]) -> list[Step]:
    """Return the completed items of a Codex app-server stream. The turns follow the order of their first event."""
    steps: list[Step] = []
    turns: dict[object, int] = {}
    for event in events:
        params = mapping(event.get("params")) if isinstance(event.get("params"), dict) else {}
        turn = params.get("turnId")
        if turn is None or event.get("method") != "item/completed":
            continue
        item = params.get("item")
        step = _codex_item(cast(dict[str, object], item)) if isinstance(item, dict) else None
        if step is not None:
            steps.append({"turn": turns.setdefault(turn, len(turns) + 1), **step})
    return steps


def journey_steps(adapter: str) -> Renderer | None:
    """Return the step renderer of a task adapter, or None for an adapter whose raw events have no known shape."""
    return {"claude": claude_steps, "codex": codex_steps}.get(adapter)
