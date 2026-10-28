"""The JSON value type and the line reader that every harness adapter shares."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from typing import TypeAlias

JsonValue: TypeAlias = "None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]"


def iter_jsonl(path: str) -> Iterator[JsonValue]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield cast(JsonValue, json.loads(line))
            # JSONDecodeError is a ValueError. A number past the int digit limit raises a plain ValueError,
            # and nesting past the recursion limit raises RecursionError. Each skips one line.
            except (ValueError, RecursionError):
                continue
