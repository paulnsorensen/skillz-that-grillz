"""Write and verify a skill's committed lock and launcher.

``wedge lock`` builds the .pyz once to learn its key and content digest, then
writes two files beside the skill's ``wedge.toml``: the lock
(``scripts/<name>.wedge.json``) and the launcher (``scripts/<name>``). The
content digest does not depend on the zlib build, so a local lock matches the
compressed asset that the post-merge job builds. ``wedge check`` verifies both
files without building.
"""

from __future__ import annotations

import json
import stat
import re
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast
from pathlib import Path

from wedge._build import BuildResult, build_many
from wedge._config import ConfigError, WedgeConfig, load_config
from wedge._fanout import Outcome
from wedge._key import FORMAT_VERSION, compute_key
from wedge._launcher import LAUNCHER_SOURCE

_LAUNCHER_MODE = 0o755
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class LockData:
    """The contents of a skill's ``<name>.wedge.json``."""

    name: str
    key: str
    content_sha256: str
    release: str
    asset: str
    repo: str
    format: int

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "key": self.key,
            "content_sha256": self.content_sha256,
            "release": self.release,
            "asset": self.asset,
            "repo": self.repo,
            "format": self.format,
        }


@dataclass(frozen=True)
class CheckIssue:
    """One reason ``wedge check`` fails for a skill directory."""

    skill_dir: str
    reason: str


def lock_path(skill_dir: Path, name: str) -> Path:
    """Where a skill's lock file lives."""
    return Path(skill_dir) / "scripts" / f"{name}.wedge.json"


def launcher_path(skill_dir: Path, name: str) -> Path:
    """Where a skill's launcher script lives."""
    return Path(skill_dir) / "scripts" / name


def _write_lock(skill_dir: Path, result: BuildResult) -> LockData:
    """Write the lock and the launcher for a skill just built as ``result``."""
    config = load_config(skill_dir)
    data = LockData(
        name=result.name,
        key=result.key,
        content_sha256=result.content_sha256,
        release="wedge",
        asset=result.path.name,
        repo=config.repo,
        format=FORMAT_VERSION,
    )
    lock_file = lock_path(skill_dir, config.name)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    _ = lock_file.write_text(json.dumps(data.to_dict(), indent=2, sort_keys=True) + "\n")
    launcher_file = launcher_path(skill_dir, config.name)
    _ = launcher_file.write_text(LAUNCHER_SOURCE)
    _ = launcher_file.chmod(_LAUNCHER_MODE)
    return data


def lock_many(skill_dirs: Sequence[Path], *, jobs: int | None = None) -> list[Outcome[Path, LockData]]:
    """Build every skill (sharing site directories), then write each lock and launcher."""
    outcomes: list[Outcome[Path, LockData]] = []
    with tempfile.TemporaryDirectory(prefix="wedge-lock-") as tmp:
        for built in build_many(skill_dirs, Path(tmp), jobs=jobs):
            if built.error is not None or built.value is None:
                outcomes.append(Outcome(built.item, error=built.error))
                continue
            try:
                outcomes.append(Outcome(built.item, value=_write_lock(built.item, built.value)))
            except (ConfigError, OSError) as exc:
                outcomes.append(Outcome(built.item, error=str(exc)))
    return outcomes


def load_lock(skill_dir: Path, name: str) -> LockData:
    """Read and validate a skill's already-written lock file."""
    path = lock_path(skill_dir, name)
    raw: object = cast(object, json.loads(path.read_text()))
    if not isinstance(raw, dict):
        raise ValueError("lock must be a JSON object")
    data = cast(dict[object, object], raw)
    expected = {"name", "key", "content_sha256", "release", "asset", "repo", "format"}
    if set(data) != expected:
        raise ValueError("lock has invalid fields")
    values = {key: data.get(key) for key in expected}
    if any(not isinstance(values[key], str) for key in expected - {"format"}) or not isinstance(values["format"], int):
        raise ValueError("lock fields have invalid types")
    lock_data = LockData(
        name=cast(str, values["name"]), key=cast(str, values["key"]),
        content_sha256=cast(str, values["content_sha256"]), release=cast(str, values["release"]),
        asset=cast(str, values["asset"]), repo=cast(str, values["repo"]),
        format=values["format"],
    )
    if lock_data.name != name or not _HEX64.fullmatch(lock_data.key) or not _HEX64.fullmatch(lock_data.content_sha256):
        raise ValueError("lock name or digest is invalid")
    if lock_data.format != FORMAT_VERSION or lock_data.release != "wedge" or lock_data.asset != f"{name}-{lock_data.content_sha256[:12]}.pyz":
        raise ValueError("lock invariants are invalid")
    return lock_data


def check(skill_dirs: list[Path]) -> list[CheckIssue]:
    """Verify every skill's lock and launcher without building."""
    issues: list[CheckIssue] = []
    configs: dict[Path, WedgeConfig] = {}
    for raw_skill_dir in skill_dirs:
        skill_dir = Path(raw_skill_dir)
        try:
            configs[skill_dir] = load_config(skill_dir)
        except ConfigError as exc:
            issues.append(CheckIssue(skill_dir=str(skill_dir), reason=str(exc)))
    names = [config.name for config in configs.values()]
    duplicate_names = {name for name in names if names.count(name) > 1}
    for skill_dir, config in configs.items():
        if config.name in duplicate_names:
            issues.append(
                CheckIssue(skill_dir=str(skill_dir), reason=f"duplicate skill name {config.name!r}")
            )
        # The launcher downloads the release asset; an archive beside it is
        # a stale copy that the skill would ship and never run.
        for archive in sorted((skill_dir / "scripts").glob("*.pyz")):
            issues.append(CheckIssue(skill_dir=str(skill_dir), reason=f"archive {archive} beside the launcher"))
        lock_file = lock_path(skill_dir, config.name)
        if not lock_file.is_file():
            issues.append(
                CheckIssue(skill_dir=str(skill_dir), reason=f"missing lock {lock_file}")
            )
            continue
        try:
            existing = load_lock(skill_dir, config.name)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            issues.append(
                CheckIssue(skill_dir=str(skill_dir), reason=f"invalid lock {lock_file}: {exc}")
            )
            continue
        if existing.repo != config.repo:
            issues.append(CheckIssue(skill_dir=str(skill_dir), reason="lock repository differs from config"))
            continue
        try:
            key = compute_key(skill_dir, config)
        except ConfigError as exc:
            issues.append(CheckIssue(skill_dir=str(skill_dir), reason=str(exc)))
            continue
        if key != existing.key:
            issues.append(
                CheckIssue(
                    skill_dir=str(skill_dir),
                    reason=f"stale lock: key {existing.key} != current {key}",
                )
            )
        launcher_file = launcher_path(skill_dir, config.name)
        if not launcher_file.is_file():
            issues.append(
                CheckIssue(skill_dir=str(skill_dir), reason=f"missing launcher {launcher_file}")
            )
        elif launcher_file.stat().st_mode & stat.S_IXUSR == 0:
            issues.append(CheckIssue(skill_dir=str(skill_dir), reason=f"launcher {launcher_file} is not executable"))
        elif launcher_file.read_text() != LAUNCHER_SOURCE:
            issues.append(
                CheckIssue(
                    skill_dir=str(skill_dir),
                    reason=f"launcher {launcher_file} differs from the template",
                )
            )
    return issues
