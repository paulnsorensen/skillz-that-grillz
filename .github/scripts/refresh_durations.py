#!/usr/bin/env python3
"""Refresh the committed skillz test durations from a merged CI artifact (quality-ratchets ADR-004, ADR-006).

    refresh_durations.py <artifact> [--github-output <file>]

`<artifact>` is the `test-durations` file of a green `validate` push run on main. When its test ids or seconds
differ from `packages/skillz/.test_durations`, the script copies it over that file. It then runs the time
ratchet's `tighten` on `pyproject.toml` with the new durations. It prints `changed: <paths>` or `no change`.
`--github-output` appends `changed=true` or `changed=false` to that file for the workflow.
The script writes no file when the artifact or the budget is bad.

Exit codes: 0 refreshed or no change, 1 the artifact is missing, malformed, or empty, 2 input error.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import check_test_durations as ratchet

ROOT = Path(__file__).resolve().parents[2]
DURATIONS = "packages/skillz/.test_durations"
USAGE = "usage: refresh_durations.py <artifact> [--github-output <file>]"


def _committed(path: Path) -> dict[str, float] | None:
    """Return the committed durations, or `None` when the file is missing, malformed, or empty."""
    try:
        return ratchet.load_durations(path)
    except ratchet.DataError:
        return None


def refresh(root: Path, artifact: Path) -> list[str]:
    """Apply the artifact to the tree at `root`; return the changed paths, relative to `root`."""
    durations = ratchet.load_durations(artifact)
    budget = ratchet.load_budget(root)
    pyproject = root / ratchet.PYPROJECT
    text = pyproject.read_text(encoding="utf-8")
    # Compute every new text before the first write, so a bad input leaves the tree as it was.
    tightened = ratchet.tighten(text, budget, ratchet.per_file(durations))
    target = root / DURATIONS
    writes: dict[str, tuple[Path, str]] = {}
    if _committed(target) != durations:
        writes[DURATIONS] = (target, artifact.read_text(encoding="utf-8"))
    if tightened != text:
        writes[ratchet.PYPROJECT] = (pyproject, tightened)
    for path, content in writes.values():
        _ = path.write_text(content, encoding="utf-8")
    return list(writes)


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) not in {1, 3} or (len(args) == 3 and args[1] != "--github-output"):
        print(USAGE, file=sys.stderr)
        return 2
    try:
        changed = refresh(root, Path(args[0]))
    except ratchet.DataError as error:
        print(error)
        return 1
    except (ratchet.ConfigError, OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        print(error, file=sys.stderr)
        return 2
    print(f"changed: {', '.join(changed)}" if changed else "no change")
    if len(args) == 3:
        with Path(args[2]).open("a", encoding="utf-8") as stream:
            _ = stream.write(f"changed={'true' if changed else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
