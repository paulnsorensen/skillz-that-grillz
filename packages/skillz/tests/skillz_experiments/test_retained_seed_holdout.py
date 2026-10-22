"""A retained seed still scores its baseline on the holdout, within the reserve. The verdict stays a tie."""
from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._budget import BudgetExhausted
from skillz_experiments._records import read
from skillz_experiments.run import _holdout, run
from skillz_experiments.run._state import RunState

from test_run_search import MODEL, Search, _start  # pyright: ignore[reportPrivateUsage]

pytestmark = pytest.mark.usefixtures("host_login")

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]


def _tie(tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
         ) -> tuple[Path, Path, Search, str, int]:
    """Start a run in which no candidate beats the seed, so the search retains it."""
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.score_of = lambda _text, _split: 0.0
    return target, out, state, case_hash, calls


def _go(target: Path, out: Path, state: Search, case_hash: str, calls: int) -> dict[str, object]:
    return run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)


def _holdout_ids(out: Path) -> set[str]:
    cases = cast(list[dict[str, object]], read(out / "cases.json")["cases"])
    return {cast(str, case["case_id"]) if "case_id" in case else cast(str, case["id"])
            for case in cases if case["split"] == "holdout"}


def _gate(out: Path) -> dict[str, object]:
    return cast(dict[str, object], read(out / "run.json")["gate"])


def _baseline_holdout(out: Path) -> list[dict[str, object]]:
    outcomes = cast(list[dict[str, object]], read(out / "run.json")["outcomes"])
    return [item for item in outcomes if item.get("arm") == "baseline" and item.get("split") == "holdout"]


@pytest.mark.ac("gh-317:AC-1")
def test_a_retained_seed_scores_its_baseline_on_the_holdout(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _tie(tmp_path, make_target, write_draft, approvals)
    result = _go(target, out, state, case_hash, calls)
    record = read(out / "run.json")
    assert result["phase"] == "complete" and record["winner_hash"] == record["seed_hash"]
    assert state.holdout_calls > 0
    baseline = _baseline_holdout(out)
    repeats = cast(int, record["repeats"])
    assert {cast(str, item["case_id"]) for item in baseline} == _holdout_ids(out)
    assert len(baseline) == repeats * len(_holdout_ids(out))
    assert all(item.get("arm") != "winner" for item in cast(list[dict[str, object]], record["outcomes"])
               if item.get("split") == "holdout")
    scores = cast(dict[str, float], _gate(out)["baseline_scores"])
    assert set(scores) == _holdout_ids(out) and "baseline_skipped" not in _gate(out)
    for case_id, mean in scores.items():
        found = [cast(float, item["score"]) for item in baseline if item["case_id"] == case_id]
        assert math.isclose(mean, math.fsum(found) / len(found))


@pytest.mark.ac("gh-317:AC-2")
def test_a_retained_seed_keeps_the_tie_verdict_and_its_one_reason(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _tie(tmp_path, make_target, write_draft, approvals)
    _ = _go(target, out, state, case_hash, calls)
    gate = _gate(out)
    assert gate["verdict"] == "inconclusive" and gate["reasons"] == ["winner-equals-baseline"]
    assert gate["delta"] == 0.0 and gate["se"] == 0.0


def _huge_need(_state: RunState) -> int:
    return 10**6


@pytest.mark.ac("gh-317:AC-3")
def test_a_retained_seed_with_too_little_budget_skips_the_baseline_and_completes(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state, case_hash, calls = _tie(tmp_path, make_target, write_draft, approvals)
    monkeypatch.setattr(_holdout, "replay_need", _huge_need)
    result = _go(target, out, state, case_hash, calls)
    gate = _gate(out)
    assert result["phase"] == "complete" and not _baseline_holdout(out) and state.holdout_calls == 0
    assert gate["baseline_skipped"] == "budget-short" and "baseline_scores" not in gate
    assert gate["reasons"] == ["winner-equals-baseline"]


@pytest.mark.ac("gh-317:AC-3")
def test_a_budget_stop_partway_through_the_baseline_keeps_the_scores_and_reports_unknown_coverage(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _tie(tmp_path, make_target, write_draft, approvals)
    state.holdout_head_delay = 0.3
    state.holdout_fault = lambda number: BudgetExhausted("out of calls") if number > 1 else None
    result = _go(target, out, state, case_hash, calls)
    gate = _gate(out)
    scores = cast(dict[str, float], gate["baseline_scores"])
    assert result["phase"] == "complete"
    assert scores and set(scores) < set(_holdout_ids(out))
    assert gate["baseline_skipped"] == "budget-short"
    runner = cast(dict[str, dict[str, object]], result["claims"])["runner"]
    assert cast(dict[str, str], runner["checks"])["holdout-coverage"] == "unknown"

@pytest.mark.ac("gh-317:AC-4")
def test_a_retained_seed_whose_baseline_evaluations_all_fault_completes(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _tie(tmp_path, make_target, write_draft, approvals)
    state.holdout_fault = lambda _number: RuntimeError("provider outage")
    result = _go(target, out, state, case_hash, calls)
    gate = _gate(out)
    assert result["phase"] == "complete"
    assert gate["baseline_skipped"] == "faulted" and "baseline_scores" not in gate
    assert gate["reasons"] == ["winner-equals-baseline"]
    assert _baseline_holdout(out) and all(item["status"] == "evaluation-failed" for item in _baseline_holdout(out))


@pytest.mark.ac("gh-317:AC-5")
def test_the_claims_compute_holdout_coverage_from_the_baseline_scores(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _tie(tmp_path, make_target, write_draft, approvals)
    result = _go(target, out, state, case_hash, calls)
    runner = cast(dict[str, dict[str, object]], result["claims"])["runner"]
    assert cast(dict[str, str], runner["checks"])["holdout-coverage"] == "pass"


@pytest.mark.ac("gh-317:AC-5")
def test_the_claims_keep_holdout_coverage_unknown_when_the_baseline_was_skipped(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _tie(tmp_path, make_target, write_draft, approvals)
    state.holdout_fault = lambda _number: RuntimeError("provider outage")
    result = _go(target, out, state, case_hash, calls)
    runner = cast(dict[str, dict[str, object]], result["claims"])["runner"]
    assert cast(dict[str, str], runner["checks"])["holdout-coverage"] == "unknown"
