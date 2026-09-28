"""Parse a skill's ``wedge.toml``.

Every path in ``wedge.toml`` is relative to the skill directory. A
``wedge.toml`` in the parent directory (the discovery root) holds defaults
for every skill beside it, so a repository of many skills over one package
states ``project``, ``source``, ``include``, ``groups``, and ``repo`` once. ``project``
names the directory that holds the ``pyproject.toml`` and ``uv.lock`` whose
non-dev closure the ``.pyz`` bundles; ``groups`` adds the project's named
dependency groups to that closure. ``source`` and every ``include`` entry
must resolve inside that project directory.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ALLOWED_KEYS = {"name", "entry", "source", "source_paths", "project", "repo", "include", "groups"}


@dataclass(frozen=True)
class WedgeConfig:
    """One skill's ``wedge.toml``: what to build and where to publish it."""

    name: str
    entry: str
    source: str
    repo: str
    source_paths: tuple[str, ...] = ()
    project: str = "."
    include: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()


class ConfigError(Exception):
    """``wedge.toml`` is missing, unreadable, invalid, or names a bad path."""


def defaults_path(skill_dir: Path) -> Path | None:
    """``<skill_dir>/../wedge.toml``: defaults shared by every skill under that root."""
    candidate = Path(skill_dir).resolve().parent / "wedge.toml"
    return candidate if candidate.is_file() else None


def _read_table(path: Path) -> dict[str, object]:
    try:
        text = path.read_text()
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    try:
        parsed: object = cast(object, tomllib.loads(text))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"cannot parse {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ConfigError(f"{path}: top level must be a table")
    return cast(dict[str, object], parsed)


def load_config(skill_dir: Path) -> WedgeConfig:
    """Read and validate ``<skill_dir>/wedge.toml`` over the root's shared defaults.

    A ``wedge.toml`` in the skill directory's parent supplies defaults for
    every key except ``name`` and ``entry``; the skill's own file wins. Paths
    in both files are relative to the skill directory.
    """
    path = Path(skill_dir) / "wedge.toml"
    own = _read_table(path)
    values = dict(own)
    sources: dict[str, Path] = dict.fromkeys(own, path)
    shared = defaults_path(skill_dir)
    if shared is not None:
        defaults = _read_table(shared)
        owned = sorted({"name", "entry"} & set(defaults))
        if owned:
            raise ConfigError(f"{shared}: shared defaults must not set {', '.join(owned)}")
        for key in defaults:
            if key not in own:
                sources[key] = shared
        values = {**defaults, **own}

    unknown = sorted(set(values) - _ALLOWED_KEYS)
    if unknown:
        bad_key = unknown[0]
        raise ConfigError(f"{sources.get(bad_key, path)}: unknown key {bad_key!r}")

    def text_key(key: str, default: str | None = None) -> str:
        value = values.get(key, default)
        if value is None:
            raise ConfigError(f"{path}: missing required key {key!r}")
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{sources.get(key, path)}: {key!r} must be a non-empty string")
        return value

    name = text_key("name")
    entry = text_key("entry")
    source = text_key("source")
    project = text_key("project", ".")
    repo = text_key("repo")
    if not _SAFE_NAME.fullmatch(name):
        raise ConfigError(f"{path}: name must be a safe filename component")
    if not _REPO.fullmatch(repo):
        raise ConfigError(f"{path}: 'repo' must be 'owner/name', got {repo!r}")
    def list_key(key: str) -> tuple[str, ...]:
        raw = values.get(key, [])
        if not isinstance(raw, list):
            raise ConfigError(f"{sources.get(key, path)}: {key!r} must be a list of non-empty strings")
        items = cast(list[object], raw)
        if not all(isinstance(item, str) and item.strip() for item in items):
            raise ConfigError(f"{sources.get(key, path)}: {key!r} must be a list of non-empty strings")
        return tuple(cast(list[str], items))

    source_paths = list_key("source_paths")
    if "source_paths" in values and not source_paths:
        raise ConfigError(f"{sources.get('source_paths', path)}: 'source_paths' must not be empty")
    for selector in source_paths:
        selector_path = Path(selector)
        if selector_path.is_absolute() or ".." in selector_path.parts:
            raise ConfigError(f"{sources.get('source_paths', path)}: source_paths must be relative")
    groups = list_key("groups")
    if "dev" in groups:
        source = sources.get("groups", path)
        raise ConfigError(f"{source}: 'groups' must not include 'dev'; move the CLI dependencies to another group")

    return WedgeConfig(
        name=name,
        entry=entry,
        source=source,
        source_paths=source_paths,
        repo=repo,
        project=project,
        include=list_key("include"),
        groups=groups,
    )
