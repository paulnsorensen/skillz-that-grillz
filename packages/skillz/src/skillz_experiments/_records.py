from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
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


def approval_digest(value: object) -> str:
    """Hash canonical approval JSON without collecting repeated fixture text."""
    result = hashlib.sha256()
    for chunk in json.JSONEncoder(sort_keys=True).iterencode(value):
        result.update(chunk.encode())
    return result.hexdigest()


def write(path: Path, value: object) -> None:
    _ = write_bytes(path, (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8"))


def write_cases(path: Path, value: object) -> None:
    """Write a frozen case manifest only when the complete JSON fits the loader bound."""
    data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(data) > 2_000_000:
        raise CodedError("cases-too-large", "the frozen cases exceed 2000000 bytes; reduce the draft and ask for approval again")
    _ = write_bytes(path, data)


def share_files(cases: Sequence[Mapping[str, object]], fixtures: dict[str, str]) -> list[dict[str, object]]:
    """Select fixture values by saved JSON bytes before encoding references (gh-580)."""
    candidates: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for case in cases:
        files = case.get("files")
        if isinstance(files, dict):
            for path, text in cast(dict[str, str], files).items():
                candidates[path][text] += 1
    for path, counts in candidates.items():
        # Each fixture adds one string; each reference replaces an inline string with null.
        selected = max(counts, key=lambda text: ((counts[text] - 1) * len(json.dumps(text)) - 4 * counts[text], text))
        _ = fixtures.setdefault(path, selected)
    shared: list[dict[str, object]] = []
    for case in cases:
        files = case.get("files")
        if isinstance(files, dict):
            references: dict[str, str | None] = {}
            for path, text in cast(dict[str, str], files).items():
                references[path] = None if fixtures.setdefault(path, text) == text else text
            case = {**case, "files": list(references) if all(text is None for text in references.values()) else references}
        shared.append(dict(case))
    return shared

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