"""The content key: a sha256 over every build input, sorted and canonical.

Two landings racing post-merge each publish their own immutable key; the
same key from two runs is the same bytes. There is no mutable "latest"
pointer: git order decides which key HEAD's lock references.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from wedge._config import ConfigError, WedgeConfig

FORMAT_VERSION = 5
TARGET_PYTHON = "3.11"


@dataclass(frozen=True)
class BuildPaths:
    """The resolved project directory and the local trees copied into the .pyz."""

    config_file: Path
    project: Path
    source: Path
    includes: tuple[Path, ...]

    @property
    def uv_lock(self) -> Path:
        return self.project / "uv.lock"


def _inside_project(project: Path, raw: str, base: Path, key: str) -> Path:
    resolved = (base / raw).resolve()
    if project not in resolved.parents:
        raise ConfigError(f"{key} {raw!r} must resolve inside the project {project}")
    if not resolved.exists():
        raise ConfigError(f"{key} {raw!r} does not exist: {resolved}")
    return resolved


def resolve_paths(skill_dir: Path, config: WedgeConfig) -> BuildPaths:
    """Resolve ``wedge.toml`` paths; raise ``ConfigError`` on a bad layout."""
    skill_dir = Path(skill_dir).resolve()
    project = (skill_dir / config.project).resolve()
    for required in ("pyproject.toml", "uv.lock"):
        if not (project / required).is_file():
            raise ConfigError(f"project {config.project!r} ({project}) has no {required}")
    source = _inside_project(project, config.source, skill_dir, "source")
    includes = tuple(_inside_project(project, item, skill_dir, "include") for item in config.include)
    names = [path.name for path in (source, *includes)]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ConfigError(f"source and include entries share top-level names: {duplicates}")
    return BuildPaths(
        config_file=skill_dir / "wedge.toml",
        project=project,
        source=source,
        includes=includes,
    )


def _is_excluded(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix == ".pyc"


def _iter_files(root: Path) -> list[Path]:
    if root.is_dir():
        return sorted(p for p in root.rglob("*") if p.is_file() and not _is_excluded(p))
    return [root]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute_key(skill_dir: Path, config: WedgeConfig) -> str:
    """The sha256 over ``wedge.toml``, ``uv.lock``, and every copied source file.

    Source and include files enter the key as (project-relative posix path,
    file sha256) pairs, so the key does not depend on the checkout path or on
    where the skill directory sits relative to the project.
    """
    paths = resolve_paths(skill_dir, config)
    inputs = {
        (file.relative_to(paths.project).as_posix(), _digest(file))
        for root in (paths.source, *paths.includes)
        for file in _iter_files(root)
    }
    canonical = json.dumps(
        {
            "format_version": FORMAT_VERSION,
            "target_python": TARGET_PYTHON,
            "config": _digest(paths.config_file),
            "uv_lock": _digest(paths.uv_lock),
            "inputs": sorted(inputs),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()