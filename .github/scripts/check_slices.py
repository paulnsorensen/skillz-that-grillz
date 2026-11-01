#!/usr/bin/env python3
"""Slice guard: hold the Python source to the slice map in `slices.toml` (rules SB-00 to SB-07).

Ported from sliced-bread-guard (paulnsorensen/sliced-bread-architecture#69) with a Python `ast` backend.
The gate compares identity sets, not counts, so a fixed violation cannot pay for a new one.
`sbguard-baseline.json` holds the adopted violations and only shrinks.

    check_slices.py check [--base REF|auto] [--strict]   exit 0 pass, 1 rule failure, 2 input error
    check_slices.py baseline --init | --shrink

Tests are outside the inventory: `[policy] roots` names `src/` trees only (AGENTS.md lets a test import internals),
so SB-05 has no Python counterpart. A computed import such as `importlib.import_module(name)` is not seen.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tokenize
from collections.abc import Iterable
from pathlib import Path
from typing import cast

import slice_map
from slice_map import Graph, MapError, SliceMap

ROOT = Path(__file__).resolve().parents[2]
BASELINE_FILE = "sbguard-baseline.json"
TITLES = {
    "SB-00": "unresolved import",
    "SB-01": "crust bypass",
    "SB-02": "undeclared dependency",
    "SB-03": "dependency cycle",
    "SB-04": "unmapped source file",
    "SB-06": "missing required dependency",
    "SB-07": "suppression or baseline growth",
}
DIRECTIVE = re.compile(r"\bsb-?guard-(?:ignore|disable)\b", re.IGNORECASE)
Identity = tuple[str, str, str]  # (rule, from, to)


def _node_edges(slices: SliceMap, graph: Graph) -> list[tuple[str, str, str, str]]:
    """Return `(source file, target file, source node, target node)` for each edge between two mapped nodes."""
    found: list[tuple[str, str, str, str]] = []
    for edge in graph.edges:
        source, target = slices.owner(edge.source), slices.owner(edge.target)
        if source and target and source.name != target.name:
            found.append((edge.source, edge.target, source.name, target.name))
    return found


def _cycles(edges: list[tuple[str, str, str, str]]) -> set[Identity]:
    """Return one SB-03 identity per file edge inside a node-level strongly connected component."""
    graph: dict[str, set[str]] = {}
    for _source, _target, a, b in edges:
        graph.setdefault(a, set()).add(b)
        _ = graph.setdefault(b, set())
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    component: dict[str, int] = {}

    def visit(node: str) -> None:  # Tarjan; the node graph is small, so recursion depth is no concern.
        index[node] = low[node] = len(index)
        stack.append(node)
        for other in sorted(graph[node]):
            if other not in index:
                visit(other)
                low[node] = min(low[node], low[other])
            elif other in stack:
                low[node] = min(low[node], index[other])
        if low[node] == index[node]:
            while True:
                member = stack.pop()
                component[member] = index[node]
                if member == node:
                    break

    for node in sorted(graph):
        if node not in index:
            visit(node)
    return {("SB-03", source, target) for source, target, a, b in edges if component[a] == component[b]}


def _directives(root: Path, files: Iterable[str]) -> set[Identity]:
    """Return one SB-07 identity per inline guard directive; the hash covers the comment and the next code line."""
    found: set[Identity] = set()
    for file in files:
        text = (root / file).read_text()
        lines = text.splitlines()
        seen: dict[str, int] = {}
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type != tokenize.COMMENT or not (match := DIRECTIVE.search(token.string)):
                continue
            row = token.end[0]
            following = next((line.strip() for line in lines[row:] if line.strip()), "")
            digest = hashlib.sha256("\n".join([token.string, lines[row - 1], following]).encode()).hexdigest()[:12]
            seen[digest] = seen.get(digest, 0) + 1
            found.add(("SB-07", file, f"inline:{match[0].lower()}:{digest}:{seen[digest]}"))
    return found


def violations(root: Path, slices: SliceMap, graph: Graph) -> tuple[set[Identity], set[Identity]]:
    """Return `(SB-00 identities, baselinable identities)` for the tree at `root`."""
    unresolved = {("SB-00", item.source, item.specifier) for item in graph.unresolved}
    found: set[Identity] = {("SB-04", file, "symlink") for file in graph.symlinks}
    found |= {("SB-04", file, "unmapped") for file in graph.files if slices.owner(file) is None}
    edges = _node_edges(slices, graph)
    for source, target, a, b in edges:
        node = slices.node(b)
        if node.kind == "slice" and target not in node.crust:
            found.add(("SB-01", source, target))
        if b not in slices.node(a).may_depend_on:
            found.add(("SB-02", source, target))
    found |= _cycles(edges)
    present = {(a, b) for _source, _target, a, b in edges}
    found |= {("SB-06", node.name, required) for node in slices.nodes for required in sorted(node.requires)
              if (node.name, required) not in present}
    found |= _directives(root, graph.files)
    return unresolved, found


def _encode(entries: Iterable[Identity]) -> str:
    rows = [{"rule": rule, "from": source, "to": target} for rule, source, target in sorted(entries)]
    return json.dumps({"version": 1, "entries": rows}, indent=2) + "\n"


def decode(text: str, where: str) -> set[Identity]:
    try:
        document = cast(object, json.loads(text))
    except json.JSONDecodeError as error:
        raise MapError(f"{where}: {error}") from error
    top = slice_map.table(document, where, {"version", "entries"}, set())
    if top["version"] != 1:
        raise MapError(f"{where}: version must be 1")
    entries: set[Identity] = set()
    for row in slice_map.array(top["entries"], f"{where} entries"):
        fields = slice_map.table(row, f"{where} entry", {"rule", "from", "to"}, set())
        rule, source, target = fields["rule"], fields["from"], fields["to"]
        if not (isinstance(rule, str) and isinstance(source, str) and isinstance(target, str)):
            raise MapError(f"{where}: each entry holds the strings rule, from, and to")
        entries.add((rule, source, target))
    return entries


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)


def resolve_base(root: Path, base: str) -> str:
    """Return the commit that `base` names; `auto` is `$GATE_BASE`, `HEAD^1` in GitHub Actions, else the merge base with origin/main.

    In a pull request run, GitHub checks out a merge commit whose first parent is the base branch tip.
    """
    if base == "auto":
        base = os.environ.get("GATE_BASE") or ("HEAD^1" if os.environ.get("GITHUB_ACTIONS") == "true" else "")
        if not base:
            merged = _git(root, "merge-base", "HEAD", "origin/main")
            if merged.returncode:
                raise MapError("no base: fetch origin/main, or set GATE_BASE to the base commit")
            base = merged.stdout.strip()
    resolved = _git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    if resolved.returncode:
        raise MapError(f"base {base!r} is not a commit here (a shallow clone needs fetch-depth 2)")
    return resolved.stdout.strip()


def _at(root: Path, commit: str, path: str) -> str | None:
    shown = _git(root, "show", f"{commit}:{path}")
    return shown.stdout if shown.returncode == 0 else None


def history(root: Path, commit: str, current: set[Identity] | None) -> set[Identity]:
    """Return SB-07 identities for baseline entries that the base does not hold, or for a deleted baseline."""
    if _at(root, commit, slice_map.MAP_FILE) is None:
        return set()  # The base has no slice map, so this change is the adoption.
    before = _at(root, commit, BASELINE_FILE)
    if before is None:
        return set() if current is None else {("SB-07", BASELINE_FILE, f"baseline:grew:{'|'.join(entry)}")
                                               for entry in current}
    if current is None:
        return {("SB-07", BASELINE_FILE, "baseline:deleted")}
    grew = current - decode(before, f"{commit}:{BASELINE_FILE}")
    return {("SB-07", BASELINE_FILE, f"baseline:grew:{'|'.join(entry)}") for entry in grew}


def _line(identity: Identity) -> str:
    rule, source, target = identity
    return f"{rule} {TITLES[rule]}: {source} -> {target}"


def check(root: Path, base: str, strict: bool) -> int:
    slices = slice_map.load(root)
    graph = slice_map.build(root, slices)
    unresolved, found = violations(root, slices, graph)
    path = root / BASELINE_FILE
    baseline = decode(path.read_text(), BASELINE_FILE) if path.is_file() else None
    suppressed = {(item.rule, item.source, item.target) for item in slices.suppressions}
    failures = sorted(unresolved | (found - (baseline or set()) - suppressed)
                      | history(root, resolve_base(root, base), baseline))
    lines = [_line(identity) for identity in failures]
    if strict:
        lines += [f"STALE baseline entry, run `baseline --shrink`: {_line(entry)}"
                  for entry in sorted((baseline or set()) - found)]
        lines += [f"STALE [[suppress]] entry in slices.toml, remove it: {_line(entry)}"
                  for entry in sorted(suppressed - found)]
    for line in lines:
        print(line)
    if not lines:
        print(f"slice guard: pass ({len(graph.files)} files, {len(graph.edges)} edges, "
              + f"{len(baseline or ())} baselined)")
    return 1 if lines else 0


def baseline(root: Path, init: bool) -> int:
    slices = slice_map.load(root)
    unresolved, found = violations(root, slices, slice_map.build(root, slices))
    path = root / BASELINE_FILE
    if init:
        if path.exists():
            print(f"{BASELINE_FILE} exists; init refuses to overwrite it", file=sys.stderr)
            return 1
        if unresolved:
            print("\n".join(map(_line, sorted(unresolved))), file=sys.stderr)
            print("init refuses while an import is unresolved (SB-00)", file=sys.stderr)
            return 1
        kept = found
    else:
        if not path.is_file():
            print(f"{BASELINE_FILE} is missing; run `baseline --init` in the adoption change", file=sys.stderr)
            return 1
        kept = decode(path.read_text(), BASELINE_FILE) & found  # Shrink only: never add an entry.
    _ = path.write_text(_encode(kept))
    counts: dict[str, int] = {}
    for rule, _source, _target in kept:
        counts[rule] = counts.get(rule, 0) + 1
    print(f"{BASELINE_FILE}: {len(kept)} entries" + "".join(f", {rule} {n}" for rule, n in sorted(counts.items())))
    return 0


class _Args(argparse.Namespace):
    command: str = ""
    base: str = "auto"
    strict: bool = False
    init: bool = False


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(prog="check_slices.py", description="Hold the source to slices.toml.")
    commands = parser.add_subparsers(dest="command", required=True)
    check_parser = commands.add_parser("check")
    _ = check_parser.add_argument("--base", default="auto")
    _ = check_parser.add_argument("--strict", action="store_true")
    baseline_parser = commands.add_parser("baseline")
    mode = baseline_parser.add_mutually_exclusive_group(required=True)
    _ = mode.add_argument("--init", action="store_true")
    _ = mode.add_argument("--shrink", action="store_true")
    args = parser.parse_args(argv, namespace=_Args())
    try:
        if args.command == "check":
            return check(root, args.base, args.strict)
        return baseline(root, args.init)
    except MapError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
