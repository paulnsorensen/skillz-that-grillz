"""Write and verify a skill's committed lock and launcher.

``wedge lock`` builds the .pyz once to learn its key and sha256, then writes
two files beside the skill's ``wedge.toml``: the lock
(``scripts/<name>.wedge.json``) and the launcher (``scripts/<name>``).
``wedge check`` verifies both without building.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from wedge._build import build
from wedge._config import ConfigError, load_config
from wedge._key import FORMAT_VERSION, compute_key
from wedge._launcher import LAUNCHER_SOURCE

_LAUNCHER_MODE = 0o755


@dataclass(frozen=True)
class LockData:
    """The contents of a skill's ``<name>.wedge.json``."""

    name: str
    key: str
    sha256: str
    release: str
    asset: str
    repo: str
    format: int

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "key": self.key,
            "sha256": self.sha256,
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


def lock(skill_dir: Path) -> LockData:
    """Build once, then write the lock and the launcher beside it."""
    skill_dir = Path(skill_dir)
    config = load_config(skill_dir)
    with tempfile.TemporaryDirectory(prefix="wedge-lock-") as tmp:
        result = build(skill_dir, Path(tmp))
        data = LockData(
            name=result.name,
            key=result.key,
            sha256=result.sha256,
            release="wedge",
            asset=f"{result.name}-{result.key[:12]}.pyz",
            repo=config.repo,
            format=FORMAT_VERSION,
        )
    lock_file = lock_path(skill_dir, config.name)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(json.dumps(data.to_dict(), indent=2, sort_keys=True) + "\n")
    launcher_file = launcher_path(skill_dir, config.name)
    launcher_file.write_text(LAUNCHER_SOURCE)
    launcher_file.chmod(_LAUNCHER_MODE)
    return data


def load_lock(skill_dir: Path, name: str) -> LockData:
    """Read a skill's already-written lock file."""
    path = lock_path(skill_dir, name)
    data = json.loads(path.read_text())
    return LockData(**data)


def check(skill_dirs: list[Path]) -> list[CheckIssue]:
    """Verify every skill's lock and launcher without building."""
    issues: list[CheckIssue] = []
    for skill_dir in skill_dirs:
        skill_dir = Path(skill_dir)
        try:
            config = load_config(skill_dir)
        except ConfigError as exc:
            issues.append(CheckIssue(skill_dir=str(skill_dir), reason=str(exc)))
            continue
        lock_file = lock_path(skill_dir, config.name)
        if not lock_file.is_file():
            issues.append(
                CheckIssue(skill_dir=str(skill_dir), reason=f"missing lock {lock_file}")
            )
            continue
        try:
            existing = load_lock(skill_dir, config.name)
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            issues.append(
                CheckIssue(skill_dir=str(skill_dir), reason=f"invalid lock {lock_file}: {exc}")
            )
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
        elif launcher_file.read_text() != LAUNCHER_SOURCE:
            issues.append(
                CheckIssue(
                    skill_dir=str(skill_dir),
                    reason=f"launcher {launcher_file} differs from the template",
                )
            )
    return issues
