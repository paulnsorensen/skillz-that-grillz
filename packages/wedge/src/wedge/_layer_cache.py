"""A content-addressed cache of populated site layers, verified before every reuse.

uv's own cache is not safe to build from: uv checks a wheel's hash only when it
downloads it, and it hardlinks cached files into every install, so an edit to
any installed copy reaches every later build. This cache belongs to wedge, and
no other tool links into it. An entry is a directory named by its key. It holds
the stripped layer in ``site`` and, in ``manifest.json``, the sha256 of every
file and the name of every directory in the layer. Each reuse hashes the whole
layer again and compares it with the manifest. On any difference the entry is
moved aside and deleted, and the caller populates a new one from a fresh,
hash-checked download. A build output thus stays a function of the key, and
the key is a function of the locked wheel hashes. The manifest detects an
accidental or partial change; a writer that changes a layer file and its
manifest together is not detected, and such a writer can also replace uv.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

_LAYER = "site"
_MANIFEST = "manifest.json"
# An entry that no build reused for this long is deleted when a new entry is made.
_UNUSED_SECONDS = 30 * 24 * 60 * 60


def cache_root() -> Path:
    """``$WEDGE_CACHE/site-layers``; the default ``$WEDGE_CACHE`` is ``$XDG_CACHE_HOME/wedge``, as for the launcher."""
    base = os.environ.get("WEDGE_CACHE") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "wedge"
    )
    return Path(base) / "site-layers"


def manifest(root: Path) -> dict[str, str]:
    """Map each path under ``root`` to the sha256 of its file, or to ``"dir"``.

    Raise ``ValueError`` on a symlink or a special file.
    """
    entries: dict[str, str] = {}
    for directory, directories, files in os.walk(root):
        for name in (*directories, *files):
            path = Path(directory) / name
            mode = path.lstat().st_mode
            relative = path.relative_to(root).as_posix()
            if stat.S_ISDIR(mode):
                entries[relative] = "dir"
            elif stat.S_ISREG(mode):
                entries[relative] = _file_sha256(path)
            else:
                raise ValueError(f"site layer must hold only regular files and directories: {path}")
    return entries


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cached_layer(key: str, populate: Callable[[Path], None]) -> tuple[Path, dict[str, str]]:
    """Return the verified layer of the entry ``key`` and its manifest; make the entry with ``populate`` on a miss.

    ``populate`` fills the new, empty directory it gets. A failure leaves no entry.
    Builders in other processes can share the cache: an entry appears by one
    atomic rename, and a builder that loses the race uses the winner's entry.
    A layer can still change after this call returns, so a caller checks its
    output against the returned manifest.
    """
    root = cache_root()
    root.mkdir(parents=True, exist_ok=True)
    entry = root / key
    # Discard only an entry that exists and fails the check, never one that another builder just placed.
    present = entry.exists() or entry.is_symlink()
    verified = _verified(entry) if present else None
    if verified is not None:
        with contextlib.suppress(OSError):
            os.utime(entry / _MANIFEST)
        return entry / _LAYER, verified
    if present:
        _discard(entry)
    staging = Path(tempfile.mkdtemp(prefix=f".{key}.", dir=root))
    try:
        (staging / _LAYER).mkdir()
        populate(staging / _LAYER)
        fresh = manifest(staging / _LAYER)
        _ = (staging / _MANIFEST).write_text(json.dumps(fresh, sort_keys=True))
        try:
            os.rename(staging, entry)
        except OSError:
            verified = _verified(entry)
            if verified is None:
                raise
            shutil.rmtree(staging)
            return entry / _LAYER, verified
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    # Pruning is housekeeping; a race with another builder must not fail this build.
    with contextlib.suppress(OSError):
        _prune(root, keep=entry)
    return entry / _LAYER, fresh


def _verified(entry: Path) -> dict[str, str] | None:
    """The manifest of ``entry`` when it is a plain directory whose layer matches it file by file, else None."""
    try:
        if not (stat.S_ISDIR(entry.lstat().st_mode) and stat.S_ISDIR((entry / _LAYER).lstat().st_mode)):
            return None
        recorded = cast(object, json.loads((entry / _MANIFEST).read_text()))
        current = manifest(entry / _LAYER)
        return current if recorded == current else None
    except (OSError, ValueError):
        return None


def _discard(entry: Path) -> None:
    """Move ``entry`` aside by one rename, then delete it. Another builder may remove it first."""
    if not (entry.exists() or entry.is_symlink()):
        return
    if entry.is_symlink() or not entry.is_dir():
        entry.unlink(missing_ok=True)
        return
    aside = Path(tempfile.mkdtemp(prefix=f".{entry.name}.stale.", dir=entry.parent))
    try:
        os.rename(entry, aside / entry.name)
    except FileNotFoundError:
        pass
    shutil.rmtree(aside, ignore_errors=True)


def _prune(root: Path, keep: Path) -> None:
    """Delete each entry other than ``keep`` that no build used for ``_UNUSED_SECONDS``.

    A leftover staging or stale directory of a killed builder goes after the same time.
    """
    cutoff = time.time() - _UNUSED_SECONDS
    for entry in root.iterdir():
        if entry == keep:
            continue
        leftover = entry.name.startswith(".")
        try:
            used = (entry if leftover else entry / _MANIFEST).lstat().st_mtime
        except OSError:
            continue
        if used >= cutoff:
            continue
        if leftover:
            shutil.rmtree(entry, ignore_errors=True)
        else:
            _discard(entry)
