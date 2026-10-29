#!/usr/bin/env python3
"""File-size ratchet: hold each `packages/*/src` Python file to a line budget.

Ported from milknado `scripts/check_file_lengths.py`. `[tool.skillz.file-size]` in `pyproject.toml` sets
`max_lines`. A file above it needs an entry in `[tool.skillz.file-size.exceptions]` with a measured
`max_lines` ceiling and a `reason`. The ceiling only goes down: `check` fails when a file shrinks below its
ceiling, and `tighten` lowers each ceiling to the measured count and removes the entries no longer needed.

    check_file_lengths.py check      exit 1 on a violation
    check_file_lengths.py tighten    rewrite the ceilings in pyproject.toml

Exit codes: 0 pass, 1 violation, 2 input error.
"""
from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = "pyproject.toml"
SOURCE_GLOB = "packages/*/src/**/*.py"


class ConfigError(Exception):
    """The `[tool.skillz.file-size]` table is missing or malformed."""


@dataclass(frozen=True)
class Waiver:
    max_lines: int
    reason: str


@dataclass(frozen=True)
class Budget:
    max_lines: int
    waivers: dict[str, Waiver]


def load_budget(root: Path) -> Budget:
    """Read the budget table; raise `ConfigError` on a missing key or a wrong type."""
    with (root / PYPROJECT).open("rb") as stream:
        config = cast(dict[str, object], tomllib.load(stream))
    table = cast(dict[str, object], cast(dict[str, object], config.get("tool", {})).get("skillz", {}))
    size = table.get("file-size")
    if not isinstance(size, dict):
        raise ConfigError("pyproject.toml has no [tool.skillz.file-size] table")
    size = cast(dict[str, object], size)
    limit = size.get("max_lines")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ConfigError("[tool.skillz.file-size].max_lines must be a positive integer")
    waivers: dict[str, Waiver] = {}
    for path, entry in cast(dict[str, object], size.get("exceptions", {})).items():
        if not isinstance(entry, dict):
            raise ConfigError(f"{path}: an exception must be an inline table")
        entry = cast(dict[str, object], entry)
        ceiling, reason = entry.get("max_lines"), entry.get("reason")
        if not isinstance(ceiling, int) or isinstance(ceiling, bool):
            raise ConfigError(f"{path}: max_lines must be an integer")
        if not isinstance(reason, str) or not reason.strip():
            raise ConfigError(f"{path}: an exception needs a non-empty reason")
        waivers[path] = Waiver(ceiling, reason)
    return Budget(limit, waivers)


def _line_count(text: str) -> int:
    """Count lines as `wc -l` and editors do: `str.splitlines` also splits on form feeds and U+2028."""
    return text.count("\n") + (bool(text) and not text.endswith("\n"))


def measure(root: Path) -> dict[str, int]:
    """Return the line count of each source file, keyed by its repository-relative POSIX path."""
    return {
        path.relative_to(root).as_posix(): _line_count(path.read_text(encoding="utf-8"))
        for path in sorted(root.glob(SOURCE_GLOB))
    }


def violations(budget: Budget, counts: dict[str, int]) -> list[str]:
    """Return one message per file over its limit, per stale exception, and per loose ceiling."""
    failures: list[str] = []
    for path, actual in counts.items():
        waiver = budget.waivers.get(path)
        limit = waiver.max_lines if waiver else budget.max_lines
        if actual > limit:
            failures.append(f"{path}: {actual} lines (limit {limit}); split the file")
    for path, waiver in budget.waivers.items():
        actual = counts.get(path)
        if actual is None:
            failures.append(f"{path}: the exception names a file that does not exist; run `tighten`")
        elif actual <= budget.max_lines:
            failures.append(f"{path}: {actual} lines fit the {budget.max_lines}-line budget; run `tighten`")
        elif actual < waiver.max_lines:
            failures.append(f"{path}: {actual} lines, below its ceiling {waiver.max_lines}; run `tighten`")
    return failures


def _entry_pattern(path: str) -> re.Pattern[str]:
    """Match one exception line, with an optional trailing comment; keep the lines around it."""
    return re.compile(rf'^"{re.escape(path)}"[ \t]*=[ \t]*\{{.*\}}[ \t]*(#.*)?\n', re.MULTILINE)


# The `max_lines` key of an inline table: it follows `{` or `,`, so a reason string that names it never matches.
_CEILING = re.compile(r"([{,]\s*)max_lines\s*=\s*\d+")


def tighten(text: str, budget: Budget, counts: dict[str, int]) -> str:
    """Lower each loose ceiling to its count and drop the exceptions no longer needed; never raise one."""
    for path, waiver in budget.waivers.items():
        actual = counts.get(path)
        pattern = _entry_pattern(path)
        if not pattern.search(text):
            raise ConfigError(f"{path}: tighten needs the exception on one line")
        if actual is None or actual <= budget.max_lines:
            text = pattern.sub("", text, count=1)
        elif actual < waiver.max_lines:
            text = pattern.sub(
                lambda match: _CEILING.sub(rf"\g<1>max_lines = {actual}", match.group(0), count=1),
                text, count=1,
            )
    return text


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args not in (['check'], ['tighten']):
        print("usage: check_file_lengths.py check|tighten", file=sys.stderr)
        return 2
    try:
        budget = load_budget(root)
        counts = measure(root)
        if args == ['tighten']:
            path = root / PYPROJECT
            _ = path.write_text(tighten(path.read_text(encoding="utf-8"), budget, counts), encoding="utf-8")
            return 0
    except (ConfigError, OSError, tomllib.TOMLDecodeError) as error:
        print(error, file=sys.stderr)
        return 2
    failures = violations(budget, counts)
    if failures:
        print(f"file-size ratchet: {len(failures)} violation(s) against the {budget.max_lines}-line budget")
        print("\n".join(failures))
        return 1
    print(f"file-size ratchet: {len(counts)} files within budget ({len(budget.waivers)} measured exceptions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
