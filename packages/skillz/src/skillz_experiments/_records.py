from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import stat
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import cast

from skillz_experiments._cases import CodedError, Fixture, image_type, mapping, relative

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


def read_bytes(root: Path, name: str, limit: int) -> bytes:
    """Return the bytes of a regular file of at most `limit` bytes at `name` under `root`.

    Open each path part with `O_NOFOLLOW`, so a symlink anywhere in `name` fails. Raise `ValueError` that
    names `name` and never an absolute path.
    """
    parts = PurePosixPath(relative(name)).parts
    descriptors: list[int] = []
    try:
        here = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        descriptors.append(here)
        for part in parts[:-1]:
            here = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=here)
            descriptors.append(here)
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=here)
        with os.fdopen(leaf, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError(f"{name} must be a regular file")
            data = stream.read(limit + 1)
    except OSError:
        raise ValueError(f"{name} cannot be read") from None
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
    if len(data) > limit:
        raise ValueError(f"{name} is too large")
    return data


def read_text(root: Path, name: str, limit: int) -> str:
    """Return the strict UTF-8 text of the file that `read_bytes` reads."""
    try:
        return read_bytes(root, name, limit).decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{name} must be UTF-8 text") from None


def share_files(cases: Sequence[Mapping[str, object]], fixtures: dict[str, Fixture]) -> list[dict[str, object]]:
    """Select fixture values by saved JSON bytes before encoding references (gh-580). A binary marker is one value."""
    candidates: defaultdict[str, Counter[str | tuple[tuple[str, str], ...]]] = defaultdict(Counter)
    for case in cases:
        files = case.get("files")
        if isinstance(files, dict):
            for path, value in cast(dict[str, Fixture], files).items():
                candidates[path][value if isinstance(value, str) else tuple(sorted(value.items()))] += 1
    for path, counts in candidates.items():
        # Each fixture adds one string; each reference replaces an inline string with null.
        selected = max(counts, key=lambda key: ((counts[key] - 1) * len(json.dumps(key if isinstance(key, str) else dict(key)))
                                                - 4 * counts[key], str(key)))
        _ = fixtures.setdefault(path, selected if isinstance(selected, str) else dict(selected))
    shared: list[dict[str, object]] = []
    for case in cases:
        files = case.get("files")
        if isinstance(files, dict):
            references: dict[str, Fixture | None] = {}
            for path, text in cast(dict[str, Fixture], files).items():
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


def fixture_from_bytes(data: bytes, name: str) -> Fixture:
    """Return a fixture file's text when it decodes as UTF-8, else a base64 marker for a png, jpg, webp, or gif.

    Valid UTF-8 always stays text, so a text file that starts with `GIF89a` does not become an image.
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    kind = image_type(data)
    if kind is None:
        raise ValueError(f"{name} must be UTF-8 text or a png, jpg, webp, or gif image")
    return {"base64": base64.b64encode(data).decode("ascii"), "type": kind}


def sibling_trees(root: Path, companions: Sequence[str]) -> list[tuple[Path, str]]:
    """Return the directory and the name of each `../<name>` companion. Refuse one that is not a plain directory beside `root`."""
    base = root.resolve()
    trees: list[tuple[Path, str]] = []
    for companion in companions:
        name = companion[3:]
        path = base.parent / name
        if not companion.startswith("../"):
            continue
        if name == base.name or path.is_symlink() or not path.is_dir() or path.resolve().parent != base.parent:
            raise CodedError("contract-surface", f"companion {companion} must name an existing directory beside the skill: "
                             + "not the skill, a symlink, or a path that leaves the parent directory")
        trees.append((path, name))
    return trees