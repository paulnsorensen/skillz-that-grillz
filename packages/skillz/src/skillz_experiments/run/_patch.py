"""Build the patch of a winner. `export` writes it and the replay applies it, so one code path builds both.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

from skillz_experiments._candidate import NEW_CLI, WEDGE_PREFIX, candidate_files, is_skill_file
from skillz_experiments._cases import CodedError, digest, mapping, relative, repo_root
from skillz_experiments._new_cli import NewCli, expand_files, parse_value

SKILL_PATCH = "candidate.patch"
WEDGE_PATCH = "wedge-sources.patch"


@dataclass(frozen=True)
class Patch:
    """The patches of a winner and what they must produce.

    `seed` and `candidate` hold the text files before and after the patches, without `@new-cli`.
    `candidate` also holds the files that a `@new-cli` value adds or changes. `expanded` names those files.
    """

    skill: str
    wedge: str | None
    cli: NewCli | None
    seed: dict[str, str]
    candidate: dict[str, str]
    expanded: frozenset[str]

    @property
    def files(self) -> dict[str, str]:
        """Return the patch texts by file name. The wedge patch is present only when a wedge source changed."""
        return {SKILL_PATCH: self.skill, **({WEDGE_PATCH: self.wedge} if self.wedge else {})}

    @property
    def digest(self) -> str:
        return digest(self.files)


def _repo_path(rest: str, repo: Path | None) -> str:
    """Return `rest` when it names a path inside the repository root. Else raise `export-path-escapes`."""
    try:
        _ = relative(rest)
        inside = repo is not None and (repo / rest).resolve().is_relative_to(repo)
    except ValueError:
        inside = False
    if not inside:
        raise CodedError("export-path-escapes", f"the wedge source {rest} is not a path inside the repository root; "
                         + "no patch was written")
    return rest


def _lines(text: str | None) -> list[str]:
    """Split `text` after each line feed and nowhere else, so that `git apply` reproduces it exactly."""
    return re.findall(r"[^\n]*\n|[^\n]+", text or "")


def _patch_lines(name: str, old: str | None, new: str | None, *, git: bool) -> list[str]:
    """Return the diff of one file, with paths `a/NAME` and `b/NAME` and `/dev/null` for an added or deleted file.

    With `git`, an added or deleted file also gets git headers, so an empty one survives `git apply` from the repository root.
    Without it the diff is plain: `patch -p1` and `git apply` read the paths from the current directory.
    """
    head: list[str] = []
    if git and (old is None or new is None):
        head = [f"diff --git a/{name} b/{name}\n", "new file mode 100644\n" if old is None else "deleted file mode 100644\n"]
    body = list(difflib.unified_diff(_lines(old), _lines(new),
                                     fromfile="/dev/null" if old is None else "a/" + name,
                                     tofile="/dev/null" if new is None else "b/" + name))
    return head + body


def _patch_text(lines: list[str]) -> str:
    """Join the patch `lines`. Add the standard no-newline marker after a last line that lacks a line feed."""
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)


def build_patch(first: object, last: object, target_root: Path) -> Patch:
    """Build the patches that turn the seed files `first` into the winner files `last`.

    A `@new-cli` value expands to the skill files it adds or changes, as the patch carries them.
    Raise `export-path-escapes` for a wedge source outside the repository root of `target_root`.
    """
    before, after = mapping(first), mapping(last)
    repo = repo_root(target_root)
    repo_names = {name: _repo_path(name.removeprefix(WEDGE_PREFIX), repo) for name in sorted(before.keys() | after.keys())
                  if name.startswith(WEDGE_PREFIX) and before.get(name) != after.get(name)}
    seed, candidate = dict(candidate_files(first)), dict(candidate_files(last))
    _ = seed.pop(NEW_CLI, None)
    value = candidate.pop(NEW_CLI, "")
    cli = parse_value(value) if value.strip() else None
    expanded: dict[str, str] = {}
    if cli is not None:
        expanded = expand_files(target_root, seed, cli)
        candidate |= expanded
    skill_lines: list[str] = []
    wedge_lines: list[str] = []
    for name in sorted(seed.keys() | candidate.keys()):
        old, new = seed.get(name), candidate.get(name)
        if old == new:
            continue
        if is_skill_file(name):
            skill_lines.extend(_patch_lines(name, old, new, git=False))
        else:
            wedge_lines.extend(_patch_lines(repo_names[name], old, new, git=True))
    return Patch(_patch_text(skill_lines), _patch_text(wedge_lines) if wedge_lines else None, cli, seed, candidate,
                 frozenset(expanded))
