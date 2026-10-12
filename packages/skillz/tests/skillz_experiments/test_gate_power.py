from __future__ import annotations

from typing import cast, get_args

import pytest

from skillz_experiments import _cli
from skillz_experiments.gate import POWER_EFFECTS, Verdict, simulate_power


def _always_promote(deltas: list[float]) -> Verdict:
    return Verdict("promote", 0.0, 0.0, len(deltas))


def _never_promote(deltas: list[float]) -> Verdict:
    return Verdict("reject", 0.0, 0.0, len(deltas))


@pytest.mark.parametrize("effect", POWER_EFFECTS)
def test_a_rule_that_never_promotes_has_zero_power(effect: float) -> None:
    result = simulate_power(effect, trials=200, rule=_never_promote)
    assert result.promotions == 0
    assert result.rate == 0.0


def test_a_rule_that_always_promotes_has_full_power() -> None:
    result = simulate_power(0.2, trials=200, rule=_always_promote)
    assert result.promotions == 200
    assert result.rate == 1.0


def test_the_result_is_deterministic_and_the_rate_matches_the_count() -> None:
    runs = [simulate_power(0.2) for _ in range(3)]
    assert all(run == runs[0] for run in runs)
    assert runs[0].rate == runs[0].promotions / runs[0].trials


def test_the_seed_changes_the_draws() -> None:
    assert simulate_power(0.2, seed=1).promotions != simulate_power(0.2, seed=2).promotions


def test_power_grows_with_the_true_effect_and_is_not_trivial() -> None:
    counts = [simulate_power(effect).promotions for effect in POWER_EFFECTS]
    assert counts == sorted(counts)
    assert 0 < counts[0] < counts[-1] < 2000


def test_the_injected_rule_sees_one_float_delta_per_holdout_case() -> None:
    seen: list[list[float]] = []

    def spy(deltas: list[float]) -> Verdict:
        seen.append(deltas)
        return Verdict("inconclusive", 0.0, 0.0, len(deltas))

    _ = simulate_power(0.2, trials=25, cases=7, rule=spy)
    assert len(seen) == 25
    assert all(len(deltas) == 7 and all(isinstance(value, float) for value in deltas) for deltas in seen)


def test_invalid_arguments_are_refused() -> None:
    calls = [lambda: simulate_power(0.0), lambda: simulate_power(-0.1), lambda: simulate_power(0.2, trials=0),
             lambda: simulate_power(0.2, cases=1), lambda: simulate_power(0.2, repeats=0)]
    for call in calls:
        with pytest.raises(ValueError, match="simulate_power needs"):
            _ = call()


def test_the_cli_reports_a_count_and_a_rate_for_each_effect() -> None:
    printed = cast(dict[str, object], _cli.self_test(simulate=True))
    for effect in POWER_EFFECTS:
        label = round(effect * 100)
        expected = simulate_power(effect)
        assert printed[f"true_promotions_effect_{label}"] == expected.promotions
        assert printed[f"power_effect_{label}"] == expected.rate
        assert type(printed[f"true_promotions_effect_{label}"]) is int


def test_every_numeric_report_key_but_the_fixed_ones_is_a_metric_selector() -> None:
    printed = cast(dict[str, object], _cli.self_test(simulate=True))
    selectors = set(get_args(_cli.Metric))
    assert set(printed) - selectors == {"trials", "cases", "live_calls"}
    assert selectors <= set(printed)
