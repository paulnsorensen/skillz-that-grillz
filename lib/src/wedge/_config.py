"""Parse a skill's ``wedge.toml``."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

DEFAULT_REPO = "paulnsorensen/skillz-that-grillz"


@dataclass(frozen=True)
class WedgeConfig:
    """One skill's ``wedge.toml``: what to build and where to publish it."""

    name: str
    entry: str
    source: str
    repo: str


class ConfigError(Exception):
    """``wedge.toml`` is missing, unreadable, or missing a required key."""


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
    name = values.get("name")
    entry = values.get("entry")
    source = values.get("source")
    repo = values.get("repo", DEFAULT_REPO)
    if not all(isinstance(value, str) and value.strip() for value in (name, entry, source, repo)):
        raise ConfigError(f"{path}: name, entry, source, and repo must be nonempty strings")
    if not _SAFE_NAME.fullmatch(cast(str, name)):
        raise ConfigError(f"{path}: name must be a safe filename component")
    if not _REPO.fullmatch(cast(str, repo)):
        raise ConfigError(f"{path}: repo must be owner/name")
    return WedgeConfig(name=cast(str, name), entry=cast(str, entry), source=cast(str, source), repo=cast(str, repo))
