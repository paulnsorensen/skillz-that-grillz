"""The own wedge targets of a run that edits no source, such as `--edit prose` (gh-695).

Each own `.pyz` stays frozen built bytes, so the plan reads only the target names of the skill's own `wedge.toml`.
It needs no shared defaults from a parent `wedge.toml` and no source path, so it also works on a copy of the skill.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import TypedDict, cast

from wedge import bundle_path

from skillz_experiments._cases import CodedError


def _names(path: Path) -> list[str]:
    """Return the target names of the `wedge.toml` at `path`: one top-level `name`, or one per `[[target]]` table."""
    try:
        table = cast(dict[str, object], tomllib.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError) as error:
        raise CodedError("wedge-config-invalid", f"{path} is not a valid wedge config: {error}") from None
    raw = table.get("target", [table])
    tables = cast(list[object], raw) if isinstance(raw, list) else []
    names = [cast(dict[str, object], item).get("name") for item in tables if isinstance(item, dict)]
    if not names or len(names) != len(tables) or not all(
            isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) for name in names):
        raise CodedError("wedge-config-invalid", f"{path} is not a valid wedge config: each target needs a safe name")
    return cast(list[str], names)


class FrozenTargets(TypedDict):
    """The `TargetPlan` fields of a plan that reads no wedge config: why each target is frozen, and its `.pyz` bytes."""

    reasons: dict[str, str]
    built: dict[str, bytes]


def frozen_targets(skill_root: Path, edit: str) -> FrozenTargets:
    """Return why each own target stays frozen under `edit`, and its built `.pyz` bytes; both are {} without a `wedge.toml`.

    Raise `wedge-config-invalid` when `wedge.toml` is not TOML or a target has no name, and `target-not-built`
    when the `.pyz` of a target is missing.
    """
    root = skill_root.resolve()
    if not (root / "wedge.toml").is_file():
        return {"reasons": {}, "built": {}}
    names = _names(root / "wedge.toml")
    built: dict[str, bytes] = {}
    for name in names:
        file, pyz = bundle_path(root, name), bundle_path(Path(), name).as_posix()
        if file.is_symlink() or not file.is_file():
            raise CodedError("target-not-built", f"{pyz} is missing; run `wedge bundle` for the skill, then run again")
        built[pyz] = file.read_bytes()
    return {"reasons": {name: f"--edit {edit} keeps every own target frozen" for name in names}, "built": built}
