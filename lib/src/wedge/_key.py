"""The content key: a sha256 over every build input, sorted and canonical.

Two landings racing post-merge each publish their own immutable key; the
same key from two runs selects the same normalized content. There is no mutable
"latest" pointer: git order decides which key HEAD's lock references.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from wedge._config import ConfigError, WedgeConfig, defaults_path

FORMAT_VERSION = 8
TARGET_PYTHON = "3.11"


@dataclass(frozen=True)
class BuildPaths:
    """Resolved project and local trees copied into the pyz."""

    config_file: Path
    project: Path
    source: Path
    includes: tuple[Path, ...]
    source_paths: tuple[str, ...] = ()
    defaults_file: Path | None = None

    @property
    def uv_lock(self) -> Path:
        return self.project / "uv.lock"


def _inside_project(project: Path, raw: str, base: Path, key: str) -> Path:
    candidate = base / raw
    common = Path(os.path.commonpath((str(base.resolve()), str(project.resolve()))))
    cursor: Path = candidate
    while True:
        if cursor.is_symlink():
            raise ValueError(f"{key} path must not contain symlink: {cursor}")
        if cursor == common or cursor.parent == cursor:
            break
        cursor = cursor.parent
    resolved = candidate.resolve()
    if resolved != project and project not in resolved.parents:
        raise ConfigError(f"{key} {raw!r} must resolve inside the project {project}")
    if not resolved.exists():
        raise ConfigError(f"{key} {raw!r} does not exist: {resolved}")
    return resolved


def _selected_path(source: Path, selector: str) -> Path:
    relative = Path(selector)
    selected = source / relative
    cursor = source
    for component in relative.parts:
        cursor = cursor / component
        if cursor.is_symlink():
            raise ValueError(f"source_paths path must not contain symlink: {cursor}")
    resolved = selected.resolve()
    if resolved != source and source not in resolved.parents:
        raise ConfigError(f"source_paths path must resolve inside source: {selector!r}")
    if not resolved.exists():
        raise ConfigError(f"source_paths path does not exist: {selected}")
    return resolved


def resolve_paths(skill_dir: Path, config: WedgeConfig) -> BuildPaths:
    skill_dir = Path(skill_dir).resolve()
    project = (skill_dir / config.project).resolve()
    for required in ("pyproject.toml", "uv.lock"):
        if not (project / required).is_file():
            raise ConfigError(f"project {config.project!r} ({project}) has no {required}")
    source = _inside_project(project, config.source, skill_dir, "source")
    includes = tuple(_inside_project(project, item, skill_dir, "include") for item in config.include)
    if config.source_paths:
        if not source.is_dir():
            raise ConfigError("source_paths requires a source directory")
        for selector in config.source_paths:
            selector_path = Path(selector)
            if selector_path.is_absolute() or ".." in selector_path.parts or not selector_path.parts:
                raise ConfigError(f"source_paths path must be relative and inside source: {selector!r}")
            _ = _selected_path(source, selector)
    names = [path.name for path in (source, *includes)]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ConfigError(f"source and include entries share top-level names: {duplicates}")
    return BuildPaths(skill_dir / "wedge.toml", project, source, includes, config.source_paths, defaults_path(skill_dir))


def selected_files(source: Path, selectors: tuple[str, ...]) -> list[Path]:
    roots = [source / selector for selector in selectors] if selectors else [source]
    return [file for root in roots for file in _iter_files(root)]


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
    return [] if _is_excluded(root) else [root]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute_key(skill_dir: Path, config: WedgeConfig) -> str:
    paths = resolve_paths(skill_dir, config)
    inputs = {
        (file.relative_to(paths.project).as_posix(), _digest(file))
        for root in (*paths.includes,)
        for file in _iter_files(root)
    } | {
        (file.relative_to(paths.project).as_posix(), _digest(file))
        for file in selected_files(paths.source, paths.source_paths)
    }
    document: dict[str, object] = {
        "format_version": FORMAT_VERSION,
        "target_python": TARGET_PYTHON,
        "config": _digest(paths.config_file),
        "uv_lock": _digest(paths.uv_lock),
        "inputs": sorted(inputs),
    }
    # Only when present, so a skill without shared defaults keeps its key.
    if paths.defaults_file is not None:
        document["defaults"] = _digest(paths.defaults_file)
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
