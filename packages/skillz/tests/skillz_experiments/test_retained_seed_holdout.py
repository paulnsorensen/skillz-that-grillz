"""A retained seed still scores its baseline on the holdout, within the reserve. The verdict stays a tie."""
from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._budget import BudgetExhausted
from skillz_experiments._cases import CodedError
from skillz_experiments._records import read
from skillz_experiments.run import _holdout, run, summary
from skillz_experiments.run._state import RunState

from fake_provider import search_approval
from test_run_search import MODEL, Search, _start  # pyright: ignore[reportPrivateUsage]

pytestmark = pytest.mark.usefixtures("host_login")

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]


def _tie(tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> tuple[Path, Path, Search]:
    """Measure two seed slots per case. Only the extra holdout repeat can need a new call."""
    target, out, measured, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    score: Callable[[str, str], float] = lambda _text, _split: 0.0
    measured.score_of = score
    result = run(target, out, MODEL, live=True, repeats=3, approve_cases=case_hash, approve_budget=calls,
                 factory=measured.factory)
    assert result["search"] == "not-run" and measured.holdout_calls == 2 * len(_holdout_ids(out))
    state = Search(target)
    state.score_of = score
    return target, out, state


def _go(target: Path, out: Path, state: Search) -> dict[str, object]:
    if read(out / "run.json")["plan_mode"] == "baseline":
        return run(target, out, MODEL, live=True, search=True, approve_budget=search_approval(target, out, MODEL),
                   factory=state.factory)
    return run(target, out, MODEL, live=True, factory=state.factory)


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
    target, out, state = _tie(tmp_path, make_target, write_draft, approvals)
    result = _go(target, out, state)
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
    target, out, state = _tie(tmp_path, make_target, write_draft, approvals)
    _ = _go(target, out, state)
    gate = _gate(out)
    assert gate["verdict"] == "inconclusive" and gate["reasons"] == ["winner-equals-baseline"]
    assert gate["delta"] == 0.0 and gate["se"] == 0.0


def _huge_need(_state: RunState) -> int:
    return 10**6


@pytest.mark.ac("gh-317:AC-3")
def test_a_retained_seed_with_too_little_budget_skips_only_new_slots_and_completes(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state = _tie(tmp_path, make_target, write_draft, approvals)
    monkeypatch.setattr(_holdout, "replay_need", _huge_need)
    result = _go(target, out, state)
    gate = _gate(out)
    assert result["phase"] == "complete" and state.holdout_calls == 0
    assert len(_baseline_holdout(out)) == 2 * len(_holdout_ids(out))
    assert gate["baseline_skipped"] == "budget-short"
    assert set(cast(dict[str, float], gate["baseline_scores"])) == _holdout_ids(out)
    assert gate["reasons"] == ["winner-equals-baseline"]


@pytest.mark.ac("gh-317:AC-3")
def test_a_budget_stop_partway_through_the_baseline_keeps_the_scores_and_reports_unknown_coverage(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state = _tie(tmp_path, make_target, write_draft, approvals)
    state.holdout_head_delay = 0.3
    state.holdout_fault = lambda number: BudgetExhausted("out of calls") if number > 1 else None
    result = _go(target, out, state)
    gate = _gate(out)
    scores = cast(dict[str, float], gate["baseline_scores"])
    assert result["phase"] == "complete"
    assert set(scores) == _holdout_ids(out)
    assert len([item for item in _baseline_holdout(out) if item["repeat"] == 2 and "score" in item]) == 1
    assert gate["baseline_skipped"] == "budget-short"
    runner = cast(dict[str, dict[str, object]], result["claims"])["runner"]
    assert cast(dict[str, str], runner["checks"])["holdout-coverage"] == "unknown"


@pytest.mark.ac("gh-317:AC-3")
def test_a_resume_with_too_little_budget_keeps_the_restored_baseline_scores(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state = _tie(tmp_path, make_target, write_draft, approvals)
    state.holdout_head_delay = 0.3
    state.holdout_fault = lambda number: CodedError("provider-unavailable", "the provider went away") if number > 1 else None
    with pytest.raises(CodedError):
        _ = _go(target, out, state)
    restored = _holdout.arm_scores(_baseline_holdout(out), "baseline")
    assert set(restored) == _holdout_ids(out)
    assert sorted(map(len, restored.values())) == [2] * (len(restored) - 1) + [3]
    state.holdout_fault = lambda _number: None
    monkeypatch.setattr(_holdout, "replay_need", _huge_need)
    result = _go(target, out, state)
    gate = _gate(out)
    assert result["phase"] == "complete" and gate["baseline_skipped"] == "budget-short"
    assert _holdout.arm_scores(_baseline_holdout(out), "baseline") == restored
    assert set(cast(dict[str, float], gate["baseline_scores"])) == set(restored)


@pytest.mark.ac("gh-317:AC-4")
def test_a_retained_seed_whose_new_baseline_repeats_all_fault_preserves_measured_scores(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state = _tie(tmp_path, make_target, write_draft, approvals)
    state.holdout_fault = lambda _number: RuntimeError("provider outage")
    result = _go(target, out, state)
    gate = _gate(out)
    assert result["phase"] == "complete"
    assert set(cast(dict[str, float], gate["baseline_scores"])) == _holdout_ids(out)
    assert gate["reasons"] == ["winner-equals-baseline"]
    new = [item for item in _baseline_holdout(out) if item["repeat"] == 2]
    assert {item["case_id"] for item in new} == _holdout_ids(out)
    assert all(item["status"] == "evaluation-failed" and item["failure"] == "provider outage" for item in new)
    assert all(len(scores) == 2 for scores in _holdout.arm_scores(_baseline_holdout(out), "baseline").values())


@pytest.mark.ac("gh-317:AC-5")
def test_the_claims_compute_holdout_coverage_from_the_baseline_scores(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state = _tie(tmp_path, make_target, write_draft, approvals)
    result = _go(target, out, state)
    runner = cast(dict[str, dict[str, object]], result["claims"])["runner"]
    assert cast(dict[str, str], runner["checks"])["holdout-coverage"] == "pass"


@pytest.mark.ac("gh-317:AC-5")
def test_the_claims_keep_holdout_coverage_unknown_when_the_baseline_was_skipped(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state = _tie(tmp_path, make_target, write_draft, approvals)
    state.holdout_fault = lambda _number: RuntimeError("provider outage")
    result = _go(target, out, state)
    runner = cast(dict[str, dict[str, object]], result["claims"])["runner"]
    assert cast(dict[str, str], runner["checks"])["holdout-coverage"] == "unknown"


@pytest.mark.ac("gh-317:AC-5")
def test_a_baseline_slot_that_ends_in_a_check_timeout_leaves_holdout_coverage_unknown(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state = _tie(tmp_path, make_target, write_draft, approvals)
    _ = _go(target, out, state)
    record = read(out / "run.json")
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    victims = [item for item in outcomes if item.get("arm") == "baseline" and item.get("split") == "holdout"]
    victim, other = victims[0], next(item for item in victims if item["case_id"] != victims[0]["case_id"])
    outcomes[outcomes.index(victim)] = {**{key: value for key, value in victim.items() if key != "score"},
                                        "status": "candidate-contract-rejected", "rejection_kind": "timeout"}

    def coverage() -> str:
        return cast(dict[str, dict[str, dict[str, str]]], summary(record, out)["claims"])["runner"]["checks"]["holdout-coverage"]

    assert coverage() == "unknown"
    outcomes.remove(other)
    assert coverage() == "fail"
