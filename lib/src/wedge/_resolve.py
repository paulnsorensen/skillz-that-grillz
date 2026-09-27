"""Resolve a project's frozen, non-dev third-party dependency closure."""

from __future__ import annotations

import re
import subprocess
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from wedge._guard import ClosureEntry, GuardError

_REQUIREMENT_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9.+!_-]+)(?:\s*;\s*(.+?))?\s*\\?$"
)


def export_requirements(project: Path, groups: Sequence[str] = ()) -> str:
    """Return the project's frozen, non-dev dependency closure plus ``groups``."""
    group_args = [arg for group in groups for arg in ("--group", group)]
    result = subprocess.run(
        [
            "uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--no-emit-local",
            *group_args, "--project", str(project),
        ],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def parse_requirements(text: str) -> list[tuple[str, str, str | None]]:
    """Parse ``name==version[; marker]`` lines from a ``uv export`` document.

    Raise ``GuardError`` on any other requirement line (an editable, path, or
    URL dependency): those carry no pinned wheel hash, so they cannot enter a
    reproducible closure. Vendor such a package through ``include`` instead.
    """
    results: list[tuple[str, str, str | None]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "--hash")):
            continue
        match = _REQUIREMENT_RE.match(stripped)
        if match is None:
            raise GuardError(f"unsupported requirement {stripped!r}: a wedge closure takes only pinned index wheels; list local packages under 'include' in wedge.toml")
        name, version, marker = match.groups()
        results.append((name, version, marker))
    return results


def resolve_closure(project: Path, requirements: str) -> list[ClosureEntry]:
    """Resolve requirement wheels from project/uv.lock."""
    lock = cast(dict[str, object], tomllib.loads((Path(project) / "uv.lock").read_text()))
    packages_raw = lock.get("package")
    if not isinstance(packages_raw, list):
        raise ValueError("uv.lock package must be a list")
    wheels_by_key: dict[tuple[str, str], list[dict[str, object]]] = {}
    for package_raw in cast(list[object], packages_raw):
        if not isinstance(package_raw, dict):
            continue
        package = cast(dict[str, object], package_raw)
        name, version, wheels = package.get("name"), package.get("version"), package.get("wheels")
        if not isinstance(name, str) or not isinstance(version, str) or not isinstance(wheels, list):
            continue
        typed_wheels = cast(list[object], wheels)
        wheels_by_key[(name, version)] = [
            cast(dict[str, object], item) for item in typed_wheels if isinstance(item, dict)
        ]
    entries: list[ClosureEntry] = []
    for name, version, marker in parse_requirements(requirements):
        wheels = wheels_by_key.get((name, version))
        if not wheels:
            raise GuardError(f"{name}=={version}: no wheel entry in {project}/uv.lock")
        for wheel in wheels:
            url = wheel.get("url")
            if not isinstance(url, str):
                raise ValueError(f"{name}=={version}: wheel URL is invalid")
            entries.append(ClosureEntry(name=name, wheel=url.rsplit("/", 1)[-1], marker=marker))
    return entries
