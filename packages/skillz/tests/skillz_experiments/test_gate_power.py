from __future__ import annotations

from functools import cache
from typing import cast, get_args

import pytest

from gate_rules import always_promote, never_promote
from skillz_experiments import _cli
from skillz_experiments.gate import POWER_EFFECTS, Power, Verdict, simulate, simulate_cheaper, simulate_power

# Each simulation is deterministic, so every test that needs a default-argument run shares one real run per argument set.
# Tests that check determinism or the seed call the uncached functions.
_gate = cache(simulate)
_power = cache(simulate_power)
_cheaper = cache(simulate_cheaper)


@pytest.fixture(autouse=True)
def _shared_simulations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_cli, "simulate_gate", _gate)
    monkeypatch.setattr(_cli, "simulate_power", _power)
    monkeypatch.setattr(_cli, "simulate_cheaper", _cheaper)


@pytest.mark.parametrize("effect", POWER_EFFECTS)
def test_a_rule_that_never_promotes_has_zero_power(effect: float) -> None:
    result = simulate_power(effect, trials=200, rule=never_promote)
    assert result.promotions == 0
    assert result.rate == 0.0


def test_a_rule_that_always_promotes_has_full_power() -> None:
    result = simulate_power(0.2, trials=200, rule=always_promote)
    assert result.promotions == 200
    assert result.rate == 1.0


def test_the_result_is_deterministic_and_the_rate_matches_the_count() -> None:
    runs = [simulate_power(0.2) for _ in range(3)]
    assert all(run == runs[0] for run in runs)
    assert runs[0].rate == runs[0].promotions / runs[0].trials


def test_the_seed_changes_the_draws() -> None:
    assert simulate_power(0.2, seed=1).promotions != simulate_power(0.2, seed=2).promotions


def test_power_grows_with_the_true_effect_and_is_not_trivial() -> None:
    counts = [_power(effect).promotions for effect in POWER_EFFECTS]
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
        expected = _power(effect)
        assert printed[f"true_promotions_effect_{label}"] == expected.promotions
        assert printed[f"power_effect_{label}"] == expected.rate
        assert type(printed[f"true_promotions_effect_{label}"]) is int


def test_a_selected_figure_equals_the_same_key_of_the_full_report() -> None:
    # The full report and the selectors share one real run per argument set (see `_shared_simulations`).
    # A selector that runs the wrong simulation, field, or arguments still reads another value or type.
    full = cast(dict[str, object], _cli.self_test(simulate=True))
    for metric in cast(tuple[_cli.Metric, ...], get_args(_cli.Metric)):
        selected = _cli.self_test(simulate=True, metric=metric)
        assert selected == full[metric], metric
        assert type(selected) is type(full[metric]), metric


def test_a_power_selector_runs_one_power_simulation_and_no_null_simulation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[float] = []

    def counting(effect: float) -> Power:
        calls.append(effect)
        return simulate_power(effect, trials=20)

    def forbidden() -> object:
        raise AssertionError("a power selector must not run the null simulation")

    monkeypatch.setattr(_cli, "simulate_power", counting)
    monkeypatch.setattr(_cli, "simulate_gate", forbidden)
    _ = _cli.self_test(simulate=True, metric="power_effect_20")
    assert calls == [0.2]


def test_a_false_promotion_selector_runs_no_power_simulation(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(effect: float) -> object:
        raise AssertionError(f"a false-promotion selector must not simulate power at {effect}")

    monkeypatch.setattr(_cli, "simulate_power", forbidden)
    assert _cli.self_test(simulate=True, metric="rate_2se") == _gate().rate_2se


def test_every_numeric_report_key_but_the_fixed_ones_is_a_metric_selector() -> None:
    printed = cast(dict[str, object], _cli.self_test(simulate=True))
    selectors = set(get_args(_cli.Metric))
    assert set(printed) - selectors == {"trials", "cases", "live_calls"}
    assert selectors <= set(printed)


@pytest.mark.parametrize("loss, margin, fixed, rate", [
    (0.05, 0.0, 0, 0.006),
    (0.05, 0.02, 0, 0.009),
    pytest.param(0.05, 0.05, 0, 0.024, marks=pytest.mark.ac("gh-529:AC-2")),
    (0.10, 0.05, 0, 0.010),
    (0.0, 0.05, 0, 0.0465),
    (0.0, 0.05, 1, 0.0995),  # a candidate that fixes one case outright and loses none
    (0.05, 1.0, 0, 0.346),  # a margin far above the loss shows what an unbounded promote-cheaper rule would do
])
def test_a_cheaper_promotion_rate_is_pinned_per_loss_and_margin(loss: float, margin: float, fixed: int, rate: float) -> None:
    result = _cheaper(loss, margin, fixed=fixed)
    assert (result.loss, result.margin, result.trials, result.cases, result.fixed) == (loss, margin, 2000, 10, fixed)
    assert result.rate == rate == result.promotions / result.trials
    if loss == 0.05 and margin == 0.05:
        assert result.rate < 0.06


def test_a_cheaper_simulation_is_deterministic_and_its_rate_matches_the_count() -> None:
    runs = [simulate_cheaper(0.0, 0.05, trials=50) for _ in range(2)]
    assert runs[0] == runs[1]
    assert runs[0].rate == runs[0].promotions / 50


@pytest.mark.parametrize("kwargs, message", [
    ({"trials": 0}, "at least 1 trial"),
    ({"cases": 1}, "at least 2 cases"),
    ({"loss": -0.01}, "loss and a margin"),
    ({"margin": -0.01}, "loss and a margin"),
    ({"fixed": 11}, "fixed cases"),
])
def test_a_cheaper_simulation_refuses_a_bad_shape_or_a_negative_loss_or_margin_or_too_many_fixed_cases(
        kwargs: dict[str, float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _ = simulate_cheaper(**cast(dict[str, int], kwargs))


def test_the_cheaper_selector_returns_the_simulated_rate() -> None:
    assert _cli.self_test(simulate=True, metric="cheaper_loss_5_margin_5") == _cheaper(0.05, 0.05, fixed=0).rate
    assert (_cli.self_test(simulate=True, metric="cheaper_loss_0_margin_5_fixed_1")
            == _cheaper(0.0, 0.05, fixed=1).rate)
