"""The reference answer of the `semver-next` fixture skill: the next version from `VERSION` and `commits.txt`.

The rules match `fixtures/semver-next/SKILL.md.fixture` one for one. The live smoke checks each case that the
`/skillz autoimprove` agent drafts against `next_version`, and it checks the exported CLI the same way.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

HEADER = re.compile(r"(?P<type>[A-Za-z]+)(?:\((?P<scope>[^()\n]+)\))?(?P<bang>!)?: (?P<description>\S.*)")
VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-rc\.(0|[1-9]\d*))?")
BREAKING = ("BREAKING CHANGE: ", "BREAKING-CHANGE: ")
IMPACT = {"feat": "minor", "fix": "patch", "perf": "patch"}
RANK = ("none", "patch", "minor", "major")
ZERO_MAJOR = {"major": "minor", "minor": "patch", "patch": "patch", "none": "none"}


@dataclass
class _Commit:
    kind: str
    header: str
    description: str
    breaking: bool
    matched: bool = False
    cancelled_by: list[_Commit] = field(default_factory=list["_Commit"])

    def live(self) -> bool:
        """Return whether no live revert cancels this commit. A cancelling revert is always later in the log."""
        return not any(revert.live() for revert in self.cancelled_by)


def _blocks(text: str) -> list[list[str]]:
    """Split `commits.txt` on lines that hold only `---`. Drop the leading and trailing blank lines of each commit.

    Lines keep their text exactly, so the prefix test of rule 3 and the revert match of rule 4 see every character.
    """
    blocks: list[list[str]] = [[]]
    for line in text.splitlines():
        if line == "---":
            blocks.append([])
        else:
            blocks[-1].append(line)
    trimmed: list[list[str]] = []
    for block in blocks:
        while block and not block[0].strip():
            _ = block.pop(0)
        while block and not block[-1].strip():
            _ = block.pop()
        if block:
            trimmed.append(block)
    return trimmed


def commit_count(text: str) -> int:
    """Return the number of commits in the text of `commits.txt`, conventional or not."""
    return len(_blocks(text))


def _parse(block: list[str]) -> _Commit | None:
    """Return the conventional commit of `block`, or None when its header does not match the grammar."""
    found = HEADER.fullmatch(block[0])
    if found is None:
        return None
    breaking = found["bang"] is not None or any(line.startswith(BREAKING) for line in block[1:])
    return _Commit(found["type"].lower(), block[0], found["description"], breaking)


def _impact(commit: _Commit, revert: bool) -> str:
    if commit.breaking:
        return "major"
    return "patch" if revert else IMPACT.get(commit.kind, "none")


def bump_of(commits: str) -> str:
    """Return the largest impact of the live commits in `commits`: `none`, `patch`, `minor`, or `major`.

    A revert matches from the commits before it. Cancelling a revert makes the commit it cancelled live again,
    unless another live revert cancels it too.
    """
    seen: list[_Commit] = []
    for block in _blocks(commits):
        commit = _parse(block)
        if commit is None:
            continue
        if commit.kind == "revert":
            target = next((earlier for earlier in reversed(seen)
                           if earlier.live() and earlier.header == commit.description), None)
            if target is not None:
                target.cancelled_by.append(commit)
                commit.matched = True
        seen.append(commit)
    impacts = ["none" if commit.matched else _impact(commit, revert=commit.kind == "revert")
               for commit in seen if commit.live()]
    return max(impacts, key=RANK.index, default="none")


def next_version(version: str, commits: str) -> dict[str, str]:
    """Return `{"bump": ..., "next": ...}` for the text of `VERSION` and of `commits.txt`.

    Raise ValueError when `version` is not `MAJOR.MINOR.PATCH` or `MAJOR.MINOR.PATCH-rc.N`.
    """
    found = VERSION.fullmatch(version.strip())
    if found is None:
        raise ValueError(f"VERSION is not a semantic version: {version.strip()!r}")
    major, minor, patch = (int(part) for part in found.groups()[:3])
    bump = bump_of(commits)
    if bump == "none":
        return {"bump": "none", "next": version.strip()}
    if found[4] is not None:
        return {"bump": "prerelease", "next": f"{major}.{minor}.{patch}-rc.{int(found[4]) + 1}"}
    if major == 0:
        bump = ZERO_MAJOR[bump]
    following = {"major": (major + 1, 0, 0), "minor": (major, minor + 1, 0), "patch": (major, minor, patch + 1)}[bump]
    return {"bump": bump, "next": ".".join(str(part) for part in following)}
