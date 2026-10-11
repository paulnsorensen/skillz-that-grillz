from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import cast

from skillz_experiments.gate import Verdict, simulate

RATCHETS = Path(__file__).resolve().parents[2] / "ratchets.json"
METRIC = "gate-false-promotions-2se"


def _threshold() -> int:
    document = cast(dict[str, dict[str, dict[str, str | int]]], json.loads(RATCHETS.read_text()))
    entry = document["metrics"][METRIC]
    assert entry["direction"] == "lower"
    assert "self-test --simulate" in str(entry["command"])
    return int(entry["threshold"])


def _mean_delta_rule(deltas: list[float]) -> Verdict:
    mean = statistics.fmean(deltas)
    return Verdict("promote" if mean > 0 else "inconclusive", mean, 0.0, len(deltas))


def test_false_promotions_2se_stay_at_or_below_the_ratchet() -> None:
    value, threshold = simulate().false_promotions_2se, _threshold()
    assert value <= threshold, f"{METRIC} is {value}, above the ratchet threshold {threshold}"


def test_a_weakened_rule_exceeds_the_ratchet() -> None:
    weakened = simulate(rule=_mean_delta_rule)
    assert weakened.false_promotions_2se > _threshold()


def test_counts_are_deterministic_and_match_the_rates() -> None:
    runs = [simulate() for _ in range(5)]
    assert all(run == runs[0] for run in runs)
    assert runs[0].rate_2se == runs[0].false_promotions_2se / runs[0].trials
    assert runs[0].rate_one_holdout == runs[0].false_promotions_one_holdout / runs[0].trials
