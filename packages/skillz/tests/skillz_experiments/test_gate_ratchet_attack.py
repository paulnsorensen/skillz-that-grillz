from __future__ import annotations

from typing import cast

from gate_rules import always_promote, never_promote
from skillz_experiments._cli import self_test
from skillz_experiments.gate import Verdict, simulate


def test_cli_prints_integer_counts_equal_to_the_simulation_and_no_live_calls() -> None:
    printed = cast(dict[str, object], self_test(simulate=True))
    expected = simulate()
    assert printed["false_promotions_2se"] == expected.false_promotions_2se
    assert printed["false_promotions_one_holdout"] == expected.false_promotions_one_holdout
    assert type(printed["false_promotions_2se"]) is int
    assert type(printed["false_promotions_one_holdout"]) is int
    assert printed["live_calls"] == 0


def test_a_rule_that_always_promotes_falsely_promotes_every_trial() -> None:
    result = simulate(trials=200, rule=always_promote)
    assert result.false_promotions_2se == 200
    assert result.rate_2se == 1.0


def test_a_rule_that_never_promotes_falsely_promotes_no_trial() -> None:
    assert simulate(trials=200, rule=never_promote).false_promotions_2se == 0


def test_the_injected_rule_leaves_the_one_holdout_arm_unchanged() -> None:
    real = simulate(trials=300)
    weakened = simulate(trials=300, rule=always_promote)
    assert weakened.false_promotions_one_holdout == real.false_promotions_one_holdout


def test_the_injected_rule_sees_one_float_delta_per_holdout_case() -> None:
    seen: list[list[float]] = []

    def spy(deltas: list[float]) -> Verdict:
        seen.append(deltas)
        return Verdict("inconclusive", 0.0, 0.0, len(deltas))

    _ = simulate(trials=25, cases=7, rule=spy)
    assert len(seen) == 25
    assert all(len(deltas) == 7 and all(isinstance(value, float) for value in deltas) for deltas in seen)


def test_counts_never_exceed_trials_and_match_rates_for_odd_sizes() -> None:
    result = simulate(seed=3, trials=37, cases=2, repeats=1, candidates=2)
    assert 0 <= result.false_promotions_2se <= 37
    assert 0 <= result.false_promotions_one_holdout <= 37
    assert result.rate_2se == result.false_promotions_2se / 37
