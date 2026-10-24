"""Joined gate verdicts from process output and task token telemetry."""
from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from support import HARNESSES, Scenario, make_scenario, raw_holdout, read_record, run_scenario


def _blocked(scenario: Scenario, result: dict[str, object]) -> tuple[object, ...]:
    """Return the one validation comparison of a challenger that the incumbent rule kept out (gh-500).

    The rule is the gate rule, so the seed stays the winner and the holdout gate reports `winner-equals-baseline`.
    """
    record = read_record(scenario.out)
    (comparison,) = cast(list[dict[str, object]], cast(dict[str, object], record["search"])["comparisons"])
    gate = cast(dict[str, object], result["gate"])
    assert "best_validated" not in record and record["winner_hash"] == record["seed_hash"]
    assert (gate["verdict"], gate["reasons"], gate["cases"], gate["repeats"]) == (
        "inconclusive", ["winner-equals-baseline"], 6, 3)
    return comparison["verdict"], comparison["reasons"], comparison["delta"], comparison["se"]


@pytest.mark.ac("skillz-experiments-end-to-end:AC-4")
@pytest.mark.parametrize("harness", HARNESSES)
def test_unknown_winner_usage_blocks_promotion(tmp_path: Path, harness: str) -> None:
    scenario = make_scenario(tmp_path)
    result = run_scenario(scenario, tmp_path, harness=harness, mode="unknown-usage",
                          extra_flags=("--max-token-increase-per-gain", "1"))
    assert _blocked(scenario, result) == ("inconclusive", ["unknown-usage"], 1.0, 0.0)


@pytest.mark.ac("skillz-experiments-end-to-end:AC-4")
@pytest.mark.parametrize("harness", HARNESSES)
def test_costly_winner_rejects_after_scored_gain(tmp_path: Path, harness: str) -> None:
    scenario = make_scenario(tmp_path)
    result = run_scenario(scenario, tmp_path, harness=harness, mode="costly",
                          extra_flags=("--max-token-increase-per-gain", "0.5"))
    assert _blocked(scenario, result) == ("reject", ["token-cost"], 1.0, 0.0)


@pytest.mark.ac("skillz-experiments-end-to-end:AC-4")
@pytest.mark.parametrize("flags, expected", [
    ((), ("inconclusive", None)),
    (("--max-token-increase-per-gain", "1"), ("inconclusive", -0.5)),
    (("--min-token-saving", "0.3", "--max-quality-loss", "0"), ("promote-cheaper", -0.5)),
    (("--min-token-saving", "0.6", "--max-quality-loss", "0"), ("inconclusive", -0.5)),
])
def test_cheaper_tied_winner_needs_opt_in(tmp_path: Path, flags: tuple[str, ...],
                                              expected: tuple[str, float | None]) -> None:
    scenario = make_scenario(tmp_path)
    for case in scenario.draft:
        if case["family"] in {"f9", "f7", "f5", "f3", "f0", "f4"}:
            source = cast(dict[str, str], case["files"])["fixture.md"].upper()
            cast(dict[str, str], case["files"])["fixture.md"] = source
    result = run_scenario(scenario, tmp_path, mode="cheap", extra_flags=("--seed", "1", *flags))
    gate = cast(dict[str, object], result["gate"])
    delta, se, token_delta, witnessed = raw_holdout(scenario)
    assert (delta, se, token_delta, witnessed) == (0.0, 0.0, -0.5, 36)
    assert (gate["verdict"], gate["token_delta"]) == expected
    assert gate["reasons"] == [] and gate["se"] == se and gate["delta"] == delta
    assert (gate["cases"], gate["repeats"]) == (6, 3)


@pytest.mark.ac("skillz-experiments-end-to-end:AC-4")
@pytest.mark.parametrize("flag, reason", [
    ("--min-gain", "min-gain"),
    ("--min-lower-bound", "min-lower-bound"),
])
def test_gain_floors_block_positive_evidence(tmp_path: Path, flag: str, reason: str) -> None:
    scenario = make_scenario(tmp_path)
    result = run_scenario(scenario, tmp_path, extra_flags=(flag, "1.1"))
    assert _blocked(scenario, result) == ("inconclusive", [reason], 1.0, 0.0)


@pytest.mark.ac("skillz-experiments-end-to-end:AC-4")
def test_nonzero_case_se_prevents_promotion(tmp_path: Path) -> None:
    scenario = make_scenario(tmp_path)
    held = {"f9", "f7", "f5", "f3", "f0", "f4"}
    for case in scenario.draft:
        if case["family"] in held - {"f9", "f7"}:
            source = cast(dict[str, str], case["files"])["fixture.md"].upper()
            cast(dict[str, str], case["files"])["fixture.md"] = source
    result = run_scenario(scenario, tmp_path, extra_flags=("--seed", "1"))
    gate = cast(dict[str, object], result["gate"])
    delta, se, token_delta, witnessed = raw_holdout(scenario)
    assert delta == pytest.approx(1 / 3) and se > 0 and delta < 2 * se
    assert token_delta == 0.0 and witnessed == 36
    assert (gate["verdict"], gate["reasons"], gate["cases"], gate["repeats"]) == (
        "inconclusive", [], 6, 3)
    assert gate["delta"] == pytest.approx(delta) and gate["se"] == pytest.approx(se)
