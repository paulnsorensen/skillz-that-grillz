#!/usr/bin/env python3
"""Test-size ratchet: hold each test file and each parametrized test function to a collected-case budget.

`[tool.skillz.test-size]` in `pyproject.toml` sets `max_cases` (the cases that pytest collects from one test file)
and `max_rows` (the rows of one parametrized test function). A file above `max_cases` needs an entry in
`[tool.skillz.test-size.files]` with a measured `max_cases` ceiling and a `reason`. A function above `max_rows` needs
an entry in `[tool.skillz.test-size.functions]`, keyed by its node id without the parameters, with a measured
`max_rows` ceiling and a `reason`. A ceiling only goes down: `check` fails when a waived count falls (improved),
and `tighten` lowers each ceiling to the measured count and removes the waivers no longer needed.

The counts come from `pytest --collect-only -q` on the `tests` tree of each workspace package, so host load does
not change them.

    check_test_counts.py check      exit 1 on a violation
    check_test_counts.py tighten    rewrite the ceilings in pyproject.toml

Exit codes: 0 pass, 1 violation, 2 input error.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = "pyproject.toml"
TEST_TREES = "packages/*/tests"
_COLLECTED = re.compile(r"^(\d+) tests? collected")


class ConfigError(Exception):
    """The `[tool.skillz.test-size]` table is malformed, or pytest cannot collect a test tree."""


@dataclass(frozen=True)
class Waiver:
    ceiling: int
    reason: str


@dataclass(frozen=True)
class Budget:
    max_cases: int
    max_rows: int
    files: dict[str, Waiver]
    functions: dict[str, Waiver]


@dataclass(frozen=True)
class Counts:
    files: dict[str, int]
    functions: dict[str, int]


@dataclass(frozen=True)
class _Kind:
    """One counted unit: its waiver table, its ceiling key, the counted noun, and the fix for an excess."""

    table: str
    key: str
    noun: str
    fix: str


_FILE = _Kind("files", "max_cases", "cases", "split the file or cut redundant cases")
_FUNCTION = _Kind("functions", "max_rows", "rows", "cut redundant rows or split the table")


def _positive(table: dict[str, object], key: str) -> int:
    value = table.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigError(f"[tool.skillz.test-size].{key} must be a positive integer")
    return value


def _waivers(size: dict[str, object], kind: _Kind) -> dict[str, Waiver]:
    table = size.get(kind.table, {})
    if not isinstance(table, dict):
        raise ConfigError(f"[tool.skillz.test-size.{kind.table}] must be a table")
    waivers: dict[str, Waiver] = {}
    for name, entry in cast(dict[str, object], table).items():
        if not isinstance(entry, dict):
            raise ConfigError(f"{name}: a waiver must be an inline table")
        entry = cast(dict[str, object], entry)
        ceiling, reason = entry.get(kind.key), entry.get("reason")
        if not isinstance(ceiling, int) or isinstance(ceiling, bool):
            raise ConfigError(f"{name}: {kind.key} must be an integer")
        if not isinstance(reason, str) or not reason.strip():
            raise ConfigError(f"{name}: a waiver needs a non-empty reason")
        waivers[name] = Waiver(ceiling, reason)
    return waivers


def load_budget(root: Path) -> Budget:
    """Read the budget table; raise `ConfigError` on a missing key or a wrong type."""
    return _parse_budget((root / PYPROJECT).read_text(encoding="utf-8"))


def _parse_budget(text: str) -> Budget:
    config = cast(dict[str, object], tomllib.loads(text))
    table = cast(dict[str, object], cast(dict[str, object], config.get("tool", {})).get("skillz", {}))
    size = table.get("test-size")
    if not isinstance(size, dict):
        raise ConfigError("pyproject.toml has no [tool.skillz.test-size] table")
    size = cast(dict[str, object], size)
    return Budget(_positive(size, "max_cases"), _positive(size, "max_rows"),
                  _waivers(size, _FILE), _waivers(size, _FUNCTION))


def tally(node_ids: Iterable[str]) -> Counts:
    """Count the cases of each file and the rows of each parametrized function.

    A node id is `<file>::<test>` or `<file>::<class>::<test>`, with `[<parameters>]` after a parametrized test.
    """
    files: Counter[str] = Counter()
    functions: Counter[str] = Counter()
    for node in node_ids:
        files[node.partition("::")[0]] += 1
        function, bracket, _parameters = node.partition("[")
        if bracket:
            functions[function] += 1
    return Counts(dict(sorted(files.items())), dict(sorted(functions.items())))


def collect(root: Path, tree: Path) -> list[str]:
    """Return the node ids that pytest collects from one test tree, relative to `root`.

    `--rootdir` keys every node id by its repository path; pytest still reads the package's own ini table.
    The node ids end at the first blank line; the summary line must give the same count.
    """
    command = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
               "--rootdir", str(root), str(tree.relative_to(root))]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    lines = result.stdout.splitlines()
    summary = next((match for line in lines if (match := _COLLECTED.match(line))), None)
    nodes = lines[:lines.index("")] if "" in lines else lines
    if result.returncode != 0 or summary is None or int(summary.group(1)) != len(nodes):
        raise ConfigError((f"{tree.relative_to(root)}: pytest collection failed (exit {result.returncode})\n"
                           f"{result.stdout}{result.stderr}"))
    return nodes


def measure(root: Path) -> Counts:
    """Collect every workspace package's test tree; each package runs under its own pytest ini table."""
    return tally(node for tree in sorted(root.glob(TEST_TREES)) for node in collect(root, tree))


def _check(kind: _Kind, counts: dict[str, int], default: int, waivers: dict[str, Waiver]) -> list[str]:
    failures: list[str] = []
    for name, actual in counts.items():
        waiver = waivers.get(name)
        limit = waiver.ceiling if waiver else default
        if actual > limit:
            failures.append(f"{name}: {actual} {kind.noun} (ceiling {limit}); {kind.fix}")
    for name, waiver in waivers.items():
        actual = counts.get(name)
        if actual is None:
            failures.append(f"{name}: the waiver names no collected test; run `tighten`")
        elif actual <= default:
            failures.append(f"improved {name}: {actual} {kind.noun} fit the default cap {default}; run `tighten`")
        elif actual < waiver.ceiling:
            failures.append(f"improved {name}: {actual} {kind.noun}, below its ceiling {waiver.ceiling}; run `tighten`")
    return failures


def violations(budget: Budget, counts: Counts) -> list[str]:
    """Return one message per count over its ceiling, per stale waiver, and per improved (loose) ceiling."""
    return (_check(_FILE, counts.files, budget.max_cases, budget.files)
            + _check(_FUNCTION, counts.functions, budget.max_rows, budget.functions))


def _entry_pattern(name: str) -> re.Pattern[str]:
    """Match one waiver line, with an optional trailing comment; keep the lines around it."""
    return re.compile(rf'^"{re.escape(name)}"[ \t]*=[ \t]*\{{.*\}}[ \t]*(#.*)?\n', re.MULTILINE)


def _set_ceiling(line: str, key: str, value: int) -> str:
    """Set `key = value` in one inline-table waiver line; text inside a TOML string never matches the key."""
    pair = re.compile(rf"([{{,]\s*){key}\s*=\s*\d+")
    quote = ""
    index = 0
    while index < len(line):
        char = line[index]
        if quote:
            if quote == '"' and char == "\\":
                index += 1
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#":
            break
        elif match := pair.match(line, index):
            return f"{line[:index]}{match.group(1)}{key} = {value}{line[match.end():]}"
        index += 1
    raise ConfigError(f"{line.strip()}: tighten found no {key} key outside a string")


def _tighten(text: str, kind: _Kind, counts: dict[str, int], default: int, waivers: dict[str, Waiver]) -> str:
    for name, waiver in waivers.items():
        actual = counts.get(name)
        pattern = _entry_pattern(name)
        match = pattern.search(text)
        if match is None:
            raise ConfigError(f"{name}: tighten needs the waiver on one line")
        if actual is None or actual <= default:
            text = text[:match.start()] + text[match.end():]
        elif actual < waiver.ceiling:
            text = text[:match.start()] + _set_ceiling(match.group(0), kind.key, actual) + text[match.end():]
    return text


def _kept(counts: dict[str, int], default: int, waivers: dict[str, Waiver]) -> dict[str, int]:
    """Return the ceiling that `tighten` must leave for each waiver it keeps."""
    return {name: min(actual, waiver.ceiling) for name, waiver in waivers.items()
            if (actual := counts.get(name)) is not None and actual > default}


def tighten(text: str, budget: Budget, counts: Counts) -> str:
    """Lower each loose ceiling to its count and drop the waivers no longer needed; never raise one.

    Parse the result and compare every ceiling, so a rewrite that misses its key fails instead of passing.
    """
    text = _tighten(text, _FILE, counts.files, budget.max_cases, budget.files)
    text = _tighten(text, _FUNCTION, counts.functions, budget.max_rows, budget.functions)
    after = _parse_budget(text)
    for kind, written, expected in (
        (_FILE, after.files, _kept(counts.files, budget.max_cases, budget.files)),
        (_FUNCTION, after.functions, _kept(counts.functions, budget.max_rows, budget.functions)),
    ):
        ceilings = {name: waiver.ceiling for name, waiver in written.items()}
        if ceilings != expected:
            raise ConfigError(f"tighten wrote the {kind.table} ceilings {ceilings}, not {expected}")
    return text


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args not in (['check'], ['tighten']):
        print("usage: check_test_counts.py check|tighten", file=sys.stderr)
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
    caps = f"{budget.max_cases} cases per file, {budget.max_rows} rows per parametrized function"
    failures = violations(budget, counts)
    if failures:
        print(f"test-size ratchet: {len(failures)} violation(s) against the caps ({caps})")
        print("\n".join(failures))
        return 1
    waived = len(budget.files) + len(budget.functions)
    print((f"test-size ratchet: {len(counts.files)} files and {len(counts.functions)} parametrized functions "
           f"within the caps ({caps}; {waived} measured waivers)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
