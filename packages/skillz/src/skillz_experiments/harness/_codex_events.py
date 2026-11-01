"""Decode line-delimited Codex execution events."""
from __future__ import annotations

import re

from skillz_experiments._cases import loads_untrusted, mapping


def parse_events(stdout: str) -> list[dict[str, object]]:
    """Read one JSON object per line without splitting raw Unicode separators."""
    events: list[dict[str, object]] = []
    for line in re.split(r"[\r\n]", stdout):
        if not line.strip():
            continue
        try:
            events.append(mapping(loads_untrusted(line)))
        except ValueError:
            raise RuntimeError("invalid Codex JSON event stream") from None
    return events
