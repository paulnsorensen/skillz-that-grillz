"""Serialize a command's return value as one JSON document, with truncation."""

from __future__ import annotations

import dataclasses
import itertools
import json
import os
import sys
from collections.abc import Iterator, Mapping, Sequence, Set
from typing import TextIO, cast

_BYTES_LIKE = (bytes, bytearray, memoryview)
# Sequences that serialize as one JSON value, never as a list of items.
_SCALAR_SEQUENCES = (str, *_BYTES_LIKE)


def write_result(
    value: object, *, limit: int | None, full: bool, stdout: TextIO | None = None
) -> None:
    """Print ``value`` as one JSON document; truncate a long sequence unless ``full``.

    A sequence longer than ``limit`` is cut to its first ``limit`` items and
    stderr gets a ``note:`` line, unless ``full`` is true or ``limit`` is
    ``None``. A set becomes a sorted list first, so it truncates the same way.
    Truncation never applies to a mapping or a string. ``NaN`` and
    ``Infinity`` are rejected, and a type ``_default`` cannot resolve (bytes,
    iterators) raises ``TypeError`` instead of printing a misleading fallback.
    """
    stream = stdout if stdout is not None else sys.stdout
    payload = _sorted_items(value) if isinstance(value, Set) else value
    note: str | None = None
    if limit is not None and isinstance(payload, Sequence) and not isinstance(payload, _SCALAR_SEQUENCES):
        items = cast("Sequence[object]", payload)
        total = len(items)
        if not full and total > limit:
            payload = list(itertools.islice(items, limit))
            note = f"note: showing {limit} of {total}; pass --full for the rest"
    serialized = json.dumps(payload, indent=2, default=_default, allow_nan=False)
    if note is not None:
        print(note, file=sys.stderr)
    print(serialized, file=stream)


def _sorted_items(items: object) -> list[object]:
    """Order set items deterministically.

    Use natural order when no member is a ``Set``. A ``Set`` member (``<`` means
    subset, so natural order is not total) or a ``TypeError`` from natural order
    sorts by canonical JSON text instead. ``repr`` is not usable here, because
    the ``repr`` of a multi-element frozenset depends on hash order.
    """
    members = list(cast("Set[object]", items))
    if not any(isinstance(member, Set) for member in members):
        try:
            return cast("list[object]", sorted(members))  # pyright: ignore[reportArgumentType]
        except TypeError:
            pass
    return sorted(members, key=lambda item: json.dumps(item, sort_keys=True, default=_default))


def _default(value: object) -> object:
    """Fallback for ``json.dumps``: dataclass via ``asdict``, path via ``str``, mapping, set, or list.

    A nested set becomes a sorted list. Bytes and iterators raise ``TypeError``
    with a hint, because bytes have no unambiguous JSON form and an iterator
    has no known length for ``limit``.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, os.PathLike):
        return str(value)
    if isinstance(value, Mapping):
        return dict(cast("Mapping[object, object]", value))
    if isinstance(value, Set):
        return _sorted_items(value)
    if isinstance(value, Sequence) and not isinstance(value, _SCALAR_SEQUENCES):
        return list(value)
    name = type(value).__name__
    if isinstance(value, _BYTES_LIKE):
        raise TypeError(f"{name} is not JSON serializable; return a str (decode it) or a list of ints")
    if isinstance(value, Iterator):
        raise TypeError(f"{name} is not JSON serializable; return a list instead of an iterator")
    raise TypeError(f"Object of type {name} is not JSON serializable")