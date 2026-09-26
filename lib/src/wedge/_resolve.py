"""Resolve fromargs' frozen, non-dev third-party closure from ``lib/fromargs/uv.lock``.

The skill runtime needs only what ``fromargs`` needs. Exporting from the
Wedge project at ``lib/`` would also pull in shiv's own dependencies
(``pip``, ``setuptools``, ``click``), which a skill never imports.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

from wedge._guard import ClosureEntry

_REQUIREMENT_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9.+!_-]+)(?:\s*;\s*(.+?))?\s*\\?$"
)


def export_requirements(repo_root: Path) -> str:
    """``uv export`` text for fromargs' frozen, non-dev dependency closure."""
    result = subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--project",
            str(Path(repo_root) / "lib" / "fromargs"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def parse_requirements(text: str) -> list[tuple[str, str, str | None]]:
    """Parse ``name==version[; marker]`` lines from a ``uv export`` document."""
    results = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _REQUIREMENT_RE.match(stripped)
        if match is None:
            continue
        name, version, marker = match.groups()
        results.append((name, version, marker))
    return results


def resolve_closure(repo_root: Path) -> list[ClosureEntry]:
    """The resolved closure as ``ClosureEntry`` objects, wheel filenames from
    ``lib/fromargs/uv.lock`` and markers from ``uv export``."""
    repo_root = Path(repo_root)
    requirements = parse_requirements(export_requirements(repo_root))
    lock = tomllib.loads((repo_root / "lib" / "fromargs" / "uv.lock").read_text())
    wheels_by_key = {
        (pkg["name"], pkg["version"]): pkg.get("wheels", []) for pkg in lock["package"]
    }
    entries: list[ClosureEntry] = []
    for name, version, marker in requirements:
        wheels = wheels_by_key.get((name, version))
        if not wheels:
            raise ValueError(f"{name}=={version}: no wheel entry in lib/fromargs/uv.lock")
        for wheel in wheels:
            filename = wheel["url"].rsplit("/", 1)[-1]
            entries.append(ClosureEntry(name=name, wheel=filename, marker=marker))
    return entries
