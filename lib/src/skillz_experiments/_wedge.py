"""Admission rules for the wedge search arm.

A wedge proposal adds exactly one stdlib-only Python script under `scripts/`.
SKILL.md must reference the script path. No other file may change.
"""
from __future__ import annotations

import ast
import re
import sys
from typing import cast

from skillz_experiments._cases import loads_untrusted

COMPONENT = "wedge-files"
SCRIPT_LIMIT = 262144
_SCRIPT = re.compile(r"scripts/[A-Za-z0-9_][A-Za-z0-9_-]*\.py")


def admit(seed_files: dict[str, str], components: dict[str, str], skill: str | None = None) -> dict[str, str]:
    """Return the one new script of a proposal. Raise `ValueError` when the proposal is not admissible.

    `skill` names the contract skill. Only its `.agents/skills/<skill>/` prefix counts as a reference.
    """
    try:
        loaded = loads_untrusted(components.get(COMPONENT, ""))
    except ValueError:
        raise ValueError(f"{COMPONENT} must be a JSON object") from None
    if not isinstance(loaded, dict):
        raise ValueError(f"{COMPONENT} must be a JSON object")
    added: dict[str, str] = {}
    for path, source in cast(dict[object, object], loaded).items():
        if not isinstance(path, str) or not isinstance(source, str):
            raise ValueError(f"{COMPONENT} must map paths to source text")
        added[path] = source
    if len(added) != 1:
        raise ValueError("a wedge proposal adds exactly one file")
    (path, source), = added.items()
    if not _SCRIPT.fullmatch(path):
        raise ValueError("the wedge file must be scripts/<name>.py")
    if path in seed_files:
        raise ValueError("the wedge file must be new")
    if len(source) > SCRIPT_LIMIT:
        raise ValueError("the wedge script exceeds the size limit")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        raise ValueError("the wedge script has invalid Python syntax") from None
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise ValueError("the wedge script must use standard-library imports")
            modules = (node.module or "",)
        else:
            continue
        if any(module.split(".", 1)[0] not in sys.stdlib_module_names for module in modules):
            raise ValueError("the wedge script must use standard-library imports")
    if len(components.get("SKILL.md", "")) > SCRIPT_LIMIT:
        raise ValueError("SKILL.md exceeds the size limit")
    if not _reference(path, skill).search(components.get("SKILL.md", "")):
        raise ValueError("SKILL.md must reference the wedge script")
    return added


def _reference(path: str, skill: str | None) -> re.Pattern[str]:
    """Match `path` as one path token, with an optional skill-directory prefix.

    This is a mention check. A negated mention such as "do not run scripts/x.py" still counts.
    """
    installed = rf"|\.agents/skills/{re.escape(skill)}/" if skill else ""
    return re.compile(rf"(?<![\w./-])(?:\./|\$\{{CLAUDE_SKILL_DIR\}}/|<this-skill-directory>/{installed})?{re.escape(path)}(?![\w/-]|\.\w)")


def new_script(seed_files: dict[str, str], files: dict[str, str]) -> str | None:
    """Return the path of the file that `files` adds to the seed, or `None`."""
    added = [path for path in files if path not in seed_files]
    return added[0] if len(added) == 1 else None
