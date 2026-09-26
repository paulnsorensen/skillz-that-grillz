"""Serialize a command's return value as one JSON document, with truncation."""

from __future__ import annotations

import dataclasses
import itertools
import json
import os
import sys
from collections.abc import Mapping, Sequence
from typing import TextIO, cast


def write_result(
    value: object, *, limit: int | None, full: bool, stdout: TextIO | None = None
) -> None:
    """Print ``value`` as one JSON document; truncate a long sequence unless ``full``.

    A sequence longer than ``limit`` is cut to its first ``limit`` items and
    stderr gets a ``note:`` line, unless ``full`` is true or ``limit`` is
    ``None``. Truncation never applies to a mapping or a string. ``NaN``
    and ``Infinity`` are rejected, and a type ``_default`` cannot resolve
    raises ``TypeError`` instead of printing a misleading fallback.
    """
    stream = stdout if stdout is not None else sys.stdout
    payload = value
    note: str | None = None
    if limit is not None and isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        total = len(value)
        if not full and total > limit:
            payload = list(itertools.islice(value, limit))
            note = f"note: showing {limit} of {total}; pass --full for the rest"
    serialized = json.dumps(payload, indent=2, default=_default, allow_nan=False)
    if note is not None:
        print(note, file=sys.stderr)
    print(serialized, file=stream)


def _default(value: object) -> object:
    """Fallback for ``json.dumps``: dataclass via ``asdict``, path via ``str``, else mapping, else list."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, os.PathLike):
        return str(value)
    if isinstance(value, Mapping):
        return dict(cast("Mapping[object, object]", value))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")