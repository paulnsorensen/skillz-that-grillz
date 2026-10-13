"""Joined gate verdicts from process output and task token telemetry."""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import cast

import pytest

from support import Scenario, make_scenario, read_cases, read_events, read_record, run_scenario


def raw_holdout(scenario: Scenario) -> tuple[float, float, float | None, int]:
    record = read_record(scenario.out)
    cases = read_cases(scenario.out)
    task_logs = [entry for entry in read_events(scenario.out) if entry["role"] == "task"]
    differences: list[float] = []
    token_changes: list[float] = []
    witnessed = 0
    for case in cases:
        if case["split"] != "holdout":
            continue
        source = cast(dict[str, str], case["files"])["fixture.md"]
        scores: dict[str, list[float]] = {}
        tokens: dict[str, list[int]] = {}
        for arm, marker in (("baseline", record["seed_hash"]), ("winner", record["winner_hash"])):
            calls = [entry for entry in task_logs if entry["candidate"] == marker
                     and entry["visible_input"] == source]
            assert len(calls) == 3
            witnessed += len(calls)
            scores[arm] = [float(cast(dict[str, str], json.loads(
                cast(dict[str, str], entry["answer"])["result_json"]))["text"] == source.upper())
                for entry in calls]
            usages = [cast(dict[str, int] | None, entry["usage"]) for entry in calls]
            if all(usage is not None for usage in usages):
                tokens[arm] = [usage["input_tokens"] - usage["cached_input_tokens"] + usage["output_tokens"]
                               for usage in usages if usage is not None]
        differences.append(statistics.fmean(scores["winner"]) - statistics.fmean(scores["baseline"]))
        if len(tokens) == 2:
            token_changes.append(statistics.fmean(tokens["winner"]) / statistics.fmean(tokens["baseline"]) - 1)
    delta = statistics.fmean(differences)
    se = statistics.stdev(differences) / math.sqrt(len(differences))
    return delta, se, statistics.fmean(token_changes) if len(token_changes) == len(differences) else None, witnessed


@pytest.mark.ac("skillz-experiments-end-to-end:AC-4")
@pytest.mark.parametrize("harness", ["command", "claude", "codex"])
def test_unknown_winner_usage_blocks_promotion(tmp_path: Path, harness: str) -> None:
    scenario = make_scenario(tmp_path)
    result = run_scenario(scenario, tmp_path, harness=harness, mode="unknown-usage",
                          extra_flags=("--max-token-increase-per-gain", "1"))
    gate = cast(dict[str, object], result["gate"])
    delta, se, token_delta, witnessed = raw_holdout(scenario)
    assert (delta, se, token_delta, witnessed) == (1.0, 0.0, None, 36)
    assert (gate["verdict"], gate["reasons"], gate["delta"], gate["se"],
            gate["token_delta"], gate["token_se"], gate["cases"], gate["repeats"]) == (
        "inconclusive", ["unknown-usage"], delta, se, None, None, 6, 3)


@pytest.mark.ac("skillz-experiments-end-to-end:AC-4")
@pytest.mark.parametrize("harness", ["command", "claude", "codex"])
def test_costly_winner_rejects_after_scored_gain(tmp_path: Path, harness: str) -> None:
    scenario = make_scenario(tmp_path)
    result = run_scenario(scenario, tmp_path, harness=harness, mode="costly",
                          extra_flags=("--max-token-increase-per-gain", "0.5"))
    gate = cast(dict[str, object], result["gate"])
    delta, se, token_delta, witnessed = raw_holdout(scenario)
    assert (delta, se, token_delta, witnessed) == (1.0, 0.0, 1.0, 36)
    assert (gate["verdict"], gate["reasons"], gate["delta"], gate["se"],
            gate["token_delta"], gate["token_se"], gate["cases"], gate["repeats"]) == (
        "reject", ["token-cost"], delta, se, token_delta, 0.0, 6, 3)


@pytest.mark.ac("skillz-experiments-end-to-end:AC-4")
@pytest.mark.parametrize("flags, expected", [
    ((), ("inconclusive", None)),
    (("--max-token-increase-per-gain", "1"), ("inconclusive", -0.5)),
    (("--min-token-saving", "0.3"), ("promote-cheaper", -0.5)),
    (("--min-token-saving", "0.6"), ("inconclusive", -0.5)),
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
    ("--min-lower-bound", "lower-bound"),
])
def test_gain_floors_block_positive_evidence(tmp_path: Path, flag: str, reason: str) -> None:
    scenario = make_scenario(tmp_path)
    result = run_scenario(scenario, tmp_path, extra_flags=(flag, "1.1"))
    gate = cast(dict[str, object], result["gate"])
    delta, se, token_delta, witnessed = raw_holdout(scenario)
    assert (delta, se, token_delta, witnessed) == (1.0, 0.0, 0.0, 36)
    assert (gate["verdict"], gate["reasons"], gate["delta"], gate["se"], gate["cases"],
            gate["repeats"]) == ("inconclusive", [reason], delta, se, 6, 3)


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
