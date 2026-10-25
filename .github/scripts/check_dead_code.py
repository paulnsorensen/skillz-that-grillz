"""Run Vulture without treating live TypedDict declarations as dead variables.

The gate exempts fields of module-level TypedDict classes in the scanned paths.
This includes subclasses of a module-level TypedDict. The rule is structural, not
name-based. Unpack and TypedDict keys are read by string, so Vulture cannot see
the reads. Nested (function-local) TypedDicts are not recognized. They fail closed
and Vulture reports their fields. A conditional alias counts only when every path
binds it to TypedDict; an `if TYPE_CHECKING:` body counts as the static path.
"""
from __future__ import annotations

import ast
import sys
import tokenize
from pathlib import Path

from vulture import Vulture
from vulture.config import InputError, make_config
from vulture.utils import ExitCode


def _rebound_names(statement: ast.stmt) -> set[str]:
    names: set[str] = set()
    pending: list[ast.AST] = [statement]
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # A nested scope binds its own name. Its body rebinds module names only through `global`.
            names.add(node.name)
            names.update(name for inner in ast.walk(node) if isinstance(inner, ast.Global) for name in inner.names)
            pending.extend(child for child in ast.iter_child_nodes(node) if child not in node.body)
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if isinstance(node.value, ast.Name):
                names.add(node.value.id)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name.partition(".")[0])
        pending.extend(ast.iter_child_nodes(node))
    return names


def _import_from(statement: ast.ImportFrom, aliases: dict[str, str]) -> None:
    for imported in statement.names:
        name = imported.asname or imported.name
        if statement.module in {"typing", "typing_extensions"} and imported.name == "TypedDict":
            aliases[name] = "class"
        else:
            _ = aliases.pop(name, None)


def _import_module(statement: ast.Import, aliases: dict[str, str]) -> None:
    for imported in statement.names:
        name = imported.asname or imported.name.partition(".")[0]
        if imported.name in {"typing", "typing_extensions"}:
            aliases[name] = "module"
        else:
            _ = aliases.pop(name, None)


def _is_typed_dict(statement: ast.ClassDef, aliases: dict[str, str]) -> bool:
    return any(
        isinstance(base, ast.Name) and aliases.get(base.id) == "class"
        or isinstance(base, ast.Attribute) and base.attr == "TypedDict"
        and isinstance(base.value, ast.Name) and aliases.get(base.value.id) == "module"
        for base in statement.bases
    )


def _visit(
    statements: list[ast.stmt], aliases: dict[str, str], fields: set[tuple[int, str]]
) -> None:
    for statement in statements:
        if isinstance(statement, ast.ImportFrom):
            _import_from(statement, aliases)
        elif isinstance(statement, ast.Import):
            _import_module(statement, aliases)
        elif isinstance(statement, ast.ClassDef):
            if _is_typed_dict(statement, aliases):
                fields.update(
                    (member.lineno, member.target.id)
                    for member in statement.body
                    if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name)
                )
                aliases[statement.name] = "class"
            else:
                _ = aliases.pop(statement.name, None)
        elif isinstance(statement, ast.If):
            # Static analysis takes the `if TYPE_CHECKING:` body; other conditions keep only common aliases.
            branches = [statement.body] if _is_type_checking(statement.test) else [statement.body, statement.orelse]
            _merge_into(aliases, [_branch(branch, aliases, fields) for branch in branches])
        elif isinstance(statement, ast.Try):
            _visit_try(statement, aliases, fields)
        else:
            for name in _rebound_names(statement):
                _ = aliases.pop(name, None)


def _is_type_checking(test: ast.expr) -> bool:
    return (
        isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"
        or isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _branch(
    statements: list[ast.stmt], aliases: dict[str, str], fields: set[tuple[int, str]]
) -> dict[str, str]:
    state = dict(aliases)
    _visit(statements, state, fields)
    return state


def _merge_into(aliases: dict[str, str], states: list[dict[str, str]]) -> None:
    """Keep an alias only when every possible path binds it the same way."""
    first, *rest = states
    merged = {name: kind for name, kind in first.items() if all(state.get(name) == kind for state in rest)}
    aliases.clear()
    aliases.update(merged)


def _visit_try(statement: ast.Try, aliases: dict[str, str], fields: set[tuple[int, str]]) -> None:
    # A handler can start after any prefix of the try body.
    state = dict(aliases)
    prefixes = [dict(state)]
    for item in statement.body:
        _visit([item], state, fields)
        prefixes.append(dict(state))
    entry = dict(aliases)
    _merge_into(entry, prefixes)
    ends = [_branch(statement.orelse, state, fields)]
    for handler in statement.handlers:
        handled = dict(entry)
        if handler.name:
            _ = handled.pop(handler.name, None)
        ends.append(_branch(handler.body, handled, fields))
    _merge_into(aliases, ends)
    _visit(statement.finalbody, aliases, fields)


def _typed_dict_fields(path: Path) -> set[tuple[int, str]]:
    with tokenize.open(path) as source:
        module = ast.parse(source.read(), filename=str(path))
    fields: set[tuple[int, str]] = set()
    _visit(module.body, {}, fields)
    return fields


def main(argv: list[str] | None = None) -> int:
    try:
        config = make_config(argv)
    except InputError as error:
        print(error, file=sys.stderr)
        return int(ExitCode.InvalidCmdlineArguments)
    scanner = Vulture(
        verbose=config["verbose"],
        ignore_names=config["ignore_names"],
        ignore_decorators=config["ignore_decorators"],
    )
    scanner.scavenge(config["paths"], exclude=config["exclude"])
    if scanner.exit_code == ExitCode.InvalidInput:
        return int(scanner.exit_code)
    fields_by_file: dict[Path, set[tuple[int, str]]] = {}
    result = ExitCode.NoDeadCode
    for item in scanner.get_unused_code(
        min_confidence=config["min_confidence"], sort_by_size=config["sort_by_size"]
    ):
        if item.typ == "variable":
            if item.filename not in fields_by_file:
                fields_by_file[item.filename] = _typed_dict_fields(item.filename)
            fields = fields_by_file[item.filename]
            if (item.first_lineno, item.name) in fields:
                continue
        print(
            item.get_whitelist_string() if config["make_whitelist"]
            else item.get_report(add_size=config["sort_by_size"])
        )
        result = ExitCode.DeadCode
    return int(result)


if __name__ == "__main__":
    sys.exit(main())
