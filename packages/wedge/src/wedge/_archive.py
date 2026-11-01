"""Place a validated site tree in a canonical archive atomically."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from zipfile import ZipFile

from wedge._config import validate_name
from wedge._digest import content_sha256
from wedge._layer_cache import manifest
from wedge._zipapp import write_archive


def archive_into(
    site_dir: Path,
    name: str,
    entry: str,
    out_dir: Path,
    check: tuple[Mapping[str, str], frozenset[str]] | None = None,
    trees: dict[str, Path] | None = None,
) -> tuple[str, Path]:
    """Package the source closure and place the archive atomically."""
    validate_name(name)
    with tempfile.TemporaryDirectory(prefix="wedge-build-") as tmp:
        built = Path(tmp) / f"{name}.pyz"
        write_archive(site_dir, entry, built, trees)
        if check is not None:
            _check_layer_members(built, *check)
        digest = content_sha256(built)
        out_path = out_dir / f"{name}-{digest[:12]}.pyz"
        descriptor, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=out_dir)
        os.close(descriptor)
        try:
            _ = shutil.copyfile(built, temporary)
            os.chmod(temporary, stat.S_IMODE(built.stat().st_mode))
            os.replace(temporary, out_path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
    return digest, out_path


def check_layer_snapshot(site_dir: Path, expected: Mapping[str, str] | None, first_party: frozenset[str]) -> None:
    if expected is None:
        return
    found = {name: digest for name, digest in manifest(site_dir).items()
             if name.split("/", 1)[0] not in first_party}
    if found != expected:
        changed = sorted(set(found) ^ set(expected) | {
            name for name in found.keys() & expected.keys() if found[name] != expected[name]
        })
        raise ValueError(f"the cached site layer changed during the build ({', '.join(changed[:3])}); build again")


def _check_layer_members(archive: Path, expected: Mapping[str, str], first_party: frozenset[str]) -> None:
    """Check only the third-party source closure against its layer manifest."""
    files = {path: digest for path, digest in expected.items() if digest != "dir"}
    found: dict[str, str] = {}
    with ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename.removeprefix("site-packages/")
            if name != info.filename and not info.is_dir() and name.split("/", 1)[0] not in first_party:
                found[name] = hashlib.sha256(zf.read(info)).hexdigest()
    if found != files:
        changed = sorted(set(found) ^ set(files) | {p for p in found.keys() & files.keys() if found[p] != files[p]})
        raise ValueError(f"the cached site layer changed during the build ({', '.join(changed[:3])}); build again")
