"""Run Vulture without treating live TypedDict declarations as dead variables."""
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
    for node in ast.walk(statement):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if isinstance(node.value, ast.Name):
                names.add(node.value.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name.partition(".")[0])
    return names


def _typed_dict_fields(path: Path) -> set[tuple[int, str]]:
    with tokenize.open(path) as source:
        module = ast.parse(source.read(), filename=str(path))
    aliases: dict[str, str] = {}
    fields: set[tuple[int, str]] = set()
    for statement in module.body:
        if isinstance(statement, ast.ImportFrom):
            for imported in statement.names:
                name = imported.asname or imported.name
                if statement.module in {"typing", "typing_extensions"} and imported.name == "TypedDict":
                    aliases[name] = "class"
                else:
                    _ = aliases.pop(name, None)
        elif isinstance(statement, ast.Import):
            for imported in statement.names:
                name = imported.asname or imported.name.partition(".")[0]
                if imported.name in {"typing", "typing_extensions"}:
                    aliases[name] = "module"
                else:
                    _ = aliases.pop(name, None)
        elif isinstance(statement, ast.ClassDef):
            typed_dict = any(
                isinstance(base, ast.Name) and aliases.get(base.id) == "class"
                or isinstance(base, ast.Attribute) and base.attr == "TypedDict"
                and isinstance(base.value, ast.Name) and aliases.get(base.value.id) == "module"
                for base in statement.bases
            )
            if typed_dict:
                fields.update(
                    (member.lineno, member.target.id)
                    for member in statement.body
                    if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name)
                )
            _ = aliases.pop(statement.name, None)
        else:
            for name in _rebound_names(statement):
                _ = aliases.pop(name, None)
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
