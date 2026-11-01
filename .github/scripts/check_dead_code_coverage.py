#!/usr/bin/env python3
"""Coverage-based whole-symbol dead-code gate.

Ported from milknado `scripts/check_dead_code_coverage.py`. It reads a Cobertura `coverage.xml` and the
`packages/*/src` source trees. A function or method whose every measured body line has zero hits is whole-symbol
dead code that the name-based Vulture lane misses (a name collision, or a dead reference cycle).

A symbol is exempt when a decorator matches `[tool.vulture].ignore_decorators`, when a `[project.scripts]`
entry of a workspace package names it, or when `[tool.skillz.dead-code].allow` lists it as `module:qualname`.

Known limitation: a function whose whole body is on the `def` line cannot be flagged. coverage.py records the
`def` line as hit at import, and the body shares that line.
"""
from __future__ import annotations

import ast
import fnmatch
import sys
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from typing_extensions import override

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class DeadSymbol:
    path: str
    module: str
    qualname: str
    lineno: int

    def report(self) -> str:
        return (f"{self.path}:{self.lineno}: {self.module}:{self.qualname} is whole-symbol dead code (zero coverage "
                "hits). Test it, delete it, mark it '# pragma: no cover', or add it to "
                "[tool.skillz.dead-code].allow with a comment.")


@dataclass(frozen=True)
class Exemptions:
    ignore_decorators: tuple[str, ...]
    script_targets: tuple[str, ...]
    allowlist: tuple[str, ...]

    def is_exempt(self, module_qualname: str, decorators: list[str]) -> bool:
        if module_qualname in self.allowlist or module_qualname in self.script_targets:
            return True
        return any(fnmatch.fnmatch(decorator, pattern)
                   for decorator in decorators for pattern in self.ignore_decorators)


def _decorator_name(decorator: ast.expr) -> ast.expr:
    """Unwrap a call decorator to its callee, as Vulture does."""
    return decorator.func if isinstance(decorator, ast.Call) else decorator


class _FunctionCollector(ast.NodeVisitor):
    """Walk a module and record each function with its dotted qualname and decorators."""

    def __init__(self) -> None:
        self.functions: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, list[str]]] = []
        self._stack: list[str] = []

    @override
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._stack.append(node.name)
        self.generic_visit(node)
        _ = self._stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualname = ".".join([*self._stack, node.name])
        self.functions.append((qualname, node, ["@" + ast.unparse(_decorator_name(d)) for d in node.decorator_list]))
        self._stack.append(node.name)
        self.generic_visit(node)
        _ = self._stack.pop()

    @override
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    @override
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)


def _toml(path: Path) -> dict[str, object]:
    with path.open("rb") as stream:
        return cast(dict[str, object], tomllib.load(stream))


def _section(config: dict[str, object], *keys: str) -> dict[str, object]:
    for key in keys:
        config = cast(dict[str, object], config.get(key, {}))
    return config


def load_exemptions(root: Path) -> Exemptions:
    """Read the exemptions from the root `pyproject.toml` and the `[project.scripts]` of each workspace package."""
    config = _toml(root / "pyproject.toml")
    scripts = [target for package in sorted(root.glob("packages/*/pyproject.toml"))
               for target in cast(dict[str, str], _section(_toml(package), "project", "scripts")).values()]
    return Exemptions(
        ignore_decorators=tuple(cast(list[str], _section(config, "tool", "vulture").get("ignore_decorators", []))),
        script_targets=tuple(scripts),
        allowlist=tuple(cast(list[str], _section(config, "tool", "skillz", "dead-code").get("allow", []))),
    )


def parse_coverage_xml(coverage_xml: Path, root: Path) -> dict[Path, dict[int, int]]:
    """Return the line hits of each measured file; raise `ValueError` when the report names no file."""
    report = ET.parse(coverage_xml).getroot()
    sources = [Path(element.text.strip()) for element in report.findall("./sources/source") if element.text]
    hits_by_file: dict[Path, dict[int, int]] = {}
    for element in report.iter("class"):
        filename = element.get("filename")
        if not filename:
            continue
        candidates = [source / filename for source in sources] + [Path(filename)]
        resolved = next(((root / path).resolve() for path in candidates if (root / path).exists()),
                        (root / filename).resolve())
        hits = {int(line.get("number", "0")): int(line.get("hits", "0")) for line in element.findall("./lines/line")}
        hits_by_file.setdefault(resolved, {}).update(hits)
    if not hits_by_file:
        raise ValueError("the report names no measured file")
    return hits_by_file


def module_name(path: Path, root: Path) -> str | None:
    """Return the dotted module of a `packages/<name>/src` file, or `None` for any other file."""
    parts = path.resolve().relative_to(root.resolve()).with_suffix("").parts
    if len(parts) < 4 or parts[0] != "packages" or parts[2] != "src":
        return None
    names = list(parts[3:])
    if names[-1] == "__init__":
        _ = names.pop()
    return ".".join(names)


def find_whole_dead_symbols(coverage_xml: Path, root: Path, exempt: Exemptions) -> list[DeadSymbol]:
    dead: list[DeadSymbol] = []
    for path, hits in sorted(parse_coverage_xml(coverage_xml, root).items()):
        module = module_name(path, root)
        if module is None:
            continue
        collector = _FunctionCollector()
        collector.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for qualname, node, decorators in collector.functions:
            end = node.end_lineno
            if end is None:
                continue
            measured = [count for line, count in hits.items() if node.body[0].lineno <= line <= end]
            if not measured or any(measured) or exempt.is_exempt(f"{module}:{qualname}", decorators):
                continue
            dead.append(DeadSymbol(path.relative_to(root.resolve()).as_posix(), module, qualname, node.lineno))
    return dead


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: check_dead_code_coverage.py COVERAGE_XML", file=sys.stderr)
        return 2
    coverage_xml = Path(args[0])
    try:
        dead = find_whole_dead_symbols(coverage_xml, root, load_exemptions(root))
    except (ET.ParseError, OSError, ValueError, tomllib.TOMLDecodeError) as error:
        print(f"{coverage_xml}: cannot analyze coverage ({error}); run the coverage lanes first", file=sys.stderr)
        return 2
    for symbol in dead:
        print(symbol.report())
    if dead:
        return 1
    print("dead-code-coverage: no whole-symbol zero-hit code")
    return 0


if __name__ == "__main__":
    sys.exit(main())
