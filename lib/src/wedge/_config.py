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
_TARGET_KEYS = {"name", "entry", "source", "source_paths", "include", "groups"}


def validate_name(name: str) -> None:
    """Raise ``ConfigError`` unless ``name`` is a safe filename component."""
    if not _SAFE_NAME.fullmatch(name):
        raise ConfigError(f"name must be a safe filename component: {name!r}")


@dataclass(frozen=True)
class WedgeConfig:
    """One build target: what to build and where to publish it.

    A single-target ``wedge.toml`` yields one config. Each ``[[target]]`` table
    yields one config with ``multi`` set. The constructor validates ``name`` and
    ``repo``, so a config made in memory passes the same checks as a parsed one.
    """

    name: str
    entry: str
    source: str
    repo: str
    source_paths: tuple[str, ...] = ()
    project: str = "."
    include: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    multi: bool = False

    def __post_init__(self) -> None:
        validate_name(self.name)
        if not _REPO.fullmatch(self.repo):
            raise ConfigError(f"'repo' must be 'owner/name', got {self.repo!r}")


class ConfigError(Exception):
    """``wedge.toml`` is missing, unreadable, invalid, or names a bad path."""


def defaults_path(skill_dir: Path) -> Path | None:
    """``<skill_dir>/../wedge.toml``: defaults shared by every skill under that root.

    A parent that holds a ``SKILL.md`` is a skill, so its ``wedge.toml`` is
    that skill's own CLI. A CLI directory nested inside a skill reads no defaults.
    """
    parent = Path(skill_dir).resolve().parent
    candidate = parent / "wedge.toml"
    if not candidate.is_file() or (parent / "SKILL.md").is_file():
        return None
    return candidate


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


def _merged_values(skill_dir: Path) -> tuple[Path, dict[str, object], dict[str, Path]]:
    """The skill's ``wedge.toml`` over the root's shared defaults, with each key's file."""
    path = Path(skill_dir) / "wedge.toml"
    own = _read_table(path)
    values = dict(own)
    sources: dict[str, Path] = dict.fromkeys(own, path)
    shared = defaults_path(skill_dir)
    if shared is not None:
        defaults = _read_table(shared)
        owned = sorted({"name", "entry", "target"} & set(defaults))
        if owned:
            raise ConfigError(f"{shared}: shared defaults must not set {', '.join(owned)}")
        for key in defaults:
            if key not in own:
                sources[key] = shared
        values = {**defaults, **own}
    return path, values, sources


def multi_target_message(skill_dir: Path) -> str:
    """The one error text for a command that cannot take ``[[target]]`` tables."""
    return f"{Path(skill_dir) / 'wedge.toml'}: [[target]] tables work only with build and bundle"


def load_config(skill_dir: Path) -> WedgeConfig:
    """Read and validate a single-target ``<skill_dir>/wedge.toml`` over the root's defaults.

    A ``wedge.toml`` in the skill directory's parent supplies defaults for
    every key except ``name`` and ``entry``; the skill's own file wins. Paths
    in both files are relative to the skill directory. A file with ``[[target]]``
    tables belongs to ``load_targets``; ``lock``, ``check``, and release-mode
    ``publish`` reject it here.
    """
    targets = load_targets(skill_dir)
    if targets[0].multi:
        raise ConfigError(multi_target_message(skill_dir))
    return targets[0]


def load_targets(skill_dir: Path) -> tuple[WedgeConfig, ...]:
    """Every target of ``<skill_dir>/wedge.toml``: one, or one per ``[[target]]`` table.

    The single-target form keeps ``name`` and ``entry`` at the top level. The
    multi-target form moves them into each table; top-level keys other than
    ``name`` and ``entry`` are defaults that a table can override.
    """
    path, values, sources = _merged_values(skill_dir)
    raw_targets = values.pop("target", None)
    if raw_targets is None:
        return (_make_config(path, values, sources, multi=False),)
    mixed = sorted({"name", "entry"} & set(values))
    if mixed:
        raise ConfigError(f"{path}: top-level {', '.join(mixed)} cannot combine with [[target]] tables")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ConfigError(f"{path}: 'target' must be a non-empty list of tables")
    configs: list[WedgeConfig] = []
    for index, raw in enumerate(cast(list[object], raw_targets), start=1):
        if not isinstance(raw, dict):
            raise ConfigError(f"{path}: each [[target]] must be a table")
        table = cast(dict[str, object], raw)
        label_name = table.get("name")
        label = f"[[target]] #{index}" + (f" ({label_name})" if isinstance(label_name, str) else "")
        try:
            bad = sorted(set(table) - _TARGET_KEYS)
            if bad:
                raise ConfigError(f"{path}: unknown key {bad[0]!r} in [[target]]")
            configs.append(
                _make_config(path, {**values, **table}, {**sources, **dict.fromkeys(table, path)}, multi=True)
            )
        except ConfigError as exc:
            raise ConfigError(f"{label}: {exc}") from exc
    names = [config.name for config in configs]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ConfigError(f"{path}: duplicate target names {duplicates}")
    return tuple(configs)


def _make_config(
    path: Path, values: dict[str, object], sources: dict[str, Path], *, multi: bool
) -> WedgeConfig:
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

    try:
        return WedgeConfig(
            name=name,
            entry=entry,
            source=source,
            source_paths=source_paths,
            repo=repo,
            project=project,
            include=list_key("include"),
            groups=groups,
            multi=multi,
        )
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
