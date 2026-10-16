"""Safe schema keys for the Claude API.

The API rejects a structured-output schema with a property key outside `API_KEY` (400 on `input_schema.properties`).
A reflection schema keys its properties by component path and by `@hypothesis`, so the transport sends safe keys and
maps the structured answer back to the real names.
"""
from __future__ import annotations

import re
from typing import cast

API_KEY = re.compile(r"[a-zA-Z0-9_.-]{1,64}")


def safe_keys(schema: dict[str, object]) -> tuple[dict[str, object], dict[str, str]]:
    """Return `schema` with safe top-level property keys, and the map from each safe key to the real one.

    A renamed property keeps its real key in `description`. A schema with only safe keys comes back unchanged.
    """
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return schema, {}
    original = cast(dict[str, object], properties)
    used = {key for key in original if API_KEY.fullmatch(key)}
    names: dict[str, str] = {}
    sent: dict[str, object] = {}
    for key, body in original.items():
        if key in used:
            sent[key] = body
            continue
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", key)[:50] + f"_{len(names)}"
        while safe in used:
            safe += "_"
        used.add(safe)
        names[safe] = key
        sent[safe] = {**cast(dict[str, object], body), "description": key} if isinstance(body, dict) else body
    if not names:
        return schema, {}
    safe_of = {key: safe for safe, key in names.items()}
    result = {**schema, "properties": sent}
    required = schema.get("required")
    if isinstance(required, list):
        result["required"] = [safe_of.get(name, name) if isinstance(name, str) else name for name in cast(list[object], required)]
    return result, names


def restore(answer: dict[str, object], names: dict[str, str]) -> dict[str, object]:
    """Return `answer` with each renamed key mapped back to its real name."""
    return {names.get(key, key): value for key, value in answer.items()} if names else answer
