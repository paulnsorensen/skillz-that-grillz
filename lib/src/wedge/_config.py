"""Parse a skill's ``wedge.toml``.

Every path in ``wedge.toml`` is relative to the skill directory. ``project``
names the directory that holds the ``pyproject.toml`` and ``uv.lock`` whose
non-dev closure the ``.pyz`` bundles. ``source`` and every ``include`` entry
must resolve inside that project directory.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class WedgeConfig:
    """One skill's ``wedge.toml``: what to build and where to publish it."""

    name: str
    entry: str
    source: str
    repo: str
    project: str = "."
    include: tuple[str, ...] = ()


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
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"cannot parse {path}: {exc}") from exc

    def text_key(key: str, default: str | None = None) -> str:
        value = data.get(key, default)
        if value is None:
            raise ConfigError(f"{path}: missing required key {key!r}")
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{path}: {key!r} must be a non-empty string")
        return value

    repo = text_key("repo")
    if not _REPO_RE.match(repo):
        raise ConfigError(f"{path}: 'repo' must be 'owner/name', got {repo!r}")
    include = data.get("include", [])
    if not isinstance(include, list) or not all(isinstance(item, str) and item for item in include):
        raise ConfigError(f"{path}: 'include' must be a list of non-empty strings")
    return WedgeConfig(
        name=text_key("name"),
        entry=text_key("entry"),
        source=text_key("source"),
        repo=repo,
        project=text_key("project", "."),
        include=tuple(include),
    )