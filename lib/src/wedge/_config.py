"""Parse a skill's ``wedge.toml``."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

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
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"cannot parse {path}: {exc}") from exc
    try:
        name = str(data["name"])
        entry = str(data["entry"])
        source = str(data["source"])
    except KeyError as exc:
        raise ConfigError(f"{path}: missing required key {exc}") from exc
    repo = str(data.get("repo", DEFAULT_REPO))
    return WedgeConfig(name=name, entry=entry, source=source, repo=repo)
