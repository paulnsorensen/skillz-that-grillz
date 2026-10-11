"""Protocol version 1 of the command adapter. The runner and the reference wrapper both import this module.

Keep it stdlib only: the wrapper runs with no third-party packages.
"""
from __future__ import annotations

from typing import cast

VERSION = 1
OPERATIONS = ("sandbox", "discover", "infer")
RESPONSE_FIELDS = {"sandbox": frozenset({"returncode", "stdout", "stderr"}), "discover": frozenset({"skills"}),
                   "infer": frozenset({"answer", "trace", "usage"})}
USAGE_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens")
LEDGER_KEY = "_ledger"
"""The private key of a transport result that carries the helper ledger lines of one call."""


def argv_strings(value: object) -> list[str] | None:
    """Return `value` when it is a non-empty list of non-empty strings, else None."""
    if not isinstance(value, list) or not value:
        return None
    items = cast(list[object], value)
    return cast(list[str], items) if all(isinstance(item, str) and item for item in items) else None
