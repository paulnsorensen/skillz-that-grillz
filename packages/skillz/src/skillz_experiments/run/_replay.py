"""The clean copy of a replay: build it, apply the exported patches with `git apply`, and compare it with the winner.
"""
from __future__ import annotations

import math
import os
import shutil
import statistics
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from skillz_experiments._candidate import NEW_CLI, WEDGE_PREFIX, Candidate, candidate_files, is_skill_file
from skillz_experiments._cases import CodedError, relative
from skillz_experiments.run._patch import SKILL_PATCH, WEDGE_PATCH, Patch

STDERR_LIMIT = 2000

DRIFT_SE = 2.0


def prepare_copy(directory: Path, seed: Candidate, patch: Patch) -> Path:
    """Replace `directory` with a clean copy: `skill/` holds the frozen seed, `repo/` its wedge sources when a wedge patch exists.

    The patch files sit next to both, so no patch changes them. The directory has mode 0700.
    """
    if directory.is_symlink():
        directory.unlink()
    elif directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(mode=0o700)
    (directory / "skill").mkdir()
    seed.materialize(directory / "skill")
    _ = (directory / SKILL_PATCH).write_bytes(patch.skill.encode("utf-8"))
    if patch.wedge is not None:
        _ = (directory / WEDGE_PATCH).write_bytes(patch.wedge.encode("utf-8"))
        (directory / "repo").mkdir()
        for name, text in seed.files.items():
            if name.startswith(WEDGE_PREFIX):
                path = directory / "repo" / relative(name.removeprefix(WEDGE_PREFIX))
                path.parent.mkdir(parents=True, exist_ok=True)
                _ = path.write_bytes(text.encode("utf-8"))
    return directory


def _git(arguments: list[str], cwd: Path, ceiling: Path) -> str | None:
    """Run git in `cwd`. Return the bounded stderr when it fails, else None. Raise when git does not run.

    The ceiling keeps git out of any enclosing repository, so paths never resolve against one.
    Git reads no global or system configuration and no global or system attributes file,
    so a user setting such as `eol=crlf` cannot change the bytes that it writes.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_CEILING_DIRECTORIES"] = str(ceiling)
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_ATTR_NOSYSTEM"] = "1"
    command = ["git", "-c", f"core.attributesFile={os.devnull}", *arguments]
    try:
        done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False, timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        raise CodedError("replay-git-unavailable", f"git did not run: {str(error)[:STDERR_LIMIT]}; resume the run") from error
    return None if done.returncode == 0 else (done.stderr.strip() or f"git exited with {done.returncode}")[:STDERR_LIMIT]


def apply_patches(directory: Path, patch: Patch) -> str | None:
    """Apply the patches to the copy with the documented user command. Return the git stderr of the first failure.

    The skill patch applies from `skill/` and the wedge patch from `repo/`. An empty skill patch has nothing to apply.
    Git rejects a patch that does not apply before it changes the tree. A missing git or a timeout raises `replay-git-unavailable`.
    """
    root = directory.resolve()
    ceiling = root.parent
    steps = [(SKILL_PATCH, root / "skill")] if patch.skill.strip() else []
    if patch.wedge is not None:
        steps.append((WEDGE_PATCH, root / "repo"))
    for name, cwd in steps:
        failure = _git(["apply", str(root / name)], cwd, ceiling)
        if failure is not None:
            return failure
    return None


def _tree(root: Path) -> dict[str, bytes | None]:
    """Return the bytes of every file under `root` by posix name. A symlink maps to None."""
    found: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            found[path.relative_to(root).as_posix()] = None
        elif path.is_file():
            found[path.relative_to(root).as_posix()] = path.read_bytes()
    return found


def _mismatch(found: Mapping[str, bytes | None], wanted: Mapping[str, bytes], prefix: str) -> list[str]:
    return [prefix + name for name in sorted(found.keys() | wanted.keys())
            if name not in found or name not in wanted or found[name] is None or found[name] != wanted[name]]


def differences(directory: Path, seed: Candidate, patch: Patch) -> list[str]:
    """Name each file whose bytes differ from what the evaluated winner needs. An empty list means the copy is exact.

    The skill copy holds the expected skill files and the unchanged frozen seed files, and no other file.
    The wedge copy holds the expected wedge sources. A file that a `@new-cli` value adds or changes
    comes from the patch alone, so the recorded winner does not hold it.
    """
    expected = {name: text.encode("utf-8") for name, text in patch.candidate.items() if is_skill_file(name)}
    frozen = {name: data for name, data in seed.frozen.items() if not name.startswith(WEDGE_PREFIX)}
    found = _mismatch(_tree(directory / "skill"), expected | frozen, "")
    if patch.wedge is not None:
        wedge = {name.removeprefix(WEDGE_PREFIX): text.encode("utf-8") for name, text in patch.candidate.items()
                 if name.startswith(WEDGE_PREFIX)}
        found += _mismatch(_tree(directory / "repo"), wedge, WEDGE_PREFIX)
    return sorted(set(found))


def components(directory: Path, seed: Candidate, patch: Patch, winner: object) -> dict[str, str]:
    """Return the editable components of the patched copy. `@new-cli` comes from the record, because a patch cannot carry it."""
    found: dict[str, str] = {}
    for name in seed.editable:
        if name == NEW_CLI:
            found[name] = candidate_files(winner).get(NEW_CLI, "")
        elif name.startswith(WEDGE_PREFIX) and patch.wedge is None:
            found[name] = seed.files[name]
        else:
            root = directory / ("repo" if name.startswith(WEDGE_PREFIX) else "skill")
            found[name] = (root / relative(name.removeprefix(WEDGE_PREFIX))).read_bytes().decode("utf-8")
    return found


def drift(deltas: Sequence[float], tolerance: float) -> tuple[float, float, bool]:
    """Return the mean of `deltas`, its standard error, and whether the replay drifts below the winner.

    The standard error follows the repeat-mean rule of the gate: the sample stdev over the square root of the count,
    and 0 for one value. This module computes it again, because the `gate/` package stays untouched to keep the scoring digest stable.
    Drift is `mean + 2 * SE < -tolerance`.
    """
    mean = math.fsum(deltas) / len(deltas)
    se = statistics.stdev(deltas) / math.sqrt(len(deltas)) if len(deltas) > 1 else 0.0
    return mean, se, mean + DRIFT_SE * se < -tolerance
