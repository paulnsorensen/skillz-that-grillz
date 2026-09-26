"""The content key: a sha256 over every build input, sorted and canonical.

Two landings racing post-merge each publish their own immutable key; the
same key from two runs is the same bytes. There is no mutable "latest"
pointer: git order decides which key HEAD's lock references.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from wedge._config import WedgeConfig

FORMAT_VERSION = 6
TARGET_PYTHON = "3.11"


def find_repo_root(start: Path) -> Path:
    """Walk up from ``start`` to the checkout that holds ``lib/fromargs``."""
    current = Path(start).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "lib" / "fromargs" / "src" / "fromargs").is_dir() and (
            candidate / "lib" / "fromargs" / "uv.lock"
        ).is_file():
            return candidate
    raise FileNotFoundError(
        f"no repo root (lib/fromargs/src/fromargs, lib/fromargs/uv.lock) found above {start}"
    )


def _is_excluded(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix == ".pyc"


def _iter_files(root: Path) -> list[Path]:
    if root.is_symlink():
        raise ValueError(f"build source must not be a symlink: {root}")
    if root.is_dir():
        files: list[Path] = []
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"build source must not contain symlink: {path}")
            if path.is_file() and not _is_excluded(path):
                files.append(path)
        return files
    if not root.is_file():
        raise FileNotFoundError(root)
    return [root]


def source_path(skill_dir: Path, config: WedgeConfig, repo_root: Path) -> Path:
    """Resolve ``config.source``; reject symlinks before containment checks."""
    skill_dir = Path(skill_dir).resolve()
    repo_root = Path(repo_root).resolve()
    raw = skill_dir / config.source
    cursor = raw
    while cursor != repo_root and repo_root in cursor.parents:
        if cursor.is_symlink():
            raise ValueError(f"source path must not contain symlink: {cursor}")
        cursor = cursor.parent
    resolved = raw.resolve()
    if resolved != repo_root and repo_root not in resolved.parents:
        raise ValueError(f"source {config.source!r} resolves outside the repo root {repo_root}")
    return resolved


def build_inputs(skill_dir: Path, config: WedgeConfig, repo_root: Path) -> list[Path]:
    """Every file whose bytes decide the key: wedge.toml, the CLI source, all
    of ``lib/fromargs/src/fromargs/``, and ``lib/fromargs/uv.lock``."""
    skill_dir = Path(skill_dir)
    files = [skill_dir / "wedge.toml"]
    files.extend(_iter_files(source_path(skill_dir, config, repo_root)))
    files.extend(_iter_files(Path(repo_root) / "lib" / "fromargs" / "src" / "fromargs"))
    files.append(Path(repo_root) / "lib" / "fromargs" / "uv.lock")
    return files


def compute_key(skill_dir: Path, config: WedgeConfig, repo_root: Path) -> str:
    """The sha256 over the sorted (repo-relative path, file sha256) pairs."""
    repo_root = Path(repo_root).resolve()
    entries: list[tuple[str, str]] = []
    for file in build_inputs(skill_dir, config, repo_root):
        rel = file.resolve().relative_to(repo_root).as_posix()
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        entries.append((rel, digest))
    entries.sort()
    canonical = json.dumps(
        {
            "format_version": FORMAT_VERSION,
            "target_python": TARGET_PYTHON,
            "inputs": entries,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
