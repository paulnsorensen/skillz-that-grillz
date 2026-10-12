#!/usr/bin/env python3
"""Fail when an acceptance criterion has no marked test, or a marker names no criterion.

Tests in packages/{skillz,wedge,fromargs} and the gate's own .github/scripts/test_*.py
carry `@pytest.mark.ac("<source>:<id>")`.
Each source keeps its criteria in docs/agents/criteria/<source>.md, a table of
`| id | kind | criterion | verified by |` rows. A `test` row needs a marked test;
a `process` row names its evidence in `verified by`. The check reads repository
files only, parses tests with `ast`, and imports no package under test.
"""
from __future__ import annotations

import ast
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

PACKAGES = ("skillz", "wedge", "fromargs")
GATE_TESTS = Path(".github/scripts")
CRITERIA_DIR = Path("docs/agents/criteria")
SOURCE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
CRITERION_ID = re.compile(r"[A-Z]+-[A-Z]*[0-9]+")
MARKER = re.compile(rf"(?P<source>{SOURCE.pattern}):(?P<id>{CRITERION_ID.pattern})")
SEPARATOR_CELL = re.compile(r":?-+:?")
CELL_SPLIT = re.compile(r"(?<!\\)\|")
HEADER = ("id", "kind", "criterion", "verified by")
KINDS = ("test", "process")
INDEX = "index.md"
LITERAL_FAULT = "ac marker needs exactly one string literal argument"
UNREADABLE_FAULT = "cannot read file"


@dataclass(frozen=True)
class Marker:
    location: str
    source: str
    criterion: str


@dataclass(frozen=True)
class Row:
    kind: str
    verified_by: str


@dataclass
class Criteria:
    rows: dict[str, Row] = field(default_factory=dict[str, Row])


def _inside(root: Path, path: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _read(path: Path, rel: str) -> tuple[str, list[str]]:
    try:
        return path.read_text(encoding="utf-8"), []
    except (UnicodeDecodeError, OSError):
        return "", [f"{rel}:1: {UNREADABLE_FAULT}"]


def _pytest_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Return the local names bound to the pytest module and to pytest.mark."""
    modules: set[str] = set()
    marks: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update((alias.asname or alias.name) for alias in node.names if alias.name == "pytest")
        elif isinstance(node, ast.ImportFrom) and node.module == "pytest" and node.level == 0:
            marks.update((alias.asname or alias.name) for alias in node.names if alias.name == "mark")
    return modules, marks


def _is_ac_mark(node: ast.AST, modules: set[str], marks: set[str]) -> bool:
    if not (isinstance(node, ast.Attribute) and node.attr == "ac"):
        return False
    owner = node.value
    if isinstance(owner, ast.Name):
        return owner.id in marks
    return (
        isinstance(owner, ast.Attribute)
        and owner.attr == "mark"
        and isinstance(owner.value, ast.Name)
        and owner.value.id in modules
    )


def _scan_test_file(root: Path, path: Path) -> tuple[list[Marker], list[str]]:
    rel = path.relative_to(root).as_posix()
    if not _inside(root, path):
        return [], [f"{rel}:1: path resolves outside the repository"]
    source, unreadable = _read(path, rel)
    if unreadable:
        return [], unreadable
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError as error:
        return [], [f"{rel}:{error.lineno or 1}: cannot parse test file: {error.msg}"]
    modules, marks = _pytest_names(tree)
    markers: list[Marker] = []
    faults: list[str] = []
    called: set[int] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_ac_mark(node.func, modules, marks)):
            continue
        called.add(id(node.func))
        where = f"{rel}:{node.lineno}"
        literal = node.args[0] if len(node.args) == 1 and not node.keywords else None
        if not (isinstance(literal, ast.Constant) and isinstance(literal.value, str)):
            faults.append(f"{where}: {LITERAL_FAULT}")
            continue
        match = MARKER.fullmatch(literal.value)
        if match is None:
            faults.append(f"{where}: ac marker {literal.value!r} must match <source>:<id>, for example gh-267:AC-1")
            continue
        markers.append(Marker(where, match["source"], match["id"]))
    faults.extend(
        f"{rel}:{node.lineno}: {LITERAL_FAULT}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and _is_ac_mark(node, modules, marks) and id(node) not in called
    )
    return markers, faults


def _test_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for package in PACKAGES:
        for directory, _dirs, names in os.walk(root / "packages" / package / "tests", followlinks=False):
            files.extend(Path(directory) / name for name in names if name.endswith(".py"))
    files.extend((root / GATE_TESTS).glob("test_*.py"))
    return sorted(files)


def _cells(line: str) -> list[str]:
    body = line.strip().removeprefix("|").removesuffix("|")
    return [cell.strip() for cell in CELL_SPLIT.split(body)]


def _parse_criteria(root: Path, path: Path) -> tuple[Criteria, list[str]]:
    rel = path.relative_to(root).as_posix()
    criteria = Criteria()
    if not _inside(root, path):
        return criteria, [f"{rel}:1: path resolves outside the repository"]
    text, unreadable = _read(path, rel)
    if unreadable:
        return criteria, unreadable
    lines = text.splitlines()
    faults: list[str] = []
    first = next((line for line in lines if line.strip()), "")
    if not first.startswith("# "):
        faults.append(f"{rel}:1: the first non-blank line must be an H1")
    if not any(line.startswith("Source:") and line.removeprefix("Source:").strip() for line in lines):
        faults.append(f"{rel}:1: missing a non-empty Source: line")
    start = next((index for index, line in enumerate(lines) if line.startswith("|")), None)
    if start is None:
        return criteria, [*faults, f"{rel}:1: missing the criteria table"]
    if tuple(cell.lower() for cell in _cells(lines[start])) != HEADER:
        return criteria, [*faults, f"{rel}:{start + 1}: table header must be | id | kind | criterion | verified by |"]
    divider = _cells(lines[start + 1]) if start + 1 < len(lines) else []
    if len(divider) != len(HEADER) or not all(SEPARATOR_CELL.fullmatch(cell) for cell in divider):
        return criteria, [*faults, f"{rel}:{start + 2}: table header needs a separator row"]
    end = next((index for index in range(start + 2, len(lines)) if not lines[index].startswith("|")), len(lines))
    faults.extend(
        f"{rel}:{index + 1}: row outside the criteria table" for index in range(end, len(lines)) if lines[index].startswith("|")
    )
    rows = end - (start + 2)
    for index in range(start + 2, end):
        where = f"{rel}:{index + 1}"
        cells = _cells(lines[index])
        if len(cells) != len(HEADER):
            faults.append(f"{where}: row needs {len(HEADER)} cells, found {len(cells)}")
            continue
        identifier, kind, text, verified_by = cells
        if not CRITERION_ID.fullmatch(identifier) or not text:
            faults.append(f"{where}: row needs an id such as AC-1 and a criterion text")
        elif identifier in criteria.rows:
            faults.append(f"{where}: duplicate id {identifier}")
        else:
            criteria.rows[identifier] = Row(kind, verified_by)
    if not rows:
        faults.append(f"{rel}:{start + 1}: the criteria table has no rows")
    return criteria, faults


def _row_faults(source: str, identifier: str, row: Row, marked: set[tuple[str, str]]) -> list[str]:
    name = f"{source}:{identifier}"
    if row.kind not in KINDS:
        return [f"{name}: kind {row.kind!r} must be test or process"]
    if row.kind == "process" and not row.verified_by:
        return [f"{name}: process criterion needs a non-empty verified by"]
    if row.kind == "test" and (source, identifier) not in marked:
        return [f"{name}: test criterion has no marked test"]
    return []


def main(root: Path) -> int:
    faults: list[str] = []
    markers: list[Marker] = []
    for path in _test_files(root):
        found, problems = _scan_test_file(root, path)
        markers.extend(found)
        faults.extend(problems)
    sources: dict[str, Criteria] = {}
    folder = root / CRITERIA_DIR
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        if path.name == INDEX:
            continue
        if not SOURCE.fullmatch(path.stem):
            faults.append(f"{path.relative_to(root).as_posix()}:1: file name must be a source slug or gh-<number>")
            continue
        sources[path.stem], problems = _parse_criteria(root, path)
        faults.extend(problems)
    for marker in markers:
        criteria = sources.get(marker.source)
        name = f"{marker.source}:{marker.criterion}"
        if criteria is None:
            faults.append(f"{marker.location}: marker {name} names unknown source {marker.source!r}")
        elif marker.criterion not in criteria.rows:
            faults.append(f"{marker.location}: marker {name} names unknown id {marker.criterion!r}")
    marked = {(marker.source, marker.criterion) for marker in markers}
    for source, criteria in sources.items():
        for identifier, row in criteria.rows.items():
            faults.extend(_row_faults(source, identifier, row, marked))
    for fault in sorted(faults):
        print(fault)
    return 1 if faults else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]))
