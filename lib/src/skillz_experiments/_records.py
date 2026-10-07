from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import cast

from skillz_experiments._cases import CodedError, mapping

SCHEMA_VERSION = 2


def write(path: Path, value: object) -> None:
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            _ = stream.write("\n")
        _ = temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


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