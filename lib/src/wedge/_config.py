"""Parse a skill's ``wedge.toml``.

Every path in ``wedge.toml`` is relative to the skill directory. ``project``
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


@dataclass(frozen=True)
class WedgeConfig:
    """One skill's ``wedge.toml``: what to build and where to publish it."""

    name: str
    entry: str
    source: str
    repo: str
    project: str = "."
    include: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()


class ConfigError(Exception):
    """``wedge.toml`` is missing, unreadable, invalid, or names a bad path."""


def load_config(skill_dir: Path) -> WedgeConfig:
    """Read and validate ``<skill_dir>/wedge.toml``."""
    path = Path(skill_dir) / "wedge.toml"
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
    values = cast(dict[str, object], parsed)

    def text_key(key: str, default: str | None = None) -> str:
        value = values.get(key, default)
        if value is None:
            raise ConfigError(f"{path}: missing required key {key!r}")
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{path}: {key!r} must be a non-empty string")
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
            raise ConfigError(f"{path}: {key!r} must be a list of non-empty strings")
        items = cast(list[object], raw)
        if not all(isinstance(item, str) and item.strip() for item in items):
            raise ConfigError(f"{path}: {key!r} must be a list of non-empty strings")
        return tuple(cast(list[str], items))

    return WedgeConfig(
        name=name,
        entry=entry,
        source=source,
        repo=repo,
        project=project,
        include=list_key("include"),
        groups=list_key("groups"),
    )
