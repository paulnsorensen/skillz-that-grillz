from __future__ import annotations

from collections.abc import Iterator
from typing import cast


def blocks(content: object) -> list[dict[str, object]]:
    """Return the object blocks of a content list, or none when `content` is not a list."""
    items = cast(list[object], content) if isinstance(content, list) else []
    return [cast(dict[str, object], item) for item in items if isinstance(item, dict)]


def tool_pairs(events: list[dict[str, object]], name: str | None = None) -> Iterator[tuple[dict[str, object], dict[str, object] | None]]:
    """Walk the message content of `events` in stream order and pair each tool use with its results.

    Yield `(use, None)` when a `tool_use` block with a string id appears. Yield `(use, result)` for each later
    `tool_result` block that answers it, so a tool use with no result yields only `(use, None)`.
    `name` keeps only the tool uses of that tool. A non-object block is skipped.
    """
    uses: dict[str, dict[str, object]] = {}
    for event in events:
        message = event.get("message")
        for block in blocks(cast(dict[str, object], message).get("content") if isinstance(message, dict) else None):
            use_id = block.get("id")
            if block.get("type") == "tool_use" and isinstance(use_id, str):
                if name is None or block.get("name") == name:
                    uses[use_id] = block
                    yield block, None
            elif block.get("type") == "tool_result":
                answered = block.get("tool_use_id")
                if isinstance(answered, str) and answered in uses:
                    yield uses[answered], block
