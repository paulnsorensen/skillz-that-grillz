"""Typed readers for the fields of a run record, and the run phases.
"""
from __future__ import annotations

from typing import cast

# The phases in order. The phase of a record is one of them.
PHASES = ("prepared", "searched", "gated", "replayed", "complete")

# The phases after the gate. A run in one of them makes no model call without a deadline check.
POST_GATE_PHASES = ("gated", "replayed")


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


def record_outcomes(record: dict[str, object]) -> list[dict[str, object]]:
    value = field(record, "outcomes")
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in cast(list[object], value)):
        raise ValueError("run record field outcomes must be a list of objects")
    return cast(list[dict[str, object]], value)


def unit_score(value: object) -> float:
    """Clamp a provider score to 0..1. A NaN counts as 0."""
    return min(1.0, max(0.0, float(cast(float, value))))


def phase(record: dict[str, object]) -> str:
    return text_field(record, "phase")
