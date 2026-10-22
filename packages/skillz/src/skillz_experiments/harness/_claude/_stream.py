from __future__ import annotations

from collections.abc import Iterator
from typing import cast


def blocks(content: object) -> list[dict[str, object]]:
    """Return the object blocks of a content list, or none when `content` is not a list."""
    items = cast(list[object], content) if isinstance(content, list) else []
    return [cast(dict[str, object], item) for item in items if isinstance(item, dict)]


def message_blocks(event: dict[str, object]) -> list[dict[str, object]]:
    """Return the object blocks of an event's message content."""
    message = event.get("message")
    return blocks(cast(dict[str, object], message).get("content") if isinstance(message, dict) else None)


def tool_input(use: dict[str, object]) -> dict[str, object]:
    """Return the input of a tool use, or an empty input when it is not an object."""
    raw = use.get("input")
    return cast(dict[str, object], raw) if isinstance(raw, dict) else {}


def tool_pairs(events: list[dict[str, object]], name: str | None = None) -> Iterator[tuple[dict[str, object], dict[str, object]]]:
    """Yield each tool use with the tool result that answers it, in the stream order of the results.

    Only a tool use with a string id, and with the tool `name` when one is given, can be answered.
    A block that is not an object is skipped.
    """
    uses: dict[str, dict[str, object]] = {}
    for event in events:
        for block in message_blocks(event):
            identity = block.get("id") if block.get("type") == "tool_use" else block.get("tool_use_id")
            if not isinstance(identity, str):
                continue
            if block.get("type") == "tool_use" and name in (None, block.get("name")):
                uses[identity] = block
            elif block.get("type") == "tool_result" and identity in uses:
                yield uses[identity], block
