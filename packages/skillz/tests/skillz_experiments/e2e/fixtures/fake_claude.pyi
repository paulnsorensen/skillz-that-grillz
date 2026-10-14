"""Types for the staged fake Claude protocol module."""
from __future__ import annotations

__file__: str
BUILTIN_PLUGINS: list[dict[str, str]]
USAGE: dict[str, int]

def main() -> int: ...
