"""Typed readers for the fields of a run record, and the run phases.
"""
from __future__ import annotations

from typing import cast

from skillz_experiments._cases import mapping

# The phases in order. The phase of a record is one of them.
PHASES = ("prepared", "searched", "gated", "replayed", "complete")

# The phases after the gate. A run in one of them makes no model call without a deadline check.
POST_GATE_PHASES = ("gated", "replayed")


def differences(recorded: object, observed: object, name: str = "") -> list[str]:
    """Name each preflight evidence key whose value differs, with both values. Keys sit in dotted paths."""
    if isinstance(recorded, dict) and isinstance(observed, dict):
        before, after = cast(dict[str, object], recorded), cast(dict[str, object], observed)
        return [line for key in sorted(set(before) | set(after))
                for line in differences(before.get(key), after.get(key), f"{name}.{key}" if name else key)]
    return [] if recorded == observed else [f"{name} (recorded {str(cast(object, recorded))[:80]!r}, observed {str(observed)[:80]!r})"]


def field(record: dict[str, object], key: str) -> object:
    if key not in record:
        raise ValueError(f"run record lacks {key}")
    return record[key]


def text_field(record: dict[str, object], key: str) -> str:
    value = field(record, key)
    if not isinstance(value, str):
        raise ValueError(f"run record field {key} must be text")
    return value


def judge_flag(record: dict[str, object]) -> str | None:
    """Return the frozen `--judge-model` value, or None when the first run passed none."""
    value = record.get("judge_model_flag")
    if value is not None and not isinstance(value, str):
        raise ValueError("run record field judge_model_flag must be text or null")
    return value


def number_field(record: dict[str, object], key: str) -> float:
    value = field(record, key)
    if type(value) not in (int, float):
        raise ValueError(f"run record field {key} must be a number")
    return cast(float, value)


def int_field(record: dict[str, object], key: str) -> int:
    value = field(record, key)
    if type(value) is not int or value < 0:
        raise ValueError(f"run record field {key} must be a nonnegative integer")
    return value


def count_map(value: object) -> dict[str, float]:
    """Load a recorded map of role to count or seconds. A missing map is empty."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("run record role counts must map roles to nonnegative numbers")
    loaded: dict[str, float] = {}
    for key, item in cast(dict[object, object], value).items():
        if not isinstance(key, str) or type(item) not in (int, float) or cast(float, item) < 0:
            raise ValueError("run record role counts must map roles to nonnegative numbers")
        loaded[key] = float(cast(float, item))
    return loaded


def recorded_rejections(record: dict[str, object]) -> dict[str, set[str]]:
    """Load the rejected proposal digests per rule from the record. They hold no case text."""
    recorded = mapping(record.get("leakage_rejected", {}))
    found: dict[str, set[str]] = {}
    for rule, keys in recorded.items():
        if not isinstance(keys, list) or not all(isinstance(key, str) for key in cast(list[object], keys)):
            raise ValueError("run record field leakage_rejected must map rules to lists of text")
        found[rule] = set(cast(list[str], keys))
    return found


def record_outcomes(record: dict[str, object]) -> list[dict[str, object]]:
    value = field(record, "outcomes")
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in cast(list[object], value)):
        raise ValueError("run record field outcomes must be a list of objects")
    return cast(list[dict[str, object]], value)


def criterion_levels(item: dict[str, object]) -> dict[str, int]:
    """Return the criterion levels (1 to 4) that the judged outcome `item` stores. Anything else yields nothing."""
    criteria = item.get("criteria")
    if not isinstance(criteria, dict):
        return {}
    found: dict[str, int] = {}
    for name, verdict in cast(dict[str, object], criteria).items():
        level = cast(dict[str, object], verdict).get("level") if isinstance(verdict, dict) else None
        if type(level) is int and 1 <= level <= 4:
            found[name] = level
    return found


def phase(record: dict[str, object]) -> str:
    return text_field(record, "phase")
