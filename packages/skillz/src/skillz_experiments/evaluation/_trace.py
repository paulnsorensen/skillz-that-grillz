"""Bound and redact the task steps that the journey judge sees as `trace`.

Each text value passes through `redact`. A text value longer than `FIELD_CHARS` keeps its head and its tail, because a
command shows its result at the end. A nested value whose JSON is longer than `FIELD_CHARS` becomes that cut JSON text.
When the steps still exceed `TRACE_CHARS` of JSON, the trace keeps the first steps up to a third of the limit and the
last steps up to the rest, and one `{"omitted_steps": n}` entry marks the gap.
"""
from __future__ import annotations

import json
from typing import cast

from skillz_experiments._redact import redact

FIELD_CHARS = 4_000
TRACE_CHARS = 48_000
_HEAD = FIELD_CHARS // 4


def _cut(text: str) -> str:
    text = redact(text)
    if len(text) <= FIELD_CHARS:
        return text
    tail = FIELD_CHARS - _HEAD
    return f"{text[:_HEAD]}\n[... {len(text) - FIELD_CHARS} characters cut ...]\n{text[-tail:]}"


def _bounded(value: object) -> object:
    if isinstance(value, str):
        return _cut(value)
    if isinstance(value, (dict, list)):
        encoded = json.dumps(value)
        if len(encoded) > FIELD_CHARS:
            return _cut(encoded)
        if isinstance(value, dict):
            return {str(key): _bounded(item) for key, item in cast(dict[object, object], value).items()}
        return [_bounded(item) for item in cast(list[object], value)]
    return value


def judge_trace(steps: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return the steps with each value bounded and redacted, and the middle steps dropped past `TRACE_CHARS`."""
    kept = [{key: _bounded(value) for key, value in step.items()} for step in steps]
    sizes = [len(json.dumps(step)) for step in kept]
    if sum(sizes) <= TRACE_CHARS:
        return kept
    used = head = 0
    while head < len(kept) and used + sizes[head] <= TRACE_CHARS // 3:
        used += sizes[head]
        head += 1
    tail = len(kept)
    while tail > head and used + sizes[tail - 1] <= TRACE_CHARS:
        used += sizes[tail - 1]
        tail -= 1
    return [*kept[:head], {"omitted_steps": tail - head}, *kept[tail:]]
