"""A provider fault is unscored: it stays out of means, stall, and feedback, and a faulted search stops with a code."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._budget import CallTimeout
from skillz_experiments._cases import CodedError
from skillz_experiments._records import read
from skillz_experiments.run import _outcomes
from skillz_experiments.run import run
from skillz_experiments.run._export import export, summary
from skillz_experiments.search import _engine
from test_run_search import MODEL, Search, _seed_pass, _start  # pyright: ignore[reportPrivateUsage]

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]
Launch = Callable[[], dict[str, object]]

RATE_LIMITED = "Codex fails: rate-limited (exit 1); the invocation counts against the budget"


def _ok(candidate: str, case: str, score: float, split: str = "validation") -> dict[str, object]:
    return {"arm": "search", "split": split, "case_id": case, "score": score, "search_candidate": candidate}


def _failed(candidate: str, case: str, split: str = "validation") -> dict[str, object]:
    return _ok(candidate, case, 0.0, split) | {"status": "evaluation-failed", "failure": "provider fell over"}


def _launch(tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
            families: int = 12) -> tuple[Path, Search, Launch]:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=families)

    def launch() -> dict[str, object]:
        return run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)

    return out, state, launch


def _rows(out: Path) -> list[dict[str, object]]:
    rows = cast(list[dict[str, object]], read(out / "run.json")["outcomes"])
    return [row for row in rows if row.get("arm") == "search" and row.get("split") in ("train", "validation")]


@pytest.mark.ac("gh-316:AC-1")
def test_a_failed_validation_outcome_adds_no_score_to_the_means() -> None:
    scores = _outcomes.add_validation_scores(
        {}, [_ok("a", "v1", 1.0), _failed("a", "v2"), _failed("b", "v1"), _failed("b", "v2")])
    assert scores == {"a": {"v1": 1.0}}
    assert _outcomes.complete_means(scores, {"v1", "v2"}) == {}


@pytest.mark.ac("gh-316:AC-1")
def test_a_failed_evaluation_makes_the_stall_observation_unknown_not_zero() -> None:
    outcomes = [_ok("seed", "v1", 0.5), {"arm": "search", "split": "reflection", "proposed": "c"}, _failed("c", "v1")]
    assert _outcomes.stall_observations(outcomes, {"v1"}) == [None]


@pytest.mark.ac("gh-316:AC-1")
def test_a_failed_evaluation_text_never_reaches_a_reflection_prompt(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    out, state, launch = _launch(tmp_path, make_target, write_draft, approvals)
    seen: list[int] = []

    def fault(number: int) -> Exception | None:
        if state.search_log[number - 1][1] == "train" and not seen:
            seen.append(number)
            return RuntimeError("secret provider text")
        return None

    state.evaluate_fault = fault
    assert launch()["phase"] == "complete"
    assert len(seen) == 1 and len(state.prompts) >= 1
    assert any(row.get("failure") == "secret provider text" for row in _rows(out))
    assert not any("secret provider text" in prompt for prompt in state.prompts)


@pytest.mark.ac("gh-316:AC-2")
def test_more_than_half_failed_after_four_finished_stops_with_search_faulted(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    out, state, launch = _launch(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda number: RuntimeError(RATE_LIMITED) if number % 3 != 1 else None
    with pytest.raises(CodedError) as caught:
        _ = launch()
    assert caught.value.code == "search-faulted" and "rate-limited" in str(caught.value)
    assert read(out / "run.json")["failure_code"] == "search-faulted" and read(out / "run.json")["phase"] == "prepared"


@pytest.mark.ac("gh-316:AC-2")
def test_five_consecutive_faults_stop_with_search_faulted_although_most_evaluations_scored(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    out, state, launch = _launch(tmp_path, make_target, write_draft, approvals)
    searched = _seed_pass(out) + 8
    state.evaluate_fault = lambda number: RuntimeError(RATE_LIMITED) if number > searched else None
    with pytest.raises(CodedError) as caught:
        _ = launch()
    rows = _rows(out)
    assert caught.value.code == "search-faulted"
    assert sum(row.get("status") == "evaluation-failed" for row in rows) * 2 < len(rows)


@pytest.mark.ac("gh-316:AC-2")
def test_four_consecutive_faults_among_scored_evaluations_do_not_stop_the_search(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    # The rule reads the evaluations in finish order. With two workers, a fast fault can finish before the slower
    # scored evaluation 4, and then 4 of 7 finished evaluations failed: a true majority. One worker keeps the call order.
    monkeypatch.setattr(_engine, "MAX_CONCURRENT_CALLS", 1)
    out, state, launch = _launch(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda number: RuntimeError(RATE_LIMITED) if 5 <= number <= 8 else None
    assert launch()["phase"] == "complete"
    assert sum(row.get("status") == "evaluation-failed" for row in _rows(out)) == 4


@pytest.mark.ac("gh-316:AC-3")
def test_the_summary_and_the_export_report_count_scored_failed_and_failed_by_reason(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    out, state, launch = _launch(tmp_path, make_target, write_draft, approvals)

    def fault(number: int) -> Exception | None:
        if number == 3:
            return RuntimeError(RATE_LIMITED)
        return CallTimeout("the model call exceeded its per-call timeout") if number == 5 else None

    state.evaluate_fault = fault
    result = launch()
    rows = _rows(out)
    expected = {"scored": len(rows) - 2, "failed": 2, "failed_by_reason": {"rate-limited": 1, "timeout": 1}}
    search = cast(dict[str, object], result["search"])
    assert {key: search[key] for key in expected} == expected
    assert cast(dict[str, object], summary(read(out / "run.json"))["search"])["failed_by_reason"] == expected["failed_by_reason"]
    _ = export(out, tmp_path / "exported")
    report = cast(dict[str, object], read(tmp_path / "exported" / "report.json")["search"])
    assert {key: report[key] for key in expected} == expected


@pytest.mark.ac("gh-316:AC-4")
def test_a_run_with_no_scored_holdout_comparison_because_of_faults_stops_with_a_code(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    out, state, launch = _launch(tmp_path, make_target, write_draft, approvals)
    state.holdout_fault = lambda _number: RuntimeError(RATE_LIMITED)
    with pytest.raises(CodedError) as caught:
        _ = launch()
    assert caught.value.code == "holdout-faulted" and "rate-limited" in str(caught.value)
    assert read(out / "run.json")["failure_code"] == "holdout-faulted"


@pytest.mark.ac("gh-316:AC-4")
def test_a_kept_seed_with_scored_evidence_is_still_an_inconclusive_complete_run(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    _, state, launch = _launch(tmp_path, make_target, write_draft, approvals)
    state.refuse = True
    result = launch()
    gate = cast(dict[str, object], result["gate"])
    assert result["phase"] == "complete" and gate["reasons"] == ["winner-equals-baseline"]
    assert cast(dict[str, object], result["search"])["failed"] == 0


@pytest.mark.ac("gh-316:AC-5")
def test_a_candidate_with_no_scored_validation_outcome_is_not_selected_over_the_seed(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    out, state, launch = _launch(tmp_path, make_target, write_draft, approvals, families=5)

    def fault(number: int) -> Exception | None:
        identity, split, _case = state.search_log[number - 1]
        return RuntimeError(RATE_LIMITED) if split == "validation" and identity != state.search_log[0][0] else None

    state.evaluate_fault = fault
    result = launch()
    assert cast(dict[str, object], result["search"])["retained_seed"] is True
    assert "best_validated" not in read(out / "run.json")


def _faulted_record(tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> tuple[Path, Search, Launch, int]:
    """End a run `search-faulted`, then return it with the evaluation count at the stop."""
    out, state, launch = _launch(tmp_path, make_target, write_draft, approvals)
    searched = _seed_pass(out) + 8
    state.evaluate_fault = lambda number: RuntimeError(RATE_LIMITED) if number > searched else None
    with pytest.raises(CodedError, match="rate-limited"):
        _ = launch()
    return out, state, launch, state.search_calls


@pytest.mark.ac("gh-316:AC-2")
def test_a_resume_after_search_faulted_counts_only_its_own_evaluations(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    out, state, launch, base = _faulted_record(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda number: RuntimeError(RATE_LIMITED) if number - base <= 2 else None
    assert launch()["phase"] == "complete"
    failed = [row for row in _rows(out) if row.get("status") == "evaluation-failed"]
    assert len(failed) > 5


@pytest.mark.ac("gh-316:AC-2")
def test_a_resumed_session_that_crosses_the_threshold_stops_with_search_faulted(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    _, state, launch, base = _faulted_record(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda number: RuntimeError(RATE_LIMITED) if number > base else None
    with pytest.raises(CodedError) as caught:
        _ = launch()
    assert caught.value.code == "search-faulted"
    assert state.search_calls - base == 4
