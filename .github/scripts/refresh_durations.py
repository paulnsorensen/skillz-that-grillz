#!/usr/bin/env python3
"""Refresh the committed skillz test durations from merged CI artifacts (quality-ratchets ADR-004, ADR-006).

    refresh_durations.py <artifact> [<artifact> ...] [--github-output <file>]

Each `<artifact>` is the `test-durations` file of a green `validate` push run on main, newest first. Per-file wall
time varies from run to run, so the script combines the runs: it keeps the test ids of the newest run and gives
each id the maximum of its seconds in all the runs. When that map differs from `packages/skillz/.test_durations`,
the script writes it there, so the shard split reads a conservative basis. With at least `TIGHTEN_RUNS` artifacts,
it then runs the time ratchet's `tighten` on `pyproject.toml` with the same map; with fewer, it does not tighten,
because one fast run is not a stable measure. It prints `changed: <paths>` or `no change`, then a note when it
does not tighten. `--github-output` appends `changed=true` or `changed=false` to that file for the workflow.
The script writes no file when an artifact or the budget is bad.

Exit codes: 0 refreshed or no change, 1 an artifact is missing, malformed, or empty, 2 input error.
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import check_test_durations as ratchet

ROOT = Path(__file__).resolve().parents[2]
DURATIONS = "packages/skillz/.test_durations"
# `tighten` needs this many runs; the workflow downloads the same number.
TIGHTEN_RUNS = 3
USAGE = "usage: refresh_durations.py <artifact> [<artifact> ...] [--github-output <file>]"


def _committed(path: Path) -> dict[str, float] | None:
    """Return the committed durations, or `None` when the file is missing, malformed, or empty."""
    try:
        return ratchet.load_durations(path)
    except ratchet.DataError:
        return None


def combine(artifacts: list[Path]) -> dict[str, float]:
    """Return the test ids of the newest artifact, each with its maximum seconds over all the artifacts."""
    newest, *older = (ratchet.load_durations(path) for path in artifacts)
    for run in older:
        for test, seconds in run.items():
            if test in newest:
                newest[test] = max(newest[test], seconds)
    return dict(sorted(newest.items()))


def refresh(root: Path, artifacts: list[Path]) -> tuple[list[str], bool]:
    """Apply the artifacts to the tree at `root`; return the changed paths, relative to `root`, and if it tightened."""
    durations = combine(artifacts)
    budget = ratchet.load_budget(root)
    pyproject = root / ratchet.PYPROJECT
    text = pyproject.read_text(encoding="utf-8")
    tightens = len(artifacts) >= TIGHTEN_RUNS
    # Compute every new text before the first write, so a bad input leaves the tree as it was.
    tightened = ratchet.tighten(text, budget, ratchet.per_file(durations)) if tightens else text
    target = root / DURATIONS
    writes: dict[str, tuple[Path, str]] = {}
    if _committed(target) != durations:
        # The format of merge_durations.py, so a refresh diff shows only the changed seconds.
        writes[DURATIONS] = (target, json.dumps(durations, indent=4, sort_keys=True) + "\n")
    if tightened != text:
        writes[ratchet.PYPROJECT] = (pyproject, tightened)
    for path, content in writes.values():
        _ = path.write_text(content, encoding="utf-8")
    return list(writes), tightens


def _parse(args: list[str]) -> tuple[list[Path], Path | None] | None:
    output = None
    if "--github-output" in args:
        index = args.index("--github-output")
        if index != len(args) - 2:
            return None
        output, args = Path(args[-1]), args[:index]
    if not args or any(arg.startswith("--") for arg in args):
        return None
    return [Path(arg) for arg in args], output


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parsed = _parse(sys.argv[1:] if argv is None else argv)
    if parsed is None:
        print(USAGE, file=sys.stderr)
        return 2
    artifacts, output = parsed
    try:
        changed, tightens = refresh(root, artifacts)
    except ratchet.DataError as error:
        print(error)
        return 1
    except (ratchet.ConfigError, OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        print(error, file=sys.stderr)
        return 2
    print(f"changed: {', '.join(changed)}" if changed else "no change")
    if not tightens:
        print(f"tighten skipped: {len(artifacts)} of {TIGHTEN_RUNS} runs")
    if output is not None:
        with output.open("a", encoding="utf-8") as stream:
            _ = stream.write(f"changed={'true' if changed else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
