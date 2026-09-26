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

FORMAT_VERSION = 4
TARGET_PYTHON = "3.11"


def find_repo_root(start: Path) -> Path:
    """Walk up from ``start`` to the checkout that holds ``lib/fromargs/src/fromargs``."""
    current = Path(start).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "lib" / "fromargs" / "src" / "fromargs").is_dir() and (
            candidate / "lib" / "uv.lock"
        ).is_file():
            return candidate
    raise FileNotFoundError(
        f"no repo root (lib/fromargs/src/fromargs, lib/uv.lock) found above {start}"
    )


def _is_excluded(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix == ".pyc"


def _iter_files(root: Path) -> list[Path]:
    if root.is_dir():
        return sorted(p for p in root.rglob("*") if p.is_file() and not _is_excluded(p))
    return [root]


def source_path(skill_dir: Path, config: WedgeConfig, repo_root: Path) -> Path:
    """Resolve ``config.source``; raise ``ValueError`` when it escapes ``repo_root``."""
    resolved = (Path(skill_dir) / config.source).resolve()
    repo_root = Path(repo_root).resolve()
    if resolved != repo_root and repo_root not in resolved.parents:
        raise ValueError(f"source {config.source!r} resolves outside the repo root {repo_root}")
    return resolved


def build_inputs(skill_dir: Path, config: WedgeConfig, repo_root: Path) -> list[Path]:
    """Every file whose bytes decide the key: wedge.toml, the CLI source, all
    of ``lib/fromargs/src/fromargs/``, and ``lib/uv.lock``."""
    skill_dir = Path(skill_dir)
    files = [skill_dir / "wedge.toml"]
    files.extend(_iter_files(source_path(skill_dir, config, repo_root)))
    files.extend(_iter_files(Path(repo_root) / "lib" / "fromargs" / "src" / "fromargs"))
    files.append(Path(repo_root) / "lib" / "uv.lock")
    return files


def compute_key(skill_dir: Path, config: WedgeConfig, repo_root: Path) -> str:
    """The sha256 over the sorted (repo-relative path, file sha256) pairs."""
    repo_root = Path(repo_root).resolve()
    entries = []
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
