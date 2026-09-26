"""Build a reproducible .pyz for one skill's CLI.

Resolve and guard the project's third-party closure, install it plus the
configured local ``include`` trees and the skill's CLI source into a fresh
site directory, strip volatile install metadata, and shiv the result with a
fixed shebang and ``SOURCE_DATE_EPOCH`` so the same key always gives the
same bytes.
"""

from __future__ import annotations

import hashlib
import os
from io import BytesIO
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

from wedge._config import load_config
from wedge._guard import guard_closure
from wedge._key import TARGET_PYTHON, compute_key, resolve_paths
from wedge._resolve import export_requirements, resolve_closure

_VOLATILE_INSTALL_FILES = {"RECORD", "INSTALLER", "REQUESTED", "direct_url.json"}
# 1980-01-01T00:00:00Z: the earliest timestamp a zip member can hold. shiv's
# reproducible mode floors SOURCE_DATE_EPOCH=0 to this same value, but set it
# explicitly so the build does not depend on that floor.
_SOURCE_DATE_EPOCH = "315532800"


@dataclass(frozen=True)
class BuildResult:
    """The built ``.pyz``: its key, sha256, and where it landed on disk."""

    name: str
    key: str
    sha256: str
    path: Path


def build(skill_dir: Path, out_dir: Path) -> BuildResult:
    """Build ``<out_dir>/<name>-<key[:12]>.pyz``; returns its key and sha256."""
    skill_dir = Path(skill_dir).resolve()
    out_dir = Path(out_dir).resolve()
    config = load_config(skill_dir)
    paths = resolve_paths(skill_dir, config)
    key = compute_key(skill_dir, config)
    requirements = export_requirements(paths.project)
    closure = resolve_closure(paths.project, requirements)
    guard_closure(closure)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{config.name}-{key[:12]}.pyz"

    with tempfile.TemporaryDirectory(prefix="wedge-build-") as tmp:
        site_dir = Path(tmp) / "site"
        site_dir.mkdir()
        if closure:
            _install_third_party(requirements, site_dir)
        for local in (*paths.includes, paths.source):
            _copy_source(local, site_dir)
        _strip_volatile(site_dir)
        _shiv(site_dir, config.entry, out_path)
        _canonicalize_archive(out_path)

    return BuildResult(name=config.name, key=key, sha256=_sha256(out_path), path=out_path)


def _install_third_party(requirements: str, site_dir: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="wedge-requirements-") as tmp:
        requirements_path = Path(tmp) / "requirements.txt"
        requirements_path.write_text(requirements)
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--require-hashes",
                "--no-deps",
                "--target",
                str(site_dir),
                "--python-version",
                TARGET_PYTHON,
                "-r",
                str(requirements_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )


def _copy_tree(src: Path, dest: Path) -> None:
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def _copy_source(source: Path, site_dir: Path) -> None:
    if source.is_dir():
        _copy_tree(source, site_dir / source.name)
    else:
        shutil.copy2(source, site_dir / source.name)


def _strip_volatile(site_dir: Path) -> None:
    # Installed console scripts embed the builder's absolute Python path.
    shutil.rmtree(site_dir / "bin", ignore_errors=True)
    for path in sorted(site_dir.rglob("*"), reverse=True):
        if path.is_dir():
            if path.name == "__pycache__":
                shutil.rmtree(path, ignore_errors=True)
        elif path.name in _VOLATILE_INSTALL_FILES or path.suffix == ".pyc":
            path.unlink()


def _shiv(site_dir: Path, entry: str, out_path: Path) -> None:
    env = dict(os.environ)
    env["SOURCE_DATE_EPOCH"] = _SOURCE_DATE_EPOCH
    # Run the shiv that is installed beside wedge, not whichever `shiv` is
    # first on PATH: a different shiv version can change the archive bytes.
    subprocess.run(
        [
            sys.executable,
            "-m",
            "shiv",
            "--reproducible",
            "--uncompressed",
            "--site-packages",
            str(site_dir),
            "-p",
            "/usr/bin/env python3",
            "-e",
            entry,
            "-o",
            str(out_path),
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _canonicalize_archive(path: Path) -> None:
    """Sort ZIP members; shiv does not sort its bundled bootstrap files."""
    original = path.read_bytes()
    shebang, separator, _ = original.partition(b"\n")
    if not separator or not shebang.startswith(b"#!"):
        raise ValueError(f"shiv output lacks a shebang: {path}")
    with ZipFile(BytesIO(original)) as source, path.open("wb") as output:
        output.write(shebang + separator)
        with ZipFile(output, "w") as target:
            for info in sorted(source.infolist(), key=lambda item: item.filename):
                target.writestr(info, source.read(info))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
