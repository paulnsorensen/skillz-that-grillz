"""Command output in JSON or text form, with list truncation."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from typing import TextIO, cast


def emit(
    value: object,
    *,
    limit: int | None = None,
    full: bool = False,
    json_mode: bool = False,
    stdout: TextIO | None = None,
) -> None:
    """Print a scalar, mapping, or sequence in the shared output format.

    ``json_mode`` and mappings dump the whole value as JSON; ``limit`` applies
    only to text output of a list or a multi-line string.
    """
    stream = stdout if stdout is not None else sys.stdout
    if json_mode or isinstance(value, dict):
        print(json.dumps(value, indent=2, default=str), file=stream)
        return
    if isinstance(value, list):
        _emit_list(cast(list[object], value), limit=limit, full=full, stream=stream)
        return
    if isinstance(value, str) and limit is not None and "\n" in value:
        _emit_list(value.splitlines(), limit=limit, full=full, stream=stream)
        return
    print(value, file=stream)


def _emit_list(
    items: Sequence[object], *, limit: int | None, full: bool, stream: TextIO
) -> None:
    total = len(items)
    for item in items if full or limit is None else items[:limit]:
        print(item, file=stream)
    if limit is None:
        return
    if full:
        print(
            f"... showing {total} of {total} (--full; default limit={limit})",
            file=stream,
        )
    elif total > limit:
        print(
            f"... showing {limit} of {total}; pass --full for the rest (limit={limit})",
            file=stream,
        )
