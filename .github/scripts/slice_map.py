"""Load `slices.toml` and build the module import graph that the slice guard and the depth ratchet read.

The schema follows sliced-bread-guard (paulnsorensen/sliced-bread-architecture#69) with a Python backend:
`[policy] language` is `"python"`, and `[policy] roots` names the import roots, such as `packages/fromargs/src`.
Each `.py` file under a root is a module. Its dotted name is its path below the root.
An input error raises `MapError`; the callers exit 2 on it.
"""
from __future__ import annotations

import ast
import fnmatch
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

SHAPES = frozenset({"thin-facade", "framework-bound", "service"})
SUPPRESSIBLE = frozenset({"SB-01", "SB-02", "SB-03", "SB-04", "SB-06"})
MAP_FILE = "slices.toml"
SOURCE_TREES = "packages/*/src"  # every workspace source tree; `[policy] roots` must list each one


class MapError(Exception):
    """`slices.toml`, a baseline, or the source tree is not valid input."""


@dataclass(frozen=True)
class Node:
    name: str
    kind: str  # "slice" or "spine"
    paths: tuple[str, ...]
    crust: tuple[str, ...]
    may_depend_on: frozenset[str]
    requires: frozenset[str]

    def owns(self, file: str) -> bool:
        return any(file == path or file.startswith(path + "/") for path in self.paths)


@dataclass(frozen=True)
class Suppression:
    rule: str
    source: str
    target: str


@dataclass(frozen=True)
class SliceMap:
    roots: tuple[str, ...]
    ignore: tuple[str, ...]
    nodes: tuple[Node, ...]
    suppressions: tuple[Suppression, ...]

    def owner(self, file: str) -> Node | None:
        return next((node for node in self.nodes if node.owns(file)), None)

    def node(self, name: str) -> Node:
        return next(node for node in self.nodes if node.name == name)


def table(value: object, where: str, required: set[str], optional: set[str] | None) -> dict[str, object]:
    """Return `value` as a TOML or JSON table with every required field; `optional=None` admits any other key."""
    if not isinstance(value, dict):
        raise MapError(f"{where} must be a table")
    found = {str(key): item for key, item in cast(dict[object, object], value).items()}
    unknown = [] if optional is None else sorted(found.keys() - required - optional)
    missing = sorted(required - found.keys())
    if unknown or missing:
        raise MapError(f"{where}: unknown fields {unknown}, missing fields {missing}")
    return found


def array(value: object, where: str) -> list[object]:
    if not isinstance(value, list):
        raise MapError(f"{where} must be an array")
    return cast(list[object], value)


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise MapError(f"{where} must be a non-empty string")
    return value


def _texts(value: object, where: str) -> tuple[str, ...]:
    items = tuple(_text(item, where) for item in array(value, where))
    if len(set(items)) != len(items):
        raise MapError(f"{where} holds a duplicate")
    return items


def _paths(value: object, where: str, root: Path) -> tuple[str, ...]:
    paths = _texts(value, where)
    for path in paths:
        if any(mark in path for mark in "*?[") or path.endswith("/") or path.startswith("/") or ".." in path.split("/"):
            raise MapError(f"{where}: {path!r} must be a plain relative path without a glob")
        if not (root / path).exists():
            raise MapError(f"{where}: {path!r} does not exist")
    return paths


def _node(value: object, kind: str, root: Path) -> Node:
    required = {"name", "paths", "may_depend_on"} | ({"crust", "shape"} if kind == "slice" else set[str]())
    fields = table(value, f"[[{kind}]]", required, {"requires"} if kind == "slice" else set())
    name = _text(fields["name"], f"[[{kind}]] name")
    where = f"[[{kind}]] {name}"
    paths = _paths(fields["paths"], f"{where} paths", root)
    crust: tuple[str, ...] = ()
    if kind == "slice":
        crust = _paths(fields["crust"], f"{where} crust", root)
        if not crust:
            raise MapError(f"{where} crust must name at least one file")
        outside = [file for file in crust if not any(file == path or file.startswith(path + "/") for path in paths)]
        if outside or not all((root / file).is_file() for file in crust):
            raise MapError(f"{where} crust must be files inside its paths: {outside or list(crust)}")
        if fields["shape"] not in SHAPES:
            raise MapError(f"{where} shape must be one of {sorted(SHAPES)}")
    may = frozenset(_texts(fields["may_depend_on"], f"{where} may_depend_on"))
    requires = frozenset(_texts(fields.get("requires", []), f"{where} requires"))
    if not requires <= may:
        raise MapError(f"{where} requires {sorted(requires - may)}, which may_depend_on does not hold")
    return Node(name, kind, paths, crust, may, requires)


def parse(text: str, root: Path) -> SliceMap:
    """Return the validated slice map that `text` declares for the tree at `root`."""
    try:
        document = cast(object, tomllib.loads(text))
    except tomllib.TOMLDecodeError as error:
        raise MapError(f"{MAP_FILE}: {error}") from error
    top = table(document, MAP_FILE, {"version", "policy"}, {"slice", "spine", "suppress"})
    if top["version"] != 1:
        raise MapError(f"{MAP_FILE}: version must be 1")
    policy = table(top["policy"], "[policy]", {"language", "unmapped_files", "suppressions", "roots"}, {"ignore"})
    for field, expected in (("language", "python"), ("unmapped_files", "error"), ("suppressions", "human-only")):
        if policy[field] != expected:
            raise MapError(f"[policy] {field} must be {expected!r}")
    roots = _paths(policy["roots"], "[policy] roots", root)
    unlisted = sorted({tree.relative_to(root).as_posix() for tree in root.glob(SOURCE_TREES) if tree.is_dir()}
                      - set(roots))
    if unlisted:
        raise MapError(f"[policy] roots must list every {SOURCE_TREES} tree; missing {unlisted}")
    ignore = _texts(policy.get("ignore", []), "[policy] ignore")
    nodes: list[Node] = []
    for kind in ("slice", "spine"):
        nodes += [_node(entry, kind, root) for entry in array(top.get(kind, []), f"[[{kind}]]")]
    names = [node.name for node in nodes]
    if len(set(names)) != len(names):
        raise MapError(f"{MAP_FILE}: duplicate node names")
    for node in nodes:
        unknown = sorted(node.may_depend_on - set(names))
        if unknown or node.name in node.may_depend_on:
            raise MapError(f"{node.name} may_depend_on names unknown nodes {unknown} or itself")
    claimed = [(path, node.name) for node in nodes for path in node.paths]
    for path, name in claimed:
        for other, other_name in claimed:
            if (path, name) != (other, other_name) and (path == other or other.startswith(path + "/")):
                raise MapError(f"{MAP_FILE}: path {other!r} of {other_name} overlaps path {path!r} of {name}")
    suppressions: list[Suppression] = []
    for entry in array(top.get("suppress", []), "[[suppress]]"):
        fields = table(entry, "[[suppress]]", {"rule", "from", "to", "reason"}, set())
        if fields["rule"] not in SUPPRESSIBLE:
            raise MapError(f"[[suppress]] rule must be one of {sorted(SUPPRESSIBLE)}")
        _ = _text(fields["reason"], "[[suppress]] reason")
        suppressions.append(Suppression(str(fields["rule"]), _text(fields["from"], "[[suppress]] from"),
                                        _text(fields["to"], "[[suppress]] to")))
    return SliceMap(roots, ignore, tuple(nodes), tuple(suppressions))


def load(root: Path) -> SliceMap:
    path = root / MAP_FILE
    if not path.is_file():
        raise MapError(f"{MAP_FILE} is missing at {root}")
    return parse(path.read_text(), root)


@dataclass(frozen=True)
class Edge:
    source: str  # importing file, relative to the repository root
    target: str  # imported file
    line: int


@dataclass(frozen=True)
class Unresolved:
    source: str
    specifier: str
    line: int


@dataclass(frozen=True)
class Graph:
    files: dict[str, str]  # file -> dotted module name, for every inventoried file
    modules: dict[str, str]  # dotted module name -> file
    trees: dict[str, ast.Module]
    edges: tuple[Edge, ...]
    unresolved: tuple[Unresolved, ...]
    symlinks: tuple[str, ...]


def _inventory(root: Path, slice_map: SliceMap) -> tuple[dict[str, str], list[str]]:
    """Return the module name of each `.py` file under the roots, and each symlinked directory or `.py` file.

    The walk does not follow a symlink, so a symlinked directory is reported, not skipped.
    """
    files: dict[str, str] = {}
    symlinks: list[str] = []
    for base in slice_map.roots:
        top = root / base
        if any((root / part).is_symlink() for part in (Path(base), *Path(base).parents) if part != Path(".")):
            symlinks.append(base)
            continue
        for directory, dirnames, filenames in os.walk(top):
            dirnames.sort()
            for name in sorted(dirnames + filenames):
                path = Path(directory) / name
                relative = path.relative_to(root).as_posix()
                if any(fnmatch.fnmatchcase(relative, pattern) for pattern in slice_map.ignore):
                    continue
                if path.is_symlink():
                    if name in dirnames or name.endswith(".py"):
                        symlinks.append(relative)
                    continue
                if name in filenames and name.endswith(".py"):
                    parts = path.relative_to(top).with_suffix("").parts
                    files[relative] = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
    return files, sorted(symlinks)


def dotted(node: ast.expr) -> str | None:
    """Return `a.b.c` for a chain of attributes on a name, or None for any other expression."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (base := dotted(node.value)):
        return f"{base}.{node.attr}"
    return None


def module_bindings(tree: ast.Module, module: str, is_package: bool) -> dict[str, str]:
    """Map each local name that an import binds to a module, to that module's dotted name (absolute)."""
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and (base := absolute(node, module, is_package)):
            for alias in node.names:
                if alias.name != "*":
                    bound[alias.asname or alias.name] = f"{base}.{alias.name}"
    return bound


def absolute(node: ast.ImportFrom, module: str, is_package: bool) -> str | None:
    """Return the absolute module that a `from ... import` statement reads, or None when it climbs above the top."""
    if not node.level:
        return node.module
    package = module.split(".") if is_package else module.split(".")[:-1]
    if node.level - 1 >= len(package):
        return None
    kept = package[:len(package) - (node.level - 1)]
    return ".".join([*kept, *([node.module] if node.module else [])])


def build(root: Path, slice_map: SliceMap) -> Graph:
    """Parse every inventoried file and return its resolved import edges and its unresolved imports."""
    files, symlinks = _inventory(root, slice_map)
    modules = {module: file for file, module in files.items()}
    tops = {module.split(".")[0] for module in modules}
    trees: dict[str, ast.Module] = {}
    edges: set[Edge] = set()
    unresolved: set[Unresolved] = set()
    for file, module in files.items():
        try:
            tree = ast.parse((root / file).read_text(), file)
        except SyntaxError as error:
            raise MapError(f"{file}: {error}") from error
        trees[file] = tree
        is_package = file.endswith("/__init__.py")

        def edge(target: str, line: int, source: str = file) -> None:
            if modules[target] != source:
                edges.add(Edge(source, modules[target], line))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] not in tops:
                        continue
                    if alias.name in modules:
                        edge(alias.name, node.lineno)
                    else:
                        unresolved.add(Unresolved(file, alias.name, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                base = absolute(node, module, is_package)
                if base is None or (node.level == 0 and base.split(".")[0] not in tops):
                    if base is None:
                        unresolved.add(Unresolved(file, "." * node.level + (node.module or ""), node.lineno))
                    continue
                for alias in node.names:
                    if f"{base}.{alias.name}" in modules:
                        edge(f"{base}.{alias.name}", node.lineno)
                    elif base in modules:
                        edge(base, node.lineno)
                    else:
                        unresolved.add(Unresolved(file, base, node.lineno))
        bound = {name: target for name, target in module_bindings(tree, module, is_package).items() if target in modules}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and (chain := dotted(node)):
                name, _, rest = chain.partition(".")
                if name in bound and (target := f"{bound[name]}.{rest}") in modules:
                    edge(target, node.lineno)
    return Graph(files, modules, trees, tuple(sorted(edges, key=lambda e: (e.source, e.target, e.line))),
                 tuple(sorted(unresolved, key=lambda u: (u.source, u.specifier, u.line))), tuple(symlinks))
