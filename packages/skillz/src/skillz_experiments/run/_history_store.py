"""The private cross-run attempt history: one JSONL file per skill and target root, under the XDG state directory.

File IO stays in `run/`. A line holds one run: its id, a timestamp, the compatibility fingerprint and fields, and its attempts.
A line holds no request, expected, label, or holdout data. A store fault never changes the run result.
"""
from __future__ import annotations

import fcntl
import json
import os
import stat
import time
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


def _open(path: Path, flags: int) -> int:
    """Open `path` without following a symlink. Raise `OSError` unless it and its directory are private."""
    folder = path.parent.lstat()
    if not stat.S_ISDIR(folder.st_mode) or not _private(folder):
        raise OSError("the history directory is not private")
    descriptor = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    status = os.fstat(descriptor)
    if not stat.S_ISREG(status.st_mode) or not _private(status):
        os.close(descriptor)
        raise OSError("the history file is not private")
    return descriptor


def _lines(descriptor: int) -> list[str]:
    _ = os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while chunk := os.read(descriptor, 1 << 16):
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8").splitlines()


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(descriptor, view):]


def _parse(line: str) -> dict[str, object] | None:
    """Return one stored run, or None when the line is not a well-formed run."""
    try:
        value = cast(object, json.loads(line))
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    item = cast(dict[str, object], value)
    attempts = item.get("attempts")
    if (not isinstance(item.get("run_id"), str) or not isinstance(item.get("compatibility"), dict)
            or not isinstance(attempts, list) or not all(isinstance(entry, dict) for entry in cast(list[object], attempts))):
        return None
    return item


def load(path: Path, record: Mapping[str, object], run_id: str) -> Loaded:
    """Load the compatible earlier runs of `path` for the run `record`. The run's own line stays out.

    A run whose fingerprint fields differ is excluded and named with the differing fields. A corrupt line is skipped and
    counted. A store that cannot be read, or that is not private, gives the warning `history-unreadable` and no attempt.
    """
    loaded = Loaded()
    if not os.path.lexists(path):
        return loaded
    try:
        descriptor = _open(path, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH)
            lines = _lines(descriptor)
        finally:
            os.close(descriptor)
    except (OSError, UnicodeDecodeError):
        loaded.warnings.append(UNREADABLE)
        return loaded
    for line in lines:
        if not line.strip():
            continue
        item = _parse(line)
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
        try:
            directory_path.mkdir(mode=0o700)
        except FileExistsError:
            pass


def write(path: Path, record: Mapping[str, object], run_id: str, attempts: Sequence[Mapping[str, object]]) -> str | None:
    """Write the line of this run under an exclusive lock. Return `history-unreadable` when the store fails, else None.

    A line with the same run id is replaced, so a resumed search writes its run once. The store keeps the newest
    `KEEP_RUNS` lines. With room left, the line is appended; else the file is rewritten without the oldest lines.
    """
    line = json.dumps({"run_id": run_id, "timestamp": time.time(), "compatibility": record.get("compatibility"),
                       "attempts": [{key: attempt.get(key) for key in ATTEMPT_KEYS} for attempt in attempts]},
                      sort_keys=True) + "\n"
    try:
        _make_directory(path.parent)
        descriptor = _open(path, os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            lines = [text for text in _lines(descriptor) if text.strip()]
            kept = [text for text in lines if (_parse(text) or {}).get("run_id") != run_id]
            if len(kept) == len(lines) and len(kept) < KEEP_RUNS:
                _ = os.lseek(descriptor, 0, os.SEEK_END)
                _write_all(descriptor, line.encode("utf-8"))
            else:
                newest = kept[max(0, len(kept) - (KEEP_RUNS - 1)):]
                os.ftruncate(descriptor, 0)
                _ = os.lseek(descriptor, 0, os.SEEK_SET)
                _write_all(descriptor, ("".join(text + "\n" for text in newest) + line).encode("utf-8"))
        finally:
            os.close(descriptor)
    except (OSError, UnicodeDecodeError):
        return UNREADABLE
    return None
