from __future__ import annotations

import math

import pytest

from skillz_experiments.gate import case_deltas, holdout_deltas, simulate, verdict


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
    assert verdict([0.1, 0.1, -0.1]).verdict == "inconclusive"


def test_verdict_needs_strictly_more_than_two_se() -> None:
    # mean 2, stdev sqrt(2), se 1 -> delta is exactly 2*se
    assert verdict([3.0, 1.0]).verdict == "inconclusive"
    assert verdict([-3.0, -1.0]).verdict == "inconclusive"


@pytest.mark.parametrize(
    "values",
    [[math.nan] * 6, [math.inf] * 6, [math.inf, 0.0, 0.0, 0.0], [-math.inf] * 4, [0.5, 0.5, math.nan, 0.5], [math.nan],
     [math.inf, -math.inf, 0.0, 0.0]],
)
def test_verdict_is_inconclusive_for_non_finite_deltas(values: list[float]) -> None:
    result = verdict(values)
    assert result.verdict == "inconclusive"
    assert result.cases == len(values)


def test_verdict_rejects_when_delta_is_below_minus_two_se() -> None:
    assert verdict({"a": -0.1, "b": -0.2, "c": -0.3}).verdict == "reject"


def test_verdict_with_fewer_than_two_cases_is_inconclusive() -> None:
    assert verdict([0.5]).verdict == "inconclusive"
    assert verdict([]).verdict == "inconclusive"


def test_zero_se_promotes_only_when_every_case_delta_is_positive() -> None:
    assert verdict([0.1, 0.1, 0.1]).verdict == "promote"
    assert verdict([-0.1, -0.1]).verdict == "reject"
    assert verdict([0.0, 0.0, 0.0]).verdict == "inconclusive"


@pytest.mark.parametrize("cases", [6, 10, 20])
def test_simulation_false_promotion_is_at_most_six_percent_and_below_one_holdout(
    cases: int,
) -> None:
    result = simulate(seed=20261006, trials=2000, cases=cases, repeats=3)
    assert result.rate_2se <= 0.06
    assert result.rate_2se < result.rate_one_holdout


def test_simulation_is_deterministic_for_a_seed() -> None:
    assert simulate(seed=7, trials=50, cases=6) == simulate(seed=7, trials=50, cases=6)


@pytest.mark.parametrize("candidates", [0, 1])
def test_simulation_needs_at_least_two_candidates(candidates: int) -> None:
    with pytest.raises(ValueError, match="at least 2 candidates"):
        _ = simulate(trials=1, cases=6, candidates=candidates)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [({"trials": 0}, "at least 1 trial"), ({"trials": -1}, "at least 1 trial"),
     ({"cases": 1}, "at least 2 cases"), ({"cases": 0}, "at least 2 cases"),
     ({"repeats": 0}, "at least 1 repeat"), ({"repeats": -1}, "at least 1 repeat")],
)
def test_simulation_rejects_sizes_that_cannot_run_a_trial(overrides: dict[str, int], message: str) -> None:
    arguments = {"trials": 1, "cases": 6, "repeats": 1} | overrides
    with pytest.raises(ValueError, match=message):
        _ = simulate(trials=arguments["trials"], cases=arguments["cases"], repeats=arguments["repeats"])


def test_simulation_accepts_the_smallest_valid_sizes() -> None:
    result = simulate(seed=7, trials=1, cases=2, repeats=1, candidates=2)
    assert result.trials == 1
    assert result.cases == 2


def _spread(mean: float, se: float, repeats: int = 3) -> list[float]:
    """Return `repeats` scores around `mean` whose repeat-mean standard error is `se`."""
    root = math.sqrt(repeats)
    return [mean - se * root + se * root * 2 / (repeats - 1) * index for index in range(repeats)]


def test_the_se_floor_turns_a_promote_on_noisy_repeats_into_inconclusive() -> None:
    baseline = {case: _spread(0.5, 0.1) for case in "abcd"}
    winner = {case: _spread(mean, 0.1) for case, mean in zip("abcd", (0.6, 0.6, 0.62, 0.6))}
    paired = holdout_deltas(baseline, winner)
    assert verdict(paired.deltas).verdict == "promote"
    result = verdict(paired.deltas, se_floor=paired.se_floor)
    assert paired.se_floor == pytest.approx((4 * (0.1**2 + 0.1**2)) ** 0.5 / 4)
    assert result.verdict == "inconclusive" and result.se_floor > verdict(paired.deltas).se
    assert result.se == result.se_floor


def test_one_repeat_per_case_gives_a_zero_floor_and_the_same_verdict() -> None:
    baseline = {"a": [0.0], "b": [0.0], "c": [0.0], "d": [0.0]}
    winner = {"a": [0.1], "b": [0.2], "c": [0.3], "d": [0.2]}
    paired = holdout_deltas(baseline, winner)
    assert paired.se_floor == 0.0
    assert verdict(paired.deltas, se_floor=paired.se_floor) == verdict(paired.deltas)


def test_the_floor_never_lowers_the_se() -> None:
    assert verdict([0.1, 0.2, 0.3], se_floor=0.0).se == pytest.approx(0.1 / 3**0.5)
    assert verdict([0.1, 0.2, 0.3], se_floor=0.001).se == pytest.approx(0.1 / 3**0.5)


def test_one_shared_case_records_the_floor_as_its_se() -> None:
    paired = holdout_deltas({"a": _spread(0.5, 0.1)}, {"a": _spread(0.6, 0.1)})
    result = verdict(paired.deltas, se_floor=paired.se_floor)
    assert paired.se_floor > 0
    assert (result.verdict, result.se, result.n_shared) == ("inconclusive", paired.se_floor, 1)


def test_the_zero_se_branch_needs_a_zero_floor() -> None:
    assert verdict([0.1, 0.1, 0.1]).verdict == "promote"
    assert verdict([0.1, 0.1, 0.1], se_floor=0.2).verdict == "inconclusive"


def test_a_move_inside_repeat_noise_is_unresolved_not_broke() -> None:
    paired = holdout_deltas({"a": _spread(1.0, 0.1)}, {"a": _spread(0.9, 0.1)})
    assert paired.moves == {"fixed": [], "broke": [], "unresolved": ["a"]}


def test_moves_split_into_fixed_broke_and_unresolved_by_case_id() -> None:
    baseline = {"up": [0.0, 0.0], "down": [1.0, 1.0], "same": [0.5, 0.5], "noisy": [0.0, 1.0]}
    winner = {"up": [1.0, 1.0], "down": [0.0, 0.0], "same": [0.5, 0.5], "noisy": [1.0, 0.0]}
    assert holdout_deltas(baseline, winner).moves == {
        "fixed": ["up"], "broke": ["down"], "unresolved": ["noisy", "same"]}


def test_a_case_without_a_scored_repeat_on_one_arm_drops_out() -> None:
    baseline = {"a": [1.0, 1.0], "b": [1.0, 1.0], "c": [1.0, 1.0]}
    winner = {"a": [1.0, 1.0], "b": [], "c": [0.0, 0.0]}
    paired = holdout_deltas(baseline, winner)
    assert paired.n_shared == 2 and sorted(paired.deltas) == ["a", "c"]
    assert "b" not in {case for ids in paired.moves.values() for case in ids}
    assert holdout_deltas({"a": [1.0]}, {}).n_shared == 0


def test_the_verdict_data_carries_the_floor_the_shared_count_and_the_moves() -> None:
    data = verdict([0.1, 0.2, 0.3], se_floor=0.5).data()
    assert data["se_floor"] == 0.5 and data["n_shared"] == 3
    assert data["moves"] == {"fixed": [], "broke": [], "unresolved": []}
