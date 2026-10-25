"""Guard checks for the resolved third-party dependency closure.

``wedge`` refuses to build a ``.pyz`` whose closure could differ by platform:
a wheel whose tags are not ``py3-none-any`` (a compressed ``py2.py3`` python
tag counts), or a dependency edge whose marker tests
the platform (``sys_platform``, ``platform_system``, and similar). A pure,
marker-free closure is what makes the same key give a byte-identical build
on any machine.
"""

from __future__ import annotations

from dataclasses import dataclass

_PLATFORM_MARKER_NAMES = (
    "sys_platform",
    "platform_system",
    "platform_machine",
    "platform_release",
    "platform_version",
    "os_name",
    "platform_python_implementation",
)


@dataclass(frozen=True)
class ClosureEntry:
    """One resolved package: its wheel filename and dependency-edge marker."""

    name: str
    wheel: str
    marker: str | None = None


class GuardError(Exception):
    """A closure entry fails the pure-wheel or no-platform-marker guard."""


def guard_closure(entries: list[ClosureEntry]) -> None:
    """Raise ``GuardError`` on a platform marker or a non-``py3-none-any`` wheel."""
    for entry in entries:
        if entry.marker and any(name in entry.marker for name in _PLATFORM_MARKER_NAMES):
            raise GuardError(
                f"{entry.name}: platform marker {entry.marker!r} is not allowed in a wedge closure"
            )
        if not _is_pure_py3(entry.wheel):
            raise GuardError(f"{entry.name}: wheel {entry.wheel!r} is not py3-none-any")


def _is_pure_py3(wheel: str) -> bool:
    # PEP 425: the last three dash fields are the python, abi, and platform
    # tags, and a dotted field is a compressed set (``py2.py3`` covers ``py3``).
    stem = wheel.removesuffix(".whl")
    parts = stem.split("-")
    if stem == wheel or len(parts) < 5:
        return False
    python, abi, platform = parts[-3:]
    return "py3" in python.split(".") and abi == "none" and platform == "any"
