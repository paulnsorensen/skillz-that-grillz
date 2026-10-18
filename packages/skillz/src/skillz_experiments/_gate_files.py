"""Resolve `gate` file references in a raw contract into the inline `argv` form.

A kind that declares `"gate": "evals/gates/<name>.py"` becomes `"argv": ["python3", "-I", "-c", SOURCE]`.
The run freezes that argv in `run.json`, so resume and export never read the target again.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path, PurePosixPath
from typing import cast

from skillz_experiments._cases import CodedError, read_text, relative

GATE_GRADERS = ("command", "hybrid", "journey")
SOURCE_LIMIT = 64 * 1024
_PREFIX = ("python3", "-I", "-c")
_STUB = ("import sys\nimport types\n_SIBLINGS = {siblings}\n_MAIN = {main}\n"
         + "for _name, _source in _SIBLINGS:\n    _module = types.ModuleType(_name)\n    sys.modules[_name] = _module\n"
         + "    exec(compile(_source, _name + '.py', 'exec'), _module.__dict__)\n"
         + "exec(compile(_MAIN, 'gate.py', 'exec'), {{'__name__': '__main__'}})\n")


def _invalid(message: str) -> CodedError:
    return CodedError("contract-gate-invalid", message)


def _read(root: Path, name: str) -> str:
    try:
        return read_text(root, name, SOURCE_LIMIT)
    except ValueError as error:
        raise _invalid(f"gate file {error}") from None


def _imports(source: str, name: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as error:
        raise _invalid(f"gate {name} is not valid Python: {error}") from None
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module is None:
                raise _invalid(f"gate {name} uses a relative import; import a sibling module by its plain name")
            found.add(node.module)
    return found


def _bundle(root: Path, gate: str) -> tuple[str, list[str]]:
    """Return the inline source and every gate file that it reads.

    A gate with no sibling imports is its own source. Otherwise the source is a small stub that registers each
    sibling module, dependencies first, and runs the gate as `__main__`.
    """
    folder = PurePosixPath(gate).parent
    main = _read(root, gate)
    siblings: dict[str, str] = {}
    order: list[str] = []

    def visit(name: str, source: str, stack: tuple[str, ...]) -> None:
        for module in sorted(_imports(source, name)):
            top = module.split(".")[0]
            path = (folder / f"{top}.py").as_posix()
            if top in sys.stdlib_module_names:
                if os.path.lexists(root / path):
                    raise _invalid(f"gate sibling {path} shadows the standard library module {top}")
                continue
            if module != top or not os.path.lexists(root / path):
                raise _invalid(f"gate {name} imports {module}: only the standard library and sibling modules in {folder} are allowed")
            if top in stack:
                raise _invalid(f"gate modules import each other in a cycle at {top}")
            if top in siblings:
                continue
            siblings[top] = _read(root, path)
            visit(top, siblings[top], (*stack, top))
            order.append(top)

    visit(gate, main, (PurePosixPath(gate).stem,))
    paths = [gate, *(f"{(folder / name).as_posix()}.py" for name in order)]
    if not order:
        return main, paths
    stub = _STUB.format(siblings=repr([(name, siblings[name]) for name in order]), main=repr(main))
    return stub, paths


def _expand(root: Path, item: dict[str, object], kind: str, used: set[str]) -> dict[str, object]:
    if item.get("grader") not in GATE_GRADERS:
        raise _invalid(f"kind {kind} grader does not take a gate")
    if "argv" in item:
        raise _invalid(f"kind {kind} declares both gate and argv; declare one")
    gate = item["gate"]
    try:
        if not isinstance(gate, str):
            raise ValueError("gate must be text")
        _ = relative(gate)
    except ValueError:
        raise _invalid(f"kind {kind} gate must be a relative path under evals/ that ends with .py") from None
    if not gate.startswith("evals/") or not gate.endswith(".py"):
        raise _invalid(f"kind {kind} gate must be a relative path under evals/ that ends with .py")
    source, paths = _bundle(root, gate)
    if len(source.encode("utf-8")) > SOURCE_LIMIT:
        raise _invalid(f"kind {kind} gate source is larger than {SOURCE_LIMIT} bytes")
    used.update(paths)
    return {key: value for key, value in item.items() if key != "gate"} | {"argv": [*_PREFIX, source]}


def expand_gates(raw: object, root: Path) -> object:
    """Return `raw` with each kind `gate` path replaced by the inline `argv` that runs the gate source.

    `root` is the directory that holds `evals/`. Raise `contract-gate-invalid` for an unusable gate and
    `contract-gate-editable` when `editable` names a gate file. Other input passes through unchanged.
    """
    if not isinstance(raw, dict):
        return raw
    document = cast(dict[str, object], raw)
    kinds = document.get("kinds")
    if not isinstance(kinds, dict):
        return document
    used: set[str] = set()
    expanded: dict[str, object] = {}
    for kind, entry in cast(dict[str, object], kinds).items():
        expanded[kind] = (_expand(root, cast(dict[str, object], entry), kind, used)
                          if isinstance(entry, dict) and "gate" in entry else entry)
    editable = document.get("editable")
    if isinstance(editable, list):
        for name in cast(list[object], editable):
            if name in used:
                raise CodedError("contract-gate-editable", f"editable names the gate file {name}; a candidate cannot edit its own grader")
    return document | {"kinds": expanded}
