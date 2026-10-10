from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import cast

from skillz_experiments._cases import CodedError, mapping

SCHEMA_VERSION = 4


def write_bytes(path: Path, data: bytes, *, mode: int = 0o600, guard: Callable[[], bool] | None = None) -> bool:
    """Write `data` to `path` atomically: temporary file, `fsync`, `guard`, then replace and directory `fsync`.

    Return False and leave `path` alone when `guard` returns False just before the replace.
    The directory `fsync` is best effort: after the replace, `path` holds `data`, so an `OSError` there returns True.
    """
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.skillz-", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            _ = stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if guard is not None and not guard():
            return False
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    return True


def write(path: Path, value: object) -> None:
    _ = write_bytes(path, (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8"))


def read(path: Path) -> dict[str, object]:
    if path.is_symlink() or path.stat().st_size > 10_000_000:
        raise ValueError("unsafe run record")
    return mapping(cast(object, json.loads(path.read_text(encoding="utf-8"))))

def open_record(path: Path) -> dict[str, object]:
    """Read `run.json`. An older schema stops with the coded error `run-schema-old`."""
    record = read(path)
    if record.get("schema_version") != SCHEMA_VERSION:
        raise CodedError("run-schema-old", f"{path} uses an older run schema; start a new run with `skillz-experiment run`")
    return record