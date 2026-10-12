from __future__ import annotations

import functools
import json
import re
import statistics
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments import _cli
from skillz_experiments.gate import Verdict, simulate

RATCHETS = Path(__file__).resolve().parents[2] / "ratchets.json"
METRIC = "gate-false-promotions-2se"
Entry = dict[str, str | int | float]


def _entries() -> dict[str, Entry]:
    document = cast(dict[str, dict[str, Entry]], json.loads(RATCHETS.read_text()))
    return document["metrics"]


def _selector(entry: Entry) -> str:
    """Return the `--metric` name that the command of the entry passes to `self-test --simulate`."""
    command = str(entry["command"])
    assert "self-test --simulate" in command
    found = re.search(r"--metric (\w+)$", command)
    assert found, f"{command!r} must select one metric with --metric"
    return found.group(1)


@functools.cache
def _measure(selector: str) -> float:
    return cast(float, _cli.self_test(simulate=True, metric=selector))  # pyright: ignore[reportArgumentType]


def _measured(entry: Entry) -> float:
    return _measure(_selector(entry))


def _breaches(entry: Entry, value: float) -> bool:
    assert entry["direction"] in ("lower", "higher"), f"unknown direction {entry['direction']!r}"
    threshold = float(entry["threshold"])
    return value > threshold if entry["direction"] == "lower" else value < threshold


def _threshold() -> int:
    return int(_entries()[METRIC]["threshold"])


def _mean_delta_rule(deltas: list[float]) -> Verdict:
    mean = statistics.fmean(deltas)
    return Verdict("promote" if mean > 0 else "inconclusive", mean, 0.0, len(deltas))


@pytest.mark.parametrize("name", sorted(_entries()))
def test_every_ratchet_metric_stays_within_its_threshold(name: str) -> None:
    entry = _entries()[name]
    value = _measured(entry)
    assert not _breaches(entry, value), f"{name} is {value}, past the {entry['direction']} threshold {entry['threshold']}"


@pytest.mark.parametrize("name", sorted(_entries()))
def test_a_ratchet_one_step_inside_the_value_fails(name: str) -> None:
    entry = _entries()[name]
    value = _measured(entry)
    worse = 1 if entry["direction"] == "lower" else -1
    assert _breaches(entry | {"threshold": value}, value) is False
    assert _breaches(entry | {"threshold": value - worse}, value) is True


def test_each_metric_has_its_own_selector() -> None:
    selectors = [_selector(entry) for entry in _entries().values()]
    assert len(set(selectors)) == len(selectors)


def test_a_weakened_rule_exceeds_the_ratchet() -> None:
    weakened = simulate(rule=_mean_delta_rule)
    assert weakened.false_promotions_2se > _threshold()


def test_counts_are_deterministic_and_match_the_rates() -> None:
    runs = [simulate() for _ in range(5)]
    assert all(run == runs[0] for run in runs)
    assert runs[0].rate_2se == runs[0].false_promotions_2se / runs[0].trials
    assert runs[0].rate_one_holdout == runs[0].false_promotions_one_holdout / runs[0].trials