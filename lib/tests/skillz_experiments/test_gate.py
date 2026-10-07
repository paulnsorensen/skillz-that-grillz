from __future__ import annotations

import pytest

from skillz_experiments._gate import case_deltas, simulate, verdict


def test_case_deltas_average_repeats_per_case() -> None:
    deltas = case_deltas({"a": [0.0, 1.0], "b": [1.0]}, {"a": [1.0, 1.0], "b": [0.0]})
    assert deltas == {"a": 0.5, "b": -1.0}


def test_case_deltas_feed_verdict_with_unequal_repeat_counts() -> None:
    baseline = {"a": [0.0] * 4, "b": [0.0], "c": [0.0, 1.0], "d": [0.0, 0.0]}
    winner = {"a": [1.0, 1.0, 1.0, 0.0], "b": [1.0], "c": [1.0, 1.0], "d": [1.0, 0.0]}
    deltas = case_deltas(baseline, winner)
    assert deltas == {"a": 0.75, "b": 1.0, "c": 0.5, "d": 0.5}
    result = verdict(deltas)
    assert result.verdict == "promote"
    assert result.delta == pytest.approx(0.6875)
    assert result.cases == 4


def test_case_deltas_reject_mismatched_case_ids() -> None:
    with pytest.raises(ValueError):
        _ = case_deltas({"a": [1.0]}, {"b": [1.0]})


def test_verdict_promotes_when_delta_exceeds_two_se() -> None:
    # mean 0.2, stdev 0.1, se 0.05 -> 0.2 > 0.1
    result = verdict([0.1, 0.2, 0.3])
    assert result.verdict == "promote"
    assert result.delta == pytest.approx(0.2)
    assert result.se == pytest.approx(0.1 / 3**0.5)
    assert result.cases == 3


def test_verdict_is_inconclusive_inside_two_se() -> None:
    # mean 0.1, stdev 0.2, se 0.2/sqrt(3) -> 2*se = 0.23
    assert verdict([-0.1, 0.1, 0.3]).verdict == "inconclusive"


def test_verdict_rejects_when_delta_is_below_minus_two_se() -> None:
    assert verdict({"a": -0.1, "b": -0.2, "c": -0.3}).verdict == "reject"


def test_verdict_with_fewer_than_two_cases_is_inconclusive() -> None:
    assert verdict([0.5]).verdict == "inconclusive"
    assert verdict([]).verdict == "inconclusive"


def test_zero_se_promotes_only_when_every_case_delta_is_positive() -> None:
    assert verdict([0.1, 0.1, 0.1]).verdict == "promote"
    assert verdict([-0.1, -0.1]).verdict == "reject"
    assert verdict([0.0, 0.0, 0.0]).verdict == "inconclusive"
    assert verdict([0.1, 0.1, 0.0]).verdict == "inconclusive"


@pytest.mark.parametrize("cases", [6, 10, 20])
def test_simulation_false_promotion_is_at_most_six_percent_and_below_skill_creator(
    cases: int,
) -> None:
    first = simulate(seed=20261006, trials=2000, cases=cases, repeats=3)
    assert first.rate_2se <= 0.06
    assert first.rate_2se < first.rate_one_holdout
    assert simulate(seed=20261006, trials=2000, cases=cases, repeats=3) == first
