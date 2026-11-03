"""gh-651: the holdout gate does not promote a winner that loses a case the baseline passed on every repeat."""
# pyright: reportAny=false
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from skillz_experiments._records import read
from skillz_experiments.gate import Statistics, holdout_deltas, verdict
from skillz_experiments.run import run

from test_run_search import MODEL, Search

# One case drops from 1 to 0 while nine cases gain 0.5: the mean delta (0.35) exceeds 2*SE (0.30).
MASKED = {"a": -1.0, **{f"c{index}": 0.5 for index in range(9)}}


@pytest.mark.ac("gh-651:AC-10")
@pytest.mark.parametrize(("baseline", "winner", "flipped"), [
    ([1.0, 1.0, 1.0], [0.0, 0.0, 0.0], True),
    ([1.0, 1.0, 1.0], [1.0, 0.0, 0.0], True),
    ([1.0, 1.0, 1.0], [1.0, 1.0, 0.0], False),
    ([1.0, 1.0, 1.0], [0.5, 0.5, 0.5], False),
    ([1.0, 1.0, 0.0], [0.0, 0.0, 0.0], False),
    ([0.9, 0.9, 0.9], [0.0, 0.0, 0.0], False),
])
def test_a_case_flips_only_when_the_baseline_passes_every_repeat_and_the_winner_mean_falls_below_the_margin(
        baseline: list[float], winner: list[float], flipped: bool) -> None:
    paired = holdout_deltas({"a": baseline, "b": [0.5] * 3, "z": [1.0] * 3}, {"a": winner, "b": [0.0] * 3})
    assert paired.flips == (["a"] if flipped else [])
    # `z` has no scored winner repeat, so a flip cannot be ruled out: it is unscored, not a flip.
    assert "z" in paired.unscored and "b" not in paired.unscored


@pytest.mark.ac("gh-651:AC-10")
@pytest.mark.parametrize(("winner", "repeats", "flips", "unscored"), [
    ([], 3, [], ["a"]),
    ([1.0], 3, [], ["a"]),
    ([1.0, 0.0], 3, [], ["a"]),
    ([1.0, 1.0], 3, [], []),
    ([0.0], 3, ["a"], []),
    ([], None, [], ["a"]),
    ([1.0], None, [], []),
])
def test_a_protected_case_whose_failed_winner_repeats_could_hide_a_flip_is_unscored(
        winner: list[float], repeats: int | None, flips: list[str], unscored: list[str]) -> None:
    paired = holdout_deltas({"a": [1.0] * 3, "b": [0.0] * 3}, {"a": winner, "b": [1.0] * 3}, repeats)
    assert (paired.flips, paired.unscored) == (flips, unscored)


@pytest.mark.ac("gh-651:AC-10")
def test_the_flips_are_sorted_by_case_id() -> None:
    perfect, lost = [1.0] * 3, [0.0] * 3
    paired = holdout_deltas({"m": perfect, "b": perfect, "x": perfect}, {"m": lost, "b": lost, "x": perfect})
    assert paired.flips == ["b", "m"]


@pytest.mark.ac("gh-651:AC-11")
def test_a_flip_turns_a_promote_into_inconclusive_and_names_the_case() -> None:
    assert verdict(MASKED).verdict == "promote"
    guarded = verdict(MASKED, flips=["a"])
    assert (guarded.verdict, guarded.reasons) == ("inconclusive", ("negative-flip:a",))
    assert (guarded.delta, guarded.se) == (verdict(MASKED).delta, verdict(MASKED).se)


@pytest.mark.ac("gh-651:AC-11")
def test_an_unscored_protected_case_turns_a_promote_into_inconclusive_after_the_flips() -> None:
    guarded = verdict(MASKED, unscored=["z"])
    assert (guarded.verdict, guarded.reasons) == ("inconclusive", ("unscored-protected:z",))
    both = verdict(MASKED, flips=["a"], unscored=["z"])
    assert both.reasons == ("negative-flip:a", "unscored-protected:z")
    assert verdict({"a": -1.0, "b": 0.5}, unscored=["z"]) == verdict({"a": -1.0, "b": 0.5})


@pytest.mark.ac("gh-651:AC-11")
def test_a_flip_turns_a_promote_cheaper_into_inconclusive() -> None:
    deltas = {"a": -1.0, **{f"c{index}": 0.15 for index in range(9)}}
    thresholds = Statistics(min_token_saving=0.3, max_quality_loss=0.2)
    tokens: dict[str, tuple[float | None, float | None]] = {name: (100.0, 50.0) for name in deltas}
    assert verdict(deltas, thresholds=thresholds, tokens=tokens).verdict == "promote-cheaper"
    guarded = verdict(deltas, thresholds=thresholds, tokens=tokens, flips=["a"])
    assert (guarded.verdict, guarded.reasons) == ("inconclusive", ("negative-flip:a",))


@pytest.mark.ac("gh-651:AC-11")
def test_a_flip_leaves_a_result_that_does_not_promote_unchanged() -> None:
    for deltas in ({"a": -1.0, "b": -1.0}, {"a": -1.0, "b": 0.5}):
        assert verdict(deltas, flips=["a"]) == verdict(deltas)


@pytest.mark.ac("gh-651:AC-11")
def test_a_family_breach_still_rejects_after_the_floor_guard() -> None:
    deltas = {"a": -1.0, "b": -1.0, **{f"c{index}": 0.5 for index in range(20)}}
    families = {name: ("x" if name in ("a", "b") else name) for name in deltas}
    thresholds = Statistics(family_budget=0.5)
    assert verdict(deltas).verdict == "promote"
    guarded = verdict(deltas, thresholds=thresholds, families=families, flips=["a"])
    assert (guarded.verdict, guarded.reasons) == ("reject", ("negative-flip:a", "family-regression:x"))


@pytest.mark.ac("gh-651:AC-12")
def test_the_holdout_gate_does_not_promote_a_winner_that_loses_a_case_the_baseline_always_passed(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out, families=6)
    approved, calls = approvals(target, out)
    held = sorted(case["id"] for case in cast(list[dict[str, str]], read(out / "cases.json")["cases"])
                  if case["split"] == "holdout")
    victim = held[0]
    state = Search(target)

    def score(text: str, split: str, case_id: str) -> float | None:
        if split != "holdout":
            return None
        improved = "improved" in text
        if case_id == victim:
            return 1 / 3 if improved else 1.0
        return 1.0 if improved else 0.0

    state.case_score_of = score
    result = run(target, out, MODEL, live=True, approve_cases=approved, approve_budget=calls, factory=state.factory)
    gate = cast(dict[str, Any], read(out / "run.json")["gate"])  # pyright: ignore[reportExplicitAny]
    deltas = {case_id: (-2 / 3 if case_id == victim else 1.0) for case_id in held}
    assert verdict(deltas).verdict == "promote", json.dumps(deltas)
    assert (gate["verdict"], gate["reasons"]) == ("inconclusive", [f"negative-flip:{victim}"])
    assert victim in gate["moves"]["broke"]
    claims = cast(dict[str, Any], result["claims"])  # pyright: ignore[reportExplicitAny]
    assert claims["improvement"]["status"] == "inconclusive" and claims["apply"] is False
    assert f"negative-flip:{victim}" in claims["improvement"]["reasons"]



@pytest.mark.ac("gh-651:AC-12")
def test_the_holdout_gate_does_not_promote_a_winner_whose_evaluations_of_a_protected_case_all_fail(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out, families=6)
    approved, calls = approvals(target, out)
    held = sorted(case["id"] for case in cast(list[dict[str, str]], read(out / "cases.json")["cases"])
                  if case["split"] == "holdout")
    victim = held[0]
    state = Search(target)
    state.case_score_of = lambda text, split, case_id: (
        None if split != "holdout" else 1.0 if case_id == victim or "improved" in text else 0.0)
    state.holdout_case_fault = lambda improved, case_id: (
        RuntimeError("the call timed out") if improved and case_id == victim else None)
    result = run(target, out, MODEL, live=True, approve_cases=approved, approve_budget=calls, factory=state.factory)
    gate = cast(dict[str, Any], read(out / "run.json")["gate"])  # pyright: ignore[reportExplicitAny]
    assert verdict({case_id: 1.0 for case_id in held if case_id != victim}).verdict == "promote"
    assert (gate["verdict"], gate["reasons"]) == ("inconclusive", [f"unscored-protected:{victim}"])
    claims = cast(dict[str, Any], result["claims"])  # pyright: ignore[reportExplicitAny]
    assert claims["improvement"]["status"] == "inconclusive" and claims["apply"] is False