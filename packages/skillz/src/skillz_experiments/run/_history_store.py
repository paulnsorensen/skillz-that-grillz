"""The private cross-run attempt history: one JSONL file per skill and target root, under the XDG state directory.

File IO stays in `run/`. A line holds one run: its id, a timestamp, the compatibility fingerprint and fields, and its attempts.
A line holds no request, expected, label, or holdout data. A store fault never changes the run result.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import stat
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from skillz_experiments._cases import digest
from skillz_experiments.run._resume import compare

# The newest runs that one store keeps. A write drops older lines.
KEEP_RUNS = 20
UNREADABLE = "history-unreadable"
# The attempt fields that a stored line keeps. They hold identities, digests, case ids, and the proposer hypothesis.
ATTEMPT_KEYS = ("index", "parent", "candidate", "components", "hypothesis", "fixed", "broke", "validation_delta",
                "accepted", "rejected", "rejected_coded")


def directory() -> Path:
    """Return `$XDG_STATE_HOME/skillz/history`. A relative or empty `XDG_STATE_HOME` falls back to `~/.local/state`."""
    base = os.environ.get("XDG_STATE_HOME", "")
    root = Path(base) if base and Path(base).is_absolute() else Path.home() / ".local" / "state"
    return root / "skillz" / "history"


def store_path(skill: str, target_root: str) -> Path:
    """Return the store file of one skill name and resolved target root."""
    return directory() / f"{digest({'skill': skill, 'target_root': target_root})}.jsonl"


@dataclass
class Loaded:
    """The compatible earlier attempts, oldest first, and the summary of the load."""
    attempts: list[dict[str, object]] = field(default_factory=lambda: [])
    runs: list[str] = field(default_factory=lambda: [])
    excluded: list[dict[str, object]] = field(default_factory=lambda: [])
    skipped_lines: int = 0
    warnings: list[str] = field(default_factory=lambda: [])

    def summary(self) -> dict[str, object]:
        return {"enabled": True, "runs": self.runs, "excluded": self.excluded, "skipped_lines": self.skipped_lines,
                "warnings": self.warnings}


def _private(status: os.stat_result) -> bool:
    return status.st_uid == os.getuid() and not status.st_mode & 0o077


def _open_directory(path: Path) -> int:
    """Open the history directory without following a symlink. Raise `OSError` unless it is private."""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    if not _private(os.fstat(descriptor)):
        os.close(descriptor)
        raise OSError("the history directory is not private")
    return descriptor


def _open(folder: int, name: str, flags: int) -> int:
    """Open `name` in `folder` without following a symlink. Raise `OSError` unless it is a private regular file."""
    descriptor = os.open(name, flags | os.O_NOFOLLOW, 0o600, dir_fd=folder)
    status = os.fstat(descriptor)
    if not stat.S_ISREG(status.st_mode) or not _private(status):
        os.close(descriptor)
        raise OSError("the history file is not private")
    return descriptor


def _read(folder: int, name: str) -> list[bytes]:
    """Return the non-blank lines of the store as bytes. A missing store has no line."""
    try:
        descriptor = _open(folder, name, os.O_RDONLY)
    except FileNotFoundError:
        return []
    with os.fdopen(descriptor, "rb") as handle:
        return [raw for raw in handle.read().split(b"\n") if raw.strip()]


def _parse(raw: bytes) -> dict[str, object] | None:
    """Return one stored run, or None when the line is not a well-formed run."""
    try:
        value = cast(object, json.loads(raw.decode("utf-8")))
    except (ValueError, RecursionError):
        return None
    if not isinstance(value, dict):
        return None
    item = cast(dict[str, object], value)
    attempts = item.get("attempts")
    if (not isinstance(item.get("run_id"), str) or not isinstance(item.get("compatibility"), dict)
            or not isinstance(attempts, list) or not all(isinstance(entry, dict) for entry in cast(list[object], attempts))):
        return None
    return item


def _lock(folder: int, name: str, mode: int) -> int:
    """Open the lock file of the store and take the lock `mode`. The caller closes the descriptor to release it."""
    descriptor = _open(folder, f"{name}.lock", os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(descriptor, mode)
    except OSError:
        os.close(descriptor)
        raise
    return descriptor


def load(path: Path, record: Mapping[str, object], run_id: str) -> Loaded:
    """Load the compatible earlier runs of `path` for the run `record`. The run's own line stays out.

    The load excludes a run whose fingerprint fields differ and names the differing fields. The load skips and counts
    a corrupt line, including a line with bad UTF-8 or nesting that is too deep. A store that cannot be read, or that
    is not private, gives the warning `history-unreadable` and no attempt.
    """
    loaded = Loaded()
    if not os.path.lexists(path):
        return loaded
    try:
        folder = _open_directory(path.parent)
        try:
            locked = _lock(folder, path.name, fcntl.LOCK_SH)
            try:
                lines = _read(folder, path.name)
            finally:
                os.close(locked)
        finally:
            os.close(folder)
    except OSError:
        loaded.warnings.append(UNREADABLE)
        return loaded
    for raw in lines:
        item = _parse(raw)
        if item is None:
            loaded.skipped_lines += 1
            continue
        earlier = cast(str, item["run_id"])
        if earlier == run_id:
            continue
        differing = compare(item, record)
        if differing:
            loaded.excluded.append({"run_id": earlier, "fields": list(differing)})
            continue
        loaded.runs.append(earlier)
        loaded.attempts.extend({"run_id": earlier} | entry for entry in cast(list[dict[str, object]], item["attempts"]))
    return loaded


def _make_directory(path: Path) -> None:
    """Create the store directory and its `skillz` parent with mode 0700. An existing directory keeps its mode."""
    path.parent.parent.mkdir(parents=True, exist_ok=True)
    for directory_path in (path.parent, path):
        directory_path.mkdir(mode=0o700, exist_ok=True)


def _stored(attempt: Mapping[str, object]) -> dict[str, object]:
    """Return the kept fields of one attempt. Raw rejection text can hold host paths, so only a code stays."""
    row = {key: attempt.get(key) for key in ATTEMPT_KEYS}
    reason = row["rejected"]
    if reason is not None and not (row["rejected_coded"] is True and isinstance(reason, str)):
        row["rejected"] = "rejected"
    return row


def _replace(folder: int, name: str, data: bytes) -> None:
    """Write `data` to a private temporary file next to the store, sync it, and move it over the store."""
    temporary = f".{name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            _ = handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, name, src_dir_fd=folder, dst_dir_fd=folder)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary, dir_fd=folder)
        raise


def write(path: Path, record: Mapping[str, object], run_id: str, attempts: Sequence[Mapping[str, object]]) -> str | None:
    """Write the line of this run under an exclusive lock. Return `history-unreadable` when the store fails, else None.

    The write replaces a line with the same run id, so a resumed search writes its run once. The write drops each
    line that is not a well-formed run, so a corrupt line uses no retention slot. The store keeps the newest
    `KEEP_RUNS` runs. The write builds the whole new store in a temporary file and moves it over the old one,
    so a kill leaves either the old store or the new store. The lock lives in the file `<store>.lock`.
    """
    line = json.dumps({"run_id": run_id, "timestamp": time.time(), "compatibility": record.get("compatibility"),
                       "attempts": [_stored(attempt) for attempt in attempts]}, sort_keys=True).encode("utf-8") + b"\n"
    try:
        _make_directory(path.parent)
        folder = _open_directory(path.parent)
        try:
            locked = _lock(folder, path.name, fcntl.LOCK_EX)
            try:
                kept = [raw for raw in _read(folder, path.name)
                        if (item := _parse(raw)) is not None and item["run_id"] != run_id]
                newest = kept[max(0, len(kept) - (KEEP_RUNS - 1)):]
                _replace(folder, path.name, b"".join(raw + b"\n" for raw in newest) + line)
            finally:
                os.close(locked)
        finally:
            os.close(folder)
    except OSError:
        return UNREADABLE
    return None
