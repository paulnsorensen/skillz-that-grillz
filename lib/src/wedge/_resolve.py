"""Resolve a project's frozen, non-dev third-party closure from its ``uv.lock``."""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

from wedge._guard import ClosureEntry, GuardError

_REQUIREMENT_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9.+!_-]+)(?:\s*;\s*(.+?))?\s*\\?$"
)


def export_requirements(project: Path) -> str:
    """``uv export`` text for the frozen, non-dev, non-local closure."""
    result = subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--no-emit-local",
            "--no-emit-package",
            "shiv",
            "--project",
            str(project),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def parse_requirements(text: str) -> list[tuple[str, str, str | None]]:
    """Parse ``name==version[; marker]`` lines from a ``uv export`` document.

    Raise ``GuardError`` on any other requirement line (an editable, path, or
    URL dependency): those carry no pinned wheel hash, so they cannot enter a
    reproducible closure. Vendor such a package through ``include`` instead.
    """
    results = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "--hash")):
            continue
        match = _REQUIREMENT_RE.match(stripped)
        if match is None:
            raise GuardError(
                f"unsupported requirement {stripped!r}: a wedge closure takes only "
                "pinned index wheels; list local packages under 'include' in wedge.toml"
            )
        name, version, marker = match.groups()
        results.append((name, version, marker))
    return results


def resolve_closure(project: Path, requirements: str) -> list[ClosureEntry]:
    """The closure as ``ClosureEntry`` objects: wheel filenames from
    ``<project>/uv.lock`` and markers from the ``uv export`` text."""
    lock = tomllib.loads((Path(project) / "uv.lock").read_text())
    wheels_by_key = {
        (pkg["name"], pkg["version"]): pkg.get("wheels", []) for pkg in lock.get("package", [])
    }
    entries: list[ClosureEntry] = []
    for name, version, marker in parse_requirements(requirements):
        wheels = wheels_by_key.get((name, version))
        if not wheels:
            raise GuardError(f"{name}=={version}: no wheel entry in {project}/uv.lock")
        for wheel in wheels:
            filename = wheel["url"].rsplit("/", 1)[-1]
            entries.append(ClosureEntry(name=name, wheel=filename, marker=marker))
    return entries
