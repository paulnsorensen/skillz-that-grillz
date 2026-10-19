#!/usr/bin/env python3
"""Fail when a workspace package imports a private module of another workspace package.

A workspace package is a directory `packages/<name>/`. It owns each import package
`packages/<name>/src/<import>/`. A private module is a dotted part that starts with `_`
and is not a dunder name. `ALLOWED` holds the imports that existed when this check landed.
It may only shrink: a new import fails, and an entry with no matching import also fails.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLZ = "packages/skillz/src/skillz_experiments"
# (importing file, imported private module). Remove an entry when its import goes. A new entry needs an ADR.
ALLOWED: frozenset[tuple[str, str]] = frozenset({
    (f"{SKILLZ}/_new_cli.py", "wedge._config"),
    (f"{SKILLZ}/run/_wedge_targets.py", "wedge._build"),
    (f"{SKILLZ}/run/_wedge_targets.py", "wedge._bundle"),
    (f"{SKILLZ}/run/_wedge_targets.py", "wedge._config"),
    (f"{SKILLZ}/run/_wedge_targets.py", "wedge._key"),
})


def _private(part: str) -> bool:
    return part.startswith("_") and not (part.startswith("__") and part.endswith("__"))


def owners(root: Path) -> dict[str, str]:
    """Map each import package under `packages/*/src/` to its workspace package directory name."""
    return {path.name: path.parent.parent.name for path in root.glob("packages/*/src/*")
            if (path / "__init__.py").is_file()}


def _modules(node: ast.Import | ast.ImportFrom) -> list[str]:
    """Return the private dotted modules that one absolute import statement names."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names if any(map(_private, alias.name.split(".")[1:]))]
    if node.level or not node.module:
        return []
    if any(map(_private, node.module.split(".")[1:])):
        return [node.module]
    return [f"{node.module}.{alias.name}" for alias in node.names if _private(alias.name)]


def cross_package_imports(root: Path) -> list[tuple[str, int, str, str]]:
    """Return `(file, line, module, owner)` for each import of another workspace package's private module."""
    names = owners(root)
    found: list[tuple[str, int, str, str]] = []
    for path in sorted(root.glob("packages/*/src/**/*.py")):
        relative = path.relative_to(root)
        for node in ast.walk(ast.parse(path.read_text(), relative.as_posix())):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for module in _modules(node):
                owner = names.get(module.split(".")[0])
                if owner is not None and owner != relative.parts[1]:
                    found.append((relative.as_posix(), node.lineno, module, owner))
    return found


def main(root: Path = ROOT, allowed: frozenset[tuple[str, str]] = ALLOWED) -> int:
    imports = cross_package_imports(root)
    where = "ALLOWED in .github/scripts/check_architecture.py"
    findings = [f"{file}:{line}: imports {module}, a private module of workspace package {owner}: "
                + f"use its public API, or record an ADR and add it to {where}"
                for file, line, module, owner in imports if (file, module) not in allowed]
    present = {(file, module) for file, _line, module, _owner in imports}
    findings += [f"{file}: ALLOWED names {module}, but the import is gone: remove the entry from {where}"
                 for file, module in sorted(allowed - present)]
    for finding in findings:
        print(finding)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
