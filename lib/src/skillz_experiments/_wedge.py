"""Admission rules for the wedge search arm.

A wedge proposal adds exactly one stdlib-only Python script under `scripts/`.
SKILL.md must reference the script path. No other file may change.
"""
from __future__ import annotations

import json
import re
from typing import cast

COMPONENT = "wedge-files"
SCRIPT_LIMIT = 262144
_SCRIPT = re.compile(r"scripts/[A-Za-z0-9_][A-Za-z0-9_-]*\.py")


def admit(seed_files: dict[str, str], components: dict[str, str]) -> dict[str, str]:
    """Return the one new script of a proposal. Raise `ValueError` when the proposal is not admissible."""
    try:
        loaded = cast(object, json.loads(components.get(COMPONENT, "")))
    except json.JSONDecodeError:
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
    if len(components.get("SKILL.md", "")) > SCRIPT_LIMIT:
        raise ValueError("SKILL.md exceeds the size limit")
    if not re.search(rf"(?<![\w.-]){re.escape(path)}(?![\w-]|\.\w)", components.get("SKILL.md", "")):
        raise ValueError("SKILL.md must reference the wedge script")
    return added


def new_script(seed_files: dict[str, str], files: dict[str, str]) -> str | None:
    """Return the path of the file that `files` adds to the seed, or `None`."""
    added = [path for path in files if path not in seed_files]
    return added[0] if len(added) == 1 else None
