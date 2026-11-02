#!/usr/bin/env python3
"""Test-time ratchet: hold each skillz test file to a wall-time budget (quality-ratchets ADR-006).

A file's seconds are the sum of the stored durations (setup + call + teardown) of its tests. The file is the
node-id prefix before `::`, as `packages/skillz/.test_durations` names it. `[tool.skillz.test-time]` in
`pyproject.toml` sets `max_seconds`, the default ceiling of each file. A file above it needs an entry in
`[tool.skillz.test-time.exceptions]` with a `max_seconds` ceiling and a `reason`. A ceiling is 1.5 x the measured
seconds, rounded up. It only goes down: `tighten` lowers each improved ceiling and removes the entries that the
default covers. Wall time varies from run to run, so `check` reports an improved ceiling but does not fail on it.

    check_test_durations.py check <durations> [--collected <dir>]   exit 1 on a breach or on missing data
    check_test_durations.py report <durations>                       print the per-file table; always exit 0
    check_test_durations.py tighten <durations>                      rewrite the ceilings in pyproject.toml

`--collected` names a directory of JUnit XML files, one for each sharded lane. The sum of their `tests` counts is
the count of tests that the shards selected; `check` fails when the durations hold fewer test ids.

Exit codes: 0 pass, 1 breach or missing data, 2 input error.
"""
from __future__ import annotations

import json
import math
import re
import sys
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = "pyproject.toml"
HEADROOM = 1.5
# The report table shows this many of the slowest files.
TOP = 10
USAGE = "usage: check_test_durations.py check|report|tighten <durations> [--collected <dir>]"


class ConfigError(Exception):
    """The `[tool.skillz.test-time]` table is missing or malformed."""


class DataError(Exception):
    """The durations or the collected counts are missing, empty, malformed, or short."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"test-time ratchet [{code}]: {message}")


@dataclass(frozen=True)
class Waiver:
    max_seconds: int
    reason: str


@dataclass(frozen=True)
class Budget:
    max_seconds: int
    waivers: dict[str, Waiver]

    def ceiling(self, path: str) -> int:
        waiver = self.waivers.get(path)
        return waiver.max_seconds if waiver else self.max_seconds


@dataclass(frozen=True)
class FileTime:
    seconds: float
    tests: int


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def load_budget(root: Path) -> Budget:
    """Read the budget table; raise `ConfigError` on a missing key or a wrong type."""
    with (root / PYPROJECT).open("rb") as stream:
        config = cast(dict[str, object], tomllib.load(stream))
    table = cast(dict[str, object], cast(dict[str, object], config.get("tool", {})).get("skillz", {}))
    budget = table.get("test-time")
    if not isinstance(budget, dict):
        raise ConfigError("pyproject.toml has no [tool.skillz.test-time] table")
    budget = cast(dict[str, object], budget)
    limit = budget.get("max_seconds")
    if not _positive_int(limit):
        raise ConfigError("[tool.skillz.test-time].max_seconds must be a positive integer")
    waivers: dict[str, Waiver] = {}
    for path, entry in cast(dict[str, object], budget.get("exceptions", {})).items():
        if not isinstance(entry, dict):
            raise ConfigError(f"{path}: an exception must be an inline table")
        entry = cast(dict[str, object], entry)
        ceiling, reason = entry.get("max_seconds"), entry.get("reason")
        if not _positive_int(ceiling):
            raise ConfigError(f"{path}: max_seconds must be a positive integer")
        if not isinstance(reason, str) or not reason.strip():
            raise ConfigError(f"{path}: an exception needs a non-empty reason")
        waivers[path] = Waiver(cast(int, ceiling), reason)
    return Budget(cast(int, limit), waivers)


def load_durations(path: Path) -> dict[str, float]:
    """Read a pytest-split durations file; raise `DataError` when it is missing, malformed, or empty."""
    if not path.is_file():
        raise DataError("TIME-MISSING", f"{path}: the durations file is missing")
    try:
        data = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DataError("TIME-INVALID", f"{path}: {error}") from error
    if not isinstance(data, dict):
        raise DataError("TIME-INVALID", f"{path}: expected a JSON object of test id to seconds")
    durations: dict[str, float] = {}
    for test, seconds in cast(dict[str, object], data).items():
        if isinstance(seconds, bool) or not isinstance(seconds, int | float) or not 0 <= seconds < math.inf:
            raise DataError("TIME-INVALID", f"{path}: {test!r} has no valid duration")
        durations[test] = float(seconds)
    if not durations:
        raise DataError("TIME-EMPTY", f"{path}: the durations file holds no test ids")
    return durations


def collected_count(directory: Path) -> int:
    """Return the sum of the `tests` counts of the JUnit XML files in `directory`; raise `DataError` on no data."""
    files = sorted(directory.glob("*.xml")) if directory.is_dir() else []
    if not files:
        raise DataError("TIME-NO-COUNT", f"{directory}: no JUnit XML file (*.xml) gives the collected count")
    total = 0
    for path in files:
        try:
            root = ElementTree.parse(path).getroot()
        except (OSError, ElementTree.ParseError) as error:
            raise DataError("TIME-NO-COUNT", f"{path.name}: {error}") from error
        suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
        counts = [suite.get("tests", "") for suite in suites]
        if not counts or not all(count.isdigit() for count in counts):
            raise DataError("TIME-NO-COUNT", f"{path.name}: no testsuite element has a `tests` count")
        total += sum(int(count) for count in counts)
    return total


def per_file(durations: dict[str, float]) -> dict[str, FileTime]:
    """Sum the durations of each test file, keyed by the node-id prefix before `::`."""
    seconds: defaultdict[str, float] = defaultdict(float)
    tests: Counter[str] = Counter()
    for test, value in durations.items():
        path = test.split("::", 1)[0]
        seconds[path] += value
        tests[path] += 1
    return {path: FileTime(seconds[path], tests[path]) for path in sorted(seconds)}


def ceiling_for(seconds: float) -> int:
    """Return the waiver ceiling of a measured file: `HEADROOM` x seconds, rounded up to a whole second."""
    # Round first, so that float noise in a sum of durations does not add a second.
    return math.ceil(round(seconds * HEADROOM, 6))


def target(budget: Budget, waiver: Waiver, time: FileTime | None) -> int | None:
    """Return the ceiling that `tighten` gives a waiver: `None` removes it; a ceiling never goes up."""
    if time is None:
        return None
    needed = ceiling_for(time.seconds)
    return None if needed <= budget.max_seconds else min(needed, waiver.max_seconds)


def breaches(budget: Budget, files: dict[str, FileTime]) -> list[str]:
    """Return one message per file whose seconds go above its ceiling."""
    return [f"{path}: {time.seconds:.2f} s (ceiling {budget.ceiling(path)} s); speed up or split the file"
            for path, time in files.items() if time.seconds > budget.ceiling(path)]


def improvements(budget: Budget, files: dict[str, FileTime]) -> list[str]:
    """Return one note per waiver that `tighten` lowers or removes."""
    notes: list[str] = []
    for path, waiver in budget.waivers.items():
        time = files.get(path)
        ceiling = target(budget, waiver, time)
        if time is None:
            notes.append(f"{path}: the exception names a file with no durations; run `tighten`")
        elif ceiling is None:
            notes.append(f"{path}: {time.seconds:.2f} s fits the {budget.max_seconds} s budget; run `tighten`")
        elif ceiling < waiver.max_seconds:
            drop = f"ceiling {waiver.max_seconds} s can drop to {ceiling} s"
            notes.append(f"{path}: {time.seconds:.2f} s, {drop}; run `tighten`")
    return notes


def report(budget: Budget, files: dict[str, FileTime], source: Path) -> list[str]:
    """Return the report lines: a summary, the slowest files, the breaches, and the improved waivers."""
    total = sum(time.seconds for time in files.values())
    tests = sum(time.tests for time in files.values())
    lines = [f"test-time report: {len(files)} files, {tests} tests, {total:.1f} s in {source}",
             f"{'seconds':>9} {'ceiling':>8} {'tests':>6}  file"]
    slowest = sorted(files.items(), key=lambda item: (-item[1].seconds, item[0]))[:TOP]
    lines += [f"{time.seconds:>9.2f} {budget.ceiling(path):>8} {time.tests:>6}  {path}" for path, time in slowest]
    lines += [f"breach: {line}" for line in breaches(budget, files)]
    return lines + improvements(budget, files)


def _entry_pattern(path: str) -> re.Pattern[str]:
    """Match one exception line, with an optional trailing comment; keep the lines around it."""
    return re.compile(rf'^"{re.escape(path)}"[ \t]*=[ \t]*\{{.*\}}[ \t]*(#.*)?\n', re.MULTILINE)


# The `max_seconds` key of an inline table: it follows `{` or `,`, so a reason string that names it never matches.
_CEILING = re.compile(r"([{,]\s*)max_seconds\s*=\s*\d+")


def tighten(text: str, budget: Budget, files: dict[str, FileTime]) -> str:
    """Lower each improved ceiling and drop the exceptions that the default covers; never raise one."""
    for path, waiver in budget.waivers.items():
        ceiling = target(budget, waiver, files.get(path))
        pattern = _entry_pattern(path)
        if not pattern.search(text):
            raise ConfigError(f"{path}: tighten needs the exception on one line")
        if ceiling is None:
            text = pattern.sub("", text, count=1)
        elif ceiling < waiver.max_seconds:
            text = pattern.sub(
                lambda match: _CEILING.sub(rf"\g<1>max_seconds = {ceiling}", match.group(0), count=1),
                text, count=1,
            )
    return text


def _parse(args: list[str]) -> tuple[str, Path, Path | None] | None:
    if len(args) == 4 and args[0] == "check" and args[2] == "--collected":
        return args[0], Path(args[1]), Path(args[3])
    if len(args) == 2 and args[0] in {"check", "report", "tighten"}:
        return args[0], Path(args[1]), None
    return None


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parsed = _parse(sys.argv[1:] if argv is None else argv)
    if parsed is None:
        print(USAGE, file=sys.stderr)
        return 2
    command, source, collected = parsed
    # `report` informs the local gate: it prints each problem and exits 0 (ADR-006).
    try:
        budget = load_budget(root)
    except (ConfigError, OSError, tomllib.TOMLDecodeError) as error:
        print(error, file=sys.stderr)
        return 0 if command == "report" else 2
    try:
        durations = load_durations(source)
        if collected is not None and len(durations) < (expected := collected_count(collected)):
            short = f"{len(durations)} test ids, fewer than the {expected} tests that the shards selected"
            raise DataError("TIME-SHORT", f"{source} holds {short}")
    except DataError as error:
        print(error)
        return 0 if command == "report" else 1
    files = per_file(durations)
    if command == "report":
        print("\n".join(report(budget, files, source)))
        return 0
    if command == "tighten":
        path = root / PYPROJECT
        try:
            _ = path.write_text(tighten(path.read_text(encoding="utf-8"), budget, files), encoding="utf-8")
        except (ConfigError, OSError) as error:
            print(error, file=sys.stderr)
            return 2
        return 0
    failures, notes = breaches(budget, files), improvements(budget, files)
    if failures:
        print(f"test-time ratchet: {len(failures)} file(s) above the ceiling (default {budget.max_seconds} s)")
        print("\n".join(failures + notes))
        return 1
    print(f"test-time ratchet: {len(files)} files within budget ({len(budget.waivers)} measured exceptions)")
    if notes:
        print("\n".join(notes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
