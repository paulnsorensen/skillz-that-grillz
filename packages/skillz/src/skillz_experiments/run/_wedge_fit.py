"""Fit the editable wedge targets of a run into the edit limits.

The staged targets join in order. A target whose sources do not fit stays editable in part when it can. The part
holds, in order: the sources that an earlier editable target already offers, the own package of the skill, and
then each source that SKILL.md or the contract names. It stops at the first source that does not fit. The own
package is a source directory named after the skill directory, such as `src/pkg/skills/cook` for `skills/cook`.
Every other source stays a fixed file that each rebuild stages from the seed. So shared code is editable in part
only when the skill names it: an edit to it changes the archives of other skills, and the gate scores only this one.
"""
from __future__ import annotations

import re
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Generic, Protocol, TypeVar

from wedge import BuildPaths

from skillz_experiments._candidate import PACKAGE_LIMIT, WEDGE_PREFIX

# The editable wedge sources of a run together stay within this many characters. A reflection echoes every one.
WEDGE_EDIT_LIMIT = 100_000


class Staged(Protocol):
    """The view of an own target that the fit reads."""

    @property
    def name(self) -> str: ...
    @property
    def names(self) -> frozenset[str]: ...
    @property
    def sources(self) -> dict[str, str] | None: ...
    @property
    def fixed(self) -> dict[str, bytes]: ...
    @property
    def paths(self) -> BuildPaths: ...
    @property
    def repo(self) -> Path | None: ...
    @property
    def skill_root(self) -> Path: ...


T = TypeVar("T", bound=Staged)


@dataclass(frozen=True)
class Fitted(Generic[T]):
    """One target that stays editable: its editable sources, its fixed files, and why it is editable only in part."""

    target: T
    sources: dict[str, str]
    fixed: dict[str, bytes]
    partial: str


def _package(name: str) -> str:
    return name.replace("-", "_").lower()


def _own(skill: str, source: PurePosixPath, path: PurePosixPath) -> bool:
    """Return whether `path` lies in a directory at or below `source` that is named after `skill`."""
    if not path.is_relative_to(source) or path == source:
        return False
    folders = (source.name, *path.relative_to(source).parts[:-1])
    return any(_package(folder) == _package(skill) for folder in folders)


def _forms(name: str) -> Iterator[str]:
    """Yield the ways a text can name the source `name`: the full name, each path tail of two or more parts, and its module."""
    yield name
    parts = PurePosixPath(name.removeprefix(WEDGE_PREFIX)).parts
    for start in range(len(parts) - 1):
        tail = parts[start:]
        yield "/".join(tail)
        module = tail[:-1] if tail[-1] == "__init__.py" else (*tail[:-1], tail[-1].removesuffix(".py"))
        if len(module) > 1 and tail[-1].endswith(".py"):
            yield ".".join(module)


def _named(name: str, text: str) -> bool:
    return any(re.search(rf"(?<![\w./@-]){re.escape(form)}(?![\w/-])", text) for form in _forms(name))


def editable_part(skill: str, source: str, sources: Mapping[str, str], taken: Collection[str], room: int,
                  text: str) -> dict[str, str]:
    """Return the sources of a target that stay editable within `room` characters.

    `skill` is the skill directory name, and `source` is the repository-relative path of the target's wedge
    source. A name in `taken` costs nothing, because an earlier target already counts it. `text` is the SKILL.md
    text and the contract paths. Return {} when no own or named source with text fits, such as when only an empty
    `__init__.py` fits.
    """
    base = PurePosixPath(source)
    paths = {name: PurePosixPath(name.removeprefix(WEDGE_PREFIX)) for name in sorted(sources)}
    own = [name for name, path in paths.items() if name not in taken and _own(skill, base, path)]
    named = [name for name in paths if name not in taken and name not in own and _named(name, text)]
    added: dict[str, str] = {}
    for name in own + named:
        if sum(map(len, added.values())) + len(sources[name]) > room:
            break
        added[name] = sources[name]
    return {name: sources[name] for name in paths if name in taken} | added if any(added.values()) else {}


def _fit(stage: Sequence[T], blocked: frozenset[str], text: int, named: str) -> list[Fitted[T]]:
    """Return the targets of `stage` that fit, with their sources less the `blocked` names.

    A target left with no source is frozen. A target that does not fit keeps its `editable_part`. The first target
    with no part that fits, and every later one, stays frozen. Each other source stays in the build as a fixed file,
    and no later target edits it.
    """
    chosen: list[Fitted[T]] = []
    taken: dict[str, str] = {}
    pinned: set[str] = set()
    wedge = 0
    for target in stage:
        sources = target.sources or {}
        mine = {name: source for name, source in sources.items() if name not in blocked and name not in pinned}
        if not mine:
            continue
        size = sum(len(source) for name, source in mine.items() if name not in taken)
        room = min(PACKAGE_LIMIT - text, WEDGE_EDIT_LIMIT) - wedge
        partial = ""
        if size > room:
            limit = f"wedge edit limit of {WEDGE_EDIT_LIMIT}" if wedge + size > WEDGE_EDIT_LIMIT else f"package limit of {PACKAGE_LIMIT}"
            root = target.paths.source.relative_to(target.repo).as_posix() if target.repo is not None else ""
            whole, mine = size, editable_part(target.skill_root.name, root, mine, taken, room, named)
            if not mine:
                break
            size = sum(len(source) for name, source in mine.items() if name not in taken)
            partial = (f"{whole} characters of sources do not fit the {limit}, so only its own package and the files that "
                       + f"SKILL.md or the contract names are editable; {len(sources) - len(mine)} sources stay fixed")
        wedge += size
        taken |= mine
        pinned |= set(sources) - set(mine)
        fixed = target.fixed | {name: source.encode("utf-8") for name, source in sources.items() if name not in mine}
        chosen.append(Fitted(target, mine, fixed, partial))
    return chosen


def choose(targets: Sequence[Staged], stage: Sequence[T], nested: frozenset[str], text: int,
           named: str = "") -> list[Fitted[T]]:
    """Return the staged targets that stay editable. Drop each target that does not fit, then fit the rest again.

    `named` is the text that names sources for a target that fits only in part: SKILL.md and the contract paths.
    """
    dropped: set[str] = set()
    while True:
        staged = {target.name for target in stage}
        blocked = nested.union(*(target.names for target in targets if target.name not in staged or target.name in dropped))
        chosen = _fit(stage, blocked, text, named)
        live = [target.name for target in stage if target.name not in dropped]
        if [fit.target.name for fit in chosen] == live:
            return chosen
        dropped |= set(live) - {fit.target.name for fit in chosen}
