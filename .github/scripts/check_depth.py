#!/usr/bin/env python3
"""Depth ratchet: measure six depth keys per slice and compare each slice with its own baseline.

Ported from sliced-bread-depth-ratchet (paulnsorensen/sliced-bread-architecture#71) with a Python `ast` backend.
Every key is lower-is-better with tolerance 0. A slice is a `[[slice]]` of `slices.toml`; its crust is its `crust` list.

    check_depth.py measure [--json]
    check_depth.py init [--mode report|block]       refuses to overwrite depth-ratchet.json
    check_depth.py check                            exit 1 on a regression or an orphan in block mode
    check_depth.py tighten [--accept-denominator-gains] [--adopt-new]
    check_depth.py diff-baseline --base REF         list loosening against REF; informs only

Exit codes: 0 pass or report mode, 1 regression in block mode or a refused tighten, 2 input error.
Dead internal code is in the denominators: the Vulture lane (check_dead_code.py) removes it instead.
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import cast

import slice_map
from slice_map import Graph, MapError, Node, SliceMap

ROOT = Path(__file__).resolve().parents[2]
BASELINE_FILE = "depth-ratchet.json"
# Increment when a definition change moves a number; check then refuses an old baseline until a human re-baselines.
MEASUREMENT_VERSION = 1
SHARES = {"M08.share": "M08.crust_logic_statements", "M10.share": "M10.crust_complexity"}
KEYS = ("M08.crust_logic_statements", "M08.share", "M09.public_surface", "M10.crust_complexity", "M10.share",
        "M12.unused_exports")
MODES = ("report", "block")
SIZE_AWARE_STATEMENTS = 200  # Advisory only (ADR-003): never blocks, in any mode.
FunctionLike = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
Value = int | tuple[int, int]  # a count, or a share as (numerator, denominator)


def _docstring(statement: ast.stmt, body: list[ast.stmt]) -> bool:
    return (statement is body[0] and isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str))


def logic_statements(tree: ast.AST) -> int:
    """Count statements inside function bodies; a lambda counts 1. Nested `def`/`class` lines and docstrings count 0."""
    total = 0

    def visit(node: ast.AST, inside: bool) -> None:
        nonlocal total
        body = cast(list[ast.stmt], getattr(node, "body", []))
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Lambda):
                total += 1
            elif (inside and isinstance(child, ast.stmt)
                  and not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  and not (body and _docstring(child, body))):
                total += 1
            visit(child, inside or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)))

    visit(tree, False)
    return total


def _decisions(node: ast.AST) -> int:
    if isinstance(node, (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler)):
        return 1
    if isinstance(node, ast.BoolOp):
        return len(node.values) - 1
    if isinstance(node, ast.comprehension):
        return 1 + len(node.ifs)
    if isinstance(node, ast.match_case):
        wildcard = isinstance(node.pattern, ast.MatchAs) and node.pattern.pattern is None
        return (0 if wildcard else 1) + (node.guard is not None)
    return 0


def complexity(tree: ast.AST) -> int:
    """Sum cyclomatic complexity over function-likes: 1 plus each decision point and parameter default in its own code."""
    total = 0
    for function in ast.walk(tree):
        if not isinstance(function, FunctionLike):
            continue
        total += 1 + len(function.args.defaults) + sum(item is not None for item in function.args.kw_defaults)
        pending: list[ast.AST] = [function.body] if isinstance(function, ast.Lambda) else list(function.body)
        while pending:
            node = pending.pop()
            if isinstance(node, FunctionLike):
                continue  # A nested function is its own unit.
            total += _decisions(node)
            pending.extend(ast.iter_child_nodes(node))
    return total


def _calls(node: ast.AST) -> bool:
    return any(isinstance(child, ast.Call) for child in ast.walk(node))


def module_init(tree: ast.Module) -> int:
    """Count top-level executable statements: logic at module scope leaves M08 and M10, so the catalog shows it."""
    count = 0
    for statement in tree.body:
        if isinstance(statement, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if _docstring(statement, tree.body) or (isinstance(statement, ast.If) and ast.unparse(statement.test)
                                                in {"TYPE_CHECKING", "typing.TYPE_CHECKING"}):
            continue
        if isinstance(statement, (ast.Assign, ast.AnnAssign)) and not _calls(statement):
            continue
        count += 1
    return count


def _all(tree: ast.Module) -> list[str] | None:
    """Return the names of a literal `__all__`, or None when the module has no `__all__` or a computed one."""
    for statement in tree.body:
        if (isinstance(statement, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__all__"
                                                      for target in statement.targets)):
            if not isinstance(statement.value, (ast.List, ast.Tuple)) or not all(
                    isinstance(item, ast.Constant) and isinstance(item.value, str) for item in statement.value.elts):
                return None
            return sorted({str(item.value) for item in statement.value.elts if isinstance(item, ast.Constant)})
    return None


def exports(tree: ast.Module) -> list[str]:
    """Return the export slots of one crust module: `__all__` when it is a literal, else its public top-level names."""
    if (listed := _all(tree)) is not None:
        return listed
    if any(isinstance(node, ast.Name) and node.id == "__all__" for node in ast.walk(tree)):
        raise MapError("a crust `__all__` must be a literal list or tuple of strings")
    names: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(statement.name)
        elif isinstance(statement, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.split(".")[0] for alias in statement.names if alias.name != "*")
        elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            names.update(node.id for target in targets for node in ast.walk(target) if isinstance(node, ast.Name))
    return sorted(name for name in names if not name.startswith("_"))


def _uses(tree: ast.Module, module: str, is_package: bool, crust: str, names: list[str]) -> set[str]:
    """Return the crust export names that one outside file uses through the crust module `crust`."""
    used: set[str] = set()
    loads = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    listed = set(_all(tree) or ())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and slice_map.absolute(node, module, is_package) == crust:
            for alias in node.names:
                if alias.name == "*":
                    return set(names)
                if alias.name in names and ((local := alias.asname or alias.name) in loads or local in listed):
                    used.add(alias.name)
    bound = {local for local, target in slice_map.module_bindings(tree, module, is_package).items() if target == crust}
    chains = {local: target for local, target in slice_map.module_bindings(tree, module, is_package).items()
              if crust.startswith(target + ".")}
    values: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        values.add(id(node.value))
        local, _, rest = (slice_map.dotted(node) or "").partition(".")
        if local in bound:
            full = f"{crust}.{rest}"
        elif local in chains:
            full = f"{chains[local]}.{rest}"
        else:
            continue
        if full.startswith(crust + ".") and (name := full.removeprefix(crust + ".").split(".")[0]) in names:
            used.add(name)
    bare = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id in bound and id(node) not in values]
    return set(names) if bare else used


@dataclass(frozen=True)
class Measure:
    values: dict[str, Value]
    exports: dict[str, list[str]]
    unused: list[str]
    module_init: int


def measure(slices: SliceMap, graph: Graph) -> dict[str, Measure]:
    """Return the depth measure of each `[[slice]]`."""
    found: dict[str, Measure] = {}
    for node in (item for item in slices.nodes if item.kind == "slice"):
        owned = [file for file in graph.files if node.owns(file)]
        crust = [file for file in owned if file in node.crust]
        statements = {file: logic_statements(graph.trees[file]) for file in owned}
        branches = {file: complexity(graph.trees[file]) for file in owned}
        crust_statements = sum(statements[file] for file in crust)
        crust_complexity = sum(branches[file] for file in crust)
        slots = {file: exports(graph.trees[file]) for file in crust}
        unused: list[str] = []
        for file in crust:
            used = _external_uses(slices, graph, node, file, slots[file])
            unused += [f"{graph.files[file]}.{name}" for name in slots[file] if name not in used]
        found[node.name] = Measure({
            "M08.crust_logic_statements": crust_statements,
            "M08.share": (crust_statements, sum(statements.values())),
            "M09.public_surface": sum(len(names) for names in slots.values()),
            "M10.crust_complexity": crust_complexity,
            "M10.share": (crust_complexity, sum(branches.values())),
            "M12.unused_exports": len(unused),
        }, slots, unused, sum(module_init(graph.trees[file]) for file in owned))
    return found


def _external_uses(slices: SliceMap, graph: Graph, node: Node, crust: str, names: list[str]) -> set[str]:
    used: set[str] = set()
    for file, module in graph.files.items():
        owner = slices.owner(file)
        if owner is not None and owner.name != node.name:
            used |= _uses(graph.trees[file], module, file.endswith("/__init__.py"), graph.files[crust], names)
    return used


def _show(value: Value) -> str:
    return f"{value[0]}/{value[1]}" if isinstance(value, tuple) else str(value)


def _number(value: Value) -> Fraction:
    return Fraction(value[0], value[1] or 1) if isinstance(value, tuple) else Fraction(value)


def _parse_value(key: str, raw: object, where: str) -> Value:
    if key in SHARES:
        if isinstance(raw, str) and (parts := raw.split("/")) and len(parts) == 2 and all(p.isdigit() for p in parts):
            return int(parts[0]), int(parts[1])
        raise MapError(f"{where}: {key} must be a 'numerator/denominator' string")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
        return raw
    raise MapError(f"{where}: {key} must be a non-negative integer")


@dataclass(frozen=True)
class Baseline:
    mode: str
    slices: dict[str, dict[str, Value]]


def decode(text: str, where: str) -> Baseline:
    try:
        document = cast(object, json.loads(text))
    except json.JSONDecodeError as error:
        raise MapError(f"{where}: {error}") from error
    top = slice_map.table(document, where, {"version", "measurement_version", "mode", "slices"}, set())
    if top["version"] != 1 or top["mode"] not in MODES:
        raise MapError(f"{where}: version must be 1 and mode one of {MODES}")
    if top["measurement_version"] != MEASUREMENT_VERSION:
        raise MapError(f"{where}: measurement_version {top['measurement_version']} is not {MEASUREMENT_VERSION}; "
                       + "a human re-baselines after a definition change")
    caps: dict[str, dict[str, Value]] = {}
    for name, row in slice_map.table(top["slices"], f"{where} slices", set(), None).items():
        fields = slice_map.table(row, f"{where} slice {name}", set(), set(KEYS))
        caps[name] = {key: _parse_value(key, raw, f"{where} {name}") for key, raw in fields.items()}
    return Baseline(str(top["mode"]), caps)


def encode(baseline: Baseline) -> str:
    slices = {name: {key: _show(caps[key]) if key in SHARES else caps[key] for key in KEYS if key in caps}
              for name, caps in sorted(baseline.slices.items())}
    return json.dumps({"version": 1, "measurement_version": MEASUREMENT_VERSION, "mode": baseline.mode,
                       "slices": slices}, indent=2) + "\n"


def _numerator(value: Value) -> int:
    return value if isinstance(value, int) else value[0]


@dataclass(frozen=True)
class Comparison:
    lines: list[str]
    regressed: bool
    orphaned: bool
    improved: dict[str, dict[str, Value]]  # slice -> key -> new cap
    held: list[str]
    new: list[str]


def compare(found: dict[str, Measure], baseline: Baseline, accept_denominator_gains: bool = False) -> Comparison:
    lines: list[str] = []
    regressed = False
    improved: dict[str, dict[str, Value]] = {}
    held: list[str] = []
    for name, result in sorted(found.items()):
        caps = baseline.slices.get(name)
        if caps is None:
            continue
        for key in KEYS:
            value = result.values[key]
            if key not in caps:
                lines.append(f"new {name} {key} = {_show(value)} (no cap)")
                continue
            cap = caps[key]
            if _number(value) > _number(cap):
                regressed = True
                note = ""
                if key in SHARES and _numerator(value) <= _numerator(cap):
                    note = " denominator-shrink: the crust did not grow; internal code shrank"
                lines.append(f"regressed {name} {key} {_show(cap)} -> {_show(value)}{note}")
            elif _number(value) < _number(cap):
                if key in SHARES and not accept_denominator_gains and _numerator(value) >= _numerator(cap):
                    held.append(f"held {name} {key} {_show(cap)} -> {_show(value)}: the share fell only because "
                                + "internal code grew; a human may pass --accept-denominator-gains")
                else:
                    improved.setdefault(name, {})[key] = value
                    lines.append(f"improved {name} {key} {_show(cap)} -> {_show(value)}: run `tighten`")
    new = sorted(set(found) - set(baseline.slices))
    lines += [f"new {name}: no baseline; `tighten --adopt-new` adds it" for name in new]
    orphans = sorted(set(baseline.slices) - set(found))
    lines += [f"orphan {name}: depth-ratchet.json holds a slice that slices.toml does not declare" for name in orphans]
    return Comparison(lines, regressed, bool(orphans), improved, held, new)


def _advisories(found: dict[str, Measure]) -> list[str]:
    lines: list[str] = []
    for name, result in sorted(found.items()):
        share = result.values["M08.share"]
        if _number(result.values["M08.crust_logic_statements"]) > SIZE_AWARE_STATEMENTS and _number(share) > Fraction(1, 2):
            lines.append(f"advisory M08.size-aware {name}: crust holds {_show(share)} of the slice's logic statements")
    return lines


def _coverage(graph: Graph) -> list[str]:
    """Name each computed import, which neither the guard nor M12 follows."""
    found: list[str] = []
    for file, tree in graph.trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and slice_map.dotted(node.func) in {"importlib.import_module", "__import__",
                                                                               "import_module"}:
                found.append(f"{file}:{node.lineno}")
    return [f"coverage: computed imports that no number follows: {', '.join(found)}"] if found else []


def _load(root: Path) -> tuple[Graph, dict[str, Measure]]:
    slices = slice_map.load(root)
    graph = slice_map.build(root, slices)
    return graph, measure(slices, graph)


def _table(found: dict[str, Measure]) -> str:
    header = ["slice", *KEYS, "module_init"]
    rows = [[name, *(_show(result.values[key]) for key in KEYS), str(result.module_init)]
            for name, result in sorted(found.items())]
    widths = [max(len(row[index]) for row in [header, *rows]) for index in range(len(header))]
    return "\n".join("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
                     for row in [header, *rows])


class _Args(argparse.Namespace):
    command: str = ""
    json: bool = False
    mode: str = "report"
    accept_denominator_gains: bool = False
    adopt_new: bool = False
    base: str = ""


def run(root: Path, args: _Args) -> int:
    command = args.command
    path = root / BASELINE_FILE
    if command == "diff-baseline":
        return diff_baseline(root, args.base)
    graph, found = _load(root)
    if command == "measure":
        if args.json:
            print(json.dumps({name: {"values": {key: _show(value) for key, value in result.values.items()},
                                     "exports": result.exports, "unused_exports": result.unused,
                                     "module_init": result.module_init} for name, result in sorted(found.items())},
                             indent=2))
        else:
            print(_table(found))
        return 0
    if command == "init":
        if path.exists():
            print(f"{BASELINE_FILE} exists; init refuses to overwrite it", file=sys.stderr)
            return 1
        _ = path.write_text(encode(Baseline(args.mode, {n: dict(r.values) for n, r in found.items()})))
        print(_table(found))
        return 0
    if not path.is_file():
        raise MapError(f"{BASELINE_FILE} is missing; run `init` in the adoption change")
    baseline = decode(path.read_text(), BASELINE_FILE)
    if command == "check":
        result = compare(found, baseline)
        lines = result.lines + _advisories(found) + _coverage(graph)
        print("\n".join(lines) if lines else f"depth ratchet: pass ({len(found)} slices, mode {baseline.mode})")
        failed = result.regressed or result.orphaned
        if failed and baseline.mode == "block":
            print("depth ratchet: a slice is shallower than its baseline. Move the logic behind the crust, "
                  + "or ask the human owner to raise the cap in depth-ratchet.json")
            return 1
        return 0
    result = compare(found, baseline, args.accept_denominator_gains)
    if result.regressed or result.orphaned:
        print("\n".join(result.lines))
        print("tighten refuses while a metric regressed or a slice is orphaned; nothing written")
        return 1
    slices = {name: dict(caps) for name, caps in baseline.slices.items()}
    for name, caps in result.improved.items():
        slices[name].update(caps)
    if args.adopt_new:
        slices.update({name: dict(found[name].values) for name in result.new})
    _ = path.write_text(encode(Baseline(baseline.mode, slices)))
    print("\n".join([line.replace(": run `tighten`", "") for line in result.lines if line.startswith("improved")]
                    + result.held) or "tighten: no cap changed")
    return 0


def diff_baseline(root: Path, base: str) -> int:
    """Print each loosening of depth-ratchet.json against `base`; it informs the reviewer and approves nothing."""
    shown = subprocess.run(["git", "show", f"{base}:{BASELINE_FILE}"], cwd=root, capture_output=True, text=True,
                           check=False)
    if shown.returncode:
        raise MapError(f"{base} has no {BASELINE_FILE}")
    try:
        document = cast(object, json.loads(shown.stdout))
    except json.JSONDecodeError as error:
        raise MapError(f"{base}:{BASELINE_FILE}: {error}") from error
    raw = slice_map.table(document, f"{base}:{BASELINE_FILE}", set(), {"version", "measurement_version", "mode", "slices"})
    if raw.get("measurement_version") != MEASUREMENT_VERSION:
        print(f"loosened measurement_version {raw.get('measurement_version')} -> {MEASUREMENT_VERSION}")
        return 0
    before = decode(shown.stdout, f"{base}:{BASELINE_FILE}")
    after = decode((root / BASELINE_FILE).read_text(), BASELINE_FILE)
    lines = ["loosened mode block -> report"] if before.mode == "block" and after.mode == "report" else []
    for name, caps in sorted(before.slices.items()):
        if name not in after.slices:
            lines.append(f"loosened removed slice {name}")
            continue
        for key, cap in caps.items():
            if key not in after.slices[name]:
                lines.append(f"loosened removed {name} {key}")
            elif _number(after.slices[name][key]) > _number(cap):
                lines.append(f"loosened raised {name} {key} {_show(cap)} -> {_show(after.slices[name][key])}")
    print("\n".join(lines) or "no loosening")
    return 0


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(prog="check_depth.py", description="Ratchet per-slice depth metrics.")
    commands = parser.add_subparsers(dest="command", required=True)
    _ = commands.add_parser("measure").add_argument("--json", action="store_true")
    _ = commands.add_parser("init").add_argument("--mode", choices=MODES, default="report")
    _ = commands.add_parser("check")
    tighten = commands.add_parser("tighten")
    _ = tighten.add_argument("--accept-denominator-gains", action="store_true")
    _ = tighten.add_argument("--adopt-new", action="store_true")
    _ = commands.add_parser("diff-baseline").add_argument("--base", required=True)
    try:
        return run(root, parser.parse_args(argv, namespace=_Args()))
    except MapError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
