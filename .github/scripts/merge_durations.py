"""Merge the per-shard pytest-split durations files into one file.

Usage: merge_durations.py <dir> <out>

Read every `*.json` file in `<dir>` and write the union of their test durations to `<out>`, keys sorted.
Each collected test runs in exactly one shard, so a test id in two files is an error (exit 1).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast


def merge(directory: Path) -> dict[str, float]:
    """Return the union of the `*.json` durations files in `directory`; raise `ValueError` on bad input."""
    files = sorted(directory.glob("*.json"))
    if not files:
        raise ValueError(f"no durations files (*.json) in {directory}")
    merged: dict[str, float] = {}
    owner: dict[str, str] = {}
    for path in files:
        data = cast(object, json.loads(path.read_text()))
        if not isinstance(data, dict):
            raise ValueError(f"{path.name}: expected a JSON object of test id to seconds")
        # JSON object keys are always strings.
        for test, seconds in cast(dict[str, object], data).items():
            if isinstance(seconds, bool) or not isinstance(seconds, int | float):
                raise ValueError(f"{path.name}: {test!r} has no numeric duration")
            if test in owner:
                raise ValueError(f"{test} is in both {owner[test]} and {path.name}")
            owner[test] = path.name
            merged[test] = float(seconds)
    return dict(sorted(merged.items()))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: merge_durations.py <dir> <out>", file=sys.stderr)
        return 2
    try:
        merged = merge(Path(argv[0]))
    except (OSError, ValueError) as error:
        print(f"merge_durations: {error}", file=sys.stderr)
        return 1
    out = Path(argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    _ = out.write_text(json.dumps(merged, indent=4, sort_keys=True) + "\n")
    print(f"merged {len(merged)} test durations into {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
