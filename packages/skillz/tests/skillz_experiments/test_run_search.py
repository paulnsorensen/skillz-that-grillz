"""The search share of a run: GEPA headroom, best validated pick, failed evaluations, and resume."""
from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast, final

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._harness import Harness
from skillz_experiments._harness import EnvironmentDiffers
from skillz_experiments._intake import intake_contract
from skillz_experiments._records import read
from skillz_experiments._records import write
from skillz_experiments._runtime import Budget, BudgetExhausted
from skillz_experiments._search import overshoot
from skillz_experiments import _workflow as workflow
from skillz_experiments._workflow import PREFLIGHT_CALLS, Stop, export, run

pytestmark = pytest.mark.usefixtures("host_login")

MODEL = "local-test"


@final
class Search:
    """A fake provider that claims the budget like the real harness. Scores are noisy; `improved` text scores best."""

    def __init__(self, target: Path, noise: int = 0) -> None:
        self.contract = intake_contract(target)
        self.noise = noise
        self.lock = threading.Lock()
        self.search_calls = 0
        self.proposals = 0
        self.improved_validated = 0
        self.evaluate_fault: Callable[[int], Exception | None] = lambda _number: None
        self.exhaust_after_improved: int | None = None
        self.fail_holdout = 0
        self.abort_after_improved: int | None = None
        self.padding = 0
        self.drain_search = False
        self.score_of: Callable[[str, str], float | None] | None = None
        self.invokes = 0
        self.invoke_fault: Callable[[int], Exception | None] = lambda _number: None
        self.holdout_calls = 0
        self.holdout_fault: Callable[[int], Exception | None] = lambda _number: None
        self.holdout_head_delay = 0.0
        self.search_delay = 0.0
        self.preflight_error: Exception | None = None
        self.close_error: Exception | None = None

    def factory(self, _model: str, budget: Budget, checkpoint: Callable[[], None]) -> Provider:
        return Provider(self, budget, checkpoint)


@final
class Provider:
    def __init__(self, state: Search, budget: Budget, checkpoint: Callable[[], None]) -> None:
        self.state = state
        self.budget = budget
        self.checkpoint = checkpoint

    def preflight(self) -> dict[str, object]:
        """Claim the live calls once. A resume reuses the recorded pass, as the real harness does."""
        if self.state.preflight_error is not None:
            raise self.state.preflight_error
        if self.budget.calls == 0:
            for _ in range(PREFLIGHT_CALLS):
                self.budget.claim()
        return {"isolation": "test-provider"}

    def close(self) -> None:
        error, self.state.close_error = self.state.close_error, None
        if error is not None:
            raise error

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
        state = self.state
        improved = "improved" in candidate.files["SKILL.md"]
        if not holdout:
            with state.lock:
                state.search_calls += 1
                number = state.search_calls
                if state.exhaust_after_improved is not None and state.improved_validated >= state.exhaust_after_improved:
                    raise BudgetExhausted("global invocation budget exhausted")
                if state.abort_after_improved is not None and state.improved_validated >= state.abort_after_improved:
                    state.abort_after_improved = None
                    raise CodedError("other-code", "the process stopped")
                if improved and case.split == "validation":
                    state.improved_validated += 1
            if number > 1:
                _ = threading.Event().wait(state.search_delay)
            fault = state.evaluate_fault(number)
            if fault is not None:
                for _ in range(state.contract.calls(case.kind)):
                    self.budget.claim()
                raise fault
        if holdout:
            with state.lock:
                state.holdout_calls += 1
                number = state.holdout_calls
            if number == 1:
                _ = threading.Event().wait(state.holdout_head_delay)
            fault = state.holdout_fault(number)
            if fault is not None:
                raise fault
        if holdout and state.drain_search:
            with state.lock:
                while state.drain_search and self.budget.calls < self.budget.maximum - self.budget.reserve:
                    self.budget.claim()
                state.drain_search = False
        for _ in range(state.contract.calls(case.kind)):
            self.budget.claim(holdout=holdout)
        if holdout:
            with state.lock:
                failing = state.fail_holdout > 0
                state.fail_holdout -= failing
            if failing:
                raise RuntimeError("provider fell over")
        self.checkpoint()
        digest = hashlib.sha256(f"{state.noise}{candidate.identity}{case.identifier}".encode()).digest()[0]
        base = 0.7 if improved else 0.2
        score = min(1.0, max(0.0, base + (digest % 3 - 1) * 0.2))
        if state.score_of is not None:
            chosen = state.score_of(candidate.files["SKILL.md"], case.split)
            score = score if chosen is None else chosen
        return {"score": score, "candidate_hash": candidate.identity, "case_hash": case.identifier}

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del prompt, candidate, case
        assert schema is not None
        with self.state.lock:
            self.state.invokes += 1
            fault = self.state.invoke_fault(self.state.invokes)
        if fault is not None:
            raise fault
        self.budget.claim(holdout=holdout)
        with self.state.lock:
            self.state.proposals += 1
            number = self.state.proposals
        return {"answer": {name: f"improved {number} " + "x" * self.state.padding for name in cast(list[str], schema["required"])}}


def _start(tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
           approvals: Callable[..., tuple[str, int]], noise: int = 0,
           families: int = 5) -> tuple[Path, Path, Search, str, int]:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out, families=families)
    case_hash, calls = approvals(target, out)
    return target, out, Search(target, noise), case_hash, calls


@pytest.mark.parametrize("noise", range(5))
def test_a_run_at_the_planned_budget_never_ends_budget_exhausted_seed_retained(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], noise: int) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, noise)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert cast(dict[str, object], result["search"])["reason"] == "validation-mean"
    assert result["phase"] == "complete" and cast(int, read(out / "run.json")["calls"]) <= calls


def test_a_mid_search_exhaustion_keeps_the_best_validated_candidate(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.exhaust_after_improved = len(_validation(out))
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    search = cast(dict[str, object], result["search"])
    assert search == {"reason": "budget-exhausted-best-validated", "retained_seed": False}
    assert "improved" in cast(dict[str, str], read(out / "run.json")["winner"])["SKILL.md"]


def _validation(out: Path) -> list[str]:
    cases = cast(list[dict[str, object]], read(out / "cases.json")["cases"])
    return [str(case["id"]) for case in cases if case["split"] == "validation"]


def test_one_failed_evaluation_scores_zero_and_does_not_abort_the_search(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda number: RuntimeError("provider fell over") if number == 3 else None
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert result["phase"] == "complete"
    assert cast(dict[str, object], result["search"])["reason"] == "validation-mean"
    outcomes = cast(list[dict[str, object]], read(out / "run.json")["outcomes"])
    failed = [item for item in outcomes if item.get("status") == "evaluation-failed"]
    assert len(failed) == 1 and failed[0]["score"] == 0.0 and failed[0]["failure"] == "provider fell over"


def test_a_resumed_search_spends_at_most_the_remaining_share(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    allowances: list[int] = []
    real = workflow.pareto_search  # pyright: ignore[reportPrivateLocalImportUsage]

    def spy(*args: Any, **kwargs: Any) -> Any:  # pyright: ignore[reportAny, reportExplicitAny]
        allowances.append(cast(int, kwargs["metric_calls"]))
        return real(*args, **kwargs)  # pyright: ignore[reportAny]

    monkeypatch.setattr(workflow, "pareto_search", spy)
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda number: (
        EnvironmentDiffers("runtime environment differs from the frozen record") if number == 3 else None)
    with pytest.raises(EnvironmentDiffers):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    assert record["phase"] == "prepared"
    planned = cast(int, cast(dict[str, object], record["estimate"])["metric_calls"])
    margin = overshoot(len(_validation(out)))
    spent = sum(item.get("arm") == "search" and item.get("split") in ("train", "validation")
                for item in cast(list[dict[str, object]], record["outcomes"]))
    assert 0 < spent < planned - margin
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert result["phase"] == "complete"
    assert allowances == [planned - margin, planned - spent - margin]


def test_a_resume_with_a_spent_search_share_keeps_the_candidate_validated_before_the_abort(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    validation = _validation(out)
    state.abort_after_improved = len(validation)
    with pytest.raises(CodedError, match="the process stopped"):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    planned = cast(int, cast(dict[str, object], record["estimate"])["metric_calls"])
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    spent = sum(item.get("arm") == "search" and item.get("split") in ("train", "validation") for item in outcomes)
    filler: dict[str, object] = {"arm": "search", "split": "train", "case_id": "filler", "score": 0.0}
    outcomes.extend([filler] * (planned - overshoot(len(validation)) - spent))
    write(out / "run.json", record)
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert cast(dict[str, object], result["search"]) == {"reason": "search-share-spent-best-validated", "retained_seed": False}
    assert "improved" in cast(dict[str, str], read(out / "run.json")["winner"])["SKILL.md"]


def test_a_resume_with_a_real_allowance_keeps_the_pre_abort_candidate_over_worse_later_proposals(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.abort_after_improved = len(_validation(out))
    with pytest.raises(CodedError, match="the process stopped"):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    state.score_of = lambda text, _split: 0.0 if "improved" in text and "improved 1 " not in text else None
    proposals_before = state.proposals
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert state.proposals > proposals_before
    assert cast(dict[str, object], result["search"]) == {"reason": "validation-mean", "retained_seed": False}
    assert "improved 1 " in cast(dict[str, str], read(out / "run.json")["winner"])["SKILL.md"]


def test_a_validated_candidate_below_the_seed_leaves_the_seed_as_the_winner(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.score_of = lambda text, split: (0.1 if split == "validation" else 0.9) if "improved" in text else 0.5
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    assert "best_validated" in record
    assert cast(dict[str, object], result["search"]) == {"reason": "validation-mean", "retained_seed": True}
    assert cast(dict[str, object], record["gate"])["reason"] == "winner-equals-baseline"


def test_a_validated_candidate_that_ties_the_seed_leaves_the_seed_as_the_winner(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.score_of = lambda text, split: (0.5 if split == "validation" else 0.9) if "improved" in text else 0.5
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    kept = cast(dict[str, object], read(out / "run.json")["best_validated"])
    assert kept["mean"] == pytest.approx(0.5)
    assert cast(dict[str, object], result["search"]) == {"reason": "validation-mean", "retained_seed": True}


def test_an_earlier_validated_candidate_stays_best_over_a_later_worse_one(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)

    def table(text: str, split: str) -> float | None:
        if "improved 1 " in text:
            return 0.9 if split == "validation" else 0.5
        return (0.6 if split == "validation" else 1.0) if "improved" in text else None

    state.score_of = table
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    validated = {item["search_candidate"] for item in outcomes if item.get("split") == "validation" and "search_candidate" in item}
    assert len(validated) >= 3
    kept = cast(dict[str, object], record["best_validated"])
    assert "improved 1 " in cast(dict[str, str], kept["files"])["SKILL.md"] and kept["mean"] == pytest.approx(0.9)
    assert "improved 1 " in cast(dict[str, str], record["winner"])["SKILL.md"]


@pytest.mark.parametrize("code", ["isolation-failed", "harness-changed"])
def test_a_coded_reflection_failure_stops_the_search_instead_of_skipping_the_proposal(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], code: str) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.invoke_fault = lambda number: CodedError(code, "a guard fired") if number == 1 else None
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == code
    record = read(out / "run.json")
    assert record["phase"] == "prepared" and record["failure"] == "a guard fired"
    assert state.invokes == 1


def test_a_lasting_reflection_fault_on_the_last_iteration_still_stops_the_run(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    real = workflow.pareto_search  # pyright: ignore[reportPrivateLocalImportUsage]

    def tiny(*args: Any, **kwargs: Any) -> Any:  # pyright: ignore[reportAny, reportExplicitAny]
        return real(*args, **{**kwargs, "metric_calls": len(cast(list[object], args[3])) + 2})  # pyright: ignore[reportAny]

    monkeypatch.setattr(workflow, "pareto_search", tiny)
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.invoke_fault = lambda _number: CodedError("isolation-failed", "a guard fired")
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == "isolation-failed" and state.invokes >= 1
    record = read(out / "run.json")
    assert record["phase"] == "prepared" and record["failure"] == "a guard fired"


def test_a_task_fault_on_the_first_validation_evaluation_starts_no_further_task_call(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.evaluate_fault = lambda number: CodedError("isolation-failed", "a guard fired") if number == 1 else None
    state.search_delay = 0.1
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == "isolation-failed"
    assert state.search_calls <= 2


def test_a_gate_fault_with_a_slow_head_task_starts_no_further_holdout_call(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.holdout_head_delay = 0.5
    state.holdout_fault = lambda number: CodedError("isolation-failed", "a guard fired") if number == 2 else None
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == "isolation-failed"
    assert read(out / "run.json")["phase"] == "searched"
    assert state.holdout_calls == 2


def test_a_transient_reflection_failure_skips_one_proposal_and_the_search_finishes(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.invoke_fault = lambda number: RuntimeError("reflection fell over") if number == 1 else None
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert result["phase"] == "complete" and state.invokes > 1


def test_the_run_record_stays_small_across_many_proposals(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.padding = 20_000
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    kept = cast(dict[str, object], record["best_validated"])
    assert "candidates" not in record and set(kept) == {"identity", "mean", "files"}
    assert state.proposals >= 2
    text = (out / "run.json").read_text()
    stored = {number for number in range(1, state.proposals + 1) if f"improved {number} " in text}
    assert len(stored) == 1


def test_a_failed_holdout_call_resumes_and_finishes_within_the_approved_budget(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.fail_holdout = 1
    state.drain_search = True
    with pytest.raises(RuntimeError, match="fell over"):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert read(out / "run.json")["phase"] == "searched"
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert result["phase"] == "complete" and cast(int, read(out / "run.json")["calls"]) <= calls
    assert cast(dict[str, object], result["estimate"])["holdout_retry_calls"] == 2 * state.contract.calls("echo")


def test_a_gate_that_cannot_finish_stops_with_gate_budget_exhausted(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.fail_holdout = 10_000
    with pytest.raises(RuntimeError):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    stop: Stop | None = None
    for _attempt in range(100):
        try:
            _ = run(target, out, MODEL, live=True, factory=state.factory)
        except Stop as error:
            stop = error
            break
        except RuntimeError:
            continue
    assert stop is not None
    assert stop.code == "gate-budget-exhausted" and "new run" in str(stop.data["next"])
    assert cast(int, read(out / "run.json")["calls"]) <= calls


def test_a_different_coded_error_aborts_the_search_instead_of_scoring_zero(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda number: CodedError("other-code", "a coded stop") if number == 3 else None
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == "other-code"
    record = read(out / "run.json")
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    assert record["phase"] == "prepared" and not any(item.get("status") == "evaluation-failed" for item in outcomes)


@pytest.mark.parametrize("code", ["isolation-failed", "harness-changed"])
def test_an_isolation_or_identity_failure_aborts_the_search_instead_of_scoring_zero(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], code: str) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda number: CodedError(code, "a guard fired") if number == 4 else None
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == code
    record = read(out / "run.json")
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    assert record["phase"] == "prepared" and record["failure"] == "a guard fired"
    assert not any(item.get("status") == "evaluation-failed" for item in outcomes)


def test_a_changed_harness_raises_the_harness_changed_code() -> None:
    harness = object.__new__(Harness)
    harness.configuration = cast(object, _Identity())  # pyright: ignore[reportAttributeAccessIssue]
    harness.identity = cast(dict[str, object], {"changed": True})
    with pytest.raises(CodedError) as caught:
        harness._unchanged()  # pyright: ignore[reportPrivateUsage]
    assert caught.value.code == "harness-changed"


def test_a_search_where_every_evaluation_fails_stops_with_search_failed(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda _number: RuntimeError("provider fell over")
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == "search-failed" and "new run directory" in str(caught.value)
    assert read(out / "run.json")["phase"] == "prepared"


class _Identity:
    def identity(self) -> dict[str, object]:
        return {}


class _Task:
    def __init__(self) -> None:
        self.seen: list[Candidate] = []

    def check_candidate(self, candidate: Candidate) -> bool:
        self.seen.append(candidate)
        return False


def test_harness_check_candidate_delegates_to_the_task_transport(
        tmp_path: Path, make_target: Callable[..., Path]) -> None:
    seed = Candidate.capture(make_target(tmp_path), [], intake_contract(make_target(tmp_path / "other")))
    task = _Task()
    harness = object.__new__(Harness)
    harness.configuration = cast(object, _Identity())  # pyright: ignore[reportAttributeAccessIssue]
    harness.identity = {}
    harness.transports = cast(dict[str, object], {"task": task})  # pyright: ignore[reportAttributeAccessIssue]
    assert harness.check_candidate(seed) is False
    assert task.seen == [seed]


def test_a_run_stopped_by_an_isolation_failure_refuses_to_resume_and_makes_no_live_call(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.invoke_fault = lambda _number: CodedError("isolation-failed", "a guard fired")
    with pytest.raises(CodedError) as first:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    recorded = read(out / "run.json")
    assert first.value.code == "isolation-failed" and recorded["failure_code"] == "isolation-failed"
    assert "preflight" not in recorded
    before = (state.invokes, state.search_calls, recorded["calls"])
    for live in (True, False):
        with pytest.raises(Stop) as second:
            _ = run(target, out, MODEL, live=live, factory=state.factory)
        assert second.value.code == "run-terminated" and "new run" in str(second.value.data["next"])
    assert (state.invokes, state.search_calls, read(out / "run.json")["calls"]) == before


def test_a_credential_change_found_when_the_provider_closes_a_complete_run_keeps_the_result_and_warns(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.close_error = CodedError("credential-changed", "the login file changed")
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    warning = {"code": "credential-changed", "message": "the login file changed"}
    exported_warning = {"code": "credential-changed", "hint": "log in again before the next run"}
    recorded = read(out / "run.json")
    assert result["phase"] == "complete" and result["close_warning"] == exported_warning
    assert recorded["close_warning"] == warning and "failure_code" not in recorded
    before = (state.invokes, state.search_calls, recorded["calls"])
    assert run(target, out, MODEL, live=True, factory=state.factory)["phase"] == "complete"
    assert (state.invokes, state.search_calls, read(out / "run.json")["calls"]) == before
    destination = tmp_path / "exported"
    assert export(out, destination)["installed"] is False
    assert read(destination / "report.json")["close_warning"] == exported_warning
    assert "login file" not in (destination / "report.json").read_text()


@pytest.mark.parametrize("code", ["credential-rotated", "credential-refreshed"])
def test_a_handled_login_change_on_close_keeps_the_result_and_asks_for_no_login(
        code: str, tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.close_error = CodedError(code, "the login changed and the run handled it")
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert result["phase"] == "complete"
    assert result["close_warning"] == {"code": code, "hint": "no action needed"}
    assert "failure_code" not in read(out / "run.json")


def test_a_run_called_inside_an_except_block_does_not_mistake_the_caller_error_for_its_own(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.close_error = CodedError("credential-changed", "the login file changed")
    try:
        raise KeyError("caller")
    except KeyError as caller:
        result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
                     factory=state.factory)
        assert not getattr(caller, "__notes__", [])
    recorded = read(out / "run.json")
    assert result["phase"] == "complete" and recorded["close_warning"] == {"code": "credential-changed", "message": "the login file changed"}
    assert "failure_code" not in recorded and "close_failure" not in recorded
    assert run(target, out, MODEL, live=True, factory=state.factory)["phase"] == "complete"


def test_a_credential_change_found_on_close_after_a_failure_keeps_the_failure_and_ends_the_run(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.fail_holdout = 1
    state.close_error = CodedError("credential-changed", "the login file changed")
    with pytest.raises(RuntimeError) as first:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    recorded = read(out / "run.json")
    assert recorded["failure"] == str(first.value) and recorded["failure"] != "the login file changed"
    assert recorded["close_failure"] == {"code": "credential-changed", "message": "the login file changed"}
    assert recorded["failure_code"] == "credential-changed" and "preflight" not in recorded
    assert any("credential-changed" in note for note in first.value.__notes__)
    with pytest.raises(Stop) as second:
        _ = run(target, out, MODEL, live=True, factory=state.factory)
    assert second.value.code == "run-terminated"


@pytest.mark.parametrize("code", ["credential-rotated", "credential-refreshed"])
def test_a_handled_login_change_on_close_after_a_failure_does_not_end_the_run(
        code: str, tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.fail_holdout = 1
    state.close_error = CodedError(code, "the login changed and the run handled it")
    with pytest.raises(RuntimeError):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    recorded = read(out / "run.json")
    assert recorded["close_failure"] == {"code": code, "message": "the login changed and the run handled it"}
    assert "failure_code" not in recorded
    state.fail_holdout = 0
    state.close_error = None
    assert run(target, out, MODEL, live=True, factory=state.factory)["phase"] == "complete"


def test_a_failed_sandbox_preflight_leaves_the_run_fixable_and_a_later_run_goes_ahead(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.preflight_error = CodedError("sandbox-unavailable", "socat is missing")
    with pytest.raises(CodedError) as first:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert first.value.code == "sandbox-unavailable"
    assert "preflight" not in read(out / "run.json")
    state.preflight_error = None
    assert run(target, out, MODEL, live=True, factory=state.factory)["phase"] == "complete"


def test_a_budget_stop_and_an_isolation_fault_in_one_batch_end_the_run_before_the_gate(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)

    def fault(number: int) -> Exception | None:
        _ = threading.Event().wait(0.1 if number == 1 else 0.4)
        if number == 1:
            return BudgetExhausted("global invocation budget exhausted")
        return CodedError("isolation-failed", "a guard fired") if number == 2 else None

    state.evaluate_fault = fault
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == "isolation-failed"
    assert read(out / "run.json")["failure_code"] == "isolation-failed"
    assert state.holdout_calls == 0


@pytest.mark.parametrize("early", ["sandbox-unavailable", "harness-changed"])
def test_a_terminal_fault_beats_an_earlier_non_terminal_fault_in_one_batch(
        early: str, tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)

    def fault(number: int) -> Exception | None:
        _ = threading.Event().wait(0.1 if number == 1 else 0.4)
        if number == 1:
            return CodedError(early, "an early fault")
        return CodedError("isolation-failed", "a guard fired") if number == 2 else None

    state.evaluate_fault = fault
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == "isolation-failed"
    assert read(out / "run.json")["failure_code"] == "isolation-failed"
    with pytest.raises(Stop) as second:
        _ = run(target, out, MODEL, live=True, factory=state.factory)
    assert second.value.code == "run-terminated"


def test_a_late_transient_gate_failure_before_an_isolation_fault_still_records_the_isolation_code(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.holdout_head_delay = 0.3
    state.holdout_fault = lambda number: (RuntimeError("provider fell over") if number == 1 else
                                          CodedError("isolation-failed", "a guard fired") if number == 2 else None)
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == "isolation-failed"
    assert read(out / "run.json")["failure_code"] == "isolation-failed"
    held = state.holdout_calls
    with pytest.raises(Stop) as second:
        _ = run(target, out, MODEL, live=True, factory=state.factory)
    assert second.value.code == "run-terminated" and state.holdout_calls == held


def test_a_transient_gate_failure_records_no_terminal_code(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.fail_holdout = 1
    with pytest.raises(RuntimeError):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert "failure_code" not in read(out / "run.json")
    assert run(target, out, MODEL, live=True, factory=state.factory)["phase"] == "complete"


def test_a_run_stopped_by_another_coded_error_still_resumes(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.invoke_fault = lambda number: CodedError("harness-changed", "a guard fired") if number == 1 else None
    with pytest.raises(CodedError):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert run(target, out, MODEL, live=True, factory=state.factory)["phase"] == "complete"
