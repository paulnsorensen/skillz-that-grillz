"""Adversarial press tests for the token-cost rule (C3): boundaries, hostile usage, ties, flags, resume.

Six tests pinned C3 flaws that a Cook correction fixed: float noise at thresholds, overflow, and negative counts.
"""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._cli import main
from skillz_experiments._contract import parse
from skillz_experiments._evaluator import task_tokens
from skillz_experiments._gate import DEFAULT_STATISTICS, Statistics, simulate, verdict
from skillz_experiments._records import read, write
from skillz_experiments._workflow import (
    Stop,
    _mean_tokens,  # pyright: ignore[reportPrivateUsage] -- unit test of the private mean
    _Session,  # pyright: ignore[reportPrivateUsage] -- tests build a bare session
    run,
)

pytestmark = pytest.mark.usefixtures("host_login")

MODEL = "local-test"
Pair = tuple[float | None, float | None]


def _document(**extra: object) -> dict[str, object]:
    return {"schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
            "kinds": {"echo": {"grader": "exact-json"}}, "editable": [], **extra}


def _tokens(*pairs: Pair) -> dict[str, Pair]:
    return {f"c{index}": pair for index, pair in enumerate(pairs)}


def _deltas(*values: float) -> dict[str, float]:
    return {f"c{index}": value for index, value in enumerate(values)}


def _same(count: int, pair: Pair) -> dict[str, Pair]:
    return _tokens(*[pair] * count)


BAND = _deltas(0.1, -0.1, 0.1, -0.1)  # mean 0, SE > 0


# --- cap boundary -----------------------------------------------------------------------------------------

def test_cap_exactly_at_the_bound_is_not_a_reject_for_a_binary_exact_ratio() -> None:
    result = verdict(_deltas(1.0, 1.0), thresholds=Statistics(max_token_increase_per_gain=0.5),
                     tokens=_same(2, (100, 150)))  # +50% = 0.5 * 1.0
    assert (result.verdict, result.reasons) == ("promote", ())


def test_cap_just_over_the_bound_rejects() -> None:
    result = verdict(_deltas(1.0, 1.0), thresholds=Statistics(max_token_increase_per_gain=0.5),
                     tokens=_same(2, (100, 151)))
    assert (result.verdict, result.reasons) == ("reject", ("token-cost",))


def test_cap_at_the_bound_with_a_decimal_ratio_does_not_reject() -> None:
    """A change within float noise of the cap counts as equal to the cap and does not reject."""
    # +10% tokens against a cap of 0.1 per unit gain of 1.0 sits exactly on the bound. 110/100 - 1 is
    # 0.10000000000000009 in floats, so the strict > test rejects a change that equals the cap.
    result = verdict(_deltas(1.0, 1.0), thresholds=Statistics(max_token_increase_per_gain=0.1),
                     tokens=_same(2, (100, 110)))
    assert (result.verdict, result.reasons) == ("promote", ())


def test_cap_at_the_bound_with_a_half_gain_does_not_reject() -> None:
    """A change within float noise of the cap at a half gain counts as equal and does not reject."""
    result = verdict(_deltas(0.5, 0.5), thresholds=Statistics(max_token_increase_per_gain=0.1),
                     tokens=_same(2, (100, 105)))  # +5% = 0.1 * 0.5
    assert (result.verdict, result.reasons) == ("promote", ())


def test_zero_cap_rejects_any_increase_and_passes_a_decrease_or_equal() -> None:
    zero = Statistics(max_token_increase_per_gain=0.0)
    promoting = _deltas(1.0, 1.0, 1.0)
    assert verdict(promoting, thresholds=zero, tokens=_same(3, (100, 101))).reasons == ("token-cost",)
    assert verdict(promoting, thresholds=zero, tokens=_same(3, (1000, 1001))).verdict == "reject"
    for pair in ((100, 99), (100, 100), (100, 0)):
        assert verdict(promoting, thresholds=zero, tokens=_same(3, pair)).verdict == "promote"


def test_zero_cap_turns_the_rule_on_for_unknown_usage() -> None:
    result = verdict(_deltas(1.0, 1.0), thresholds=Statistics(max_token_increase_per_gain=0.0),
                     tokens=_same(2, (None, None)))
    assert (result.verdict, result.reasons) == ("inconclusive", ("unknown-usage",))


def test_a_huge_cap_never_rejects_and_a_tiny_gain_with_any_increase_rejects() -> None:
    huge = Statistics(max_token_increase_per_gain=1e300)
    assert verdict(_deltas(1.0, 1.0), thresholds=huge, tokens=_same(2, (1, 10**6))).verdict == "promote"
    tight = Statistics(max_token_increase_per_gain=1.0)
    assert verdict(_deltas(1e-9, 1e-9), thresholds=tight, tokens=_same(2, (100, 101))).verdict == "reject"


def test_cap_with_a_cheaper_winner_stays_a_promote_and_reports_a_negative_change() -> None:
    result = verdict(_deltas(1.0, 1.0), thresholds=Statistics(max_token_increase_per_gain=0.0),
                     tokens=_same(2, (100, 50)))
    assert (result.verdict, result.token_delta) == ("promote", -0.5)


# --- promote-cheaper boundary -----------------------------------------------------------------------------

def test_saving_exactly_at_the_share_promotes_cheaper_for_a_binary_exact_ratio() -> None:
    result = verdict(BAND, thresholds=Statistics(min_token_saving=0.5), tokens=_same(4, (100, 50)))
    assert result.verdict == "promote-cheaper"


def test_saving_just_under_the_share_does_not_promote_cheaper() -> None:
    result = verdict(BAND, thresholds=Statistics(min_token_saving=0.5), tokens=_same(4, (100, 51)))
    assert result.verdict == "inconclusive"


def test_saving_exactly_at_the_share_promotes_cheaper_for_a_decimal_ratio() -> None:
    """A saving within float noise of the share counts as reaching it and promotes cheaper."""
    # A drop from 100 to 90 saves exactly 10%. 90/100 - 1 is -0.09999999999999998, which sits below 0.1.
    result = verdict(BAND, thresholds=Statistics(min_token_saving=0.1), tokens=_same(4, (100, 90)))
    assert result.verdict == "promote-cheaper"


def test_saving_exactly_at_two_token_se_does_not_promote_cheaper() -> None:
    tokens = _tokens((100, 25), (100, 75))  # changes -0.75 and -0.25: mean -0.5, SE 0.25
    probe = verdict(_deltas(0.1, -0.1), thresholds=Statistics(min_token_saving=0.2), tokens=tokens)
    assert (probe.token_delta, probe.token_se) == (-0.5, 0.25)  # precondition: -delta == 2 * SE exactly
    assert probe.verdict == "inconclusive"


def test_saving_just_past_two_token_se_promotes_cheaper() -> None:
    tokens = _tokens((100, 30), (100, 70))  # changes -0.7, -0.3: mean -0.5, SE 0.2, 2 SE 0.4
    result = verdict(_deltas(0.1, -0.1), thresholds=Statistics(min_token_saving=0.2), tokens=tokens)
    assert result.verdict == "promote-cheaper"


def test_saving_of_everything_is_a_valid_change_of_minus_one() -> None:
    result = verdict(BAND, thresholds=Statistics(min_token_saving=0.9), tokens=_same(4, (100, 0)))
    assert (result.verdict, result.token_delta) == ("promote-cheaper", -1.0)


def test_a_single_case_has_no_band_so_it_never_promotes_cheaper() -> None:
    result = verdict(_deltas(0.0), thresholds=Statistics(min_token_saving=0.1), tokens=_same(1, (100, 10)))
    assert result.verdict == "inconclusive" and result.token_se == 0.0
    assert result.reasons == ()


# --- score band edges -------------------------------------------------------------------------------------

def test_delta_exactly_plus_two_se_is_in_the_band_and_may_promote_cheaper() -> None:
    cheap = _same(2, (100, 10))
    result = verdict(_deltas(3.0, 1.0), thresholds=Statistics(min_token_saving=0.2), tokens=cheap)
    assert (result.delta, result.se) == (2.0, 1.0)  # precondition: delta == 2 * SE
    assert result.verdict == "promote-cheaper"


def test_delta_exactly_minus_two_se_is_in_the_band_but_a_loss_never_promotes_cheaper() -> None:
    cheap = _same(2, (100, 10))
    result = verdict(_deltas(-3.0, -1.0), thresholds=Statistics(min_token_saving=0.2), tokens=cheap)
    assert (result.delta, result.se) == (-2.0, 1.0)
    assert (result.verdict, result.reasons) == ("inconclusive", ())


def test_delta_just_past_two_se_is_a_promote_and_a_costly_one_rejects() -> None:
    tokens = _same(2, (100, 300))
    result = verdict(_deltas(3.0, 1.001), thresholds=Statistics(max_token_increase_per_gain=0.1), tokens=tokens)
    assert result.verdict == "reject" and result.reasons == ("token-cost",)


def test_all_zero_deltas_are_inconclusive_before_floors_and_may_promote_cheaper() -> None:
    result = verdict(_deltas(0.0, 0.0, 0.0), thresholds=Statistics(min_token_saving=0.2), tokens=_same(3, (100, 50)))
    assert (result.verdict, result.delta, result.se) == ("promote-cheaper", 0.0, 0.0)


def test_all_zero_deltas_with_unknown_usage_name_the_reason() -> None:
    result = verdict(_deltas(0.0, 0.0, 0.0), thresholds=Statistics(min_token_saving=0.2), tokens=_same(3, (0, 0)))
    assert (result.verdict, result.reasons) == ("inconclusive", ("unknown-usage",))


def test_equal_negative_deltas_reject_whatever_the_tokens() -> None:
    result = verdict(_deltas(-1.0, -1.0, -1.0), thresholds=Statistics(min_token_saving=0.2),
                     tokens=_same(3, (100, 1)))
    assert (result.verdict, result.reasons) == ("reject", ())


def test_a_negative_delta_inside_the_band_never_promotes_cheaper() -> None:
    result = verdict(_deltas(-0.2, 0.1, -0.2, 0.1), thresholds=Statistics(min_token_saving=0.2),
                     tokens=_same(4, (100, 40)))
    assert (result.verdict, result.reasons) == ("inconclusive", ()) and result.delta < 0


def test_a_large_mean_loss_inside_the_band_never_promotes_cheaper() -> None:
    result = verdict(_deltas(-0.9, 0.1), thresholds=Statistics(min_token_saving=0.2), tokens=_same(2, (100, 10)))
    assert result.delta == pytest.approx(-0.4) and result.verdict == "inconclusive"


def test_a_mean_delta_of_exactly_zero_with_a_spread_promotes_cheaper() -> None:
    result = verdict(_deltas(0.3, -0.3), thresholds=Statistics(min_token_saving=0.2), tokens=_same(2, (100, 10)))
    assert (result.delta, result.se > 0, result.verdict) == (0.0, True, "promote-cheaper")


def test_a_small_gain_inside_the_band_promotes_cheaper() -> None:
    result = verdict(_deltas(0.3, -0.2), thresholds=Statistics(min_token_saving=0.2), tokens=_same(2, (100, 10)))
    assert result.delta > 0 and result.verdict == "promote-cheaper"


def test_an_active_token_rule_with_no_tokens_is_unknown_usage() -> None:
    for thresholds in (Statistics(max_token_increase_per_gain=1.0), Statistics(min_token_saving=0.2)):
        promoted = verdict(_deltas(1.0, 1.0), thresholds=thresholds)
        assert (promoted.verdict, promoted.reasons) == ("inconclusive", ("unknown-usage",))


def test_a_floor_failure_is_not_a_band_result_even_with_a_big_saving() -> None:
    result = verdict(_deltas(1.0, 1.0, 1.0), thresholds=Statistics(min_lower_bound=5.0, min_token_saving=0.2),
                     tokens=_same(3, (100, 10)))
    assert (result.verdict, result.reasons) == ("inconclusive", ("lower-bound",))


# --- family and reason order ------------------------------------------------------------------------------

FAMILIES = {"a1": "a", "a2": "a", "b1": "b", "b2": "b", "b3": "b", "b4": "b"}
DIVERGENT = {"a1": -0.1, "a2": -0.1, "b1": 3.0, "b2": 3.0, "b3": 3.0, "b4": 3.0}


def test_a_floor_demotion_skips_unknown_usage_and_the_family_reason_follows() -> None:
    thresholds = Statistics(min_gain=5.0, max_token_increase_per_gain=1.0, family_budget=0.05)
    unknown: dict[str, Pair] = {key: (None, None) for key in DIVERGENT}
    result = verdict(DIVERGENT, thresholds=thresholds, families=FAMILIES, tokens=unknown)
    assert (result.verdict, result.reasons) == ("reject", ("min-gain", "family-regression:a"))


def test_a_family_breach_rejects_a_cheaper_band_result_with_the_family_reason() -> None:
    deltas = {"a1": -1.0, "a2": -1.0, "b1": 1.0, "b2": 1.0}
    families = {"a1": "a", "a2": "a", "b1": "b", "b2": "b"}
    cheap: dict[str, Pair] = {key: (100.0, 50.0) for key in deltas}
    thresholds = Statistics(family_budget=0.0, min_token_saving=0.2)
    result = verdict(deltas, thresholds=thresholds, families=families, tokens=cheap)
    assert (result.verdict, result.reasons) == ("reject", ("family-regression:a",))
    assert result.token_delta == pytest.approx(-0.5)


def test_a_token_cost_reject_keeps_both_reasons_when_a_family_also_breaches() -> None:
    thresholds = Statistics(max_token_increase_per_gain=0.0, family_budget=0.05)
    costly: dict[str, Pair] = {key: (100.0, 101.0) for key in DIVERGENT}
    result = verdict(DIVERGENT, thresholds=thresholds, families=FAMILIES, tokens=costly)
    assert result.reasons == ("token-cost", "family-regression:a")


# --- hostile token pairs ----------------------------------------------------------------------------------

@pytest.mark.parametrize("pair", [(None, None), (None, 5), (5, None), (0, 5), (-5, 5), (5, -1), (math.nan, 5),
                                  (5, math.nan), (math.inf, 5), (5, math.inf), (-math.inf, 5), (5, -math.inf),
                                  (0.0, 0.0), (-0.0, 5)])
def test_one_hostile_case_among_good_ones_makes_the_whole_change_unknown(pair: Pair) -> None:
    good: Pair = (100, 50)
    for hostile_at in (0, 3):
        pairs: list[Pair] = [good] * 4
        pairs[hostile_at] = pair
        for thresholds in (Statistics(max_token_increase_per_gain=1.0), Statistics(min_token_saving=0.2)):
            for deltas in (_deltas(1.0, 1.0, 1.0, 1.0), BAND):
                result = verdict(deltas, thresholds=thresholds, tokens=_tokens(*pairs))
                assert (result.token_delta, result.token_se) == (None, None)
                assert result.verdict in ("inconclusive",)
                if thresholds.min_token_saving is not None or deltas is not BAND:
                    assert result.reasons == ("unknown-usage",)


def test_the_change_is_json_safe_for_an_extreme_ratio() -> None:
    """A non-finite change makes the usage unknown, so the report holds no inf or nan."""
    # Both means are finite and the baseline is above 0, so the case counts as known; the ratio overflows to inf.
    result = verdict(_deltas(1.0, 1.0), thresholds=Statistics(max_token_increase_per_gain=1e300),
                     tokens=_same(2, (1e-300, 1e300)))
    _ = json.dumps(result.data(), allow_nan=False)


def test_a_tiny_but_positive_baseline_is_known() -> None:
    result = verdict(_deltas(1.0, 1.0), thresholds=Statistics(max_token_increase_per_gain=10.0),
                     tokens=_same(2, (1e-6, 1e-6)))
    assert result.token_delta == 0.0


def test_empty_tokens_with_empty_deltas_do_not_crash() -> None:
    result = verdict({}, thresholds=Statistics(min_token_saving=0.2, max_token_increase_per_gain=1.0), tokens={})
    assert result.verdict == "inconclusive" and result.token_delta is None


def test_the_non_finite_delta_path_never_promotes_cheaper_and_never_crashes_on_hostile_tokens() -> None:
    result = verdict(_deltas(math.nan, 0.0), thresholds=Statistics(min_token_saving=0.2),
                     tokens=_tokens((None, 1), (5, math.nan)))
    assert (result.verdict, result.token_delta) == ("inconclusive", None)
    assert math.isnan(result.delta)


# --- ValueError on mismatched ids -------------------------------------------------------------------------

@pytest.mark.parametrize("tokens", [
    {"x0": (1.0, 1.0), "x1": (1.0, 1.0)},  # same count, other ids
    {"c0": (1.0, 1.0)},  # subset
    {"c0": (1.0, 1.0), "c1": (1.0, 1.0), "c2": (1.0, 1.0)},  # superset
    {}])
def test_tokens_with_different_case_ids_raise_value_error(tokens: dict[str, Pair]) -> None:
    for thresholds in (DEFAULT_STATISTICS, Statistics(min_token_saving=0.2)):
        with pytest.raises(ValueError, match="tokens need deltas keyed by the same case ids"):
            _ = verdict(_deltas(1.0, 1.0), thresholds=thresholds, tokens=tokens)


def test_tokens_with_sequence_deltas_raise_value_error() -> None:
    with pytest.raises(ValueError, match="tokens need deltas keyed"):
        _ = verdict((1.0, 1.0), thresholds=Statistics(min_token_saving=0.2), tokens=_same(2, (1, 1)))


# --- charged tokens: hostile usage ------------------------------------------------------------------------

def _usage(**counts: object) -> dict[str, object]:
    return dict(counts)


@pytest.mark.parametrize("usage", [
    _usage(input_tokens=10, output_tokens=5),  # cached input is missing
    _usage(input_tokens=10, cached_input_tokens=None, output_tokens=5),
    _usage(cached_input_tokens=0, output_tokens=10),
    _usage(input_tokens=10, cached_input_tokens=0),
    _usage(input_tokens=True, cached_input_tokens=0, output_tokens=5),
    _usage(input_tokens=5, cached_input_tokens=0, output_tokens=False),
    _usage(input_tokens=5, cached_input_tokens=False, output_tokens=5),
    _usage(input_tokens="10", cached_input_tokens=0, output_tokens=5),
    _usage(input_tokens=10.0, cached_input_tokens=0, output_tokens=5),
    _usage(input_tokens=10, cached_input_tokens=0.0, output_tokens=5),
    _usage(input_tokens=10, cached_input_tokens=0, output_tokens=5.5),
    _usage(input_tokens=math.nan, cached_input_tokens=0, output_tokens=5),
    _usage(input_tokens=None, cached_input_tokens=None, output_tokens=None),
    _usage(cached_input_tokens=99),
    _usage(),
])
def test_task_tokens_reject_a_usage_without_three_int_counts(usage: dict[str, object]) -> None:
    assert task_tokens({"usage": usage}) is None
    assert task_tokens({"usage": None, "task_usage": usage}) is None


@pytest.mark.parametrize("usage", [[1, 2], "abc", 7, (1, 2), True])
def test_task_tokens_reject_a_usage_that_is_not_a_mapping(usage: object) -> None:
    assert task_tokens({"usage": usage}) is None
    assert task_tokens({"task_usage": usage, "usage": _usage(input_tokens=1, cached_input_tokens=0,
                                                           output_tokens=1)}) is None


def test_task_usage_wins_over_usage_even_when_usage_is_the_better_record() -> None:
    item = {"usage": _usage(input_tokens=1, cached_input_tokens=0, output_tokens=1),
            "task_usage": _usage(input_tokens=7, cached_input_tokens=0, output_tokens=3)}
    assert task_tokens(item) == 10


def test_cached_input_is_not_charged() -> None:
    assert task_tokens({"usage": _usage(input_tokens=10, cached_input_tokens=4, output_tokens=3)}) == 9
    assert task_tokens({"usage": _usage(input_tokens=10, cached_input_tokens=10, output_tokens=3)}) == 3


def test_the_charge_is_equal_when_only_the_cached_share_of_input_differs() -> None:
    first = {"usage": _usage(input_tokens=10, cached_input_tokens=4, output_tokens=3)}
    second = {"usage": _usage(input_tokens=14, cached_input_tokens=8, output_tokens=3)}
    assert task_tokens(first) == task_tokens(second) == 9


def test_cached_input_above_input_is_unknown() -> None:
    assert task_tokens({"usage": _usage(input_tokens=3, cached_input_tokens=3000, output_tokens=4)}) is None


def test_zero_counts_are_known_and_charge_zero() -> None:
    assert task_tokens({"usage": _usage(input_tokens=0, cached_input_tokens=0, output_tokens=0)}) == 0


def test_a_negative_count_is_unknown_not_cancelled_by_the_other_field() -> None:
    """A negative count makes the task tokens unknown."""
    # -50 + 100 sums to 50, a believable charge. A negative count is corrupt and should read as unknown.
    assert task_tokens({"usage": _usage(input_tokens=-50, cached_input_tokens=0, output_tokens=100)}) is None
    assert task_tokens({"usage": _usage(input_tokens=50, cached_input_tokens=-5, output_tokens=100)}) is None
    assert task_tokens({"usage": _usage(input_tokens=50, cached_input_tokens=0, output_tokens=-1)}) is None


def test_a_huge_count_does_not_crash_the_mean() -> None:
    """A count too large for a float makes the mean unknown instead of raising."""
    # fsum on a 10**400 int raises OverflowError, which would abort the gate mid-run.
    huge = task_tokens({"usage": _usage(input_tokens=10**400, cached_input_tokens=0, output_tokens=1)})
    assert huge is not None
    assert _mean_tokens([huge]) is None


def test_mean_tokens_is_none_for_empty_or_any_unknown_and_exact_otherwise() -> None:
    assert _mean_tokens([]) is None
    assert _mean_tokens([None]) is None
    assert _mean_tokens([10, None, 20]) is None
    assert _mean_tokens([10, 20]) == 15.0
    assert _mean_tokens([0, 0]) == 0.0


# --- session: holdout tokens and tie-breaks ---------------------------------------------------------------

def _changed(files: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(identity="changed", files=files)


def _cases_for(_split: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(identifier=item) for item in ("v1", "v2")]


def _stub(statistics: Statistics, outcomes: list[dict[str, object]], kept: dict[str, object] | None = None) -> _Session:
    session = object.__new__(_Session)
    attributes: dict[str, object] = {
        "lock": threading.RLock(), "statistics": statistics, "outcomes": outcomes,
        "record": {} if kept is None else {"best_validated": kept},
        "seed": SimpleNamespace(identity="seed", editable=("a",),
                                changed=_changed),
        "cases_for": _cases_for,
        "checkpoint": lambda: None}
    for name, value in attributes.items():
        setattr(session, name, value)
    return session


def _holdout(arm: str, case_id: str, repeat: int, low: object, high: object = 0, *, split: str = "holdout",
             task: bool = True, cached: object = 0) -> dict[str, object]:
    usage = {"input_tokens": low, "cached_input_tokens": cached, "output_tokens": high}
    return {"arm": arm, "split": split, "case_id": case_id, "repeat": repeat, "score": 1.0,
            "task_usage" if task else "usage": usage}


def test_holdout_tokens_average_repeats_and_ignore_other_arms_and_splits() -> None:
    outcomes = [_holdout("baseline", "h1", 0, 100), _holdout("baseline", "h1", 1, 200),
                _holdout("winner", "h1", 0, 10), _holdout("winner", "h1", 1, 30),
                _holdout("search", "h1", 0, 999999), _holdout("baseline", "h1", 0, 888888, split="train")]
    session = _stub(Statistics(min_token_saving=0.2), outcomes)
    assert session._holdout_tokens() == {"h1": (150.0, 20.0)}  # pyright: ignore[reportPrivateUsage] -- unit test of the private reader


def test_holdout_tokens_with_one_unknown_repeat_make_that_arm_unknown() -> None:
    outcomes = [_holdout("baseline", "h1", 0, 100), _holdout("baseline", "h1", 1, None),
                _holdout("winner", "h1", 0, 10), _holdout("winner", "h1", 1, 30)]
    session = _stub(Statistics(min_token_saving=0.2), outcomes)
    assert session._holdout_tokens() == {"h1": (None, 20.0)}  # pyright: ignore[reportPrivateUsage] -- unit test of the private reader


def test_holdout_tokens_with_a_missing_arm_are_unknown_for_that_arm() -> None:
    session = _stub(Statistics(min_token_saving=0.2), [_holdout("baseline", "h1", 0, 100)])
    assert session._holdout_tokens() == {"h1": (100.0, None)}  # pyright: ignore[reportPrivateUsage] -- unit test of the private reader


def test_holdout_tokens_fall_back_to_usage_when_task_usage_is_absent() -> None:
    outcomes = [_holdout("baseline", "h1", 0, 100, 5, task=False), _holdout("winner", "h1", 0, 10, 5, task=False)]
    session = _stub(Statistics(min_token_saving=0.2), outcomes)
    assert session._holdout_tokens() == {"h1": (105.0, 15.0)}  # pyright: ignore[reportPrivateUsage] -- unit test of the private reader


def test_holdout_tokens_charge_only_the_uncached_input() -> None:
    outcomes = [_holdout("baseline", "h1", 0, 100, 5, cached=40), _holdout("winner", "h1", 0, 100, 5, cached=90)]
    session = _stub(Statistics(min_token_saving=0.2), outcomes)
    assert session._holdout_tokens() == {"h1": (65.0, 15.0)}  # pyright: ignore[reportPrivateUsage] -- unit test of the private reader


def _validation(identity: str, score: float, tokens: list[object]) -> list[dict[str, object]]:
    return [{"arm": "search", "split": "validation", "search_candidate": identity, "case_id": case_id,
             "score": score, "usage": {"input_tokens": count, "cached_input_tokens": 0, "output_tokens": 0}}
            for case_id, count in zip(("v1", "v2"), tokens, strict=True)]


def _keep(statistics: Statistics, *rounds: tuple[str, float, list[object]]) -> object:
    session = _stub(statistics, [])
    case = cast(Case, cast(object, SimpleNamespace(split="validation")))
    for identity, score, tokens in rounds:
        session.outcomes.extend(_validation(identity, score, tokens))
        candidate = cast(Candidate, cast(object, SimpleNamespace(identity=identity, files={"a": identity})))
        session._keep_if_best(candidate, case)  # pyright: ignore[reportPrivateUsage] -- unit test of the private keeper
    kept = session.record.get("best_validated")
    return cast(dict[str, object], kept)["identity"] if isinstance(kept, dict) else None


SAVE = Statistics(min_token_saving=0.2)


def test_keep_if_best_a_cheaper_tie_displaces_and_a_later_cheapest_tie_displaces_again() -> None:
    assert _keep(SAVE, ("one", 0.5, [100, 100]), ("two", 0.5, [60, 60]), ("three", 0.5, [10, 10])) == "three"
    assert _keep(SAVE, ("one", 0.5, [100, 100]), ("two", 0.5, [60, 60]), ("three", 0.5, [70, 70])) == "two"


def test_keep_if_best_one_unknown_case_makes_the_candidate_unknown_and_it_never_wins_a_tie() -> None:
    assert _keep(SAVE, ("one", 0.5, [100, 100]), ("two", 0.5, [1, None])) == "one"
    assert _keep(SAVE, ("one", 0.5, [100, None]), ("two", 0.5, [1, 1])) == "one"
    assert _keep(SAVE, ("one", 0.5, ["bad", 100]), ("two", 0.5, [1, 1])) == "one"


def test_keep_if_best_a_float_count_is_unknown_for_the_tie_break() -> None:
    assert _keep(SAVE, ("one", 0.5, [100, 100]), ("two", 0.5, [1.0, 1.0])) == "one"
    assert _keep(SAVE, ("one", 0.5, [100, 100]), ("two", 0.5, [True, True])) == "one"


def test_keep_if_best_equal_means_with_equal_tokens_keep_the_first_with_every_field_combination() -> None:
    for statistics in (SAVE, Statistics(max_token_increase_per_gain=0.0), DEFAULT_STATISTICS):
        assert _keep(statistics, ("one", 0.5, [7, 7]), ("two", 0.5, [7, 7])) == "one"


def test_keep_if_best_without_a_token_field_ignores_cheaper_ties_even_at_zero_tokens() -> None:
    assert _keep(DEFAULT_STATISTICS, ("one", 0.5, [100, 100]), ("two", 0.5, [0, 0])) == "one"
    assert _keep(Statistics(max_token_increase_per_gain=0.0), ("one", 0.5, [100, 100]), ("two", 0.5, [0, 0])) == "two"


def _best(statistics: Statistics, seed: list[object], other: list[object], other_score: float = 0.5) -> object:
    outcomes = _validation("seed", 0.5, seed) + _validation("other", other_score, other)
    kept: dict[str, object] = {"identity": "other", "mean": other_score, "files": {"a": "other"}}
    return _stub(statistics, outcomes, kept)._best_validated(["v1", "v2"]).identity  # pyright: ignore[reportPrivateUsage] -- unit test of the private picker


def test_best_validated_a_cheaper_tie_picks_the_candidate_only_when_a_token_field_is_set() -> None:
    assert _best(SAVE, [100, 100], [99, 99]) == "changed"
    assert _best(Statistics(max_token_increase_per_gain=0.0), [100, 100], [99, 99]) == "changed"
    assert _best(DEFAULT_STATISTICS, [100, 100], [99, 99]) == "seed"


def test_best_validated_unknown_or_equal_or_costlier_tokens_keep_the_seed() -> None:
    assert _best(SAVE, [100, None], [1, 1]) == "seed"
    assert _best(SAVE, [100, 100], [1, None]) == "seed"
    assert _best(SAVE, [100, 100], [100, 100]) == "seed"
    assert _best(SAVE, [100, 100], [101, 101]) == "seed"
    assert _best(SAVE, ["x", 100], [1, 1]) == "seed"


def test_best_validated_a_lower_candidate_mean_never_wins_on_tokens() -> None:
    assert _best(SAVE, [100, 100], [0, 0], other_score=0.49) == "seed"


def test_best_validated_without_a_kept_candidate_returns_the_seed() -> None:
    session = _stub(SAVE, _validation("seed", 0.5, [1, 1]))
    assert session._best_validated(["v1", "v2"]).identity == "seed"  # pyright: ignore[reportPrivateUsage] -- unit test of the private picker


def _arrivals(identity: str, scores: list[tuple[str, float]], tokens: int) -> list[dict[str, object]]:
    return [{"arm": "search", "split": "validation", "search_candidate": identity, "case_id": case_id,
             "score": score, "usage": {"input_tokens": tokens, "cached_input_tokens": 0, "output_tokens": 0}}
            for case_id, score in scores]


def _tie_pick(seed_tokens: int, other_tokens: int, other_scores: list[tuple[str, float]],
              statistics: Statistics = SAVE) -> object:
    outcomes = (_arrivals("seed", [("v1", 0.1), ("v2", 0.2), ("v3", 0.3)], seed_tokens)
                + _arrivals("other", other_scores, other_tokens))
    kept: dict[str, object] = {"identity": "other", "mean": 0.2, "files": {"a": "other"}}
    session = _stub(statistics, outcomes, kept)
    return session._best_validated(["v1", "v2", "v3"]).identity  # pyright: ignore[reportPrivateUsage] -- unit test of the private picker


def test_best_validated_equal_scores_in_another_order_are_a_tie_that_a_cheaper_candidate_wins() -> None:
    reordered = [("v3", 0.3), ("v2", 0.2), ("v1", 0.1)]  # a plain sum differs only on Python 3.11; the test below covers newer interpreters
    assert _tie_pick(100, 99, reordered) == "changed"
    assert _tie_pick(100, 101, reordered) == "seed"


def test_best_validated_a_mean_within_float_noise_above_the_seed_is_still_a_tie() -> None:
    noisy = [("v1", 0.1), ("v2", 0.2), ("v3", 0.3 + 1e-16)]
    assert _tie_pick(100, 101, noisy) == "seed"
    assert _tie_pick(100, 99, noisy) == "changed"


def test_best_validated_a_mean_above_the_seed_only_by_float_noise_loses_without_a_token_field() -> None:
    noisy = [("v1", 0.1), ("v2", 0.2), ("v3", 0.3 + 1e-16)]
    assert _tie_pick(100, 1, noisy, DEFAULT_STATISTICS) == "seed"


def test_best_validated_a_clear_mean_gain_still_wins_when_the_candidate_costs_more() -> None:
    better = [("v1", 0.1), ("v2", 0.2), ("v3", 0.4)]
    assert _tie_pick(100, 500, better) == "changed"


# --- statistics values ------------------------------------------------------------------------------------

@pytest.mark.parametrize("value", [0, 1, 0.0, 1.0, -0.0, True, False, "0.5", [0.5], {"x": 0.5}, math.nan, math.inf,
                                  -math.inf, 10**400, -1])
def test_a_contract_refuses_every_min_token_saving_outside_the_open_unit_interval(value: object) -> None:
    with pytest.raises(CodedError) as caught:
        _ = parse(_document(statistics={"min_token_saving": value}), "skill")
    assert caught.value.code == "contract-statistics-invalid"


@pytest.mark.parametrize("value", [0.5, 0.999999999, 1e-300, 5e-324])
def test_a_contract_accepts_a_min_token_saving_strictly_inside_the_open_unit_interval(value: float) -> None:
    assert Statistics.declared({"min_token_saving": value}) == {"min_token_saving": value}


def test_min_token_saving_one_ulp_below_one_is_accepted_and_one_is_refused() -> None:
    assert Statistics.declared({"min_token_saving": math.nextafter(1.0, 0.0)})
    with pytest.raises(ValueError, match="min_token_saving"):
        _ = Statistics.declared({"min_token_saving": 1})


def test_negative_zero_cap_is_stored_as_positive_zero() -> None:
    parsed = Statistics.declared({"max_token_increase_per_gain": -0.0})
    assert math.copysign(1.0, cast(float, parsed["max_token_increase_per_gain"])) == 1.0


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "-1", "-0.5"])
def test_a_bad_max_token_increase_per_gain_is_refused_at_the_cli(
        value: str, tmp_path: Path, make_target: Callable[..., Path], capsys: pytest.CaptureFixture[str]) -> None:
    target = make_target(tmp_path)
    code = main(["run", "--target", str(target), "--out", str(tmp_path / "run"), "--model", MODEL,
                 f"--max-token-increase-per-gain={value}"])
    captured = capsys.readouterr()
    assert code != 0
    assert "max-token-increase-per-gain" in captured.out + captured.err


@pytest.mark.parametrize("value", ["0", "1", "1.0", "0.0", "-0", "-0.0", "nan", "inf", "-inf", "1.5", "-0.25"])
def test_a_bad_min_token_saving_is_refused_at_the_cli(
        value: str, tmp_path: Path, make_target: Callable[..., Path], capsys: pytest.CaptureFixture[str]) -> None:
    target = make_target(tmp_path)
    code = main(["run", "--target", str(target), "--out", str(tmp_path / "run"), "--model", MODEL,
                 f"--min-token-saving={value}"])
    captured = capsys.readouterr()
    assert code != 0
    assert "min-token-saving" in captured.out + captured.err
    assert not (tmp_path / "run" / "run.json").exists()


def test_a_good_min_token_saving_is_not_refused_at_the_cli(
        tmp_path: Path, make_target: Callable[..., Path], capsys: pytest.CaptureFixture[str]) -> None:
    target = make_target(tmp_path)
    _ = main(["run", "--target", str(target), "--out", str(tmp_path / "run"), "--model", MODEL,
              "--min-token-saving=0.5"])
    captured = capsys.readouterr()
    assert "above 0 and below 1" not in captured.out + captured.err


# --- resume and tamper ------------------------------------------------------------------------------------

def _prepared(tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
              approvals: Callable[..., tuple[str, int]], flags: dict[str, float] | None) -> tuple[Path, Path]:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    with pytest.raises(Stop) as prepared:
        _ = run(target, out, MODEL, approve_cases=case_hash, approve_budget=calls, statistics=flags)
    assert prepared.value.code == "live-required"
    return target, out


def test_resuming_with_a_token_flag_added_to_a_run_frozen_without_one_differs(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = _prepared(tmp_path, make_target, write_draft, approvals, None)
    for flags in ({"min_token_saving": 0.5}, {"max_token_increase_per_gain": 0.0}):
        with pytest.raises(CodedError) as caught:
            _ = run(target, out, MODEL, statistics=flags)
        assert caught.value.code == "run-config-differs"


def test_resuming_a_frozen_token_run_with_zero_cap_flag_changed_to_a_different_cap_differs(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = _prepared(tmp_path, make_target, write_draft, approvals, {"max_token_increase_per_gain": 0.0})
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, statistics={"max_token_increase_per_gain": 1e-12})
    assert caught.value.code == "run-config-differs"
    with pytest.raises(Stop) as same:
        _ = run(target, out, MODEL, statistics={"max_token_increase_per_gain": -0.0})
    assert same.value.code == "live-required"


@pytest.mark.parametrize("mutate", ["drop-max", "drop-min", "string-min", "one-min", "bool-min", "nan-max"])
def test_a_frozen_record_with_a_bad_token_key_is_tampered(
        mutate: str, tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = _prepared(tmp_path, make_target, write_draft, approvals, {"min_token_saving": 0.5})
    record = read(out / "run.json")
    frozen = cast(dict[str, object], record["statistics"])
    if mutate == "drop-max":
        del frozen["max_token_increase_per_gain"]
    elif mutate == "drop-min":
        del frozen["min_token_saving"]
    elif mutate == "string-min":
        frozen["min_token_saving"] = "0.5"
    elif mutate == "one-min":
        frozen["min_token_saving"] = 1.0
    elif mutate == "bool-min":
        frozen["min_token_saving"] = True
    else:
        frozen["max_token_increase_per_gain"] = -1.0
    write(out / "run.json", record)
    with pytest.raises(Stop) as caught:
        _ = run(target, out, MODEL)
    assert caught.value.code == "run-record-tampered"


# --- end to end: zero cap and prompt ----------------------------------------------------------------------

SENTENCE = " Prefer fewer tokens when correctness is equal."


class _Probe:
    """A fake provider whose improved candidate always scores 1.0 and costs `IMPROVED` task tokens."""

    IMPROVED: int = 101
    prompts: list[str] = []

    def __init__(self, _model: str, _budget: object, _checkpoint: Callable[[], None]) -> None:
        pass

    def preflight(self) -> dict[str, object]:
        return {"isolation": "test-provider"}

    def close(self) -> None:
        pass

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
        del holdout
        improved = "improved" in candidate.files["SKILL.md"]
        low = self.IMPROVED if improved else 100
        usage = {"input_tokens": low, "cached_input_tokens": 0, "output_tokens": 0}
        return {"score": 1.0 if improved else 0.0, "candidate_hash": candidate.identity, "case_hash": case.identifier,
                "usage": usage, "task_usage": usage, "judge_usage": None}

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del candidate, case, holdout
        assert schema is not None
        self.prompts.append(prompt)
        return {"answer": {name: "improved" for name in cast(list[str], schema["required"])}}


def _probe(improved: int) -> type[_Probe]:
    return cast(type[_Probe], type("Probe", (_Probe,), {"IMPROVED": improved, "prompts": []}))


def _gate_of(tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
             approvals: Callable[..., tuple[str, int]], provider: type[_Probe],
             flags: dict[str, float] | None) -> dict[str, object]:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=provider,
                 statistics=flags)
    return cast(dict[str, object], result["gate"])


def test_a_zero_cap_run_adds_the_prompt_sentence_and_rejects_a_one_token_increase(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    provider = _probe(101)
    gate = _gate_of(tmp_path, make_target, write_draft, approvals, provider, {"max_token_increase_per_gain": 0.0})
    assert (gate["verdict"], gate["reasons"]) == ("reject", ["token-cost"])
    assert provider.prompts and all(SENTENCE in prompt for prompt in provider.prompts)


def test_a_zero_cap_run_promotes_an_equal_cost_winner(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    gate = _gate_of(tmp_path, make_target, write_draft, approvals, _probe(100), {"max_token_increase_per_gain": 0.0})
    assert (gate["verdict"], gate["reasons"], gate["token_delta"]) == ("promote", [], 0.0)


def test_a_run_without_flags_keeps_the_prompt_free_of_the_token_sentence(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    provider = _probe(101)
    gate = _gate_of(tmp_path, make_target, write_draft, approvals, provider, None)
    assert gate["verdict"] == "promote" and gate["token_delta"] is None and gate["token_se"] is None
    assert provider.prompts and all("tokens" not in prompt.split("\n", 1)[0] for prompt in provider.prompts)


# --- simulate ---------------------------------------------------------------------------------------------

def test_simulate_rates_are_unchanged_by_the_token_rule() -> None:
    result = simulate()
    assert (result.trials, result.cases, result.rate_2se, result.rate_one_holdout) == (2000, 10, 0.0365, 0.8115)
    again = simulate()
    assert (again.rate_2se, again.rate_one_holdout) == (result.rate_2se, result.rate_one_holdout)
